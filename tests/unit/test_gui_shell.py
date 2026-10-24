"""Tests for the redesigned main-window shell: run bar, status bar, rail and recent files."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox, QPushButton, QToolBar

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.core.persistence import ScenarioPersistence
from action_macro.gui import theme
from action_macro.gui.components import EmptyState
from action_macro.gui.dashboard import ExecutionDashboard
from action_macro.gui.main_window import MainWindow
from action_macro.gui.sidebar import CURRENT_ROLE


@pytest.fixture
def window(qtbot, isolated_settings, monkeypatch):
    """A MainWindow with private QSettings; never blocks on an unsaved-changes prompt."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    windows = []

    def _make(**kwargs) -> MainWindow:
        win = MainWindow(settings=isolated_settings, **kwargs)
        qtbot.addWidget(win)
        windows.append(win)
        return win

    yield _make
    for win in windows:
        win.mark_dirty(False)


def _scenario_file(tmp_path: Path, name: str = "daily_quest") -> Path:
    scenario = ScenarioModel(
        name=name.replace("_", " ").title(),
        loop_count=2,
        actions=[ActionBlock(id="w1", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10))],
    )
    path = tmp_path / f"{name}.json"
    ScenarioPersistence.save(scenario, path)
    return path


# -----------------------------------------------------------------------------
# Layout
# -----------------------------------------------------------------------------

def test_shell_layout_places_contract_widgets(window):
    win = window()
    assert win.findChild(QToolBar) is None  # the old text toolbar is gone
    # Run bar lives in the editor header; the status read-outs live in the status bar.
    assert win.dashboard.parent() is win.editor_header
    assert win.status_bar.isAncestorOf(win.dashboard.status_widget)
    assert win.status_bar.isAncestorOf(win.dashboard.banner_label)
    assert win.status_bar.isAncestorOf(win.dashboard.loop_bar)
    assert win.editor_panel.isAncestorOf(win.scenario_builder)
    assert win.inspector_scroll.widget() is win.property_inspector
    assert win.console_panel.isAncestorOf(win.log_console)
    assert win.main_splitter.indexOf(win.sidebar) == 0
    assert win.editor_splitter.indexOf(win.inspector_panel) == 1
    assert win.work_splitter.indexOf(win.console_panel) == 1
    assert win.findChild(QPushButton, "RunButton") is win.dashboard.btn_start
    # Menus stay (macOS moves them to the screen's menu bar).
    titles = [a.text() for a in win.menuBar().actions()]
    for title in ("&File", "&Run", "&Tools", "&View", "&Help"):
        assert title in titles


def test_shell_renders_at_design_size(window, qtbot):
    win = window()
    win.resize(1400, 900)
    win.show()
    qtbot.waitExposed(win)
    image = win.grab().toImage()
    assert (image.width(), image.height()) == (1400, 900)
    assert win.sidebar.width() >= 200 and win.inspector_panel.width() >= 300


# -----------------------------------------------------------------------------
# Navigation rail toggles
# -----------------------------------------------------------------------------

def test_rail_toggles_sidebar_and_console(window):
    win = window()
    assert win.is_sidebar_visible() and win.is_console_visible()
    assert win.rail_btn_scenarios.isChecked() and win.rail_btn_console.isChecked()

    win.rail_btn_scenarios.click()
    assert not win.is_sidebar_visible()
    assert not win.act_toggle_sidebar.isChecked()
    assert not win.rail_btn_scenarios.isChecked()

    win.rail_btn_console.click()
    assert not win.is_console_visible()
    assert not win.btn_status_console.isChecked()  # the status-bar Console button mirrors it

    win.act_toggle_sidebar.trigger()
    win.btn_status_console.click()
    assert win.is_sidebar_visible() and win.is_console_visible()

    win.set_console_visible(False)
    assert not win.act_toggle_console.isChecked() and not win.rail_btn_console.isChecked()


def test_view_shortcuts_are_assigned(window):
    win = window()
    assert win.act_toggle_sidebar.shortcut().toString() == "Ctrl+B"
    assert win.act_toggle_console.shortcut().toString() == "Ctrl+J"


# -----------------------------------------------------------------------------
# Run bar and status bar
# -----------------------------------------------------------------------------

