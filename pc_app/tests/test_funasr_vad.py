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
