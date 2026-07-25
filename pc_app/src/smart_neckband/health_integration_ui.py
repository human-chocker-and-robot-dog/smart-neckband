from __future__ import annotations

from pathlib import Path

from .health_integration import (
    MCP_TOOL_NAMES,
    HealthIntegrationController,
    HealthIntegrationSettings,
)


class HealthIntegrationPanel:
    def __init__(
        self,
        *,
        QtCore: object,
        QtWidgets: object,
        stores: object,
        reader_provider,
        analysis_provider,
        controller: HealthIntegrationController | None = None,
    ) -> None:
        self.QtWidgets = QtWidgets
        self.controller = controller or HealthIntegrationController(
            stores=stores,
            reader_provider=reader_provider,
            analysis_provider=analysis_provider,
        )
        configured = HealthIntegrationSettings.from_environment()

        self.widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.widget)

        summary = QtWidgets.QGroupBox("统一 Health 数据服务")
        summary_layout = QtWidgets.QGridLayout(summary)
        self.health_status = QtWidgets.QLabel("Health 数据：未启动")
        self.mcp_status = QtWidgets.QLabel("MCP：未启动")
        self.endpoint_status = QtWidgets.QLabel("RDK endpoint：--")
        self.log_status = QtWidgets.QLabel("MCP 日志：--")
        self.error_status = QtWidgets.QLabel("")
        self.error_status.setWordWrap(True)
        summary_layout.addWidget(self.health_status, 0, 0)
        summary_layout.addWidget(self.mcp_status, 0, 1)
        summary_layout.addWidget(self.endpoint_status, 1, 0, 1, 2)
        summary_layout.addWidget(self.log_status, 2, 0, 1, 2)
        summary_layout.addWidget(self.error_status, 3, 0, 1, 2)
        layout.addWidget(summary)

        health_group = QtWidgets.QGroupBox("Health 数据源")
        health_form = QtWidgets.QFormLayout(health_group)
        self.wearer_id = QtWidgets.QLineEdit(configured.wearer_id)
        self.wearer_id.setPlaceholderText("稳定匿名 wearer ID，例如 xwen")
        self.db_path = QtWidgets.QLineEdit(str(configured.db_path))
        self.rules_path = QtWidgets.QLineEdit(
            str(configured.rules_path) if configured.rules_path is not None else ""
        )
        self.rules_path.setPlaceholderText("可选 health_rules.json")
        health_form.addRow("Wearer ID", self.wearer_id)
        health_form.addRow("SQLite", self.db_path)
        health_form.addRow("规则文件", self.rules_path)
        layout.addWidget(health_group)

        mcp_group = QtWidgets.QGroupBox("RDK Streamable HTTP MCP")
        mcp_form = QtWidgets.QFormLayout(mcp_group)
        self.mcp_host = QtWidgets.QLineEdit(configured.mcp_host)
        self.mcp_port = QtWidgets.QSpinBox()
        self.mcp_port.setRange(1, 65_535)
        self.mcp_port.setValue(configured.mcp_port)
        self.mcp_path = QtWidgets.QLineEdit(configured.mcp_path)
        mcp_form.addRow("监听地址", self.mcp_host)
        mcp_form.addRow("端口", self.mcp_port)
        mcp_form.addRow("路径", self.mcp_path)
        layout.addWidget(mcp_group)

        tools_group = QtWidgets.QGroupBox("Agent 可调用的四个 MCP 工具")
        tools_layout = QtWidgets.QVBoxLayout(tools_group)
        self.tools_label = QtWidgets.QLabel("\n".join(MCP_TOOL_NAMES))
        self.tools_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        tools_layout.addWidget(self.tools_label)
        layout.addWidget(tools_group)

        controls = QtWidgets.QHBoxLayout()
        self.start_health_button = QtWidgets.QPushButton("启动 Health 数据")
        self.start_mcp_button = QtWidgets.QPushButton("启动 Health + MCP")
        self.stop_mcp_button = QtWidgets.QPushButton("停止 MCP")
        self.stop_all_button = QtWidgets.QPushButton("停止全部 Health 服务")
        controls.addWidget(self.start_health_button)
        controls.addWidget(self.start_mcp_button)
        controls.addWidget(self.stop_mcp_button)
        controls.addWidget(self.stop_all_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        notice = QtWidgets.QLabel(
            "黑客松可信内网模式：MCP 不做应用层鉴权，由 CPE 内网边界负责访问控制。"
            "不要把此端口直接暴露到公网。"
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        layout.addStretch(1)

        self.start_health_button.clicked.connect(self.start_health)
        self.start_mcp_button.clicked.connect(self.start_mcp)
        self.stop_mcp_button.clicked.connect(self.stop_mcp)
        self.stop_all_button.clicked.connect(self.stop_all)

        self.refresh_timer = QtCore.QTimer()
        self.refresh_timer.timeout.connect(self.refresh_status)
        self.refresh_timer.start(500)
        QtCore.QTimer.singleShot(0, self.auto_start)
        self.refresh_status()

    def settings(self) -> HealthIntegrationSettings:
        host = self.mcp_host.text().strip()
        port = int(self.mcp_port.value())
        rules_value = self.rules_path.text().strip()
        return HealthIntegrationSettings(
            wearer_id=self.wearer_id.text().strip(),
            db_path=Path(self.db_path.text().strip()),
            rules_path=Path(rules_value) if rules_value else None,
            mcp_host=host,
            mcp_port=port,
            mcp_path=self.mcp_path.text().strip(),
        )

    def auto_start(self) -> None:
        configured = self.settings()
        try:
            self.controller.auto_start(configured)
        except Exception as exc:
            self.error_status.setText(f"自动启动未完成：{exc}")
        self.refresh_status()

    def start_health(self) -> None:
        try:
            self.controller.start_runtime(self.settings())
            self.error_status.setText("")
        except Exception as exc:
            self._show_error("Health 数据启动失败", exc)
        self.refresh_status()

    def start_mcp(self) -> None:
        try:
            self.controller.start_mcp(self.settings())
            self.error_status.setText("")
        except Exception as exc:
            self._show_error("Health MCP 启动失败", exc)
        self.refresh_status()

    def stop_mcp(self) -> None:
        self.controller.stop_mcp()
        self.refresh_status()

    def stop_all(self) -> None:
        self.controller.close()
        self.refresh_status()

    def refresh_status(self) -> None:
        configured = self.settings()
        status = self.controller.status()
        self.health_status.setText(
            "Health 数据：运行中" if status.health_running else "Health 数据：未启动"
        )
        if status.mcp_running:
            self.mcp_status.setText(f"MCP：运行中 / PID {status.mcp_pid}")
            self.endpoint_status.setText(f"RDK endpoint：{configured.endpoint}")
        else:
            exit_suffix = (
                f" / exit {status.mcp_exit_code}"
                if status.mcp_exit_code is not None
                else ""
            )
            self.mcp_status.setText(f"MCP：未启动{exit_suffix}")
            self.endpoint_status.setText("RDK endpoint：--")
        self.log_status.setText(
            f"MCP 日志：{status.mcp_log_path}"
            if status.mcp_log_path is not None
            else "MCP 日志：--"
        )
        if status.last_error:
            self.error_status.setText(status.last_error)

    def close(self) -> None:
        self.refresh_timer.stop()
        self.controller.close()

    def _show_error(self, title: str, exc: BaseException) -> None:
        detail = str(exc)
        self.error_status.setText(detail)
        self.QtWidgets.QMessageBox.warning(self.widget, title, detail)
