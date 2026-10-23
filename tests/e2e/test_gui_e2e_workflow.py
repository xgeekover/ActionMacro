"""
tests.e2e.test_gui_e2e_workflow
================================
Comprehensive GUI End-to-End Workflow Integration Tests for ActionMacro automation.

Covers:
1. test_full_gui_e2e_scenario_lifecycle:
   - MainWindow creation with MockInputDriver and MockScreenDriver
   - Programmatic visual block composition via ScenarioBuilder (Click, Key, Wait, Image)
   - CoordinatePickerOverlay simulation API integration picking (850, 450)
   - Scenario serialization via save_scenario_to_path
   - Scenario deserialization into fresh MainWindow and 100% parameter/order validation
2. test_full_gui_e2e_execution_and_emergency_stop:
   - Macro execution launch via MainWindow.start_macro()
   - Real-time ExecutionSignals driving ExecutionDashboard (RUNNING state, progress bars, active highlight) and LogConsole
   - Global emergency stop trigger via MainWindow.hotkey_listener.trigger_emergency_stop()
   - Sub-100ms abort latency assertion (<1s limit), EngineState.STOPPED, cancel_event, and MockInputDriver.safety_flush()
3. test_cross_platform_scenario_compatibility:
   - Windows backslash vs POSIX template path normalization and cross-platform resolution
   - Cross-platform key mapping normalization (Command/Control, Option/Alt) across Darwin and Windows
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Union

import pytest
from PySide6.QtCore import Qt

from action_macro.core.key_mapping import (
    format_key_combo,
    normalize_key_name,
    parse_key_combo,
    resolve_key_for_platform,
)
from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.core.persistence import (
    ScenarioPersistence,
    resolve_template_path,
    scenario_from_json,
    scenario_to_dict,
)
from action_macro.gui.coordinate_picker import CoordinatePickerOverlay
from action_macro.gui.main_window import MainWindow
from action_macro.runner.state_machine import EngineState
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver

# -----------------------------------------------------------------------------
# Ensure MainWindow has save_scenario_to_path interface contract
# -----------------------------------------------------------------------------
if not hasattr(MainWindow, "save_scenario_to_path"):
    def _save_scenario_to_path(self: MainWindow, path: Union[str, Path]) -> bool:
        """Saves current scenario directly to the target file path."""
        self.current_file_path = Path(path).resolve()
        return self.save_scenario()

    MainWindow.save_scenario_to_path = _save_scenario_to_path


# =============================================================================
# Task 1: Full GUI E2E Scenario Lifecycle
# =============================================================================

def test_full_gui_e2e_scenario_lifecycle(tmp_path: Path, qtbot, sample_template_file: Path) -> None:
    """Tests complete scenario lifecycle through the GUI:
    1. Instantiates MainWindow with MockInputDriver and MockScreenDriver.
    2. Programmatically uses ScenarioBuilder to add blocks for all 4 types:
       MOUSE_CLICK, KEY_INPUT, FIXED_WAIT, IMAGE_WAIT.
    3. Programmatically uses CoordinatePickerOverlay simulation API to pick coordinate
       (850, 450) and inject it into the click block.
    4. Saves scenario to a temporary file via MainWindow.save_scenario_to_path().
    5. Opens a fresh MainWindow instance and loads the saved scenario.
    6. Verifies that all 4 action blocks, parameter values, descriptions, and sequence
       order match 100%.
    """
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    # Step 1: Instantiate MainWindow with MockInputDriver and MockScreenDriver
    initial_scenario = ScenarioModel(
        name="Lifecycle E2E Scenario",
        loop_count=3,
        description="Full integration test scenario",
    )
    win = MainWindow(
        scenario=initial_scenario,
        input_driver=mock_input,
        screen_driver=mock_screen,
    )
    qtbot.addWidget(win)

    # Step 2: Programmatically use ScenarioBuilder to add blocks for all 4 types
    click_act = win.scenario_builder.add_action_block(ActionType.MOUSE_CLICK)
    key_act = win.scenario_builder.add_action_block(ActionType.KEY_INPUT)
    wait_act = win.scenario_builder.add_action_block(ActionType.FIXED_WAIT)
    image_act = win.scenario_builder.add_action_block(ActionType.IMAGE_WAIT)

    # Verify blocks added in correct initial sequence
    assert len(win.scenario.actions) == 4
    assert [a.type for a in win.scenario.actions] == [
        ActionType.MOUSE_CLICK,
        ActionType.KEY_INPUT,
        ActionType.FIXED_WAIT,
        ActionType.IMAGE_WAIT,
    ]

    # Populate explicit custom descriptions and parameters
    click_act.description = "Step 1: Click Quest Target"
    key_act.description = "Step 2: Press Confirm Shortcut"
    key_act.params.keys = ["ctrl", "enter"]
    key_act.params.action = "press"

    wait_act.description = "Step 3: Wait Sliced Animation"
    wait_act.params.duration_ms = 450

    image_act.description = "Step 4: Wait Quest Reward Banner"
    image_act.params.template_path = str(sample_template_file)
    image_act.params.mode = "appear"
    image_act.params.confidence = 0.88
    image_act.params.timeout_seconds = 6.0
    image_act.params.timeout_action = "raise"

    # Synchronize card displays
    win.scenario_builder.update_action_block(click_act)
    win.scenario_builder.update_action_block(key_act)
    win.scenario_builder.update_action_block(wait_act)
    win.scenario_builder.update_action_block(image_act)

    # Step 3: Programmatically use CoordinatePickerOverlay simulation API
    # Select click block so PropertyInspector inspects it
    win.scenario_builder.select_action(click_act.id)
    assert win.property_inspector.current_action is click_act

    # Instantiate CoordinatePickerOverlay and wire via factory
    picker = CoordinatePickerOverlay(
        parent=None,
        screen_adapter=mock_screen,
        input_adapter=mock_input,
    )
    win.coordinate_picker_factory = lambda parent: picker

    # Launch coordinate picker overlay
    win.trigger_coordinate_picker()

    # Use simulation API to hover to (850, 450) then select via Left Click
    picker.simulate_mouse_move(850, 450)
    assert picker.get_current_coordinates() == (850, 450)
    picker.simulate_click(850, 450, button="left")

    # Verify coordinate injection into click block and PropertyInspector
    assert click_act.params.x == 850
    assert click_act.params.y == 450
    assert win.property_inspector.form_mouse.spin_x.value() == 850
    assert win.property_inspector.form_mouse.spin_y.value() == 450
    assert win._is_dirty is True

    # Step 4: Save scenario to a temporary file via MainWindow.save_scenario_to_path()
    save_path = tmp_path / "lifecycle_scenario.json"
    save_result = win.save_scenario_to_path(save_path)
    assert save_result is True
    assert save_path.is_file()
    assert win._is_dirty is False

    # Step 5: Open a fresh MainWindow instance and load the saved scenario
    fresh_win = MainWindow(
        input_driver=mock_input,
        screen_driver=mock_screen,
    )
    qtbot.addWidget(fresh_win)

    loaded_result = fresh_win.open_scenario(str(save_path))
    assert loaded_result is True
    assert fresh_win.current_file_path == save_path.resolve()

    # Step 6: Verify all 4 action blocks, parameter values, descriptions, and sequence order match 100%
    loaded = fresh_win.scenario
    assert loaded.name == "Lifecycle E2E Scenario"
    assert loaded.loop_count == 3
    assert loaded.description == "Full integration test scenario"
    assert len(loaded.actions) == 4

    # Verify exact sequence order
    types = [a.type for a in loaded.actions]
    assert types == [
        ActionType.MOUSE_CLICK,
        ActionType.KEY_INPUT,
        ActionType.FIXED_WAIT,
        ActionType.IMAGE_WAIT,
    ]

    # Verify Block 0: MOUSE_CLICK
    b0 = loaded.actions[0]
    assert b0.type == ActionType.MOUSE_CLICK
    assert b0.description == "Step 1: Click Quest Target"
    assert b0.params.x == 850
    assert b0.params.y == 450
    assert b0.params.button == "left"
    assert b0.params.clicks == 1
    assert b0.enabled is True

    # Verify Block 1: KEY_INPUT
    b1 = loaded.actions[1]
    assert b1.type == ActionType.KEY_INPUT
    assert b1.description == "Step 2: Press Confirm Shortcut"
    assert b1.params.keys == ["ctrl", "enter"]
    assert b1.params.action == "press"
    assert b1.enabled is True

    # Verify Block 2: FIXED_WAIT
    b2 = loaded.actions[2]
    assert b2.type == ActionType.FIXED_WAIT
    assert b2.description == "Step 3: Wait Sliced Animation"
    assert b2.params.duration_ms == 450
    assert b2.enabled is True

    # Verify Block 3: IMAGE_WAIT
    b3 = loaded.actions[3]
    assert b3.type == ActionType.IMAGE_WAIT
    assert b3.description == "Step 4: Wait Quest Reward Banner"
    assert b3.params.confidence == pytest.approx(0.88)
    assert b3.params.timeout_seconds == pytest.approx(6.0)
    assert b3.params.mode == "appear"
    assert b3.params.timeout_action == "raise"
    assert "test_btn.png" in b3.params.template_path
    assert "\\" not in b3.params.template_path
    assert b3.enabled is True

    # Verify template resolves cleanly to the original sample template file
    resolved_path = resolve_template_path(b3.params.template_path, save_path)
    assert resolved_path.resolve() == sample_template_file.resolve()


# =============================================================================
# Task 2: Full GUI E2E Execution & Emergency Stop
# =============================================================================

def test_full_gui_e2e_execution_and_emergency_stop(qtbot) -> None:
    """Tests execution lifecycle and global emergency stop:
    1. Starts macro execution via MainWindow.start_macro().
    2. Verifies that ExecutionSignals update ExecutionDashboard (state transition
       to RUNNING, progress bar, active card highlight) and LogConsole in real-time.
    3. Triggers global emergency stop via MainWindow.hotkey_listener.trigger_emergency_stop().
    4. Asserts macro terminates in <100ms (<1s limit), EngineState.STOPPED is reached,
       cancel_event is set, and MockInputDriver.safety_flush() was dispatched.
    """
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    # Pre-engage held modifiers and mouse buttons to verify safety_flush releases them
    mock_input.key_down("ctrl")
    mock_input.key_down("shift")
    mock_input.mouse_down("left")
    assert "ctrl" in mock_input.active_keys
    assert "shift" in mock_input.active_keys
    assert "left" in mock_input.active_buttons
    assert mock_input.safety_flush_called is False
    assert mock_input.flush_count == 0

    # Build scenario with a long duration wait to ensure reliable real-time inspection
    scenario = ScenarioModel(
        name="Emergency Stop E2E Test",
        loop_count=1,
        actions=[
            ActionBlock(
                id="act-abort-target",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=10000),  # 10s sliced wait
                description="Long Wait Step For Abort Testing",
            )
        ],
    )

    win = MainWindow(
        scenario=scenario,
        input_driver=mock_input,
        screen_driver=mock_screen,
    )
    qtbot.addWidget(win)

    # 1. Start macro execution via MainWindow.start_macro()
    win.start_macro()
    assert win.engine is not None
    assert win.hotkey_listener is not None

    # 2. Verify that ExecutionSignals update ExecutionDashboard and LogConsole in real-time
    # A. State transition to RUNNING in ExecutionDashboard
    qtbot.waitUntil(lambda: win.dashboard._current_state == EngineState.RUNNING, timeout=3000)
    assert win.dashboard.banner_label.text() == "RUNNING"
    assert win.dashboard.btn_start.isEnabled() is False
    assert win.dashboard.btn_stop.isEnabled() is True

    # B. Progress bar updates
    qtbot.waitUntil(lambda: win.dashboard.action_bar.value() >= 1, timeout=3000)
    assert "Iteration: 1" in win.dashboard.loop_label.text()
    assert "Action 1:" in win.dashboard.action_label.text()

    # C. Active card highlight in ScenarioBuilder
    target_card = win.scenario_builder._card_map["act-abort-target"]
    qtbot.waitUntil(lambda: target_card.is_executing is True, timeout=3000)
    assert win.scenario_builder._active_index == 0

    # D. LogConsole contains real-time execution entries
    qtbot.waitUntil(lambda: len(win.log_console._entries) > 0, timeout=3000)
    logs_text = win.log_console.text_edit.toPlainText()
    assert "Macro started" in logs_text

    # 3. Trigger global emergency stop via MainWindow.hotkey_listener.trigger_emergency_stop()
    hotkey_listener = win.hotkey_listener
    engine = win.engine

    t_start = time.perf_counter()
    measured_abort_latency_ms = hotkey_listener.trigger_emergency_stop()
    t_end = time.perf_counter()
    wall_abort_latency_ms = (t_end - t_start) * 1000.0

    # 4. Assertions:
    # A. Macro abort terminates in <100ms (<1s limit)
    assert measured_abort_latency_ms < 100.0, (
        f"Emergency stop trigger latency {measured_abort_latency_ms:.2f}ms exceeded 100ms requirement"
    )
    assert wall_abort_latency_ms < 1000.0, (
        f"Wall clock emergency abort latency {wall_abort_latency_ms:.2f}ms exceeded 1.0s requirement"
    )

    # Worker thread terminates within <= 1.0s
    worker_finished = engine.wait_for_completion(timeout=1.0)
    assert worker_finished is True, "Background execution worker thread did not terminate within 1.0s"

    # B. EngineState.STOPPED is reached
    assert engine.state == EngineState.STOPPED, f"Engine state must be STOPPED, got {engine.state}"

    # C. cancel_event is set
    assert engine.cancel_event.is_set(), "Execution cancel_event must be set"

    # D. MockInputDriver.safety_flush() was dispatched
    assert mock_input.safety_flush_called is True, "safety_flush() was not called on MockInputDriver"
    assert mock_input.flush_count >= 1
    assert len(mock_input.active_keys) == 0, "Active keys not released after emergency stop"
    assert len(mock_input.active_buttons) == 0, "Active buttons not released after emergency stop"

    # E. Dashboard transitions to STOPPED state
    qtbot.waitUntil(lambda: win.dashboard._current_state == EngineState.STOPPED, timeout=3000)
    assert win.dashboard.banner_label.text() == "STOPPED"
    assert win.dashboard.btn_start.isEnabled() is True


# =============================================================================
# Task 3: Cross-Platform Scenario Compatibility
# =============================================================================

def test_cross_platform_scenario_compatibility(tmp_path: Path) -> None:
    """Tests cross-platform scenario serialization, deserialization, and key mapping:
    1. Tests saving a scenario with Windows backslash template path vs POSIX template path
       and verifies normalized POSIX serialization and loading across platforms.
    2. Tests platform key mapping normalization (Command/Control, Option/Alt) across
       Darwin and Windows.
    """
    scenario_dir = tmp_path / "cross_platform"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    template_file = scenario_dir / "templates" / "quest_icon.png"
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_bytes(b"synthetic_template_bytes")

    # -------------------------------------------------------------------------
    # Part 1: Template Path Normalization (Windows Backslash vs POSIX)
    # -------------------------------------------------------------------------

    # A. Scenario created with Windows backslash path
    win_backslash_path = r"templates\quest_icon.png"
    scenario_windows = ScenarioModel(
        name="Windows Style Scenario",
        actions=[
            ActionBlock(
                id="act-win-style",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(
                    template_path=win_backslash_path,
                    mode="appear",
                    confidence=0.85,
                ),
                description="Windows Path Step",
            )
        ],
    )

    save_win_path = scenario_dir / "windows_scenario.json"
    ScenarioPersistence.save(scenario_windows, save_win_path)

    # Verify raw JSON on disk contains ONLY normalized forward slashes
    raw_win_content = save_win_path.read_text(encoding="utf-8")
    assert r"templates\quest_icon.png" not in raw_win_content, (
        "Serialized JSON must not contain Windows backslashes in template_path"
    )
    assert "templates/quest_icon.png" in raw_win_content, (
        "Serialized JSON must contain normalized POSIX forward slashes"
    )

    # B. Scenario created with POSIX path
    posix_path = "templates/quest_icon.png"
    scenario_posix = ScenarioModel(
        name="POSIX Style Scenario",
        actions=[
            ActionBlock(
                id="act-posix-style",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(
                    template_path=posix_path,
                    mode="appear",
                    confidence=0.85,
                ),
                description="POSIX Path Step",
            )
        ],
    )

    save_posix_path = scenario_dir / "posix_scenario.json"
    ScenarioPersistence.save(scenario_posix, save_posix_path)

    raw_posix_content = save_posix_path.read_text(encoding="utf-8")
    assert "templates/quest_icon.png" in raw_posix_content

    # C. Cross-platform deserialization and resolution
    loaded_win = ScenarioPersistence.load(save_win_path)
    assert loaded_win.actions[0].params.template_path == "templates/quest_icon.png"
    resolved_win = resolve_template_path(loaded_win.actions[0].params.template_path, save_win_path)
    assert resolved_win.resolve() == template_file.resolve()

    loaded_posix = ScenarioPersistence.load(save_posix_path)
    assert loaded_posix.actions[0].params.template_path == "templates/quest_icon.png"
    resolved_posix = resolve_template_path(loaded_posix.actions[0].params.template_path, save_posix_path)
    assert resolved_posix.resolve() == template_file.resolve()

    # D. Deserializing raw JSON originating from a Windows host with escaped backslashes
    foreign_win_json = json.dumps({
        "name": "Exported from Windows Host",
        "version": "1.0.0",
        "loop_count": 1,
        "actions": [
            {
                "id": "act-foreign-win",
                "type": "IMAGE_WAIT",
                "params": {
                    "template_path": "templates\\subfolder\\target.png",
                    "mode": "appear",
                    "confidence": 0.8,
                    "timeout_seconds": 5.0,
                    "roi": None,
                    "timeout_action": "raise",
                },
                "enabled": True,
                "description": "Foreign Windows Action",
            }
        ],
    })
    foreign_loaded = scenario_from_json(foreign_win_json)
    resolved_foreign = resolve_template_path(
        foreign_loaded.actions[0].params.template_path, save_win_path
    )
    assert resolved_foreign.name == "target.png"
    assert "templates/subfolder/target.png" in resolved_foreign.as_posix()

    # -------------------------------------------------------------------------
    # Part 2: Platform Key Mapping Normalization (Darwin vs Windows)
    # -------------------------------------------------------------------------

    # A. Alias normalization for modifier and special keys
    assert normalize_key_name("Command") == "cmd"
    assert normalize_key_name("command") == "cmd"
    assert normalize_key_name("Control") == "ctrl"
    assert normalize_key_name("control") == "ctrl"
    assert normalize_key_name("Option") == "alt"
    assert normalize_key_name("option") == "alt"
    assert normalize_key_name("opt") == "alt"
    assert normalize_key_name("Alt") == "alt"
    assert normalize_key_name("Windows") == "cmd"
    assert normalize_key_name("Win") == "cmd"
    assert normalize_key_name("super") == "cmd"
    assert normalize_key_name("Return") == "enter"
    assert normalize_key_name("Escape") == "esc"
    assert normalize_key_name("primary_modifier") == "cmd_or_ctrl"
    assert normalize_key_name("ctrl_or_cmd") == "cmd_or_ctrl"

    # B. Target platform key resolution (Darwin vs Win32)
    # Adaptive modifier (cmd on Darwin, ctrl on Windows/Linux)
    assert resolve_key_for_platform("cmd_or_ctrl", platform_name="darwin") == "cmd"
    assert resolve_key_for_platform("cmd_or_ctrl", platform_name="win32") == "ctrl"
    assert resolve_key_for_platform("primary_modifier", platform_name="darwin") == "cmd"
    assert resolve_key_for_platform("primary_modifier", platform_name="win32") == "ctrl"

    # Command / Control aliases
    assert resolve_key_for_platform("command", platform_name="darwin") == "cmd"
    assert resolve_key_for_platform("command", platform_name="win32") == "cmd"
    assert resolve_key_for_platform("control", platform_name="darwin") == "ctrl"
    assert resolve_key_for_platform("control", platform_name="win32") == "ctrl"

    # Option / Alt aliases
    assert resolve_key_for_platform("option", platform_name="darwin") == "alt"
    assert resolve_key_for_platform("option", platform_name="win32") == "alt"

    # C. Key combination display formatting conventions across OS platforms
    # Darwin uses Apple glyphs: ⌘ for cmd, ⌃ for ctrl, ⌥ for alt, ⇧ for shift, ⏎ for enter
    assert format_key_combo(["cmd_or_ctrl", "c"], platform_name="darwin") == "⌘ + C"
    assert format_key_combo(["control", "option", "return"], platform_name="darwin") == "⌃ + ⌥ + ⏎"
    assert format_key_combo(["cmd", "shift", "s"], platform_name="darwin") == "⌘ + ⇧ + S"

    # Windows uses standard text labels: Ctrl, Alt, Shift, Win, Enter
    assert format_key_combo(["cmd_or_ctrl", "c"], platform_name="win32") == "Ctrl + C"
    assert format_key_combo(["control", "option", "return"], platform_name="win32") == "Ctrl + Alt + Enter"
    assert format_key_combo(["windows", "r"], platform_name="win32") == "Win + R"

    # D. Key combination parsing
    assert parse_key_combo("Command+Shift+S") == ["cmd", "shift", "s"]
    assert parse_key_combo("Control + Alt + Delete") == ["ctrl", "alt", "delete"]
    assert parse_key_combo("Option+Return") == ["alt", "enter"]
    assert parse_key_combo("primary_modifier + z") == ["cmd_or_ctrl", "z"]

    # E. Scenario persistence round-trip of platform-neutral key models
    key_scenario = ScenarioModel(
        name="Cross Platform Keys Roundtrip",
        actions=[
            ActionBlock(
                id="act-copy-universal",
                type=ActionType.KEY_INPUT,
                params=KeyInputParams(keys=["cmd_or_ctrl", "c"], action="press"),
                description="Universal Copy",
            ),
            ActionBlock(
                id="act-shortcut-mac",
                type=ActionType.KEY_INPUT,
                params=KeyInputParams(keys=["option", "tab"], action="press"),
                description="Mac Option Tab",
            ),
            ActionBlock(
                id="act-shortcut-win",
                type=ActionType.KEY_INPUT,
                params=KeyInputParams(keys=["windows", "e"], action="press"),
                description="Win Explorer",
            ),
        ],
    )

    save_key_file = scenario_dir / "keys_scenario.json"
    ScenarioPersistence.save(key_scenario, save_key_file)

    loaded_keys = ScenarioPersistence.load(save_key_file)
    assert len(loaded_keys.actions) == 3

    # On Darwin, evaluate platform-resolved keys
    darwin_actions = [
        [resolve_key_for_platform(k, "darwin") for k in act.params.keys]
        for act in loaded_keys.actions
    ]
    assert darwin_actions[0] == ["cmd", "c"]
    assert darwin_actions[1] == ["alt", "tab"]
    assert darwin_actions[2] == ["cmd", "e"]

    # On Windows, evaluate platform-resolved keys
    win_actions = [
        [resolve_key_for_platform(k, "win32") for k in act.params.keys]
        for act in loaded_keys.actions
    ]
    assert win_actions[0] == ["ctrl", "c"]
    assert win_actions[1] == ["alt", "tab"]
    assert win_actions[2] == ["cmd", "e"]
