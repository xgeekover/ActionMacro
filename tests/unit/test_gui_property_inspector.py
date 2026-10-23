"""Unit tests for PropertyInspector and parameter forms."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
)
from action_macro.gui.property_inspector import (
    FixedWaitForm,
    ImageWaitForm,
    KeyInputForm,
    MouseClickForm,
    PropertyInspector,
)


def test_property_inspector_empty_state(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)

    inspector.set_action(None)
    assert inspector.common_frame.isHidden()
    assert inspector.stacked_widget.currentWidget() is inspector.page_empty
    assert inspector.current_action is None


def test_mouse_click_form_editing_and_coordinate_injection(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)

    act = ActionBlock(
        id="act-mouse",
        type=ActionType.MOUSE_CLICK,
        params=MouseClickParams(x=100, y=200, button="left", clicks=1, interval_ms=0),
        description="Click Action",
    )
    inspector.set_action(act)

    assert not inspector.common_frame.isHidden()
    assert inspector.stacked_widget.currentWidget() is inspector.form_mouse
    assert inspector.form_mouse.spin_x.value() == 100
    assert inspector.form_mouse.spin_y.value() == 200

    # Inject coordinates from picker
    with qtbot.waitSignal(inspector.action_modified) as blocker:
        inspector.set_coordinates(750, 420)
    assert blocker.args[0].params.x == 750
    assert blocker.args[0].params.y == 420
    assert inspector.form_mouse.spin_x.value() == 750
    assert inspector.form_mouse.spin_y.value() == 420

    # Modify button and clicks
    with qtbot.waitSignal(inspector.action_modified):
        inspector.form_mouse.combo_button.setCurrentText("right")
    assert act.params.button == "right"

    with qtbot.waitSignal(inspector.action_modified):
        inspector.form_mouse.spin_clicks.setValue(2)
    assert act.params.clicks == 2

    # Click pick coordinate button
    with qtbot.waitSignal(inspector.pick_coordinate_requested):
        inspector.form_mouse.btn_pick.click()


def test_key_input_form_editing_and_combo_helper(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)

    act = ActionBlock(
        id="act-key",
        type=ActionType.KEY_INPUT,
        params=KeyInputParams(keys=["enter"], action="press"),
        description="Key Action",
    )
    inspector.set_action(act)

    assert inspector.stacked_widget.currentWidget() is inspector.form_key
    assert inspector.form_key.edit_keys.text() == "enter"

    # Direct edit of combo
    with qtbot.waitSignal(inspector.action_modified):
        inspector.form_key.edit_keys.setText("ctrl+c")
    assert "cmd_or_ctrl" in act.params.keys or "ctrl" in act.params.keys
    assert "c" in act.params.keys

    # Combo helper usage
    inspector.form_key.chk_ctrl.setChecked(True)
    inspector.form_key.combo_preset_keys.setCurrentText("esc")
    with qtbot.waitSignal(inspector.action_modified):
        inspector.form_key._apply_combo_helper()
    assert "esc" in act.params.keys

    # Validation: empty keys
    inspector.form_key.edit_keys.setText("")
    assert "⚠" in inspector.lbl_status.text()
    # Ensure invalid empty string was not committed
    assert len(act.params.keys) > 0


def test_fixed_wait_form_sync_and_presets(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)

    act = ActionBlock(
        id="act-wait",
        type=ActionType.FIXED_WAIT,
        params=FixedWaitParams(duration_ms=1000),
        description="Wait Action",
    )
    inspector.set_action(act)

    assert inspector.stacked_widget.currentWidget() is inspector.form_wait
    assert inspector.form_wait.spin_duration.value() == 1000
    assert inspector.form_wait.slider.value() == 1000
    assert "1.00 seconds" in inspector.form_wait.lbl_human.text()

    # Changing spinbox updates slider and label
    with qtbot.waitSignal(inspector.action_modified):
        inspector.form_wait.spin_duration.setValue(2500)
    assert act.params.duration_ms == 2500
    assert inspector.form_wait.slider.value() == 2500
    assert "2.50 seconds" in inspector.form_wait.lbl_human.text()

    # Changing slider updates spinbox
    with qtbot.waitSignal(inspector.action_modified):
        inspector.form_wait.slider.setValue(500)
    assert act.params.duration_ms == 500
    assert inspector.form_wait.spin_duration.value() == 500


def test_image_wait_form_editing_validation_and_roi(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)

    act = ActionBlock(
        id="act-img",
        type=ActionType.IMAGE_WAIT,
        params=ImageWaitParams(
            template_path="templates/target.png",
            mode="appear",
            confidence=0.8,
            timeout_seconds=10.0,
            roi=None,
        ),
        description="Image Wait Action",
    )
    inspector.set_action(act)

    assert inspector.stacked_widget.currentWidget() is inspector.form_image
    assert inspector.form_image.edit_path.text() == "templates/target.png"
    assert not inspector.form_image.chk_roi.isChecked()

    # Validation: empty path must show warning and NOT corrupt params
    inspector.form_image.edit_path.setText("")
    assert "⚠" in inspector.lbl_status.text()
    assert act.params.template_path != ""

    # Restore valid path
    with qtbot.waitSignal(inspector.action_modified):
        inspector.form_image.edit_path.setText("templates/new_target.png")
    assert act.params.template_path == "templates/new_target.png"
    assert "✓" in inspector.lbl_status.text()

    # Enable ROI
    with qtbot.waitSignal(inspector.action_modified):
        inspector.form_image.chk_roi.setChecked(True)
        inspector.form_image.spin_rx.setValue(50)
        inspector.form_image.spin_ry.setValue(60)
        inspector.form_image.spin_rw.setValue(200)
        inspector.form_image.spin_rh.setValue(150)
    assert act.params.roi == (50, 60, 200, 150)

    # Disable ROI
    with qtbot.waitSignal(inspector.action_modified):
        inspector.form_image.chk_roi.setChecked(False)
    assert act.params.roi is None


def test_property_inspector_common_fields(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)

    act = ActionBlock(
        id="act-test",
        type=ActionType.FIXED_WAIT,
        params=FixedWaitParams(duration_ms=500),
        description="Initial Description",
        enabled=True,
    )
    inspector.set_action(act)

    assert inspector.edit_description.text() == "Initial Description"
    assert inspector.chk_enabled.isChecked()

    with qtbot.waitSignal(inspector.action_modified):
        inspector.edit_description.setText("Updated Description")
    assert act.description == "Updated Description"

    with qtbot.waitSignal(inspector.action_modified):
        inspector.chk_enabled.setChecked(False)
    assert act.enabled is False
