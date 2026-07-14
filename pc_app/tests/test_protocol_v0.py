import json
from pathlib import Path

import pytest

from smart_neckband.protocol import (
    ECG_PACKET_SIZE,
    ECG_PAYLOAD_SIZE,
    ECG_SAMPLE_COUNT,
    FLAG_ADC_CLIPPING,
    FLAG_LO_MINUS,
    PACKET_TYPE_ECG,
    PROTOCOL_VERSION,
    ProtocolError,
    crc16_ccitt_false,
    decode_header,
    encode_ecg_packet,
)


def load_golden_vector() -> dict:
    repo_root = Path(__file__).resolve().parents[2]
    vector_path = repo_root / "docs" / "protocol" / "v0_golden_vectors.json"
    data = json.loads(vector_path.read_text(encoding="utf-8"))
    return data["vectors"][0]


def test_crc16_ccitt_false_standard_check_value() -> None:
    assert crc16_ccitt_false(b"123456789") == 0x29B1


def test_encode_ecg_packet_matches_shared_golden_vector() -> None:
    vector = load_golden_vector()
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


def test_decode_header_from_golden_vector() -> None:
    packet = bytes.fromhex(load_golden_vector()["packet_hex"])

    header = decode_header(packet)

    assert header.protocol_version == PROTOCOL_VERSION
    assert header.packet_type == PACKET_TYPE_ECG
    assert header.payload_length == ECG_PAYLOAD_SIZE
    assert header.packet_sequence == 0x01020304
    assert header.timestamp_us == 0x0102030405060708


def test_encode_rejects_wrong_sample_count() -> None:
    with pytest.raises(ProtocolError):
        encode_ecg_packet(
            packet_sequence=1,
            timestamp_us=2,
            first_sample_index=3,
            samples=(2048,),
        )


def test_decode_header_rejects_bad_magic() -> None:
    packet = bytearray(bytes.fromhex(load_golden_vector()["packet_hex"]))
    packet[0] = 0x00

    with pytest.raises(ProtocolError):
        decode_header(bytes(packet))
