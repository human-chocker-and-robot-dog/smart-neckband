from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Iterator
from uuid import uuid4

from .webhook_models import (
    InstructionRecord,
    InstructionState,
    MAX_WEBHOOK_BODY_BYTES,
    ReplyEvent,
    WebhookSettings,
)


class InstructionConflictError(ValueError):
    pass


class ReplyConflictError(ValueError):
    pass


class WebhookStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS webhook_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    settings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS webhook_instructions (
                    instruction_id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL,
                    last_http_status INTEGER,
                    last_error TEXT,
                    accepted_at TEXT
                );

                CREATE INDEX IF NOT EXISTS webhook_instructions_due
                    ON webhook_instructions(state, next_attempt_at);

                CREATE TABLE IF NOT EXISTS webhook_replies (
                    reply_id TEXT PRIMARY KEY,
                    instruction_id TEXT NOT NULL UNIQUE,
                    event TEXT NOT NULL,
                    text TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );
                """
            )

    def load_settings(self) -> WebhookSettings:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT settings_json FROM webhook_settings WHERE id = 1"
            ).fetchone()
        if row is None:
            return WebhookSettings()
        try:
            data = json.loads(str(row["settings_json"]))
            if not isinstance(data, dict):
                raise ValueError("settings JSON is not an object")
            return WebhookSettings.from_dict(data)
        except (TypeError, ValueError, json.JSONDecodeError):
            return WebhookSettings()

    def save_settings(self, settings: WebhookSettings) -> None:
        validated = settings.validated()
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO webhook_settings(id, settings_json, updated_at)
                VALUES(1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    updated_at = excluded.updated_at
                """,
                (json.dumps(validated.to_dict(), ensure_ascii=False, separators=(",", ":")), now),
            )

    def create_instruction(self, text: str, *, instruction_id: str | None = None) -> InstructionRecord:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("指令文本不能为空")
        stable_id = (instruction_id or str(uuid4())).strip()
        if not stable_id:
            raise ValueError("instruction_id 不能为空")
        payload = json.dumps(
            {"instruction_id": stable_id, "text": text},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > MAX_WEBHOOK_BODY_BYTES:
            raise ValueError("Webhook 请求体超过 64 KiB")

        now = _utc_now()
        with self._connection() as connection:
            existing = connection.execute(
                "SELECT text FROM webhook_instructions WHERE instruction_id = ?",
                (stable_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["text"]) != text:
                    raise InstructionConflictError("同一 instruction_id 已绑定不同文本")
                return self.get_instruction(stable_id)
            connection.execute(
                """
                INSERT INTO webhook_instructions(
                    instruction_id, text, state, created_at, updated_at, next_attempt_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    stable_id,
                    text,
                    InstructionState.PENDING.value,
                    now,
                    now,
                    time.time(),
                ),
            )
        return self.get_instruction(stable_id)

    def get_instruction(self, instruction_id: str) -> InstructionRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM webhook_instructions WHERE instruction_id = ?",
                (instruction_id,),
            ).fetchone()
        if row is None:
            raise KeyError(instruction_id)
        return _instruction_from_row(row)

    def list_instructions(self, limit: int = 100) -> tuple[InstructionRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM webhook_instructions
                ORDER BY created_at DESC, instruction_id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return tuple(_instruction_from_row(row) for row in rows)

    def claim_next_due(self, now_epoch_s: float | None = None) -> InstructionRecord | None:
        now_epoch_s = time.time() if now_epoch_s is None else now_epoch_s
        now = _utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM webhook_instructions
                WHERE state IN (?, ?)
                  AND COALESCE(next_attempt_at, 0) <= ?
                ORDER BY created_at ASC, instruction_id ASC
                LIMIT 1
                """,
                (
                    InstructionState.PENDING.value,
                    InstructionState.RETRY_WAIT.value,
                    now_epoch_s,
                ),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE webhook_instructions
                SET state = ?, updated_at = ?, attempt_count = attempt_count + 1,
                    next_attempt_at = NULL
                WHERE instruction_id = ?
                """,
                (
                    InstructionState.SUBMITTING.value,
                    now,
                    str(row["instruction_id"]),
                ),
            )
        return self.get_instruction(str(row["instruction_id"]))

    def seconds_until_next_due(self, now_epoch_s: float | None = None) -> float | None:
        now_epoch_s = time.time() if now_epoch_s is None else now_epoch_s
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT MIN(COALESCE(next_attempt_at, 0)) AS due_at
                FROM webhook_instructions
                WHERE state IN (?, ?)
                """,
                (InstructionState.PENDING.value, InstructionState.RETRY_WAIT.value),
            ).fetchone()
        if row is None or row["due_at"] is None:
            return None
        return max(0.0, float(row["due_at"]) - now_epoch_s)

    def mark_accepted(self, instruction_id: str, *, http_status: int = 202) -> InstructionRecord:
        now = _utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE webhook_instructions
                SET state = ?, updated_at = ?, accepted_at = ?,
                    last_http_status = ?, last_error = NULL, next_attempt_at = NULL
                WHERE instruction_id = ?
                """,
                (
                    InstructionState.ACCEPTED.value,
                    now,
                    now,
                    http_status,
                    instruction_id,
                ),
            )
        return self.get_instruction(instruction_id)

    def mark_retry_wait(
        self,
        instruction_id: str,
        *,
        error: str,
        delay_s: float,
        http_status: int | None,
    ) -> InstructionRecord:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE webhook_instructions
                SET state = ?, updated_at = ?, next_attempt_at = ?,
                    last_http_status = ?, last_error = ?
                WHERE instruction_id = ?
                """,
                (
                    InstructionState.RETRY_WAIT.value,
                    _utc_now(),
                    time.time() + max(0.0, delay_s),
                    http_status,
                    error,
                    instruction_id,
                ),
            )
        return self.get_instruction(instruction_id)

    def mark_failed(
        self,
        instruction_id: str,
        *,
        error: str,
        http_status: int | None,
    ) -> InstructionRecord:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE webhook_instructions
                SET state = ?, updated_at = ?, next_attempt_at = NULL,
                    last_http_status = ?, last_error = ?
                WHERE instruction_id = ?
                """,
                (
                    InstructionState.FAILED.value,
                    _utc_now(),
                    http_status,
                    error,
                    instruction_id,
                ),
            )
        return self.get_instruction(instruction_id)

    def retry_now(self, instruction_id: str) -> InstructionRecord:
        record = self.get_instruction(instruction_id)
        if record.state is InstructionState.ACCEPTED:
            return record
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE webhook_instructions
                SET state = ?, updated_at = ?, next_attempt_at = ?, last_error = NULL
                WHERE instruction_id = ?
                """,
                (
                    InstructionState.PENDING.value,
                    _utc_now(),
                    time.time(),
                    instruction_id,
                ),
            )
        return self.get_instruction(instruction_id)

    def recover_submitting(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE webhook_instructions
                SET state = ?, updated_at = ?, next_attempt_at = ?,
                    last_error = '上位机上次在提交过程中退出；使用相同 ID 和文本重试'
                WHERE state = ?
                """,
                (
                    InstructionState.RETRY_WAIT.value,
                    _utc_now(),
                    time.time(),
                    InstructionState.SUBMITTING.value,
                ),
            )
            return int(cursor.rowcount)

    def save_reply(self, event: ReplyEvent) -> bool:
        received_at = _utc_now()
        with self._connection() as connection:
            existing_reply = connection.execute(
                "SELECT * FROM webhook_replies WHERE reply_id = ?",
                (event.reply_id,),
            ).fetchone()
            if existing_reply is not None:
                if (
                    str(existing_reply["instruction_id"]) != event.instruction_id
                    or str(existing_reply["event"]) != event.event
                    or str(existing_reply["text"]) != event.text
                    or str(existing_reply["completed_at"]) != event.completed_at
                ):
                    raise ReplyConflictError("同一 reply_id 对应了不同回调内容")
                return False
            existing_instruction = connection.execute(
                "SELECT reply_id FROM webhook_replies WHERE instruction_id = ?",
                (event.instruction_id,),
            ).fetchone()
            if existing_instruction is not None:
                raise ReplyConflictError("同一 instruction_id 已收到不同 reply_id")
            connection.execute(
                """
                INSERT INTO webhook_replies(
                    reply_id, instruction_id, event, text, completed_at, received_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    event.reply_id,
                    event.instruction_id,
                    event.event,
                    event.text,
                    event.completed_at,
                    received_at,
                ),
            )
        return True

    def list_replies(self, limit: int = 100) -> tuple[ReplyEvent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT reply_id, instruction_id, event, text, completed_at
                FROM webhook_replies
                ORDER BY received_at DESC, reply_id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return tuple(
            ReplyEvent(
                reply_id=str(row["reply_id"]),
                instruction_id=str(row["instruction_id"]),
                event=str(row["event"]),
                text=str(row["text"]),
                completed_at=str(row["completed_at"]),
            )
            for row in rows
        )


def _instruction_from_row(row: sqlite3.Row) -> InstructionRecord:
    return InstructionRecord(
        instruction_id=str(row["instruction_id"]),
        text=str(row["text"]),
        state=InstructionState(str(row["state"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        attempt_count=int(row["attempt_count"]),
        next_attempt_at=float(row["next_attempt_at"]) if row["next_attempt_at"] is not None else None,
        last_http_status=int(row["last_http_status"]) if row["last_http_status"] is not None else None,
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
        accepted_at=str(row["accepted_at"]) if row["accepted_at"] is not None else None,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
