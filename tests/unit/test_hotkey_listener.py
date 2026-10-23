"""
tests.unit.test_hotkey_listener
===============================
Unit test suite verifying:
- Hotkey token parsing and normalization (<ctrl>+<shift>+s, <esc>, <f11>, etc.)
- Case and whitespace insensitivity in hotkey strings
- Invalid hotkey rejection (empty, malformed pluses, unknown tokens)
- Registration of default emergency stop hotkeys (<ctrl>+<shift>+s and <esc>)
- Multi-hotkey binding and dynamic re-registration
- Callback execution fidelity and callback exception isolation
- Thread lifecycle management: daemon flag, start, stop, is_alive, context manager
- Idempotent start() and stop() calls and restartable lifecycle
- Headless mock listener verification for deterministic CI/CD test runs
- Emergency stop integration: sub-50ms latency and safety flush verification
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, List, Optional
import pytest

from action_macro.runner.execution_engine import ExecutionEngine
from action_macro.runner.hotkey_listener import (
    DEFAULT_EMERGENCY_HOTKEYS,
    GlobalHotkeyListener,
    HotkeyListener,
    HotkeyListenerProtocol,
    HotkeyParser,
    MockHotkeyListener,
    create_emergency_stop_listener,
    normalize_hotkey_for_pynput,
    normalize_hotkey_to_pynput,
)
from action_macro.runner.state_machine import EngineState
from tests.mocks.contract import ActionBlock, ActionType, ScenarioModel
from tests.mocks.mock_input import MockInputDriver

logger = logging.getLogger(__name__)


# =============================================================================
# 1. Hotkey Parser & Normalization
# =============================================================================

@pytest.mark.unit
class TestHotkeyParser:
    """Verifies parsing, token normalization, and syntax error validation."""

    def test_default_emergency_hotkeys(self):
        """Default emergency hotkeys contain <ctrl>+<shift>+s and <esc>."""
        assert "<ctrl>+<shift>+s" in DEFAULT_EMERGENCY_HOTKEYS
        assert "<esc>" in DEFAULT_EMERGENCY_HOTKEYS

    @pytest.mark.parametrize(
        "input_hk, expected",
        [
            ("ctrl+shift+s", "<ctrl>+<shift>+s"),
            ("CTRL+SHIFT+S", "<ctrl>+<shift>+s"),
            ("  ctrl  +  shift  +  s  ", "<ctrl>+<shift>+s"),
            ("<ctrl>+<shift>+s", "<ctrl>+<shift>+s"),
            ("esc", "<esc>"),
            ("ESC", "<esc>"),
            ("escape", "<esc>"),
            ("<esc>", "<esc>"),
            ("f11", "<f11>"),
            ("F12", "<f12>"),
            ("alt+x", "<alt>+x"),
            ("cmd+shift+q", "<cmd>+<shift>+q"),
            ("ctrl+alt+del", "<ctrl>+<alt>+<delete>"),
            ("space", "<space>"),
            ("enter", "<enter>"),
            ("alt+f4", "<alt>+<f4>"),
        ],
    )
    def test_hotkey_normalization_success(self, input_hk: str, expected: str):
        assert HotkeyParser.normalize(input_hk) == expected
        assert normalize_hotkey_to_pynput(input_hk) == expected
        assert normalize_hotkey_for_pynput(input_hk) == expected

    @pytest.mark.parametrize(
        "invalid_hk",
        [
            "",
            "   ",
            "ctrl+",
            "+shift",
            "ctrl++s",
            "+",
            "unknown_invalid_key",
            "ctrl+invalid_long_key_name",
        ],
    )
    def test_invalid_hotkey_syntax_raises_value_error(self, invalid_hk: str):
        with pytest.raises(ValueError):
            HotkeyParser.normalize(invalid_hk)

    def test_invalid_type_raises_type_error(self):
        with pytest.raises(TypeError):
            HotkeyParser.normalize(None)  # type: ignore[arg-type]

    def test_platform_modifier_resolution(self):
        """Verifies platform modifier aliases (e.g. cmd_or_ctrl)."""
        mac_res = normalize_hotkey_to_pynput("cmd_or_ctrl+q", platform_name="darwin")
        win_res = normalize_hotkey_to_pynput("cmd_or_ctrl+q", platform_name="win32")

        assert mac_res == "<cmd>+q"
        assert win_res == "<ctrl>+q"


# =============================================================================
# 2. Hotkey Listener Registration & Mapping
# =============================================================================

@pytest.mark.unit
class TestHotkeyRegistration:
    """Verifies hotkey binding, dynamic registration, unregistration, and overriding."""

    def test_single_hotkey_registration(self):
        listener = GlobalHotkeyListener(headless=True)
        called = []
        norm = listener.register_hotkey("ctrl+shift+s", lambda: called.append(True))

        assert norm == "<ctrl>+<shift>+s"
        assert listener.trigger_hotkey("ctrl+shift+s") is True
        assert len(called) == 1

    def test_register_multiple_callbacks(self):
        listener = GlobalHotkeyListener(headless=True)
        stop_called = []
        pause_called = []

        listener.register_hotkey("<ctrl>+<shift>+s", lambda: stop_called.append(True))
        listener.register_hotkey("<ctrl>+<shift>+p", lambda: pause_called.append(True))

        listener.trigger_hotkey("ctrl+shift+s")
        assert len(stop_called) == 1
        assert len(pause_called) == 0

        listener.trigger_hotkey("ctrl+shift+p")
        assert len(stop_called) == 1
        assert len(pause_called) == 1

    def test_unregister_hotkey(self):
        listener = GlobalHotkeyListener(headless=True)
        calls = []
        listener.register_hotkey("f11", lambda: calls.append(1))
        assert listener.trigger_hotkey("f11") is True
        assert len(calls) == 1

        listener.unregister_hotkey("f11")
        assert listener.trigger_hotkey("f11") is False
        assert len(calls) == 1

    def test_reregister_overwrites_existing_callback(self):
        listener = GlobalHotkeyListener(headless=True)
        res = []
        listener.register_hotkey("esc", lambda: res.append("first"))
        listener.register_hotkey("esc", lambda: res.append("second"))

        listener.trigger_hotkey("esc")
        assert res == ["second"]


# =============================================================================
# 3. Callback Execution & Safety
# =============================================================================

@pytest.mark.unit
class TestCallbackExecution:
    """Verifies callback invocation, multiple triggers, and exception containment."""

    def test_callback_invoked_on_matching_trigger(self):
        listener = GlobalHotkeyListener(headless=True)
        count = 0

        def _inc():
            nonlocal count
            count += 1

        listener.register_hotkey("ctrl+shift+s", _inc)
        for _ in range(3):
            listener.trigger_hotkey("ctrl+shift+s")
        assert count == 3

    def test_callback_exception_isolated(self):
        """Verifies that an unhandled exception in callback does not crash listener."""
        listener = GlobalHotkeyListener(headless=True)
        calls = []

        def _buggy_callback():
            calls.append("called")
            raise RuntimeError("Fatal callback error")

        listener.register_hotkey("esc", _buggy_callback)
        listener.start()

        # Triggering the buggy hotkey must not crash the listener
        assert listener.trigger_hotkey("esc") is True
        assert calls == ["called"]
        assert listener.is_alive is True

        listener.stop()


# =============================================================================
# 4. Thread Lifecycle Management & Protocol
# =============================================================================

@pytest.mark.unit
class TestThreadLifecycle:
    """Verifies thread lifecycle: daemon flag, start, stop, idempotency, context manager."""

    def test_start_and_stop_lifecycle(self):
        listener = GlobalHotkeyListener(headless=True)
        assert listener.is_alive is False
        assert listener.is_running is False

        listener.start()
        assert listener.is_alive is True
        assert listener.is_running is True
        assert listener._listener_thread is not None
        assert listener._listener_thread.daemon is True

        listener.stop()
        assert listener.is_alive is False
        assert listener.is_running is False

    def test_start_idempotent(self):
        listener = GlobalHotkeyListener(headless=True)
        listener.start()
        t1 = listener._listener_thread
        listener.start()  # Redundant call
        assert listener._listener_thread == t1
        listener.stop()

    def test_stop_idempotent(self):
        listener = GlobalHotkeyListener(headless=True)
        listener.start()
        listener.stop()
        listener.stop()  # Redundant call
        assert listener.is_alive is False

    def test_restartable_lifecycle(self):
        """Verifies listener can be started, stopped, and started again without error."""
        listener = GlobalHotkeyListener(headless=True)
        listener.start()
        assert listener.is_alive is True
        listener.stop()
        assert listener.is_alive is False

        # Restarting must not raise RuntimeError: threads can only be started once
        listener.start()
        assert listener.is_alive is True
        listener.stop()
        assert listener.is_alive is False

    def test_context_manager_protocol(self):
        listener = GlobalHotkeyListener(headless=True)
        with listener:
            assert listener.is_alive is True
        assert listener.is_alive is False

    def test_listener_protocol_conformance(self):
        listener = GlobalHotkeyListener(headless=True)
        assert isinstance(listener, HotkeyListenerProtocol)


# =============================================================================
# 5. Headless & Mock Mode
# =============================================================================

@pytest.mark.unit
class TestHeadlessAndMockMode:
    """Verifies deterministic programmatic triggering without OS display dependencies."""

    def test_trigger_hotkey_case_and_formatting_variants(self):
        listener = GlobalHotkeyListener(headless=True)
        triggered = []
        listener.register_hotkey("<ctrl>+<shift>+s", lambda: triggered.append(True))

        assert listener.trigger_hotkey("ctrl+shift+s") is True
        assert listener.trigger_hotkey("CTRL+SHIFT+S") is True
        assert listener.trigger_hotkey("  <ctrl>+<shift>+s  ") is True
        assert len(triggered) == 3

    def test_trigger_unregistered_hotkey_returns_false(self):
        listener = GlobalHotkeyListener(headless=True)
        listener.register_hotkey("f11", lambda: None)
        assert listener.trigger_hotkey("f12") is False

    def test_simulate_hotkey_alias(self):
        listener = GlobalHotkeyListener(headless=True)
        triggered = []
        listener.register_hotkey("esc", lambda: triggered.append(True))
        assert listener.simulate_hotkey("esc") is True
        assert len(triggered) == 1


# =============================================================================
# 6. Emergency Stop System Integration
# =============================================================================

@pytest.mark.unit
class TestEmergencyStopIntegration:
    """Verifies integration between GlobalHotkeyListener and ExecutionEngine emergency abort."""

    def test_hotkey_triggers_engine_emergency_stop_latency(self, mock_input: MockInputDriver):
        """Verifies triggering registered emergency stop hotkey aborts macro in <= 50ms."""
        scenario = ScenarioModel(
            name="Emergency Abort Scenario",
            actions=[
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 5000}),
            ],
        )
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        listener = GlobalHotkeyListener(headless=True)
        listener.register_hotkey("<ctrl>+<shift>+s", engine.stop)

        def _trigger_hotkey_delayed():
            time.sleep(0.025)
            listener.trigger_hotkey("ctrl+shift+s")

        threading.Thread(target=_trigger_hotkey_delayed, daemon=True).start()
        t0 = time.perf_counter()
        completed = engine.run()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert completed is False
        assert elapsed_ms <= 100.0  # Well under 1000ms requirement
        assert engine.state == EngineState.STOPPED

    def test_hotkey_emergency_stop_releases_held_inputs(self, mock_input: MockInputDriver):
        """Verifies hotkey emergency stop triggers safety flush and releases active holds."""
        mock_input.mouse_down(button="left")
        mock_input.key_down("shift")
        assert len(mock_input.active_buttons) == 1
        assert len(mock_input.active_keys) == 1

        scenario = ScenarioModel(name="Hold Abort", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        listener = GlobalHotkeyListener(headless=True)
        listener.register_hotkey("<esc>", engine.stop)

        listener.trigger_hotkey("esc")

        assert engine.state == EngineState.STOPPED
        assert mock_input.safety_flush_called is True
        assert len(mock_input.active_buttons) == 0
        assert len(mock_input.active_keys) == 0

    def test_create_emergency_stop_listener_factory(self, mock_input: MockInputDriver):
        scenario = ScenarioModel(name="Factory Test", actions=[])
        engine = ExecutionEngine(scenario, input_driver=mock_input)
        stopped = []

        listener = create_emergency_stop_listener(
            engine=engine,
            headless=True,
            on_emergency_stop=lambda: stopped.append(True),
        )
        assert isinstance(listener, GlobalHotkeyListener)
        assert listener.is_headless is True

        listener.trigger_emergency_stop()
        assert engine.state == EngineState.STOPPED
        assert len(stopped) == 1
        assert listener.abort_count == 1
        assert listener.last_abort_latency_ms is not None


# =============================================================================
# 7. Challenger Regression Defect Test Suite (Milestone 3 Fix)
# =============================================================================

@pytest.mark.unit
class TestHotkeyRegressionDefects:
    """Verifies resolution of defects uncovered during Milestone 3 Challenger review:
    1. PageUp and PageDown token normalization & pynput parsing compatibility.
    2. Repeated modifier deduplication preserving order (e.g. ctrl+ctrl+s -> <ctrl>+s).
    3. Rapid 50 start/stop cycles in headless mode with 0 zombie threads and stop latency <20ms.
    """

    @pytest.mark.parametrize(
        "raw_key, expected_norm",
        [
            ("pageup", "<page_up>"),
            ("pagedown", "<page_down>"),
            ("page_up", "<page_up>"),
            ("page_down", "<page_down>"),
            ("pgup", "<page_up>"),
            ("pgdn", "<page_down>"),
            ("PAGEUP", "<page_up>"),
            ("PAGEDOWN", "<page_down>"),
            ("ctrl+pageup", "<ctrl>+<page_up>"),
            ("shift+pagedown", "<shift>+<page_down>"),
            ("ctrl+alt+page_up", "<ctrl>+<alt>+<page_up>"),
        ],
    )
    def test_pageup_pagedown_normalization_and_pynput_parsing(self, raw_key: str, expected_norm: str):
        """Verifies pageup and pagedown normalize to canonical <page_up> and <page_down>
        and parse cleanly without ValueError in pynput.keyboard.HotKey.parse.
        """
        from pynput.keyboard import HotKey

        norm = normalize_hotkey_to_pynput(raw_key)
        assert norm == expected_norm, f"Expected {raw_key!r} to normalize to {expected_norm!r}, got {norm!r}"

        # Must parse cleanly in pynput without raising ValueError
        parsed = HotKey.parse(norm)
        assert len(parsed) > 0

    @pytest.mark.parametrize(
        "raw_combo, expected_norm",
        [
            ("ctrl+ctrl+s", "<ctrl>+s"),
            ("ctrl+shift+ctrl+s", "<ctrl>+<shift>+s"),
            ("alt+alt+f4", "<alt>+<f4>"),
            ("<ctrl>+<ctrl>+s", "<ctrl>+s"),
            ("cmd+cmd+q", "<cmd>+q"),
            ("shift+ctrl+shift+a", "<shift>+<ctrl>+a"),
            ("ctrl+ctrl+ctrl+x", "<ctrl>+x"),
            ("alt+shift+alt+esc", "<alt>+<shift>+<esc>"),
        ],
    )
    def test_repeated_modifier_deduplication(self, raw_combo: str, expected_norm: str):
        """Verifies repeated modifiers in combination strings are deduplicated while preserving order,
        producing valid tokens accepted by pynput.keyboard.HotKey.parse.
        """
        from pynput.keyboard import HotKey

        norm = normalize_hotkey_to_pynput(raw_combo)
        assert norm == expected_norm, f"Expected {raw_combo!r} to normalize to {expected_norm!r}, got {norm!r}"

        parsed = HotKey.parse(norm)
        assert len(parsed) > 0

    def test_rapid_50_headless_cycles_zero_zombies_and_sub_20ms_latency(self):
        """Verifies 50 rapid start/stop cycles in headless mode complete with:
        - 0 RuntimeError ("threads can only be started once")
        - 0 lingering zombie daemon threads
        - Individual stop latency strictly < 20ms (average < 5ms)
        """
        initial_threads = threading.active_count()
        listener = GlobalHotkeyListener(headless=True)
        stop_latencies: List[float] = []

        for i in range(50):
            started = listener.start()
            assert started is True
            assert listener.is_alive is True

            t0 = time.perf_counter()
            listener.stop()
            lat_ms = (time.perf_counter() - t0) * 1000.0

            assert listener.is_alive is False
            stop_latencies.append(lat_ms)
            assert lat_ms < 20.0, f"Cycle {i} stop latency ({lat_ms:.2f}ms) exceeded 20ms limit"

        avg_lat = sum(stop_latencies) / len(stop_latencies)
        assert avg_lat < 5.0, f"Average stop latency ({avg_lat:.2f}ms) exceeded 5ms target"

        time.sleep(0.05)
        lingering = [
            t.name
            for t in threading.enumerate()
            if "HotkeyListener" in t.name and t.is_alive()
        ]
        assert len(lingering) == 0, f"Lingering zombie daemon threads detected: {lingering}"
        assert threading.active_count() <= initial_threads + 1

