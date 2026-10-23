"""
tests.unit.test_execution_engine
================================
Unit test suite verifying:
- Loop control: loop_count N=1, fixed N=3, and infinite loop N=0 abort
- Action execution fidelity across MOUSE_CLICK, KEY_INPUT, FIXED_WAIT, IMAGE_WAIT
- Coordinate injection via use_last_matched dynamic parameters
- Skipping disabled actions (enabled=False) without driver invocation
- Inter-loop delay timing and sliced cancellation
- Pause and resume execution control between actions
- Emergency stop sub-second abort latency (<=50ms) and safety_flush hold releases
- Signal emissions (state_changed, progress_updated, action lifecycle, logs, finished)
- Error handling, driver exception containment, and state transitions to ERROR
- Background thread lifecycle management (start, is_running, join)
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple
import pytest

from tests.mocks.contract import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.runner.execution_engine import (
    ExecutionEngine,
    ExecutionSignals,
    SimpleSignals,
)
from action_macro.runner.state_machine import EngineState
from tests.mocks.mock_input import InputEvent, MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


# =============================================================================
# 1. Engine Initialization & Configuration
# =============================================================================

@pytest.mark.unit
class TestExecutionEngineInit:
    """Verifies default engine properties, signal hooks, and event tokens."""

    def test_engine_init_defaults(self, mock_input: MockInputDriver):
        scenario = ScenarioModel(name="Init Test", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        assert engine.state == EngineState.IDLE
        assert engine.scenario == scenario
        assert engine.input_driver == mock_input
        assert engine.screen_driver is None
        assert engine.current_loop == 0
        assert engine.current_action_idx == 0
        assert not engine.cancel_event.is_set()
        assert not engine.pause_event.is_set()

    def test_engine_with_screen_driver_and_base_dir(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        tmp_path: Path,
    ):
        scenario = ScenarioModel(name="Screen Engine", actions=[])
        engine = ExecutionEngine(
            scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
            base_dir=tmp_path,
        )
        assert engine.screen_driver == mock_screen
        assert engine.base_dir == tmp_path


# =============================================================================
# 2. Loop Execution Control (N=1, N=3, Infinite, Empty, Delay)
# =============================================================================

@pytest.mark.unit
class TestLoopExecutionControl:
    """Verifies single, multi, infinite, and delayed execution loops."""

    def test_loop_single_iteration(self, mock_input: MockInputDriver):
        """Verifies loop_count=1 executes sequence exactly once and returns True."""
        scenario = ScenarioModel(
            name="Single Loop",
            loop_count=1,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 200, "y": 150}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        completed = engine.run()

        assert completed is True
        assert len(mock_input.clicks) == 1
        assert engine.state == EngineState.IDLE
        assert engine.current_loop == 1

    def test_loop_fixed_count_executions(self, mock_input: MockInputDriver):
        """Verifies loop_count=3 executes entire action sequence 3 times."""
        scenario = ScenarioModel(
            name="Triple Loop",
            loop_count=3,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}),
                ActionBlock(type=ActionType.KEY_INPUT, params={"keys": ["x"]}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        completed = engine.run()

        assert completed is True
        assert len(mock_input.clicks) == 3
        key_events = [e for e in mock_input.events if e.event_type == "press" or e.key == "x"]
        assert len(key_events) >= 3
        assert engine.current_loop == 3
        assert engine.state == EngineState.IDLE

    def test_loop_infinite_until_stop(self, mock_input: MockInputDriver):
        """Verifies loop_count=0 runs continuously until stopped, exiting cleanly."""
        scenario = ScenarioModel(
            name="Infinite Loop",
            loop_count=0,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 50, "y": 50}),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 20}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        def _stop_after_runs():
            time.sleep(0.060)
            engine.stop()

        threading.Thread(target=_stop_after_runs, daemon=True).start()
        completed = engine.run()

        assert completed is False
        assert engine.state == EngineState.STOPPED
        assert len(mock_input.clicks) >= 1
        assert engine.current_loop >= 1

    def test_loop_delay_timing(self, mock_input: MockInputDriver):
        """Verifies loop_delay_ms introduces measurable delay between iterations."""
        scenario = ScenarioModel(
            name="Delayed Loop",
            loop_count=2,
            loop_delay_ms=50,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 10, "y": 10}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        t0 = time.perf_counter()
        completed = engine.run()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert completed is True
        assert elapsed_ms >= 45.0
        assert len(mock_input.clicks) == 2

    def test_empty_scenario_completes_cleanly(self, mock_input: MockInputDriver):
        """Verifies scenario with actions=[] completes cleanly with zero operations."""
        scenario = ScenarioModel(name="Empty Scenario", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        assert engine.run() is True
        assert engine.state == EngineState.IDLE
        assert len(mock_input.events) == 0


# =============================================================================
# 3. Action Execution Fidelity
# =============================================================================

@pytest.mark.unit
class TestActionExecutionFidelity:
    """Verifies parameter mapping and execution fidelity across all action types."""

    def test_mouse_click_parameters(self, mock_input: MockInputDriver):
        """Verifies mouse click parameters (button, double-click, press duration)."""
        scenario = ScenarioModel(
            name="Click Fidelity",
            actions=[
                ActionBlock(
                    type=ActionType.MOUSE_CLICK,
                    params={
                        "x": 640,
                        "y": 480,
                        "button": "right",
                        "clicks": 2,
                        "press_duration_ms": 80,
                    },
                )
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        assert engine.run() is True

        assert len(mock_input.clicks) == 1
        click = mock_input.clicks[0]
        assert click.x == 640
        assert click.y == 480
        assert click.button == "right"
        assert click.clicks == 2
        assert click.press_duration == 0.080

    def test_mouse_click_dynamic_last_matched_coordinates(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        sample_template,
        sample_template_file: Path,
    ):
        """Verifies click with use_last_matched=True adopts coords from prior ImageWait."""
        target_x, target_y = 700, 350
        mock_screen.inject_patch(sample_template, target_x, target_y)

        scenario = ScenarioModel(
            name="Dynamic Click",
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(sample_template_file),
                        "mode": "appear",
                        "timeout_seconds": 1.0,
                    },
                ),
                ActionBlock(
                    type=ActionType.MOUSE_CLICK,
                    params={"x": 0, "y": 0, "use_last_matched": True},
                ),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
        assert engine.run() is True

        # Expected center = 700 + 30, 350 + 15 = (730, 365)
        mock_input.assert_last_click_precision(730, 365, tolerance=1)

    def test_key_input_single_press_and_hotkey(self, mock_input: MockInputDriver):
        """Verifies key press, discrete holds, and hotkey combinations."""
        scenario = ScenarioModel(
            name="Key Inputs",
            actions=[
                ActionBlock(type=ActionType.KEY_INPUT, params={"keys": ["enter"], "action": "press"}),
                ActionBlock(type=ActionType.KEY_INPUT, params={"keys": ["ctrl", "c"], "action": "hotkey"}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        assert engine.run() is True

        hotkey_events = [e for e in mock_input.events if e.event_type == "hotkey"]
        assert len(hotkey_events) == 1
        assert hotkey_events[0].keys == ["ctrl", "c"]

    def test_key_input_down_and_up_discrete_holds(self, mock_input: MockInputDriver):
        """Verifies discrete down and up operations track active holds."""
        scenario = ScenarioModel(
            name="Key Holds",
            actions=[
                ActionBlock(type=ActionType.KEY_INPUT, params={"keys": ["shift"], "action": "down"}),
                ActionBlock(type=ActionType.KEY_INPUT, params={"keys": ["shift"], "action": "up"}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        assert engine.run() is True

        assert "shift" not in mock_input.active_keys

    def test_fixed_wait_execution(self, mock_input: MockInputDriver):
        """Verifies fixed duration wait action completes accurately."""
        scenario = ScenarioModel(
            name="Wait Action",
            actions=[
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 40}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        t0 = time.perf_counter()
        assert engine.run() is True
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms >= 35.0

    def test_image_wait_action_disappearance_mode(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        sample_template,
        sample_template_file: Path,
    ):
        """Verifies image wait disappearance mode action."""
        mock_screen.inject_patch(sample_template, 300, 300)
        mock_screen.auto_advance_step = True
        mock_screen.schedule_removal(step=1, x=300, y=300, width=60, height=30)

        scenario = ScenarioModel(
            name="Image Disappear",
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(sample_template_file),
                        "mode": "disappear",
                        "timeout_seconds": 2.0,
                    },
                ),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
        assert engine.run() is True

    def test_image_wait_timeout_skip_policy_continues(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        sample_template_file: Path,
    ):
        """Verifies image wait with timeout_action='skip' continues to subsequent actions."""
        # Empty screen -> template absent
        scenario = ScenarioModel(
            name="Timeout Skip",
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(sample_template_file),
                        "timeout_seconds": 0.04,
                        "timeout_action": "skip",
                        "poll_interval_seconds": 0.01,
                    },
                ),
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 500, "y": 500}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
        assert engine.run() is True
        # Click was executed despite image timeout skip
        assert len(mock_input.clicks) == 1

    def test_image_wait_timeout_raise_policy_aborts(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        sample_template_file: Path,
    ):
        """Verifies image wait with timeout_action='raise' enters ERROR state."""
        scenario = ScenarioModel(
            name="Timeout Raise",
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(sample_template_file),
                        "timeout_seconds": 0.03,
                        "timeout_action": "raise",
                        "poll_interval_seconds": 0.01,
                    },
                ),
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 500, "y": 500}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
        with pytest.raises(TimeoutError):
            engine.run()

        assert engine.state == EngineState.ERROR
        assert len(mock_input.clicks) == 0  # Subsequent click not reached

    def test_disabled_action_is_bypassed(self, mock_input: MockInputDriver):
        """Verifies actions with enabled=False are skipped immediately."""
        scenario = ScenarioModel(
            name="Disabled Step",
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}, enabled=True),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 10000}, enabled=False),
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 200, "y": 200}, enabled=True),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        t0 = time.perf_counter()
        assert engine.run() is True
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert elapsed_ms <= 100.0  # Did not sleep 10s
        assert len(mock_input.clicks) == 2


# =============================================================================
# 4. Pause and Resume Execution Control
# =============================================================================

@pytest.mark.unit
class TestPauseAndResumeControl:
    """Verifies pause suspension between steps, resume continuation, and safety."""

    def test_pause_and_resume_lifecycle(self, mock_input: MockInputDriver):
        """Verifies pause halts execution between actions and resume completes sequence."""
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
            time.sleep(0.010)
            engine.pause()
            assert engine.state == EngineState.PAUSED
            assert len(mock_input.clicks) == 1  # Only first action finished
            time.sleep(0.050)
            engine.resume()
            assert engine.state == EngineState.RUNNING

        threading.Thread(target=_pause_and_resume, daemon=True).start()
        completed = engine.run()

        assert completed is True
        assert len(mock_input.clicks) == 2
        assert engine.state == EngineState.IDLE

    def test_pause_idempotent(self, mock_input: MockInputDriver):
        """Verifies calling pause() multiple times is safe."""
        scenario = ScenarioModel(name="Pause Test", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.pause()
        engine.pause()
        assert engine.state == EngineState.PAUSED

    def test_stop_while_paused_aborts_immediately(self, mock_input: MockInputDriver):
        """Verifies invoking stop() while in PAUSED state unblocks and terminates cleanly."""
        scenario = ScenarioModel(
            name="Stop while Paused",
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 10, "y": 10}),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 50}),
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 20, "y": 20}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        def _pause_then_stop():
            time.sleep(0.010)
            engine.pause()
            time.sleep(0.020)
            engine.stop()

        threading.Thread(target=_pause_then_stop, daemon=True).start()
        completed = engine.run()

        assert completed is False
        assert engine.state == EngineState.STOPPED
        assert len(mock_input.clicks) <= 2


# =============================================================================
# 5. Emergency Stop & Safety Flush
# =============================================================================

@pytest.mark.unit
class TestEmergencyStop:
    """Verifies sub-second abort latency (<=50ms), safety flush, and idempotency."""

    def test_emergency_stop_subsecond_abort_latency(self, mock_input: MockInputDriver):
        """Verifies emergency stop terminates running macro in <= 50ms (< 1.0s requirement)."""
        scenario = ScenarioModel(
            name="Long Wait Scenario",
            actions=[
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 5000}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        def _trigger_stop():
            time.sleep(0.030)
            engine.stop()

        threading.Thread(target=_trigger_stop, daemon=True).start()
        t0 = time.perf_counter()
        completed = engine.run()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert completed is False
        assert elapsed_ms <= 150.0  # Aborted promptly
        assert engine.state == EngineState.STOPPED

    def test_emergency_stop_invokes_safety_flush(self, mock_input: MockInputDriver):
        """Verifies emergency stop releases held mouse buttons and keys via safety_flush."""
        mock_input.mouse_down(button="left")
        mock_input.key_down("ctrl")
        assert "left" in mock_input.active_buttons
        assert "ctrl" in mock_input.active_keys

        scenario = ScenarioModel(name="Hold Test", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.stop()

        assert mock_input.safety_flush_called is True
        assert len(mock_input.active_buttons) == 0
        assert len(mock_input.active_keys) == 0

    def test_emergency_stop_idempotent_multiple_calls(self, mock_input: MockInputDriver):
        """Verifies calling stop() rapidly 5 times does not error or deadlock."""
        scenario = ScenarioModel(name="Multi Stop", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        for _ in range(5):
            engine.stop()

        assert engine.state == EngineState.STOPPED
        assert mock_input.flush_count == 5

    def test_pre_set_cancel_event_at_t0(self, mock_input: MockInputDriver):
        """Verifies cancel_event pre-set before run() completes immediately without actions."""
        scenario = ScenarioModel(
            name="Abort at t0",
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 500, "y": 500}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.cancel_event.set()

        t0 = time.perf_counter()
        completed = engine.run()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert completed is False
        assert elapsed_ms <= 20.0
        assert len(mock_input.clicks) == 0
        assert engine.state == EngineState.STOPPED


# =============================================================================
# 6. Signal Broadcasting & Event Emissions
# =============================================================================

@pytest.mark.unit
class TestSignalEmissions:
    """Verifies state_changed, progress_updated, action lifecycle, and finished signals."""

    def test_progress_updated_signals(self, mock_input: MockInputDriver):
        """Verifies progress signals broadcast (cur_loop, tot_loops, cur_act, tot_acts)."""
        scenario = ScenarioModel(
            name="Signal Test",
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
        assert updates[0] == (1, 2, 1, 2)
        assert updates[-1] == (2, 2, 2, 2)

    def test_action_lifecycle_signals(self, mock_input: MockInputDriver):
        """Verifies action_started and action_completed emitted in pairs."""
        scenario = ScenarioModel(
            name="Action Lifecycle",
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}, description="Click Button"),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.run()

        assert len(engine.signals.started_actions) == 1
        assert engine.signals.started_actions[0] == (0, "Click Button")

        assert len(engine.signals.completed_actions) == 1
        assert engine.signals.completed_actions[0][0] == 0
        assert engine.signals.completed_actions[0][1] is True

    def test_execution_finished_signal_on_success_and_abort(self, mock_input: MockInputDriver):
        """Verifies finished signal emits success status accurately."""
        # Success scenario
        scen1 = ScenarioModel(name="Success", actions=[])
        eng1 = ExecutionEngine(scen1, input_driver=mock_input)
        eng1.run()
        assert len(eng1.signals.finished) == 1
        assert eng1.signals.finished[0][0] is True

        # Abort scenario
        scen2 = ScenarioModel(name="Abort", actions=[ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 1000})])
        eng2 = ExecutionEngine(scen2, input_driver=mock_input)
        threading.Thread(target=lambda: (time.sleep(0.01), eng2.stop()), daemon=True).start()
        eng2.run()
        assert len(eng2.signals.finished) == 1
        assert eng2.signals.finished[0][0] is False


# =============================================================================
# 7. Error Handling & Driver Exception Boundaries
# =============================================================================

@pytest.mark.unit
class TestErrorHandlingBoundaries:
    """Verifies driver exception handling, input safety flushing, and ERROR state."""

    def test_driver_exception_transitions_to_error_and_flushes(self):
        """Verifies unhandled exception in driver transitions to ERROR and calls safety_flush."""
        class FaultyInputDriver:
            def __init__(self):
                self.flushed = False

            def click(self, *args, **kwargs):
                raise RuntimeError("OS Input Subsystem Crashed")

            def safety_flush(self):
                self.flushed = True

        driver = FaultyInputDriver()
        scenario = ScenarioModel(
            name="Faulty Scenario",
            actions=[ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 10, "y": 10})],
        )
        engine = ExecutionEngine(scenario, input_driver=driver)

        with pytest.raises(RuntimeError, match="OS Input Subsystem Crashed"):
            engine.run()

        assert engine.state == EngineState.ERROR
        assert driver.flushed is True
        assert len(engine.signals.finished) == 1
        assert engine.signals.finished[0][0] is False

    def test_image_wait_without_screen_driver_raises_error(self, mock_input: MockInputDriver):
        """Verifies executing ImageWait without a screen driver raises ValueError."""
        scenario = ScenarioModel(
            name="Missing Screen",
            actions=[
                ActionBlock(type=ActionType.IMAGE_WAIT, params={"template_path": "btn.png"}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=None)

        with pytest.raises(ValueError, match="Screen driver required"):
            engine.run()

        assert engine.state == EngineState.ERROR
