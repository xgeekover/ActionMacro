"""
tests.unit.test_challenger_m4_it2_stress
========================================
Milestone 4 Iteration 2 Adversarial Stress Testing Suite.
Empirically stress-tests:
1. Rapid drag & drop order reversals (e.g. 10+ consecutive drop events).
2. Rapid scenario re-population with active executions.
3. Malformed key combinations with multiple whitespace and delimiter patterns ('  +  +  ', 'Ctrl++', 'Alt+').
4. Complete remediation verification of previous 4 vulnerabilities.
"""

from __future__ import annotations

import time
import pytest
from PySide6.QtCore import Qt
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
# 1. RAPID DRAG & DROP ORDER REVERSALS
# ===========================================================================

class TestRapidDragAndDropReversals:
    """Stress testing repeated and rapid drag & drop reordering in ScenarioBuilder."""

    def test_ten_consecutive_drop_reversals(self, qtbot):
        """
        Perform 10 consecutive drop events moving the top item to the bottom.
        Verifies:
        - itemWidget is never None after any move.
        - Card widget IDs match list item UserRole data.
        - lbl_index text reflects 1-based order (#1, #2, ...).
        - btn_up and btn_down enablement is correct after each cycle.
        - ScenarioModel.actions perfectly mirrors the QListWidget order.
        """
        scenario = ScenarioModel(name="DnD Stress 10")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        # Create 4 distinct action blocks
        types = [ActionType.MOUSE_CLICK, ActionType.KEY_INPUT, ActionType.FIXED_WAIT, ActionType.IMAGE_WAIT]
        for t in types:
            builder.add_action_block(t)

        assert builder.list_widget.count() == 4
        assert len(scenario.actions) == 4

        for cycle in range(10):
            # Take top item and insert at bottom (simulating drag-and-drop InternalMove)
            item = builder.list_widget.takeItem(0)
            builder.list_widget.insertItem(3, item)
            builder._sync_model_from_list_order()

            # Verify every row
            for row in range(4):
                curr_item = builder.list_widget.item(row)
                card = builder.list_widget.itemWidget(curr_item)
                assert card is not None, f"Cycle {cycle}: Row {row} itemWidget is None!"
                act_id = curr_item.data(Qt.ItemDataRole.UserRole)
                assert card.action_id == act_id, f"Cycle {cycle}: Row {row} card ID mismatch!"
                assert card.lbl_index.text() == f"#{row + 1}"
                assert scenario.actions[row].id == act_id

                # Boundary buttons
                if row == 0:
                    assert not card.btn_up.isEnabled()
                    assert card.btn_down.isEnabled()
                elif row == 3:
                    assert card.btn_up.isEnabled()
                    assert not card.btn_down.isEnabled()
                else:
                    assert card.btn_up.isEnabled()
                    assert card.btn_down.isEnabled()

    def test_bottom_to_top_consecutive_drop_reversals(self, qtbot):
        """Perform 10 consecutive drop events moving the bottom item to the top."""
        scenario = ScenarioModel(name="DnD Bottom-to-Top Stress")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        for t in [ActionType.MOUSE_CLICK, ActionType.KEY_INPUT, ActionType.FIXED_WAIT, ActionType.IMAGE_WAIT, ActionType.MOUSE_CLICK]:
            builder.add_action_block(t)

        tot = builder.list_widget.count()
        assert tot == 5

        for cycle in range(10):
            item = builder.list_widget.takeItem(tot - 1)
            builder.list_widget.insertItem(0, item)
            builder._sync_model_from_list_order()

            for row in range(tot):
                curr_item = builder.list_widget.item(row)
                card = builder.list_widget.itemWidget(curr_item)
                assert card is not None, f"Cycle {cycle}: Row {row} itemWidget is None!"
                act_id = curr_item.data(Qt.ItemDataRole.UserRole)
                assert card.action_id == act_id
                assert scenario.actions[row].id == act_id
                assert card.lbl_index.text() == f"#{row + 1}"

    def test_alternating_inner_reversals(self, qtbot):
        """Perform alternating swaps between internal indices (e.g. index 1 and index 2)."""
        scenario = ScenarioModel(name="DnD Internal Swap")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        for t in [ActionType.MOUSE_CLICK, ActionType.KEY_INPUT, ActionType.FIXED_WAIT, ActionType.IMAGE_WAIT]:
            builder.add_action_block(t)

        for cycle in range(12):
            item = builder.list_widget.takeItem(1)
            builder.list_widget.insertItem(2, item)
            builder._sync_model_from_list_order()

            assert builder.list_widget.count() == 4
            for row in range(4):
                curr_item = builder.list_widget.item(row)
                card = builder.list_widget.itemWidget(curr_item)
                assert card is not None
                assert card.action_id == curr_item.data(Qt.ItemDataRole.UserRole)
                assert scenario.actions[row].id == card.action_id

    def test_empty_and_single_item_drop_resilience(self, qtbot):
        """Calling _sync_model_from_list_order on empty list or 1-item list does not fail."""
        scenario = ScenarioModel(name="DnD Degenerate")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        # Empty list sync
        builder._sync_model_from_list_order()
        assert len(scenario.actions) == 0
        assert builder.list_widget.count() == 0

        # 1-item list sync
        act = builder.add_action_block(ActionType.MOUSE_CLICK)
        builder._sync_model_from_list_order()
        assert len(scenario.actions) == 1
        item = builder.list_widget.item(0)
        card = builder.list_widget.itemWidget(item)
        assert card is not None
        assert not card.btn_up.isEnabled()
        assert not card.btn_down.isEnabled()


