from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import time
from typing import Callable

from .analysis import EcgAnalysisResult, RrIntervalObservation
from .buffers import EcgSample, StatusSample
from .health_contract import SCHEMA_VERSION, validate_wearer_id
from .health_motion import MotionAnalysis, analyze_motion
from .health_quality import (
    HealthQuality,
    HealthQualityWindow,
    HealthWindowMetrics,
    clipping_window,
    evaluate_quality,
)
from .protocol import (
    ECG_SAMPLE_RATE_HZ,
    IMU_SAMPLE_RATE_HZ,
    STATUS_MPU6050_ONLINE,
    STATUS_OLED_ONLINE,
    STATUS_SAMPLING_ACTIVE,
    STATUS_SPP_CONGESTED,
    ParserStats,
)
from .serial_io import PcDataStores, SerialRuntimeStatus


@dataclass(frozen=True, slots=True)
class BuiltHealthState:
    document: dict[str, object]
    committed_monotonic_ns: int
    ecg_received_monotonic_ns: int
    transport_received_monotonic_ns: int
    status_evidence_key: str | None
    clipping_window_full: bool
    motion_analysis: MotionAnalysis = field(
        default_factory=lambda: analyze_motion((), source_instance_id="")
    )
    rr_intervals: tuple[RrIntervalObservation, ...] = ()


