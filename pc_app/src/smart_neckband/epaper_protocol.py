from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
import struct
import zlib


PROTOCOL_VERSION = 1

DISPLAY_SERVICE_UUID = "7f510001-1b15-4a7b-9e9f-6b64d5f7c100"
DEVICE_INFO_UUID = "7f510002-1b15-4a7b-9e9f-6b64d5f7c100"
CONTROL_UUID = "7f510003-1b15-4a7b-9e9f-6b64d5f7c100"
FRAME_DATA_UUID = "7f510004-1b15-4a7b-9e9f-6b64d5f7c100"
STATUS_UUID = "7f510005-1b15-4a7b-9e9f-6b64d5f7c100"

FRAME_WIDTH = 296
FRAME_HEIGHT = 152
FRAME_STRIDE = FRAME_WIDTH // 8
FRAME_BYTES = FRAME_STRIDE * FRAME_HEIGHT
DEFAULT_CHUNK_DATA_BYTES = 180


class DisplayProtocolError(ValueError):
    """Raised when bytes do not conform to BLE Display Protocol v1."""


class ControlCommand(IntEnum):
    BEGIN_FRAME = 1
    COMMIT_FRAME = 2
    CANCEL_FRAME = 3
    GET_STATUS = 4
    FORCE_FULL_NEXT = 5


class RefreshRequest(IntEnum):
    AUTO = 0
    FORCE_FULL = 1


class DisplayStateCode(IntEnum):
    READY = 0
    RECEIVING = 1
    QUEUED = 2
    REFRESHING = 3
    DONE = 4
    ERROR = 5


class RefreshMode(IntEnum):
    NONE = 0
    FULL = 1
    PARTIAL = 2


class DisplayErrorCode(IntEnum):
    NONE = 0
    BAD_VERSION = 1
    BAD_LENGTH = 2
    BAD_FRAME_ID = 3
    BAD_OFFSET = 4
    CRC_MISMATCH = 5
    BUSY = 6
    TIMEOUT = 7
    DISPLAY_FAILED = 8
    NOT_ENCRYPTED = 9
    INTERNAL = 10


class DeviceCapability(IntFlag):
    PARTIAL_REFRESH = 1 << 0
    FORCE_FULL = 1 << 1
    BATTERY_STATUS = 1 << 2
    ENCRYPTED_WRITES = 1 << 3
    LATEST_PENDING = 1 << 4


class StatusFlag(IntFlag):
    LINK_ENCRYPTED = 1 << 0
    LINK_BONDED = 1 << 1
    PENDING_FRAME = 1 << 2
    LOW_BATTERY = 1 << 3


BEGIN_FRAME_STRUCT = struct.Struct("<BBIHIHBBQQ")
FRAME_ID_CONTROL_STRUCT = struct.Struct("<BI")
SIMPLE_CONTROL_STRUCT = struct.Struct("<B")
FRAME_CHUNK_PREFIX_STRUCT = struct.Struct("<IHH")
DEVICE_INFO_STRUCT = struct.Struct("<BBHHHH16s")
STATUS_STRUCT = struct.Struct("<BBIHHBBHHHHIHHBBHHHH")


@dataclass(frozen=True, slots=True)
class BeginFrame:
    frame_id: int
    crc32: int
    source_sample_index: int
    source_timestamp_us: int
    refresh_request: RefreshRequest = RefreshRequest.AUTO
    rotation: int = 90
    frame_length: int = FRAME_BYTES
    flags: int = 0


@dataclass(frozen=True, slots=True)
class FrameChunk:
    frame_id: int
    offset: int
    data: bytes


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    capabilities: DeviceCapability
    width: int
    height: int
    frame_bytes: int
    max_chunk_bytes: int
    firmware_version: str
    protocol_version: int = PROTOCOL_VERSION


@dataclass(frozen=True, slots=True)
class DisplayStatus:
    state: DisplayStateCode
    frame_id: int
    received_bytes: int
    expected_bytes: int
    refresh_mode: RefreshMode
    error_code: DisplayErrorCode
    x: int
    y: int
    width: int
    height: int
    refresh_ms: int
    partial_refresh_count: int
    battery_mv: int
    battery_percent: int
    flags: StatusFlag
    pending_replaced_count: int
    crc_error_count: int
    timeout_count: int
    display_failure_count: int
    protocol_version: int = PROTOCOL_VERSION


