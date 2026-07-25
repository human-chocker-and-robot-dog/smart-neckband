from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import statistics
import sys
import socket

import jsonschema
import pytest

from smart_neckband.health_mcp import (
    HealthToolService,
    SlidingWindowRateLimiter,
    _validate_http_path,
    create_mcp_server,
)
from smart_neckband.health_store import HealthStore


NOW = datetime(2026, 7, 25, 12, 0, 30, tzinfo=timezone.utc)
SOURCE_ID = "ef132c67-a98f-474a-a673-4ab6ea784790"


def utc_text(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def seed_recent_metrics(
    store: HealthStore,
    *,
    now: datetime = NOW,
) -> None:
    with sqlite3.connect(store.path) as connection:
        for index in range(31):
            observed = utc_text(now - timedelta(seconds=30 - index))
            connection.execute(
                """
                INSERT INTO health_metric_samples(
                    wearer_id, state_revision, source_instance_id, observed_at,
                    data_source, test_mode, heart_rate_bpm, signal_quality,
                    lead_off, adc_clipping_ratio, motion_score,
                    still_ratio_percent, motion_level, motion_coverage_ratio,
                    imu_online, created_at
                ) VALUES(?, ?, ?, ?, 'live', 0, ?, ?, 0, 0, ?, ?, ?, 1, 1, ?)
                """,
                (
                    "xwen",
                    index + 1,
                    SOURCE_ID,
                    observed,
                    80.0 + (index % 5),
                    0.9,
                    10.0 + index,
                    90.0 - index,
                    "light" if index < 25 else "moderate",
                    observed,
                ),
            )
        rr_values = (800.0, 810.0, 790.0, 805.0, 795.0, 820.0, 800.0, 810.0)
        for index, rr_ms in enumerate(rr_values):
            observed = utc_text(now - timedelta(seconds=14 - index * 2))
            connection.execute(
                """
                INSERT INTO health_rr_intervals(
                    wearer_id, source_instance_id, end_sample_index,
                    observed_at, rr_ms, created_at
                ) VALUES('xwen', ?, ?, ?, ?, ?)
                """,
                (SOURCE_ID, 10_000 + index, observed, rr_ms, observed),
            )


def service(store: HealthStore) -> HealthToolService:
    return HealthToolService(
        store=store,
        configured_wearer_ids={"xwen"},
        monotonic_ns=lambda: 1_000_000_000,
        utc_now=lambda: utc_text(NOW),
    )


def validate_result(tool_service: HealthToolService, tool_name: str, result) -> None:
    jsonschema.Draft202012Validator(
        tool_service.tools[tool_name]["outputSchema"]
    ).validate(result.envelope)


def test_service_requires_exactly_one_valid_wearer(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    with pytest.raises(ValueError, match="wearer_id must match"):
        HealthToolService(store=store, configured_wearer_ids={"contains space"})
    with pytest.raises(ValueError, match="exactly one"):
        HealthToolService(store=store, configured_wearer_ids=set())
    with pytest.raises(ValueError, match="exactly one"):
        HealthToolService(store=store, configured_wearer_ids={"xwen", "other"})


def test_tools_list_is_exactly_three(tmp_path) -> None:
    from mcp import types

    tool_service = service(HealthStore(tmp_path / "health.sqlite3"))
    server = create_mcp_server(tool_service)
    listed = asyncio.run(
        server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
    )

    assert [tool.name for tool in listed.root.tools] == [
        "health.get_heart_rate",
        "health.get_hrv",
        "health.get_imu_state",
    ]
    assert all(tool.annotations.readOnlyHint for tool in listed.root.tools)
    assert all(tool.outputSchema is not None for tool in listed.root.tools)


def test_heart_rate_returns_recent_summary_and_bounded_series(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    seed_recent_metrics(store)
    tool_service = service(store)

    result = tool_service.call("health.get_heart_rate", {"window_s": 30})

    assert not result.is_error
    data = result.envelope["data"]
    assert data["valid"]
    assert data["latest_bpm"] == 80.0
    assert data["min_bpm"] == 80.0
    assert data["max_bpm"] == 84.0
    assert data["coverage_ratio"] == 1.0
    assert len(data["series"]) == 31
    assert result.envelope["meta"]["wearer_id"] == "xwen"
    validate_result(tool_service, "health.get_heart_rate", result)


def test_hrv_uses_time_domain_nn_metrics_and_quality_gate(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    seed_recent_metrics(store)
    tool_service = service(store)

    result = tool_service.call("health.get_hrv", {})

    data = result.envelope["data"]
    assert data["valid"]
    assert data["valid_nn_count"] == 8
    assert data["estimate_type"] == "ultra_short_time_domain"
    assert data["mean_nn_ms"] == round(statistics.fmean((800, 810, 790, 805, 795, 820, 800, 810)), 3)
    assert data["rmssd_ms"] is not None
    assert data["sdnn_ms"] is not None
    validate_result(tool_service, "health.get_hrv", result)


def test_imu_state_returns_latest_motion_score_and_trend(tmp_path) -> None:
    store = HealthStore(tmp_path / "health.sqlite3")
    seed_recent_metrics(store)
    tool_service = service(store)

    result = tool_service.call("health.get_imu_state", {"window_s": 30})

    data = result.envelope["data"]
    assert data["valid"]
    assert data["motion_score"] == 40.0
    assert data["still_ratio_percent"] == 60.0
    assert data["level"] == "moderate"
    assert data["coverage_ratio"] == 1.0
    assert len(data["series"]) == 31
    validate_result(tool_service, "health.get_imu_state", result)


def test_empty_history_is_explicitly_unavailable_not_fabricated(tmp_path) -> None:
    tool_service = service(HealthStore(tmp_path / "health.sqlite3"))

    for tool_name in tool_service.tools:
        result = tool_service.call(tool_name, {})
        assert not result.is_error
        assert result.envelope["data"]["valid"] is False
        assert result.envelope["data"]["unavailable_reason"] is not None
        assert result.envelope["meta"]["latest_observed_at"] is None
        validate_result(tool_service, tool_name, result)


def test_invalid_window_uses_contract_failure(tmp_path) -> None:
    tool_service = service(HealthStore(tmp_path / "health.sqlite3"))

    result = tool_service.call("health.get_heart_rate", {"window_s": 9})

    assert result.is_error
    assert result.envelope["error"]["code"] == "INVALID_ARGUMENT"
    validate_result(tool_service, "health.get_heart_rate", result)


def test_rate_limiter_matches_product_tool_limits() -> None:
    now = [1_000_000_000]
    limiter = SlidingWindowRateLimiter(monotonic_ns=lambda: now[0])
    for _ in range(60):
        assert limiter.check("health.get_hrv", "xwen") is None
    assert limiter.check("health.get_hrv", "xwen") == 60_000
    now[0] += 60_000_000_001
    assert limiter.check("health.get_hrv", "xwen") is None


def test_http_path_configuration() -> None:
    assert _validate_http_path("/mcp") == "/mcp"
    with pytest.raises(ValueError):
        _validate_http_path("mcp")


def test_official_client_can_list_and_call_stdio_server(tmp_path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    db_path = tmp_path / "health.sqlite3"
    store = HealthStore(db_path)
    seed_recent_metrics(store, now=datetime.now(timezone.utc))
    environment = os.environ.copy()
    environment["SMART_COLLAR_WEARER_ID"] = "xwen"
    environment["SMART_COLLAR_HEALTH_DB_PATH"] = str(db_path)
    environment["PYTHONPATH"] = str(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "src"
    )

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "smart_neckband.health_mcp", "--transport", "stdio"],
            env=environment,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert [tool.name for tool in listed.tools] == [
                    "health.get_heart_rate",
                    "health.get_hrv",
                    "health.get_imu_state",
                ]
                result = await session.call_tool(
                    "health.get_heart_rate",
                    {"window_s": 30},
                )
                assert result.isError is False
                assert json.loads(result.content[0].text) == result.structuredContent

    asyncio.run(exercise())


def test_official_client_can_call_trusted_lan_streamable_http(tmp_path) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    db_path = tmp_path / "health.sqlite3"
    store = HealthStore(db_path)
    seed_recent_metrics(store, now=datetime.now(timezone.utc))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(
        __import__("pathlib").Path(__file__).resolve().parents[1] / "src"
    )

    async def exercise() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "smart_neckband.health_mcp",
            "--transport",
            "streamable-http",
            "--db",
            str(db_path),
            "--wearer-id",
            "xwen",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--path",
            "/mcp",
            env=environment,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            for _ in range(100):
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    del reader
                    break
                except OSError:
                    await asyncio.sleep(0.05)
            else:
                raise AssertionError("Streamable HTTP server did not start")

            url = f"http://127.0.0.1:{port}/mcp"
            async with streamable_http_client(url) as streams:
                read_stream, write_stream, _session_id = streams
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    assert [tool.name for tool in listed.tools] == [
                        "health.get_heart_rate",
                        "health.get_hrv",
                        "health.get_imu_state",
                    ]
                    result = await session.call_tool(
                        "health.get_imu_state",
                        {"window_s": 30},
                    )
                    assert result.isError is False
                    payload = result.structuredContent
                    assert json.loads(result.content[0].text) == payload
                    assert payload["meta"]["wearer_id"] == "xwen"
                    assert payload["meta"]["window_s"] == 30
                    assert isinstance(payload["data"]["valid"], bool)
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            assert process.returncode is not None

    asyncio.run(exercise())
