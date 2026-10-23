"""
tests.unit.test_challenger_m4_2
===============================
Adversarial empirical challenge suite for Milestone 4 (PySide6 Desktop GUI).
Targeting:
1. Coordinate Picker Simulation Stress:
   - Negative offsets (e.g., x=-1920, y=-500) and extreme coordinates (e.g. 100k)
   - Rapid coordinate updates (burst simulation)
   - Multi-screen geometry offset handling
   - Painting and HUD clamping at negative and offscreen positions via widget render
   - Click and cancel permutations (Left, Right, ESC, repeated triggers)
2. LogConsole Memory Stress:
   - 20,000 log lines rapid push (verifying <= 5,000 capping in buffer & widget)
   - Multi-threaded concurrent log bursts via Qt queued signal/slot mechanism
   - Non-signal direct thread invocation behavior check (identifying lack of thread-safety for direct calls)
   - Memory stability & leak prevention check (< 15MB diff after 20k entries)
   - Filter switches, clipboard copy, and clear under heavy load
3. Execution Lifecycle Stress:
   - Rapid Start -> Pause -> Resume -> Stop cycles
   - Rapid Start -> Emergency Stop abort within <1s
   - Re-entrancy vulnerability analysis (start_macro while already running)
   - Empty scenario execution prevention
   - Clean runner thread termination on window close
4. Dirty Tracking & Persistence:
   - Title asterisk '*' tracking across builder, inspector, dashboard, and picker triggers
   - Discard changes confirmation dialog (Save, Discard, Cancel branches)
   - Save/Load scenario roundtrips with full parameter fidelity across all action types
"""

from __future__ import annotations

import os
import threading
import time
import tracemalloc
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMessageBox

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
from action_macro.gui.dashboard import ExecutionDashboard
from action_macro.gui.log_console import LogConsole
from action_macro.gui.main_window import MainWindow
from action_macro.runner.execution_engine import ExecutionEngine
from action_macro.runner.state_machine import EngineState
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


# =============================================================================
# 1. COORDINATE PICKER SIMULATION STRESS
# =============================================================================

