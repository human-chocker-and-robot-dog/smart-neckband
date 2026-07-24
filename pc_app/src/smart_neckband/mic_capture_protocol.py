from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
import wave


MAGIC = b"MIC1"
VERSION = 1
FRAME_TYPE_AUDIO = 1
FRAME_TYPE_STATUS = 2
ENCODING_PCM16 = 1
ENCODING_PCM8 = 2
FLAG_CLIPPED = 1 << 0
FLAG_I2S_ERROR = 1 << 1
FLAG_TX_ERROR = 1 << 2

HEADER = struct.Struct("<4sBBBHIIQHH")
CRC = struct.Struct("<H")
STATUS_PAYLOAD = struct.Struct("<IIIIHBB")
MAX_PAYLOAD_BYTES = 1024


def crc16_ccitt_false(data: bytes | bytearray | memoryview) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True, slots=True)
class AudioFrame:
    encoding: int
    flags: int
    sequence: int
    sample_rate: int
    first_sample_index: int
    samples: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DeviceStatusFrame:
    encoding: int
    sequence: int
    sample_rate: int
    first_sample_index: int
    i2s_errors: int
    tx_errors: int
    clipped_frames: int
    reserved: int
    pcm_shift: int
    streaming: bool


MicFrame = AudioFrame | DeviceStatusFrame


@dataclass(slots=True)
class ParserStats:
    audio_frames: int = 0
    status_frames: int = 0
    samples: int = 0
    sequence_gaps: int = 0
    crc_errors: int = 0
    malformed_frames: int = 0
    discarded_bytes: int = 0


class MicFrameParser:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._next_audio_sequence: int | None = None
        self.stats = ParserStats()

    def reset(self) -> None:
        self._buffer.clear()
        self._next_audio_sequence = None
        self.stats = ParserStats()

    def feed(self, data: bytes | bytearray | memoryview) -> list[MicFrame]:
        self._buffer.extend(data)
        frames: list[MicFrame] = []
        while True:
            magic_at = self._buffer.find(MAGIC)
            if magic_at < 0:
                keep = min(len(self._buffer), len(MAGIC) - 1)
                discarded = len(self._buffer) - keep
                if discarded:
                    del self._buffer[:discarded]
                    self.stats.discarded_bytes += discarded
                break
            if magic_at:
                del self._buffer[:magic_at]
                self.stats.discarded_bytes += magic_at
            if len(self._buffer) < HEADER.size:
                break

            (
                _magic,
                version,
                frame_type,
                encoding,
                flags,
                sequence,
                sample_rate,
                first_sample_index,
                sample_count,
                payload_length,
            ) = HEADER.unpack_from(self._buffer)
            if (
                version != VERSION
                or frame_type not in (FRAME_TYPE_AUDIO, FRAME_TYPE_STATUS)
                or sample_rate != 16_000
                or payload_length > MAX_PAYLOAD_BYTES
            ):
                del self._buffer[0]
                self.stats.malformed_frames += 1
                continue

            frame_length = HEADER.size + payload_length + CRC.size
            if len(self._buffer) < frame_length:
                break
            expected_crc = CRC.unpack_from(self._buffer, frame_length - CRC.size)[0]
            actual_crc = crc16_ccitt_false(memoryview(self._buffer)[: frame_length - CRC.size])
            if expected_crc != actual_crc:
                del self._buffer[0]
                self.stats.crc_errors += 1
                continue

            payload = bytes(self._buffer[HEADER.size : HEADER.size + payload_length])
            del self._buffer[:frame_length]
            parsed = self._decode(
                frame_type=frame_type,
                encoding=encoding,
                flags=flags,
                sequence=sequence,
                sample_rate=sample_rate,
                first_sample_index=first_sample_index,
                sample_count=sample_count,
                payload=payload,
            )
            if parsed is not None:
                frames.append(parsed)
        return frames

    def _decode(
        self,
        *,
        frame_type: int,
        encoding: int,
        flags: int,
        sequence: int,
        sample_rate: int,
        first_sample_index: int,
        sample_count: int,
        payload: bytes,
    ) -> MicFrame | None:
        if frame_type == FRAME_TYPE_AUDIO:
            if encoding == ENCODING_PCM16 and len(payload) == sample_count * 2:
                samples = struct.unpack(f"<{sample_count}h", payload)
            elif encoding == ENCODING_PCM8 and len(payload) == sample_count:
                samples = tuple(value << 8 for value in struct.unpack(f"<{sample_count}b", payload))
            else:
                self.stats.malformed_frames += 1
                return None
            if self._next_audio_sequence is not None and sequence != self._next_audio_sequence:
                self.stats.sequence_gaps += (sequence - self._next_audio_sequence) & 0xFFFFFFFF
            self._next_audio_sequence = (sequence + 1) & 0xFFFFFFFF
            self.stats.audio_frames += 1
            self.stats.samples += sample_count
            return AudioFrame(
                encoding=encoding,
                flags=flags,
                sequence=sequence,
                sample_rate=sample_rate,
                first_sample_index=first_sample_index,
                samples=tuple(samples),
            )

        if len(payload) != STATUS_PAYLOAD.size or sample_count != 0:
            self.stats.malformed_frames += 1
            return None
        (
            i2s_errors,
            tx_errors,
            clipped_frames,
            reserved,
            pcm_shift,
            status_encoding,
            streaming,
        ) = STATUS_PAYLOAD.unpack(payload)
        self.stats.status_frames += 1
        return DeviceStatusFrame(
            encoding=status_encoding,
            sequence=sequence,
            sample_rate=sample_rate,
            first_sample_index=first_sample_index,
            i2s_errors=i2s_errors,
            tx_errors=tx_errors,
            clipped_frames=clipped_frames,
            reserved=reserved,
            pcm_shift=pcm_shift,
            streaming=bool(streaming),
        )


class PcmWaveRecorder:
    def __init__(self, path: str | Path, sample_rate: int = 16_000) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._wave = wave.open(str(self.path), "wb")
        self._wave.setnchannels(1)
        self._wave.setsampwidth(2)
        self._wave.setframerate(sample_rate)
        self.sample_count = 0
        self._closed = False

    def write(self, samples: tuple[int, ...]) -> None:
        if self._closed:
            raise RuntimeError("WAV recorder is already closed")
        if not samples:
            return
        self._wave.writeframesraw(struct.pack(f"<{len(samples)}h", *samples))
        self.sample_count += len(samples)

    def close(self) -> None:
        if not self._closed:
            self._wave.close()
            self._closed = True

    def __enter__(self) -> PcmWaveRecorder:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