def test_status_bar_shows_messages_and_ready(window):
    win = window()
    assert win.status_bar.currentMessage() == "Ready"
    assert win.lbl_status_message.text() == "Ready"
    win.status_bar.showMessage("Saved: x.json", 3000)
    assert win.lbl_status_message.text() == "Saved: x.json"
    win.status_bar.clearMessage()
    assert win.lbl_status_message.text() == ""


@pytest.mark.parametrize(
    "state, text, pill_state, colour",
    [
        ("IDLE", "IDLE", "idle", theme.COLORS.text_muted),
        ("RUNNING", "RUNNING", "running", theme.COLORS.success),
        ("PAUSED", "PAUSED", "paused", theme.COLORS.warning),
        ("STOPPING", "STOPPING", "stopping", theme.COLORS.warning),
        ("STOPPED", "STOPPED", "stopped", theme.COLORS.text_muted),
        ("ERROR", "ERROR", "error", theme.COLORS.danger),
    ],
)
def test_status_pill_follows_engine_state(qtbot, state, text, pill_state, colour):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)
    with qtbot.waitSignal(dash.state_display_changed) as blocker:
        dash.set_state(state)
    assert blocker.args == [pill_state]
    assert dash.banner_label.text() == text
    assert dash.state_pill.property("state") == pill_state
    assert dash.state_dot.color() == QColor(colour)


def test_stop_button_turns_danger_only_while_a_run_can_be_stopped(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)
    assert dash.btn_stop.property("variant") == "secondary"
    dash.set_state("RUNNING")
    assert dash.btn_stop.property("variant") == "danger"
    dash.set_state("PAUSED")
    assert dash.btn_stop.property("variant") == "danger"
    dash.set_state("STOPPING")
    assert dash.btn_stop.property("variant") == "secondary"
    dash.set_state("IDLE")
    assert dash.btn_stop.property("variant") == "secondary"
    assert dash.btn_start.property("variant") == "primary"


def test_run_bar_controls_have_icons_and_tooltips(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)
    for button in (dash.btn_start, dash.btn_pause, dash.btn_stop, dash.cb_infinite):
        assert not button.icon().isNull()
        assert button.toolTip()
    assert dash.btn_start.text() == "Run" and dash.btn_start.hint() == "F5"
    assert "F5" in dash.btn_start.toolTip()


def test_idle_iteration_readout_follows_repeat_count(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)
    dash.spin_loops.setValue(3)
    assert dash.loop_label.text() == "Iteration: 0 / 3"
    assert dash.loop_bar.maximum() == 3 and dash.loop_bar.value() == 0
    dash.cb_infinite.setChecked(True)
    assert dash.loop_label.text() == "Iteration: 0 (Infinite Loop)"
    assert dash.loop_bar.maximum() == 1  # no busy animation while idle

    # During a run the engine's progress owns the read-out.
    dash.cb_infinite.setChecked(False)
    dash.set_state("RUNNING")
    dash.update_progress(2, 3, 1, 4)
    dash.spin_loops.setValue(7)  # (spin is disabled while running; programmatic change)
    assert dash.loop_label.text() == "Iteration: 2 / 3"


def test_status_bar_reflects_a_simulated_run(window):
    win = window()
    win.dashboard.set_state("RUNNING")
    win.dashboard.update_progress(1, 3, 2, 4)
    win.dashboard.on_action_started(1, "Press ctrl+c")
    assert win.dashboard.banner_label.text() == "RUNNING"
    assert win.dashboard.loop_label.text() == "Iteration: 1 / 3"
    assert win.dashboard.action_label.text() == "Action 2: Press ctrl+c"
    assert win.dashboard.action_bar.value() == 2
    win.dashboard.on_execution_finished(True, "Completed 3 loops")
    assert win.dashboard.banner_label.text() == "IDLE"


# -----------------------------------------------------------------------------
# Document chrome and recent scenarios
# -----------------------------------------------------------------------------

