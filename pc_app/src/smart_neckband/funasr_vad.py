from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Thread
from typing import Callable


@dataclass(frozen=True, slots=True)
class FunAsrVadSettings:
    model: str = "fsmn-vad"
    sample_rate: int = 16000
    chunk_ms: int = 200
    queue_depth: int = 96

    @property
    def chunk_samples(self) -> int:
        return max(1, self.sample_rate * self.chunk_ms // 1000)


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
    ) -> None:
        super().__init__(name="funasr-vad", daemon=True)
        self._settings = settings
        self._on_event = on_event
        self._audio: Queue[tuple[int, ...] | None] = Queue(maxsize=settings.queue_depth)
        self._stop_event = Event()

    def feed(self, samples: tuple[int, ...]) -> None:
        if self._stop_event.is_set():
            return
        try:
            self._audio.put_nowait(tuple(samples))
        except Exception:
            self._on_event(VadEvent("error", "VAD 音频队列已满"))

    def finish(self) -> None:
        try:
            self._audio.put_nowait(None)
        except Exception:
            self._stop_event.set()

    def cancel(self) -> None:
        self._stop_event.set()
        self.finish()

    def run(self) -> None:
        try:
            self._run()
        except BaseException as exc:
            self._on_event(VadEvent("error", str(exc)))
        finally:
            self._on_event(VadEvent("closed"))

    def _run(self) -> None:
        try:
            import numpy as np
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError(
                "缺少 FunASR VAD；请运行 "
                "cd .\\pc_app; .\\.venv\\Scripts\\python.exe -m pip install -e .[vad]"
            ) from exc

        self._on_event(VadEvent("status", "FunASR VAD 模型加载中…"))
        model = AutoModel(model=self._settings.model)
        cache: dict[str, object] = {}
        chunk: list[int] = []
        self._on_event(VadEvent("status", "FunASR VAD 已启动"))

        while not self._stop_event.is_set():
            try:
                item = self._audio.get(timeout=0.1)
            except Empty:
                continue
            if item is None:
                if chunk:
                    self._run_chunk(model, cache, np, tuple(chunk), final=True)
                    chunk.clear()
                break

            chunk.extend(item)
            while len(chunk) >= self._settings.chunk_samples:
                current = tuple(chunk[: self._settings.chunk_samples])
                del chunk[: self._settings.chunk_samples]
                if self._run_chunk(model, cache, np, current, final=False):
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
    ) -> bool:
        audio = np.asarray(samples, dtype=np.float32) / 32768.0
        result = model.generate(
            input=audio,
            cache=cache,
            is_final=final,
            chunk_size=self._settings.chunk_ms,
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


def _iter_result_items(result: object) -> tuple[dict[str, object], ...]:
    if isinstance(result, dict):
        return (result,)
    if isinstance(result, list):
        return tuple(item for item in result if isinstance(item, dict))
    return ()