class TestCoordinatePickerStress:
    """Adversarial stress testing for CoordinatePickerOverlay."""

    def test_negative_and_extreme_coordinates_tracking(self, qtbot):
        """Tests that negative offsets (multi-monitor) and extreme coordinates are tracked properly."""
        picker = CoordinatePickerOverlay()
        qtbot.addWidget(picker)

        test_coords = [
            (-1920, -500),
            (-3840, -1080),
            (-1, -1),
            (0, 0),
            (1920, 1080),
            (3840, 2160),
            (99999, 88888),
            (-50000, -50000),
        ]

        for x, y in test_coords:
            with qtbot.waitSignal(picker.mouse_moved) as blocker:
                picker.simulate_mouse_move(x, y)
            assert blocker.args == [x, y]
            assert picker.get_current_coordinates() == (x, y)

    def test_negative_coordinates_paint_pipeline_safety(self, qtbot):
        """Tests that widget render and HUD layout do not crash or overflow with negative coordinates."""
        mock_screen = MockScreenDriver(width=1920, height=1080)
        picker = CoordinatePickerOverlay(screen_adapter=mock_screen)
        qtbot.addWidget(picker)
        picker.resize(1920, 1080)

        # Offscreen image canvas for rendering
        img = QImage(1920, 1080, QImage.Format.Format_ARGB32)

        stress_points = [
            (-1920, -500),
            (-100, 500),
            (500, -200),
            (3000, 3000),
            (-1, 0),
            (0, -1),
        ]

        for x, y in stress_points:
            picker.update_cursor_position(x, y, x, y)
            # Render widget into image to ensure no painting exception or division by zero
            picker.render(img)

        assert picker.get_current_coordinates() == (0, -1)

    def test_rapid_coordinate_burst_simulation(self, qtbot):
        """Simulates rapid high-frequency mouse movements (e.g. 5,000 hops)."""
        picker = CoordinatePickerOverlay()
        qtbot.addWidget(picker)

        start_time = time.monotonic()
        for i in range(5000):
            # Alternating positive and negative coordinates
            x = (i % 3840) - 1920
            y = (i % 2160) - 1080
            picker.update_cursor_position(x, y, x, y)

        elapsed = time.monotonic() - start_time
        # Ensure 5,000 updates complete rapidly without lag
        assert elapsed < 3.0, f"5,000 coordinate updates took too long: {elapsed:.2f}s"
        assert picker.get_current_coordinates() == ((4999 % 3840) - 1920, (4999 % 2160) - 1080)

    def test_mock_target_screen_with_negative_geometry(self, qtbot):
        """Tests coordinate translation when target screen has negative top-left geometry."""
        class MockNegativeScreen:
            def geometry(self):
                return QRect(-1920, 0, 1920, 1080)
            def devicePixelRatio(self):
                return 1.0
            def grabWindow(self, win_id):
                from PySide6.QtGui import QPixmap
                return QPixmap()

        mock_screen_obj = MockNegativeScreen()
        picker = CoordinatePickerOverlay(target_screen=mock_screen_obj)
        qtbot.addWidget(picker)

        # Hover at global coordinates (-1000, 400)
        # local_x = -1000 - (-1920) = 920, local_y = 400 - 0 = 400
        picker.simulate_mouse_move(-1000, 400)
        assert picker.current_local_x == 920
        assert picker.current_local_y == 400
        assert picker.get_current_coordinates() == (-1000, 400)

    def test_click_and_cancel_permutations(self, qtbot):
        """Tests various click buttons and cancel pathways."""
        # 1. Left click commits coordinates
        picked = []
        picker1 = CoordinatePickerOverlay(callback=lambda x, y: picked.append((x, y)))
        qtbot.addWidget(picker1)
        picker1.start()

        with qtbot.waitSignal(picker1.coordinate_selected) as blocker:
            picker1.simulate_click(-500, 250, button="left")

        assert blocker.args == [-500, 250]
        assert picked == [(-500, 250)]
        assert picker1.selected_coord == (-500, 250)
        assert not picker1.isVisible()

        # 2. Right click cancels
        cancelled = []
        picker2 = CoordinatePickerOverlay(cancel_callback=lambda: cancelled.append(True))
        qtbot.addWidget(picker2)
        picker2.start()

        with qtbot.waitSignal(picker2.cancelled):
            picker2.simulate_click(100, 200, button="right")

        assert cancelled == [True]
        assert picker2.is_cancelled is True
        assert picker2.selected_coord is None
        assert not picker2.isVisible()

        # 3. Middle / arbitrary button cancels
        cancelled_mid = []
        picker3 = CoordinatePickerOverlay(cancel_callback=lambda: cancelled_mid.append(True))
        qtbot.addWidget(picker3)
        picker3.start()

        with qtbot.waitSignal(picker3.cancelled):
            picker3.simulate_click(100, 200, button="middle")

        assert cancelled_mid == [True]
        assert not picker3.isVisible()


# =============================================================================
# 2. LOG CONSOLE MEMORY STRESS
# =============================================================================

