"""Optional V0 acquisition-control extension. Existing PC capture is unchanged."""
from dataclasses import dataclass
import struct
from .protocol import HEADER_STRUCT, MAGIC, PROTOCOL_VERSION, crc16_ccitt_false

CONTROL_TYPE = 7
ACK_TYPE = 8
PAYLOAD = struct.Struct("<IBBH")


@dataclass(frozen=True)
class AcquisitionAck:
    request_id: int
    active: bool
    result: int


def encode_command(request_id: int, operation: int) -> bytes:
    if operation not in (0, 1, 2):
        raise ValueError("operation must be STOP=0, START=1 or QUERY=2")
    packet = HEADER_STRUCT.pack(MAGIC, PROTOCOL_VERSION, CONTROL_TYPE, 8, 0, 0)
    packet += PAYLOAD.pack(request_id, operation, 0, 0)
    return packet + struct.pack("<H", crc16_ccitt_false(packet))


def decode_ack(packet: bytes) -> AcquisitionAck:
    if len(packet) != 28:
        raise ValueError("ACK length must be 28")
    magic, version, kind, size, _, _ = HEADER_STRUCT.unpack_from(packet)
    if (magic, version, kind, size) != (MAGIC, PROTOCOL_VERSION, ACK_TYPE, 8):
        raise ValueError("invalid ACK header")
    if crc16_ccitt_false(packet[:-2]) != struct.unpack_from("<H", packet, 26)[0]:
        raise ValueError("invalid ACK CRC")
    request_id, active, result, reserved = PAYLOAD.unpack_from(packet, 18)
    if active not in (0, 1) or reserved != 0 or result not in (0, 1):
        raise ValueError("invalid ACK payload")
    return AcquisitionAck(request_id, bool(active), result)
