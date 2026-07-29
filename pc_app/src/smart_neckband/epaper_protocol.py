from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
import struct
import zlib


PROTOCOL_VERSION = 1
STRUCTURE_VERSION = 1

DISPLAY_SERVICE_UUID = "7b7d1000-4a30-4b15-9f41-5d2e6a8f6c20"
DEVICE_INFO_UUID = "7b7d1001-4a30-4b15-9f41-5d2e6a8f6c20"
CONTROL_UUID = "7b7d1002-4a30-4b15-9f41-5d2e6a8f6c20"
FRAME_DATA_UUID = "7b7d1003-4a30-4b15-9f41-5d2e6a8f6c20"
STATUS_UUID = "7b7d1004-4a30-4b15-9f41-5d2e6a8f6c20"

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


class DisplayOwner(IntEnum):
    NONE = 0
    USB = 1
    BLE = 2


class DisplayErrorCode(IntEnum):
    NONE = 0
    BAD_VERSION = 1
    BAD_OPCODE = 2
    NOT_ENCRYPTED = 3
    BUSY = 4
    INVALID_LENGTH = 5
    INVALID_ROTATION = 6
    INVALID_REFRESH_REQUEST = 7
    FRAME_ID_MISMATCH = 8
    OFFSET_MISMATCH = 9
    OVERFLOW = 10
    CRC_MISMATCH = 11
    TIMEOUT = 12
    NO_MEMORY = 13
    QUEUE_FAILURE = 14
    DISPLAY_FAILURE = 15
    INVALID_STATE = 16
    MALFORMED_MESSAGE = 17
    INTERNAL = 255


class DeviceCapability(IntFlag):
    ENCRYPTED_WRITES = 1 << 0
    BONDING = 1 << 1
    STATUS_NOTIFY = 1 << 2
    PARTIAL_REFRESH = 1 << 3
    USB_TRANSPORT = 1 << 4
    LATEST_PENDING = 1 << 5


class StatusFlag(IntFlag):
    LINK_ENCRYPTED = 1 << 0
    LINK_BONDED = 1 << 1
    LINK_CONNECTED = 1 << 2
    PENDING_FRAME = 1 << 3
    LEGACY_MODE = 1 << 4
    SYNC_MODE = 1 << 5
    LOW_BATTERY = 1 << 6


BEGIN_FRAME_STRUCT = struct.Struct("<BBBBIHHIQQ")
FRAME_ID_CONTROL_STRUCT = struct.Struct("<BBHI")
FRAME_CHUNK_PREFIX_STRUCT = struct.Struct("<BBHIHH")
DEVICE_INFO_STRUCT = struct.Struct("<BBBBBBHHHHHHHHHII")
STATUS_STRUCT = struct.Struct("<BBBBIIHHHHHHIHBBIHHIIIIII")


@dataclass(frozen=True, slots=True)
class BeginFrame:
    frame_id: int
    crc32: int
    source_sample_index: int
    source_timestamp_us: int
    refresh_request: RefreshRequest = RefreshRequest.AUTO
    rotation: int = 90
    frame_length: int = FRAME_BYTES


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
    max_chunk_payload: int
    firmware_major: int
    firmware_minor: int
    firmware_patch: int
    status_size: int
    begin_frame_size: int
    preferred_att_mtu: int
    device_id_tail: int
    build_id: int
    protocol_version: int = PROTOCOL_VERSION
    structure_version: int = STRUCTURE_VERSION

    @property
    def firmware_version(self) -> str:
        return f"{self.firmware_major}.{self.firmware_minor}.{self.firmware_patch}"


@dataclass(frozen=True, slots=True)
class DisplayStatus:
    state: DisplayStateCode
    last_error: DisplayErrorCode
    actual_refresh_mode: RefreshMode
    active_frame_id: int
    last_frame_id: int
    received_bytes: int
    flags: StatusFlag
    changed_x: int
    changed_y: int
    changed_width: int
    changed_height: int
    refresh_ms: int
    partial_refresh_count: int
    battery_percent: int
    owner: DisplayOwner
    milliseconds_since_last_full: int
    battery_mv: int
    pending_replaced_count: int
    crc_error_count: int
    timeout_count: int
    display_failure_count: int
    free_heap_bytes: int
    status_sequence: int
    protocol_version: int = PROTOCOL_VERSION


