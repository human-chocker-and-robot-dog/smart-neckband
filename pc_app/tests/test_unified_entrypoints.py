from __future__ import annotations


def test_legacy_microphone_entry_opens_unified_gui(monkeypatch) -> None:
    from smart_neckband import gui, mic_capture_gui

    calls: list[str] = []
    monkeypatch.setattr(gui, "main", lambda: calls.append("unified") or 27)

    assert mic_capture_gui.main() == 27
    assert calls == ["unified"]
