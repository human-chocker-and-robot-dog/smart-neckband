from smart_neckband.analysis import EcgAnalysisResult
from smart_neckband.buffers import EcgSample
from smart_neckband.live_uploader import build_ecg_upload_batch


def _samples(count: int) -> tuple[EcgSample, ...]:
    return tuple(
        EcgSample(sample_index=index, timestamp_us=index * 2_000, raw_adc=2000 + index, flags=0)
        for index in range(count)
    )


def test_build_ecg_upload_batch_sends_only_new_clean_samples() -> None:
    analysis = EcgAnalysisResult(
        timestamp_s=1.0,
        raw=tuple(float(index) for index in range(6)),
        cleaned=tuple(float(index) / 10.0 for index in range(6)),
        r_peak_indices=(2, 5),
        heart_rate_bpm=72.4,
        latest_rr_ms=830.0,
        signal_quality=0.91,
        message="ok",
    )

    batch = build_ecg_upload_batch(
        seq=7,
        window=_samples(6),
        analysis=analysis,
        last_sent_sample_index=2,
        lead_off=False,
    )

    assert batch is not None
    assert batch.last_sample_index == 5
    assert batch.message["seq"] == 7
    assert batch.message["sample_rate"] == 500
    assert batch.message["samples"] == [0.3, 0.4, 0.5]
    assert batch.message["r_peaks"] == [2]
    assert batch.message["hr_bpm"] == 72.4
    assert batch.message["sqi"] == 0.91


def test_build_ecg_upload_batch_returns_none_when_no_new_samples() -> None:
    analysis = EcgAnalysisResult(
        timestamp_s=1.0,
        raw=(1.0, 2.0),
        cleaned=(1.0, 2.0),
        r_peak_indices=(),
        heart_rate_bpm=None,
        latest_rr_ms=None,
        signal_quality=None,
        message="waiting",
    )

    assert (
        build_ecg_upload_batch(
            seq=1,
            window=_samples(2),
            analysis=analysis,
            last_sent_sample_index=1,
            lead_off=True,
        )
        is None
    )
