from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct
from typing import ClassVar

MAGIC = 0x4E53
MAGIC_BYTES = struct.pack("<H", MAGIC)
PROTOCOL_VERSION = 1


class PacketType(IntEnum):
    ECG_BATCH = 1
    IMU_BATCH = 2
    DEVICE_STATUS = 3
    VOICE_TEXT_CHUNK = 4
    VOICE_STATUS = 5
    VOICE_TEXT_ACK = 6


PACKET_TYPE_ECG_BATCH = PacketType.ECG_BATCH
PACKET_TYPE_IMU_BATCH = PacketType.IMU_BATCH
PACKET_TYPE_DEVICE_STATUS = PacketType.DEVICE_STATUS
PACKET_TYPE_VOICE_TEXT_CHUNK = PacketType.VOICE_TEXT_CHUNK
PACKET_TYPE_VOICE_STATUS = PacketType.VOICE_STATUS
PACKET_TYPE_VOICE_TEXT_ACK = PacketType.VOICE_TEXT_ACK
PACKET_TYPE_ECG = PACKET_TYPE_ECG_BATCH

ECG_SAMPLE_RATE_HZ = 500
ECG_SAMPLE_COUNT = 20
IMU_SAMPLE_RATE_HZ = 50
IMU_SAMPLE_COUNT = 2

FLAG_LO_MINUS = 1 << 0
FLAG_LO_PLUS = 1 << 1
FLAG_ADC_CLIPPING = 1 << 2
FLAG_SAMPLE_LATE = 1 << 3
FLAG_SAMPLE_MISSED = 1 << 4
FLAG_SAMPLE_QUEUE_OVERFLOW = 1 << 5
FLAG_TRANSPORT_OVERFLOW = 1 << 6
FLAG_HISTORICAL_DATA = 1 << 7

STATUS_MPU6050_ONLINE = 1 << 0
STATUS_OLED_ONLINE = 1 << 1
STATUS_BT_CONNECTED = 1 << 2
STATUS_SAMPLING_ACTIVE = 1 << 3
STATUS_SPP_CONGESTED = 1 << 4

HEADER_STRUCT = struct.Struct("<HBBHIQ")
ECG_PREFIX_STRUCT = struct.Struct("<IHBB")
IMU_PREFIX_STRUCT = struct.Struct("<IHBB")
IMU_POINT_STRUCT = struct.Struct("<hhhhhh")
DEVICE_STATUS_STRUCT = struct.Struct("<BBBBBBHIIIIII")
VOICE_TEXT_CHUNK_PREFIX_STRUCT = struct.Struct("<QBBBB")
VOICE_STATUS_STRUCT = struct.Struct("<BBHIIII")
VOICE_TEXT_ACK_STRUCT = struct.Struct("<Q")
CRC_STRUCT = struct.Struct("<H")

VOICE_TEXT_CHUNK_DATA_SIZE = 36
VOICE_TEXT_MAX_BYTES = 512
VOICE_TEXT_FLAG_FINAL = 1 << 0
VOICE_TEXT_FLAG_RETRANSMIT = 1 << 1

HEADER_SIZE = HEADER_STRUCT.size
ECG_PAYLOAD_SIZE = ECG_PREFIX_STRUCT.size + (ECG_SAMPLE_COUNT * 2)
IMU_PAYLOAD_SIZE = IMU_PREFIX_STRUCT.size + (IMU_SAMPLE_COUNT * IMU_POINT_STRUCT.size)
DEVICE_STATUS_PAYLOAD_SIZE = DEVICE_STATUS_STRUCT.size
VOICE_TEXT_CHUNK_PAYLOAD_SIZE = (
    VOICE_TEXT_CHUNK_PREFIX_STRUCT.size + VOICE_TEXT_CHUNK_DATA_SIZE
)
VOICE_STATUS_PAYLOAD_SIZE = VOICE_STATUS_STRUCT.size
VOICE_TEXT_ACK_PAYLOAD_SIZE = VOICE_TEXT_ACK_STRUCT.size
ECG_PACKET_SIZE = HEADER_SIZE + ECG_PAYLOAD_SIZE + CRC_STRUCT.size
IMU_PACKET_SIZE = HEADER_SIZE + IMU_PAYLOAD_SIZE + CRC_STRUCT.size
DEVICE_STATUS_PACKET_SIZE = HEADER_SIZE + DEVICE_STATUS_PAYLOAD_SIZE + CRC_STRUCT.size
MAX_PAYLOAD_SIZE = max(
    ECG_PAYLOAD_SIZE,
    IMU_PAYLOAD_SIZE,
    DEVICE_STATUS_PAYLOAD_SIZE,
    VOICE_TEXT_CHUNK_PAYLOAD_SIZE,
    VOICE_STATUS_PAYLOAD_SIZE,
    VOICE_TEXT_ACK_PAYLOAD_SIZE,
)
MAX_PACKET_SIZE = HEADER_SIZE + MAX_PAYLOAD_SIZE + CRC_STRUCT.size


