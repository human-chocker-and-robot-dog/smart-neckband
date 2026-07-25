from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import email.utils
import hashlib
import hmac
import json
import random
import re
import sqlite3
from pathlib import Path
from threading import Event, Thread
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .health_contract import compact_json
from .health_event_contract import BRIDGE_SCHEMA_VERSION, definition_schema


HEALTH_WEBHOOK_PATH = "/v1/health-events"
MAX_WEBHOOK_BODY_BYTES = 65_536
SECRET_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SIGNATURE_PATTERN = re.compile(r"^v1=([0-9a-f]{64})$")
TIMESTAMP_PATTERN = re.compile(r"^[1-9][0-9]{0,11}$")
BACKOFF_SECONDS = (1, 2, 4, 8, 16, 30, 60)


def parse_secret_hex(value: str | None) -> bytes:
    if value is None or not SECRET_HEX_PATTERN.fullmatch(value):
        raise ValueError(
            "SMART_COLLAR_HEALTH_WEBHOOK_SECRET_HEX must be 64 lowercase hex characters"
        )
    secret = bytes.fromhex(value)
    if len(secret) != 32:
        raise ValueError("health webhook secret must decode to 32 bytes")
    return secret


def validate_health_webhook_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.path != HEALTH_WEBHOOK_PATH or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"health webhook URL path must be {HEALTH_WEBHOOK_PATH}")
    if parsed.scheme == "https" and parsed.hostname:
        return value
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    ):
        return value
    raise ValueError("health webhook URL must use HTTPS unless it is loopback")


def canonical_webhook_body(payload: dict[str, object]) -> bytes:
    required_order = (
        "schema_version",
        "notification_id",
        "notification_sequence",
        "event_id",
        "event_revision",
        "transition",
        "event_type",
        "severity",
        "priority",
        "wearer_id",
        "source_instance_id",
        "state_revision",
        "data_source",
        "occurred_at",
        "sent_at",
        "trace_id",
        "test_mode",
        "summary",
        "evidence",
        "recommended_capabilities",
    )
    if tuple(payload) != required_order:
        raise ValueError("webhook payload keys are not in canonical contract order")
    body = compact_json(payload).encode("utf-8")
    if not body or len(body) > MAX_WEBHOOK_BODY_BYTES:
        raise ValueError("health webhook body size is outside 1..65536 bytes")
    return body


def signature_header(secret: bytes, timestamp: int, raw_body: bytes) -> str:
    signed_payload = str(timestamp).encode("ascii") + b"." + raw_body
    return "v1=" + hmac.new(secret, signed_payload, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    secret: bytes,
    timestamp_text: str,
    signature: str,
    raw_body: bytes,
    now_epoch_s: int | None = None,
) -> bool:
    if not TIMESTAMP_PATTERN.fullmatch(timestamp_text):
        return False
    timestamp = int(timestamp_text)
    if not 1 <= timestamp <= 253_402_300_799:
        return False
    now = int(time.time()) if now_epoch_s is None else now_epoch_s
    if abs(now - timestamp) > 300:
        return False
    if not SIGNATURE_PATTERN.fullmatch(signature):
        return False
    return hmac.compare_digest(
        signature,
        signature_header(secret, timestamp, raw_body),
    )


def retry_delay_seconds(
    attempt_count: int,
    *,
    retry_after: str | None = None,
    now_epoch_s: float | None = None,
    random_uniform: Callable[[float, float], float] = random.uniform,
) -> float:
    index = max(0, attempt_count - 1)
    base = BACKOFF_SECONDS[index] if index < len(BACKOFF_SECONDS) else 300
    local = base * random_uniform(0.8, 1.2)
    parsed_retry_after = _parse_retry_after(
        retry_after,
        time.time() if now_epoch_s is None else now_epoch_s,
    )
    if parsed_retry_after is not None:
        local = max(local, parsed_retry_after)
    return min(3_600.0, local)


