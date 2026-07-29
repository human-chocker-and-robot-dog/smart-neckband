from __future__ import annotations

import math
import os
from dataclasses import replace

from smart_neckband.analysis import EcgAnalysisResult, RrIntervalObservation
from smart_neckband.buffers import EcgSample, StatusSample
from smart_neckband.epaper_protocol import FRAME_BYTES, FRAME_STRIDE, FRAME_WIDTH
from smart_neckband.epaper_sync import (
    EpaperDisplayStateBuilder,
    EpaperFrameScheduler,
    EpaperHrvWindow,
    HrvEstimate,
    EpaperDisplayState,
    minmax_envelope,
    render_epaper_frame,
)
from smart_neckband.protocol import DeviceStatusPayload


SOURCE = "source-a"
NOW_NS = 40_000_000_000


def _analysis(
    *,
    rr_count: int = 5,
    quality: float | None = 0.9,
    message: str = "ok",
    received_ns: int = NOW_NS - 500_000_000,
) -> EcgAnalysisResult:
    rr = tuple(
        RrIntervalObservation(
            source_instance_id=SOURCE,
            end_sample_index=1_000 + index,
            observed_at=f"2026-07-29T00:00:{index:02d}.000Z",
            received_monotonic_ns=received_ns - ((rr_count - index) * 800_000_000),
            rr_ms=800.0 + (20.0 if index % 2 else 0.0),
        )
        for index in range(rr_count)
    )
    waveform = tuple(
        2_000.0 + 180.0 * math.sin(index * 2.0 * math.pi / 250.0)
        for index in range(5_000)
    )
    return EcgAnalysisResult(
        timestamp_s=0.0,
        raw=waveform,
        cleaned=waveform,
        r_peak_indices=(),
        heart_rate_bpm=72.4,
        latest_rr_ms=810.0,
        signal_quality=quality,
        message=message,
        source_instance_id=SOURCE,
        analyzed_through_ecg_sample_index=1_500,
        analyzed_through_received_monotonic_ns=received_ns,
        analyzed_through_received_at_utc="2026-07-29T00:00:39.500Z",
        rr_intervals=rr,
    )


def _status(lead_off: bool = False) -> StatusSample:
    return StatusSample(
        timestamp_us=9_000_000,
        payload=DeviceStatusPayload(
            lead_off_flags=1 if lead_off else 0,
            sensor_status_flags=0,
            ecg_buffer_usage_percent=0,
            imu_buffer_usage_percent=0,
            spp_queue_usage_percent=0,
            status_flags=0,
            error_count=0,
            ecg_ring_overflow_count=0,
            imu_ring_overflow_count=0,
            spp_queue_overflow_count=0,
            transport_drop_count=0,
            i2c_error_count=0,
        ),
        source_instance_id=SOURCE,
        received_monotonic_ns=NOW_NS - 500_000_000,
    )


def _sample() -> EcgSample:
    return EcgSample(
        sample_index=1_500,
        timestamp_us=9_500_000,
        raw_adc=2_000,
        flags=0,
        source_instance_id=SOURCE,
        received_monotonic_ns=NOW_NS - 500_000_000,
    )


def test_hrv_window_deduplicates_overlapping_analysis_and_applies_quality_gates() -> None:
    window = EpaperHrvWindow()
    analysis = _analysis()

    first = window.observe(
        analysis,
        now_monotonic_ns=NOW_NS,
        lead_off=False,
        clipping=False,
        stale=False,
    )
    second = window.observe(
        analysis,
        now_monotonic_ns=NOW_NS,
        lead_off=False,
        clipping=False,
        stale=False,
    )

    assert first.valid and second.valid
    assert first.valid_nn_count == second.valid_nn_count == 5
    assert first.value_ms is not None
    assert window.observe(
        _analysis(quality=0.49),
        now_monotonic_ns=NOW_NS,
        lead_off=False,
        clipping=False,
        stale=False,
    ).unavailable_reason == "quality_below_threshold"


def test_hrv_window_clears_on_lead_off() -> None:
    window = EpaperHrvWindow()
    window.observe(_analysis(), now_monotonic_ns=NOW_NS, lead_off=False, clipping=False, stale=False)
    invalid = window.observe(
        _analysis(),
        now_monotonic_ns=NOW_NS,
        lead_off=True,
        clipping=False,
        stale=False,
    )
    waiting = window.observe(
        _analysis(rr_count=0),
        now_monotonic_ns=NOW_NS,
        lead_off=False,
        clipping=False,
        stale=False,
    )
    assert invalid.unavailable_reason == "lead_off"
    assert waiting.valid_nn_count == 0


