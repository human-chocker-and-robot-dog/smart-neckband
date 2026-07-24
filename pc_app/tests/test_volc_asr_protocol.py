from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from smart_neckband.volc_asr_client import (
    VolcAsrClientThread,
    VolcAsrEvent,
    VolcAsrSettings,
    build_audio_frame,
    build_client_frame,
    build_full_request,
    load_volc_asr_settings,
    parse_server_frame,
    save_volc_asr_settings,
)


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "volc_asr_v3.json").read_text(
        encoding="utf-8"
    )
)


def _client_frame(message_type: int, flags: int, serialization: int, payload: bytes) -> bytes:
    return bytes((0x11, (message_type << 4) | flags, serialization << 4, 0)) + struct.pack(
        ">I", len(payload)
    ) + payload


def _server_response(sequence: int, payload: dict[str, object], *, final: bool) -> bytes:
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    flags = 3 if final else 1
    return bytes((0x11, 0x90 | flags, 0x10, 0)) + struct.pack(
        ">iI", sequence, len(encoded)
    ) + encoded


def _server_error(code: int, message: str) -> bytes:
    encoded = message.encode("utf-8")
    return bytes((0x11, 0xF0, 0, 0)) + struct.pack(">II", code, len(encoded)) + encoded


def _parse_server_frame(frame: bytes) -> tuple[int, int, int, bytes]:
    if len(frame) < 4 or frame[0] >> 4 != 1:
        raise ValueError("bad protocol header")
    header_size = (frame[0] & 0x0F) * 4
    message_type = frame[1] >> 4
    flags = frame[1] & 0x0F
    if frame[2] & 0x0F:
        raise ValueError("fixture parser intentionally requires no compression")
    offset = header_size
    value = 0
    if message_type == 15:
        value, payload_size = struct.unpack_from(">II", frame, offset)
        offset += 8
    else:
        if flags & 1:
            value = struct.unpack_from(">i", frame, offset)[0]
            offset += 4
        payload_size = struct.unpack_from(">I", frame, offset)[0]
        offset += 4
    if payload_size != len(frame) - offset:
        raise ValueError("payload length mismatch")
    return message_type, flags, value, frame[offset:]


def test_client_golden_frames_use_official_no_compression_mode() -> None:
    assert build_client_frame(1, 0, 1, b"{}").hex() == FIXTURE[
        "full_request_no_compression_hex"
    ]
    pcm = struct.pack("<hh", 1, -2)
    assert build_audio_frame((1, -2), final=False).hex() == FIXTURE[
        "audio_nonfinal_no_compression_hex"
    ]
    assert build_audio_frame((1, -2), final=True).hex() == FIXTURE[
        "audio_final_no_compression_hex"
    ]


def test_fragmented_partial_and_final_responses() -> None:
    partial = FIXTURE["partial_response"]
    partial_frame = _server_response(
        partial["sequence"], partial["payload"], final=False
    )
    final = FIXTURE["final_response"]
    final_frame = _server_response(final["sequence"], final["payload"], final=True)

    # WebSocket callbacks may split one binary message at arbitrary byte offsets.
    reassembled = b"".join(
        [final_frame[:1], final_frame[1:9], final_frame[9:17], final_frame[17:]]
    )
    message_type, flags, sequence, payload = _parse_server_frame(reassembled)
    parsed = json.loads(payload)
    assert message_type == 9
    assert flags & 2
    assert sequence < 0
    assert parsed["result"]["text"] == "打开灯"
    assert parsed["result"]["utterances"][0]["definite"] is True
    assert parse_server_frame(reassembled) == VolcAsrEvent("final", text="打开灯")

    _, partial_flags, partial_sequence, partial_payload = _parse_server_frame(
        partial_frame
    )
    assert partial_flags == 1
    assert partial_sequence > 0
    assert json.loads(partial_payload)["result"]["text"] == "打开"
    assert parse_server_frame(partial_frame) == VolcAsrEvent("partial", text="打开")


def test_error_and_malformed_length_are_rejected() -> None:
    error = FIXTURE["error_response"]
    message_type, _, code, payload = _parse_server_frame(
        _server_error(error["code"], error["message"])
    )
    assert message_type == 15
    assert code == error["code"]
    assert payload.decode() == error["message"]

    malformed = bytearray(_server_response(-1, {"result": {"text": "x"}}, final=True))
    malformed[11] += 1
    with pytest.raises(ValueError, match="payload length"):
        _parse_server_frame(bytes(malformed))
    with pytest.raises(ValueError, match="payload length"):
        parse_server_frame(bytes(malformed))