# ===========================================================================
# 2. RAPID SCENARIO RE-POPULATION WITH ACTIVE EXECUTIONS
# ===========================================================================

class TestRapidRepopulationWithActiveExecutions:
    """Stress testing scenario repopulation while cards are marked active / executing."""

    def test_rapid_repopulation_with_active_highlights(self, qtbot):
        """
        25 cycles of:
        1. Set active executing card
        2. Replace scenario with a new scenario of variable length (including 0 actions)
        3. Verify _active_index is properly cleared and card executing states are reset
        4. Simulate arriving runner signals (highlight_active_action, clear_active_action)
        """
        scenario = ScenarioModel(name="Initial Scenario")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        for _ in range(6):
            builder.add_action_block(ActionType.MOUSE_CLICK)

        current_scenario = scenario

        for cycle in range(25):
            curr_len = len(current_scenario.actions)
            if curr_len > 0:
                active_idx = cycle % curr_len
                builder.set_active_action(active_idx)
                assert builder._active_index == active_idx
                active_act_id = current_scenario.actions[active_idx].id
                assert builder._card_map[active_act_id].is_executing is True

            # Create a new scenario of varying length: 0 to 8 actions
            new_len = (cycle * 5) % 9
            new_scenario = ScenarioModel(name=f"Scenario Cycle {cycle}")
            for i in range(new_len):
                new_scenario.actions.append(
                    ActionBlock(id=f"act-c{cycle}-i{i}", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=100))
                )

            # Repopulate
            builder.set_scenario(new_scenario)
            current_scenario = new_scenario

            # Post-condition 1: _active_index must be None
            assert builder._active_index is None, f"Cycle {cycle}: _active_index remained {builder._active_index}!"

            # Post-condition 2: No card in new scenario should be executing
            for cid, card in builder._card_map.items():
                assert not card.is_executing, f"Cycle {cycle}: card {cid} unexpectedly marked is_executing!"

            # Post-condition 3: Interleaving arriving runner signals does not crash or corrupt
            target_signal_idx = cycle % 7
            builder.highlight_active_action(target_signal_idx)
            if target_signal_idx < new_len:
                assert builder._active_index == target_signal_idx
            else:
                assert builder._active_index is None

            builder.clear_active_action()
            assert builder._active_index is None

    def test_repopulation_to_empty_and_back_repeatedly(self, qtbot):
        """Repeatedly toggle between 5 actions and 0 actions while active highlight is toggled."""
        scenario = ScenarioModel(name="Toggle Empty Scenario")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        for cycle in range(10):
            # Fill with 5
            full_scenario = ScenarioModel(name="Full")
            for i in range(5):
                full_scenario.actions.append(
                    ActionBlock(id=f"full-{i}", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["enter"]))
                )
            builder.set_scenario(full_scenario)
            builder.set_active_action(2)
            assert builder._active_index == 2
            assert builder.list_widget.count() == 5
            assert builder.lbl_empty.isHidden()

            # Empty
            empty_scenario = ScenarioModel(name="Empty")
            builder.set_scenario(empty_scenario)
            assert builder._active_index is None
            assert builder.list_widget.count() == 0
            assert not builder.lbl_empty.isHidden()
            assert builder.list_widget.isHidden()

            # Calling clear or highlight on empty must be safe
            builder.clear_active_action()
            builder.highlight_active_action(None)
            builder.highlight_active_action(-1)
            builder.highlight_active_action(0)
            builder.set_active_action(0)
            assert builder._active_index is None


