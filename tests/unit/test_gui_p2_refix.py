"""Regression tests for the phase-2 review fixes (cards, inspector, console, perms, main window).

Every test here guards one reviewed problem; each failed on the tree before its fix.
All widgets run offscreen and only receive synthetic Qt events.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QColor, QImage, QKeySequence, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from action_macro.core.key_mapping import format_key_combo
from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.core.persistence import ScenarioPersistence, resolve_template_path
from action_macro.gui import step_text, theme
from action_macro.gui import components
from action_macro.gui.components import SegmentedControl, style_button, with_shortcut
from action_macro.gui.log_console import LogConsole
from action_macro.gui.main_window import MainWindow
from action_macro.gui.property_inspector import PropertyInspector
from action_macro.gui.scenario_builder import ActionCardWidget, ScenarioBuilder

C = theme.COLORS


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def themed_app(qapp):
    """Runs this module against the shipped look (sizes and focus rings come from the QSS)."""
    saved_sheet, saved_palette, saved_font = qapp.styleSheet(), qapp.palette(), qapp.font()
    theme.apply_theme(qapp)
    yield qapp
    qapp.setStyleSheet(saved_sheet)
    qapp.setPalette(saved_palette)
    qapp.setFont(saved_font)


def _four_steps(template: str = "templates/quest_button.png") -> ScenarioModel:
    return ScenarioModel(name="Daily quest", actions=[
        ActionBlock(id="s1", type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=1280, y=720)),
        ActionBlock(id="s2", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["ctrl", "c"])),
        ActionBlock(id="s3", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=1000)),
        ActionBlock(id="s4", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path=template)),
    ])


@pytest.fixture
def window(qtbot, isolated_settings, monkeypatch):
    """MainWindow factory with private QSettings; never blocks on an unsaved-changes prompt."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    windows: List[MainWindow] = []

    def _make(size=(1400, 900), show=True, **kwargs) -> MainWindow:
        win = MainWindow(settings=isolated_settings, **kwargs)
        qtbot.addWidget(win)
        windows.append(win)
        if show:
            win.resize(*size)
            win.show()
            qtbot.waitExposed(win)
            QApplication.processEvents()
        return win

    yield _make
    for win in windows:
        win.mark_dirty(False)


