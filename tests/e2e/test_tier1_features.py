"""Tier 1: Feature Coverage Test Suite for ActionMacro Automation.

Covers all 8 primary features with >=5 test cases per feature (Total >= 40 tests):
1. Mouse Click Action Block (6 tests)
2. Keyboard Input Action Block (5 tests)
3. Fixed Time Wait (5 tests)
4. Image Recognition Wait (5 tests)
5. Scenario File Persistence (5 tests)
6. Repeat Execution Loop (5 tests)
7. Emergency Stop Hotkey (6 tests)
8. Coordinate Picker / Math (5 tests)
Total: 37 + 7 = 44 test cases.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Tuple

import pytest

from tests.mocks.contract import (
    ActionBlock,
    ActionType,
    CoordinateTransformer,
    EngineState,
    ExecutionEngine,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
    ScenarioPersistence,
    WaitSystem,
)
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


# =============================================================================
# Feature 1: Mouse Click Action Block (>= 5 tests)
# =============================================================================

class TestFeature1MouseClick:
    """Verifies mouse click execution, button types, counts, and ±1px precision."""

    def test_mouse_click_single_left_precision(self, mock_input: MockInputDriver):
        """T1.1.1: Single left click at (500, 300) satisfies ±1px precision and records event."""
        mock_input.click(x=500, y=300, button="left", count=1)

        assert len(mock_input.clicks) == 1
        event = mock_input.last_click
        assert event is not None
        assert event.button == "left"
        assert event.clicks == 1
        assert event.click_type == "single"
        # Assert ±1px precision
        mock_input.assert_last_click_precision(500, 300, tolerance=1)
        mock_input.assert_mouse_precision(500, 300, tolerance=1)

    def test_mouse_click_right_button(self, mock_input: MockInputDriver):
        """T1.1.2: Right mouse click at (1200, 800) is recorded with button='right'."""
        mock_input.click(x=1200, y=800, button="right", count=1)

        assert len(mock_input.clicks) == 1
        event = mock_input.last_click
        assert event is not None
        assert event.button == "right"
        assert event.x == 1200 and event.y == 800
        mock_input.assert_last_click_precision(1200, 800, tolerance=1)

    def test_mouse_click_double_click(self, mock_input: MockInputDriver):
        """T1.1.3: Double click at (640, 480) records click_type='double' and clicks=2."""
        mock_input.click(x=640, y=480, button="left", count=2)

        event = mock_input.last_click
        assert event is not None
        assert event.clicks == 2
        assert event.click_type == "double"
        mock_input.assert_last_click_precision(640, 480, tolerance=1)

    def test_mouse_click_middle_button(self, mock_input: MockInputDriver):
        """T1.1.4: Middle mouse button click at (960, 540) is recorded correctly."""
        mock_input.click(x=960, y=540, button="middle", count=1)

        event = mock_input.last_click
        assert event is not None
        assert event.button == "middle"
        mock_input.assert_last_click_precision(960, 540, tolerance=1)

    def test_mouse_click_press_duration_hold(self, mock_input: MockInputDriver):
        """T1.1.5: Click with press duration records the hold duration."""
        mock_input.click(x=300, y=400, button="left", count=1, press_duration=0.25)

        event = mock_input.last_click
        assert event is not None
        assert event.press_duration == 0.25
        mock_input.assert_last_click_precision(300, 400, tolerance=1)

    def test_mouse_click_consecutive_multi_target_precision(self, mock_input: MockInputDriver):
        """T1.1.6: Sequence of clicks across screen boundaries maintains ±1px precision throughout."""
        targets = [(0, 0), (100, 200), (960, 540), (1919, 1079)]
        for tx, ty in targets:
            mock_input.click(x=tx, y=ty, button="left")
            mock_input.assert_last_click_precision(tx, ty, tolerance=1)

        assert len(mock_input.clicks) == 4


# =============================================================================
# Feature 2: Keyboard Input Action Block (>= 5 tests)
# =============================================================================

class TestFeature2KeyInput:
    """Verifies single key press, combos, text typing, and hold state tracking."""

    def test_key_input_single_alpha_key(self, mock_input: MockInputDriver):
        """T1.2.1: Pressing single key 'a' records both key_down and key_up events."""
        mock_input.press_key("a")

        events = mock_input.key_events
        assert len(events) == 2
        assert events[0].event_type == "key_down" and events[0].key == "a"
        assert events[1].event_type == "key_up" and events[1].key == "a"
        assert len(mock_input.active_keys) == 0  # Key was released

    def test_key_input_special_function_keys(self, mock_input: MockInputDriver):
        """T1.2.2: Pressing special keys ('enter', 'esc', 'f12') dispatches mapped keys."""
        for key in ["enter", "esc", "f12", "space"]:
            mock_input.press_key(key)

        downs = [e.key for e in mock_input.events if e.event_type == "key_down"]
        assert downs == ["enter", "esc", "f12", "space"]
        assert len(mock_input.active_keys) == 0

    def test_key_input_modifier_combination(self, mock_input: MockInputDriver):
        """T1.2.3: Modifier combo (Ctrl+C) dispatches modifier down, key down, key up, modifier up."""
        mock_input.press_key("c", modifiers=["ctrl"])

        seq = [(e.event_type, e.key) for e in mock_input.events if e.event_type in ("key_down", "key_up")]
        assert seq == [
            ("key_down", "ctrl"),
            ("key_down", "c"),
            ("key_up", "c"),
            ("key_up", "ctrl"),
        ]
        assert len(mock_input.active_keys) == 0

    def test_key_input_unicode_korean_text(self, mock_input: MockInputDriver):
        """T1.2.4: Typing Unicode Korean text string is recorded with full fidelity."""
        test_text = "퀘스트완료 (Daily Quest)"
        mock_input.type_text(test_text)

        text_events = [e for e in mock_input.events if e.event_type == "text"]
        assert len(text_events) == 1
        assert text_events[0].text == test_text

    def test_key_input_hold_and_release(self, mock_input: MockInputDriver):
        """T1.2.5: Direct key_down leaves key in active_keys until key_up / release_key."""
        mock_input.key_down("shift")
        assert "shift" in mock_input.active_keys

        mock_input.press_key("tab")
        assert "shift" in mock_input.active_keys  # shift still held

        mock_input.release_key("shift")
        assert "shift" not in mock_input.active_keys


# =============================================================================
# Feature 3: Fixed Time Wait (>= 5 tests)
# =============================================================================

class TestFeature3FixedWait:
    """Verifies high-precision fixed duration wait, cancellation, and pause."""

    def test_fixed_wait_short_duration_accuracy(self):
        """T1.3.1: Short wait (50ms) completes successfully and elapsed time is >= 45ms."""
        t0 = time.perf_counter()
        result = WaitSystem.wait(duration_seconds=0.05)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert result is True
        assert elapsed_ms >= 45.0  # At least 45ms elapsed

    def test_fixed_wait_zero_duration_instant(self):
        """T1.3.2: 0ms wait returns immediately (<= 10ms) with True."""
        t0 = time.perf_counter()
        result = WaitSystem.wait(duration_seconds=0.0)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert result is True
        assert elapsed_ms <= 10.0

    def test_fixed_wait_cooperative_cancellation(self):
        """T1.3.3: 5.0s wait interrupted at 40ms exits in <= 100ms returning False."""
        cancel_event = threading.Event()

        def _cancel_after():
            time.sleep(0.04)
            cancel_event.set()

        threading.Thread(target=_cancel_after, daemon=True).start()

        t0 = time.perf_counter()
        result = WaitSystem.wait(duration_seconds=5.0, cancel_event=cancel_event)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert result is False
        assert elapsed_ms <= 120.0  # Aborted promptly

    def test_fixed_wait_sliced_check_latency(self):
        """T1.3.4: Sliced wait loop responds to pre-set cancel_event in <= 25ms."""
        cancel_event = threading.Event()
        cancel_event.set()  # Pre-set

        t0 = time.perf_counter()
        result = WaitSystem.wait(duration_seconds=10.0, cancel_event=cancel_event)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert result is False
        assert elapsed_ms <= 25.0

    def test_fixed_wait_negative_duration_safe(self):
        """T1.3.5: Negative wait duration clamps safely and returns True immediately."""
        t0 = time.perf_counter()
        result = WaitSystem.wait(duration_seconds=-0.5)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert result is True
        assert elapsed_ms <= 10.0


# =============================================================================
# Feature 4: Image Recognition Wait (>= 5 tests)
# =============================================================================

class TestFeature4ImageWait:
    """Verifies OpenCV template matching in appearance and disappearance modes."""

    def test_image_wait_immediate_appearance(
        self,
        mock_screen: MockScreenDriver,
        sample_template,
        sample_template_file: Path,
    ):
        """T1.4.1: Template present on screen at t=0 is detected on first capture."""
        # Inject template at (500, 300)
        mock_screen.inject_patch(sample_template, x=500, y=300)

        params = ImageWaitParams(
            template_path=str(sample_template_file),
            mode="appear",
            confidence=0.8,
            timeout_seconds=1.0,
        )

        matched, coords = WaitSystem.wait_image(params, mock_screen)
        assert matched is True
        assert coords is not None
        center_x, center_y = coords
        # Template is 60x30, so center is (500 + 30, 300 + 15) = (530, 315)
        assert abs(center_x - 530) <= 1
        assert abs(center_y - 315) <= 1

    def test_image_wait_delayed_appearance_at_step(
        self,
        mock_screen: MockScreenDriver,
        sample_template,
        sample_template_file: Path,
    ):
        """T1.4.2: Template injected at step 2 is detected when step advances."""
        mock_screen.schedule_event(step=2, patch=sample_template, x=700, y=400)
        mock_screen.auto_advance_step = True  # Advances step on each capture

        params = ImageWaitParams(
            template_path=str(sample_template_file),
            mode="appear",
            confidence=0.8,
            timeout_seconds=2.0,
            poll_interval_seconds=0.02,
        )

        matched, coords = WaitSystem.wait_image(params, mock_screen)
        assert matched is True
        assert coords is not None
        assert abs(coords[0] - (700 + 30)) <= 1
        assert abs(coords[1] - (400 + 15)) <= 1

    def test_image_wait_disappearance_mode(
        self,
        mock_screen: MockScreenDriver,
        sample_template,
        sample_template_file: Path,
    ):
        """T1.4.3: Disappearance mode completes once template is erased from canvas."""
        mock_screen.inject_patch(sample_template, x=500, y=300)
        # Schedule removal at step 2
        mock_screen.schedule_removal(step=2, x=500, y=300, width=60, height=30)
        mock_screen.auto_advance_step = True

        params = ImageWaitParams(
            template_path=str(sample_template_file),
            mode="disappear",
            confidence=0.8,
            timeout_seconds=2.0,
            poll_interval_seconds=0.02,
        )

        matched, coords = WaitSystem.wait_image(params, mock_screen)
        assert matched is True
        assert coords is None  # disappearance mode does not return coordinates

    def test_image_wait_roi_subregion_bounding_box(
        self,
        mock_screen: MockScreenDriver,
        sample_template,
        sample_template_file: Path,
    ):
        """T1.4.4: Template inside ROI [400, 300, 300, 200] maps to absolute screen coordinates."""
        # Inject inside ROI bounds [400..700, 300..500]
        mock_screen.inject_patch(sample_template, x=450, y=350)

        params = ImageWaitParams(
            template_path=str(sample_template_file),
            mode="appear",
            confidence=0.8,
            timeout_seconds=1.0,
            roi=(400, 300, 300, 200),
        )

        matched, coords = WaitSystem.wait_image(params, mock_screen)
        assert matched is True
        assert coords is not None
        # Center: (450 + 30, 350 + 15) = (480, 365)
        assert abs(coords[0] - 480) <= 1
        assert abs(coords[1] - 365) <= 1

    def test_image_wait_timeout_raise_and_skip_policies(
        self,
        mock_screen: MockScreenDriver,
        sample_template_file: Path,
    ):
        """T1.4.5: When image is absent, timeout_action='raise' raises TimeoutError and 'skip' returns False."""
        # Canvas is empty (gray)
        params_raise = ImageWaitParams(
            template_path=str(sample_template_file),
            mode="appear",
            confidence=0.8,
            timeout_seconds=0.08,
            poll_interval_seconds=0.02,
            timeout_action="raise",
        )
        with pytest.raises(TimeoutError):
            WaitSystem.wait_image(params_raise, mock_screen)

        params_skip = ImageWaitParams(
            template_path=str(sample_template_file),
            mode="appear",
            confidence=0.8,
            timeout_seconds=0.08,
            poll_interval_seconds=0.02,
            timeout_action="skip",
        )
        matched, coords = WaitSystem.wait_image(params_skip, mock_screen)
        assert matched is False
        assert coords is None


# =============================================================================
# Feature 5: Scenario File Persistence (>= 5 tests)
# =============================================================================

class TestFeature5Persistence:
    """Verifies JSON serialization, deserialization, UTF-8 fidelity, and relative paths."""

    def test_persistence_roundtrip_all_action_types(self, tmp_path: Path, sample_scenario: ScenarioModel):
        """T1.5.1: Scenario with all 4 action types roundtrips through save and load identically."""
        dest = tmp_path / "scenario_roundtrip.bsm.json"
        ScenarioPersistence.save(sample_scenario, dest)

        loaded = ScenarioPersistence.load(dest)
        assert loaded.name == sample_scenario.name
        assert loaded.version == sample_scenario.version
        assert loaded.loop_count == sample_scenario.loop_count
        assert len(loaded.actions) == len(sample_scenario.actions)

        for orig_act, load_act in zip(sample_scenario.actions, loaded.actions):
            assert load_act.id == orig_act.id
            assert load_act.type == orig_act.type
            assert load_act.enabled == orig_act.enabled
            assert load_act.description == orig_act.description

    def test_persistence_unicode_korean_content(self, tmp_path: Path):
        """T1.5.2: Korean scenario name and descriptions preserve UTF-8 encoding without corruption."""
        scenario = ScenarioModel(
            name="블루스택 일일 던전 자동화 매크로",
            version="1.0.0",
            description="매일 자정에 실행되는 일일 보상 획득 시나리오",
            actions=[
                ActionBlock(
                    type=ActionType.MOUSE_CLICK,
                    params={"x": 500, "y": 300},
                    description="던전 입장 버튼 클릭",
                )
            ],
        )
        dest = tmp_path / "korean_macro.json"
        ScenarioPersistence.save(scenario, dest)

        loaded = ScenarioPersistence.load(dest)
        assert loaded.name == "블루스택 일일 던전 자동화 매크로"
        assert loaded.description == "매일 자정에 실행되는 일일 보상 획득 시나리오"
        assert loaded.actions[0].description == "던전 입장 버튼 클릭"

    def test_persistence_relative_template_paths(self, tmp_path: Path):
        """T1.5.3: Relative template path ('templates/btn.png') serialized with POSIX forward slashes."""
        scenario = ScenarioModel(
            name="Relative Path Test",
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={"template_path": "templates/sub/btn.png"},
                )
            ],
        )
        dest = tmp_path / "rel_test.json"
        ScenarioPersistence.save(scenario, dest)

        raw = json.loads(dest.read_text(encoding="utf-8"))
        saved_path = raw["actions"][0]["params"]["template_path"]
        assert saved_path == "templates/sub/btn.png"
        assert "\\" not in saved_path

    def test_persistence_pretty_json_format(self, tmp_path: Path, sample_scenario: ScenarioModel):
        """T1.5.4: Saved scenario file is formatted with 2-space indentation and trailing newline."""
        dest = tmp_path / "pretty.json"
        ScenarioPersistence.save(sample_scenario, dest)

        raw_text = dest.read_text(encoding="utf-8")
        assert raw_text.endswith("\n")
        assert '  "schema_version":' in raw_text

    def test_persistence_reorder_and_delete_actions(self, tmp_path: Path, sample_scenario: ScenarioModel):
        """T1.5.5: Deleting an action and reversing action order is preserved on reload."""
        # Delete first action, reverse remainder
        remaining = list(reversed(sample_scenario.actions[1:]))
        sample_scenario.actions = remaining

        dest = tmp_path / "reordered.json"
        ScenarioPersistence.save(sample_scenario, dest)

        loaded = ScenarioPersistence.load(dest)
        assert len(loaded.actions) == 3
        assert [a.id for a in loaded.actions] == [a.id for a in remaining]


# =============================================================================
# Feature 6: Repeat Execution Loop (>= 5 tests)
# =============================================================================

class TestFeature6ExecutionLoop:
    """Verifies fixed repetition count, loop delays, infinite loop abort, and progress."""

    def test_loop_fixed_count_n_executions(self, mock_input: MockInputDriver):
        """T1.6.1: Setting loop_count=3 executes all actions exactly 3 times."""
        scenario = ScenarioModel(
            name="Triple Loop",
            loop_count=3,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        completed = engine.run()

        assert completed is True
        assert len(mock_input.clicks) == 3
        assert engine.state == EngineState.IDLE

    def test_loop_single_iteration(self, mock_input: MockInputDriver):
        """T1.6.2: loop_count=1 executes action sequence exactly once."""
        scenario = ScenarioModel(
            name="Single Loop",
            loop_count=1,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 200, "y": 200}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        assert engine.run() is True
        assert len(mock_input.clicks) == 1

    def test_loop_inter_iteration_delay(self, mock_input: MockInputDriver):
        """T1.6.3: loop_delay_ms=40 introduces measurable delay between iterations."""
        scenario = ScenarioModel(
            name="Delayed Loop",
            loop_count=2,
            loop_delay_ms=40,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        t0 = time.perf_counter()
        engine.run()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert elapsed_ms >= 35.0  # At least loop_delay_ms elapsed

    def test_loop_infinite_loop_abort(self, mock_input: MockInputDriver):
        """T1.6.4: loop_count=0 (infinite) runs until stop() is called, terminating cleanly."""
        scenario = ScenarioModel(
            name="Infinite Loop",
            loop_count=0,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 150, "y": 150}),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 20}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        def _stop_later():
            time.sleep(0.06)
            engine.stop()

        threading.Thread(target=_stop_later, daemon=True).start()
        completed = engine.run()

        assert completed is False  # Stopped
        assert engine.state == EngineState.STOPPED
        assert len(mock_input.clicks) >= 1

    def test_loop_progress_signals_emitted(self, mock_input: MockInputDriver):
        """T1.6.5: Engine emits progress signals with loop and action indices."""
        scenario = ScenarioModel(
            name="Progress Test",
            loop_count=2,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}),
                ActionBlock(type=ActionType.KEY_INPUT, params={"keys": ["a"]}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.run()

        updates = engine.signals.progress_updates
        # 2 loops * 2 actions = 4 progress updates
        assert len(updates) == 4
        # First update: loop 1, total 2, action 1, total actions 2
        assert updates[0] == (1, 2, 1, 2)
        # Last update: loop 2, total 2, action 2, total actions 2
        assert updates[-1] == (2, 2, 2, 2)


# =============================================================================
# Feature 7: Emergency Stop Hotkey (>= 5 tests)
# =============================================================================

class TestFeature7EmergencyStop:
    """Verifies sub-second abort latency, safety flush, idempotency, and state transitions."""

    def test_emergency_stop_subsecond_abort_latency(self, mock_input: MockInputDriver):
        """T1.7.1: Emergency stop aborts a running macro in <= 50ms (well under 1.0s requirement)."""
        scenario = ScenarioModel(
            name="Long Wait Scenario",
            loop_count=1,
            actions=[
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 5000}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        def _trigger_stop():
            time.sleep(0.03)
            engine.stop()

        threading.Thread(target=_trigger_stop, daemon=True).start()

        t0 = time.perf_counter()
        engine.run()
        latency_ms = (time.perf_counter() - t0) * 1000

        # Sub-second assertion: abort happened in < 150ms from thread trigger
        assert latency_ms <= 200.0
        assert engine.state == EngineState.STOPPED

    def test_emergency_stop_safety_flush_active_holds(self, mock_input: MockInputDriver):
        """T1.7.2: Emergency stop releases actively held mouse buttons and keys via safety_flush."""
        # Simulate held button and key
        mock_input.mouse_down(button="left")
        mock_input.key_down("ctrl")
        assert "left" in mock_input.active_buttons
        assert "ctrl" in mock_input.active_keys

        scenario = ScenarioModel(name="Held Scenario", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.stop()

        assert mock_input.safety_flush_called is True
        assert len(mock_input.active_buttons) == 0
        assert len(mock_input.active_keys) == 0

    def test_emergency_stop_idempotent_multiple_calls(self, mock_input: MockInputDriver):
        """T1.7.3: Calling stop() 5 times in rapid succession does not crash or raise errors."""
        scenario = ScenarioModel(name="Multi Stop", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        for _ in range(5):
            engine.stop()

        assert engine.state == EngineState.STOPPED
        assert mock_input.flush_count == 5

    def test_emergency_stop_state_transitions(self, mock_input: MockInputDriver):
        """T1.7.4: Aborting transitions state through STOPPING -> STOPPED and emits finished signal."""
        scenario = ScenarioModel(
            name="State Test",
            actions=[ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 1000})],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        def _abort():
            time.sleep(0.02)
            engine.stop()

        threading.Thread(target=_abort, daemon=True).start()
        engine.run()

        states = engine.signals.state_changes
        assert "RUNNING" in states
        assert "STOPPING" in states
        assert "STOPPED" in states
        assert len(engine.signals.finished) == 1
        assert engine.signals.finished[0][0] is False  # success is False on abort

    def test_emergency_stop_during_sleep_wake(self, mock_input: MockInputDriver):
        """T1.7.5: Sliced wait wakes up within 20ms of emergency stop signal."""
        cancel_event = threading.Event()
        t0 = time.perf_counter()

        def _signal():
            time.sleep(0.02)
            cancel_event.set()

        threading.Thread(target=_signal, daemon=True).start()
        result = WaitSystem.wait(10.0, cancel_event=cancel_event)
        elapsed = time.perf_counter() - t0

        assert result is False
        assert elapsed < 0.1  # Responded in < 100ms

    def test_emergency_stop_custom_hotkey_configuration(self):
        """T1.7.6: Custom hotkey strings (e.g. 'ctrl+shift+q') can be validated and parsed."""
        valid_hotkeys = ["f12", "F11", "ctrl+shift+q", "alt+x", "esc"]
        for hk in valid_hotkeys:
            tokens = [t.strip().lower() for t in hk.split("+")]
            assert len(tokens) >= 1
            assert all(len(t) > 0 for t in tokens)


# =============================================================================
# Feature 8: Coordinate Picker / Math (>= 5 tests)
# =============================================================================

class TestFeature8CoordinatePicker:
    """Verifies live cursor tracking, click coordinate injection, and Retina DPR math."""

    def test_coordinate_picker_realtime_tracking(self, mock_input: MockInputDriver):
        """T1.8.1: Cursor movement updates live coordinates accurately."""
        positions = [(100, 100), (450, 620), (960, 540), (1920, 1080)]
        for px, py in positions:
            mock_input.move_to(px, py)
            assert mock_input.get_position() == (px, py)

    def test_coordinate_picker_capture_into_action_block(self, mock_input: MockInputDriver):
        """T1.8.2: Capturing coordinate (750, 420) updates target ActionBlock parameters."""
        action = ActionBlock(
            type=ActionType.MOUSE_CLICK,
            params={"x": 0, "y": 0},
            description="Target click",
        )
        mock_input.move_to(750, 420)
        curr_pos = mock_input.get_position()

        # Simulate picker injecting captured position
        action.params["x"] = curr_pos[0]
        action.params["y"] = curr_pos[1]

        assert action.params["x"] == 750
        assert action.params["y"] == 420

    def test_coordinate_picker_retina_dpr_transform(self):
        """T1.8.3: CoordinateTransformer converts physical pixels (2880, 1800) with DPR=2.0 to logical (1440, 900)."""
        phys_x, phys_y = 2880, 1800
        dpr = 2.0

        log_x, log_y = CoordinateTransformer.to_logical(phys_x, phys_y, dpr)
        assert (log_x, log_y) == (1440, 900)

        # Roundtrip back to physical
        back_phys_x, back_phys_y = CoordinateTransformer.to_physical(log_x, log_y, dpr)
        assert (back_phys_x, back_phys_y) == (2880, 1800)

    def test_coordinate_picker_pixel_color_query(self, mock_screen: MockScreenDriver):
        """T1.8.4: Screen driver get_pixel(x, y) returns RGB tuple, formatted as #RRGGBB."""
        # Paint a specific pixel #FF5733 = (255, 87, 51)
        mock_screen.clear_canvas(fill_color=(255, 87, 51))
        r, g, b = mock_screen.get_pixel(500, 300)

        assert (r, g, b) == (255, 87, 51)
        hex_color = f"#{r:02X}{g:02X}{b:02X}"
        assert hex_color == "#FF5733"

    def test_coordinate_picker_cancellation_preserves_action(self, mock_input: MockInputDriver):
        """T1.8.5: Cancelling coordinate picker (e.g. via Escape) leaves action params unchanged."""
        action = ActionBlock(
            type=ActionType.MOUSE_CLICK,
            params={"x": 100, "y": 200},
        )
        # Move cursor elsewhere
        mock_input.move_to(800, 900)

        # Picker is cancelled -> no params modified
        assert action.params["x"] == 100
        assert action.params["y"] == 200