def age_ms(now_monotonic_ns: int, evidence_monotonic_ns: int | None) -> int | None:
    if evidence_monotonic_ns is None or evidence_monotonic_ns <= 0:
        return None
    return max(0, (now_monotonic_ns - evidence_monotonic_ns) // 1_000_000)


def _freshness(ecg_age_ms: int) -> str:
    if ecg_age_ms <= 2_000:
        return "fresh"
    if ecg_age_ms <= 10_000:
        return "stale"
    return "offline"


def _connection_state(runtime: SerialRuntimeStatus, transport_age_ms: int | None) -> str:
    if runtime.last_error is not None:
        return "ERROR"
    if not runtime.serial_open:
        return "DISCONNECTED"
    if transport_age_ms is None:
        return "CONNECTED_WAITING_DATA"
    if transport_age_ms <= 2_000:
        return "RECEIVING"
    return "STALE"


def _status_bits(
    latest_status: StatusSample | None,
    *,
    now_monotonic_ns: int,
) -> tuple[StatusSample | None, bool | None]:
    if latest_status is None:
        return None, None
    status_age = age_ms(now_monotonic_ns, latest_status.received_monotonic_ns)
    if status_age is None or status_age > 2_000:
        return None, None
    return latest_status, bool(latest_status.payload.lead_off_flags)


def build_device_state(
    *,
    runtime: SerialRuntimeStatus,
    parser_stats: ParserStats,
    latest_status: StatusSample | None,
    transport: str,
    now_monotonic_ns: int,
    clipping_ratio_10s: float | None = None,
    device_counter_delta_10s: int = 0,
) -> dict[str, object]:
    ecg_age = age_ms(now_monotonic_ns, runtime.last_ecg_packet_monotonic_ns)
    transport_age = age_ms(
        now_monotonic_ns,
        runtime.last_transport_packet_monotonic_ns,
    )
    fresh_status, lead_off = _status_bits(
        latest_status,
        now_monotonic_ns=now_monotonic_ns,
    )
    status_flags = fresh_status.payload.status_flags if fresh_status else None
    counters_payload = fresh_status.payload if fresh_status else None
    connection_state = _connection_state(runtime, transport_age)

    no_packet_too_long = (
        transport_age is None
        and runtime.started_at_monotonic_s is not None
        and now_monotonic_ns
        - int(runtime.started_at_monotonic_s * 1_000_000_000)
        > 10_000_000_000
    )
    if (
        runtime.last_error is not None
        or not runtime.serial_open
        or (transport_age is not None and transport_age > 10_000)
        or no_packet_too_long
    ):
        device_status = "offline"
    elif transport_age is not None and transport_age > 2_000:
        device_status = "stale"
    else:
        degraded = (
            transport_age is None
            or ecg_age is None
            or ecg_age > 2_000
            or status_flags is None
            or not bool(status_flags & STATUS_SAMPLING_ACTIVE)
            or bool(status_flags & STATUS_SPP_CONGESTED)
            or not bool(status_flags & STATUS_MPU6050_ONLINE)
            or bool(lead_off)
            or (
                clipping_ratio_10s is not None
                and clipping_ratio_10s > 0
            )
            or device_counter_delta_10s > 0
        )
        device_status = "degraded" if degraded else "ok"

    return {
        "status": device_status,
        "connection_state": connection_state,
        "transport": (
            transport if transport in {"uart", "spp", "ble", "unknown"} else "unknown"
        ),
        "firmware_protocol_version": 1,
        "ecg_sample_rate_hz": ECG_SAMPLE_RATE_HZ,
        "imu_sample_rate_hz": IMU_SAMPLE_RATE_HZ,
        "last_ecg_packet_age_ms": ecg_age,
        "last_transport_packet_age_ms": transport_age,
        "sampling_active": (
            bool(status_flags & STATUS_SAMPLING_ACTIVE)
            if status_flags is not None
            else None
        ),
        "imu_online": (
            bool(status_flags & STATUS_MPU6050_ONLINE)
            if status_flags is not None
            else None
        ),
        "oled_online": (
            bool(status_flags & STATUS_OLED_ONLINE)
            if status_flags is not None
            else None
        ),
        "transport_connected": runtime.serial_open,
        "transport_congested": (
            bool(status_flags & STATUS_SPP_CONGESTED)
            if status_flags is not None
            else None
        ),
        "counters": {
            "packets_ok": parser_stats.packets_ok,
            "packets_lost": parser_stats.packets_lost,
            "crc_errors": parser_stats.crc_errors,
            "firmware_error_count": (
                counters_payload.error_count if counters_payload else 0
            ),
            "ecg_ring_overflow_count": (
                counters_payload.ecg_ring_overflow_count if counters_payload else 0
            ),
            "imu_ring_overflow_count": (
                counters_payload.imu_ring_overflow_count if counters_payload else 0
            ),
            "transport_queue_overflow_count": (
                counters_payload.spp_queue_overflow_count if counters_payload else 0
            ),
            "transport_drop_count": (
                counters_payload.transport_drop_count if counters_payload else 0
            ),
            "i2c_error_count": (
                counters_payload.i2c_error_count if counters_payload else 0
            ),
        },
    }


def _unavailable_metric(unit: str, method: str, reason: str) -> dict[str, object]:
    return {
        "value": None,
        "unit": unit,
        "valid": False,
        "observed_at": None,
        "age_ms": None,
        "window_s": 10.0,
        "method": method,
        "unavailable_reason": reason,
    }


def _metric_reason(
    *,
    quality: HealthQuality,
    freshness: str,
    analysis: EcgAnalysisResult | None,
    source_instance_id: str,
    current_ecg_ordinal: int,
    samples: tuple[EcgSample, ...],
    now_monotonic_ns: int,
) -> tuple[str | None, int | None]:
    if quality.lead_off:
        return "lead_off", None
    if (
        quality.adc_clipping_ratio_10s is not None
        and quality.adc_clipping_ratio_10s >= 0.80
    ):
        return "adc_clipping", None
    if freshness != "fresh":
        return "stale_data", None
    if analysis is None:
        return "insufficient_window", None
    analyzed_index = analysis.analyzed_through_ecg_sample_index
    evidence_ns = analysis.analyzed_through_received_monotonic_ns
    provenance_valid = (
        analysis.source_instance_id == source_instance_id
        and analyzed_index is not None
        and analyzed_index <= current_ecg_ordinal
        and evidence_ns is not None
        and any(
            sample.source_instance_id == source_instance_id
            and sample.sample_index == analyzed_index
            for sample in samples
        )
    )
    analysis_age = age_ms(now_monotonic_ns, evidence_ns)
    if not provenance_valid or analysis_age is None or analysis_age > 2_000:
        return "stale_data", analysis_age
    if analysis.message == "waiting for ECG window":
        return "insufficient_window", analysis_age
    if analysis.message in {"need more R peaks", "need stable R peaks"}:
        return "insufficient_stable_r_peaks", analysis_age
    if analysis.message != "ok":
        return "analysis_unavailable", analysis_age
    if quality.quality_rank < 2:
        return "quality_below_threshold", analysis_age
    return None, analysis_age


def _build_heart(
    *,
    analysis: EcgAnalysisResult | None,
    quality: HealthQuality,
    freshness: str,
    source_instance_id: str,
    current_ecg_ordinal: int,
    samples: tuple[EcgSample, ...],
    now_monotonic_ns: int,
) -> dict[str, object]:
    reason, analysis_age = _metric_reason(
        quality=quality,
        freshness=freshness,
        analysis=analysis,
        source_instance_id=source_instance_id,
        current_ecg_ordinal=current_ecg_ordinal,
        samples=samples,
        now_monotonic_ns=now_monotonic_ns,
    )
    heart_rate = _unavailable_metric(
        "bpm",
        "neurokit2_rr_median",
        reason or "analysis_unavailable",
    )
    rr_interval = _unavailable_metric(
        "ms",
        "neurokit2_latest_valid_rr",
        reason or "analysis_unavailable",
    )
    if (
        reason is None
        and analysis is not None
        and analysis_age is not None
        and analysis.analyzed_through_received_at_utc is not None
    ):
        if analysis.heart_rate_bpm is not None and 20 <= analysis.heart_rate_bpm <= 240:
            heart_rate.update(
                {
                    "value": analysis.heart_rate_bpm,
                    "valid": True,
                    "observed_at": analysis.analyzed_through_received_at_utc,
                    "age_ms": analysis_age,
                    "unavailable_reason": None,
                }
            )
        else:
            heart_rate["unavailable_reason"] = "insufficient_stable_r_peaks"
        if analysis.latest_rr_ms is not None and 300 <= analysis.latest_rr_ms <= 2_000:
            rr_interval.update(
                {
                    "value": analysis.latest_rr_ms,
                    "valid": True,
                    "observed_at": analysis.analyzed_through_received_at_utc,
                    "age_ms": analysis_age,
                    "unavailable_reason": None,
                }
            )
        else:
            rr_interval["unavailable_reason"] = "insufficient_stable_r_peaks"

    return {
        "analysis_source_instance_id": (
            analysis.source_instance_id if analysis is not None else None
        ),
        "analyzed_through_ecg_sample_index": (
            analysis.analyzed_through_ecg_sample_index
            if analysis is not None
            else None
        ),
        "heart_rate": heart_rate,
        "rr_interval": rr_interval,
    }


class HealthStateBuilder:
    def __init__(
        self,
        *,
        wearer_id: str,
        data_source: str = "live",
        test_mode: bool = False,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        if data_source not in {"live", "replay", "synthetic"}:
            raise ValueError("unsupported data_source")
        if data_source != "live" and not test_mode:
            raise ValueError("replay and synthetic states require test_mode=true")
        self.wearer_id = validate_wearer_id(wearer_id)
        self.data_source = data_source
        self.test_mode = test_mode
        self.monotonic_ns = monotonic_ns
        self.quality_window = HealthQualityWindow()

    def _window_metrics(
        self,
        *,
        source_instance_id: str,
        parser_stats: ParserStats,
        latest_status: StatusSample | None,
        now_monotonic_ns: int,
    ) -> HealthWindowMetrics:
        return self.quality_window.observe(
            source_instance_id=source_instance_id,
            now_monotonic_ns=now_monotonic_ns,
            parser_stats=parser_stats,
            latest_status=latest_status,
        )

    def build_device_snapshot(
        self,
        *,
        stores: PcDataStores,
        runtime: SerialRuntimeStatus,
        parser_stats: ParserStats,
        transport: str,
        now_monotonic_ns: int | None = None,
    ) -> dict[str, object]:
        now_ns = self.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        samples = stores.ecg.snapshot()
        latest_status = stores.status.latest()
        source_instance_id = (
            samples[-1].source_instance_id
            if samples
            else runtime.source_instance_id
        )
        metrics = self._window_metrics(
            source_instance_id=source_instance_id,
            parser_stats=parser_stats,
            latest_status=latest_status,
            now_monotonic_ns=now_ns,
        )
        clip_ratio, _ = clipping_window(
            samples,
            source_instance_id=source_instance_id,
        )
        return build_device_state(
            runtime=runtime,
            parser_stats=parser_stats,
            latest_status=latest_status,
            transport=transport,
            now_monotonic_ns=now_ns,
            clipping_ratio_10s=clip_ratio,
            device_counter_delta_10s=metrics.device_counter_delta_10s,
        )

    def build(
        self,
        *,
        stores: PcDataStores,
        runtime: SerialRuntimeStatus,
        parser_stats: ParserStats,
        analysis: EcgAnalysisResult | None,
        transport: str,
        now_monotonic_ns: int | None = None,
    ) -> BuiltHealthState | None:
        now_ns = self.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        samples = stores.ecg.snapshot()
        if not samples:
            return None
        latest_ecg = samples[-1]
        if (
            not latest_ecg.source_instance_id
            or latest_ecg.received_monotonic_ns <= 0
            or not latest_ecg.received_at_utc
        ):
            return None

        ecg_age = age_ms(now_ns, latest_ecg.received_monotonic_ns)
        assert ecg_age is not None
        freshness = (
            "offline"
            if runtime.last_error is not None or not runtime.serial_open
            else _freshness(ecg_age)
        )
        latest_status = stores.status.latest()
        fresh_status, lead_off = _status_bits(
            latest_status,
            now_monotonic_ns=now_ns,
        )
        metrics = self._window_metrics(
            source_instance_id=latest_ecg.source_instance_id,
            now_monotonic_ns=now_ns,
            parser_stats=parser_stats,
            latest_status=latest_status,
        )
        quality = evaluate_quality(
            samples=samples,
            analysis=analysis,
            lead_off=lead_off,
            freshness=freshness,
            source_instance_id=latest_ecg.source_instance_id,
            packet_loss_ratio_10s=metrics.packet_loss_ratio_10s,
            crc_errors_delta_10s=metrics.crc_errors_delta_10s,
        )
        device = build_device_state(
            runtime=runtime,
            parser_stats=parser_stats,
            latest_status=latest_status,
            transport=transport,
            now_monotonic_ns=now_ns,
            clipping_ratio_10s=quality.adc_clipping_ratio_10s,
            device_counter_delta_10s=metrics.device_counter_delta_10s,
        )
        transport_received_ns = runtime.last_transport_packet_monotonic_ns
        if transport_received_ns is None:
            transport_received_ns = latest_ecg.received_monotonic_ns
        motion_analysis = analyze_motion(
            stores.imu.snapshot(),
            source_instance_id=latest_ecg.source_instance_id,
            window_s=30.0,
        )
        document: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "wearer_id": self.wearer_id,
            "state_revision": 0,
            "source_instance_id": latest_ecg.source_instance_id,
            "data_source": self.data_source,
            "observed_at": latest_ecg.received_at_utc,
            "device_timestamp_us": latest_ecg.timestamp_us,
            "ecg_sample_index": latest_ecg.sample_index,
            "age_ms": ecg_age,
            "freshness": freshness,
            "heart": _build_heart(
                analysis=analysis,
                quality=quality,
                freshness=freshness,
                source_instance_id=latest_ecg.source_instance_id,
                current_ecg_ordinal=latest_ecg.sample_index,
                samples=samples,
                now_monotonic_ns=now_ns,
            ),
            "signal": quality.to_dict(),
            "motion": {
                "level": None,
                "confidence": None,
                "observed_at": None,
                "age_ms": None,
                "window_s": None,
                "method": None,
                "unavailable_reason": "not_implemented",
            },
            "device": device,
            "active_events": [],
            "test_mode": self.test_mode,
        }
        return BuiltHealthState(
            document=deepcopy(document),
            committed_monotonic_ns=now_ns,
            ecg_received_monotonic_ns=latest_ecg.received_monotonic_ns,
            transport_received_monotonic_ns=transport_received_ns,
            status_evidence_key=(
                f"{fresh_status.source_instance_id}:"
                f"{fresh_status.received_monotonic_ns}:"
                f"{fresh_status.timestamp_us}"
                if fresh_status is not None
                else None
            ),
            clipping_window_full=clipping_window(
                samples,
                source_instance_id=latest_ecg.source_instance_id,
            )[1],
            motion_analysis=motion_analysis,
            rr_intervals=(analysis.rr_intervals if analysis is not None else ()),
        )
