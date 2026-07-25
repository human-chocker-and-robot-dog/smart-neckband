from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import TextIOWrapper
import json
import logging
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Callable
from uuid import uuid4
import hmac

import jsonschema

from .health_contract import (
    SCHEMA_VERSION,
    compact_json,
    validate_wearer_id,
)
from .health_mcp_contract import MCP_SCHEMA_VERSION, mcp_tool_contracts
from .health_store import HealthStore
from .source_coordinator import utc_now_millisecond_z


LOGGER = logging.getLogger(__name__)
TOOL_LIMITS = {
    "health.get_heart_rate": 120,
    "health.get_hrv": 60,
    "health.get_imu_state": 120,
}


@dataclass(frozen=True, slots=True)
class HealthToolResult:
    envelope: dict[str, object]
    is_error: bool


class SlidingWindowRateLimiter:
    def __init__(self, monotonic_ns: Callable[[], int] = time.monotonic_ns) -> None:
        self.monotonic_ns = monotonic_ns
        self._calls: dict[tuple[str, str], deque[int]] = defaultdict(deque)

    def check(self, tool_name: str, wearer_id: str) -> int | None:
        now = self.monotonic_ns()
        window_start = now - 60_000_000_000
        calls = self._calls[(tool_name, wearer_id)]
        while calls and calls[0] <= window_start:
            calls.popleft()
        limit = TOOL_LIMITS[tool_name]
        if len(calls) >= limit:
            return max(1, (calls[0] + 60_000_000_000 - now) // 1_000_000)
        calls.append(now)
        return None


class HealthToolService:
    def __init__(
        self,
        *,
        store: HealthStore,
        configured_wearer_ids: set[str],
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        utc_now: Callable[[], str] = utc_now_millisecond_z,
    ) -> None:
        self.store = store
        self.configured_wearer_ids = {
            validate_wearer_id(value) for value in configured_wearer_ids
        }
        if len(self.configured_wearer_ids) != 1:
            raise ValueError("Health MCP V0.3 requires exactly one configured wearer")
        self.wearer_id = next(iter(self.configured_wearer_ids))
        self.monotonic_ns = monotonic_ns
        self.utc_now = utc_now
        self.tools = {item["name"]: item for item in mcp_tool_contracts()}
        self.rate_limiter = SlidingWindowRateLimiter(monotonic_ns)

    def call(
        self,
        tool_name: str,
        arguments: dict[str, object],
    ) -> HealthToolResult:
        if tool_name not in self.tools:
            raise KeyError(tool_name)
        started = self.monotonic_ns()
        trace_id = str(uuid4())
        wearer_id = self.wearer_id
        try:
            try:
                jsonschema.Draft202012Validator(
                    self.tools[tool_name]["inputSchema"]
                ).validate(arguments)
            except jsonschema.ValidationError as exc:
                result = self._metric_failure(
                    code="INVALID_ARGUMENT",
                    message="Tool arguments do not match the Health MCP contract.",
                    retryable=False,
                    retry_after_ms=None,
                    details={
                        "field": ".".join(str(part) for part in exc.absolute_path)
                        or "arguments",
                        "reason": exc.message[:256],
                    },
                    trace_id=trace_id,
                    window_s=30,
                )
            else:
                retry_after = self.rate_limiter.check(tool_name, "__process__")
                if retry_after is not None:
                    result = self._metric_failure(
                        code="RATE_LIMITED",
                        message="Health MCP rate limit exceeded.",
                        retryable=True,
                        retry_after_ms=retry_after,
                        details={},
                        trace_id=trace_id,
                        window_s=int(arguments.get("window_s", 30)),
                    )
                elif tool_name == "health.get_heart_rate":
                    result = self._get_heart_rate(arguments, trace_id)
                elif tool_name == "health.get_hrv":
                    result = self._get_hrv(arguments, trace_id)
                else:
                    result = self._get_imu_state(arguments, trace_id)
        except Exception:
            LOGGER.exception("Health MCP tool failed; trace_id=%s", trace_id)
            result = self._metric_failure(
                code="INTERNAL_ERROR",
                message="The Health state store could not complete the request.",
                retryable=True,
                retry_after_ms=1_000,
                details={},
                trace_id=trace_id,
                window_s=int(arguments.get("window_s", 30)) if isinstance(arguments, dict) else 30,
            )

        latency_ms = max(0, (self.monotonic_ns() - started) // 1_000_000)
        try:
            self.store.record_mcp_audit(
                trace_id=trace_id,
                tool_name=tool_name,
                wearer_id=wearer_id,
                outcome="error" if result.is_error else "success",
                error_code=(
                    str(result.envelope["error"]["code"])
                    if result.is_error
                    else None
                ),
                latency_ms=latency_ms,
            )
        except Exception:
            LOGGER.exception("Health MCP audit write failed; trace_id=%s", trace_id)
        return result

    def _get_heart_rate(
        self,
        arguments: dict[str, object],
        trace_id: str,
    ) -> HealthToolResult:
        window_s, rows, context = self._window(arguments)
        valid_rows = [row for row in rows if row["heart_rate_bpm"] is not None]
        values = [float(row["heart_rate_bpm"]) for row in valid_rows]
        quality_values = [
            float(row["signal_quality"])
            for row in rows
            if row["signal_quality"] is not None
        ]
        lead_off_seen = any(row["lead_off"] == 1 for row in rows)
        clipping_seen = any(
            row["adc_clipping_ratio"] is not None
            and float(row["adc_clipping_ratio"]) >= 0.8
            for row in rows
        )
        coverage = _window_coverage(rows, window_s)
        valid = bool(values) and coverage >= 0.5 and not lead_off_seen and not clipping_seen
        unavailable_reason = (
            None
            if valid
            else "lead_off"
            if lead_off_seen
            else "adc_clipping"
            if clipping_seen
            else "insufficient_window"
        )
        data = {
            "valid": valid,
            "latest_bpm": values[-1] if values else None,
            "mean_bpm": round(statistics.fmean(values), 3) if values else None,
            "min_bpm": min(values) if values else None,
            "max_bpm": max(values) if values else None,
            "valid_sample_count": len(values),
            "total_sample_count": len(rows),
            "coverage_ratio": coverage,
            "signal_quality_mean": (
                round(statistics.fmean(quality_values), 6)
                if quality_values
                else None
            ),
            "lead_off_seen": lead_off_seen,
            "clipping_seen": clipping_seen,
            "series": _one_per_second(valid_rows, "heart_rate_bpm", "bpm"),
            "unavailable_reason": unavailable_reason,
        }
        return self._metric_success(data, trace_id, window_s, context)

    def _get_hrv(
        self,
        arguments: dict[str, object],
        trace_id: str,
    ) -> HealthToolResult:
        window_s, rows, context = self._window(arguments)
        rr_rows = self.store.list_rr_intervals(
            wearer_id=self.wearer_id,
            since_utc=context["since_utc"],
        )
        rr_values = [float(row["rr_ms"]) for row in rr_rows]
        differences = [
            rr_values[index] - rr_values[index - 1]
            for index in range(1, len(rr_values))
        ]
        quality_values = [
            float(row["signal_quality"])
            for row in rows
            if row["signal_quality"] is not None
        ]
        quality_mean = statistics.fmean(quality_values) if quality_values else None
        lead_off_seen = any(row["lead_off"] == 1 for row in rows)
        clipping_seen = any(
            row["adc_clipping_ratio"] is not None
            and float(row["adc_clipping_ratio"]) >= 0.8
            for row in rows
        )
        valid = (
            len(rr_values) >= 5
            and len(differences) >= 4
            and not lead_off_seen
            and not clipping_seen
            and quality_mean is not None
            and quality_mean >= 0.5
        )
        unavailable_reason = (
            None
            if valid
            else "lead_off"
            if lead_off_seen
            else "adc_clipping"
            if clipping_seen
            else "quality_below_threshold"
            if quality_mean is not None and quality_mean < 0.5
            else "insufficient_nn_intervals"
        )
        data = {
            "valid": valid,
            "estimate_type": "ultra_short_time_domain",
            "rmssd_ms": (
                round(math.sqrt(statistics.fmean(value * value for value in differences)), 3)
                if differences
                else None
            ),
            "sdnn_ms": round(statistics.stdev(rr_values), 3) if len(rr_values) >= 2 else None,
            "pnn50_percent": (
                round(100.0 * sum(abs(value) > 50.0 for value in differences) / len(differences), 3)
                if differences
                else None
            ),
            "mean_nn_ms": round(statistics.fmean(rr_values), 3) if rr_values else None,
            "valid_nn_count": len(rr_values),
            "signal_quality_mean": round(quality_mean, 6) if quality_mean is not None else None,
            "lead_off_seen": lead_off_seen,
            "clipping_seen": clipping_seen,
            "unavailable_reason": unavailable_reason,
        }
        return self._metric_success(data, trace_id, window_s, context)

    def _get_imu_state(
        self,
        arguments: dict[str, object],
        trace_id: str,
    ) -> HealthToolResult:
        window_s, rows, context = self._window(arguments)
        motion_rows = [row for row in rows if row["motion_score"] is not None]
        latest = motion_rows[-1] if motion_rows else None
        coverage = _window_coverage(rows, window_s)
        imu_online = (
            None
            if not rows or rows[-1]["imu_online"] is None
            else bool(rows[-1]["imu_online"])
        )
        valid = latest is not None and coverage >= 0.5 and imu_online is not False
        data = {
            "valid": valid,
            "motion_score": float(latest["motion_score"]) if latest is not None else None,
            "still_ratio_percent": (
                float(latest["still_ratio_percent"]) if latest is not None else None
            ),
            "level": str(latest["motion_level"]) if latest is not None else None,
            "coverage_ratio": coverage,
            "imu_online": imu_online,
            "method": "mpu6050_accel_gyro_activity_v1",
            "series": _one_per_second(motion_rows, "motion_score", "score"),
            "unavailable_reason": (
                None
                if valid
                else "imu_offline"
                if imu_online is False
                else "insufficient_window"
            ),
        }
        return self._metric_success(data, trace_id, window_s, context)

    def _window(
        self,
        arguments: dict[str, object],
    ) -> tuple[int, list[dict[str, object]], dict[str, object]]:
        window_s = int(arguments.get("window_s", 30))
        now_text = self.utc_now()
        now = datetime.fromisoformat(now_text.replace("Z", "+00:00"))
        since_utc = (now - timedelta(seconds=window_s)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        rows = self.store.list_metric_samples(
            wearer_id=self.wearer_id,
            since_utc=since_utc,
        )
        latest = rows[-1] if rows else None
        age_ms = (
            max(
                0,
                int(
                    (
                        now
                        - datetime.fromisoformat(
                            str(latest["observed_at"]).replace("Z", "+00:00")
                        )
                    ).total_seconds()
                    * 1000
                ),
            )
            if latest is not None
            else None
        )
        return window_s, rows, {
            "now_text": now_text,
            "since_utc": since_utc,
            "latest": latest,
            "age_ms": age_ms,
        }

    def _metric_success(
        self,
        data: dict[str, object],
        trace_id: str,
        window_s: int,
        context: dict[str, object],
    ) -> HealthToolResult:
        return HealthToolResult(
            envelope={
                "ok": True,
                "data": data,
                "meta": self._metric_meta(trace_id, window_s, context),
                "error": None,
            },
            is_error=False,
        )

    def _metric_failure(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        retry_after_ms: int | None,
        details: dict[str, object],
        trace_id: str,
        window_s: int,
    ) -> HealthToolResult:
        context = {"now_text": self.utc_now(), "latest": None, "age_ms": None}
        return HealthToolResult(
            envelope={
                "ok": False,
                "data": None,
                "meta": self._metric_meta(trace_id, window_s, context),
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "retry_after_ms": retry_after_ms,
                    "details": details,
                },
            },
            is_error=True,
        )

    def _metric_meta(
        self,
        trace_id: str,
        window_s: int,
        context: dict[str, object],
    ) -> dict[str, object]:
        latest = context.get("latest")
        return {
            "schema_version": MCP_SCHEMA_VERSION,
            "generated_at": context["now_text"],
            "wearer_id": self.wearer_id,
            "window_s": window_s,
            "data_source": latest["data_source"] if latest is not None else None,
            "test_mode": bool(latest["test_mode"]) if latest is not None else None,
            "source_instance_id": (
                latest["source_instance_id"] if latest is not None else None
            ),
            "latest_observed_at": latest["observed_at"] if latest is not None else None,
            "age_ms": context.get("age_ms"),
            "trace_id": trace_id,
        }

    def _get_current_state(
        self,
        arguments: dict[str, object],
        trace_id: str,
    ) -> HealthToolResult:
        wearer_id = str(arguments["wearer_id"])
        configured = self._configured(wearer_id, trace_id)
        if configured is not None:
            return configured
        record = self.store.get_state(wearer_id)
        if record is None:
            return self._failure(
                code="STATE_UNAVAILABLE",
                message="No wearer state has been produced from a valid ECG packet.",
                retryable=True,
                retry_after_ms=500,
                details={},
                trace_id=trace_id,
            )
        state = self._refresh_state(record)
        ecg_age = int(state["age_ms"])
        transport_age = state["device"]["last_transport_packet_age_ms"]
        connection = state["device"]["connection_state"]
        if (
            connection in {"DISCONNECTED", "ERROR"}
            or transport_age is None
            or int(transport_age) > 10_000
        ):
            return self._failure(
                code="DEVICE_OFFLINE",
                message="The reader or inbound transport is offline.",
                retryable=True,
                retry_after_ms=1_000,
                details=(
                    {"actual_age_ms": int(transport_age)}
                    if transport_age is not None
                    else {}
                ),
                trace_id=trace_id,
                data_source=str(state["data_source"]),
                age_ms=(
                    int(transport_age) if transport_age is not None else None
                ),
            )
        if ecg_age > 10_000:
            return self._failure(
                code="STATE_OFFLINE",
                message="ECG physiology input has not updated for more than 10 seconds.",
                retryable=True,
                retry_after_ms=1_000,
                details={"actual_age_ms": ecg_age},
                trace_id=trace_id,
                data_source=str(state["data_source"]),
                age_ms=ecg_age,
            )
        max_age = int(arguments.get("max_age_ms", 2_000))
        if ecg_age > max_age:
            return self._failure(
                code="STATE_STALE",
                message="Latest wearer state is older than max_age_ms.",
                retryable=True,
                retry_after_ms=500,
                details={"actual_age_ms": ecg_age, "max_age_ms": max_age},
                trace_id=trace_id,
                data_source=str(state["data_source"]),
                age_ms=ecg_age,
            )
        return self._success(
            data={"state": state},
            trace_id=trace_id,
            data_source=str(state["data_source"]),
            age_ms=ecg_age,
        )

    def _get_event_details(
        self,
        arguments: dict[str, object],
        trace_id: str,
    ) -> HealthToolResult:
        event_id = str(arguments["event_id"])
        event = self.store.get_event(event_id)
        if (
            event is None
            or str(event["wearer_id"]) not in self.configured_wearer_ids
        ):
            return self._failure(
                code="EVENT_NOT_FOUND",
                message="No persisted Health event has that event_id.",
                retryable=False,
                retry_after_ms=None,
                details={"event_id": event_id},
                trace_id=trace_id,
            )
        return self._success(
            data={"event": event},
            trace_id=trace_id,
            data_source=str(event["data_source"]),
            age_ms=None,
        )

    def _get_recent_events(
        self,
        arguments: dict[str, object],
        trace_id: str,
    ) -> HealthToolResult:
        wearer_id = str(arguments["wearer_id"])
        configured = self._configured(wearer_id, trace_id)
        if configured is not None:
            return configured
        try:
            events, next_cursor = self.store.list_events(
                wearer_id=wearer_id,
                event_types=arguments.get("event_types"),
                statuses=arguments.get("statuses"),
                since=arguments.get("since"),
                cursor=arguments.get("cursor"),
                limit=int(arguments.get("limit", 20)),
            )
        except ValueError as exc:
            return self._failure(
                code="INVALID_ARGUMENT",
                message="The recent-events cursor is invalid for these filters.",
                retryable=False,
                retry_after_ms=None,
                details={"field": "cursor", "reason": str(exc)[:256]},
                trace_id=trace_id,
            )
        sources = {str(event["data_source"]) for event in events}
        data_source = next(iter(sources)) if len(sources) == 1 else None
        return self._success(
            data={"events": events, "next_cursor": next_cursor},
            trace_id=trace_id,
            data_source=data_source,
            age_ms=None,
        )

    def _get_device_status(
        self,
        arguments: dict[str, object],
        trace_id: str,
    ) -> HealthToolResult:
        wearer_id = str(arguments["wearer_id"])
        configured = self._configured(wearer_id, trace_id)
        if configured is not None:
            return configured
        record = self.store.get_device_snapshot(wearer_id)
        if record is None:
            return self._failure(
                code="STATE_UNAVAILABLE",
                message="No reader or device snapshot exists for this wearer.",
                retryable=True,
                retry_after_ms=500,
                details={},
                trace_id=trace_id,
            )
        device = deepcopy(record["device"])
        now_ns = self.monotonic_ns()
        committed_ns = int(record["committed_monotonic_ns"])
        clock_reset = now_ns < committed_ns
        elapsed = (
            10_001
            if clock_reset
            else max(0, (now_ns - committed_ns) // 1_000_000)
        )
        transport_age = device["last_transport_packet_age_ms"]
        if transport_age is not None:
            transport_age = (
                10_001
                if clock_reset
                else int(transport_age) + elapsed
            )
            device["last_transport_packet_age_ms"] = transport_age
        ecg_age = device["last_ecg_packet_age_ms"]
        if ecg_age is not None:
            device["last_ecg_packet_age_ms"] = (
                10_001 if clock_reset else int(ecg_age) + elapsed
            )
        self._refresh_device_status(device)
        return self._success(
            data={"device": device},
            trace_id=trace_id,
            data_source=(
                str(record["data_source"])
                if record["data_source"] is not None
                else None
            ),
            age_ms=transport_age,
        )

    def _configured(
        self,
        wearer_id: str,
        trace_id: str,
    ) -> HealthToolResult | None:
        if wearer_id in self.configured_wearer_ids:
            return None
        return self._failure(
            code="WEARER_NOT_FOUND",
            message="wearer_id is not configured on this PC.",
            retryable=False,
            retry_after_ms=None,
            details={},
            trace_id=trace_id,
        )

    def _refresh_state(self, record: dict[str, object]) -> dict[str, object]:
        state = deepcopy(record["state"])
        now_ns = self.monotonic_ns()
        committed_ns = int(record["committed_monotonic_ns"])
        ecg_received_ns = int(record["ecg_received_monotonic_ns"])
        transport_received_ns = int(record["transport_received_monotonic_ns"])
        clock_reset = now_ns < max(
            committed_ns,
            ecg_received_ns,
            transport_received_ns,
        )
        if clock_reset:
            elapsed = ecg_age = transport_age = 10_001
        else:
            elapsed = max(0, (now_ns - committed_ns) // 1_000_000)
            ecg_age = max(0, (now_ns - ecg_received_ns) // 1_000_000)
            transport_age = max(
                0,
                (now_ns - transport_received_ns) // 1_000_000,
            )
        state["age_ms"] = ecg_age
        device = state["device"]
        disconnected = device["connection_state"] in {"DISCONNECTED", "ERROR"}
        state["freshness"] = (
            "offline"
            if disconnected
            else (
                "fresh"
                if ecg_age <= 2_000
                else "stale"
                if ecg_age <= 10_000
                else "offline"
            )
        )
        device["last_ecg_packet_age_ms"] = ecg_age
        device["last_transport_packet_age_ms"] = transport_age
        self._refresh_device_status(device)
        for metric_name in ("heart_rate", "rr_interval"):
            metric = state["heart"][metric_name]
            if metric["age_ms"] is not None:
                metric["age_ms"] = int(metric["age_ms"]) + elapsed
            if metric["valid"] and int(metric["age_ms"]) > 2_000:
                metric["value"] = None
                metric["valid"] = False
                metric["unavailable_reason"] = "stale_data"
        return state

    @staticmethod
    def _refresh_device_status(device: dict[str, object]) -> None:
        connection = device["connection_state"]
        transport_age = device["last_transport_packet_age_ms"]
        ecg_age = device["last_ecg_packet_age_ms"]
        if (
            connection in {"DISCONNECTED", "ERROR"}
            or (transport_age is not None and int(transport_age) > 10_000)
        ):
            device["status"] = "offline"
        elif transport_age is not None and int(transport_age) > 2_000:
            device["status"] = "stale"
        elif ecg_age is None or int(ecg_age) > 2_000:
            device["status"] = "degraded"

    def _success(
        self,
        *,
        data: dict[str, object],
        trace_id: str,
        data_source: str | None,
        age_ms: int | None,
    ) -> HealthToolResult:
        return HealthToolResult(
            envelope={
                "ok": True,
                "data": data,
                "meta": {
                    "schema_version": SCHEMA_VERSION,
                    "generated_at": self.utc_now(),
                    "data_source": data_source,
                    "age_ms": age_ms,
                    "trace_id": trace_id,
                },
                "error": None,
            },
            is_error=False,
        )

    def _failure(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        retry_after_ms: int | None,
        details: dict[str, object],
        trace_id: str,
        data_source: str | None = None,
        age_ms: int | None = None,
    ) -> HealthToolResult:
        return HealthToolResult(
            envelope={
                "ok": False,
                "data": None,
                "meta": {
                    "schema_version": SCHEMA_VERSION,
                    "generated_at": self.utc_now(),
                    "data_source": data_source,
                    "age_ms": age_ms,
                    "trace_id": trace_id,
                },
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": retryable,
                    "retry_after_ms": retry_after_ms,
                    "details": details,
                },
            },
            is_error=True,
        )


def _window_coverage(rows: list[dict[str, object]], window_s: int) -> float:
    if len(rows) < 2:
        return 0.0
    first = datetime.fromisoformat(str(rows[0]["observed_at"]).replace("Z", "+00:00"))
    last = datetime.fromisoformat(str(rows[-1]["observed_at"]).replace("Z", "+00:00"))
    span = max(0.0, (last - first).total_seconds())
    return round(min(1.0, span / max(1, window_s)), 6)


def _one_per_second(
    rows: list[dict[str, object]],
    value_key: str,
    output_key: str,
) -> list[dict[str, object]]:
    buckets: dict[str, dict[str, object]] = {}
    for row in rows:
        value = row[value_key]
        if value is None:
            continue
        observed_at = str(row["observed_at"])
        buckets[observed_at[:19]] = {
            "observed_at": observed_at,
            output_key: round(float(value), 3),
        }
    return list(buckets.values())[-300:]


def create_mcp_server(service: HealthToolService):
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.shared.exceptions import McpError

    server = Server(
        "smart-neckband-health",
        version=MCP_SCHEMA_VERSION,
        instructions=(
            "Read-only engineering health state. Results are not medical diagnoses "
            "and never authorize robot motion."
        ),
    )
    tool_contracts = service.tools

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=item["name"],
                title=item["title"],
                description=item["description"],
                inputSchema=item["inputSchema"],
                outputSchema=item["outputSchema"],
                annotations=types.ToolAnnotations(**item["annotations"]),
            )
            for item in tool_contracts.values()
        ]

    async def call_tool_handler(req: types.CallToolRequest):
        name = req.params.name
        if name not in tool_contracts:
            raise McpError(
                types.ErrorData(
                    code=types.INVALID_PARAMS,
                    message=f"Unknown tool: {name}",
                )
            )
        result = service.call(name, req.params.arguments or {})
        text = compact_json(result.envelope)
        return types.ServerResult(
            types.CallToolResult(
                content=[types.TextContent(type="text", text=text)],
                structuredContent=result.envelope,
                isError=result.is_error,
            )
        )

    server.request_handlers[types.CallToolRequest] = call_tool_handler
    return server


async def _run_stdio(service: HealthToolService) -> None:
    server = create_mcp_server(service)
    async with _health_stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


class _BearerAuthMiddleware:
    def __init__(self, app, *, token: str, protected_path: str) -> None:
        self.app = app
        self.token = token
        self.protected_path = protected_path

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http" and scope.get("path") == self.protected_path:
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(headers.get("authorization", ""), expected):
                body = b'{"error":"unauthorized"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json; charset=utf-8"),
                            (b"www-authenticate", b"Bearer"),
                            (b"content-length", str(len(body)).encode("ascii")),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


class _StreamableHttpASGIApp:
    def __init__(self, manager) -> None:
        self.manager = manager

    async def __call__(self, scope, receive, send) -> None:
        await self.manager.handle_request(scope, receive, send)


def _validate_http_path(value: str) -> str:
    path = value.strip()
    if not path.startswith("/") or path == "/" or path.endswith("/"):
        raise ValueError("Streamable HTTP path must start with / and must not end with /")
    if "?" in path or "#" in path:
        raise ValueError("Streamable HTTP path must not include query or fragment")
    return path


def _validate_bearer_token(value: str | None) -> str:
    if value is None or len(value) < 32 or len(value) > 512:
        raise ValueError("Health MCP bearer token must contain 32..512 characters")
    if any(character.isspace() for character in value):
        raise ValueError("Health MCP bearer token must not contain whitespace")
    return value


async def _run_streamable_http(
    service: HealthToolService,
    *,
    host: str,
    port: int,
    path: str,
    bearer_token: str,
    allowed_hosts: list[str],
) -> None:
    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.routing import Route

    server = create_mcp_server(service)
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[],
        ),
    )

    @asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    application = Starlette(
        routes=[Route(path, endpoint=_StreamableHttpASGIApp(manager))],
        lifespan=lifespan,
    )
    protected = _BearerAuthMiddleware(
        application,
        token=bearer_token,
        protected_path=path,
    )
    config = uvicorn.Config(
        protected,
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    await uvicorn.Server(config).serve()


@asynccontextmanager
async def _health_stdio_server(stdin=None, stdout=None):
    """SDK-compatible stdio transport with the frozen malformed-call response."""

    import anyio
    import anyio.lowlevel
    from mcp import types
    from mcp.shared.message import SessionMessage

    if stdin is None:
        stdin = anyio.wrap_file(
            TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        )
    if stdout is None:
        stdout = anyio.wrap_file(
            TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        )
    read_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_reader = anyio.create_memory_object_stream(0)

    async def stdin_reader() -> None:
        try:
            async with read_writer:
                async for line in stdin:
                    try:
                        raw = json.loads(line)
                        if (
                            isinstance(raw, dict)
                            and raw.get("method") == "tools/call"
                            and isinstance(raw.get("params"), dict)
                            and "name" not in raw["params"]
                            and "id" in raw
                        ):
                            error = types.JSONRPCError(
                                jsonrpc="2.0",
                                id=raw["id"],
                                error=types.ErrorData(
                                    code=types.INVALID_PARAMS,
                                    message=(
                                        "Invalid tools/call params: name is required."
                                    ),
                                ),
                            )
                            await write_stream.send(
                                SessionMessage(types.JSONRPCMessage(error))
                            )
                            continue
                        message = types.JSONRPCMessage.model_validate(raw)
                    except Exception as exc:
                        await read_writer.send(exc)
                        continue
                    await read_writer.send(SessionMessage(message))
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def stdout_writer() -> None:
        try:
            async with write_reader:
                async for session_message in write_reader:
                    serialized = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    )
                    await stdout.write(serialized + "\n")
                    await stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(stdin_reader)
        task_group.start_soon(stdout_writer)
        yield read_stream, write_stream


