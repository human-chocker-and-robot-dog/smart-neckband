from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from threading import Event, Lock, Thread
import time
from typing import Callable, Iterable
from urllib.parse import urlsplit

from .analysis import EcgAnalysisResult
from .attitude import Orientation
from .health_motion import MotionAnalysis, analyze_motion
from .live_uploader import (
    LiveIngestSocket,
    _valid_hr,
    _valid_sqi,
    build_ecg_upload_batch,
    lead_off_from_status,
)
from .protocol import (
    ECG_SAMPLE_RATE_HZ,
    IMU_SAMPLE_RATE_HZ,
    STATUS_MPU6050_ONLINE,
)
from .serial_io import PcDataStores
from .uwb import DistanceProvider, UnavailableDistanceProvider


@dataclass(frozen=True, slots=True)
class DashboardRelaySettings:
    ws_url: str
    session_id: str
    ingest_token: str
    public_url: str = "/dashboard"

    @classmethod
    def from_environment(cls) -> DashboardRelaySettings:
        return cls(
            ws_url=os.environ.get("LIVE_WS_URL", "").strip(),
            session_id=os.environ.get("LIVE_SESSION_ID", "").strip(),
            ingest_token=os.environ.get("LIVE_INGEST_TOKEN", "").strip(),
            public_url=os.environ.get(
                "SMART_COLLAR_DASHBOARD_PUBLIC_URL", "/dashboard"
            ).strip()
            or "/dashboard",
        )

    @property
    def configured(self) -> bool:
        return bool(self.ws_url and self.session_id and self.ingest_token)

    @property
    def endpoint_label(self) -> str:
        if not self.ws_url:
            return "-"
        parsed = urlsplit(self.ws_url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def validate(self) -> None:
        values = (self.ws_url, self.session_id, self.ingest_token)
        if any(values) and not all(values):
            raise ValueError(
                "LIVE_WS_URL, LIVE_SESSION_ID, and LIVE_INGEST_TOKEN must be configured together"
            )
        if not self.configured:
            raise ValueError("Dashboard relay is not configured")
        parsed = urlsplit(self.ws_url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise ValueError("LIVE_WS_URL must be an absolute ws:// or wss:// URL")


@dataclass(frozen=True, slots=True)
class DashboardRelayStatus:
    configured: bool
    running: bool
    connected: bool
    endpoint: str
    public_url: str
    last_success_at: str | None
    last_error: str | None


def _iso_to_epoch_ms(value: object) -> int:
    if not isinstance(value, str) or not value:
        return int(time.time() * 1000)
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return int(time.time() * 1000)


def _public_event_type(value: str) -> str:
    return {
        "lead_off": "signal.lead_off",
        "adc_clipping": "signal.adc_clipping",
        "input_stale": "input.stale",
        "input_offline": "input.offline",
    }.get(value, value)


def build_health_event_message(event: dict[str, object]) -> dict[str, object]:
    severity = str(event.get("bridge_severity") or event.get("severity") or "warning")
    if severity == "error":
        severity = "critical"
    if severity not in {"info", "warning", "critical"}:
        severity = "warning"
    priority = str(event.get("priority") or "normal")
    if priority not in {"normal", "urgent"}:
        priority = "normal"
    status = str(event.get("status") or "active")
    return {
        "type": "health_event",
        "event_id": str(event["event_id"]),
        "event_revision": max(1, int(event.get("event_revision") or 1)),
        "event_type": _public_event_type(str(event["event_type"])),
        "transition": "resolved" if status == "resolved" else "opened",
        "severity": severity,
        "priority": priority,
        "timestamp_ms": _iso_to_epoch_ms(event.get("updated_at")),
        "summary": str(event.get("summary") or event["event_type"])[:512],
    }


def build_telemetry_message(
    *,
    stores: PcDataStores,
    reader: object | None,
    analysis: EcgAnalysisResult | None,
    orientation: Orientation | None,
    distance_provider: DistanceProvider,
    motion_analysis: MotionAnalysis | None = None,
    now_epoch_ms: int | None = None,
    now_monotonic_ns: int | None = None,
) -> dict[str, object]:
    timestamp_ms = int(time.time() * 1000) if now_epoch_ms is None else now_epoch_ms
    monotonic_ns = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
    latest_status = stores.status.latest()
    lead_off = lead_off_from_status(latest_status)
    runtime = getattr(reader, "runtime_status", None) if reader is not None else None
    stats = getattr(reader, "stats", None) if reader is not None else None
    source_instance_id = str(getattr(runtime, "source_instance_id", ""))
    motion = motion_analysis or analyze_motion(
        stores.imu.snapshot(), source_instance_id=source_instance_id
    )
    status_flags = latest_status.payload.status_flags if latest_status is not None else None
    imu_online = (
        bool(status_flags & STATUS_MPU6050_ONLINE)
        if status_flags is not None
        else None
    )
    last_transport_ns = getattr(runtime, "last_transport_packet_monotonic_ns", None)
    data_age_ms = (
        max(0, int((monotonic_ns - int(last_transport_ns)) / 1_000_000))
        if last_transport_ns is not None
        else None
    )
    distance = distance_provider.latest()
    distance_source = (
        distance.source
        if distance.source in {"live", "demo", "unavailable"}
        else "unavailable"
    )
    return {
        "type": "telemetry",
        "timestamp_ms": timestamp_ms,
        "heart": {
            "bpm": _valid_hr(analysis.heart_rate_bpm) if analysis is not None else None,
            "sqi": _valid_sqi(analysis.signal_quality) if analysis is not None else None,
            "lead_off": lead_off,
        },
        "imu": {
            "online": imu_online,
            "roll_deg": orientation.roll_deg if orientation is not None else None,
            "pitch_deg": orientation.pitch_deg if orientation is not None else None,
            "yaw_deg": orientation.yaw_deg if orientation is not None else None,
            "motion_score": motion.score,
            "still_ratio_percent": motion.still_ratio_percent,
            "level": motion.level,
        },
        "uwb": {
            "distance_m": (
                max(0.0, float(distance.distance_m))
                if distance.distance_m is not None
                else None
            ),
            "quality": (
                min(1.0, max(0.0, float(distance.quality)))
                if distance.quality is not None
                else None
            ),
            "source": distance_source,
        },
        "device": {
            "transport": str(getattr(reader, "health_transport", "unknown")),
            "ecg_sample_rate_hz": ECG_SAMPLE_RATE_HZ,
            "imu_sample_rate_hz": IMU_SAMPLE_RATE_HZ,
            "packet_loss": max(0, int(getattr(stats, "packets_lost", 0))),
            "crc_errors": max(0, int(getattr(stats, "crc_errors", 0))),
            "data_age_ms": data_age_ms,
        },
    }


class DashboardRelayWorker:
    def __init__(
        self,
        *,
        settings: DashboardRelaySettings,
        stores: PcDataStores,
        reader_provider: Callable[[], object | None],
        analysis_provider: Callable[[], EcgAnalysisResult | None],
        orientation_provider: Callable[[], Orientation | None],
        event_provider: Callable[[], Iterable[dict[str, object]]] = lambda: (),
        distance_provider: DistanceProvider | None = None,
        socket_factory: Callable[..., LiveIngestSocket] = LiveIngestSocket,
    ) -> None:
        self.settings = settings
        self.stores = stores
        self.reader_provider = reader_provider
        self.analysis_provider = analysis_provider
        self.orientation_provider = orientation_provider
        self.event_provider = event_provider
        self.distance_provider = distance_provider or UnavailableDistanceProvider()
        self.socket_factory = socket_factory
        self._stop = Event()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._connected = False
        self._last_success_at: str | None = None
        self._last_error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        self.settings.validate()
        if self.is_running:
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="DashboardRelayWorker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def status(self) -> DashboardRelayStatus:
        with self._lock:
            return DashboardRelayStatus(
                configured=self.settings.configured,
                running=self.is_running,
                connected=self._connected,
                endpoint=self.settings.endpoint_label,
                public_url=self.settings.public_url,
                last_success_at=self._last_success_at,
                last_error=self._last_error,
            )

    def _set_status(
        self,
        *,
        connected: bool | None = None,
        success: bool = False,
        error: str | None = None,
    ) -> None:
        with self._lock:
            if connected is not None:
                self._connected = connected
            if success:
                self._last_success_at = datetime.now().astimezone().isoformat(timespec="seconds")
                self._last_error = None
            if error is not None:
                self._last_error = error

    def _run(self) -> None:
        last_sent_sample_index: int | None = None
        sent_event_revisions: dict[str, int] = {}
        cached_motion: MotionAnalysis | None = None
        reconnect_attempt = 0
        while not self._stop.is_set():
            live = self.socket_factory(
                ws_url=self.settings.ws_url,
                session_id=self.settings.session_id,
                ingest_token=self.settings.ingest_token,
            )
            next_telemetry_at = 0.0
            next_status_at = 0.0
            next_events_at = 0.0
            next_motion_at = 0.0
            try:
                sequence = live.connect()
                reconnect_attempt = 0
                self._set_status(connected=True)
                while not self._stop.is_set():
                    now = time.monotonic()
                    reader = self.reader_provider()
                    analysis = self.analysis_provider()
                    if now >= next_motion_at:
                        runtime = getattr(reader, "runtime_status", None) if reader is not None else None
                        source_instance_id = str(getattr(runtime, "source_instance_id", ""))
                        cached_motion = analyze_motion(
                            self.stores.imu.snapshot(),
                            source_instance_id=source_instance_id,
                        )
                        next_motion_at = now + 1.0
                    if analysis is not None:
                        batch = build_ecg_upload_batch(
                            seq=sequence,
                            window=self.stores.ecg.snapshot(),
                            analysis=analysis,
                            last_sent_sample_index=last_sent_sample_index,
                            lead_off=lead_off_from_status(self.stores.status.latest()),
                        )
                        if batch is not None:
                            live.send_json(batch.message)
                            sequence = live.next_sequence
                            last_sent_sample_index = batch.last_sample_index
                            self._set_status(connected=True, success=True)

                    if now >= next_telemetry_at:
                        live.send_json(
                            build_telemetry_message(
                                stores=self.stores,
                                reader=reader,
                                analysis=analysis,
                                orientation=self.orientation_provider(),
                                distance_provider=self.distance_provider,
                                motion_analysis=cached_motion,
                            )
                        )
                        self._set_status(connected=True, success=True)
                        next_telemetry_at = now + 0.2

                    if now >= next_status_at:
                        current_stats = getattr(reader, "stats", None) if reader is not None else None
                        if current_stats is not None and int(getattr(current_stats, "packets_ok", 0)) > 0:
                            live.send_json(
                                {
                                    "type": "status",
                                    "timestamp_ms": int(time.time() * 1000),
                                    "hr_bpm": _valid_hr(analysis.heart_rate_bpm) if analysis is not None else None,
                                    "sqi": _valid_sqi(analysis.signal_quality) if analysis is not None else None,
                                    "lead_off": lead_off_from_status(self.stores.status.latest()),
                                    "packet_loss": max(0, int(getattr(current_stats, "packets_lost", 0))),
                                    "crc_errors": max(0, int(getattr(current_stats, "crc_errors", 0))),
                                    "note": analysis.message if analysis is not None else "waiting for ECG analysis",
                                }
                            )
                        next_status_at = now + 1.0

                    if now >= next_events_at:
                        for event in self.event_provider():
                            event_id = str(event.get("event_id") or "")
                            revision = int(event.get("event_revision") or 0)
                            if not event_id or revision <= sent_event_revisions.get(event_id, 0):
                                continue
                            live.send_json(build_health_event_message(event))
                            sent_event_revisions[event_id] = revision
                        next_events_at = now + 1.0
                    self._stop.wait(0.05)
            except Exception as exc:
                reconnect_attempt += 1
                self._set_status(connected=False, error=str(exc))
            finally:
                live.close()
                self._set_status(connected=False)
            delay_s = min(30.0, 0.5 * (2 ** min(reconnect_attempt, 6)))
            self._stop.wait(delay_s)
