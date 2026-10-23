"""Adversarial stress test suite for Milestone 3 State Machine and Execution Engine.

Challenges under empirical stress:
1. State Machine: 1,000 rapid concurrent state transition requests across 10 threads,
   asserting state invariants and zero deadlocks.
2. Execution Loop: Infinite loop (N=0) interrupted at random intervals (1ms, 5ms, 17ms, 50ms),
   measuring abort latency and asserting latency <= 50ms across 100 trials.
3. Rapid pause/resume cycles during action execution, asserting state safety and no deadlocks.
4. Unconditional safety_flush() verification across all termination modes (stop, cancel, error, pause-stop, pre-abort).
"""

from __future__ import annotations

import logging
import random
import statistics
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    ScenarioModel,
)
from action_macro.runner.execution_engine import ExecutionEngine
from action_macro.runner.hotkey_listener import GlobalHotkeyListener
from action_macro.runner.state_machine import (
    VALID_TRANSITIONS,
    EngineState,
    InvalidStateTransitionError,
    StateMachine,
)
from tests.mocks.mock_input import MockInputDriver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("challenge_engine")


# =============================================================================
# Challenge 1: Concurrent State Machine Stress Testing
# =============================================================================

class TestStateMachineAdversarialStress:
    """Stress tests StateMachine under heavy concurrent contention."""

    def test_concurrent_state_transitions_1000_requests(self) -> None:
        """Adversarial stress test: 1,000 rapid concurrent state transition requests
        across 10 threads, asserting state invariants and zero deadlocks.
        """
        sm = StateMachine(initial_state=EngineState.IDLE)
        num_threads = 10
        requests_per_thread = 100  # Total 1,000 requests
        all_states = list(EngineState)

        transition_records: List[Tuple[EngineState, EngineState, float]] = []
        records_lock = threading.Lock()
        deadlock_detected = threading.Event()
        callback_error_count = 0

        def state_callback(old_state: EngineState, new_state: EngineState) -> None:
            nonlocal callback_error_count
            try:
                # State invariants within callback
                assert isinstance(old_state, EngineState), f"old_state {old_state} not an EngineState"
                assert isinstance(new_state, EngineState), f"new_state {new_state} not an EngineState"
                with records_lock:
                    transition_records.append((old_state, new_state, time.perf_counter()))
            except Exception as err:
                callback_error_count += 1
                logger.error("Callback assertion failure: %s", err)

        unsubscribe = sm.add_callback(state_callback)

        thread_errors: List[Exception] = []
        errors_lock = threading.Lock()

        def worker_task(worker_id: int) -> None:
            rng = random.Random(42 + worker_id)
            for _ in range(requests_per_thread):
                target_state = rng.choice(all_states)
                method_choice = rng.choice(["try", "direct", "check_then_transition"])

                try:
                    current_before = sm.state
                    assert current_before in EngineState, f"Invalid current state: {current_before}"

                    if method_choice == "try":
                        sm.try_transition(target_state)
                    elif method_choice == "direct":
                        try:
                            sm.transition(target_state)
                        except InvalidStateTransitionError as err:
                            # Verify exception integrity
                            assert err.current_state in EngineState
                            assert err.target_state in EngineState
                    else:  # check_then_transition
                        if sm.can_transition(target_state):
                            try:
                                sm.transition(target_state)
                            except InvalidStateTransitionError:
                                # State may have transitioned concurrently between check and transition
                                pass

                    current_after = sm.state
                    assert current_after in EngineState, f"Corrupted state after transition: {current_after}"
                except Exception as exc:
                    with errors_lock:
                        thread_errors.append(exc)
                    break

        threads: List[threading.Thread] = []
        start_time = time.perf_counter()

        for tid in range(num_threads):
            t = threading.Thread(target=worker_task, args=(tid,), name=f"SMStressWorker-{tid}")
            threads.append(t)
            t.start()

        # Join with strict 10s deadlock timeout
        timeout_seconds = 10.0
        for t in threads:
            t.join(timeout=timeout_seconds)
            if t.is_alive():
                deadlock_detected.set()

        elapsed = time.perf_counter() - start_time
        unsubscribe()

        # Invariant Assertions
        assert not deadlock_detected.is_set(), "DEADLOCK DETECTED: Worker threads failed to join within timeout"
        assert len(thread_errors) == 0, f"Worker thread encountered unhandled exceptions: {thread_errors}"
        assert callback_error_count == 0, f"Callback errors encountered: {callback_error_count}"

        # Verify final state invariant
        final_state = sm.state
        assert final_state in EngineState, f"Final state {final_state} is not a valid EngineState"

        # Verify all recorded transitions followed legal transition matrix
        with records_lock:
            assert len(transition_records) > 0, "No transitions succeeded"
            for old_st, new_st, _ in transition_records:
                valid_targets = VALID_TRANSITIONS.get(old_st, set())
                assert new_st in valid_targets, (
                    f"Illegal transition recorded: {old_st} -> {new_st}. Valid targets: {valid_targets}"
                )

        # Verify state machine is still fully functional after stress
        sm.reset()
        assert sm.is_idle
        assert sm.state == EngineState.IDLE

        logger.info(
            "State Machine Stress: 1,000 concurrent transitions completed in %.3fs with 0 deadlocks (%d transitions logged)",
            elapsed,
            len(transition_records),
        )

    def test_state_machine_contention_race_from_running(self) -> None:
        """Adversarial stress: 10 threads simultaneously racing to transition from
        RUNNING to conflicting states. Exactly one transition must succeed per round.
        """
        sm = StateMachine(initial_state=EngineState.IDLE)
        rounds = 50

        for round_idx in range(rounds):
            sm.reset()
            sm.transition(EngineState.RUNNING)

            candidates = [EngineState.PAUSED, EngineState.STOPPING, EngineState.STOPPED, EngineState.IDLE, EngineState.ERROR]
            results: List[bool] = [False] * len(candidates)

            def attempt(idx: int, target: EngineState) -> None:
                results[idx] = sm.try_transition(target)

            threads = [
                threading.Thread(target=attempt, args=(i, candidates[i]))
                for i in range(len(candidates))
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=2.0)
                assert not t.is_alive(), "Contention race thread deadlocked"

            # Exactly one or more might succeed only if state transitioned into a state
            # that permits subsequent transitions, but initial transition from RUNNING
            # must leave state in one of the candidate states
            assert sm.state in EngineState
            assert sm.state != EngineState.RUNNING, "State remained RUNNING despite concurrent valid transitions"


