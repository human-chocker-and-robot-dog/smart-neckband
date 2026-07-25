from __future__ import annotations

from collections import deque
from datetime import datetime
import logging
from pathlib import Path
import sys
from threading import Event, Lock, Thread
import time

from .analysis import EcgAnalysisResult, analyze_recent_ecg, get_ecg_analysis_info
from .attitude import ComplementaryAttitudeFilter, Orientation
from .ble_io import BleDeviceInfo, BlePacketReader, list_ble_devices
from .buffers import ImuSample
from .history import (
    ComparisonTrack,
    ComparisonViewport,
    HistoryEcgData,
    analyze_history_ecg,
    clamp_viewport,
    default_compare_csv_name,
    downsample_xy,
    list_session_records,
    load_session_analysis_summary,
    load_session_ecg_samples,
    load_session_markers,
    session_duration_seconds,
    visible_markers,
    visible_r_peak_indices,
    visible_values,
    write_compare_csv,
    y_range_for,
)
from .health_runtime import HealthRuntimeWorker
from .protocol import ECG_SAMPLE_RATE_HZ, FLAG_LO_MINUS, FLAG_LO_PLUS, VoiceStatusPayload
from .serial_io import PcDataStores, SerialPacketReader, list_serial_ports
from .sessions import ExperimentSessionRecorder, PLACEMENT_PRESETS, WIRE_MAPS, RecordingState
from .status import ConnectionSnapshot, ConnectionState, connection_state_text
from .webhook_ui import WebhookTab


LOGGER = logging.getLogger(__name__)


