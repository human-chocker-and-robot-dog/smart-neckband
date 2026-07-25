from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .analysis import EcgAnalysisResult
from .buffers import EcgSample, StatusSample
from .protocol import FLAG_ADC_CLIPPING, DeviceStatusPayload, ParserStats


QUALITY_RANK = {
    "unknown": 0,
    "bad": 1,
    "fair": 2,
    "good": 3,
}


@dataclass(frozen=True, slots=True)
class HealthQuality:
    quality_score: float | None
    quality_level: str
    quality_rank: int
    lead_off: bool | None
    adc_clipping_ratio_10s: float | None
    packet_loss_ratio_10s: float | None
    crc_errors_delta_10s: int
    window_s: float = 10.0

    def to_dict(self) -> dict[str, object]:
        return {
            "quality_score": self.quality_score,
            "quality_level": self.quality_level,
            "quality_rank": self.quality_rank,
            "lead_off": self.lead_off,
            "adc_clipping_ratio_10s": self.adc_clipping_ratio_10s,
            "packet_loss_ratio_10s": self.packet_loss_ratio_10s,
            "crc_errors_delta_10s": self.crc_errors_delta_10s,
            "window_s": self.window_s,
        }


DEVICE_COUNTER_FIELDS = (
    "error_count",
    "ecg_ring_overflow_count",
    "imu_ring_overflow_count",
    "spp_queue_overflow_count",
    "transport_drop_count",
    "i2c_error_count",
)


@dataclass(frozen=True, slots=True)
class HealthWindowMetrics:
    packet_loss_ratio_10s: float | None
    crc_errors_delta_10s: int
    device_counter_delta_10s: int


def clipping_window(
    samples: tuple[EcgSample, ...],
    *,
    source_instance_id: str,
) -> tuple[float | None, bool]:
    source_samples = tuple(
        sample
        for sample in samples
        if sample.source_instance_id == source_instance_id
        and sample.received_monotonic_ns > 0
    )
    if not source_samples:
        return None, False
    newest_receipt_ns = source_samples[-1].received_monotonic_ns
    cutoff_ns = newest_receipt_ns - 10_000_000_000
    window = tuple(
        sample
        for sample in source_samples
        if sample.received_monotonic_ns >= cutoff_ns
    )
    if not window:
        return None, False
    full = (
        len(window) >= 5_000
        and source_samples[0].received_monotonic_ns <= cutoff_ns
    )
    ratio = sum(
        bool(sample.flags & FLAG_ADC_CLIPPING) for sample in window
    ) / len(window)
    return ratio, full


def packet_loss_ratio(packets_ok: int, packets_lost: int) -> float | None:
    denominator = packets_ok + packets_lost
    if denominator <= 0:
        return None
    return packets_lost / denominator


