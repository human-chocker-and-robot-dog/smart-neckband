from __future__ import annotations

import random
import struct

from smart_neckband.mic_capture_protocol import (
    CRC,
    ENCODING_IMA_ADPCM,
    FRAME_TYPE_AUDIO,
    HEADER,
    MAGIC,
    crc16_ccitt_false,
)
from smart_neckband.protocol import encode_ecg_packet
from smart_neckband.unified_stream import UnifiedFrameKind, UnifiedStreamDemux


def make_mic_frame(*, sequence: int = 0, payload: bytes | None = None) -> bytes:
    samples = (0, 10, -10, 20, -20)
    if payload is None:
        payload = struct.pack("<hBB", samples[0], 0, 0) + bytes((0x10, 0x32))
    header = HEADER.pack(
        MAGIC,
        1,
        FRAME_TYPE_AUDIO,
        ENCODING_IMA_ADPCM,
        0,
        sequence,
        16_000,
        sequence * len(samples),
        len(samples),
        len(payload),
    )
    body = header + payload
    return body + CRC.pack(crc16_ccitt_false(body))


def make_v0_packet(sequence: int = 1) -> bytes:
    return encode_ecg_packet(
        packet_sequence=sequence,
        timestamp_us=123_456,
        first_sample_index=0,
        flags=0,
        samples=tuple(range(20)),
    )


def test_demux_handles_bytewise_mixed_frames() -> None:
    v0 = make_v0_packet()
    mic = make_mic_frame()
    demux = UnifiedStreamDemux()
    frames = []
    for value in v0 + mic + v0:
        frames.extend(demux.feed(bytes((value,))))

    assert [frame.kind for frame in frames] == [
        UnifiedFrameKind.V0,
        UnifiedFrameKind.MIC1,
        UnifiedFrameKind.V0,
    ]
    assert [frame.data for frame in frames] == [v0, mic, v0]


def test_demux_recovers_from_noise_crc_error_and_partial_magic() -> None:
    broken = bytearray(make_mic_frame())
    broken[HEADER.size] ^= 0xFF
    v0 = make_v0_packet()
    mic = make_mic_frame(sequence=2)
    demux = UnifiedStreamDemux()

    assert demux.feed(b"noiseM") == []
    frames = demux.feed(b"IC0" + broken + v0[:1])
    assert frames == []
    frames = demux.feed(v0[1:] + mic)

    assert [frame.data for frame in frames] == [v0, mic]
    assert demux.stats.crc_errors == 1
    assert demux.stats.v0_crc_errors == 0
    assert demux.stats.mic1_crc_errors == 1
    assert demux.stats.discarded_bytes >= len(b"noiseMIC0")


def test_demux_tracks_v0_crc_errors_separately() -> None:
    broken = bytearray(make_v0_packet())
    broken[-1] ^= 0xFF
    mic = make_mic_frame()
    demux = UnifiedStreamDemux()

    frames = demux.feed(broken + mic)

    assert [frame.data for frame in frames] == [mic]
    assert demux.stats.crc_errors == 1
    assert demux.stats.v0_crc_errors == 1
    assert demux.stats.mic1_crc_errors == 0


def test_demux_does_not_split_on_magic_inside_payload() -> None:
    payload = struct.pack("<hBB", 0, 0, 0) + b"SNMIC1"
    mic = make_mic_frame(payload=payload)
    v0 = make_v0_packet()
    demux = UnifiedStreamDemux()

    frames = demux.feed(mic + v0)

    assert [frame.data for frame in frames] == [mic, v0]


def test_demux_random_noise_preserves_following_frames() -> None:
    rng = random.Random(20260725)
    noise = bytes(rng.randrange(0, 256) for _ in range(257))
    v0 = make_v0_packet()
    mic = make_mic_frame()
    demux = UnifiedStreamDemux()

    frames = demux.feed(noise + v0 + mic)

    assert [frame.data for frame in frames] == [v0, mic]
    assert demux.stats.discarded_bytes >= len(noise)