def frame_crc32(frame: bytes) -> int:
    if len(frame) != FRAME_BYTES:
        raise DisplayProtocolError(
            f"Quote/0 frames must contain {FRAME_BYTES} bytes, got {len(frame)}"
        )
    return zlib.crc32(frame) & 0xFFFFFFFF


def encode_begin_frame(value: BeginFrame) -> bytes:
    _validate_frame_id(value.frame_id)
    _validate_u32(value.crc32, "crc32")
    if value.frame_length != FRAME_BYTES:
        raise DisplayProtocolError(f"frame_length must be {FRAME_BYTES}")
    if value.rotation not in {90, 270}:
        raise DisplayProtocolError("rotation must be 90 or 270")
    _validate_u8(value.flags, "flags")
    _validate_u64(value.source_sample_index, "source_sample_index")
    _validate_u64(value.source_timestamp_us, "source_timestamp_us")
    return BEGIN_FRAME_STRUCT.pack(
        ControlCommand.BEGIN_FRAME,
        PROTOCOL_VERSION,
        value.frame_id,
        value.frame_length,
        value.crc32,
        value.rotation,
        int(value.refresh_request),
        value.flags,
        value.source_sample_index,
        value.source_timestamp_us,
    )


def decode_begin_frame(data: bytes) -> BeginFrame:
    if len(data) != BEGIN_FRAME_STRUCT.size:
        raise DisplayProtocolError("invalid BEGIN_FRAME length")
    (
        command,
        version,
        frame_id,
        frame_length,
        crc32,
        rotation,
        refresh_request,
        flags,
        source_sample_index,
        source_timestamp_us,
    ) = BEGIN_FRAME_STRUCT.unpack(data)
    if command != ControlCommand.BEGIN_FRAME:
        raise DisplayProtocolError("packet is not BEGIN_FRAME")
    _require_version(version)
    try:
        refresh = RefreshRequest(refresh_request)
    except ValueError as exc:
        raise DisplayProtocolError("invalid refresh request") from exc
    value = BeginFrame(
        frame_id=frame_id,
        crc32=crc32,
        source_sample_index=source_sample_index,
        source_timestamp_us=source_timestamp_us,
        refresh_request=refresh,
        rotation=rotation,
        frame_length=frame_length,
        flags=flags,
    )
    encode_begin_frame(value)
    return value


def encode_frame_id_control(command: ControlCommand, frame_id: int) -> bytes:
    if command not in {ControlCommand.COMMIT_FRAME, ControlCommand.CANCEL_FRAME}:
        raise DisplayProtocolError("command does not carry a frame ID")
    _validate_frame_id(frame_id)
    return FRAME_ID_CONTROL_STRUCT.pack(command, frame_id)


def encode_simple_control(command: ControlCommand) -> bytes:
    if command not in {ControlCommand.GET_STATUS, ControlCommand.FORCE_FULL_NEXT}:
        raise DisplayProtocolError("command is not a simple control")
    return SIMPLE_CONTROL_STRUCT.pack(command)


def encode_frame_chunk(chunk: FrameChunk) -> bytes:
    _validate_frame_id(chunk.frame_id)
    if not chunk.data:
        raise DisplayProtocolError("frame chunks must not be empty")
    if len(chunk.data) > 0xFFFF:
        raise DisplayProtocolError("frame chunk is too large")
    if not 0 <= chunk.offset < FRAME_BYTES:
        raise DisplayProtocolError("frame chunk offset is outside the frame")
    if chunk.offset + len(chunk.data) > FRAME_BYTES:
        raise DisplayProtocolError("frame chunk extends past the frame")
    return FRAME_CHUNK_PREFIX_STRUCT.pack(
        chunk.frame_id,
        chunk.offset,
        len(chunk.data),
    ) + chunk.data


def decode_frame_chunk(data: bytes) -> FrameChunk:
    if len(data) < FRAME_CHUNK_PREFIX_STRUCT.size:
        raise DisplayProtocolError("frame chunk is shorter than its prefix")
    frame_id, offset, length = FRAME_CHUNK_PREFIX_STRUCT.unpack_from(data)
    payload = data[FRAME_CHUNK_PREFIX_STRUCT.size :]
    if len(payload) != length:
        raise DisplayProtocolError("frame chunk payload length mismatch")
    chunk = FrameChunk(frame_id=frame_id, offset=offset, data=payload)
    encode_frame_chunk(chunk)
    return chunk