def test_top_bar_and_header_follow_document_state(window, tmp_path):
    win = window()
    assert win.top_bar.lbl_file.text() == "Not saved yet"
    assert win.top_bar.lbl_save_state.isHidden()
    assert win.lbl_step_count.text() == "0 steps"

    win.mark_dirty(True)
    assert win.top_bar.lbl_save_state.text() == "Unsaved changes"
    assert not win.editor_dirty_dot.isHidden()

    path = tmp_path / "saved.json"
    win.current_file_path = path
    assert win.save_scenario() is True
    assert win.top_bar.lbl_file.text() == "saved.json"
    assert win.top_bar.lbl_save_state.text() == "Saved"
    assert win.editor_dirty_dot.isHidden()


def test_scenario_name_edit_renames_and_marks_dirty(window):
    win = window()
    win.edit_scenario_name.setText("Daily quest")
    win.edit_scenario_name.editingFinished.emit()
    assert win.scenario.name == "Daily quest"
    assert win._is_dirty is True
    assert "Daily quest" in win.windowTitle()

    win.edit_scenario_name.setText("   ")
    win.edit_scenario_name.editingFinished.emit()
    assert win.scenario.name == "Daily quest"  # blank names are rejected
    assert win.edit_scenario_name.text() == "Daily quest"


def test_open_and_save_record_recent_files(window, tmp_path):
    win = window()
    first = _scenario_file(tmp_path, "daily_quest")
    second = _scenario_file(tmp_path, "arena_farm")

    assert win.open_scenario(str(first)) is True
    assert win.recent_store.paths() == [str(first.resolve())]
    assert win.lbl_step_count.text() == "1 step"

    win.current_file_path = second.resolve()
    assert win.save_scenario() is True
    assert win.recent_store.paths()[0] == str(second.resolve())
    item = win.sidebar.item_for_path(second)
    assert item is not None and item.data(CURRENT_ROLE) is True
    assert win.sidebar.item_for_path(first).data(CURRENT_ROLE) is False


def test_sidebar_activation_opens_scenario(window, tmp_path, qtbot):
    win = window()
    path = _scenario_file(tmp_path, "login_routine")
    win.recent_store.add(path)
    win.sidebar.refresh()

    win.sidebar.scenario_activated.emit(str(path.resolve()))
    assert win.current_file_path == path.resolve()
    assert win.scenario.name == "Login Routine"
    assert win.dashboard.get_effective_loop_count() == 2


def test_missing_recent_file_is_reported_not_opened(window, tmp_path):
    win = window()
    gone = tmp_path / "gone.json"
    win.sidebar.missing_scenario_activated.emit(str(gone))
    assert win.current_file_path is None
    assert "File not found: gone.json" in win.status_bar.currentMessage()
    assert any("not found" in e.message for e in win.log_console._entries)


def test_new_scenario_shows_draft_row(window, tmp_path):
    win = window()
    path = _scenario_file(tmp_path)
    win.open_scenario(str(path))
    win.new_scenario()
    first = win.sidebar.list_widget.item(0)
    assert first.text() == "Untitled Scenario"
    assert win.top_bar.lbl_file.text() == "Not saved yet"


def test_permission_warning_slot(window, qtbot):
    win = window()
    assert win.top_bar.btn_permissions.isHidden()
    win.set_permission_warning("Accessibility access is required.")
    assert not win.top_bar.btn_permissions.isHidden()
    with qtbot.waitSignal(win.permissions_requested):
        win.top_bar.btn_permissions.click()
    assert win.status_bar.currentMessage() == "Accessibility access is required."
    win.set_permission_warning(None)
    assert win.top_bar.btn_permissions.isHidden()


# -----------------------------------------------------------------------------
# Scenario name typed but not applied yet (review 1, major)
# -----------------------------------------------------------------------------

def _native(sequence) -> str:
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


def _type_new_name(win: MainWindow, qtbot, text: str) -> None:
    """Focuses the header name field and types ``text`` with synthetic Qt key events."""
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    field = win.edit_scenario_name
    field.setFocus(Qt.FocusReason.MouseFocusReason)
    qtbot.waitUntil(field.hasFocus, timeout=2000)
    field.selectAll()
    QTest.keyClicks(field, text)
    assert field.hasFocus(), "the field must still have focus (the bug only shows then)"


