from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
import sys
from threading import Thread

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


def main() -> int:
    try:
        import numpy as np
        import pyqtgraph as pg
        from PySide6.QtCore import QObject, QTimer, Signal
        from PySide6.QtWidgets import (
            QApplication,
            QComboBox,
            QFileDialog,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPushButton,
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
            self.worker: MicBleClientThread | None = None
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

            controls = QHBoxLayout()
            self.encoding = QComboBox()
            self.encoding.addItem("IMA-ADPCM（推荐，16 kHz 实时传输）", "ADPCM")
            self.encoding.addItem("PCM8（带宽压力测试）", "PCM8")
            self.encoding.addItem("PCM16（带宽压力测试）", "PCM16")
            self.shift = QSpinBox()
            self.shift.setRange(10, 20)
            self.shift.setValue(16)
            self.start_button = QPushButton("等待 Hi ESP（唤醒后录音）")
            self.wave_test_button = QPushButton("手动测试波形")
            self.stop_button = QPushButton("停止并封装 WAV")
            self.start_button.setEnabled(False)
            self.wave_test_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            controls.addWidget(QLabel("传输格式"))
            controls.addWidget(self.encoding)
            controls.addWidget(QLabel("I2S 右移"))
            controls.addWidget(self.shift)
            controls.addWidget(self.start_button)
            controls.addWidget(self.wave_test_button)
            controls.addWidget(self.stop_button)
            layout.addLayout(controls)

            path_row = QHBoxLayout()
            self.path = QLineEdit(
                str(Path.cwd() / "captures" / f"inmp441-{datetime.now():%Y%m%d-%H%M%S}.wav")
            )
            self.browse_button = QPushButton("选择 WAV")
            path_row.addWidget(self.path, 1)
            path_row.addWidget(self.browse_button)
            layout.addLayout(path_row)

            self.plot = pg.PlotWidget()
            self.plot.setLabel("left", "PCM")
            self.plot.setLabel("bottom", "最近 2 秒样本")
            self.plot.setYRange(-32768, 32767)
            self.curve = self.plot.plot(pen=pg.mkPen("#42a5f5", width=1))
            layout.addWidget(self.plot, 1)

            form = QFormLayout()
            self.level_label = QLabel("RMS 0 / Peak 0")
            self.link_label = QLabel("帧 0 / 样本 0 / 丢帧 0 / CRC 0")
            self.device_label = QLabel("I2S 错误 0 / TX 错误 0 / 削顶帧 0")
            self.file_label = QLabel("尚未录制")
            form.addRow("电平", self.level_label)
            form.addRow("PC 链路", self.link_label)
            form.addRow("ESP 状态", self.device_label)
            form.addRow("文件", self.file_label)
            layout.addLayout(form)
            self.setCentralWidget(root)

            self.scan_button.clicked.connect(self.scan)
            self.connect_button.clicked.connect(self.toggle_connection)
            self.start_button.clicked.connect(self.start_capture)
            self.wave_test_button.clicked.connect(self.start_wave_test)
            self.stop_button.clicked.connect(self.stop_capture)
            self.browse_button.clicked.connect(self.choose_path)
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
            if not self.begin_recording("wake"):
                return
            self.worker.send(f"SHIFT {self.shift.value()}")
            self.worker.send(f"ARM {self.encoding.currentData()}")
            self.state_label.setText("正在确认 Hi ESP 固件…")
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
            if self.worker is not None:
                self.worker.send("STOP")
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
            self.start_button.setEnabled(self.worker is not None)
            self.wave_test_button.setEnabled(self.worker is not None)

        def on_frame(self, frame: object, stats: object) -> None:
            self.latest_stats = stats
            if isinstance(frame, AudioFrame):
                self.samples.extend(frame.samples)
                if self.recorder is not None:
                    self.recorder.write(frame.samples)
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

        def closeEvent(self, event: object) -> None:
            self.scan_generation += 1
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
