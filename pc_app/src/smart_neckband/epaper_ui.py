from __future__ import annotations

from collections.abc import Callable
from threading import Thread
import time

from .analysis import EcgAnalysisResult
from .epaper_ble import (
    EpaperDevice,
    EpaperDisplayClient,
    EpaperRuntimeStatus,
    list_epaper_devices,
)
from .epaper_protocol import DisplayStateCode, RefreshMode
from .epaper_sync import (
    EpaperDisplayState,
    EpaperDisplayStateBuilder,
    EpaperFrameScheduler,
    RenderedEpaperFrame,
    framebuffer_to_qimage,
    render_epaper_frame,
)
from .serial_io import PcDataStores


class EpaperSyncPanel:
    def __init__(
        self,
        *,
        QtCore: object,
        QtGui: object,
        QtWidgets: object,
        stores: PcDataStores,
        analysis_provider: Callable[[], EcgAnalysisResult | None],
        post_gui: Callable[[object], None],
        scanner: Callable[[], list[EpaperDevice]] = list_epaper_devices,
        client_builder: Callable[..., EpaperDisplayClient] = EpaperDisplayClient,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        self.stores = stores
        self.analysis_provider = analysis_provider
        self.post_gui = post_gui
        self.scanner = scanner
        self.client_builder = client_builder
        self.monotonic = monotonic
        self.client: EpaperDisplayClient | None = None
        self.state_builder = EpaperDisplayStateBuilder()
        self.scheduler = EpaperFrameScheduler(interval_seconds=15.0)
        self.latest_state: EpaperDisplayState | None = None
        self.latest_rendered: RenderedEpaperFrame | None = None
        self._last_render_monotonic_s = 0.0
        self._scan_generation = 0
        self._scan_thread: Thread | None = None

        self.widget = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.widget)

        device_group = QtWidgets.QGroupBox("Quote/0 设备")
        device_layout = QtWidgets.QGridLayout(device_group)
        self.device_combo = QtWidgets.QComboBox()
        self.scan_button = QtWidgets.QPushButton("扫描墨水屏")
        self.connect_button = QtWidgets.QPushButton("连接墨水屏")
        self.disconnect_button = QtWidgets.QPushButton("断开墨水屏")
        self.auto_reconnect_checkbox = QtWidgets.QCheckBox("自动重连")
        self.auto_reconnect_checkbox.setChecked(True)
        self.connection_label = QtWidgets.QLabel("墨水屏未连接")
        self.connection_label.setWordWrap(True)
        device_layout.addWidget(self.device_combo, 0, 0, 1, 4)
        device_layout.addWidget(self.scan_button, 0, 4)
        device_layout.addWidget(self.connect_button, 0, 5)
        device_layout.addWidget(self.disconnect_button, 0, 6)
        device_layout.addWidget(self.auto_reconnect_checkbox, 1, 0)
        device_layout.addWidget(self.connection_label, 1, 1, 1, 6)
        root.addWidget(device_group)

        sync_group = QtWidgets.QGroupBox("同步控制")
        sync_layout = QtWidgets.QHBoxLayout(sync_group)
        self.auto_sync_checkbox = QtWidgets.QCheckBox("启用自动同步")
        self.interval_spin = QtWidgets.QSpinBox()
        self.interval_spin.setRange(10, 60)
        self.interval_spin.setValue(15)
        self.interval_spin.setSuffix(" 秒")
        self.send_now_button = QtWidgets.QPushButton("立即发送")
        self.force_full_button = QtWidgets.QPushButton("下一帧强制全刷")
        sync_layout.addWidget(self.auto_sync_checkbox)
        sync_layout.addWidget(QtWidgets.QLabel("最短发送间隔"))
        sync_layout.addWidget(self.interval_spin)
        sync_layout.addWidget(self.send_now_button)
        sync_layout.addWidget(self.force_full_button)
        sync_layout.addStretch(1)
        root.addWidget(sync_group)

        metrics_group = QtWidgets.QGroupBox("当前显示数据")
        metrics_layout = QtWidgets.QGridLayout(metrics_group)
        self.hr_label = QtWidgets.QLabel("BPM --")
        self.hrv_label = QtWidgets.QLabel("HRV -- ms")
        self.sqi_label = QtWidgets.QLabel("SQI --")
        self.lead_label = QtWidgets.QLabel("导联等待")
        self.source_label = QtWidgets.QLabel("源样本 --")
        self.age_label = QtWidgets.QLabel("数据年龄 --")
        self.send_status_label = QtWidgets.QLabel("尚未发送画面")
        self.send_status_label.setWordWrap(True)
        for column, widget in enumerate(
            (
                self.hr_label,
                self.hrv_label,
                self.sqi_label,
                self.lead_label,
                self.source_label,
                self.age_label,
            )
        ):
            metrics_layout.addWidget(widget, 0, column)
        metrics_layout.addWidget(self.send_status_label, 1, 0, 1, 6)
        root.addWidget(metrics_group)

        preview_group = QtWidgets.QGroupBox("电子墨水屏预览 · 296×152")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)
        self.preview_label = QtWidgets.QLabel()
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setMinimumSize(592, 304)
        self.preview_label.setStyleSheet("QLabel { background: white; border: 1px solid #777; }")
        preview_layout.addWidget(self.preview_label)
        root.addWidget(preview_group, 1)

        self.debug_group = QtWidgets.QGroupBox("墨水屏同步日志")
        self.debug_group.setCheckable(True)
        self.debug_group.setChecked(False)
        debug_layout = QtWidgets.QVBoxLayout(self.debug_group)
        self.debug_log = QtWidgets.QPlainTextEdit()
        self.debug_log.setReadOnly(True)
        self.debug_log.setMaximumBlockCount(500)
        self.debug_log.setVisible(False)
        debug_layout.addWidget(self.debug_log)
        root.addWidget(self.debug_group)

        self.scan_button.clicked.connect(self.scan_devices)
        self.connect_button.clicked.connect(self.connect_device)
        self.disconnect_button.clicked.connect(self.disconnect_device)
        self.interval_spin.valueChanged.connect(self._set_interval)
        self.send_now_button.clicked.connect(lambda: self.send_current(force_full=False))
        self.force_full_button.clicked.connect(lambda: self.send_current(force_full=True))
        self.debug_group.toggled.connect(self.debug_log.setVisible)

        self._render_preview(force=True)

    def scan_devices(self) -> None:
        self._scan_generation += 1
        generation = self._scan_generation
        self.device_combo.clear()
        self.device_combo.addItem("正在扫描 InkCanvas-Quote0……", "")
        self.scan_button.setEnabled(False)

        def run() -> None:
            try:
                devices = self.scanner()
                error = None
            except Exception as exc:
                devices = []
                error = str(exc)
            self.post_gui(
                lambda: self._apply_scan_result(
                    generation=generation,
                    devices=devices,
                    error=error,
                )
            )

        self._scan_thread = Thread(target=run, name="EpaperDeviceScanner", daemon=True)
        self._scan_thread.start()

    def _apply_scan_result(
        self,
        *,
        generation: int,
        devices: list[EpaperDevice],
        error: str | None,
    ) -> None:
        if generation != self._scan_generation:
            return
        self.scan_button.setEnabled(True)
        self.device_combo.clear()
        if error is not None:
            self.device_combo.addItem(f"扫描失败：{error}", "")
            self._append_debug(f"扫描失败：{error}")
            return
        if not devices:
            self.device_combo.addItem("未发现 InkCanvas-Quote0", "")
            return
        for device in devices:
            self.device_combo.addItem(f"{device.name} ({device.address})", device.address)

    def connect_device(self) -> None:
        address = str(self.device_combo.currentData() or "")
        if not address:
            self.connection_label.setText("请选择扫描到的 InkCanvas-Quote0")
            return
        self.disconnect_device()
        self.client = self.client_builder(
            address=address,
            debug_callback=self._queue_debug,
            auto_reconnect=self.auto_reconnect_checkbox.isChecked(),
        )
        self.client.start()
        self.connection_label.setText(f"正在连接 {address}……")

    def disconnect_device(self) -> None:
        if self.client is not None:
            self.client.stop()
            self.client = None
        self.connection_label.setText("墨水屏未连接")

    def refresh(self) -> None:
        self._refresh_runtime_status()
        now = self.monotonic()
        if now - self._last_render_monotonic_s < 0.5:
            return
        self._last_render_monotonic_s = now
        self._render_preview(force=False)
        if self.client is None or not self.client.runtime_status.connected:
            return
        if self.latest_state is None or self.latest_rendered is None:
            return
        scheduled = self.scheduler.consider(
            rendered=self.latest_rendered,
            state=self.latest_state,
            auto_enabled=self.auto_sync_checkbox.isChecked(),
        )
        if scheduled is not None:
            frame_id = self.client.queue_frame(scheduled)
            self.send_status_label.setText(f"已排队帧 {frame_id}，等待 Quote/0 刷新")

    def send_current(self, *, force_full: bool) -> None:
        if self.client is None or not self.client.runtime_status.connected:
            self.send_status_label.setText("墨水屏未连接，无法发送")
            return
        self._render_preview(force=True)
        if self.latest_state is None or self.latest_rendered is None:
            self.send_status_label.setText("尚无可发送画面")
            return
        scheduled = self.scheduler.consider(
            rendered=self.latest_rendered,
            state=self.latest_state,
            auto_enabled=self.auto_sync_checkbox.isChecked(),
            force=True,
            force_full=force_full,
        )
        if scheduled is None:
            return
        frame_id = self.client.queue_frame(scheduled)
        mode = "强制全刷" if force_full else "自动刷新"
        self.send_status_label.setText(f"已排队帧 {frame_id}（{mode}）")

    def close(self) -> None:
        self._scan_generation += 1
        self.disconnect_device()

    def _render_preview(self, *, force: bool) -> None:
        analysis = self.analysis_provider()
        samples = self.stores.ecg.snapshot()
        source_sample = None
        if analysis is not None and analysis.analyzed_through_ecg_sample_index is not None:
            for sample in reversed(samples):
                if (
                    sample.sample_index == analysis.analyzed_through_ecg_sample_index
                    and sample.source_instance_id == (analysis.source_instance_id or "")
                ):
                    source_sample = sample
                    break
        state = self.state_builder.build(
            analysis=analysis,
            status=self.stores.status.latest(),
            source_sample=source_sample,
        )
        if not force and state == self.latest_state:
            return
        rendered = render_epaper_frame(
            state,
            QtCore=self.QtCore,
            QtGui=self.QtGui,
        )
        self.latest_state = state
        self.latest_rendered = rendered
        image = framebuffer_to_qimage(rendered.frame, QtGui=self.QtGui)
        pixmap = self.QtGui.QPixmap.fromImage(image).scaled(
            592,
            304,
            self.QtCore.Qt.KeepAspectRatio,
            self.QtCore.Qt.FastTransformation,
        )
        self.preview_label.setPixmap(pixmap)
        self.hr_label.setText(
            f"BPM {state.heart_rate_bpm:.0f}" if state.heart_rate_bpm is not None else "BPM --"
        )
        self.hrv_label.setText(
            f"HRV {state.hrv.value_ms:.0f} ms" if state.hrv.valid else "HRV -- ms"
        )
        self.sqi_label.setText(
            f"SQI {state.signal_quality * 100:.0f}%"
            if state.signal_quality is not None
            else "SQI --"
        )
        self.lead_label.setText(state.lead_text)
        self.source_label.setText(
            f"源样本 {state.source_sample_index}" if state.source_sample_index else "源样本 --"
        )
        self.age_label.setText(
            f"数据年龄 {state.data_age_ms} ms" if state.data_age_ms is not None else "数据年龄 --"
        )

    def _refresh_runtime_status(self) -> None:
        if self.client is None:
            return
        runtime = self.client.runtime_status
        if not runtime.connected:
            if runtime.last_error is not None:
                self.connection_label.setText(f"墨水屏连接失败：{runtime.last_error}")
            else:
                self.connection_label.setText(f"正在连接 {runtime.address}……")
            return
        info = runtime.device_info
        info_text = (
            f" · firmware {info.firmware_version}"
            if info is not None
            else ""
        )
        self.connection_label.setText(f"墨水屏已连接 {runtime.address}{info_text}")
        status = runtime.display_status
        if status is None:
            self.send_status_label.setText(
                f"已发送 {runtime.frame_sent_count}，等待设备状态"
            )
            return
        mode = {
            RefreshMode.NONE: "无变化",
            RefreshMode.FULL: "全刷",
            RefreshMode.PARTIAL: "局刷",
        }[status.refresh_mode]
        state = {
            DisplayStateCode.READY: "就绪",
            DisplayStateCode.RECEIVING: "接收中",
            DisplayStateCode.QUEUED: "已排队",
            DisplayStateCode.REFRESHING: "刷新中",
            DisplayStateCode.DONE: "完成",
            DisplayStateCode.ERROR: "错误",
        }[status.state]
        self.send_status_label.setText(
            f"帧 {status.frame_id}：{state} / {mode} / {status.refresh_ms} ms · "
            f"电池 {status.battery_percent}% ({status.battery_mv} mV) · "
            f"局刷累计 {status.partial_refresh_count}"
        )

    def _set_interval(self, value: int) -> None:
        self.scheduler.set_interval_seconds(float(value))

    def _queue_debug(self, message: str) -> None:
        self.post_gui(lambda: self._append_debug(message))

    def _append_debug(self, message: str) -> None:
        self.debug_log.appendPlainText(message)
