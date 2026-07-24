from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time

import pytest

from smart_neckband.health_contract import load_health_contract
from smart_neckband.health_state import BuiltHealthState
from smart_neckband.health_store import HealthStore
from smart_neckband.health_webhook import (
    HealthWebhookClient,
    HealthWebhookDispatcher,
    HealthWebhookReceiver,
    HealthWebhookReceiverStore,
    WebhookDeliveryResult,
    canonical_webhook_body,
    parse_secret_hex,
    retry_delay_seconds,
    signature_header,
    validate_health_webhook_url,
)
from smart_neckband.webhook_store import WebhookStore


def golden() -> dict:
    return load_health_contract()["x-golden"]


def signed_headers(
    raw_body: bytes,
    *,
    secret: bytes,
    timestamp: int,
    notification_id: str | None = None,
) -> dict[str, str]:
    if notification_id is None:
        notification_id = json.loads(raw_body)["notification_id"]
    return {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(raw_body)),
        "X-Smart-Collar-Key-Id": "key-1",
        "X-Smart-Collar-Timestamp": str(timestamp),
        "X-Smart-Collar-Notification-Id": notification_id,
        "X-Smart-Collar-Signature": signature_header(
            secret,
            timestamp,
            raw_body,
        ),
    }


def enqueue_lead_off(store: HealthStore, *, now_ns: int = 1_000_000_000) -> None:
    document = deepcopy(golden()["wearer_state_live"])
    document["signal"].update(
        {
            "lead_off": True,
            "quality_score": 0.1,
            "quality_level": "bad",
            "quality_rank": 1,
        }
    )
    for metric in document["heart"].values():
        if isinstance(metric, dict) and "valid" in metric:
            metric.update(
                {
                    "value": None,
                    "valid": False,
                    "observed_at": None,
                    "age_ms": None,
                    "unavailable_reason": "lead_off",
                }
            )
    store.commit_state(
        BuiltHealthState(
            document=document,
            committed_monotonic_ns=now_ns,
            ecg_received_monotonic_ns=now_ns - 120_000_000,
            transport_received_monotonic_ns=now_ns - 35_000_000,
            status_evidence_key="status-1",
            clipping_window_full=True,
        )
    )


def test_secret_parser_url_policy_and_hmac_golden() -> None:
    vector = golden()
    secret = parse_secret_hex(vector["webhook_secret_hex"])
    raw_body = canonical_webhook_body(vector["webhook_request"])

    assert raw_body.decode() == vector["webhook_canonical_body_utf8"]
    assert (
        signature_header(secret, vector["webhook_timestamp"], raw_body)
        == vector["webhook_signature_v1"]
    )
    assert validate_health_webhook_url(
        "http://127.0.0.1:8766/v1/health-events"
    )
    assert validate_health_webhook_url(
        "https://example.test/v1/health-events"
    )
    with pytest.raises(ValueError):
        validate_health_webhook_url("http://192.168.1.5/v1/health-events")

    invalid_secrets = (
        None,
        "",
        "0x" + vector["webhook_secret_hex"],
        vector["webhook_secret_hex"].upper(),
        vector["webhook_secret_hex"] + "\n",
        "a" * 63,
        "g" * 64,
    )
    for invalid in invalid_secrets:
        with pytest.raises(ValueError):
            parse_secret_hex(invalid)


def test_mock_receiver_validates_persists_deduplicates_and_conflicts(tmp_path) -> None:
    vector = golden()
    secret = parse_secret_hex(vector["webhook_secret_hex"])
    raw_body = vector["webhook_canonical_body_utf8"].encode()
    timestamp = vector["webhook_timestamp"]
    store = HealthWebhookReceiverStore(tmp_path / "receiver.sqlite3")
    receiver = HealthWebhookReceiver(store=store, keys={"key-1": secret})
    headers = signed_headers(raw_body, secret=secret, timestamp=timestamp)

    accepted = receiver.handle(
        method="POST",
        path="/v1/health-events",
        headers=headers,
        raw_body=raw_body,
        now_epoch_s=timestamp,
    )
    duplicate = receiver.handle(
        method="POST",
        path="/v1/health-events",
        headers=headers,
        raw_body=raw_body,
        now_epoch_s=timestamp,
    )

    changed_payload = deepcopy(vector["webhook_request"])
    changed_payload["sent_at"] = "2026-07-23T02:10:03.121Z"
    changed_body = canonical_webhook_body(changed_payload)
    changed_headers = signed_headers(
        changed_body,
        secret=secret,
        timestamp=timestamp,
    )
    conflict = receiver.handle(
        method="POST",
        path="/v1/health-events",
        headers=changed_headers,
        raw_body=changed_body,
        now_epoch_s=timestamp,
    )

    assert accepted[0] == 202
    assert json.loads(accepted[2])["status"] == "accepted"
    assert duplicate[0] == 202
    assert json.loads(duplicate[2])["status"] == "duplicate"
    assert conflict[0] == 409
    assert json.loads(conflict[2]) == {"error": "notification_id_conflict"}
    assert store.queue_count() == 1


