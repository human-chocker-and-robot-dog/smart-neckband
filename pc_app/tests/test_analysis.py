import sys
import types
import warnings

import numpy as np

from smart_neckband.analysis import analyze_recent_ecg
from smart_neckband.buffers import EcgSample
from smart_neckband.protocol import ECG_SAMPLE_RATE_HZ, FLAG_ADC_CLIPPING


def _ecg_window(*, flags: int = 0, raw_adc: int = 2048) -> tuple[EcgSample, ...]:
    return tuple(
        EcgSample(
            sample_index=index,
            timestamp_us=index * 2_000,
            raw_adc=raw_adc,
            flags=flags,
        )
        for index in range(ECG_SAMPLE_RATE_HZ * 2)
    )


def test_analysis_ignores_nan_only_quality_without_runtime_warning(monkeypatch) -> None:
    fake_neurokit = types.SimpleNamespace(
        ecg_clean=lambda raw, sampling_rate: np.asarray(raw, dtype=float),
        ecg_peaks=lambda cleaned, sampling_rate: (
            None,
            {"ECG_R_Peaks": np.asarray([100, 400])},
        ),
        ecg_quality=lambda cleaned, sampling_rate: np.asarray([np.nan, np.nan]),
    )
    monkeypatch.setitem(sys.modules, "neurokit2", fake_neurokit)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = analyze_recent_ecg(_ecg_window())

    assert result.signal_quality is None
    assert not [warning for warning in caught if issubclass(warning.category, RuntimeWarning)]


def test_analysis_reports_clipped_ecg_without_neurokit(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "neurokit2",
        types.SimpleNamespace(
            ecg_clean=lambda raw, sampling_rate: (_ for _ in ()).throw(AssertionError("unexpected neurokit call")),
        ),
    )

    result = analyze_recent_ecg(_ecg_window(flags=FLAG_ADC_CLIPPING, raw_adc=0))

    assert result.message == "ECG clipped"
    assert result.heart_rate_bpm is None
    assert result.r_peak_indices == ()


def test_analysis_uses_stable_rr_median_for_heart_rate(monkeypatch) -> None:
    fake_neurokit = types.SimpleNamespace(
        ecg_clean=lambda raw, sampling_rate: np.asarray(raw, dtype=float),
        ecg_peaks=lambda cleaned, sampling_rate: (
            None,
            {"ECG_R_Peaks": np.asarray([100, 600, 1100, 1600, 2100, 2350])},
        ),
        ecg_quality=lambda cleaned, sampling_rate: np.asarray([0.9, 0.9]),
    )
    monkeypatch.setitem(sys.modules, "neurokit2", fake_neurokit)

    result = analyze_recent_ecg(_ecg_window())

    assert result.latest_rr_ms == 500.0
    assert result.heart_rate_bpm == 60.0
