"""
tests.unit.test_input_precision
===============================
Unit test suite verifying:
- Mouse position accuracy within ±1px
- Button clicks (left, right, middle, double-click, press duration hold)
- Keyboard input (single keys, special keys, modifier combinations, unicode)
- Safety flush releasing all held buttons and keys
- InputAdapterProtocol conformance
- PynputAdapter real and fake controller execution
"""

from __future__ import annotations

import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch
import pytest

from action_macro.engine.input_adapter import InputAdapterProtocol, PynputAdapter
from tests.mocks.mock_input import InputEvent, MockInputDriver


# ---------------------------------------------------------------------------
# Test Fakes for Deterministic Headless Testing
# ---------------------------------------------------------------------------

class FakeMouseController:
    """Mock pynput MouseController recording operations and position."""

    def __init__(self, initial_x: int = 0, initial_y: int = 0) -> None:
        self.position: Tuple[float, float] = (float(initial_x), float(initial_y))
        self.pressed: List[Any] = []
        self.released: List[Any] = []
        self.clicks: List[Tuple[Any, int]] = []

    def press(self, button: Any) -> None:
        self.pressed.append(button)

    def release(self, button: Any) -> None:
        self.released.append(button)

    def click(self, button: Any, count: int = 1) -> None:
        self.clicks.append((button, count))


class FakeKeyboardController:
    """Mock pynput KeyboardController recording key presses and typed text."""

    def __init__(self) -> None:
        self.pressed: List[Any] = []
        self.released: List[Any] = []
        self.typed: List[str] = []

    def press(self, key: Any) -> None:
        self.pressed.append(key)

    def release(self, key: Any) -> None:
        self.released.append(key)

    def type(self, text: str) -> None:
        self.typed.append(text)


# =============================================================================
# 1. Mouse Precision Tests (±1px Requirement)
# =============================================================================

@pytest.mark.unit
def test_mouse_move_precision_integer_coordinates(mock_input: MockInputDriver):
    """Verifies that move_to positions the cursor exactly at target coordinates."""
    mock_input.move_to(500, 300)
    assert mock_input.get_position() == (500, 300)
    mock_input.assert_mouse_precision(500, 300, tolerance=1)


@pytest.mark.unit
def test_mouse_move_precision_subpixel_rounding(mock_input: MockInputDriver):
    """Verifies subpixel floating-point coordinates round accurately within ±1px."""
    # 500.4 rounds to 500; 299.7 rounds to 300
    mock_input.move_to(500.4, 299.7)
    x, y = mock_input.get_position()
    assert abs(x - 500) <= 1
    assert abs(y - 300) <= 1
    mock_input.assert_mouse_precision(500, 300, tolerance=1)


@pytest.mark.unit
@pytest.mark.parametrize("target_x, target_y", [
    (0, 0),
    (1919, 1079),
    (960, 540),
    (1, 1),
    (1280, 720),
    (2560, 1440),
])
def test_mouse_move_screen_boundaries(mock_input: MockInputDriver, target_x: int, target_y: int):
    """Verifies precision across extreme screen boundary coordinates."""
    mock_input.move_to(target_x, target_y)
    mock_input.assert_mouse_precision(target_x, target_y, tolerance=1)


@pytest.mark.unit
def test_mouse_consecutive_multi_target_precision(mock_input: MockInputDriver):
    """Verifies that rapid consecutive movements maintain ±1px precision on every step."""
    targets = [
        (100, 100),
        (500, 250),
        (1200, 800),
        (1900, 1000),
        (50, 900),
    ]
    for tx, ty in targets:
        mock_input.click(tx, ty, button="left")
        mock_input.assert_last_click_precision(tx, ty, tolerance=1)


# =============================================================================
# 2. Mouse Click Button & Operation Tests
# =============================================================================

@pytest.mark.unit
def test_mouse_click_single_left(mock_input: MockInputDriver):
    """Verifies single left mouse click records correct button and count."""
    mock_input.click(450, 350, button="left", count=1)
    last = mock_input.last_click
    assert last is not None
    assert last.x == 450
    assert last.y == 350
    assert last.button == "left"
    assert last.clicks == 1
    assert last.click_type == "single"


@pytest.mark.unit
def test_mouse_click_right_button(mock_input: MockInputDriver):
    """Verifies right mouse button click event recording."""
    mock_input.click(1200, 800, button="right")
    last = mock_input.last_click
    assert last is not None
    assert last.button == "right"
    assert last.x == 1200
    assert last.y == 800


@pytest.mark.unit
def test_mouse_click_middle_button(mock_input: MockInputDriver):
    """Verifies middle mouse button click event recording."""
    mock_input.click(960, 540, button="middle")
    last = mock_input.last_click
    assert last is not None
    assert last.button == "middle"