def _write_png(path: Path, size=(40, 20)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    image.fill(QColor(C.accent))
    assert image.save(str(path))
    return path


def _contrast(foreground: str, background: str) -> float:
    def luminance(color: QColor) -> float:
        channels = []
        for value in color.getRgbF()[:3]:
            channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
        r, g, b = channels
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    lighter, darker = sorted((luminance(QColor(foreground)), luminance(QColor(background))), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _over(foreground: str, opacity: float, background: str) -> str:
    """``foreground`` at ``opacity`` composited over ``background`` (``#RRGGBB``)."""
    f, b = QColor(foreground), QColor(background)
    mix = [round(opacity * x + (1 - opacity) * y) for x, y in
           ((f.red(), b.red()), (f.green(), b.green()), (f.blue(), b.blue()))]
    return QColor(*mix).name()


def _qss_color(selector: str) -> str:
    """The ``color:`` value of the first rule whose selector list is exactly ``selector``."""
    sheet = theme.build_stylesheet()
    match = re.search(re.escape(selector) + r"\s*\{[^}]*?(?<![-\w])color:\s*([^;]+);", sheet)
    assert match, f"no colour rule for {selector}"
    return match.group(1).strip()


def _focus(widget: QWidget, qtbot) -> None:
    widget.window().activateWindow()
    widget.setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.waitUntil(widget.hasFocus, timeout=2000)
    QApplication.processEvents()


def _accent_pixels(image: QImage, color: str, tolerance: int = 40) -> int:
    target = QColor(color)
    count = 0
    for x in range(image.width()):
        for y in (0, 1, image.height() - 2, image.height() - 1):
            pixel = image.pixelColor(x, y)
            if (abs(pixel.red() - target.red()) + abs(pixel.green() - target.green())
                    + abs(pixel.blue() - target.blue())) <= tolerance:
                count += 1
    return count


# -----------------------------------------------------------------------------
# [major] card switch <-> inspector "enabled" stay in sync
# -----------------------------------------------------------------------------


def test_card_switch_updates_the_inspector_and_a_later_edit_keeps_the_step_off(window, qtbot):
    win = window(scenario=_four_steps())
    win.scenario_builder.select_action("s2")
    action = win.scenario.get_action("s2")
    card = win.scenario_builder._card_map["s2"]
    inspector = win.property_inspector
    assert inspector.current_action is action and inspector.chk_enabled.isChecked()

    card.chk_enabled.click()
    assert action.enabled is False
    assert not inspector.chk_enabled.isChecked()  # the inspector shows the new state

    inspector.edit_description.setFocus()
    QTest.keyClicks(inspector.edit_description, "x")
    assert action.description == "x"
    assert action.enabled is False  # editing another field never switches the step back on
    assert not card.chk_enabled.isChecked()
    assert card.lbl_summary.text().startswith(step_text.DISABLED_PREFIX)


def test_inspector_description_edit_writes_only_the_description(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    action = ActionBlock(type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=500), enabled=True)
    inspector.set_action(action)
    action.enabled = False  # changed elsewhere (the card switch)
    inspector.edit_description.setText("note")
    assert action.description == "note" and action.enabled is False
    inspector.chk_enabled.setChecked(True)  # unchanged view -> no toggle: set it off then on
    inspector.chk_enabled.setChecked(False)
    assert action.enabled is False
    inspector.chk_enabled.setChecked(True)
    assert action.enabled is True


def test_inspector_enable_control_is_the_same_switch_as_the_card(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    assert isinstance(inspector.chk_enabled, components.ToggleSwitch) and isinstance(inspector.chk_enabled, QCheckBox)
    assert inspector.chk_enabled.text() == "Enabled"
    card = ActionCardWidget(ActionBlock(type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=1)), 0, 1)
    qtbot.addWidget(card)
    assert card.chk_enabled.accessibleName() == "Enabled"


# -----------------------------------------------------------------------------
# [major] image wait without a template file
# -----------------------------------------------------------------------------


def test_missing_template_shows_a_warning_status_not_a_green_check(qtbot, tmp_path):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    inspector.set_base_dir(tmp_path)
    action = ActionBlock(type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="templates/nope.png"))
    inspector.set_action(action)
    assert inspector.form_image.preview_state() == "missing"
    assert inspector.lbl_status.text().startswith("⚠")
    assert "✓" not in inspector.lbl_status.text()
    assert inspector.lbl_status.property("state") == "warning"

    _write_png(tmp_path / "templates" / "nope.png")
    inspector.form_image.edit_path.setText("templates/nope.png ")  # re-check the same file
    assert inspector.form_image.preview_state() == "found"
    assert inspector.lbl_status.text().startswith("✓")
    assert inspector.lbl_status.property("state") == "valid"


def test_missing_template_path_is_one_line_with_the_full_path_in_the_tooltip(qtbot, tmp_path):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    deep = tmp_path / ("a-very-long-folder-name-" * 3) / "nested" / "deeper" / "templates"
    inspector.set_base_dir(deep)
    inspector.set_action(ActionBlock(type=ActionType.IMAGE_WAIT,
                                     params=ImageWaitParams(template_path="button.png")))
    inspector.resize(320, 900)
    inspector.show()
    qtbot.waitExposed(inspector)
    QApplication.processEvents()
    label = inspector.form_image.lbl_missing_path
    looked_for = str(resolve_template_path("button.png", deep))
    assert label.isVisible() and label.text() == looked_for
    assert looked_for in label.toolTip()
    assert label.height() <= label.fontMetrics().height() + 4  # one line, not 8-10 wrapped lines
    shown = label.elided_text(label.contentsRect().width())
    assert "…" in shown and shown.endswith("/button.png")  # the folder is shortened, the name stays whole


def test_new_image_wait_step_starts_with_the_friendly_empty_drop_zone(window):
    win = window(scenario=ScenarioModel(name="New"))
    action = win.scenario_builder.add_action_block(ActionType.IMAGE_WAIT)
    assert action.params.template_path == step_text.DEFAULT_TEMPLATE_PATH
    form = win.property_inspector.form_image
    assert form.preview_state() == "placeholder"
    assert form.lbl_template_name.text() == "No template image"
    assert form.lbl_missing_path.isHidden()
    status = win.property_inspector.lbl_status
    assert status.property("state") == "warning" and "Choose" in status.text()
    card = win.scenario_builder._card_map[action.id]
    assert card.lbl_status.isVisible() and card.lbl_status.property("state") == "missing"
    assert card.lbl_status.text() == "No image"  # same message as the inspector, not an error


def test_step_card_flags_a_missing_template(qtbot, tmp_path):
    _write_png(tmp_path / "templates" / "ok.png")
    scenario = ScenarioModel(name="x", actions=[
        ActionBlock(id="ok", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="templates/ok.png")),
        ActionBlock(id="gone", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="templates/gone.png")),
    ])
    builder = ScenarioBuilder(scenario)
    qtbot.addWidget(builder)
    builder.set_base_dir(tmp_path)
    ok, gone = builder._card_map["ok"], builder._card_map["gone"]
    assert ok.lbl_status.isHidden()
    assert not gone.lbl_status.isHidden()
    assert gone.lbl_status.text() == "Image not found" and gone.lbl_status.property("state") == "missing"
    builder.set_active_action(1)  # running wins over the missing flag
    assert gone.lbl_status.text() == "Running"
    builder.clear_active_action()
    assert gone.lbl_status.text() == "Image not found"
    gone.chk_enabled.click()  # a disabled step is skipped: nothing to warn about
    assert gone.lbl_status.isHidden()
    gone.chk_enabled.click()
    assert not gone.lbl_status.isHidden()


