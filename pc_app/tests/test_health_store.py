from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import time

import jsonschema
import pytest

from smart_neckband.analysis import EcgAnalysisResult
from smart_neckband.health_contract import (
    definition_schema,
    load_health_contract,
)
from smart_neckband.health_admin import deletion_plan, execute_deletion
from smart_neckband.health_state import BuiltHealthState
from smart_neckband.health_store import HealthStore
from smart_neckband.protocol import ParserStats


def state_document() -> dict:
    return deepcopy(load_health_contract()["x-golden"]["wearer_state_live"])


def built(
    document: dict,
    *,
    now_ns: int,
    status_key: str | None = "status-1",
    clipping_window_full: bool = True,
) -> BuiltHealthState:
    return BuiltHealthState(
        document=deepcopy(document),
        committed_monotonic_ns=now_ns,
        ecg_received_monotonic_ns=now_ns - int(document["age_ms"]) * 1_000_000,
        transport_received_monotonic_ns=now_ns
        - int(document["device"]["last_transport_packet_age_ms"]) * 1_000_000,
        status_evidence_key=status_key,
        clipping_window_full=clipping_window_full,
    )


def invalidate_heart(document: dict, reason: str) -> None:
    heart = document["heart"]
    for name in ("heart_rate", "rr_interval"):
        metric = heart[name]
        metric["value"] = None
        metric["valid"] = False
        metric["observed_at"] = None
        metric["age_ms"] = None
        metric["unavailable_reason"] = reason


