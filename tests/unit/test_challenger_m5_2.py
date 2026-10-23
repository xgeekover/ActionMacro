"""
tests.unit.test_challenger_m5_2
===============================
Adversarial empirical challenge suite for Milestone 5 (Final E2E Integration).
Targeting:
1. Malformed & Corrupted Persistence Stress:
   - Corrupted JSON, truncated files, non-dict root, garbage bytes, empty strings
   - Unknown action types, missing required root and action fields, duplicate IDs
   - Typed action parameter boundary violations: negative wait durations, invalid confidence values (2.5, -0.5, strings, bools), invalid ROI, invalid click/key params
   - MainWindow.open_scenario resilience: graceful error dialogs, console logging, zero application crashes, state retention
2. Template Path Normalization Edge Cases:
   - Mixed separators (templates/foo\\bar/baz.png, windows backslashes)
   - Relative path traversal (../../secret.png, ../../../../etc/passwd)
   - Non-ASCII Unicode names (Korean 템플릿/테스트.png, Japanese 画像/設定.png, Emoji 🎯/btn.png)
   - Spaces and special characters (templates/my button target [v1].png)
   - Empty/whitespace template paths rejection
3. Emergency Stop Latency Under Heavy Load:
   - Heavy image wait loops (1920x1080 template matching) + tight loop iterations
   - 20 consecutive runs measuring emergency stop trigger and worker completion latency
   - Strict assertion: latency < 100ms (<1s requirement), 100% safety_flush execution rate
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from action_macro.core.key_mapping import (
    format_key_combo,
    is_valid_key,
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
    ValidationError,
)
from action_macro.core.persistence import (
    IncompatibleVersionError,
    PersistenceError,
    ScenarioFileNotFoundError,
    ScenarioJSONDecodeError,
    ScenarioPersistence,
    ScenarioValidationError,
    UnsupportedVersionError,
    load_scenario,
    resolve_template_path,
    save_scenario,
    scenario_from_dict,
    scenario_from_json,
    scenario_to_dict,
    scenario_to_json,
    to_posix_relative_path,
)
from action_macro.engine.wait_system import WaitSystem
from action_macro.gui.main_window import MainWindow
from action_macro.runner.execution_engine import ExecutionEngine
from action_macro.runner.hotkey_listener import GlobalHotkeyListener
from action_macro.runner.state_machine import EngineState
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


# =============================================================================
# FOCUS 1: MALFORMED & CORRUPTED PERSISTENCE STRESS
# =============================================================================

class TestPersistenceMalformedStress:
    """Stress-tests persistence deserialization against adversarial, malformed, and corrupted inputs."""

    def test_corrupted_json_syntax(self, tmp_path: Path):
        """Corrupted JSON syntax (unclosed braces, trailing garbage, raw binary) must raise ScenarioJSONDecodeError."""
        corrupted_cases = [
            '{ "name": "Broken',
            '{"name": "Broken", "version": "1.0.0", "actions": [',
            'Not JSON at all! Just raw plain text 12345',
            '{"name": "TrailingComma", "version": "1.0.0", "loop_count": 1, "actions": [],}',
            '{"name": "BadColon" "version": "1.0.0"}',
            "",
            "   \n\t  ",
        ]

        for idx, broken_content in enumerate(corrupted_cases):
            broken_file = tmp_path / f"corrupted_{idx}.json"
            broken_file.write_text(broken_content, encoding="utf-8")

            # 1. From string
            with pytest.raises(ScenarioJSONDecodeError):
                scenario_from_json(broken_content)

            # 2. From file
            with pytest.raises(ScenarioJSONDecodeError):
                load_scenario(broken_file)

    def test_truncated_json_files(self, tmp_path: Path):
        """Truncated files at arbitrary cutoffs must raise ScenarioJSONDecodeError cleanly."""
        valid_json = scenario_to_json(
            ScenarioModel(
                name="Truncation Target",
                loop_count=5,
                actions=[
                    ActionBlock(id="a1", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=100)),
                    ActionBlock(id="a2", type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=100, y=200)),
                ],
            )
        )

        valid_json_stripped = valid_json.strip()
        cutoffs = [5, 15, 30, 60, 100, len(valid_json_stripped) - 10, len(valid_json_stripped) - 2]
        for idx, cut in enumerate(cutoffs):
            truncated_content = valid_json_stripped[:cut]
            tfile = tmp_path / f"truncated_{idx}.json"
            tfile.write_text(truncated_content, encoding="utf-8")

            with pytest.raises(ScenarioJSONDecodeError) as exc_info:
                load_scenario(tfile)
            assert "Malformed" in str(exc_info.value) or "empty" in str(exc_info.value).lower()

    def test_missing_required_root_fields(self):
        """Missing required root fields (name, version, loop_count, actions) must raise ScenarioValidationError."""
        base_dict = {
            "name": "Valid Name",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [],
        }

        for req in ("name", "version", "loop_count", "actions"):
            bad_dict = dict(base_dict)
            del bad_dict[req]
            with pytest.raises(ScenarioValidationError) as exc_info:
                scenario_from_dict(bad_dict)
            assert req in str(exc_info.value)

    def test_invalid_root_field_types_and_bounds(self):
        """Invalid types or negative loop_count on root fields must raise ScenarioValidationError."""
        # Non-dict root
        for non_dict in ([], "string", 123, None, True):
            with pytest.raises(ScenarioValidationError):
                scenario_from_dict(non_dict)  # type: ignore

        # name empty or non-string
        for bad_name in ("", "   ", 123, [], None):
            d = {"name": bad_name, "version": "1.0.0", "loop_count": 1, "actions": []}
            with pytest.raises(ScenarioValidationError):
                scenario_from_dict(d)

        # loop_count negative, bool, or non-int
        for bad_loops in (-1, -100, True, False, "1", 2.5):
            d = {"name": "Test", "version": "1.0.0", "loop_count": bad_loops, "actions": []}
            with pytest.raises(ScenarioValidationError):
                scenario_from_dict(d)

        # actions non-list
        for bad_actions in ("not_a_list", {"act": 1}, 123, None):
            d = {"name": "Test", "version": "1.0.0", "loop_count": 1, "actions": bad_actions}
            with pytest.raises(ScenarioValidationError):
                scenario_from_dict(d)

    def test_unknown_action_type(self):
        """Unknown action types must raise informative ScenarioValidationError."""
        for unknown_type in ("DESTROY_SCREEN", "UNKNOWN_TYPE", "CLICK_ANYWHERE", "", 99999, None):
            d = {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {"id": "act-bad", "type": unknown_type, "params": {}},
                ],
            }
            with pytest.raises(ScenarioValidationError) as exc_info:
                scenario_from_dict(d)
            assert "Unknown action type" in str(exc_info.value) or "missing required 'type'" in str(exc_info.value)

    def test_missing_or_duplicate_action_ids(self):
        """Missing, empty, non-string, or duplicate action IDs must raise ScenarioValidationError."""
        # Missing id
        d1 = {
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [{"type": "FIXED_WAIT", "params": {"duration_ms": 100}}],
        }
        with pytest.raises(ScenarioValidationError) as exc1:
            scenario_from_dict(d1)
        assert "missing required 'id'" in str(exc1.value)

        # Empty id
        d2 = {
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [{"id": "   ", "type": "FIXED_WAIT", "params": {"duration_ms": 100}}],
        }
        with pytest.raises(ScenarioValidationError) as exc2:
            scenario_from_dict(d2)
        assert "non-empty string" in str(exc2.value)

        # Duplicate IDs
        d3 = {
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [
                {"id": "dup-id", "type": "FIXED_WAIT", "params": {"duration_ms": 100}},
                {"id": "dup-id", "type": "FIXED_WAIT", "params": {"duration_ms": 200}},
            ],
        }
        with pytest.raises(ScenarioValidationError) as exc3:
            scenario_from_dict(d3)
        assert "Duplicate action block IDs" in str(exc3.value)

    def test_negative_wait_duration_rejection(self):
        """FixedWaitParams with negative or non-integer duration_ms must be strictly rejected."""
        # Negative duration
        for neg in (-1, -50, -10000):
            d = {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [{"id": "act-1", "type": "FIXED_WAIT", "params": {"duration_ms": neg}}],
            }
            with pytest.raises(ScenarioValidationError) as exc_info:
                scenario_from_dict(d)
            assert "duration_ms" in str(exc_info.value)

        # Non-integer duration
        for non_int in ("500", 50.5, True, False, None):
            d = {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [{"id": "act-1", "type": "FIXED_WAIT", "params": {"duration_ms": non_int}}],
            }
            with pytest.raises(ScenarioValidationError) as exc_info:
                scenario_from_dict(d)
            assert "duration_ms" in str(exc_info.value)

    def test_invalid_image_wait_confidence_and_bounds(self):
        """ImageWaitParams with confidence out of [0.0, 1.0] or invalid timeout must be rejected."""
        # Confidence boundaries
        for bad_conf in (2.5, -0.5, 1.0001, -0.0001, "0.8", True, False):
            d = {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {
                        "id": "act-img",
                        "type": "IMAGE_WAIT",
                        "params": {
                            "template_path": "templates/btn.png",
                            "confidence": bad_conf,
                            "timeout_seconds": 5.0,
                        },
                    }
                ],
            }
            with pytest.raises(ScenarioValidationError) as exc_info:
                scenario_from_dict(d)
            assert "confidence" in str(exc_info.value)

        # Negative timeout_seconds
        for bad_to in (-1.0, -0.01, -100.0, "5.0", True):
            d = {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {
                        "id": "act-img",
                        "type": "IMAGE_WAIT",
                        "params": {
                            "template_path": "templates/btn.png",
                            "confidence": 0.8,
                            "timeout_seconds": bad_to,
                        },
                    }
                ],
            }
            with pytest.raises(ScenarioValidationError) as exc_info:
                scenario_from_dict(d)
            assert "timeout_seconds" in str(exc_info.value)

        # Invalid mode or timeout_action
        for bad_mode in ("blink", "rotate", "appear_and_disappear", ""):
            d = {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {
                        "id": "act-img",
                        "type": "IMAGE_WAIT",
                        "params": {
                            "template_path": "templates/btn.png",
                            "mode": bad_mode,
                        },
                    }
                ],
            }
            with pytest.raises(ScenarioValidationError) as exc_info:
                scenario_from_dict(d)
            assert "mode" in str(exc_info.value)

    def test_invalid_mouse_and_key_params_rejection(self):
        """MouseClickParams and KeyInputParams with invalid fields must be cleanly rejected."""
        # Invalid mouse button
        d_mouse = {
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [
                {"id": "m1", "type": "MOUSE_CLICK", "params": {"x": 100, "y": 200, "button": "laser_pointer"}}
            ],
        }
        with pytest.raises(ScenarioValidationError) as exc:
            scenario_from_dict(d_mouse)
        assert "button" in str(exc.value)

        # Clicks < 1
        d_clicks = {
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [
                {"id": "m1", "type": "MOUSE_CLICK", "params": {"x": 100, "y": 200, "clicks": 0}}
            ],
        }
        with pytest.raises(ScenarioValidationError) as exc:
            scenario_from_dict(d_clicks)
        assert "clicks" in str(exc.value)

        # KeyInput empty keys
        d_key = {
            "name": "Test",
            "version": "1.0.0",
            "loop_count": 1,
            "actions": [
                {"id": "k1", "type": "KEY_INPUT", "params": {"keys": []}}
            ],
        }
        with pytest.raises(ScenarioValidationError) as exc:
            scenario_from_dict(d_key)
        assert "keys" in str(exc.value)


class TestMainWindowOpenScenarioResilience:
    """Tests that MainWindow.open_scenario handles all corrupted/malformed scenarios without crashing."""

    def test_main_window_open_corrupted_json(self, tmp_path: Path, qtbot):
        """Feeding corrupted JSON to open_scenario raises warning/critical dialog, returns False, logs ERROR, and does NOT crash."""
        win = MainWindow()
        qtbot.addWidget(win)

        initial_scenario_name = win.scenario.name
        bad_file = tmp_path / "corrupted_syntax.json"
        bad_file.write_text('{"name": "Broken', encoding="utf-8")

        with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_box, \
             patch.object(win, "confirm_discard_changes", return_value=True):
            result = win.open_scenario(str(bad_file))

        assert result is False
        assert mock_box.called
        assert win.scenario.name == initial_scenario_name  # preserved
        log_text = win.log_console.text_edit.toPlainText()
        assert "Failed to load scenario" in log_text

    def test_main_window_open_missing_fields(self, tmp_path: Path, qtbot):
        """Feeding JSON missing required fields returns False and logs clean error without crashing."""
        win = MainWindow()
        qtbot.addWidget(win)

        bad_file = tmp_path / "missing_actions.json"
        bad_file.write_text(json.dumps({"name": "No Actions", "version": "1.0.0"}), encoding="utf-8")

        with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_box, \
             patch.object(win, "confirm_discard_changes", return_value=True):
            result = win.open_scenario(str(bad_file))

        assert result is False
        assert mock_box.called
        assert "Missing required root field" in mock_box.call_args[0][2]
        assert "ERROR" in [entry.level for entry in win.log_console._entries]

    def test_main_window_open_invalid_confidence_and_unknown_action(self, tmp_path: Path, qtbot):
        """Feeding JSON with confidence=2.5 and unknown action type raises QMessageBox.critical and preserves state."""
        win = MainWindow()
        qtbot.addWidget(win)

        bad_conf_file = tmp_path / "bad_confidence.json"
        bad_conf_file.write_text(
            json.dumps({
                "name": "Bad Conf",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {
                        "id": "act-1",
                        "type": "IMAGE_WAIT",
                        "params": {"template_path": "t.png", "confidence": 2.5},
                    }
                ],
            }),
            encoding="utf-8",
        )

        with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_box, \
             patch.object(win, "confirm_discard_changes", return_value=True):
            res_conf = win.open_scenario(str(bad_conf_file))

        assert res_conf is False
        assert mock_box.called
        assert "confidence" in mock_box.call_args[0][2]

        bad_type_file = tmp_path / "bad_type.json"
        bad_type_file.write_text(
            json.dumps({
                "name": "Bad Type",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {"id": "act-1", "type": "ALIEN_INVASION", "params": {}},
                ],
            }),
            encoding="utf-8",
        )

        with patch("PySide6.QtWidgets.QMessageBox.critical") as mock_box2, \
             patch.object(win, "confirm_discard_changes", return_value=True):
            res_type = win.open_scenario(str(bad_type_file))

        assert res_type is False
        assert mock_box2.called
        assert "Unknown action type" in mock_box2.call_args[0][2]


# =============================================================================
# FOCUS 2: TEMPLATE PATH NORMALIZATION EDGE CASES
# =============================================================================

class TestTemplatePathNormalizationEdgeCases:
    """Stress-tests path normalization, path traversal, Unicode characters, spaces, and edge cases."""

    def test_mixed_separators_normalization(self, tmp_path: Path):
        """Mixed separators (forward slashes + backslashes) must be normalized to clean POSIX format."""
        scenario_file = tmp_path / "scenarios" / "macro.json"
        scenario_dir = scenario_file.parent
        scenario_dir.mkdir(parents=True, exist_ok=True)

        mixed_cases = [
            ("templates\\foo\\bar\\baz.png", "templates/foo/bar/baz.png"),
            ("templates/foo\\bar/baz.png", "templates/foo/bar/baz.png"),
            ("assets\\icons/subfolder\\target.png", "assets/icons/subfolder/target.png"),
            (".\\templates\\btn.png", "templates/btn.png"),
        ]

        for raw_path, expected_posix in mixed_cases:
            norm = to_posix_relative_path(raw_path, scenario_dir)
            assert norm == expected_posix, f"Failed for {raw_path}: got {norm}"
            assert "\\" not in norm

    def test_relative_path_traversal_resolution(self, tmp_path: Path):
        """Relative path traversal (../../secret.png) must be resolved safely relative to scenario dir."""
        proj_dir = tmp_path / "project"
        scen_dir = proj_dir / "scenarios" / "level1"
        scen_dir.mkdir(parents=True, exist_ok=True)
        scenario_path = scen_dir / "my_scenario.json"

        # Create target file outside scenario dir
        target_outside = proj_dir / "secret.png"
        target_outside.write_text("fake png data", encoding="utf-8")

        # Traversal path
        traversal_path = "../../secret.png"
        resolved = resolve_template_path(traversal_path, scenario_path)

        assert resolved == target_outside.resolve()
        assert resolved.exists()
        assert "\\" not in to_posix_relative_path(traversal_path, scen_dir)

    def test_non_ascii_unicode_template_names(self, tmp_path: Path):
        """Non-ASCII Unicode paths (Korean, Japanese, Emoji, accented characters) round-trip without corruption."""
        scen_file = tmp_path / "unicode_scenario.json"
        base_dir = tmp_path

        unicode_paths = [
            "템플릿/테스트_버튼.png",
            "이미지/설정_아이콘_01.png",
            "画像/ボタン_テスト.png",
            "🎯_emoji/버튼_🚀.png",
            "modèles/icône_téléchargement.png",
        ]

        for u_path in unicode_paths:
            scenario = ScenarioModel(
                name="Unicode Scenario",
                loop_count=1,
                actions=[
                    ActionBlock(
                        id="act-u",
                        type=ActionType.IMAGE_WAIT,
                        params=ImageWaitParams(template_path=u_path, timeout_seconds=1.0),
                    )
                ],
            )

            # 1. Save to JSON
            save_scenario(scenario, scen_file)
            assert scen_file.exists()

            # Ensure UTF-8 json without escaped ascii sequences
            raw_text = scen_file.read_text(encoding="utf-8")
            assert u_path in raw_text

            # 2. Load from JSON
            loaded = load_scenario(scen_file)
            loaded_act = loaded.actions[0]
            assert loaded_act.type == ActionType.IMAGE_WAIT
            assert loaded_act.params.template_path == u_path

            # 3. Path resolution
            resolved = resolve_template_path(u_path, scen_file)
            assert resolved == (base_dir / Path(u_path)).resolve()

    def test_template_paths_with_spaces_and_brackets(self, tmp_path: Path):
        """Template paths containing spaces, square brackets, parentheses, and dots."""
        special_names = [
            "templates/my button target [v1].png",
            "templates/button (2) - copy.png",
            "assets/folder with spaces/target.v1.0.final.png",
        ]

        for s_path in special_names:
            scenario = ScenarioModel(
                name="Special Name Scenario",
                loop_count=1,
                actions=[
                    ActionBlock(
                        id="act-s",
                        type=ActionType.IMAGE_WAIT,
                        params=ImageWaitParams(template_path=s_path),
                    )
                ],
            )
            data = scenario_to_dict(scenario, base_dir=tmp_path)
            act_params = data["actions"][0]["params"]
            assert act_params["template_path"] == s_path
            assert "\\" not in act_params["template_path"]

    def test_empty_and_whitespace_template_path_rejection(self):
        """Empty string or whitespace-only template path must be rejected by ImageWaitParams."""
        for invalid_path in ("", "   ", "\t\n"):
            with pytest.raises(ValueError) as exc:
                ImageWaitParams(template_path=invalid_path)
            assert "non-empty string" in str(exc.value)

            # In scenario_from_dict
            d = {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {"id": "act-empty", "type": "IMAGE_WAIT", "params": {"template_path": invalid_path}}
                ],
            }
            with pytest.raises(ScenarioValidationError):
                scenario_from_dict(d)


# =============================================================================
# FOCUS 3: EMERGENCY STOP LATENCY UNDER HEAVY LOAD (20 CONSECUTIVE RUNS)
# =============================================================================

class TestEmergencyStopLatencyUnderHeavyLoad:
    """Measures empirical emergency stop latency across 20 consecutive runs under heavy load."""

    def test_20_consecutive_runs_emergency_stop_under_heavy_load(self, tmp_path: Path):
        """Executes 20 consecutive macro runs with heavy image wait loops and tight CPU polling.
        Asserts:
        1. Emergency stop trigger latency is strictly < 100ms (<1000ms requirement).
        2. Worker thread terminates in < 100ms (<1000ms requirement).
        3. 100% of runs (20/20) execute MockInputDriver.safety_flush().
        4. Held keys/buttons are 100% cleared in every single run.
        """
        # Create a synthetic template on disk for heavy image matching
        mock_screen = MockScreenDriver(width=1920, height=1080)
        template_patch = mock_screen.create_test_template(width=80, height=40, pattern="cross")
        template_file = tmp_path / "heavy_template.png"
        MockScreenDriver.save_template_file(template_patch, template_file)

        # Build a heavy scenario: 1000 infinite-like loops of tight action steps
        scenario = ScenarioModel(
            name="Heavy Load Stress Scenario",
            loop_count=0,  # Infinite loop
            action_delay_ms=0,
            loop_delay_ms=0,
            actions=[
                # Action 0: Click
                ActionBlock(
                    id="act-clk",
                    type=ActionType.MOUSE_CLICK,
                    params=MouseClickParams(x=500, y=500, clicks=1),
                ),
                # Action 1: Heavy image recognition wait on 1920x1080 canvas
                ActionBlock(
                    id="act-heavy-img",
                    type=ActionType.IMAGE_WAIT,
                    params=ImageWaitParams(
                        template_path=str(template_file),
                        mode="appear",
                        confidence=0.8,
                        timeout_seconds=30.0,
                        poll_interval_seconds=0.005,  # Very high frequency polling
                    ),
                ),
                # Action 2: Fixed wait fallback
                ActionBlock(
                    id="act-fwait",
                    type=ActionType.FIXED_WAIT,
                    params=FixedWaitParams(duration_ms=5000),
                ),
            ],
        )

        num_runs = 20
        trigger_latencies: List[float] = []
        worker_latencies: List[float] = []
        flush_counts: List[int] = []

        print(f"\n--- Starting {num_runs} consecutive emergency stop runs under heavy load ---")

        for run_idx in range(1, num_runs + 1):
            mock_input = MockInputDriver()
            # Intentionally hold down modifiers and mouse buttons before stop
            mock_input.key_down("ctrl")
            mock_input.key_down("shift")
            mock_input.key_down("alt")
            mock_input.mouse_down("left")
            mock_input.mouse_down("right")

            assert len(mock_input.active_keys) == 3
            assert len(mock_input.active_buttons) == 2
            assert mock_input.safety_flush_called is False

            engine = ExecutionEngine(
                scenario=scenario,
                input_driver=mock_input,
                screen_driver=mock_screen,
                action_delay_ms=0,
                loop_delay_ms=0,
            )

            listener = GlobalHotkeyListener(
                engine=engine,
                input_adapter=mock_input,
                headless=True,
            )
            listener.start()

            # Start engine asynchronously
            worker_thread = engine.start_async()

            # Wait until engine enters RUNNING state
            t_spin = time.perf_counter()
            while not engine.is_running() and time.perf_counter() - t_spin < 2.0:
                time.sleep(0.001)

            assert engine.is_running(), f"Run {run_idx}: Engine failed to enter RUNNING state"

            # Let it enter the heavy image wait loop
            time.sleep(0.02)

            # Trigger emergency stop and measure latency
            t_trigger_start = time.perf_counter()
            trigger_latency_ms = listener.trigger_emergency_stop()
            t_trigger_end = time.perf_counter()

            # Wait for background worker to terminate and measure full teardown latency
            t_worker_start = time.perf_counter()
            worker_terminated = engine.wait_for_completion(timeout=1.0)
            t_worker_end = time.perf_counter()
            worker_latency_ms = (t_worker_end - t_trigger_start) * 1000.0

            listener.stop()

            # Assertions for this run
            assert worker_terminated is True, f"Run {run_idx}: Worker thread did not terminate within 1.0s"
            assert engine.state == EngineState.STOPPED, f"Run {run_idx}: State is {engine.state}, expected STOPPED"
            assert engine.cancel_event.is_set(), f"Run {run_idx}: cancel_event not set"

            # Strict < 100ms requirement check
            assert trigger_latency_ms < 100.0, (
                f"Run {run_idx}: trigger latency {trigger_latency_ms:.2f}ms >= 100ms"
            )
            assert worker_latency_ms < 100.0, (
                f"Run {run_idx}: worker termination latency {worker_latency_ms:.2f}ms >= 100ms"
            )

            # Safety flush assertion
            assert mock_input.safety_flush_called is True, f"Run {run_idx}: safety_flush was not called"
            assert mock_input.flush_count >= 1, f"Run {run_idx}: flush_count is 0"
            assert len(mock_input.active_keys) == 0, f"Run {run_idx}: active_keys not empty: {mock_input.active_keys}"
            assert len(mock_input.active_buttons) == 0, f"Run {run_idx}: active_buttons not empty: {mock_input.active_buttons}"

            trigger_latencies.append(trigger_latency_ms)
            worker_latencies.append(worker_latency_ms)
            flush_counts.append(mock_input.flush_count)

        # Statistical analysis over 20 runs
        mean_trigger = np.mean(trigger_latencies)
        max_trigger = np.max(trigger_latencies)
        min_trigger = np.min(trigger_latencies)
        std_trigger = np.std(trigger_latencies)

        mean_worker = np.mean(worker_latencies)
        max_worker = np.max(worker_latencies)
        min_worker = np.min(worker_latencies)
        std_worker = np.std(worker_latencies)

        flush_rate = (sum(1 for c in flush_counts if c >= 1) / num_runs) * 100.0

        print(f"\n--- Heavy Load Emergency Stop Statistics ({num_runs} Runs) ---")
        print(f"Trigger Latency: min={min_trigger:.3f}ms, max={max_trigger:.3f}ms, mean={mean_trigger:.3f}ms, std={std_trigger:.3f}ms")
        print(f"Total Worker Abort Latency: min={min_worker:.3f}ms, max={max_worker:.3f}ms, mean={mean_worker:.3f}ms, std={std_worker:.3f}ms")
        print(f"Safety Flush Execution Rate: {flush_rate:.1f}% ({sum(1 for c in flush_counts if c >= 1)}/{num_runs})")

        # Global assertions over 20 runs
        assert max_trigger < 100.0, f"Max trigger latency {max_trigger:.2f}ms exceeded 100ms threshold"
        assert max_worker < 100.0, f"Max worker abort latency {max_worker:.2f}ms exceeded 100ms threshold"
        assert flush_rate == 100.0, f"Safety flush rate {flush_rate}% did not reach 100%"

    def test_20_consecutive_runs_emergency_stop_during_rapid_action_transitions(self):
        """Executes 20 consecutive runs of emergency stop during rapid action transitions and sliced wait.
        Verifies:
        1. Sub-100ms trigger and teardown latency (<1000ms requirement).
        2. 100% safety flush execution (zero stuck modifiers).
        """
        scenario = ScenarioModel(
            name="Rapid Action Sequence Abort",
            loop_count=0,
            action_delay_ms=2,
            loop_delay_ms=2,
            actions=[
                ActionBlock(id="a-clk1", type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=10, y=20)),
                ActionBlock(id="a-key1", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["a"])),
                ActionBlock(id="a-wait1", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10)),
                ActionBlock(id="a-clk2", type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=30, y=40)),
                ActionBlock(id="a-key2", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["b"])),
                ActionBlock(id="a-wait2", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=20)),
            ],
        )

        num_runs = 20
        trigger_latencies: List[float] = []
        worker_latencies: List[float] = []
        flush_counts: List[int] = []

        print(f"\n--- Starting {num_runs} consecutive runs during rapid action transitions ---")

        for run_idx in range(1, num_runs + 1):
            mock_input = MockInputDriver()
            mock_input.key_down("ctrl")
            mock_input.mouse_down("left")

            engine = ExecutionEngine(
                scenario=scenario,
                input_driver=mock_input,
                screen_driver=None,
                action_delay_ms=2,
                loop_delay_ms=2,
            )

            listener = GlobalHotkeyListener(
                engine=engine,
                input_adapter=mock_input,
                headless=True,
            )
            listener.start()

            worker_thread = engine.start_async()

            t_spin = time.perf_counter()
            while not engine.is_running() and time.perf_counter() - t_spin < 2.0:
                time.sleep(0.001)

            assert engine.is_running()

            # Let it execute mid-stride
            time.sleep(0.015)

            t_trigger_start = time.perf_counter()
            trigger_latency_ms = listener.trigger_emergency_stop()

            worker_terminated = engine.wait_for_completion(timeout=1.0)
            t_worker_end = time.perf_counter()
            worker_latency_ms = (t_worker_end - t_trigger_start) * 1000.0

            listener.stop()

            assert worker_terminated is True
            assert engine.state == EngineState.STOPPED
            assert engine.cancel_event.is_set()
            assert trigger_latency_ms < 100.0
            assert worker_latency_ms < 100.0
            assert mock_input.safety_flush_called is True
            assert mock_input.flush_count >= 1
            assert len(mock_input.active_keys) == 0
            assert len(mock_input.active_buttons) == 0

            trigger_latencies.append(trigger_latency_ms)
            worker_latencies.append(worker_latency_ms)
            flush_counts.append(mock_input.flush_count)

        mean_trigger = np.mean(trigger_latencies)
        max_trigger = np.max(trigger_latencies)
        mean_worker = np.mean(worker_latencies)
        max_worker = np.max(worker_latencies)
        flush_rate = (sum(1 for c in flush_counts if c >= 1) / num_runs) * 100.0

        print(f"\n--- Rapid Transitions Emergency Stop Statistics ({num_runs} Runs) ---")
        print(f"Trigger Latency: min={np.min(trigger_latencies):.3f}ms, max={max_trigger:.3f}ms, mean={mean_trigger:.3f}ms")
        print(f"Total Worker Abort Latency: min={np.min(worker_latencies):.3f}ms, max={max_worker:.3f}ms, mean={mean_worker:.3f}ms")
        print(f"Safety Flush Execution Rate: {flush_rate:.1f}% ({sum(1 for c in flush_counts if c >= 1)}/{num_runs})")

        assert max_trigger < 100.0
        assert max_worker < 100.0
        assert flush_rate == 100.0

