"""
tests.unit.test_challenger_m4_lifecycle_stress
=============================================
Deep adversarial empirical lifecycle stress testing for MainWindow in Milestone 4.

Targeting:
1. Rapid Window Close Stress:
   - Closing window immediately after start_macro() (race condition on thread spawn)
   - Closing window while macro is actively executing actions
   - Multi-cycle rapid open/start/close stress (20 consecutive cycles)
   - Window close while in PAUSED state
2. Re-entrancy & Repeated F5 Stress:
   - Burst 50 rapid F5 / start_macro calls while running
   - Burst F5 calls while paused
   - Verification that signals are not multiplied or connected multiple times
   - Verification that subsequent macro runs work correctly after natural completion
3. Disconnect & Teardown Verification:
   - Strict capture of all warnings ensuring zero RuntimeWarning or RuntimeError
   - Idempotency of _cleanup_runner (multiple calls in series)
   - Behavior when signals are emitted after teardown
"""

from __future__ import annotations

import threading
import time
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.gui.main_window import MainWindow
from action_macro.runner.execution_engine import ExecutionEngine
from action_macro.runner.state_machine import EngineState
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


@pytest.fixture
def sample_scenario() -> ScenarioModel:
    return ScenarioModel(
        name="Lifecycle Stress Scenario",
        loop_count=10,
        actions=[
            ActionBlock(
                id="act-1",
                type=ActionType.MOUSE_CLICK,
                params=MouseClickParams(x=100, y=200, clicks=1),
                description="Click test",
            ),
            ActionBlock(
                id="act-2",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=50),
                description="Wait 50ms",
            ),
            ActionBlock(
                id="act-3",
                type=ActionType.KEY_INPUT,
                params=KeyInputParams(keys=["enter"], action="press"),
                description="Key press",
            ),
        ],
    )


