from __future__ import annotations

from collections import deque
from datetime import datetime
import json
from pathlib import Path
import sys
from threading import Thread

from .mic_capture_debug import DebugLogger
from .mic_capture_ble import MicBleClientThread, scan_mic_devices
from .mic_capture_protocol import (
    AudioFrame,
    DeviceStatusFrame,
    ENCODING_IMA_ADPCM,
    ENCODING_PCM16,
    FLAG_CLIPPED,
    PcmWaveRecorder,
    WakeEventFrame,
)
from .funasr_vad import FunAsrVadSettings, FunAsrVadThread, VadEvent
from .volc_asr_client import (
    VolcAsrClientThread,
    VolcAsrEvent,
    VolcAsrSettings,
    load_volc_asr_settings,
    save_volc_asr_settings,
)


def main() -> int:
    try:
        import numpy as np
        import pyqtgraph as pg
        from PySide6.QtCore import QObject, QTimer, Signal
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDoubleSpinBox,
            QFileDialog,
            QFormLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPlainTextEdit,
            QPushButton,
            QTabWidget,
            QSpinBox,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise SystemExit(
            "缺少上位机依赖；请先运行 .\\tools\\project.ps1 pc-setup"
        ) from exc

    class Bridge(QObject):
        frame = Signal(object, object)
        state = Signal(str)
        error = Signal(object)
        scan_finished = Signal(int, object, object)
        asr_event = Signal(object)
        vad_event = Signal(object)

    class Window(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("INMP441 BLE 采集测试")
            self.resize(980, 680)
            self.bridge = Bridge()
            self.bridge.frame.connect(self.on_frame)
            self.bridge.state.connect(self.on_state)
            self.bridge.error.connect(self.on_error)
            self.bridge.scan_finished.connect(self.finish_scan)
            self.bridge.asr_event.connect(self.on_asr_event)
            self.bridge.vad_event.connect(self.on_vad_event)
            self.worker: MicBleClientThread | None = None
            self.asr_worker: VolcAsrClientThread | None = None
            self.vad_worker: FunAsrVadThread | None = None
            self.scan_worker: Thread | None = None
            self.scan_generation = 0
            self.recorder: PcmWaveRecorder | None = None
            self.samples: deque[int] = deque(maxlen=32_000)
            self.latest_stats = None
            self.device_status: DeviceStatusFrame | None = None
            self.capture_mode: str | None = None

            root = QWidget()
            layout = QVBoxLayout(root)
            connection_row = QHBoxLayout()
            self.devices = QComboBox()
            self.scan_button = QPushButton("扫描")
            self.connect_button = QPushButton("连接")
            self.state_label = QLabel("未连接")
            connection_row.addWidget(self.devices, 1)
            connection_row.addWidget(self.scan_button)
            connection_row.addWidget(self.connect_button)
            connection_row.addWidget(self.state_label)
            layout.addLayout(connection_row)

            self.asr_settings_path = Path.cwd() / "data" / "volc_asr_settings.json"
            self.vad_settings_path = Path.cwd() / "data" / "funasr_vad_settings.json"
            self.debug_log_path = Path.cwd() / "data" / "mic_capture_debug.log"
            self.debug_logger = DebugLogger(self.debug_log_path)
            try:
                settings = load_volc_asr_settings(self.asr_settings_path)
            except Exception as exc:
                settings = VolcAsrSettings.from_environment()
                QMessageBox.warning(
                    self,
                    "ASR 配置读取失败",
                    f"无法读取本地 ASR 配置，将使用环境变量/default。\n\n{exc}",
                )
            try:
                vad_settings = self.load_vad_settings()
            except Exception as exc:
                vad_settings = FunAsrVadSettings()
                QMessageBox.warning(
                    self,
                    "VAD 配置读取失败",
                    f"无法读取本地 VAD 配置，将使用默认值。\n\n{exc}",
                )

            tabs = QTabWidget()
            asr_tab = QWidget()
            asr_layout = QVBoxLayout(asr_tab)

            asr_group = QGroupBox("豆包 / 火山流式 ASR")
            asr_form = QFormLayout(asr_group)
            self.asr_endpoint = QLineEdit(settings.endpoint)
            self.asr_resource_id = QLineEdit(settings.resource_id)
            self.asr_auth_mode = QComboBox()
            self.asr_auth_mode.addItem("API Key", "api_key")
            self.asr_auth_mode.addItem("App Key + Access Key", "legacy")
            mode_index = self.asr_auth_mode.findData(settings.auth_mode)
            self.asr_auth_mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
            self.asr_api_key = QLineEdit(settings.api_key)
            self.asr_api_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.asr_app_key = QLineEdit(settings.app_key)
            self.asr_access_key = QLineEdit(settings.access_key)
            self.asr_access_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.asr_uid = QLineEdit(settings.uid)
            self.asr_model = QLineEdit(settings.model_name)
            self.asr_queue_depth = QSpinBox()
            self.asr_queue_depth.setRange(8, 4096)
            self.asr_queue_depth.setValue(settings.audio_queue_depth)
            self.asr_chunk_ms = QSpinBox()
            self.asr_chunk_ms.setRange(20, 1000)
            self.asr_chunk_ms.setSingleStep(20)
            self.asr_chunk_ms.setValue(settings.audio_chunk_ms)
            self.asr_connect_timeout = QDoubleSpinBox()
            self.asr_connect_timeout.setRange(1.0, 60.0)
            self.asr_connect_timeout.setSingleStep(1.0)
            self.asr_connect_timeout.setValue(settings.connect_timeout_s)
            self.asr_receive_timeout = QDoubleSpinBox()
            self.asr_receive_timeout.setRange(0.02, 5.0)
            self.asr_receive_timeout.setSingleStep(0.02)
            self.asr_receive_timeout.setDecimals(2)
            self.asr_receive_timeout.setValue(settings.receive_timeout_s)
            self.asr_final_timeout = QDoubleSpinBox()
            self.asr_final_timeout.setRange(1.0, 30.0)
            self.asr_final_timeout.setSingleStep(1.0)
            self.asr_final_timeout.setValue(settings.final_timeout_s)
            self.asr_end_window_ms = QSpinBox()
            self.asr_end_window_ms.setRange(0, 5000)
            self.asr_end_window_ms.setSingleStep(100)
            self.asr_end_window_ms.setValue(settings.end_window_size_ms)
            self.asr_force_speech_ms = QSpinBox()
            self.asr_force_speech_ms.setRange(0, 10000)
            self.asr_force_speech_ms.setSingleStep(100)
            self.asr_force_speech_ms.setValue(settings.force_to_speech_time_ms)
            asr_form.addRow("Endpoint", self.asr_endpoint)
            asr_form.addRow("Resource ID", self.asr_resource_id)
            asr_form.addRow("鉴权模式", self.asr_auth_mode)
            asr_form.addRow("API Key", self.asr_api_key)
            asr_form.addRow("App Key", self.asr_app_key)
            asr_form.addRow("Access Key", self.asr_access_key)
            asr_form.addRow("UID", self.asr_uid)
            asr_form.addRow("模型", self.asr_model)
            asr_form.addRow("ASR 队列深度", self.asr_queue_depth)
            asr_form.addRow("ASR 发送分片 ms", self.asr_chunk_ms)
            asr_form.addRow("连接超时 s", self.asr_connect_timeout)
            asr_form.addRow("接收轮询超时 s", self.asr_receive_timeout)
            asr_form.addRow("final 等待超时 s", self.asr_final_timeout)
            asr_form.addRow("end_window_size ms", self.asr_end_window_ms)
            asr_form.addRow("force_to_speech_time ms", self.asr_force_speech_ms)
            self.save_asr_settings_button = QPushButton("保存 ASR/VAD 配置")
            asr_form.addRow("本地配置", self.save_asr_settings_button)
            asr_layout.addWidget(asr_group)

            vad_group = QGroupBox("自动断句")
            vad_layout = QFormLayout(vad_group)
            self.vad_enabled = QCheckBox("启用 FunASR FSMN-VAD 自动停止")
            self.vad_enabled.setChecked(True)
            self.vad_model = QLineEdit(vad_settings.model)
            self.vad_device = QLineEdit(vad_settings.device)
            self.vad_sample_rate = QSpinBox()
            self.vad_sample_rate.setRange(8000, 48000)
            self.vad_sample_rate.setSingleStep(1000)
            self.vad_sample_rate.setValue(vad_settings.sample_rate)
            self.vad_chunk_ms = QSpinBox()
            self.vad_chunk_ms.setRange(20, 1000)
            self.vad_chunk_ms.setSingleStep(20)
            self.vad_chunk_ms.setValue(vad_settings.chunk_ms)
            self.vad_queue_depth = QSpinBox()
            self.vad_queue_depth.setRange(8, 4096)
            self.vad_queue_depth.setValue(vad_settings.queue_depth)
            self.vad_model_kwargs = QLineEdit(vad_settings.model_kwargs_json)
            self.vad_model_kwargs.setPlaceholderText('例如 {"disable_update": true}')
            self.vad_generate_kwargs = QLineEdit(vad_settings.generate_kwargs_json)
            self.vad_generate_kwargs.setPlaceholderText('例如 {"max_end_silence_time": 800}')
            self.vad_status_label = QLabel("VAD 尚未启动")
            self.vad_status_label.setWordWrap(True)
            self.debug_log_label = QLabel(f"调试日志：{self.debug_log_path}")
            self.debug_log_label.setWordWrap(True)
            vad_layout.addRow(self.vad_enabled)
            vad_layout.addRow("VAD 模型", self.vad_model)
            vad_layout.addRow("Device", self.vad_device)
            vad_layout.addRow("采样率", self.vad_sample_rate)
            vad_layout.addRow("VAD chunk ms", self.vad_chunk_ms)
            vad_layout.addRow("VAD 队列深度", self.vad_queue_depth)
            vad_layout.addRow("AutoModel kwargs JSON", self.vad_model_kwargs)
            vad_layout.addRow("generate kwargs JSON", self.vad_generate_kwargs)
            vad_layout.addRow("VAD 状态", self.vad_status_label)
            vad_layout.addRow("日志", self.debug_log_label)
            asr_layout.addWidget(vad_group)

            controls = QHBoxLayout()
            self.encoding = QComboBox()
            self.encoding.addItem("IMA-ADPCM（推荐，16 kHz 实时传输）", "ADPCM")
            self.encoding.addItem("PCM8（带宽压力测试）", "PCM8")
            self.encoding.addItem("PCM16（带宽压力测试）", "PCM16")
            self.shift = QSpinBox()
            self.shift.setRange(10, 20)
            self.shift.setValue(16)
            self.start_button = QPushButton("等待 Hi ESP 并识别")
            self.stop_button = QPushButton("停止并等待 final")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            controls.addWidget(QLabel("传输格式"))
            controls.addWidget(self.encoding)
            controls.addWidget(QLabel("I2S 右移"))
            controls.addWidget(self.shift)
            controls.addWidget(self.start_button)
            controls.addWidget(self.stop_button)
            asr_layout.addLayout(controls)

            self.asr_status_label = QLabel("ASR 尚未连接")
            self.asr_partial_label = QLabel("Partial：--")
            self.asr_partial_label.setWordWrap(True)
            self.asr_final_text = QPlainTextEdit()
            self.asr_final_text.setReadOnly(True)
            self.asr_final_text.setPlaceholderText("流式 final 文本会显示在这里")
            asr_layout.addWidget(self.asr_status_label)
            asr_layout.addWidget(self.asr_partial_label)
            asr_layout.addWidget(self.asr_final_text, 1)

            diagnostic_tab = QWidget()
            diagnostic_layout = QVBoxLayout(diagnostic_tab)
            diagnostic_controls = QHBoxLayout()
            self.wave_test_button = QPushButton("手动测试波形")
            self.wave_test_button.setEnabled(False)
            diagnostic_controls.addWidget(self.wave_test_button)
            diagnostic_controls.addStretch(1)
            diagnostic_layout.addLayout(diagnostic_controls)

            path_row = QHBoxLayout()
            self.path = QLineEdit(
                str(Path.cwd() / "captures" / f"inmp441-{datetime.now():%Y%m%d-%H%M%S}.wav")
            )
            self.browse_button = QPushButton("选择 WAV")
            path_row.addWidget(self.path, 1)
            path_row.addWidget(self.browse_button)
            diagnostic_layout.addLayout(path_row)

            self.plot = pg.PlotWidget()
            self.plot.setLabel("left", "PCM")
            self.plot.setLabel("bottom", "最近 2 秒样本")
            self.plot.setYRange(-32768, 32767)
            self.curve = self.plot.plot(pen=pg.mkPen("#42a5f5", width=1))
            diagnostic_layout.addWidget(self.plot, 1)

            form = QFormLayout()
            self.level_label = QLabel("RMS 0 / Peak 0")
            self.link_label = QLabel("帧 0 / 样本 0 / 丢帧 0 / CRC 0")
            self.device_label = QLabel("I2S 错误 0 / TX 错误 0 / 削顶帧 0")
            self.file_label = QLabel("尚未录制")
            form.addRow("电平", self.level_label)
            form.addRow("PC 链路", self.link_label)
            form.addRow("ESP 状态", self.device_label)
            form.addRow("文件", self.file_label)
            diagnostic_layout.addLayout(form)

            tabs.addTab(asr_tab, "ASR")
            tabs.addTab(diagnostic_tab, "诊断")
            layout.addWidget(tabs, 1)
            self.setCentralWidget(root)

            self.scan_button.clicked.connect(self.scan)
            self.connect_button.clicked.connect(self.toggle_connection)
            self.start_button.clicked.connect(self.start_capture)
            self.wave_test_button.clicked.connect(self.start_wave_test)
            self.stop_button.clicked.connect(self.stop_capture)
            self.browse_button.clicked.connect(self.choose_path)
            self.save_asr_settings_button.clicked.connect(self.save_asr_settings)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.refresh_plot)
            self.timer.start(100)
            self.arm_timer = QTimer(self)
            self.arm_timer.setSingleShot(True)
            self.arm_timer.timeout.connect(self.verify_arm_ack)

        def scan(self) -> None:
            self.scan_generation += 1
            generation = self.scan_generation
            self.scan_button.setEnabled(False)
            self.state_label.setText("扫描中…")
            self.scan_worker = Thread(
                target=self.scan_in_background,
                args=(generation,),
                name="MicBleDeviceScanner",
                daemon=True,
            )
            self.scan_worker.start()

        def scan_in_background(self, generation: int) -> None:
            try:
                found = tuple(scan_mic_devices())
                error = None
            except Exception as exc:
                found = ()
                error = exc
            self.bridge.scan_finished.emit(generation, found, error)

        def finish_scan(
            self,
            generation: int,
            found: object,
            error: object,
        ) -> None:
            if generation != self.scan_generation:
                return
            self.scan_worker = None
            self.scan_button.setEnabled(True)
            self.devices.clear()
            if error is not None:
                QMessageBox.critical(self, "扫描失败", str(error))
                self.state_label.setText("扫描失败")
                return
            devices = tuple(found)
            for device in devices:
                self.devices.addItem(f"{device.name}  {device.address}", device.address)
            self.state_label.setText(f"发现 {len(devices)} 台")

        def toggle_connection(self) -> None:
            if self.worker is not None:
                self.worker.disconnect()
                self.connect_button.setEnabled(False)
                return
            address = self.devices.currentData()
            if not address:
                QMessageBox.information(self, "未选择设备", "请先扫描并选择 CollarMic 设备。")
                return
            self.worker = MicBleClientThread(
                str(address),
                on_frame=self.bridge.frame.emit,
                on_state=self.bridge.state.emit,
                on_error=self.bridge.error.emit,
            )
            self.worker.start()

        def on_state(self, state: str) -> None:
            names = {
                "connecting": "连接中…",
                "connected": "已连接",
                "disconnected": "未连接",
            }
            self.state_label.setText(names.get(state, state))
            connected = state == "connected"
            self.start_button.setEnabled(connected and self.recorder is None)
            self.wave_test_button.setEnabled(connected and self.recorder is None)
            self.connect_button.setText("断开" if connected else "连接")
            self.connect_button.setEnabled(True)
            if state == "disconnected":
                self.worker = None
                self.finish_recording()

        def on_error(self, error: BaseException) -> None:
            QMessageBox.critical(self, "BLE 错误", str(error))

        def choose_path(self) -> None:
            selected, _ = QFileDialog.getSaveFileName(
                self, "保存麦克风 WAV", self.path.text(), "WAV audio (*.wav)"
            )
            if selected:
                self.path.setText(selected if selected.lower().endswith(".wav") else selected + ".wav")

        def start_capture(self) -> None:
            try:
                self.asr_settings().validate()
                if self.vad_enabled.isChecked():
                    self.vad_settings().validate()
            except Exception as exc:
                QMessageBox.warning(self, "ASR 配置不完整", str(exc))
                return
            self.debug_logger.event(
                "capture.start",
                mode="wake",
                log_path=str(self.debug_log_path),
            )
            if not self.begin_recording("wake"):
                return
            self.worker.send(f"SHIFT {self.shift.value()}")
            self.worker.send(f"ARM {self.encoding.currentData()}")
            self.state_label.setText("正在确认 Hi ESP 固件…")
            self.asr_status_label.setText("等待 Hi ESP 唤醒")
            self.asr_partial_label.setText("Partial：--")
            self.vad_status_label.setText(
                "VAD 等待唤醒" if self.vad_enabled.isChecked() else "VAD 已关闭"
            )
            self.asr_final_text.clear()
            self.file_label.setText(
                f"等待设备确认；唤醒前不会显示波形，唤醒后写入 {self.path.text()}"
            )
            self.arm_timer.start(1500)

        def start_wave_test(self) -> None:
            if not self.begin_recording("manual"):
                return
            self.worker.send(f"SHIFT {self.shift.value()}")
            self.worker.send(f"START {self.encoding.currentData()}")
            self.state_label.setText("手动波形测试中")
            self.file_label.setText(f"无需唤醒，正在写入 {self.path.text()}")

        def begin_recording(self, mode: str) -> bool:
            if self.worker is None:
                return False
            try:
                self.recorder = PcmWaveRecorder(self.path.text())
            except Exception as exc:
                QMessageBox.critical(self, "无法创建 WAV", str(exc))
                return False
            self.capture_mode = mode
            self.samples.clear()
            self.curve.setData([])
            self.latest_stats = None
            self.device_status = None
            self.start_button.setEnabled(False)
            self.wave_test_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.encoding.setEnabled(False)
            self.shift.setEnabled(False)
            self.set_asr_settings_enabled(False)
            return True

        def verify_arm_ack(self) -> None:
            if self.capture_mode != "wake":
                return
            if self.device_status is not None and (
                self.device_status.armed or self.device_status.streaming
            ):
                return
            if self.worker is not None:
                self.worker.send("STOP")
            self.finish_recording()
            self.state_label.setText("设备未进入 Hi ESP 等待状态")
            self.file_label.setText(
                "当前设备固件不支持 ARM；请先刷写 Hi ESP 固件，"
                "也可点击“手动测试波形”检查麦克风链路。"
            )
            QMessageBox.warning(
                self,
                "Hi ESP 固件未就绪",
                "设备没有确认 ARM 命令。当前硬件很可能仍在运行旧的麦克风测试固件。\n\n"
                "请先刷写包含官方 Hi ESP 模型的新固件；"
                "如果只想确认麦克风和蓝牙是否正常，可点击“手动测试波形”。",
            )

        def stop_capture(self) -> None:
            self.debug_logger.event(
                "capture.stop",
                mode=self.capture_mode,
                has_asr=self.asr_worker is not None,
                has_vad=self.vad_worker is not None,
            )
            if self.worker is not None:
                for _ in range(3):
                    self.worker.send("STOP")
            self.finish_asr_session()
            self.finish_vad_session()
            self.finish_recording()

        def finish_recording(self) -> None:
            self.arm_timer.stop()
            if self.recorder is not None:
                count = self.recorder.sample_count
                self.recorder.close()
                self.file_label.setText(f"已保存 {count} 个样本：{self.path.text()}")
                self.recorder = None
            self.capture_mode = None
            self.stop_button.setEnabled(False)
            self.encoding.setEnabled(True)
            self.shift.setEnabled(True)
            self.set_asr_settings_enabled(True)
            self.start_button.setEnabled(self.worker is not None)
            self.wave_test_button.setEnabled(self.worker is not None)

        def set_asr_settings_enabled(self, enabled: bool) -> None:
            for widget in (
                self.asr_endpoint,
                self.asr_resource_id,
                self.asr_auth_mode,
                self.asr_api_key,
                self.asr_app_key,
                self.asr_access_key,
                self.asr_uid,
                self.asr_model,
                self.asr_queue_depth,
                self.asr_chunk_ms,
                self.asr_connect_timeout,
                self.asr_receive_timeout,
                self.asr_final_timeout,
                self.asr_end_window_ms,
                self.asr_force_speech_ms,
                self.save_asr_settings_button,
                self.vad_enabled,
                self.vad_model,
                self.vad_device,
                self.vad_sample_rate,
                self.vad_chunk_ms,
                self.vad_queue_depth,
                self.vad_model_kwargs,
                self.vad_generate_kwargs,
            ):
                widget.setEnabled(enabled)

        def asr_settings(self) -> VolcAsrSettings:
            return VolcAsrSettings(
                endpoint=self.asr_endpoint.text().strip(),
                auth_mode=str(self.asr_auth_mode.currentData()),
                api_key=self.asr_api_key.text().strip(),
                app_key=self.asr_app_key.text().strip(),
                access_key=self.asr_access_key.text().strip(),
                resource_id=self.asr_resource_id.text().strip(),
                uid=self.asr_uid.text().strip() or "smart-neckband-pc",
                model_name=self.asr_model.text().strip() or "bigmodel",
                connect_timeout_s=float(self.asr_connect_timeout.value()),
                receive_timeout_s=float(self.asr_receive_timeout.value()),
                final_timeout_s=float(self.asr_final_timeout.value()),
                audio_queue_depth=int(self.asr_queue_depth.value()),
                audio_chunk_ms=int(self.asr_chunk_ms.value()),
                end_window_size_ms=int(self.asr_end_window_ms.value()),
                force_to_speech_time_ms=int(self.asr_force_speech_ms.value()),
            )

        def vad_settings(self) -> FunAsrVadSettings:
            return FunAsrVadSettings(
                model=self.vad_model.text().strip() or "fsmn-vad",
                device=self.vad_device.text().strip() or "cpu",
                sample_rate=int(self.vad_sample_rate.value()),
                chunk_ms=int(self.vad_chunk_ms.value()),
                queue_depth=int(self.vad_queue_depth.value()),
                model_kwargs_json=self.vad_model_kwargs.text().strip(),
                generate_kwargs_json=self.vad_generate_kwargs.text().strip(),
            )

        def load_vad_settings(self) -> FunAsrVadSettings:
            if not self.vad_settings_path.exists():
                return FunAsrVadSettings()
            data = json.loads(self.vad_settings_path.read_text(encoding="utf-8"))
            settings = FunAsrVadSettings.from_json_dict(data)
            settings.validate()
            return settings

        def save_vad_settings(self, settings: FunAsrVadSettings) -> None:
            self.vad_settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.vad_settings_path.write_text(
                json.dumps(settings.to_json_dict(), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )

        def save_asr_settings(self) -> None:
            try:
                settings = self.asr_settings()
                vad_settings = self.vad_settings()
                settings.validate()
                vad_settings.validate()
                save_volc_asr_settings(self.asr_settings_path, settings)
                self.save_vad_settings(vad_settings)
            except Exception as exc:
                QMessageBox.warning(self, "ASR 配置保存失败", str(exc))
                return
            QMessageBox.information(
                self,
                "ASR/VAD 配置已保存",
                f"已保存到：\n{self.asr_settings_path}\n{self.vad_settings_path}",
            )

        def start_asr_session(self) -> None:
            if self.asr_worker is not None:
                return
            try:
                settings = self.asr_settings()
                self.asr_worker = VolcAsrClientThread(
                    settings,
                    on_event=self.bridge.asr_event.emit,
                    debug_logger=self.debug_logger,
                )
            except Exception as exc:
                self.asr_status_label.setText(f"ASR 配置错误：{exc}")
                self.debug_logger.exception("asr.start_error", exc)
                return
            self.asr_worker.start()

        def finish_asr_session(self) -> None:
            if self.asr_worker is not None:
                self.asr_status_label.setText("ASR 等待 final…")
                self.asr_worker.finish()

        def start_vad_session(self) -> None:
            if not self.vad_enabled.isChecked():
                self.vad_status_label.setText("VAD 已关闭")
                return
            if self.vad_worker is not None:
                return
            try:
                settings = self.vad_settings()
                self.vad_worker = FunAsrVadThread(
                    settings,
                    on_event=self.bridge.vad_event.emit,
                    debug_logger=self.debug_logger,
                )
            except Exception as exc:
                self.vad_status_label.setText(f"VAD 配置错误：{exc}")
                self.debug_logger.exception("vad.start_error", exc)
                return
            self.vad_worker.start()

        def finish_vad_session(self) -> None:
            if self.vad_worker is not None:
                self.vad_worker.finish()

        def on_frame(self, frame: object, stats: object) -> None:
            self.latest_stats = stats
            if isinstance(frame, AudioFrame):
                self.samples.extend(frame.samples)
                if self.recorder is not None:
                    self.recorder.write(frame.samples)
                if self.capture_mode == "wake" and self.asr_worker is not None:
                    self.asr_worker.feed(frame.samples)
                if self.capture_mode == "wake" and self.vad_worker is not None:
                    self.vad_worker.feed(frame.samples)
                values = np.asarray(frame.samples, dtype=np.float64)
                rms = float(np.sqrt(np.mean(values * values))) if len(values) else 0.0
                peak = int(np.max(np.abs(values))) if len(values) else 0
                clipped = " / 本帧削顶" if frame.flags & FLAG_CLIPPED else ""
                if frame.encoding == ENCODING_PCM16:
                    mode = "PCM16"
                elif frame.encoding == ENCODING_IMA_ADPCM:
                    mode = "IMA-ADPCM→PCM16"
                else:
                    mode = "PCM8→PCM16"
                self.level_label.setText(f"{mode} / RMS {rms:.0f} / Peak {peak}{clipped}")
            elif isinstance(frame, DeviceStatusFrame):
                self.device_status = frame
                if self.capture_mode == "wake" and frame.armed:
                    self.arm_timer.stop()
                    self.state_label.setText("已等待 Hi ESP；唤醒前不传输波形")
                    self.file_label.setText(
                        f"请说 Hi ESP；唤醒后写入 {self.path.text()}"
                    )
            elif isinstance(frame, WakeEventFrame):
                self.arm_timer.stop()
                self.state_label.setText("Hi ESP 已唤醒，正在录音")
                self.debug_logger.event(
                    "wake.detected",
                    wake_count=frame.wake_count,
                    detected_sample_index=frame.detected_sample_index,
                    word_index=frame.word_index,
                )
                self.start_asr_session()
                self.start_vad_session()
                if self.recorder is not None:
                    self.file_label.setText(
                        f"第 {frame.wake_count} 次唤醒，正在写入 {self.path.text()}"
                    )

            if self.latest_stats is not None:
                self.link_label.setText(
                    f"帧 {self.latest_stats.audio_frames} / "
                    f"样本 {self.latest_stats.samples} / "
                    f"唤醒 {self.latest_stats.wake_events} / "
                    f"丢帧 {self.latest_stats.sequence_gaps} / "
                    f"CRC {self.latest_stats.crc_errors} / "
                    f"废弃字节 {self.latest_stats.discarded_bytes}"
                )
            if self.device_status is not None:
                status = self.device_status
                self.device_label.setText(
                    f"I2S 错误 {status.i2s_errors} / "
                    f"TX 错误 {status.tx_errors} / "
                    f"削顶帧 {status.clipped_frames} / "
                    f"右移 {status.pcm_shift} / "
                    f"模式 {'等待唤醒' if status.armed else '传输' if status.streaming else '停止'} / "
                    f"连接间隔 {status.reserved * 1.25:.2f} ms"
                )

        def refresh_plot(self) -> None:
            if self.samples:
                self.curve.setData(list(self.samples))

        def on_asr_event(self, event: object) -> None:
            if not isinstance(event, VolcAsrEvent):
                return
            self.debug_logger.event(
                "asr.ui_event",
                kind=event.kind,
                text=event.text,
                detail=event.detail,
            )
            if event.kind == "status":
                self.asr_status_label.setText(event.detail)
            elif event.kind == "partial":
                self.asr_partial_label.setText(f"Partial：{event.text}")
            elif event.kind == "final":
                self.asr_status_label.setText("ASR final 已返回")
                self.asr_partial_label.setText("Partial：--")
                self.asr_final_text.appendPlainText(event.text)
            elif event.kind == "error":
                self.asr_status_label.setText(f"ASR 错误：{event.detail}")
            elif event.kind == "closed":
                self.asr_worker = None

        def on_vad_event(self, event: object) -> None:
            if not isinstance(event, VadEvent):
                return
            self.debug_logger.event(
                "vad.ui_event",
                kind=event.kind,
                detail=event.detail,
            )
            if event.kind == "status":
                self.vad_status_label.setText(event.detail)
            elif event.kind == "speech_start":
                self.vad_status_label.setText(f"检测到开始说话：{event.detail}")
            elif event.kind == "speech_end":
                self.vad_status_label.setText(f"检测到说话结束：{event.detail}")
                if self.capture_mode == "wake" and self.recorder is not None:
                    self.stop_capture()
            elif event.kind == "error":
                self.vad_status_label.setText(f"VAD 错误：{event.detail}")
            elif event.kind == "closed":
                self.vad_worker = None

        def closeEvent(self, event: object) -> None:
            self.scan_generation += 1
            if self.vad_worker is not None:
                self.vad_worker.cancel()
                self.vad_worker.join(timeout=2.0)
            if self.asr_worker is not None:
                self.asr_worker.cancel()
                self.asr_worker.join(timeout=2.0)
            self.finish_recording()
            if self.worker is not None:
                self.worker.disconnect()
                self.worker.join(timeout=2.0)
            event.accept()

    app = QApplication(sys.argv)
    pg.setConfigOptions(antialias=False)
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
