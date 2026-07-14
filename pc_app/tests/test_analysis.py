import sys
import types
import warnings

import numpy as np

from smart_neckband.analysis import analyze_recent_ecg
from smart_neckband.buffers import EcgSample
from smart_neckband.protocol import ECG_SAMPLE_RATE_HZ


def _ecg_window() -> tuple[EcgSample, ...]:
    return tuple(
        EcgSample(
            sample_index=index,
            timestamp_us=index * 2_000,
            raw_adc=2048,
            flags=0,
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
