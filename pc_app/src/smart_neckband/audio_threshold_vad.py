from __future__ import annotations

from dataclasses import dataclass
import math
from queue import Empty, Full, Queue
from statistics import median
from threading import Event, Thread
from typing import Callable, Iterable

from .mic_capture_debug import DebugLogger


@dataclass(frozen=True, slots=True)
class AudioThresholdVadSettings:
    sample_rate: int = 16000
    analysis_window_ms: int = 100
    queue_depth: int = 512
    noise_rms: float = 300.0
    speech_rms: float = 0.0
    rms_multiplier: float = 3.0
    min_rms_delta: float = 250.0
    speech_end_threshold_ratio: float = 0.60
    silence_ms: int = 500
    min_speech_ms: int = 300
    calibration_ms: int = 2500

    @property
    def chunk_samples(self) -> int:
        return max(1, self.sample_rate * self.analysis_window_ms // 1000)

    @property
    def speech_rms_threshold(self) -> float:
        base = max(
            self.noise_rms * self.rms_multiplier,
            self.noise_rms + self.min_rms_delta,
        )
        if self.speech_rms <= self.noise_rms:
            return base
        gap = self.speech_rms - self.noise_rms
        calibrated = self.noise_rms + gap * 0.35
        ceiling = self.noise_rms + gap * 0.70
        return min(max(base, calibrated), ceiling)

    @property
    def speech_end_rms_threshold(self) -> float:
        return max(
            self.noise_rms,
            self.speech_rms_threshold * self.speech_end_threshold_ratio,
        )

    def validate(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("音频阈值 VAD sample_rate 必须大于 0")
        if self.analysis_window_ms < 20:
            raise ValueError("音频阈值 VAD 分析窗口至少为 20 ms")
        if self.queue_depth < 8:
            raise ValueError("音频阈值 VAD 队列深度至少为 8")
        if self.noise_rms < 0:
            raise ValueError("静默 RMS 不能为负数")
        if self.speech_rms < 0:
            raise ValueError("说话 RMS 不能为负数")
        if self.rms_multiplier < 1.0:
            raise ValueError("RMS 倍数至少为 1.0")
        if self.min_rms_delta < 0:
            raise ValueError("最小 RMS 增量不能为负数")
        if not 0.1 <= self.speech_end_threshold_ratio <= 1.0:
            raise ValueError("VAD 结束阈值比例必须在 0.1 到 1.0 之间")
        if self.silence_ms < self.analysis_window_ms:
            raise ValueError("停止静默时间不能小于分析窗口")
        if self.min_speech_ms < 0:
            raise ValueError("最小说话时间不能为负数")
        if self.calibration_ms < 500:
            raise ValueError("静默采样时长至少为 500 ms")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "sample_rate": self.sample_rate,
            "analysis_window_ms": self.analysis_window_ms,
            "queue_depth": self.queue_depth,
            "noise_rms": self.noise_rms,
            "speech_rms": self.speech_rms,
            "rms_multiplier": self.rms_multiplier,
            "min_rms_delta": self.min_rms_delta,
            "speech_end_threshold_ratio": self.speech_end_threshold_ratio,
            "silence_ms": self.silence_ms,
            "min_speech_ms": self.min_speech_ms,
            "calibration_ms": self.calibration_ms,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "AudioThresholdVadSettings":
        if not isinstance(data, dict):
            raise ValueError("音频阈值 VAD 配置文件格式错误")
        defaults = cls()
        settings = cls(
            sample_rate=_integer(data.get("sample_rate"), defaults.sample_rate),
            analysis_window_ms=_integer(
                data.get("analysis_window_ms"), defaults.analysis_window_ms
            ),
            queue_depth=_integer(data.get("queue_depth"), defaults.queue_depth),
            noise_rms=_number(data.get("noise_rms"), defaults.noise_rms),
            speech_rms=_number(data.get("speech_rms"), defaults.speech_rms),
            rms_multiplier=_number(
                data.get("rms_multiplier"), defaults.rms_multiplier
            ),
            min_rms_delta=_number(
                data.get("min_rms_delta"), defaults.min_rms_delta
            ),
            speech_end_threshold_ratio=_number(
                data.get("speech_end_threshold_ratio"),
                defaults.speech_end_threshold_ratio,
            ),
            silence_ms=_integer(data.get("silence_ms"), defaults.silence_ms),
            min_speech_ms=_integer(
                data.get("min_speech_ms"), defaults.min_speech_ms
            ),
            calibration_ms=_integer(
                data.get("calibration_ms"), defaults.calibration_ms
            ),
        )
        settings.validate()
        return settings


@dataclass(frozen=True, slots=True)
class VadEvent:
    kind: str
    detail: str = ""


VadEventCallback = Callable[[VadEvent], None]


class AudioThresholdVadThread(Thread):
    def __init__(
        self,
        settings: AudioThresholdVadSettings,
        *,
        on_event: VadEventCallback,
        debug_logger: DebugLogger | None = None,
    ) -> None:
        super().__init__(name="audio-threshold-vad", daemon=True)
        settings.validate()
        self._settings = settings
        self._on_event = on_event
        self._logger = debug_logger
        self._audio: Queue[tuple[int, ...] | None] = Queue(maxsize=settings.queue_depth)
        self._stop_event = Event()
        self._dropped_frames = 0
        self._chunks = 0

    def feed(self, samples: tuple[int, ...]) -> None:
        if self._stop_event.is_set():
            return
        item = tuple(samples)
        try:
            self._audio.put_nowait(item)
        except Full:
            self._drop_oldest_audio_frame()
            try:
                self._audio.put_nowait(item)
            except Full:
                self._dropped_frames += 1

    def finish(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._audio.put_nowait(None)
                return
            except Full:
                self._drop_oldest_audio_frame()

    def cancel(self) -> None:
        self._stop_event.set()
        try:
            self._audio.put_nowait(None)
        except Full:
            pass

    def run(self) -> None:
        try:
            self._run()
        except BaseException as exc:
            self._log_exception("threshold_vad.thread_error", exc)
            self._on_event(VadEvent("error", str(exc)))
        finally:
            self._log(
                "threshold_vad.closed",
                dropped_frames=self._dropped_frames,
                chunks=self._chunks,
            )
            self._on_event(VadEvent("closed"))

    def _run(self) -> None:
        start_threshold = self._settings.speech_rms_threshold
        end_threshold = self._settings.speech_end_rms_threshold
        self._log(
            "threshold_vad.start",
            noise_rms=self._settings.noise_rms,
            speech_rms_threshold=start_threshold,
            speech_end_rms_threshold=end_threshold,
            silence_ms=self._settings.silence_ms,
            min_speech_ms=self._settings.min_speech_ms,
        )
        self._on_event(
            VadEvent(
                "status",
                f"音频阈值 VAD 已启动：静默 RMS {self._settings.noise_rms:.0f}，起始阈值 {start_threshold:.0f}，结束阈值 {end_threshold:.0f}",
            )
        )

        chunk: list[int] = []
        speech_started = False
        speech_ms = 0.0
        silence_ms = 0.0
        while not self._stop_event.is_set():
            try:
                item = self._audio.get(timeout=0.1)
            except Empty:
                continue
            if item is None:
                if chunk:
                    self._handle_chunk(tuple(chunk), final=True)
                break

            chunk.extend(item)
            while len(chunk) >= self._settings.chunk_samples:
                current = tuple(chunk[: self._settings.chunk_samples])
                del chunk[: self._settings.chunk_samples]
                rms = self._handle_chunk(current, final=False)
                duration_ms = len(current) * 1000.0 / self._settings.sample_rate

                active_threshold = end_threshold if speech_started else start_threshold
                if rms >= active_threshold:
                    speech_ms += duration_ms
                    silence_ms = 0.0
                    if not speech_started:
                        speech_started = True
                        self._on_event(VadEvent("speech_start", f"RMS {rms:.0f}"))
                elif speech_started:
                    silence_ms += duration_ms

                if (
                    speech_started
                    and speech_ms >= self._settings.min_speech_ms
                    and silence_ms >= self._settings.silence_ms
                ):
                    self._on_event(
                        VadEvent(
                            "speech_end",
                            f"低于结束阈值 {silence_ms:.0f} ms，最后 RMS {rms:.0f}",
                        )
                    )
                    self._stop_event.set()
                    break

    def _handle_chunk(self, samples: tuple[int, ...], *, final: bool) -> float:
        self._chunks += 1
        rms = pcm_rms(samples)
        peak = pcm_peak(samples)
        if self._chunks == 1 or self._chunks % 20 == 0 or final:
            self._log(
                "threshold_vad.chunk",
                chunk=self._chunks,
                final=final,
                samples=len(samples),
                rms=rms,
                peak=peak,
                start_threshold=self._settings.speech_rms_threshold,
                end_threshold=self._settings.speech_end_rms_threshold,
            )
        return rms

    def _drop_oldest_audio_frame(self) -> None:
        try:
            dropped = self._audio.get_nowait()
        except Empty:
            return
        if dropped is None:
            self.finish()
            return
        self._dropped_frames += 1
        if self._dropped_frames == 1 or self._dropped_frames % 100 == 0:
            self._on_event(
                VadEvent(
                    "status",
                    f"音频阈值 VAD 忙，已丢弃旧音频 {self._dropped_frames} 帧",
                )
            )

    def _log(self, name: str, **fields: object) -> None:
        if self._logger is not None:
            self._logger.event(name, **fields)

    def _log_exception(self, name: str, exc: BaseException, **fields: object) -> None:
        if self._logger is not None:
            self._logger.exception(name, exc, **fields)


def pcm_rms(samples: Iterable[int]) -> float:
    values = tuple(samples)
    if not values:
        return 0.0
    return math.sqrt(sum(float(value) * float(value) for value in values) / len(values))


def pcm_peak(samples: Iterable[int]) -> int:
    return max((abs(int(value)) for value in samples), default=0)


def calibrated_noise_rms(window_rms_values: Iterable[float]) -> float:
    values = sorted(float(value) for value in window_rms_values if value >= 0.0)
    if not values:
        return 0.0
    high_index = min(len(values) - 1, int((len(values) - 1) * 0.8))
    return max(float(median(values)), values[high_index])


def _integer(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
