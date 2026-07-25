from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from .health_store import HealthStore
from .sleep_ecg import (
    SleepEcgCancelled,
    SleepEcgDemographics,
    SleepEcgSource,
    StoredSleepEcgAnalysis,
    execute_sleep_analysis,
    probe_sleep_ecg_source,
)


class SleepEcgPanel:
    def __init__(
        self,
        *,
        QtCore,
        QtWidgets,
        pg,
        settings_provider: Callable[[], object],
        post_gui: Callable[[object], None],
        execute: Callable[..., StoredSleepEcgAnalysis] = execute_sleep_analysis,
        store_factory: Callable[[Path], HealthStore] = HealthStore,
    ) -> None:
        self.QtCore = QtCore
        self.QtWidgets = QtWidgets
        self.pg = pg
        self.settings_provider = settings_provider
        self.post_gui = post_gui
        self.execute = execute
        self.store_factory = store_factory
        self.source: SleepEcgSource | None = None
        self.last_result: StoredSleepEcgAnalysis | None = None
        self.cancel_event = Event()
        self.worker: Thread | None = None

        self.widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(self.widget)

        source_group = QtWidgets.QGroupBox("Sleep ECG 数据源")
        source_layout = QtWidgets.QGridLayout(source_group)
        self.session_button = QtWidgets.QPushButton("选择本项目会话目录")
        self.file_button = QtWidgets.QPushButton("选择 EDF / WFDB / raw.bin")
        self.source_path = QtWidgets.QLineEdit()
        self.source_path.setReadOnly(True)
        self.source_info = QtWidgets.QLabel("尚未选择睡眠 ECG 数据")
        self.source_info.setWordWrap(True)
        self.lead_combo = QtWidgets.QComboBox()
        self.display_name = QtWidgets.QLineEdit()
        self.recording_start = QtWidgets.QLineEdit()
        self.recording_start.setPlaceholderText("ISO 时间或 HH:MM:SS")
        self.age = QtWidgets.QSpinBox()
        self.age.setRange(0, 120)
        self.age.setSpecialValueText("未知")
        self.gender = QtWidgets.QComboBox()
        self.gender.addItem("未知", None)
        self.gender.addItem("女性", "female")
        self.gender.addItem("男性", "male")
        source_layout.addWidget(self.session_button, 0, 0)
        source_layout.addWidget(self.file_button, 0, 1)
        source_layout.addWidget(self.source_path, 0, 2, 1, 4)
        source_layout.addWidget(QtWidgets.QLabel("ECG 导联"), 1, 0)
        source_layout.addWidget(self.lead_combo, 1, 1)
        source_layout.addWidget(QtWidgets.QLabel("报告名称"), 1, 2)
        source_layout.addWidget(self.display_name, 1, 3)
        source_layout.addWidget(QtWidgets.QLabel("记录开始时间"), 1, 4)
        source_layout.addWidget(self.recording_start, 1, 5)
        source_layout.addWidget(QtWidgets.QLabel("年龄"), 2, 0)
        source_layout.addWidget(self.age, 2, 1)
        source_layout.addWidget(QtWidgets.QLabel("性别"), 2, 2)
        source_layout.addWidget(self.gender, 2, 3)
        source_layout.addWidget(self.source_info, 2, 4, 1, 2)
        layout.addWidget(source_group)

        controls = QtWidgets.QHBoxLayout()
        self.analyze_button = QtWidgets.QPushButton("开始 SleepECG 离线分析")
        self.cancel_button = QtWidgets.QPushButton("取消")
        self.cancel_button.setEnabled(False)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QtWidgets.QLabel("等待导入")
        controls.addWidget(self.analyze_button)
        controls.addWidget(self.cancel_button)
        controls.addWidget(self.progress, 2)
        controls.addWidget(self.status, 2)
        layout.addLayout(controls)

        self.warning_label = QtWidgets.QLabel(
            "研究用途：单导联 ECG 睡眠分期不是医疗诊断。"
        )
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        self.summary = QtWidgets.QLabel("尚无分析结果")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        plots = QtWidgets.QSplitter(self.QtCore.Qt.Vertical)
        self.hypnogram_plot = pg.PlotWidget(title="SleepECG Hypnogram")
        self.hypnogram_plot.setLabel("left", "Stage")
        self.hypnogram_plot.setLabel("bottom", "Hours")
        self.hypnogram_curve = self.hypnogram_plot.plot(
            pen=pg.mkPen("#6a1b9a", width=2)
        )
        self.reference_curve = self.hypnogram_plot.plot(
            pen=pg.mkPen("#2e7d32", width=2, style=self.QtCore.Qt.DashLine),
            name="Reference",
        )
        self.probability_plot = pg.PlotWidget(title="Sleep stage probabilities")
        self.probability_plot.setLabel("left", "Probability")
        self.probability_plot.setLabel("bottom", "Hours")
        self.probability_plot.setYRange(0, 1)
        self.wake_curve = self.probability_plot.plot(
            pen=pg.mkPen("#ef6c00", width=1), name="WAKE"
        )
        self.rem_curve = self.probability_plot.plot(
            pen=pg.mkPen("#c2185b", width=1), name="REM"
        )
        self.nrem_curve = self.probability_plot.plot(
            pen=pg.mkPen("#1565c0", width=1), name="NREM"
        )
        plots.addWidget(self.hypnogram_plot)
        plots.addWidget(self.probability_plot)
        layout.addWidget(plots, 2)

        self.epoch_table = QtWidgets.QTableWidget(0, 8)
        self.epoch_table.setHorizontalHeaderLabels(
            ["Epoch", "开始", "分期", "参考分期", "置信度", "WAKE", "REM", "NREM"]
        )
        self.epoch_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.epoch_table, 2)

        self.session_button.clicked.connect(self.choose_session_directory)
        self.file_button.clicked.connect(self.choose_file)
        self.analyze_button.clicked.connect(self.start_analysis)
        self.cancel_button.clicked.connect(self.cancel_analysis)

    @property
    def running(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def choose_session_directory(self) -> None:
        path = self.QtWidgets.QFileDialog.getExistingDirectory(
            self.widget,
            "选择包含 session.json 和 raw.bin 的目录",
            str(Path("data") / "sessions"),
        )
        if path:
            self.load_path(path)

    def choose_file(self) -> None:
        path, _selected_filter = self.QtWidgets.QFileDialog.getOpenFileName(
            self.widget,
            "选择 Sleep ECG 文件",
            str(Path("data")),
            "Sleep ECG (*.bin *.edf *.bdf *.hea *.dat);;All files (*)",
        )
        if path:
            self.load_path(path)

    def load_path(self, path: str | Path) -> SleepEcgSource:
        source = probe_sleep_ecg_source(path)
        self.source = source
        self.source_path.setText(str(source.path))
        self.lead_combo.clear()
        for lead in source.lead_names:
            self.lead_combo.addItem(lead, lead)
        self.lead_combo.setCurrentText(source.default_lead)
        self.display_name.setText(source.source_session_id or source.path.name)
        self.recording_start.setText(source.recording_start_time or "")
        self.source_info.setText(
            f"{source.source_type} | {source.sample_rate_hz:g} Hz | "
            f"{source.duration_seconds / 60.0:.1f} min | "
            f"参考分期 {len(source.reference_stages)} epochs"
        )
        self.status.setText("数据源已就绪")
        return source

    def start_analysis(self) -> None:
        if self.running:
            return
        if self.source is None:
            self._show_error("请先选择 Sleep ECG 数据")
            return
        try:
            settings = self.settings_provider()
            settings.validate_health()
        except Exception as exc:
            self._show_error(f"Health 数据库配置无效：{exc}")
            return
        start_text = self.recording_start.text().strip() or None
        source = replace(self.source, recording_start_time=start_text)
        age = int(self.age.value()) or None
        demographics = SleepEcgDemographics(
            age=age,
            gender=self.gender.currentData(),
        )
        lead = str(self.lead_combo.currentData() or source.default_lead)
        display_name = self.display_name.text().strip() or source.path.name
        store = self.store_factory(Path(settings.db_path))
        self.cancel_event = Event()
        self.analyze_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText("正在准备 SleepECG 分析……")

        def progress(value: int, message: str) -> None:
            self.post_gui(lambda: self._set_progress(value, message))

        def worker() -> None:
            try:
                stored = self.execute(
                    store=store,
                    wearer_id=settings.wearer_id,
                    source=source,
                    lead_name=lead,
                    demographics=demographics,
                    display_name=display_name,
                    cancel_event=self.cancel_event,
                    progress=progress,
                )
            except BaseException as exc:
                self.post_gui(lambda exc=exc: self._finish_error(exc))
            else:
                self.post_gui(lambda: self._finish_success(stored))

        self.worker = Thread(target=worker, name="SleepEcgAnalysis", daemon=True)
        self.worker.start()

    def cancel_analysis(self) -> None:
        if self.running:
            self.cancel_event.set()
            self.status.setText("正在取消……")

    def _set_progress(self, value: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, int(value))))
        self.status.setText(message)

    def _finish_error(self, exc: BaseException) -> None:
        self.analyze_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if isinstance(exc, SleepEcgCancelled):
            self.status.setText("分析已取消")
        else:
            self._show_error(str(exc))

    def _finish_success(self, stored: StoredSleepEcgAnalysis) -> None:
        self.last_result = stored
        self.analyze_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setValue(100)
        self.status.setText(
            f"分析完成并写入健康数据库：{stored.sleep_record_id}"
        )
        result = stored.result
        summary = result.summary
        warning_text = "；".join(result.warnings) if result.warnings else "无"
        reference_text = _reference_text(result.epochs)
        self.summary.setText(
            " | ".join(
                [
                    f"总睡眠 {summary.total_sleep_time_s / 3600.0:.2f} h",
                    f"睡眠效率 {summary.sleep_efficiency_percent or 0:.1f}%",
                    f"REM {summary.stage_percent['REM']:.1f}%",
                    f"NREM {summary.stage_percent['NREM']:.1f}%",
                    f"清醒 {summary.stage_percent['WAKE']:.1f}%",
                    f"平均置信度 {summary.mean_confidence or 0:.3f}",
                    reference_text,
                    f"限制：{warning_text}",
                ]
            )
        )
        self._plot_result(stored)
        self._fill_epoch_table(stored)

    def _plot_result(self, stored: StoredSleepEcgAnalysis) -> None:
        epochs = stored.result.epochs
        x = [epoch.start_offset_s / 3600.0 for epoch in epochs]
        levels = {"UNDEFINED": 0, "NREM": 1, "REM": 2, "WAKE": 3}
        self.hypnogram_curve.setData(x, [levels[epoch.stage] for epoch in epochs])
        self.reference_curve.setData(
            x,
            [
                levels[epoch.reference_stage]
                if epoch.reference_stage in levels
                and epoch.reference_stage != "UNDEFINED"
                else float("nan")
                for epoch in epochs
            ],
        )
        self.wake_curve.setData(x, [epoch.probabilities.get("WAKE", 0.0) for epoch in epochs])
        self.rem_curve.setData(x, [epoch.probabilities.get("REM", 0.0) for epoch in epochs])
        self.nrem_curve.setData(x, [epoch.probabilities.get("NREM", 0.0) for epoch in epochs])

    def _fill_epoch_table(self, stored: StoredSleepEcgAnalysis) -> None:
        epochs = stored.result.epochs
        self.epoch_table.setRowCount(len(epochs))
        for row, epoch in enumerate(epochs):
            values = (
                epoch.epoch_index,
                f"{epoch.start_offset_s / 60.0:.1f} min",
                epoch.stage,
                epoch.reference_stage or "",
                f"{epoch.confidence:.3f}",
                f"{epoch.probabilities.get('WAKE', 0.0):.3f}",
                f"{epoch.probabilities.get('REM', 0.0):.3f}",
                f"{epoch.probabilities.get('NREM', 0.0):.3f}",
            )
            for column, value in enumerate(values):
                self.epoch_table.setItem(
                    row, column, self.QtWidgets.QTableWidgetItem(str(value))
                )

    def _show_error(self, message: str) -> None:
        self.status.setText(f"错误：{message}")

    def close(self) -> None:
        self.cancel_event.set()
        if self.worker is not None:
            self.worker.join(timeout=2.0)