# -----------------------------------------------------------------------------
# [major] key steps read like the inspector chip
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("params", [
    KeyInputParams(keys=["cmd_or_ctrl", "shift", "t"]),
    KeyInputParams(keys=["c"], modifiers=["cmd_or_ctrl"]),
    KeyInputParams(keys=["page_up"]),
    KeyInputParams(keys=["shift"], action="down"),
])
def test_card_summary_uses_the_same_key_formatting_as_the_inspector(qtbot, params):
    block = ActionBlock(type=ActionType.KEY_INPUT, params=params)
    summary = step_text.step_summary(block)
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    inspector.set_action(block)
    chip = inspector.form_key.lbl_human.text()
    assert summary.endswith(chip) and chip
    assert "_" not in summary and "cmd_or_ctrl" not in summary


# -----------------------------------------------------------------------------
# [major] focus rings
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["ghost", "secondary"])
def test_buttons_show_a_two_pixel_focus_ring(qtbot, variant):
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    first = QPushButton("First")
    button = QPushButton("Add step")
    style_button(button, variant, "plus", icon_px=14, height=theme.CONTROL_HEIGHT_SM)
    layout.addWidget(first)
    layout.addWidget(button)
    host.show()
    qtbot.waitExposed(host)
    before = _accent_pixels(button.grab().toImage(), C.accent)
    _focus(button, qtbot)
    after = _accent_pixels(button.grab().toImage(), C.accent)
    assert before == 0
    assert after >= 2 * (button.width() - 16)  # top and bottom ring, two pixels deep


def test_small_selection_controls_use_two_pixel_rings_and_checked_check_boxes_stay_visible():
    sheet = theme.build_stylesheet()
    for selector in ('QFrame#SegmentedControl QPushButton:focus', 'QCheckBox[variant="chip"]:focus',
                     'QToolButton:focus'):
        rule = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", sheet)
        assert rule and "2px solid" in rule.group(1), selector
    rule = re.search(r"QCheckBox::indicator:checked:focus\s*\{([^}]*)\}", sheet)
    assert rule and C.accent_text in rule.group(1)  # not the accent fill colour


