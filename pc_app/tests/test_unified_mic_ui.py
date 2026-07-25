from __future__ import annotations

import os

from smart_neckband.mic_capture_protocol import (
    ENCODING_IMA_ADPCM,
    DeviceStatusFrame,
    ParserStats,
    WakeEventFrame,
)
from smart_neckband.volc_asr_client import VolcAsrEvent


def test_unified_mic_panel_constructs_and_uses_stable_wake_id(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    import pyqtgraph as pg
    from PySide6 import QtCore, QtWidgets

    from smart_neckband.unified_mic_ui import UnifiedMicPanel

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None
    submitted: list[tuple[str, str]] = []

    class FakeReader:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def queue_mic_command(self, command: str) -> None:
            self.commands.append(command)

    panel = UnifiedMicPanel(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        pg=pg,
        post_gui=lambda callback: callback(),
        webhook_submit=lambda text, *, instruction_id: submitted.append(
            (text, instruction_id)
        )
        is None,
    )
    reader = FakeReader()
    panel.set_reader(reader, "AA:BB:CC:DD:EE:FF")
    panel.auto_asr.setChecked(False)
    panel.auto_vad.setChecked(False)
    panel.handle_frame(
        DeviceStatusFrame(
            encoding=ENCODING_IMA_ADPCM,
            sequence=0,
            sample_rate=16_000,
            first_sample_index=0,
            i2s_errors=0,
            tx_errors=0,
            clipped_frames=0,
            reserved=8,
            pcm_shift=16,
            streaming=False,
            armed=True,
        ),
        ParserStats(),
    )
    panel.handle_frame(
        WakeEventFrame(
            encoding=ENCODING_IMA_ADPCM,
            sequence=1,
            sample_rate=16_000,
            detected_sample_index=48_000,
            wake_count=3,
            word_index=1,
        ),
        ParserStats(wake_events=1),
    )
    instruction_id = panel.instruction_id

    panel._on_asr_event(VolcAsrEvent("final", text="forward"), panel.session_generation)
    panel._on_asr_event(VolcAsrEvent("final", text="forward"), panel.session_generation)
    panel.stop_stream()

    assert instruction_id is not None and instruction_id.startswith("mic-")
    assert submitted == [("forward", instruction_id), ("forward", instruction_id)]
    assert reader.commands == ["STOP"]
    panel.close()
