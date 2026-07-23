from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Callable

from .webhook_client import WebhookDispatcher
from .webhook_models import (
    InstructionState,
    ReplyEvent,
    SubmitOutcome,
    WebhookSettings,
    ordinary_send_allowed,
)
from .webhook_receiver import ReplyWebhookServer
from .webhook_store import WebhookStore
from .protocol import VoiceStatusPayload
from .voice import VoiceTranscript


STATE_TEXT = {
    InstructionState.PENDING: "等待提交",
    InstructionState.SUBMITTING: "正在提交",
    InstructionState.RETRY_WAIT: "等待重试",
    InstructionState.ACCEPTED: "已受理",
    InstructionState.FAILED: "提交失败",
}


VOICE_STATE_TEXT = {
    0: "禁用",
    1: "等待唤醒",
    2: "连接 ASR",
    3: "正在识别",
    4: "等待最终文本",
    5: "错误冷却",
}


class WebhookTab:
    def __init__(
        self,
        *,
        QtCore: object,
        QtWidgets: object,
        post_gui: Callable[[object], None],
        store_path: str | Path = Path("data") / "webhook_client.sqlite3",
    ) -> None:
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.post_gui = post_gui
        self.store = WebhookStore(store_path)
        self.settings = self.store.load_settings()
        self.receiver: ReplyWebhookServer | None = None
        self._receiving = False
        self._receiver_stopping = False
        self.widget = self._build_widget()
        self.dispatcher = WebhookDispatcher(
            store=self.store,
            settings_provider=lambda: self.settings,
            outcome_callback=self._queue_outcome,
        )
        self.dispatcher.start()
        self._refresh_records()
        self.set_receiving(False)
        if self.settings.auto_start_receiver:
            self._start_receiver()

    def _build_widget(self) -> object:
        QtWidgets = self.QtWidgets
        widget = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(widget)

        voice_group = QtWidgets.QGroupBox("设备语音链路")
        voice_layout = QtWidgets.QGridLayout(voice_group)
        self.voice_state_label = QtWidgets.QLabel("语音状态：--")
        self.voice_transcript_label = QtWidgets.QLabel("最终文本：--")
        self.voice_transcript_label.setWordWrap(True)
        self.voice_error_label = QtWidgets.QLabel("最近错误：0")
        self.voice_pending_label = QtWidgets.QLabel("待重组：0")
        voice_layout.addWidget(self.voice_state_label, 0, 0)
        voice_layout.addWidget(self.voice_error_label, 0, 1)
        voice_layout.addWidget(self.voice_pending_label, 0, 2)
        voice_layout.addWidget(self.voice_transcript_label, 1, 0, 1, 3)
        root.addWidget(voice_group)

        settings_group = QtWidgets.QGroupBox("Webhook 设置")
        settings_layout = QtWidgets.QGridLayout(settings_group)
        self.gateway_url_edit = QtWidgets.QLineEdit(self.settings.gateway_url)
        self.callback_bind_host_edit = QtWidgets.QLineEdit(self.settings.callback_bind_host)
        self.callback_port_spin = QtWidgets.QSpinBox()
        self.callback_port_spin.setRange(1, 65_535)
        self.callback_port_spin.setValue(self.settings.callback_port)
        self.callback_path_edit = QtWidgets.QLineEdit(self.settings.callback_path)
        self.callback_public_url_edit = QtWidgets.QLineEdit(self.settings.callback_public_url)
        self.request_timeout_spin = QtWidgets.QDoubleSpinBox()
        self.request_timeout_spin.setRange(0.1, 300.0)
        self.request_timeout_spin.setDecimals(1)
        self.request_timeout_spin.setValue(self.settings.request_timeout_s)
        self.request_timeout_spin.setSuffix(" 秒")
        self.require_receiving_checkbox = QtWidgets.QCheckBox("普通指令要求颈环正在接收数据")
        self.require_receiving_checkbox.setChecked(self.settings.require_device_receiving)
        self.auto_start_receiver_checkbox = QtWidgets.QCheckBox("启动上位机时自动监听回调")
        self.auto_start_receiver_checkbox.setChecked(self.settings.auto_start_receiver)
        self.save_settings_button = QtWidgets.QPushButton("保存设置")
        self.start_receiver_button = QtWidgets.QPushButton("启动回调监听")
        self.stop_receiver_button = QtWidgets.QPushButton("停止回调监听")
        self.stop_receiver_button.setEnabled(False)
        self.copy_reply_env_button = QtWidgets.QPushButton("复制 Gateway 回调环境变量")
        self.receiver_status_label = QtWidgets.QLabel("回调监听：未启动")
        self.receiver_status_label.setWordWrap(True)

        settings_layout.addWidget(QtWidgets.QLabel("Agent Gateway"), 0, 0)
        settings_layout.addWidget(self.gateway_url_edit, 0, 1, 1, 5)
        settings_layout.addWidget(QtWidgets.QLabel("监听主机"), 1, 0)
        settings_layout.addWidget(self.callback_bind_host_edit, 1, 1)
        settings_layout.addWidget(QtWidgets.QLabel("端口"), 1, 2)
        settings_layout.addWidget(self.callback_port_spin, 1, 3)
        settings_layout.addWidget(QtWidgets.QLabel("路径"), 1, 4)
        settings_layout.addWidget(self.callback_path_edit, 1, 5)
        settings_layout.addWidget(QtWidgets.QLabel("Gateway 可访问的回调 URL"), 2, 0)
        settings_layout.addWidget(self.callback_public_url_edit, 2, 1, 1, 5)
        settings_layout.addWidget(QtWidgets.QLabel("请求超时"), 3, 0)
        settings_layout.addWidget(self.request_timeout_spin, 3, 1)
        settings_layout.addWidget(self.require_receiving_checkbox, 3, 2, 1, 2)
        settings_layout.addWidget(self.auto_start_receiver_checkbox, 3, 4, 1, 2)
        settings_layout.addWidget(self.save_settings_button, 4, 0)
        settings_layout.addWidget(self.start_receiver_button, 4, 1)
        settings_layout.addWidget(self.stop_receiver_button, 4, 2)
        settings_layout.addWidget(self.copy_reply_env_button, 4, 3, 1, 2)
        settings_layout.addWidget(self.receiver_status_label, 5, 0, 1, 6)
        root.addWidget(settings_group)

        input_group = QtWidgets.QGroupBox("指令调试")
        input_layout = QtWidgets.QVBoxLayout(input_group)
        self.device_gate_label = QtWidgets.QLabel("颈环数据状态：未接收")
        self.instruction_edit = QtWidgets.QPlainTextEdit()
        self.instruction_edit.setPlaceholderText("输入完整真实用户请求，例如：请让机器狗向前走 1 米")
        self.instruction_edit.setMaximumHeight(100)
        input_buttons = QtWidgets.QHBoxLayout()
        self.send_instruction_button = QtWidgets.QPushButton("发送指令")
        self.send_stop_button = QtWidgets.QPushButton("发送“停”（非物理急停）")
        self.retry_instruction_button = QtWidgets.QPushButton("重试选中请求")
        self.last_instruction_label = QtWidgets.QLabel("instruction_id：--")
        input_buttons.addWidget(self.send_instruction_button)
        input_buttons.addWidget(self.send_stop_button)
        input_buttons.addWidget(self.retry_instruction_button)
        input_buttons.addWidget(self.last_instruction_label, 1)
        input_layout.addWidget(self.device_gate_label)
        input_layout.addWidget(self.instruction_edit)
        input_layout.addLayout(input_buttons)
        root.addWidget(input_group)

        tables = QtWidgets.QSplitter()
        tables.setOrientation(self.QtCore.Qt.Vertical)

        instruction_panel = QtWidgets.QWidget()
        instruction_panel_layout = QtWidgets.QVBoxLayout(instruction_panel)
        instruction_panel_layout.setContentsMargins(0, 0, 0, 0)
        instruction_panel_layout.addWidget(QtWidgets.QLabel("提交记录（先持久化，再发送）"))
        self.instruction_table = QtWidgets.QTableWidget(0, 7)
        self.instruction_table.setHorizontalHeaderLabels(
            ("instruction_id", "原始文本", "状态", "HTTP", "尝试", "最后错误", "创建时间")
        )
        self.instruction_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.instruction_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.instruction_table.horizontalHeader().setStretchLastSection(True)
        self.instruction_table.setColumnWidth(0, 220)
        self.instruction_table.setColumnWidth(1, 280)
        instruction_panel_layout.addWidget(self.instruction_table)
        tables.addWidget(instruction_panel)

        reply_panel = QtWidgets.QWidget()
        reply_panel_layout = QtWidgets.QVBoxLayout(reply_panel)
        reply_panel_layout.setContentsMargins(0, 0, 0, 0)
        reply_panel_layout.addWidget(QtWidgets.QLabel("最终回复（按 reply_id 去重）"))
        self.reply_table = QtWidgets.QTableWidget(0, 4)
        self.reply_table.setHorizontalHeaderLabels(("reply_id", "instruction_id", "最终回复", "完成时间"))
        self.reply_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.reply_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.reply_table.horizontalHeader().setStretchLastSection(True)
        self.reply_table.setColumnWidth(0, 220)
        self.reply_table.setColumnWidth(1, 220)
        self.reply_table.setColumnWidth(2, 420)
        reply_panel_layout.addWidget(self.reply_table)
        tables.addWidget(reply_panel)
        root.addWidget(tables, 2)

        debug_group = QtWidgets.QGroupBox("Webhook DEBUG 日志")
        debug_layout = QtWidgets.QVBoxLayout(debug_group)
        self.debug_output = QtWidgets.QPlainTextEdit()
        self.debug_output.setReadOnly(True)
        self.debug_output.setMaximumBlockCount(1000)
        self.debug_output.setMinimumHeight(100)
        self.clear_debug_button = QtWidgets.QPushButton("清空 Webhook 日志")
        debug_layout.addWidget(self.debug_output)
        debug_layout.addWidget(self.clear_debug_button)
        root.addWidget(debug_group, 1)

        self.save_settings_button.clicked.connect(self._save_settings)
        self.start_receiver_button.clicked.connect(self._start_receiver)
        self.stop_receiver_button.clicked.connect(self._stop_receiver_async)
        self.copy_reply_env_button.clicked.connect(self._copy_reply_env)
        self.send_instruction_button.clicked.connect(self._send_instruction)
        self.send_stop_button.clicked.connect(self._send_stop)
        self.retry_instruction_button.clicked.connect(self._retry_selected)
        self.require_receiving_checkbox.toggled.connect(lambda _checked: self.set_receiving(self._receiving))
        self.clear_debug_button.clicked.connect(self.debug_output.clear)
        return widget

    def set_receiving(self, receiving: bool) -> None:
        self._receiving = bool(receiving)
        self.device_gate_label.setText(
            "颈环数据状态：正在接收，可发送普通指令"
            if self._receiving
            else "颈环数据状态：未达到 RECEIVING"
        )
        allowed = ordinary_send_allowed(
            require_device_receiving=self.require_receiving_checkbox.isChecked(),
            receiving=self._receiving,
        )
        self.send_instruction_button.setEnabled(allowed)
        if allowed:
            self.send_instruction_button.setToolTip("")
        else:
            self.send_instruction_button.setToolTip("连接颈环并收到有效数据后才能发送普通指令")

    def close(self) -> None:
        if self.receiver is not None:
            self.receiver.stop()
            self.receiver = None
        self.dispatcher.stop()

    def _settings_from_fields(self) -> WebhookSettings:
        return WebhookSettings(
            gateway_url=self.gateway_url_edit.text(),
            callback_bind_host=self.callback_bind_host_edit.text(),
            callback_port=self.callback_port_spin.value(),
            callback_path=self.callback_path_edit.text(),
            callback_public_url=self.callback_public_url_edit.text(),
            request_timeout_s=self.request_timeout_spin.value(),
            require_device_receiving=self.require_receiving_checkbox.isChecked(),
            auto_start_receiver=self.auto_start_receiver_checkbox.isChecked(),
        ).validated()

    def _save_settings(self) -> WebhookSettings | None:
        try:
            settings = self._settings_from_fields()
            self.store.save_settings(settings)
        except Exception as exc:
            self._append_debug(f"设置保存失败：{type(exc).__name__}: {exc}")
            self.receiver_status_label.setText(f"设置错误：{exc}")
            return None
        self.settings = settings
        self._append_debug("Webhook 设置已保存")
        self.set_receiving(self._receiving)
        if self.receiver is not None:
            self.receiver_status_label.setText("设置已保存；监听地址变化需停止并重新启动回调监听")
        return settings

    def _start_receiver(self) -> None:
        if self.receiver is not None and self.receiver.running:
            self._append_debug("回调监听已经在运行")
            return
        if self._receiver_stopping:
            self._append_debug("回调监听正在停止，请稍后重试")
            return
        settings = self._save_settings()
        if settings is None:
            return
        receiver = ReplyWebhookServer(
            store=self.store,
            host=settings.callback_bind_host,
            port=settings.callback_port,
            path=settings.callback_path,
            reply_callback=self._queue_reply,
            debug_callback=self._queue_debug,
        )
        try:
            port = receiver.start()
        except Exception as exc:
            self.receiver_status_label.setText(f"回调监听启动失败：{exc}")
            self._append_debug(f"回调监听启动失败：{type(exc).__name__}: {exc}")
            return
        self.receiver = receiver
        self.receiver_status_label.setText(
            f"回调监听运行中：{settings.callback_bind_host}:{port}{settings.callback_path}；"
            f"Gateway 应配置 {settings.callback_public_url}"
        )
        self.start_receiver_button.setEnabled(False)
        self.stop_receiver_button.setEnabled(True)

    def _stop_receiver_async(self) -> None:
        receiver = self.receiver
        if receiver is None or self._receiver_stopping:
            return
        self.receiver = None
        self._receiver_stopping = True
        self.start_receiver_button.setEnabled(False)
        self.stop_receiver_button.setEnabled(False)
        self.receiver_status_label.setText("正在停止回调监听……")

        def worker() -> None:
            receiver.stop()
            self.post_gui(self._finish_receiver_stop)

        Thread(target=worker, name="ReplyWebhookServerStop", daemon=True).start()

    def _finish_receiver_stop(self) -> None:
        self._receiver_stopping = False
        self.start_receiver_button.setEnabled(True)
        self.stop_receiver_button.setEnabled(False)
        self.receiver_status_label.setText("回调监听：未启动")

    def _copy_reply_env(self) -> None:
        settings = self._save_settings()
        if settings is None:
            return
        command = f'$env:AGENT_WEBHOOK_REPLY_URL = "{settings.callback_public_url}"'
        self.QtWidgets.QApplication.clipboard().setText(command)
        self._append_debug("已复制 AGENT_WEBHOOK_REPLY_URL PowerShell 配置")

    def _send_instruction(self) -> None:
        settings = self._save_settings()
        if settings is None:
            return
        if not ordinary_send_allowed(
            require_device_receiving=settings.require_device_receiving,
            receiving=self._receiving,
        ):
            self._append_debug("普通指令未发送：颈环尚未达到 RECEIVING")
            return
        self._enqueue_text(self.instruction_edit.toPlainText())

    def _send_stop(self) -> None:
        if self._save_settings() is None:
            return
        self._enqueue_text("停")
        self._append_debug("已提交停止快速路径文本；这不是物理急停，也不证明机器狗已静止")

    def _enqueue_text(self, text: str) -> None:
        try:
            record = self.dispatcher.enqueue_text(text)
        except Exception as exc:
            self._append_debug(f"指令持久化失败：{type(exc).__name__}: {exc}")
            return
        self.last_instruction_label.setText(f"instruction_id：{record.instruction_id}")
        self._append_debug(
            f"指令已先写入本地 SQLite，等待提交：instruction_id={record.instruction_id}"
        )
        self._refresh_records()

    def enqueue_voice_text(self, transcript: VoiceTranscript) -> bool:
        """Persist a final device transcript before the BLE reader emits ACK."""

        try:
            record = self.dispatcher.enqueue_text(
                transcript.text,
                instruction_id=transcript.instruction_id,
            )
        except Exception as exc:
            self._queue_debug(
                "语音文本持久化失败，未发送 ACK："
                f"{type(exc).__name__}: {exc}"
            )
            return False

        def update_ui() -> None:
            self.last_instruction_label.setText(
                f"instruction_id：{record.instruction_id}"
            )
            self.voice_transcript_label.setText(f"最终文本：{record.text}")
            self._append_debug(
                "语音文本已写入 SQLite，允许 BLE ACK："
                f"instruction_id={record.instruction_id}"
            )
            self._refresh_records()

        self.post_gui(update_ui)
        return True

    def update_voice_status(self, status: VoiceStatusPayload, *, pending_count: int) -> None:
        def update_ui() -> None:
            state_text = VOICE_STATE_TEXT.get(status.state, f"未知({status.state})")
            self.voice_state_label.setText(
                f"语音状态：{state_text}，唤醒 {status.wake_count}，"
                f"成功 {status.asr_success_count}，失败 {status.asr_error_count}"
            )
            self.voice_error_label.setText(f"最近错误：{status.last_error}")
            self.voice_pending_label.setText(
                f"待重组：{pending_count}，设备丢弃 {status.text_drop_count}"
            )

        self.post_gui(update_ui)

    def _retry_selected(self) -> None:
        row = self.instruction_table.currentRow()
        if row < 0:
            self._append_debug("请先选择一条提交记录")
            return
        item = self.instruction_table.item(row, 0)
        if item is None:
            return
        instruction_id = item.text()
        try:
            record = self.dispatcher.retry_now(instruction_id)
        except Exception as exc:
            self._append_debug(f"重新提交失败：{type(exc).__name__}: {exc}")
            return
        if record.state is InstructionState.ACCEPTED:
            self._append_debug("该请求已经被 Gateway 受理，不会重新运行 Agent")
        else:
            self._append_debug(f"已用相同 ID 和原文安排重试：{instruction_id}")
        self._refresh_records()

    def _queue_outcome(self, outcome: SubmitOutcome) -> None:
        self.post_gui(lambda outcome=outcome: self._handle_outcome(outcome))

    def _handle_outcome(self, outcome: SubmitOutcome) -> None:
        status = STATE_TEXT[outcome.state]
        details = f"HTTP {outcome.http_status}" if outcome.http_status is not None else "无 HTTP 响应"
        retry = (
            f"，{outcome.retry_delay_s:.1f} 秒后自动重试"
            if outcome.retry_delay_s is not None
            else ""
        )
        self._append_debug(
            f"{status}：instruction_id={outcome.instruction_id}，{details}，"
            f"尝试 {outcome.attempt_count} 次，{outcome.message}{retry}"
        )
        self._refresh_records()

    def _queue_reply(self, event: ReplyEvent) -> None:
        self.post_gui(lambda event=event: self._handle_reply(event))

    def _handle_reply(self, event: ReplyEvent) -> None:
        self._append_debug(
            f"最终回复已持久化：instruction_id={event.instruction_id} reply_id={event.reply_id}"
        )
        self._refresh_records()

    def _queue_debug(self, message: str) -> None:
        self.post_gui(lambda message=message: self._append_debug(message))

    def _append_debug(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.debug_output.appendPlainText(f"[{timestamp}] {message}")

    def _refresh_records(self) -> None:
        instructions = self.store.list_instructions()
        self.instruction_table.setRowCount(len(instructions))
        for row, record in enumerate(instructions):
            values = (
                record.instruction_id,
                record.text,
                STATE_TEXT[record.state],
                str(record.last_http_status) if record.last_http_status is not None else "--",
                str(record.attempt_count),
                record.last_error or "",
                record.created_at,
            )
            for column, value in enumerate(values):
                self.instruction_table.setItem(row, column, self.QtWidgets.QTableWidgetItem(value))

        replies = self.store.list_replies()
        self.reply_table.setRowCount(len(replies))
        for row, reply in enumerate(replies):
            values = (reply.reply_id, reply.instruction_id, reply.text, reply.completed_at)
            for column, value in enumerate(values):
                self.reply_table.setItem(row, column, self.QtWidgets.QTableWidgetItem(value))