# -----------------------------------------------------------------------------
# [major] contrast of informative small text
# -----------------------------------------------------------------------------


def test_informative_small_text_meets_wcag_aa():
    for selector, background in (
        ("QLabel#ConsoleEmpty", C.bg),
        ("QLabel#PermissionsCheckedLabel", C.surface),
    ):
        assert _contrast(_qss_color(selector), background) >= 4.5, selector
    invalid = _qss_color('QLabel#ValidationStatus[state="invalid"]')
    assert _contrast(invalid, _over(C.danger, 0.14, C.surface)) >= 4.5
    placeholder = theme.build_palette().color(QPalette.ColorRole.PlaceholderText).name()
    assert _contrast(placeholder, C.surface_2) >= 4.5


def test_console_timestamps_and_disabled_step_text_are_not_faint(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)
    console.append_log("DEBUG", "debug message")
    html_line = console._entries[0].to_formatted_html()
    assert C.text_faint not in html_line
    card = ActionCardWidget(ActionBlock(type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=1),
                                        enabled=False), 0, 1)
    qtbot.addWidget(card)
    assert card.lbl_summary.property("role") == "muted" and card.lbl_index.property("role") == "muted"


# -----------------------------------------------------------------------------
# [minor] run state on the cards
# -----------------------------------------------------------------------------


def test_paused_run_shows_a_paused_pill(window):
    win = window(scenario=_four_steps())
    win.dashboard.set_state("RUNNING")
    win.scenario_builder.set_active_action(1)
    card = win.scenario_builder._card_map["s2"]
    assert card.lbl_status.text() == "Running" and card.lbl_status.property("state") == "running"
    win.dashboard.set_state("PAUSED")
    assert card.lbl_status.isVisible() and card.lbl_status.text() == "Paused"
    assert card.lbl_status.property("state") == "paused"
    win.dashboard.set_state("RUNNING")
    assert card.lbl_status.text() == "Running"
    win.dashboard.set_state("IDLE")
    win.scenario_builder.clear_active_action()


def test_selected_card_is_not_tinted_like_the_running_card(qtbot):
    builder = ScenarioBuilder(_four_steps())
    qtbot.addWidget(builder)
    builder.resize(760, 640)
    builder.show()
    qtbot.waitExposed(builder)
    builder.select_action("s1")
    builder.set_active_action(2)
    idle, selected, running = (builder._card_map[i] for i in ("s2", "s1", "s3"))

    def fill(card) -> QColor:
        return card.grab().toImage().pixelColor(card.width() // 2, card.height() - ActionCardWidget.INSET - 6)

    assert fill(selected) == fill(idle)  # selection: accent border only
    assert fill(running) != fill(idle)   # running: tinted
    builder.clear_active_action()


# -----------------------------------------------------------------------------
# [minor] alignment, badges and control heights
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(1400, 900), (1100, 680)])
def test_console_lines_up_with_the_editor_column(window, size):
    win = window(scenario=_four_steps(), size=size)
    steps_x = win.scenario_builder.lbl_section.mapTo(win, QPoint(0, 0)).x()
    console_x = win.log_console.lbl_title.mapTo(win, QPoint(0, 0)).x()
    assert console_x == steps_x


def test_count_badges_and_status_pills_share_one_shape(window):
    win = window(scenario=_four_steps())
    win.log_console.append_log("INFO", "one")
    QApplication.processEvents()
    steps, console = win.scenario_builder.lbl_count, win.log_console.lbl_count
    assert steps.height() == console.height() == 18
    assert steps.font().weight() == console.font().weight()
    assert steps.property("role") == console.property("role") == "count"
    panel = win.show_permissions_panel()
    pill = panel.findChild(QWidget, "PermissionPill")
    assert pill.height() == 18
    panel.close()