def test_state_builder_exposes_requested_labels_and_marks_stale_data_invalid() -> None:
    builder = EpaperDisplayStateBuilder(monotonic_ns=lambda: NOW_NS)
    state = builder.build(analysis=_analysis(), status=_status(), source_sample=_sample())
    assert state.heart_rate_bpm == 72.4
    assert state.hrv.valid
    assert state.lead_text == "导联正常"
    assert len(state.waveform) == 4_000
    assert state.source_timestamp_us == 9_500_000

    stale = builder.build(
        analysis=_analysis(received_ns=NOW_NS - 3_000_000_000),
        status=_status(),
        source_sample=_sample(),
    )
    assert stale.stale
    assert stale.heart_rate_bpm is None
    assert stale.hrv.unavailable_reason == "stale_data"

    mismatched_status = replace(_status(), source_instance_id="old-source")
    waiting = builder.build(
        analysis=_analysis(),
        status=mismatched_status,
        source_sample=_sample(),
    )
    assert waiting.lead_text == "导联等待"


def test_minmax_envelope_preserves_a_narrow_peak() -> None:
    values = [0.0] * 1_000
    values[501] = 100.0
    envelope = minmax_envelope(values, columns=100)
    assert any(pair is not None and pair[1] == 100.0 for pair in envelope)


def test_renderer_produces_quote0_frame_and_requested_layout() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtWidgets

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None

    state = EpaperDisplayStateBuilder(monotonic_ns=lambda: NOW_NS).build(
        analysis=_analysis(),
        status=_status(),
        source_sample=_sample(),
    )
    rendered = render_epaper_frame(state, QtCore=QtCore, QtGui=QtGui)

    assert len(rendered.frame) == FRAME_BYTES
    assert rendered.labels[0].endswith("BPM")
    assert rendered.labels[1].startswith("HRV ")
    assert rendered.labels[2] == "导联正常"
    assert "最近 8 秒" in rendered.labels[3]
    assert "SQI" in rendered.labels[3]
    assert all("更新时间" not in label for label in rendered.labels)
    assert rendered.graph_black_pixels > FRAME_WIDTH
    top_left = rendered.frame[0 : 26 * FRAME_STRIDE]
    graph = rendered.frame[80 * FRAME_STRIDE :]
    assert any(value != 0xFF for value in top_left)
    assert any(value != 0xFF for value in graph)


def test_scheduler_throttles_ordinary_updates_but_prioritizes_lead_change() -> None:
    now = [0]
    scheduler = EpaperFrameScheduler(
        interval_seconds=15,
        monotonic_ns=lambda: now[0],
    )
    state = EpaperDisplayState(
        source_instance_id=SOURCE,
        source_sample_index=10,
        source_timestamp_us=20,
        heart_rate_bpm=72.0,
        hrv=HrvEstimate(30.0, True, 5, 30, "rmssd", None),
        signal_quality=0.9,
        lead_text="导联正常",
        lead_off=False,
        clipping=False,
        stale=False,
        data_age_ms=100,
        analysis_message="ok",
        waveform=(1.0, 2.0, 3.0),
    )
    from smart_neckband.epaper_sync import RenderedEpaperFrame

    first_render = RenderedEpaperFrame(frame=b"\xFF" * FRAME_BYTES, labels=(), graph_black_pixels=0)
    changed_render = RenderedEpaperFrame(
        frame=b"\xFE" + (b"\xFF" * (FRAME_BYTES - 1)),
        labels=(),
        graph_black_pixels=1,
    )
    assert scheduler.consider(rendered=first_render, state=state, auto_enabled=True) is not None
    assert scheduler.consider(rendered=changed_render, state=state, auto_enabled=True) is None

    lead_changed = replace(state, lead_text="导联脱落", lead_off=True)
    prioritized = scheduler.consider(
        rendered=changed_render,
        state=lead_changed,
        auto_enabled=True,
    )
    assert prioritized is not None
    now[0] = 16_000_000_000
    assert scheduler.consider(
        rendered=changed_render,
        state=lead_changed,
        auto_enabled=True,
    ) is None
