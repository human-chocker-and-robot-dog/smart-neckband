from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from threading import Event, Thread
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .webhook_models import (
    InstructionRecord,
    InstructionState,
    MAX_WEBHOOK_BODY_BYTES,
    SubmitOutcome,
    WebhookSettings,
)
from .webhook_store import WebhookStore


LOGGER = logging.getLogger(__name__)


class UrlOpener(Protocol):
    def __call__(self, request: Request, *, timeout: float) -> object: ...


@dataclass(frozen=True, slots=True)
class AttemptResult:
    accepted: bool
    retryable: bool
    http_status: int | None
    message: str


class WebhookInstructionClient:
    def __init__(self, *, opener: UrlOpener = urlopen) -> None:
        self.opener = opener

    def submit_once(
        self,
        record: InstructionRecord,
        settings: WebhookSettings,
    ) -> AttemptResult:
        validated = settings.validated()
        payload = json.dumps(
            {"instruction_id": record.instruction_id, "text": record.text},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_WEBHOOK_BODY_BYTES:
            return AttemptResult(False, False, None, "Webhook 请求体超过 64 KiB")
        request = Request(
            validated.gateway_url,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            response = self.opener(request, timeout=validated.request_timeout_s)
            with response:
                status = int(response.getcode())
                response_body = response.read(MAX_WEBHOOK_BODY_BYTES + 1)
        except HTTPError as exc:
            status = int(exc.code)
            message = _read_http_error(exc)
            return AttemptResult(
                accepted=False,
                retryable=status == 503,
                http_status=status,
                message=f"HTTP {status}: {message}",
            )
        except (URLError, TimeoutError, OSError) as exc:
            return AttemptResult(
                accepted=False,
                retryable=True,
                http_status=None,
                message=f"{type(exc).__name__}: {exc}",
            )

        if status != 202:
            return AttemptResult(False, status == 503, status, f"未预期的 HTTP 状态：{status}")
        if len(response_body) > MAX_WEBHOOK_BODY_BYTES:
            return AttemptResult(False, False, status, "Gateway 响应体超过 64 KiB")
        try:
            response_data = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return AttemptResult(False, False, status, f"Gateway 202 响应不是有效 JSON：{exc}")
        if response_data != {"instruction_id": record.instruction_id, "status": "accepted"}:
            return AttemptResult(False, False, status, "Gateway 202 响应不符合 accepted 契约")
        return AttemptResult(True, False, status, "Gateway 已持久化受理")


class WebhookDispatcher:
    def __init__(
        self,
        *,
        store: WebhookStore,
        settings_provider: Callable[[], WebhookSettings],
        outcome_callback: Callable[[SubmitOutcome], None] | None = None,
        client: WebhookInstructionClient | None = None,
        retry_base_s: float = 1.0,
        retry_max_s: float = 60.0,
    ) -> None:
        self.store = store
        self.settings_provider = settings_provider
        self.outcome_callback = outcome_callback
        self.client = client or WebhookInstructionClient()
        self.retry_base_s = max(0.01, retry_base_s)
        self.retry_max_s = max(self.retry_base_s, retry_max_s)
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.store.recover_submitting()
        self._stop.clear()
        self._thread = Thread(target=self._run, name="WebhookDispatcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def enqueue_text(self, text: str, *, instruction_id: str | None = None) -> InstructionRecord:
        record = self.store.create_instruction(text, instruction_id=instruction_id)
        self._wake.set()
        return record

    def retry_now(self, instruction_id: str) -> InstructionRecord:
        record = self.store.retry_now(instruction_id)
        self._wake.set()
        return record

    def _run(self) -> None:
        while not self._stop.is_set():
            record = self.store.claim_next_due()
            if record is None:
                delay = self.store.seconds_until_next_due()
                self._wake.wait(timeout=min(1.0, delay) if delay is not None else 1.0)
                self._wake.clear()
                continue

            try:
                settings = self.settings_provider().validated()
                attempt = self.client.submit_once(record, settings)
            except Exception as exc:
                LOGGER.exception("Webhook submit setup failed")
                attempt = AttemptResult(False, False, None, f"{type(exc).__name__}: {exc}")

            if attempt.accepted:
                updated = self.store.mark_accepted(
                    record.instruction_id,
                    http_status=attempt.http_status or 202,
                )
                outcome = SubmitOutcome(
                    instruction_id=updated.instruction_id,
                    state=updated.state,
                    attempt_count=updated.attempt_count,
                    http_status=updated.last_http_status,
                    message=attempt.message,
                )
            elif attempt.retryable:
                delay = min(
                    self.retry_max_s,
                    self.retry_base_s * (2 ** min(16, max(0, record.attempt_count - 1))),
                )
                updated = self.store.mark_retry_wait(
                    record.instruction_id,
                    error=attempt.message,
                    delay_s=delay,
                    http_status=attempt.http_status,
                )
                outcome = SubmitOutcome(
                    instruction_id=updated.instruction_id,
                    state=updated.state,
                    attempt_count=updated.attempt_count,
                    http_status=updated.last_http_status,
                    message=attempt.message,
                    retry_delay_s=delay,
                )
            else:
                updated = self.store.mark_failed(
                    record.instruction_id,
                    error=attempt.message,
                    http_status=attempt.http_status,
                )
                outcome = SubmitOutcome(
                    instruction_id=updated.instruction_id,
                    state=updated.state,
                    attempt_count=updated.attempt_count,
                    http_status=updated.last_http_status,
                    message=attempt.message,
                )
            self._notify(outcome)

    def _notify(self, outcome: SubmitOutcome) -> None:
        if self.outcome_callback is None:
            return
        try:
            self.outcome_callback(outcome)
        except Exception:
            LOGGER.exception("Webhook outcome callback failed")


def _read_http_error(error: HTTPError) -> str:
    try:
        body = error.read(MAX_WEBHOOK_BODY_BYTES + 1)
    except Exception:
        return error.reason or "HTTP error"
    if len(body) > MAX_WEBHOOK_BODY_BYTES:
        return "响应体超过 64 KiB"
    text = body.decode("utf-8", errors="replace").strip()
    return text or str(error.reason or "HTTP error")
