import json
from types import SimpleNamespace
import numpy as np
import neurokit2 as nk
import pytest
import collar_engine
from collar_engine import Engine
from smart_neckband.analysis import analyze_recent_ecg


def append_ecg(engine, first, samples, flags=0, timestamp=None):
    engine.append(json.dumps(dict(kind="ecg", first=first, timestamp=first * 2000 if timestamp is None else timestamp,
                                  flags=flags, samples=list(map(int, samples)))))


def test_real_neurokit_adapter_matches_pc_and_preserves_raw():
    signal = nk.ecg_simulate(duration=10, sampling_rate=500, heart_rate=72, noise=0.01, random_state=42)
    raw = (2048 + signal * 500).astype(int)
    engine = Engine()
    for offset in range(0, len(raw), 20):
        append_ecg(engine, offset, raw[offset:offset + 20])
    before = tuple(engine.ecg)
    expected = analyze_recent_ecg(before)
    actual = json.loads(engine.analyze())
    assert tuple(engine.ecg) == before
    assert actual["cleaned"] == pytest.approx(expected.cleaned[-1250:][::4])
    assert actual["quality"] == pytest.approx(expected.signal_quality)
    assert actual["bpm"] == pytest.approx(expected.heart_rate_bpm)
    assert 65 < actual["bpm"] < 80
    assert actual["rmssd"] is None  # No adequate quiet window yet.


@pytest.mark.parametrize("flag", [1, 2, 4, 8, 16, 32, 64])
def test_bad_data_cannot_generate_live_metrics(flag):
    engine = Engine()
    append_ecg(engine, 0, [2048] * 5000, flag)
    result = json.loads(engine.analyze())
    assert result["bpm"] is None
    assert result["rmssd"] is None
    assert len(engine.ecg) == 5000  # Bad raw data retained in its own ring.


def test_gap_historical_and_staleness_clear_rr():
    engine = Engine()
    append_ecg(engine, 0, [2048] * 100)
    engine.rr.append((1234, 800))
    append_ecg(engine, 120, [2048] * 20)
    assert len(engine.ecg) == 20 and not engine.rr
    append_ecg(engine, 140, [2048] * 20, 128)
    assert not engine.ecg
    engine.last_ecg_ns = 1
    result = json.loads(engine.analyze())
    assert result["bpm"] is None and result["raw"] == []


def test_hrv_deduplicates_overlapping_windows_and_requires_60_seconds(monkeypatch):
    def fake_analyze(samples):
        peaks = tuple(i for i, s in enumerate(samples) if s.sample_index % 400 == 0)
        return SimpleNamespace(signal_quality=.9, heart_rate_bpm=75, cleaned=[0.0] * len(samples),
                               message="ok", r_peak_indices=peaks)
    monkeypatch.setattr(collar_engine, "analyze_recent_ecg", fake_analyze)
    engine = Engine()
    for second in range(85):
        append_ecg(engine, second * 500, [2048] * 500)
        engine.append(json.dumps(dict(kind="imu", first=second * 50, timestamp=second * 1_000_000,
            flags=0, samples=[[0, 0, 16384, 0, 0, 0]] * 50)))
        result = json.loads(engine.analyze())
        if second < 60:
            assert result["rmssd"] is None
    assert result["rmssd"] == 0
    assert 65 <= result["rr_count"] <= 76
    count = result["rr_count"]
    assert json.loads(engine.analyze())["rr_count"] == count
    append_ecg(engine, 85 * 500, [2048] * 20, 1)
    assert json.loads(engine.analyze())["rmssd"] is None


def test_reboot_does_not_join_previous_rr():
    engine = Engine()
    append_ecg(engine, 9000, [2048] * 20)
    engine.rr.append((10000000, 800))
    append_ecg(engine, 0, [2050] * 20)
    assert not engine.rr
    assert len(engine.ecg) == 20


def test_repeated_bad_packets_are_retained_in_raw_ring():
    engine = Engine()
    for first in range(0, 5000, 20):
        append_ecg(engine, first, [4095] * 20, 4)
    assert len(engine.raw_ecg) == 5000
    assert all(sample.flags == 4 and sample.raw_adc == 4095 for sample in engine.raw_ecg)
    assert json.loads(engine.analyze())["bpm"] is None