def test_buttons_honour_the_28_and_32_pixel_heights(window):
    win = window(scenario=_four_steps())
    assert win.scenario_builder.btn_add_step.height() == theme.CONTROL_HEIGHT_SM
    assert win.btn_save.height() == theme.CONTROL_HEIGHT_SM
    win.scenario_builder.select_action("s1")
    QApplication.processEvents()
    assert win.property_inspector.form_mouse.btn_pick.height() == theme.CONTROL_HEIGHT
    win.scenario_builder.select_action("s2")
    QApplication.processEvents()
    form = win.property_inspector.form_key
    assert form.edit_keys.height() == theme.CONTROL_HEIGHT
    assert form.spin_duration.height() == theme.CONTROL_HEIGHT
    assert form.seg_action.height() == theme.CONTROL_HEIGHT
    assert form.chk_ctrl.height() == theme.CONTROL_HEIGHT_SM
    win.scenario_builder.select_action("s4")
    QApplication.processEvents()
    assert win.property_inspector.form_image.btn_browse.height() == theme.CONTROL_HEIGHT_SM


@pytest.mark.parametrize("size", [(1600, 1000), (1400, 900), (1100, 680)])
def test_empty_state_tile_titles_share_a_baseline(window, size):
    win = window(scenario=ScenarioModel(name="Empty"), size=size)
    tiles = list(win.scenario_builder.add_step_tiles.values())
    tops = {tile.lbl_label.y() for tile in tiles}
    assert len(tops) == 1, tops


# -----------------------------------------------------------------------------
# [minor] console empty state and layout jumps
# -----------------------------------------------------------------------------


def test_console_chips_do_not_move_when_the_first_entry_arrives(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)
    console.resize(900, 200)
    console.show()
    qtbot.waitExposed(console)
    QApplication.processEvents()
    chip = console.filter_chips["ERROR"]
    before = chip.mapTo(console, QPoint(0, 0)).x()
    assert console.lbl_count.isHidden()
    console.append_log("INFO", "first")
    QApplication.processEvents()
    assert chip.mapTo(console, QPoint(0, 0)).x() == before
    console.clear_logs()
    QApplication.processEvents()
    assert chip.mapTo(console, QPoint(0, 0)).x() == before


def test_console_empty_line_is_centred_in_the_view(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)
    console.resize(900, 200)
    console.show()
    qtbot.waitExposed(console)
    QApplication.processEvents()
    viewport = console.text_edit.viewport().rect()
    label = console.lbl_empty.geometry()
    assert abs(label.center().x() - viewport.center().x()) <= 1
    assert abs(label.center().y() - viewport.center().y()) <= 1


def test_adding_the_first_step_does_not_resize_the_console(window):
    win = window(scenario=ScenarioModel(name="Empty"), size=(1100, 680))
    before = win.console_panel.height()
    win.scenario_builder.add_action_block(ActionType.MOUSE_CLICK)
    QApplication.processEvents()
    assert win.console_panel.height() == before


# -----------------------------------------------------------------------------
# [minor] editing is paused during a run; deleted steps can be restored
# -----------------------------------------------------------------------------


def test_steps_are_read_only_while_a_run_is_active(window, qtbot):
    win = window(scenario=_four_steps())
    builder, inspector = win.scenario_builder, win.property_inspector
    win.dashboard.set_state("RUNNING")
    assert not builder.is_editable()
    assert not builder.btn_add_step.isEnabled() and not builder.btn_add_step_inline.isEnabled()
    card = builder._card_map["s1"]
    assert not card.chk_enabled.isEnabled() and not card.btn_more.isEnabled()
    assert not inspector.isEnabled()
    assert not win.act_picker.isEnabled()  # picking a point writes into the selected step
    builder.list_widget.setFocus()
    builder.select_action("s1")
    QTest.keyClick(builder.list_widget, Qt.Key.Key_Delete)
    assert [a.id for a in win.scenario.actions] == ["s1", "s2", "s3", "s4"]
    win.dashboard.set_state("IDLE")
    assert builder.is_editable() and inspector.isEnabled() and card.chk_enabled.isEnabled()
    assert win.act_picker.isEnabled()