def test_receiver_validation_order_and_error_shapes(tmp_path) -> None:
    vector = golden()
    secret = parse_secret_hex(vector["webhook_secret_hex"])
    raw_body = vector["webhook_canonical_body_utf8"].encode()
    timestamp = vector["webhook_timestamp"]
    receiver = HealthWebhookReceiver(
        store=HealthWebhookReceiverStore(tmp_path / "receiver.sqlite3"),
        keys={"key-1": secret},
    )
    valid = signed_headers(raw_body, secret=secret, timestamp=timestamp)

    method = receiver.handle(
        method="GET",
        path="/wrong",
        headers={},
        raw_body=b"",
        now_epoch_s=timestamp,
    )
    path = receiver.handle(
        method="POST",
        path="/wrong",
        headers={},
        raw_body=b"",
        now_epoch_s=timestamp,
    )
    content_type = receiver.handle(
        method="POST",
        path="/v1/health-events",
        headers={},
        raw_body=raw_body,
        now_epoch_s=timestamp,
    )
    bad_timestamp_headers = dict(valid)
    bad_timestamp_headers["X-Smart-Collar-Timestamp"] = "0"
    bad_timestamp = receiver.handle(
        method="POST",
        path="/v1/health-events",
        headers=bad_timestamp_headers,
        raw_body=raw_body,
        now_epoch_s=timestamp,
    )
    bad_signature_headers = dict(valid)
    bad_signature_headers["X-Smart-Collar-Signature"] = "v1=" + "0" * 64
    bad_signature = receiver.handle(
        method="POST",
        path="/v1/health-events",
        headers=bad_signature_headers,
        raw_body=raw_body,
        now_epoch_s=timestamp,
    )

    assert method[0] == 405 and method[1]["Allow"] == "POST"
    assert json.loads(method[2]) == {"error": "method_not_allowed"}
    assert path[0] == 404
    assert content_type[0] == 415
    assert json.loads(bad_timestamp[2]) == {"error": "timestamp_out_of_range"}
    assert json.loads(bad_signature[2]) == {"error": "invalid_signature"}
    for response in (method, path, content_type, bad_timestamp, bad_signature):
        assert set(json.loads(response[2])) == {"error"}


def test_receiver_rejects_complete_length_timestamp_and_body_matrix(
    tmp_path,
) -> None:
    vector = golden()
    secret = parse_secret_hex(vector["webhook_secret_hex"])
    raw_body = vector["webhook_canonical_body_utf8"].encode()
    timestamp = int(vector["webhook_timestamp"])
    receiver = HealthWebhookReceiver(
        store=HealthWebhookReceiverStore(tmp_path / "receiver.sqlite3"),
        keys={"key-1": secret},
    )
    valid = signed_headers(raw_body, secret=secret, timestamp=timestamp)

    cases: list[tuple[dict[str, str], bytes, int, str]] = []
    for value in (None, "", "-1", "+1", "1.0", "01"):
        headers = dict(valid)
        if value is None:
            headers.pop("Content-Length")
        else:
            headers["Content-Length"] = value
        cases.append((headers, raw_body, 400, "invalid_request"))
    headers = dict(valid)
    headers["Transfer-Encoding"] = "chunked"
    cases.append((headers, raw_body, 400, "invalid_request"))
    headers = dict(valid)
    headers["Content-Length"] = str(len(raw_body) + 1)
    cases.append((headers, raw_body, 400, "invalid_request"))
    headers = dict(valid)
    headers["Content-Length"] = "65537"
    cases.append((headers, raw_body, 413, "body_too_large"))
    oversized = b"x" * 65_537
    cases.append(
        (
            signed_headers(
                oversized,
                secret=secret,
                timestamp=timestamp,
                notification_id=vector["webhook_request"]["notification_id"],
            ),
            oversized,
            413,
            "body_too_large",
        )
    )

    for value in (
        "01784772600",
        "+1784772600",
        "-1784772600",
        "1784772600.0",
        "253402300800",
        str(timestamp - 301),
        str(timestamp + 301),
    ):
        headers = dict(valid)
        headers["X-Smart-Collar-Timestamp"] = value
        cases.append((headers, raw_body, 401, "timestamp_out_of_range"))

    non_utf8 = b"\xff"
    cases.append(
        (
            signed_headers(
                non_utf8,
                secret=secret,
                timestamp=timestamp,
                notification_id=vector["webhook_request"]["notification_id"],
            ),
            non_utf8,
            400,
            "invalid_request",
        )
    )
    invalid_schema = b"{}"
    cases.append(
        (
            signed_headers(
                invalid_schema,
                secret=secret,
                timestamp=timestamp,
                notification_id=vector["webhook_request"]["notification_id"],
            ),
            invalid_schema,
            400,
            "invalid_request",
        )
    )
    mismatch = dict(valid)
    mismatch["X-Smart-Collar-Notification-Id"] = (
        "25ec8019-0dc5-4f1e-802b-633e29c545b6"
    )
    cases.append((mismatch, raw_body, 400, "invalid_request"))

    for headers, body, status, error in cases:
        response = receiver.handle(
            method="POST",
            path="/v1/health-events",
            headers=headers,
            raw_body=body,
            now_epoch_s=timestamp,
        )
        assert response[0] == status
        assert json.loads(response[2]) == {"error": error}


