from __future__ import annotations

import struct
import wave
import json
from pathlib import Path

from smart_neckband.mic_capture_protocol import (
    CRC,
    ENCODING_IMA_ADPCM,
    ENCODING_PCM16,
    ENCODING_PCM8,
    FRAME_TYPE_AUDIO,
    FRAME_TYPE_STATUS,
    FRAME_TYPE_WAKE,
    HEADER,
    MAGIC,
    STATUS_PAYLOAD,
    WAKE_PAYLOAD,
    AudioFrame,
    DeviceStatusFrame,
    MicFrameParser,
    PcmWaveRecorder,
    WakeEventFrame,
    crc16_ccitt_false,
)


def make_frame(
    *,
    frame_type: int,
    sequence: int,
    sample_count: int,
    payload: bytes,
    flags: int = 0,
    encoding: int = ENCODING_PCM16,
    first_sample_index: int | None = None,
) -> bytes:
    header = HEADER.pack(
        MAGIC,
        1,
        frame_type,
        encoding,
        flags,
        sequence,
        16_000,
        sequence * sample_count if first_sample_index is None else first_sample_index,
        sample_count,
        len(payload),
    )
    body = header + payload
    return body + CRC.pack(crc16_ccitt_false(body))


def test_shared_mic1_golden_status_vector() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    vectors = json.loads(
        (repo_root / "docs" / "protocol" / "mic1_golden_vectors.json").read_text(
            encoding="utf-8"
        )
    )["vectors"]
    encoded = bytes.fromhex(vectors[0]["frame_hex"])

    frames = MicFrameParser().feed(encoded)

    assert frames == [
        DeviceStatusFrame(
            encoding=ENCODING_IMA_ADPCM,
            sequence=7,
            sample_rate=16_000,
            first_sample_index=123_456,
            i2s_errors=1,
            tx_errors=2,
            clipped_frames=3,
            reserved=8,
            pcm_shift=16,
            streaming=False,
            armed=True,
        )
    ]


def test_pcm16_frame_survives_arbitrary_ble_fragmentation() -> None:
    samples = (-32768, -100, 0, 100, 32767)
    encoded = make_frame(
        frame_type=FRAME_TYPE_AUDIO,
        sequence=7,
        sample_count=len(samples),
        payload=struct.pack("<5h", *samples),
    )
    parser = MicFrameParser()
    frames = []
    for cut in (encoded[:1], encoded[1:9], encoded[9:27], encoded[27:31], encoded[31:]):
        frames.extend(parser.feed(cut))

    assert frames == [
        AudioFrame(
            encoding=ENCODING_PCM16,
            flags=0,
            sequence=7,
            sample_rate=16_000,
            first_sample_index=35,
            samples=samples,
        )
    ]
    assert parser.stats.crc_errors == 0


def test_pcm8_expands_to_pcm16_and_counts_sequence_gap() -> None:
    parser = MicFrameParser()
    first = make_frame(
        frame_type=FRAME_TYPE_AUDIO,
        sequence=1,
        sample_count=3,
        payload=struct.pack("<3b", -128, 0, 127),
        encoding=ENCODING_PCM8,
    )
    third = make_frame(
        frame_type=FRAME_TYPE_AUDIO,
        sequence=3,
        sample_count=1,
        payload=struct.pack("<b", 5),
        encoding=ENCODING_PCM8,
    )
    frames = parser.feed(first + third)
    assert isinstance(frames[0], AudioFrame)
    assert frames[0].encoding == ENCODING_PCM8
    assert frames[0].samples == (-32768, 0, 32512)
    assert parser.stats.sequence_gaps == 1


