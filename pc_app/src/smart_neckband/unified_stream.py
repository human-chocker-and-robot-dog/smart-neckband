from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .mic_capture_protocol import (
    CRC as MIC_CRC,
    FRAME_TYPE_AUDIO,
    FRAME_TYPE_STATUS,
    FRAME_TYPE_WAKE,
    HEADER as MIC_HEADER,
    MAGIC as MIC_MAGIC,
    MAX_PAYLOAD_BYTES as MIC_MAX_PAYLOAD_BYTES,
    VERSION as MIC_VERSION,
    crc16_ccitt_false,
)
from .protocol import (
    CRC_STRUCT as V0_CRC,
    HEADER_SIZE as V0_HEADER_SIZE,
    HEADER_STRUCT as V0_HEADER,
    MAGIC_BYTES as V0_MAGIC,
    MAX_PAYLOAD_SIZE as V0_MAX_PAYLOAD_SIZE,
    PROTOCOL_VERSION as V0_VERSION,
)


class UnifiedFrameKind(str, Enum):
    V0 = "v0"
    MIC1 = "mic1"


@dataclass(frozen=True, slots=True)
class UnifiedWireFrame:
    kind: UnifiedFrameKind
    data: bytes


@dataclass(slots=True)
class UnifiedStreamStats:
    v0_frames: int = 0
    mic1_frames: int = 0
    crc_errors: int = 0
    v0_crc_errors: int = 0
    mic1_crc_errors: int = 0
    malformed_headers: int = 0
    discarded_bytes: int = 0


class UnifiedStreamDemux:
    """Split one BLE byte stream into validated V0 and MIC1 wire frames."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.stats = UnifiedStreamStats()

    def reset(self) -> None:
        self._buffer.clear()
        self.stats = UnifiedStreamStats()

    def feed(self, chunk: bytes | bytearray | memoryview) -> list[UnifiedWireFrame]:
        if chunk:
            self._buffer.extend(chunk)

        frames: list[UnifiedWireFrame] = []
        while self._buffer:
            magic_offset = self._find_next_magic()
            if magic_offset < 0:
                keep = self._partial_magic_suffix_length()
                self._discard(len(self._buffer) - keep)
                break
            if magic_offset:
                self._discard(magic_offset)

            if self._buffer.startswith(V0_MAGIC):
                result = self._extract_v0()
            else:
                result = self._extract_mic1()
            if result is None:
                break
            if result is False:
                continue
            frames.append(result)
        return frames

    def _extract_v0(self) -> UnifiedWireFrame | bool | None:
        if len(self._buffer) < V0_HEADER_SIZE:
            return None
        magic, version, _packet_type, payload_length, _sequence, _timestamp = (
            V0_HEADER.unpack_from(self._buffer)
        )
        if magic != int.from_bytes(V0_MAGIC, "little") or version != V0_VERSION:
            self._reject_header()
            return False
        if payload_length > V0_MAX_PAYLOAD_SIZE:
            self._reject_header()
            return False

        frame_length = V0_HEADER_SIZE + payload_length + V0_CRC.size
        if len(self._buffer) < frame_length:
            return None
        return self._finish_frame(UnifiedFrameKind.V0, frame_length)

    def _extract_mic1(self) -> UnifiedWireFrame | bool | None:
        if len(self._buffer) < MIC_HEADER.size:
            return None
        (
            magic,
            version,
            frame_type,
            _encoding,
            _flags,
            _sequence,
            sample_rate,
            _first_sample_index,
            _sample_count,
            payload_length,
        ) = MIC_HEADER.unpack_from(self._buffer)
        if (
            magic != MIC_MAGIC
            or version != MIC_VERSION
            or frame_type not in (FRAME_TYPE_AUDIO, FRAME_TYPE_STATUS, FRAME_TYPE_WAKE)
            or sample_rate != 16_000
            or payload_length > MIC_MAX_PAYLOAD_BYTES
        ):
            self._reject_header()
            return False

        frame_length = MIC_HEADER.size + payload_length + MIC_CRC.size
        if len(self._buffer) < frame_length:
            return None
        return self._finish_frame(UnifiedFrameKind.MIC1, frame_length)

    def _finish_frame(
        self, kind: UnifiedFrameKind, frame_length: int
    ) -> UnifiedWireFrame | bool:
        expected_crc = int.from_bytes(
            self._buffer[frame_length - 2 : frame_length], "little"
        )
        actual_crc = crc16_ccitt_false(memoryview(self._buffer)[: frame_length - 2])
        if expected_crc != actual_crc:
            del self._buffer[0]
            self.stats.crc_errors += 1
            if kind is UnifiedFrameKind.V0:
                self.stats.v0_crc_errors += 1
            else:
                self.stats.mic1_crc_errors += 1
            self.stats.discarded_bytes += 1
            return False

        data = bytes(self._buffer[:frame_length])
        del self._buffer[:frame_length]
        if kind is UnifiedFrameKind.V0:
            self.stats.v0_frames += 1
        else:
            self.stats.mic1_frames += 1
        return UnifiedWireFrame(kind=kind, data=data)

    def _find_next_magic(self) -> int:
        offsets = (
            self._buffer.find(V0_MAGIC),
            self._buffer.find(MIC_MAGIC),
        )
        candidates = [offset for offset in offsets if offset >= 0]
        return min(candidates) if candidates else -1

    def _partial_magic_suffix_length(self) -> int:
        max_length = max(len(V0_MAGIC), len(MIC_MAGIC)) - 1
        for length in range(min(len(self._buffer), max_length), 0, -1):
            suffix = self._buffer[-length:]
            if V0_MAGIC.startswith(suffix) or MIC_MAGIC.startswith(suffix):
                return length
        return 0

    def _reject_header(self) -> None:
        del self._buffer[0]
        self.stats.malformed_headers += 1
        self.stats.discarded_bytes += 1

    def _discard(self, count: int) -> None:
        if count <= 0:
            return
        del self._buffer[:count]
        self.stats.discarded_bytes += count
