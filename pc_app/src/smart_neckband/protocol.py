from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import ClassVar

MAGIC = 0x4E53
PROTOCOL_VERSION = 1
PACKET_TYPE_ECG = 1

ECG_SAMPLE_RATE_HZ = 500
ECG_SAMPLE_COUNT = 20

FLAG_LO_MINUS = 1 << 0
FLAG_LO_PLUS = 1 << 1
FLAG_ADC_CLIPPING = 1 << 2
FLAG_SAMPLE_LATE = 1 << 3
FLAG_SAMPLE_MISSED = 1 << 4
FLAG_SAMPLE_QUEUE_OVERFLOW = 1 << 5
FLAG_TRANSPORT_OVERFLOW = 1 << 6
FLAG_HISTORICAL_DATA = 1 << 7

HEADER_STRUCT = struct.Struct("<HBBHIQ")
ECG_PREFIX_STRUCT = struct.Struct("<IHBB")
CRC_STRUCT = struct.Struct("<H")

HEADER_SIZE = HEADER_STRUCT.size
ECG_PAYLOAD_SIZE = ECG_PREFIX_STRUCT.size + (ECG_SAMPLE_COUNT * 2)
ECG_PACKET_SIZE = HEADER_SIZE + ECG_PAYLOAD_SIZE + CRC_STRUCT.size


class ProtocolError(ValueError):
    """Raised when a packet cannot be represented by the V0 wire format."""


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
            self.packet_type,
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
        if not 0 <= self.flags <= 0xFF:
            raise ProtocolError(f"flags out of uint8 range: {self.flags}")

        prefix = ECG_PREFIX_STRUCT.pack(
            self.first_sample_index,
            self.sample_rate_hz,
            len(self.samples),
            self.flags,
        )
        return prefix + struct.pack(f"<{ECG_SAMPLE_COUNT}H", *self.samples)


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


def encode_ecg_packet(
    *,
    packet_sequence: int,
    timestamp_us: int,
    first_sample_index: int,
    samples: tuple[int, ...],
    flags: int = 0,
) -> bytes:
    payload = EcgPayload(
        first_sample_index=first_sample_index,
        samples=samples,
        flags=flags,
    ).to_bytes()
    header = PacketHeader(
        packet_type=PACKET_TYPE_ECG,
        payload_length=len(payload),
        packet_sequence=packet_sequence,
        timestamp_us=timestamp_us,
    ).to_bytes()
    body = header + payload
    return body + CRC_STRUCT.pack(crc16_ccitt_false(body))


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
