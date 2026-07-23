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


def load_ecg_packet() -> bytes:
    repo_root = Path(__file__).resolve().parents[2]
    vectors = json.loads(
        (repo_root / "docs" / "protocol" / "v0_golden_vectors.json").read_text(encoding="utf-8")
    )["vectors"]
    vector = next(item for item in vectors if item["name"].startswith("ecg_batch"))
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
