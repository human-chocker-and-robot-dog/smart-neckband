from __future__ import annotations

from copy import deepcopy
import json

from smart_neckband.analysis import RrIntervalObservation
from smart_neckband.health_contract import load_health_contract
from smart_neckband.health_motion import MotionAnalysis, MotionBucket
from smart_neckband.health_rules import (
    HealthAlertRule,
    HealthRuleCondition,
    load_health_rules,
)
from smart_neckband.health_state import BuiltHealthState
from smart_neckband.health_store import HealthStore


SOURCE = "ef132c67-a98f-474a-a673-4ab6ea784790"


def rule() -> HealthAlertRule:
    return HealthAlertRule(
        rule_id="low_motion_high_hr",
        event_type="cardio.high_hr_low_motion",
        summary="Heart rate stayed high while recent motion remained low.",
        severity="warning",
        priority="urgent",
        conditions=(
            HealthRuleCondition("motion_score", "lte", 20),
            HealthRuleCondition("heart_rate_bpm", "gte", 110),
        ),
        for_s=0,
        clear_for_s=0,
        cooldown_s=300,
        minimum_signal_quality=0.7,
        recommended_capabilities=("health.inspect_recent_metrics",),
        recommended_window_s=30,
    )


def built(*, heart_rate: float, motion_score: float, revision: int) -> BuiltHealthState:
    state = deepcopy(load_health_contract()["x-golden"]["wearer_state_live"])
    state["state_revision"] = 0
    state["source_instance_id"] = SOURCE
    state["heart"]["heart_rate"].update(
        {"value": heart_rate, "valid": True, "unavailable_reason": None}
    )
    state["signal"].update(
        {
            "quality_score": 0.9,
            "quality_level": "good",
            "quality_rank": 3,
            "lead_off": False,
            "adc_clipping_ratio_10s": 0.0,
        }
    )
    motion = MotionAnalysis(
        score=motion_score,
        still_ratio_percent=90.0,
        level="light",
        coverage_ratio=1.0,
        observed_at="2026-07-25T12:00:00.000Z",
        received_monotonic_ns=revision * 1_000_000_000,
        window_s=30.0,
        method="mpu6050_accel_gyro_activity_v1",
        buckets=(MotionBucket(0, motion_score),),
        unavailable_reason=None,
    )
    rr = tuple(
        RrIntervalObservation(
            source_instance_id=SOURCE,
            end_sample_index=revision * 100 + index,
            observed_at=f"2026-07-25T12:00:{index:02d}.000Z",
            received_monotonic_ns=revision * 1_000_000_000,
            rr_ms=800.0 + (index % 2) * 10,
        )
        for index in range(8)
    )
    return BuiltHealthState(
        document=state,
        committed_monotonic_ns=revision * 1_000_000_000,
        ecg_received_monotonic_ns=revision * 1_000_000_000,
        transport_received_monotonic_ns=revision * 1_000_000_000,
        status_evidence_key=f"status-{revision}",
        clipping_window_full=True,
        motion_analysis=motion,
        rr_intervals=rr,
    )


def test_rule_opens_signed_bridge_snapshot_and_resolves_without_spam(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3", alert_rules=(rule(),))

    opened = store.commit_state(built(heart_rate=118, motion_score=10, revision=1))

    active = [event for event in opened["active_events"] if event["event_type"].startswith("cardio.")]
    assert len(active) == 1
    outbox = store.list_outbox()
    assert len(outbox) == 1
    payload = json.loads(outbox[0]["raw_body"])
    assert payload["schema_version"] == "0.3.0"
    assert payload["event_type"] == "cardio.high_hr_low_motion"
    assert payload["evidence"]["heart_rate_bpm"] == 118
    assert payload["evidence"]["motion_score_30s"] == 10
    assert payload["recommended_capabilities"] == ["health.inspect_recent_metrics"]

    resolved = store.commit_state(built(heart_rate=80, motion_score=10, revision=2))
    assert not any(
        event["event_type"] == "cardio.high_hr_low_motion"
        for event in resolved["active_events"]
    )
    assert len(store.list_outbox()) == 2

    store.commit_state(built(heart_rate=118, motion_score=10, revision=3))
    assert len(store.list_outbox()) == 2


def test_rule_loader_accepts_disabled_example_and_enabled_copy(tmp_path) -> None:
    example = json.loads(
        (__import__("pathlib").Path(__file__).resolve().parents[2]
         / "config" / "health_rules.example.json").read_text(encoding="utf-8")
    )
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(example), encoding="utf-8")
    assert load_health_rules(path) == ()

    example["rules"][0]["enabled"] = True
    path.write_text(json.dumps(example), encoding="utf-8")
    loaded = load_health_rules(path)
    assert len(loaded) == 1
    assert loaded[0].event_type == "cardio.high_hr_low_motion"
