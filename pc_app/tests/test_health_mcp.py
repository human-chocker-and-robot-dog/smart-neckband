from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
import sqlite3
import sys

import jsonschema
import pytest

from smart_neckband.health_contract import load_health_contract
from smart_neckband.health_mcp import (
    HealthToolService,
    SlidingWindowRateLimiter,
    create_mcp_server,
)
from smart_neckband.health_state import BuiltHealthState
from smart_neckband.health_store import HealthStore


NOW_NS = 20_000_000_000


def golden_state() -> dict:
    return deepcopy(load_health_contract()["x-golden"]["wearer_state_live"])


def commit_document(
    store: HealthStore,
    document: dict,
    *,
    now_ns: int = NOW_NS,
) -> dict:
    built = BuiltHealthState(
        document=document,
        committed_monotonic_ns=now_ns,
        ecg_received_monotonic_ns=now_ns - int(document["age_ms"]) * 1_000_000,
        transport_received_monotonic_ns=now_ns
        - int(document["device"]["last_transport_packet_age_ms"]) * 1_000_000,
        status_evidence_key="status-1",
        clipping_window_full=True,
    )
    return store.commit_state(built)


def service(store: HealthStore, now_ns: int = NOW_NS) -> HealthToolService:
    return HealthToolService(
        store=store,
        configured_wearer_ids={"xwen"},
        monotonic_ns=lambda: now_ns,
        utc_now=lambda: "2026-07-24T00:00:00.000Z",
    )


def test_service_rejects_invalid_configured_wearer_id(tmp_path) -> None:
    with pytest.raises(ValueError, match="wearer_id must match"):
        HealthToolService(
            store=HealthStore(tmp_path / "health.sqlite3"),
            configured_wearer_ids={"contains space"},
        )


def validate_tool_result(
    tool_service: HealthToolService,
    tool_name: str,
    result,
) -> None:
    jsonschema.Draft202012Validator(
        tool_service.tools[tool_name]["outputSchema"]
    ).validate(result.envelope)


def test_current_state_success_matches_output_schema_and_text_semantics(
    tmp_path,
) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    commit_document(store, golden_state())
    tool_service = service(store)

    result = tool_service.call(
        "health.get_current_state",
        {"wearer_id": "xwen", "max_age_ms": 2_000},
    )

    assert not result.is_error
    state = result.envelope["data"]["state"]
    assert result.envelope["meta"]["age_ms"] == state["age_ms"]
    assert state["age_ms"] == state["device"]["last_ecg_packet_age_ms"]
    validate_tool_result(tool_service, "health.get_current_state", result)