class TestLogConsoleMemoryStress:
    """Stress tests verifying block count capping, memory stability, and threading."""

    def test_20000_lines_push_and_capping(self, qtbot):
        """Pushes 20,000 log lines rapidly and verifies capping <= 5,000 lines."""
        console = LogConsole()
        qtbot.addWidget(console)

        levels = ["INFO", "WARNING", "ERROR", "ACTION", "DEBUG", "CUSTOM"]
        start_time = time.monotonic()

        for i in range(20000):
            lvl = levels[i % len(levels)]
            console.append_log(lvl, f"Stress test log entry #{i} - payload with <chars & symbols>")

        elapsed = time.monotonic() - start_time

        # Verification 1: Internal list strictly capped at MAX_BLOCK_COUNT (5000)
        assert len(console._entries) == 5000, f"Expected 5000 entries, got {len(console._entries)}"

        # Verification 2: QPlainTextEdit block count capped <= 5000
        assert console.text_edit.blockCount() <= 5000

        # Verification 3: Buffer contains the latest logs, not the first
        assert "entry #19999" in console._entries[-1].message
        assert "entry #0" not in [e.message for e in console._entries[:100]]

        # Verification 4: Throughput check (20k entries within 5 seconds)
        assert elapsed < 5.0, f"20,000 log additions took {elapsed:.2f}s"

    def test_multithreaded_signal_log_burst(self, qtbot):
        """Pushes 10,000 log lines across 4 concurrent threads via Qt QueuedConnection."""
        console = LogConsole()
        qtbot.addWidget(console)

        class SignalEmitter(QObject):
            log_sig = Signal(str, str)

        emitter = SignalEmitter()
        emitter.log_sig.connect(console.append_log, Qt.ConnectionType.QueuedConnection)

        num_threads = 4
        logs_per_thread = 2500

        def worker_task(thread_id: int):
            for i in range(logs_per_thread):
                emitter.log_sig.emit("INFO", f"[T{thread_id}] Log #{i}")

        threads = [
            threading.Thread(target=worker_task, args=(t,), daemon=True)
            for t in range(num_threads)
        ]

        t0 = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        # Allow Qt event loop to process queued signals
        while len(console._entries) < 5000 and time.monotonic() - t0 < 6.0:
            QApplication.processEvents()
            time.sleep(0.01)

        QApplication.processEvents()

        assert len(console._entries) == 5000
        assert console.text_edit.blockCount() <= 5000

    def test_memory_stability_tracemalloc(self, qtbot):
        """Verifies memory usage does not leak after pushing 20,000 logs and clearing."""
        console = LogConsole()
        qtbot.addWidget(console)

        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        # Push 20,000 lines
        for i in range(20000):
            console.append_log("INFO", f"Message payload #{i}")

        # Clear logs
        console.clear_logs()
        QApplication.processEvents()

        snapshot_after = tracemalloc.take_snapshot()
        top_stats = snapshot_after.compare_to(snapshot_before, 'lineno')
        total_diff_kb = sum(stat.size_diff for stat in top_stats) / 1024

        tracemalloc.stop()

        # Memory diff after clear should be negligible (< 15MB)
        assert total_diff_kb < 15360, f"Potential memory leak: diff is {total_diff_kb:.2f} KB"
        assert len(console._entries) == 0
        assert console.text_edit.toPlainText() == ""

    def test_filter_and_clear_under_heavy_load(self, qtbot):
        """Verifies switching filters with 5,000 active entries does not hang or crash."""
        console = LogConsole()
        qtbot.addWidget(console)

        for i in range(6000):
            lvl = "ERROR" if i % 3 == 0 else "INFO"
            console.append_log(lvl, f"Item {i} of type {lvl}")

        assert len(console._entries) == 5000

        # Switch filter to ERROR
        console.filter_combo.setCurrentText("ERROR")
        text_err = console.text_edit.toPlainText()
        assert "Item" in text_err
        assert "INFO" not in text_err

        # Switch filter to WARNING (0 matches in buffer)
        console.filter_combo.setCurrentText("WARNING")
        assert console.text_edit.toPlainText() == ""

        # Switch back to All Levels
        console.filter_combo.setCurrentText("All Levels")
        assert len(console.text_edit.toPlainText().splitlines()) >= 4900

        # Copy to clipboard
        console.copy_to_clipboard()
        clip = QApplication.clipboard().text()
        assert "Item" in clip

        # Clear logs
        console.clear_logs()
        assert len(console._entries) == 0
        assert console.text_edit.toPlainText() == ""


# =============================================================================
# 3. EXECUTION LIFECYCLE STRESS
# =============================================================================