class ProtocolError(ValueError):
    """Raised when bytes cannot be represented by the V0 wire format."""


@dataclass(frozen=True, slots=True)
class PacketHeader:
    packet_type: int
    payload_length: int
    packet_sequence: int
    timestamp_us: int
    magic: int = MAGIC
    protocol_version: int = PROTOCOL_VERSION

    struct: ClassVar[struct.Struct] = HEADER_STRUCT

    def to_bytes(self) -> bytes:
        return self.struct.pack(
            self.magic,
            self.protocol_version,
            int(self.packet_type),
            self.payload_length,
            self.packet_sequence,
            self.timestamp_us,
        )


@dataclass(frozen=True, slots=True)
class EcgPayload:
    first_sample_index: int
    samples: tuple[int, ...]
    flags: int = 0
    sample_rate_hz: int = ECG_SAMPLE_RATE_HZ

    def to_bytes(self) -> bytes:
        if len(self.samples) != ECG_SAMPLE_COUNT:
            raise ProtocolError(f"ECG packets must contain {ECG_SAMPLE_COUNT} samples")
        for sample in self.samples:
            if not 0 <= sample <= 0xFFFF:
                raise ProtocolError(f"ECG sample out of uint16 range: {sample}")
        _validate_u8(self.flags, "flags")

        prefix = ECG_PREFIX_STRUCT.pack(
            self.first_sample_index,
            self.sample_rate_hz,
            len(self.samples),
            self.flags,
        )
        return prefix + struct.pack(f"<{ECG_SAMPLE_COUNT}H", *self.samples)


@dataclass(frozen=True, slots=True)
class ImuPoint:
    ax: int
    ay: int
    az: int
    gx: int
    gy: int
    gz: int

    def to_bytes(self) -> bytes:
        values = (self.ax, self.ay, self.az, self.gx, self.gy, self.gz)
        for value in values:
            if not -0x8000 <= value <= 0x7FFF:
                raise ProtocolError(f"IMU value out of int16 range: {value}")
        return IMU_POINT_STRUCT.pack(*values)


@dataclass(frozen=True, slots=True)
class ImuPayload:
    first_sample_index: int
    samples: tuple[ImuPoint, ...]
    flags: int = 0
    sample_rate_hz: int = IMU_SAMPLE_RATE_HZ

    def to_bytes(self) -> bytes:
        if len(self.samples) != IMU_SAMPLE_COUNT:
            raise ProtocolError(f"IMU packets must contain {IMU_SAMPLE_COUNT} samples")
        _validate_u8(self.flags, "flags")
        prefix = IMU_PREFIX_STRUCT.pack(
            self.first_sample_index,
            self.sample_rate_hz,
            len(self.samples),
            self.flags,
        )
        return prefix + b"".join(point.to_bytes() for point in self.samples)


@dataclass(frozen=True, slots=True)
class DeviceStatusPayload:
    lead_off_flags: int
    sensor_status_flags: int
    ecg_buffer_usage_percent: int
    imu_buffer_usage_percent: int
    spp_queue_usage_percent: int
    status_flags: int
    error_count: int
    ecg_ring_overflow_count: int
    imu_ring_overflow_count: int
    spp_queue_overflow_count: int
    transport_drop_count: int
    i2c_error_count: int

    def to_bytes(self) -> bytes:
        for name in (
            "lead_off_flags",
            "sensor_status_flags",
            "ecg_buffer_usage_percent",
            "imu_buffer_usage_percent",
            "spp_queue_usage_percent",
        ):
            _validate_u8(getattr(self, name), name)
        if not 0 <= self.status_flags <= 0xFFFF:
            raise ProtocolError(f"status_flags out of uint16 range: {self.status_flags}")
        counters = (
            self.error_count,
            self.ecg_ring_overflow_count,
            self.imu_ring_overflow_count,
            self.spp_queue_overflow_count,
            self.transport_drop_count,
            self.i2c_error_count,
        )
        for counter in counters:
            if not 0 <= counter <= 0xFFFFFFFF:
                raise ProtocolError(f"status counter out of uint32 range: {counter}")
        return DEVICE_STATUS_STRUCT.pack(
            self.lead_off_flags,
            self.sensor_status_flags,
            self.ecg_buffer_usage_percent,
            self.imu_buffer_usage_percent,
            self.spp_queue_usage_percent,
            0,
            self.status_flags,
            *counters,
        )