def _parse_retry_after(value: str | None, now_epoch_s: float) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return float(stripped)
    try:
        parsed = email.utils.parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, parsed.timestamp() - now_epoch_s)


@dataclass(frozen=True, slots=True)
class WebhookDeliveryResult:
    success: bool
    retryable: bool
    http_status: int | None
    message: str
    retry_after: str | None = None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HealthWebhookClient:
    def __init__(
        self,
        *,
        url: str,
        key_id: str,
        secret: bytes,
        timeout_s: float = 3.0,
    ) -> None:
        self.url = validate_health_webhook_url(url)
        if not key_id:
            raise ValueError("health webhook key_id is required")
        if len(secret) != 32:
            raise ValueError("health webhook secret must contain 32 bytes")
        self.key_id = key_id
        self.secret = secret
        self.timeout_s = timeout_s
        self._opener = build_opener(_NoRedirect())

    def deliver(
        self,
        *,
        notification_id: str,
        raw_body: bytes,
        timestamp: int | None = None,
    ) -> WebhookDeliveryResult:
        timestamp = int(time.time()) if timestamp is None else timestamp
        request = Request(
            self.url,
            data=raw_body,
            method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": f"smart-neckband-health/{BRIDGE_SCHEMA_VERSION}",
                "X-Smart-Collar-Key-Id": self.key_id,
                "X-Smart-Collar-Timestamp": str(timestamp),
                "X-Smart-Collar-Notification-Id": notification_id,
                "X-Smart-Collar-Signature": signature_header(
                    self.secret,
                    timestamp,
                    raw_body,
                ),
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout_s) as response:
                status = int(response.status)
                body = response.read(MAX_WEBHOOK_BODY_BYTES + 1)
        except HTTPError as exc:
            status = int(exc.code)
            retryable = status in {408, 425, 429} or 500 <= status <= 599
            return WebhookDeliveryResult(
                success=False,
                retryable=retryable,
                http_status=status,
                message=f"HTTP {status}",
                retry_after=exc.headers.get("Retry-After"),
            )
        except (URLError, TimeoutError, OSError) as exc:
            return WebhookDeliveryResult(
                success=False,
                retryable=True,
                http_status=None,
                message=type(exc).__name__,
            )

        if status != 202:
            return WebhookDeliveryResult(
                success=False,
                retryable=False,
                http_status=status,
                message=f"unexpected HTTP {status}",
            )
        if len(body) > MAX_WEBHOOK_BODY_BYTES:
            return WebhookDeliveryResult(False, True, status, "ACK body too large")
        try:
            ack = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return WebhookDeliveryResult(False, True, status, "invalid ACK JSON")
        if (
            not isinstance(ack, dict)
            or ack.get("notification_id") != notification_id
            or ack.get("status") not in {"accepted", "duplicate"}
            or set(ack) != {"notification_id", "status"}
        ):
            return WebhookDeliveryResult(False, True, status, "invalid ACK contract")
        return WebhookDeliveryResult(True, False, status, str(ack["status"]))


