from __future__ import annotations

from dataclasses import dataclass
import time
import warnings

from .buffers import EcgSample
from .protocol import ECG_SAMPLE_RATE_HZ


@dataclass(frozen=True, slots=True)
class EcgAnalysisResult:
    timestamp_s: float
    raw: tuple[float, ...]
    cleaned: tuple[float, ...]
    r_peak_indices: tuple[int, ...]
    heart_rate_bpm: float | None
    latest_rr_ms: float | None
    signal_quality: float | None
    message: str


def analyze_recent_ecg(samples: tuple[EcgSample, ...]) -> EcgAnalysisResult:
    window = samples[-10 * ECG_SAMPLE_RATE_HZ :]
    raw = tuple(float(sample.raw_adc) for sample in window)
    if len(raw) < ECG_SAMPLE_RATE_HZ:
        return EcgAnalysisResult(
            timestamp_s=time.time(),
            raw=raw,
            cleaned=raw,
            r_peak_indices=(),
            heart_rate_bpm=None,
            latest_rr_ms=None,
            signal_quality=None,
            message="waiting for ECG window",
        )

    try:
        import neurokit2 as nk
        import numpy as np
    except ImportError:
        return EcgAnalysisResult(
            timestamp_s=time.time(),
            raw=raw,
            cleaned=raw,
            r_peak_indices=(),
            heart_rate_bpm=None,
            latest_rr_ms=None,
            signal_quality=None,
            message="install neurokit2 and numpy for ECG analysis",
        )

    raw_array = np.asarray(raw, dtype=float)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Too few peaks detected.*")
        cleaned_array = nk.ecg_clean(raw_array, sampling_rate=ECG_SAMPLE_RATE_HZ)
        _, peak_info = nk.ecg_peaks(cleaned_array, sampling_rate=ECG_SAMPLE_RATE_HZ)
    peaks = tuple(int(index) for index in peak_info.get("ECG_R_Peaks", []))

    latest_rr_ms: float | None = None
    heart_rate_bpm: float | None = None
    message = "need more R peaks"
    if len(peaks) >= 2:
        latest_rr_ms = (peaks[-1] - peaks[-2]) * 1000.0 / ECG_SAMPLE_RATE_HZ
        if latest_rr_ms > 0:
            heart_rate_bpm = 60_000.0 / latest_rr_ms
            message = "ok"

    quality: float | None = None
    if len(peaks) >= 2:
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="Too few peaks detected.*")
                quality_values = nk.ecg_quality(cleaned_array, sampling_rate=ECG_SAMPLE_RATE_HZ)
            quality_array = np.asarray(quality_values, dtype=float)
            finite_quality = quality_array[np.isfinite(quality_array)]
            if finite_quality.size > 0:
                quality = float(np.mean(finite_quality))
        except Exception:
            quality = None

    return EcgAnalysisResult(
        timestamp_s=time.time(),
        raw=raw,
        cleaned=tuple(float(value) for value in cleaned_array),
        r_peak_indices=peaks,
        heart_rate_bpm=heart_rate_bpm,
        latest_rr_ms=latest_rr_ms,
        signal_quality=quality,
        message=message,
    )