@dataclass(frozen=True, slots=True)
class VoiceTextChunkPayload:
    utterance_id: int
    chunk_index: int
    chunk_count: int
    text_bytes: bytes
    flags: int = VOICE_TEXT_FLAG_FINAL

    def to_bytes(self) -> bytes:
        if not 0 <= self.utterance_id <= 0xFFFFFFFFFFFFFFFF:
            raise ProtocolError(f"utterance_id out of uint64 range: {self.utterance_id}")
        if not 1 <= self.chunk_count <= 0xFF:
            raise ProtocolError(f"chunk_count out of range: {self.chunk_count}")
        if not 0 <= self.chunk_index < self.chunk_count:
            raise ProtocolError(
                f"chunk_index {self.chunk_index} outside chunk_count {self.chunk_count}"
            )
        if not 0 < len(self.text_bytes) <= VOICE_TEXT_CHUNK_DATA_SIZE:
            raise ProtocolError(
                f"voice text chunk must contain 1..{VOICE_TEXT_CHUNK_DATA_SIZE} bytes"
            )
        _validate_u8(self.flags, "flags")
        prefix = VOICE_TEXT_CHUNK_PREFIX_STRUCT.pack(
            self.utterance_id,
            self.chunk_index,
            self.chunk_count,
            len(self.text_bytes),
            self.flags,
        )
        return prefix + self.text_bytes.ljust(VOICE_TEXT_CHUNK_DATA_SIZE, b"\x00")


@dataclass(frozen=True, slots=True)
class VoiceStatusPayload:
    state: int
    flags: int
    last_error: int
    wake_count: int
    asr_success_count: int
    asr_error_count: int
    text_drop_count: int

    def to_bytes(self) -> bytes:
        _validate_u8(self.state, "state")
        _validate_u8(self.flags, "flags")
        if not 0 <= self.last_error <= 0xFFFF:
            raise ProtocolError(f"last_error out of uint16 range: {self.last_error}")
        counters = (
            self.wake_count,
            self.asr_success_count,
            self.asr_error_count,
            self.text_drop_count,
        )
        for counter in counters:
            if not 0 <= counter <= 0xFFFFFFFF:
                raise ProtocolError(f"voice status counter out of uint32 range: {counter}")
        return VOICE_STATUS_STRUCT.pack(
            self.state,
            self.flags,
            self.last_error,
            *counters,
        )


@dataclass(frozen=True, slots=True)
class VoiceTextAckPayload:
    utterance_id: int

    def to_bytes(self) -> bytes:
        if not 0 <= self.utterance_id <= 0xFFFFFFFFFFFFFFFF:
            raise ProtocolError(f"utterance_id out of uint64 range: {self.utterance_id}")
        return VOICE_TEXT_ACK_STRUCT.pack(self.utterance_id)


@dataclass(frozen=True, slots=True)
class ParsedPacket:
    header: PacketHeader
    payload: (
        EcgPayload
        | ImuPayload
        | DeviceStatusPayload
        | VoiceTextChunkPayload
        | VoiceStatusPayload
        | VoiceTextAckPayload
    )
    crc16_ccitt_false: int
    raw: bytes


@dataclass(slots=True)
class ParserStats:
    bytes_discarded: int = 0
    packets_ok: int = 0
    crc_errors: int = 0
    length_errors: int = 0
    version_errors: int = 0
    sequence_gap_count: int = 0
    packets_lost: int = 0
    last_sequence: int | None = None


def _validate_u8(value: int, name: str) -> None:
    if not 0 <= value <= 0xFF:
        raise ProtocolError(f"{name} out of uint8 range: {value}")


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _encode_packet(
    *,
    packet_type: PacketType,
    packet_sequence: int,
    timestamp_us: int,
    payload: bytes,
) -> bytes:
    header = PacketHeader(
        packet_type=int(packet_type),
        payload_length=len(payload),
        packet_sequence=packet_sequence,
        timestamp_us=timestamp_us,
    ).to_bytes()
    body = header + payload
    return body + CRC_STRUCT.pack(crc16_ccitt_false(body))


