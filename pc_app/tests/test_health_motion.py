from __future__ import annotations

from smart_neckband.buffers import ImuSample
from smart_neckband.health_motion import analyze_motion


SOURCE = "ef132c67-a98f-474a-a673-4ab6ea784790"


def samples(*, seconds: int, ax: int, gx: int) -> tuple[ImuSample, ...]:
    result = []
    for index in range(seconds * 50):
        result.append(
            ImuSample(
                sample_index=index,
                timestamp_us=index * 20_000,
                ax=ax,
                ay=0,
                az=16_384,
                gx=gx,
                gy=0,
                gz=0,
                flags=0,
                source_instance_id=SOURCE,
                received_monotonic_ns=1_000_000_000 + index * 20_000_000,
                received_at_utc="2026-07-25T12:00:00.000Z",
            )
        )
    return tuple(result)


def test_static_window_has_low_motion_score() -> None:
    result = analyze_motion(samples(seconds=30, ax=0, gx=0), source_instance_id=SOURCE)

    assert result.unavailable_reason is None
    assert result.score is not None and result.score < 10
    assert result.still_ratio_percent == 100.0
    assert result.level == "still"
    assert result.coverage_ratio > 0.99


def test_vigorous_window_has_high_bounded_score() -> None:
    result = analyze_motion(
        samples(seconds=30, ax=16_384, gx=20_000),
        source_instance_id=SOURCE,
    )

    assert result.unavailable_reason is None
    assert result.score is not None and 65 <= result.score <= 100
    assert result.level == "vigorous"
    assert result.still_ratio_percent == 0.0


def test_short_window_is_unavailable_instead_of_inflated() -> None:
    result = analyze_motion(samples(seconds=5, ax=0, gx=0), source_instance_id=SOURCE)

    assert result.score is None
    assert result.unavailable_reason == "insufficient_coverage"
    assert 0 < result.coverage_ratio < 0.5
