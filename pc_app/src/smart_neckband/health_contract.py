from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = "0.2.0"
MCP_PROTOCOL_VERSION = "2025-11-25"
WEARER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def validate_wearer_id(value: str) -> str:
    if not WEARER_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "wearer_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}"
        )
    return value


def contract_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "specs"
        / "health-mcp-v0.2.contract.json"
    )


def load_health_contract() -> dict[str, Any]:
    return json.loads(contract_path().read_text(encoding="utf-8"))


def schema_with_definitions(reference: dict[str, Any]) -> dict[str, Any]:
    contract = load_health_contract()
    return {
        "$schema": contract["$schema"],
        **deepcopy(reference),
        "$defs": deepcopy(contract["$defs"]),
    }


def definition_schema(name: str) -> dict[str, Any]:
    return schema_with_definitions({"$ref": f"#/$defs/{name}"})


def mcp_tool_contracts() -> tuple[dict[str, Any], ...]:
    contract = load_health_contract()
    tools: list[dict[str, Any]] = []
    for item in contract["x-mcp-tools"]:
        tool = deepcopy(item)
        tool["inputSchema"] = schema_with_definitions(item["inputSchema"])
        tool["outputSchema"] = schema_with_definitions(item["outputSchema"])
        tools.append(tool)
    return tuple(tools)


def compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