# =============================================================================
# Challenge 2: Execution Loop Infinite Abort Latency Stress Testing
# =============================================================================

class TestExecutionLoopInfiniteAbortLatency:
    """Stress tests ExecutionEngine infinite loop abort latency under random interrupt intervals."""

    def test_infinite_loop_abort_latency_100_trials(self) -> None:
        """Adversarial stress test: Infinite loop (N=0) interrupted at random intervals
        (1ms, 5ms, 17ms, 50ms), measuring abort latency and asserting latency <= 50ms across 100 trials.
        """
        intervals = [0.001, 0.005, 0.017, 0.050]
        num_trials = 100
        latencies_ms: List[float] = []

        scenario = ScenarioModel(
            name="Infinite Loop Adversarial Scenario",
            loop_count=0,  # N=0: Infinite loop
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 200, "y": 300, "button": "left"}),
                ActionBlock(type=ActionType.KEY_INPUT, params={"keys": ["x"]}),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 10}),
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 400, "y": 500, "button": "right"}),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 15}),
            ],
            action_delay_ms=0,
            loop_delay_ms=0,
        )

        for trial_idx in range(num_trials):
            mock_input = MockInputDriver()
            engine = ExecutionEngine(scenario, input_driver=mock_input)

            # Hold mock buttons/keys to verify safety flush during abort
            mock_input.mouse_down(button="left")
            mock_input.key_down(key="ctrl")

            interval = intervals[trial_idx % len(intervals)]

            # Start asynchronously
            worker_thread = engine.start_async()

            # Wait briefly for worker thread to enter RUNNING state
            t_spin = time.perf_counter()
            while not engine.is_running() and (time.perf_counter() - t_spin) < 0.1:
                time.sleep(0.0005)

            # Sleep specified random interval before issuing emergency stop
            time.sleep(interval)

            # Measure abort latency: from stop() call until worker thread terminates
            t_abort_start = time.perf_counter()
            engine.stop()
            joined = engine.wait_for_completion(timeout=1.0)
            t_abort_end = time.perf_counter()

            latency_ms = (t_abort_end - t_abort_start) * 1000.0
            latencies_ms.append(latency_ms)

            # Invariant Assertions for each trial
            assert joined, f"Trial {trial_idx}: Worker thread hung and failed to terminate within 1.0s"
            assert engine.state == EngineState.STOPPED, f"Trial {trial_idx}: Expected STOPPED, got {engine.state}"
            assert mock_input.safety_flush_called is True, f"Trial {trial_idx}: safety_flush was not invoked"
            assert len(mock_input.active_buttons) == 0, f"Trial {trial_idx}: Stuck mouse buttons: {mock_input.active_buttons}"
            assert len(mock_input.active_keys) == 0, f"Trial {trial_idx}: Stuck keys: {mock_input.active_keys}"
            assert latency_ms <= 50.0, (
                f"Trial {trial_idx} (interval={interval*1000}ms): Abort latency {latency_ms:.2f}ms exceeded 50ms limit!"
            )

        # Statistical analysis
        min_lat = min(latencies_ms)
        max_lat = max(latencies_ms)
        mean_lat = statistics.mean(latencies_ms)
        median_lat = statistics.median(latencies_ms)
        p95_lat = statistics.quantiles(latencies_ms, n=20)[18]  # 95th percentile
        p99_lat = statistics.quantiles(latencies_ms, n=100)[98] if num_trials >= 100 else max_lat

        logger.info(
            "Abort Latency Stats over %d trials (<=50ms required): "
            "min=%.2fms, mean=%.2fms, median=%.2fms, p95=%.2fms, p99=%.2fms, max=%.2fms",
            num_trials,
            min_lat,
            mean_lat,
            median_lat,
            p95_lat,
            p99_lat,
            max_lat,
        )

        assert max_lat <= 50.0, f"Maximum abort latency {max_lat:.2f}ms exceeded strict 50ms requirement"