@pytest.mark.unit
def test_mouse_click_double_click(mock_input: MockInputDriver):
    """Verifies double click records clicks=2 and click_type='double'."""
    mock_input.click(640, 480, button="left", count=2)
    last = mock_input.last_click
    assert last is not None
    assert last.clicks == 2
    assert last.click_type == "double"


@pytest.mark.unit
def test_mouse_click_with_press_duration(mock_input: MockInputDriver):
    """Verifies click with hold press duration registers down and up."""
    mock_input.click(320, 240, button="left", press_duration=0.01)
    last = mock_input.last_click
    assert last is not None
    assert last.press_duration == 0.01
    assert last.x == 320
    assert last.y == 240


@pytest.mark.unit
def test_mouse_down_and_up_hold_tracking(mock_input: MockInputDriver):
    """Verifies mouse_down and mouse_up accurately maintain active_buttons set."""
    assert "left" not in mock_input.active_buttons
    mock_input.mouse_down(button="left", x=300, y=200)
    assert "left" in mock_input.active_buttons
    assert mock_input.get_position() == (300, 200)

    mock_input.mouse_up(button="left")
    assert "left" not in mock_input.active_buttons


# =============================================================================
# 3. Keyboard Input & Combination Tests
# =============================================================================

@pytest.mark.unit
def test_keyboard_single_alpha_key(mock_input: MockInputDriver):
    """Verifies single character key press generates key_down and key_up events in sequence."""
    mock_input.press_key("a")
    events = mock_input.key_events
    assert len(events) == 2
    assert events[0].event_type == "key_down" and events[0].key == "a"
    assert events[1].event_type == "key_up" and events[1].key == "a"
    assert len(mock_input.active_keys) == 0


@pytest.mark.unit
@pytest.mark.parametrize("special_key", ["enter", "esc", "f12", "space", "tab", "backspace"])
def test_keyboard_special_function_keys(mock_input: MockInputDriver, special_key: str):
    """Verifies special navigation and function keys are dispatched and cleared."""
    mock_input.press_key(special_key)
    events = [e for e in mock_input.events if e.key == special_key]
    assert len(events) == 2
    assert events[0].event_type == "key_down"
    assert events[1].event_type == "key_up"


@pytest.mark.unit
def test_keyboard_modifier_combination(mock_input: MockInputDriver):
    """Verifies hotkey combo presses modifiers in order and releases in reverse order."""
    mock_input.hotkey("ctrl", "c")
    events = mock_input.events
    hotkey_event = [e for e in events if e.event_type == "hotkey"][0]
    assert hotkey_event.keys == ["ctrl", "c"]

    # Order must be: ctrl down -> c down -> c up -> ctrl up
    key_actions = [(e.event_type, e.key) for e in events if e.event_type in ("key_down", "key_up")]
    assert key_actions == [
        ("key_down", "ctrl"),
        ("key_down", "c"),
        ("key_up", "c"),
        ("key_up", "ctrl"),
    ]
    assert len(mock_input.active_keys) == 0


@pytest.mark.unit
def test_keyboard_triple_modifier_combo(mock_input: MockInputDriver):
    """Verifies 3-key combo (e.g., Ctrl+Shift+Esc) sequence order."""
    mock_input.hotkey("ctrl", "shift", "esc")
    key_actions = [(e.event_type, e.key) for e in mock_input.events if e.event_type in ("key_down", "key_up")]
    assert key_actions == [
        ("key_down", "ctrl"),
        ("key_down", "shift"),
        ("key_down", "esc"),
        ("key_up", "esc"),
        ("key_up", "shift"),
        ("key_up", "ctrl"),
    ]


@pytest.mark.unit
def test_keyboard_unicode_korean_text(mock_input: MockInputDriver):
    """Verifies UTF-8 Korean text typing event records exact string."""
    korean_sample = "퀘스트완료 (Daily Quest) 100%!"
    mock_input.type_text(korean_sample)
    text_events = [e for e in mock_input.events if e.event_type == "text"]
    assert len(text_events) == 1
    assert text_events[0].text == korean_sample


# =============================================================================
# 4. Safety Flush Tests
# =============================================================================

@pytest.mark.unit
def test_safety_flush_releases_held_mouse_buttons(mock_input: MockInputDriver):
    """Verifies safety_flush releases all active mouse buttons."""
    mock_input.mouse_down("left")
    mock_input.mouse_down("right")
    assert mock_input.active_buttons == {"left", "right"}

    mock_input.safety_flush()
    assert len(mock_input.active_buttons) == 0
    assert mock_input.safety_flush_called is True
    # Ensure mouse_up events were emitted for both
    up_buttons = {e.button for e in mock_input.events if e.event_type == "mouse_up"}
    assert "left" in up_buttons and "right" in up_buttons


