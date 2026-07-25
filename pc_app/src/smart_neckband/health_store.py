from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Iterator, Sequence
from uuid import uuid4

from .analysis import EcgAnalysisResult
from .health_contract import SCHEMA_VERSION, compact_json
from .health_event_contract import BRIDGE_SCHEMA_VERSION
from .health_rules import HealthAlertRule, rmssd_ms
from .health_state import BuiltHealthState
from .health_webhook import canonical_webhook_body
from .protocol import ParserStats
from .source_coordinator import utc_now_millisecond_z


EVENT_TYPES = ("lead_off", "adc_clipping", "input_stale", "input_offline")


class HealthStore:
    def __init__(
        self,
        path: str | Path,
        *,
        alert_rules: tuple[HealthAlertRule, ...] = (),
    ) -> None:
        self.path = Path(path)
        self.alert_rules = alert_rules
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS health_schema (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    migration_version INTEGER NOT NULL
                );
                INSERT INTO health_schema(id, migration_version)
                VALUES(1, 5)
                ON CONFLICT(id) DO UPDATE SET
                    migration_version=MAX(
                        health_schema.migration_version,
                        excluded.migration_version
                    );

                CREATE TABLE IF NOT EXISTS health_wearer_sequences (
                    wearer_id TEXT PRIMARY KEY,
                    state_revision INTEGER NOT NULL DEFAULT 0,
                    notification_sequence INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS health_states (
                    wearer_id TEXT PRIMARY KEY,
                    state_revision INTEGER NOT NULL,
                    source_instance_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    committed_monotonic_ns INTEGER NOT NULL,
                    ecg_received_monotonic_ns INTEGER NOT NULL,
                    transport_received_monotonic_ns INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS health_device_snapshots (
                    wearer_id TEXT PRIMARY KEY,
                    source_instance_id TEXT,
                    data_source TEXT,
                    device_json TEXT NOT NULL,
                    committed_monotonic_ns INTEGER NOT NULL,
                    transport_received_monotonic_ns INTEGER,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS health_events (
                    event_id TEXT PRIMARY KEY,
                    wearer_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    event_revision INTEGER NOT NULL,
                    source_instance_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    event_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS health_active_event_unique
                    ON health_events(wearer_id, event_type)
                    WHERE status = 'active';
                CREATE INDEX IF NOT EXISTS health_events_recent
                    ON health_events(wearer_id, updated_at DESC, event_id DESC);

                CREATE TABLE IF NOT EXISTS health_event_gates (
                    wearer_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    clear_count INTEGER NOT NULL DEFAULT 0,
                    last_evidence_key TEXT,
                    clear_since_ns INTEGER,
                    fresh_since_ns INTEGER,
                    PRIMARY KEY(wearer_id, event_type)
                );

                CREATE TABLE IF NOT EXISTS health_alert_rule_state (
                    wearer_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    active_event_id TEXT,
                    matched_since_at TEXT,
                    clear_since_at TEXT,
                    cooldown_until_at TEXT,
                    last_evidence_json TEXT,
                    PRIMARY KEY(wearer_id, rule_id)
                );

                CREATE TABLE IF NOT EXISTS health_webhook_outbox (
                    notification_id TEXT PRIMARY KEY,
                    wearer_id TEXT NOT NULL,
                    notification_sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    event_revision INTEGER NOT NULL,
                    raw_body BLOB NOT NULL,
                    raw_body_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    lease_until REAL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    first_attempt_at REAL,
                    next_attempt_at REAL NOT NULL,
                    last_http_status INTEGER,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(wearer_id, notification_sequence)
                );
                CREATE INDEX IF NOT EXISTS health_outbox_due
                    ON health_webhook_outbox(state, wearer_id, notification_sequence, next_attempt_at);

                CREATE TABLE IF NOT EXISTS health_webhook_dead_letters (
                    notification_id TEXT PRIMARY KEY,
                    wearer_id TEXT NOT NULL,
                    notification_sequence INTEGER NOT NULL,
                    raw_body_sha256 TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    last_http_status INTEGER,
                    error_category TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    dead_lettered_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS health_webhook_deliveries (
                    notification_id TEXT PRIMARY KEY,
                    wearer_id TEXT NOT NULL,
                    notification_sequence INTEGER NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    http_status INTEGER NOT NULL,
                    delivered_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS health_metric_samples (
                    wearer_id TEXT NOT NULL,
                    state_revision INTEGER NOT NULL,
                    source_instance_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    data_source TEXT NOT NULL,
                    test_mode INTEGER NOT NULL,
                    heart_rate_bpm REAL,
                    signal_quality REAL,
                    lead_off INTEGER,
                    adc_clipping_ratio REAL,
                    motion_score REAL,
                    still_ratio_percent REAL,
                    motion_level TEXT,
                    motion_coverage_ratio REAL NOT NULL,
                    imu_online INTEGER,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(wearer_id, state_revision)
                );
                CREATE INDEX IF NOT EXISTS health_metric_samples_window
                    ON health_metric_samples(wearer_id, observed_at);

                CREATE TABLE IF NOT EXISTS health_rr_intervals (
                    wearer_id TEXT NOT NULL,
                    source_instance_id TEXT NOT NULL,
                    end_sample_index INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    rr_ms REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(
                        wearer_id,
                        source_instance_id,
                        end_sample_index
                    )
                );
                CREATE INDEX IF NOT EXISTS health_rr_intervals_window
                    ON health_rr_intervals(wearer_id, observed_at);

                CREATE TABLE IF NOT EXISTS health_sleep_records (
                    sleep_record_id TEXT PRIMARY KEY,
                    wearer_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_session_id TEXT,
                    source_sha256 TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    lead_name TEXT NOT NULL,
                    sample_rate_hz REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    duration_s REAL NOT NULL,
                    recording_start_time TEXT,
                    demographics_json TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS health_sleep_records_wearer
                    ON health_sleep_records(wearer_id, imported_at DESC);

                CREATE TABLE IF NOT EXISTS health_sleep_analysis_runs (
                    analysis_run_id TEXT PRIMARY KEY,
                    sleep_record_id TEXT NOT NULL,
                    wearer_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(
                        status IN ('running', 'completed', 'failed', 'cancelled')
                    ),
                    model_name TEXT NOT NULL,
                    stages_mode TEXT,
                    sleepecg_version TEXT,
                    tensorflow_version TEXT,
                    heartbeat_count INTEGER,
                    parameters_json TEXT NOT NULL,
                    summary_json TEXT,
                    quality_json TEXT,
                    error_message TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(sleep_record_id)
                        REFERENCES health_sleep_records(sleep_record_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS health_sleep_runs_latest
                    ON health_sleep_analysis_runs(
                        wearer_id, status, completed_at DESC, analysis_run_id DESC
                    );

                CREATE TABLE IF NOT EXISTS health_sleep_epochs (
                    analysis_run_id TEXT NOT NULL,
                    epoch_index INTEGER NOT NULL,
                    start_offset_s INTEGER NOT NULL,
                    stage TEXT NOT NULL CHECK(
                        stage IN ('UNDEFINED', 'WAKE', 'REM', 'NREM')
                    ),
                    confidence REAL NOT NULL,
                    probabilities_json TEXT NOT NULL,
                    reference_stage TEXT CHECK(
                        reference_stage IS NULL OR
                        reference_stage IN ('UNDEFINED', 'WAKE', 'REM', 'NREM')
                    ),
                    PRIMARY KEY(analysis_run_id, epoch_index),
                    FOREIGN KEY(analysis_run_id)
                        REFERENCES health_sleep_analysis_runs(analysis_run_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS health_mcp_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    wearer_id TEXT,
                    outcome TEXT NOT NULL,
                    error_code TEXT,
                    latency_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS health_runtime_observability (
                    wearer_id TEXT PRIMARY KEY,
                    parser_packets_ok INTEGER NOT NULL,
                    parser_packets_lost INTEGER NOT NULL,
                    parser_crc_errors INTEGER NOT NULL,
                    analysis_source_instance_id TEXT,
                    analysis_sample_index INTEGER,
                    analysis_evidence_monotonic_ns INTEGER,
                    analysis_last_success_monotonic_ns INTEGER,
                    analysis_message TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS health_deletion_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wearer_id TEXT NOT NULL,
                    before_utc TEXT NOT NULL,
                    counts_json TEXT NOT NULL,
                    deleted_at TEXT NOT NULL
                );
                """
            )

    def save_runtime_observability(
        self,
        *,
        wearer_id: str,
        parser_stats: ParserStats,
        analysis: EcgAnalysisResult | None,
    ) -> None:
        success_ns = (
            analysis.analyzed_through_received_monotonic_ns
            if analysis is not None and analysis.message == "ok"
            else None
        )
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO health_runtime_observability(
                    wearer_id, parser_packets_ok, parser_packets_lost,
                    parser_crc_errors, analysis_source_instance_id,
                    analysis_sample_index, analysis_evidence_monotonic_ns,
                    analysis_last_success_monotonic_ns, analysis_message,
                    updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wearer_id) DO UPDATE SET
                    parser_packets_ok=excluded.parser_packets_ok,
                    parser_packets_lost=excluded.parser_packets_lost,
                    parser_crc_errors=excluded.parser_crc_errors,
                    analysis_source_instance_id=excluded.analysis_source_instance_id,
                    analysis_sample_index=excluded.analysis_sample_index,
                    analysis_evidence_monotonic_ns=excluded.analysis_evidence_monotonic_ns,
                    analysis_last_success_monotonic_ns=COALESCE(
                        excluded.analysis_last_success_monotonic_ns,
                        health_runtime_observability.analysis_last_success_monotonic_ns
                    ),
                    analysis_message=excluded.analysis_message,
                    updated_at=excluded.updated_at
                """,
                (
                    wearer_id,
                    parser_stats.packets_ok,
                    parser_stats.packets_lost,
                    parser_stats.crc_errors,
                    analysis.source_instance_id if analysis is not None else None,
                    (
                        analysis.analyzed_through_ecg_sample_index
                        if analysis is not None
                        else None
                    ),
                    (
                        analysis.analyzed_through_received_monotonic_ns
                        if analysis is not None
                        else None
                    ),
                    success_ns,
                    analysis.message if analysis is not None else "no_analysis",
                    utc_now_millisecond_z(),
                ),
            )

    def get_observability(
        self,
        *,
        wearer_id: str,
        now_monotonic_ns: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        monotonic_now = (
            time.monotonic_ns()
            if now_monotonic_ns is None
            else now_monotonic_ns
        )
        utc_now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            schema_row = connection.execute(
                "SELECT migration_version FROM health_schema WHERE id=1"
            ).fetchone()
            state_row = connection.execute(
                "SELECT * FROM health_states WHERE wearer_id=?",
                (wearer_id,),
            ).fetchone()
            runtime_row = connection.execute(
                """
                SELECT * FROM health_runtime_observability
                WHERE wearer_id=?
                """,
                (wearer_id,),
            ).fetchone()
            event_row = connection.execute(
                """
                SELECT COUNT(*) AS opened_total,
                       SUM(CASE WHEN status='active' THEN 1 ELSE 0 END)
                           AS active_count,
                       SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END)
                           AS resolved_total
                FROM health_events WHERE wearer_id=?
                """,
                (wearer_id,),
            ).fetchone()
            outbox_row = connection.execute(
                """
                SELECT COUNT(*) AS pending_count, MIN(created_at) AS oldest
                FROM health_webhook_outbox WHERE wearer_id=?
                """,
                (wearer_id,),
            ).fetchone()
            delivery_rows = [
                connection.execute(
                    f"""
                    SELECT COUNT(*) AS count,
                           COALESCE(SUM(attempt_count), 0) AS attempts,
                           COALESCE(SUM(
                               CASE WHEN attempt_count > 1
                                    THEN attempt_count - 1 ELSE 0 END
                           ), 0) AS retries
                    FROM {table} WHERE wearer_id=?
                    """,
                    (wearer_id,),
                ).fetchone()
                for table in (
                    "health_webhook_outbox",
                    "health_webhook_deliveries",
                    "health_webhook_dead_letters",
                )
            ]
            mcp_row = connection.execute(
                """
                SELECT COUNT(*) AS call_count,
                       COALESCE(ROUND(AVG(latency_ms)), 0) AS average_latency_ms,
                       COALESCE(MAX(latency_ms), 0) AS max_latency_ms,
                       SUM(CASE WHEN outcome!='success' THEN 1 ELSE 0 END)
                           AS error_count
                FROM health_mcp_audit WHERE wearer_id=?
                """,
                (wearer_id,),
            ).fetchone()
            last_error_row = connection.execute(
                """
                SELECT error_code FROM health_mcp_audit
                WHERE wearer_id=? AND error_code IS NOT NULL
                ORDER BY id DESC LIMIT 1
                """,
                (wearer_id,),
            ).fetchone()

        state = json.loads(str(state_row["state_json"])) if state_row else None
        evidence_ns = (
            int(state_row["ecg_received_monotonic_ns"]) if state_row else None
        )
        state_age_ms = _monotonic_age_ms(monotonic_now, evidence_ns)
        oldest = outbox_row["oldest"]
        oldest_age_ms = None
        if oldest is not None:
            oldest_time = datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))
            oldest_age_ms = max(
                0,
                int((utc_now - oldest_time).total_seconds() * 1000),
            )
        analysis_evidence_ns = (
            int(runtime_row["analysis_evidence_monotonic_ns"])
            if runtime_row is not None
            and runtime_row["analysis_evidence_monotonic_ns"] is not None
            else None
        )
        analysis_success_ns = (
            int(runtime_row["analysis_last_success_monotonic_ns"])
            if runtime_row is not None
            and runtime_row["analysis_last_success_monotonic_ns"] is not None
            else None
        )
        return {
            "wearer_id": wearer_id,
            "database": {
                "migration_version": int(schema_row["migration_version"]),
            },
            "state": {
                "available": state is not None,
                "revision": state.get("state_revision") if state else None,
                "source_instance_id": (
                    state.get("source_instance_id") if state else None
                ),
                "data_source": state.get("data_source") if state else None,
                "test_mode": state.get("test_mode") if state else None,
                "freshness": _effective_freshness(
                    str(state.get("freshness")) if state else None,
                    state_age_ms,
                ),
                "age_ms": state_age_ms,
            },
            "events": {
                "opened_total": int(event_row["opened_total"] or 0),
                "active_count": int(event_row["active_count"] or 0),
                "resolved_total": int(event_row["resolved_total"] or 0),
            },
            "outbox": {
                "pending_count": int(outbox_row["pending_count"] or 0),
                "oldest_age_ms": oldest_age_ms,
            },
            "delivery": {
                "attempt_count": sum(int(row["attempts"]) for row in delivery_rows),
                "success_count": int(delivery_rows[1]["count"]),
                "retry_count": sum(int(row["retries"]) for row in delivery_rows),
                "dead_letter_count": int(delivery_rows[2]["count"]),
            },
            "mcp": {
                "call_count": int(mcp_row["call_count"] or 0),
                "average_latency_ms": int(mcp_row["average_latency_ms"] or 0),
                "max_latency_ms": int(mcp_row["max_latency_ms"] or 0),
                "error_count": int(mcp_row["error_count"] or 0),
                "last_error_code": (
                    last_error_row["error_code"]
                    if last_error_row is not None
                    else None
                ),
            },
            "parser": {
                "packets_ok": (
                    int(runtime_row["parser_packets_ok"]) if runtime_row else 0
                ),
                "packets_lost": (
                    int(runtime_row["parser_packets_lost"]) if runtime_row else 0
                ),
                "crc_errors": (
                    int(runtime_row["parser_crc_errors"]) if runtime_row else 0
                ),
            },
            "analysis": {
                "source_instance_id": (
                    runtime_row["analysis_source_instance_id"]
                    if runtime_row
                    else None
                ),
                "sample_index": (
                    runtime_row["analysis_sample_index"] if runtime_row else None
                ),
                "message": (
                    runtime_row["analysis_message"]
                    if runtime_row
                    else "unavailable"
                ),
                "evidence_age_ms": _monotonic_age_ms(
                    monotonic_now, analysis_evidence_ns
                ),
                "last_success_age_ms": _monotonic_age_ms(
                    monotonic_now, analysis_success_ns
                ),
            },
        }

    def save_device_snapshot(
        self,
        *,
        wearer_id: str,
        source_instance_id: str | None,
        data_source: str,
        device: dict[str, object],
        committed_monotonic_ns: int,
        transport_received_monotonic_ns: int | None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO health_device_snapshots(
                    wearer_id, source_instance_id, data_source, device_json,
                    committed_monotonic_ns, transport_received_monotonic_ns, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wearer_id) DO UPDATE SET
                    source_instance_id=excluded.source_instance_id,
                    data_source=excluded.data_source,
                    device_json=excluded.device_json,
                    committed_monotonic_ns=excluded.committed_monotonic_ns,
                    transport_received_monotonic_ns=excluded.transport_received_monotonic_ns,
                    updated_at=excluded.updated_at
                """,
                (
                    wearer_id,
                    source_instance_id,
                    data_source,
                    compact_json(device),
                    committed_monotonic_ns,
                    transport_received_monotonic_ns,
                    utc_now_millisecond_z(),
                ),
            )

    def get_device_snapshot(self, wearer_id: str) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM health_device_snapshots WHERE wearer_id = ?",
                (wearer_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "device": json.loads(str(row["device_json"])),
            "source_instance_id": row["source_instance_id"],
            "data_source": row["data_source"],
            "committed_monotonic_ns": int(row["committed_monotonic_ns"]),
            "transport_received_monotonic_ns": (
                int(row["transport_received_monotonic_ns"])
                if row["transport_received_monotonic_ns"] is not None
                else None
            ),
        }

    def commit_state(self, built: BuiltHealthState) -> dict[str, object]:
        state = deepcopy(built.document)
        wearer_id = str(state["wearer_id"])
        now_utc = utc_now_millisecond_z()
        trace_id = str(uuid4())
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO health_wearer_sequences(wearer_id)
                VALUES(?)
                ON CONFLICT(wearer_id) DO NOTHING
                """,
                (wearer_id,),
            )
            connection.execute(
                """
                UPDATE health_wearer_sequences
                SET state_revision = state_revision + 1
                WHERE wearer_id = ?
                """,
                (wearer_id,),
            )
            revision = int(
                connection.execute(
                    """
                    SELECT state_revision FROM health_wearer_sequences
                    WHERE wearer_id = ?
                    """,
                    (wearer_id,),
                ).fetchone()["state_revision"]
            )
            state["state_revision"] = revision
            self._save_metric_history(
                connection,
                state=state,
                built=built,
                state_revision=revision,
                created_at=now_utc,
            )
            transitions = self._evaluate_events(
                connection,
                state=state,
                state_revision=revision,
                now_utc=now_utc,
                now_monotonic_ns=built.committed_monotonic_ns,
                status_evidence_key=built.status_evidence_key,
                clipping_window_full=built.clipping_window_full,
                trace_id=trace_id,
            )
            transitions.extend(
                self._evaluate_alert_rules(
                    connection,
                    state=state,
                    state_revision=revision,
                    now_utc=now_utc,
                    trace_id=trace_id,
                    motion_score=built.motion_analysis.score,
                )
            )
            del transitions
            active_rows = connection.execute(
                """
                SELECT event_json FROM health_events
                WHERE wearer_id = ? AND status = 'active'
                ORDER BY event_type ASC
                """,
                (wearer_id,),
            ).fetchall()
            state["active_events"] = [
                _event_ref(json.loads(str(row["event_json"]))) for row in active_rows
            ]
            connection.execute(
                """
                INSERT INTO health_states(
                    wearer_id, state_revision, source_instance_id, state_json,
                    committed_monotonic_ns, ecg_received_monotonic_ns,
                    transport_received_monotonic_ns, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wearer_id) DO UPDATE SET
                    state_revision=excluded.state_revision,
                    source_instance_id=excluded.source_instance_id,
                    state_json=excluded.state_json,
                    committed_monotonic_ns=excluded.committed_monotonic_ns,
                    ecg_received_monotonic_ns=excluded.ecg_received_monotonic_ns,
                    transport_received_monotonic_ns=excluded.transport_received_monotonic_ns,
                    updated_at=excluded.updated_at
                """,
                (
                    wearer_id,
                    revision,
                    state["source_instance_id"],
                    compact_json(state),
                    built.committed_monotonic_ns,
                    built.ecg_received_monotonic_ns,
                    built.transport_received_monotonic_ns,
                    now_utc,
                ),
            )
            connection.execute(
                """
                INSERT INTO health_device_snapshots(
                    wearer_id, source_instance_id, data_source, device_json,
                    committed_monotonic_ns, transport_received_monotonic_ns, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wearer_id) DO UPDATE SET
                    source_instance_id=excluded.source_instance_id,
                    data_source=excluded.data_source,
                    device_json=excluded.device_json,
                    committed_monotonic_ns=excluded.committed_monotonic_ns,
                    transport_received_monotonic_ns=excluded.transport_received_monotonic_ns,
                    updated_at=excluded.updated_at
                """,
                (
                    wearer_id,
                    state["source_instance_id"],
                    state["data_source"],
                    compact_json(state["device"]),
                    built.committed_monotonic_ns,
                    built.transport_received_monotonic_ns,
                    now_utc,
                ),
            )
        return state

    def get_state(self, wearer_id: str) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM health_states WHERE wearer_id = ?",
                (wearer_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "state": json.loads(str(row["state_json"])),
            "committed_monotonic_ns": int(row["committed_monotonic_ns"]),
            "ecg_received_monotonic_ns": int(row["ecg_received_monotonic_ns"]),
            "transport_received_monotonic_ns": int(
                row["transport_received_monotonic_ns"]
            ),
        }

    def list_metric_samples(
        self,
        *,
        wearer_id: str,
        since_utc: str,
        limit: int = 1_000,
    ) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM health_metric_samples
                WHERE wearer_id=? AND observed_at>=?
                ORDER BY observed_at ASC, state_revision ASC
                LIMIT ?
                """,
                (wearer_id, since_utc, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_rr_intervals(
        self,
        *,
        wearer_id: str,
        since_utc: str,
        limit: int = 2_000,
    ) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM health_rr_intervals
                WHERE wearer_id=? AND observed_at>=?
                ORDER BY observed_at ASC, end_sample_index ASC
                LIMIT ?
                """,
                (wearer_id, since_utc, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_sleep_record(
        self,
        *,
        wearer_id: str,
        source_type: str,
        source_session_id: str | None,
        source_sha256: str,
        display_name: str,
        source_name: str,
        lead_name: str,
        sample_rate_hz: float,
        sample_count: int,
        duration_s: float,
        recording_start_time: str | None,
        demographics: dict[str, object],
        provenance: dict[str, object],
        sleep_record_id: str | None = None,
        imported_at: str | None = None,
    ) -> str:
        record_id = sleep_record_id or str(uuid4())
        imported = imported_at or utc_now_millisecond_z()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO health_sleep_records(
                    sleep_record_id, wearer_id, source_type, source_session_id,
                    source_sha256, display_name, source_name, lead_name,
                    sample_rate_hz, sample_count, duration_s,
                    recording_start_time, demographics_json, provenance_json,
                    imported_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    wearer_id,
                    source_type,
                    source_session_id,
                    source_sha256,
                    display_name,
                    source_name,
                    lead_name,
                    float(sample_rate_hz),
                    int(sample_count),
                    float(duration_s),
                    recording_start_time,
                    compact_json(demographics),
                    compact_json(provenance),
                    imported,
                ),
            )
        return record_id

    def start_sleep_analysis_run(
        self,
        *,
        sleep_record_id: str,
        wearer_id: str,
        model_name: str,
        parameters: dict[str, object],
        analysis_run_id: str | None = None,
        started_at: str | None = None,
    ) -> str:
        run_id = analysis_run_id or str(uuid4())
        with self._connection() as connection:
            record = connection.execute(
                """
                SELECT wearer_id FROM health_sleep_records
                WHERE sleep_record_id=?
                """,
                (sleep_record_id,),
            ).fetchone()
            if record is None:
                raise KeyError(sleep_record_id)
            if str(record["wearer_id"]) != wearer_id:
                raise ValueError("sleep record wearer_id does not match analysis wearer_id")
            connection.execute(
                """
                INSERT INTO health_sleep_analysis_runs(
                    analysis_run_id, sleep_record_id, wearer_id, status,
                    model_name, parameters_json, started_at
                ) VALUES(?, ?, ?, 'running', ?, ?, ?)
                """,
                (
                    run_id,
                    sleep_record_id,
                    wearer_id,
                    model_name,
                    compact_json(parameters),
                    started_at or utc_now_millisecond_z(),
                ),
            )
        return run_id

    def complete_sleep_analysis_run(
        self,
        *,
        analysis_run_id: str,
        stages_mode: str,
        sleepecg_version: str,
        tensorflow_version: str,
        heartbeat_count: int,
        summary: dict[str, object],
        quality: dict[str, object],
        epochs: Sequence[dict[str, object]],
        completed_at: str | None = None,
    ) -> None:
        completed = completed_at or utc_now_millisecond_z()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                """
                SELECT status FROM health_sleep_analysis_runs
                WHERE analysis_run_id=?
                """,
                (analysis_run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(analysis_run_id)
            if str(run["status"]) != "running":
                raise ValueError("only running sleep analyses can be completed")
            connection.executemany(
                """
                INSERT INTO health_sleep_epochs(
                    analysis_run_id, epoch_index, start_offset_s, stage,
                    confidence, probabilities_json, reference_stage
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        analysis_run_id,
                        int(epoch["epoch_index"]),
                        int(epoch["start_offset_s"]),
                        str(epoch["stage"]),
                        float(epoch["confidence"]),
                        compact_json(epoch["probabilities"]),
                        epoch.get("reference_stage"),
                    )
                    for epoch in epochs
                ],
            )
            connection.execute(
                """
                UPDATE health_sleep_analysis_runs
                SET status='completed', stages_mode=?, sleepecg_version=?,
                    tensorflow_version=?, heartbeat_count=?, summary_json=?,
                    quality_json=?, error_message=NULL, completed_at=?
                WHERE analysis_run_id=?
                """,
                (
                    stages_mode,
                    sleepecg_version,
                    tensorflow_version,
                    int(heartbeat_count),
                    compact_json(summary),
                    compact_json(quality),
                    completed,
                    analysis_run_id,
                ),
            )

    def fail_sleep_analysis_run(
        self,
        *,
        analysis_run_id: str,
        error_message: str,
        cancelled: bool = False,
        completed_at: str | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE health_sleep_analysis_runs
                SET status=?, error_message=?, completed_at=?
                WHERE analysis_run_id=? AND status='running'
                """,
                (
                    "cancelled" if cancelled else "failed",
                    error_message[:1024],
                    completed_at or utc_now_millisecond_z(),
                    analysis_run_id,
                ),
            )

    def get_sleep_record(
        self,
        *,
        wearer_id: str,
        sleep_record_id: str,
    ) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM health_sleep_records
                WHERE wearer_id=? AND sleep_record_id=?
                """,
                (wearer_id, sleep_record_id),
            ).fetchone()
        return _sleep_record_from_row(row) if row is not None else None

    def get_sleep_report(
        self,
        *,
        wearer_id: str,
        sleep_record_id: str | None = None,
        include_epochs: bool = False,
        epoch_offset: int = 0,
        epoch_limit: int = 240,
    ) -> dict[str, object] | None:
        offset = max(0, int(epoch_offset))
        limit = min(240, max(1, int(epoch_limit)))
        with self._connection() as connection:
            if sleep_record_id is None:
                run = connection.execute(
                    """
                    SELECT runs.*, records.*
                    FROM health_sleep_analysis_runs AS runs
                    JOIN health_sleep_records AS records
                      ON records.sleep_record_id=runs.sleep_record_id
                    WHERE runs.wearer_id=? AND runs.status='completed'
                    ORDER BY runs.completed_at DESC, runs.analysis_run_id DESC
                    LIMIT 1
                    """,
                    (wearer_id,),
                ).fetchone()
            else:
                run = connection.execute(
                    """
                    SELECT runs.*, records.*
                    FROM health_sleep_analysis_runs AS runs
                    JOIN health_sleep_records AS records
                      ON records.sleep_record_id=runs.sleep_record_id
                    WHERE runs.wearer_id=? AND runs.sleep_record_id=?
                      AND runs.status='completed'
                    ORDER BY runs.completed_at DESC, runs.analysis_run_id DESC
                    LIMIT 1
                    """,
                    (wearer_id, sleep_record_id),
                ).fetchone()
            if run is None:
                return None
            total_epochs = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM health_sleep_epochs
                    WHERE analysis_run_id=?
                    """,
                    (run["analysis_run_id"],),
                ).fetchone()["count"]
            )
            epoch_rows: list[sqlite3.Row] = []
            if include_epochs:
                epoch_rows = connection.execute(
                    """
                    SELECT * FROM health_sleep_epochs
                    WHERE analysis_run_id=?
                    ORDER BY epoch_index ASC
                    LIMIT ? OFFSET ?
                    """,
                    (run["analysis_run_id"], limit, offset),
                ).fetchall()
        record = _sleep_record_from_row(run)
        analysis = {
            "analysis_run_id": str(run["analysis_run_id"]),
            "status": str(run["status"]),
            "model_name": str(run["model_name"]),
            "stages_mode": run["stages_mode"],
            "sleepecg_version": run["sleepecg_version"],
            "tensorflow_version": run["tensorflow_version"],
            "heartbeat_count": int(run["heartbeat_count"] or 0),
            "parameters": json.loads(str(run["parameters_json"])),
            "summary": json.loads(str(run["summary_json"])),
            "quality": json.loads(str(run["quality_json"])),
            "started_at": str(run["started_at"]),
            "completed_at": str(run["completed_at"]),
        }
        epochs = [_sleep_epoch_from_row(row) for row in epoch_rows]
        returned = len(epochs)
        return {
            "record": record,
            "analysis": analysis,
            "epochs": epochs,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "returned": returned,
                "total": total_epochs,
                "next_offset": (
                    offset + returned if include_epochs and offset + returned < total_epochs else None
                ),
            },
        }

    def get_sleep_analysis_status(
        self,
        *,
        wearer_id: str,
        sleep_record_id: str,
    ) -> str | None:
        with self._connection() as connection:
            record = connection.execute(
                """
                SELECT 1 FROM health_sleep_records
                WHERE wearer_id=? AND sleep_record_id=?
                """,
                (wearer_id, sleep_record_id),
            ).fetchone()
            if record is None:
                return None
            run = connection.execute(
                """
                SELECT status FROM health_sleep_analysis_runs
                WHERE wearer_id=? AND sleep_record_id=?
                ORDER BY started_at DESC, analysis_run_id DESC
                LIMIT 1
                """,
                (wearer_id, sleep_record_id),
            ).fetchone()
        return str(run["status"]) if run is not None else "not_started"

    def get_event(self, event_id: str) -> dict[str, object] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT event_json FROM health_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return json.loads(str(row["event_json"])) if row is not None else None

    def list_events(
        self,
        *,
        wearer_id: str,
        event_types: Sequence[str] | None = None,
        statuses: Sequence[str] | None = None,
        since: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[dict[str, object]], str | None]:
        filters = {
            "wearer_id": wearer_id,
            "event_types": sorted(event_types or []),
            "statuses": sorted(statuses or []),
            "since": since,
        }
        query_digest = hashlib.sha256(compact_json(filters).encode()).hexdigest()
        cursor_key: tuple[str, str] | None = None
        if cursor is not None:
            try:
                decoded = json.loads(
                    base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                )
                if decoded["query"] != query_digest:
                    raise ValueError("cursor filters do not match")
                cursor_key = (decoded["updated_at"], decoded["event_id"])
            except Exception as exc:
                raise ValueError("invalid recent-events cursor") from exc

        clauses = ["wearer_id = ?"]
        parameters: list[object] = [wearer_id]
        if event_types:
            clauses.append(
                "event_type IN (" + ",".join("?" for _ in event_types) + ")"
            )
            parameters.extend(event_types)
        if statuses:
            clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            parameters.extend(statuses)
        if since is not None:
            clauses.append("updated_at >= ?")
            parameters.append(since)
        if cursor_key is not None:
            clauses.append("(updated_at < ? OR (updated_at = ? AND event_id < ?))")
            parameters.extend((cursor_key[0], cursor_key[0], cursor_key[1]))
        parameters.append(limit + 1)
        sql = (
            "SELECT event_json FROM health_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, event_id DESC LIMIT ?"
        )
        with self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        events = [json.loads(str(row["event_json"])) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit and events:
            last = events[-1]
            encoded = compact_json(
                {
                    "updated_at": last["updated_at"],
                    "event_id": last["event_id"],
                    "query": query_digest,
                }
            ).encode()
            next_cursor = base64.urlsafe_b64encode(encoded).decode().rstrip("=")
        return events, next_cursor

    def record_mcp_audit(
        self,
        *,
        trace_id: str,
        tool_name: str,
        wearer_id: str | None,
        outcome: str,
        error_code: str | None,
        latency_ms: int,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO health_mcp_audit(
                    trace_id, tool_name, wearer_id, outcome,
                    error_code, latency_ms, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    tool_name,
                    wearer_id,
                    outcome,
                    error_code,
                    max(0, latency_ms),
                    utc_now_millisecond_z(),
                ),
            )

    def recover_webhook_deliveries(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE health_webhook_outbox
                SET state='retry_wait', lease_until=NULL, next_attempt_at=?,
                    last_error='previous process exited during delivery'
                WHERE state='delivering'
                  AND COALESCE(lease_until, 0) <= ?
                """,
                (time.time(), time.time()),
            )
            return int(cursor.rowcount)

    def claim_next_outbox(
        self,
        *,
        now_epoch_s: float | None = None,
    ) -> dict[str, object] | None:
        now_epoch_s = time.time() if now_epoch_s is None else now_epoch_s
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT candidate.*
                FROM health_webhook_outbox AS candidate
                WHERE (
                        candidate.state IN ('pending', 'retry_wait')
                        OR (
                            candidate.state='delivering'
                            AND COALESCE(candidate.lease_until, 0) <= ?
                        )
                      )
                  AND candidate.next_attempt_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM health_webhook_outbox AS earlier
                      WHERE earlier.wearer_id = candidate.wearer_id
                        AND earlier.notification_sequence
                            < candidate.notification_sequence
                  )
                ORDER BY candidate.next_attempt_at ASC,
                         candidate.wearer_id ASC,
                         candidate.notification_sequence ASC
                LIMIT 1
                """,
                (now_epoch_s, now_epoch_s),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE health_webhook_outbox
                SET state='delivering',
                    lease_until=?,
                    attempt_count=attempt_count+1,
                    first_attempt_at=COALESCE(first_attempt_at, ?)
                WHERE notification_id=?
                """,
                (now_epoch_s + 30.0, now_epoch_s, row["notification_id"]),
            )
            claimed = connection.execute(
                """
                SELECT * FROM health_webhook_outbox
                WHERE notification_id=?
                """,
                (row["notification_id"],),
            ).fetchone()
        return _outbox_from_row(claimed)

    def mark_outbox_delivered(
        self,
        notification_id: str,
        *,
        http_status: int,
    ) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM health_webhook_outbox
                WHERE notification_id=?
                """,
                (notification_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                INSERT INTO health_webhook_deliveries(
                    notification_id, wearer_id, notification_sequence,
                    attempt_count, http_status, delivered_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    notification_id,
                    row["wearer_id"],
                    row["notification_sequence"],
                    row["attempt_count"],
                    http_status,
                    utc_now_millisecond_z(),
                ),
            )
            connection.execute(
                "DELETE FROM health_webhook_outbox WHERE notification_id=?",
                (notification_id,),
            )

    def mark_outbox_retry(
        self,
        notification_id: str,
        *,
        delay_s: float,
        error: str,
        http_status: int | None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE health_webhook_outbox
                SET state='retry_wait', lease_until=NULL, next_attempt_at=?,
                    last_error=?, last_http_status=?
                WHERE notification_id=?
                """,
                (
                    time.time() + max(0.0, delay_s),
                    error[:256],
                    http_status,
                    notification_id,
                ),
            )

    def move_outbox_to_dead_letter(
        self,
        notification_id: str,
        *,
        error_category: str,
        http_status: int | None,
    ) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM health_webhook_outbox
                WHERE notification_id=?
                """,
                (notification_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                INSERT INTO health_webhook_dead_letters(
                    notification_id, wearer_id, notification_sequence,
                    raw_body_sha256, attempt_count, last_http_status,
                    error_category, created_at, dead_lettered_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification_id,
                    row["wearer_id"],
                    row["notification_sequence"],
                    row["raw_body_sha256"],
                    row["attempt_count"],
                    http_status,
                    error_category[:128],
                    row["created_at"],
                    utc_now_millisecond_z(),
                ),
            )
            connection.execute(
                "DELETE FROM health_webhook_outbox WHERE notification_id=?",
                (notification_id,),
            )

    def list_outbox(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM health_webhook_outbox
                ORDER BY wearer_id, notification_sequence
                """
            ).fetchall()
        return [_outbox_from_row(row) for row in rows]

    def list_dead_letters(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM health_webhook_dead_letters
                ORDER BY dead_lettered_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def cleanup_retention(self, *, now: datetime | None = None) -> dict[str, int]:
        current = now or datetime.now(timezone.utc)
        seven_day = _timestamp(current - timedelta(days=7))
        thirty_day = _timestamp(current - timedelta(days=30))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            counts = {
                "resolved_events": int(
                    connection.execute(
                        """
                        DELETE FROM health_events
                        WHERE status='resolved' AND updated_at < ?
                        """,
                        (seven_day,),
                    ).rowcount
                ),
                "deliveries": int(
                    connection.execute(
                        """
                        DELETE FROM health_webhook_deliveries
                        WHERE delivered_at < ?
                        """,
                        (seven_day,),
                    ).rowcount
                ),
                "dead_letters": int(
                    connection.execute(
                        """
                        DELETE FROM health_webhook_dead_letters
                        WHERE dead_lettered_at < ?
                        """,
                        (thirty_day,),
                    ).rowcount
                ),
                "mcp_audit": int(
                    connection.execute(
                        """
                        DELETE FROM health_mcp_audit
                        WHERE created_at < ?
                        """,
                        (thirty_day,),
                    ).rowcount
                ),
                "metric_samples": int(
                    connection.execute(
                        """
                        DELETE FROM health_metric_samples
                        WHERE observed_at < ?
                        """,
                        (seven_day,),
                    ).rowcount
                ),
                "rr_intervals": int(
                    connection.execute(
                        """
                        DELETE FROM health_rr_intervals
                        WHERE observed_at < ?
                        """,
                        (seven_day,),
                    ).rowcount
                ),
            }
        return counts

    def deletion_counts(
        self,
        *,
        wearer_id: str,
        before_utc: str,
    ) -> dict[str, int]:
        with self._connection() as connection:
            return self._deletion_counts(connection, wearer_id, before_utc)

    def delete_wearer_records(
        self,
        *,
        wearer_id: str,
        before_utc: str,
        expected_counts: dict[str, int],
    ) -> dict[str, int]:
        with self._connection() as connection:
            connection.execute("BEGIN EXCLUSIVE")
            current = self._deletion_counts(connection, wearer_id, before_utc)
            if current != expected_counts:
                raise ValueError("deletion counts changed; generate a new confirmation plan")
            statements = {
                "states": (
                    "DELETE FROM health_states WHERE wearer_id=? AND updated_at<=?"
                ),
                "device_snapshots": (
                    "DELETE FROM health_device_snapshots "
                    "WHERE wearer_id=? AND updated_at<=?"
                ),
                "events": (
                    "DELETE FROM health_events WHERE wearer_id=? AND updated_at<=?"
                ),
                "outbox": (
                    "DELETE FROM health_webhook_outbox "
                    "WHERE wearer_id=? AND created_at<=?"
                ),
                "dead_letters": (
                    "DELETE FROM health_webhook_dead_letters "
                    "WHERE wearer_id=? AND created_at<=?"
                ),
                "deliveries": (
                    "DELETE FROM health_webhook_deliveries "
                    "WHERE wearer_id=? AND delivered_at<=?"
                ),
                "mcp_audit": (
                    "DELETE FROM health_mcp_audit "
                    "WHERE wearer_id=? AND created_at<=?"
                ),
                "runtime_observability": (
                    "DELETE FROM health_runtime_observability "
                    "WHERE wearer_id=? AND updated_at<=?"
                ),
                "metric_samples": (
                    "DELETE FROM health_metric_samples "
                    "WHERE wearer_id=? AND observed_at<=?"
                ),
                "rr_intervals": (
                    "DELETE FROM health_rr_intervals "
                    "WHERE wearer_id=? AND observed_at<=?"
                ),
                "sleep_records": (
                    "DELETE FROM health_sleep_records "
                    "WHERE wearer_id=? AND imported_at<=?"
                ),
            }
            deleted = {
                name: int(
                    connection.execute(
                        statement,
                        (wearer_id, before_utc),
                    ).rowcount
                )
                for name, statement in statements.items()
            }
            if not self._wearer_has_time_records(connection, wearer_id):
                deleted["event_gates"] = int(
                    connection.execute(
                        "DELETE FROM health_event_gates WHERE wearer_id=?",
                        (wearer_id,),
                    ).rowcount
                )
                deleted["alert_rule_state"] = int(
                    connection.execute(
                        "DELETE FROM health_alert_rule_state WHERE wearer_id=?",
                        (wearer_id,),
                    ).rowcount
                )
            else:
                deleted["event_gates"] = 0
                deleted["alert_rule_state"] = 0
            connection.execute(
                """
                INSERT INTO health_deletion_audit(
                    wearer_id, before_utc, counts_json, deleted_at
                ) VALUES(?, ?, ?, ?)
                """,
                (
                    wearer_id,
                    before_utc,
                    compact_json(deleted),
                    utc_now_millisecond_z(),
                ),
            )
        return deleted

    @staticmethod
    def _deletion_counts(
        connection: sqlite3.Connection,
        wearer_id: str,
        before_utc: str,
    ) -> dict[str, int]:
        queries = {
            "states": (
                "SELECT COUNT(*) AS count FROM health_states "
                "WHERE wearer_id=? AND updated_at<=?"
            ),
            "device_snapshots": (
                "SELECT COUNT(*) AS count FROM health_device_snapshots "
                "WHERE wearer_id=? AND updated_at<=?"
            ),
            "events": (
                "SELECT COUNT(*) AS count FROM health_events "
                "WHERE wearer_id=? AND updated_at<=?"
            ),
            "outbox": (
                "SELECT COUNT(*) AS count FROM health_webhook_outbox "
                "WHERE wearer_id=? AND created_at<=?"
            ),
            "dead_letters": (
                "SELECT COUNT(*) AS count FROM health_webhook_dead_letters "
                "WHERE wearer_id=? AND created_at<=?"
            ),
            "deliveries": (
                "SELECT COUNT(*) AS count FROM health_webhook_deliveries "
                "WHERE wearer_id=? AND delivered_at<=?"
            ),
            "mcp_audit": (
                "SELECT COUNT(*) AS count FROM health_mcp_audit "
                "WHERE wearer_id=? AND created_at<=?"
            ),
            "runtime_observability": (
                "SELECT COUNT(*) AS count FROM health_runtime_observability "
                "WHERE wearer_id=? AND updated_at<=?"
            ),
            "metric_samples": (
                "SELECT COUNT(*) AS count FROM health_metric_samples "
                "WHERE wearer_id=? AND observed_at<=?"
            ),
            "rr_intervals": (
                "SELECT COUNT(*) AS count FROM health_rr_intervals "
                "WHERE wearer_id=? AND observed_at<=?"
            ),
            "sleep_records": (
                "SELECT COUNT(*) AS count FROM health_sleep_records "
                "WHERE wearer_id=? AND imported_at<=?"
            ),
        }
        counts = {
            name: int(
                connection.execute(query, (wearer_id, before_utc)).fetchone()[
                    "count"
                ]
            )
            for name, query in queries.items()
        }
        if HealthStore._wearer_has_records_after(
            connection,
            wearer_id,
            before_utc,
        ):
            counts["event_gates"] = 0
            counts["alert_rule_state"] = 0
        else:
            counts["event_gates"] = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM health_event_gates
                    WHERE wearer_id=?
                    """,
                    (wearer_id,),
                ).fetchone()["count"]
            )
            counts["alert_rule_state"] = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM health_alert_rule_state
                    WHERE wearer_id=?
                    """,
                    (wearer_id,),
                ).fetchone()["count"]
            )
        return counts

    @staticmethod
    def _wearer_has_time_records(
        connection: sqlite3.Connection,
        wearer_id: str,
    ) -> bool:
        queries = (
            "SELECT 1 FROM health_states WHERE wearer_id=?",
            "SELECT 1 FROM health_device_snapshots WHERE wearer_id=?",
            "SELECT 1 FROM health_runtime_observability WHERE wearer_id=?",
            "SELECT 1 FROM health_events WHERE wearer_id=?",
            "SELECT 1 FROM health_webhook_outbox WHERE wearer_id=?",
            "SELECT 1 FROM health_webhook_dead_letters WHERE wearer_id=?",
            "SELECT 1 FROM health_webhook_deliveries WHERE wearer_id=?",
            "SELECT 1 FROM health_mcp_audit WHERE wearer_id=?",
            "SELECT 1 FROM health_metric_samples WHERE wearer_id=?",
            "SELECT 1 FROM health_rr_intervals WHERE wearer_id=?",
            "SELECT 1 FROM health_sleep_records WHERE wearer_id=?",
        )
        return any(
            connection.execute(query + " LIMIT 1", (wearer_id,)).fetchone()
            is not None
            for query in queries
        )

    @staticmethod
    def _wearer_has_records_after(
        connection: sqlite3.Connection,
        wearer_id: str,
        after_utc: str,
    ) -> bool:
        queries = (
            "SELECT 1 FROM health_states WHERE wearer_id=? AND updated_at>?",
            (
                "SELECT 1 FROM health_device_snapshots "
                "WHERE wearer_id=? AND updated_at>?"
            ),
            (
                "SELECT 1 FROM health_runtime_observability "
                "WHERE wearer_id=? AND updated_at>?"
            ),
            "SELECT 1 FROM health_events WHERE wearer_id=? AND updated_at>?",
            (
                "SELECT 1 FROM health_webhook_outbox "
                "WHERE wearer_id=? AND created_at>?"
            ),
            (
                "SELECT 1 FROM health_webhook_dead_letters "
                "WHERE wearer_id=? AND created_at>?"
            ),
            (
                "SELECT 1 FROM health_webhook_deliveries "
                "WHERE wearer_id=? AND delivered_at>?"
            ),
            (
                "SELECT 1 FROM health_mcp_audit "
                "WHERE wearer_id=? AND created_at>?"
            ),
            (
                "SELECT 1 FROM health_metric_samples "
                "WHERE wearer_id=? AND observed_at>?"
            ),
            (
                "SELECT 1 FROM health_rr_intervals "
                "WHERE wearer_id=? AND observed_at>?"
            ),
            (
                "SELECT 1 FROM health_sleep_records "
                "WHERE wearer_id=? AND imported_at>?"
            ),
        )
        return any(
            connection.execute(
                query + " LIMIT 1",
                (wearer_id, after_utc),
            ).fetchone()
            is not None
            for query in queries
        )

    def _save_metric_history(
        self,
        connection: sqlite3.Connection,
        *,
        state: dict[str, object],
        built: BuiltHealthState,
        state_revision: int,
        created_at: str,
    ) -> None:
        wearer_id = str(state["wearer_id"])
        source_instance_id = str(state["source_instance_id"])
        heart = state["heart"]["heart_rate"]
        signal = state["signal"]
        device = state["device"]
        motion = built.motion_analysis
        connection.execute(
            """
            INSERT INTO health_metric_samples(
                wearer_id, state_revision, source_instance_id, observed_at,
                data_source, test_mode, heart_rate_bpm, signal_quality,
                lead_off, adc_clipping_ratio, motion_score,
                still_ratio_percent, motion_level, motion_coverage_ratio,
                imu_online, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                wearer_id,
                state_revision,
                source_instance_id,
                state["observed_at"],
                state["data_source"],
                1 if bool(state["test_mode"]) else 0,
                heart["value"] if bool(heart["valid"]) else None,
                signal["quality_score"],
                (
                    None
                    if signal["lead_off"] is None
                    else 1 if bool(signal["lead_off"]) else 0
                ),
                signal["adc_clipping_ratio_10s"],
                motion.score,
                motion.still_ratio_percent,
                motion.level,
                motion.coverage_ratio,
                (
                    None
                    if device["imu_online"] is None
                    else 1 if bool(device["imu_online"]) else 0
                ),
                created_at,
            ),
        )
        for observation in built.rr_intervals:
            if observation.source_instance_id != source_instance_id:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO health_rr_intervals(
                    wearer_id, source_instance_id, end_sample_index,
                    observed_at, rr_ms, created_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    wearer_id,
                    observation.source_instance_id,
                    observation.end_sample_index,
                    observation.observed_at,
                    observation.rr_ms,
                    created_at,
                ),
            )

    def _evaluate_alert_rules(
        self,
        connection: sqlite3.Connection,
        *,
        state: dict[str, object],
        state_revision: int,
        now_utc: str,
        trace_id: str,
        motion_score: float | None,
    ) -> list[dict[str, object]]:
        if not self.alert_rules:
            return []
        wearer_id = str(state["wearer_id"])
        now = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
        since = _timestamp(now - timedelta(seconds=300))
        rr_values = [
            float(row["rr_ms"])
            for row in connection.execute(
                """
                SELECT rr_ms FROM health_rr_intervals
                WHERE wearer_id=? AND observed_at>=?
                ORDER BY observed_at ASC, end_sample_index ASC
                """,
                (wearer_id, since),
            ).fetchall()
        ]
        heart_metric = state["heart"]["heart_rate"]
        signal = state["signal"]
        metrics: dict[str, float | None] = {
            "heart_rate_bpm": (
                float(heart_metric["value"])
                if bool(heart_metric["valid"])
                and heart_metric["value"] is not None
                else None
            ),
            "hrv_rmssd_ms": rmssd_ms(rr_values),
            "motion_score": motion_score,
            "signal_quality": (
                float(signal["quality_score"])
                if signal["quality_score"] is not None
                else None
            ),
        }
        quality_blocked = (
            signal["lead_off"] is True
            or (
                signal["adc_clipping_ratio_10s"] is not None
                and float(signal["adc_clipping_ratio_10s"]) >= 0.8
            )
            or str(state["freshness"]) != "fresh"
        )
        transitions: list[dict[str, object]] = []
        for rule in self.alert_rules:
            connection.execute(
                """
                INSERT INTO health_alert_rule_state(wearer_id, rule_id)
                VALUES(?, ?)
                ON CONFLICT(wearer_id, rule_id) DO NOTHING
                """,
                (wearer_id, rule.rule_id),
            )
            row = connection.execute(
                """
                SELECT * FROM health_alert_rule_state
                WHERE wearer_id=? AND rule_id=?
                """,
                (wearer_id, rule.rule_id),
            ).fetchone()
            assert row is not None
            evidence = _rule_evidence(rule, metrics, signal, len(rr_values))
            matched = not quality_blocked and rule.matches(metrics)
            active_event_id = row["active_event_id"]
            if active_event_id is not None:
                event_row = connection.execute(
                    "SELECT event_json FROM health_events WHERE event_id=?",
                    (active_event_id,),
                ).fetchone()
                if event_row is None:
                    connection.execute(
                        """
                        UPDATE health_alert_rule_state
                        SET active_event_id=NULL, matched_since_at=NULL,
                            clear_since_at=NULL
                        WHERE wearer_id=? AND rule_id=?
                        """,
                        (wearer_id, rule.rule_id),
                    )
                    continue
                event = json.loads(str(event_row["event_json"]))
                if matched:
                    connection.execute(
                        """
                        UPDATE health_alert_rule_state
                        SET clear_since_at=NULL, last_evidence_json=?
                        WHERE wearer_id=? AND rule_id=?
                        """,
                        (compact_json(evidence), wearer_id, rule.rule_id),
                    )
                    continue
                clear_since = row["clear_since_at"]
                if clear_since is None:
                    clear_since = now_utc
                    connection.execute(
                        """
                        UPDATE health_alert_rule_state SET clear_since_at=?
                        WHERE wearer_id=? AND rule_id=?
                        """,
                        (clear_since, wearer_id, rule.rule_id),
                    )
                if _elapsed_seconds(now, str(clear_since)) < rule.clear_for_s:
                    continue
                resolved = self._resolve_rule_event(
                    connection,
                    event=event,
                    state=state,
                    state_revision=state_revision,
                    now_utc=now_utc,
                    trace_id=trace_id,
                    evidence=evidence,
                )
                transitions.append(resolved)
                cooldown_until = _timestamp(now + timedelta(seconds=rule.cooldown_s))
                connection.execute(
                    """
                    UPDATE health_alert_rule_state
                    SET active_event_id=NULL, matched_since_at=NULL,
                        clear_since_at=NULL, cooldown_until_at=?,
                        last_evidence_json=?
                    WHERE wearer_id=? AND rule_id=?
                    """,
                    (
                        cooldown_until,
                        compact_json(evidence),
                        wearer_id,
                        rule.rule_id,
                    ),
                )
                continue

            if not matched:
                connection.execute(
                    """
                    UPDATE health_alert_rule_state
                    SET matched_since_at=NULL, clear_since_at=NULL,
                        last_evidence_json=?
                    WHERE wearer_id=? AND rule_id=?
                    """,
                    (compact_json(evidence), wearer_id, rule.rule_id),
                )
                continue
            cooldown_until = row["cooldown_until_at"]
            if cooldown_until is not None and now < _parse_timestamp(str(cooldown_until)):
                continue
            matched_since = row["matched_since_at"]
            if matched_since is None:
                matched_since = now_utc
                connection.execute(
                    """
                    UPDATE health_alert_rule_state
                    SET matched_since_at=?, last_evidence_json=?
                    WHERE wearer_id=? AND rule_id=?
                    """,
                    (
                        matched_since,
                        compact_json(evidence),
                        wearer_id,
                        rule.rule_id,
                    ),
                )
            if _elapsed_seconds(now, str(matched_since)) < rule.for_s:
                continue
            opened = self._open_rule_event(
                connection,
                rule=rule,
                state=state,
                state_revision=state_revision,
                now_utc=now_utc,
                trace_id=trace_id,
                evidence=evidence,
            )
            transitions.append(opened)
            connection.execute(
                """
                UPDATE health_alert_rule_state
                SET active_event_id=?, matched_since_at=NULL,
                    clear_since_at=NULL, last_evidence_json=?
                WHERE wearer_id=? AND rule_id=?
                """,
                (
                    opened["event_id"],
                    compact_json(evidence),
                    wearer_id,
                    rule.rule_id,
                ),
            )
        return transitions

    def _open_rule_event(
        self,
        connection: sqlite3.Connection,
        *,
        rule: HealthAlertRule,
        state: dict[str, object],
        state_revision: int,
        now_utc: str,
        trace_id: str,
        evidence: dict[str, object],
    ) -> dict[str, object]:
        event = {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "event_id": str(uuid4()),
            "event_revision": 1,
            "event_type": rule.event_type,
            "wearer_id": state["wearer_id"],
            "source_instance_id": state["source_instance_id"],
            "data_source": state["data_source"],
            "status": "active",
            "severity": rule.severity,
            "bridge_severity": rule.severity,
            "priority": rule.priority,
            "summary": rule.summary,
            "opened_at": now_utc,
            "updated_at": now_utc,
            "resolved_at": None,
            "state_revision": state_revision,
            "evidence": evidence,
            "recommended_capabilities": list(rule.recommended_capabilities),
            "test_mode": state["test_mode"],
        }
        self._save_event(connection, event)
        self._enqueue_transition(
            connection,
            event=event,
            transition="opened",
            occurred_at=now_utc,
            trace_id=trace_id,
        )
        return event

    def _resolve_rule_event(
        self,
        connection: sqlite3.Connection,
        *,
        event: dict[str, object],
        state: dict[str, object],
        state_revision: int,
        now_utc: str,
        trace_id: str,
        evidence: dict[str, object],
    ) -> dict[str, object]:
        resolved = deepcopy(event)
        resolved.update(
            {
                "event_revision": int(event["event_revision"]) + 1,
                "status": "resolved",
                "updated_at": now_utc,
                "resolved_at": now_utc,
                "state_revision": state_revision,
                "source_instance_id": state["source_instance_id"],
                "data_source": state["data_source"],
                "evidence": evidence,
                "test_mode": state["test_mode"],
            }
        )
        self._save_event(connection, resolved)
        self._enqueue_transition(
            connection,
            event=resolved,
            transition="resolved",
            occurred_at=now_utc,
            trace_id=trace_id,
        )
        return resolved

    def _evaluate_events(
        self,
        connection: sqlite3.Connection,
        *,
        state: dict[str, object],
        state_revision: int,
        now_utc: str,
        now_monotonic_ns: int,
        status_evidence_key: str | None,
        clipping_window_full: bool,
        trace_id: str,
    ) -> list[dict[str, object]]:
        wearer_id = str(state["wearer_id"])
        active = {
            str(row["event_type"]): json.loads(str(row["event_json"]))
            for row in connection.execute(
                """
                SELECT event_type, event_json FROM health_events
                WHERE wearer_id = ? AND status = 'active'
                """,
                (wearer_id,),
            ).fetchall()
        }
        transitions: list[dict[str, object]] = []
        freshness = str(state["freshness"])

        if freshness == "offline":
            if "input_stale" in active:
                transitions.append(
                    self._resolve_event(
                        connection,
                        event=active.pop("input_stale"),
                        state=state,
                        state_revision=state_revision,
                        now_utc=now_utc,
                        trace_id=trace_id,
                    )
                )
            if "input_offline" not in active:
                opened = self._open_event(
                    connection,
                    event_type="input_offline",
                    state=state,
                    state_revision=state_revision,
                    now_utc=now_utc,
                    trace_id=trace_id,
                )
                active["input_offline"] = opened
                transitions.append(opened)
            self._reset_gate(connection, wearer_id, "input_stale")
        elif freshness == "stale":
            if "input_stale" not in active:
                opened = self._open_event(
                    connection,
                    event_type="input_stale",
                    state=state,
                    state_revision=state_revision,
                    now_utc=now_utc,
                    trace_id=trace_id,
                )
                active["input_stale"] = opened
                transitions.append(opened)
            self._reset_gate(connection, wearer_id, "input_offline")
        else:
            for event_type in ("input_stale", "input_offline"):
                if event_type in active and self._fresh_for_one_second(
                    connection,
                    wearer_id,
                    event_type,
                    now_monotonic_ns,
                ):
                    transitions.append(
                        self._resolve_event(
                            connection,
                            event=active.pop(event_type),
                            state=state,
                            state_revision=state_revision,
                            now_utc=now_utc,
                            trace_id=trace_id,
                        )
                    )

        lead_off = state["signal"]["lead_off"]
        if lead_off is True and "lead_off" not in active:
            opened = self._open_event(
                connection,
                event_type="lead_off",
                state=state,
                state_revision=state_revision,
                now_utc=now_utc,
                trace_id=trace_id,
            )
            active["lead_off"] = opened
            transitions.append(opened)
        elif (
            lead_off is False
            and "lead_off" in active
            and status_evidence_key is not None
            and self._lead_clear_count(
                connection,
                wearer_id,
                status_evidence_key,
            )
            >= 3
        ):
            transitions.append(
                self._resolve_event(
                    connection,
                    event=active.pop("lead_off"),
                    state=state,
                    state_revision=state_revision,
                    now_utc=now_utc,
                    trace_id=trace_id,
                )
            )
        elif lead_off is True:
            self._reset_gate(connection, wearer_id, "lead_off")

        clip_ratio = state["signal"]["adc_clipping_ratio_10s"]
        if (
            clipping_window_full
            and clip_ratio is not None
            and float(clip_ratio) >= 0.80
            and "adc_clipping" not in active
        ):
            opened = self._open_event(
                connection,
                event_type="adc_clipping",
                state=state,
                state_revision=state_revision,
                now_utc=now_utc,
                trace_id=trace_id,
            )
            active["adc_clipping"] = opened
            transitions.append(opened)
        elif "adc_clipping" in active:
            if freshness == "fresh" and clip_ratio is not None and float(clip_ratio) < 0.20:
                if self._clear_for_ten_seconds(
                    connection,
                    wearer_id,
                    now_monotonic_ns,
                ):
                    transitions.append(
                        self._resolve_event(
                            connection,
                            event=active.pop("adc_clipping"),
                            state=state,
                            state_revision=state_revision,
                            now_utc=now_utc,
                            trace_id=trace_id,
                        )
                    )
            else:
                self._reset_gate(connection, wearer_id, "adc_clipping")
        return transitions

    def _open_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: str,
        state: dict[str, object],
        state_revision: int,
        now_utc: str,
        trace_id: str,
    ) -> dict[str, object]:
        event = _event_document(
            event_id=str(uuid4()),
            event_revision=1,
            event_type=event_type,
            status="active",
            opened_at=now_utc,
            updated_at=now_utc,
            resolved_at=None,
            state=state,
            state_revision=state_revision,
        )
        self._save_event(connection, event)
        self._enqueue_transition(
            connection,
            event=event,
            transition="opened",
            occurred_at=now_utc,
            trace_id=trace_id,
        )
        return event

    def _resolve_event(
        self,
        connection: sqlite3.Connection,
        *,
        event: dict[str, object],
        state: dict[str, object],
        state_revision: int,
        now_utc: str,
        trace_id: str,
    ) -> dict[str, object]:
        resolved = _event_document(
            event_id=str(event["event_id"]),
            event_revision=int(event["event_revision"]) + 1,
            event_type=str(event["event_type"]),
            status="resolved",
            opened_at=str(event["opened_at"]),
            updated_at=now_utc,
            resolved_at=now_utc,
            state=state,
            state_revision=state_revision,
        )
        self._save_event(connection, resolved)
        self._enqueue_transition(
            connection,
            event=resolved,
            transition="resolved",
            occurred_at=now_utc,
            trace_id=trace_id,
        )
        self._reset_gate(
            connection,
            str(state["wearer_id"]),
            str(event["event_type"]),
        )
        return resolved

    def _save_event(
        self,
        connection: sqlite3.Connection,
        event: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO health_events(
                event_id, wearer_id, event_type, status, event_revision,
                source_instance_id, updated_at, event_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                status=excluded.status,
                event_revision=excluded.event_revision,
                source_instance_id=excluded.source_instance_id,
                updated_at=excluded.updated_at,
                event_json=excluded.event_json
            """,
            (
                event["event_id"],
                event["wearer_id"],
                event["event_type"],
                event["status"],
                event["event_revision"],
                event["source_instance_id"],
                event["updated_at"],
                compact_json(event),
            ),
        )

    def _enqueue_transition(
        self,
        connection: sqlite3.Connection,
        *,
        event: dict[str, object],
        transition: str,
        occurred_at: str,
        trace_id: str,
    ) -> None:
        if event["data_source"] != "live" or bool(event["test_mode"]):
            return
        wearer_id = str(event["wearer_id"])
        connection.execute(
            """
            UPDATE health_wearer_sequences
            SET notification_sequence = notification_sequence + 1
            WHERE wearer_id = ?
            """,
            (wearer_id,),
        )
        sequence = int(
            connection.execute(
                """
                SELECT notification_sequence FROM health_wearer_sequences
                WHERE wearer_id = ?
                """,
                (wearer_id,),
            ).fetchone()["notification_sequence"]
        )
        notification_id = str(uuid4())
        bridge = _bridge_fields(event, transition=transition)
        body = canonical_webhook_body(
            {
                "schema_version": BRIDGE_SCHEMA_VERSION,
                "notification_id": notification_id,
                "notification_sequence": sequence,
                "event_id": event["event_id"],
                "event_revision": event["event_revision"],
                "transition": transition,
                "event_type": bridge["event_type"],
                "severity": bridge["severity"],
                "priority": bridge["priority"],
                "wearer_id": wearer_id,
                "source_instance_id": event["source_instance_id"],
                "state_revision": event["state_revision"],
                "data_source": event["data_source"],
                "occurred_at": occurred_at,
                "sent_at": occurred_at,
                "trace_id": trace_id,
                "test_mode": event["test_mode"],
                "summary": bridge["summary"],
                "evidence": bridge["evidence"],
                "recommended_capabilities": bridge[
                    "recommended_capabilities"
                ],
            }
        )
        connection.execute(
            """
            INSERT INTO health_webhook_outbox(
                notification_id, wearer_id, notification_sequence,
                event_id, event_revision, raw_body, raw_body_sha256,
                state, next_attempt_at, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                notification_id,
                wearer_id,
                sequence,
                event["event_id"],
                event["event_revision"],
                body,
                hashlib.sha256(body).hexdigest(),
                time.time(),
                occurred_at,
            ),
        )

    def _gate(
        self,
        connection: sqlite3.Connection,
        wearer_id: str,
        event_type: str,
    ) -> sqlite3.Row:
        connection.execute(
            """
            INSERT INTO health_event_gates(wearer_id, event_type)
            VALUES(?, ?)
            ON CONFLICT(wearer_id, event_type) DO NOTHING
            """,
            (wearer_id, event_type),
        )
        return connection.execute(
            """
            SELECT * FROM health_event_gates
            WHERE wearer_id = ? AND event_type = ?
            """,
            (wearer_id, event_type),
        ).fetchone()

    def _reset_gate(
        self,
        connection: sqlite3.Connection,
        wearer_id: str,
        event_type: str,
    ) -> None:
        self._gate(connection, wearer_id, event_type)
        connection.execute(
            """
            UPDATE health_event_gates
            SET clear_count=0, last_evidence_key=NULL,
                clear_since_ns=NULL, fresh_since_ns=NULL
            WHERE wearer_id=? AND event_type=?
            """,
            (wearer_id, event_type),
        )

    def _lead_clear_count(
        self,
        connection: sqlite3.Connection,
        wearer_id: str,
        evidence_key: str,
    ) -> int:
        row = self._gate(connection, wearer_id, "lead_off")
        if row["last_evidence_key"] != evidence_key:
            connection.execute(
                """
                UPDATE health_event_gates
                SET clear_count=clear_count+1, last_evidence_key=?
                WHERE wearer_id=? AND event_type='lead_off'
                """,
                (evidence_key, wearer_id),
            )
        return int(
            connection.execute(
                """
                SELECT clear_count FROM health_event_gates
                WHERE wearer_id=? AND event_type='lead_off'
                """,
                (wearer_id,),
            ).fetchone()["clear_count"]
        )

    def _fresh_for_one_second(
        self,
        connection: sqlite3.Connection,
        wearer_id: str,
        event_type: str,
        now_ns: int,
    ) -> bool:
        row = self._gate(connection, wearer_id, event_type)
        if row["fresh_since_ns"] is None:
            connection.execute(
                """
                UPDATE health_event_gates SET fresh_since_ns=?
                WHERE wearer_id=? AND event_type=?
                """,
                (now_ns, wearer_id, event_type),
            )
            return False
        return now_ns - int(row["fresh_since_ns"]) >= 1_000_000_000

    def _clear_for_ten_seconds(
        self,
        connection: sqlite3.Connection,
        wearer_id: str,
        now_ns: int,
    ) -> bool:
        row = self._gate(connection, wearer_id, "adc_clipping")
        if row["clear_since_ns"] is None:
            connection.execute(
                """
                UPDATE health_event_gates SET clear_since_ns=?
                WHERE wearer_id=? AND event_type='adc_clipping'
                """,
                (now_ns, wearer_id),
            )
            return False
        return now_ns - int(row["clear_since_ns"]) >= 10_000_000_000


def _event_ref(event: dict[str, object]) -> dict[str, object]:
    return {
        "event_id": event["event_id"],
        "event_revision": event["event_revision"],
        "event_type": event["event_type"],
        "status": "active",
        "severity": event["severity"],
        "opened_at": event["opened_at"],
        "updated_at": event["updated_at"],
    }


def _outbox_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "notification_id": str(row["notification_id"]),
        "wearer_id": str(row["wearer_id"]),
        "notification_sequence": int(row["notification_sequence"]),
        "event_id": str(row["event_id"]),
        "event_revision": int(row["event_revision"]),
        "raw_body": bytes(row["raw_body"]),
        "raw_body_sha256": str(row["raw_body_sha256"]),
        "state": str(row["state"]),
        "lease_until": (
            float(row["lease_until"]) if row["lease_until"] is not None else None
        ),
        "attempt_count": int(row["attempt_count"]),
        "first_attempt_at": (
            float(row["first_attempt_at"])
            if row["first_attempt_at"] is not None
            else None
        ),
        "next_attempt_at": float(row["next_attempt_at"]),
        "last_http_status": (
            int(row["last_http_status"])
            if row["last_http_status"] is not None
            else None
        ),
        "last_error": (
            str(row["last_error"]) if row["last_error"] is not None else None
        ),
        "created_at": str(row["created_at"]),
    }


def _event_document(
    *,
    event_id: str,
    event_revision: int,
    event_type: str,
    status: str,
    opened_at: str,
    updated_at: str,
    resolved_at: str | None,
    state: dict[str, object],
    state_revision: int,
) -> dict[str, object]:
    signal = state["signal"]
    device = state["device"]
    detail = (
        f"{event_type} became {status} under the frozen P0 engineering thresholds."
    )
    behavior = ["refresh_current_state", "do_not_infer_diagnosis"]
    if event_type in {"input_stale", "input_offline"}:
        behavior.insert(1, "do_not_use_old_physiology")
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "event_revision": event_revision,
        "event_type": event_type,
        "wearer_id": state["wearer_id"],
        "source_instance_id": state["source_instance_id"],
        "data_source": state["data_source"],
        "status": status,
        "severity": "error" if event_type == "input_offline" else "warning",
        "opened_at": opened_at,
        "updated_at": updated_at,
        "resolved_at": resolved_at,
        "state_revision": state_revision,
        "evidence": {
            "lead_off": signal["lead_off"],
            "adc_clipping_ratio_10s": signal["adc_clipping_ratio_10s"],
            "last_ecg_packet_age_ms": device["last_ecg_packet_age_ms"],
            "last_transport_packet_age_ms": device[
                "last_transport_packet_age_ms"
            ],
            "quality_level": signal["quality_level"],
            "detail": detail,
        },
        "recommended_consumer_behavior": behavior,
        "test_mode": state["test_mode"],
    }


def _bridge_fields(
    event: dict[str, object],
    *,
    transition: str,
) -> dict[str, object]:
    event_type = str(event["event_type"])
    namespaced = {
        "lead_off": "signal.lead_off",
        "adc_clipping": "signal.adc_clipping",
        "input_stale": "input.stale",
        "input_offline": "input.offline",
    }.get(event_type, event_type)
    if "summary" in event:
        summary = str(event["summary"])
    else:
        descriptions = {
            "signal.lead_off": "ECG electrodes are not providing a connected signal.",
            "signal.adc_clipping": "The recent ECG window is dominated by ADC clipping.",
            "input.stale": "Recent ECG input has become stale.",
            "input.offline": "ECG input is currently offline.",
        }
        summary = descriptions.get(namespaced, f"Health event {namespaced} changed state.")
        if transition == "resolved":
            summary = f"Resolved: {summary}"
    severity = str(event.get("bridge_severity") or event.get("severity") or "warning")
    if severity == "error":
        severity = "critical"
    priority = str(
        event.get("priority")
        or ("urgent" if namespaced == "input.offline" else "normal")
    )
    evidence = deepcopy(event.get("evidence") or {})
    capabilities = list(event.get("recommended_capabilities") or [])
    return {
        "event_type": namespaced,
        "severity": severity,
        "priority": priority,
        "summary": summary,
        "evidence": evidence,
        "recommended_capabilities": capabilities,
    }


def _rule_evidence(
    rule: HealthAlertRule,
    metrics: dict[str, float | None],
    signal: dict[str, object],
    valid_nn_count: int,
) -> dict[str, object]:
    return {
        "rule_id": rule.rule_id,
        "heart_rate_bpm": metrics["heart_rate_bpm"],
        "hrv_rmssd_ms": metrics["hrv_rmssd_ms"],
        "motion_score_30s": metrics["motion_score"],
        "signal_quality": metrics["signal_quality"],
        "quality_level": signal["quality_level"],
        "lead_off": signal["lead_off"],
        "adc_clipping_ratio_10s": signal["adc_clipping_ratio_10s"],
        "valid_nn_count": valid_nn_count,
        "conditions": [
            {
                "metric": condition.metric,
                "operator": condition.operator,
                "threshold": condition.threshold,
            }
            for condition in rule.conditions
        ],
        "required_duration_s": rule.for_s,
        "recommended_window_s": rule.recommended_window_s,
    }


def _sleep_record_from_row(row: sqlite3.Row) -> dict[str, object]:
    recording_start_time = row["recording_start_time"]
    return {
        "sleep_record_id": str(row["sleep_record_id"]),
        "wearer_id": str(row["wearer_id"]),
        "source_type": str(row["source_type"]),
        "source_session_id": row["source_session_id"],
        "source_sha256": str(row["source_sha256"]),
        "display_name": str(row["display_name"]),
        "source_name": str(row["source_name"]),
        "lead_name": str(row["lead_name"]),
        "sample_rate_hz": float(row["sample_rate_hz"]),
        "sample_count": int(row["sample_count"]),
        "duration_s": float(row["duration_s"]),
        "recording_start_time": recording_start_time,
        "recording_end_time": _sleep_recording_end_time(
            recording_start_time,
            float(row["duration_s"]),
        ),
        "demographics": json.loads(str(row["demographics_json"])),
        "provenance": json.loads(str(row["provenance_json"])),
        "imported_at": str(row["imported_at"]),
    }


def _sleep_recording_end_time(
    recording_start_time: object,
    duration_s: float,
) -> str | None:
    if not isinstance(recording_start_time, str) or not recording_start_time.strip():
        return None
    value = recording_start_time.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    result = (parsed + timedelta(seconds=duration_s)).isoformat(timespec="seconds")
    return result.replace("+00:00", "Z") if value.endswith("Z") else result


def _sleep_epoch_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "epoch_index": int(row["epoch_index"]),
        "start_offset_s": int(row["start_offset_s"]),
        "stage": str(row["stage"]),
        "confidence": float(row["confidence"]),
        "probabilities": json.loads(str(row["probabilities_json"])),
        "reference_stage": row["reference_stage"],
    }


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _elapsed_seconds(now: datetime, since: str) -> float:
    return max(0.0, (now - _parse_timestamp(since)).total_seconds())


def _timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _monotonic_age_ms(now_ns: int, evidence_ns: int | None) -> int | None:
    if evidence_ns is None:
        return None
    if now_ns < evidence_ns:
        return 10_001
    return max(0, (now_ns - evidence_ns) // 1_000_000)


def _effective_freshness(stored: str | None, age_ms: int | None) -> str:
    if age_ms is None:
        return "unavailable"
    age_freshness = (
        "fresh" if age_ms <= 2_000 else "stale" if age_ms <= 10_000 else "offline"
    )
    ranks = {"fresh": 0, "stale": 1, "offline": 2}
    if stored not in ranks:
        return age_freshness
    return max((stored, age_freshness), key=ranks.__getitem__)