def test_typing_a_name_shows_unsaved_before_it_is_applied(window, qtbot, tmp_path):
    win = window()
    path = _scenario_file(tmp_path)
    win.open_scenario(str(path))
    assert win.top_bar.lbl_save_state.text() == "Saved"

    _type_new_name(win, qtbot, "Weekend quest")
    assert win.scenario.name == "Daily Quest"  # not applied while typing
    assert win._is_dirty is False
    assert win.has_unsaved_changes() is True
    assert win.top_bar.lbl_save_state.text() == "Unsaved changes"
    assert not win.editor_dirty_dot.isHidden()
    assert win.windowTitle().endswith(" *")


def test_save_action_applies_a_name_still_being_typed(window, qtbot, tmp_path):
    win = window()
    path = _scenario_file(tmp_path)
    win.open_scenario(str(path))
    _type_new_name(win, qtbot, "Weekend quest")

    win.act_save.trigger()  # Ctrl+S / File > Save keep the focus in the field

    assert ScenarioPersistence.load(path).name == "Weekend quest"
    assert win.scenario.name == "Weekend quest"
    assert win._is_dirty is False and win.has_unsaved_changes() is False
    assert win.top_bar.lbl_save_state.text() == "Saved"
    assert win.editor_dirty_dot.isHidden()


def test_closing_with_a_name_still_being_typed_asks_to_save(window, qtbot, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.Cancel)
    win = window()
    path = _scenario_file(tmp_path)
    win.open_scenario(str(path))
    _type_new_name(win, qtbot, "Weekend quest")

    assert win.close() is False  # Cancel keeps the window open
    assert asked == ["Unsaved Changes"]
    assert win.scenario.name == "Weekend quest"  # the typed name was applied, not lost
    assert ScenarioPersistence.load(path).name == "Daily Quest"


def test_new_scenario_with_a_name_still_being_typed_asks_to_save(window, qtbot, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.Discard)
    win = window()
    win.open_scenario(str(_scenario_file(tmp_path)))
    _type_new_name(win, qtbot, "Weekend quest")

    win.act_new.trigger()  # Ctrl+N

    assert asked == ["Unsaved Changes"]
    assert win.scenario.name == "Untitled Scenario"
    assert win.edit_scenario_name.text() == "Untitled Scenario"  # replaced although focused
    assert win.has_unsaved_changes() is False


def test_escape_cancels_the_rename_and_enter_applies_it(window, qtbot, tmp_path):
    win = window()
    win.open_scenario(str(_scenario_file(tmp_path)))
    field = win.edit_scenario_name

    _type_new_name(win, qtbot, "Weekend quest")
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert field.text() == "Daily Quest" and win.scenario.name == "Daily Quest"
    assert win.has_unsaved_changes() is False
    assert not field.hasFocus()  # Esc leaves the field
    assert win.top_bar.lbl_save_state.text() == "Saved"

    _type_new_name(win, qtbot, "Weekend quest")
    QTest.keyClick(field, Qt.Key.Key_Return)
    assert win.scenario.name == "Weekend quest" and win._is_dirty is True
    assert not field.hasFocus()


def test_run_and_save_as_apply_a_name_still_being_typed(window, qtbot, monkeypatch):
    seen = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: seen.append(a[2]) or ("", ""))
    win = window()
    _type_new_name(win, qtbot, "Arena: farm/2")

    win._sync_model_from_views()  # the first step of start_macro() and save_scenario()
    assert win.scenario.name == "Arena: farm/2"

    win.edit_scenario_name.setFocus()
    win.edit_scenario_name.selectAll()
    QTest.keyClicks(win.edit_scenario_name, "Login routine")
    assert win.save_scenario_as() is False  # dialog cancelled
    assert win.scenario.name == "Login routine"
    assert seen == ["Login routine.json"]  # suggested from the scenario name


def test_programmatic_rename_is_not_overwritten_by_the_header(window, tmp_path):
    """Only text the user typed is applied; a stale header never replaces the model's name."""
    win = window()
    win.scenario.name = "Set by code"
    win.current_file_path = tmp_path / "code.json"
    assert win.save_scenario() is True
    assert ScenarioPersistence.load(tmp_path / "code.json").name == "Set by code"
    assert win.edit_scenario_name.text() == "Set by code"


