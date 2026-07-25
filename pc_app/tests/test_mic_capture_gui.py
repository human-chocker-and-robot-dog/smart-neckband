from smart_neckband.mic_capture_gui import (
    CaptureStopDecision,
    wake_capture_stop_decision,
)


def test_wake_capture_stops_immediately_when_local_vad_ends() -> None:
    assert wake_capture_stop_decision(
        "wake",
        event_source="vad",
        event_kind="speech_end",
    ) == CaptureStopDecision(reason="vad_speech_end", finish_asr=True)


def test_wake_capture_stops_when_cloud_asr_returns_final() -> None:
    assert wake_capture_stop_decision(
        "wake",
        event_source="asr",
        event_kind="final",
    ) == CaptureStopDecision(reason="asr_final", finish_asr=False)


def test_non_terminal_or_non_wake_events_do_not_stop_capture() -> None:
    assert (
        wake_capture_stop_decision(
            "wake",
            event_source="asr",
            event_kind="partial",
        )
        is None
    )
    assert (
        wake_capture_stop_decision(
            "manual",
            event_source="vad",
            event_kind="speech_end",
        )
        is None
    )
