from __future__ import annotations

from types import SimpleNamespace
import time

from smart_neckband.analysis import EcgAnalysisResult
from smart_neckband.attitude import Orientation
from smart_neckband.buffers import ImuSample
from smart_neckband.dashboard_relay import (
    DashboardRelaySettings,
    DashboardRelayWorker,
    build_health_event_message,
    build_telemetry_message,
)
from smart_neckband.uwb import UnavailableDistanceProvider


SOURCE = "ef132c67-a98f-474a-a673-4ab6ea784790"


class SnapshotBuffer:
    def __init__(self, values=()):
        self.values = tuple(values)

    def snapshot(self):
        return self.values


class LatestBuffer:
    def latest(self):
        return None


def imu_samples() -> tuple[ImuSample, ...]:
    return tuple(
        ImuSample(
            sample_index=index,
            timestamp_us=index * 20_000,
            ax=0,
            ay=0,
            az=16_384,
            gx=0,
            gy=0,
            gz=0,
            flags=0,
            source_instance_id=SOURCE,
        )
        for index in range(30 * 50)
    )


def analysis() -> EcgAnalysisResult:
    return EcgAnalysisResult(
        timestamp_s=1.0,
        raw=(),
        cleaned=(),
        r_peak_indices=(),
        heart_rate_bpm=74.0,
        latest_rr_ms=810.0,
        signal_quality=0.92,
        message="ok",
    )


def test_telemetry_uses_shared_state_and_unavailable_uwb() -> None:
    stores = SimpleNamespace(
        ecg=SnapshotBuffer(),
        imu=SnapshotBuffer(imu_samples()),
        status=LatestBuffer(),
    )
    runtime = SimpleNamespace(
        source_instance_id=SOURCE,
        last_transport_packet_monotonic_ns=1_000_000_000,
    )
    reader = SimpleNamespace(
        runtime_status=runtime,
        stats=SimpleNamespace(packets_lost=2, crc_errors=1),
        health_transport="ble",
    )

    message = build_telemetry_message(
        stores=stores,
        reader=reader,
        analysis=analysis(),
        orientation=Orientation(roll_deg=4.0, pitch_deg=-8.0, yaw_deg=12.0),
        distance_provider=UnavailableDistanceProvider(),
        now_epoch_ms=2_000,
        now_monotonic_ns=1_080_000_000,
    )

    assert message["heart"] == {"bpm": 74.0, "sqi": 0.92, "lead_off": False}
    assert message["imu"]["motion_score"] == 0.0
    assert message["imu"]["level"] == "still"
    assert message["imu"]["roll_deg"] == 4.0
    assert message["uwb"] == {
        "distance_m": None,
        "quality": None,
        "source": "unavailable",
    }
    assert message["device"]["transport"] == "ble"
    assert message["device"]["data_age_ms"] == 80


def test_health_event_maps_internal_names_and_revisions() -> None:
    message = build_health_event_message(
        {
            "event_id": "event-1",
            "event_revision": 2,
            "event_type": "lead_off",
            "status": "resolved",
            "severity": "error",
            "priority": "urgent",
            "updated_at": "2026-07-26T10:00:00.000Z",
            "summary": "Lead contact restored.",
        }
    )

    assert message["event_type"] == "signal.lead_off"
    assert message["transition"] == "resolved"
    assert message["severity"] == "critical"
    assert message["event_revision"] == 2


def test_settings_status_never_contains_ingest_token() -> None:
    settings = DashboardRelaySettings(
        ws_url="wss://heart.example.test/api/ws",
        session_id="sess_live_main_0123456789abcdef",
        ingest_token="secret-token-that-must-not-be-rendered",
        public_url="https://heart.example.test/dashboard",
    )

    settings.validate()

    assert settings.endpoint_label == "wss://heart.example.test/api/ws"
    assert settings.ingest_token not in settings.endpoint_label


def test_worker_reuses_providers_without_opening_an_acquisition_source() -> None:
    messages: list[dict[str, object]] = []
    provider_calls = 0

    class FakeSocket:
        def __init__(self, **_kwargs) -> None:
            self.next_sequence = 0

        def connect(self) -> int:
            return 0

        def send_json(self, message: dict[str, object]) -> None:
            messages.append(message)

        def close(self) -> None:
            return None

    def reader_provider():
        nonlocal provider_calls
        provider_calls += 1
        return None

    stores = SimpleNamespace(
        ecg=SnapshotBuffer(),
        imu=SnapshotBuffer(),
        status=LatestBuffer(),
    )
    worker = DashboardRelayWorker(
        settings=DashboardRelaySettings(
            ws_url="wss://heart.example.test/api/ws",
            session_id="sess_live_main_0123456789abcdef",
            ingest_token="secret-token-that-must-not-be-rendered",
        ),
        stores=stores,
        reader_provider=reader_provider,
        analysis_provider=lambda: None,
        orientation_provider=lambda: None,
        socket_factory=FakeSocket,
    )

    worker.start()
    time.sleep(0.28)
    worker.stop()

    assert provider_calls > 0
    assert messages
    assert {message["type"] for message in messages} == {"telemetry"}