class HealthQualityWindow:
    """Track parser counter deltas over the most recent ten-second window."""

    def __init__(self, window_ns: int = 10_000_000_000) -> None:
        self.window_ns = window_ns
        self._observations: deque[tuple[int, int, int, int]] = deque()
        self._status_observations: deque[
            tuple[int, tuple[int, ...]]
        ] = deque()
        self._source_instance_id: str | None = None
        self._last_status_key: tuple[str, int, int] | None = None

    def observe(
        self,
        *,
        source_instance_id: str,
        now_monotonic_ns: int,
        parser_stats: ParserStats,
        latest_status: StatusSample | None,
    ) -> HealthWindowMetrics:
        if source_instance_id != self._source_instance_id:
            self._observations.clear()
            self._status_observations.clear()
            self._last_status_key = None
            self._source_instance_id = source_instance_id
        current = (
            now_monotonic_ns,
            parser_stats.packets_ok,
            parser_stats.packets_lost,
            parser_stats.crc_errors,
        )
        if self._observations and (
            now_monotonic_ns < self._observations[-1][0]
            or parser_stats.packets_ok < self._observations[-1][1]
            or parser_stats.packets_lost < self._observations[-1][2]
            or parser_stats.crc_errors < self._observations[-1][3]
        ):
            self._observations.clear()
        self._observations.append(current)
        cutoff = now_monotonic_ns - self.window_ns
        while len(self._observations) > 1 and self._observations[1][0] <= cutoff:
            self._observations.popleft()
        baseline = self._observations[0]
        packets_ok = parser_stats.packets_ok - baseline[1]
        packets_lost = parser_stats.packets_lost - baseline[2]
        crc_errors = parser_stats.crc_errors - baseline[3]
        device_counter_delta = self._observe_status(
            source_instance_id=source_instance_id,
            latest_status=latest_status,
        )
        return HealthWindowMetrics(
            packet_loss_ratio_10s=packet_loss_ratio(
                packets_ok,
                packets_lost,
            ),
            crc_errors_delta_10s=crc_errors,
            device_counter_delta_10s=device_counter_delta,
        )

    def _observe_status(
        self,
        *,
        source_instance_id: str,
        latest_status: StatusSample | None,
    ) -> int:
        if (
            latest_status is None
            or latest_status.source_instance_id != source_instance_id
        ):
            return 0
        status_key = (
            latest_status.source_instance_id,
            latest_status.received_monotonic_ns,
            latest_status.timestamp_us,
        )
        counters = _device_counters(latest_status.payload)
        if status_key != self._last_status_key:
            if self._status_observations and (
                latest_status.received_monotonic_ns
                < self._status_observations[-1][0]
                or any(
                    current < previous
                    for current, previous in zip(
                        counters,
                        self._status_observations[-1][1],
                    )
                )
            ):
                self._status_observations.clear()
            self._status_observations.append(
                (latest_status.received_monotonic_ns, counters)
            )
            self._last_status_key = status_key
        cutoff = latest_status.received_monotonic_ns - self.window_ns
        while (
            len(self._status_observations) > 1
            and self._status_observations[1][0] <= cutoff
        ):
            self._status_observations.popleft()
        if len(self._status_observations) <= 1:
            return 0
        baseline = self._status_observations[0][1]
        return sum(
            max(0, current - previous)
            for current, previous in zip(counters, baseline)
        )


def _device_counters(payload: DeviceStatusPayload) -> tuple[int, ...]:
    return tuple(int(getattr(payload, field)) for field in DEVICE_COUNTER_FIELDS)


def evaluate_quality(
    *,
    samples: tuple[EcgSample, ...],
    analysis: EcgAnalysisResult | None,
    lead_off: bool | None,
    freshness: str,
    source_instance_id: str,
    packet_loss_ratio_10s: float | None,
    crc_errors_delta_10s: int,
) -> HealthQuality:
    clip_ratio, _ = clipping_window(
        samples,
        source_instance_id=source_instance_id,
    )
    loss_ratio = packet_loss_ratio_10s
    crc_delta = crc_errors_delta_10s
    score = analysis.signal_quality if analysis is not None else None

    if lead_off is None:
        level = "unknown"
        score = None
    elif lead_off:
        level = "bad"
    elif clip_ratio is not None and clip_ratio >= 0.80:
        level = "bad"
    elif freshness != "fresh":
        level = "unknown"
    elif score is None:
        level = "unknown"
    elif score < 0.50:
        level = "bad"
    elif score < 0.80:
        level = "fair"
    elif (
        clip_ratio is not None
        and clip_ratio < 0.05
        and loss_ratio is not None
        and loss_ratio <= 0.01
        and crc_delta == 0
    ):
        level = "good"
    else:
        level = "fair"

    return HealthQuality(
        quality_score=score,
        quality_level=level,
        quality_rank=QUALITY_RANK[level],
        lead_off=lead_off,
        adc_clipping_ratio_10s=clip_ratio,
        packet_loss_ratio_10s=loss_ratio,
        crc_errors_delta_10s=crc_delta,
    )
