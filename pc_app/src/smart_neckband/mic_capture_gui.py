from __future__ import annotations

from collections import deque
from datetime import datetime
from pathlib import Path
import sys

from .mic_capture_ble import MicBleClientThread, scan_mic_devices
from .mic_capture_protocol import (
    AudioFrame,
    DeviceStatusFrame,
    ENCODING_IMA_ADPCM,
    ENCODING_PCM16,
    FLAG_CLIPPED,
    PcmWaveRecorder,
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

    class Window(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("INMP441 BLE 采集测试")
            self.resize(980, 680)
            self.bridge = Bridge()
            self.bridge.frame.connect(self.on_frame)
            self.bridge.state.connect(self.on_state)
            self.bridge.error.connect(self.on_error)
            self.worker: MicBleClientThread | None = None
            self.recorder: PcmWaveRecorder | None = None
            self.samples: deque[int] = deque(maxlen=32_000)
            self.latest_stats = None
            self.device_status: DeviceStatusFrame | None = None

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
            self.start_button = QPushButton("开始采集")
            self.stop_button = QPushButton("停止并封装 WAV")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            controls.addWidget(QLabel("传输格式"))
            controls.addWidget(self.encoding)
            controls.addWidget(QLabel("I2S 右移"))
            controls.addWidget(self.shift)
            controls.addWidget(self.start_button)
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
            self.stop_button.clicked.connect(self.stop_capture)
            self.browse_button.clicked.connect(self.choose_path)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.refresh_plot)
            self.timer.start(100)

        def scan(self) -> None:
            self.scan_button.setEnabled(False)
            self.state_label.setText("扫描中…")
            QApplication.processEvents()
            try:
                found = scan_mic_devices()
                self.devices.clear()
                for device in found:
                    self.devices.addItem(f"{device.name}  {device.address}", device.address)
                self.state_label.setText(f"发现 {len(found)} 台")
            except Exception as exc:
                QMessageBox.critical(self, "扫描失败", str(exc))
                self.state_label.setText("扫描失败")
            finally:
                self.scan_button.setEnabled(True)

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
            if self.worker is None:
                return
            try:
                self.recorder = PcmWaveRecorder(self.path.text())
            except Exception as exc:
                QMessageBox.critical(self, "无法创建 WAV", str(exc))
                return
            self.samples.clear()
            self.latest_stats = None
            self.worker.send(f"SHIFT {self.shift.value()}")
            self.worker.send(f"START {self.encoding.currentData()}")
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.encoding.setEnabled(False)
            self.shift.setEnabled(False)
            self.file_label.setText(f"正在写入 {self.path.text()}")

        def stop_capture(self) -> None:
            if self.worker is not None:
                self.worker.send("STOP")
            self.finish_recording()

        def finish_recording(self) -> None:
            if self.recorder is not None:
                count = self.recorder.sample_count
                self.recorder.close()
                self.file_label.setText(f"已保存 {count} 个样本：{self.path.text()}")
                self.recorder = None
            self.stop_button.setEnabled(False)
            self.encoding.setEnabled(True)
            self.shift.setEnabled(True)
            self.start_button.setEnabled(self.worker is not None)

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

            if self.latest_stats is not None:
                self.link_label.setText(
                    f"帧 {self.latest_stats.audio_frames} / "
                    f"样本 {self.latest_stats.samples} / "
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
                    f"连接间隔 {status.reserved * 1.25:.2f} ms"
                )

        def refresh_plot(self) -> None:
            if self.samples:
                self.curve.setData(list(self.samples))

        def closeEvent(self, event: object) -> None:
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
