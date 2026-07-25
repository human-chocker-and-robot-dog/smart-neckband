from __future__ import annotations

from io import BytesIO
import http.client
import json
from threading import Event
from urllib.error import HTTPError, URLError

import pytest

from smart_neckband.webhook_client import AttemptResult, WebhookDispatcher, WebhookInstructionClient
from smart_neckband.webhook_models import (
    InstructionState,
    ReplyEvent,
    WebhookSettings,
    ordinary_send_allowed,
)
from smart_neckband.webhook_receiver import ReplyWebhookServer
from smart_neckband.webhook_store import ReplyConflictError, WebhookStore


class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, object]) -> None:
        self.status = status
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, _limit: int) -> bytes:
        return self.body


def test_settings_require_exact_gateway_and_callback_paths() -> None:
    settings = WebhookSettings().validated()
    assert settings.gateway_url.endswith("/v1/instructions")
    assert settings.callback_public_url.endswith("/agent-replies")

    with pytest.raises(ValueError, match="精确为 /v1/instructions"):
        WebhookSettings(gateway_url="http://127.0.0.1:8080/v1/instructions/").validated()
    with pytest.raises(ValueError, match="不能包含 query"):
        WebhookSettings(gateway_url="http://127.0.0.1:8080/v1/instructions?test=1").validated()


def test_store_persists_instruction_before_submission_and_reuses_exact_id(tmp_path) -> None:
    store = WebhookStore(tmp_path / "webhook.sqlite3")
    first = store.create_instruction("向前走 1 米", instruction_id="request-1")
    duplicate = store.create_instruction("向前走 1 米", instruction_id="request-1")

    assert first == duplicate
    assert first.state is InstructionState.PENDING
    claimed = store.claim_next_due()
    assert claimed is not None
    assert claimed.instruction_id == "request-1"
    assert claimed.text == "向前走 1 米"
    assert claimed.state is InstructionState.SUBMITTING
    assert claimed.attempt_count == 1


def test_client_posts_only_contract_fields_and_accepts_202(tmp_path) -> None:
    store = WebhookStore(tmp_path / "webhook.sqlite3")
    record = store.create_instruction("测试文本", instruction_id="stable-id")
    requests: list[object] = []

    def opener(request: object, *, timeout: float) -> _FakeResponse:
        requests.append(request)
        assert timeout == 10.0
        return _FakeResponse(202, {"instruction_id": "stable-id", "status": "accepted"})

    result = WebhookInstructionClient(opener=opener).submit_once(record, WebhookSettings())

    assert result.accepted
    assert not result.retryable
    request = requests[0]
    assert json.loads(getattr(request, "data").decode("utf-8")) == {
        "instruction_id": "stable-id",
        "text": "测试文本",
    }


@pytest.mark.parametrize(
    ("error", "retryable", "http_status"),
    [
        (
            HTTPError(
                "http://127.0.0.1:8080/v1/instructions",
                503,
                "unavailable",
                hdrs=None,
                fp=BytesIO(b'{"error":"persistence_unavailable"}'),
            ),
            True,
            503,
        ),
        (
            HTTPError(
                "http://127.0.0.1:8080/v1/instructions",
                409,
                "conflict",
                hdrs=None,
                fp=BytesIO(b'{"error":"instruction_id_conflict"}'),
            ),
            False,
            409,
        ),
        (URLError("offline"), True, None),
    ],
)
def test_client_classifies_retryable_and_terminal_errors(
    tmp_path,
    error: Exception,
    retryable: bool,
    http_status: int | None,
) -> None:
    store = WebhookStore(tmp_path / "webhook.sqlite3")
    record = store.create_instruction("测试", instruction_id="stable-id")

    def opener(_request: object, *, timeout: float) -> object:
        del timeout
        raise error

    result = WebhookInstructionClient(opener=opener).submit_once(record, WebhookSettings())

    assert not result.accepted
    assert result.retryable is retryable
    assert result.http_status == http_status


def test_store_deduplicates_identical_reply_and_rejects_conflict(tmp_path) -> None:
    store = WebhookStore(tmp_path / "webhook.sqlite3")
    event = ReplyEvent(
        reply_id="reply-1",
        instruction_id="instruction-1",
        text="已经受理。",
        completed_at="2026-07-23T12:30:00.000Z",
    )

    assert store.save_reply(event)
    assert not store.save_reply(event)
    with pytest.raises(ReplyConflictError):
        store.save_reply(
            ReplyEvent(
                reply_id="reply-1",
                instruction_id="instruction-1",
                text="不同回复",
                completed_at=event.completed_at,
            )
        )


def test_dispatcher_retries_with_identical_id_and_text_then_accepts(tmp_path) -> None:
    store = WebhookStore(tmp_path / "webhook.sqlite3")
    calls: list[tuple[str, str]] = []
    accepted = Event()

    class Client:
        def submit_once(self, record: object, settings: WebhookSettings) -> AttemptResult:
            del settings
            calls.append((getattr(record, "instruction_id"), getattr(record, "text")))
            if len(calls) == 1:
                return AttemptResult(False, True, 503, "temporary")
            return AttemptResult(True, False, 202, "accepted")

    dispatcher = WebhookDispatcher(
        store=store,
        settings_provider=WebhookSettings,
        outcome_callback=lambda outcome: accepted.set()
        if outcome.state is InstructionState.ACCEPTED
        else None,
        client=Client(),  # type: ignore[arg-type]
        retry_base_s=0.01,
        retry_max_s=0.01,
    )
    dispatcher.start()
    try:
        record = dispatcher.enqueue_text("完全相同的文本", instruction_id="stable-id")
        assert accepted.wait(2.0)
    finally:
        dispatcher.stop()

    assert calls == [
        (record.instruction_id, record.text),
        (record.instruction_id, record.text),
    ]
    final = store.get_instruction(record.instruction_id)
    assert final.state is InstructionState.ACCEPTED
    assert final.attempt_count == 2


def test_reply_server_persists_before_204_and_deduplicates_callback(tmp_path) -> None:
    store = WebhookStore(tmp_path / "webhook.sqlite3")
    callbacks: list[ReplyEvent] = []
    callback_seen = Event()

    def on_reply(event: ReplyEvent) -> None:
        callbacks.append(event)
        callback_seen.set()

    server = ReplyWebhookServer(
        store=store,
        host="127.0.0.1",
        port=0,
        path="/agent-replies",
        reply_callback=on_reply,
    )
    port = server.start()
    payload = {
        "event": "agent.reply.completed",
        "reply_id": "reply-1",
        "instruction_id": "instruction-1",
        "text": "已发送停止指令。",
        "completed_at": "2026-07-23T12:30:00.000Z",
    }
    try:
        assert _post_json(port, "/agent-replies", payload) == 204
        assert callback_seen.wait(1.0)
        assert len(store.list_replies()) == 1
        assert _post_json(port, "/agent-replies", payload) == 204
        assert len(callbacks) == 1
        assert len(store.list_replies()) == 1
    finally:
        server.stop()


def test_ordinary_send_gate_requires_actual_receiving_state() -> None:
    assert ordinary_send_allowed(require_device_receiving=False, receiving=False)
    assert ordinary_send_allowed(require_device_receiving=True, receiving=True)
    assert not ordinary_send_allowed(require_device_receiving=True, receiving=False)


def _post_json(port: int, path: str, payload: dict[str, object]) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()
