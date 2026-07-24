from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from queue import Empty, Queue
import struct
from threading import Event, Thread
import time
import uuid
from typing import Callable

from .mic_capture_debug import DebugLogger


VOLC_ASR_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
MESSAGE_FULL_REQUEST = 1
MESSAGE_AUDIO = 2
MESSAGE_FULL_RESPONSE = 9
MESSAGE_ERROR = 15
FLAG_SEQUENCE = 1
FLAG_LAST = 2
SERIALIZATION_NONE = 0
SERIALIZATION_JSON = 1
COMPRESSION_NONE = 0


@dataclass(frozen=True, slots=True)
class VolcAsrSettings:
    endpoint: str = VOLC_ASR_ENDPOINT
    auth_mode: str = "api_key"
    api_key: str = ""
    app_key: str = ""
    access_key: str = ""
    resource_id: str = ""
    uid: str = "smart-neckband-pc"
    model_name: str = "bigmodel"
    connect_timeout_s: float = 10.0
    receive_timeout_s: float = 0.1
    final_timeout_s: float = 5.0
    audio_queue_depth: int = 512
    audio_chunk_ms: int = 200
    end_window_size_ms: int = 800
    force_to_speech_time_ms: int = 1000

    @classmethod
    def from_environment(cls) -> "VolcAsrSettings":
        auth_mode = os.getenv("VOLC_ASR_AUTH_MODE", "api_key").strip() or "api_key"
        return cls(
            endpoint=os.getenv("VOLC_ASR_ENDPOINT", VOLC_ASR_ENDPOINT).strip()
            or VOLC_ASR_ENDPOINT,
            auth_mode=auth_mode,
            api_key=os.getenv("VOLC_ASR_API_KEY", "").strip(),
            app_key=os.getenv("VOLC_ASR_APP_KEY", "").strip(),
            access_key=os.getenv("VOLC_ASR_ACCESS_KEY", "").strip(),
            resource_id=os.getenv("VOLC_ASR_RESOURCE_ID", "").strip(),
            uid=os.getenv("VOLC_ASR_UID", "smart-neckband-pc").strip()
            or "smart-neckband-pc",
            model_name=os.getenv("VOLC_ASR_MODEL_NAME", "bigmodel").strip()
            or "bigmodel",
            audio_queue_depth=_env_int("VOLC_ASR_AUDIO_QUEUE_DEPTH", 512),
            audio_chunk_ms=_env_int("VOLC_ASR_AUDIO_CHUNK_MS", 200),
            end_window_size_ms=_env_int("VOLC_ASR_END_WINDOW_SIZE_MS", 800),
            force_to_speech_time_ms=_env_int(
                "VOLC_ASR_FORCE_TO_SPEECH_TIME_MS", 1000
            ),
        )

    def validate(self) -> None:
        if not self.endpoint:
            raise ValueError("ASR endpoint 不能为空")
        if not self.resource_id:
            raise ValueError("Resource ID 不能为空")
        if self.auth_mode == "api_key":
            if not self.api_key:
                raise ValueError("API Key 不能为空")
        elif self.auth_mode == "legacy":
            if not self.app_key or not self.access_key:
                raise ValueError("App Key 和 Access Key 不能为空")
        else:
            raise ValueError(f"未知鉴权模式：{self.auth_mode}")
        if self.audio_queue_depth < 8:
            raise ValueError("ASR 音频队列深度至少为 8")
        if self.audio_chunk_ms < 20:
            raise ValueError("ASR 发送分片至少为 20 ms")
        if self.end_window_size_ms < 0:
            raise ValueError("ASR end_window_size 不能为负数")
        if self.force_to_speech_time_ms < 0:
            raise ValueError("ASR force_to_speech_time 不能为负数")

    def to_json_dict(self) -> dict[str, object]:
        return {
            "endpoint": self.endpoint,
            "auth_mode": self.auth_mode,
            "api_key": self.api_key,
            "app_key": self.app_key,
            "access_key": self.access_key,
            "resource_id": self.resource_id,
            "uid": self.uid,
            "model_name": self.model_name,
            "connect_timeout_s": self.connect_timeout_s,
            "receive_timeout_s": self.receive_timeout_s,
            "final_timeout_s": self.final_timeout_s,
            "audio_queue_depth": self.audio_queue_depth,
            "audio_chunk_ms": self.audio_chunk_ms,
            "end_window_size_ms": self.end_window_size_ms,
            "force_to_speech_time_ms": self.force_to_speech_time_ms,
        }

    @classmethod
    def from_json_dict(cls, data: object) -> "VolcAsrSettings":
        if not isinstance(data, dict):
            raise ValueError("ASR 配置文件格式错误")
        defaults = cls.from_environment()
        return cls(
            endpoint=str(data.get("endpoint", defaults.endpoint)).strip()
            or defaults.endpoint,
            auth_mode=str(data.get("auth_mode", defaults.auth_mode)).strip()
            or defaults.auth_mode,
            api_key=str(data.get("api_key", defaults.api_key)).strip(),
            app_key=str(data.get("app_key", defaults.app_key)).strip(),
            access_key=str(data.get("access_key", defaults.access_key)).strip(),
            resource_id=str(data.get("resource_id", defaults.resource_id)).strip(),
            uid=str(data.get("uid", defaults.uid)).strip() or defaults.uid,
            model_name=str(data.get("model_name", defaults.model_name)).strip()
            or defaults.model_name,
            connect_timeout_s=_number(
                data.get("connect_timeout_s"), defaults.connect_timeout_s
            ),
            receive_timeout_s=_number(
                data.get("receive_timeout_s"), defaults.receive_timeout_s
            ),
            final_timeout_s=_number(
                data.get("final_timeout_s"), defaults.final_timeout_s
            ),
            audio_queue_depth=_integer(
                data.get("audio_queue_depth"), defaults.audio_queue_depth
            ),
            audio_chunk_ms=_integer(data.get("audio_chunk_ms"), defaults.audio_chunk_ms),
            end_window_size_ms=_integer(
                data.get("end_window_size_ms"), defaults.end_window_size_ms
            ),
            force_to_speech_time_ms=_integer(
                data.get("force_to_speech_time_ms"),
                defaults.force_to_speech_time_ms,
            ),
        )


