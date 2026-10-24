"""Tests for the redesigned step list: sentence summaries, step cards, menus and the empty state."""

from __future__ import annotations

from typing import List

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QDrag, QEnterEvent, QKeyEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QApplication, QCheckBox, QLabel, QMessageBox, QPushButton, QWidgetAction

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
from action_macro.gui import step_text, theme
from action_macro.gui.components import ElidedLabel, EmptyState
from action_macro.gui.main_window import MainWindow
from action_macro.gui.scenario_builder import (
    DUPLICATE_SHORTCUT,
    STEP_TYPE_ORDER,
    ActionCardWidget,
    ScenarioBuilder,
    StepsEmptyState,
    StepTypeOption,
    StepTypeTile,
    ToggleSwitch,
)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _click(x=1280, y=720, button="left", clicks=1, interval_ms=0) -> MouseClickParams:
    return MouseClickParams(x=x, y=y, button=button, clicks=clicks, interval_ms=interval_ms)


def _block(action_type: ActionType, params, enabled: bool = True, description: str = "", id: str = None) -> ActionBlock:
    kwargs = {"type": action_type, "params": params, "enabled": enabled, "description": description}
    if id:
        kwargs["id"] = id
    return ActionBlock(**kwargs)


def _four_steps() -> ScenarioModel:
    return ScenarioModel(name="Daily quest", actions=[
        _block(ActionType.MOUSE_CLICK, _click(), id="s1"),
        _block(ActionType.KEY_INPUT, KeyInputParams(keys=["ctrl", "c"]), id="s2"),
        _block(ActionType.FIXED_WAIT, FixedWaitParams(duration_ms=1000), id="s3", enabled=False),
        _block(ActionType.IMAGE_WAIT, ImageWaitParams(template_path="templates/quest_button.png"), id="s4"),
    ])


def _builder(qtbot, scenario: ScenarioModel = None, show: bool = False) -> ScenarioBuilder:
    builder = ScenarioBuilder(scenario=scenario if scenario is not None else ScenarioModel(name="Test"))
    qtbot.addWidget(builder)
    if show:
        builder.resize(760, 640)
        builder.show()
        qtbot.waitExposed(builder)
    return builder


