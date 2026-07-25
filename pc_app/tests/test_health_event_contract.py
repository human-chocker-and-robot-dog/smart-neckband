from __future__ import annotations

import hashlib

import jsonschema

from smart_neckband.health_event_contract import (
    BRIDGE_SCHEMA_VERSION,
    definition_schema,
    load_event_contract,
)
from smart_neckband.health_webhook import canonical_webhook_body, signature_header


def test_bridge_contract_and_golden_bytes_are_frozen() -> None:
    contract = load_event_contract()
    jsonschema.Draft202012Validator.check_schema(contract)
    golden = contract["x-golden"]
    body = canonical_webhook_body(golden["webhook_request"])

    assert contract["x-contract"]["contract_version"] == BRIDGE_SCHEMA_VERSION
    assert body.decode("utf-8") == golden["webhook_canonical_body_utf8"]
    assert hashlib.sha256(body).hexdigest() == golden["webhook_raw_body_sha256"]
    assert signature_header(
        bytes.fromhex(golden["webhook_secret_hex"]),
        golden["webhook_timestamp"],
        body,
    ) == golden["webhook_signature_v1"]
    jsonschema.Draft202012Validator(definition_schema("WebhookRequest")).validate(
        golden["webhook_request"]
    )
