from __future__ import annotations

import re

import pytest

from smart_neckband.mic_webhook import mic_asr_instruction_id
from smart_neckband.webhook_client import WebhookDispatcher
from smart_neckband.webhook_store import InstructionConflictError, WebhookStore


def _instruction_id(*, connection: str = "connection-a") -> str:
    return mic_asr_instruction_id(
        device_identity="AA:BB:CC:DD:EE:FF",
        connection_instance_id=connection,
        detected_sample_index=123_456,
        wake_count=7,
    )


def test_mic_instruction_id_is_stable_and_normalized() -> None:
    first = _instruction_id()
    second = mic_asr_instruction_id(
        device_identity="  aa:bb:cc:dd:ee:ff  ",
        connection_instance_id=" CONNECTION-A ",
        detected_sample_index=123_456,
        wake_count=7,
    )

    assert first == second
    assert re.fullmatch(r"mic-[0-9a-f]{32}", first)


def test_new_connection_instance_prevents_device_restart_collisions() -> None:
    assert _instruction_id(connection="connection-a") != _instruction_id(
        connection="connection-b"
    )


def test_duplicate_pc_asr_final_is_one_durable_instruction(tmp_path) -> None:
    store = WebhookStore(tmp_path / "webhook.sqlite3")
    dispatcher = WebhookDispatcher(
        store=store,
        settings_provider=store.load_settings,
    )
    instruction_id = _instruction_id()

    first = dispatcher.enqueue_text("请汇报当前状态", instruction_id=instruction_id)
    second = dispatcher.enqueue_text("请汇报当前状态", instruction_id=instruction_id)

    assert first == second
    assert store.list_instructions() == (first,)


def test_same_mic_instruction_id_rejects_conflicting_final_text(tmp_path) -> None:
    store = WebhookStore(tmp_path / "webhook.sqlite3")
    dispatcher = WebhookDispatcher(
        store=store,
        settings_provider=store.load_settings,
    )
    instruction_id = _instruction_id()
    dispatcher.enqueue_text("第一条 final", instruction_id=instruction_id)

    with pytest.raises(InstructionConflictError):
        dispatcher.enqueue_text("冲突的第二条 final", instruction_id=instruction_id)
