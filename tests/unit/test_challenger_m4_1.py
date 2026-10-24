"""
tests.unit.test_challenger_m4_1
===============================
Adversarial empirical challenge suite for Milestone 4 (PySide6 Desktop GUI).
Targeting ScenarioBuilder and PropertyInspector components:
- `src/action_macro/gui/scenario_builder.py`
- `src/action_macro/gui/property_inspector.py`

CHALLENGE FOCUS:
1. Rapid and boundary reordering:
   - Move top item up
   - Move bottom item down
   - Move in empty list
   - Move with invalid index / nonexistent action ID
   - Single item list reordering
   - Drag and drop synchronization with ScenarioModel
   - Rapid back-to-back reordering stress

2. Rapid block deletion and clearing:
   - Successive deletion until list is empty
   - Deletion while PropertyInspector is inspecting the deleted block
   - Rapid deletion from varied positions (head, tail, middle)
   - Add 50 blocks rapidly under tight loop
   - Deletion with invalid/empty/None ID

3. Malformed and adversarial property inputs:
   - Negative coordinates, extreme coordinates in MouseClickForm
   - Extreme clicks, interval, duration in MouseClickForm
   - Empty, whitespace, malformed key combinations in KeyInputForm (e.g. '+++', '+', 'ctrl++')
   - Unicode / emoji / special characters in KeyInputForm ('ctrl+한글', 'ctrl+🔥', 'alt+⌥')
   - Status vs Commit synchronization discrepancy when key combo parses to empty list
   - FixedWaitForm boundary: negative duration, extreme duration, slider clamping
   - ImageWaitForm boundary: empty path, non-existent path, Unicode path, confidence/timeout limits, ROI limits
   - Rapid type switching between different action forms without cross-contamination

4. Active card execution highlighting:
   - Out of bounds indices (-1, -999, 99999, len(actions))
   - Clearing scenario while active execution highlight is on
   - Deleting active action while execution highlight is on
   - Reordering actions while execution highlight is on
"""

from __future__ import annotations

import time
import uuid
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.gui.property_inspector import (
    FixedWaitForm,
    ImageWaitForm,
    KeyInputForm,
    MouseClickForm,
    PropertyInspector,
)
from action_macro.gui.scenario_builder import (
    ActionCardWidget,
    ActionListWidget,
    ScenarioBuilder,
)


# ===========================================================================
# 1. RAPID AND BOUNDARY REORDERING
# ===========================================================================