def test_first_state_can_open_lead_off_and_enqueue_atomically(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    document = state_document()
    document["signal"].update(
        {
            "lead_off": True,
            "quality_score": 0.1,
            "quality_level": "bad",
            "quality_rank": 1,
        }
    )
    invalidate_heart(document, "lead_off")

    committed = store.commit_state(built(document, now_ns=1_000_000_000))

    assert committed["state_revision"] == 1
    assert [event["event_type"] for event in committed["active_events"]] == [
        "lead_off"
    ]
    event = store.get_event(committed["active_events"][0]["event_id"])
    assert event is not None
    jsonschema.Draft202012Validator(definition_schema("WearerEvent")).validate(
        event
    )
    outbox = store.list_outbox()
    assert len(outbox) == 1
    assert outbox[0]["notification_sequence"] == 1
    assert json.loads(outbox[0]["raw_body"])["transition"] == "opened"


def test_lead_off_resolves_only_after_three_distinct_clear_status_packets(
    tmp_path,
) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    opened = state_document()
    opened["signal"].update(
        {
            "lead_off": True,
            "quality_score": 0.1,
            "quality_level": "bad",
            "quality_rank": 1,
        }
    )
    invalidate_heart(opened, "lead_off")
    state = store.commit_state(built(opened, now_ns=1_000_000_000))
    event_id = state["active_events"][0]["event_id"]

    clear = state_document()
    for index in range(3):
        latest = store.commit_state(
            built(
                clear,
                now_ns=2_000_000_000 + index * 1_000_000_000,
                status_key=f"status-clear-{index}",
            )
        )
        if index < 2:
            assert latest["active_events"]

    assert latest["active_events"] == []
    resolved = store.get_event(event_id)
    assert resolved is not None
    assert resolved["status"] == "resolved"
    assert resolved["event_revision"] == 2
    assert len(store.list_outbox()) == 2

    reopened = store.commit_state(
        built(opened, now_ns=6_000_000_000, status_key="lead-again")
    )
    assert reopened["active_events"][0]["event_id"] != event_id


def test_stale_to_offline_is_one_state_revision_and_ordered_notifications(
    tmp_path,
) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    fresh = state_document()
    store.commit_state(built(fresh, now_ns=1_000_000_000))

    stale = state_document()
    stale["age_ms"] = 2_001
    stale["freshness"] = "stale"
    stale["device"]["last_ecg_packet_age_ms"] = 2_001
    stale["device"]["status"] = "degraded"
    invalidate_heart(stale, "stale_data")
    stale_state = store.commit_state(built(stale, now_ns=4_000_000_000))
    stale_event_id = stale_state["active_events"][0]["event_id"]

    offline = deepcopy(stale)
    offline["age_ms"] = 10_001
    offline["freshness"] = "offline"
    offline["device"]["last_ecg_packet_age_ms"] = 10_001
    offline_state = store.commit_state(built(offline, now_ns=12_000_000_000))

    assert offline_state["state_revision"] == 3
    assert [event["event_type"] for event in offline_state["active_events"]] == [
        "input_offline"
    ]
    assert store.get_event(stale_event_id)["status"] == "resolved"
    outbox_bodies = [json.loads(item["raw_body"]) for item in store.list_outbox()]
    assert [body["notification_sequence"] for body in outbox_bodies] == [1, 2, 3]
    assert [body["transition"] for body in outbox_bodies] == [
        "opened",
        "resolved",
        "opened",
    ]
    assert outbox_bodies[1]["state_revision"] == 3
    assert outbox_bodies[2]["state_revision"] == 3
    assert outbox_bodies[1]["trace_id"] == outbox_bodies[2]["trace_id"]
    assert outbox_bodies[1]["occurred_at"] == outbox_bodies[2]["occurred_at"]


def test_clipping_full_lifecycle_hysteresis_and_reopen_uses_new_id(
    tmp_path,
) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    clipped = state_document()
    clipped["signal"].update(
        {
            "adc_clipping_ratio_10s": 0.8,
            "quality_score": 0.1,
            "quality_level": "bad",
            "quality_rank": 1,
        }
    )
    invalidate_heart(clipped, "adc_clipping")
    opened = store.commit_state(
        built(clipped, now_ns=1_000_000_000, clipping_window_full=True)
    )
    first_id = opened["active_events"][0]["event_id"]

    maintained = deepcopy(clipped)
    maintained["signal"]["adc_clipping_ratio_10s"] = 0.2
    still_active = store.commit_state(
        built(maintained, now_ns=2_000_000_000)
    )
    assert still_active["active_events"][0]["event_id"] == first_id

    clear = state_document()
    store.commit_state(built(clear, now_ns=3_000_000_000))
    almost = store.commit_state(
        built(clear, now_ns=12_999_999_999)
    )
    assert almost["active_events"][0]["event_id"] == first_id
    resolved = store.commit_state(
        built(clear, now_ns=13_000_000_000)
    )
    assert resolved["active_events"] == []
    assert store.get_event(first_id)["status"] == "resolved"

    reopened = store.commit_state(
        built(clipped, now_ns=14_000_000_000, clipping_window_full=True)
    )
    second_id = reopened["active_events"][0]["event_id"]
    assert second_id != first_id


def test_stale_event_resolves_after_fresh_hysteresis_and_reopens(
    tmp_path,
) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    stale = state_document()
    stale["age_ms"] = 2_001
    stale["freshness"] = "stale"
    stale["device"]["last_ecg_packet_age_ms"] = 2_001
    stale["device"]["status"] = "degraded"
    invalidate_heart(stale, "stale_data")
    first = store.commit_state(built(stale, now_ns=3_000_000_000))
    first_id = first["active_events"][0]["event_id"]

    fresh = state_document()
    store.commit_state(built(fresh, now_ns=4_000_000_000))
    almost = store.commit_state(
        built(fresh, now_ns=4_999_999_999)
    )
    assert almost["active_events"][0]["event_id"] == first_id
    resolved = store.commit_state(
        built(fresh, now_ns=5_000_000_000)
    )
    assert resolved["active_events"] == []

    reopened = store.commit_state(
        built(stale, now_ns=6_000_000_000)
    )
    assert reopened["active_events"][0]["event_id"] != first_id


def test_offline_event_resolves_after_fresh_hysteresis_and_reopens(
    tmp_path,
) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    offline = state_document()
    offline["age_ms"] = 10_001
    offline["freshness"] = "offline"
    offline["device"]["last_ecg_packet_age_ms"] = 10_001
    offline["device"]["status"] = "degraded"
    invalidate_heart(offline, "stale_data")
    first = store.commit_state(built(offline, now_ns=11_000_000_000))
    first_id = first["active_events"][0]["event_id"]

    fresh = state_document()
    store.commit_state(built(fresh, now_ns=12_000_000_000))
    resolved = store.commit_state(
        built(fresh, now_ns=13_000_000_000)
    )
    assert resolved["active_events"] == []
    assert store.get_event(first_id)["status"] == "resolved"

    reopened = store.commit_state(
        built(offline, now_ns=24_000_000_000)
    )
    assert reopened["active_events"][0]["event_id"] != first_id


def test_restart_does_not_duplicate_active_event_or_outbox(tmp_path) -> None:
    path = tmp_path / "health.sqlite3"
    stale = state_document()
    stale["age_ms"] = 2_001
    stale["freshness"] = "stale"
    stale["device"]["last_ecg_packet_age_ms"] = 2_001
    stale["device"]["status"] = "degraded"
    invalidate_heart(stale, "stale_data")
    first_store = HealthStore(path)
    first = first_store.commit_state(built(stale, now_ns=4_000_000_000))

    second_store = HealthStore(path)
    second = second_store.commit_state(built(stale, now_ns=5_000_000_000))

    assert second["active_events"][0]["event_id"] == first["active_events"][0]["event_id"]
    assert len(second_store.list_outbox()) == 1


def test_expired_delivery_lease_cannot_let_later_sequence_overtake(
    tmp_path,
) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    opened = state_document()
    opened["signal"].update(
        {
            "lead_off": True,
            "quality_score": 0.1,
            "quality_level": "bad",
            "quality_rank": 1,
        }
    )
    invalidate_heart(opened, "lead_off")
    store.commit_state(built(opened, now_ns=1_000_000_000))
    clear = state_document()
    for index in range(3):
        store.commit_state(
            built(
                clear,
                now_ns=2_000_000_000 + index * 1_000_000_000,
                status_key=f"clear-{index}",
            )
        )

    now = time.time() + 1
    first = store.claim_next_outbox(now_epoch_s=now)
    assert first is not None
    assert first["notification_sequence"] == 1
    reclaimed = store.claim_next_outbox(now_epoch_s=now + 31)
    assert reclaimed is not None
    assert reclaimed["notification_id"] == first["notification_id"]
    assert reclaimed["notification_sequence"] == 1

    store.mark_outbox_delivered(
        first["notification_id"],
        http_status=202,
    )
    second = store.claim_next_outbox(now_epoch_s=now + 31)
    assert second is not None
    assert second["notification_sequence"] == 2


def test_two_dispatchers_cannot_claim_same_or_later_sequence_concurrently(
    tmp_path,
) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    opened = state_document()
    opened["signal"].update(
        {
            "lead_off": True,
            "quality_score": 0.1,
            "quality_level": "bad",
            "quality_rank": 1,
        }
    )
    invalidate_heart(opened, "lead_off")
    store.commit_state(built(opened, now_ns=1_000_000_000))
    now = time.time() + 1

    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(
                lambda _index: store.claim_next_outbox(now_epoch_s=now),
                range(2),
            )
        )

    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0]["notification_sequence"] == 1


