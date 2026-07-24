from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from urllib.parse import urlsplit


AGENT_REPLY_EVENT = "agent.reply.completed"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8080/v1/instructions"
DEFAULT_CALLBACK_PATH = "/agent-replies"
DEFAULT_CALLBACK_URL = "http://127.0.0.1:9080/agent-replies"
MAX_WEBHOOK_BODY_BYTES = 65_536


class InstructionState(str, Enum):
    PENDING = "pending"
    SUBMITTING = "submitting"
    RETRY_WAIT = "retry_wait"
    ACCEPTED = "accepted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WebhookSettings:
    gateway_url: str = DEFAULT_GATEWAY_URL
    callback_bind_host: str = "127.0.0.1"
    callback_port: int = 9080
    callback_path: str = DEFAULT_CALLBACK_PATH
    callback_public_url: str = DEFAULT_CALLBACK_URL
    request_timeout_s: float = 10.0
    require_device_receiving: bool = True
    auto_start_receiver: bool = False

    def validated(self) -> WebhookSettings:
        gateway_url = _validate_http_url(
            self.gateway_url,
            expected_path="/v1/instructions",
            field_name="Agent Gateway 地址",
        )
        callback_path = normalize_callback_path(self.callback_path)
        callback_public_url = _validate_http_url(
            self.callback_public_url,
            expected_path=callback_path,
            field_name="公开回调地址",
        )
        bind_host = self.callback_bind_host.strip()
        if not bind_host:
            raise ValueError("回调监听主机不能为空")
        if not 1 <= int(self.callback_port) <= 65_535:
            raise ValueError("回调监听端口必须在 1-65535 之间")
        if not 0 < float(self.request_timeout_s) <= 300:
            raise ValueError("请求超时必须在 0-300 秒之间")
        return WebhookSettings(
            gateway_url=gateway_url,
            callback_bind_host=bind_host,
            callback_port=int(self.callback_port),
            callback_path=callback_path,
            callback_public_url=callback_public_url,
            request_timeout_s=float(self.request_timeout_s),
            require_device_receiving=bool(self.require_device_receiving),
            auto_start_receiver=bool(self.auto_start_receiver),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> WebhookSettings:
        defaults = cls()
        return cls(
            gateway_url=str(data.get("gateway_url", defaults.gateway_url)),
            callback_bind_host=str(data.get("callback_bind_host", defaults.callback_bind_host)),
            callback_port=int(data.get("callback_port", defaults.callback_port)),
            callback_path=str(data.get("callback_path", defaults.callback_path)),
            callback_public_url=str(data.get("callback_public_url", defaults.callback_public_url)),
            request_timeout_s=float(data.get("request_timeout_s", defaults.request_timeout_s)),
            require_device_receiving=bool(
                data.get("require_device_receiving", defaults.require_device_receiving)
            ),
            auto_start_receiver=bool(data.get("auto_start_receiver", defaults.auto_start_receiver)),
        ).validated()


@dataclass(frozen=True, slots=True)
class InstructionRecord:
    instruction_id: str
    text: str
    state: InstructionState
    created_at: str
    updated_at: str
    attempt_count: int
    next_attempt_at: float | None
    last_http_status: int | None
    last_error: str | None
    accepted_at: str | None


@dataclass(frozen=True, slots=True)
class ReplyEvent:
    reply_id: str
    instruction_id: str
    text: str
    completed_at: str
    event: str = AGENT_REPLY_EVENT

    @classmethod
    def from_dict(cls, data: object) -> ReplyEvent:
        if not isinstance(data, dict):
            raise ValueError("回调请求体必须是 JSON object")
        required = ("event", "reply_id", "instruction_id", "text", "completed_at")
        if any(not isinstance(data.get(name), str) for name in required):
            raise ValueError("回调字段必须是字符串")
        event = str(data["event"])
        if event != AGENT_REPLY_EVENT:
            raise ValueError(f"不支持的回调事件：{event}")
        reply_id = str(data["reply_id"]).strip()
        instruction_id = str(data["instruction_id"]).strip()
        text = str(data["text"])
        completed_at = str(data["completed_at"]).strip()
        if not reply_id or not instruction_id or not text.strip() or not completed_at:
            raise ValueError("回调字段不能为空")
        try:
            datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("completed_at 必须是 ISO 8601 时间") from exc
        return cls(
            reply_id=reply_id,
            instruction_id=instruction_id,
            text=text,
            completed_at=completed_at,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "event": self.event,
            "reply_id": self.reply_id,
            "instruction_id": self.instruction_id,
            "text": self.text,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class SubmitOutcome:
    instruction_id: str
    state: InstructionState
    attempt_count: int
    http_status: int | None
    message: str
    retry_delay_s: float | None = None


def normalize_callback_path(path: str) -> str:
    normalized = path.strip()
    if not normalized:
        raise ValueError("回调路径不能为空")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if normalized != "/" and normalized.endswith("/"):
        raise ValueError("回调路径不能以 / 结尾")
    if "?" in normalized or "#" in normalized:
        raise ValueError("回调路径不能包含 query 或 fragment")
    return normalized


def ordinary_send_allowed(*, require_device_receiving: bool, receiving: bool) -> bool:
    return not require_device_receiving or receiving


def _validate_http_url(url: str, *, expected_path: str, field_name: str) -> str:
    normalized = url.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name}必须是绝对 HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field_name}不能包含 query 或 fragment")
    if parsed.path != expected_path:
        raise ValueError(f"{field_name}路径必须精确为 {expected_path}")
    return normalized
