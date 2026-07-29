from __future__ import annotations

from dataclasses import dataclass, replace
import math
from threading import Lock
import time
from typing import Callable, Iterable

from .analysis import EcgAnalysisResult, RrIntervalObservation
from .buffers import EcgSample, StatusSample
from .epaper_protocol import FRAME_BYTES, FRAME_HEIGHT, FRAME_STRIDE, FRAME_WIDTH, frame_crc32
from .health_rules import rmssd_ms
from .protocol import FLAG_ADC_CLIPPING


ECG_PREVIEW_SECONDS = 8
HRV_WINDOW_SECONDS = 30
ANALYSIS_STALE_MS = 2_000
MIN_HRV_NN_COUNT = 5
MIN_HRV_SQI = 0.5
METRIC_REFRESH_SECONDS = 5.0
WAVEFORM_REFRESH_SECONDS = 10.0
MIN_METRIC_REFRESH_SECONDS = 5.0
MIN_WAVEFORM_REFRESH_SECONDS = 10.0
MAX_REFRESH_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class HrvEstimate:
    value_ms: float | None
    valid: bool
    valid_nn_count: int
    window_seconds: int
    method: str
    unavailable_reason: str | None


@dataclass(frozen=True, slots=True)
class EpaperDisplayState:
    source_instance_id: str | None
    source_sample_index: int
    source_timestamp_us: int
    heart_rate_bpm: float | None
    hrv: HrvEstimate
    signal_quality: float | None
    lead_text: str
    lead_off: bool
    clipping: bool
    stale: bool
    data_age_ms: int | None
    analysis_message: str
    waveform: tuple[float, ...]

    @property
    def priority_signature(self) -> tuple[object, ...]:
        return (
            self.lead_text,
            self.lead_off,
            self.clipping,
            self.stale,
            self.signal_quality is not None and self.signal_quality >= MIN_HRV_SQI,
        )


@dataclass(frozen=True, slots=True)
class RenderedEpaperFrame:
    frame: bytes
    labels: tuple[str, ...]
    graph_black_pixels: int


@dataclass(frozen=True, slots=True)
class ScheduledEpaperFrame:
    frame: bytes
    crc32: int
    source_sample_index: int
    source_timestamp_us: int
    force_full: bool


class EpaperHrvWindow:
    def __init__(
        self,
        *,
        window_seconds: int = HRV_WINDOW_SECONDS,
        min_nn_count: int = MIN_HRV_NN_COUNT,
        min_signal_quality: float = MIN_HRV_SQI,
    ) -> None:
        self.window_seconds = window_seconds
        self.min_nn_count = min_nn_count
        self.min_signal_quality = min_signal_quality
        self._source_instance_id: str | None = None
        self._observations: dict[tuple[str, int], RrIntervalObservation] = {}

    def clear(self) -> None:
        self._source_instance_id = None
        self._observations.clear()

    def observe(
        self,
        analysis: EcgAnalysisResult | None,
        *,
        now_monotonic_ns: int,
        lead_off: bool,
        clipping: bool,
        stale: bool,
    ) -> HrvEstimate:
        if analysis is None or not analysis.source_instance_id:
            self.clear()
            return self._unavailable("analysis_unavailable")

        if analysis.source_instance_id != self._source_instance_id:
            self._source_instance_id = analysis.source_instance_id
            self._observations.clear()

        if lead_off:
            self._observations.clear()
            return self._unavailable("lead_off")
        if clipping:
            self._observations.clear()
            return self._unavailable("adc_clipping")

        for observation in analysis.rr_intervals:
            if observation.source_instance_id != self._source_instance_id:
                continue
            self._observations[
                (observation.source_instance_id, observation.end_sample_index)
            ] = observation

        cutoff_ns = now_monotonic_ns - (self.window_seconds * 1_000_000_000)
        self._observations = {
            key: observation
            for key, observation in self._observations.items()
            if observation.received_monotonic_ns >= cutoff_ns
        }
        ordered = sorted(
            self._observations.values(),
            key=lambda value: (value.received_monotonic_ns, value.end_sample_index),
        )

        if stale:
            return self._unavailable("stale_data", len(ordered))
        if analysis.message != "ok":
            return self._unavailable("analysis_unavailable", len(ordered))
        quality = analysis.signal_quality
        if quality is None or quality < self.min_signal_quality:
            return self._unavailable("quality_below_threshold", len(ordered))
        if len(ordered) < self.min_nn_count:
            return self._unavailable("insufficient_nn_intervals", len(ordered))

        value = rmssd_ms([observation.rr_ms for observation in ordered])
        if value is None or not math.isfinite(value):
            return self._unavailable("insufficient_nn_intervals", len(ordered))
        return HrvEstimate(
            value_ms=float(value),
            valid=True,
            valid_nn_count=len(ordered),
            window_seconds=self.window_seconds,
            method="rmssd",
            unavailable_reason=None,
        )

    def _unavailable(self, reason: str, count: int = 0) -> HrvEstimate:
        return HrvEstimate(
            value_ms=None,
            valid=False,
            valid_nn_count=count,
            window_seconds=self.window_seconds,
            method="rmssd",
            unavailable_reason=reason,
        )


