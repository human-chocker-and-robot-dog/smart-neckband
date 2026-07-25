from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
import statistics
import time
import warnings

from .buffers import EcgSample
from .protocol import ECG_SAMPLE_RATE_HZ, FLAG_ADC_CLIPPING


@dataclass(frozen=True, slots=True)
class RrIntervalObservation:
    source_instance_id: str
    end_sample_index: int
    observed_at: str
    received_monotonic_ns: int
    rr_ms: float


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
    source_instance_id: str | None = None
    analyzed_through_ecg_sample_index: int | None = None
    analyzed_through_received_monotonic_ns: int | None = None
    analyzed_through_received_at_utc: str | None = None
    rr_intervals: tuple[RrIntervalObservation, ...] = ()


@dataclass(frozen=True, slots=True)
class EcgAnalysisInfo:
    library_name: str
    library_version: str
    numpy_version: str
    sampling_rate_hz: int
    clean_method: str
    peak_method: str
    quality_method: str
    analysis_window_seconds: float
    overlap_seconds: float
    hr_method: str
    rr_valid_range_ms: tuple[float, float]
    clipping_rule: str
    resampling: str
    powerline_handling: str


def _package_version(package_name: str) -> str:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return "未安装"


def get_ecg_analysis_info() -> EcgAnalysisInfo:
    return EcgAnalysisInfo(
        library_name="NeuroKit2",
        library_version=_package_version("neurokit2"),
        numpy_version=_package_version("numpy"),
        sampling_rate_hz=ECG_SAMPLE_RATE_HZ,
        clean_method="nk.ecg_clean，method 使用 NeuroKit2 默认值",
        peak_method="nk.ecg_peaks，method 使用 NeuroKit2 默认值",
        quality_method="nk.ecg_quality，method 使用 NeuroKit2 默认值；无 finite SQI 时不显示数值",
        analysis_window_seconds=10.0,
        overlap_seconds=9.5,
        hr_method="300-2000 ms 合法 RR；最近最多 5 个 RR 的中位数",
        rr_valid_range_ms=(300.0, 2000.0),
        clipping_rule="最近分析窗口中 >=80% ECG 样本带 ADC_CLIPPING 标志时判定为 ECG clipped",
        resampling="不重采样",
        powerline_handling="未显式配置 50 Hz 工频处理；使用 NeuroKit2 默认清洗流程",
    )


def _rr_intervals_ms(peaks: tuple[int, ...]) -> tuple[float, ...]:
    return tuple(
        (peaks[index] - peaks[index - 1]) * 1000.0 / ECG_SAMPLE_RATE_HZ
        for index in range(1, len(peaks))
    )


def _valid_rr_intervals_ms(peaks: tuple[int, ...]) -> tuple[float, ...]:
    return tuple(rr for rr in _rr_intervals_ms(peaks) if 300.0 <= rr <= 2000.0)


def _rr_observations(
    window: tuple[EcgSample, ...],
    peaks: tuple[int, ...],
) -> tuple[RrIntervalObservation, ...]:
    observations: list[RrIntervalObservation] = []
    for index in range(1, len(peaks)):
        start_peak = peaks[index - 1]
        end_peak = peaks[index]
        if not (0 <= start_peak < end_peak < len(window)):
            continue
        rr_ms = (end_peak - start_peak) * 1000.0 / ECG_SAMPLE_RATE_HZ
        if not 300.0 <= rr_ms <= 2000.0:
            continue
        end_sample = window[end_peak]
        if (
            not end_sample.source_instance_id
            or not end_sample.received_at_utc
            or end_sample.received_monotonic_ns <= 0
        ):
            continue
        observations.append(
            RrIntervalObservation(
                source_instance_id=end_sample.source_instance_id,
                end_sample_index=end_sample.sample_index,
                observed_at=end_sample.received_at_utc,
                received_monotonic_ns=end_sample.received_monotonic_ns,
                rr_ms=rr_ms,
            )
        )
    return tuple(observations)


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
    latest = window[-1] if window else None
    provenance = {
        "source_instance_id": (
            latest.source_instance_id if latest and latest.source_instance_id else None
        ),
        "analyzed_through_ecg_sample_index": (
            latest.sample_index if latest is not None else None
        ),
        "analyzed_through_received_monotonic_ns": (
            latest.received_monotonic_ns
            if latest is not None and latest.received_monotonic_ns > 0
            else None
        ),
        "analyzed_through_received_at_utc": (
            latest.received_at_utc
            if latest is not None and latest.received_at_utc
            else None
        ),
    }
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
            **provenance,
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
            **provenance,
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
            **provenance,
        )

    raw_array = np.asarray(raw, dtype=float)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Too few peaks detected.*")
        cleaned_array = nk.ecg_clean(raw_array, sampling_rate=ECG_SAMPLE_RATE_HZ)
        _, peak_info = nk.ecg_peaks(cleaned_array, sampling_rate=ECG_SAMPLE_RATE_HZ)
    peaks = tuple(int(index) for index in peak_info.get("ECG_R_Peaks", []))
    rr_observations = _rr_observations(window, peaks)

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
        rr_intervals=rr_observations,
        **provenance,
    )
