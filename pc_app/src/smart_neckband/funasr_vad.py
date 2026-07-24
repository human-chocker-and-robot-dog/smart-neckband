from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Callable

from .mic_capture_debug import DebugLogger


@dataclass(frozen=True, slots=True)
class FunAsrVadSettings:
    model: str = "fsmn-vad"
    device: str = "cpu"
    sample_rate: int = 16000
    chunk_ms: int = 200
    queue_depth: int = 512
    model_kwargs_json: str = '{"disable_update": true}'
    generate_kwargs_json: str = ""

    @property
    def chunk_samples(self) -> int:
        return max(1, self.sample_rate * self.chunk_ms // 1000)

    def validate(self) -> None:
        if not self.model:
            raise ValueError("VAD 模型不能为空")
        if not self.device:
            raise ValueError("VAD device 不能为空")
        if self.sample_rate <= 0:
            raise ValueError("VAD sample_rate 必须大于 0")
        if self.chunk_ms < 20:
            raise ValueError("VAD chunk_ms 至少为 20")
        if self.queue_depth < 8:
            raise ValueError("VAD 队列深度至少为 8")
        _json_dict(self.model_kwargs_json, "VAD AutoModel kwargs")
        _json_dict(self.generate_kwargs_json, "VAD generate kwargs")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "device": self.device,
            "sample_rate": self.sample_rate,
            "chunk_ms": self.chunk_ms,
            "queue_depth": self.queue_depth,
            "model_kwargs_json": self.model_kwargs_json,
            "generate_kwargs_json": self.generate_kwargs_json,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "FunAsrVadSettings":
        if not isinstance(data, dict):
            raise ValueError("VAD 配置文件格式错误")
        defaults = cls()
        return cls(
            model=str(data.get("model", defaults.model)).strip() or defaults.model,
            device=str(data.get("device", defaults.device)).strip() or defaults.device,
            sample_rate=_integer(data.get("sample_rate"), defaults.sample_rate),
            chunk_ms=_integer(data.get("chunk_ms"), defaults.chunk_ms),
            queue_depth=_integer(data.get("queue_depth"), defaults.queue_depth),
            model_kwargs_json=str(
                data.get("model_kwargs_json", defaults.model_kwargs_json)
            ).strip(),
            generate_kwargs_json=str(
                data.get("generate_kwargs_json", defaults.generate_kwargs_json)
            ).strip(),
        )


@dataclass(frozen=True, slots=True)
class VadEvent:
    kind: str
    detail: str = ""


VadEventCallback = Callable[[VadEvent], None]


class FunAsrVadThread(Thread):
    def __init__(
        self,
        settings: FunAsrVadSettings,
        *,
        on_event: VadEventCallback,
        debug_logger: DebugLogger | None = None,
    ) -> None:
        super().__init__(name="funasr-vad", daemon=True)
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
                    f"VAD 忙，已丢弃旧音频 {self._dropped_frames} 帧",
                )
            )

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
            self._log_exception("vad.thread_error", exc)
            self._on_event(VadEvent("error", str(exc)))
        finally:
            self._log(
                "vad.closed",
                dropped_frames=self._dropped_frames,
                chunks=self._chunks,
            )
            self._on_event(VadEvent("closed"))

    def _run(self) -> None:
        if importlib.util.find_spec("torch") is None:
            raise RuntimeError(
                "缺少 PyTorch；FunASR AutoModel 需要 torch/torchaudio。"
                "请在 pc_app venv 中安装，例如："
                ".\\.venv\\Scripts\\python.exe -m pip install torch torchaudio"
            )
        try:
            import numpy as np
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError(
                "缺少 FunASR VAD；请运行 "
                "cd .\\pc_app; .\\.venv\\Scripts\\python.exe -m pip install -e .[vad]"
            ) from exc

        self._log(
            "vad.model_load_start",
            model=self._settings.model,
            device=self._settings.device,
            chunk_ms=self._settings.chunk_ms,
            queue_depth=self._settings.queue_depth,
            model_kwargs_json=self._settings.model_kwargs_json,
            generate_kwargs_json=self._settings.generate_kwargs_json,
        )
        self._on_event(VadEvent("status", "FunASR VAD 模型加载中…"))
        model_kwargs = _json_dict(self._settings.model_kwargs_json, "VAD AutoModel kwargs")
        model_kwargs.setdefault("device", self._settings.device)
        generate_kwargs = _json_dict(
            self._settings.generate_kwargs_json, "VAD generate kwargs"
        )
        model = AutoModel(
            model=self._settings.model,
            **model_kwargs,
        )
        cache: dict[str, object] = {}
        chunk: list[int] = []
        self._log("vad.model_load_ok")
        self._on_event(VadEvent("status", "FunASR VAD 已启动"))

        while not self._stop_event.is_set():
            try:
                item = self._audio.get(timeout=0.1)
            except Empty:
                continue
            if item is None:
                if chunk:
                    self._run_chunk(
                        model,
                        cache,
                        np,
                        tuple(chunk),
                        final=True,
                        generate_kwargs=generate_kwargs,
                    )
                    chunk.clear()
                break

            chunk.extend(item)
            while len(chunk) >= self._settings.chunk_samples:
                current = tuple(chunk[: self._settings.chunk_samples])
                del chunk[: self._settings.chunk_samples]
                if self._run_chunk(
                    model,
                    cache,
                    np,
                    current,
                    final=False,
                    generate_kwargs=generate_kwargs,
                ):
                    self._stop_event.set()
                    break

    def _run_chunk(
        self,
        model: object,
        cache: dict[str, object],
        np: object,
        samples: tuple[int, ...],
        *,
        final: bool,
        generate_kwargs: dict[str, object] | None = None,
    ) -> bool:
        audio = np.asarray(samples, dtype=np.float32) / 32768.0
        self._chunks += 1
        kwargs = dict(generate_kwargs or {})
        kwargs.setdefault("cache", cache)
        kwargs.setdefault("is_final", final)
        kwargs.setdefault("chunk_size", self._settings.chunk_ms)
        try:
            result = model.generate(input=audio, **kwargs)
        except BaseException as exc:
            self._log_exception(
                "vad.generate_error",
                exc,
                chunk=self._chunks,
                final=final,
                samples=len(samples),
            )
            raise
        self._log(
            "vad.generate_result",
            chunk=self._chunks,
            final=final,
            samples=len(samples),
            result=result,
        )
        return self._handle_result(result)

    def _handle_result(self, result: object) -> bool:
        should_stop = False
        for item in _iter_result_items(result):
            value = item.get("value")
            if not isinstance(value, list):
                continue
            for segment in value:
                if (
                    not isinstance(segment, (list, tuple))
                    or len(segment) < 2
                    or not isinstance(segment[0], int)
                    or not isinstance(segment[1], int)
                ):
                    continue
                begin_ms, end_ms = segment[0], segment[1]
                if begin_ms >= 0 and end_ms < 0:
                    self._on_event(VadEvent("speech_start", f"{begin_ms} ms"))
                elif end_ms >= 0:
                    self._on_event(VadEvent("speech_end", f"{end_ms} ms"))
                    should_stop = True
        return should_stop

    def _log(self, name: str, **fields: object) -> None:
        if self._logger is not None:
            self._logger.event(name, **fields)

    def _log_exception(self, name: str, exc: BaseException, **fields: object) -> None:
        if self._logger is not None:
            self._logger.exception(name, exc, **fields)


def _iter_result_items(result: object) -> tuple[dict[str, object], ...]:
    if isinstance(result, dict):
        return (result,)
    if isinstance(result, list):
        return tuple(item for item in result if isinstance(item, dict))
    return ()


def _integer(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_dict(value: str, label: str) -> dict[str, object]:
    text = value.strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{label} 必须是 JSON object")
    return data
