from __future__ import annotations

import os

from smart_neckband.epaper_ble import EpaperDevice, EpaperRuntimeStatus
from smart_neckband.serial_io import PcDataStores


class FakeClient:
    def __init__(self, *, address: str, debug_callback=None, auto_reconnect=True) -> None:
        self.address = address
        self.debug_callback = debug_callback
        self.auto_reconnect = auto_reconnect
        self.started = False
        self.stopped = False
        self.queued = []
        self.pending_frame_id = None
        self.active_frame_id = None

    @property
    def runtime_status(self) -> EpaperRuntimeStatus:
        return EpaperRuntimeStatus(
            address=self.address,
            started_at_monotonic_s=0,
            connected=self.started and not self.stopped,
            device_info=None,
            display_status=None,
            pending_frame_id=self.pending_frame_id,
            active_frame_id=self.active_frame_id,
            frame_sent_count=0,
            frame_completed_count=0,
            local_pending_replaced_count=0,
            last_error=None,
        )

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def queue_frame(self, scheduled) -> int:
        frame_id = self.try_queue_frame(scheduled)
        if frame_id is None:
            raise RuntimeError("busy")
        return frame_id

    def try_queue_frame(self, scheduled) -> int | None:
        if self.pending_frame_id is not None or self.active_frame_id is not None:
            return None
        self.queued.append(scheduled)
        return len(self.queued)


def test_epaper_panel_is_integrated_single_gui_surface() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtGui, QtWidgets

    from smart_neckband.epaper_ui import EpaperSyncPanel

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    callbacks = []
    clients = []

    def build_client(**kwargs):
        client = FakeClient(**kwargs)
        clients.append(client)
        return client

    panel = EpaperSyncPanel(
        QtCore=QtCore,
        QtGui=QtGui,
        QtWidgets=QtWidgets,
        stores=PcDataStores.create(),
        analysis_provider=lambda: None,
        post_gui=callbacks.append,
        scanner=lambda: [EpaperDevice("AA:BB", "InkCanvas-Quote0-AABB")],
        client_builder=build_client,
    )
    try:
        assert panel.auto_sync_checkbox.isChecked() is False
        assert panel.interval_spin.value() == 5
        assert panel.waveform_interval_spin.value() == 10
        assert panel.auto_reconnect_checkbox.isChecked() is True
        assert panel.debug_group.isChecked() is False
        assert panel.debug_log.isHidden()
        assert panel.preview_label.pixmap() is not None
        assert panel.hr_label.text() == "BPM --"
        assert panel.hrv_label.text() == "HRV -- ms"

        panel.interval_spin.setValue(6)
        panel.waveform_interval_spin.setValue(12)
        assert panel.scheduler.interval_ns == 6_000_000_000
        assert panel.waveform_snapshotter.interval_ns == 12_000_000_000

        panel._apply_scan_result(
            generation=0,
            devices=[EpaperDevice("AA:BB", "InkCanvas-Quote0-AABB")],
            error=None,
        )
        panel.connect_button.click()
        app.processEvents()
        assert clients[0].started
        assert clients[0].auto_reconnect is True
        clients[0].active_frame_id = 99
        panel.send_now_button.click()
        assert len(clients[0].queued) == 0
        assert "未排队" in panel.send_status_label.text()
        clients[0].active_frame_id = None
        panel.send_now_button.click()
        assert len(clients[0].queued) == 1
        panel.debug_group.setChecked(True)
        app.processEvents()
        assert not panel.debug_log.isHidden()
    finally:
        panel.close()
    assert clients[0].stopped


def test_main_window_contains_epaper_sync_tab(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    from PySide6 import QtWidgets

    from smart_neckband.gui import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        tabs = window.window.centralWidget()
        assert "墨水屏同步" in [tabs.tabText(index) for index in range(tabs.count())]
        assert window.epaper_panel.widget is not None
    finally:
        window.close()
