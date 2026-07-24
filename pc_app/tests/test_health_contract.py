from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")


CONTRACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "specs"
    / "health-mcp-v0.2.contract.json"
)


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def definition_schema(contract: dict, name: str) -> dict:
    return {
        "$schema": contract["$schema"],
        "$ref": f"#/$defs/{name}",
        "$defs": contract["$defs"],
    }


def assert_valid(contract: dict, definition: str, instance: object) -> None:
    jsonschema.Draft202012Validator(
        definition_schema(contract, definition)
    ).validate(instance)


def walk_refs(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            refs.add(ref)
        for child in value.values():
            refs.update(walk_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(walk_refs(child))
    return refs


def test_contract_is_valid_utf8_json_schema_with_resolvable_local_refs() -> None:
    raw = CONTRACT_PATH.read_bytes()
    assert raw.decode("utf-8").encode("utf-8") == raw
    contract = json.loads(raw)

    jsonschema.Draft202012Validator.check_schema(contract)
    definitions = set(contract["$defs"])
    for ref in walk_refs(contract):
        assert ref.startswith("#/$defs/")
        assert ref.removeprefix("#/$defs/") in definitions


def test_contract_exposes_exactly_four_read_only_health_tools() -> None:
    contract = load_contract()
    tools = contract["x-mcp-tools"]

    assert [tool["name"] for tool in tools] == [
        "health.get_current_state",
        "health.get_event_details",
        "health.get_recent_events",
        "health.get_device_status",
    ]
    for tool in tools:
        assert tool["inputSchema"]["$ref"].startswith("#/$defs/")
        assert tool["outputSchema"]["$ref"].startswith("#/$defs/")
        assert tool["annotations"] == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }


@pytest.mark.parametrize(
    ("golden_name", "definition"),
    [
        ("wearer_state_live", "WearerState"),
        ("wearer_event_resolved", "WearerEvent"),
        ("mcp_get_current_state_success_structured_content", "GetCurrentStateResult"),
        ("mcp_state_stale_structured_content", "GetCurrentStateResult"),
        ("mcp_state_offline_structured_content", "GetCurrentStateResult"),
        ("mcp_get_current_state_success", "McpCallToolResponse"),
        ("mcp_state_stale_error", "McpCallToolResponse"),
        ("mcp_state_offline_error", "McpCallToolResponse"),
        ("mcp_unknown_tool_error", "JsonRpcErrorResponse"),
        ("mcp_malformed_call_error", "JsonRpcErrorResponse"),
        ("tool_meta_device_status_success", "ToolMeta"),
        ("tool_meta_event_details_success", "ToolMeta"),
        ("tool_meta_recent_events_mixed", "ToolMeta"),
        ("tool_meta_generic_failure", "ToolMeta"),
        ("webhook_request", "WebhookRequest"),
        ("webhook_ack_accepted", "WebhookAck"),
        ("webhook_ack_duplicate", "WebhookAck"),
    ],
)
def test_machine_contract_goldens_validate(
    golden_name: str, definition: str
) -> None:
    contract = load_contract()
    assert_valid(contract, definition, contract["x-golden"][golden_name])


def test_mcp_text_content_is_deep_equal_to_structured_content() -> None:
    contract = load_contract()

    for name in (
        "mcp_get_current_state_success",
        "mcp_state_stale_error",
        "mcp_state_offline_error",
    ):
        response = contract["x-golden"][name]
        result = response["result"]
        assert len(result["content"]) == 1
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]


def test_webhook_canonical_body_digest_and_signature_match_golden() -> None:
    contract = load_contract()
    golden = contract["x-golden"]
    raw_body = golden["webhook_canonical_body_utf8"].encode("utf-8")
    secret = bytes.fromhex(golden["webhook_secret_hex"])
    signed_payload = (
        str(golden["webhook_timestamp"]).encode("ascii") + b"." + raw_body
    )

    assert json.loads(raw_body) == golden["webhook_request"]
    assert hashlib.sha256(raw_body).hexdigest() == golden["webhook_raw_body_sha256"]
    assert (
        "v1=" + hmac.new(secret, signed_payload, hashlib.sha256).hexdigest()
        == golden["webhook_signature_v1"]
    )


def test_ecg_ordinal_wrap_golden_is_internally_consistent() -> None:
    golden = load_contract()["x-golden"]["ecg_sample_ordinal_wrap"]

    assert golden["expanded_batch_last"] == 4_294_967_299
    assert golden["next_expanded_batch_first"] == 4_294_967_300
    assert golden["next_expanded_batch_last"] == 4_294_967_319
    assert golden["runtime_last_ecg_raw_sample_index"] == 23
    assert golden["runtime_last_ecg_sample_ordinal"] == 4_294_967_319