def configure_debug_logging() -> Path:
    log_dir = Path("data") / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"pc_debug_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s %(threadName)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=(console_handler, file_handler), force=True)
    logging.getLogger("smart_neckband").setLevel(logging.DEBUG)
    logging.getLogger("bleak").setLevel(logging.DEBUG)
    logging.getLogger("bleak.backends.winrt.scanner").setLevel(logging.INFO)
    LOGGER.debug("Python runtime: executable=%s version=%s", sys.executable, sys.version.replace("\n", " "))
    LOGGER.debug("PC DEBUG logging initialized: %s", log_path.resolve())
    return log_path


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
    def __init__(self, *, debug_log_path: Path | None = None) -> None:
        from PySide6 import QtCore, QtWidgets
        import pyqtgraph as pg

        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.pg = pg
        self.stores = PcDataStores.create()
        self.reader: SerialPacketReader | BlePacketReader | None = None
        self.session_recorder: ExperimentSessionRecorder | None = None
        self.recording_state = RecordingState.IDLE
        self.countdown_deadline_s: float | None = None
        self.waiting_after_sample_index: int | None = None
        self.record_prebuffer: deque[bytes] = deque()
        self.record_prebuffer_bytes = 0
        self.record_prebuffer_limit_bytes = 64 * 1024
        self.record_prebuffer_lock = Lock()
        self.gui_callbacks: deque[object] = deque()
        self.gui_callbacks_lock = Lock()
        self._ble_scan_generation = 0
        self._ble_scan_thread: Thread | None = None
        self._debug_enabled = True
        self.debug_log_path = debug_log_path
        self.raw_marker_items: list[object] = []
        self.clean_marker_items: list[object] = []
        self.history_records: tuple[object, ...] = ()
        self.session_load_cache: dict[Path, tuple[HistoryEcgData, tuple[object, ...], dict[str, object]]] = {}
        self.history_record: object | None = None
        self.history_ecg_data: HistoryEcgData | None = None
        self.history_markers: tuple[object, ...] = ()
        self.history_marker_items: list[object] = []
        self.history_viewport = ComparisonViewport(0.0, 10.0)
        self.history_loading = False
        self.compare_loading = False
        self.compare_a: ComparisonTrack | None = None
        self.compare_b: ComparisonTrack | None = None
        self.compare_viewport = ComparisonViewport(0.0, 10.0)
        self.compare_marker_items_a: list[object] = []
        self.compare_marker_items_b: list[object] = []
        self.ecg_worker = EcgAnalysisWorker(self.stores)
        self.attitude_worker = AttitudeWorker(self.stores)
        self.ecg_worker.start()
        self.attitude_worker.start()
        try:
            self.health_worker = HealthRuntimeWorker.from_environment(
                stores=self.stores,
                reader_provider=lambda: self.reader,
                analysis_provider=self.ecg_worker.latest,
            )
        except ValueError:
            LOGGER.exception("Health runtime configuration rejected")
            self.health_worker = None
        if self.health_worker is not None:
            self.health_worker.start()

        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle("AI 智能颈环 V0 上位机")
        self.window.resize(1280, 820)

        tabs = QtWidgets.QTabWidget()
        live_tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(live_tab)
        toolbar = QtWidgets.QHBoxLayout()
        self.transport_combo = QtWidgets.QComboBox()
        self.transport_combo.addItem("USB 串口（仅台架）", "serial")
        self.transport_combo.addItem("ESP32-C3 BLE", "ble")
        self.port_combo = QtWidgets.QComboBox()
        self.refresh_button = QtWidgets.QPushButton("刷新设备")
        self.connect_button = QtWidgets.QPushButton("连接设备")
        self.disconnect_button = QtWidgets.QPushButton("断开连接")
        toolbar.addWidget(self.transport_combo)
        toolbar.addWidget(self.port_combo, 2)
        toolbar.addWidget(self.refresh_button)
        toolbar.addWidget(self.connect_button)
        toolbar.addWidget(self.disconnect_button)
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

        self.debug_group = QtWidgets.QGroupBox("BLE DEBUG 日志")
        debug_layout = QtWidgets.QVBoxLayout(self.debug_group)
        self.debug_log_output = QtWidgets.QPlainTextEdit()
        self.debug_log_output.setReadOnly(True)
        self.debug_log_output.setMaximumBlockCount(500)
        self.debug_log_output.setMinimumHeight(110)
        debug_layout.addWidget(self.debug_log_output)
        self.clear_debug_button = QtWidgets.QPushButton("清空显示")
        debug_layout.addWidget(self.clear_debug_button)
        layout.addWidget(self.debug_group)

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

        diagnostics_tab = self._build_diagnostics_tab()
        history_tab = self._build_history_tab()
        compare_tab = self._build_compare_tab()
        self.webhook_tab = WebhookTab(
            QtCore=QtCore,
            QtWidgets=QtWidgets,
            post_gui=self._post_gui,
        )
        tabs.addTab(live_tab, "实时")
        tabs.addTab(diagnostics_tab, "诊断")
        tabs.addTab(history_tab, "历史记录")
        tabs.addTab(compare_tab, "双轨对比")
        tabs.addTab(self.webhook_tab.widget, "Webhook")
        self.window.setCentralWidget(tabs)
        self.refresh_button.clicked.connect(self.refresh_ports)
        self.transport_combo.currentIndexChanged.connect(self.refresh_ports)
        self.connect_button.clicked.connect(self.connect_device)
        self.disconnect_button.clicked.connect(self.disconnect_serial)
        self.clear_debug_button.clicked.connect(self.debug_log_output.clear)
        self.debug_enabled_checkbox.toggled.connect(self._set_debug_enabled)
        self.calibrate_button.clicked.connect(self.attitude_worker.calibrate_flat)
        self.start_record_button.clicked.connect(self.start_recording)
        self.stop_record_button.clicked.connect(self.stop_recording)
        self.cancel_countdown_button.clicked.connect(self.cancel_countdown)
        self.swallow_marker_button.clicked.connect(lambda: self.add_marker("swallow", "吞咽"))
        self.cough_marker_button.clicked.connect(lambda: self.add_marker("cough", "咳嗽"))
        self.talk_marker_button.clicked.connect(lambda: self.add_marker("talk", "说话"))
        self.turn_marker_button.clicked.connect(lambda: self.add_marker("turn", "转头"))
        self.custom_marker_button.clicked.connect(self.add_custom_marker)
        self.history_refresh_button.clicked.connect(self.refresh_history_sessions)
        self.history_load_button.clicked.connect(self.load_selected_history_session)
        self.history_mode_combo.currentIndexChanged.connect(self.update_history_plot)
        self.history_duration_combo.currentIndexChanged.connect(self.update_history_duration)
        self.history_scrollbar.valueChanged.connect(self.update_history_scrollbar)
        self.history_show_rpeaks_checkbox.stateChanged.connect(self.update_history_plot)
        self.history_show_markers_checkbox.stateChanged.connect(self.update_history_plot)
        self.compare_refresh_button.clicked.connect(self.refresh_history_sessions)
        self.compare_load_button.clicked.connect(self.load_compare_sessions)
        self.compare_mode_combo.currentIndexChanged.connect(self.update_compare_plot)
        self.compare_y_mode_combo.currentIndexChanged.connect(self.update_compare_plot)
        self.compare_duration_combo.currentIndexChanged.connect(self.update_compare_duration)
        self.compare_scrollbar.valueChanged.connect(self.update_compare_scrollbar)
        self.compare_show_rpeaks_checkbox.stateChanged.connect(self.update_compare_plot)
        self.compare_show_markers_checkbox.stateChanged.connect(self.update_compare_plot)
        self.compare_marker_jump_combo.currentIndexChanged.connect(self.jump_compare_marker_type)
        self.compare_prev_marker_button.clicked.connect(lambda: self.jump_compare_marker(-1))
        self.compare_next_marker_button.clicked.connect(lambda: self.jump_compare_marker(1))
        self.compare_export_csv_button.clicked.connect(self.export_compare_csv)
        self.compare_export_png_button.clicked.connect(self.export_compare_png)

        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update_view)
        self.timer.start(100)
        if self.debug_log_path is not None:
            self._append_debug_log(f"Python：{sys.executable}")
            self._append_debug_log(f"完整日志文件：{self.debug_log_path.resolve()}")
        self.refresh_ports()
        self.refresh_history_sessions()

    def _build_diagnostics_tab(self) -> object:
        QtWidgets = self.QtWidgets
        layout_widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(layout_widget)

        controls = QtWidgets.QHBoxLayout()
        self.calibrate_button = QtWidgets.QPushButton("平放校准")
        self.debug_enabled_checkbox = QtWidgets.QCheckBox("启用连接 DEBUG 日志")
        self.debug_enabled_checkbox.setChecked(True)
        controls.addWidget(self.calibrate_button)
        controls.addWidget(self.debug_enabled_checkbox)
        controls.addStretch(1)
        layout.addLayout(controls)

        imu_group = QtWidgets.QGroupBox("IMU 姿态")
        imu_group_layout = QtWidgets.QHBoxLayout(imu_group)
        imu_panel = QtWidgets.QGridLayout()
        self.imu_labels: dict[str, object] = {}
        for row, name in enumerate(("ax", "ay", "az", "gx", "gy", "gz", "roll", "pitch", "yaw")):
            label = QtWidgets.QLabel(f"{name} --")
            self.imu_labels[name] = label
            imu_panel.addWidget(label, row // 3, row % 3)
        self.yaw_note = QtWidgets.QLabel("Yaw is gyro-integrated and may drift.")
        imu_panel.addWidget(self.yaw_note, 3, 0, 1, 3)
        imu_group_layout.addLayout(imu_panel, 1)

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
            imu_group_layout.addWidget(self.gl_widget, 2)
        except Exception:
            imu_group_layout.addWidget(QtWidgets.QLabel("3D 姿态视图需要 pyqtgraph OpenGL 支持。"), 2)
        layout.addWidget(imu_group, 2)

        analysis_group = QtWidgets.QGroupBox("当前 ECG 分析方式")
        analysis_layout = QtWidgets.QGridLayout(analysis_group)
        self.analysis_info_labels: list[object] = []
        for row, text in enumerate(self._analysis_info_lines()):
            label = QtWidgets.QLabel(text)
            label.setWordWrap(True)
            self.analysis_info_labels.append(label)
            analysis_layout.addWidget(label, row // 2, row % 2)
        layout.addWidget(analysis_group, 1)
        return layout_widget

    def _build_history_tab(self) -> object:
        QtWidgets = self.QtWidgets
        layout_widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(layout_widget)

        toolbar = QtWidgets.QHBoxLayout()
        self.history_session_combo = QtWidgets.QComboBox()
        self.history_refresh_button = QtWidgets.QPushButton("刷新记录")
        self.history_load_button = QtWidgets.QPushButton("加载记录")
        self.history_mode_combo = QtWidgets.QComboBox()
        self.history_mode_combo.addItem("原始 ECG", "raw")
        self.history_mode_combo.addItem("清洗后 ECG", "clean")
        self.history_duration_combo = QtWidgets.QComboBox()
        self.history_duration_combo.addItem("5 秒", 5.0)
        self.history_duration_combo.addItem("10 秒", 10.0)
        self.history_duration_combo.addItem("20 秒", 20.0)
        self.history_duration_combo.addItem("30 秒", 30.0)
        self.history_duration_combo.addItem("完整记录", None)
        self.history_duration_combo.setCurrentIndex(1)
        self.history_show_rpeaks_checkbox = QtWidgets.QCheckBox("显示 R 峰")
        self.history_show_rpeaks_checkbox.setChecked(True)
        self.history_show_markers_checkbox = QtWidgets.QCheckBox("显示动作标记")
        self.history_show_markers_checkbox.setChecked(True)
        toolbar.addWidget(self.history_session_combo, 3)
        toolbar.addWidget(self.history_refresh_button)
        toolbar.addWidget(self.history_load_button)
        toolbar.addWidget(self.history_mode_combo)
        toolbar.addWidget(self.history_duration_combo)
        toolbar.addWidget(self.history_show_rpeaks_checkbox)
        toolbar.addWidget(self.history_show_markers_checkbox)
        layout.addLayout(toolbar)

        self.history_status_label = QtWidgets.QLabel("未加载历史记录")
        self.history_range_label = QtWidgets.QLabel("当前范围：--")
        self.history_metadata_label = QtWidgets.QLabel("")
        self.history_metadata_label.setWordWrap(True)
        self.history_analysis_label = QtWidgets.QLabel("")
        self.history_analysis_label.setWordWrap(True)
        layout.addWidget(self.history_status_label)
        layout.addWidget(self.history_range_label)
        layout.addWidget(self.history_metadata_label)
        layout.addWidget(self.history_analysis_label)

        self.history_plot = self.pg.PlotWidget(title="历史 ECG")
        self.history_curve = self.history_plot.plot(pen=self.pg.mkPen("#1769aa", width=1))
        self.history_peak_scatter = self.pg.ScatterPlotItem(
            pen=self.pg.mkPen("#c62828"),
            brush=self.pg.mkBrush("#c62828"),
            size=7,
        )
        self.history_plot.addItem(self.history_peak_scatter)
        layout.addWidget(self.history_plot, 1)
        self.history_scrollbar = QtWidgets.QScrollBar(self.QtCore.Qt.Horizontal)
        layout.addWidget(self.history_scrollbar)
        return layout_widget

    def _build_compare_tab(self) -> object:
        QtWidgets = self.QtWidgets
        layout_widget = QtWidgets.QWidget()
        self.compare_tab_widget = layout_widget
        layout = QtWidgets.QVBoxLayout(layout_widget)

        top = QtWidgets.QGridLayout()
        self.compare_a_combo = QtWidgets.QComboBox()
        self.compare_b_combo = QtWidgets.QComboBox()
        self.compare_refresh_button = QtWidgets.QPushButton("刷新记录")
        self.compare_load_button = QtWidgets.QPushButton("加载对比")
        self.compare_mode_combo = QtWidgets.QComboBox()
        self.compare_mode_combo.addItem("原始 ECG", "raw")
        self.compare_mode_combo.addItem("清洗后 ECG", "clean")
        self.compare_y_mode_combo = QtWidgets.QComboBox()
        self.compare_y_mode_combo.addItem("分别自动缩放", "auto")
        self.compare_y_mode_combo.addItem("相同幅值范围", "same")
        self.compare_y_mode_combo.addItem("归一化显示", "normalized")
        self.compare_duration_combo = QtWidgets.QComboBox()
        self.compare_duration_combo.addItem("5 秒", 5.0)
        self.compare_duration_combo.addItem("10 秒", 10.0)
        self.compare_duration_combo.addItem("20 秒", 20.0)
        self.compare_duration_combo.addItem("30 秒", 30.0)
        self.compare_duration_combo.addItem("完整记录", None)
        self.compare_duration_combo.setCurrentIndex(1)
        self.compare_show_rpeaks_checkbox = QtWidgets.QCheckBox("显示 R 峰")
        self.compare_show_rpeaks_checkbox.setChecked(True)
        self.compare_show_markers_checkbox = QtWidgets.QCheckBox("显示动作标记")
        self.compare_show_markers_checkbox.setChecked(True)
        self.compare_marker_jump_combo = QtWidgets.QComboBox()
        self.compare_marker_jump_combo.addItem("动作位置：完整记录", "all")
        self.compare_marker_jump_combo.addItem("吞咽", "swallow")
        self.compare_marker_jump_combo.addItem("咳嗽", "cough")
        self.compare_marker_jump_combo.addItem("说话", "talk")
        self.compare_marker_jump_combo.addItem("转头", "turn")
        self.compare_marker_jump_combo.addItem("自定义标记", "custom")
        self.compare_prev_marker_button = QtWidgets.QPushButton("上一个标记")
        self.compare_next_marker_button = QtWidgets.QPushButton("下一个标记")
        self.compare_export_csv_button = QtWidgets.QPushButton("导出分析 CSV")
        self.compare_export_png_button = QtWidgets.QPushButton("导出当前视图 PNG")
        top.addWidget(QtWidgets.QLabel("记录 A"), 0, 0)
        top.addWidget(self.compare_a_combo, 0, 1, 1, 7)
        top.addWidget(QtWidgets.QLabel("记录 B"), 1, 0)
        top.addWidget(self.compare_b_combo, 1, 1, 1, 7)
        top.addWidget(self.compare_refresh_button, 2, 0)
        top.addWidget(self.compare_load_button, 2, 1)
        top.addWidget(QtWidgets.QLabel("显示数据"), 2, 2)
        top.addWidget(self.compare_mode_combo, 2, 3)
        top.addWidget(QtWidgets.QLabel("纵轴模式"), 2, 4)
        top.addWidget(self.compare_y_mode_combo, 2, 5)
        top.addWidget(QtWidgets.QLabel("显示时长"), 2, 6)
        top.addWidget(self.compare_duration_combo, 2, 7)
        top.addWidget(self.compare_show_rpeaks_checkbox, 3, 0)
        top.addWidget(self.compare_show_markers_checkbox, 3, 1)
        top.addWidget(self.compare_marker_jump_combo, 3, 2, 1, 3)
        top.addWidget(self.compare_prev_marker_button, 3, 5)
        top.addWidget(self.compare_next_marker_button, 3, 6)
        top.addWidget(self.compare_export_csv_button, 4, 0)
        top.addWidget(self.compare_export_png_button, 4, 1, 1, 2)
        layout.addLayout(top)

        self.compare_status_label = QtWidgets.QLabel("未加载对比")
        self.compare_status_label.setWordWrap(True)
        layout.addWidget(self.compare_status_label)

        self.compare_range_label = QtWidgets.QLabel("当前范围：--")
        layout.addWidget(self.compare_range_label)
        self.compare_normalized_note = QtWidgets.QLabel("归一化模式仅用于比较波形形态，不能比较真实信号振幅。")
        self.compare_normalized_note.setVisible(False)
        layout.addWidget(self.compare_normalized_note)

        self.compare_plot_a = self.pg.PlotWidget(title="记录 A")
        self.compare_plot_b = self.pg.PlotWidget(title="记录 B")
        self.compare_plot_a.setMouseEnabled(x=True, y=False)
        self.compare_plot_b.setMouseEnabled(x=True, y=False)
        self.compare_plot_b.setXLink(self.compare_plot_a)
        self.compare_curve_a = self.compare_plot_a.plot(pen=self.pg.mkPen("#1769aa", width=1))
        self.compare_curve_b = self.compare_plot_b.plot(pen=self.pg.mkPen("#d81b60", width=1))
        self.compare_peak_scatter_a = self.pg.ScatterPlotItem(
            pen=self.pg.mkPen("#c62828"),
            brush=self.pg.mkBrush("#c62828"),
            size=7,
        )
        self.compare_peak_scatter_b = self.pg.ScatterPlotItem(
            pen=self.pg.mkPen("#c62828"),
            brush=self.pg.mkBrush("#c62828"),
            size=7,
        )
        self.compare_plot_a.addItem(self.compare_peak_scatter_a)
        self.compare_plot_b.addItem(self.compare_peak_scatter_b)
        layout.addWidget(self.compare_plot_a, 1)
        layout.addWidget(self.compare_plot_b, 1)

        self.compare_scrollbar = QtWidgets.QScrollBar(self.QtCore.Qt.Horizontal)
        layout.addWidget(self.compare_scrollbar)
        return layout_widget

    def show(self) -> None:
        self.window.show()

    def close(self) -> None:
        self.webhook_tab.close()
        self.disconnect_serial()
        if self.health_worker is not None:
            self.health_worker.stop()
        self.ecg_worker.stop()
        self.attitude_worker.stop()

    def refresh_ports(self) -> None:
        self._ble_scan_generation += 1
        scan_generation = self._ble_scan_generation
        self.port_combo.clear()
        if self.transport_combo.currentData() == "ble":
            self._append_debug_log("开始扫描 BLE 设备……")
            self.port_combo.addItem("正在扫描 BLE 设备……", "")
            self.refresh_button.setEnabled(False)
            self._ble_scan_thread = Thread(
                target=self._scan_ble_devices,
                args=(scan_generation,),
                name="BleDeviceScanner",
                daemon=True,
            )
            self._ble_scan_thread.start()
            return

        self.refresh_button.setEnabled(True)
        try:
            ports = list_serial_ports()
        except RuntimeError as exc:
            self.port_combo.addItem(str(exc), "")
            return
        ports.sort(key=lambda port: (not port.is_bluetooth_outgoing, not port.is_bluetooth_candidate, port.device))
        for port in ports:
            suffix = " BT OUT" if port.is_bluetooth_outgoing else " BT" if port.is_bluetooth_candidate else ""
            self.port_combo.addItem(f"{port.device} - {port.description}{suffix}", port.device)

    def _scan_ble_devices(self, scan_generation: int) -> None:
        try:
            devices = tuple(list_ble_devices())
            error_text = None
        except Exception as exc:
            LOGGER.exception("BLE scan failed")
            devices = ()
            error_text = f"{type(exc).__name__}: {exc}"
        self._post_gui(
            lambda: self._finish_ble_scan(
                scan_generation=scan_generation,
                devices=devices,
                error_text=error_text,
            )
        )

    def _finish_ble_scan(
        self,
        *,
        scan_generation: int,
        devices: tuple[BleDeviceInfo, ...],
        error_text: str | None,
    ) -> None:
        if scan_generation != self._ble_scan_generation:
            return
        self._ble_scan_thread = None
        self.refresh_button.setEnabled(True)
        if self.transport_combo.currentData() != "ble":
            return

        self.port_combo.clear()
        if error_text is not None:
            self._append_debug_log(f"BLE 扫描失败：{error_text}")
            self.port_combo.addItem(error_text, "")
            return
        for device in devices:
            self.port_combo.addItem(f"{device.name} - {device.address}", device.address)
        if not devices:
            self.port_combo.addItem("未发现 BLE 设备", "")
            self._append_debug_log("扫描完成：未发现 CollarC3 设备")
        else:
            summary = ", ".join(f"{device.name} ({device.address})" for device in devices)
            self._append_debug_log(f"扫描完成：{summary}")

    def connect_device(self) -> None:
        endpoint = self.port_combo.currentData()
        if not endpoint:
            self._append_debug_log("连接已取消：没有选择有效设备")
            return
        self.disconnect_serial()
        raw_path = Path("data") / f"smartcollar_v0_{time.strftime('%Y%m%d_%H%M%S')}.bin"
        if self.transport_combo.currentData() == "ble":
            self._append_debug_log(f"点击连接：BLE {endpoint}")
            self.reader = BlePacketReader(
                address=endpoint,
                stores=self.stores,
                raw_log_path=raw_path,
                raw_chunk_callback=self._record_raw_chunk,
                debug_callback=self._queue_ble_debug,
                voice_text_callback=self.webhook_tab.enqueue_voice_text,
                voice_status_callback=self._queue_voice_status,
            )
            self.reader.health_transport = "ble"
        else:
            self.reader = SerialPacketReader(
                port=endpoint,
                stores=self.stores,
                raw_log_path=raw_path,
                raw_chunk_callback=self._record_raw_chunk,
            )
            self.reader.health_transport = "uart"
        self.reader.start()
        self.connection_label.setText(f"正在连接 {endpoint}……")

    def disconnect_serial(self) -> None:
        if self.session_recorder is not None:
            self._finish_recording(status="interrupted", reason="连接断开")
        if self.reader is not None:
            self._append_debug_log(f"断开设备：{self.reader.port}")
            self.reader.stop()
            self.reader = None
        self.connection_label.setText("未连接")

    def update_view(self) -> None:
        self._drain_gui_callbacks()
        self._update_ecg()
        self._update_status_labels()
        self._update_connection_status()
        self._update_recording_state()
        self._update_imu()

    def _post_gui(self, callback: object) -> None:
        with self.gui_callbacks_lock:
            self.gui_callbacks.append(callback)

    def _queue_ble_debug(self, message: str) -> None:
        if not self._debug_enabled:
            return
        self._post_gui(lambda message=message: self._append_debug_log(message))

    def _queue_voice_status(self, status: VoiceStatusPayload) -> None:
        reader = self.reader
        pending_count = (
            reader.voice_assembler.pending_count
            if isinstance(reader, BlePacketReader)
            else 0
        )
        self.webhook_tab.update_voice_status(status, pending_count=pending_count)

    def _append_debug_log(self, message: str) -> None:
        if not self._debug_enabled:
            return
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.debug_log_output.appendPlainText(f"[{timestamp}] {message}")

    def _set_debug_enabled(self, enabled: bool) -> None:
        self._debug_enabled = bool(enabled)
        self.debug_group.setVisible(self._debug_enabled)
        level = logging.DEBUG if self._debug_enabled else logging.INFO
        logging.getLogger("smart_neckband").setLevel(level)
        logging.getLogger("bleak").setLevel(level)
        if self._debug_enabled:
            logging.getLogger("bleak.backends.winrt.scanner").setLevel(logging.INFO)
            self._append_debug_log("DEBUG 日志已启用")

    def _drain_gui_callbacks(self) -> None:
        callbacks: list[object] = []
        with self.gui_callbacks_lock:
            while self.gui_callbacks:
                callbacks.append(self.gui_callbacks.popleft())
        for callback in callbacks:
            callback()

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
        link_name = "BLE" if self.transport_combo.currentData() == "ble" else "串口"
        self.port_status_label.setText(
            f"{link_name} {snapshot.port or '--'}：{'已连接' if snapshot.serial_open else '未连接'}"
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
        self.webhook_tab.set_receiving(snapshot.state is ConnectionState.RECEIVING)
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
            connection_type=(
                "Bluetooth LE GATT"
                if self.transport_combo.currentData() == "ble"
                else "USB serial bench connection"
            ),
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
            return
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
        marker_enabled = self.session_recorder is not None and self.recording_state is RecordingState.RECORDING
        for button in (
            self.swallow_marker_button,
            self.cough_marker_button,
            self.talk_marker_button,
            self.turn_marker_button,
            self.custom_marker_button,
        ):
            button.setEnabled(marker_enabled)
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

    def refresh_history_sessions(self) -> None:
        self.history_session_combo.clear()
        records = list_session_records(Path("data") / "sessions")
        self.history_records = records
        if not records:
            self.history_session_combo.addItem("暂无历史记录", None)
            self.compare_a_combo.clear()
            self.compare_b_combo.clear()
            self.compare_a_combo.addItem("暂无历史记录", None)
            self.compare_b_combo.addItem("暂无历史记录", None)
            self.history_status_label.setText("暂无历史记录")
            return
        for record in records:
            metadata = record.metadata
            label = (
                f"{metadata.started_at or metadata.ended_at or metadata.session_id} | "
                f"{metadata.display_name} | {metadata.placement_id} {metadata.placement_name} | "
                f"{metadata.sample_count} 点"
            )
            self.history_session_combo.addItem(label, record)
        self._fill_compare_session_combos(records)
        self.history_status_label.setText(f"找到 {len(records)} 条历史记录")

    def _fill_compare_session_combos(self, records: tuple[object, ...]) -> None:
        self.compare_a_combo.clear()
        self.compare_b_combo.clear()
        for record in records:
            metadata = record.metadata
            label = f"{metadata.display_name} | {metadata.placement_id} {metadata.placement_name} | {metadata.session_id}"
            self.compare_a_combo.addItem(label, record)
            self.compare_b_combo.addItem(label, record)
        if self.compare_b_combo.count() > 1:
            self.compare_b_combo.setCurrentIndex(1)

    def _load_session_async(self, record: object, on_loaded: object, on_error: object) -> None:
        cached = self.session_load_cache.get(record.session_dir)
        if cached is not None:
            on_loaded(*cached)
            return

        def worker() -> None:
            try:
                result = self._load_session_sync(record)
            except Exception as exc:
                message = str(exc)
                self._post_gui(lambda: on_error(message))
                return
            self._post_gui(lambda: on_loaded(*result))

        Thread(target=worker, name=f"SessionLoad-{record.metadata.session_id}", daemon=True).start()

    def _load_session_sync(self, record: object) -> tuple[HistoryEcgData, tuple[object, ...], dict[str, object]]:
        cached = self.session_load_cache.get(record.session_dir)
        if cached is not None:
            return cached
        markers = load_session_markers(record.session_dir)
        analysis_summary = load_session_analysis_summary(record.session_dir)
        samples = load_session_ecg_samples(record.session_dir)
        ecg_data = analyze_history_ecg(samples)
        result = (ecg_data, markers, analysis_summary)
        self.session_load_cache[record.session_dir] = result
        return result

    def load_selected_history_session(self) -> None:
        record = self.history_session_combo.currentData()
        if record is None:
            return
        if self.history_loading:
            return
        self.history_loading = True
        self.history_load_button.setEnabled(False)
        self.history_status_label.setText("正在后台加载历史记录……")

        def on_loaded(ecg_data: HistoryEcgData, markers: tuple[object, ...], analysis_summary: dict[str, object]) -> None:
            self.history_loading = False
            self.history_load_button.setEnabled(True)
            self.history_record = record
            self.history_markers = markers
            self.history_ecg_data = ecg_data
            metadata = record.metadata
            duration_s = session_duration_seconds(ecg_data)
            self.history_viewport = self._clamped_history_viewport(
                ComparisonViewport(0.0, self._selected_history_duration())
            )
            self.history_status_label.setText(
                f"已加载：{metadata.display_name}，{len(ecg_data.samples)} 点，约 {duration_s:.1f} 秒，分析 {ecg_data.message}"
            )
            self.history_metadata_label.setText(
                " | ".join(
                    (
                        f"点位 {metadata.placement_id} {metadata.placement_name}",
                        f"线序 {metadata.wire_map}",
                        f"电极 {metadata.electrode_type or '--'}",
                        f"状态 {metadata.status}",
                        f"端口 {metadata.port or '--'}",
                        f"备注 {metadata.notes or '--'}",
                    )
                )
            )
            self.history_analysis_label.setText(
                " | ".join(
                    (
                        f"markers {len(markers)}",
                        f"analysis_completed {analysis_summary.get('analysis_completed', False)}",
                        f"library {analysis_summary.get('analysis_library', '--')} "
                        f"{analysis_summary.get('analysis_library_version', '')}",
                    )
                )
            )
            self.update_history_plot()

        def on_error(message: str) -> None:
            self.history_loading = False
            self.history_load_button.setEnabled(True)
            self.history_status_label.setText(f"加载失败：{message}")

        self._load_session_async(record, on_loaded, on_error)

    def _selected_history_duration(self) -> float | None:
        value = self.history_duration_combo.currentData()
        return None if value is None else float(value)

    def _clamped_history_viewport(self, viewport: ComparisonViewport) -> ComparisonViewport:
        if self.history_ecg_data is None:
            return viewport
        return clamp_viewport(viewport, max_duration_seconds=session_duration_seconds(self.history_ecg_data))

    def update_history_duration(self, _value: object = None) -> None:
        self.history_viewport = self._clamped_history_viewport(
            ComparisonViewport(self.history_viewport.start_seconds, self._selected_history_duration())
        )
        self.update_history_plot()

    def update_history_scrollbar(self, value: int) -> None:
        if self.history_ecg_data is None:
            return
        self.history_viewport = self._clamped_history_viewport(
            ComparisonViewport(value / 1000.0, self.history_viewport.duration_seconds)
        )
        self.update_history_plot(update_scrollbar=False)

    def update_history_plot(self, _value: object = None, *, update_scrollbar: bool = True) -> None:
        if self.history_ecg_data is None:
            return
        data = self.history_ecg_data
        mode = self.history_mode_combo.currentData()
        x_values, y_values, sample_slice = visible_values(
            data,
            mode=str(mode),
            viewport=self.history_viewport,
            y_axis_mode="auto",
        )
        if not data.samples or not y_values:
            self.history_curve.setData([], [])
            self.history_peak_scatter.setData([])
            return
        x_plot, y_plot = downsample_xy(x_values, y_values, max_points=12_000)
        self.history_curve.setData(x_plot, y_plot)

        if self.history_show_rpeaks_checkbox.isChecked() and mode == "clean" and data.r_peak_indices:
            start = sample_slice.start or 0
            spots = [
                {"pos": (x_values[index - start], y_values[index - start])}
                for index in visible_r_peak_indices(data, sample_slice)
                if 0 <= index - start < len(y_values)
            ]
            self.history_peak_scatter.setData(spots)
        else:
            self.history_peak_scatter.setData([])

        for item in self.history_marker_items:
            self.history_plot.removeItem(item)
        self.history_marker_items.clear()
        if self.history_show_markers_checkbox.isChecked() and self.history_record is not None:
            track = ComparisonTrack("历史", self.history_record, data, self.history_markers)
            for offset, marker in enumerate(visible_markers(track, sample_slice)):
                x_pos = (marker.sample_index - data.samples[0].sample_index) / float(ECG_SAMPLE_RATE_HZ)
                line = self.pg.InfiniteLine(
                    pos=x_pos,
                    angle=90,
                    pen=self.pg.mkPen("#f9a825", width=1, style=self.QtCore.Qt.DashLine),
                )
                line.setToolTip(f"{marker.label} {x_pos:.2f} 秒")
                self.history_plot.addItem(line)
                self.history_marker_items.append(line)
                label = self.pg.TextItem(marker.label, color="#f9a825", anchor=(0.5, 0.0 if offset % 2 else 1.0))
                label.setPos(x_pos, 0.0)
                label.setToolTip(f"{marker.label} {x_pos:.2f} 秒")
                self.history_plot.addItem(label)
                self.history_marker_items.append(label)
        y_range = y_range_for(y_values)
        if y_range is not None:
            self.history_plot.setYRange(*y_range, padding=0.0)
        end_seconds = self._history_view_end_seconds()
        self.history_plot.setXRange(self.history_viewport.start_seconds, end_seconds, padding=0.0)
        self._update_history_range_label(sample_slice)
        if update_scrollbar:
            self._update_history_scrollbar_control()

    def _history_view_end_seconds(self) -> float:
        if self.history_viewport.duration_seconds is None:
            return session_duration_seconds(self.history_ecg_data) if self.history_ecg_data is not None else 0.0
        return self.history_viewport.start_seconds + self.history_viewport.duration_seconds

    def _update_history_range_label(self, sample_slice: slice) -> None:
        if self.history_ecg_data is None:
            return
        start = self.history_viewport.start_seconds
        end = self._history_view_end_seconds()
        duration_label = "完整记录" if self.history_viewport.duration_seconds is None else f"{self.history_viewport.duration_seconds:.0f} 秒"
        sample_count = max(0, (sample_slice.stop or 0) - (sample_slice.start or 0))
        peak_count = len(visible_r_peak_indices(self.history_ecg_data, sample_slice))
        total_duration = session_duration_seconds(self.history_ecg_data)
        self.history_range_label.setText(
            f"当前范围：{start:.1f} ～ {end:.1f} 秒，显示时长 {duration_label} | "
            f"样本 {sample_count}，R 峰 {peak_count}，总长 {total_duration:.2f} 秒"
        )

    def _update_history_scrollbar_control(self) -> None:
        if self.history_ecg_data is None:
            return
        max_duration = session_duration_seconds(self.history_ecg_data)
        duration = self.history_viewport.duration_seconds
        maximum = 0 if duration is None else max(0, int(round((max_duration - duration) * 1000.0)))
        self.history_scrollbar.blockSignals(True)
        self.history_scrollbar.setRange(0, maximum)
        self.history_scrollbar.setSingleStep(500)
        self.history_scrollbar.setPageStep(max(1, int(round((duration or max_duration) * 1000.0))))
        self.history_scrollbar.setValue(int(round(self.history_viewport.start_seconds * 1000.0)))
        self.history_scrollbar.blockSignals(False)

    def load_compare_sessions(self) -> None:
        record_a = self.compare_a_combo.currentData()
        record_b = self.compare_b_combo.currentData()
        if record_a is None or record_b is None:
            return
        if self.compare_loading:
            return
        self.compare_loading = True
        self.compare_load_button.setEnabled(False)
        self.compare_status_label.setText("正在后台加载对比记录……")

        def worker() -> None:
            try:
                data_a, markers_a, _summary_a = self._load_session_sync(record_a)
                data_b, markers_b, _summary_b = self._load_session_sync(record_b)
            except Exception as exc:
                message = str(exc)
                self._post_gui(lambda: self._finish_compare_load_error(message))
                return
            track_a = ComparisonTrack("A", record_a, data_a, markers_a)
            track_b = ComparisonTrack("B", record_b, data_b, markers_b)
            self._post_gui(lambda: self._finish_compare_load(record_a, record_b, track_a, track_b))

        Thread(target=worker, name="CompareSessionLoad", daemon=True).start()

    def _finish_compare_load(
        self,
        record_a: object,
        record_b: object,
        track_a: ComparisonTrack,
        track_b: ComparisonTrack,
    ) -> None:
        self.compare_loading = False
        self.compare_load_button.setEnabled(True)
        self.compare_a = track_a
        self.compare_b = track_b
        self.compare_viewport = self._clamped_compare_viewport(ComparisonViewport(0.0, self._selected_compare_duration()))
        self.compare_status_label.setText(
            f"A {record_a.metadata.display_name} / B {record_b.metadata.display_name}"
        )
        self.update_compare_plot()

    def _finish_compare_load_error(self, message: str) -> None:
        self.compare_loading = False
        self.compare_load_button.setEnabled(True)
        self.compare_status_label.setText(f"加载失败：{message}")

    def _load_compare_track(self, label: str, record: object) -> ComparisonTrack:
        data, markers, _summary = self._load_session_sync(record)
        return ComparisonTrack(label, record, data, markers)

    def _selected_compare_duration(self) -> float | None:
        value = self.compare_duration_combo.currentData()
        return None if value is None else float(value)

    def _max_compare_duration(self) -> float:
        durations = []
        if self.compare_a is not None:
            durations.append(session_duration_seconds(self.compare_a.data))
        if self.compare_b is not None:
            durations.append(session_duration_seconds(self.compare_b.data))
        return max(durations) if durations else 0.0

    def _clamped_compare_viewport(self, viewport: ComparisonViewport) -> ComparisonViewport:
        return clamp_viewport(viewport, max_duration_seconds=self._max_compare_duration())

    def update_compare_duration(self, _value: object = None) -> None:
        self.compare_viewport = self._clamped_compare_viewport(
            ComparisonViewport(self.compare_viewport.start_seconds, self._selected_compare_duration())
        )
        self.update_compare_plot()

    def update_compare_scrollbar(self, value: int) -> None:
        if self.compare_a is None or self.compare_b is None:
            return
        self.compare_viewport = self._clamped_compare_viewport(
            ComparisonViewport(value / 1000.0, self.compare_viewport.duration_seconds)
        )
        self.update_compare_plot(update_scrollbar=False)

    def update_compare_plot(self, _value: object = None, *, update_scrollbar: bool = True) -> None:
        if self.compare_a is None or self.compare_b is None:
            return
        mode = self.compare_mode_combo.currentData()
        y_axis_mode = self.compare_y_mode_combo.currentData()
        self.compare_viewport = self._clamped_compare_viewport(self.compare_viewport)
        x_a, y_a, slice_a = visible_values(
            self.compare_a.data,
            mode=str(mode),
            viewport=self.compare_viewport,
            y_axis_mode=str(y_axis_mode),
        )
        x_b, y_b, slice_b = visible_values(
            self.compare_b.data,
            mode=str(mode),
            viewport=self.compare_viewport,
            y_axis_mode=str(y_axis_mode),
        )
        self.compare_curve_a.setData(x_a, y_a)
        self.compare_curve_b.setData(x_b, y_b)
        self._update_compare_titles(str(mode))
        self._update_compare_y_ranges(y_a, y_b, str(y_axis_mode))
        self._update_compare_rpeaks(self.compare_a, self.compare_peak_scatter_a, x_a, y_a, slice_a)
        self._update_compare_rpeaks(self.compare_b, self.compare_peak_scatter_b, x_b, y_b, slice_b)
        self._update_compare_markers(
            self.compare_a,
            self.compare_plot_a,
            self.compare_marker_items_a,
            slice_a,
            "#1769aa",
        )
        self._update_compare_markers(
            self.compare_b,
            self.compare_plot_b,
            self.compare_marker_items_b,
            slice_b,
            "#d81b60",
        )
        self._update_compare_range_label(slice_a, slice_b)
        if update_scrollbar:
            self._update_compare_scrollbar_control()
        end_seconds = self._compare_view_end_seconds()
        self.compare_plot_a.setXRange(self.compare_viewport.start_seconds, end_seconds, padding=0.0)
        self.compare_plot_b.setXRange(self.compare_viewport.start_seconds, end_seconds, padding=0.0)

    def _compare_view_end_seconds(self) -> float:
        if self.compare_viewport.duration_seconds is None:
            return self._max_compare_duration()
        return self.compare_viewport.start_seconds + self.compare_viewport.duration_seconds

    def _update_compare_scrollbar_control(self) -> None:
        max_duration = self._max_compare_duration()
        duration = self.compare_viewport.duration_seconds
        maximum = 0 if duration is None else max(0, int(round((max_duration - duration) * 1000.0)))
        self.compare_scrollbar.blockSignals(True)
        self.compare_scrollbar.setRange(0, maximum)
        self.compare_scrollbar.setSingleStep(500)
        self.compare_scrollbar.setPageStep(max(1, int(round((duration or max_duration) * 1000.0))))
        self.compare_scrollbar.setValue(int(round(self.compare_viewport.start_seconds * 1000.0)))
        self.compare_scrollbar.blockSignals(False)

    def _update_compare_titles(self, mode: str) -> None:
        if self.compare_a is None or self.compare_b is None:
            return
        mode_text = "清洗后 ECG" if mode == "clean" else "原始 ECG"
        for plot, track in ((self.compare_plot_a, self.compare_a), (self.compare_plot_b, self.compare_b)):
            metadata = track.record.metadata
            plot.setTitle(f"记录 {track.label} · {metadata.placement_name} · {mode_text}")

    def _update_compare_y_ranges(self, y_a: list[float], y_b: list[float], y_axis_mode: str) -> None:
        self.compare_normalized_note.setVisible(y_axis_mode == "normalized")
        if y_axis_mode == "same":
            combined_range = y_range_for(y_a + y_b)
            if combined_range is not None:
                self.compare_plot_a.setYRange(*combined_range, padding=0.0)
                self.compare_plot_b.setYRange(*combined_range, padding=0.0)
            return
        if y_axis_mode == "normalized":
            self.compare_plot_a.setYRange(-1.1, 1.1, padding=0.0)
            self.compare_plot_b.setYRange(-1.1, 1.1, padding=0.0)
            return
        range_a = y_range_for(y_a)
        range_b = y_range_for(y_b)
        if range_a is not None:
            self.compare_plot_a.setYRange(*range_a, padding=0.0)
        if range_b is not None:
            self.compare_plot_b.setYRange(*range_b, padding=0.0)

    def _update_compare_rpeaks(
        self,
        track: ComparisonTrack,
        scatter: object,
        x_values: list[float],
        y_values: list[float],
        sample_slice: slice,
    ) -> None:
        if not self.compare_show_rpeaks_checkbox.isChecked():
            scatter.setData([])
            return
        start = sample_slice.start or 0
        spots = []
        for index in visible_r_peak_indices(track.data, sample_slice):
            local_index = index - start
            if 0 <= local_index < len(x_values) and 0 <= local_index < len(y_values):
                spots.append({"pos": (x_values[local_index], y_values[local_index])})
        scatter.setData(spots)

    def _update_compare_markers(
        self,
        track: ComparisonTrack,
        plot: object,
        marker_items: list[object],
        sample_slice: slice,
        color: str,
    ) -> None:
        for item in marker_items:
            plot.removeItem(item)
        marker_items.clear()
        if not self.compare_show_markers_checkbox.isChecked():
            return
        for offset, marker in enumerate(visible_markers(track, sample_slice)):
            if not track.data.samples:
                continue
            x_pos = (marker.sample_index - track.data.samples[0].sample_index) / float(ECG_SAMPLE_RATE_HZ)
            pen = self.pg.mkPen(color, width=1, style=self.QtCore.Qt.DashLine)
            line = self.pg.InfiniteLine(pos=x_pos, angle=90, pen=pen)
            line.setToolTip(f"{marker.label} {x_pos:.2f} 秒")
            plot.addItem(line)
            marker_items.append(line)
            label = self.pg.TextItem(marker.label, color=color, anchor=(0.5, 0.0 if offset % 2 else 1.0))
            label.setPos(x_pos, 0.0)
            label.setToolTip(f"{marker.label} {x_pos:.2f} 秒")
            plot.addItem(label)
            marker_items.append(label)

    def _update_compare_range_label(self, slice_a: slice, slice_b: slice) -> None:
        start = self.compare_viewport.start_seconds
        end = self._compare_view_end_seconds()
        duration_label = "完整记录" if self.compare_viewport.duration_seconds is None else f"{self.compare_viewport.duration_seconds:.0f} 秒"
        samples_a = max(0, (slice_a.stop or 0) - (slice_a.start or 0))
        samples_b = max(0, (slice_b.stop or 0) - (slice_b.start or 0))
        duration_a = session_duration_seconds(self.compare_a.data) if self.compare_a is not None else 0.0
        duration_b = session_duration_seconds(self.compare_b.data) if self.compare_b is not None else 0.0
        peaks_a = len(visible_r_peak_indices(self.compare_a.data, slice_a)) if self.compare_a is not None else 0
        peaks_b = len(visible_r_peak_indices(self.compare_b.data, slice_b)) if self.compare_b is not None else 0
        self.compare_range_label.setText(
            f"当前范围：{start:.1f} ～ {end:.1f} 秒，显示时长 {duration_label} | "
            f"A 样本 {samples_a}，R 峰 {peaks_a}，总长 {duration_a:.2f} 秒 | "
            f"B 样本 {samples_b}，R 峰 {peaks_b}，总长 {duration_b:.2f} 秒 | "
            "拖动滚动条浏览记录，滚轮仅缩放时间轴"
        )

    def jump_compare_marker_type(self) -> None:
        marker_type = self.compare_marker_jump_combo.currentData()
        if marker_type == "all":
            self.compare_viewport = self._clamped_compare_viewport(
                ComparisonViewport(0.0, self.compare_viewport.duration_seconds)
            )
            self.update_compare_plot()

    def jump_compare_marker(self, direction: int) -> None:
        if self.compare_a is None or self.compare_b is None:
            return
        marker_type = self.compare_marker_jump_combo.currentData()
        marker_times = self._compare_marker_times(None if marker_type == "all" else str(marker_type))
        if not marker_times:
            self.compare_status_label.setText("当前记录没有匹配的动作标记")
            return
        duration = self.compare_viewport.duration_seconds or min(10.0, self._max_compare_duration())
        center = self.compare_viewport.start_seconds + duration / 2.0
        if direction > 0:
            candidates = [value for value in marker_times if value > center]
            target = candidates[0] if candidates else marker_times[0]
        else:
            candidates = [value for value in marker_times if value < center]
            target = candidates[-1] if candidates else marker_times[-1]
        self.compare_viewport = self._clamped_compare_viewport(
            ComparisonViewport(max(0.0, target - duration / 2.0), duration)
        )
        self.update_compare_plot()

    def _compare_marker_times(self, marker_type: str | None) -> list[float]:
        times: list[float] = []
        for track in (self.compare_a, self.compare_b):
            if track is None or not track.data.samples:
                continue
            first_sample_index = track.data.samples[0].sample_index
            for marker in track.markers:
                if marker_type is None or marker.type == marker_type:
                    times.append((marker.sample_index - first_sample_index) / float(ECG_SAMPLE_RATE_HZ))
        return sorted(set(times))

    def export_compare_csv(self) -> None:
        if self.compare_a is None or self.compare_b is None:
            self.compare_status_label.setText("请先加载 A/B 对比")
            return
        choice, accepted = self.QtWidgets.QInputDialog.getItem(
            self.window,
            "导出范围",
            "导出范围",
            ("当前可见范围", "完整记录"),
            0,
            False,
        )
        if not accepted:
            return
        export_full = choice == "完整记录"
        export_path = Path("data") / "exports" / default_compare_csv_name(
            self.compare_a.record,
            self.compare_b.record,
        )
        self.compare_status_label.setText("正在写入 CSV……")

        def worker() -> None:
            try:
                write_compare_csv(
                    export_path,
                    track_a=self.compare_a,
                    track_b=self.compare_b,
                    viewport=self.compare_viewport,
                    export_full=export_full,
                )
            except Exception as exc:
                message = str(exc)
                self.QtCore.QTimer.singleShot(0, lambda: self.compare_status_label.setText(f"CSV 导出失败：{message}"))
                return
            self.QtCore.QTimer.singleShot(0, lambda: self.compare_status_label.setText(f"CSV 已导出：{export_path}"))

        Thread(target=worker, name="CompareCsvExport", daemon=True).start()

    def export_compare_png(self) -> None:
        if self.compare_a is None or self.compare_b is None:
            self.compare_status_label.setText("请先加载 A/B 对比")
            return
        export_path = Path("data") / "exports" / f"compare_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        export_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.compare_tab_widget.grab().save(str(export_path))
        except Exception as exc:
            self.compare_status_label.setText(f"PNG 导出失败：{exc}")
            return
        self.compare_status_label.setText(f"PNG 已导出：{export_path}")

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
        raise SystemExit("Install the gui optional dependencies: bleak pyserial PySide6 pyqtgraph neurokit2 numpy") from exc

    debug_log_path = configure_debug_logging()
    app = QtWidgets.QApplication([])
    window = MainWindow(debug_log_path=debug_log_path)
    app.aboutToQuit.connect(window.close)
    window.show()
    return app.exec()
