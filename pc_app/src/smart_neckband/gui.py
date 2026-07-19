from __future__ import annotations

from collections import deque
from pathlib import Path
from threading import Event, Lock, Thread
import time

from .analysis import EcgAnalysisResult, analyze_recent_ecg, get_ecg_analysis_info
from .attitude import ComplementaryAttitudeFilter, Orientation
from .buffers import ImuSample
from .protocol import ECG_SAMPLE_RATE_HZ, FLAG_LO_MINUS, FLAG_LO_PLUS
from .serial_io import PcDataStores, SerialPacketReader, list_serial_ports
from .sessions import ExperimentSessionRecorder, PLACEMENT_PRESETS, WIRE_MAPS, RecordingState
from .status import ConnectionSnapshot, ConnectionState, connection_state_text


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
        self._filter_lock = Lock()
        self._last_index: int | None = None
        self._orientation = Orientation(0.0, 0.0, 0.0)
        self._latest_sample: ImuSample | None = None
        self._thread = Thread(target=self._run, name="AttitudeWorker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def calibrate_flat(self) -> None:
        with self._filter_lock:
            orientation = self.filter.calibrate_flat()
        with self._lock:
            self._orientation = orientation

    def reset_orientation(self) -> None:
        self.calibrate_flat()

    def latest(self) -> tuple[Orientation, ImuSample | None]:
        with self._lock:
            return self._orientation, self._latest_sample

    def _run(self) -> None:
        while not self._stop.is_set():
            samples = self.stores.imu.snapshot()
            for sample in samples:
                if self._last_index is not None and sample.sample_index <= self._last_index:
                    continue
                with self._filter_lock:
                    orientation = self.filter.update(sample)
                with self._lock:
                    self._orientation = orientation
                    self._latest_sample = sample
                    self._last_index = sample.sample_index
            self._stop.wait(0.02)


def _body_mesh_data(gl: object) -> object:
    half_x = 0.9
    half_y = 0.32
    half_z = 0.16
    vertices = [
        (-half_x, -half_y, -half_z),
        (half_x, -half_y, -half_z),
        (half_x, half_y, -half_z),
        (-half_x, half_y, -half_z),
        (-half_x, -half_y, half_z),
        (half_x, -half_y, half_z),
        (half_x, half_y, half_z),
        (-half_x, half_y, half_z),
    ]
    faces = [
        (0, 1, 2),
        (0, 2, 3),
        (4, 6, 5),
        (4, 7, 6),
        (0, 4, 5),
        (0, 5, 1),
        (1, 5, 6),
        (1, 6, 2),
        (2, 6, 7),
        (2, 7, 3),
        (3, 7, 4),
        (3, 4, 0),
    ]
    return gl.MeshData(vertexes=vertices, faces=faces)


class MainWindow:
    def __init__(self) -> None:
        from PySide6 import QtCore, QtWidgets
        import pyqtgraph as pg

        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.pg = pg
        self.stores = PcDataStores.create()
        self.reader: SerialPacketReader | None = None
        self.session_recorder: ExperimentSessionRecorder | None = None
        self.recording_state = RecordingState.IDLE
        self.countdown_deadline_s: float | None = None
        self.waiting_after_sample_index: int | None = None
        self.record_prebuffer: deque[bytes] = deque()
        self.record_prebuffer_bytes = 0
        self.record_prebuffer_limit_bytes = 64 * 1024
        self.record_prebuffer_lock = Lock()
        self.raw_marker_items: list[object] = []
        self.clean_marker_items: list[object] = []
        self.ecg_worker = EcgAnalysisWorker(self.stores)
        self.attitude_worker = AttitudeWorker(self.stores)
        self.ecg_worker.start()
        self.attitude_worker.start()

        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle("AI 智能颈环 V0 上位机")
        self.window.resize(1280, 820)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        toolbar = QtWidgets.QHBoxLayout()
        self.port_combo = QtWidgets.QComboBox()
        self.refresh_button = QtWidgets.QPushButton("刷新串口")
        self.connect_button = QtWidgets.QPushButton("连接设备")
        self.disconnect_button = QtWidgets.QPushButton("断开连接")
        self.calibrate_button = QtWidgets.QPushButton("平放校准")
        toolbar.addWidget(self.port_combo, 2)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.connect_button)
        toolbar.addWidget(self.disconnect_button)
        toolbar.addWidget(self.calibrate_button)
        layout.addLayout(toolbar)

        connection_group = QtWidgets.QGroupBox("连接状态")
        connection_layout = QtWidgets.QGridLayout(connection_group)
        self.connection_label = QtWidgets.QLabel("未连接")
        self.port_status_label = QtWidgets.QLabel("串口 --")
        self.packet_status_label = QtWidgets.QLabel("包 0 / ECG 包 0")
        self.ecg_rate_label = QtWidgets.QLabel("实测 ECG -- Hz")
        self.last_packet_label = QtWidgets.QLabel("最后一包 --")
        for index, widget in enumerate(
            (
                self.connection_label,
                self.port_status_label,
                self.packet_status_label,
                self.ecg_rate_label,
                self.last_packet_label,
            )
        ):
            connection_layout.addWidget(widget, index // 3, index % 3)
        layout.addWidget(connection_group)

        record_group = QtWidgets.QGroupBox("实验记录")
        record_layout = QtWidgets.QGridLayout(record_group)
        self.record_name_edit = QtWidgets.QLineEdit()
        self.record_name_edit.setPlaceholderText("例如：锁骨内侧第一次")
        self.placement_combo = QtWidgets.QComboBox()
        for preset in PLACEMENT_PRESETS:
            self.placement_combo.addItem(f"{preset.placement_id} {preset.placement_name}", preset)
        self.wire_combo = QtWidgets.QComboBox()
        for value, label in WIRE_MAPS:
            self.wire_combo.addItem(label, value)
        self.electrode_edit = QtWidgets.QLineEdit("AgAgCl 湿电极")
        self.notes_edit = QtWidgets.QLineEdit()
        self.notes_edit.setPlaceholderText("备注")
        self.delay_combo = QtWidgets.QComboBox()
        self.delay_combo.addItem("立即开始", 0)
        self.delay_combo.addItem("3 秒后开始", 3)
        self.delay_combo.addItem("5 秒后开始", 5)
        self.delay_combo.setCurrentIndex(1)
        self.start_record_button = QtWidgets.QPushButton("开始记录")
        self.stop_record_button = QtWidgets.QPushButton("停止记录")
        self.cancel_countdown_button = QtWidgets.QPushButton("取消倒计时")
        self.record_status_label = QtWidgets.QLabel("未记录")
        self.record_sample_label = QtWidgets.QLabel("记录样本 0")
        self.swallow_marker_button = QtWidgets.QPushButton("吞咽")
        self.cough_marker_button = QtWidgets.QPushButton("咳嗽")
        self.talk_marker_button = QtWidgets.QPushButton("说话")
        self.turn_marker_button = QtWidgets.QPushButton("转头")
        self.custom_marker_edit = QtWidgets.QLineEdit()
        self.custom_marker_edit.setPlaceholderText("自定义标记")
        self.custom_marker_button = QtWidgets.QPushButton("添加标记")
        record_layout.addWidget(QtWidgets.QLabel("记录名称"), 0, 0)
        record_layout.addWidget(self.record_name_edit, 0, 1)
        record_layout.addWidget(QtWidgets.QLabel("电极点位"), 0, 2)
        record_layout.addWidget(self.placement_combo, 0, 3)
        record_layout.addWidget(QtWidgets.QLabel("线序"), 1, 0)
        record_layout.addWidget(self.wire_combo, 1, 1)
        record_layout.addWidget(QtWidgets.QLabel("电极类型"), 1, 2)
        record_layout.addWidget(self.electrode_edit, 1, 3)
        record_layout.addWidget(QtWidgets.QLabel("备注"), 2, 0)
        record_layout.addWidget(self.notes_edit, 2, 1)
        record_layout.addWidget(QtWidgets.QLabel("开始延时"), 2, 2)
        record_layout.addWidget(self.delay_combo, 2, 3)
        record_layout.addWidget(self.start_record_button, 3, 0)
        record_layout.addWidget(self.stop_record_button, 3, 1)
        record_layout.addWidget(self.cancel_countdown_button, 3, 2)
        record_layout.addWidget(self.record_status_label, 3, 3)
        record_layout.addWidget(self.record_sample_label, 4, 0, 1, 4)
        record_layout.addWidget(self.swallow_marker_button, 5, 0)
        record_layout.addWidget(self.cough_marker_button, 5, 1)
        record_layout.addWidget(self.talk_marker_button, 5, 2)
        record_layout.addWidget(self.turn_marker_button, 5, 3)
        record_layout.addWidget(self.custom_marker_edit, 6, 0, 1, 3)
        record_layout.addWidget(self.custom_marker_button, 6, 3)
        layout.addWidget(record_group)

        status_layout = QtWidgets.QGridLayout()
        self.hr_label = QtWidgets.QLabel("心率（HR）--")
        self.rr_label = QtWidgets.QLabel("RR 间期 --")
        self.sqi_label = QtWidgets.QLabel("信号质量（SQI）--")
        self.lead_label = QtWidgets.QLabel("导联 --")
        self.loss_label = QtWidgets.QLabel("丢包 0")
        self.crc_label = QtWidgets.QLabel("CRC 错误 0")
        for column, widget in enumerate(
            (self.hr_label, self.rr_label, self.sqi_label, self.lead_label, self.loss_label, self.crc_label)
        ):
            status_layout.addWidget(widget, 0, column)
        layout.addLayout(status_layout)

        splitter = QtWidgets.QSplitter()
        splitter.setOrientation(QtCore.Qt.Vertical)
        self.raw_plot = pg.PlotWidget(title="原始 ECG（Raw）- 最近 10 秒")
        self.clean_plot = pg.PlotWidget(title="清洗后 ECG（Clean）- 最近 10 秒")
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
        self.gl_body = None
        try:
            import pyqtgraph.opengl as gl

            self.gl_widget = gl.GLViewWidget()
            self.gl_widget.setCameraPosition(distance=4)
            grid = gl.GLGridItem()
            grid.setSize(x=4, y=4)
            grid.setSpacing(x=0.5, y=0.5)
            grid.translate(0, 0, -0.45)
            self.gl_widget.addItem(grid)
            self.gl_body = gl.GLMeshItem(
                meshdata=_body_mesh_data(gl),
                smooth=False,
                color=(0.12, 0.45, 0.78, 1.0),
                shader="shaded",
                drawEdges=True,
                edgeColor=(0.92, 0.96, 1.0, 1.0),
            )
            self.gl_widget.addItem(self.gl_body)
            lower.addWidget(self.gl_widget, 2)
        except Exception:
            lower.addWidget(QtWidgets.QLabel("3D 姿态视图需要 pyqtgraph OpenGL 支持。"), 2)
        layout.addLayout(lower, 2)

        analysis_group = QtWidgets.QGroupBox("当前 ECG 分析方式")
        analysis_layout = QtWidgets.QGridLayout(analysis_group)
        self.analysis_info_labels: list[object] = []
        for row, text in enumerate(self._analysis_info_lines()):
            label = QtWidgets.QLabel(text)
            label.setWordWrap(True)
            self.analysis_info_labels.append(label)
            analysis_layout.addWidget(label, row // 2, row % 2)
        layout.addWidget(analysis_group)

        self.window.setCentralWidget(central)
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.connect_serial)
        self.disconnect_button.clicked.connect(self.disconnect_serial)
        self.calibrate_button.clicked.connect(self.attitude_worker.calibrate_flat)
        self.start_record_button.clicked.connect(self.start_recording)
        self.stop_record_button.clicked.connect(self.stop_recording)
        self.cancel_countdown_button.clicked.connect(self.cancel_countdown)
        self.swallow_marker_button.clicked.connect(lambda: self.add_marker("swallow", "吞咽"))
        self.cough_marker_button.clicked.connect(lambda: self.add_marker("cough", "咳嗽"))
        self.talk_marker_button.clicked.connect(lambda: self.add_marker("talk", "说话"))
        self.turn_marker_button.clicked.connect(lambda: self.add_marker("turn", "转头"))
        self.custom_marker_button.clicked.connect(self.add_custom_marker)

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
        ports.sort(key=lambda port: (not port.is_bluetooth_outgoing, not port.is_bluetooth_candidate, port.device))
        for port in ports:
            suffix = " BT OUT" if port.is_bluetooth_outgoing else " BT" if port.is_bluetooth_candidate else ""
            self.port_combo.addItem(f"{port.device} - {port.description}{suffix}", port.device)

    def connect_serial(self) -> None:
        port = self.port_combo.currentData()
        if not port:
            return
        self.disconnect_serial()
        raw_path = Path("data") / f"smartcollar_v0_{time.strftime('%Y%m%d_%H%M%S')}.bin"
        self.reader = SerialPacketReader(
            port=port,
            stores=self.stores,
            raw_log_path=raw_path,
            raw_chunk_callback=self._record_raw_chunk,
        )
        self.reader.start()
        self.connection_label.setText(f"正在连接 {port}……")

    def disconnect_serial(self) -> None:
        if self.session_recorder is not None:
            self._finish_recording(status="interrupted", reason="连接断开")
        if self.reader is not None:
            self.reader.stop()
            self.reader = None
        self.connection_label.setText("未连接")

    def update_view(self) -> None:
        self._update_ecg()
        self._update_status_labels()
        self._update_connection_status()
        self._update_recording_state()
        self._update_imu()

    def _analysis_info_lines(self) -> tuple[str, ...]:
        info = get_ecg_analysis_info()
        return (
            f"分析库：{info.library_name} {info.library_version}",
            f"NumPy：{info.numpy_version}",
            f"ECG 采样率：{info.sampling_rate_hz} Hz",
            f"清洗方法：{info.clean_method}",
            f"R 峰检测方法：{info.peak_method}",
            f"信号质量方法：{info.quality_method}",
            f"分析窗口：{info.analysis_window_seconds:.0f} 秒，刷新间隔约 {info.analysis_window_seconds - info.overlap_seconds:.1f} 秒",
            f"心率计算：{info.hr_method}",
            f"RR 合法范围：{info.rr_valid_range_ms[0]:.0f}-{info.rr_valid_range_ms[1]:.0f} ms",
            f"Clipping 判定：{info.clipping_rule}",
            f"重采样：{info.resampling}",
            f"工频处理：{info.powerline_handling}",
        )

    def _update_ecg(self) -> None:
        samples = self.stores.ecg.snapshot()
        recent = samples[-5000:]
        if recent:
            t0 = recent[0].timestamp_us
            x = [(sample.timestamp_us - t0) / 1_000_000.0 for sample in recent]
            y = [sample.raw_adc for sample in recent]
            self.raw_curve.setData(x, y)
        self._update_marker_lines(recent)

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
                f"心率（HR）{analysis.heart_rate_bpm:.1f}" if analysis.heart_rate_bpm is not None else "心率（HR）--"
            )
            self.rr_label.setText(
                f"RR 间期 {analysis.latest_rr_ms:.0f} ms" if analysis.latest_rr_ms is not None else "RR 间期 --"
            )
            self.sqi_label.setText(
                f"信号质量（SQI）{analysis.signal_quality:.2f}"
                if analysis.signal_quality is not None
                else f"信号质量（SQI）{analysis.message}"
            )

    def _update_status_labels(self) -> None:
        latest_status = self.stores.status.latest()
        if latest_status is not None:
            flags = latest_status.payload.lead_off_flags
            lead_text = "导联正常" if flags == 0 else "导联脱落"
            if flags & FLAG_LO_MINUS:
                lead_text += " LO-"
            if flags & FLAG_LO_PLUS:
                lead_text += " LO+"
            self.lead_label.setText(lead_text)
        stats = self.reader.stats if self.reader is not None else None
        if stats is not None:
            self.loss_label.setText(f"丢包 {stats.packets_lost}")
            self.crc_label.setText(f"CRC 错误 {stats.crc_errors}")

    def _connection_snapshot(self) -> ConnectionSnapshot:
        if self.reader is None:
            return ConnectionSnapshot(
                state=ConnectionState.DISCONNECTED,
                port=None,
                serial_open=False,
                packet_count=0,
                ecg_packet_count=0,
                seconds_since_last_packet=None,
                measured_ecg_rate_hz=None,
                crc_errors=0,
                packets_lost=0,
            )

        runtime = self.reader.runtime_status
        stats = self.reader.stats
        now = time.monotonic()
        last_age = (
            now - runtime.last_packet_monotonic_s
            if runtime.last_packet_monotonic_s is not None
            else None
        )
        measured_rate = None
        if runtime.started_at_monotonic_s is not None and runtime.ecg_packet_count > 0:
            elapsed = max(0.001, now - runtime.started_at_monotonic_s)
            measured_rate = (runtime.ecg_packet_count * 20.0) / elapsed

        if runtime.last_error is not None:
            state = ConnectionState.ERROR
        elif not runtime.serial_open:
            state = ConnectionState.CONNECTING
        elif runtime.packet_count == 0:
            state = ConnectionState.CONNECTED_WAITING_DATA
        elif last_age is not None and last_age > 2.0:
            state = ConnectionState.STALE
        else:
            state = ConnectionState.RECEIVING

        return ConnectionSnapshot(
            state=state,
            port=runtime.port,
            serial_open=runtime.serial_open,
            packet_count=runtime.packet_count,
            ecg_packet_count=runtime.ecg_packet_count,
            seconds_since_last_packet=last_age,
            measured_ecg_rate_hz=measured_rate,
            crc_errors=stats.crc_errors,
            packets_lost=stats.packets_lost,
            error_text=str(runtime.last_error) if runtime.last_error is not None else None,
        )

    def _update_connection_status(self) -> None:
        snapshot = self._connection_snapshot()
        self.connection_label.setText(connection_state_text(snapshot))
        self.port_status_label.setText(
            f"串口 {snapshot.port or '--'}：{'已打开' if snapshot.serial_open else '未打开'}"
        )
        self.packet_status_label.setText(
            f"接收包 {snapshot.packet_count} / ECG 包 {snapshot.ecg_packet_count}"
        )
        self.ecg_rate_label.setText(
            f"实测 ECG {snapshot.measured_ecg_rate_hz:.0f} Hz"
            if snapshot.measured_ecg_rate_hz is not None
            else "实测 ECG -- Hz"
        )
        self.last_packet_label.setText(
            f"最后一包 {snapshot.seconds_since_last_packet:.1f} 秒前"
            if snapshot.seconds_since_last_packet is not None
            else "最后一包 --"
        )
        self.connect_button.setEnabled(self.reader is None or snapshot.state is ConnectionState.ERROR)
        self.disconnect_button.setEnabled(self.reader is not None)

    def _latest_ecg_sample(self) -> object | None:
        samples = self.stores.ecg.snapshot()
        return samples[-1] if samples else None

    def _record_raw_chunk(self, chunk: bytes) -> None:
        recorder = self.session_recorder
        if recorder is None:
            return
        with self.record_prebuffer_lock:
            if self.recording_state is RecordingState.RECORDING:
                recorder.write_raw(chunk)
                return
            if self.recording_state is RecordingState.WAITING_FIRST_VALID_SAMPLE:
                self.record_prebuffer.append(chunk)
                self.record_prebuffer_bytes += len(chunk)
                while self.record_prebuffer_bytes > self.record_prebuffer_limit_bytes and self.record_prebuffer:
                    removed = self.record_prebuffer.popleft()
                    self.record_prebuffer_bytes -= len(removed)

    def _selected_placement(self) -> object:
        return self.placement_combo.currentData()

    def add_custom_marker(self) -> None:
        label = self.custom_marker_edit.text().strip()
        self.add_marker("custom", label or "自定义")
        self.custom_marker_edit.clear()

    def add_marker(self, marker_type: str, label: str) -> None:
        if self.session_recorder is None or self.recording_state is not RecordingState.RECORDING:
            self.record_status_label.setText("只能在记录中添加标记")
            return
        latest = self._latest_ecg_sample()
        if latest is None:
            self.record_status_label.setText("暂无 ECG 样本，无法添加标记")
            return
        marker = self.session_recorder.add_marker(
            label=label,
            marker_type=marker_type,
            sample_index=getattr(latest, "sample_index"),
            device_timestamp_us=getattr(latest, "timestamp_us"),
        )
        self.record_status_label.setText(f"已添加标记：{marker.label} @ {marker.sample_index}")

    def _arm_recording_wait(self) -> None:
        with self.record_prebuffer_lock:
            self.record_prebuffer.clear()
            self.record_prebuffer_bytes = 0
        self.recording_state = RecordingState.WAITING_FIRST_VALID_SAMPLE
        self.record_status_label.setText("正在等待第一份有效 ECG 数据")

    def start_recording(self) -> None:
        if self.session_recorder is not None:
            return
        snapshot = self._connection_snapshot()
        if snapshot.state is not ConnectionState.RECEIVING:
            self.record_status_label.setText("请先连接设备并确认正在接收 ECG")
            return
        latest = self._latest_ecg_sample()
        self.waiting_after_sample_index = getattr(latest, "sample_index", None)
        port = snapshot.port or ""
        placement = self._selected_placement()
        self.session_recorder = ExperimentSessionRecorder(
            base_dir=Path("data") / "sessions",
            display_name=self.record_name_edit.text(),
            placement=placement,
            wire_map=str(self.wire_combo.currentData()),
            electrode_type=self.electrode_edit.text(),
            notes=self.notes_edit.text(),
            port=port,
        )
        delay_seconds = int(self.delay_combo.currentData())
        if delay_seconds > 0:
            self.recording_state = RecordingState.COUNTDOWN
            self.countdown_deadline_s = time.monotonic() + delay_seconds
            self.record_status_label.setText(f"{delay_seconds} 秒后开始记录")
        else:
            self._arm_recording_wait()

    def stop_recording(self) -> None:
        if self.session_recorder is None:
            marker_enabled = False
            for button in (
                self.swallow_marker_button,
                self.cough_marker_button,
                self.talk_marker_button,
                self.turn_marker_button,
                self.custom_marker_button,
            ):
                button.setEnabled(marker_enabled)
            return
        marker_enabled = self.recording_state is RecordingState.RECORDING
        for button in (
            self.swallow_marker_button,
            self.cough_marker_button,
            self.talk_marker_button,
            self.turn_marker_button,
            self.custom_marker_button,
        ):
            button.setEnabled(marker_enabled)
        if self.recording_state in (RecordingState.COUNTDOWN, RecordingState.WAITING_FIRST_VALID_SAMPLE):
            self._finish_recording(status="interrupted", reason="用户停止，尚未写入有效 ECG 数据")
            return
        self._finish_recording(status="completed", reason=None)

    def cancel_countdown(self) -> None:
        if self.recording_state is RecordingState.COUNTDOWN and self.session_recorder is not None:
            self._finish_recording(status="interrupted", reason="用户取消倒计时")

    def _finish_recording(self, *, status: str, reason: str | None) -> None:
        recorder = self.session_recorder
        if recorder is None:
            return
        self.recording_state = RecordingState.SAVING
        self.record_status_label.setText("正在保存")
        latest = self._latest_ecg_sample()
        if latest is not None:
            recorder.update_latest_sample(getattr(latest, "sample_index"))
        try:
            final_dir = recorder.finish(status=status, interrupted_reason=reason)
        except Exception as exc:
            self.recording_state = RecordingState.ERROR
            self.record_status_label.setText(f"保存失败：{exc}")
            self.session_recorder = None
            return
        self.session_recorder = None
        self.countdown_deadline_s = None
        self.waiting_after_sample_index = None
        with self.record_prebuffer_lock:
            self.record_prebuffer.clear()
            self.record_prebuffer_bytes = 0
        self.recording_state = RecordingState.SAVED if status == "completed" else RecordingState.INTERRUPTED
        self.record_status_label.setText(f"保存完成：{final_dir}")

    def _update_recording_state(self) -> None:
        self.stop_record_button.setEnabled(self.session_recorder is not None)
        self.cancel_countdown_button.setEnabled(self.recording_state is RecordingState.COUNTDOWN)
        self.start_record_button.setEnabled(self.session_recorder is None)
        if self.session_recorder is None:
            return

        if self.recording_state is RecordingState.COUNTDOWN:
            if self.countdown_deadline_s is None:
                return
            remaining = max(0, int(self.countdown_deadline_s - time.monotonic()) + 1)
            if remaining > 0:
                self.record_status_label.setText(f"{remaining} 秒后开始记录")
                return
            self._arm_recording_wait()

        latest = self._latest_ecg_sample()
        if latest is None:
            return
        latest_index = getattr(latest, "sample_index")
        if self.recording_state is RecordingState.WAITING_FIRST_VALID_SAMPLE:
            if self.waiting_after_sample_index is None or latest_index > self.waiting_after_sample_index:
                with self.record_prebuffer_lock:
                    self.session_recorder.start_at_sample(latest_index)
                    for chunk in self.record_prebuffer:
                        self.session_recorder.write_raw(chunk)
                    self.record_prebuffer.clear()
                    self.record_prebuffer_bytes = 0
                    self.recording_state = RecordingState.RECORDING

        if self.recording_state is RecordingState.RECORDING:
            self.session_recorder.update_latest_sample(latest_index)
            seconds = self.session_recorder.sample_count / 500.0
            self.record_status_label.setText(f"正在记录：{seconds:0>8.1f} 秒")
            self.record_sample_label.setText(f"记录样本 {self.session_recorder.sample_count}")

    def _update_marker_lines(self, recent: list[object]) -> None:
        for item in self.raw_marker_items:
            self.raw_plot.removeItem(item)
        for item in self.clean_marker_items:
            self.clean_plot.removeItem(item)
        self.raw_marker_items.clear()
        self.clean_marker_items.clear()
        if self.session_recorder is None or not recent:
            return

        start_timestamp_us = getattr(recent[0], "timestamp_us")
        end_timestamp_us = getattr(recent[-1], "timestamp_us")
        start_sample_index = getattr(recent[0], "sample_index")
        end_sample_index = getattr(recent[-1], "sample_index")
        for marker in self.session_recorder.markers:
            if start_timestamp_us <= marker.device_timestamp_us <= end_timestamp_us:
                raw_x = (marker.device_timestamp_us - start_timestamp_us) / 1_000_000.0
                line = self.pg.InfiniteLine(pos=raw_x, angle=90, pen=self.pg.mkPen("#f9a825", width=1))
                self.raw_plot.addItem(line)
                self.raw_marker_items.append(line)
            if start_sample_index <= marker.sample_index <= end_sample_index:
                clean_x = (marker.sample_index - start_sample_index) / float(ECG_SAMPLE_RATE_HZ)
                line = self.pg.InfiniteLine(pos=clean_x, angle=90, pen=self.pg.mkPen("#f9a825", width=1))
                self.clean_plot.addItem(line)
                self.clean_marker_items.append(line)

    def _update_imu(self) -> None:
        orientation, sample = self.attitude_worker.latest()
        if sample is not None:
            for name in ("ax", "ay", "az", "gx", "gy", "gz"):
                self.imu_labels[name].setText(f"{name} {getattr(sample, name)}")
        self.imu_labels["roll"].setText(f"roll {orientation.roll_deg:.1f}")
        self.imu_labels["pitch"].setText(f"pitch {orientation.pitch_deg:.1f}")
        self.imu_labels["yaw"].setText(f"yaw {orientation.yaw_deg:.1f}")
        if self.gl_body is not None:
            self.gl_body.resetTransform()
            self.gl_body.rotate(orientation.yaw_deg, 0, 0, 1)
            self.gl_body.rotate(orientation.pitch_deg, 0, 1, 0)
            self.gl_body.rotate(orientation.roll_deg, 1, 0, 0)


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
