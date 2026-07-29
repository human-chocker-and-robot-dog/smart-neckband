from __future__ import annotations

import json
from pathlib import Path

import pytest

from smart_neckband.epaper_protocol import (
    DEFAULT_CHUNK_DATA_BYTES,
    FRAME_BYTES,
    BeginFrame,
    ControlCommand,
    DeviceCapability,
    DeviceInfo,
    DisplayErrorCode,
    DisplayProtocolError,
    DisplayStateCode,
    DisplayStatus,
    FrameChunk,
    RefreshMode,
    RefreshRequest,
    StatusFlag,
    decode_begin_frame,
    decode_device_info,
    decode_frame_chunk,
    decode_status,
    encode_begin_frame,
    encode_device_info,
    encode_frame_chunk,
    encode_frame_id_control,
    encode_simple_control,
    encode_status,
    frame_crc32,
    iter_frame_chunks,
)


def _vectors() -> dict[str, str]:
    path = Path(__file__).resolve().parents[2] / "docs" / "protocol" / "quote0_ble_display_v1_golden_vectors.json"
    return {
        str(item["name"]): str(item["hex"])
        for item in json.loads(path.read_text(encoding="utf-8"))["vectors"]
    }


def test_begin_frame_matches_golden_vector() -> None:
    packet = encode_begin_frame(
        BeginFrame(
            frame_id=0x10203040,
            crc32=0x89ABCDEF,
            source_sample_index=0x0102030405060708,
            source_timestamp_us=0x1112131415161718,
            refresh_request=RefreshRequest.FORCE_FULL,
            rotation=90,
        )
    )
    assert packet.hex() == _vectors()["begin_frame_force_full"]
    assert decode_begin_frame(packet).frame_id == 0x10203040


def test_frame_chunk_matches_golden_vector_and_round_trips() -> None:
    packet = encode_frame_chunk(
        FrameChunk(frame_id=0x10203040, offset=0x0120, data=bytes.fromhex("001122334455"))
    )
    assert packet.hex() == _vectors()["frame_chunk"]
    assert decode_frame_chunk(packet).data == bytes.fromhex("001122334455")


def test_device_info_and_status_match_golden_vectors() -> None:
    info = DeviceInfo(
        capabilities=(
            DeviceCapability.PARTIAL_REFRESH
            | DeviceCapability.FORCE_FULL
            | DeviceCapability.BATTERY_STATUS
            | DeviceCapability.ENCRYPTED_WRITES
            | DeviceCapability.LATEST_PENDING
        ),
        width=296,
        height=152,
        frame_bytes=FRAME_BYTES,
        max_chunk_bytes=180,
        firmware_version="2.2.0-sync",
    )
    info_packet = encode_device_info(info)
    assert info_packet.hex() == _vectors()["device_info"]
    assert decode_device_info(info_packet) == info

    status = DisplayStatus(
        state=DisplayStateCode.DONE,
        frame_id=0x10203040,
        received_bytes=FRAME_BYTES,
        expected_bytes=FRAME_BYTES,
        refresh_mode=RefreshMode.PARTIAL,
        error_code=DisplayErrorCode.NONE,
        x=8,
        y=80,
        width=280,
        height=72,
        refresh_ms=4102,
        partial_refresh_count=7,
        battery_mv=3890,
        battery_percent=66,
        flags=StatusFlag.LINK_ENCRYPTED | StatusFlag.LINK_BONDED,
        pending_replaced_count=2,
        crc_error_count=1,
        timeout_count=3,
        display_failure_count=4,
    )
    status_packet = encode_status(status)
    assert status_packet.hex() == _vectors()["status_done_partial"]
    assert decode_status(status_packet) == status


def test_control_packets_match_golden_vectors() -> None:
    vectors = _vectors()
    assert (
        encode_frame_id_control(ControlCommand.COMMIT_FRAME, 0x10203040).hex()
        == vectors["commit_frame"]
    )
    assert encode_simple_control(ControlCommand.GET_STATUS).hex() == vectors["get_status"]


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
        encode_begin_frame(BeginFrame(frame_id=0, crc32=0, source_sample_index=0, source_timestamp_us=0))
    with pytest.raises(DisplayProtocolError):
        encode_frame_chunk(FrameChunk(frame_id=1, offset=FRAME_BYTES - 1, data=b"xx"))
    with pytest.raises(DisplayProtocolError):
        decode_frame_chunk(bytes.fromhex("0100000000000200ff"))
