from __future__ import annotations

from hashlib import sha256
import json
from uuid import uuid4


def new_connection_instance_id() -> str:
    """Return a process-local identity for one BLE connection attempt."""

    return uuid4().hex


def mic_asr_instruction_id(
    *,
    device_identity: str,
    connection_instance_id: str,
    detected_sample_index: int,
    wake_count: int,
) -> str:
    """Derive a stable ordinary Webhook ID for one PC ASR wake session."""

    source = device_identity.strip().lower()
    connection = connection_instance_id.strip().lower()
    if not source:
        raise ValueError("device_identity must not be empty")
    if not connection:
        raise ValueError("connection_instance_id must not be empty")
    if detected_sample_index < 0:
        raise ValueError("detected_sample_index must be non-negative")
    if wake_count < 0:
        raise ValueError("wake_count must be non-negative")

    canonical = json.dumps(
        {
            "connection_instance_id": connection,
            "detected_sample_index": int(detected_sample_index),
            "device_identity": source,
            "wake_count": int(wake_count),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"mic-{sha256(canonical).hexdigest()[:32]}"