def test_a_deleted_step_can_be_restored_with_undo(window, qtbot):
    win = window(scenario=_four_steps())
    builder = win.scenario_builder
    builder.select_action("s2")
    builder.list_widget.setFocus()
    QTest.keyClick(builder.list_widget, Qt.Key.Key_Delete)
    assert [a.id for a in win.scenario.actions] == ["s1", "s3", "s4"]
    assert "Undo" in win.status_bar.currentMessage()
    assert win.act_undo_delete.isEnabled()
    assert win.act_undo_delete.shortcut() == QKeySequence(QKeySequence.StandardKey.Undo)
    win.act_undo_delete.trigger()
    assert [a.id for a in win.scenario.actions] == ["s1", "s2", "s3", "s4"]
    assert win.property_inspector.current_action is win.scenario.get_action("s2")
    assert not win.act_undo_delete.isEnabled()


# -----------------------------------------------------------------------------
# [minor] wording
# -----------------------------------------------------------------------------


def test_ui_wording_is_consistent(window, qtbot):
    win = window(scenario=_four_steps())
    texts = [a.text() for menu in win.menuBar().actions() if menu.menu() for a in menu.menu().actions()]
    texts += [a.text() for a in win.more_menu.actions()]
    assert not [t for t in texts if "..." in t]
    assert "&Open Scenario…" in texts and "Save &As…" in texts
    assert win.act_picker.text() == "&Pick from Screen"  # same name as the inspector button

    picks: List[str] = []
    win.coordinate_picker_factory = lambda parent: None
    win.log_console.append_log = lambda level, message: picks.append(message)  # type: ignore[assignment]
    win.trigger_coordinate_picker()
    assert picks[0] == "Coordinate picker started. Click to pick · Esc to cancel"

    win.scenario_builder.select_action("s3")
    QApplication.processEvents()
    inspector = win.property_inspector
    assert inspector.form_wait.lbl_human.text() == "1.0 s"
    assert inspector.chk_enabled.toolTip() == "Disabled steps are skipped when the scenario runs."
    win.scenario_builder.select_action("s4")
    QApplication.processEvents()
    assert inspector.form_image.spin_conf.text() == "80%"
    win.scenario_builder.select_action("s2")
    QApplication.processEvents()
    inspector.form_key.edit_keys.setText("+++")
    assert inspector.lbl_status.text() == "⚠ Invalid key combination (e.g. ctrl+c or enter)"


def test_console_header_icons_explain_themselves(window):
    win = window()
    console = win.log_console
    assert console.btn_collapse.toolTip() == with_shortcut("Hide console", win.act_toggle_console.shortcut())
    assert console.auto_scroll_cb.objectName() == "AutoScrollButton"
    assert console.auto_scroll_cb.isChecked()
    image = console.auto_scroll_cb.grab().toImage()
    corner = image.pixelColor(2, 2)
    header_bg = console.findChild(QWidget, "ConsoleHeader").grab().toImage().pixelColor(2, 2)
    assert corner == header_bg  # checked: tinted icon only, no accent background


def test_buttons_leave_room_between_icon_and_text(qtbot):
    button = QPushButton("Pick from screen")
    qtbot.addWidget(button)
    style_button(button, "secondary", "crosshair", icon_px=14)
    assert button.iconSize().width() >= 14 + 4 and button.iconSize().height() == 14


def test_selected_last_card_stays_visible_after_the_console_comes_back(window):
    win = window(scenario=_four_steps(), size=(1100, 680))
    builder = win.scenario_builder
    builder.select_action("s4")
    win.set_console_visible(False)
    QApplication.processEvents()
    win.set_console_visible(True)
    QApplication.processEvents()
    item = builder._item_map["s4"]
    rect = builder.list_widget.visualItemRect(item)
    assert builder.list_widget.viewport().rect().contains(rect)


# -----------------------------------------------------------------------------
# [minor] console trimming while a filter is active
# -----------------------------------------------------------------------------


