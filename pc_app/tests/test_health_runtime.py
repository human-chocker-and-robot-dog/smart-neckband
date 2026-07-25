from __future__ import annotations

from types import SimpleNamespace

import pytest

from smart_neckband.health_runtime import HealthRuntimeWorker
from smart_neckband.health_store import HealthStore
from smart_neckband.protocol import EcgPayload, PacketHeader, ParserStats
from smart_neckband.serial_io import PcDataStores, SerialRuntimeStatus
from smart_neckband.source_coordinator import EcgBatchOrdinal, PacketReceipt


SOURCE_ID = "ef132c67-a98f-474a-a673-4ab6ea784790"


def make_runtime() -> SerialRuntimeStatus:
    return SerialRuntimeStatus(
        port="COM19",
        started_at_monotonic_s=0.0,
        serial_open=True,
        packet_count=1,
        ecg_packet_count=1,
        last_packet_monotonic_s=1.0,
        last_ecg_sample_index=19,
        source_instance_id=SOURCE_ID,
        last_transport_packet_monotonic_ns=1_000_000_000,
        last_transport_packet_received_at_utc="2026-07-24T00:00:00.000Z",
        last_ecg_packet_monotonic_ns=1_000_000_000,
        last_ecg_packet_received_at_utc="2026-07-24T00:00:00.000Z",
        last_status_packet_monotonic_ns=None,
        last_status_packet_received_at_utc=None,
        last_ecg_sample_ordinal=19,
        last_ecg_raw_sample_index=19,
        last_error=None,
    )


def test_runtime_worker_persists_state_then_marks_disconnect_offline(tmp_path) -> None:
    stores = PcDataStores.create()
    stores.ecg.append_batch(
        PacketHeader(
            packet_type=1,
            payload_length=0,
            packet_sequence=1,
            timestamp_us=0,
        ),
        EcgPayload(first_sample_index=0, samples=(2048,) * 20),
        source_instance_id=SOURCE_ID,
        receipt=PacketReceipt(
            received_monotonic_ns=1_000_000_000,
            received_at_utc="2026-07-24T00:00:00.000Z",
        ),
        ordinal=EcgBatchOrdinal(
            raw_first_sample_index=0,
            raw_last_sample_index=19,
            first_sample_ordinal=0,
            last_sample_ordinal=19,
        ),
    )
    current_reader = [
        SimpleNamespace(
            runtime_status=make_runtime(),
            stats=ParserStats(packets_ok=1),
            health_transport="spp",
        )
    ]
    store = HealthStore(tmp_path / "health.sqlite3")
    worker = HealthRuntimeWorker(
        wearer_id="xwen",
        stores=stores,
        reader_provider=lambda: current_reader[0] if current_reader else None,
        analysis_provider=lambda: None,
        store=store,
    )

    assert worker.update_once(now_monotonic_ns=1_100_000_000)
    first = store.get_state("xwen")
    assert first["state"]["freshness"] == "fresh"

    current_reader.clear()
    assert worker.update_once(now_monotonic_ns=1_200_000_000)
    latest = store.get_state("xwen")["state"]
    assert latest["freshness"] == "offline"
    assert latest["device"]["status"] == "offline"
    assert [event["event_type"] for event in latest["active_events"]] == [
        "input_offline"
    ]


def test_environment_requires_wearer_and_complete_webhook_configuration(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("SMART_COLLAR_WEARER_ID", raising=False)
    assert (
        HealthRuntimeWorker.from_environment(
            stores=PcDataStores.create(),
            reader_provider=lambda: None,
            analysis_provider=lambda: None,
        )
        is None
    )

    monkeypatch.setenv("SMART_COLLAR_WEARER_ID", "xwen")
    monkeypatch.setenv("SMART_COLLAR_HEALTH_DB_PATH", str(tmp_path / "health.db"))
    monkeypatch.setenv(
        "SMART_COLLAR_HEALTH_WEBHOOK_URL",
        "http://127.0.0.1:8766/v1/health-events",
    )
    monkeypatch.delenv("SMART_COLLAR_HEALTH_WEBHOOK_KEY_ID", raising=False)
    monkeypatch.delenv("SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX", raising=False)

    try:
        HealthRuntimeWorker.from_environment(
            stores=PcDataStores.create(),
            reader_provider=lambda: None,
            analysis_provider=lambda: None,
        )
    except ValueError as exc:
        assert "configured together" in str(exc)
    else:
        raise AssertionError("partial webhook configuration must fail closed")

    monkeypatch.setenv("SMART_COLLAR_WEARER_ID", "contains space")
    monkeypatch.delenv("SMART_COLLAR_HEALTH_WEBHOOK_URL", raising=False)
    with pytest.raises(ValueError, match="wearer_id must match"):
        HealthRuntimeWorker.from_environment(
            stores=PcDataStores.create(),
            reader_provider=lambda: None,
            analysis_provider=lambda: None,
        )


def test_transport_without_ecg_saves_device_only_and_opens_no_event(
    tmp_path,
) -> None:
    runtime = make_runtime()
    runtime = SerialRuntimeStatus(
        port=runtime.port,
        started_at_monotonic_s=runtime.started_at_monotonic_s,
        serial_open=True,
        packet_count=3,
        ecg_packet_count=0,
        last_packet_monotonic_s=1.0,
        last_ecg_sample_index=None,
        source_instance_id=runtime.source_instance_id,
        last_transport_packet_monotonic_ns=1_000_000_000,
        last_transport_packet_received_at_utc=(
            "2026-07-24T00:00:00.000Z"
        ),
        last_ecg_packet_monotonic_ns=None,
        last_ecg_packet_received_at_utc=None,
        last_status_packet_monotonic_ns=1_000_000_000,
        last_status_packet_received_at_utc=(
            "2026-07-24T00:00:00.000Z"
        ),
        last_ecg_sample_ordinal=None,
        last_ecg_raw_sample_index=None,
        last_error=None,
    )
    reader = SimpleNamespace(
        runtime_status=runtime,
        stats=ParserStats(packets_ok=3),
        health_transport="spp",
    )
    store = HealthStore(tmp_path / "health.sqlite3")
    worker = HealthRuntimeWorker(
        wearer_id="xwen",
        stores=PcDataStores.create(),
        reader_provider=lambda: reader,
        analysis_provider=lambda: None,
        store=store,
    )

    assert not worker.update_once(now_monotonic_ns=1_100_000_000)
    assert store.get_state("xwen") is None
    assert store.get_device_snapshot("xwen") is not None
    assert store.list_events(wearer_id="xwen")[0] == []
    assert store.list_outbox() == []
    observability = store.get_observability(
        wearer_id="xwen",
        now_monotonic_ns=1_100_000_000,
    )
    assert observability["state"]["available"] is False
    assert observability["parser"]["packets_ok"] == 3
    assert observability["analysis"]["message"] == "no_analysis"
