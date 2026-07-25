from __future__ import annotations

from dataclasses import dataclass
import math
import statistics

from .buffers import ImuSample
from .protocol import IMU_SAMPLE_RATE_HZ


ACCEL_LSB_PER_G = 16_384.0
GYRO_LSB_PER_DPS = 131.0
DEFAULT_ACCEL_ACTIVITY_REF_G = 0.35
DEFAULT_GYRO_ACTIVITY_REF_DPS = 120.0
DEFAULT_STILL_BUCKET_SCORE = 10.0


@dataclass(frozen=True, slots=True)
class MotionBucket:
    second_offset: int
    score: float


@dataclass(frozen=True, slots=True)
class MotionAnalysis:
    score: float | None
    still_ratio_percent: float | None
    level: str | None
    coverage_ratio: float
    observed_at: str | None
    received_monotonic_ns: int | None
    window_s: float
    method: str
    buckets: tuple[MotionBucket, ...]
    unavailable_reason: str | None


def analyze_motion(
    samples: tuple[ImuSample, ...],
    *,
    source_instance_id: str,
    window_s: float = 30.0,
    accel_activity_ref_g: float = DEFAULT_ACCEL_ACTIVITY_REF_G,
    gyro_activity_ref_dps: float = DEFAULT_GYRO_ACTIVITY_REF_DPS,
    still_bucket_score: float = DEFAULT_STILL_BUCKET_SCORE,
) -> MotionAnalysis:
    method = "mpu6050_accel_gyro_activity_v1"
    if window_s <= 0:
        raise ValueError("window_s must be positive")
    if accel_activity_ref_g <= 0 or gyro_activity_ref_dps <= 0:
        raise ValueError("motion reference values must be positive")

    same_source = tuple(
        sample
        for sample in samples
        if sample.source_instance_id == source_instance_id
    )
    if not same_source:
        return _unavailable(window_s, method, "no_imu_samples")

    latest = same_source[-1]
    window_us = int(window_s * 1_000_000)
    cutoff_us = latest.timestamp_us - window_us
    window = tuple(sample for sample in same_source if sample.timestamp_us >= cutoff_us)
    if not window:
        return _unavailable(window_s, method, "no_imu_samples")

    expected = max(1, int(window_s * IMU_SAMPLE_RATE_HZ))
    count_coverage = min(1.0, len(window) / expected)
    span_s = max(0.0, (window[-1].timestamp_us - window[0].timestamp_us) / 1_000_000.0)
    span_coverage = min(1.0, span_s / window_s)
    coverage = min(count_coverage, span_coverage if len(window) > 1 else 0.0)

    bucket_values: dict[int, list[float]] = {}
    first_timestamp = window[0].timestamp_us
    for sample in window:
        accel_g = math.sqrt(sample.ax**2 + sample.ay**2 + sample.az**2) / ACCEL_LSB_PER_G
        accel_activity = abs(accel_g - 1.0)
        gyro_dps = math.sqrt(sample.gx**2 + sample.gy**2 + sample.gz**2) / GYRO_LSB_PER_DPS
        normalized = (
            0.6 * min(1.0, accel_activity / accel_activity_ref_g)
            + 0.4 * min(1.0, gyro_dps / gyro_activity_ref_dps)
        )
        bucket_index = max(0, int((sample.timestamp_us - first_timestamp) // 1_000_000))
        bucket_values.setdefault(bucket_index, []).append(normalized * 100.0)

    buckets = tuple(
        MotionBucket(second_offset=index, score=round(statistics.fmean(values), 3))
        for index, values in sorted(bucket_values.items())
        if values
    )
    if not buckets:
        return _unavailable(window_s, method, "no_imu_samples")

    score = round(statistics.fmean(bucket.score for bucket in buckets), 3)
    still_ratio = round(
        100.0 * sum(bucket.score <= still_bucket_score for bucket in buckets) / len(buckets),
        3,
    )
    level = (
        "still"
        if score < 10.0
        else "light"
        if score < 35.0
        else "moderate"
        if score < 65.0
        else "vigorous"
    )
    unavailable_reason = None if coverage >= 0.5 else "insufficient_coverage"
    return MotionAnalysis(
        score=score if unavailable_reason is None else None,
        still_ratio_percent=still_ratio if unavailable_reason is None else None,
        level=level if unavailable_reason is None else None,
        coverage_ratio=round(coverage, 6),
        observed_at=latest.received_at_utc or None,
        received_monotonic_ns=(
            latest.received_monotonic_ns if latest.received_monotonic_ns > 0 else None
        ),
        window_s=window_s,
        method=method,
        buckets=buckets,
        unavailable_reason=unavailable_reason,
    )


def _unavailable(window_s: float, method: str, reason: str) -> MotionAnalysis:
    return MotionAnalysis(
        score=None,
        still_ratio_percent=None,
        level=None,
        coverage_ratio=0.0,
        observed_at=None,
        received_monotonic_ns=None,
        window_s=window_s,
        method=method,
        buckets=(),
        unavailable_reason=reason,
    )
