"""Tier 2: Boundary & Corner Cases Test Suite for ActionMacro Automation.

Tests extreme parameters, zero/negative durations, missing assets, malformed files,
large templates, zero-variance flat images, out-of-bounds coordinates, and abort race conditions.
Contains 11 test cases (requirement: >= 10 tests).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from tests.mocks.contract import (
    ActionBlock,
    ActionType,
    EngineState,
    ExecutionEngine,
    ImageWaitParams,
    ScenarioModel,
    ScenarioPersistence,
    WaitSystem,
)
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


class TestTier2Boundaries:
    """Verifies edge cases, boundary parameters, and fault-tolerance."""

    def test_boundary_zero_and_sub_millisecond_wait(self):
        """T2.1: Fixed wait with duration=0ms returns True in <= 10ms without blocking."""
        t0 = time.perf_counter()
        result = WaitSystem.wait(0.0)
        elapsed = (time.perf_counter() - t0) * 1000

        assert result is True
        assert elapsed <= 10.0

    def test_boundary_negative_wait_duration(self):
        """T2.2: Negative wait duration (-500ms) clamps to 0 and returns True safely."""
        t0 = time.perf_counter()
        result = WaitSystem.wait(-0.5)
        elapsed = (time.perf_counter() - t0) * 1000

        assert result is True
        assert elapsed <= 10.0

    def test_boundary_missing_template_file(self, mock_screen: MockScreenDriver, tmp_path: Path):
        """T2.3: Non-existent template file path raises FileNotFoundError immediately."""
        missing_path = tmp_path / "non_existent_button.png"
        params = ImageWaitParams(
            template_path=str(missing_path),
            timeout_seconds=1.0,
        )

        with pytest.raises(FileNotFoundError, match="not found"):
            WaitSystem.wait_image(params, mock_screen)

    def test_boundary_out_of_bounds_screen_coordinates(self, mock_screen: MockScreenDriver):
        """T2.4: Querying pixel or ROI exceeding virtual screen dimensions raises error."""
        # Query beyond 1920x1080 canvas
        with pytest.raises(IndexError):
            mock_screen.get_pixel(99999, -5000)

        # ROI extending past screen width
        with pytest.raises(ValueError, match="exceeds screen bounds"):
            mock_screen.capture(roi=(1800, 100, 300, 200))

    def test_boundary_empty_scenario_execution(self, mock_input: MockInputDriver):
        """T2.5: Executing a scenario with actions=[] completes cleanly with success=True."""
        scenario = ScenarioModel(name="Empty Scenario", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        success = engine.run()
        assert success is True
        assert engine.state == EngineState.IDLE
        assert len(mock_input.clicks) == 0

    def test_boundary_immediate_abort_at_t0(self, mock_input: MockInputDriver):
        """T2.6: Pre-setting cancel event before run() completes safely without hang or deadlock."""
        scenario = ScenarioModel(
            name="Abort at t0",
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 100, "y": 100}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        engine.cancel_event.set()  # Pre-aborted

        success = engine.run()
        assert success is False
        assert engine.state == EngineState.STOPPED
        assert len(mock_input.clicks) == 0

    def test_boundary_malformed_json_persistence(self, tmp_path: Path):
        """T2.7: Loading a malformed JSON file (syntax error / truncated) raises ValueError."""
        corrupt_file = tmp_path / "corrupt.json"
        corrupt_file.write_text('{"schema_version": "1.0.0", "actions": [', encoding="utf-8")

        with pytest.raises(ValueError, match="Malformed scenario JSON"):
            ScenarioPersistence.load(corrupt_file)

    def test_boundary_template_larger_than_roi_or_screen(
        self,
        mock_screen: MockScreenDriver,
        tmp_path: Path,
    ):
        """T2.8: Template image dimensions larger than ROI or screen raise ValueError."""
        # Create a large 200x200 patch
        large_patch = np.zeros((200, 200, 3), dtype=np.uint8)
        large_file = tmp_path / "large_template.png"
        MockScreenDriver.save_template_file(large_patch, large_file)

        # Set ROI to 100x100 (smaller than template)
        params = ImageWaitParams(
            template_path=str(large_file),
            roi=(0, 0, 100, 100),
            timeout_seconds=0.5,
        )

        with pytest.raises(ValueError, match="exceed"):
            WaitSystem.wait_image(params, mock_screen)

    def test_boundary_flat_zero_variance_template(
        self,
        mock_screen: MockScreenDriver,
        tmp_path: Path,
    ):
        """T2.9: Flat zero-variance template (solid color) is detected and handled via fallback."""
        flat_patch = MockScreenDriver.create_flat_template(width=40, height=40, fill_value=200)
        flat_file = tmp_path / "flat_template.png"
        MockScreenDriver.save_template_file(flat_patch, flat_file)

        # Inject flat patch into screen canvas at (200, 200)
        mock_screen.inject_patch(flat_patch, x=200, y=200)

        params = ImageWaitParams(
            template_path=str(flat_file),
            mode="appear",
            confidence=0.8,
            timeout_seconds=1.0,
        )

        # Should match without OpenCV division-by-zero crash
        matched, coords = WaitSystem.wait_image(params, mock_screen)
        assert matched is True
        assert coords is not None

    def test_boundary_unsupported_schema_version(self, tmp_path: Path):
        """T2.10: Scenario with unsupported future major schema version raises ValueError."""
        future_file = tmp_path / "future_v999.json"
        data = {
            "schema_version": "999.0.0",
            "metadata": {"name": "Future Scenario"},
            "settings": {"loop_count": 1},
            "actions": [],
        }
        future_file.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(ValueError, match="Unsupported schema version"):
            ScenarioPersistence.load(future_file)

    def test_boundary_rapid_stop_toggle(self, mock_input: MockInputDriver):
        """T2.11: Rapid toggling between run and stop in multiple threads does not deadlock."""
        scenario = ScenarioModel(
            name="Rapid Stop Scenario",
            loop_count=0,
            actions=[
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 10}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)

        thread = threading.Thread(target=engine.run)
        thread.start()

        # Rapidly invoke stop
        time.sleep(0.02)
        engine.stop()
        thread.join(timeout=1.0)

        assert not thread.is_alive()
        assert engine.state == EngineState.STOPPED
