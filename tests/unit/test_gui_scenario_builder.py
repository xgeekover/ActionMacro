"""Unit tests for ScenarioBuilder and ActionCardWidget."""

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
    ScenarioModel,
)
from action_macro.gui.scenario_builder import (
    ActionCardWidget,
    ActionListWidget,
    ScenarioBuilder,
)


def test_scenario_builder_empty_initialization(qtbot):
    scenario = ScenarioModel(name="Empty Test Scenario", actions=[])
    builder = ScenarioBuilder(scenario=scenario)
    qtbot.addWidget(builder)
    builder.show()

    assert not builder.lbl_empty.isHidden()
    assert builder.list_widget.isHidden()
    assert builder.list_widget.count() == 0
    assert builder.get_scenario() is scenario


def test_scenario_builder_add_all_four_action_types(qtbot):
    scenario = ScenarioModel(name="Add Actions Scenario", actions=[])
    builder = ScenarioBuilder(scenario=scenario)
    qtbot.addWidget(builder)
    builder.show()

    # 1. Mouse Click
    with qtbot.waitSignal(builder.action_added) as blocker:
        act_mouse = builder.add_action_block(ActionType.MOUSE_CLICK)
    assert blocker.args[0].id == act_mouse.id
    assert act_mouse.type == ActionType.MOUSE_CLICK
    assert isinstance(act_mouse.params, MouseClickParams)

    # 2. Key Input
    with qtbot.waitSignal(builder.action_added) as blocker:
        act_key = builder.add_action_block(ActionType.KEY_INPUT)
    assert act_key.type == ActionType.KEY_INPUT
    assert isinstance(act_key.params, KeyInputParams)

    # 3. Fixed Wait
    with qtbot.waitSignal(builder.action_added) as blocker:
        act_wait = builder.add_action_block(ActionType.FIXED_WAIT)
    assert act_wait.type == ActionType.FIXED_WAIT
    assert isinstance(act_wait.params, FixedWaitParams)

    # 4. Image Wait (Template path must not be empty)
    with qtbot.waitSignal(builder.action_added) as blocker:
        act_image = builder.add_action_block(ActionType.IMAGE_WAIT)
    assert act_image.type == ActionType.IMAGE_WAIT
    assert isinstance(act_image.params, ImageWaitParams)
    assert act_image.params.template_path != ""

    assert len(scenario.actions) == 4
    assert builder.list_widget.count() == 4
    assert builder.lbl_empty.isHidden()
    assert not builder.list_widget.isHidden()


def test_scenario_builder_delete_action(qtbot):
    scenario = ScenarioModel(name="Delete Scenario")
    builder = ScenarioBuilder(scenario=scenario)
    qtbot.addWidget(builder)

    act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
    act2 = builder.add_action_block(ActionType.FIXED_WAIT)
    assert len(scenario.actions) == 2

    with qtbot.waitSignal(builder.action_deleted) as blocker:
        builder.delete_action(act1.id)
    assert blocker.args[0] == act1.id
    assert len(scenario.actions) == 1
    assert scenario.actions[0].id == act2.id
    assert builder.list_widget.count() == 1


def test_scenario_builder_card_reorder_buttons(qtbot):
    scenario = ScenarioModel(name="Reorder Scenario")
    builder = ScenarioBuilder(scenario=scenario)
    qtbot.addWidget(builder)

    act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
    act2 = builder.add_action_block(ActionType.KEY_INPUT)
    act3 = builder.add_action_block(ActionType.FIXED_WAIT)

    card1 = builder._card_map[act1.id]
    card2 = builder._card_map[act2.id]
    card3 = builder._card_map[act3.id]

    # Verify initial button enablement
    assert not card1.btn_up.isEnabled()
    assert card1.btn_down.isEnabled()

    assert card2.btn_up.isEnabled()
    assert card2.btn_down.isEnabled()

    assert card3.btn_up.isEnabled()
    assert not card3.btn_down.isEnabled()

    # Move act1 down
    with qtbot.waitSignal(builder.scenario_changed):
        builder.move_action_down(act1.id)

    assert scenario.actions[0].id == act2.id
    assert scenario.actions[1].id == act1.id
    assert scenario.actions[2].id == act3.id

    # Move act1 back up
    with qtbot.waitSignal(builder.scenario_changed):
        builder.move_action_up(act1.id)

    assert scenario.actions[0].id == act1.id
    assert scenario.actions[1].id == act2.id
    assert scenario.actions[2].id == act3.id


def test_scenario_builder_enabled_toggle(qtbot):
    scenario = ScenarioModel(name="Toggle Enabled Scenario")
    builder = ScenarioBuilder(scenario=scenario)
    qtbot.addWidget(builder)

    act = builder.add_action_block(ActionType.MOUSE_CLICK)
    card = builder._card_map[act.id]
    assert act.enabled is True
    assert card.chk_enabled.isChecked()

    with qtbot.waitSignal(builder.scenario_changed):
        card.chk_enabled.setChecked(False)

    assert act.enabled is False
    assert not card.chk_enabled.isChecked()

    with qtbot.waitSignal(builder.scenario_changed):
        card.chk_enabled.setChecked(True)

    assert act.enabled is True


def test_scenario_builder_selection_and_highlight(qtbot):
    scenario = ScenarioModel(name="Selection & Highlight Scenario")
    builder = ScenarioBuilder(scenario=scenario)
    qtbot.addWidget(builder)

    act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
    act2 = builder.add_action_block(ActionType.KEY_INPUT)

    # Test selecting card
    with qtbot.waitSignal(builder.block_selected) as blocker:
        builder.select_action(act2.id)
    assert blocker.args[0].id == act2.id
    assert builder._card_map[act2.id].is_selected is True
    assert builder._card_map[act1.id].is_selected is False

    # Test active execution highlight
    builder.set_active_action(0)
    assert builder._card_map[act1.id].is_executing is True
    assert builder._card_map[act2.id].is_executing is False

    builder.clear_active_action()
    assert builder._card_map[act1.id].is_executing is False


def test_scenario_builder_update_action_block(qtbot):
    scenario = ScenarioModel(name="Update Action Scenario")
    builder = ScenarioBuilder(scenario=scenario)
    qtbot.addWidget(builder)

    act = builder.add_action_block(ActionType.MOUSE_CLICK)
    card = builder._card_map[act.id]

    act.description = "New Custom Description"
    act.params = MouseClickParams(x=999, y=888, button="right", clicks=2)

    with qtbot.waitSignal(builder.scenario_changed):
        builder.update_action_block(act)

    assert card.lbl_title.text() == "New Custom Description"
    # The card summary is a sentence now: point, button and click count in one line.
    assert card.lbl_summary.text() == "Double-click right at (999, 888)"