def test_trimmed_entries_leave_the_filtered_view(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)
    console.resize(900, 200)
    console.show()
    console.set_max_block_count(5)
    console.filter_combo.setCurrentText("ERROR")
    for i in range(3):
        console.append_log("ERROR", f"error {i}")
    for i in range(10):
        console.append_log("INFO", f"info {i}")
    assert console.text_edit.toPlainText() == ""
    assert console.shown_count() == 0 and console.lbl_count.text() == "0 / 5"
    assert console.lbl_empty.isVisible()

    console.append_log("ERROR", "kept")
    assert console.text_edit.toPlainText().strip().endswith("kept")
    assert len(console.text_edit.toPlainText().splitlines()) == 1


def test_set_max_block_count_trims_the_filtered_view_too(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)
    for i in range(6):
        console.append_log("ERROR" if i % 2 else "INFO", f"message {i}")  # errors: 1, 3, 5
    console.filter_combo.setCurrentText("ERROR")
    console.set_max_block_count(4)  # keeps messages 2..5: errors 3 and 5
    lines = console.text_edit.toPlainText().splitlines()
    assert console.shown_count() == 2
    assert len(lines) == 2 and lines[0].endswith("message 3") and lines[1].endswith("message 5")


# -----------------------------------------------------------------------------
# [minor] Save As to another folder keeps relative templates working
# -----------------------------------------------------------------------------


def test_save_as_to_another_folder_rebases_relative_templates(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    folder_a, folder_b = tmp_path / "A", tmp_path / "B"
    _write_png(folder_a / "templates" / "t.png")
    folder_b.mkdir()
    scenario = ScenarioModel(name="s", actions=[
        ActionBlock(id="img", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="templates/t.png")),
    ])
    ScenarioPersistence.save(scenario, folder_a / "s.json")
    win = window()
    assert win.open_scenario(str(folder_a / "s.json"))
    assert win.property_inspector.form_image.preview_state() == "found"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(folder_b / "s.json"), ""))
    assert win.save_scenario_as()
    assert win.property_inspector.form_image.preview_state() == "found"

    saved = json.loads((folder_b / "s.json").read_text(encoding="utf-8"))
    raw = saved["actions"][0]["params"]["template_path"]
    loaded = ScenarioPersistence.load(folder_b / "s.json")
    engine_file = resolve_template_path(loaded.actions[0].params.template_path, folder_b)
    assert engine_file.is_file(), raw
    assert engine_file.resolve() == (folder_a / "templates" / "t.png").resolve()


def test_save_as_keeps_a_relative_template_that_exists_in_the_new_folder(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    folder_a, folder_b = tmp_path / "A", tmp_path / "B"
    _write_png(folder_a / "templates" / "t.png")
    _write_png(folder_b / "templates" / "t.png")
    scenario = ScenarioModel(name="s", actions=[
        ActionBlock(id="img", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="templates/t.png")),
    ])
    ScenarioPersistence.save(scenario, folder_a / "s.json")
    win = window()
    assert win.open_scenario(str(folder_a / "s.json"))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(folder_b / "s.json"), ""))
    assert win.save_scenario_as()
    saved = json.loads((folder_b / "s.json").read_text(encoding="utf-8"))
    assert saved["actions"][0]["params"]["template_path"] == "templates/t.png"


# -----------------------------------------------------------------------------
# [minor] initial selection reaches the inspector
# -----------------------------------------------------------------------------


def test_window_built_with_a_scenario_shows_the_selected_step(window):
    scenario = _four_steps()
    win = window(scenario=scenario, show=False)
    assert win.scenario_builder.list_widget.currentRow() == 0
    assert win.property_inspector.current_action is scenario.actions[0]
    assert win.property_inspector.step_index() == 1


# -----------------------------------------------------------------------------
# [minor] the test run never reaches the real permission bindings
# -----------------------------------------------------------------------------


def test_real_permission_bindings_are_guarded_during_tests():
    from action_macro.engine import macos_permissions as mp

    for name in ("_ax_is_process_trusted", "_cg_preflight_screen_capture_access", "_iohid_check_access"):
        assert getattr(getattr(mp, name), "_test_guard", False), name
    assert mp.default_checker is mp.all_granted


# -----------------------------------------------------------------------------
# Shared widgets
# -----------------------------------------------------------------------------