def iter_frame_chunks(
    *,
    frame_id: int,
    frame: bytes,
    chunk_data_bytes: int = DEFAULT_CHUNK_DATA_BYTES,
) -> tuple[bytes, ...]:
    if len(frame) != FRAME_BYTES:
        raise DisplayProtocolError(f"frame must contain {FRAME_BYTES} bytes")
    if not 1 <= chunk_data_bytes <= 0xFFFF:
        raise DisplayProtocolError("chunk_data_bytes is outside the supported range")
    return tuple(
        encode_frame_chunk(
            FrameChunk(
                frame_id=frame_id,
                offset=offset,
                data=frame[offset : offset + chunk_data_bytes],
            )
        )
        for offset in range(0, len(frame), chunk_data_bytes)
    )


def encode_device_info(value: DeviceInfo) -> bytes:
    if value.protocol_version != PROTOCOL_VERSION:
        raise DisplayProtocolError("unsupported device info protocol version")
    firmware = value.firmware_version.encode("ascii", errors="strict")
    if len(firmware) > 16:
        raise DisplayProtocolError("firmware version exceeds 16 ASCII bytes")
    return DEVICE_INFO_STRUCT.pack(
        value.protocol_version,
        int(value.capabilities),
        value.width,
        value.height,
        value.frame_bytes,
        value.max_chunk_bytes,
        firmware.ljust(16, b"\0"),
    )


def decode_device_info(data: bytes) -> DeviceInfo:
    if len(data) != DEVICE_INFO_STRUCT.size:
        raise DisplayProtocolError("invalid DEVICE_INFO length")
    version, capabilities, width, height, frame_bytes, max_chunk, firmware = (
        DEVICE_INFO_STRUCT.unpack(data)
    )
    _require_version(version)
    return DeviceInfo(
        protocol_version=version,
        capabilities=DeviceCapability(capabilities),
        width=width,
        height=height,
        frame_bytes=frame_bytes,
        max_chunk_bytes=max_chunk,
        firmware_version=firmware.split(b"\0", 1)[0].decode("ascii"),
    )


def encode_status(value: DisplayStatus) -> bytes:
    if value.protocol_version != PROTOCOL_VERSION:
        raise DisplayProtocolError("unsupported status protocol version")
    return STATUS_STRUCT.pack(
        value.protocol_version,
        int(value.state),
        value.frame_id,
        value.received_bytes,
        value.expected_bytes,
        int(value.refresh_mode),
        int(value.error_code),
        value.x,
        value.y,
        value.width,
        value.height,
        value.refresh_ms,
        value.partial_refresh_count,
        value.battery_mv,
        value.battery_percent,
        int(value.flags),
        value.pending_replaced_count,
        value.crc_error_count,
        value.timeout_count,
        value.display_failure_count,
    )


def decode_status(data: bytes) -> DisplayStatus:
    if len(data) != STATUS_STRUCT.size:
        raise DisplayProtocolError("invalid STATUS length")
    values = STATUS_STRUCT.unpack(data)
    _require_version(values[0])
    try:
        state = DisplayStateCode(values[1])
        refresh_mode = RefreshMode(values[5])
        error_code = DisplayErrorCode(values[6])
    except ValueError as exc:
        raise DisplayProtocolError("STATUS contains an unknown enum value") from exc
    return DisplayStatus(
        protocol_version=values[0],
        state=state,
        frame_id=values[2],
        received_bytes=values[3],
        expected_bytes=values[4],
        refresh_mode=refresh_mode,
        error_code=error_code,
        x=values[7],
        y=values[8],
        width=values[9],
        height=values[10],
        refresh_ms=values[11],
        partial_refresh_count=values[12],
        battery_mv=values[13],
        battery_percent=values[14],
        flags=StatusFlag(values[15]),
        pending_replaced_count=values[16],
        crc_error_count=values[17],
        timeout_count=values[18],
        display_failure_count=values[19],
    )


def _require_version(version: int) -> None:
    if version != PROTOCOL_VERSION:
        raise DisplayProtocolError(
            f"unsupported BLE display protocol version {version}"
        )


def _validate_frame_id(value: int) -> None:
    _validate_u32(value, "frame_id")
    if value == 0:
        raise DisplayProtocolError("frame_id 0 is reserved")


def _validate_u8(value: int, name: str) -> None:
    if not 0 <= value <= 0xFF:
        raise DisplayProtocolError(f"{name} is outside uint8 range")


def _validate_u32(value: int, name: str) -> None:
    if not 0 <= value <= 0xFFFFFFFF:
        raise DisplayProtocolError(f"{name} is outside uint32 range")


def _validate_u64(value: int, name: str) -> None:
    if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise DisplayProtocolError(f"{name} is outside uint64 range")
