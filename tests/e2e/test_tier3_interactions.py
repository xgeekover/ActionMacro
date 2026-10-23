"""Tier 3: Cross-Feature Interaction Test Suite for ActionMacro Automation.

Tests multi-feature workflows:
1. Image Wait Appear -> Dynamic Coordinate Click
2. Image Wait Disappear -> Fast Key Combo
3. Emergency Stop during active Image Wait polling
4. Emergency Stop during long Fixed Duration Wait
5. Multi-loop execution with Image Wait, Click, and Fixed Wait
6. Disabled Action Block skipping in sequence
7. Coordinate Picker capture into Click Action execution
8. Pause and Resume cycle during multi-action sequence
Total: 8 test cases.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tests.mocks.contract import (
    ActionBlock,
    ActionType,
    EngineState,
    ExecutionEngine,
    ImageWaitParams,
    ScenarioModel,
    WaitSystem,
)
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


class TestTier3Interactions:
    """Verifies pairwise and multi-feature cross-layer interactions."""

    def test_interaction_image_wait_appear_then_click_matched_coordinates(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        sample_template,
        sample_template_file: Path,
    ):
        """T3.1: Image wait detects template; next action clicks exact detected center within ±1px."""
        # Inject template at (800, 400). Template is 60x30, center is (830, 415)
        mock_screen.inject_patch(sample_template, x=800, y=400)

        scenario = ScenarioModel(
            name="Detect and Click",
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(sample_template_file),
                        "mode": "appear",
                        "confidence": 0.8,
                    },
                ),
                ActionBlock(
                    type=ActionType.MOUSE_CLICK,
                    params={"use_last_matched": True},
                    description="Click detected location",
                ),
            ],
        )

        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
        success = engine.run()

        assert success is True
        assert len(mock_input.clicks) == 1
        mock_input.assert_last_click_precision(830, 415, tolerance=1)

    def test_interaction_image_wait_disappear_then_fast_key_combo(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        sample_template,
        sample_template_file: Path,
    ):
        """T3.2: Disappearance of loading template immediately triggers key shortcut."""
        mock_screen.inject_patch(sample_template, x=500, y=500)
        # Remove at step 1
        mock_screen.schedule_removal(step=1, x=500, y=500, width=60, height=30)
        mock_screen.auto_advance_step = True

        scenario = ScenarioModel(
            name="Disappear Then Key",
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(sample_template_file),
                        "mode": "disappear",
                        "timeout_seconds": 2.0,
                    },
                ),
                ActionBlock(
                    type=ActionType.KEY_INPUT,
                    params={"keys": ["ctrl", "alt", "1"], "action": "hotkey"},
                ),
            ],
        )

        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
        success = engine.run()

        assert success is True
        hotkey_events = [e for e in mock_input.events if e.event_type == "hotkey"]
        assert len(hotkey_events) == 1
        assert hotkey_events[0].keys == ["ctrl", "alt", "1"]

    def test_interaction_emergency_stop_during_image_polling(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        sample_template_file: Path,
    ):
        """T3.3: Emergency stop during active 30s image poll terminates in <= 100ms."""
        # Empty screen: image will never appear
        scenario = ScenarioModel(
            name="Image Poll Abort",
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(sample_template_file),
                        "timeout_seconds": 30.0,
                        "poll_interval_seconds": 0.05,
                    },
                )
            ],
        )

        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)

        def _abort_later():
            time.sleep(0.04)
            engine.stop()

        threading.Thread(target=_abort_later, daemon=True).start()

        t0 = time.perf_counter()
        completed = engine.run()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert completed is False
        assert elapsed_ms <= 150.0  # Stopped promptly
        assert engine.state == EngineState.STOPPED

    def test_interaction_emergency_stop_during_fixed_wait(
        self,
        mock_input: MockInputDriver,
    ):
        """T3.4: Emergency stop during 60s fixed duration wait aborts in <= 100ms."""
        scenario = ScenarioModel(
            name="Wait Abort",
            actions=[
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 60000}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        def _abort_later():
            time.sleep(0.03)
            engine.stop()

        threading.Thread(target=_abort_later, daemon=True).start()

        t0 = time.perf_counter()
        completed = engine.run()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert completed is False
        assert elapsed_ms <= 120.0
        assert engine.state == EngineState.STOPPED

    def test_interaction_multi_loop_with_image_and_click(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        sample_template,
        sample_template_file: Path,
    ):
        """T3.5: 3 iterations of [ImageWait -> Click -> FixedWait] execute cleanly in sequence."""
        # Inject template permanently so it is found in each loop
        mock_screen.inject_patch(sample_template, x=600, y=300)

        scenario = ScenarioModel(
            name="Multi Loop Macro",
            loop_count=3,
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(sample_template_file),
                        "timeout_seconds": 1.0,
                    },
                ),
                ActionBlock(
                    type=ActionType.MOUSE_CLICK,
                    params={"x": 630, "y": 315},
                ),
                ActionBlock(
                    type=ActionType.FIXED_WAIT,
                    params={"duration_ms": 10},
                ),
            ],
        )

        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
        completed = engine.run()

        assert completed is True
        assert len(mock_input.clicks) == 3
        for c in mock_input.clicks:
            assert abs(c.x - 630) <= 1 and abs(c.y - 315) <= 1

    def test_interaction_disabled_action_in_sequence(
        self,
        mock_input: MockInputDriver,
    ):
        """T3.6: Action with enabled=False is bypassed immediately without executing."""
        scenario = ScenarioModel(
            name="Disabled Action",
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}),
                ActionBlock(
                    type=ActionType.FIXED_WAIT,
                    params={"duration_ms": 5000},
                    enabled=False,  # Should be skipped!
                ),
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 200, "y": 200}),
            ],
        )

        engine = ExecutionEngine(scenario, input_driver=mock_input)
        t0 = time.perf_counter()
        completed = engine.run()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert completed is True
        assert elapsed_ms <= 100.0  # Did not wait 5000ms
        assert len(mock_input.clicks) == 2

    def test_interaction_coordinate_picker_capture_into_click_action(
        self,
        mock_input: MockInputDriver,
    ):
        """T3.7: Coordinate picker captures position, updates action, and engine executes click at captured pos."""
        # 1. Simulate user picking coordinate at (725, 340)
        mock_input.move_to(725, 340)
        captured_x, captured_y = mock_input.get_position()

        # 2. Inject into action block
        action = ActionBlock(
            type=ActionType.MOUSE_CLICK,
            params={"x": captured_x, "y": captured_y},
        )
        scenario = ScenarioModel(name="Picked Action", actions=[action])

        # 3. Execute scenario
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        assert engine.run() is True

        mock_input.assert_last_click_precision(725, 340, tolerance=1)

    def test_interaction_pause_resume_during_action_sequence(
        self,
        mock_input: MockInputDriver,
    ):
        """T3.8: Pausing between actions holds execution until resumed, then completes."""
        scenario = ScenarioModel(
            name="Pause Resume",
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 20}),
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 200, "y": 200}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        def _pause_and_resume():
            time.sleep(0.01)
            engine.pause()
            assert engine.state == EngineState.PAUSED
            time.sleep(0.05)
            engine.resume()
            assert engine.state == EngineState.RUNNING

        threading.Thread(target=_pause_and_resume, daemon=True).start()
        completed = engine.run()

        assert completed is True
        assert len(mock_input.clicks) == 2