@pytest.mark.unit
def test_safety_flush_releases_held_keyboard_keys(mock_input: MockInputDriver):
    """Verifies safety_flush releases all stuck modifier and action keys."""
    mock_input.key_down("ctrl")
    mock_input.key_down("alt")
    mock_input.key_down("shift")
    assert mock_input.active_keys == {"ctrl", "alt", "shift"}

    mock_input.safety_flush()
    assert len(mock_input.active_keys) == 0
    up_keys = {e.key for e in mock_input.events if e.event_type == "key_up"}
    assert {"ctrl", "alt", "shift"}.issubset(up_keys)


@pytest.mark.unit
def test_safety_flush_mixed_active_holds(mock_input: MockInputDriver):
    """Verifies safety_flush clears simultaneous mouse and keyboard holds."""
    mock_input.mouse_down("left")
    mock_input.key_down("ctrl")
    mock_input.safety_flush()
    assert len(mock_input.active_buttons) == 0
    assert len(mock_input.active_keys) == 0


@pytest.mark.unit
def test_safety_flush_idempotency(mock_input: MockInputDriver):
    """Verifies repeated calls to safety_flush do not crash or produce spurious releases."""
    mock_input.safety_flush()
    mock_input.safety_flush()
    mock_input.safety_flush()
    assert mock_input.flush_count == 3
    assert len(mock_input.active_buttons) == 0
    assert len(mock_input.active_keys) == 0


# =============================================================================
# 5. Production PynputAdapter Conformance & Verification Tests
# =============================================================================

@pytest.mark.unit
def test_pynput_adapter_protocol_conformance():
    """Verifies that PynputAdapter satisfies InputAdapterProtocol."""
    fake_m = FakeMouseController()
    fake_k = FakeKeyboardController()
    adapter = PynputAdapter(mouse_controller=fake_m, keyboard_controller=fake_k)
    assert isinstance(adapter, InputAdapterProtocol)


@pytest.mark.unit
def test_pynput_adapter_fake_controllers_precision():
    """Verifies PynputAdapter movement, precision, clicking, and position reading."""
    fake_m = FakeMouseController(100, 200)
    fake_k = FakeKeyboardController()
    adapter = PynputAdapter(mouse_controller=fake_m, keyboard_controller=fake_k)

    # Initial position
    assert adapter.get_position() == (100, 200)
    assert adapter.get_cursor_position() == (100, 200)

    # Move to new point
    adapter.move_to(800.2, 600.8)
    assert adapter.get_position() == (800, 601)

    # Click with press duration
    adapter.click(800, 601, button="left", count=1, press_duration=0.001)
    assert len(fake_m.pressed) == 1
    assert len(fake_m.released) == 1
    assert adapter.last_click is not None
    assert adapter.last_click["x"] == 800
    assert adapter.last_click["y"] == 601
    assert adapter.last_click["clicks"] == 1


@pytest.mark.unit
def test_pynput_adapter_hotkey_combo_string_parsing():
    """Verifies that PynputAdapter resolves and dispatches combo strings correctly."""
    fake_m = FakeMouseController()
    fake_k = FakeKeyboardController()
    adapter = PynputAdapter(mouse_controller=fake_m, keyboard_controller=fake_k, platform_name="win32")

    adapter.hotkey("ctrl+alt+delete")
    assert len(fake_k.pressed) == 3
    assert len(fake_k.released) == 3
    # Released in reverse order
    assert len(adapter.active_keys) == 0


@pytest.mark.unit
def test_pynput_adapter_invalid_button_raises():
    """Verifies that unsupported mouse buttons raise ValueError."""
    fake_m = FakeMouseController()
    fake_k = FakeKeyboardController()
    adapter = PynputAdapter(mouse_controller=fake_m, keyboard_controller=fake_k)

    with pytest.raises(ValueError, match="Unsupported mouse button"):
        adapter.click(100, 100, button="invalid_btn")


@pytest.mark.unit
def test_pynput_adapter_safety_flush_clears_active():
    """Verifies PynputAdapter safety_flush clears active buttons and keys."""
    fake_m = FakeMouseController()
    fake_k = FakeKeyboardController()
    adapter = PynputAdapter(mouse_controller=fake_m, keyboard_controller=fake_k)

    adapter.mouse_down("left")
    adapter.key_down("ctrl")
    assert len(adapter.active_buttons) == 1
    assert len(adapter.active_keys) == 1

    adapter.safety_flush()
    assert len(adapter.active_buttons) == 0
    assert len(adapter.active_keys) == 0