class TestReorderingBoundaries:
    """Empirical challenge tests for ScenarioBuilder reordering edge cases."""

    def test_move_top_item_up_is_safe_noop(self, qtbot):
        """Moving the first item up must be a safe no-op and not corrupt model or crash."""
        scenario = ScenarioModel(name="Top Up Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)

        # Attempt to move the first item up
        builder.move_action_up(act1.id)
        assert scenario.actions[0].id == act1.id
        assert scenario.actions[1].id == act2.id

        card1 = builder._card_map[act1.id]
        assert not card1.btn_up.isEnabled()
        assert card1.btn_down.isEnabled()

    def test_move_bottom_item_down_is_safe_noop(self, qtbot):
        """Moving the bottom item down must be a safe no-op."""
        scenario = ScenarioModel(name="Bottom Down Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)

        # Attempt to move the last item down
        builder.move_action_down(act2.id)
        assert scenario.actions[0].id == act1.id
        assert scenario.actions[1].id == act2.id

        card2 = builder._card_map[act2.id]
        assert not card2.btn_down.isEnabled()
        assert card2.btn_up.isEnabled()

    def test_move_in_empty_scenario(self, qtbot):
        """Moving in an empty scenario must not raise IndexError or AttributeError."""
        scenario = ScenarioModel(name="Empty Move Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        # These calls must silently and safely no-op
        builder.move_action_up("nonexistent-id")
        builder.move_action_down("nonexistent-id")
        assert len(scenario.actions) == 0

    def test_move_with_nonexistent_or_invalid_id(self, qtbot):
        """Moving with invalid or foreign action IDs must not modify actions."""
        scenario = ScenarioModel(name="Invalid ID Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)

        builder.move_action_up("bogus-uuid-1234")
        builder.move_action_down("bogus-uuid-1234")
        builder.move_action_up("")
        builder.move_action_down("")

        assert [a.id for a in scenario.actions] == [act1.id, act2.id]

    def test_single_item_scenario_reorder(self, qtbot):
        """In a 1-item scenario, both move up and move down must be disabled and no-op."""
        scenario = ScenarioModel(name="Single Item Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act = builder.add_action_block(ActionType.FIXED_WAIT)
        card = builder._card_map[act.id]

        assert not card.btn_up.isEnabled()
        assert not card.btn_down.isEnabled()

        builder.move_action_up(act.id)
        assert len(scenario.actions) == 1
        builder.move_action_down(act.id)
        assert len(scenario.actions) == 1

    def test_rapid_back_to_back_reordering_stress(self, qtbot):
        """Perform 50 alternating move up and move down operations rapidly."""
        scenario = ScenarioModel(name="Rapid Reorder Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        acts = [
            builder.add_action_block(ActionType.MOUSE_CLICK),
            builder.add_action_block(ActionType.KEY_INPUT),
            builder.add_action_block(ActionType.FIXED_WAIT),
            builder.add_action_block(ActionType.IMAGE_WAIT),
        ]
        target_id = acts[1].id  # Key input initially at index 1

        for _ in range(25):
            builder.move_action_down(target_id)  # moves to index 2
            builder.move_action_up(target_id)    # moves back to index 1

        assert [a.id for a in scenario.actions] == [a.id for a in acts]
        assert builder.list_widget.count() == 4
        # Verify card indices are synchronized
        for idx, act in enumerate(scenario.actions):
            card = builder._card_map[act.id]
            assert card.lbl_index.text() == f"#{idx + 1}"

    def test_drag_and_drop_sync_with_scenario_model(self, qtbot):
        """
        Verify list order synchronization logic when items are moved.
        Specifically tests `_sync_model_from_list_order` when QListWidget item order changes.
        """
        scenario = ScenarioModel(name="DnD Sync Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)
        act3 = builder.add_action_block(ActionType.FIXED_WAIT)

        # Simulate internal list widget reorder: move item 0 to item 2
        item0 = builder.list_widget.takeItem(0)
        builder.list_widget.insertItem(2, item0)
        # Note: takeItem removes the widget; test if itemWidget still exists
        w = builder.list_widget.itemWidget(item0)

        # Trigger order changed sync
        builder._sync_model_from_list_order()

        # Check if scenario model reflects new order: [act2, act3, act1]
        assert scenario.actions[0].id == act2.id
        assert scenario.actions[1].id == act3.id
        assert scenario.actions[2].id == act1.id

        # Adversarial check: In Qt QListWidget with InternalMove / takeItem,
        # does the moved item retain its ActionCardWidget?
        # If itemWidget is None, the user sees an empty row instead of a card!
        assert builder.list_widget.itemWidget(item0) is not None, (
            "VULNERABILITY: Moved list item lost its ActionCardWidget in QListWidget! "
            "_sync_model_from_list_order did not re-attach item widgets."
        )


# ===========================================================================
# 2. RAPID BLOCK DELETION AND CLEARING
# ===========================================================================

class TestDeletionAndClearing:
    """Empirical challenge tests for block deletion, clearing, and rapid addition."""

    def test_successive_deletion_until_empty(self, qtbot):
        """Delete all blocks one by one until list is completely empty."""
        scenario = ScenarioModel(name="Delete All Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)
        act3 = builder.add_action_block(ActionType.FIXED_WAIT)

        # Track signals
        selected_emissions = []
        builder.block_selected.connect(lambda b: selected_emissions.append(b))

        builder.delete_action(act1.id)
        assert len(scenario.actions) == 2
        assert builder.list_widget.count() == 2

        builder.delete_action(act2.id)
        assert len(scenario.actions) == 1
        assert builder.list_widget.count() == 1

        builder.delete_action(act3.id)
        assert len(scenario.actions) == 0
        assert builder.list_widget.count() == 0
        assert not builder.lbl_empty.isHidden()
        assert builder.list_widget.isHidden()
        assert builder._selected_id is None
        # Must have emitted None when emptied
        assert selected_emissions[-1] is None

    def test_delete_while_property_inspector_active(self, qtbot):
        """
        When PropertyInspector is actively inspecting a block and that block is deleted,
        the system must gracefully transition to the next block or empty state without crashing.
        """
        scenario = ScenarioModel(name="Inspector Sync Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        inspector = PropertyInspector()
        qtbot.addWidget(builder)
        qtbot.addWidget(inspector)

        # Wire up components as in MainWindow
        builder.block_selected.connect(inspector.set_action)
        inspector.action_modified.connect(builder.update_action_block)

        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)

        # Select act2
        builder.select_action(act2.id)
        assert inspector.current_action is not None
        assert inspector.current_action.id == act2.id

        # Delete act2 while it's active in inspector
        builder.delete_action(act2.id)

        # Builder repopulates and selects act1 (row 0)
        assert inspector.current_action is not None
        assert inspector.current_action.id == act1.id
        assert inspector.stacked_widget.currentWidget() is inspector.form_mouse

        # Delete remaining act1
        builder.delete_action(act1.id)
        assert inspector.current_action is None
        assert inspector.stacked_widget.currentWidget() is inspector.page_empty
        assert inspector.common_frame.isHidden()

    def test_rapid_addition_of_50_blocks(self, qtbot):
        """Add 50 blocks in rapid succession and verify integrity and performance."""
        scenario = ScenarioModel(name="Scale 50 Blocks Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        start_time = time.perf_counter()
        types = [ActionType.MOUSE_CLICK, ActionType.KEY_INPUT, ActionType.FIXED_WAIT, ActionType.IMAGE_WAIT]
        for i in range(50):
            builder.add_action_block(types[i % 4])
        elapsed = time.perf_counter() - start_time

        assert len(scenario.actions) == 50
        assert builder.list_widget.count() == 50
        assert elapsed < 5.0  # Must complete rapidly within 5s

        # Verify integrity of first, middle, and last cards
        card_first = builder._card_map[scenario.actions[0].id]
        card_mid = builder._card_map[scenario.actions[25].id]
        card_last = builder._card_map[scenario.actions[49].id]

        assert card_first.lbl_index.text() == "#1"
        assert not card_first.btn_up.isEnabled()
        assert card_first.btn_down.isEnabled()

        assert card_mid.lbl_index.text() == "#26"
        assert card_mid.btn_up.isEnabled()
        assert card_mid.btn_down.isEnabled()

        assert card_last.lbl_index.text() == "#50"
        assert card_last.btn_up.isEnabled()
        assert not card_last.btn_down.isEnabled()

    def test_delete_with_invalid_or_nonexistent_ids(self, qtbot):
        """Deleting non-existent IDs, empty strings, or None must safely no-op."""
        scenario = ScenarioModel(name="Safe Delete Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act = builder.add_action_block(ActionType.MOUSE_CLICK)
        builder.delete_action("nonexistent-1234")
        builder.delete_action("")
        builder.delete_action(" ")

        assert len(scenario.actions) == 1
        assert scenario.actions[0].id == act.id


# ===========================================================================
# 3. MALFORMED AND ADVERSARIAL PROPERTY INPUTS
# ===========================================================================

class TestMalformedAndAdversarialInputs:
    """Empirical challenge tests for input validation, boundary values, and unicode."""

    def test_mouse_form_negative_and_extreme_coordinates(self, qtbot):
        """
        Coordinates fed to MouseClickForm:
        - Negative coordinates are kept as-is (monitors left of / above the primary
          display have negative global coordinates)
        - Extreme coordinates < -99999 should be clamped to -99999
        - Extreme coordinates > 99999 should be clamped to 99999
        """
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-m",
            type=ActionType.MOUSE_CLICK,
            params=MouseClickParams(x=100, y=100),
        )
        inspector.set_action(act)

        # Inject negative coordinates via set_coordinates
        inspector.set_coordinates(-500, -999)
        assert inspector.form_mouse.spin_x.value() == -500
        assert inspector.form_mouse.spin_y.value() == -999
        assert act.params.x == -500
        assert act.params.y == -999

        # Inject extreme negative coordinates
        inspector.set_coordinates(-1000000, -500000)
        assert inspector.form_mouse.spin_x.value() == -99999
        assert inspector.form_mouse.spin_y.value() == -99999
        assert act.params.x == -99999
        assert act.params.y == -99999

        # Inject extreme coordinates
        inspector.set_coordinates(1000000, 500000)
        assert inspector.form_mouse.spin_x.value() == 99999
        assert inspector.form_mouse.spin_y.value() == 99999
        assert act.params.x == 99999
        assert act.params.y == 99999

    def test_mouse_form_extreme_options(self, qtbot):
        """Test clicks and interval boundary handling."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-m",
            type=ActionType.MOUSE_CLICK,
            params=MouseClickParams(x=10, y=10),
        )
        inspector.set_action(act)

        # Clicks spinbox has range [1, 10]
        inspector.form_mouse.spin_clicks.setValue(0)
        assert inspector.form_mouse.spin_clicks.value() == 1

        inspector.form_mouse.spin_clicks.setValue(100)
        assert inspector.form_mouse.spin_clicks.value() == 10

        # Interval has range [0, 60000]
        inspector.form_mouse.spin_interval.setValue(-50)
        assert inspector.form_mouse.spin_interval.value() == 0

    def test_key_form_empty_and_whitespace_validation(self, qtbot):
        """Empty string or whitespace keys must trigger invalid status and not commit."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-k",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        # Set empty text
        inspector.form_key.edit_keys.setText("")
        assert "⚠" in inspector.lbl_status.text()
        assert act.params.keys == ["enter"]  # Must not corrupt

        # Set whitespace text
        inspector.form_key.edit_keys.setText("    ")
        assert "⚠" in inspector.lbl_status.text()
        assert act.params.keys == ["enter"]

    def test_key_form_malformed_combos_sync_discrepancy(self, qtbot):
        """
        Adversarial test: '+++' parses to empty list `[]`.
        Does the PropertyInspector validator catch this, or does it claim '✓ Valid'
        while failing to commit?
        """
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-k",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        # Input '+++' which splits into empty tokens and parses to []
        inspector.form_key.edit_keys.setText("+++")

        # Check empirical behavior:
        # If validator says valid, but params are not committed, that is a silent sync bug.
        parsed = inspector.form_key.get_values()
        is_valid_status = "✓" in inspector.lbl_status.text()

        if parsed is None:
            # If get_values returns None, status MUST NOT say Valid
            # Finding: If is_valid_status is True, validator is out-of-sync with get_values!
            assert not is_valid_status, (
                "VULNERABILITY: Form status displays '✓ Valid' for '+++', but get_values() returns None, "
                "causing silent commit failure!"
            )

    def test_key_form_unicode_and_special_keys(self, qtbot):
        """Unicode characters (Korean, Emoji, special symbols) in key input."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-k",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        # Unicode / Korean
        inspector.form_key.edit_keys.setText("ctrl+한글")
        # Should parse to ['ctrl', '한글']
        assert act.params.keys == ["ctrl", "한글"]

        # Symbol '+' key via 'ctrl++'
        inspector.form_key.edit_keys.setText("ctrl++")
        assert act.params.keys == ["ctrl", "+"]

    def test_fixed_wait_boundary_inputs(self, qtbot):
        """FixedWait duration boundaries: negative, 0, extreme."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-w",
            type=ActionType.FIXED_WAIT,
            params=FixedWaitParams(duration_ms=1000),
        )
        inspector.set_action(act)

        # Minimum: 0ms
        inspector.form_wait.spin_duration.setValue(-100)
        assert inspector.form_wait.spin_duration.value() == 0
        assert act.params.duration_ms == 0

        # Maximum: 3600000ms (1 hour)
        inspector.form_wait.spin_duration.setValue(99999999)
        assert inspector.form_wait.spin_duration.value() == 3600000
        assert act.params.duration_ms == 3600000

        # Slider max is 10000 (10s), spinbox at 50000 should clamp slider to 10000 without error
        inspector.form_wait.spin_duration.setValue(50000)
        assert inspector.form_wait.slider.value() == 10000
        assert act.params.duration_ms == 50000

    def test_image_wait_boundary_inputs(self, qtbot):
        """ImageWait parameter boundaries: empty path, confidence range, timeout range, ROI."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-img",
            type=ActionType.IMAGE_WAIT,
            params=ImageWaitParams(template_path="templates/t.png", mode="appear"),
        )
        inspector.set_action(act)

        # Empty path triggers error
        inspector.form_image.edit_path.setText("")
        assert "⚠" in inspector.lbl_status.text()
        assert act.params.template_path == "templates/t.png"  # Not corrupted

        # Confidence boundary [0.0, 1.0]
        inspector.form_image.edit_path.setText("templates/t.png")
        inspector.form_image.spin_conf.setValue(-0.5)
        assert inspector.form_image.spin_conf.value() == 0.0

        inspector.form_image.spin_conf.setValue(1.5)
        assert inspector.form_image.spin_conf.value() == 1.0

        # ROI dimensions must have positive w and h (QSpinBox min is 1)
        inspector.form_image.chk_roi.setChecked(True)
        inspector.form_image.spin_rw.setValue(0)
        inspector.form_image.spin_rh.setValue(-5)
        assert inspector.form_image.spin_rw.value() == 1
        assert inspector.form_image.spin_rh.value() == 1
        assert act.params.roi == (0, 0, 1, 1)

    def test_rapid_type_switching_without_leakage(self, qtbot):
        """
        Rapidly switch PropertyInspector between actions of different types.
        Verify that values and forms do not leak across types.
        """
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act_mouse = ActionBlock(id="m", type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=123, y=456))
        act_key = ActionBlock(id="k", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["tab"]))
        act_wait = ActionBlock(id="w", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=777))
        act_img = ActionBlock(id="i", type=ActionType.IMAGE_WAIT, params=ImageWaitParams(template_path="img.png"))

        actions = [act_mouse, act_key, act_wait, act_img]

        for _ in range(10):
            for a in actions:
                inspector.set_action(a)
                assert inspector.current_action is a

                if a.type == ActionType.MOUSE_CLICK:
                    assert inspector.stacked_widget.currentWidget() is inspector.form_mouse
                    assert inspector.form_mouse.spin_x.value() == 123
                elif a.type == ActionType.KEY_INPUT:
                    assert inspector.stacked_widget.currentWidget() is inspector.form_key
                    assert inspector.form_key.edit_keys.text() == "tab"
                elif a.type == ActionType.FIXED_WAIT:
                    assert inspector.stacked_widget.currentWidget() is inspector.form_wait
                    assert inspector.form_wait.spin_duration.value() == 777
                elif a.type == ActionType.IMAGE_WAIT:
                    assert inspector.stacked_widget.currentWidget() is inspector.form_image
                    assert inspector.form_image.edit_path.text() == "img.png"


# ===========================================================================
# 4. ACTIVE CARD EXECUTION HIGHLIGHTING
# ===========================================================================

class TestActiveCardHighlighting:
    """Empirical challenge tests for execution highlight lifecycle and edge cases."""

    def test_highlight_out_of_bounds_indices(self, qtbot):
        """Setting active action index out of bounds (-1, -999, 99999, len) must not crash."""
        scenario = ScenarioModel(name="Highlight Bounds Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        builder.add_action_block(ActionType.MOUSE_CLICK)
        builder.add_action_block(ActionType.KEY_INPUT)

        # Valid highlight
        builder.set_active_action(0)
        assert builder._active_index == 0

        # Out-of-bounds high
        builder.set_active_action(99999)
        assert builder._active_index is None

        # Out-of-bounds len(count)
        builder.set_active_action(2)
        assert builder._active_index is None

        # Out-of-bounds negative
        builder.set_active_action(-1)
        assert builder._active_index is None

        # Via highlight_active_action
        builder.highlight_active_action(-1)
        assert builder._active_index is None
        builder.highlight_active_action(-999)
        assert builder._active_index is None

    def test_clearing_scenario_during_active_execution(self, qtbot):
        """
        When scenario is cleared while an action card is highlighted as executing:
        - Must not crash
        - Internal state should be consistent
        """
        scenario = ScenarioModel(name="Clear During Exec Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)

        # Highlight action 1
        builder.set_active_action(1)
        assert builder._card_map[act2.id].is_executing is True

        # Clear scenario
        scenario.actions.clear()
        builder.populate_from_scenario(scenario)

        # Check state: empty prompt is visible, list is hidden
        assert not builder.lbl_empty.isHidden()
        assert builder.list_widget.count() == 0

        # Subsequent calls to clear or highlight must not raise exceptions
        builder.clear_active_action()
        builder.highlight_active_action(-1)
        builder.highlight_active_action(0)

    def test_deleting_active_executing_card(self, qtbot):
        """
        When the currently executing card is deleted:
        - Must not crash
        - ScenarioBuilder must maintain valid state
        """
        scenario = ScenarioModel(name="Delete Exec Card Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)
        act3 = builder.add_action_block(ActionType.FIXED_WAIT)

        # Action 1 (KEY_INPUT) is executing
        builder.set_active_action(1)
        assert builder._card_map[act2.id].is_executing is True

        # Delete the executing card
        builder.delete_action(act2.id)

        assert len(scenario.actions) == 2
        assert builder.list_widget.count() == 2
        # Verify subsequent highlight operations are safe
        builder.clear_active_action()
        builder.set_active_action(0)
        assert builder._card_map[act1.id].is_executing is True

    def test_active_index_state_after_scenario_repopulation(self, qtbot):
        """
        Adversarial check: Does `populate_from_scenario` reset `_active_index`?
        If `_active_index` is left dangling, subsequent `clear_active_action()`
        could index into a different action block or be out of sync.
        """
        scenario = ScenarioModel(name="Active Index Sync Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)

        builder.set_active_action(1)
        assert builder._active_index == 1

        # Repopulate scenario with empty actions
        empty_scenario = ScenarioModel(name="Empty")
        builder.populate_from_scenario(empty_scenario)

        # Finding: Check whether _active_index was reset or left dangling
        # If _active_index is still 1, it's a state leak!
        is_cleared = (builder._active_index is None)
        assert is_cleared, (
            f"VULNERABILITY: ScenarioBuilder._active_index was NOT cleared on populate_from_scenario! "
            f"Value remains {builder._active_index} despite empty scenario."
        )

    def test_highlight_active_action_none_handling(self, qtbot):
        """
        Adversarial test: Passing None to highlight_active_action should safely clear
        active action instead of crashing with TypeError ('<' not supported between NoneType and int).
        """
        scenario = ScenarioModel(name="None Highlight Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        builder.add_action_block(ActionType.MOUSE_CLICK)
        builder.set_active_action(0)

        # In Runner signal callbacks or execution finish handlers, None is often passed.
        # Check whether highlight_active_action(None) is handled gracefully without TypeError.
        try:
            builder.highlight_active_action(None)
        except TypeError as e:
            pytest.fail(f"VULNERABILITY: highlight_active_action(None) crashed with {type(e).__name__}: {e}")

        assert builder._active_index is None

    def test_set_active_action_none_handling(self, qtbot):
        """
        Adversarial test: Passing None to set_active_action should safely no-op/clear
        instead of crashing with TypeError ('<=' not supported between int and NoneType).
        """
        scenario = ScenarioModel(name="None Set Active Test", actions=[])
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        builder.add_action_block(ActionType.MOUSE_CLICK)

        try:
            builder.set_active_action(None)
        except TypeError as e:
            pytest.fail(f"VULNERABILITY: set_active_action(None) crashed with {type(e).__name__}: {e}")

        assert builder._active_index is None