def _hover(widget, inside: bool = True) -> None:
    """Synthetic Enter/Leave events (no real pointer is moved)."""
    if inside:
        QApplication.sendEvent(widget, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    else:
        QApplication.sendEvent(widget, QEvent(QEvent.Type.Leave))


def _ids(builder: ScenarioBuilder) -> List[str]:
    return [a.id for a in builder.scenario.actions]


@pytest.fixture(scope="module", autouse=True)
def themed_app(qapp):
    """Style the application like the shipped app (``create_app`` applies the same theme).

    Sizes depend on the style sheet (scroll bar width, button minimum heights), so the
    layout checks below run against the real look. The full suite is already themed at
    this point (``test_gui_main_window`` calls ``create_app``); this keeps the module
    meaningful when it runs on its own. The previous style sheet, palette and font are
    restored afterwards, so the theme does not leak into later modules.
    """
    saved_sheet, saved_palette, saved_font = qapp.styleSheet(), qapp.palette(), qapp.font()
    theme.apply_theme(qapp)
    yield qapp
    qapp.setStyleSheet(saved_sheet)
    qapp.setPalette(saved_palette)
    qapp.setFont(saved_font)


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


# -----------------------------------------------------------------------------
# step_text: human sentences
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(("params", "expected"), [
    (_click(), "Click left at (1280, 720)"),
    (_click(button="right"), "Click right at (1280, 720)"),
    (_click(clicks=2), "Double-click left at (1280, 720)"),
    (_click(clicks=2, interval_ms=80), "Double-click left at (1280, 720), 80 ms apart"),
    (_click(button="right", clicks=3, interval_ms=100), "Click right 3 times at (1280, 720), 100 ms apart"),
    (_click(clicks=5), "Click left 5 times at (1280, 720)"),
    (_click(x=-1200, y=-300, button="middle"), "Click middle at (-1200, -300)"),
])
def test_click_sentences(params, expected):
    assert step_text.step_summary(_block(ActionType.MOUSE_CLICK, params)) == expected


# Keys read like the inspector's shortcut chip (format_key_combo: "⌘ + C" on macOS,
# "Ctrl + C" elsewhere), never as the internal tokens typed into the Keys field.
@pytest.mark.parametrize(("params", "expected"), [
    (KeyInputParams(keys=["ctrl", "c"]), "Press " + format_key_combo(["ctrl", "c"])),
    (KeyInputParams(keys=["c"], modifiers=["cmd_or_ctrl"]), "Press " + format_key_combo(["cmd_or_ctrl", "c"])),
    # build_key_chord drops the repeated modifier
    (KeyInputParams(keys=["ctrl", "v"], modifiers=["ctrl"]), "Press " + format_key_combo(["ctrl", "v"])),
    (KeyInputParams(keys=["shift"], action="down"), "Hold down " + format_key_combo(["shift"])),
    (KeyInputParams(keys=["shift"], action="up"), "Release " + format_key_combo(["shift"])),
    (KeyInputParams(keys=["enter"]), "Press " + format_key_combo(["enter"])),
])
def test_key_sentences_use_the_full_chord(params, expected):
    assert step_text.step_summary(_block(ActionType.KEY_INPUT, params)) == expected


@pytest.mark.parametrize(("platform", "expected"), [
    ("darwin", "Press ⌘ + ⇧ + T"),
    ("win32", "Press Ctrl + Shift + T"),
    ("linux", "Press Ctrl + Shift + T"),
])
def test_key_sentences_read_naturally_on_each_platform(monkeypatch, platform, expected):
    monkeypatch.setattr("action_macro.core.key_mapping.sys.platform", platform)
    block = _block(ActionType.KEY_INPUT, KeyInputParams(keys=["cmd_or_ctrl", "shift", "t"]))
    assert step_text.step_summary(block) == expected


@pytest.mark.parametrize(("duration_ms", "expected"), [
    (0, "Wait 0 ms"),
    (250, "Wait 250 ms"),
    (1000, "Wait 1.0 s"),
    (1250, "Wait 1.25 s"),
    (3500, "Wait 3.5 s"),
    (59_990, "Wait 59.99 s"),
    (60_000, "Wait 1 min"),
    (90_500, "Wait 1 min 30.5 s"),
    (123_456, "Wait 2 min 3.46 s"),
])
def test_wait_sentences(duration_ms, expected):
    assert step_text.step_summary(_block(ActionType.FIXED_WAIT, FixedWaitParams(duration_ms=duration_ms))) == expected


@pytest.mark.parametrize(("params", "expected"), [
    (ImageWaitParams(template_path="templates/quest_button.png"),
     "Wait until quest_button.png appears · timeout 10 s"),
    (ImageWaitParams(template_path="/abs/dir/loading.png", mode="disappear", timeout_seconds=2.5),
     "Wait until loading.png disappears · timeout 2.5 s"),
    (ImageWaitParams(template_path="C:\\shots\\ok.png", timeout_seconds=5.0, timeout_action="skip"),
     "Wait until ok.png appears · skip after 5 s"),
])
def test_image_wait_sentences(params, expected):
    assert step_text.step_summary(_block(ActionType.IMAGE_WAIT, params)) == expected


def test_summaries_accept_plain_dict_params_and_missing_templates():
    assert step_text.image_wait_summary({"template_path": "", "mode": "appear", "timeout_seconds": 10.0}) == \
        "Wait until an image appears · timeout 10 s"
    assert step_text.click_summary({"x": 1, "y": 2}) == "Click left at (1, 2)"
    assert step_text.key_summary({"keys": []}) == "No key set"


def test_titles_prefer_the_description_and_fall_back_to_the_type_label():
    for action_type, params in (
        (ActionType.MOUSE_CLICK, _click()),
        (ActionType.KEY_INPUT, KeyInputParams(keys=["a"])),
        (ActionType.FIXED_WAIT, FixedWaitParams(duration_ms=5)),
        (ActionType.IMAGE_WAIT, ImageWaitParams(template_path="a.png")),
    ):
        assert step_text.step_title(_block(action_type, params)) == theme.STEP_TYPES[action_type].label
        assert step_text.step_title(_block(action_type, params, description="  Open the map ")) == "Open the map"


def test_switched_off_step_summary_is_prefixed_with_skipped():
    block = _block(ActionType.FIXED_WAIT, FixedWaitParams(duration_ms=1000), enabled=False)
    assert step_text.card_summary(block) == "Disabled · Wait 1.0 s"
    block.enabled = True
    assert step_text.card_summary(block) == "Wait 1.0 s"


# -----------------------------------------------------------------------------
# Card layout and content
# -----------------------------------------------------------------------------

def test_card_shows_index_type_icon_title_and_sentence(qtbot):
    builder = _builder(qtbot, _four_steps())
    for index, (action_id, action_type) in enumerate(
            (("s1", ActionType.MOUSE_CLICK), ("s2", ActionType.KEY_INPUT),
             ("s3", ActionType.FIXED_WAIT), ("s4", ActionType.IMAGE_WAIT))):
        card = builder._card_map[action_id]
        meta = theme.STEP_TYPES[action_type]
        assert card.lbl_index.text() == f"#{index + 1}"
        assert (card.badge.icon_name(), card.badge.tint()) == (meta.icon, meta.tint)
        assert card.lbl_title.text() == meta.label
        assert isinstance(card.lbl_title, ElidedLabel) and isinstance(card.lbl_summary, ElidedLabel)
    assert builder._card_map["s1"].lbl_summary.text() == "Click left at (1280, 720)"
    assert builder._card_map["s2"].lbl_summary.text() == "Press " + format_key_combo(["ctrl", "c"])
    assert builder._card_map["s4"].lbl_summary.text() == "Wait until quest_button.png appears · timeout 10 s"


def test_card_height_and_rows_follow_the_design(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    lw = builder.list_widget
    first, second = (builder._card_map[i] for i in ("s1", "s2"))
    assert first.visual_rect().height() == ActionCardWidget.CARD_HEIGHT == 56
    assert lw.visualItemRect(lw.item(0)).height() == first.height() == first.total_height()
    # Visible gap between cards = the two insets.
    gap = (second.geometry().top() + second.visual_rect().top()) - (first.geometry().top() + first.visual_rect().bottom())
    assert gap == pytest.approx(2 * ActionCardWidget.INSET, abs=1)


def test_no_emoji_or_symbol_glyphs_anywhere_in_the_step_list(qtbot):
    builder = _builder(qtbot, _four_steps())
    empty = _builder(qtbot)
    texts = []
    for root in (builder, empty, builder.add_step_menu):
        texts += [w.text() for w in root.findChildren(QLabel)]
        texts += [w.text() for w in root.findChildren(QAbstractButton)]
    for card in builder._card_map.values():
        texts += [a.text() for a in card.menu_more.actions()]
    assert texts
    # The key step's summary is the shortcut itself, written like the inspector's chip
    # (format_key_combo): on macOS that is the native notation (⌘ ⌃ ⌥ ⇧ ⏎ ...). Those text
    # glyphs are allowed there and only there; no other symbol or emoji anywhere.
    key_symbols = set("⌘⌃⌥⇧⏎⌫⌦⇥")
    key_summary = builder._card_map["s2"].lbl_summary.text()
    assert key_summary == "Press " + format_key_combo(["ctrl", "c"])
    offenders = [t for t in texts
                 if any(ord(ch) >= 0x2190 and not (t == key_summary and ch in key_symbols) for ch in t)]
    assert offenders == []
    assert not hasattr(ActionCardWidget, "TYPE_COLORS")


def test_elided_summary_keeps_full_text_in_tooltip(qtbot):
    long_params = ImageWaitParams(template_path="templates/a_really_long_template_name_for_testing.png")
    builder = _builder(qtbot, ScenarioModel(name="x", actions=[_block(ActionType.IMAGE_WAIT, long_params, id="i")]))
    builder.resize(380, 400)
    builder.show()
    qtbot.waitExposed(builder)
    card = builder._card_map["i"]
    full = "Wait until a_really_long_template_name_for_testing.png appears · timeout 10 s"
    assert card.lbl_summary.text() == full
    qtbot.waitUntil(card.lbl_summary.is_elided)
    assert card.lbl_summary.toolTip() == full
    assert builder.list_widget.horizontalScrollBar().maximum() == 0


# -----------------------------------------------------------------------------
# Enable switch and the dimmed (skipped) look
# -----------------------------------------------------------------------------

def test_enable_switch_is_a_checkbox_painted_as_a_switch(qtbot):
    switch = ToggleSwitch()
    qtbot.addWidget(switch)
    switch.resize(switch.sizeHint())
    assert isinstance(switch, QCheckBox)
    assert switch.hitButton(switch.rect().center())

    def pixel(x: float) -> str:
        image = switch.grab().toImage()
        return image.pixelColor(int(x), int(switch.track_rect().center().y())).name().upper()

    track = switch.track_rect()
    switch.setChecked(True)  # accent track, knob at the right end
    assert pixel(track.left() + 4) == theme.COLORS.accent.upper()
    assert pixel(switch.knob_rect().center().x()) == theme.COLORS.on_accent.upper()
    switch.setChecked(False)  # neutral track, knob at the left end
    assert pixel(track.right() - 4) == theme.COLORS.border.upper()
    assert switch.knob_rect().center().x() < track.center().x()


def test_switching_a_step_off_dims_the_card_and_marks_it_skipped(qtbot):
    builder = _builder(qtbot, _four_steps())
    card = builder._card_map["s1"]
    assert card.lbl_summary.property("role") == "muted" and not card.badge.is_faded()

    with qtbot.waitSignal(builder.scenario_changed):
        card.chk_enabled.click()
    assert builder.scenario.get_action("s1").enabled is False
    assert card.lbl_summary.text() == "Disabled · Click left at (1280, 720)"
    assert card.lbl_title.property("role") == "muted"
    # Summary and number stay text-muted: text-faint is below WCAG AA (4.5:1) for this
    # informative text; the "Disabled ·" prefix, muted title, faded icon and darker card
    # carry the switched-off look.
    assert card.lbl_summary.property("role") == "muted"
    assert card.lbl_index.property("role") == "muted"
    assert card.badge.is_faded()
    assert card.background_color() == theme.COLORS.surface

    card.chk_enabled.click()
    assert card.lbl_summary.text() == "Click left at (1280, 720)"
    assert card.lbl_title.property("role") == "" and not card.badge.is_faded()


def test_loaded_disabled_step_is_dimmed_from_the_start(qtbot):
    builder = _builder(qtbot, _four_steps())
    card = builder._card_map["s3"]
    assert not card.chk_enabled.isChecked()
    assert card.lbl_summary.text() == "Disabled · Wait 1.0 s"
    assert card.badge.is_faded()


# -----------------------------------------------------------------------------
# States: hover, selected, running
# -----------------------------------------------------------------------------

def test_reorder_chevrons_are_quiet_until_hover_or_selection_but_always_clickable(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    builder.select_action("s1")
    first, second = builder._card_map["s1"], builder._card_map["s2"]
    assert first.btn_up.isVisible() and first.btn_down.isVisible()  # selected
    assert second.btn_up.isHidden() and second.btn_down.isHidden()

    _hover(second)
    assert second.is_hovered and second.btn_up.isVisible()
    assert second.border_color() == theme.COLORS.border_strong
    assert second.background_color() == theme.COLORS.surface_3
    _hover(second, inside=False)
    assert second.btn_up.isHidden() and second.border_color() == theme.COLORS.border

    # Hidden chevrons keep their place (no layout jump) and still work with click().
    assert second.btn_up.sizePolicy().retainSizeWhenHidden()
    with qtbot.waitSignal(builder.scenario_changed):
        second.btn_up.click()
    assert _ids(builder)[:2] == ["s2", "s1"]


def test_selected_card_has_an_accent_border(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    builder.select_action("s2")
    card = builder._card_map["s2"]
    assert card.is_selected and card.border_color() == theme.COLORS.accent
    image = card.grab().toImage()
    top_mid = image.pixelColor(card.width() // 2, ActionCardWidget.INSET)
    assert top_mid.name().upper() == theme.COLORS.accent.upper()
    assert builder._card_map["s1"].border_color() == theme.COLORS.border


def test_running_card_has_accent_bar_glow_and_running_label(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    builder.highlight_active_action(1)
    card, idle = builder._card_map["s2"], builder._card_map["s1"]
    assert card.is_executing and card.lbl_status.isVisible() and card.lbl_status.text() == "Running"
    assert idle.lbl_status.isHidden()

    image = card.grab().toImage()
    mid_y = card.height() // 2
    inset = ActionCardWidget.INSET
    bar = image.pixelColor(inset + 1, mid_y)
    assert bar.name().upper() == theme.COLORS.accent.upper()
    # In the inset around the card only the glow paints: a violet tint over the background.
    glow = image.pixelColor(inset - 2, mid_y)
    background = idle.grab().toImage().pixelColor(inset - 2, mid_y)
    assert glow != background
    assert glow.blue() > glow.green() and background.blue() - background.green() < glow.blue() - glow.green()

    builder.highlight_active_action(-1)
    assert not card.is_executing and card.lbl_status.isHidden()


# -----------------------------------------------------------------------------
# Overflow menu, duplicate, keyboard
# -----------------------------------------------------------------------------

def test_overflow_menu_lists_actions_with_correct_enabled_states(qtbot):
    builder = _builder(qtbot, _four_steps())
    first, middle, last = (builder._card_map[i] for i in ("s1", "s2", "s4"))
    texts = [a.text() for a in first.menu_more.actions() if not a.isSeparator()]
    assert texts == ["Duplicate", "Move up", "Move down", "Delete"]
    assert (first.act_move_up.isEnabled(), first.act_move_down.isEnabled()) == (False, True)
    assert (middle.act_move_up.isEnabled(), middle.act_move_down.isEnabled()) == (True, True)
    assert (last.act_move_up.isEnabled(), last.act_move_down.isEnabled()) == (True, False)
    assert first.act_duplicate.isEnabled() and last.act_delete.isEnabled()
    assert first.act_duplicate.shortcut() == DUPLICATE_SHORTCUT

    single = _builder(qtbot, ScenarioModel(name="one", actions=[_block(ActionType.FIXED_WAIT, FixedWaitParams(duration_ms=5), id="w")]))
    only = single._card_map["w"]
    assert not only.act_move_up.isEnabled() and not only.act_move_down.isEnabled()


def test_overflow_button_selects_its_step_and_opens_the_menu(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    card = builder._card_map["s3"]
    card.btn_more.click()
    try:
        assert builder._selected_id == "s3"
        qtbot.waitUntil(card.menu_more.isVisible)
    finally:
        card.menu_more.hide()


def test_overflow_menu_actions_move_duplicate_and_delete(qtbot):
    builder = _builder(qtbot, _four_steps())
    builder._card_map["s1"].act_move_down.trigger()
    assert _ids(builder) == ["s2", "s1", "s3", "s4"]
    builder._card_map["s1"].act_move_up.trigger()
    assert _ids(builder) == ["s1", "s2", "s3", "s4"]

    builder._card_map["s2"].act_duplicate.trigger()
    assert len(builder.scenario.actions) == 5 and _ids(builder)[1] == "s2"
    copy_id = _ids(builder)[2]
    assert copy_id not in ("s1", "s2", "s3", "s4")

    builder._card_map["s4"].act_delete.trigger()
    assert "s4" not in _ids(builder) and len(builder.scenario.actions) == 4


def test_duplicate_action_inserts_an_independent_copy_after_the_source(qtbot):
    scenario = _four_steps()
    scenario.actions[1].description = "Copy the code"
    builder = _builder(qtbot, scenario)
    added, changed, model = [], [], []
    builder.action_added.connect(added.append)
    builder.scenario_changed.connect(lambda: changed.append(True))
    builder.model_changed.connect(lambda: model.append(True))

    clone = builder.duplicate_action("s2")
    source = builder.scenario.get_action("s2")
    assert clone is not None and clone.id != "s2"
    assert _ids(builder) == ["s1", "s2", clone.id, "s3", "s4"]
    assert (clone.type, clone.params, clone.enabled, clone.description) == (
        source.type, source.params, source.enabled, source.description)
    assert clone.params is not source.params
    clone.params.keys.append("x")
    assert source.params.keys == ["ctrl", "c"]
    assert builder._selected_id == clone.id and builder._card_map[clone.id].is_selected
    assert added == [clone] and changed and model
    assert builder.duplicate_action("nope") is None


def test_duplicate_marks_the_main_window_dirty(window):
    win = window(scenario=_four_steps())
    win.mark_dirty(False)
    assert not win.has_unsaved_changes()
    win.scenario_builder.duplicate_action("s1")
    assert win.has_unsaved_changes()
    assert len(win.scenario.actions) == 5
    assert win.lbl_step_count.text() == "5 steps"


@pytest.mark.parametrize("key", [Qt.Key.Key_Delete, Qt.Key.Key_Backspace])
def test_delete_keys_on_the_list_delete_the_selected_step(qtbot, key):
    builder = _builder(qtbot, _four_steps(), show=True)
    builder.select_action("s2")
    builder.list_widget.setFocus()
    with qtbot.waitSignal(builder.action_deleted) as blocker:
        QTest.keyClick(builder.list_widget, key)
    assert blocker.args == ["s2"]
    assert _ids(builder) == ["s1", "s3", "s4"]
    assert builder._selected_id == "s3"  # the step that took its place


def test_ctrl_d_duplicates_the_selected_step(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    builder.select_action("s4")
    assert DUPLICATE_SHORTCUT == QKeySequence("Ctrl+D")  # Cmd+D on macOS (native mapping)
    with qtbot.waitSignal(builder.action_added):
        QTest.keyClick(builder.list_widget, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert len(builder.scenario.actions) == 5
    assert builder.scenario.actions[4].type == ActionType.IMAGE_WAIT and _ids(builder)[3] == "s4"
    QTest.keyClick(builder.list_widget, Qt.Key.Key_D)  # plain "d" is not the shortcut
    assert len(builder.scenario.actions) == 5


def test_list_claims_its_keys_before_window_shortcuts(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    builder.select_action("s1")
    for key, mods in ((Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier),
                      (Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)):
        override = QKeyEvent(QEvent.Type.ShortcutOverride, key, mods)
        override.ignore()
        QApplication.sendEvent(builder.list_widget, override)
        assert override.isAccepted()
    other = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_F5, Qt.KeyboardModifier.NoModifier)
    other.ignore()
    QApplication.sendEvent(builder.list_widget, other)
    assert not other.isAccepted()  # F5 (Run) still reaches the window


def test_deleting_an_unselected_step_keeps_the_selection(qtbot):
    builder = _builder(qtbot, _four_steps())
    builder.select_action("s4")
    builder.delete_action("s1")
    assert builder._selected_id == "s4"
    builder.delete_action("s4")  # deleting the selected last step selects the new last one
    assert builder._selected_id == "s3"


# -----------------------------------------------------------------------------
# Adding steps: header menu, dashed row, empty-state tiles
# -----------------------------------------------------------------------------

def test_old_type_buttons_are_gone_and_add_step_controls_exist(qtbot):
    builder = _builder(qtbot, _four_steps())
    labels = [b.text() for b in builder.findChildren(QPushButton)]
    assert not [t for t in labels if t.startswith("+ ")]
    assert builder.btn_add_step.text() == "Add step" and not builder.btn_add_step.icon().isNull()
    assert builder.btn_add_step_inline.text() == "Add step"
    assert builder.lbl_section.text() == "STEPS"
    assert builder.lbl_count.text() == "4" and not builder.lbl_count.isHidden()


def test_add_step_menu_rows_show_icon_label_and_description(qtbot):
    builder = _builder(qtbot)
    actions = builder.add_step_menu.actions()
    assert list(builder.add_step_actions) == list(STEP_TYPE_ORDER)
    assert actions == [builder.add_step_actions[t] for t in STEP_TYPE_ORDER]
    for action_type, action in builder.add_step_actions.items():
        meta = theme.STEP_TYPES[action_type]
        assert isinstance(action, QWidgetAction)
        row = action.defaultWidget()
        assert isinstance(row, StepTypeOption)
        assert action.text() == row.lbl_label.text() == meta.label
        assert row.lbl_description.text() == meta.description
        assert (row.badge.icon_name(), row.badge.tint()) == (meta.icon, meta.tint)


def test_header_button_opens_the_add_step_menu(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    builder.btn_add_step.click()
    try:
        qtbot.waitUntil(builder.add_step_menu.isVisible)
        # The menu's active row paints its own highlight (widget actions get none from QMenu).
        row = builder.add_step_actions[ActionType.KEY_INPUT].defaultWidget()
        builder.add_step_menu.setActiveAction(builder.add_step_actions[ActionType.KEY_INPUT])
        assert row.is_highlighted()
    finally:
        builder.add_step_menu.hide()


def test_add_step_menu_entries_add_after_the_selected_step(qtbot):
    builder = _builder(qtbot, _four_steps())
    builder.select_action("s1")
    with qtbot.waitSignal(builder.scenario_changed):
        builder.add_step_actions[ActionType.FIXED_WAIT].trigger()
    new = builder.scenario.actions[1]
    assert new.type == ActionType.FIXED_WAIT and builder._selected_id == new.id
    assert builder.lbl_count.text() == "5"


def test_dashed_row_adds_at_the_end_and_sits_under_the_last_card(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    builder.select_action("s1")
    lw, row = builder.list_widget, builder.btn_add_step_inline
    gap = row.mapTo(builder, row.rect().topLeft()).y() - lw.mapTo(builder, lw.rect().bottomLeft()).y()
    assert 0 < gap <= ActionCardWidget.INSET + 1
    assert row.height() == 40

    row.click()
    try:
        qtbot.waitUntil(builder.add_step_menu.isVisible)
    finally:
        builder.add_step_menu.hide()
    builder.add_step_actions[ActionType.KEY_INPUT].trigger()
    assert builder.scenario.actions[-1].type == ActionType.KEY_INPUT
    assert _ids(builder)[0] == "s1" and len(builder.scenario.actions) == 5

    # The flag is one-shot: the next menu use adds after the selection again.
    builder.select_action("s1")
    builder.add_step_actions[ActionType.FIXED_WAIT].trigger()
    assert builder.scenario.actions[1].type == ActionType.FIXED_WAIT


def test_empty_state_offers_four_tiles_that_add_the_first_step(qtbot):
    builder = _builder(qtbot, show=True)
    empty = builder.lbl_empty
    assert isinstance(empty, StepsEmptyState) and isinstance(empty, EmptyState)
    assert empty.isVisible() and builder.list_widget.isHidden() and builder.lbl_count.isHidden()
    assert empty.title() == "Add your first step"
    assert empty.text() == "A scenario runs its steps from top to bottom."
    assert list(builder.add_step_tiles) == list(STEP_TYPE_ORDER)
    grid = empty.tiles_host.layout()
    for position, action_type in enumerate(STEP_TYPE_ORDER):
        tile = builder.add_step_tiles[action_type]
        assert isinstance(tile, StepTypeTile) and tile.isVisible()
        row, column, _, _ = grid.getItemPosition(grid.indexOf(tile))
        assert (row, column) == divmod(position, 2)
        assert tile.lbl_label.text() == theme.STEP_TYPES[action_type].label
        assert tile.lbl_description.text() == theme.STEP_TYPES[action_type].description

    with qtbot.waitSignal(builder.action_added):
        builder.add_step_tiles[ActionType.IMAGE_WAIT].click()
    assert [a.type for a in builder.scenario.actions] == [ActionType.IMAGE_WAIT]
    assert builder.lbl_empty.isHidden() and builder.list_widget.isVisible()
    builder.delete_action(builder.scenario.actions[0].id)
    assert builder.lbl_empty.isVisible() and builder.list_widget.isHidden()


@pytest.mark.parametrize("size", [(1100, 680), (1400, 900)])
def test_empty_state_tiles_fit_in_the_main_window(window, qtbot, size):
    win = window()
    win.resize(*size)
    win.show()
    qtbot.waitExposed(win)
    empty = win.scenario_builder.lbl_empty
    qtbot.waitUntil(lambda: empty.height() > 0)
    for tile in win.scenario_builder.add_step_tiles.values():
        bottom_right = tile.mapTo(empty, tile.rect().bottomRight())
        assert empty.rect().contains(bottom_right)
        assert tile.height() == StepTypeTile.TILE_HEIGHT


# -----------------------------------------------------------------------------
# Narrow window with many steps, drag & drop
# -----------------------------------------------------------------------------

def test_cards_stay_inside_the_viewport_when_the_list_scrolls(window, qtbot):
    """Many steps in a small window: the vertical scroll bar must not cover the cards' controls."""
    scenario = ScenarioModel(name="Long", actions=[
        _block(ActionType.FIXED_WAIT, FixedWaitParams(duration_ms=100 * (i + 1)), id=f"w{i}") for i in range(14)])
    win = window(scenario=scenario)
    win.resize(1100, 680)
    win.show()
    qtbot.waitExposed(win)
    lw = win.scenario_builder.list_widget
    qtbot.waitUntil(lambda: lw.verticalScrollBar().isVisible())
    assert lw.horizontalScrollBar().maximum() == 0
    for card in win.scenario_builder._card_map.values():
        assert card.width() <= lw.viewport().width()
        assert card.btn_more.geometry().right() <= card.visual_rect().right()
    # The "Add step" row stays visible below the list and lines up with the cards.
    row = win.scenario_builder.btn_add_step_inline
    assert row.isVisible()

    def misalignment() -> int:
        row_right = row.mapTo(win, row.rect().topRight()).x()
        card_right = lw.viewport().mapTo(win, lw.viewport().rect().topRight()).x() - ActionCardWidget.INSET
        return abs(row_right - card_right)

    qtbot.waitUntil(lambda: misalignment() <= 1)  # layouts apply on the next event-loop pass


def test_grip_handle_passes_presses_to_the_list(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    card = builder._card_map["s3"]
    assert card.lbl_grip.cursor().shape() == Qt.CursorShape.OpenHandCursor
    QTest.mousePress(card.lbl_grip, Qt.MouseButton.LeftButton)
    QTest.mouseRelease(card.lbl_grip, Qt.MouseButton.LeftButton)
    assert builder._selected_id == "s3"
    assert builder.list_widget.dragDropMode() == builder.list_widget.DragDropMode.InternalMove


def test_drag_uses_the_card_picture_and_leaves_rows_to_the_drop(qtbot, monkeypatch):
    builder = _builder(qtbot, _four_steps(), show=True)
    builder.select_action("s2")
    lw = builder.list_widget
    seen = {}

    def fake_run(drag: QDrag, actions):
        seen["pixmap"] = drag.pixmap()
        seen["mime"] = drag.mimeData()
        return Qt.DropAction.MoveAction

    monkeypatch.setattr(lw, "_run_drag", fake_run)
    lw.startDrag(Qt.DropAction.MoveAction)
    card = builder._card_map["s2"]
    assert seen["pixmap"].size() == card.grab().size()
    assert seen["mime"] is not None
    assert lw.count() == 4 and _ids(builder) == ["s1", "s2", "s3", "s4"]


def test_internal_move_reorders_the_model_and_keeps_cards(qtbot):
    builder = _builder(qtbot, _four_steps(), show=True)
    lw = builder.list_widget
    assert lw.model().moveRow(lw.rootIndex(), 0, lw.rootIndex(), 3)  # what an internal drop does
    lw.order_changed.emit()
    assert _ids(builder) == ["s2", "s3", "s1", "s4"]
    for row in range(lw.count()):
        card = lw.itemWidget(lw.item(row))
        assert card is builder._card_map[_ids(builder)[row]]
        assert card.lbl_index.text() == f"#{row + 1}"