def test_receiver_accept_header_is_ignored_and_store_failure_is_503(
    tmp_path,
) -> None:
    vector = golden()
    secret = parse_secret_hex(vector["webhook_secret_hex"])
    raw_body = vector["webhook_canonical_body_utf8"].encode()
    timestamp = int(vector["webhook_timestamp"])
    headers = signed_headers(raw_body, secret=secret, timestamp=timestamp)
    headers["Accept"] = "text/plain"
    receiver = HealthWebhookReceiver(
        store=HealthWebhookReceiverStore(tmp_path / "receiver.sqlite3"),
        keys={"key-1": secret},
    )
    assert receiver.handle(
        method="POST",
        path="/v1/health-events",
        headers=headers,
        raw_body=raw_body,
        now_epoch_s=timestamp,
    )[0] == 202

    class BrokenStore:
        def persist(self, _notification_id, _raw_body):
            raise OSError("database unavailable")

    broken = HealthWebhookReceiver(
        store=BrokenStore(),
        keys={"key-1": secret},
    )
    response = broken.handle(
        method="POST",
        path="/v1/health-events",
        headers=headers,
        raw_body=raw_body,
        now_epoch_s=timestamp,
    )
    assert response[0] == 503
    assert json.loads(response[2]) == {"error": "internal_error"}


def test_receiver_concurrent_same_id_is_single_persisted_notification(
    tmp_path,
) -> None:
    vector = golden()
    secret = parse_secret_hex(vector["webhook_secret_hex"])
    first_body = vector["webhook_canonical_body_utf8"].encode()
    changed = deepcopy(vector["webhook_request"])
    changed["sent_at"] = "2026-07-23T02:10:03.121Z"
    second_body = canonical_webhook_body(changed)
    timestamp = int(vector["webhook_timestamp"])
    store = HealthWebhookReceiverStore(tmp_path / "receiver.sqlite3")
    receiver = HealthWebhookReceiver(store=store, keys={"key-1": secret})

    def submit(body: bytes) -> tuple[int, str]:
        response = receiver.handle(
            method="POST",
            path="/v1/health-events",
            headers=signed_headers(
                body,
                secret=secret,
                timestamp=timestamp,
            ),
            raw_body=body,
            now_epoch_s=timestamp,
        )
        return response[0], json.loads(response[2]).get(
            "status",
            json.loads(response[2]).get("error"),
        )

    bodies = [first_body, second_body] * 8
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(submit, bodies))

    assert sum(status == "accepted" for _, status in outcomes) == 1
    assert all(code in {202, 409} for code, _ in outcomes)
    assert store.queue_count() == 1


def test_client_never_follows_any_redirect_status() -> None:
    destination_hits: list[str] = []

    class DestinationHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            destination_hits.append("GET")
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            destination_hits.append("POST")
            self.send_response(202)
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    destination = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        DestinationHandler,
    )
    destination_thread = Thread(target=destination.serve_forever, daemon=True)
    destination_thread.start()

    class RedirectHandler(BaseHTTPRequestHandler):
        code = 301

        def do_POST(self):
            self.send_response(self.code)
            self.send_header(
                "Location",
                (
                    "http://127.0.0.1:"
                    f"{destination.server_address[1]}/captured"
                ),
            )
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    origin = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    origin_thread = Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()
    try:
        for code in (301, 302, 303, 307, 308):
            RedirectHandler.code = code
            client = HealthWebhookClient(
                url=(
                    "http://127.0.0.1:"
                    f"{origin.server_address[1]}/v1/health-events"
                ),
                key_id="key-1",
                secret=b"x" * 32,
            )
            result = client.deliver(
                notification_id="6d48a169-862d-4331-93c7-f27ca101b63c",
                raw_body=b"{}",
                timestamp=1_784_772_600,
            )
            assert not result.success
            assert not result.retryable
            assert result.http_status == code
        assert destination_hits == []
    finally:
        origin.shutdown()
        destination.shutdown()
        origin.server_close()
        destination.server_close()


