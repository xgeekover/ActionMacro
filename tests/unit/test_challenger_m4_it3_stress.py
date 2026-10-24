"""
tests.unit.test_challenger_m4_it3_stress
========================================
Milestone 4 Iteration 3 Adversarial Stress Testing Suite.
Empirically stress-tests:
1. Intensive Drag & Drop Reordering:
   - 50 consecutive arbitrary transpositions and shuffles across action list
   - Retention of ActionCardWidget on all items
   - Strict 1:1 synchronization between ScenarioModel.actions, list items, and card widgets
   - Button enablement (top/bottom boundaries) and index badges (#1..#N)
   - Selection identity preservation across arbitrary moves
   - Active execution highlight preservation across moves
   - Mixed enabled/disabled state preservation
   - Large list (40 items) reordering resilience
2. Rapid Scenario Clearing and Reloading:
   - 50 rapid scenario reloads of varying lengths (0, 1, 5, 20 items)
   - Integration with PropertyInspector: clearing while inspector is inspecting an action
   - Rapid interleaved add, select, delete, clear operations
   - Signal emission idempotency and no signal duplication
3. Adversarial Key Combination Validation:
   - Exhaustive delimiter, whitespace, and empty string boundaries
   - Complex modifier combinations (Ctrl+Shift+Alt+Delete, Cmd+F12, etc.)
   - Literal plus symbol combinations (Ctrl++, Alt++, etc.)
   - Non-ASCII, Unicode, emoji, and special character inputs
   - Real-time keystroke typing simulation
   - Key Combo Helper button rapid interaction
4. Integrated Builder & Inspector Stress:
   - Parameter editing across all 4 action types with real-time card summary sync
   - Deletion of inspected card during active editing
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from action_macro.core.key_mapping import parse_key_combo, format_key_combo
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
# 1. INTENSIVE DRAG & DROP REORDERING STRESS
# ===========================================================================

class TestIntensiveDragAndDropStress:
    """Adversarial stress testing of rapid, sequential, and boundary drag & drop reordering."""

    def test_rapid_50_move_shuffles_consistency(self, qtbot):
        """
        Execute 50 consecutive transpositions simulating rapid drag & drop across 10 actions.
        After every single move, strictly verify:
        - Exactly 10 items in list widget and ScenarioModel
        - itemWidget is non-None for all items
        - Card action_id == item UserRole == scenario.actions[i].id
        - Monotonic index badges: '#1' to '#10'
        - Boundary button rules: index 0 (btn_up disabled), index 9 (btn_down disabled), others enabled
        """
        scenario = ScenarioModel(name="DnD 50-Move Shuffle")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        # Populate with 10 distinct actions
        types = [
            ActionType.MOUSE_CLICK, ActionType.KEY_INPUT,
            ActionType.FIXED_WAIT, ActionType.IMAGE_WAIT,
            ActionType.MOUSE_CLICK, ActionType.KEY_INPUT,
            ActionType.FIXED_WAIT, ActionType.IMAGE_WAIT,
            ActionType.MOUSE_CLICK, ActionType.KEY_INPUT,
        ]
        for t in types:
            builder.add_action_block(t)

        assert builder.list_widget.count() == 10
        assert len(scenario.actions) == 10

        for step in range(50):
            src_idx = step % 10
            dst_idx = (step * 7) % 10
            if src_idx == dst_idx:
                dst_idx = (dst_idx + 1) % 10

            item = builder.list_widget.takeItem(src_idx)
            builder.list_widget.insertItem(dst_idx, item)
            builder._sync_model_from_list_order()

            assert builder.list_widget.count() == 10, f"Step {step}: List count mismatch"
            assert len(scenario.actions) == 10, f"Step {step}: Scenario actions count mismatch"

            for row in range(10):
                curr_item = builder.list_widget.item(row)
                card = builder.list_widget.itemWidget(curr_item)
                assert card is not None, f"Step {step}, Row {row}: card widget is None"
                act_id = curr_item.data(Qt.ItemDataRole.UserRole)
                assert card.action_id == act_id, f"Step {step}, Row {row}: card ID mismatch"
                assert scenario.actions[row].id == act_id, f"Step {step}, Row {row}: model mismatch"
                assert card.lbl_index.text() == f"#{row + 1}", f"Step {step}, Row {row}: badge mismatch"

                # Check boundary buttons
                if row == 0:
                    assert not card.btn_up.isEnabled(), f"Step {step}: Row 0 btn_up must be disabled"
                    assert card.btn_down.isEnabled(), f"Step {step}: Row 0 btn_down must be enabled"
                elif row == 9:
                    assert card.btn_up.isEnabled(), f"Step {step}: Row 9 btn_up must be enabled"
                    assert not card.btn_down.isEnabled(), f"Step {step}: Row 9 btn_down must be disabled"
                else:
                    assert card.btn_up.isEnabled(), f"Step {step}: Row {row} btn_up must be enabled"
                    assert card.btn_down.isEnabled(), f"Step {step}: Row {row} btn_down must be enabled"

    def test_drag_drop_preserves_selection_identity(self, qtbot):
        """
        Verify that dragging and dropping items (either moving the selected card or moving cards around it)
        preserves selection identity without losing focus or selecting the wrong action.
        """
        scenario = ScenarioModel(name="DnD Selection Track")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        for _ in range(6):
            builder.add_action_block(ActionType.MOUSE_CLICK)

        # Select action at index 2
        target_act_id = scenario.actions[2].id
        builder.select_action(target_act_id)
        assert builder._selected_id == target_act_id
        assert builder._card_map[target_act_id].is_selected is True

        # Move top item (index 0) to bottom (index 5)
        item0 = builder.list_widget.takeItem(0)
        builder.list_widget.insertItem(5, item0)
        builder._sync_model_from_list_order()

        # Target should now be at index 1, still selected
        assert builder._selected_id == target_act_id
        assert builder._card_map[target_act_id].is_selected is True
        assert scenario.actions[1].id == target_act_id

        # Now move target action itself from index 1 to index 4 (simulating Qt dragging the selected item)
        item_target = builder.list_widget.takeItem(1)
        builder.list_widget.insertItem(4, item_target)
        builder.list_widget.setCurrentItem(item_target)
        builder._sync_model_from_list_order()

        assert builder._selected_id == target_act_id
        assert builder._card_map[target_act_id].is_selected is True
        assert scenario.actions[4].id == target_act_id

    def test_drag_drop_with_active_execution_highlight(self, qtbot):
        """
        Mark a card as executing, then perform drag & drop moves.
        Ensure card execution highlight state persists correctly and does not attach to unrelated cards.
        """
        scenario = ScenarioModel(name="DnD Active Highlight")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        for _ in range(5):
            builder.add_action_block(ActionType.FIXED_WAIT)

        exec_id = scenario.actions[2].id
        builder.set_active_action(2)
        assert builder._active_index == 2
        assert builder._card_map[exec_id].is_executing is True

        # Move item 0 to 4
        it = builder.list_widget.takeItem(0)
        builder.list_widget.insertItem(4, it)
        builder._sync_model_from_list_order()

        # The card with exec_id must still retain is_executing True
        assert builder._card_map[exec_id].is_executing is True
        for act in scenario.actions:
            if act.id != exec_id:
                assert not builder._card_map[act.id].is_executing

    def test_drag_drop_with_mixed_enabled_disabled_states(self, qtbot):
        """
        Verify that toggling card enable/disable checkboxes maintains strict integrity
        and visual state across 20 rapid drag & drop transpositions.
        """
        scenario = ScenarioModel(name="DnD Enabled/Disabled")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        for i in range(6):
            act = builder.add_action_block(ActionType.KEY_INPUT)
            if i % 2 == 1:
                # Disable odd-indexed actions
                card = builder._card_map[act.id]
                card.chk_enabled.setChecked(False)

        # Record expected enable states by action ID
        expected_states = {act.id: act.enabled for act in scenario.actions}
        assert expected_states[scenario.actions[0].id] is True
        assert expected_states[scenario.actions[1].id] is False

        for cycle in range(20):
            src = cycle % 6
            dst = (cycle + 3) % 6
            item = builder.list_widget.takeItem(src)
            builder.list_widget.insertItem(dst, item)
            builder._sync_model_from_list_order()

            for row in range(6):
                act_id = scenario.actions[row].id
                card = builder._card_map[act_id]
                expected = expected_states[act_id]
                assert card.chk_enabled.isChecked() == expected, f"Cycle {cycle}: card {act_id} state corrupted"
                assert scenario.actions[row].enabled == expected, f"Cycle {cycle}: model {act_id} state corrupted"

    def test_large_list_reordering_stress(self, qtbot):
        """Stress test drag & drop reordering on a 40-item action scenario."""
        scenario = ScenarioModel(name="DnD Large 40")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        for i in range(40):
            builder.add_action_block(ActionType.MOUSE_CLICK)

        assert builder.list_widget.count() == 40
        assert len(scenario.actions) == 40

        # Rapid move top to bottom 5 times
        for _ in range(5):
            item = builder.list_widget.takeItem(0)
            builder.list_widget.insertItem(39, item)
            builder._sync_model_from_list_order()

        assert builder.list_widget.count() == 40
        assert len(scenario.actions) == 40
        for i in range(40):
            item = builder.list_widget.item(i)
            card = builder.list_widget.itemWidget(item)
            assert card is not None
            assert card.action_id == item.data(Qt.ItemDataRole.UserRole)
            assert scenario.actions[i].id == card.action_id
            assert card.lbl_index.text() == f"#{i + 1}"


# ===========================================================================
# 2. RAPID SCENARIO CLEARING AND RELOADING STRESS
# ===========================================================================

class TestRapidScenarioClearingAndReloadingStress:
    """Stress testing scenario reloads, clearings, and empty/loaded transitions."""

    def test_50_consecutive_scenario_reloads_variable_lengths(self, qtbot):
        """
        Perform 50 consecutive reloads of scenarios with varying sizes (0, 1, 3, 7, 15).
        Verifies:
        - Widget and model sizes match exactly
        - Empty label is visible only when size == 0
        - List widget is hidden when empty, visible when non-empty
        - _active_index is None after every load
        - No dangling or ghost cards in _card_map
        """
        builder = ScenarioBuilder()
        qtbot.addWidget(builder)

        sizes = [0, 1, 5, 0, 10, 3, 0, 15, 2, 0] * 5

        for cycle, count in enumerate(sizes):
            scen = ScenarioModel(name=f"Cycle {cycle} - Size {count}")
            for i in range(count):
                scen.actions.append(
                    ActionBlock(
                        id=f"c{cycle}-a{i}",
                        type=ActionType.FIXED_WAIT,
                        params=FixedWaitParams(duration_ms=100 + i),
                    )
                )

            builder.set_scenario(scen)

            assert builder.list_widget.count() == count, f"Cycle {cycle}: list count != {count}"
            assert len(builder.scenario.actions) == count, f"Cycle {cycle}: model count != {count}"
            assert len(builder._card_map) == count, f"Cycle {cycle}: card map size != {count}"
            assert builder._active_index is None, f"Cycle {cycle}: _active_index not None"

            if count == 0:
                assert not builder.lbl_empty.isHidden()
                assert builder.list_widget.isHidden()
            else:
                assert builder.lbl_empty.isHidden()
                assert not builder.list_widget.isHidden()

    def test_clearing_scenario_while_property_inspector_is_active(self, qtbot):
        """
        ScenarioBuilder connected to PropertyInspector (MainWindow workflow).
        Inspect an action, then immediately clear/replace the scenario.
        Verify:
        - PropertyInspector switches to empty page
        - current_action becomes None
        - Calling commit or editing fields does not crash or corrupt state
        """
        scenario = ScenarioModel(name="Inspector Clear Test")
        builder = ScenarioBuilder(scenario=scenario)
        inspector = PropertyInspector()
        qtbot.addWidget(builder)
        qtbot.addWidget(inspector)

        # Wire signals as in MainWindow
        builder.block_selected.connect(inspector.set_action)
        inspector.action_modified.connect(builder.update_action_block)

        # Add 3 actions
        act1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        act2 = builder.add_action_block(ActionType.KEY_INPUT)
        act3 = builder.add_action_block(ActionType.FIXED_WAIT)

        # Select act2
        builder.select_action(act2.id)
        assert inspector.current_action is not None
        assert inspector.current_action.id == act2.id
        assert inspector.stacked_widget.currentWidget() == inspector.form_key

        # Clear scenario
        empty_scenario = ScenarioModel(name="Empty")
        builder.set_scenario(empty_scenario)

        # Inspector should now show empty page
        assert inspector.current_action is None
        assert inspector.stacked_widget.currentWidget() == inspector.page_empty
        assert inspector.common_frame.isHidden()
        assert inspector.lbl_status.isHidden()

        # Try triggering common edits and form changes: must be safe no-op
        inspector.edit_description.setText("Dangerous Ghost Edit")
        inspector._commit_changes()
        assert len(empty_scenario.actions) == 0

    def test_rapid_interleaved_add_select_delete_clear_stress(self, qtbot):
        """
        20 cycles of:
        - Add 4 actions
        - Select action 2
        - Delete action 0
        - Select new action 1
        - Clear scenario
        Verify no stale signal connections or memory corruption.
        """
        builder = ScenarioBuilder()
        inspector = PropertyInspector()
        qtbot.addWidget(builder)
        qtbot.addWidget(inspector)

        builder.block_selected.connect(inspector.set_action)

        for cycle in range(20):
            # Fresh scenario
            scen = ScenarioModel(name=f"Stress Interleaved {cycle}")
            builder.set_scenario(scen)

            a1 = builder.add_action_block(ActionType.MOUSE_CLICK)
            a2 = builder.add_action_block(ActionType.KEY_INPUT)
            a3 = builder.add_action_block(ActionType.FIXED_WAIT)
            a4 = builder.add_action_block(ActionType.IMAGE_WAIT)

            builder.select_action(a2.id)
            assert inspector.current_action.id == a2.id

            builder.delete_action(a1.id)
            assert builder.list_widget.count() == 3
            assert len(builder.scenario.actions) == 3

            # Clear
            builder.set_scenario(ScenarioModel(name="Empty"))
            assert builder.list_widget.count() == 0
            assert inspector.current_action is None

    def test_signal_emission_stability_under_rapid_reload(self, qtbot):
        """Ensure scenario_changed and model_changed emit cleanly without compounding connections."""
        builder = ScenarioBuilder()
        qtbot.addWidget(builder)

        scenario_signal_count = 0
        model_signal_count = 0

        def on_scen_changed():
            nonlocal scenario_signal_count
            scenario_signal_count += 1

        def on_model_changed():
            nonlocal model_signal_count
            model_signal_count += 1

        builder.scenario_changed.connect(on_scen_changed)
        builder.model_changed.connect(on_model_changed)

        for i in range(10):
            scen = ScenarioModel(name=f"S-{i}")
            builder.set_scenario(scen)

        # set_scenario does not emit scenario_changed (it replaces the model)
        # Now add one block and check exactly 1 emission each
        count_before_scen = scenario_signal_count
        count_before_model = model_signal_count

        builder.add_action_block(ActionType.MOUSE_CLICK)
        assert scenario_signal_count == count_before_scen + 1
        assert model_signal_count == count_before_model + 1


# ===========================================================================
# 3. ADVERSARIAL KEY COMBINATION VALIDATION
# ===========================================================================

class TestAdversarialKeyComboValidation:
    """Empirical verification of PropertyInspector key combination parsing, validation, and rejection."""

    @pytest.mark.parametrize(
        "invalid_key_combo",
        [
            "",
            "   ",
            "\t",
            "\n",
            "\r\n",
            "+",
            "++",
            "+++",
            "+++++",
            "  +  ",
            " + + ",
            "  +  +  +  ",
            "+++\t\n",
        ],
    )
    def test_empty_and_delimiter_only_inputs_rejected(self, qtbot, invalid_key_combo):
        """Exhaustive check that empty and delimiter-only inputs are rejected, invalid, and never committed."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-key-reject",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        signal_emissions = []
        inspector.validation_state_changed.connect(lambda v: signal_emissions.append(v))

        inspector.form_key.edit_keys.setText(invalid_key_combo)

        assert not inspector.form_key.is_valid()
        assert inspector.form_key.get_values() is None
        assert "⚠" in inspector.lbl_status.text()
        assert act.params.keys == ["enter"]  # Pristine
        assert False in signal_emissions

    @pytest.mark.parametrize(
        "complex_valid_combo,expected_keys",
        [
            ("Ctrl+Shift+Alt+Delete", ["ctrl", "shift", "alt", "delete"]),
            ("cmd_or_ctrl+f12", ["cmd_or_ctrl", "f12"]),
            ("alt+page_down", ["alt", "page_down"]),
            ("shift+tab", ["shift", "tab"]),
            ("ctrl+space", ["ctrl", "space"]),
            ("ctrl+home", ["ctrl", "home"]),
            ("ctrl+end", ["ctrl", "end"]),
            ("ctrl+insert", ["ctrl", "insert"]),
            ("backspace", ["backspace"]),
            ("caps_lock", ["caps_lock"]),
            ("print_screen", ["print_screen"]),
            ("scroll_lock", ["scroll_lock"]),
            ("num_lock", ["num_lock"]),
        ],
    )
    def test_complex_valid_combos_accepted_and_committed(self, qtbot, complex_valid_combo, expected_keys):
        """Verify that sophisticated valid modifier and navigation key combinations commit properly."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-key-accept",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        inspector.form_key.edit_keys.setText(complex_valid_combo)

        assert inspector.form_key.is_valid()
        vals = inspector.form_key.get_values()
        assert vals is not None
        assert vals.keys == expected_keys
        assert "✓" in inspector.lbl_status.text()
        assert act.params.keys == expected_keys

    @pytest.mark.parametrize(
        "literal_plus_input,expected_keys",
        [
            ("Ctrl++", ["ctrl", "+"]),
            ("Alt++", ["alt", "+"]),
            ("Shift++", ["shift", "+"]),
            ("Ctrl+Alt++", ["ctrl", "alt", "+"]),
            ("ctrl+shift++", ["ctrl", "shift", "+"]),
            ("  ctrl  +  +  ", ["ctrl", "+"]),
        ],
    )
    def test_literal_plus_combinations(self, qtbot, literal_plus_input, expected_keys):
        """Verify zoom/plus key combinations parse the literal '+' symbol correctly."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-plus",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["a"], action="press"),
        )
        inspector.set_action(act)

        inspector.form_key.edit_keys.setText(literal_plus_input)

        if expected_keys is not None:
            assert inspector.form_key.is_valid()
            vals = inspector.form_key.get_values()
            assert vals is not None
            assert vals.keys == expected_keys
            assert "✓" in inspector.lbl_status.text()
            assert act.params.keys == expected_keys

    def test_unicode_and_special_character_resilience(self, qtbot):
        """Test non-ASCII and special characters are handled safely without unhandled exceptions."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-unicode",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        test_inputs = [
            "ctrl+한글",
            "alt+🔥",
            "shift+§",
            "cmd+©",
            "ctrl+日本語",
        ]

        for text in test_inputs:
            # Must not raise exception
            inspector.form_key.edit_keys.setText(text)
            # Either valid or invalid, but inspector status and state must remain coherent
            valid = inspector.form_key.is_valid()
            if valid:
                assert "✓" in inspector.lbl_status.text()
                assert inspector.form_key.get_values() is not None
            else:
                assert "⚠" in inspector.lbl_status.text()

    def test_realtime_keystroke_typing_simulation(self, qtbot):
        """
        Simulate character-by-character typing of 'ctrl+shift+delete' followed by backspacing.
        Ensures that intermediate partial states (e.g. 'ctrl+', 'ctrl+shift+') transition
        validation states cleanly without unhandled exceptions or state latching.
        """
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-typing",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["x"], action="press"),
        )
        inspector.set_action(act)

        full_string = "ctrl+shift+delete"
        typed = ""

        # Typing forward
        for char in full_string:
            typed += char
            inspector.form_key.edit_keys.setText(typed)
            # Simply check that status text and validation state are updated without exception
            assert inspector.lbl_status.text() != ""

        # At the end of typing, must be valid
        assert inspector.form_key.is_valid()
        assert inspector.form_key.get_values().keys == ["ctrl", "shift", "delete"]
        assert "✓" in inspector.lbl_status.text()

        # Backspacing backwards
        while len(typed) > 0:
            typed = typed[:-1]
            inspector.form_key.edit_keys.setText(typed)
            assert inspector.lbl_status.text() != ""

        # At empty string, must be invalid
        assert not inspector.form_key.is_valid()
        assert "⚠" in inspector.lbl_status.text()

    def test_key_combo_helper_rapid_interactions(self, qtbot):
        """Rapidly toggle helper checkboxes and click 'Insert Combo' repeatedly."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="act-helper",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        form = inspector.form_key

        # 1. Ctrl + F5
        form.chk_ctrl.setChecked(True)
        form.chk_alt.setChecked(False)
        form.chk_shift.setChecked(False)
        idx_f5 = form.combo_preset_keys.findText("f5")
        form.combo_preset_keys.setCurrentIndex(idx_f5)
        form._apply_combo_helper()

        assert form.is_valid()
        assert form.get_values().keys == ["cmd_or_ctrl", "f5"]

        # 2. Ctrl + Shift + Esc
        form.chk_shift.setChecked(True)
        idx_esc = form.combo_preset_keys.findText("esc")
        form.combo_preset_keys.setCurrentIndex(idx_esc)
        form._apply_combo_helper()

        assert form.is_valid()
        assert form.get_values().keys == ["cmd_or_ctrl", "shift", "esc"]

        # 3. All modifiers + Tab
        form.chk_alt.setChecked(True)
        idx_tab = form.combo_preset_keys.findText("tab")
        form.combo_preset_keys.setCurrentIndex(idx_tab)
        form._apply_combo_helper()

        assert form.is_valid()
        assert form.get_values().keys == ["cmd_or_ctrl", "alt", "shift", "tab"]


