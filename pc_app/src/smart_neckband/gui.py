from __future__ import annotations

from pathlib import Path
from threading import Event, Lock, Thread
import time

from .analysis import EcgAnalysisResult, analyze_recent_ecg
from .attitude import ComplementaryAttitudeFilter, Orientation
from .buffers import ImuSample
from .protocol import FLAG_LO_MINUS, FLAG_LO_PLUS
from .serial_io import PcDataStores, SerialPacketReader, list_serial_ports


class EcgAnalysisWorker:
    def __init__(self, stores: PcDataStores) -> None:
        self.stores = stores
        self._stop = Event()
        self._lock = Lock()
        self._result: EcgAnalysisResult | None = None
        self._thread = Thread(target=self._run, name="EcgAnalysisWorker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def latest(self) -> EcgAnalysisResult | None:
        with self._lock:
            return self._result

    def _run(self) -> None:
        while not self._stop.is_set():
            result = analyze_recent_ecg(self.stores.ecg.snapshot())
            with self._lock:
                self._result = result
            self._stop.wait(0.5)


class AttitudeWorker:
    def __init__(self, stores: PcDataStores) -> None:
        self.stores = stores
        self.filter = ComplementaryAttitudeFilter()
        self._stop = Event()
        self._lock = Lock()
        self._last_index: int | None = None
        self._orientation = Orientation(0.0, 0.0, 0.0)
        self._latest_sample: ImuSample | None = None
        self._thread = Thread(target=self._run, name="AttitudeWorker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def reset_orientation(self) -> None:
        self.filter.reset_orientation()

    def latest(self) -> tuple[Orientation, ImuSample | None]:
        with self._lock:
            return self._orientation, self._latest_sample

    def _run(self) -> None:
        while not self._stop.is_set():
            samples = self.stores.imu.snapshot()
            for sample in samples:
                if self._last_index is not None and sample.sample_index <= self._last_index:
                    continue
                orientation = self.filter.update(sample)
                with self._lock:
                    self._orientation = orientation
                    self._latest_sample = sample
                    self._last_index = sample.sample_index
            self._stop.wait(0.02)


class MainWindow:
    def __init__(self) -> None:
        from PySide6 import QtCore, QtWidgets
        import pyqtgraph as pg

        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.pg = pg
        self.stores = PcDataStores.create()
        self.reader: SerialPacketReader | None = None
        self.ecg_worker = EcgAnalysisWorker(self.stores)
        self.attitude_worker = AttitudeWorker(self.stores)
        self.ecg_worker.start()
        self.attitude_worker.start()

        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle("SmartCollar V0")
        self.window.resize(1280, 820)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        toolbar = QtWidgets.QHBoxLayout()
        self.port_combo = QtWidgets.QComboBox()
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.disconnect_button = QtWidgets.QPushButton("Disconnect")
        self.reset_button = QtWidgets.QPushButton("Reset Orientation")
        toolbar.addWidget(self.port_combo, 2)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.connect_button)
        toolbar.addWidget(self.disconnect_button)
        toolbar.addWidget(self.reset_button)
        layout.addLayout(toolbar)

        status_layout = QtWidgets.QGridLayout()
        self.hr_label = QtWidgets.QLabel("HR --")
        self.rr_label = QtWidgets.QLabel("RR --")
        self.sqi_label = QtWidgets.QLabel("SQI --")
        self.lead_label = QtWidgets.QLabel("LEAD --")
        self.loss_label = QtWidgets.QLabel("LOSS 0")
        self.crc_label = QtWidgets.QLabel("CRC 0")
        for column, widget in enumerate(
            (self.hr_label, self.rr_label, self.sqi_label, self.lead_label, self.loss_label, self.crc_label)
        ):
            status_layout.addWidget(widget, 0, column)
        layout.addLayout(status_layout)

        splitter = QtWidgets.QSplitter()
        splitter.setOrientation(QtCore.Qt.Vertical)
        self.raw_plot = pg.PlotWidget(title="Raw ECG - last 10 s")
        self.clean_plot = pg.PlotWidget(title="Clean ECG - last 10 s")
        self.raw_curve = self.raw_plot.plot(pen=pg.mkPen("#1769aa", width=1))
        self.clean_curve = self.clean_plot.plot(pen=pg.mkPen("#2e7d32", width=1))
        self.peak_scatter = pg.ScatterPlotItem(pen=pg.mkPen("#c62828"), brush=pg.mkBrush("#c62828"), size=8)
        self.clean_plot.addItem(self.peak_scatter)
        splitter.addWidget(self.raw_plot)
        splitter.addWidget(self.clean_plot)
        layout.addWidget(splitter, 3)

        lower = QtWidgets.QHBoxLayout()
        imu_panel = QtWidgets.QGridLayout()
        self.imu_labels: dict[str, object] = {}
        for row, name in enumerate(("ax", "ay", "az", "gx", "gy", "gz", "roll", "pitch", "yaw")):
            label = QtWidgets.QLabel(f"{name} --")
            self.imu_labels[name] = label
            imu_panel.addWidget(label, row // 3, row % 3)
        self.yaw_note = QtWidgets.QLabel("Yaw is gyro-integrated and may drift.")
        imu_panel.addWidget(self.yaw_note, 3, 0, 1, 3)
        lower.addLayout(imu_panel, 1)

        self.gl_widget = None
        self.gl_box = None
        try:
            import pyqtgraph.opengl as gl
            from PySide6 import QtGui

            self.gl_widget = gl.GLViewWidget()
            self.gl_widget.setCameraPosition(distance=4)
            grid = gl.GLGridItem()
            self.gl_widget.addItem(grid)
            self.gl_box = gl.GLBoxItem(size=QtGui.QVector3D(1.6, 0.5, 0.25), color=(0.1, 0.45, 0.7, 0.55))
            self.gl_box.translate(-0.8, -0.25, -0.125)
            self.gl_widget.addItem(self.gl_box)
            lower.addWidget(self.gl_widget, 2)
        except Exception:
            lower.addWidget(QtWidgets.QLabel("3D view requires pyqtgraph OpenGL support."), 2)
        layout.addLayout(lower, 2)

        self.window.setCentralWidget(central)
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.connect_serial)
        self.disconnect_button.clicked.connect(self.disconnect_serial)
        self.reset_button.clicked.connect(self.attitude_worker.reset_orientation)

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_view)
        self.timer.start(100)
        self.refresh_ports()

    def show(self) -> None:
        self.window.show()

    def close(self) -> None:
        self.disconnect_serial()
        self.ecg_worker.stop()
        self.attitude_worker.stop()

    def refresh_ports(self) -> None:
        self.port_combo.clear()
        try:
            ports = list_serial_ports()
        except RuntimeError as exc:
            self.port_combo.addItem(str(exc), "")
            return
        for port in ports:
            suffix = " BT" if port.is_bluetooth_candidate else ""
            self.port_combo.addItem(f"{port.device} - {port.description}{suffix}", port.device)

    def connect_serial(self) -> None:
        port = self.port_combo.currentData()
        if not port:
            return
        self.disconnect_serial()
        raw_path = Path("data") / f"smartcollar_v0_{time.strftime('%Y%m%d_%H%M%S')}.bin"
        self.reader = SerialPacketReader(port=port, stores=self.stores, raw_log_path=raw_path)
        self.reader.start()

    def disconnect_serial(self) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.reader = None

    def update_view(self) -> None:
        self._update_ecg()
        self._update_status_labels()
        self._update_imu()

    def _update_ecg(self) -> None:
        samples = self.stores.ecg.snapshot()
        recent = samples[-5000:]
        if recent:
            t0 = recent[0].timestamp_us
            x = [(sample.timestamp_us - t0) / 1_000_000.0 for sample in recent]
            y = [sample.raw_adc for sample in recent]
            self.raw_curve.setData(x, y)

        analysis = self.ecg_worker.latest()
        if analysis is not None and analysis.cleaned:
            x = [index / 500.0 for index in range(len(analysis.cleaned))]
            self.clean_curve.setData(x, analysis.cleaned)
            spots = [
                {"pos": (index / 500.0, analysis.cleaned[index])}
                for index in analysis.r_peak_indices
                if 0 <= index < len(analysis.cleaned)
            ]
            self.peak_scatter.setData(spots)
            self.hr_label.setText(
                f"HR {analysis.heart_rate_bpm:.1f}" if analysis.heart_rate_bpm is not None else "HR --"
            )
            self.rr_label.setText(
                f"RR {analysis.latest_rr_ms:.0f} ms" if analysis.latest_rr_ms is not None else "RR --"
            )
            self.sqi_label.setText(
                f"SQI {analysis.signal_quality:.2f}" if analysis.signal_quality is not None else f"SQI {analysis.message}"
            )

    def _update_status_labels(self) -> None:
        latest_status = self.stores.status.latest()
        if latest_status is not None:
            flags = latest_status.payload.lead_off_flags
            lead_text = "LEAD OK" if flags == 0 else "LEAD OFF"
            if flags & FLAG_LO_MINUS:
                lead_text += " LO-"
            if flags & FLAG_LO_PLUS:
                lead_text += " LO+"
            self.lead_label.setText(lead_text)
        stats = self.reader.stats if self.reader is not None else None
        if stats is not None:
            self.loss_label.setText(f"LOSS {stats.packets_lost}")
            self.crc_label.setText(f"CRC {stats.crc_errors}")

    def _update_imu(self) -> None:
        orientation, sample = self.attitude_worker.latest()
        if sample is not None:
            for name in ("ax", "ay", "az", "gx", "gy", "gz"):
                self.imu_labels[name].setText(f"{name} {getattr(sample, name)}")
        self.imu_labels["roll"].setText(f"roll {orientation.roll_deg:.1f}")
        self.imu_labels["pitch"].setText(f"pitch {orientation.pitch_deg:.1f}")
        self.imu_labels["yaw"].setText(f"yaw {orientation.yaw_deg:.1f}")
        if self.gl_box is not None:
            self.gl_box.resetTransform()
            self.gl_box.translate(-0.8, -0.25, -0.125)
            self.gl_box.rotate(orientation.yaw_deg, 0, 0, 1)
            self.gl_box.rotate(orientation.pitch_deg, 0, 1, 0)
            self.gl_box.rotate(orientation.roll_deg, 1, 0, 0)


def main() -> int:
    try:
        from PySide6 import QtWidgets
    except ImportError as exc:
        raise SystemExit("Install the gui optional dependencies: pyserial PySide6 pyqtgraph neurokit2 numpy") from exc

    app = QtWidgets.QApplication([])
    window = MainWindow()
    app.aboutToQuit.connect(window.close)
    window.show()
    return app.exec()
