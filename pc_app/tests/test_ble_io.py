import asyncio
import json
from pathlib import Path

from smart_neckband.ble_io import (
    BLE_UART_RX_UUID,
    BLE_UART_SERVICE_UUID,
    BLE_UART_TX_UUID,
    BlePacketReader,
    _matches_ble_uart_device,
    _start_notify_after_bond,
)
from smart_neckband.serial_io import PcDataStores
from smart_neckband.protocol import (
    VoiceTextAckPayload,
    VoiceTextChunkPayload,
    decode_packet,
    encode_voice_text_chunk_packet,
)
from smart_neckband.source_coordinator import PacketReceipt
from smart_neckband.webhook_store import WebhookStore


def load_ecg_packet() -> bytes:
    repo_root = Path(__file__).resolve().parents[2]
    vectors = json.loads(
        (repo_root / "docs" / "protocol" / "v0_golden_vectors.json").read_text(encoding="utf-8")
    )["vectors"]
    vector = next(item for item in vectors if item["name"].startswith("ecg_batch"))
    return bytes.fromhex(vector["packet_hex"])


def load_vector(name: str) -> bytes:
    repo_root = Path(__file__).resolve().parents[2]
    vectors = json.loads(
        (repo_root / "docs" / "protocol" / "v0_golden_vectors.json").read_text(encoding="utf-8")
    )["vectors"]
    vector = next(item for item in vectors if item["name"] == name)
    return bytes.fromhex(vector["packet_hex"])


def test_ble_uart_uuids_match_esp_idf_service() -> None:
    assert BLE_UART_SERVICE_UUID == "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
    assert BLE_UART_RX_UUID.endswith("0002-b5a3-f393-e0a9-e50e24dcca9e")
    assert BLE_UART_TX_UUID.endswith("0003-b5a3-f393-e0a9-e50e24dcca9e")


def test_ble_scan_matches_service_uuid_or_project_name() -> None:
    assert _matches_ble_uart_device("Unknown", [BLE_UART_SERVICE_UUID.upper()])
    assert _matches_ble_uart_device("CollarC3-12AB", [])
    assert not _matches_ble_uart_device("Other sensor", [])


def test_ble_notify_waits_for_bond_encryption() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def start_notify(self, uuid: str, callback: object) -> None:
            del uuid, callback
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("GATT Protocol Error: Insufficient Authentication")

    client = FakeClient()
    asyncio.run(_start_notify_after_bond(client, lambda *_: None, attempts=2, delay_s=0.0))
    assert client.calls == 2


def test_ble_reader_forwards_debug_messages() -> None:
    messages: list[str] = []
    reader = BlePacketReader(address="AA:BB:CC:DD:EE:FF", debug_callback=messages.append)

    reader._debug("连接阶段 %d", 2)

    assert messages == ["连接阶段 2"]


def test_ble_reader_reassembles_default_mtu_notifications() -> None:
    packet = load_ecg_packet()
    stores = PcDataStores.create()
    reader = BlePacketReader(address="AA:BB:CC:DD:EE:FF", stores=stores)

    for offset in range(0, len(packet), 20):
        reader.feed_notification(packet[offset : offset + 20])

    runtime = reader.runtime_status
    samples = stores.ecg.snapshot()
    assert runtime.packet_count == 1
    assert runtime.ecg_packet_count == 1
    assert len(samples) == 20
    assert reader.stats.crc_errors == 0


def test_ble_reader_recovers_after_truncated_packet() -> None:
    packet = load_ecg_packet()
    stores = PcDataStores.create()
    reader = BlePacketReader(address="AA:BB:CC:DD:EE:FF", stores=stores)

    reader.feed_notification(packet[:20])
    reader.feed_notification(packet)

    assert reader.runtime_status.ecg_packet_count == 1
    assert len(stores.ecg.snapshot()) == 20


def test_voice_text_is_durable_before_ack_and_duplicate_reuses_instruction(tmp_path) -> None:
    packet = load_vector("voice_text_final_single_chunk")
    store = WebhookStore(tmp_path / "webhook.sqlite3")
    events: list[str] = []
    acks: list[bytes] = []

    def persist(transcript) -> bool:
        store.create_instruction(
            transcript.text,
            instruction_id=transcript.instruction_id,
        )
        events.append(f"persist:{transcript.instruction_id}")
        return True

    def write_control(control_packet: bytes) -> None:
        assert store.get_instruction("voice-1122334455667788").text == "主人主人，向前走。"
        events.append("ack")
        acks.append(control_packet)

    reader = BlePacketReader(
        address="AA:BB:CC:DD:EE:FF",
        voice_text_callback=persist,
        control_write_callback=write_control,
    )
    for offset in range(0, len(packet), 7):
        reader.feed_notification(packet[offset : offset + 7])

    assert events == ["persist:voice-1122334455667788", "ack"]
    ack = decode_packet(acks[0])
    assert isinstance(ack.payload, VoiceTextAckPayload)
    assert ack.payload.utterance_id == 0x1122334455667788

    reader.feed_notification(packet)
    assert events[-2:] == ["persist:voice-1122334455667788", "ack"]
    assert len(store.list_instructions()) == 1


def test_voice_text_is_not_acked_when_persistence_fails() -> None:
    packet = load_vector("voice_text_final_single_chunk")
    acks: list[bytes] = []
    reader = BlePacketReader(
        address="AA:BB:CC:DD:EE:FF",
        voice_text_callback=lambda _transcript: False,
        control_write_callback=acks.append,
    )

    reader.feed_notification(packet)

    assert acks == []


def test_voice_reset_candidate_is_not_persisted_or_acked_before_confirmation() -> None:
    monotonic_values = iter((1, 2, 3))
    events: list[str] = []
    acks: list[bytes] = []

    def make_receipt() -> PacketReceipt:
        return PacketReceipt(
            received_monotonic_ns=next(monotonic_values),
            received_at_utc="2026-07-24T00:00:00.000Z",
        )

    def persist(transcript) -> bool:
        events.append(transcript.text)
        return True

    baseline = encode_voice_text_chunk_packet(
        packet_sequence=100,
        timestamp_us=5_000_001,
        chunk=VoiceTextChunkPayload(
            utterance_id=1,
            chunk_index=0,
            chunk_count=1,
            text_bytes=b"old",
        ),
    )
    trigger = encode_voice_text_chunk_packet(
        packet_sequence=1,
        timestamp_us=1_000_000,
        chunk=VoiceTextChunkPayload(
            utterance_id=2,
            chunk_index=0,
            chunk_count=2,
            text_bytes=b"new-",
        ),
    )
    confirmation = encode_voice_text_chunk_packet(
        packet_sequence=2,
        timestamp_us=1_100_000,
        chunk=VoiceTextChunkPayload(
            utterance_id=2,
            chunk_index=1,
            chunk_count=2,
            text_bytes=b"text",
        ),
    )
    reader = BlePacketReader(
        address="AA:BB:CC:DD:EE:FF",
        voice_text_callback=persist,
        control_write_callback=acks.append,
        receipt_factory=make_receipt,
    )

    reader.feed_notification(baseline)
    assert events == ["old"]
    assert len(acks) == 1

    reader.feed_notification(trigger)
    assert events == ["old"]
    assert len(acks) == 1

    reader.feed_notification(confirmation)
    assert events == ["old", "new-text"]
    assert len(acks) == 2
    assert reader.source_coordinator.stats.reset_confirmed == 1
