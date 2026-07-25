from __future__ import annotations

from collections import deque
import json
import math
from pathlib import Path
from typing import Callable

from .audio_threshold_vad import AudioThresholdVadSettings, AudioThresholdVadThread, VadEvent
from .mic_capture_debug import DebugLogger
from .mic_capture_protocol import (
    AudioFrame,
    DeviceStatusFrame,
    MicFrame,
    ParserStats,
    PcmWaveRecorder,
    WakeEventFrame,
)
from .mic_webhook import mic_asr_instruction_id, new_connection_instance_id
from .volc_asr_client import (
    VolcAsrClientThread,
    VolcAsrEvent,
    load_volc_asr_settings,
)


class UnifiedMicPanel:
    """Hi ESP, MIC1, PC VAD, ASR, and Webhook controls for the main GUI."""

    def __init__(
        self,
        *,
        QtCore: object,
        QtWidgets: object,
        pg: object,
        post_gui: Callable[[object], None],
        webhook_submit: Callable[..., bool],
    ) -> None:
        self.post_gui = post_gui
        self.webhook_submit = webhook_submit
        self.reader: object | None = None
        self.device_identity: str | None = None
        self.connection_instance_id: str | None = None
        self.instruction_id: str | None = None
        self.stream_stop_requested = False
        self.session_generation = 0
        self.asr_worker: VolcAsrClientThread | None = None
        self.vad_worker: AudioThresholdVadThread | None = None
        self.recorder: PcmWaveRecorder | None = None
        self.samples: deque[int] = deque(maxlen=32_000)
        self.asr_settings_path = Path("data") / "volc_asr_settings.json"
        self.vad_settings_path = Path("data") / "audio_threshold_vad_settings.json"
        self.debug_logger = DebugLogger(Path("data") / "unified_mic_debug.log")

        self.widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.widget)

        controls = QtWidgets.QHBoxLayout()
        self.arm_button = QtWidgets.QPushButton("ARM")
        self.disarm_button = QtWidgets.QPushButton("DISARM")
        self.stop_button = QtWidgets.QPushButton("STOP")
        self.shift = QtWidgets.QSpinBox()
        self.shift.setRange(10, 20)
        self.shift.setValue(16)
        self.auto_asr = QtWidgets.QCheckBox("Auto ASR")
        self.auto_asr.setChecked(True)
        self.auto_vad = QtWidgets.QCheckBox("Auto VAD stop")
        self.auto_vad.setChecked(True)
        self.save_wav = QtWidgets.QCheckBox("Save wake WAV")
        controls.addWidget(self.arm_button)
        controls.addWidget(self.disarm_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(QtWidgets.QLabel("PCM shift"))
        controls.addWidget(self.shift)
        controls.addWidget(self.auto_asr)
        controls.addWidget(self.auto_vad)
        controls.addWidget(self.save_wav)
        controls.addStretch(1)
        layout.addLayout(controls)

        status_group = QtWidgets.QGroupBox("Hi ESP / MIC1")
        status_layout = QtWidgets.QGridLayout(status_group)
        self.state_label = QtWidgets.QLabel("Disconnected")
        self.level_label = QtWidgets.QLabel("RMS -- / Peak --")
        self.link_label = QtWidgets.QLabel("MIC1 frames 0 / gaps 0 / CRC 0")
        self.device_label = QtWidgets.QLabel("I2S -- / TX -- / interval --")
        self.vad_label = QtWidgets.QLabel("VAD waiting")
        self.asr_label = QtWidgets.QLabel("ASR waiting")
        status_layout.addWidget(self.state_label, 0, 0)
        status_layout.addWidget(self.level_label, 0, 1)
        status_layout.addWidget(self.link_label, 1, 0)
        status_layout.addWidget(self.device_label, 1, 1)
        status_layout.addWidget(self.vad_label, 2, 0)
        status_layout.addWidget(self.asr_label, 2, 1)
        layout.addWidget(status_group)

        self.plot = pg.PlotWidget(title="MIC1 last 2 seconds")
        self.curve = self.plot.plot(pen=pg.mkPen("#00796b", width=1))
        self.plot.setYRange(-32768, 32767)
        layout.addWidget(self.plot, 1)

        self.transcript = QtWidgets.QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText("ASR final")
        self.transcript.setMaximumBlockCount(100)
        layout.addWidget(self.transcript)

        self.arm_button.clicked.connect(lambda: self.send_command("ARM"))
        self.disarm_button.clicked.connect(lambda: self.send_command("DISARM"))
        self.stop_button.clicked.connect(self.stop_stream)
        self.shift.valueChanged.connect(
            lambda value: self.send_command(f"SHIFT {int(value)}")
        )
        self.refresh_timer = QtCore.QTimer()
        self.refresh_timer.timeout.connect(self.refresh_plot)
        self.refresh_timer.start(100)
        self._set_controls_enabled(False)

    def set_reader(self, reader: object | None, device_identity: str | None = None) -> None:
        self.session_generation += 1
        self._finish_session(cancel=True)
        self.reader = reader
        self.device_identity = device_identity
        self.connection_instance_id = new_connection_instance_id() if reader else None
        self.instruction_id = None
        self.stream_stop_requested = False
        self.samples.clear()
        self.curve.setData([])
        self._set_controls_enabled(reader is not None)
        self.state_label.setText("Connecting / auto ARM" if reader else "Disconnected")

    def handle_frame(self, frame: MicFrame, stats: ParserStats) -> None:
        self.link_label.setText(
            f"MIC1 frames {stats.audio_frames} / gaps {stats.sequence_gaps} / CRC {stats.crc_errors}"
        )
        if isinstance(frame, AudioFrame):
            self._handle_audio(frame)
        elif isinstance(frame, DeviceStatusFrame):
            self._handle_status(frame)
        elif isinstance(frame, WakeEventFrame):
            self._handle_wake(frame)

    def send_command(self, command: str) -> None:
        if self.reader is None:
            return
        try:
            self.reader.queue_mic_command(command)  # type: ignore[attr-defined]
        except Exception as exc:
            self.state_label.setText(f"MIC command failed: {exc}")

    def stop_stream(self) -> None:
        if not self.stream_stop_requested:
            self.send_command("STOP")
            self.stream_stop_requested = True
        self._finish_session(cancel=False)
        self.state_label.setText("Stopping / automatic rearm")

    def close(self) -> None:
        self.refresh_timer.stop()
        self._finish_session(cancel=True)
        self.stream_stop_requested = False
        self.reader = None

    def refresh_plot(self) -> None:
        if self.samples:
            self.curve.setData(list(self.samples))

    def _handle_audio(self, frame: AudioFrame) -> None:
        self.samples.extend(frame.samples)
        if self.recorder is not None:
            self.recorder.write(frame.samples)
        if self.asr_worker is not None:
            self.asr_worker.feed(frame.samples)
        if self.vad_worker is not None:
            self.vad_worker.feed(frame.samples)
        if frame.samples:
            rms = math.sqrt(sum(value * value for value in frame.samples) / len(frame.samples))
            peak = max(abs(value) for value in frame.samples)
            self.level_label.setText(f"RMS {rms:.0f} / Peak {peak}")

    def _handle_status(self, frame: DeviceStatusFrame) -> None:
        state = "STREAMING" if frame.streaming else "ARMED" if frame.armed else "DISARMED"
        self.state_label.setText(state)
        self.device_label.setText(
            f"I2S {frame.i2s_errors} / TX {frame.tx_errors} / clip {frame.clipped_frames} / "
            f"interval {frame.reserved * 1.25:.2f} ms"
        )

    def _handle_wake(self, frame: WakeEventFrame) -> None:
        self.session_generation += 1
        self._finish_session(cancel=True)
        self.stream_stop_requested = False
        if self.device_identity is not None and self.connection_instance_id is not None:
            self.instruction_id = mic_asr_instruction_id(
                device_identity=self.device_identity,
                connection_instance_id=self.connection_instance_id,
                detected_sample_index=frame.detected_sample_index,
                wake_count=frame.wake_count,
            )
        else:
            self.instruction_id = None
        self.state_label.setText(f"STREAMING / wake {frame.wake_count}")
        if self.save_wav.isChecked():
            path = Path("data") / "mic" / f"wake_{frame.wake_count:06d}.wav"
            self.recorder = PcmWaveRecorder(path)
        if self.auto_asr.isChecked():
            self._start_asr(self.session_generation)
        if self.auto_vad.isChecked():
            self._start_vad(self.session_generation)

    def _start_asr(self, generation: int) -> None:
        try:
            settings = load_volc_asr_settings(self.asr_settings_path)
            settings.validate()
            self.asr_worker = VolcAsrClientThread(
                settings,
                on_event=lambda event: self.post_gui(
                    lambda event=event, generation=generation: self._on_asr_event(
                        event, generation
                    )
                ),
                debug_logger=self.debug_logger,
            )
            self.asr_worker.start()
            self.asr_label.setText("ASR connecting")
        except Exception as exc:
            self.asr_worker = None
            self.asr_label.setText(f"ASR configuration error: {exc}")

    def _start_vad(self, generation: int) -> None:
        try:
            settings = self._load_vad_settings()
            self.vad_worker = AudioThresholdVadThread(
                settings,
                on_event=lambda event: self.post_gui(
                    lambda event=event, generation=generation: self._on_vad_event(
                        event, generation
                    )
                ),
                debug_logger=self.debug_logger,
            )
            self.vad_worker.start()
            self.vad_label.setText("VAD running")
        except Exception as exc:
            self.vad_worker = None
            self.vad_label.setText(f"VAD configuration error: {exc}")

    def _load_vad_settings(self) -> AudioThresholdVadSettings:
        if not self.vad_settings_path.exists():
            return AudioThresholdVadSettings()
        settings = AudioThresholdVadSettings.from_json_dict(
            json.loads(self.vad_settings_path.read_text(encoding="utf-8"))
        )
        settings.validate()
        return settings

    def _on_asr_event(self, event: VolcAsrEvent, generation: int) -> None:
        if generation != self.session_generation:
            return
        if event.kind == "status":
            self.asr_label.setText(event.detail)
        elif event.kind == "partial":
            self.asr_label.setText(f"Partial: {event.text}")
        elif event.kind == "final":
            self.transcript.appendPlainText(event.text)
            if self.instruction_id is None:
                self.asr_label.setText("ASR final missing wake session ID")
            else:
                persisted = self.webhook_submit(event.text, instruction_id=self.instruction_id)
                self.asr_label.setText(
                    "ASR final queued for Webhook" if persisted else "ASR final persistence failed"
                )
            self.stop_stream()
        elif event.kind == "error":
            self.asr_label.setText(f"ASR error: {event.detail}")
        elif event.kind == "closed":
            self.asr_worker = None

    def _on_vad_event(self, event: VadEvent, generation: int) -> None:
        if generation != self.session_generation:
            return
        if event.kind in ("status", "speech_start"):
            self.vad_label.setText(event.detail)
        elif event.kind == "speech_end":
            self.vad_label.setText(event.detail)
            self.stop_stream()
        elif event.kind == "error":
            self.vad_label.setText(f"VAD error: {event.detail}")
        elif event.kind == "closed":
            self.vad_worker = None

    def _finish_session(self, *, cancel: bool) -> None:
        vad_worker = self.vad_worker
        asr_worker = self.asr_worker
        self.vad_worker = None
        self.asr_worker = None
        if vad_worker is not None:
            vad_worker.cancel() if cancel else vad_worker.finish()
        if asr_worker is not None:
            asr_worker.cancel() if cancel else asr_worker.finish()
        if self.recorder is not None:
            self.recorder.close()
            self.recorder = None

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.arm_button.setEnabled(enabled)
        self.disarm_button.setEnabled(enabled)
        self.stop_button.setEnabled(enabled)
        self.shift.setEnabled(enabled)