def _default_db_path() -> Path:
    configured = os.environ.get("SMART_COLLAR_HEALTH_DB_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / "data" / "health" / "health_state.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smart Collar read-only Health MCP")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--db", type=Path, default=_default_db_path())
    parser.add_argument(
        "--wearer-id",
        default=os.environ.get("SMART_COLLAR_WEARER_ID"),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("SMART_COLLAR_HEALTH_MCP_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SMART_COLLAR_HEALTH_MCP_PORT", "8765")),
    )
    parser.add_argument(
        "--path",
        default=os.environ.get("SMART_COLLAR_HEALTH_MCP_PATH", "/mcp"),
    )
    parser.add_argument(
        "--bearer-token",
        default=os.environ.get("SMART_COLLAR_HEALTH_MCP_BEARER_TOKEN"),
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=None,
        help="Accepted HTTP Host header; repeat for multiple RDK-visible names.",
    )
    args = parser.parse_args(argv)
    if not args.wearer_id:
        parser.error(
            "--wearer-id or SMART_COLLAR_WEARER_ID is required; no implicit wearer is used"
        )
    try:
        validate_wearer_id(args.wearer_id)
    except ValueError as exc:
        parser.error(str(exc))

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    store = HealthStore(args.db)
    service = HealthToolService(
        store=store,
        configured_wearer_ids={args.wearer_id},
    )
    if args.transport == "stdio":
        asyncio.run(_run_stdio(service))
    else:
        try:
            path = _validate_http_path(args.path)
            token = _validate_bearer_token(args.bearer_token)
        except ValueError as exc:
            parser.error(str(exc))
        if not 1 <= args.port <= 65_535:
            parser.error("--port must be in 1..65535")
        allowed_hosts = args.allowed_host
        if allowed_hosts is None:
            if args.host not in {"127.0.0.1", "::1", "localhost"}:
                parser.error(
                    "LAN Streamable HTTP requires at least one --allowed-host "
                    "matching the Windows address used by the RDK"
                )
            allowed_hosts = [
                f"127.0.0.1:{args.port}",
                f"localhost:{args.port}",
                f"[::1]:{args.port}",
            ]
        asyncio.run(
            _run_streamable_http(
                service,
                host=args.host,
                port=args.port,
                path=path,
                bearer_token=token,
                allowed_hosts=allowed_hosts,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
