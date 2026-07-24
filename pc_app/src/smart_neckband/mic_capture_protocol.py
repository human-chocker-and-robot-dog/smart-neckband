from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct
import wave


MAGIC = b"MIC1"
VERSION = 1
FRAME_TYPE_AUDIO = 1
FRAME_TYPE_STATUS = 2
FRAME_TYPE_WAKE = 3
ENCODING_PCM16 = 1
ENCODING_PCM8 = 2
ENCODING_IMA_ADPCM = 3
FLAG_CLIPPED = 1 << 0
FLAG_I2S_ERROR = 1 << 1
FLAG_TX_ERROR = 1 << 2

HEADER = struct.Struct("<4sBBBHIIQHH")
CRC = struct.Struct("<H")
STATUS_PAYLOAD = struct.Struct("<IIIIHBB")
WAKE_PAYLOAD = struct.Struct("<IHH")
MAX_PAYLOAD_BYTES = 1024

ADPCM_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
    143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
    494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
    1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
    4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767,
)
ADPCM_INDEX_TABLE = (
    -1, -1, -1, -1, 2, 4, 6, 8,
    -1, -1, -1, -1, 2, 4, 6, 8,
)


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
    armed: bool


@dataclass(frozen=True, slots=True)
class WakeEventFrame:
    encoding: int
    sequence: int
    sample_rate: int
    detected_sample_index: int
    wake_count: int
    word_index: int


MicFrame = AudioFrame | DeviceStatusFrame | WakeEventFrame


@dataclass(slots=True)
class ParserStats:
    audio_frames: int = 0
    status_frames: int = 0
    wake_events: int = 0
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
                or frame_type not in (
                    FRAME_TYPE_AUDIO,
                    FRAME_TYPE_STATUS,
                    FRAME_TYPE_WAKE,
                )
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
            elif encoding == ENCODING_IMA_ADPCM:
                samples = self._decode_ima_adpcm(payload, sample_count)
                if samples is None:
                    self.stats.malformed_frames += 1
                    return None
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

        if frame_type == FRAME_TYPE_WAKE:
            if len(payload) != WAKE_PAYLOAD.size or sample_count != 0:
                self.stats.malformed_frames += 1
                return None
            wake_count, word_index, _reserved = WAKE_PAYLOAD.unpack(payload)
            self.stats.wake_events += 1
            return WakeEventFrame(
                encoding=encoding,
                sequence=sequence,
                sample_rate=sample_rate,
                detected_sample_index=first_sample_index,
                wake_count=wake_count,
                word_index=word_index,
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
            capture_state,
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
            streaming=capture_state == 1,
            armed=capture_state == 2,
        )

    @staticmethod
    def _decode_ima_adpcm(payload: bytes, sample_count: int) -> tuple[int, ...] | None:
        expected_length = 4 + (sample_count // 2)
        if sample_count == 0 or len(payload) != expected_length:
            return None
        predictor, index, _reserved = struct.unpack_from("<hBB", payload)
        if index > 88:
            return None

        samples = [predictor]
        for sample_index in range(1, sample_count):
            nibble_index = sample_index - 1
            encoded = payload[4 + nibble_index // 2]
            code = encoded & 0x0F if nibble_index % 2 == 0 else encoded >> 4
            step = ADPCM_STEP_TABLE[index]
            delta = step >> 3
            if code & 4:
                delta += step
            if code & 2:
                delta += step >> 1
            if code & 1:
                delta += step >> 2
            predictor += -delta if code & 8 else delta
            predictor = max(-32768, min(32767, predictor))
            index = max(0, min(88, index + ADPCM_INDEX_TABLE[code]))
            samples.append(predictor)
        return tuple(samples)


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
