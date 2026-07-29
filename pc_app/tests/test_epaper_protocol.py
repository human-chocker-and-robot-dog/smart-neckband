from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_neckband.epaper_protocol import (
    BEGIN_FRAME_STRUCT,
    CONTROL_UUID,
    DEFAULT_CHUNK_DATA_BYTES,
    DEVICE_INFO_STRUCT,
    DEVICE_INFO_UUID,
    DISPLAY_SERVICE_UUID,
    FRAME_BYTES,
    FRAME_CHUNK_PREFIX_STRUCT,
    FRAME_DATA_UUID,
    FRAME_ID_CONTROL_STRUCT,
    STATUS_STRUCT,
    STATUS_UUID,
    BeginFrame,
    ControlCommand,
    DeviceCapability,
    DeviceInfo,
    DisplayErrorCode,
    DisplayOwner,
    DisplayProtocolError,
    DisplayStateCode,
    DisplayStatus,
    FrameChunk,
    RefreshMode,
    RefreshRequest,
    StatusFlag,
    crc32_iso_hdlc,
    decode_begin_frame,
    decode_device_info,
    decode_frame_chunk,
    decode_frame_id_control,
    decode_status,
    encode_begin_frame,
    encode_device_info,
    encode_frame_chunk,
    encode_frame_id_control,
    encode_status,
    frame_crc32,
    iter_frame_chunks,
)


def _golden() -> dict[str, object]:
    path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "protocol"
        / "quote0_ble_display_v1_golden_vectors.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_uuids_and_structure_sizes_match_firmware_golden_vectors() -> None:
    golden = _golden()
    assert golden["uuids"] == {
        "service": DISPLAY_SERVICE_UUID,
        "deviceInfo": DEVICE_INFO_UUID,
        "control": CONTROL_UUID,
        "frameData": FRAME_DATA_UUID,
        "status": STATUS_UUID,
    }
    assert golden["constants"] == {
        "protocolVersion": 1,
        "frameBytes": FRAME_BYTES,
        "maxChunkPayload": DEFAULT_CHUNK_DATA_BYTES,
        "beginFrameSize": BEGIN_FRAME_STRUCT.size,
        "commandSize": FRAME_ID_CONTROL_STRUCT.size,
        "frameDataHeaderSize": FRAME_CHUNK_PREFIX_STRUCT.size,
        "deviceInfoSize": DEVICE_INFO_STRUCT.size,
        "statusSize": STATUS_STRUCT.size,
    }


def test_begin_commit_and_cancel_match_firmware_bytes() -> None:
    vectors = _golden()["vectors"]
    begin_packet = encode_begin_frame(
        BeginFrame(
            frame_id=0x10203040,
            crc32=0x89ABCDEF,
            source_sample_index=0x0102030405060708,
            source_timestamp_us=0x1122334455667788,
            refresh_request=RefreshRequest.AUTO,
            rotation=90,
        )
    )
    assert begin_packet == bytes.fromhex(vectors["beginFrame"]["hex"])
    assert decode_begin_frame(begin_packet) == BeginFrame(
        frame_id=0x10203040,
        crc32=0x89ABCDEF,
        source_sample_index=0x0102030405060708,
        source_timestamp_us=0x1122334455667788,
        refresh_request=RefreshRequest.AUTO,
        rotation=90,
    )

    for command, vector_name in (
        (ControlCommand.COMMIT_FRAME, "commitFrame"),
        (ControlCommand.CANCEL_FRAME, "cancelFrame"),
    ):
        packet = encode_frame_id_control(command, 0x10203040)
        assert packet == bytes.fromhex(vectors[vector_name]["hex"])
        assert decode_frame_id_control(packet) == (command, 0x10203040)


def test_frame_data_matches_firmware_bytes_and_round_trips() -> None:
    vector = _golden()["vectors"]["frameData"]
    chunk = FrameChunk(
        frame_id=0x10203040,
        offset=180,
        data=bytes.fromhex("deadbeef"),
    )
    packet = encode_frame_chunk(chunk)
    assert packet == bytes.fromhex(vector["hex"])
    assert decode_frame_chunk(packet) == chunk


def test_device_info_matches_firmware_bytes_and_round_trips() -> None:
    vector = _golden()["vectors"]["deviceInfo"]
    info = DeviceInfo(
        capabilities=DeviceCapability(0x3F),
        width=296,
        height=152,
        frame_bytes=5624,
        max_chunk_payload=180,
        firmware_major=2,
        firmware_minor=1,
        firmware_patch=0,
        status_size=64,
        begin_frame_size=32,
        preferred_att_mtu=200,
        device_id_tail=0xB17A47E4,
        build_id=0x02010000,
    )
    packet = encode_device_info(info)
    assert packet == bytes.fromhex(vector["hex"])
    decoded = decode_device_info(packet)
    assert decoded == info
    assert decoded.firmware_version == "2.1.0"
    assert encode_device_info(decoded) == packet


