"""Phase-2 tests for the redesigned property inspector (header, sections, segmented
controls, template preview card, base folder of relative template paths)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from action_macro.core.models import ActionBlock, ActionType, ImageWaitParams, ScenarioModel
from action_macro.core.persistence import ScenarioPersistence
from action_macro.gui.main_window import MainWindow
from action_macro.gui.property_inspector import PropertyInspector


def _write_png(path: Path, width: int = 40, height: int = 20) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#7C3AED"))
    assert image.save(str(path))
    return path


def _image_scenario(template_path: str) -> ScenarioModel:
    return ScenarioModel(
        name="Quest",
        actions=[ActionBlock(id="img", type=ActionType.IMAGE_WAIT,
                             params=ImageWaitParams(template_path=template_path))],
    )


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
# Bug fix: relative template paths resolve against the scenario file's folder
# -----------------------------------------------------------------------------

def test_opened_scenario_previews_its_relative_template(window, tmp_path, monkeypatch):
    """A saved scenario stores the template relative to the scenario file; the
    inspector must find it there, not in the current working directory."""
    project = tmp_path / "project"
    template = _write_png(project / "templates" / "btn.png")
    scenario_file = project / "quest.json"
    ScenarioPersistence.save(_image_scenario(str(template)), scenario_file)
    stored = json.loads(scenario_file.read_text(encoding="utf-8"))
    assert stored["actions"][0]["params"]["template_path"] == "templates/btn.png"  # saving is unchanged

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    win = window()
    assert win.open_scenario(str(scenario_file))
    win.scenario_builder.select_action("img")
    form = win.property_inspector.form_image
    assert win.property_inspector.stacked_widget.currentWidget() is form
    assert form.edit_path.text() == "templates/btn.png"
    preview = form.lbl_preview.pixmap()
    assert preview is not None and not preview.isNull()


def test_inspector_base_dir_matches_engine_resolution(qtbot, tmp_path, monkeypatch):
    from action_macro.core.persistence import resolve_template_path

    project = tmp_path / "project"
    template = _write_png(project / "templates" / "btn.png", 64, 32)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    inspector.set_base_dir(project)
    act = ActionBlock(id="img", type=ActionType.IMAGE_WAIT,
                      params=ImageWaitParams(template_path="templates/btn.png"))
    inspector.set_action(act)
    form = inspector.form_image

    # Same file the engine opens (WaitSystem.wait_image with base_dir=<scenario folder>).
    assert form.resolved_template_path() == resolve_template_path("templates/btn.png", project)
    assert form.resolved_template_path() == template.resolve()
    assert form.preview_state() == "found"
    assert not form.lbl_preview.pixmap().isNull()

    # Without a scenario folder (new scenario) the engine and the preview use the cwd.
    inspector.set_base_dir(None)
    assert form.preview_state() == "missing"
    assert form.resolved_template_path() == (elsewhere / "templates" / "btn.png").resolve()
    # The missing state shows the path it looked for.
    assert str((elsewhere / "templates" / "btn.png").resolve()) in form.lbl_missing_path.toolTip()

    # Switching the folder back refreshes the visible step at once.
    inspector.set_base_dir(project)
    assert form.preview_state() == "found"
    assert inspector.base_dir() == project.resolve()


def test_main_window_tells_inspector_the_scenario_folder(window, tmp_path, monkeypatch):
    project = tmp_path / "project"
    template = _write_png(project / "templates" / "btn.png")
    scenario_file = project / "quest.json"
    ScenarioPersistence.save(_image_scenario(str(template)), scenario_file)
    monkeypatch.chdir(tmp_path)

    win = window()
    inspector = win.property_inspector
    assert inspector.base_dir() is None

    assert win.open_scenario(str(scenario_file))
    assert inspector.base_dir() == project.resolve()

    # Save As into another folder: relative paths now resolve there (as the engine will).
    other = tmp_path / "other"
    other.mkdir()
    target = other / "copy.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    assert win.save_scenario_as()
    assert inspector.base_dir() == other.resolve()

    # Plain Save keeps (re-confirms) the folder of the current file.
    inspector.set_base_dir(None)
    assert win.save_scenario()
    assert inspector.base_dir() == other.resolve()

    # A new scenario has no file, so relative paths fall back to the cwd.
    win.new_scenario()
    assert inspector.base_dir() is None


# -----------------------------------------------------------------------------
# Header: "STEP n" + type
# -----------------------------------------------------------------------------

def _all_steps():
    from action_macro.core.models import FixedWaitParams, KeyInputParams, MouseClickParams

    return [
        ActionBlock(id="c", type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=10, y=20)),
        ActionBlock(id="k", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["ctrl", "c"])),
        ActionBlock(id="w", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=1500)),
        ActionBlock(id="i", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="t.png")),
    ]


def test_header_shows_step_number_and_type(qtbot):
    from action_macro.gui import theme

    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    for position, act in enumerate(_all_steps(), start=1):
        inspector.set_action(act, index=position)
        meta = theme.STEP_TYPES[act.type]
        assert inspector.lbl_step.text() == f"STEP {position}"
        assert inspector.step_index() == position
        assert inspector.lbl_type.text() == meta.label
        assert not inspector.lbl_type_icon.pixmap().isNull()  # type icon in a tinted circle

    # Backward compatible: set_action(action) without an index shows no number ...
    inspector.set_action(_all_steps()[0])
    assert inspector.lbl_step.text() == "STEP"
    assert inspector.lbl_type.text() == "Mouse click"
    # ... and set_step_index() fills it in later.
    inspector.set_step_index(7)
    assert inspector.lbl_step.text() == "STEP 7"
    assert inspector.edit_description.placeholderText() == "Describe this step (optional)"
    # The enable control is the step card's switch with the label "Enabled" (one control
    # for one flag; it is still a QCheckBox).
    assert inspector.chk_enabled.text() == "Enabled"


def test_main_window_passes_the_selected_step_number(window):
    win = window(scenario=ScenarioModel(name="Steps", actions=_all_steps()))
    inspector = win.property_inspector
    win.scenario_builder.select_action("w")
    assert inspector.lbl_step.text() == "STEP 3"
    assert inspector.lbl_type.text() == "Wait"

    win.scenario_builder.move_action_up("w")  # the number follows the new position
    assert inspector.current_action.id == "w"
    assert inspector.lbl_step.text() == "STEP 2"

    win.scenario_builder.select_action("i")
    assert inspector.lbl_step.text() == "STEP 4"


# -----------------------------------------------------------------------------
# Segmented controls mirror the (hidden) combos both ways
# -----------------------------------------------------------------------------

def _segmented_pairs(inspector):
    return [
        (inspector.form_mouse, inspector.form_mouse.combo_button, inspector.form_mouse.seg_button,
         {"left": "Left", "right": "Right", "middle": "Middle"}),
        (inspector.form_key, inspector.form_key.combo_action, inspector.form_key.seg_action,
         {"press": "Press", "down": "Hold down", "up": "Release"}),
        (inspector.form_image, inspector.form_image.combo_mode, inspector.form_image.seg_mode,
         {"appear": "Appears", "disappear": "Disappears"}),
        (inspector.form_image, inspector.form_image.combo_timeout_action, inspector.form_image.seg_timeout,
         {"raise": "Stop with error", "skip": "Skip and continue"}),
    ]


def test_segmented_controls_mirror_their_combos(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    inspector.resize(340, 900)
    inspector.show()
    steps = {act.type: act for act in _all_steps()}
    form_step = {inspector.form_mouse: steps[ActionType.MOUSE_CLICK],
                 inspector.form_key: steps[ActionType.KEY_INPUT],
                 inspector.form_image: steps[ActionType.IMAGE_WAIT]}

    for form, combo, seg, labels in _segmented_pairs(inspector):
        inspector.set_action(form_step[form])
        assert combo.isHidden()                       # the combo stays, hidden, as the source of truth
        assert seg.isVisible()
        assert seg.values() == [combo.itemText(i) for i in range(combo.count())]
        for value, label in labels.items():
            assert seg.button(value).text() == label
            assert seg.button(value).toolTip()          # every segment explains itself
        assert seg.value() == combo.currentText()

        # combo -> segments (code and tests keep using the combo)
        for index in range(combo.count()):
            combo.setCurrentIndex(index)
            assert seg.value() == combo.itemText(index)
            assert seg.button(combo.itemText(index)).isChecked()
        combo.setCurrentText(combo.itemText(0))
        assert seg.value() == combo.itemText(0)

        # segments -> combo -> model (a click commits like a combo change)
        last = combo.itemText(combo.count() - 1)
        with qtbot.waitSignal(inspector.action_modified):
            seg.button(last).click()
        assert combo.currentText() == last
        assert sum(seg.button(v).isChecked() for v in seg.values()) == 1


def test_segment_clicks_update_the_step_parameters(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    steps = _all_steps()

    inspector.set_action(steps[0])
    inspector.form_mouse.seg_button.button("middle").click()
    assert steps[0].params.button == "middle"

    inspector.set_action(steps[1])
    inspector.form_key.seg_action.button("down").click()
    assert steps[1].params.action == "down"

    inspector.set_action(steps[3])
    inspector.form_image.seg_mode.button("disappear").click()
    inspector.form_image.seg_timeout.button("skip").click()
    assert steps[3].params.mode == "disappear"
    assert steps[3].params.timeout_action == "skip"

    # Loading another step moves the segments with the combos.
    other = ActionBlock(id="i2", type=ActionType.IMAGE_WAIT,
                        params=ImageWaitParams(template_path="t.png", mode="appear", timeout_action="raise"))
    inspector.set_action(other)
    assert inspector.form_image.seg_mode.value() == "appear"
    assert inspector.form_image.seg_timeout.value() == "raise"


def test_segmented_control_standalone():
    from action_macro.gui.components import SegmentedControl

    seg = SegmentedControl([("a", "A"), ("b", "B", "tip")])
    seen = []
    seg.value_changed.connect(seen.append)
    assert seg.value() is None
    seg.set_value("b")
    seg.set_value("b")  # no change, no signal
    seg.button("a").click()
    assert seen == ["b", "a"]
    seg.set_value("zzz")  # unknown value clears the selection
    assert seg.value() is None and not any(seg.button(v).isChecked() for v in seg.values())
    assert seg.button("b").toolTip() == "tip"
    assert seg.button("nope") is None


# -----------------------------------------------------------------------------
# Fields, help texts, units
# -----------------------------------------------------------------------------

def test_mouse_form_help_pick_button_and_units(qtbot):
    from action_macro.gui.components import shortcut_text

    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    inspector.set_action(_all_steps()[0])
    form = inspector.form_mouse
    assert form.lbl_coord_help.text() == (
        "Screen position in logical points; negative values reach monitors left of or above the main one."
    )
    assert form.btn_pick.text() == "Pick from screen"
    assert not form.btn_pick.icon().isNull()
    assert shortcut_text("Ctrl+P") in form.btn_pick.toolTip()
    assert form.spin_interval.suffix() == " ms"
    assert form.spin_duration.suffix() == " ms"
    assert form.spin_x.minimum() < 0  # negative coordinates stay reachable


def test_key_form_chord_preview_and_duration_dimming(qtbot):
    from action_macro.core.key_mapping import format_key_combo

    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    inspector.resize(340, 900)
    inspector.show()
    act = _all_steps()[1]
    inspector.set_action(act)
    form = inspector.form_key
    assert form.lbl_keys_help.text() == (
        "Join keys with + to press them together, e.g. ctrl+c or cmd_or_ctrl+shift+t."
    )
    assert form.lbl_human.text() == format_key_combo(form.get_parsed_keys())
    assert form.lbl_human.isVisible()
    assert form.spin_duration.suffix() == " ms"
    assert form.spin_duration.isEnabled()

    form.edit_keys.setText("")
    assert not form.lbl_human.isVisible()  # nothing to format
    assert form.edit_keys.property("state") == "invalid"
    assert inspector.lbl_status.property("state") == "invalid"

    form.edit_keys.setText("alt+f4")
    assert form.edit_keys.property("state") != "invalid"
    assert form.lbl_human.text() == format_key_combo(["alt", "f4"])

    # The press duration only applies to Press: dimmed for Hold down / Release.
    form.combo_action.setCurrentText("down")
    assert not form.spin_duration.isEnabled() and not form.lbl_duration.isEnabled()
    form.seg_action.button("press").click()
    assert form.spin_duration.isEnabled()

    # Modifier chips are still check boxes; the helper still builds the chord.
    for chk in (form.chk_ctrl, form.chk_alt, form.chk_shift):
        assert chk.property("variant") == "chip"
    form.chk_shift.setChecked(True)
    form.combo_preset_keys.setCurrentText("f5")
    form.btn_insert.click()
    assert act.params.keys[-2:] == ["shift", "f5"]


def test_wait_form_readout_and_presets(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    act = _all_steps()[2]
    inspector.set_action(act)
    form = inspector.form_wait
    assert form.spin_duration.suffix() == " ms"
    assert form.lbl_human.text() == "1.5 s"  # the step card's wording
    form.preset_buttons[-1].click()
    assert act.params.duration_ms == 5000
    assert form.slider.value() == 5000


def test_image_confidence_slider_and_percent_spin(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    act = ActionBlock(id="i", type=ActionType.IMAGE_WAIT,
                      params=ImageWaitParams(template_path="t.png", confidence=0.85))
    inspector.set_action(act)
    form = inspector.form_image
    assert form.spin_conf.value() == pytest.approx(0.85)
    assert form.spin_conf.text() == "85%"  # no space before the unit, like the rest of the UI
    assert form.slider_conf.value() == 85
    form.slider_conf.setValue(70)
    assert form.spin_conf.value() == pytest.approx(0.70)
    assert act.params.confidence == pytest.approx(0.70)
    form.spin_conf.setValue(0.95)
    assert form.slider_conf.value() == 95
    assert form.spin_timeout.suffix() == " s"
    assert form.lbl_conf_help.text()


def test_roi_toggle_dims_fields_and_resets_for_steps_without_roi(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    with_roi = ActionBlock(id="r", type=ActionType.IMAGE_WAIT,
                           params=ImageWaitParams(template_path="t.png", roi=(-1920, 0, 640, 360)))
    inspector.set_action(with_roi)
    form = inspector.form_image
    spins = (form.spin_rx, form.spin_ry, form.spin_rw, form.spin_rh)
    assert form.chk_roi.isChecked()
    assert all(s.isEnabled() for s in spins) and all(lbl.isEnabled() for lbl in form._roi_labels)

    form.chk_roi.setChecked(False)
    assert not any(s.isEnabled() for s in spins) and not any(lbl.isEnabled() for lbl in form._roi_labels)
    assert with_roi.params.roi is None

    # A step without a region shows the defaults, not the previous step's region.
    inspector.set_action(ActionBlock(id="n", type=ActionType.IMAGE_WAIT,
                                     params=ImageWaitParams(template_path="t.png")))
    assert [s.value() for s in spins] == [0, 0, 100, 100]
    assert form.lbl_roi_help.text()


# -----------------------------------------------------------------------------
# Template preview card
# -----------------------------------------------------------------------------

def test_template_card_states(qtbot, tmp_path):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    inspector.set_base_dir(tmp_path)
    _write_png(tmp_path / "shot.png", 40, 20)
    (tmp_path / "notes.png").write_text("not an image", encoding="utf-8")
    act = ActionBlock(id="i", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="shot.png"))
    inspector.set_action(act)
    form = inspector.form_image

    assert form.preview_state() == "found"
    assert form.card.property("state") == "found"
    assert form.lbl_template_name.text() == "shot.png"
    assert form.lbl_template_meta.text() == "40 × 20 px"
    assert not form.lbl_preview.pixmap().isNull()
    assert form.lbl_missing_path.isHidden()

    form.edit_path.setText("gone.png")
    assert form.preview_state() == "missing"
    assert form.card.property("state") == "missing"
    assert form.lbl_template_name.text() == "Image not found"
    assert not form.lbl_missing_path.isHidden()
    looked_for = str((tmp_path / "gone.png").resolve())
    assert looked_for in form.lbl_missing_path.toolTip()
    assert form.lbl_missing_path.text() == looked_for  # one middle-elided line, full text kept
    assert form.lbl_preview.pixmap().isNull()

    form.edit_path.setText("notes.png")
    assert form.preview_state() == "invalid"
    assert form.lbl_template_name.text() == "Can't read this image"

    form.edit_path.setText("")
    assert form.preview_state() == "empty"
    assert form.card.property("state") == "empty"  # dashed drop-zone look
    assert form.lbl_template_name.text() == "No template image"
    assert form.btn_browse.isVisibleTo(form)
    assert act.params.template_path == "notes.png"  # an empty path is not committed


def test_dropping_an_image_on_the_card_sets_the_path(qtbot, tmp_path):
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtCore import Qt as QtCore_Qt
    from PySide6.QtGui import QDragEnterEvent, QDropEvent

    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    act = ActionBlock(id="i", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="t.png"))
    inspector.set_action(act)
    card = inspector.form_image.card
    image = _write_png(tmp_path / "drop.png")

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(image))])
    enter = QDragEnterEvent(QPointF(5, 5), QtCore_Qt.DropAction.CopyAction, mime,
                            QtCore_Qt.MouseButton.LeftButton, QtCore_Qt.KeyboardModifier.NoModifier)
    card.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(5, 5), QtCore_Qt.DropAction.CopyAction, mime,
                      QtCore_Qt.MouseButton.LeftButton, QtCore_Qt.KeyboardModifier.NoModifier)
    card.dropEvent(drop)
    # Qt gives the dropped path with forward slashes, also on Windows (C:/Users/...).
    assert Path(inspector.form_image.edit_path.text()) == image
    assert Path(act.params.template_path) == image
    assert inspector.form_image.preview_state() == "found"

    text_only = QMimeData()
    text_only.setUrls([QUrl.fromLocalFile(str(tmp_path / "notes.txt"))])
    enter = QDragEnterEvent(QPointF(5, 5), QtCore_Qt.DropAction.CopyAction, text_only,
                            QtCore_Qt.MouseButton.LeftButton, QtCore_Qt.KeyboardModifier.NoModifier)
    enter.setAccepted(False)
    card.dragEnterEvent(enter)
    assert not enter.isAccepted()  # only image files are accepted


# -----------------------------------------------------------------------------
# Layout: fits the panel, no emoji
# -----------------------------------------------------------------------------

def _long_steps(tmp_path: Path):
    from action_macro.core.models import MouseClickParams

    long_name = tmp_path / ("a_very_long_template_file_name_without_any_spaces_" * 2 + ".png")
    _write_png(long_name)
    deep = tmp_path / ("x" * 60) / ("y" * 60) / "missing_template.png"
    return [
        ActionBlock(id="c", type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=-99999, y=-99999)),
        _all_steps()[1],
        _all_steps()[2],
        ActionBlock(id="i1", type=ActionType.IMAGE_WAIT,
                    params=ImageWaitParams(template_path=str(long_name), roi=(-99999, -99999, 99999, 99999))),
        ActionBlock(id="i2", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path=str(deep))),
    ]


@pytest.mark.parametrize("styled", [True, False], ids=["styled", "unstyled"])
@pytest.mark.parametrize("size", [(1100, 680), (1400, 900)])
def test_inspector_fits_the_panel_without_sideways_overflow(window, qtbot, tmp_path, size, styled, request):
    from PySide6.QtWidgets import QApplication

    from action_macro.gui import theme

    if not styled:
        # Earlier modules (create_app, the cards module) may have themed the whole
        # application; clear it for this case so "unstyled" really runs unstyled.
        app = QApplication.instance()
        saved_sheet = app.styleSheet()
        app.setStyleSheet("")
        request.addfinalizer(lambda: app.setStyleSheet(saved_sheet))
    steps = _long_steps(tmp_path)
    win = window(scenario=ScenarioModel(name="Fit", actions=steps))
    if styled:  # the app style sheet, scoped to this window so it cannot leak into other tests
        win.setStyleSheet(theme.build_stylesheet())
        win.setFont(theme.ui_font(theme.FONT_BODY))
    win.resize(*size)
    win.show()
    qtbot.waitExposed(win)
    scroll = win.inspector_scroll
    inspector = win.property_inspector
    for act in steps:
        win.scenario_builder.select_action(act.id)
        qtbot.wait(1)
        viewport = scroll.viewport().width()
        assert inspector.width() <= viewport, act.id  # nothing pushes the panel wider
        page = inspector.stacked_widget.currentWidget()
        right = inspector.content.mapTo(inspector, inspector.content.contentsRect().topRight()).x()
        for child in page.findChildren(QWidget):
            if not child.isVisibleTo(page) or child.width() == 0:
                continue
            child_right = child.mapTo(inspector, child.rect().topRight()).x()
            assert child_right <= right + 1, (act.id, child.objectName() or type(child).__name__)


def test_inspector_texts_have_no_emoji(qtbot):
    from PySide6.QtWidgets import QAbstractButton, QLabel

    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    for act in _all_steps():
        inspector.set_action(act)
        for widget in inspector.findChildren(QLabel) + inspector.findChildren(QAbstractButton):
            text = widget.text()
            assert "️" not in text
            assert all(ord(ch) < 0x1F000 for ch in text), text
