from types import SimpleNamespace

import pytest
from serial.tools import list_ports

from smart_neckband.ble_io import BlePacketReader
from smart_neckband.protocol import encode_ecg_packet
from smart_neckband.serial_io import SerialPacketReader, list_serial_ports
from smart_neckband.source_coordinator import PacketReceipt


def test_list_serial_ports_marks_bluetooth_outgoing_port(monkeypatch) -> None:
    monkeypatch.setattr(
        list_ports,
        "comports",
        lambda: [
            SimpleNamespace(
                device="COM19",
                description="Bluetooth serial",
                hwid=r"BTHENUM\{00001101}_LOCALMFG&001D\0&08B61F3D3386_C00000000",
            ),
            SimpleNamespace(
                device="COM20",
                description="Bluetooth serial",
                hwid=r"BTHENUM\{00001101}_LOCALMFG&0000\0&000000000000_00000002",
            ),
        ],
    )

    ports = list_serial_ports()

    assert ports[0].is_bluetooth_candidate is True
    assert ports[0].is_bluetooth_outgoing is True
    assert ports[1].is_bluetooth_candidate is True
    assert ports[1].is_bluetooth_outgoing is False


@pytest.mark.parametrize("transport", ["serial", "ble"])
def test_raw_chunks_are_preserved_once_across_crc_error_and_reset_replay(
    transport: str,
) -> None:
    received_ns = iter((1, 2, 2_100_000_003))
    raw_chunks: list[bytes] = []

    def receipt_factory() -> PacketReceipt:
        return PacketReceipt(
            received_monotonic_ns=next(received_ns),
            received_at_utc="2026-07-24T00:00:00.000Z",
        )

    baseline = encode_ecg_packet(
        packet_sequence=100,
        timestamp_us=5_000_001,
        first_sample_index=0,
        samples=(2048,) * 20,
    )
    bad_crc = bytearray(
        encode_ecg_packet(
            packet_sequence=1000,
            timestamp_us=6_000_000,
            first_sample_index=20,
            samples=(2048,) * 20,
        )
    )
    bad_crc[-1] ^= 0xFF
    trigger = encode_ecg_packet(
        packet_sequence=1,
        timestamp_us=1_000_000,
        first_sample_index=20,
        samples=(2048,) * 20,
    )
    following = encode_ecg_packet(
        packet_sequence=101,
        timestamp_us=5_100_000,
        first_sample_index=20,
        samples=(2048,) * 20,
    )
    chunks = (baseline, bytes(bad_crc), trigger, following)

    if transport == "serial":
        reader = SerialPacketReader(
            port="COM19",
            raw_chunk_callback=raw_chunks.append,
            receipt_factory=receipt_factory,
        )
        feed = reader.feed_bytes
    else:
        reader = BlePacketReader(
            address="AA:BB:CC:DD:EE:FF",
            raw_chunk_callback=raw_chunks.append,
            receipt_factory=receipt_factory,
        )
        feed = reader.feed_notification
    for chunk in chunks:
        feed(chunk)

    assert b"".join(raw_chunks) == b"".join(chunks)
    assert reader.stats.crc_errors == 1
    assert reader.stats.packets_ok == 2
    assert reader.source_coordinator.stats.reset_candidate_rejected == 1
    assert reader.runtime_status.ecg_packet_count == 2
    assert len(reader.stores.ecg.snapshot()) == 40


@pytest.mark.parametrize("transport", ["serial", "ble"])
def test_live_reader_flushes_pending_reset_without_a_following_packet(
    transport: str,
) -> None:
    received_ns = iter((1, 2, 3))
    clock_ns = [2]

    def receipt_factory() -> PacketReceipt:
        return PacketReceipt(
            received_monotonic_ns=next(received_ns),
            received_at_utc="2026-07-24T00:00:00.000Z",
        )

    baseline = encode_ecg_packet(
        packet_sequence=100,
        timestamp_us=5_000_001,
        first_sample_index=0,
        samples=(2048,) * 20,
    )
    trigger = encode_ecg_packet(
        packet_sequence=1,
        timestamp_us=1_000_000,
        first_sample_index=20,
        samples=(2048,) * 20,
    )
    old_source_following = encode_ecg_packet(
        packet_sequence=101,
        timestamp_us=5_100_000,
        first_sample_index=20,
        samples=(2048,) * 20,
    )
    common = {
        "receipt_factory": receipt_factory,
        "monotonic_ns": lambda: clock_ns[0],
    }
    if transport == "serial":
        reader = SerialPacketReader(port="COM19", **common)
        feed = reader.feed_bytes
    else:
        reader = BlePacketReader(
            address="AA:BB:CC:DD:EE:FF",
            **common,
        )
        feed = reader.feed_notification

    feed(baseline)
    feed(trigger)
    feed(old_source_following)
    assert reader.source_coordinator.pending
    assert reader.stats.packets_ok == 1

    clock_ns[0] = 2_000_000_003
    feed(b"")

    assert not reader.source_coordinator.pending
    assert reader.source_coordinator.stats.reset_candidate_rejected == 1
    assert reader.stats.packets_ok == 2
    assert reader.runtime_status.ecg_packet_count == 2