class HealthWebhookDispatcher:
    """Dedicated worker for the health outbox.

    The store enforces per-wearer sequence ordering. This worker never shares
    the ordinary instruction/reply queue.
    """

    def __init__(
        self,
        *,
        store,
        client: HealthWebhookClient,
        poll_interval_s: float = 0.5,
    ) -> None:
        self.store = store
        self.client = client
        self.poll_interval_s = poll_interval_s
        self._stop = Event()
        self._wake = Event()
        self._thread: Thread | None = None
        self.paused_for_auth_failure = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.store.recover_webhook_deliveries()
        self._stop.clear()
        self._thread = Thread(
            target=self._run,
            name="HealthWebhookDispatcher",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def wake(self) -> None:
        self._wake.set()

    def run_once(self, *, now_epoch_s: float | None = None) -> bool:
        if self.paused_for_auth_failure:
            return False
        now_epoch_s = time.time() if now_epoch_s is None else now_epoch_s
        item = self.store.claim_next_outbox(now_epoch_s=now_epoch_s)
        if item is None:
            return False
        first_attempt = item["first_attempt_at"]
        if (
            first_attempt is not None
            and now_epoch_s - float(first_attempt) > 24 * 60 * 60
        ):
            self.store.move_outbox_to_dead_letter(
                str(item["notification_id"]),
                error_category="delivery_window_exceeded",
                http_status=item["last_http_status"],
            )
            return True

        result = self.client.deliver(
            notification_id=str(item["notification_id"]),
            raw_body=bytes(item["raw_body"]),
            timestamp=int(now_epoch_s),
        )
        if result.success:
            self.store.mark_outbox_delivered(
                str(item["notification_id"]),
                http_status=result.http_status or 202,
            )
        elif not result.retryable:
            self.store.move_outbox_to_dead_letter(
                str(item["notification_id"]),
                error_category=result.message,
                http_status=result.http_status,
            )
            if result.http_status in {401, 403}:
                self.paused_for_auth_failure = True
        else:
            delay = retry_delay_seconds(
                int(item["attempt_count"]),
                retry_after=result.retry_after,
                now_epoch_s=now_epoch_s,
            )
            self.store.mark_outbox_retry(
                str(item["notification_id"]),
                delay_s=delay,
                error=result.message,
                http_status=result.http_status,
            )
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            worked = self.run_once()
            if worked:
                continue
            self._wake.wait(self.poll_interval_s)
            self._wake.clear()


class HealthWebhookReceiverStore:
    """SQLite idempotency store for the local/mock receiver contract."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS health_notifications (
                    notification_id TEXT PRIMARY KEY,
                    raw_body_sha256 TEXT NOT NULL,
                    raw_body BLOB NOT NULL,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS health_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT NOT NULL UNIQUE,
                    raw_body BLOB NOT NULL,
                    received_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS health_event_heads (
                    event_id TEXT PRIMARY KEY,
                    highest_revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS health_agent_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL,
                    event_revision INTEGER NOT NULL,
                    raw_body BLOB NOT NULL,
                    received_at TEXT NOT NULL,
                    UNIQUE(event_id, event_revision)
                );
                """
            )

    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return _ClosingConnection(connection)

    def persist(self, notification_id: str, raw_body: bytes) -> str:
        digest = hashlib.sha256(raw_body).hexdigest()
        payload = json.loads(raw_body.decode("utf-8"))
        event_id = str(payload["event_id"])
        event_revision = int(payload["event_revision"])
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO health_notifications(
                    notification_id, raw_body_sha256, raw_body, received_at
                ) VALUES(?, ?, ?, ?)
                ON CONFLICT(notification_id) DO NOTHING
                """,
                (
                    notification_id,
                    digest,
                    raw_body,
                    datetime.now(timezone.utc)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                ),
            )
            if cursor.rowcount == 1:
                head = connection.execute(
                    "SELECT highest_revision FROM health_event_heads WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if head is None or event_revision > int(head["highest_revision"]):
                    connection.execute(
                        """
                        INSERT INTO health_event_heads(event_id, highest_revision)
                        VALUES(?, ?)
                        ON CONFLICT(event_id) DO UPDATE SET
                            highest_revision=MAX(
                                health_event_heads.highest_revision,
                                excluded.highest_revision
                            )
                        """,
                        (event_id, event_revision),
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO health_agent_queue(
                            notification_id, event_id, event_revision,
                            raw_body, received_at
                        ) VALUES(?, ?, ?, ?, ?)
                        """,
                        (
                            notification_id,
                            event_id,
                            event_revision,
                            raw_body,
                            datetime.now(timezone.utc)
                            .isoformat(timespec="milliseconds")
                            .replace("+00:00", "Z"),
                        ),
                    )
                return "accepted"
            existing = connection.execute(
                """
                SELECT raw_body_sha256 FROM health_notifications
                WHERE notification_id=?
                """,
                (notification_id,),
            ).fetchone()
            return "duplicate" if existing["raw_body_sha256"] == digest else "conflict"

    def queue_count(self) -> int:
        with self._connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM health_agent_queue"
                ).fetchone()["count"]
            )


