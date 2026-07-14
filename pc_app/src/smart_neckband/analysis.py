from __future__ import annotations

from dataclasses import dataclass
import statistics
import time
import warnings

from .buffers import EcgSample
from .protocol import ECG_SAMPLE_RATE_HZ, FLAG_ADC_CLIPPING


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


def _rr_intervals_ms(peaks: tuple[int, ...]) -> tuple[float, ...]:
    return tuple(
        (peaks[index] - peaks[index - 1]) * 1000.0 / ECG_SAMPLE_RATE_HZ
        for index in range(1, len(peaks))
    )


def _valid_rr_intervals_ms(peaks: tuple[int, ...]) -> tuple[float, ...]:
    return tuple(rr for rr in _rr_intervals_ms(peaks) if 300.0 <= rr <= 2000.0)


def _stable_rr_ms(intervals_ms: tuple[float, ...]) -> float | None:
    if len(intervals_ms) < 2:
        return None
    recent = intervals_ms[-5:]
    median_rr = statistics.median(recent)
    if median_rr <= 0.0:
        return None
    stable = tuple(rr for rr in recent if (median_rr * 0.75) <= rr <= (median_rr * 1.25))
    return float(statistics.median(stable or recent))


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

    clipped_count = sum(1 for sample in window if sample.flags & FLAG_ADC_CLIPPING)
    if clipped_count >= int(len(window) * 0.8):
        return EcgAnalysisResult(
            timestamp_s=time.time(),
            raw=raw,
            cleaned=raw,
            r_peak_indices=(),
            heart_rate_bpm=None,
            latest_rr_ms=None,
            signal_quality=None,
            message="ECG clipped",
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
        valid_rr = _valid_rr_intervals_ms(peaks)
        if valid_rr:
            latest_rr_ms = valid_rr[-1]
        stable_rr = _stable_rr_ms(valid_rr)
        if stable_rr is not None:
            heart_rate_bpm = 60_000.0 / stable_rr
            message = "ok"
        else:
            message = "need stable R peaks"

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