def _reference_text(epochs) -> str:
    pairs = [
        (epoch.stage, epoch.reference_stage)
        for epoch in epochs
        if epoch.reference_stage not in {None, "UNDEFINED"}
        and epoch.stage != "UNDEFINED"
    ]
    if not pairs:
        return "无参考分期"
    accuracy = sum(predicted == reference for predicted, reference in pairs) / len(pairs)
    labels = ("WAKE", "REM", "NREM")
    predicted_counts = {label: 0 for label in labels}
    reference_counts = {label: 0 for label in labels}
    confusion = {
        reference: {predicted: 0 for predicted in labels} for reference in labels
    }
    for predicted, reference in pairs:
        predicted_counts[predicted] += 1
        reference_counts[reference] += 1
        confusion[reference][predicted] += 1
    expected = sum(
        predicted_counts[label] * reference_counts[label] for label in labels
    ) / (len(pairs) * len(pairs))
    kappa = (accuracy - expected) / (1.0 - expected) if expected < 1.0 else 0.0
    matrix = "; ".join(
        f"{reference}="
        + "/".join(str(confusion[reference][predicted]) for predicted in labels)
        for reference in labels
    )
    return (
        f"参考 accuracy {accuracy:.3f} / Cohen's kappa {kappa:.3f} / "
        f"混淆矩阵(真实行 WAKE/REM/NREM, 预测列 WAKE/REM/NREM): {matrix}"
    )
