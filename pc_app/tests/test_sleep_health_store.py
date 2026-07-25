from __future__ import annotations

import sqlite3

import pytest

from smart_neckband.health_store import HealthStore


def create_record(store: HealthStore, *, record_id: str = "record-1") -> str:
    return store.create_sleep_record(
        wearer_id="xwen",
        source_type="smart_neckband_session",
        source_session_id="session-1",
        source_sha256="a" * 64,
        display_name="Night one",
        source_name="session-1",
        lead_name="ECG",
        sample_rate_hz=500,
        sample_count=900_000,
        duration_s=1800,
        recording_start_time="2026-07-26T23:00:00+08:00",
        demographics={"age": 30, "gender": "male"},
        provenance={"license": "local"},
        sleep_record_id=record_id,
        imported_at="2026-07-26T15:00:00.000Z",
    )


def epoch(index: int, stage: str = "NREM") -> dict[str, object]:
    return {
        "epoch_index": index,
        "start_offset_s": index * 30,
        "stage": stage,
        "confidence": 0.8,
        "probabilities": {
            "UNDEFINED": 0.0,
            "WAKE": 0.1,
            "REM": 0.1,
            "NREM": 0.8,
        },
        "reference_stage": "NREM",
    }


def complete_run(store: HealthStore, record_id: str = "record-1") -> str:
    run_id = store.start_sleep_analysis_run(
        sleep_record_id=record_id,
        wearer_id="xwen",
        model_name="wrn-gru-mesa-weighted",
        parameters={"epoch_duration_s": 30},
        analysis_run_id=f"run-{record_id}",
        started_at="2026-07-26T15:01:00.000Z",
    )
    store.complete_sleep_analysis_run(
        analysis_run_id=run_id,
        stages_mode="wake-rem-nrem",
        sleepecg_version="0.5.9",
        tensorflow_version="2.21.0",
        heartbeat_count=1800,
        summary={"total_sleep_time_s": 60},
        quality={"warnings": []},
        epochs=[epoch(0, "WAKE"), epoch(1), epoch(2, "REM")],
        completed_at="2026-07-26T15:02:00.000Z",
    )
    return run_id


def test_schema_migrates_to_version_five(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")

    with sqlite3.connect(store.path) as connection:
        version = connection.execute(
            "SELECT migration_version FROM health_schema WHERE id=1"
        ).fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert version == 5
    assert {
        "health_sleep_records",
        "health_sleep_analysis_runs",
        "health_sleep_epochs",
    } <= tables


def test_completed_sleep_run_is_queryable_with_bounded_epochs(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    create_record(store)
    complete_run(store)

    report = store.get_sleep_report(
        wearer_id="xwen",
        include_epochs=True,
        epoch_offset=1,
        epoch_limit=1,
    )

    assert report is not None
    assert report["record"]["sleep_record_id"] == "record-1"
    assert report["analysis"]["status"] == "completed"
    assert report["analysis"]["summary"]["total_sleep_time_s"] == 60
    assert [item["epoch_index"] for item in report["epochs"]] == [1]
    assert report["pagination"] == {
        "offset": 1,
        "limit": 1,
        "returned": 1,
        "total": 3,
        "next_offset": 2,
    }


def test_failed_or_running_sleep_run_is_not_a_completed_report(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    create_record(store)
    run_id = store.start_sleep_analysis_run(
        sleep_record_id="record-1",
        wearer_id="xwen",
        model_name="wrn-gru-mesa-weighted",
        parameters={},
    )

    assert store.get_sleep_report(wearer_id="xwen") is None
    assert store.get_sleep_analysis_status(
        wearer_id="xwen", sleep_record_id="record-1"
    ) == "running"

    store.fail_sleep_analysis_run(
        analysis_run_id=run_id,
        error_message="model error",
    )

    assert store.get_sleep_report(wearer_id="xwen") is None
    assert store.get_sleep_analysis_status(
        wearer_id="xwen", sleep_record_id="record-1"
    ) == "failed"


def test_epoch_failure_rolls_back_completion_transaction(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    create_record(store)
    run_id = store.start_sleep_analysis_run(
        sleep_record_id="record-1",
        wearer_id="xwen",
        model_name="wrn-gru-mesa-weighted",
        parameters={},
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.complete_sleep_analysis_run(
            analysis_run_id=run_id,
            stages_mode="wake-rem-nrem",
            sleepecg_version="0.5.9",
            tensorflow_version="2.21.0",
            heartbeat_count=100,
            summary={},
            quality={},
            epochs=[epoch(0, "INVALID")],
        )

    with sqlite3.connect(store.path) as connection:
        status = connection.execute(
            "SELECT status FROM health_sleep_analysis_runs WHERE analysis_run_id=?",
            (run_id,),
        ).fetchone()[0]
        count = connection.execute(
            "SELECT COUNT(*) FROM health_sleep_epochs WHERE analysis_run_id=?",
            (run_id,),
        ).fetchone()[0]
    assert status == "running"
    assert count == 0


def test_wearer_deletion_cascades_sleep_runs_and_epochs(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    create_record(store)
    complete_run(store)
    counts = store.deletion_counts(
        wearer_id="xwen",
        before_utc="2026-07-27T00:00:00.000Z",
    )

    assert counts["sleep_records"] == 1
    deleted = store.delete_wearer_records(
        wearer_id="xwen",
        before_utc="2026-07-27T00:00:00.000Z",
        expected_counts=counts,
    )

    assert deleted["sleep_records"] == 1
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM health_sleep_records").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM health_sleep_analysis_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM health_sleep_epochs").fetchone()[0] == 0