def test_full_request_carries_pc_side_bigmodel_audio_settings() -> None:
    frame = build_full_request(
        VolcAsrSettings(
            api_key="key",
            resource_id="resource",
            uid="unit-test",
            model_name="bigmodel",
            end_window_size_ms=900,
            force_to_speech_time_ms=1200,
        )
    )
    message_type, flags, _sequence, payload = _parse_server_frame(frame)
    request = json.loads(payload)
    assert message_type == 1
    assert flags == 0
    assert request["user"]["uid"] == "unit-test"
    assert request["audio"] == {
        "format": "pcm",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
        "codec": "raw",
    }
    assert request["request"]["model_name"] == "bigmodel"
    assert request["request"]["show_utterances"] is True
    assert request["request"]["end_window_size"] == 900
    assert request["request"]["force_to_speech_time"] == 1200


def test_asr_settings_validate_required_credentials() -> None:
    VolcAsrSettings(api_key="key", resource_id="resource").validate()
    VolcAsrSettings(
        auth_mode="legacy",
        app_key="app",
        access_key="access",
        resource_id="resource",
    ).validate()

    with pytest.raises(ValueError, match="Resource ID"):
        VolcAsrSettings(api_key="key").validate()
    with pytest.raises(ValueError, match="API Key"):
        VolcAsrSettings(resource_id="resource").validate()


def test_asr_settings_save_and_load_local_json(tmp_path: Path) -> None:
    path = tmp_path / "volc_asr_settings.json"
    settings = VolcAsrSettings(
        endpoint="wss://example.test/asr",
        auth_mode="legacy",
        app_key="app",
        access_key="access",
        resource_id="resource",
        uid="unit",
        model_name="bigmodel",
        audio_queue_depth=768,
        audio_chunk_ms=100,
        end_window_size_ms=900,
        force_to_speech_time_ms=1200,
    )

    save_volc_asr_settings(path, settings)

    assert load_volc_asr_settings(path) == settings


def test_asr_drops_old_audio_instead_of_erroring_when_queue_is_full() -> None:
    events: list[VolcAsrEvent] = []
    asr = VolcAsrClientThread(
        VolcAsrSettings(
            api_key="key",
            resource_id="resource",
            audio_queue_depth=8,
        ),
        on_event=events.append,
    )

    for value in range(9):
        asr.feed((value,))

    assert VolcAsrEvent("error", detail="ASR 音频队列已满") not in events
    assert events == [VolcAsrEvent("status", detail="ASR 忙，已丢弃旧音频 1 帧")]
    assert asr._audio.get_nowait() == (1,)
    assert asr._audio.get_nowait() == (2,)


def test_asr_finish_drops_stale_audio_until_final_marker_fits() -> None:
    asr = VolcAsrClientThread(
        VolcAsrSettings(
            api_key="key",
            resource_id="resource",
            audio_queue_depth=8,
        ),
        on_event=lambda event: None,
    )

    for value in range(8):
        asr.feed((value,))
    asr.finish()

    queued = []
    while not asr._audio.empty():
        queued.append(asr._audio.get_nowait())
    assert queued[-1] is None
    assert len(queued) == 8
    assert queued[0] == (1,)


def test_asr_drain_sends_backlog_without_recv_pacing() -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def send_binary(self, frame: bytes) -> None:
            self.sent.append(frame)

    fake = FakeWebSocket()
    asr = VolcAsrClientThread(
        VolcAsrSettings(
            api_key="key",
            resource_id="resource",
            audio_chunk_ms=20,
            receive_timeout_s=0.5,
        ),
        on_event=lambda event: None,
    )
    pending: list[int] = []
    chunk_samples = 4

    asr.feed((1, 2))
    asr.feed((3, 4))
    asr.feed((5, 6))
    asr.finish()
    drained = asr._drain_audio_queue(
        fake,
        pending,
        chunk_samples,
        wait_timeout_s=0.0,
    )

    assert drained.final_sent is True
    assert drained.frames == 3
    assert len(fake.sent) == 2
    audio_type, audio_flags, _sequence, audio_payload = _parse_server_frame(fake.sent[0])
    final_type, final_flags, _sequence, final_payload = _parse_server_frame(fake.sent[1])
    assert audio_type == 2
    assert audio_flags == 0
    assert struct.unpack("<hhhh", audio_payload) == (1, 2, 3, 4)
    assert final_type == 2
    assert final_flags & 2
    assert struct.unpack("<hh", final_payload) == (5, 6)
    assert asr._effective_receive_timeout_s() == 0.02
