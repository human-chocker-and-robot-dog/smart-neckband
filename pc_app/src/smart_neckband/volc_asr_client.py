from __future__ import annotations

from dataclasses import dataclass
import json
import os
from queue import Empty, Queue
import struct
from threading import Event, Thread
import time
import uuid
from typing import Callable


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


@dataclass(frozen=True, slots=True)
class VolcAsrEvent:
    kind: str
    text: str = ""
    detail: str = ""


EventCallback = Callable[[VolcAsrEvent], None]


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
            "end_window_size": 800,
            "force_to_speech_time": 1000,
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
    ) -> None:
        super().__init__(name="volc-asr-client", daemon=True)
        settings.validate()
        self._settings = settings
        self._on_event = on_event
        self._audio: Queue[tuple[int, ...] | None] = Queue(maxsize=128)
        self._stop_event = Event()

    def feed(self, samples: tuple[int, ...]) -> None:
        if self._stop_event.is_set():
            return
        try:
            self._audio.put_nowait(tuple(samples))
        except Exception:
            self._on_event(VolcAsrEvent("error", detail="ASR 音频队列已满"))

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
            self._on_event(VolcAsrEvent("error", detail=str(exc)))
        finally:
            self._on_event(VolcAsrEvent("closed"))

    def _run(self) -> None:
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError(
                "缺少 websocket-client；请运行 .\\tools\\project.ps1 pc-setup"
            ) from exc

        headers = self._headers()
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
            while not self._stop_event.is_set():
                try:
                    item = self._audio.get(timeout=0.02)
                    if item is None:
                        ws.send_binary(build_audio_frame((), final=True))
                        final_sent = True
                        final_deadline = time.monotonic() + self._settings.final_timeout_s
                    else:
                        ws.send_binary(build_audio_frame(item, final=False))
                except Empty:
                    pass

                try:
                    message = ws.recv()
                except websocket.WebSocketTimeoutException:
                    message = None
                if message is None:
                    if final_sent and time.monotonic() >= final_deadline:
                        self._on_event(VolcAsrEvent("error", detail="ASR final 超时"))
                        break
                    continue
                if isinstance(message, str):
                    message = message.encode("utf-8")
                event = parse_server_frame(bytes(message))
                if event is None:
                    continue
                self._on_event(event)
                if event.kind in ("final", "error"):
                    break
        finally:
            try:
                ws.close()
            except Exception:
                pass

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
