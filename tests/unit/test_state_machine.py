"""
tests.unit.test_state_machine
=============================
Unit test suite verifying:
- EngineState enum values and case-insensitive string parsing
- InvalidStateTransitionError exception hierarchy and diagnostics
- StateMachine default initialization and custom initial state
- Comprehensive valid transition matrix (IDLE, RUNNING, PAUSED, STOPPING, STOPPED, ERROR)
- Rejection of invalid transitions and state immutability
- Query helper properties (is_idle, is_running, etc.) and can_transition()
- Transition callbacks, on_enter/on_exit hooks, and callback error isolation
- Thread safety, re-entrant lock behavior, and concurrent contention
"""

from __future__ import annotations

import threading
import time
from typing import Callable, List, Tuple
import pytest

from action_macro.runner.state_machine import (
    EngineState,
    InvalidStateTransitionError,
    StateMachine,
)


# =============================================================================
# 1. EngineState Enum Tests
# =============================================================================

@pytest.mark.unit
class TestEngineStateEnum:
    """Verifies EngineState enum values, types, and case-insensitive parsing."""

    def test_state_enum_values(self):
        assert EngineState.IDLE.value == "IDLE"
        assert EngineState.RUNNING.value == "RUNNING"
        assert EngineState.PAUSED.value == "PAUSED"
        assert EngineState.STOPPING.value == "STOPPING"
        assert EngineState.STOPPED.value == "STOPPED"
        assert EngineState.ERROR.value == "ERROR"

    def test_state_enum_str_subclass(self):
        for state in EngineState:
            assert isinstance(state, str)
            assert state == state.value

    @pytest.mark.parametrize(
        "raw_str, expected",
        [
            ("idle", EngineState.IDLE),
            ("IDLE", EngineState.IDLE),
            ("Running", EngineState.RUNNING),
            ("running", EngineState.RUNNING),
            ("PAUSED", EngineState.PAUSED),
            ("paused", EngineState.PAUSED),
            ("stopping", EngineState.STOPPING),
            ("STOPPED", EngineState.STOPPED),
            ("error", EngineState.ERROR),
            (EngineState.RUNNING, EngineState.RUNNING),
        ],
    )
    def test_state_case_insensitive_lookup(self, raw_str, expected):
        assert EngineState.from_str(raw_str) == expected

    def test_state_invalid_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown EngineState"):
            EngineState.from_str("NON_EXISTENT_STATE")

    def test_state_invalid_type_raises_type_error(self):
        with pytest.raises(TypeError):
            EngineState.from_str(12345)  # type: ignore[arg-type]


# =============================================================================
# 2. InvalidStateTransitionError Diagnostics
# =============================================================================

@pytest.mark.unit
class TestInvalidStateTransitionError:
    """Verifies InvalidStateTransitionError exception attributes and formatting."""

    def test_error_attributes_and_message(self):
        err = InvalidStateTransitionError(EngineState.IDLE, EngineState.PAUSED)
        assert isinstance(err, ValueError)
        assert err.from_state == EngineState.IDLE
        assert err.to_state == EngineState.PAUSED
        assert "IDLE to PAUSED" in str(err)

    def test_error_custom_message(self):
        err = InvalidStateTransitionError(EngineState.STOPPED, EngineState.PAUSED, msg="Custom rejected")
        assert "Custom rejected" in str(err)


# =============================================================================
# 3. StateMachine Initialization & Properties
# =============================================================================

@pytest.mark.unit
class TestStateMachineInitialization:
    """Verifies default and custom initialization, and helper query properties."""

    def test_default_initialization(self):
        sm = StateMachine()
        assert sm.state == EngineState.IDLE
        assert sm.is_idle is True
        assert sm.is_running is False
        assert sm.is_paused is False
        assert sm.is_stopping is False
        assert sm.is_stopped is False
        assert sm.is_error is False

    def test_custom_initial_state(self):
        sm1 = StateMachine(initial_state=EngineState.STOPPED)
        assert sm1.state == EngineState.STOPPED
        assert sm1.is_stopped is True

        sm2 = StateMachine(initial_state="PAUSED")
        assert sm2.state == EngineState.PAUSED
        assert sm2.is_paused is True

    def test_boolean_query_properties_reflect_transitions(self):
        sm = StateMachine()
        assert sm.is_idle is True
        sm.transition(EngineState.RUNNING)
        assert sm.is_running is True
        assert sm.is_idle is False
        sm.transition(EngineState.PAUSED)
        assert sm.is_paused is True
        assert sm.is_running is False
        sm.transition(EngineState.STOPPING)
        assert sm.is_stopping is True
        sm.transition(EngineState.STOPPED)
        assert sm.is_stopped is True
        sm.transition(EngineState.RUNNING)
        sm.transition(EngineState.ERROR)
        assert sm.is_error is True