def test_transport_fresh_but_ecg_offline_returns_state_offline(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    document = golden_state()
    document["age_ms"] = 10_001
    document["freshness"] = "offline"
    document["device"]["last_ecg_packet_age_ms"] = 10_001
    document["device"]["last_transport_packet_age_ms"] = 10
    document["device"]["status"] = "degraded"
    for metric in document["heart"].values():
        if isinstance(metric, dict) and "valid" in metric:
            metric.update(
                {
                    "value": None,
                    "valid": False,
                    "observed_at": None,
                    "age_ms": None,
                    "unavailable_reason": "stale_data",
                }
            )
    commit_document(store, document)
    tool_service = service(store)

    result = tool_service.call(
        "health.get_current_state",
        {"wearer_id": "xwen"},
    )

    assert result.is_error
    assert result.envelope["error"]["code"] == "STATE_OFFLINE"
    assert result.envelope["meta"]["data_source"] == "live"
    assert result.envelope["meta"]["age_ms"] == 10_001
    validate_tool_result(tool_service, "health.get_current_state", result)


def test_monotonic_clock_reset_never_revives_persisted_state(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    commit_document(store, golden_state(), now_ns=NOW_NS)
    tool_service = service(store, now_ns=1_000_000_000)

    state = tool_service.call(
        "health.get_current_state",
        {"wearer_id": "xwen"},
    )
    device = tool_service.call(
        "health.get_device_status",
        {"wearer_id": "xwen"},
    )

    assert state.is_error
    assert state.envelope["error"]["code"] == "DEVICE_OFFLINE"
    assert state.envelope["meta"]["age_ms"] == 10_001
    assert device.envelope["data"]["device"]["status"] == "offline"
    assert device.envelope["meta"]["age_ms"] == 10_001


def test_invalid_argument_and_unknown_wearer_use_domain_failures(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    tool_service = service(store)

    invalid = tool_service.call(
        "health.get_current_state",
        {"wearer_id": "xwen", "max_age_ms": 99},
    )
    missing = tool_service.call(
        "health.get_device_status",
        {"wearer_id": "other"},
    )

    assert invalid.envelope["error"]["code"] == "INVALID_ARGUMENT"
    assert invalid.envelope["meta"]["data_source"] is None
    assert missing.envelope["error"]["code"] == "WEARER_NOT_FOUND"
    validate_tool_result(tool_service, "health.get_current_state", invalid)
    validate_tool_result(tool_service, "health.get_device_status", missing)


def test_event_tools_and_device_status_match_contract(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    document = golden_state()
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
    committed = commit_document(store, document)
    event_id = committed["active_events"][0]["event_id"]
    tool_service = service(store)

    details = tool_service.call(
        "health.get_event_details",
        {"event_id": event_id},
    )
    recent = tool_service.call(
        "health.get_recent_events",
        {"wearer_id": "xwen", "limit": 20},
    )
    device = tool_service.call(
        "health.get_device_status",
        {"wearer_id": "xwen"},
    )

    assert details.envelope["data"]["event"]["event_id"] == event_id
    assert recent.envelope["data"]["events"][0]["event_id"] == event_id
    assert device.envelope["data"]["device"]["status"] in {
        "ok",
        "degraded",
        "stale",
        "offline",
    }
    validate_tool_result(tool_service, "health.get_event_details", details)
    validate_tool_result(tool_service, "health.get_recent_events", recent)
    validate_tool_result(tool_service, "health.get_device_status", device)


def test_event_details_hides_events_for_unconfigured_wearers(tmp_path) -> None:
    db_path = tmp_path / "health.sqlite3"
    store = HealthStore(db_path)
    document = golden_state()
    document["wearer_id"] = "other"
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
    committed = commit_document(store, document)
    event_id = committed["active_events"][0]["event_id"]
    tool_service = service(store)

    result = tool_service.call(
        "health.get_event_details",
        {"event_id": event_id},
    )

    assert result.is_error
    assert result.envelope["error"]["code"] == "EVENT_NOT_FOUND"
    with sqlite3.connect(db_path) as connection:
        audit_wearer = connection.execute(
            """
            SELECT wearer_id FROM health_mcp_audit
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()[0]
    assert audit_wearer == "other"


def test_tools_list_is_exact_and_unknown_tool_is_json_rpc_error(tmp_path) -> None:
    from mcp import types
    from mcp.shared.exceptions import McpError

    tool_service = service(HealthStore(tmp_path / "health.sqlite3"))
    server = create_mcp_server(tool_service)
    list_handler = server.request_handlers[types.ListToolsRequest]
    listed = asyncio.run(list_handler(types.ListToolsRequest()))
    tools = listed.root.tools

    assert [tool.name for tool in tools] == [
        "health.get_current_state",
        "health.get_event_details",
        "health.get_recent_events",
        "health.get_device_status",
    ]
    assert all(tool.outputSchema is not None for tool in tools)
    assert all(tool.annotations.readOnlyHint for tool in tools)

    call_handler = server.request_handlers[types.CallToolRequest]
    with pytest.raises(McpError) as error:
        asyncio.run(
            call_handler(
                types.CallToolRequest(
                    params=types.CallToolRequestParams(
                        name="health.unknown",
                        arguments={},
                    )
                )
            )
        )
    assert error.value.error.code == types.INVALID_PARAMS
    assert error.value.error.message == "Unknown tool: health.unknown"


def test_rate_limiter_returns_next_allowed_delay() -> None:
    now = [1_000_000_000]
    limiter = SlidingWindowRateLimiter(monotonic_ns=lambda: now[0])

    for _ in range(60):
        assert limiter.check("health.get_device_status", "xwen") is None
    assert limiter.check("health.get_device_status", "xwen") == 60_000
    now[0] += 60_000_000_001
    assert limiter.check("health.get_device_status", "xwen") is None


def test_official_client_can_initialize_list_and_call_stdio_server(tmp_path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    db_path = tmp_path / "health.sqlite3"
    store = HealthStore(db_path)
    commit_document(store, golden_state(), now_ns=1_000_000_000)
    environment = os.environ.copy()
    environment["SMART_COLLAR_WEARER_ID"] = "xwen"
    environment["SMART_COLLAR_HEALTH_DB_PATH"] = str(db_path)

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "smart_neckband.health_mcp",
                "--transport",
                "stdio",
            ],
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.protocolVersion == "2025-11-25"
                listed = await session.list_tools()
                assert [tool.name for tool in listed.tools] == [
                    "health.get_current_state",
                    "health.get_event_details",
                    "health.get_recent_events",
                    "health.get_device_status",
                ]
                result = await session.call_tool(
                    "health.get_device_status",
                    {"wearer_id": "xwen"},
                )
                assert result.isError is False
                assert len(result.content) == 1
                assert json.loads(result.content[0].text) == result.structuredContent

    asyncio.run(exercise())


def test_real_stdio_unknown_and_malformed_calls_match_frozen_wire(
    tmp_path,
) -> None:
    contract = load_health_contract()
    environment = os.environ.copy()
    environment["SMART_COLLAR_WEARER_ID"] = "xwen"
    environment["SMART_COLLAR_HEALTH_DB_PATH"] = str(
        tmp_path / "health.sqlite3"
    )

    async def exercise() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "smart_neckband.health_mcp",
            "--transport",
            "stdio",
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        assert process.stdout is not None

        async def exchange(message: dict) -> dict:
            process.stdin.write(
                json.dumps(message, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
            await process.stdin.drain()
            line = await asyncio.wait_for(
                process.stdout.readline(),
                timeout=10,
            )
            return json.loads(line)

        initialized = await exchange(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "wire-test", "version": "1"},
                },
            }
        )
        assert initialized["id"] == 1
        process.stdin.write(
            b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        )
        await process.stdin.drain()

        unknown = await exchange(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "health.unknown",
                    "arguments": {},
                },
            }
        )
        malformed = await exchange(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"arguments": {}},
            }
        )
        assert unknown == contract["x-golden"]["mcp_unknown_tool_error"]
        assert malformed == contract["x-golden"]["mcp_malformed_call_error"]

        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=10)

    asyncio.run(exercise())
