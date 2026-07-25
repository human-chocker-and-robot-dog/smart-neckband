from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from io import TextIOWrapper
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Callable
from uuid import uuid4

import jsonschema

from .health_contract import (
    SCHEMA_VERSION,
    compact_json,
    mcp_tool_contracts,
    validate_wearer_id,
)
from .health_store import HealthStore
from .source_coordinator import utc_now_millisecond_z


LOGGER = logging.getLogger(__name__)
TOOL_LIMITS = {
    "health.get_current_state": 120,
    "health.get_event_details": 120,
    "health.get_recent_events": 30,
    "health.get_device_status": 60,
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
        wearer_id = (
            str(arguments["wearer_id"])
            if isinstance(arguments, dict) and "wearer_id" in arguments
            else None
        )
        try:
            try:
                jsonschema.Draft202012Validator(
                    self.tools[tool_name]["inputSchema"]
                ).validate(arguments)
            except jsonschema.ValidationError as exc:
                result = self._failure(
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
                )
            else:
                if tool_name == "health.get_event_details":
                    audit_event = self.store.get_event(str(arguments["event_id"]))
                    if audit_event is not None:
                        wearer_id = str(audit_event["wearer_id"])
                assert wearer_id is not None or tool_name == "health.get_event_details"
                retry_after = self.rate_limiter.check(tool_name, "__process__")
                if retry_after is not None:
                    result = self._failure(
                        code="RATE_LIMITED",
                        message="Health MCP rate limit exceeded.",
                        retryable=True,
                        retry_after_ms=retry_after,
                        details={},
                        trace_id=trace_id,
                    )
                elif tool_name == "health.get_current_state":
                    result = self._get_current_state(arguments, trace_id)
                elif tool_name == "health.get_event_details":
                    result = self._get_event_details(arguments, trace_id)
                elif tool_name == "health.get_recent_events":
                    result = self._get_recent_events(arguments, trace_id)
                else:
                    result = self._get_device_status(arguments, trace_id)
        except Exception:
            LOGGER.exception("Health MCP tool failed; trace_id=%s", trace_id)
            result = self._failure(
                code="INTERNAL_ERROR",
                message="The Health state store could not complete the request.",
                retryable=True,
                retry_after_ms=1_000,
                details={},
                trace_id=trace_id,
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


def create_mcp_server(service: HealthToolService):
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.shared.exceptions import McpError

    server = Server(
        "smart-neckband-health",
        version=SCHEMA_VERSION,
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
    parser.add_argument("--transport", choices=("stdio",), default="stdio")
    parser.add_argument("--db", type=Path, default=_default_db_path())
    parser.add_argument(
        "--wearer-id",
        default=os.environ.get("SMART_COLLAR_WEARER_ID"),
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
    asyncio.run(_run_stdio(service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