class EpaperDisplayStateBuilder:
    def __init__(
        self,
        *,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        hrv_window: EpaperHrvWindow | None = None,
    ) -> None:
        self.monotonic_ns = monotonic_ns
        self.hrv_window = hrv_window or EpaperHrvWindow()

    def build(
        self,
        *,
        analysis: EcgAnalysisResult | None,
        status: StatusSample | None,
        source_sample: EcgSample | None,
    ) -> EpaperDisplayState:
        now_ns = self.monotonic_ns()
        lead_known = bool(
            status is not None
            and (
                analysis is None
                or not analysis.source_instance_id
                or status.source_instance_id == analysis.source_instance_id
            )
        )
        lead_off = bool(lead_known and status and status.payload.lead_off_flags)
        clipping = bool(
            (analysis is not None and analysis.message == "ECG clipped")
            or (source_sample is not None and source_sample.flags & FLAG_ADC_CLIPPING)
        )
        age_ms = _age_ms(
            now_ns,
            analysis.analyzed_through_received_monotonic_ns if analysis else None,
        )
        stale = age_ms is None or age_ms > ANALYSIS_STALE_MS
        analysis_message = analysis.message if analysis is not None else "waiting for ECG window"

        if not lead_known:
            lead_text = "导联等待"
        elif lead_off:
            lead_text = "导联脱落"
        elif clipping:
            lead_text = "信号无效"
        elif stale:
            lead_text = "等待数据"
        else:
            lead_text = "导联正常"

        metric_valid = bool(
            analysis is not None
            and not stale
            and not lead_off
            and not clipping
            and analysis.message == "ok"
        )
        heart_rate = analysis.heart_rate_bpm if metric_valid and analysis else None
        if heart_rate is not None and not 20.0 <= heart_rate <= 240.0:
            heart_rate = None
        quality = analysis.signal_quality if metric_valid and analysis else None
        if quality is not None and not 0.0 <= quality <= 1.0:
            quality = None

        hrv = self.hrv_window.observe(
            analysis,
            now_monotonic_ns=now_ns,
            lead_off=lead_off,
            clipping=clipping,
            stale=stale,
        )
        waveform = tuple(analysis.cleaned[-ECG_PREVIEW_SECONDS * 500 :]) if analysis else ()
        return EpaperDisplayState(
            source_instance_id=analysis.source_instance_id if analysis else None,
            source_sample_index=(
                int(analysis.analyzed_through_ecg_sample_index)
                if analysis and analysis.analyzed_through_ecg_sample_index is not None
                else 0
            ),
            source_timestamp_us=source_sample.timestamp_us if source_sample else 0,
            heart_rate_bpm=heart_rate,
            hrv=hrv,
            signal_quality=quality,
            lead_text=lead_text,
            lead_off=lead_off,
            clipping=clipping,
            stale=stale,
            data_age_ms=age_ms,
            analysis_message=analysis_message,
            waveform=waveform,
        )