def crc32_iso_hdlc(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def frame_crc32(frame: bytes) -> int:
    if len(frame) != FRAME_BYTES:
        raise DisplayProtocolError(
            f"Quote/0 frames must contain {FRAME_BYTES} bytes, got {len(frame)}"
        )
    return crc32_iso_hdlc(frame)


def encode_begin_frame(value: BeginFrame) -> bytes:
    _validate_frame_id(value.frame_id)
    _validate_u32(value.crc32, "crc32")
    if value.frame_length != FRAME_BYTES:
        raise DisplayProtocolError(f"frame_length must be {FRAME_BYTES}")
    if value.rotation not in {90, 270}:
        raise DisplayProtocolError("rotation must be 90 or 270")
    _validate_u64(value.source_sample_index, "source_sample_index")
    _validate_u64(value.source_timestamp_us, "source_timestamp_us")
    return BEGIN_FRAME_STRUCT.pack(
        PROTOCOL_VERSION,
        ControlCommand.BEGIN_FRAME,
        int(value.refresh_request),
        0,
        value.frame_id,
        value.frame_length,
        value.rotation,
        value.crc32,
        value.source_sample_index,
        value.source_timestamp_us,
    )


def decode_begin_frame(data: bytes) -> BeginFrame:
    if len(data) != BEGIN_FRAME_STRUCT.size:
        raise DisplayProtocolError("invalid BEGIN_FRAME length")
    (
        version,
        opcode,
        refresh_request,
        reserved,
        frame_id,
        frame_length,
        rotation,
        crc32,
        source_sample_index,
        source_timestamp_us,
    ) = BEGIN_FRAME_STRUCT.unpack(data)
    _require_version(version)
    if opcode != ControlCommand.BEGIN_FRAME:
        raise DisplayProtocolError("packet is not BEGIN_FRAME")
    if reserved != 0:
        raise DisplayProtocolError("BEGIN_FRAME reserved byte must be zero")
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
    )
    encode_begin_frame(value)
    return value


def encode_frame_id_control(command: ControlCommand, frame_id: int) -> bytes:
    if command not in {ControlCommand.COMMIT_FRAME, ControlCommand.CANCEL_FRAME}:
        raise DisplayProtocolError("command does not carry a frame ID")
    _validate_frame_id(frame_id)
    return FRAME_ID_CONTROL_STRUCT.pack(PROTOCOL_VERSION, command, 0, frame_id)


def decode_frame_id_control(data: bytes) -> tuple[ControlCommand, int]:
    if len(data) != FRAME_ID_CONTROL_STRUCT.size:
        raise DisplayProtocolError("invalid frame-ID control length")
    version, opcode, reserved, frame_id = FRAME_ID_CONTROL_STRUCT.unpack(data)
    _require_version(version)
    try:
        command = ControlCommand(opcode)
    except ValueError as exc:
        raise DisplayProtocolError("unknown control opcode") from exc
    if command not in {ControlCommand.COMMIT_FRAME, ControlCommand.CANCEL_FRAME}:
        raise DisplayProtocolError("control packet is not COMMIT_FRAME or CANCEL_FRAME")
    if reserved != 0:
        raise DisplayProtocolError("control reserved field must be zero")
    _validate_frame_id(frame_id)
    return command, frame_id


def encode_frame_chunk(chunk: FrameChunk) -> bytes:
    _validate_frame_id(chunk.frame_id)
    if not chunk.data:
        raise DisplayProtocolError("frame chunks must not be empty")
    if len(chunk.data) > DEFAULT_CHUNK_DATA_BYTES:
        raise DisplayProtocolError(
            f"frame chunk payload exceeds {DEFAULT_CHUNK_DATA_BYTES} bytes"
        )
    if not 0 <= chunk.offset < FRAME_BYTES:
        raise DisplayProtocolError("frame chunk offset is outside the frame")
    if chunk.offset + len(chunk.data) > FRAME_BYTES:
        raise DisplayProtocolError("frame chunk extends past the frame")
    return FRAME_CHUNK_PREFIX_STRUCT.pack(
        PROTOCOL_VERSION,
        0,
        len(chunk.data),
        chunk.frame_id,
        chunk.offset,
        0,
    ) + chunk.data


def decode_frame_chunk(data: bytes) -> FrameChunk:
    if len(data) < FRAME_CHUNK_PREFIX_STRUCT.size:
        raise DisplayProtocolError("frame chunk is shorter than its prefix")
    version, flags, length, frame_id, offset, reserved = (
        FRAME_CHUNK_PREFIX_STRUCT.unpack_from(data)
    )
    _require_version(version)
    if flags != 0 or reserved != 0:
        raise DisplayProtocolError("frame chunk flags and reserved field must be zero")
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
    if not 1 <= chunk_data_bytes <= DEFAULT_CHUNK_DATA_BYTES:
        raise DisplayProtocolError(
            f"chunk_data_bytes must be between 1 and {DEFAULT_CHUNK_DATA_BYTES}"
        )
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
    if value.structure_version != STRUCTURE_VERSION:
        raise DisplayProtocolError("unsupported device info structure version")
    for field_name in ("firmware_major", "firmware_minor", "firmware_patch"):
        _validate_u8(getattr(value, field_name), field_name)
    _validate_u16(int(value.capabilities), "capabilities")
    for field_name in (
        "width",
        "height",
        "frame_bytes",
        "max_chunk_payload",
        "status_size",
        "begin_frame_size",
        "preferred_att_mtu",
    ):
        _validate_u16(getattr(value, field_name), field_name)
    _validate_u32(value.device_id_tail, "device_id_tail")
    _validate_u32(value.build_id, "build_id")
    return DEVICE_INFO_STRUCT.pack(
        value.protocol_version,
        value.structure_version,
        value.firmware_major,
        value.firmware_minor,
        value.firmware_patch,
        0,
        int(value.capabilities),
        value.width,
        value.height,
        value.frame_bytes,
        value.max_chunk_payload,
        value.status_size,
        value.begin_frame_size,
        value.preferred_att_mtu,
        0,
        value.device_id_tail,
        value.build_id,
    )