# -----------------------------------------------------------------------------
# Run availability, Save As shortcut during runs
# -----------------------------------------------------------------------------

def test_run_is_disabled_until_the_scenario_has_a_step(window):
    win = window()
    dash = win.dashboard
    assert not dash.btn_start.isEnabled() and not win.act_run.isEnabled()
    assert dash.btn_start.toolTip() == MainWindow.NO_STEPS_HINT

    block = win.scenario_builder.add_action_block(ActionType.FIXED_WAIT)
    assert dash.btn_start.isEnabled() and win.act_run.isEnabled()
    assert dash.btn_start.toolTip() == ExecutionDashboard.RUN_TOOLTIP

    win.scenario_builder.delete_action(block.id)
    assert not dash.btn_start.isEnabled() and not win.act_run.isEnabled()

    dash.set_state("RUNNING")
    dash.set_state("IDLE")
    assert not dash.btn_start.isEnabled()  # finishing a run does not re-enable an empty scenario


def test_dashboard_runnable_flag_combines_with_engine_state(qtbot):
    dash = ExecutionDashboard()
    qtbot.addWidget(dash)
    assert dash.is_runnable() and dash.btn_start.isEnabled()
    dash.set_runnable(False, "Add a step")
    assert not dash.btn_start.isEnabled() and dash.btn_start.toolTip() == "Add a step"
    dash.set_state("RUNNING")
    dash.set_runnable(True)
    assert not dash.btn_start.isEnabled()  # running: Run stays disabled
    dash.set_state("STOPPED")
    assert dash.btn_start.isEnabled()
    assert dash.btn_start.toolTip() == ExecutionDashboard.RUN_TOOLTIP


def test_save_as_shortcut_is_suspended_while_a_run_is_active(window):
    """Ctrl+Shift+S is the global emergency stop; during a run it must not open Save As too."""
    win = window()
    save_as = QKeySequence(QKeySequence.StandardKey.SaveAs)
    assert win.act_save_as.shortcut() == save_as
    for state in ("RUNNING", "PAUSED", "STOPPING"):
        win.dashboard.set_state(state)
        assert win.act_save_as.shortcut().isEmpty(), state
        assert win.act_save_as.isEnabled()  # the menu item stays usable
    win.dashboard.set_state("IDLE")
    assert win.act_save_as.shortcut() == save_as


# -----------------------------------------------------------------------------
# Chrome: menus in the top bar, "More" menu, native shortcut wording
# -----------------------------------------------------------------------------

def test_menus_share_the_top_bar_when_there_is_no_global_menu_bar(window, qtbot):
    win = window()
    if win.menuBar().isNativeMenuBar():
        pytest.skip("menus live in the platform's global menu bar")
    inline = win.inline_menu_bar
    assert inline is not None and win.top_bar.isAncestorOf(inline)
    assert win.menuBar().isHidden()  # no second header row
    assert [a.text() for a in inline.actions()] == [a.text() for a in win.menuBar().actions()]
    assert [a.menu() for a in inline.actions()] == [a.menu() for a in win.menuBar().actions()]

    # Shortcuts of the shared menus still work although the window's own bar is hidden.
    win.show()
    qtbot.waitExposed(win)
    win.activateWindow()
    qtbot.waitUntil(win.isActiveWindow, timeout=2000)
    assert win.is_sidebar_visible() and win.act_toggle_sidebar.shortcut() == QKeySequence("Ctrl+B")
    QTest.keyClick(win, Qt.Key.Key_B, Qt.KeyboardModifier.ControlModifier)
    assert not win.is_sidebar_visible()


def test_more_menu_replaces_the_misleading_settings_menu(window):
    win = window()
    assert win.more_menu.title() == "More"
    assert win.top_bar.btn_more.menu() is win.more_menu
    assert win.rail_btn_more.menu() is win.more_menu
    assert win.top_bar.btn_more.toolTip() == "More" and win.rail_btn_more.toolTip() == "More"
    texts = [a.text() for a in win.more_menu.actions() if not a.isSeparator()]
    assert "Clear Recent Scenarios" in texts and "&About ActionMacro" in texts


