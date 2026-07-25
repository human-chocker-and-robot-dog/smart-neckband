from __future__ import annotations

import jsonschema

from smart_neckband.health_mcp_contract import (
    MCP_SCHEMA_VERSION,
    load_mcp_contract,
    mcp_tool_contracts,
)


def test_v04_contract_exposes_exact_four_tools() -> None:
    contract = load_mcp_contract()
    jsonschema.Draft202012Validator.check_schema(contract)
    assert contract["x-contract"]["contract_version"] == MCP_SCHEMA_VERSION
    assert contract["x-contract"]["business_tool_count"] == 4
    assert [tool["name"] for tool in mcp_tool_contracts()] == [
        "health.get_heart_rate",
        "health.get_hrv",
        "health.get_imu_state",
        "health.get_sleep_report",
    ]


def test_v04_tool_schemas_are_closed_and_read_only() -> None:
    for tool in mcp_tool_contracts():
        jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
        jsonschema.Draft202012Validator.check_schema(tool["outputSchema"])
        assert tool["inputSchema"]["$ref"] in {
            "#/$defs/WindowInput",
            "#/$defs/SleepReportInput",
        }
        assert tool["annotations"] == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