class EpaperWaveformSnapshotter:
    def __init__(
        self,
        *,
        interval_seconds: float = WAVEFORM_REFRESH_SECONDS,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.monotonic_ns = monotonic_ns
        self.interval_ns = _waveform_interval_ns(interval_seconds)
        self._last_updated_ns: int | None = None
        self._source_instance_id: str | None = None
        self._waveform: tuple[float, ...] = ()

    def set_interval_seconds(self, value: float) -> None:
        self.interval_ns = _waveform_interval_ns(value)

    def apply(
        self,
        state: EpaperDisplayState,
        *,
        force: bool = False,
    ) -> tuple[EpaperDisplayState, bool]:
        now_ns = self.monotonic_ns()
        source_changed = state.source_instance_id != self._source_instance_id
        interval_due = (
            self._last_updated_ns is None
            or now_ns - self._last_updated_ns >= self.interval_ns
        )
        updated = force or source_changed or interval_due
        if updated:
            self._waveform = state.waveform
            self._source_instance_id = state.source_instance_id
            self._last_updated_ns = now_ns
        return replace(state, waveform=self._waveform), updated


class EpaperFrameScheduler:
    def __init__(
        self,
        *,
        interval_seconds: float = METRIC_REFRESH_SECONDS,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self.monotonic_ns = monotonic_ns
        self.interval_ns = _metric_interval_ns(interval_seconds)
        self._last_scheduled_ns: int | None = None
        self._last_crc32: int | None = None
        self._last_priority_signature: tuple[object, ...] | None = None
        self._lock = Lock()

    def set_interval_seconds(self, value: float) -> None:
        with self._lock:
            self.interval_ns = _metric_interval_ns(value)

    def consider(
        self,
        *,
        rendered: RenderedEpaperFrame,
        state: EpaperDisplayState,
        auto_enabled: bool,
        force: bool = False,
        force_full: bool = False,
    ) -> ScheduledEpaperFrame | None:
        if not auto_enabled and not force:
            return None
        now_ns = self.monotonic_ns()
        crc = frame_crc32(rendered.frame)
        with self._lock:
            priority_changed = (
                self._last_priority_signature is not None
                and state.priority_signature != self._last_priority_signature
            )
            interval_due = (
                self._last_scheduled_ns is None
                or now_ns - self._last_scheduled_ns >= self.interval_ns
            )
            frame_changed = crc != self._last_crc32
            if not force and not priority_changed and not interval_due:
                return None
            if not force and not frame_changed:
                self._last_priority_signature = state.priority_signature
                return None
            self._last_scheduled_ns = now_ns
            self._last_crc32 = crc
            self._last_priority_signature = state.priority_signature
        return ScheduledEpaperFrame(
            frame=rendered.frame,
            crc32=crc,
            source_sample_index=state.source_sample_index,
            source_timestamp_us=state.source_timestamp_us,
            force_full=force_full,
        )


def render_epaper_frame(
    state: EpaperDisplayState,
    *,
    QtCore: object | None = None,
    QtGui: object | None = None,
) -> RenderedEpaperFrame:
    if QtCore is None or QtGui is None:
        from PySide6 import QtCore as imported_core, QtGui as imported_gui

        QtCore = imported_core
        QtGui = imported_gui

    image = QtGui.QImage(FRAME_WIDTH, FRAME_HEIGHT, QtGui.QImage.Format_Grayscale8)
    image.fill(255)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing, False)
    painter.setPen(QtGui.QPen(QtCore.Qt.black, 1))
    painter.setBrush(QtCore.Qt.black)

    _draw_heart(painter, QtCore, QtGui, left=4, top=4, width=24, height=24)
    bpm_text = (
        f"{state.heart_rate_bpm:.0f} BPM"
        if state.heart_rate_bpm is not None
        else "-- BPM"
    )
    hrv_text = f"HRV {state.hrv.value_ms:.0f} ms" if state.hrv.valid else "HRV -- ms"
    ecg_text = "ECG 最近 8 秒"
    sqi_text = (
        f"SQI {state.signal_quality * 100:.0f}%"
        if state.signal_quality is not None
        else "SQI --"
    )
    lead_text = _compact_lead_text(state.lead_text)
    labels = (bpm_text, hrv_text, lead_text, ecg_text, sqi_text)

    _draw_text(painter, QtCore, QtGui, (32, 0, 116, 32), bpm_text, 19, bold=True)
    _draw_text(
        painter,
        QtCore,
        QtGui,
        (150, 0, 142, 32),
        hrv_text,
        19,
        bold=True,
        align_right=True,
    )
    _draw_text(painter, QtCore, QtGui, (4, 32, 70, 27), lead_text, 13, bold=True)
    _draw_text(painter, QtCore, QtGui, (74, 32, 128, 27), ecg_text, 13, bold=True)
    _draw_text(
        painter,
        QtCore,
        QtGui,
        (202, 32, 90, 27),
        sqi_text,
        13,
        bold=True,
        align_right=True,
    )

    graph_left = 4
    graph_top = 61
    graph_width = FRAME_WIDTH - 8
    graph_height = FRAME_HEIGHT - graph_top - 2
    graph_black_pixels = _draw_waveform(
        painter,
        QtCore,
        state.waveform,
        left=graph_left,
        top=graph_top,
        width=graph_width,
        height=graph_height,
    )
    painter.end()
    return RenderedEpaperFrame(
        frame=_pack_grayscale_image(image),
        labels=labels,
        graph_black_pixels=graph_black_pixels,
    )


def framebuffer_to_qimage(frame: bytes, *, QtGui: object | None = None) -> object:
    if len(frame) != FRAME_BYTES:
        raise ValueError(f"frame must contain {FRAME_BYTES} bytes")
    if QtGui is None:
        from PySide6 import QtGui as imported_gui

        QtGui = imported_gui
    image = QtGui.QImage(FRAME_WIDTH, FRAME_HEIGHT, QtGui.QImage.Format_Grayscale8)
    view = image.bits()
    stride = image.bytesPerLine()
    pixels = bytearray(stride * FRAME_HEIGHT)
    for y in range(FRAME_HEIGHT):
        row_offset = y * FRAME_STRIDE
        output_offset = y * stride
        for x in range(FRAME_WIDTH):
            bit = frame[row_offset + (x // 8)] & (0x80 >> (x % 8))
            pixels[output_offset + x] = 255 if bit else 0
    view[:] = pixels
    return image


def minmax_envelope(
    values: Iterable[float],
    *,
    columns: int,
) -> tuple[tuple[float, float] | None, ...]:
    if columns <= 0:
        raise ValueError("columns must be positive")
    data = tuple(float(value) for value in values)
    if not data:
        return tuple(None for _ in range(columns))
    result: list[tuple[float, float] | None] = []
    for column in range(columns):
        start = (column * len(data)) // columns
        end = ((column + 1) * len(data)) // columns
        if end <= start:
            end = min(len(data), start + 1)
        finite = [value for value in data[start:end] if math.isfinite(value)]
        result.append((min(finite), max(finite)) if finite else None)
    return tuple(result)


def _draw_text(
    painter: object,
    QtCore: object,
    QtGui: object,
    rect: tuple[int, int, int, int],
    text: str,
    pixel_size: int,
    *,
    bold: bool = False,
    align_right: bool = False,
) -> None:
    font = QtGui.QFont("Microsoft YaHei UI")
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    painter.setFont(font)
    painter.drawText(
        QtCore.QRect(*rect),
        (QtCore.Qt.AlignRight if align_right else QtCore.Qt.AlignLeft)
        | QtCore.Qt.AlignVCenter,
        text,
    )


def _draw_heart(
    painter: object,
    QtCore: object,
    QtGui: object,
    *,
    left: int,
    top: int,
    width: int,
    height: int,
) -> None:
    points = (
        (0.50, 1.00),
        (0.08, 0.58),
        (0.03, 0.35),
        (0.11, 0.16),
        (0.26, 0.08),
        (0.40, 0.12),
        (0.50, 0.25),
        (0.60, 0.12),
        (0.74, 0.08),
        (0.89, 0.16),
        (0.97, 0.35),
        (0.92, 0.58),
    )
    painter.drawPolygon(
        QtGui.QPolygonF(
            [
                QtCore.QPointF(left + x * width, top + y * height)
                for x, y in points
            ]
        )
    )


def _draw_waveform(
    painter: object,
    QtCore: object,
    values: tuple[float, ...],
    *,
    left: int,
    top: int,
    width: int,
    height: int,
) -> int:
    envelope = minmax_envelope(values, columns=width)
    finite_values = [
        value
        for pair in envelope
        if pair is not None
        for value in pair
        if math.isfinite(value)
    ]
    if not finite_values:
        baseline = top + height // 2
        for x in range(left, left + width, 4):
            painter.drawPoint(x, baseline)
        return width // 4

    low = min(finite_values)
    high = max(finite_values)
    if high <= low:
        low -= 1.0
        high += 1.0
    padding = (high - low) * 0.04
    low -= padding
    high += padding

    black_pixels = 0
    previous_point: tuple[int, int] | None = None
    for column, pair in enumerate(envelope):
        if pair is None:
            previous_point = None
            continue
        minimum, maximum = pair
        y_top = _map_y(maximum, low=low, high=high, top=top, height=height)
        y_bottom = _map_y(minimum, low=low, high=high, top=top, height=height)
        x = left + column
        midpoint = (y_top + y_bottom) // 2
        if previous_point is not None:
            painter.drawLine(previous_point[0], previous_point[1], x, midpoint)
            black_pixels += max(
                abs(x - previous_point[0]),
                abs(midpoint - previous_point[1]),
            ) + 1
        if column % 2 == 0 or abs(y_bottom - y_top) >= 4:
            painter.drawLine(x, y_top, x, y_bottom)
            black_pixels += abs(y_bottom - y_top) + 1
        previous_point = (x, midpoint)
    return black_pixels


def _map_y(value: float, *, low: float, high: float, top: int, height: int) -> int:
    ratio = (value - low) / (high - low)
    ratio = min(1.0, max(0.0, ratio))
    return top + int(round((1.0 - ratio) * max(0, height - 1)))


def _pack_grayscale_image(image: object) -> bytes:
    if image.width() != FRAME_WIDTH or image.height() != FRAME_HEIGHT:
        raise ValueError("unexpected render image dimensions")
    raw = bytes(image.constBits())
    input_stride = image.bytesPerLine()
    frame = bytearray(b"\xFF" * FRAME_BYTES)
    for y in range(FRAME_HEIGHT):
        input_offset = y * input_stride
        output_offset = y * FRAME_STRIDE
        for x in range(FRAME_WIDTH):
            if raw[input_offset + x] < 128:
                frame[output_offset + (x // 8)] &= ~(0x80 >> (x % 8))
    return bytes(frame)


def _age_ms(now_ns: int, evidence_ns: int | None) -> int | None:
    if evidence_ns is None or evidence_ns <= 0 or now_ns < evidence_ns:
        return None
    return int((now_ns - evidence_ns) / 1_000_000)


def _compact_lead_text(value: str) -> str:
    return {
        "导联正常": "正常",
        "导联脱落": "脱落",
        "导联等待": "等待",
    }.get(value, value)


def _metric_interval_ns(value: float) -> int:
    if not MIN_METRIC_REFRESH_SECONDS <= value <= MAX_REFRESH_SECONDS:
        raise ValueError(
            "electronic-paper metric interval must be between 5 and 60 seconds"
        )
    return int(value * 1_000_000_000)


def _waveform_interval_ns(value: float) -> int:
    if not MIN_WAVEFORM_REFRESH_SECONDS <= value <= MAX_REFRESH_SECONDS:
        raise ValueError(
            "electronic-paper waveform interval must be between 10 and 60 seconds"
        )
    return int(value * 1_000_000_000)
