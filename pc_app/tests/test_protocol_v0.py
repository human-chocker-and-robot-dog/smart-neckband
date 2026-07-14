import json
from pathlib import Path

import pytest

from smart_neckband.protocol import (
    DEVICE_STATUS_PACKET_SIZE,
    DEVICE_STATUS_PAYLOAD_SIZE,
    ECG_PACKET_SIZE,
    ECG_PAYLOAD_SIZE,
    ECG_SAMPLE_COUNT,
    FLAG_ADC_CLIPPING,
    FLAG_LO_MINUS,
    FLAG_SAMPLE_QUEUE_OVERFLOW,
    FLAG_TRANSPORT_OVERFLOW,
    IMU_PACKET_SIZE,
    IMU_PAYLOAD_SIZE,
    DeviceStatusPayload,
    EcgPayload,
    ImuPayload,
    ImuPoint,
    PacketParser,
    PacketType,
    PROTOCOL_VERSION,
    ProtocolError,
    crc16_ccitt_false,
    decode_header,
    decode_packet,
    encode_device_status_packet,
    encode_ecg_packet,
    encode_imu_packet,
)


def load_vectors() -> dict[str, dict]:
    repo_root = Path(__file__).resolve().parents[2]
    vector_path = repo_root / "docs" / "protocol" / "v0_golden_vectors.json"
    data = json.loads(vector_path.read_text(encoding="utf-8"))
    return {vector["name"]: vector for vector in data["vectors"]}


def test_crc16_ccitt_false_standard_check_value() -> None:
    assert crc16_ccitt_false(b"123456789") == 0x29B1


def test_encode_ecg_packet_matches_shared_golden_vector() -> None:
    vector = load_vectors()["ecg_batch_20_samples_lo_minus_adc_clipping"]
    samples = tuple(range(2048, 2048 + ECG_SAMPLE_COUNT))

    packet = encode_ecg_packet(
        packet_sequence=0x01020304,
        timestamp_us=0x0102030405060708,
        first_sample_index=0x00001000,
        samples=samples,
        flags=FLAG_LO_MINUS | FLAG_ADC_CLIPPING,
    )

    assert len(packet) == ECG_PACKET_SIZE
    assert packet.hex() == vector["packet_hex"]
    assert int.from_bytes(packet[-2:], "little") == int(vector["crc16_ccitt_false"], 16)


def test_encode_imu_packet_matches_shared_golden_vector() -> None:
    vector = load_vectors()["imu_batch_two_raw_samples"]
    packet = encode_imu_packet(
        packet_sequence=0x01020305,
        timestamp_us=0x0102030405060710,
        first_sample_index=0x00000020,
        samples=(
            ImuPoint(ax=1000, ay=-1000, az=16384, gx=10, gy=-20, gz=30),
            ImuPoint(ax=1001, ay=-999, az=16380, gx=11, gy=-21, gz=31),
        ),
    )

    assert len(packet) == IMU_PACKET_SIZE
    assert packet.hex() == vector["packet_hex"]
    assert int.from_bytes(packet[-2:], "little") == int(vector["crc16_ccitt_false"], 16)


def test_encode_device_status_packet_matches_shared_golden_vector() -> None:
    vector = load_vectors()["device_status_lead_off_and_overflow"]
    packet = encode_device_status_packet(
        packet_sequence=0x01020306,
        timestamp_us=0x0102030405060800,
        status=DeviceStatusPayload(
            lead_off_flags=FLAG_LO_MINUS,
            sensor_status_flags=0x0F,
            ecg_buffer_usage_percent=25,
            imu_buffer_usage_percent=10,
            spp_queue_usage_percent=40,
            status_flags=FLAG_LO_MINUS | FLAG_SAMPLE_QUEUE_OVERFLOW | FLAG_TRANSPORT_OVERFLOW,
            error_count=3,
            ecg_ring_overflow_count=1,
            imu_ring_overflow_count=2,
            spp_queue_overflow_count=3,
            transport_drop_count=4,
            i2c_error_count=5,
        ),
    )

    assert len(packet) == DEVICE_STATUS_PACKET_SIZE
    assert packet.hex() == vector["packet_hex"]
    assert int.from_bytes(packet[-2:], "little") == int(vector["crc16_ccitt_false"], 16)


@pytest.mark.parametrize(
    ("name", "packet_type", "payload_length"),
    [
        ("ecg_batch_20_samples_lo_minus_adc_clipping", PacketType.ECG_BATCH, ECG_PAYLOAD_SIZE),
        ("imu_batch_two_raw_samples", PacketType.IMU_BATCH, IMU_PAYLOAD_SIZE),
        ("device_status_lead_off_and_overflow", PacketType.DEVICE_STATUS, DEVICE_STATUS_PAYLOAD_SIZE),
    ],
)
def test_decode_header_from_golden_vectors(name: str, packet_type: PacketType, payload_length: int) -> None:
    packet = bytes.fromhex(load_vectors()[name]["packet_hex"])

    header = decode_header(packet)

    assert header.protocol_version == PROTOCOL_VERSION
    assert header.packet_type == packet_type
    assert header.payload_length == payload_length


def test_decode_packet_returns_typed_payloads() -> None:
    vectors = load_vectors()
    assert isinstance(
        decode_packet(bytes.fromhex(vectors["ecg_batch_20_samples_lo_minus_adc_clipping"]["packet_hex"])).payload,
        EcgPayload,
    )
    assert isinstance(
        decode_packet(bytes.fromhex(vectors["imu_batch_two_raw_samples"]["packet_hex"])).payload,
        ImuPayload,
    )
    assert isinstance(
        decode_packet(bytes.fromhex(vectors["device_status_lead_off_and_overflow"]["packet_hex"])).payload,
        DeviceStatusPayload,
    )


def test_encode_rejects_wrong_sample_count() -> None:
    with pytest.raises(ProtocolError):
        encode_ecg_packet(
            packet_sequence=1,
            timestamp_us=2,
            first_sample_index=3,
            samples=(2048,),
        )


def test_decode_header_rejects_bad_magic() -> None:
    packet = bytearray(bytes.fromhex(load_vectors()["ecg_batch_20_samples_lo_minus_adc_clipping"]["packet_hex"]))
    packet[0] = 0x00

    with pytest.raises(ProtocolError):
        decode_header(bytes(packet))


def test_stream_parser_resyncs_and_tracks_sequence_gap() -> None:
    vectors = load_vectors()
    first = bytes.fromhex(vectors["ecg_batch_20_samples_lo_minus_adc_clipping"]["packet_hex"])
    second = bytes.fromhex(vectors["device_status_lead_off_and_overflow"]["packet_hex"])
    parser = PacketParser()

    assert parser.feed(b"noise") == []
    packets = parser.feed(first[:7])
    assert packets == []
    packets = parser.feed(first[7:] + second)

    assert [packet.header.packet_sequence for packet in packets] == [0x01020304, 0x01020306]
    assert parser.stats.bytes_discarded == len(b"noise")
    assert parser.stats.sequence_gap_count == 1
    assert parser.stats.packets_lost == 1


def test_stream_parser_rejects_crc_error() -> None:
    packet = bytearray(bytes.fromhex(load_vectors()["ecg_batch_20_samples_lo_minus_adc_clipping"]["packet_hex"]))
    packet[-1] ^= 0xFF
    parser = PacketParser()

    assert parser.feed(bytes(packet)) == []
    assert parser.stats.crc_errors == 1