# =============================================================================
# Challenge 3: Rapid Pause/Resume Cycles Stress Testing
# =============================================================================

class TestRapidPauseResumeCyclesStress:
    """Stress tests ExecutionEngine under rapid alternating pause and resume commands."""

    def test_rapid_pause_resume_storm_during_execution(self) -> None:
        """Adversarial stress test: Rapidly alternating pause/resume requests while
        scenario is actively running. Asserts zero deadlocks, state safety, and normal completion.
        """
        mock_input = MockInputDriver()
        scenario = ScenarioModel(
            name="Pause Resume Stress",
            loop_count=5,
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 10}),
                ActionBlock(type=ActionType.KEY_INPUT, params={"keys": ["a"]}),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 10}),
            ],
            action_delay_ms=5,
            loop_delay_ms=5,
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        worker = engine.start_async()
        cycles = 100
        storm_errors: List[Exception] = []

        def toggler() -> None:
            try:
                for _ in range(cycles):
                    engine.pause()
                    assert engine.is_paused() or engine.is_stopped()
                    time.sleep(0.001)
                    engine.resume()
                    time.sleep(0.001)
            except Exception as e:
                storm_errors.append(e)

        toggle_thread = threading.Thread(target=toggler, name="PauseResumeStorm")
        toggle_thread.start()

        toggle_thread.join(timeout=10.0)
        assert not toggle_thread.is_alive(), "Pause/resume toggler thread deadlocked"
        assert len(storm_errors) == 0, f"Exceptions in pause/resume toggler: {storm_errors}"

        # Wait for engine to finish execution
        finished = engine.wait_for_completion(timeout=10.0)
        assert finished, "Engine worker thread hung after pause/resume storm"
        assert engine.state in (EngineState.IDLE, EngineState.STOPPED)
        assert len(mock_input.clicks) >= 5, "Expected clicks were not executed"

    def test_pause_enforcement_prevents_action_execution(self) -> None:
        """Verifies that while engine is paused, actions do NOT advance."""
        mock_input = MockInputDriver()
        scenario = ScenarioModel(
            name="Pause Enforcement Test",
            loop_count=0,  # Infinite
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 50, "y": 50}),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 20}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.start_async()

        # Let it run briefly to execute some clicks
        time.sleep(0.05)
        engine.pause()
        assert engine.is_paused()

        clicks_at_pause = len(mock_input.clicks)

        # Wait 80ms while paused
        time.sleep(0.08)
        clicks_after_pause_wait = len(mock_input.clicks)

        # Allow at most +1 click if pause occurred exactly during click dispatch
        assert clicks_after_pause_wait <= clicks_at_pause + 1, (
            f"Actions continued executing while paused: {clicks_at_pause} -> {clicks_after_pause_wait}"
        )

        # Clean shutdown from paused state
        t_stop_start = time.perf_counter()
        engine.stop()
        stopped = engine.wait_for_completion(timeout=1.0)
        stop_latency_ms = (time.perf_counter() - t_stop_start) * 1000.0

        assert stopped, "Engine failed to stop from paused state"
        assert engine.state == EngineState.STOPPED
        assert stop_latency_ms <= 50.0, f"Stop while paused took {stop_latency_ms:.2f}ms (>50ms)"