class TestRapidWindowCloseStress:
    """Empirical verification of rapid and race-condition window closure."""

    def test_rapid_window_close_immediate_after_start(self, sample_scenario, qtbot):
        """Close window immediately (<1ms) after calling start_macro.
        Checks for thread startup race conditions and dangling thread termination.
        """
        for cycle in range(10):
            mock_input = MockInputDriver()
            mock_screen = MockScreenDriver()
            win = MainWindow(
                scenario=sample_scenario.clone(),
                input_driver=mock_input,
                screen_driver=mock_screen,
            )
            qtbot.addWidget(win)

            win.start_macro()
            engine = win.engine
            worker = win._worker_thread
            try:
                # Immediate close without sleeping
                with patch.object(win, "confirm_discard_changes", return_value=True):
                    win.close()

                # Ensure engine stopped
                if engine is not None:
                    assert engine.cancel_event.is_set(), (
                        "VULNERABILITY: win.close() immediately after start_macro did NOT set cancel_event "
                        "because engine was still in EngineState.IDLE, leaving an orphaned worker thread running!"
                    )
                    assert engine.is_running() is False
                assert win.hotkey_listener is None
            finally:
                if engine is not None:
                    engine.stop()
                    engine.wait_for_completion(timeout=2.0)
                if worker is not None and worker.is_alive():
                    worker.join(timeout=1.0)
            QApplication.processEvents()

    def test_window_close_while_actively_executing_actions(self, sample_scenario, qtbot):
        """Close window while macro engine is actively inside a running loop."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        # Longer scenario to ensure it is in the middle of execution
        scenario = ScenarioModel(
            name="Active Execution Close",
            loop_count=50,
            actions=[
                ActionBlock(
                    id="act-wait",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=100),
                )
            ],
        )

        win = MainWindow(
            scenario=scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        win.start_macro()
        assert win.engine is not None
        # Wait until it is confirmed running
        qtbot.waitUntil(lambda: win.engine.is_running(), timeout=1000)

        # Close while running
        with patch.object(win, "confirm_discard_changes", return_value=True):
            win.close()

        assert win.engine.is_running() is False
        assert win.engine.cancel_event.is_set() is True
        assert win._worker_thread is None or not win._worker_thread.is_alive()
        assert win.hotkey_listener is None

    def test_window_close_while_paused(self, sample_scenario, qtbot):
        """Close window while macro is in PAUSED state."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        scenario = ScenarioModel(
            name="Paused Close",
            loop_count=50,
            actions=[
                ActionBlock(
                    id="act-w",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=100),
                )
            ],
        )

        win = MainWindow(
            scenario=scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        win.start_macro()
        qtbot.waitUntil(lambda: win.engine is not None and win.engine.is_running(), timeout=1000)

        win.pause_macro()
        qtbot.waitUntil(lambda: win.engine.is_paused(), timeout=1000)
        assert win.engine.is_paused()

        # Close while paused
        with patch.object(win, "confirm_discard_changes", return_value=True):
            win.close()

        assert win.engine.is_running() is False
        assert win.engine.cancel_event.is_set() is True
        assert win.hotkey_listener is None


class TestRepeatedF5AndReentrancyStress:
    """Empirical verification of repeated start_macro calls and signal stability."""

    def test_burst_f5_calls_while_running(self, sample_scenario, qtbot):
        """Burst 50 rapid calls to start_macro while execution is underway."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        win = MainWindow(
            scenario=sample_scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        win.start_macro()
        initial_engine = win.engine
        assert initial_engine is not None
        qtbot.waitUntil(lambda: initial_engine.is_running(), timeout=1000)

        # Record log count before burst
        logs_before = len(win.log_console._entries)

        # Burst 50 start_macro calls
        for _ in range(50):
            win.start_macro()

        # Verification: engine was NOT replaced
        assert win.engine is initial_engine
        assert win.engine.is_running()

        # Verify warning log was emitted for duplicate starts
        warning_entries = [e for e in win.log_console._entries[logs_before:] if e.level == "WARNING"]
        assert len(warning_entries) == 50
        assert all("already in progress" in e.message for e in warning_entries)

        win.stop_macro()
        initial_engine.wait_for_completion(timeout=2.0)
        win.mark_dirty(False)

    def test_immediate_f5_double_tap_race_condition(self, sample_scenario, qtbot):
        """Rapid double tap of F5 (<1ms between calls) must NOT spawn two engines concurrently.
        Exposes TOCTOU race where engine is still in EngineState.IDLE when 2nd call arrives.
        """
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        win = MainWindow(
            scenario=sample_scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        # Immediate double start without waiting
        win.start_macro()
        first_engine = win.engine
        first_worker = win._worker_thread

        win.start_macro()
        second_engine = win.engine
        second_worker = win._worker_thread

        try:
            # Both engines must be identical, only one worker thread should exist
            assert second_engine is first_engine, (
                "VULNERABILITY: Rapid double F5 spawned a second ExecutionEngine because start_macro "
                "only checked engine.is_running() instead of worker thread liveness!"
            )
            assert second_worker is first_worker
        finally:
            if first_engine is not None:
                first_engine.stop()
                first_engine.wait_for_completion(timeout=2.0)
            if second_engine is not None and second_engine is not first_engine:
                second_engine.stop()
                second_engine.wait_for_completion(timeout=2.0)
            win.mark_dirty(False)

    def test_burst_f5_calls_while_paused(self, sample_scenario, qtbot):
        """Burst 20 calls to start_macro while execution is PAUSED."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        win = MainWindow(
            scenario=sample_scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        win.start_macro()
        qtbot.waitUntil(lambda: win.engine is not None and win.engine.is_running(), timeout=1000)
        win.pause_macro()
        qtbot.waitUntil(lambda: win.engine.is_paused(), timeout=1000)

        engine_before = win.engine

        for _ in range(20):
            win.start_macro()

        assert win.engine is engine_before
        assert win.engine.is_paused()

        win.stop_macro()
        win.engine.wait_for_completion(timeout=2.0)
        win.mark_dirty(False)

    def test_sequential_runs_after_natural_completion(self, qtbot):
        """Verifies that multiple macro runs can be executed back-to-back naturally."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        scenario = ScenarioModel(
            name="Quick Scenario",
            loop_count=1,
            actions=[
                ActionBlock(
                    id="act-q",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=10),
                )
            ],
        )

        win = MainWindow(
            scenario=scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        for run_idx in range(5):
            win.start_macro()
            engine = win.engine
            assert engine is not None

            # Wait for natural completion
            engine.wait_for_completion(timeout=3.0)
            QApplication.processEvents()

            # State after natural completion should be IDLE/STOPPED and cleaned
            assert win.engine is not None
            assert win.engine.is_running() is False
            assert win.hotkey_listener is None

        win.mark_dirty(False)

    def test_no_signal_multiplication_across_multiple_runs(self, qtbot):
        """Verify that starting a macro multiple times does not result in duplicated signal slots."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        scenario = ScenarioModel(
            name="Signal Multi Test",
            loop_count=1,
            actions=[
                ActionBlock(
                    id="act-s",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=10),
                )
            ],
        )

        win = MainWindow(
            scenario=scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        # Run 1
        win.start_macro()
        win.engine.wait_for_completion(timeout=2.0)
        QApplication.processEvents()

        # Run 2
        count_before = len(win.log_console._entries)
        win.start_macro()
        win.engine.wait_for_completion(timeout=2.0)
        QApplication.processEvents()
        count_after = len(win.log_console._entries)

        # If signals were multiplied (e.g. connected to old and new slots),
        # logs would duplicate exponentially.
        new_logs = win.log_console._entries[count_before:count_after]
        # In a 1-action, 1-loop run: Macro started, Action started, Action completed, Macro finished
        # There should be roughly 4-6 logs, definitely fewer than 15.
        assert len(new_logs) <= 8, f"Too many duplicate logs emitted: {len(new_logs)}"
        win.mark_dirty(False)


class TestDisconnectAndTeardownVerification:
    """Empirical verification ensuring no RuntimeWarning or RuntimeError during disconnect."""

    def test_no_runtime_warning_during_execution_finish(self, sample_scenario, qtbot):
        """Run macro to completion while recording all warnings. Zero RuntimeWarning expected."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        scenario = ScenarioModel(
            name="Warning Free Run",
            loop_count=1,
            actions=[
                ActionBlock(
                    id="act-w1",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=20),
                )
            ],
        )

        win = MainWindow(
            scenario=scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        with warnings.catch_warnings(record=True) as captured_warnings:
            warnings.simplefilter("always")
            win.start_macro()
            win.engine.wait_for_completion(timeout=3.0)
            QApplication.processEvents()

            # Trigger cleanup
            win._cleanup_runner()
            QApplication.processEvents()

        runtime_warnings = [
            w for w in captured_warnings
            if issubclass(w.category, RuntimeWarning) or "disconnect" in str(w.message).lower()
        ]
        assert len(runtime_warnings) == 0, f"Encountered unexpected RuntimeWarnings: {[str(w.message) for w in runtime_warnings]}"
        win.mark_dirty(False)

    def test_cleanup_runner_idempotency(self, sample_scenario, qtbot):
        """Calling _cleanup_runner multiple times consecutively must be safe and idempotent."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        win = MainWindow(
            scenario=sample_scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        # 1. Cleanup on fresh window (engine is None)
        for _ in range(5):
            win._cleanup_runner()

        # 2. Cleanup after starting
        win.start_macro()
        qtbot.waitUntil(lambda: win.engine is not None and win.engine.is_running(), timeout=1000)

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always")
            for _ in range(5):
                win._cleanup_runner()

        runtime_warnings = [w for w in captured if issubclass(w.category, RuntimeWarning)]
        assert len(runtime_warnings) == 0
        assert win.engine.is_running() is False
        assert win.hotkey_listener is None
        win.mark_dirty(False)

    def test_post_close_signal_emission_safety(self, sample_scenario, qtbot):
        """Emitting engine signals after _cleanup_runner has disconnected them must be safe."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        win = MainWindow(
            scenario=sample_scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        win.start_macro()
        engine = win.engine
        assert engine is not None
        signals = engine.signals

        # Run cleanup
        win._cleanup_runner()

        # Manually emit signals from engine
        # Since they are disconnected, they should not trigger slots on destroyed widgets
        signals.state_changed.emit("RUNNING")
        signals.progress_updated.emit(1, 10, 0, 3)
        signals.action_started.emit(0, "Action 0")
        signals.log_emitted.emit("INFO", "Orphaned log")
        signals.execution_finished.emit(True, "Finished")

        QApplication.processEvents()
        # Ensure no unhandled exception or crash occurred
        assert True
        win.mark_dirty(False)