def encode_ecg_packet(
    *,
    packet_sequence: int,
    timestamp_us: int,
    first_sample_index: int,
    samples: tuple[int, ...],
    flags: int = 0,
) -> bytes:
    return _encode_packet(
        packet_type=PacketType.ECG_BATCH,
        packet_sequence=packet_sequence,
        timestamp_us=timestamp_us,
        payload=EcgPayload(
            first_sample_index=first_sample_index,
            samples=samples,
            flags=flags,
        ).to_bytes(),
    )


def encode_imu_packet(
    *,
    packet_sequence: int,
    timestamp_us: int,
    first_sample_index: int,
    samples: tuple[ImuPoint, ...],
    flags: int = 0,
) -> bytes:
    return _encode_packet(
        packet_type=PacketType.IMU_BATCH,
        packet_sequence=packet_sequence,
        timestamp_us=timestamp_us,
        payload=ImuPayload(
            first_sample_index=first_sample_index,
            samples=samples,
            flags=flags,
        ).to_bytes(),
    )


def encode_device_status_packet(
    *,
    packet_sequence: int,
    timestamp_us: int,
    status: DeviceStatusPayload,
) -> bytes:
    return _encode_packet(
        packet_type=PacketType.DEVICE_STATUS,
        packet_sequence=packet_sequence,
        timestamp_us=timestamp_us,
        payload=status.to_bytes(),
    )


def encode_voice_text_chunk_packet(
    *,
    packet_sequence: int,
    timestamp_us: int,
    chunk: VoiceTextChunkPayload,
) -> bytes:
    return _encode_packet(
        packet_type=PacketType.VOICE_TEXT_CHUNK,
        packet_sequence=packet_sequence,
        timestamp_us=timestamp_us,
        payload=chunk.to_bytes(),
    )


def encode_voice_status_packet(
    *,
    packet_sequence: int,
    timestamp_us: int,
    status: VoiceStatusPayload,
) -> bytes:
    return _encode_packet(
        packet_type=PacketType.VOICE_STATUS,
        packet_sequence=packet_sequence,
        timestamp_us=timestamp_us,
        payload=status.to_bytes(),
    )


def encode_voice_text_ack_packet(
    *,
    packet_sequence: int,
    timestamp_us: int,
    utterance_id: int,
) -> bytes:
    return _encode_packet(
        packet_type=PacketType.VOICE_TEXT_ACK,
        packet_sequence=packet_sequence,
        timestamp_us=timestamp_us,
        payload=VoiceTextAckPayload(utterance_id=utterance_id).to_bytes(),
    )


def decode_header(packet: bytes) -> PacketHeader:
    if len(packet) < HEADER_SIZE:
        raise ProtocolError(f"packet too short for V0 header: {len(packet)}")
    magic, version, packet_type, payload_length, sequence, timestamp_us = HEADER_STRUCT.unpack(
        packet[:HEADER_SIZE]
    )
    if magic != MAGIC:
        raise ProtocolError(f"bad magic 0x{magic:04x}")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {version}")
    return PacketHeader(
        packet_type=packet_type,
        payload_length=payload_length,
        packet_sequence=sequence,
        timestamp_us=timestamp_us,
        magic=magic,
        protocol_version=version,
    )


def decode_packet(packet: bytes) -> ParsedPacket:
    header = decode_header(packet)
    expected_length = HEADER_SIZE + header.payload_length + CRC_STRUCT.size
    if len(packet) != expected_length:
        raise ProtocolError(f"packet length {len(packet)} != declared {expected_length}")
    received_crc = CRC_STRUCT.unpack(packet[-CRC_STRUCT.size :])[0]
    calculated_crc = crc16_ccitt_false(packet[:-CRC_STRUCT.size])
    if received_crc != calculated_crc:
        raise ProtocolError(
            f"CRC mismatch got=0x{received_crc:04x} expected=0x{calculated_crc:04x}"
        )

    payload_bytes = packet[HEADER_SIZE:-CRC_STRUCT.size]
    if header.packet_type == PacketType.ECG_BATCH:
        payload = decode_ecg_payload(payload_bytes)
    elif header.packet_type == PacketType.IMU_BATCH:
        payload = decode_imu_payload(payload_bytes)
    elif header.packet_type == PacketType.DEVICE_STATUS:
        payload = decode_device_status_payload(payload_bytes)
    elif header.packet_type == PacketType.VOICE_TEXT_CHUNK:
        payload = decode_voice_text_chunk_payload(payload_bytes)
    elif header.packet_type == PacketType.VOICE_STATUS:
        payload = decode_voice_status_payload(payload_bytes)
    elif header.packet_type == PacketType.VOICE_TEXT_ACK:
        payload = decode_voice_text_ack_payload(payload_bytes)
    else:
        raise ProtocolError(f"unsupported packet type {header.packet_type}")

    return ParsedPacket(
        header=header,
        payload=payload,
        crc16_ccitt_false=received_crc,
        raw=packet,
    )


