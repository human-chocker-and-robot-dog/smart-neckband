from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any


BRIDGE_SCHEMA_VERSION = "0.3.0"


def contract_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "specs"
        / "health-event-bridge-v0.3.contract.json"
    )


def load_event_contract() -> dict[str, Any]:
    return json.loads(contract_path().read_text(encoding="utf-8"))


def definition_schema(name: str) -> dict[str, Any]:
    contract = load_event_contract()
    return {
        "$schema": contract["$schema"],
        "$ref": f"#/$defs/{name}",
        "$defs": deepcopy(contract["$defs"]),
    }