def decode_device_info(data: bytes) -> DeviceInfo:
    if len(data) != DEVICE_INFO_STRUCT.size:
        raise DisplayProtocolError("invalid DEVICE_INFO length")
    values = DEVICE_INFO_STRUCT.unpack(data)
    _require_version(values[0])
    if values[1] != STRUCTURE_VERSION:
        raise DisplayProtocolError(
            f"unsupported DEVICE_INFO structure version {values[1]}"
        )
    if values[5] != 0 or values[14] != 0:
        raise DisplayProtocolError("DEVICE_INFO reserved fields must be zero")
    return DeviceInfo(
        protocol_version=values[0],
        structure_version=values[1],
        firmware_major=values[2],
        firmware_minor=values[3],
        firmware_patch=values[4],
        capabilities=DeviceCapability(values[6]),
        width=values[7],
        height=values[8],
        frame_bytes=values[9],
        max_chunk_payload=values[10],
        status_size=values[11],
        begin_frame_size=values[12],
        preferred_att_mtu=values[13],
        device_id_tail=values[15],
        build_id=values[16],
    )


def encode_status(value: DisplayStatus) -> bytes:
    if value.protocol_version != PROTOCOL_VERSION:
        raise DisplayProtocolError("unsupported status protocol version")
    for field_name in ("active_frame_id", "last_frame_id"):
        _validate_u32(getattr(value, field_name), field_name)
    for field_name in (
        "received_bytes",
        "changed_x",
        "changed_y",
        "changed_width",
        "changed_height",
        "partial_refresh_count",
        "battery_mv",
    ):
        _validate_u16(getattr(value, field_name), field_name)
    _validate_u8(value.battery_percent, "battery_percent")
    for field_name in (
        "refresh_ms",
        "milliseconds_since_last_full",
        "pending_replaced_count",
        "crc_error_count",
        "timeout_count",
        "display_failure_count",
        "free_heap_bytes",
        "status_sequence",
    ):
        _validate_u32(getattr(value, field_name), field_name)
    return STATUS_STRUCT.pack(
        value.protocol_version,
        int(value.state),
        int(value.last_error),
        int(value.actual_refresh_mode),
        value.active_frame_id,
        value.last_frame_id,
        value.received_bytes,
        int(value.flags),
        value.changed_x,
        value.changed_y,
        value.changed_width,
        value.changed_height,
        value.refresh_ms,
        value.partial_refresh_count,
        value.battery_percent,
        int(value.owner),
        value.milliseconds_since_last_full,
        value.battery_mv,
        0,
        value.pending_replaced_count,
        value.crc_error_count,
        value.timeout_count,
        value.display_failure_count,
        value.free_heap_bytes,
        value.status_sequence,
    )


def decode_status(data: bytes) -> DisplayStatus:
    if len(data) != STATUS_STRUCT.size:
        raise DisplayProtocolError("invalid STATUS length")
    values = STATUS_STRUCT.unpack(data)
    _require_version(values[0])
    if values[18] != 0:
        raise DisplayProtocolError("STATUS reserved field must be zero")
    try:
        state = DisplayStateCode(values[1])
        last_error = DisplayErrorCode(values[2])
        refresh_mode = RefreshMode(values[3])
        owner = DisplayOwner(values[15])
    except ValueError as exc:
        raise DisplayProtocolError("STATUS contains an unknown enum value") from exc
    return DisplayStatus(
        protocol_version=values[0],
        state=state,
        last_error=last_error,
        actual_refresh_mode=refresh_mode,
        active_frame_id=values[4],
        last_frame_id=values[5],
        received_bytes=values[6],
        flags=StatusFlag(values[7]),
        changed_x=values[8],
        changed_y=values[9],
        changed_width=values[10],
        changed_height=values[11],
        refresh_ms=values[12],
        partial_refresh_count=values[13],
        battery_percent=values[14],
        owner=owner,
        milliseconds_since_last_full=values[16],
        battery_mv=values[17],
        pending_replaced_count=values[19],
        crc_error_count=values[20],
        timeout_count=values[21],
        display_failure_count=values[22],
        free_heap_bytes=values[23],
        status_sequence=values[24],
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


def _validate_u16(value: int, name: str) -> None:
    if not 0 <= value <= 0xFFFF:
        raise DisplayProtocolError(f"{name} is outside uint16 range")


def _validate_u32(value: int, name: str) -> None:
    if not 0 <= value <= 0xFFFFFFFF:
        raise DisplayProtocolError(f"{name} is outside uint32 range")


def _validate_u64(value: int, name: str) -> None:
    if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise DisplayProtocolError(f"{name} is outside uint64 range")