def test_retry_delay_is_deterministic_with_injected_jitter() -> None:
    assert retry_delay_seconds(1, random_uniform=lambda _a, _b: 1.0) == 1.0
    assert retry_delay_seconds(8, random_uniform=lambda _a, _b: 1.0) == 300.0
    assert (
        retry_delay_seconds(
            1,
            retry_after="3601",
            random_uniform=lambda _a, _b: 1.0,
        )
        == 3_600.0
    )


def test_dispatcher_reuses_body_and_recovers_in_sequence(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    enqueue_lead_off(store)

    class FakeClient:
        def __init__(self) -> None:
            self.bodies: list[bytes] = []

        def deliver(self, *, notification_id, raw_body, timestamp):
            del notification_id, timestamp
            self.bodies.append(raw_body)
            if len(self.bodies) == 1:
                return WebhookDeliveryResult(False, True, 503, "HTTP 503")
            return WebhookDeliveryResult(True, False, 202, "accepted")

    fake = FakeClient()
    dispatcher = HealthWebhookDispatcher(store=store, client=fake)
    first_now = time.time() + 1

    assert dispatcher.run_once(now_epoch_s=first_now)
    assert len(store.list_outbox()) == 1
    assert dispatcher.run_once(now_epoch_s=first_now + 4_000)

    assert fake.bodies[0] == fake.bodies[1]
    assert store.list_outbox() == []


def test_dispatcher_pauses_after_auth_failure(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    enqueue_lead_off(store)

    class AuthFailClient:
        def deliver(self, **_kwargs):
            return WebhookDeliveryResult(False, False, 401, "HTTP 401")

    dispatcher = HealthWebhookDispatcher(store=store, client=AuthFailClient())

    assert dispatcher.run_once(now_epoch_s=time.time() + 1)
    assert dispatcher.paused_for_auth_failure
    assert len(store.list_dead_letters()) == 1
    assert not dispatcher.run_once(now_epoch_s=time.time() + 2)


def test_blocked_ordinary_agent_queue_does_not_block_health_queue(
    tmp_path,
) -> None:
    ordinary = WebhookStore(tmp_path / "ordinary.sqlite3")
    instruction = ordinary.create_instruction("remain pending")
    health = HealthStore(tmp_path / "health.sqlite3")
    enqueue_lead_off(health)

    class AcceptedClient:
        def deliver(self, **_kwargs):
            return WebhookDeliveryResult(True, False, 202, "accepted")

    dispatcher = HealthWebhookDispatcher(
        store=health,
        client=AcceptedClient(),
    )
    assert dispatcher.run_once(now_epoch_s=time.time() + 1)
    assert health.list_outbox() == []
    assert ordinary.get_instruction(instruction.instruction_id).state.value == "pending"


def test_dispatcher_restart_preserves_id_body_and_24_hour_dead_letter(
    tmp_path,
) -> None:
    path = tmp_path / "health.sqlite3"
    first_store = HealthStore(path)
    enqueue_lead_off(first_store)

    class RetryClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bytes]] = []

        def deliver(self, *, notification_id, raw_body, timestamp):
            del timestamp
            self.calls.append((notification_id, raw_body))
            return WebhookDeliveryResult(False, True, 503, "HTTP 503")

    base = time.time() + 1
    retry = RetryClient()
    assert HealthWebhookDispatcher(
        store=first_store,
        client=retry,
    ).run_once(now_epoch_s=base)

    restarted_store = HealthStore(path)
    retry_after_restart = RetryClient()
    assert HealthWebhookDispatcher(
        store=restarted_store,
        client=retry_after_restart,
    ).run_once(now_epoch_s=base + 4_000)
    assert retry_after_restart.calls == retry.calls

    assert HealthWebhookDispatcher(
        store=HealthStore(path),
        client=retry_after_restart,
    ).run_once(now_epoch_s=base + 24 * 60 * 60 + 1)
    assert HealthStore(path).list_outbox() == []
    dead = HealthStore(path).list_dead_letters()
    assert len(dead) == 1
    assert dead[0]["notification_id"] == retry.calls[0][0]
