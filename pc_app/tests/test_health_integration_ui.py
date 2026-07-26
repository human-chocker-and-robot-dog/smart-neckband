from __future__ import annotations

import os

from smart_neckband.dashboard_relay import DashboardRelayStatus
from smart_neckband.health_integration import HealthIntegrationStatus, MCP_TOOL_NAMES


def test_health_panel_exposes_mcp_inside_main_gui(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    monkeypatch.setenv("SMART_COLLAR_WEARER_ID", "xwen")
    monkeypatch.setenv("SMART_COLLAR_HEALTH_DB_PATH", str(tmp_path / "health.db"))

    from PySide6 import QtCore, QtWidgets

    from smart_neckband.health_integration_ui import HealthIntegrationPanel

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None

    class FakeController:
        def __init__(self) -> None:
            self.runtime_settings = None
            self.mcp_settings = None
            self.closed = False

        def auto_start(self, settings) -> None:
            self.runtime_settings = settings
            self.mcp_settings = settings

        def start_runtime(self, settings) -> None:
            self.runtime_settings = settings

        def start_mcp(self, settings) -> None:
            self.mcp_settings = settings

        def stop_mcp(self) -> None:
            self.mcp_settings = None

        def close(self) -> None:
            self.closed = True

        def status(self) -> HealthIntegrationStatus:
            return HealthIntegrationStatus(
                health_running=self.runtime_settings is not None,
                mcp_running=self.mcp_settings is not None,
                mcp_pid=1234 if self.mcp_settings is not None else None,
                mcp_exit_code=None,
                mcp_log_path=None,
                dashboard=DashboardRelayStatus(
                    configured=False,
                    running=False,
                    connected=False,
                    endpoint="-",
                    public_url="/dashboard",
                    last_success_at=None,
                    last_error=None,
                ),
                last_error=None,
            )

    controller = FakeController()
    panel = HealthIntegrationPanel(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        stores=object(),
        reader_provider=lambda: None,
        analysis_provider=lambda: None,
        controller=controller,  # type: ignore[arg-type]
    )
    panel.auto_start()
    panel.refresh_status()

    assert panel.tools_label.text().splitlines() == list(MCP_TOOL_NAMES)
    assert controller.runtime_settings.wearer_id == "xwen"
    assert controller.mcp_settings.mcp_host == "0.0.0.0"
    assert not hasattr(panel, "bearer_token")
    assert "PID 1234" in panel.mcp_status.text()

    panel.close()
    assert controller.closed