def decode_ecg_payload(payload: bytes) -> EcgPayload:
    if len(payload) != ECG_PAYLOAD_SIZE:
        raise ProtocolError(f"ECG payload length must be {ECG_PAYLOAD_SIZE}, got {len(payload)}")
    first_sample_index, sample_rate_hz, sample_count, flags = ECG_PREFIX_STRUCT.unpack(
        payload[: ECG_PREFIX_STRUCT.size]
    )
    if sample_count != ECG_SAMPLE_COUNT:
        raise ProtocolError(f"ECG sample_count must be {ECG_SAMPLE_COUNT}, got {sample_count}")
    samples = struct.unpack(
        f"<{ECG_SAMPLE_COUNT}H",
        payload[ECG_PREFIX_STRUCT.size :],
    )
    return EcgPayload(
        first_sample_index=first_sample_index,
        sample_rate_hz=sample_rate_hz,
        samples=tuple(samples),
        flags=flags,
    )


def decode_imu_payload(payload: bytes) -> ImuPayload:
    if len(payload) != IMU_PAYLOAD_SIZE:
        raise ProtocolError(f"IMU payload length must be {IMU_PAYLOAD_SIZE}, got {len(payload)}")
    first_sample_index, sample_rate_hz, sample_count, flags = IMU_PREFIX_STRUCT.unpack(
        payload[: IMU_PREFIX_STRUCT.size]
    )
    if sample_count != IMU_SAMPLE_COUNT:
        raise ProtocolError(f"IMU sample_count must be {IMU_SAMPLE_COUNT}, got {sample_count}")
    samples: list[ImuPoint] = []
    offset = IMU_PREFIX_STRUCT.size
    for _ in range(IMU_SAMPLE_COUNT):
        samples.append(ImuPoint(*IMU_POINT_STRUCT.unpack(payload[offset : offset + IMU_POINT_STRUCT.size])))
        offset += IMU_POINT_STRUCT.size
    return ImuPayload(
        first_sample_index=first_sample_index,
        sample_rate_hz=sample_rate_hz,
        samples=tuple(samples),
        flags=flags,
    )


def decode_device_status_payload(payload: bytes) -> DeviceStatusPayload:
    if len(payload) != DEVICE_STATUS_PAYLOAD_SIZE:
        raise ProtocolError(
            f"device status payload length must be {DEVICE_STATUS_PAYLOAD_SIZE}, got {len(payload)}"
        )
    (
        lead_off_flags,
        sensor_status_flags,
        ecg_usage,
        imu_usage,
        spp_usage,
        _reserved,
        status_flags,
        error_count,
        ecg_overflow,
        imu_overflow,
        spp_overflow,
        transport_drop,
        i2c_error,
    ) = DEVICE_STATUS_STRUCT.unpack(payload)
    return DeviceStatusPayload(
        lead_off_flags=lead_off_flags,
        sensor_status_flags=sensor_status_flags,
        ecg_buffer_usage_percent=ecg_usage,
        imu_buffer_usage_percent=imu_usage,
        spp_queue_usage_percent=spp_usage,
        status_flags=status_flags,
        error_count=error_count,
        ecg_ring_overflow_count=ecg_overflow,
        imu_ring_overflow_count=imu_overflow,
        spp_queue_overflow_count=spp_overflow,
        transport_drop_count=transport_drop,
        i2c_error_count=i2c_error,
    )