# ===========================================================================
# 3. MALFORMED KEY COMBINATIONS AND DELIMITER PATTERNS
# ===========================================================================

class TestMalformedKeyCombinationsAndDelimiterPatterns:
    """Stress testing PropertyInspector with whitespace, delimiter, and adversarial key inputs."""

    @pytest.mark.parametrize(
        "invalid_input",
        [
            "",
            "   ",
            "\t\n  ",
            "+",
            "++",
            "+++",
            "++++",
            "  +  +  ",
            "+ + +",
            "+   +",
            "+\t+\n+",
            "  + \t +  ",
        ],
    )
    def test_malformed_delimiter_only_inputs_are_invalid_and_not_committed(self, qtbot, invalid_input):
        """
        Delimiter-only and whitespace-only strings must:
        - Result in get_values() returning None.
        - Result in is_valid() returning False.
        - Display '⚠' error status in PropertyInspector.
        - NEVER corrupt the existing ActionBlock.params.
        """
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="test-key-act",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        # Set invalid input
        inspector.form_key.edit_keys.setText(invalid_input)

        # Assert UI status and validation consistency
        assert not inspector.form_key.is_valid()
        assert inspector.form_key.get_values() is None
        assert "⚠" in inspector.lbl_status.text()
        assert "✓" not in inspector.lbl_status.text()

        # Assert no silent corruption: params remain pristine
        assert act.params.keys == ["enter"]

    @pytest.mark.parametrize(
        "valid_input,expected_keys",
        [
            ("Ctrl++", ["ctrl", "+"]),
            ("Alt+", ["alt", "+"]),
            ("Ctrl+Alt++", ["ctrl", "alt", "+"]),
            ("ctrl+shift+a", ["ctrl", "shift", "a"]),
            ("   Ctrl   +   v   ", ["ctrl", "v"]),
            ("cmd_or_ctrl + c", ["cmd_or_ctrl", "c"]),
            ("enter", ["enter"]),
            ("esc", ["esc"]),
            ("F11", ["f11"]),
            ("up", ["up"]),
            ("ctrl+한", ["ctrl", "한"]),  # one character is a key; a word ("한글") is not
            ("+a+", ["a", "+"]),
            ("ctrl+", ["ctrl", "+"]),
        ],
    )
    def test_valid_and_symbol_plus_inputs_are_properly_committed(self, qtbot, valid_input, expected_keys):
        """
        Valid key combinations (including literal '+' symbol combos like Ctrl++) must:
        - Result in get_values() returning KeyInputParams with expected keys.
        - Result in is_valid() returning True.
        - Display '✓' valid status.
        - Commit the new keys to ActionBlock.params.
        """
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="test-key-act",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        inspector.form_key.edit_keys.setText(valid_input)

        assert inspector.form_key.is_valid()
        values = inspector.form_key.get_values()
        assert values is not None
        assert values.keys == expected_keys
        assert "✓" in inspector.lbl_status.text()
        assert act.params.keys == expected_keys

    def test_symmetric_state_transition(self, qtbot):
        """
        Rapidly toggle back and forth between invalid ('  +  +  ') and valid ('Ctrl++') inputs.
        Verify that validation state, status text, and ActionBlock params transition symmetrically
        without getting stuck or corrupted.
        """
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(
            id="test-key-act",
            type=ActionType.KEY_INPUT,
            params=KeyInputParams(keys=["enter"], action="press"),
        )
        inspector.set_action(act)

        for cycle in range(10):
            # Invalid turn
            inspector.form_key.edit_keys.setText("  +  +  ")
            assert "⚠" in inspector.lbl_status.text()
            assert inspector.form_key.get_values() is None
            # Params should keep previous valid value
            if cycle == 0:
                assert act.params.keys == ["enter"]
            else:
                assert act.params.keys == ["ctrl", "+"]

            # Valid turn
            inspector.form_key.edit_keys.setText("Ctrl++")
            assert "✓" in inspector.lbl_status.text()
            assert inspector.form_key.get_values() is not None
            assert act.params.keys == ["ctrl", "+"]


