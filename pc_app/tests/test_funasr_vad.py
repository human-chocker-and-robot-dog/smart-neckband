from __future__ import annotations

from smart_neckband.funasr_vad import FunAsrVadSettings, FunAsrVadThread, VadEvent


def test_funasr_vad_interprets_streaming_start_and_end_segments() -> None:
    events: list[VadEvent] = []
    vad = FunAsrVadThread(FunAsrVadSettings(), on_event=events.append)

    should_stop = vad._handle_result(
        [{"value": [[120, -1], [-1, 960]]}]
    )

    assert should_stop is True
    assert events == [
        VadEvent("speech_start", "120 ms"),
        VadEvent("speech_end", "960 ms"),
    ]


def test_funasr_vad_ignores_empty_or_malformed_results() -> None:
    events: list[VadEvent] = []
    vad = FunAsrVadThread(FunAsrVadSettings(), on_event=events.append)

    assert vad._handle_result([{"value": []}, {"value": [["bad", -1]]}]) is False
    assert events == []


def test_funasr_vad_drops_old_audio_instead_of_erroring_when_queue_is_full() -> None:
    events: list[VadEvent] = []
    vad = FunAsrVadThread(
        FunAsrVadSettings(queue_depth=2),
        on_event=events.append,
    )

    vad.feed((1,))
    vad.feed((2,))
    vad.feed((3,))

    assert VadEvent("error", "VAD 音频队列已满") not in events
    assert events == [VadEvent("status", "VAD 忙，已丢弃旧音频 1 帧")]
    assert vad._audio.get_nowait() == (2,)
    assert vad._audio.get_nowait() == (3,)