def decode_voice_text_chunk_payload(payload: bytes) -> VoiceTextChunkPayload:
    if len(payload) != VOICE_TEXT_CHUNK_PAYLOAD_SIZE:
        raise ProtocolError(
            "voice text chunk payload length must be "
            f"{VOICE_TEXT_CHUNK_PAYLOAD_SIZE}, got {len(payload)}"
        )
    utterance_id, chunk_index, chunk_count, text_length, flags = (
        VOICE_TEXT_CHUNK_PREFIX_STRUCT.unpack(
            payload[: VOICE_TEXT_CHUNK_PREFIX_STRUCT.size]
        )
    )
    if chunk_count == 0 or chunk_index >= chunk_count:
        raise ProtocolError(
            f"invalid voice chunk index/count: {chunk_index}/{chunk_count}"
        )
    if not 0 < text_length <= VOICE_TEXT_CHUNK_DATA_SIZE:
        raise ProtocolError(f"invalid voice chunk text_length: {text_length}")
    text_bytes = payload[
        VOICE_TEXT_CHUNK_PREFIX_STRUCT.size :
        VOICE_TEXT_CHUNK_PREFIX_STRUCT.size + text_length
    ]
    return VoiceTextChunkPayload(
        utterance_id=utterance_id,
        chunk_index=chunk_index,
        chunk_count=chunk_count,
        text_bytes=text_bytes,
        flags=flags,
    )


def decode_voice_status_payload(payload: bytes) -> VoiceStatusPayload:
    if len(payload) != VOICE_STATUS_PAYLOAD_SIZE:
        raise ProtocolError(
            f"voice status payload length must be {VOICE_STATUS_PAYLOAD_SIZE}, got {len(payload)}"
        )
    return VoiceStatusPayload(*VOICE_STATUS_STRUCT.unpack(payload))


def decode_voice_text_ack_payload(payload: bytes) -> VoiceTextAckPayload:
    if len(payload) != VOICE_TEXT_ACK_PAYLOAD_SIZE:
        raise ProtocolError(
            f"voice ACK payload length must be {VOICE_TEXT_ACK_PAYLOAD_SIZE}, got {len(payload)}"
        )
    return VoiceTextAckPayload(utterance_id=VOICE_TEXT_ACK_STRUCT.unpack(payload)[0])


class PacketParser:
    """Streaming V0 parser for arbitrary serial chunks."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.stats = ParserStats()

    def feed(self, chunk: bytes) -> list[ParsedPacket]:
        if not chunk:
            return []
        self._buffer.extend(chunk)
        packets: list[ParsedPacket] = []

        while len(self._buffer) >= HEADER_SIZE:
            magic_offset = self._buffer.find(MAGIC_BYTES)
            if magic_offset < 0:
                keep = 1 if self._buffer[-1:] == MAGIC_BYTES[:1] else 0
                self.stats.bytes_discarded += len(self._buffer) - keep
                del self._buffer[: len(self._buffer) - keep]
                break
            if magic_offset > 0:
                self.stats.bytes_discarded += magic_offset
                del self._buffer[:magic_offset]
            if len(self._buffer) < HEADER_SIZE:
                break

            try:
                header = decode_header(bytes(self._buffer[:HEADER_SIZE]))
            except ProtocolError:
                self.stats.version_errors += 1
                del self._buffer[0]
                continue

            if header.payload_length > MAX_PAYLOAD_SIZE:
                self.stats.length_errors += 1
                del self._buffer[0]
                continue

            total_length = HEADER_SIZE + header.payload_length + CRC_STRUCT.size
            if len(self._buffer) < total_length:
                break

            raw = bytes(self._buffer[:total_length])
            received_crc = CRC_STRUCT.unpack(raw[-CRC_STRUCT.size :])[0]
            calculated_crc = crc16_ccitt_false(raw[:-CRC_STRUCT.size])
            if received_crc != calculated_crc:
                self.stats.crc_errors += 1
                # Keep the remaining bytes so a valid magic sequence embedded
                # after a truncated BLE notification can be found immediately.
                # Dropping the whole apparent packet would also discard the
                # prefix of the next valid packet.
                del self._buffer[0]
                continue

            del self._buffer[:total_length]
            try:
                parsed = decode_packet(raw)
            except ProtocolError:
                self.stats.length_errors += 1
                continue
            self._record_sequence(parsed.header.packet_sequence)
            self.stats.packets_ok += 1
            packets.append(parsed)

        return packets

    def _record_sequence(self, sequence: int) -> None:
        last = self.stats.last_sequence
        if last is not None:
            expected = (last + 1) & 0xFFFFFFFF
            if sequence != expected:
                self.stats.sequence_gap_count += 1
                forward_distance = (sequence - expected) & 0xFFFFFFFF
                if forward_distance < 0x80000000:
                    self.stats.packets_lost += forward_distance
        self.stats.last_sequence = sequence
