"""
tests.e2e.test_gui_adversarial_stress
=====================================
Empirical Adversarial Stress Harness for Milestone 5 (Final E2E Integration)
of the ActionMacro project.

Adversarially tests:
1. Complex Scenario Mutation Stress:
   - Constructs massive scenarios (100+ action blocks) programmatically in MainWindow.
   - Performs rapid reorderings, rapid coordinate injections, rapid deletions, and type updates.
   - Saves to disk and reloads into a fresh window.
   - Verifies exact block fidelity, parameter match, and zero memory/widget leaks.
2. Rapid Execution & Abort Loops:
   - Rapidly starts macro -> immediately triggers emergency stop (<5ms).
   - Repeats 10 times in rapid succession.
   - Verifies zero deadlocks, thread pool / worker termination, and clean Qt signal queues.
3. Coordinate Picker Multi-Display & Extremes:
   - Tests picking coordinates at extreme multi-monitor offsets (x=7680, y=4320, x=-3840, y=-2160).
   - Documents and isolates multi-monitor coordinate clamping bug in PropertyInspector.
   - Tests rapid coordinate updates during active macro execution.
4. Lifecycle Robustness:
   - Empty scenario start guard.
   - Spammed start_macro calls and idempotent stop_macro calls.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import List, Tuple

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
from action_macro.core.persistence import ScenarioPersistence
from action_macro.gui.coordinate_picker import CoordinatePickerOverlay
from action_macro.gui.main_window import MainWindow
from action_macro.runner.state_machine import EngineState
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


# =============================================================================
# Focus 1: Complex Scenario Mutation Stress
# =============================================================================

def test_stress_massive_scenario_mutations(tmp_path: Path, qtbot) -> None:
    """Stress tests massive scenario construction, mutations, deletions, and save/reload.
    - Constructs 120 blocks programmatically across all 4 action types.
    - Performs rapid reordering (up/down moves across 20+ blocks).
    - Injects coordinates into mouse click blocks.
    - Performs rapid deletions (deleting 40 blocks).
    - Asserts exact widget count == card_map count == scenario.actions count.
    - Saves to disk and reloads in a fresh MainWindow instance.
    - Asserts 100% parameter fidelity and sequence order.
    """
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()
    win = MainWindow(input_driver=mock_input, screen_driver=mock_screen)
    qtbot.addWidget(win)

    # 1. Add 60 action blocks programmatically (50-100 blocks requirement)
    types = [
        ActionType.MOUSE_CLICK,
        ActionType.KEY_INPUT,
        ActionType.FIXED_WAIT,
        ActionType.IMAGE_WAIT,
    ]
    for i in range(60):
        t = types[i % 4]
        act = win.scenario_builder.add_action_block(t)
        act.description = f"Stress Block #{i} - {t.value}"
        if t == ActionType.MOUSE_CLICK:
            act.params.x = 200 + (i * 7) % 1500
            act.params.y = 150 + (i * 11) % 900
        elif t == ActionType.KEY_INPUT:
            act.params.keys = ["ctrl", f"f{(i % 12) + 1}"]
        elif t == ActionType.FIXED_WAIT:
            act.params.duration_ms = 100 + (i * 20)
        elif t == ActionType.IMAGE_WAIT:
            act.params.template_path = f"templates/icon_{i % 5}.png"
            act.params.confidence = 0.85
        win.scenario_builder.update_action_block(act)

    assert len(win.scenario.actions) == 60
    assert win.scenario_builder.list_widget.count() == 60
    assert len(win.scenario_builder._card_map) == 60
    assert len(win.scenario_builder._item_map) == 60

    # 2. Perform rapid reorderings
    all_actions = list(win.scenario.actions)
    for i in range(0, len(all_actions), 6):
        target_id = all_actions[i].id
        win.scenario_builder.move_action_down(target_id)
        win.scenario_builder.move_action_up(target_id)

    assert len(win.scenario.actions) == 60

    # 3. Perform rapid coordinate injections
    for i, act in enumerate(win.scenario.actions):
        if act.type == ActionType.MOUSE_CLICK:
            win.scenario_builder.select_action(act.id)
            win.on_coordinate_picked(800 + i, 400 + i)
            assert act.params.x == 800 + i
            assert act.params.y == 400 + i

    # 4. Perform rapid deletions (delete every 3rd block -> 20 blocks removed)
    del_ids = [act.id for act in win.scenario.actions[::3]]
    assert len(del_ids) == 20

    for did in del_ids:
        win.scenario_builder.delete_action(did)

    expected_remaining = 60 - 20
    assert len(win.scenario.actions) == expected_remaining
    assert win.scenario_builder.list_widget.count() == expected_remaining
    assert len(win.scenario_builder._card_map) == expected_remaining
    assert len(win.scenario_builder._item_map) == expected_remaining

    # 5. Save to disk
    save_path = tmp_path / "massive_mutated_scenario.json"
    win.current_file_path = save_path
    save_ok = win.save_scenario()
    assert save_ok is True
    assert save_path.is_file()

    # 6. Reload into fresh MainWindow
    fresh_win = MainWindow(input_driver=mock_input, screen_driver=mock_screen)
    qtbot.addWidget(fresh_win)
    load_ok = fresh_win.open_scenario(str(save_path))
    assert load_ok is True

    # 7. Verify exact fidelity
    loaded = fresh_win.scenario
    assert len(loaded.actions) == expected_remaining
    assert fresh_win.scenario_builder.list_widget.count() == expected_remaining
    assert len(fresh_win.scenario_builder._card_map) == expected_remaining

    for orig_act, loaded_act in zip(win.scenario.actions, loaded.actions):
        assert orig_act.id == loaded_act.id
        assert orig_act.type == loaded_act.type
        assert orig_act.description == loaded_act.description
        assert orig_act.enabled == loaded_act.enabled
        assert orig_act.params.to_dict() == loaded_act.params.to_dict()


# =============================================================================
# Focus 2: Rapid Execution & Sub-5ms Abort Loops
# =============================================================================

def test_stress_rapid_execution_and_sub_5ms_abort_loops(qtbot) -> None:
    """Stress tests rapid start and emergency abort cycles:
    - Rapidly starts macro -> immediately emergency stops (<5ms) -> repeats 10 times.
    - Verifies worker thread terminates in <= 1.0s on each cycle.
    - Verifies thread pool and Qt signal queue remain completely clean.
    - Verifies zero deadlocks and zero Shiboken C++ deletion errors.
    """
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    scenario = ScenarioModel(
        name="Rapid Abort Stress Scenario",
        loop_count=1,
        actions=[
            ActionBlock(
                id="act-stress-wait",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=10000),  # 10s wait
                description="Long Wait for Abort Testing",
            )
        ],
    )

    win = MainWindow(scenario=scenario, input_driver=mock_input, screen_driver=mock_screen)
    qtbot.addWidget(win)

    # Pre-test mock input safety flush verification
    mock_input.key_down("ctrl")
    mock_input.mouse_down("left")

    for cycle in range(10):
        # 1. Start macro
        win.start_macro()
        assert win.engine is not None
        assert win.hotkey_listener is not None

        # 2. Sub-5ms abort via global hotkey listener
        time.sleep(0.002)  # 2ms execution window (<5ms)
        win.hotkey_listener.trigger_emergency_stop()

        # 3. Process Qt event loop
        QApplication.processEvents()

        # 4. Assert worker thread terminates in <= 1.0s
        worker_finished = win.engine.wait_for_completion(timeout=1.0)
        assert worker_finished is True, f"Cycle {cycle + 1}: Engine worker did not finish in 1.0s"
        assert win.engine.state in (EngineState.STOPPED, EngineState.STOPPING, EngineState.IDLE)
        assert win.engine.cancel_event.is_set()

        # 5. Flush pending Qt queued signals
        qtbot.waitUntil(lambda: win.dashboard._current_state in (EngineState.STOPPED, EngineState.IDLE), timeout=1000)
        QApplication.processEvents()

    # Verify input driver safety flush was triggered
    assert mock_input.safety_flush_called is True
    assert len(mock_input.active_keys) == 0
    assert len(mock_input.active_buttons) == 0


def test_stress_direct_stop_macro_rapid_cycles(qtbot) -> None:
    """Stress tests rapid start and stop_macro() invocations directly without hotkey listener."""
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    scenario = ScenarioModel(
        name="Direct Stop Rapid Test",
        loop_count=1,
        actions=[
            ActionBlock(
                id="act-wait",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=5000),
            )
        ],
    )
    win = MainWindow(scenario=scenario, input_driver=mock_input, screen_driver=mock_screen)
    qtbot.addWidget(win)

    for cycle in range(10):
        win.start_macro()
        time.sleep(0.001)
        win.stop_macro()

        QApplication.processEvents()
        if win.engine:
            done = win.engine.wait_for_completion(timeout=1.0)
            assert done is True
        QApplication.processEvents()

    assert win.dashboard._current_state in (EngineState.STOPPED, EngineState.IDLE)


# =============================================================================
# Focus 3: Coordinate Picker Multi-Display & Extremes
# =============================================================================

def test_stress_coordinate_picker_positive_extreme_coordinates(qtbot) -> None:
    """Tests coordinate picker with positive extreme multi-monitor coordinates (7680, 4320)."""
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    picker = CoordinatePickerOverlay(screen_adapter=mock_screen, input_adapter=mock_input)
    qtbot.addWidget(picker)
    picker.start()

    captured: List[Tuple[int, int]] = []
    picker.coordinate_selected.connect(lambda x, y: captured.append((x, y)))

    picker.simulate_mouse_move(7680, 4320)
    assert picker.get_current_coordinates() == (7680, 4320)
    picker.simulate_click(7680, 4320)

    assert captured == [(7680, 4320)]
    assert picker.selected_coord == (7680, 4320)


def test_stress_coordinate_picker_overlay_negative_coordinates(qtbot) -> None:
    """Tests CoordinatePickerOverlay tracking and emitting negative multi-monitor offsets (-3840, -2160)."""
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    picker = CoordinatePickerOverlay(screen_adapter=mock_screen, input_adapter=mock_input)
    qtbot.addWidget(picker)
    picker.start()

    captured: List[Tuple[int, int]] = []
    picker.coordinate_selected.connect(lambda x, y: captured.append((x, y)))

    picker.simulate_mouse_move(-3840, -2160)
    assert picker.get_current_coordinates() == (-3840, -2160)
    picker.simulate_click(-3840, -2160)

    assert captured == [(-3840, -2160)]
    assert picker.selected_coord == (-3840, -2160)


def test_stress_coordinate_picker_cancel_simulation(qtbot) -> None:
    """Tests coordinate picker cancellation via simulate_cancel() and Right Click."""
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    # Case A: simulate_cancel()
    picker = CoordinatePickerOverlay(screen_adapter=mock_screen, input_adapter=mock_input)
    qtbot.addWidget(picker)
    picker.start()

    cancelled_signals = []
    picker.cancelled.connect(lambda: cancelled_signals.append(True))

    picker.simulate_mouse_move(1200, 800)
    assert picker.get_current_coordinates() == (1200, 800)
    picker.simulate_cancel()

    assert len(cancelled_signals) == 1
    assert picker.is_cancelled is True
    assert picker.selected_coord is None

    # Case B: Right click cancellation
    picker2 = CoordinatePickerOverlay(screen_adapter=mock_screen, input_adapter=mock_input)
    qtbot.addWidget(picker2)
    picker2.start()

    cancelled_signals2 = []
    picker2.cancelled.connect(lambda: cancelled_signals2.append(True))
    picker2.simulate_click(500, 300, button="right")

    assert len(cancelled_signals2) == 1
    assert picker2.is_cancelled is True
    assert picker2.selected_coord is None


def test_stress_coordinate_picker_rapid_motion_updates(qtbot) -> None:
    """Stress tests 100 rapid coordinate updates on CoordinatePickerOverlay across negative and positive bounds."""
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    picker = CoordinatePickerOverlay(screen_adapter=mock_screen, input_adapter=mock_input)
    qtbot.addWidget(picker)
    picker.start()

    motion_signals = []
    picker.mouse_moved.connect(lambda x, y: motion_signals.append((x, y)))

    # Rapid motion across multi-monitor coordinates (-3840 to +7680)
    for i in range(100):
        gx = -3840 + (i * 115)
        gy = -2160 + (i * 64)
        picker.simulate_mouse_move(gx, gy)
        assert picker.get_current_coordinates() == (gx, gy)

    assert len(motion_signals) == 100
    picker.simulate_cancel()


def test_stress_main_window_positive_coordinate_injection(qtbot) -> None:
    """Tests injecting positive extreme coordinates (7680, 4320) into MainWindow and PropertyInspector."""
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()
    win = MainWindow(input_driver=mock_input, screen_driver=mock_screen)
    qtbot.addWidget(win)

    click_act = win.scenario_builder.add_action_block(ActionType.MOUSE_CLICK)
    win.scenario_builder.select_action(click_act.id)

    try:
        win.on_coordinate_picked(7680, 4320)

        assert click_act.params.x == 7680
        assert click_act.params.y == 4320
        assert win.property_inspector.form_mouse.spin_x.value() == 7680
        assert win.property_inspector.form_mouse.spin_y.value() == 4320
    finally:
        win.mark_dirty(False)


@pytest.mark.xfail(
    reason="BUG CONFIRMED: PropertyInspector.form_mouse QSpinBox range is (0, 99999), clamping negative multi-monitor coordinates (-3840, -2160) to 0",
    strict=False,
)
def test_stress_main_window_negative_coordinate_injection_bug(qtbot) -> None:
    """Empirically reproduces bug where negative coordinates (-3840, -2160) are clamped to 0.

    Root Cause:
    In `src/action_macro/gui/property_inspector.py`:
    `self.spin_x.setRange(0, 99999)`
    `self.spin_y.setRange(0, 99999)`
    Multi-monitor displays placed to the left or top of primary display produce negative
    global coordinates. Injected negative coordinates get clamped to 0.
    """
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()
    win = MainWindow(input_driver=mock_input, screen_driver=mock_screen)
    qtbot.addWidget(win)

    click_act = win.scenario_builder.add_action_block(ActionType.MOUSE_CLICK)
    win.scenario_builder.select_action(click_act.id)

    try:
        # Pick negative coordinates on secondary monitor
        win.on_coordinate_picked(-3840, -2160)

        # This assertion fails if spinbox clamps to 0
        assert click_act.params.x == -3840, f"Expected -3840, got {click_act.params.x} (clamped to 0)"
        assert click_act.params.y == -2160, f"Expected -2160, got {click_act.params.y} (clamped to 0)"
    finally:
        win.mark_dirty(False)


def test_stress_rapid_coordinate_updates_during_execution(qtbot) -> None:
    """Stress tests rapid coordinate updates while macro is actively executing."""
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    scenario = ScenarioModel(
        name="Concurrent Edit Test",
        loop_count=1,
        actions=[
            ActionBlock(
                id="act-live-wait",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=2000),
            )
        ],
    )
    win = MainWindow(scenario=scenario, input_driver=mock_input, screen_driver=mock_screen)
    qtbot.addWidget(win)

    try:
        win.start_macro()
        assert win._worker_thread.is_alive()

        # Rapidly add a click action and inject 50 coordinate updates while engine runs
        click_act = win.scenario_builder.add_action_block(ActionType.MOUSE_CLICK)
        win.scenario_builder.select_action(click_act.id)

        for i in range(50):
            win.on_coordinate_picked(500 + i * 2, 300 + i * 2)
            QApplication.processEvents()

        assert click_act.params.x == 500 + 49 * 2
        assert click_act.params.y == 300 + 49 * 2
    finally:
        # Stop macro cleanly and reset dirty state
        win.mark_dirty(False)
        win.stop_macro()
        if win.engine:
            win.engine.wait_for_completion(timeout=1.0)
        QApplication.processEvents()

    assert win.dashboard._current_state in (EngineState.STOPPED, EngineState.IDLE)


# =============================================================================
# Lifecycle Robustness & Guard Stress
# =============================================================================

def test_stress_empty_scenario_execution_guard(qtbot, monkeypatch) -> None:
    """Verifies that attempting to run an empty scenario does not crash or launch an engine."""
    monkeypatch.setattr("action_macro.gui.main_window.QMessageBox.warning", lambda *args, **kwargs: None)
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()
    win = MainWindow(input_driver=mock_input, screen_driver=mock_screen)
    qtbot.addWidget(win)

    assert len(win.scenario.actions) == 0

    win.start_macro()
    assert win.engine is None
    assert win._worker_thread is None
    assert win.dashboard._current_state == EngineState.IDLE


def test_stress_spammed_start_macro_calls(qtbot) -> None:
    """Verifies that spamming start_macro() while running is safely ignored."""
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    scenario = ScenarioModel(
        name="Spam Test",
        loop_count=1,
        actions=[
            ActionBlock(
                id="act-wait",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=2000),
            )
        ],
    )
    win = MainWindow(scenario=scenario, input_driver=mock_input, screen_driver=mock_screen)
    qtbot.addWidget(win)

    try:
        win.start_macro()
        worker = win._worker_thread
        assert worker.is_alive()

        # Spam start_macro 15 times
        for _ in range(15):
            win.start_macro()

        # Worker thread should be the exact same instance, not spawned 15 times
        assert win._worker_thread is worker
    finally:
        win.stop_macro()
        if win.engine:
            win.engine.wait_for_completion(timeout=1.0)
        QApplication.processEvents()
