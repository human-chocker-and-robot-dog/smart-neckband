from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


MCP_SCHEMA_VERSION = "0.3.0"


def contract_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "specs"
        / "health-mcp-v0.3.contract.json"
    )


def load_mcp_contract() -> dict[str, Any]:
    return json.loads(contract_path().read_text(encoding="utf-8"))


def schema_with_definitions(reference: dict[str, Any]) -> dict[str, Any]:
    contract = load_mcp_contract()
    return {
        "$schema": contract["$schema"],
        **deepcopy(reference),
        "$defs": deepcopy(contract["$defs"]),
    }


def mcp_tool_contracts() -> tuple[dict[str, Any], ...]:
    contract = load_mcp_contract()
    tools: list[dict[str, Any]] = []
    for item in contract["x-mcp-tools"]:
        tool = deepcopy(item)
        tool["inputSchema"] = schema_with_definitions(item["inputSchema"])
        tool["outputSchema"] = schema_with_definitions(item["outputSchema"])
        tools.append(tool)
    return tuple(tools)