class TestExecutionLifecycleStress:
    """Stress tests covering rapid state cycling, abort latency, and concurrency."""

    def test_rapid_start_pause_resume_stop_cycles(self, qtbot):
        """Executes 5 rapid Start -> Pause -> Resume -> Stop cycles consecutively."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        scenario = ScenarioModel(
            name="Lifecycle Stress",
            loop_count=100,
            actions=[
                ActionBlock(
                    id="act-s1",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=50),
                )
            ],
        )

        win = MainWindow(
            scenario=scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        for cycle in range(5):
            win.start_macro()
            assert win.engine is not None
            assert win.engine.is_running() or win.engine.is_idle()

            # Rapid pause
            win.pause_macro()
            time.sleep(0.01)

            # Rapid resume
            win.resume_macro()
            time.sleep(0.01)

            # Rapid stop
            win.stop_macro()
            win.engine.wait_for_completion(timeout=2.0)
            assert win.engine.cancel_event.is_set()

            QApplication.processEvents()

        win.mark_dirty(False)

    def test_emergency_stop_abort_under_one_second(self, qtbot):
        """Verifies that an Emergency Stop aborts a running macro in < 1.0 second."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        # Scenario with 10-second wait
        scenario = ScenarioModel(
            name="Long Wait Scenario",
            loop_count=1,
            actions=[
                ActionBlock(
                    id="act-long",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=10000),
                )
            ],
        )

        win = MainWindow(
            scenario=scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        # Start macro
        win.start_macro()
        assert win.engine is not None

        # Give it 50ms to enter wait state
        time.sleep(0.05)

        stop_start_time = time.monotonic()
        win.stop_macro()
        completed = win.engine.wait_for_completion(timeout=2.0)
        abort_duration = time.monotonic() - stop_start_time

        assert completed is True, "Engine did not complete within timeout"
        assert abort_duration < 1.0, f"Emergency stop exceeded 1s target: took {abort_duration:.3f}s"
        win.mark_dirty(False)

    def test_consecutive_emergency_stops_latency(self, qtbot):
        """Runs 5 rapid Start -> Emergency Stop cycles, verifying each aborts in <1s."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        scenario = ScenarioModel(
            name="Abort Latency Test",
            loop_count=5,
            actions=[
                ActionBlock(
                    id="act-w",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=5000),
                )
            ],
        )

        win = MainWindow(
            scenario=scenario,
            input_driver=mock_input,
            screen_driver=mock_screen,
        )
        qtbot.addWidget(win)

        latencies = []
        for _ in range(5):
            win.start_macro()
            time.sleep(0.02)

            t0 = time.monotonic()
            win.stop_macro()
            win.engine.wait_for_completion(timeout=2.0)
            latency = time.monotonic() - t0
            latencies.append(latency)

        assert all(lat < 1.0 for lat in latencies), f"Aborts took too long: {latencies}"
        avg_lat = sum(latencies) / len(latencies)
        assert avg_lat < 0.2, f"Average abort latency higher than expected: {avg_lat:.3f}s"
        win.mark_dirty(False)

    def test_empty_scenario_execution_guard(self, qtbot):
        """Verifies that attempting to start an empty scenario is blocked gracefully."""
        win = MainWindow()
        qtbot.addWidget(win)

        with patch.object(QMessageBox, "warning") as mock_warn:
            win.start_macro()
            assert mock_warn.called
            assert win.engine is None

        win.mark_dirty(False)

    def test_window_close_cleans_up_running_engine(self, qtbot):
        """Tests that closing the window stops the running engine and cleans up threads."""
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        scenario = ScenarioModel(
            name="Close Clean Scenario",
            loop_count=10,
            actions=[
                ActionBlock(
                    id="act-close",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=2000),
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
        time.sleep(0.05)
        assert win.engine.is_running()

        with patch.object(win, "confirm_discard_changes", return_value=True):
            win.close()

        # Remediation Verified: _cleanup_runner stops hotkey_listener and stops engine cleanly
        assert win.hotkey_listener is None
        assert win.engine.is_running() is False
        assert win.engine.cancel_event.is_set() is True
        win.mark_dirty(False)

    def test_reentrancy_vulnerability_observation(self, qtbot):
        """Verifies that calling start_macro while execution is underway is safely blocked.

        Remediation: MainWindow.start_macro guards against calling start_macro
        while an existing engine is already actively running or paused. If invoked twice (e.g. via F5),
        the second call is blocked and self.engine is not replaced.
        """
        mock_input = MockInputDriver()
        mock_screen = MockScreenDriver()

        scenario = ScenarioModel(
            name="Reentrancy Observation Scenario",
            loop_count=10,
            actions=[
                ActionBlock(
                    id="act-wait",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=1000),
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
        first_engine = win.engine
        assert first_engine is not None
        qtbot.waitUntil(lambda: first_engine.is_running(), timeout=1000)
        assert first_engine.is_running()

        # Empirical challenge: trigger start_macro again without stopping
        win.start_macro()
        second_engine = win.engine

        # Remediation Verified: second engine did not replace first_engine; re-entrancy blocked
        assert second_engine is first_engine
        assert first_engine.is_running()

        # Clean up engine
        win.stop_macro()
        first_engine.stop()
        first_engine.wait_for_completion(timeout=2.0)
        win.mark_dirty(False)


# =============================================================================
# 4. DIRTY TRACKING & PERSISTENCE
# =============================================================================

class TestDirtyTrackingAndPersistence:
    """Empirical verification of dirty indicator '*' and persistence roundtrips."""

    def test_dirty_flag_and_window_title_updates(self, qtbot):
        """Verifies '*' appears in window title when dirty and disappears when clean."""
        win = MainWindow()
        qtbot.addWidget(win)

        # Initially clean
        assert win._is_dirty is False
        assert "*" not in win.windowTitle()

        # 1. Mark dirty
        win.mark_dirty(True)
        assert win._is_dirty is True
        assert "*" in win.windowTitle()

        # 2. Mark clean
        win.mark_dirty(False)
        assert win._is_dirty is False
        assert "*" not in win.windowTitle()

        # 3. Trigger via loop count change
        win._on_dashboard_loop_count_changed(5)
        assert win._is_dirty is True
        assert "*" in win.windowTitle()

        # 4. Trigger via coordinate picker capture
        win.mark_dirty(False)
        win.on_coordinate_picked(400, 300)
        assert win._is_dirty is True
        assert "*" in win.windowTitle()

        win.mark_dirty(False)

    def test_confirm_discard_changes_branches(self, tmp_path: Path, qtbot):
        """Tests Save, Discard, and Cancel responses in confirmation dialog."""
        win = MainWindow()
        qtbot.addWidget(win)
        win.current_file_path = tmp_path / "temp_scenario.json"
        win.mark_dirty(True)

        # Branch 1: User selects Save
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
            res = win.confirm_discard_changes()
            assert res is True
            assert win._is_dirty is False
            assert win.current_file_path.exists()

        # Branch 2: User selects Discard
        win.mark_dirty(True)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
            res = win.confirm_discard_changes()
            assert res is True

        # Branch 3: User selects Cancel
        win.mark_dirty(True)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            res = win.confirm_discard_changes()
            assert res is False

        # Ensure clean state so qtbot teardown doesn't pop up dialog
        win.mark_dirty(False)

    def test_persistence_roundtrip_all_action_types(self, tmp_path: Path, qtbot):
        """Tests saving and reloading a complex scenario with all 4 action types."""
        save_file = tmp_path / "comprehensive_scenario.json"

        # Build comprehensive scenario
        actions = [
            ActionBlock(
                id="act-click-1",
                type=ActionType.MOUSE_CLICK,
                params=MouseClickParams(x=1024, y=768, button="left", clicks=2, interval_ms=50),
                description="Double click target",
            ),
            ActionBlock(
                id="act-key-1",
                type=ActionType.KEY_INPUT,
                params=KeyInputParams(keys=["ctrl", "c"], action="press"),
                description="Copy combo",
            ),
            ActionBlock(
                id="act-wait-1",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=500),
                description="Wait half second",
            ),
            ActionBlock(
                id="act-img-1",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(
                    template_path="templates/btn.png",
                    mode="appear",
                    confidence=0.85,
                    timeout_seconds=3.0,
                    roi=(100, 100, 200, 200),
                    timeout_action="skip",
                ),
                description="Wait for button appear",
            ),
        ]

        original_scenario = ScenarioModel(
            name="Comprehensive Roundtrip Scenario",
            version="1.0.0",
            loop_count=3,
            actions=actions,
        )

        win1 = MainWindow(scenario=original_scenario)
        qtbot.addWidget(win1)
        win1.current_file_path = save_file
        win1.mark_dirty(True)

        # Save
        save_success = win1.save_scenario()
        assert save_success is True
        assert save_file.exists()
        assert win1._is_dirty is False

        # Open in clean window
        win2 = MainWindow()
        qtbot.addWidget(win2)

        with patch.object(win2, "confirm_discard_changes", return_value=True):
            open_success = win2.open_scenario(str(save_file))

        assert open_success is True
        loaded = win2.scenario

        # Parameter fidelity verification
        assert loaded.name == original_scenario.name
        assert loaded.loop_count == 3
        assert len(loaded.actions) == 4

        # Action 0: MOUSE_CLICK
        a0 = loaded.actions[0]
        assert a0.type == ActionType.MOUSE_CLICK
        assert a0.params.x == 1024
        assert a0.params.y == 768
        assert a0.params.button == "left"
        assert a0.params.clicks == 2
        assert a0.params.interval_ms == 50

        # Action 1: KEY_INPUT
        a1 = loaded.actions[1]
        assert a1.type == ActionType.KEY_INPUT
        assert a1.params.keys == ["ctrl", "c"]
        assert a1.params.action == "press"

        # Action 2: FIXED_WAIT
        a2 = loaded.actions[2]
        assert a2.type == ActionType.FIXED_WAIT
        assert a2.params.duration_ms == 500

        # Action 3: IMAGE_WAIT
        a3 = loaded.actions[3]
        assert a3.type == ActionType.IMAGE_WAIT
        assert a3.params.template_path == "templates/btn.png"
        assert a3.params.mode == "appear"
        assert a3.params.confidence == 0.85
        assert a3.params.timeout_seconds == 3.0
        assert a3.params.roi == (100, 100, 200, 200)
        assert a3.params.timeout_action == "skip"

        win1.mark_dirty(False)
        win2.mark_dirty(False)
