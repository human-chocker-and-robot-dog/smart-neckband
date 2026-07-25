from __future__ import annotations

from datetime import datetime, timezone

import jsonschema

from smart_neckband.health_mcp import HealthToolService
from smart_neckband.health_store import HealthStore


NOW = "2026-07-26T16:00:00.000Z"


def service(store: HealthStore) -> HealthToolService:
    return HealthToolService(
        store=store,
        configured_wearer_ids={"xwen"},
        monotonic_ns=lambda: 1_000_000_000,
        utc_now=lambda: NOW,
    )


def seed_completed_report(store: HealthStore, record_id: str = "sleep-1") -> None:
    store.create_sleep_record(
        wearer_id="xwen",
        source_type="wfdb",
        source_session_id="slp03",
        source_sha256="b" * 64,
        display_name="SLPDB slp03",
        source_name="slp03",
        lead_name="ECG",
        sample_rate_hz=250,
        sample_count=5_400_000,
        duration_s=21_600,
        recording_start_time="1989-09-02T23:12:00",
        demographics={"age": 51, "gender": "male"},
        provenance={"doi": "10.13026/C23K5S"},
        sleep_record_id=record_id,
        imported_at="2026-07-26T15:00:00.000Z",
    )
    run_id = store.start_sleep_analysis_run(
        sleep_record_id=record_id,
        wearer_id="xwen",
        model_name="wrn-gru-mesa-weighted",
        parameters={"epoch_duration_s": 30},
        analysis_run_id=f"run-{record_id}",
        started_at="2026-07-26T15:01:00.000Z",
    )
    summary = {
        "epoch_duration_s": 30,
        "total_epoch_count": 2,
        "valid_epoch_count": 2,
        "analyzed_duration_s": 60,
        "total_sleep_time_s": 30,
        "sleep_efficiency_percent": 50.0,
        "sleep_onset_latency_min": 0.5,
        "wake_after_sleep_onset_min": 0.0,
        "awakening_count": 0,
        "stage_transition_count": 1,
        "mean_confidence": 0.85,
        "stage_duration_s": {
            "UNDEFINED": 0,
            "WAKE": 30,
            "REM": 0,
            "NREM": 30,
        },
        "stage_percent": {
            "UNDEFINED": 0.0,
            "WAKE": 50.0,
            "REM": 0.0,
            "NREM": 50.0,
        },
    }
    epochs = [
        {
            "epoch_index": 0,
            "start_offset_s": 0,
            "stage": "WAKE",
            "confidence": 0.9,
            "probabilities": {
                "UNDEFINED": 0.0,
                "WAKE": 0.9,
                "REM": 0.05,
                "NREM": 0.05,
            },
            "reference_stage": "WAKE",
        },
        {
            "epoch_index": 1,
            "start_offset_s": 30,
            "stage": "NREM",
            "confidence": 0.8,
            "probabilities": {
                "UNDEFINED": 0.0,
                "WAKE": 0.1,
                "REM": 0.1,
                "NREM": 0.8,
            },
            "reference_stage": "NREM",
        },
    ]
    store.complete_sleep_analysis_run(
        analysis_run_id=run_id,
        stages_mode="wake-rem-nrem",
        sleepecg_version="0.5.9",
        tensorflow_version="2.21.0",
        heartbeat_count=20_000,
        summary=summary,
        quality={"demographics_complete": True, "warnings": []},
        epochs=epochs,
        completed_at="2026-07-26T15:02:00.000Z",
    )


def validate(tool_service: HealthToolService, result) -> None:
    jsonschema.Draft202012Validator(
        tool_service.tools["health.get_sleep_report"]["outputSchema"]
    ).validate(result.envelope)


def test_sleep_report_returns_latest_completed_without_raw_ecg(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    seed_completed_report(store)
    tool_service = service(store)

    result = tool_service.call("health.get_sleep_report", {})

    assert not result.is_error
    assert result.envelope["data"]["record"]["sleep_record_id"] == "sleep-1"
    assert (
        result.envelope["data"]["record"]["recording_end_time"]
        == "1989-09-03T05:12:00"
    )
    assert result.envelope["data"]["epochs"] == []
    assert "raw" not in str(result.envelope).lower()
    assert result.envelope["meta"] == {
        "schema_version": "0.4.0",
        "generated_at": NOW,
        "wearer_id": "xwen",
        "trace_id": result.envelope["meta"]["trace_id"],
    }
    validate(tool_service, result)


def test_sleep_report_supports_explicit_record_and_epoch_page(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    seed_completed_report(store)
    tool_service = service(store)

    result = tool_service.call(
        "health.get_sleep_report",
        {
            "sleep_record_id": "sleep-1",
            "include_epochs": True,
            "epoch_offset": 1,
            "epoch_limit": 1,
        },
    )

    assert [epoch["epoch_index"] for epoch in result.envelope["data"]["epochs"]] == [1]
    assert result.envelope["data"]["pagination"]["total"] == 2
    validate(tool_service, result)


def test_sleep_report_distinguishes_missing_not_ready_and_failed(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    tool_service = service(store)

    missing = tool_service.call(
        "health.get_sleep_report", {"sleep_record_id": "missing"}
    )
    assert missing.envelope["error"]["code"] == "SLEEP_RECORD_NOT_FOUND"

    record_id = store.create_sleep_record(
        wearer_id="xwen",
        source_type="wfdb",
        source_session_id="pending",
        source_sha256="c" * 64,
        display_name="Pending",
        source_name="pending",
        lead_name="ECG",
        sample_rate_hz=250,
        sample_count=150_000,
        duration_s=600,
        recording_start_time=None,
        demographics={},
        provenance={},
        sleep_record_id="pending",
    )
    run_id = store.start_sleep_analysis_run(
        sleep_record_id=record_id,
        wearer_id="xwen",
        model_name="wrn-gru-mesa-weighted",
        parameters={},
    )
    pending = tool_service.call(
        "health.get_sleep_report", {"sleep_record_id": "pending"}
    )
    assert pending.envelope["error"]["code"] == "SLEEP_ANALYSIS_NOT_READY"

    store.fail_sleep_analysis_run(
        analysis_run_id=run_id,
        error_message="failure",
    )
    failed = tool_service.call(
        "health.get_sleep_report", {"sleep_record_id": "pending"}
    )
    assert failed.envelope["error"]["code"] == "SLEEP_ANALYSIS_FAILED"
    validate(tool_service, missing)
    validate(tool_service, pending)
    validate(tool_service, failed)


def test_sleep_report_input_is_closed_and_bounded(tmp_path) -> None:
    tool_service = service(HealthStore(tmp_path / "health.sqlite3"))

    invalid = tool_service.call(
        "health.get_sleep_report",
        {"include_epochs": True, "epoch_limit": 241},
    )

    assert invalid.is_error
    assert invalid.envelope["error"]["code"] == "INVALID_ARGUMENT"
    validate(tool_service, invalid)
