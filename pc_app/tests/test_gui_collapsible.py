from __future__ import annotations


def test_collapsible_section_toggles_body_and_arrow(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PySide6 import QtCore, QtWidgets

    from smart_neckband.gui import _build_collapsible_section

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    body = QtWidgets.QFrame()
    section, toggle = _build_collapsible_section(
        QtCore=QtCore,
        QtWidgets=QtWidgets,
        title="BLE DEBUG 日志",
        body=body,
        expanded=False,
    )

    assert toggle.isChecked() is False
    assert toggle.arrowType() == QtCore.Qt.RightArrow
    assert body.isHidden() is True

    toggle.click()
    app.processEvents()

    assert toggle.isChecked() is True
    assert toggle.arrowType() == QtCore.Qt.DownArrow
    assert body.isHidden() is False
    section.close()


def test_live_page_defaults_to_ecg_focused_layout(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PySide6 import QtWidgets

    from smart_neckband.gui import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    try:
        window.window.resize(1280, 720)
        window.show()
        app.processEvents()

        assert window.connection_toggle_button.isChecked() is True
        assert window.connection_group.isHidden() is False
        assert window.debug_toggle_button.isChecked() is False
        assert window.debug_group.isHidden() is True
        assert window.record_toggle_button.isChecked() is False
        assert window.record_group.isHidden() is True
        assert window.live_splitter.count() == 2
        collapsed_plot_height = window.live_splitter.height()

        window.debug_toggle_button.click()
        app.processEvents()
        assert window.debug_group.isHidden() is False
        assert window.live_splitter.height() < collapsed_plot_height

        window.debug_toggle_button.click()
        app.processEvents()
        assert window.debug_group.isHidden() is True
        assert window.live_splitter.height() >= collapsed_plot_height
    finally:
        window.close()