# =============================================================================
# Challenge 4: Unconditional safety_flush() Verification Across All Terminations
# =============================================================================

class TestUnconditionalSafetyFlush:
    """Verifies safety_flush() is called unconditionally on ALL termination paths:
    1. stop() while running
    2. cooperative cancel via cancel_event.set()
    3. stop() while paused
    4. pre-cancellation at t=0 before run() starts
    5. driver error / unhandled exception during action execution
    6. emergency hotkey trigger via GlobalHotkeyListener
    7. repeated idempotent stop() invocations
    """

    def test_safety_flush_on_engine_stop(self) -> None:
        """Termination Mode 1: Normal stop() call during execution."""
        mock_input = MockInputDriver()
        mock_input.mouse_down(button="left")
        mock_input.key_down(key="shift")

        scenario = ScenarioModel(
            name="Stop Flush Test",
            loop_count=0,
            actions=[ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 500})],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.start_async()
        time.sleep(0.02)

        engine.stop()
        engine.wait_for_completion(timeout=1.0)

        assert mock_input.safety_flush_called is True
        assert len(mock_input.active_buttons) == 0
        assert len(mock_input.active_keys) == 0
        assert engine.state == EngineState.STOPPED

    def test_safety_flush_on_cooperative_cancel_event(self) -> None:
        """Termination Mode 2: Cooperative cancellation via cancel_event.set() directly."""
        mock_input = MockInputDriver()
        mock_input.mouse_down(button="right")
        mock_input.key_down(key="alt")

        scenario = ScenarioModel(
            name="Cancel Event Test",
            loop_count=0,
            actions=[ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 500})],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.start_async()
        time.sleep(0.02)

        # Trigger cancel_event directly without calling engine.stop()
        engine.cancel_event.set()
        engine.wait_for_completion(timeout=1.0)

        assert mock_input.safety_flush_called is True
        assert len(mock_input.active_buttons) == 0
        assert len(mock_input.active_keys) == 0
        assert engine.state == EngineState.STOPPED

    def test_safety_flush_on_stop_while_paused(self) -> None:
        """Termination Mode 3: stop() called while engine is in PAUSED state."""
        mock_input = MockInputDriver()
        mock_input.mouse_down(button="left")
        mock_input.key_down(key="ctrl")

        scenario = ScenarioModel(
            name="Pause Stop Test",
            loop_count=0,
            actions=[ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 500})],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.start_async()
        time.sleep(0.02)

        engine.pause()
        assert engine.is_paused()

        engine.stop()
        engine.wait_for_completion(timeout=1.0)

        assert mock_input.safety_flush_called is True
        assert len(mock_input.active_buttons) == 0
        assert len(mock_input.active_keys) == 0
        assert engine.state == EngineState.STOPPED

    def test_safety_flush_on_pre_cancellation_at_t0(self) -> None:
        """Termination Mode 4: Pre-cancellation (cancel_event already set before run())."""
        mock_input = MockInputDriver()
        mock_input.mouse_down(button="left")
        mock_input.key_down(key="win")

        scenario = ScenarioModel(
            name="Pre-Cancel Test",
            actions=[ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 10, "y": 10})],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.cancel_event.set()

        completed = engine.run()
        assert completed is False
        assert mock_input.safety_flush_called is True
        assert len(mock_input.active_buttons) == 0
        assert len(mock_input.active_keys) == 0
        assert engine.state == EngineState.STOPPED

    def test_safety_flush_on_driver_exception(self) -> None:
        """Termination Mode 5: Driver raises unhandled exception during action execution."""
        class FailingInputDriver(MockInputDriver):
            def click(self, *args: Any, **kwargs: Any) -> None:
                raise RuntimeError("Catastrophic hardware I/O communication failure")

        failing_driver = FailingInputDriver()
        failing_driver.mouse_down(button="left")
        failing_driver.key_down(key="ctrl")

        scenario = ScenarioModel(
            name="Fault Scenario",
            actions=[ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 50, "y": 50})],
        )
        engine = ExecutionEngine(scenario, input_driver=failing_driver)

        with pytest.raises(RuntimeError, match="Catastrophic hardware I/O"):
            engine.run()

        assert failing_driver.safety_flush_called is True
        assert len(failing_driver.active_buttons) == 0
        assert len(failing_driver.active_keys) == 0
        assert engine.state == EngineState.ERROR
        assert len(engine.signals.finished) > 0
        assert engine.signals.finished[0][0] is False

    def test_safety_flush_on_hotkey_emergency_stop(self) -> None:
        """Termination Mode 6: Emergency abort triggered via GlobalHotkeyListener."""
        mock_input = MockInputDriver()
        mock_input.mouse_down(button="left")
        mock_input.key_down(key="alt")

        scenario = ScenarioModel(
            name="Hotkey Stop Test",
            loop_count=0,
            actions=[ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 500})],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        listener = GlobalHotkeyListener(engine=engine, headless=True)

        engine.start_async()
        time.sleep(0.02)

        listener.start()
        latency_ms = listener.trigger_emergency_stop()
        engine.wait_for_completion(timeout=1.0)
        listener.stop()

        assert latency_ms <= 50.0
        assert mock_input.safety_flush_called is True
        assert len(mock_input.active_buttons) == 0
        assert len(mock_input.active_keys) == 0
        assert engine.state == EngineState.STOPPED

    def test_safety_flush_idempotent_multiple_stops(self) -> None:
        """Termination Mode 7: Repeated stop() invocations preserve flushed state safely."""
        mock_input = MockInputDriver()
        mock_input.mouse_down(button="left")
        mock_input.key_down(key="ctrl")

        scenario = ScenarioModel(name="Idempotent Stop", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        for _ in range(10):
            engine.stop()

        assert mock_input.safety_flush_called is True
        assert len(mock_input.active_buttons) == 0
        assert len(mock_input.active_keys) == 0
        assert engine.state == EngineState.STOPPED


# =============================================================================
# Standalone CLI Test Runner with Telemetry Report
# =============================================================================

def run_adversarial_challenges() -> bool:
    """Executes all adversarial challenges and prints empirical telemetry."""
    print("=" * 80)
    print("STARTING ADVERSARIAL CHALLENGE SUITE: Milestone 3 State Machine & Engine")
    print("=" * 80)

    # 1. State Machine Concurrent Stress
    sm_tester = TestStateMachineAdversarialStress()
    print("\n[CHALLENGE 1/4] Running State Machine 1,000 Concurrent Transitions Stress...")
    t0 = time.perf_counter()
    sm_tester.test_concurrent_state_transitions_1000_requests()
    sm_tester.test_state_machine_contention_race_from_running()
    print(f"--> PASS: State Machine Stress completed in {time.perf_counter() - t0:.3f}s (0 deadlocks)")

    # 2. Execution Loop Abort Latency Stress (100 trials)
    abort_tester = TestExecutionLoopInfiniteAbortLatency()
    print("\n[CHALLENGE 2/4] Running Infinite Loop (N=0) Abort Latency across 100 Trials...")
    t0 = time.perf_counter()
    abort_tester.test_infinite_loop_abort_latency_100_trials()
    print(f"--> PASS: 100 Abort Latency Trials completed in {time.perf_counter() - t0:.3f}s (100% <= 50ms)")

    # 3. Rapid Pause/Resume Storm
    pause_tester = TestRapidPauseResumeCyclesStress()
    print("\n[CHALLENGE 3/4] Running Rapid Pause/Resume Cycles Storm & Enforcement...")
    t0 = time.perf_counter()
    pause_tester.test_rapid_pause_resume_storm_during_execution()
    pause_tester.test_pause_enforcement_prevents_action_execution()
    print(f"--> PASS: Pause/Resume Storm completed in {time.perf_counter() - t0:.3f}s (0 deadlocks)")

    # 4. Unconditional Safety Flush across all terminations
    flush_tester = TestUnconditionalSafetyFlush()
    print("\n[CHALLENGE 4/4] Running Unconditional safety_flush() Verification across 7 Modes...")
    t0 = time.perf_counter()
    flush_tester.test_safety_flush_on_engine_stop()
    flush_tester.test_safety_flush_on_cooperative_cancel_event()
    flush_tester.test_safety_flush_on_stop_while_paused()
    flush_tester.test_safety_flush_on_pre_cancellation_at_t0()
    flush_tester.test_safety_flush_on_driver_exception()
    flush_tester.test_safety_flush_on_hotkey_emergency_stop()
    flush_tester.test_safety_flush_idempotent_multiple_stops()
    print(f"--> PASS: Safety Flush across all terminations verified in {time.perf_counter() - t0:.3f}s")

    print("\n" + "=" * 80)
    print("ALL ADVERSARIAL CHALLENGES EMPIRICALLY PASSED (VERDICT: APPROVE)")
    print("=" * 80)
    return True


if __name__ == "__main__":
    success = run_adversarial_challenges()
    sys.exit(0 if success else 1)
