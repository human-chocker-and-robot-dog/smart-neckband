from __future__ import annotations

from smart_neckband.audio_threshold_vad import (
    AudioThresholdVadSettings,
    AudioThresholdVadThread,
    VadEvent,
    calibrated_noise_rms,
    pcm_peak,
    pcm_rms,
)


def test_audio_threshold_settings_compute_robust_speech_threshold() -> None:
    settings = AudioThresholdVadSettings(
        noise_rms=100.0,
        rms_multiplier=2.0,
        min_rms_delta=250.0,
    )

    assert settings.speech_rms_threshold == 350.0


def test_audio_threshold_uses_speech_sample_when_available() -> None:
    settings = AudioThresholdVadSettings(
        noise_rms=100.0,
        speech_rms=1000.0,
        rms_multiplier=3.0,
        min_rms_delta=250.0,
    )

    assert settings.speech_rms_threshold == 415.0


def test_audio_threshold_settings_roundtrip_keeps_speech_sample() -> None:
    settings = AudioThresholdVadSettings(noise_rms=120.0, speech_rms=980.0)

    assert AudioThresholdVadSettings.from_json_dict(settings.to_json_dict()) == settings


def test_audio_threshold_uses_lower_threshold_after_speech_starts() -> None:
    settings = AudioThresholdVadSettings(
        noise_rms=100.0,
        rms_multiplier=3.0,
        min_rms_delta=0.0,
        speech_end_threshold_ratio=0.60,
    )

    assert settings.speech_rms_threshold == 300.0
    assert settings.speech_end_rms_threshold == 180.0


def test_audio_threshold_vad_detects_speech_end_after_silence() -> None:
    events: list[VadEvent] = []
    settings = AudioThresholdVadSettings(
        sample_rate=1000,
        analysis_window_ms=100,
        noise_rms=10.0,
        rms_multiplier=2.0,
        min_rms_delta=0.0,
        min_speech_ms=100,
        silence_ms=200,
    )
    vad = AudioThresholdVadThread(settings, on_event=events.append)

    assert vad._handle_chunk((40,) * 100, final=False) == 40.0
    vad.feed((40,) * 100)
    vad.feed((0,) * 100)
    vad.feed((0,) * 100)
    vad.finish()
    vad.start()
    vad.join(timeout=2.0)

    assert VadEvent("speech_start", "RMS 40") in events
    assert any(event.kind == "speech_end" for event in events)


def test_audio_threshold_vad_does_not_cut_quiet_speech_at_start_threshold() -> None:
    events: list[VadEvent] = []
    settings = AudioThresholdVadSettings(
        sample_rate=1000,
        analysis_window_ms=100,
        noise_rms=10.0,
        rms_multiplier=3.0,
        min_rms_delta=0.0,
        speech_end_threshold_ratio=0.60,
        min_speech_ms=100,
        silence_ms=200,
    )
    vad = AudioThresholdVadThread(settings, on_event=events.append)

    vad.feed((40,) * 100)
    vad.feed((20,) * 100)
    vad.feed((20,) * 100)
    vad.finish()
    vad.start()
    vad.join(timeout=2.0)

    assert VadEvent("speech_start", "RMS 40") in events
    assert not any(event.kind == "speech_end" for event in events)


def test_audio_threshold_vad_drops_old_audio_instead_of_erroring_when_queue_is_full() -> None:
    events: list[VadEvent] = []
    vad = AudioThresholdVadThread(
        AudioThresholdVadSettings(queue_depth=8),
        on_event=events.append,
    )

    for value in range(9):
        vad.feed((value,))

    assert VadEvent("error", "音频阈值 VAD 音频队列已满") not in events
    assert events == [VadEvent("status", "音频阈值 VAD 忙，已丢弃旧音频 1 帧")]
    assert vad._audio.get_nowait() == (1,)
    assert vad._audio.get_nowait() == (2,)


def test_audio_threshold_calibration_uses_high_silent_window() -> None:
    assert calibrated_noise_rms([10.0, 11.0, 12.0, 100.0]) == 12.0
    assert calibrated_noise_rms([]) == 0.0
    assert pcm_rms((3, 4)) == 3.5355339059327378
    assert pcm_peak((-3, 4)) == 4
