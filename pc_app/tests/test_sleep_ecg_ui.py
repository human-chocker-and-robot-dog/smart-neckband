from __future__ import annotations

import json
import os
from pathlib import Path

from smart_neckband.protocol import ECG_SAMPLE_COUNT, encode_ecg_packet
from smart_neckband.sleep_ecg import (
    SleepEcgAnalysisResult,
    SleepEcgEpoch,
    SleepEcgSummary,
    StoredSleepEcgAnalysis,
)


def stored_result() -> StoredSleepEcgAnalysis:
    epochs = (
        SleepEcgEpoch(
            epoch_index=0,
            start_offset_s=0,
            stage="WAKE",
            confidence=0.9,
            probabilities={"UNDEFINED": 0.0, "WAKE": 0.9, "REM": 0.05, "NREM": 0.05},
            reference_stage="WAKE",
        ),
        SleepEcgEpoch(
            epoch_index=1,
            start_offset_s=30,
            stage="NREM",
            confidence=0.8,
            probabilities={"UNDEFINED": 0.0, "WAKE": 0.1, "REM": 0.1, "NREM": 0.8},
            reference_stage="NREM",
        ),
    )
    summary = SleepEcgSummary(
        epoch_duration_s=30,
        total_epoch_count=2,
        valid_epoch_count=2,
        analyzed_duration_s=60,
        total_sleep_time_s=30,
        sleep_efficiency_percent=50.0,
        sleep_onset_latency_min=0.5,
        wake_after_sleep_onset_min=0.0,
        awakening_count=0,
        stage_transition_count=1,
        mean_confidence=0.85,
        stage_duration_s={"UNDEFINED": 0, "WAKE": 30, "REM": 0, "NREM": 30},
        stage_percent={"UNDEFINED": 0.0, "WAKE": 50.0, "REM": 0.0, "NREM": 50.0},
    )
    return StoredSleepEcgAnalysis(
        sleep_record_id="record-1",
        analysis_run_id="run-1",
        result=SleepEcgAnalysisResult(
            model_name="wrn-gru-mesa-weighted",
            stages_mode="wake-rem-nrem",
            sleepecg_version="0.5.9",
            tensorflow_version="2.21.0",
            heartbeat_count=600,
            demographics_complete=False,
            warnings=("age metadata is missing",),
            summary=summary,
            epochs=epochs,
            completed_at="2026-07-26T16:00:00.000Z",
        ),
    )


def write_native_session(tmp_path: Path) -> Path:
    session = tmp_path / "session"
    session.mkdir()
    (session / "session.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": "night-1",
                "ecg_sample_rate_hz": 500,
                "sample_count": ECG_SAMPLE_COUNT,
                "started_at": "2026-07-26T23:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    (session / "raw.bin").write_bytes(
        encode_ecg_packet(
            packet_sequence=1,
            timestamp_us=0,
            first_sample_index=0,
            samples=tuple(2000 + index for index in range(ECG_SAMPLE_COUNT)),
        )
    )
    return session


def test_sleep_ecg_panel_loads_source_runs_worker_and_renders_result(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    import pyqtgraph as pg
    from PySide6 import QtCore, QtWidgets

    from smart_neckband.sleep_ecg_ui import SleepEcgPanel

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    callbacks: list[object] = []
    calls: list[dict[str, object]] = []

    class Settings:
        wearer_id = "xwen"
        db_path = tmp_path / "health.sqlite3"

        def validate_health(self) -> None:
            return None

    def fake_execute(**kwargs):
        calls.append(kwargs)
        kwargs["progress"](50, "predicting")
        return stored_result()

    panel = SleepEcgPanel(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        pg=pg,
        settings_provider=lambda: Settings(),
        post_gui=callbacks.append,
        execute=fake_execute,
        store_factory=lambda _path: object(),
    )
    source = panel.load_path(write_native_session(tmp_path))

    assert source.source_session_id == "night-1"
    assert panel.lead_combo.currentText() == "ECG"
    assert "500 Hz" in panel.source_info.text()

    panel.start_analysis()
    assert panel.worker is not None
    panel.worker.join(timeout=5)
    while callbacks:
        callbacks.pop(0)()
    app.processEvents()

    assert calls[0]["wearer_id"] == "xwen"
    assert panel.last_result is not None
    assert "record-1" in panel.status.text()
    assert "睡眠效率 50.0%" in panel.summary.text()
    assert panel.epoch_table.rowCount() == 2
    assert panel.epoch_table.item(1, 2).text() == "NREM"
    assert panel.epoch_table.item(1, 3).text() == "NREM"
    assert "Cohen's kappa" in panel.summary.text()
    assert "混淆矩阵" in panel.summary.text()
    panel.close()


def test_sleep_ecg_panel_requires_valid_health_settings(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    import pyqtgraph as pg
    from PySide6 import QtCore, QtWidgets

    from smart_neckband.sleep_ecg_ui import SleepEcgPanel

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None

    class InvalidSettings:
        db_path = Path("health.sqlite3")
        wearer_id = ""

        def validate_health(self) -> None:
            raise ValueError("wearer_id required")

    panel = SleepEcgPanel(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        pg=pg,
        settings_provider=lambda: InvalidSettings(),
        post_gui=lambda callback: callback(),
    )
    panel.source = object()  # type: ignore[assignment]
    panel.start_analysis()

    assert "Health 数据库配置无效" in panel.status.text()
    panel.close()