def test_ima_adpcm_block_decodes_to_pcm16() -> None:
    payload = struct.pack("<hBB", 0, 0, 0) + bytes((0x10, 0x32))
    parser = MicFrameParser()
    frames = parser.feed(
        make_frame(
            frame_type=FRAME_TYPE_AUDIO,
            sequence=0,
            sample_count=5,
            payload=payload,
            encoding=ENCODING_IMA_ADPCM,
        )
    )
    assert len(frames) == 1
    assert isinstance(frames[0], AudioFrame)
    assert frames[0].encoding == ENCODING_IMA_ADPCM
    assert frames[0].samples == (0, 0, 1, 4, 8)


def test_crc_error_resynchronizes_to_next_frame() -> None:
    broken = bytearray(
        make_frame(
            frame_type=FRAME_TYPE_AUDIO,
            sequence=0,
            sample_count=1,
            payload=struct.pack("<h", 10),
        )
    )
    broken[HEADER.size] ^= 0xFF
    good = make_frame(
        frame_type=FRAME_TYPE_AUDIO,
        sequence=1,
        sample_count=1,
        payload=struct.pack("<h", 20),
    )
    parser = MicFrameParser()
    frames = parser.feed(b"noise" + broken + good)
    assert len(frames) == 1
    assert isinstance(frames[0], AudioFrame)
    assert frames[0].samples == (20,)
    assert parser.stats.crc_errors == 1
    assert parser.stats.discarded_bytes >= 5


def test_status_frame_decodes_counters() -> None:
    payload = STATUS_PAYLOAD.pack(2, 3, 4, 0, 14, ENCODING_PCM16, 1)
    parser = MicFrameParser()
    frames = parser.feed(
        make_frame(
        frame_type=FRAME_TYPE_STATUS,
        sequence=9,
        sample_count=0,
        payload=payload,
        encoding=ENCODING_PCM16,
        )
    )
    assert frames == [
        DeviceStatusFrame(
            encoding=ENCODING_PCM16,
            sequence=9,
            sample_rate=16_000,
            first_sample_index=0,
            i2s_errors=2,
            tx_errors=3,
            clipped_frames=4,
            reserved=0,
            pcm_shift=14,
            streaming=True,
            armed=False,
        )
    ]


def test_status_frame_decodes_hi_esp_armed_state() -> None:
    payload = STATUS_PAYLOAD.pack(0, 0, 0, 12, 16, ENCODING_IMA_ADPCM, 2)
    parser = MicFrameParser()
    frames = parser.feed(
        make_frame(
            frame_type=FRAME_TYPE_STATUS,
            sequence=0,
            sample_count=0,
            payload=payload,
            encoding=ENCODING_IMA_ADPCM,
        )
    )
    assert len(frames) == 1
    status = frames[0]
    assert isinstance(status, DeviceStatusFrame)
    assert status.armed is True
    assert status.streaming is False


def test_hi_esp_wake_event_decodes_count_and_sample_index() -> None:
    parser = MicFrameParser()
    frames = parser.feed(
        make_frame(
            frame_type=FRAME_TYPE_WAKE,
            sequence=3,
            sample_count=0,
            payload=WAKE_PAYLOAD.pack(3, 1, 0),
            encoding=ENCODING_IMA_ADPCM,
            first_sample_index=48_000,
        )
    )
    assert frames == [
        WakeEventFrame(
            encoding=ENCODING_IMA_ADPCM,
            sequence=3,
            sample_rate=16_000,
            detected_sample_index=48_000,
            wake_count=3,
            word_index=1,
        )
    ]
    assert parser.stats.wake_events == 1


def test_wave_recorder_writes_mono_16khz_pcm(tmp_path) -> None:
    path = tmp_path / "capture.wav"
    with PcmWaveRecorder(path) as recorder:
        recorder.write((-32768, -1, 0, 1, 32767))
        assert recorder.sample_count == 5

    with wave.open(str(path), "rb") as captured:
        assert captured.getnchannels() == 1
        assert captured.getsampwidth() == 2
        assert captured.getframerate() == 16_000
        assert captured.getnframes() == 5
        assert struct.unpack("<5h", captured.readframes(5)) == (-32768, -1, 0, 1, 32767)