def test_recent_events_cursor_is_bound_to_filters(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    lead = state_document()
    lead["signal"].update(
        {
            "lead_off": True,
            "quality_score": 0.1,
            "quality_level": "bad",
            "quality_rank": 1,
        }
    )
    invalidate_heart(lead, "lead_off")
    store.commit_state(built(lead, now_ns=1_000_000_000))

    stale = state_document()
    stale["age_ms"] = 2_001
    stale["freshness"] = "stale"
    stale["device"]["last_ecg_packet_age_ms"] = 2_001
    stale["device"]["status"] = "degraded"
    invalidate_heart(stale, "stale_data")
    store.commit_state(built(stale, now_ns=4_000_000_000))

    events, cursor = store.list_events(wearer_id="xwen", limit=1)
    assert len(events) == 1
    assert cursor is not None
    next_events, _ = store.list_events(
        wearer_id="xwen",
        cursor=cursor,
        limit=1,
    )
    assert len(next_events) == 1

    try:
        store.list_events(
            wearer_id="xwen",
            event_types=["lead_off"],
            cursor=cursor,
            limit=1,
        )
    except ValueError as exc:
        assert "cursor" in str(exc)
    else:
        raise AssertionError("cursor filter mismatch must fail")


def test_retention_deletes_only_expired_terminal_records(tmp_path) -> None:
    db_path = tmp_path / "health.sqlite3"
    store = HealthStore(db_path)
    lead = state_document()
    lead["signal"].update(
        {
            "lead_off": True,
            "quality_score": 0.1,
            "quality_level": "bad",
            "quality_rank": 1,
        }
    )
    invalidate_heart(lead, "lead_off")
    opened = store.commit_state(built(lead, now_ns=1_000_000_000))
    resolved_id = opened["active_events"][0]["event_id"]
    clear = state_document()
    for index in range(3):
        store.commit_state(
            built(
                clear,
                now_ns=2_000_000_000 + index * 1_000_000_000,
                status_key=f"retention-clear-{index}",
            )
        )
    now_epoch = time.time() + 1
    delivered = store.claim_next_outbox(now_epoch_s=now_epoch)
    assert delivered is not None
    store.mark_outbox_delivered(
        delivered["notification_id"],
        http_status=202,
    )
    dead = store.claim_next_outbox(now_epoch_s=now_epoch)
    assert dead is not None
    store.move_outbox_to_dead_letter(
        dead["notification_id"],
        error_category="terminal",
        http_status=400,
    )
    store.record_mcp_audit(
        trace_id="e4ead7b3-2014-47ea-90d2-4c4f4484dca0",
        tool_name="health.get_current_state",
        wearer_id="xwen",
        outcome="success",
        error_code=None,
        latency_ms=1,
    )
    stale = state_document()
    stale["age_ms"] = 2_001
    stale["freshness"] = "stale"
    stale["device"]["last_ecg_packet_age_ms"] = 2_001
    stale["device"]["status"] = "degraded"
    invalidate_heart(stale, "stale_data")
    active = store.commit_state(built(stale, now_ns=6_000_000_000))
    active_id = active["active_events"][0]["event_id"]

    now = datetime(2026, 7, 24, tzinfo=timezone.utc)
    seven_old = (
        now - timedelta(days=7, milliseconds=1)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    thirty_old = (
        now - timedelta(days=30, milliseconds=1)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE health_events SET updated_at=? WHERE event_id=?",
            (seven_old, resolved_id),
        )
        connection.execute(
            "UPDATE health_webhook_deliveries SET delivered_at=?",
            (seven_old,),
        )
        connection.execute(
            "UPDATE health_webhook_dead_letters SET dead_lettered_at=?",
            (thirty_old,),
        )
        connection.execute(
            "UPDATE health_mcp_audit SET created_at=?",
            (thirty_old,),
        )

    counts = store.cleanup_retention(now=now)

    assert counts == {
        "resolved_events": 1,
        "deliveries": 1,
        "dead_letters": 1,
        "mcp_audit": 1,
        "metric_samples": 0,
        "rr_intervals": 0,
    }
    assert store.get_event(active_id)["status"] == "active"
    assert store.list_outbox()


def test_local_admin_deletion_requires_current_count_confirmation(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    lead = state_document()
    lead["signal"].update(
        {
            "lead_off": True,
            "quality_score": 0.1,
            "quality_level": "bad",
            "quality_rank": 1,
        }
    )
    invalidate_heart(lead, "lead_off")
    committed = store.commit_state(built(lead, now_ns=1_000_000_000))
    event_id = committed["active_events"][0]["event_id"]
    store.commit_state(
        built(
            state_document(),
            now_ns=2_000_000_000,
            status_key="deletion-clear-1",
        )
    )
    store.record_mcp_audit(
        trace_id="7a916c4a-3b3e-4ec5-8491-e5fc7e843863",
        tool_name="health.get_current_state",
        wearer_id="xwen",
        outcome="success",
        error_code=None,
        latency_ms=1,
    )
    raw_session = tmp_path / "raw.bin"
    raw_session.write_bytes(b"raw evidence")
    before = "9999-12-31T23:59:59.999Z"
    plan = deletion_plan(store, wearer_id="xwen", before_utc=before)

    with pytest.raises(ValueError):
        execute_deletion(
            store,
            wearer_id="xwen",
            before_utc=before,
            confirmation_token="0" * 64,
        )
    deleted = execute_deletion(
        store,
        wearer_id="xwen",
        before_utc=before,
        confirmation_token=plan["confirmation_token"],
    )

    assert deleted == plan["counts"]
    assert deleted["event_gates"] > 0
    assert store.get_state("xwen") is None
    assert store.get_event(event_id) is None
    assert store.list_outbox() == []
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM health_event_gates WHERE wearer_id='xwen'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM health_wearer_sequences WHERE wearer_id='xwen'"
        ).fetchone()[0] == 1
    assert raw_session.read_bytes() == b"raw evidence"


def test_local_observability_is_complete_and_excludes_sensitive_payloads(
    tmp_path,
) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    document = state_document()
    store.commit_state(built(document, now_ns=2_000_000_000))
    store.save_runtime_observability(
        wearer_id="xwen",
        parser_stats=ParserStats(
            packets_ok=12,
            packets_lost=2,
            crc_errors=1,
        ),
        analysis=EcgAnalysisResult(
            timestamp_s=2.0,
            raw=(),
            cleaned=(),
            r_peak_indices=(),
            heart_rate_bpm=None,
            latest_rr_ms=None,
            signal_quality=None,
            message="ok",
            source_instance_id=str(document["source_instance_id"]),
            analyzed_through_ecg_sample_index=99,
            analyzed_through_received_monotonic_ns=1_900_000_000,
        ),
    )
    store.record_mcp_audit(
        trace_id="05d266d0-5da1-487c-8300-2aa05af71d6d",
        tool_name="health.get_current_state",
        wearer_id="xwen",
        outcome="error",
        error_code="STATE_STALE",
        latency_ms=7,
    )

    status = store.get_observability(
        wearer_id="xwen",
        now_monotonic_ns=2_100_000_000,
        now=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )

    assert status["database"]["migration_version"] == 5
    assert status["state"]["revision"] == 1
    assert status["state"]["source_instance_id"] == document["source_instance_id"]
    assert status["state"]["age_ms"] == 220
    assert status["events"] == {
        "opened_total": 0,
        "active_count": 0,
        "resolved_total": 0,
    }
    assert status["outbox"] == {
        "pending_count": 0,
        "oldest_age_ms": None,
    }
    assert status["parser"] == {
        "packets_ok": 12,
        "packets_lost": 2,
        "crc_errors": 1,
    }
    assert status["analysis"]["message"] == "ok"
    assert status["analysis"]["evidence_age_ms"] == 200
    assert status["analysis"]["last_success_age_ms"] == 200
    assert status["mcp"] == {
        "call_count": 1,
        "average_latency_ms": 7,
        "max_latency_ms": 7,
        "error_count": 1,
        "last_error_code": "STATE_STALE",
    }
    serialized = json.dumps(status)
    for forbidden in (
        "raw_body",
        "webhook_secret",
        "signature",
        "heart_rate",
        "rr_interval",
        "quality_score",
    ):
        assert forbidden not in serialized