# ===========================================================================
# 4. REMEDIATION CONFIRMATION & REGRESSION CHECK
# ===========================================================================

class TestRemediationConfirmation:
    """Verifies that all 4 previous vulnerabilities remain 100% resolved under adversarial conditions."""

    def test_vulnerability_1_dnd_item_widget_retention(self, qtbot):
        """Vulnerability 1: Drag & drop must retain ActionCardWidget on moved items."""
        scenario = ScenarioModel(name="V1 Check")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        a1 = builder.add_action_block(ActionType.MOUSE_CLICK)
        a2 = builder.add_action_block(ActionType.KEY_INPUT)

        item = builder.list_widget.takeItem(0)
        builder.list_widget.insertItem(1, item)
        builder._sync_model_from_list_order()

        assert builder.list_widget.itemWidget(item) is not None
        assert scenario.actions[0].id == a2.id
        assert scenario.actions[1].id == a1.id

    def test_vulnerability_2_key_form_sync_discrepancy(self, qtbot):
        """Vulnerability 2: Malformed key inputs must never show '✓ Valid' while dropping commits."""
        inspector = PropertyInspector()
        qtbot.addWidget(inspector)

        act = ActionBlock(id="v2", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["enter"]))
        inspector.set_action(act)

        inspector.form_key.edit_keys.setText("+++")
        assert "✓" not in inspector.lbl_status.text()
        assert "⚠" in inspector.lbl_status.text()
        assert inspector.form_key.get_values() is None

    def test_vulnerability_3_active_index_state_leak(self, qtbot):
        """Vulnerability 3: populate_from_scenario must reset _active_index."""
        scenario = ScenarioModel(name="V3 Check")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        builder.add_action_block(ActionType.MOUSE_CLICK)
        builder.set_active_action(0)
        assert builder._active_index == 0

        builder.populate_from_scenario(ScenarioModel(name="Empty"))
        assert builder._active_index is None

    def test_vulnerability_4_none_sentinel_handling(self, qtbot):
        """Vulnerability 4: Passing None to highlight_active_action and set_active_action must not crash."""
        scenario = ScenarioModel(name="V4 Check")
        builder = ScenarioBuilder(scenario=scenario)
        qtbot.addWidget(builder)

        builder.add_action_block(ActionType.MOUSE_CLICK)

        # Must safely execute without TypeError
        builder.set_active_action(None)
        assert builder._active_index is None

        builder.highlight_active_action(None)
        assert builder._active_index is None