def test_segmented_control_compact_mode_hugs_its_content(qtbot):
    seg = SegmentedControl([("a", "All"), ("b", "Info")], compact=True)
    qtbot.addWidget(seg)
    seg.show()
    assert seg.height() == theme.CONTROL_HEIGHT_SM
    assert seg.sizePolicy().horizontalPolicy().name in ("Fixed", "Maximum")


def test_console_filters_are_a_compact_segmented_control(qtbot):
    console = LogConsole()
    qtbot.addWidget(console)
    seg = console.filter_segments
    assert isinstance(seg, SegmentedControl)
    assert list(console.filter_chips.values()) == [seg.button(v) for v in seg.values()]
    console.filter_chips["WARNING"].click()
    assert console.filter_combo.currentText() == "WARNING" and seg.value() == "WARNING"


# -----------------------------------------------------------------------------
# Follow-ups from the final verification
# -----------------------------------------------------------------------------


def test_status_bar_names_the_running_step_like_the_step_list(window):
    scenario = _four_steps()
    scenario.actions[2].description = "Let the menu open"
    win = window(scenario=scenario, show=False)
    win._run_actions = list(scenario.actions)
    # The engine sends the description, or the internal type when there is none.
    win._on_engine_action_started(1, "key_input")
    assert win.dashboard.action_label.text() == "Action 2: Press " + format_key_combo(["ctrl", "c"])
    win._on_engine_action_started(2, "Let the menu open")
    assert win.dashboard.action_label.text() == "Action 3: Let the menu open"
    win._on_engine_action_started(9, "mouse_click")  # out of range: shown as sent
    assert win.dashboard.action_label.text() == "Action 10: mouse_click"


def test_save_as_rebases_relative_templates_of_a_never_saved_scenario(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    folder_a, folder_b = tmp_path / "A", tmp_path / "B"
    _write_png(folder_a / "templates" / "t.png")
    folder_b.mkdir()
    monkeypatch.chdir(folder_a)  # unsaved: relative paths start at the working directory
    scenario = ScenarioModel(name="s", actions=[
        ActionBlock(id="img", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="templates/t.png")),
    ])
    win = window(scenario=scenario, show=False)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(folder_b / "s.json"), ""))
    assert win.save_scenario_as()
    loaded = ScenarioPersistence.load(folder_b / "s.json")
    engine_file = resolve_template_path(loaded.actions[0].params.template_path, folder_b)
    assert engine_file.resolve() == (folder_a / "templates" / "t.png").resolve()


def test_new_image_wait_card_does_not_name_the_default_file(window, tmp_path):
    win = window(scenario=ScenarioModel(name="New"), show=False)
    action = win.scenario_builder.add_action_block(ActionType.IMAGE_WAIT)
    card = win.scenario_builder._card_map[action.id]
    assert card.template_state() == "placeholder"
    assert card.lbl_summary.text() == "Wait until an image appears · timeout 10 s"
    action.params.template_path = str(_write_png(tmp_path / "quest_button.png"))
    win.scenario_builder.update_action_block(action)
    assert card.lbl_summary.text() == "Wait until quest_button.png appears · timeout 10 s"


def test_toggle_switch_focus_ring_is_two_pixels_and_not_clipped(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    first = QPushButton("First")
    switch = components.ToggleSwitch("Enabled")
    switch.setChecked(True)
    layout.addWidget(first)
    layout.addWidget(switch)
    host.show()
    qtbot.waitExposed(host)
    track = switch.track_rect()
    column = int(track.center().x())

    def ring_rows() -> List[bool]:
        """For each pixel row above the track (at its centre): is it the ring colour?"""
        image = switch.grab().toImage()
        target = QColor(C.accent_text)
        return [sum(abs(a - b) for a, b in zip(image.pixelColor(column, y).getRgb()[:3], target.getRgb()[:3])) <= 30
                for y in range(int(track.top()))]

    assert not any(ring_rows())
    _focus(switch, qtbot)
    # A 2 px ring inside the widget, then a 1 px gap before the track (like the QSS rings).
    assert ring_rows() == [True, True, False]
