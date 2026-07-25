from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from smart_neckband.protocol import ECG_SAMPLE_COUNT, encode_ecg_packet
from smart_neckband.sleep_ecg import (
    MIN_RECORDING_SECONDS,
    SleepEcgDemographics,
    SleepEcgEpoch,
    SleepEcgError,
    SleepEcgSignal,
    SleepEcgSource,
    analyze_sleep_ecg,
    load_sleep_ecg_signal,
    probe_sleep_ecg_source,
    summarize_sleep_epochs,
)


def _source(*, sample_rate_hz: float = 10.0, sample_count: int = 6_000):
    return SleepEcgSource(
        path=Path("record.bin"),
        source_type="smart_neckband_raw",
        source_session_id="record-1",
        lead_names=("ECG",),
        default_lead="ECG",
        sample_rate_hz=sample_rate_hz,
        sample_count=sample_count,
        recording_start_time="2026-07-26T23:00:00+08:00",
    )


def test_native_session_probe_and_load(tmp_path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    (session / "session.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": "native-1",
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
    (session / "source.json").write_text(
        json.dumps(
            {
                "license": "Open Data Commons Attribution License v1.0",
                "url": "https://physionet.org/content/slpdb/1.0.0/",
            }
        ),
        encoding="utf-8",
    )

    source = probe_sleep_ecg_source(session)
    signal = load_sleep_ecg_signal(source)

    assert source.source_type == "smart_neckband_session"
    assert source.source_session_id == "native-1"
    assert source.duration_seconds == pytest.approx(0.04)
    assert source.source_license == "Open Data Commons Attribution License v1.0"
    assert source.source_url == "https://physionet.org/content/slpdb/1.0.0/"
    assert signal.samples.dtype == np.float32
    assert signal.samples.tolist() == list(range(2000, 2000 + ECG_SAMPLE_COUNT))


def test_probe_rejects_unknown_file(tmp_path) -> None:
    path = tmp_path / "record.csv"
    path.write_text("ecg\n1\n", encoding="utf-8")

    with pytest.raises(SleepEcgError, match="Unsupported"):
        probe_sleep_ecg_source(path)


def test_summary_calculates_sleep_metrics() -> None:
    stages = ("WAKE", "NREM", "NREM", "WAKE", "REM", "REM")
    epochs = tuple(
        SleepEcgEpoch(
            epoch_index=index,
            start_offset_s=index * 30,
            stage=stage,
            confidence=0.8,
            probabilities={stage: 0.8},
        )
        for index, stage in enumerate(stages)
    )

    summary = summarize_sleep_epochs(epochs)

    assert summary.total_sleep_time_s == 120
    assert summary.sleep_efficiency_percent == pytest.approx(66.667)
    assert summary.sleep_onset_latency_min == 0.5
    assert summary.wake_after_sleep_onset_min == 0.5
    assert summary.awakening_count == 1
    assert summary.stage_transition_count == 3


def test_analyzer_uses_three_class_probabilities_and_warns_on_missing_metadata(
    monkeypatch,
) -> None:
    import sleepecg

    fake_classifier = SimpleNamespace(stages_mode="wake-rem-nrem")
    monkeypatch.setattr(
        sleepecg,
        "detect_heartbeats",
        lambda values, fs: np.arange(0, len(values), int(fs)),
    )
    monkeypatch.setattr(
        sleepecg,
        "load_classifier",
        lambda name, directory: fake_classifier,
    )
    probabilities = np.asarray(
        [
            [0.05, 0.8, 0.1, 0.05],
            [0.05, 0.1, 0.1, 0.75],
            [0.05, 0.1, 0.75, 0.1],
        ]
    )
    monkeypatch.setattr(sleepecg, "stage", lambda *args, **kwargs: probabilities)
    source = _source()
    signal = SleepEcgSignal(
        source=source,
        lead_name="ECG",
        samples=np.sin(np.linspace(0, 20, source.sample_count)).astype(np.float32),
        sample_rate_hz=source.sample_rate_hz,
        recording_start_time=source.recording_start_time,
    )

    result = analyze_sleep_ecg(signal)

    assert [epoch.stage for epoch in result.epochs] == ["WAKE", "NREM", "REM"]
    assert result.summary.total_epoch_count == 3
    assert result.heartbeat_count == MIN_RECORDING_SECONDS
    assert not result.demographics_complete
    assert any("age metadata" in warning for warning in result.warnings)
    assert any("gender metadata" in warning for warning in result.warnings)


def test_analyzer_rejects_short_recording() -> None:
    source = _source(sample_count=5_999)
    signal = SleepEcgSignal(
        source=source,
        lead_name="ECG",
        samples=np.zeros(source.sample_count, dtype=np.float32),
        sample_rate_hz=source.sample_rate_hz,
        recording_start_time=None,
    )

    with pytest.raises(SleepEcgError, match="too short"):
        analyze_sleep_ecg(signal, demographics=SleepEcgDemographics(age=30, gender="male"))


def test_wfdb_probe_detects_multiple_ecg_leads(monkeypatch, tmp_path) -> None:
    header_path = tmp_path / "multi.hea"
    header_path.write_text("placeholder", encoding="utf-8")
    fake_header = SimpleNamespace(
        sig_name=["ECG I", "ECG II", "EEG"],
        fs=250,
        sig_len=2500,
        base_datetime=datetime(2026, 7, 26, 23, 0, 0),
        base_time=None,
    )
    fake_record = SimpleNamespace(
        p_signal=np.column_stack((np.arange(2500),)),
        fs=250,
    )
    fake_wfdb = SimpleNamespace(
        rdheader=lambda _path: fake_header,
        rdrecord=lambda _path, channels: fake_record,
    )
    monkeypatch.setitem(sys.modules, "wfdb", fake_wfdb)

    source = probe_sleep_ecg_source(header_path)
    signal = load_sleep_ecg_signal(source, lead_name="ECG II")

    assert source.lead_names == ("ECG I", "ECG II")
    assert signal.lead_name == "ECG II"
    assert signal.sample_rate_hz == 250


def test_real_edfio_round_trip_detects_and_selects_ecg_leads(tmp_path) -> None:
    edfio = pytest.importorskip("edfio")
    path = tmp_path / "multi.edf"
    samples = np.linspace(-1.0, 1.0, 100, dtype=np.float64)
    edfio.Edf(
        [
            edfio.EdfSignal(samples, 10, label="ECG I"),
            edfio.EdfSignal(samples * 2, 10, label="ECG II"),
            edfio.EdfSignal(samples * 3, 10, label="EEG"),
        ]
    ).write(path)

    source = probe_sleep_ecg_source(path)
    signal = load_sleep_ecg_signal(source, lead_name="ECG II")

    assert source.lead_names == ("ECG I", "ECG II")
    assert source.sample_count == 100
    assert signal.lead_name == "ECG II"
    assert signal.sample_rate_hz == 10
    assert signal.samples == pytest.approx(samples * 2, abs=1e-3)