# ===========================================================================
# 4. INTEGRATED SCENARIO BUILDER & PROPERTY INSPECTOR STRESS
# ===========================================================================

class TestIntegratedBuilderAndInspectorStress:
    """Verify live synchronization between PropertyInspector and ScenarioBuilder cards."""

    def test_edit_mouse_click_and_verify_card_summary_sync(self, qtbot):
        """Mutate mouse parameters in inspector and verify card summary label updates dynamically."""
        builder = ScenarioBuilder()
        inspector = PropertyInspector()
        qtbot.addWidget(builder)
        qtbot.addWidget(inspector)

        builder.block_selected.connect(inspector.set_action)
        inspector.action_modified.connect(builder.update_action_block)

        act = builder.add_action_block(ActionType.MOUSE_CLICK)
        builder.select_action(act.id)

        # Modify coordinates in inspector
        inspector.form_mouse.spin_x.setValue(1234)
        inspector.form_mouse.spin_y.setValue(5678)
        inspector.form_mouse.spin_clicks.setValue(2)

        card = builder._card_map[act.id]
        assert card.lbl_summary.text() == "Double-click left at (1234, 5678)"

    def test_edit_fixed_wait_and_verify_card_summary_sync(self, qtbot):
        """Mutate fixed wait duration in inspector and verify card summary updates dynamically."""
        builder = ScenarioBuilder()
        inspector = PropertyInspector()
        qtbot.addWidget(builder)
        qtbot.addWidget(inspector)

        builder.block_selected.connect(inspector.set_action)
        inspector.action_modified.connect(builder.update_action_block)

        act = builder.add_action_block(ActionType.FIXED_WAIT)
        builder.select_action(act.id)

        inspector.form_wait.spin_duration.setValue(3500)

        card = builder._card_map[act.id]
        assert card.lbl_summary.text() == "Wait 3.5 s"

    def test_delete_inspected_card_under_active_modification(self, qtbot):
        """
        Select and inspect an action. Delete it from the builder.
        Ensure inspector switches to empty or remaining card, and modifying inspector
        afterward causes no corruption or zombie actions.
        """
        builder = ScenarioBuilder()
        inspector = PropertyInspector()
        qtbot.addWidget(builder)
        qtbot.addWidget(inspector)

        builder.block_selected.connect(inspector.set_action)
        inspector.action_modified.connect(builder.update_action_block)

        a1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        builder.select_action(a1.id)
        assert inspector.current_action.id == a1.id

        # Delete the only action
        builder.delete_action(a1.id)

        assert builder.list_widget.count() == 0
        assert inspector.current_action is None
        assert inspector.stacked_widget.currentWidget() == inspector.page_empty

        # Mutate inspector fields
        inspector.edit_description.setText("Zombie description")
        inspector._commit_changes()

        assert builder.list_widget.count() == 0
        assert len(builder.scenario.actions) == 0