def test_status_matches_firmware_bytes_and_round_trips() -> None:
    vector = _golden()["vectors"]["status"]
    status = DisplayStatus(
        state=DisplayStateCode.DONE,
        last_error=DisplayErrorCode.NONE,
        actual_refresh_mode=RefreshMode.PARTIAL,
        active_frame_id=0,
        last_frame_id=0x10203040,
        received_bytes=5624,
        flags=(
            StatusFlag.LINK_ENCRYPTED
            | StatusFlag.LINK_BONDED
            | StatusFlag.LINK_CONNECTED
        ),
        changed_x=24,
        changed_y=112,
        changed_width=16,
        changed_height=16,
        refresh_ms=4104,
        partial_refresh_count=7,
        battery_percent=97,
        owner=DisplayOwner.NONE,
        milliseconds_since_last_full=120000,
        battery_mv=4178,
        pending_replaced_count=2,
        crc_error_count=1,
        timeout_count=3,
        display_failure_count=0,
        free_heap_bytes=204040,
        status_sequence=42,
    )
    packet = encode_status(status)
    assert packet == bytes.fromhex(vector["hex"])
    assert decode_status(packet) == status
    assert encode_status(decode_status(packet)) == packet


def test_crc_vectors_match_firmware_values() -> None:
    crc_vectors = {item["name"]: item for item in _golden()["crc32"]}
    assert crc32_iso_hdlc(b"123456789") == int(
        crc_vectors["ascii_123456789"]["expected"], 16
    )
    white = bytes([crc_vectors["all_white_frame"]["byte"]]) * int(
        crc_vectors["all_white_frame"]["count"]
    )
    assert frame_crc32(white) == int(
        crc_vectors["all_white_frame"]["expected"], 16
    )


def test_capability_status_and_error_enums_match_firmware_spec() -> None:
    assert {name: int(value) for name, value in DeviceCapability.__members__.items()} == {
        "ENCRYPTED_WRITES": 1,
        "BONDING": 2,
        "STATUS_NOTIFY": 4,
        "PARTIAL_REFRESH": 8,
        "USB_TRANSPORT": 16,
        "LATEST_PENDING": 32,
    }
    assert {name: int(value) for name, value in StatusFlag.__members__.items()} == {
        "LINK_ENCRYPTED": 1,
        "LINK_BONDED": 2,
        "LINK_CONNECTED": 4,
        "PENDING_FRAME": 8,
        "LEGACY_MODE": 16,
        "SYNC_MODE": 32,
        "LOW_BATTERY": 64,
    }
    assert {name: int(value) for name, value in DisplayErrorCode.__members__.items()} == {
        "NONE": 0,
        "BAD_VERSION": 1,
        "BAD_OPCODE": 2,
        "NOT_ENCRYPTED": 3,
        "BUSY": 4,
        "INVALID_LENGTH": 5,
        "INVALID_ROTATION": 6,
        "INVALID_REFRESH_REQUEST": 7,
        "FRAME_ID_MISMATCH": 8,
        "OFFSET_MISMATCH": 9,
        "OVERFLOW": 10,
        "CRC_MISMATCH": 11,
        "TIMEOUT": 12,
        "NO_MEMORY": 13,
        "QUEUE_FAILURE": 14,
        "DISPLAY_FAILURE": 15,
        "INVALID_STATE": 16,
        "MALFORMED_MESSAGE": 17,
        "INTERNAL": 255,
    }


def test_frame_crc_and_chunking_cover_exact_frame() -> None:
    frame = bytes((index * 17) & 0xFF for index in range(FRAME_BYTES))
    chunks = iter_frame_chunks(frame_id=7, frame=frame)
    decoded = tuple(decode_frame_chunk(packet) for packet in chunks)

    assert frame_crc32(frame) == 0x032B98B9
    assert len(chunks) == 32
    assert all(len(chunk.data) <= DEFAULT_CHUNK_DATA_BYTES for chunk in decoded)
    assert b"".join(chunk.data for chunk in decoded) == frame
    assert tuple(chunk.offset for chunk in decoded) == tuple(
        range(0, FRAME_BYTES, DEFAULT_CHUNK_DATA_BYTES)
    )


def test_protocol_rejects_invalid_frame_shapes_and_offsets() -> None:
    with pytest.raises(DisplayProtocolError):
        frame_crc32(b"short")
    with pytest.raises(DisplayProtocolError):
        encode_begin_frame(
            BeginFrame(
                frame_id=0,
                crc32=0,
                source_sample_index=0,
                source_timestamp_us=0,
            )
        )
    with pytest.raises(DisplayProtocolError):
        encode_frame_chunk(
            FrameChunk(frame_id=1, offset=FRAME_BYTES - 1, data=b"xx")
        )
    with pytest.raises(DisplayProtocolError):
        decode_frame_chunk(bytes.fromhex("010002000100000000000000ff"))