def test_tooltips_use_the_platform_shortcut_wording(window):
    win = window()
    assert win.act_toggle_sidebar.toolTip() == f"Scenarios ({_native('Ctrl+B')})"
    assert win.act_toggle_console.toolTip() == f"Console ({_native('Ctrl+J')})"
    assert win.rail_btn_scenarios.toolTip() == win.act_toggle_sidebar.toolTip()
    assert win.btn_save.toolTip() == f"Save scenario ({_native(QKeySequence.StandardKey.Save)})"
    assert win.sidebar.btn_new.toolTip() == f"New scenario ({_native(QKeySequence.StandardKey.New)})"
    assert win.sidebar.btn_open.toolTip() == f"Open a scenario file ({_native(QKeySequence.StandardKey.Open)})"
    # The emergency stop is a physical key combination read by pynput: always "Ctrl".
    assert "Ctrl+Shift+S" in win.dashboard.btn_stop.toolTip()


def test_informative_chrome_text_does_not_use_the_faint_colour(window):
    """text-faint (#6B7280) is for placeholders/disabled; it misses WCAG AA for small text."""
    win = window()
    for container in (win.top_bar, win.editor_header, win.status_bar):
        for label in container.findChildren(QLabel):
            assert label.property("role") != "faint", label.text()
    assert win.lbl_step_count.property("role") == "muted"
    assert win.lbl_version.property("role") == "muted"
    assert "QLabel#SaveStateLabel { color: %s" % theme.COLORS.text_muted in theme.build_stylesheet()


# -----------------------------------------------------------------------------
# Narrow window, empty states
# -----------------------------------------------------------------------------

def _long_scenario() -> ScenarioModel:
    return ScenarioModel(
        name="Daily quest",
        actions=[
            ActionBlock(id="c", type=ActionType.MOUSE_CLICK,
                        params=MouseClickParams(x=12800, y=7200, button="left", clicks=3, interval_ms=120)),
            ActionBlock(id="k", type=ActionType.KEY_INPUT,
                        params=KeyInputParams(keys=["ctrl", "shift", "alt", "f12"], action="press")),
            ActionBlock(id="w", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=123456)),
        ],
    )


@pytest.mark.parametrize("size", [(1100, 680), (1180, 700)])
def test_step_cards_fit_a_narrow_window(window, qtbot, size):
    win = window(scenario=_long_scenario())
    assert win.minimumWidth() <= size[0] and win.minimumHeight() <= size[1]
    win.resize(*size)
    win.show()
    qtbot.waitExposed(win)
    lw = win.scenario_builder.list_widget
    qtbot.waitUntil(lambda: lw.viewport().width() > 0)
    assert lw.horizontalScrollBar().maximum() == 0  # no sideways scrolling
    for card in win.scenario_builder._card_map.values():
        assert card.width() <= lw.viewport().width()
        for control in (card.chk_enabled, card.btn_more):  # the right-hand controls stay visible
            assert control.geometry().right() <= card.contentsRect().right()
    click_card = win.scenario_builder._card_map["c"]
    assert click_card.lbl_summary.text() == "Click left 3 times at (12800, 7200), 120 ms apart"  # full text kept


def test_inspector_empty_state_needs_no_scrollbar(window, qtbot):
    win = window()
    win.resize(1100, 680)
    win.show()
    qtbot.waitExposed(win)
    inspector = win.property_inspector
    assert inspector.stacked_widget.currentWidget() is inspector.page_empty
    assert isinstance(inspector.page_empty, EmptyState)
    assert inspector.page_empty.title() == "No step selected"
    assert win.inspector_scroll.verticalScrollBar().maximum() == 0


def test_sidebar_hint_sits_under_the_rows(window, qtbot):
    win = window()
    win.resize(1400, 900)
    win.show()
    qtbot.waitExposed(win)
    sidebar = win.sidebar
    assert sidebar.lbl_hint.isVisible()
    list_bottom = sidebar.list_widget.geometry().bottom()
    assert sidebar.lbl_hint.geometry().top() - list_bottom <= 2 * theme.SPACE_SM
    assert sidebar.btn_open.geometry().top() - sidebar.lbl_hint.geometry().bottom() > 200