def load_volc_asr_settings(path: Path) -> VolcAsrSettings:
    if not path.exists():
        return VolcAsrSettings.from_environment()
    return VolcAsrSettings.from_json_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )


def save_volc_asr_settings(path: Path, settings: VolcAsrSettings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings.to_json_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@dataclass(frozen=True, slots=True)
class VolcAsrEvent:
    kind: str
    text: str = ""
    detail: str = ""


EventCallback = Callable[[VolcAsrEvent], None]


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


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


def build_client_frame(
    message_type: int,
    flags: int,
    serialization: int,
    payload: bytes,
) -> bytes:
    return (
        bytes(
            (
                0x11,
                (message_type << 4) | flags,
                (serialization << 4) | COMPRESSION_NONE,
                0,
            )
        )
        + struct.pack(">I", len(payload))
        + payload
    )


def build_full_request(settings: VolcAsrSettings) -> bytes:
    request = {
        "user": {"uid": settings.uid or f"smart-neckband-{uuid.uuid4().hex[:8]}"},
        "audio": {
            "format": "pcm",
            "rate": 16000,
            "bits": 16,
            "channel": 1,
            "codec": "raw",
        },
        "request": {
            "model_name": settings.model_name,
            "enable_punc": True,
            "enable_itn": True,
            "show_utterances": True,
            "end_window_size": settings.end_window_size_ms,
            "force_to_speech_time": settings.force_to_speech_time_ms,
        },
    }
    payload = json.dumps(
        request, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return build_client_frame(
        MESSAGE_FULL_REQUEST,
        0,
        SERIALIZATION_JSON,
        payload,
    )


def build_audio_frame(samples: tuple[int, ...], *, final: bool) -> bytes:
    if samples:
        payload = struct.pack(f"<{len(samples)}h", *samples)
    else:
        payload = b""
    return build_client_frame(
        MESSAGE_AUDIO,
        FLAG_LAST if final else 0,
        SERIALIZATION_NONE,
        payload,
    )


def parse_server_frame(frame: bytes) -> VolcAsrEvent | None:
    if len(frame) < 8 or frame[0] >> 4 != 1:
        raise ValueError("bad ASR protocol header")
    header_size = (frame[0] & 0x0F) * 4
    if header_size < 4 or header_size > len(frame):
        raise ValueError("bad ASR header size")
    message_type = frame[1] >> 4
    flags = frame[1] & 0x0F
    serialization = frame[2] >> 4
    compression = frame[2] & 0x0F
    if compression != COMPRESSION_NONE:
        raise ValueError("compressed ASR response is not supported")

    offset = header_size
    sequence = 0
    if message_type == MESSAGE_ERROR:
        if len(frame) - offset < 8:
            raise ValueError("short ASR error frame")
        error_code, payload_size = struct.unpack_from(">II", frame, offset)
        offset += 8
        if payload_size != len(frame) - offset:
            raise ValueError("ASR payload length mismatch")
        detail = frame[offset:].decode("utf-8", errors="replace")
        return VolcAsrEvent("error", detail=f"{error_code}: {detail}")

    if flags & FLAG_SEQUENCE:
        if len(frame) - offset < 8:
            raise ValueError("short ASR response frame")
        sequence = struct.unpack_from(">i", frame, offset)[0]
        offset += 4

    if len(frame) - offset < 4:
        raise ValueError("short ASR payload length")
    payload_size = struct.unpack_from(">I", frame, offset)[0]
    offset += 4
    if payload_size != len(frame) - offset:
        raise ValueError("ASR payload length mismatch")
    if message_type != MESSAGE_FULL_RESPONSE or serialization != SERIALIZATION_JSON:
        return None

    payload = json.loads(frame[offset:].decode("utf-8"))
    text = _find_text(payload)
    if not text:
        return None
    final = bool(flags & FLAG_LAST) or sequence < 0 or _has_definite(payload)
    return VolcAsrEvent("final" if final else "partial", text=text)


def _find_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str) and text:
            return text
        utterances = result.get("utterances")
        if isinstance(utterances, list):
            for utterance in reversed(utterances):
                if isinstance(utterance, dict):
                    text = utterance.get("text")
                    if isinstance(text, str) and text:
                        return text
    text = payload.get("text")
    return text if isinstance(text, str) else ""


def _has_definite(payload: object) -> bool:
    if isinstance(payload, dict):
        if payload.get("definite") is True:
            return True
        return any(_has_definite(value) for value in payload.values())
    if isinstance(payload, list):
        return any(_has_definite(value) for value in payload)
    return False


class VolcAsrClientThread(Thread):
    def __init__(
        self,
        settings: VolcAsrSettings,
        *,
        on_event: EventCallback,
        debug_logger: DebugLogger | None = None,
    ) -> None:
        super().__init__(name="volc-asr-client", daemon=True)
        settings.validate()
        self._settings = settings
        self._on_event = on_event
        self._logger = debug_logger
        self._audio: Queue[tuple[int, ...] | None] = Queue(
            maxsize=settings.audio_queue_depth
        )
        self._stop_event = Event()
        self._finish_event = Event()
        self._dropped_frames = 0
        self._sent_chunks = 0

    def feed(self, samples: tuple[int, ...]) -> None:
        if self._stop_event.is_set():
            return
        if self._finish_event.is_set():
            return
        item = tuple(samples)
        try:
            self._audio.put_nowait(item)
        except Exception:
            self._drop_oldest_audio_frame()
            try:
                self._audio.put_nowait(item)
            except Exception:
                self._dropped_frames += 1
            self._log_queue_drop()

    def finish(self) -> None:
        self._finish_event.set()
        while True:
            try:
                self._audio.put_nowait(None)
                self._log("asr.finish_enqueued", queue_size=self._audio.qsize())
                return
            except Exception:
                if not self._drop_oldest_audio_frame():
                    self._stop_event.set()
                    return

    def cancel(self) -> None:
        self._stop_event.set()
        try:
            self._audio.put_nowait(None)
        except Exception:
            pass

    def run(self) -> None:
        try:
            self._run()
        except BaseException as exc:
            self._log_exception("asr.thread_error", exc)
            self._on_event(VolcAsrEvent("error", detail=str(exc)))
        finally:
            self._log(
                "asr.closed",
                dropped_frames=self._dropped_frames,
                sent_chunks=self._sent_chunks,
            )
            self._on_event(VolcAsrEvent("closed"))

    def _run(self) -> None:
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError(
                "缺少 websocket-client；请运行 .\\tools\\project.ps1 pc-setup"
            ) from exc

        headers = self._headers()
        self._log(
            "asr.connect_start",
            endpoint=self._settings.endpoint,
            queue_depth=self._settings.audio_queue_depth,
            audio_chunk_ms=self._settings.audio_chunk_ms,
            end_window_size_ms=self._settings.end_window_size_ms,
            force_to_speech_time_ms=self._settings.force_to_speech_time_ms,
        )
        self._on_event(VolcAsrEvent("status", detail="ASR 连接中…"))
        ws = websocket.create_connection(
            self._settings.endpoint,
            header=headers,
            timeout=self._settings.connect_timeout_s,
        )
        try:
            ws.settimeout(self._settings.receive_timeout_s)
            self._on_event(VolcAsrEvent("status", detail="ASR 已连接，发送请求…"))
            ws.send_binary(build_full_request(self._settings))
            self._on_event(VolcAsrEvent("status", detail="ASR 正在识别"))
            final_sent = False
            final_deadline = 0.0
            pending: list[int] = []
            chunk_samples = max(1, 16000 * self._settings.audio_chunk_ms // 1000)
            while not self._stop_event.is_set():
                try:
                    item = self._audio.get(timeout=0.02)
                    if item is None:
                        ws.send_binary(build_audio_frame(tuple(pending), final=True))
                        self._sent_chunks += 1
                        self._log(
                            "asr.send_final",
                            samples=len(pending),
                            dropped_frames=self._dropped_frames,
                        )
                        pending.clear()
                        final_sent = True
                        final_deadline = time.monotonic() + self._settings.final_timeout_s
                    else:
                        pending.extend(item)
                        while len(pending) >= chunk_samples:
                            current = tuple(pending[:chunk_samples])
                            del pending[:chunk_samples]
                            ws.send_binary(build_audio_frame(current, final=False))
                            self._sent_chunks += 1
                            if self._sent_chunks == 1 or self._sent_chunks % 25 == 0:
                                self._log(
                                    "asr.send_audio",
                                    sent_chunks=self._sent_chunks,
                                    samples=len(current),
                                    queue_size=self._audio.qsize(),
                                )
                except Empty:
                    pass

                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    message = None
                if message is None:
                    if final_sent and time.monotonic() >= final_deadline:
                        self._log("asr.final_timeout")
                        self._on_event(VolcAsrEvent("error", detail="ASR final 超时"))
                        break
                    continue
                if isinstance(message, str):
                    message = message.encode("utf-8")
                event = parse_server_frame(bytes(message))
                if event is None:
                    continue
                self._log(
                    "asr.event",
                    kind=event.kind,
                    text=event.text,
                    detail=event.detail,
                )
                self._on_event(event)
                if event.kind in ("final", "error"):
                    break
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def _drop_oldest_audio_frame(self) -> bool:
        try:
            dropped = self._audio.get_nowait()
        except Empty:
            return False
        if dropped is None:
            try:
                self._audio.put_nowait(None)
            except Exception:
                pass
            return False
        self._dropped_frames += 1
        return True

    def _log_queue_drop(self) -> None:
        self._log(
            "asr.queue_drop",
            dropped_frames=self._dropped_frames,
            queue_size=self._audio.qsize(),
            queue_depth=self._settings.audio_queue_depth,
        )
        if self._dropped_frames == 1 or self._dropped_frames % 100 == 0:
            self._on_event(
                VolcAsrEvent(
                    "status",
                    detail=f"ASR 忙，已丢弃旧音频 {self._dropped_frames} 帧",
                )
            )

    def _log(self, name: str, **fields: object) -> None:
        if self._logger is not None:
            self._logger.event(name, **fields)

    def _log_exception(self, name: str, exc: BaseException, **fields: object) -> None:
        if self._logger is not None:
            self._logger.exception(name, exc, **fields)

    def _headers(self) -> list[str]:
        connection_id = str(uuid.uuid4())
        if self._settings.auth_mode == "api_key":
            return [
                f"X-Api-Key: {self._settings.api_key}",
                f"X-Api-Resource-Id: {self._settings.resource_id}",
                f"X-Api-Connect-Id: {connection_id}",
            ]
        return [
            f"X-Api-App-Key: {self._settings.app_key}",
            f"X-Api-Access-Key: {self._settings.access_key}",
            f"X-Api-Resource-Id: {self._settings.resource_id}",
            f"X-Api-Connect-Id: {connection_id}",
        ]