class _ClosingConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()


class HealthWebhookReceiver:
    def __init__(
        self,
        *,
        store: HealthWebhookReceiverStore,
        keys: dict[str, bytes],
    ) -> None:
        if not keys or any(len(secret) != 32 for secret in keys.values()):
            raise ValueError("receiver keys must map key IDs to 32-byte secrets")
        self.store = store
        self.keys = keys

    def handle(
        self,
        *,
        method: str,
        path: str,
        headers: dict[str, str],
        raw_body: bytes,
        now_epoch_s: int,
    ) -> tuple[int, dict[str, str], bytes]:
        normalized = {key.lower(): value for key, value in headers.items()}
        if method != "POST":
            return self._error(405, "method_not_allowed", allow_post=True)
        if path != HEALTH_WEBHOOK_PATH:
            return self._error(404, "not_found")
        if not _valid_content_type(normalized.get("content-type")):
            return self._error(415, "unsupported_media_type")
        transfer_encoding = normalized.get("transfer-encoding", "")
        content_length_text = normalized.get("content-length")
        if (
            transfer_encoding
            or content_length_text is None
            or not content_length_text.isdigit()
            or content_length_text.startswith("0")
        ):
            return self._error(400, "invalid_request")
        declared_length = int(content_length_text)
        if declared_length <= 0:
            return self._error(400, "invalid_request")
        if declared_length > MAX_WEBHOOK_BODY_BYTES or len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
            return self._error(413, "body_too_large")
        if declared_length != len(raw_body):
            return self._error(400, "invalid_request")

        timestamp_text = normalized.get("x-smart-collar-timestamp", "")
        receiver_now = int(now_epoch_s)
        if (
            not TIMESTAMP_PATTERN.fullmatch(timestamp_text)
            or not 1 <= int(timestamp_text) <= 253_402_300_799
            or abs(receiver_now - int(timestamp_text)) > 300
        ):
            return self._error(401, "timestamp_out_of_range")

        key_id = normalized.get("x-smart-collar-key-id")
        signature = normalized.get("x-smart-collar-signature", "")
        secret = self.keys.get(key_id or "")
        if (
            secret is None
            or not SIGNATURE_PATTERN.fullmatch(signature)
            or not hmac.compare_digest(
                signature,
                signature_header(secret, int(timestamp_text), raw_body),
            )
        ):
            return self._error(401, "invalid_signature")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
            from jsonschema import Draft202012Validator

            Draft202012Validator(definition_schema("WebhookRequest")).validate(
                payload
            )
        except Exception:
            return self._error(400, "invalid_request")

        notification_id = normalized.get("x-smart-collar-notification-id")
        if notification_id != payload["notification_id"]:
            return self._error(400, "invalid_request")
        try:
            outcome = self.store.persist(notification_id, raw_body)
        except Exception:
            return self._error(503, "internal_error")
        if outcome == "conflict":
            return self._error(409, "notification_id_conflict")
        body = compact_json(
            {
                "notification_id": notification_id,
                "status": outcome,
            }
        ).encode()
        return 202, {"Content-Type": "application/json; charset=utf-8"}, body

    @staticmethod
    def _error(
        status: int,
        code: str,
        *,
        allow_post: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if allow_post:
            headers["Allow"] = "POST"
        return status, headers, compact_json({"error": code}).encode()


def _valid_content_type(value: str | None) -> bool:
    if value is None:
        return False
    return bool(
        re.fullmatch(
            r"(?i)application/json(?:\s*;\s*charset\s*=\s*utf-8)?",
            value.strip(),
        )
    )