# =============================================================================
# 4. Valid State Transitions Matrix (16 Valid Paths)
# =============================================================================

@pytest.mark.unit
class TestValidTransitions:
    """Verifies the complete matrix of 16 permissible state transitions."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            (EngineState.IDLE, EngineState.RUNNING),
            (EngineState.RUNNING, EngineState.PAUSED),
            (EngineState.PAUSED, EngineState.RUNNING),
            (EngineState.RUNNING, EngineState.STOPPING),
            (EngineState.STOPPING, EngineState.STOPPED),
            (EngineState.RUNNING, EngineState.STOPPED),
            (EngineState.RUNNING, EngineState.IDLE),
            (EngineState.RUNNING, EngineState.ERROR),
            (EngineState.PAUSED, EngineState.STOPPING),
            (EngineState.PAUSED, EngineState.STOPPED),
            (EngineState.PAUSED, EngineState.ERROR),
            (EngineState.STOPPING, EngineState.ERROR),
            (EngineState.STOPPED, EngineState.IDLE),
            (EngineState.STOPPED, EngineState.RUNNING),
            (EngineState.ERROR, EngineState.IDLE),
            (EngineState.ERROR, EngineState.RUNNING),
        ],
    )
    def test_valid_transition_execution(self, from_state: EngineState, to_state: EngineState):
        sm = StateMachine(initial_state=from_state)
        assert sm.can_transition(to_state) is True
        res = sm.transition(to_state)
        assert res == to_state
        assert sm.state == to_state

    def test_try_transition_valid_returns_true(self):
        sm = StateMachine()
        assert sm.try_transition(EngineState.RUNNING) is True
        assert sm.state == EngineState.RUNNING

    def test_reset_from_stopped_and_error(self):
        sm_stopped = StateMachine(initial_state=EngineState.STOPPED)
        sm_stopped.reset()
        assert sm_stopped.state == EngineState.IDLE

        sm_error = StateMachine(initial_state=EngineState.ERROR)
        sm_error.reset()
        assert sm_error.state == EngineState.IDLE


# =============================================================================
# 5. Invalid State Transitions Matrix
# =============================================================================

@pytest.mark.unit
class TestInvalidTransitions:
    """Verifies rejection of invalid transitions and state immutability on failure."""

    @pytest.mark.parametrize(
        "from_state, to_state",
        [
            (EngineState.IDLE, EngineState.PAUSED),
            (EngineState.IDLE, EngineState.STOPPING),
            (EngineState.IDLE, EngineState.STOPPED),
            (EngineState.PAUSED, EngineState.IDLE),
            (EngineState.STOPPING, EngineState.RUNNING),
            (EngineState.STOPPING, EngineState.PAUSED),
            (EngineState.STOPPING, EngineState.IDLE),
            (EngineState.STOPPED, EngineState.PAUSED),
            (EngineState.STOPPED, EngineState.STOPPING),
            (EngineState.ERROR, EngineState.PAUSED),
            (EngineState.ERROR, EngineState.STOPPING),
            # Self-transitions
            (EngineState.IDLE, EngineState.IDLE),
            (EngineState.RUNNING, EngineState.RUNNING),
            (EngineState.PAUSED, EngineState.PAUSED),
            (EngineState.STOPPED, EngineState.STOPPED),
            (EngineState.ERROR, EngineState.ERROR),
        ],
    )
    def test_invalid_transition_rejected(self, from_state: EngineState, to_state: EngineState):
        sm = StateMachine(initial_state=from_state)
        assert sm.can_transition(to_state) is False

        with pytest.raises(InvalidStateTransitionError) as exc_info:
            sm.transition(to_state)

        assert exc_info.value.from_state == from_state
        assert exc_info.value.to_state == to_state
        assert sm.state == from_state  # State strictly preserved

    def test_try_transition_invalid_returns_false_without_raising(self):
        sm = StateMachine(initial_state=EngineState.IDLE)
        assert sm.try_transition(EngineState.PAUSED) is False
        assert sm.state == EngineState.IDLE

    def test_transition_to_unknown_string_raises(self):
        sm = StateMachine()
        with pytest.raises(ValueError):
            sm.transition("UNKNOWN_INVALID_STATE")
        assert sm.state == EngineState.IDLE


# =============================================================================
# 6. State Machine Callbacks & Enter/Exit Hooks
# =============================================================================

@pytest.mark.unit
class TestStateCallbacksAndHooks:
    """Verifies listener callbacks, state enter/exit hooks, and error isolation."""

    def test_global_transition_callback(self):
        sm = StateMachine()
        recorded: List[Tuple[EngineState, EngineState]] = []

        def _on_transition(old: EngineState, new: EngineState):
            recorded.append((old, new))

        sm.add_callback(_on_transition)
        sm.transition(EngineState.RUNNING)
        sm.transition(EngineState.PAUSED)
        sm.transition(EngineState.RUNNING)
        sm.transition(EngineState.IDLE)

        assert recorded == [
            (EngineState.IDLE, EngineState.RUNNING),
            (EngineState.RUNNING, EngineState.PAUSED),
            (EngineState.PAUSED, EngineState.RUNNING),
            (EngineState.RUNNING, EngineState.IDLE),
        ]

    def test_multiple_callbacks_invoked_in_order(self):
        sm = StateMachine()
        order: List[int] = []

        sm.add_callback(lambda o, n: order.append(1))
        sm.add_callback(lambda o, n: order.append(2))
        sm.add_callback(lambda o, n: order.append(3))

        sm.transition(EngineState.RUNNING)
        assert order == [1, 2, 3]

    def test_remove_callback(self):
        sm = StateMachine()
        calls = []

        def _cb(o, n):
            calls.append(n)

        sm.add_callback(_cb)
        sm.transition(EngineState.RUNNING)
        assert len(calls) == 1

        sm.remove_callback(_cb)
        sm.transition(EngineState.PAUSED)
        assert len(calls) == 1  # No additional calls

    def test_enter_and_exit_hooks(self):
        sm = StateMachine()
        exited_idle = []
        entered_running = []

        sm.on_exit(EngineState.IDLE, lambda: exited_idle.append(True))
        sm.on_enter(EngineState.RUNNING, lambda: entered_running.append(True))

        sm.transition(EngineState.RUNNING)
        assert len(exited_idle) == 1
        assert len(entered_running) == 1

        # Moving to PAUSED shouldn't trigger idle exit or running enter
        sm.transition(EngineState.PAUSED)
        assert len(exited_idle) == 1
        assert len(entered_running) == 1

    def test_callback_exception_isolation(self):
        """Verifies that an exception in a callback does not crash transition or corrupt lock."""
        sm = StateMachine()

        def _failing_callback(o, n):
            raise RuntimeError("Callback bug!")

        recorded: List[EngineState] = []
        def _healthy_callback(o, n):
            recorded.append(n)

        sm.add_callback(_failing_callback)
        sm.add_callback(_healthy_callback)

        # Transition should complete successfully despite the buggy callback
        sm.transition(EngineState.RUNNING)
        assert sm.state == EngineState.RUNNING
        assert recorded == [EngineState.RUNNING]

        # Ensure lock is not stuck: subsequent transition succeeds
        sm.transition(EngineState.PAUSED)
        assert sm.state == EngineState.PAUSED


# =============================================================================
# 7. Concurrency & Re-Entrant Lock Safety
# =============================================================================

@pytest.mark.unit
class TestStateMachineConcurrency:
    """Verifies thread-safety, re-entrant lock queries, and race condition resilience."""

    def test_reentrant_lock_safety_inside_callback(self):
        """Verifies that inspecting state inside a callback does not deadlock (RLock test)."""
        sm = StateMachine()
        queried_states = []

        def _reentrant_callback(old_s: EngineState, new_s: EngineState):
            # Query properties that acquire the internal lock
            queried_states.append(sm.state)
            queried_states.append(sm.is_running)
            queried_states.append(sm.can_transition(EngineState.PAUSED))

        sm.add_callback(_reentrant_callback)
        sm.transition(EngineState.RUNNING)

        assert queried_states[0] == EngineState.RUNNING
        assert queried_states[1] is True
        assert queried_states[2] is True

    def test_concurrent_transition_threads(self):
        """Verifies concurrent threads executing transitions do not corrupt state machine."""
        sm = StateMachine(initial_state=EngineState.RUNNING)
        errors: List[Exception] = []

        def _worker_pause():
            try:
                for _ in range(20):
                    sm.try_transition(EngineState.PAUSED)
                    time.sleep(0.001)
                    sm.try_transition(EngineState.RUNNING)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_worker_pause) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert sm.state in {EngineState.RUNNING, EngineState.PAUSED}
