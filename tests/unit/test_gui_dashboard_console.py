"""Unit tests for LogConsole and ExecutionDashboard."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from action_macro.gui.dashboard import ExecutionDashboard
from action_macro.gui.log_console import LogConsole, LogEntry
from action_macro.runner.state_machine import EngineState


# -----------------------------------------------------------------------------
# LogConsole Tests
# -----------------------------------------------------------------------------

def test_log_entry_formatting():
    from datetime import datetime
    entry = LogEntry(
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        level="INFO",
        message="Test message <with markup>",
    )
    plain = entry.to_plain_text()
    assert "[INFO   ]" in plain
    assert "Test message <with markup>" in plain

    html_str = entry.to_formatted_html()
    assert "#4A90E2" in html_str
    assert "&lt;with markup&gt;" in html_str


def test_log_console_initial_state(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)

    assert console.text_edit.isReadOnly()
    assert console.filter_combo.currentText() == "All Levels"
    assert console.auto_scroll_cb.isChecked()
    assert console.text_edit.maximumBlockCount() == 5000
    assert len(console._entries) == 0


def test_log_console_append_and_filter(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)

    console.append_log("INFO", "Info log message")
    console.append_log("WARNING", "Warning log message")
    console.append_log("ERROR", "Error log message")
    console.append_log("ACTION", "Action log message")

    assert len(console._entries) == 4
    all_text = console.text_edit.toPlainText()
    assert "Info log message" in all_text
    assert "Warning log message" in all_text
    assert "Error log message" in all_text
    assert "Action log message" in all_text

    # Switch filter to ERROR
    console.filter_combo.setCurrentText("ERROR")
    err_text = console.text_edit.toPlainText()
    assert "Error log message" in err_text
    assert "Info log message" not in err_text

    # Switch filter back to All Levels
    console.filter_combo.setCurrentText("All Levels")
    restored_text = console.text_edit.toPlainText()
    assert "Info log message" in restored_text
    assert "Error log message" in restored_text


def test_log_console_clear_logs(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)

    console.append_log("INFO", "Temporary message")
    assert len(console._entries) == 1

    console.clear_logs()
    assert len(console._entries) == 0
    assert console.text_edit.toPlainText() == ""


def test_log_console_copy_to_clipboard(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)

    console.append_log("INFO", "Clipboard item 1")
    console.append_log("ERROR", "Clipboard item 2")

    console.copy_to_clipboard()
    clip_text = QApplication.clipboard().text()
    assert "Clipboard item 1" in clip_text
    assert "Clipboard item 2" in clip_text


def test_log_console_max_block_count(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)
    console.set_max_block_count(50)

    for i in range(75):
        console.append_log("INFO", f"Message {i}")

    assert len(console._entries) == 50
    assert "Message 74" in console.text_edit.toPlainText()
    assert "Message 0" not in console.text_edit.toPlainText()


# -----------------------------------------------------------------------------
# ExecutionDashboard Tests
# -----------------------------------------------------------------------------

def test_dashboard_initial_state(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)

    assert dash.btn_start.isEnabled()
    assert not dash.btn_pause.isEnabled()
    assert not dash.btn_stop.isEnabled()
    assert dash.spin_loops.isEnabled()
    assert not dash.cb_infinite.isChecked()
    assert dash.get_effective_loop_count() == 1
    assert "IDLE" in dash.banner_label.text()


def test_dashboard_state_transitions(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)

    # Transition to RUNNING
    dash.set_state("RUNNING")
    assert not dash.btn_start.isEnabled()
    assert dash.btn_pause.isEnabled()
    assert dash.btn_pause.text() == "⏸ Pause"
    assert dash.btn_stop.isEnabled()
    assert not dash.spin_loops.isEnabled()
    assert not dash.cb_infinite.isEnabled()

    # Transition to PAUSED
    dash.set_state("PAUSED")
    assert not dash.btn_start.isEnabled()
    assert dash.btn_pause.isEnabled()
    assert dash.btn_pause.text() == "▶ Resume"
    assert dash.btn_stop.isEnabled()

    # Transition to STOPPING
    dash.set_state("STOPPING")
    assert not dash.btn_start.isEnabled()
    assert not dash.btn_pause.isEnabled()
    assert not dash.btn_stop.isEnabled()

    # Transition to STOPPED
    dash.set_state("STOPPED")
    assert dash.btn_start.isEnabled()
    assert not dash.btn_pause.isEnabled()
    assert not dash.btn_stop.isEnabled()
    assert dash.spin_loops.isEnabled()

    # Transition to ERROR
    dash.set_state("ERROR")
    assert dash.btn_start.isEnabled()
    assert not dash.btn_pause.isEnabled()
    assert not dash.btn_stop.isEnabled()


def test_dashboard_progress_updates(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)

    # Finite loop progress
    dash.update_progress(cur_loop=2, tot_loops=5, cur_act=3, tot_acts=4)
    assert dash.loop_label.text() == "Iteration: 2 / 5"
    assert dash.loop_bar.value() == 2
    assert dash.loop_bar.maximum() == 5
    assert dash.action_label.text() == "Action: 3 / 4"
    assert dash.action_bar.value() == 3
    assert dash.action_bar.maximum() == 4

    # Infinite loop progress
    dash.update_progress(cur_loop=10, tot_loops=0, cur_act=1, tot_acts=4)
    assert "Iteration: 10 (Infinite Loop)" in dash.loop_label.text()
    assert dash.loop_bar.maximum() == 0  # Indeterminate pulse


def test_dashboard_action_started_and_finished(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)

    dash.on_action_started(1, "Click Target")
    assert dash.action_label.text() == "Action 2: Click Target"

    dash.set_state("RUNNING")
    dash.on_execution_finished(True, "Finished 1 loop")
    assert "IDLE" in dash.banner_label.text()

    dash.set_state("RUNNING")
    dash.on_execution_finished(False, "Cancelled")
    assert "STOPPED" in dash.banner_label.text()


def test_dashboard_signals_emitted_on_clicks(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)

    with qtbot.waitSignal(dash.start_requested) as blocker:
        dash.btn_start.click()
    assert blocker.args == [1]

    dash.set_state("RUNNING")
    with qtbot.waitSignal(dash.pause_requested):
        dash.btn_pause.click()

    dash.set_state("PAUSED")
    with qtbot.waitSignal(dash.resume_requested):
        dash.btn_pause.click()

    dash.set_state("RUNNING")
    with qtbot.waitSignal(dash.stop_requested):
        dash.btn_stop.click()


def test_dashboard_loop_configuration(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)

    with qtbot.waitSignal(dash.loop_count_changed) as blocker:
        dash.spin_loops.setValue(5)
    assert blocker.args == [5]
    assert dash.get_effective_loop_count() == 5

    with qtbot.waitSignal(dash.loop_count_changed) as blocker:
        dash.cb_infinite.setChecked(True)
    assert blocker.args == [0]
    assert dash.get_effective_loop_count() == 0
    assert not dash.spin_loops.isEnabled()

    dash.set_loop_count(3)
    assert not dash.cb_infinite.isChecked()
    assert dash.spin_loops.value() == 3
    assert dash.spin_loops.isEnabled()

    dash.set_loop_count(0)
    assert dash.cb_infinite.isChecked()
    assert not dash.spin_loops.isEnabled()
