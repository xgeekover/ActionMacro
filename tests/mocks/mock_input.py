"""Mock input driver for deterministic headless testing of mouse and keyboard actions.

Conforms to InputAdapterProtocol with precision tracking (±1px), event logging,
active hold tracking, and safety flush verification.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple


@dataclass
class InputEvent:
    """Represents a recorded input event."""
    event_type: str  # "move", "click", "mouse_down", "mouse_up", "key_down", "key_up", "hotkey", "text", "safety_flush"
    x: Optional[int] = None
    y: Optional[int] = None
    button: Optional[str] = None
    click_type: Optional[str] = None
    clicks: int = 1
    press_duration: float = 0.0
    key: Optional[str] = None
    modifiers: Optional[List[str]] = None
    keys: Optional[List[str]] = None
    text: Optional[str] = None
    timestamp: float = field(default_factory=time.perf_counter)


class MockInputDriver:
    """Deterministic mock input driver for testing mouse/keyboard automation.

    Tracks cursor position, records all events, enforces ±1px mouse precision assertions,
    maintains active key/button states, and supports emergency safety flush.
    """

    def __init__(self, initial_x: int = 0, initial_y: int = 0) -> None:
        self.cursor_x: int = initial_x
        self.cursor_y: int = initial_y
        self.active_buttons: Set[str] = set()
        self.active_keys: Set[str] = set()
        self.events: List[InputEvent] = []
        self.safety_flush_called: bool = False
        self.flush_count: int = 0

    # -------------------------------------------------------------------------
    # Mouse Operations
    # -------------------------------------------------------------------------

    def move_to(self, x: int, y: int) -> None:
        """Moves cursor to logical screen coordinates (x, y)."""
        self.cursor_x = int(round(x))
        self.cursor_y = int(round(y))
        self.events.append(InputEvent(
            event_type="move",
            x=self.cursor_x,
            y=self.cursor_y,
            timestamp=time.perf_counter(),
        ))

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        count: int = 1,
        interval_ms: int = 0,
        press_duration: float = 0.05,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Executes mouse click at (x, y) with specified button and count.

        Mirrors the real adapter contract: nothing is emitted once ``cancel_event``
        is set (this mock never blocks, so it cannot be interrupted mid-click).
        """
        if cancel_event is not None and cancel_event.is_set():
            return
        self.move_to(x, y)
        self.events.append(InputEvent(
            event_type="click",
            x=self.cursor_x,
            y=self.cursor_y,
            button=button.lower(),
            clicks=count,
            click_type="single" if count == 1 else ("double" if count == 2 else "triple"),
            press_duration=press_duration,
            timestamp=time.perf_counter(),
        ))

    def mouse_down(self, button: str = "left", x: Optional[int] = None, y: Optional[int] = None) -> None:
        """Presses and holds a mouse button."""
        if x is not None and y is not None:
            self.move_to(x, y)
        btn = button.lower()
        self.active_buttons.add(btn)
        self.events.append(InputEvent(
            event_type="mouse_down",
            x=self.cursor_x,
            y=self.cursor_y,
            button=btn,
            timestamp=time.perf_counter(),
        ))

    def mouse_up(self, button: str = "left", x: Optional[int] = None, y: Optional[int] = None) -> None:
        """Releases a held mouse button."""
        if x is not None and y is not None:
            self.move_to(x, y)
        btn = button.lower()
        self.active_buttons.discard(btn)
        self.events.append(InputEvent(
            event_type="mouse_up",
            x=self.cursor_x,
            y=self.cursor_y,
            button=btn,
            timestamp=time.perf_counter(),
        ))

    def get_position(self) -> Tuple[int, int]:
        """Returns the current cursor position (x, y)."""
        return (self.cursor_x, self.cursor_y)

    def get_cursor_position(self) -> Tuple[int, int]:
        """Alias for get_position."""
        return self.get_position()

    # -------------------------------------------------------------------------
    # Keyboard Operations
    # -------------------------------------------------------------------------

    def press_key(
        self,
        key: str,
        modifiers: Optional[List[str]] = None,
        duration: float = 0.05,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Simulates a complete key press (down then up), with optional modifiers.

        Modifiers are held as one chord with ``key`` and released in reverse order.
        Nothing is emitted once ``cancel_event`` is set.
        """
        if cancel_event is not None and cancel_event.is_set():
            return
        mods = [m.lower() for m in modifiers] if modifiers else []
        for mod in mods:
            self.key_down(mod)
        self.key_down(key)
        self.key_up(key)
        for mod in reversed(mods):
            self.key_up(mod)

    def key_down(self, key: str) -> None:
        """Presses and holds a keyboard key."""
        k = key.lower()
        self.active_keys.add(k)
        self.events.append(InputEvent(
            event_type="key_down",
            key=k,
            timestamp=time.perf_counter(),
        ))

    def key_up(self, key: str) -> None:
        """Releases a keyboard key."""
        k = key.lower()
        self.active_keys.discard(k)
        self.events.append(InputEvent(
            event_type="key_up",
            key=k,
            timestamp=time.perf_counter(),
        ))

    def release_key(self, key: str) -> None:
        """Alias for key_up conforming to InputAdapterProtocol."""
        self.key_up(key)

    def hotkey(self, *keys: str, cancel_event: Optional[threading.Event] = None) -> None:
        """Dispatches a multi-key shortcut combination in strict order.

        Nothing is emitted once ``cancel_event`` is set.
        """
        if cancel_event is not None and cancel_event.is_set():
            return
        key_list = [k.lower() for k in keys]
        self.events.append(InputEvent(
            event_type="hotkey",
            keys=key_list,
            timestamp=time.perf_counter(),
        ))
        for k in key_list:
            self.key_down(k)
        for k in reversed(key_list):
            self.key_up(k)

    def type_text(
        self,
        text: str,
        interval: float = 0.02,
        cancel_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> None:
        """Types a string of text character by character or via paste simulation.

        Nothing is emitted once ``cancel_event`` is set.
        """
        if cancel_event is not None and cancel_event.is_set():
            return
        self.events.append(InputEvent(
            event_type="text",
            text=text,
            timestamp=time.perf_counter(),
        ))

    # -------------------------------------------------------------------------
    # Safety Flush & Teardown
    # -------------------------------------------------------------------------

    def safety_flush(self) -> None:
        """Releases all actively held mouse buttons and keyboard keys.

        Guarantees system does not leave stuck modifier keys (Ctrl/Shift/Alt) or
        dragging mouse states when an emergency stop occurs.
        """
        self.safety_flush_called = True
        self.flush_count += 1

        for btn in list(self.active_buttons):
            self.mouse_up(button=btn)
        self.active_buttons.clear()

        for k in list(self.active_keys):
            self.key_up(key=k)
        self.active_keys.clear()

        self.events.append(InputEvent(
            event_type="safety_flush",
            timestamp=time.perf_counter(),
        ))

    # -------------------------------------------------------------------------
    # Assertion & Query Helpers
    # -------------------------------------------------------------------------

    @property
    def clicks(self) -> List[InputEvent]:
        """Returns all recorded click events."""
        return [e for e in self.events if e.event_type == "click"]

    @property
    def last_click(self) -> Optional[InputEvent]:
        """Returns the most recent click event, or None."""
        clicks = self.clicks
        return clicks[-1] if clicks else None

    @property
    def key_events(self) -> List[InputEvent]:
        """Returns all key-related events."""
        return [e for e in self.events if e.event_type in ("key_down", "key_up", "hotkey", "text")]

    def assert_mouse_precision(self, target_x: int, target_y: int, tolerance: int = 1) -> None:
        """Asserts that the current mouse position matches target within ±tolerance pixels."""
        diff_x = abs(self.cursor_x - target_x)
        diff_y = abs(self.cursor_y - target_y)
        assert diff_x <= tolerance and diff_y <= tolerance, (
            f"Mouse position ({self.cursor_x}, {self.cursor_y}) deviates from target "
            f"({target_x}, {target_y}) by ({diff_x}px, {diff_y}px) - tolerance is ±{tolerance}px"
        )

    def assert_last_click_precision(self, target_x: int, target_y: int, tolerance: int = 1) -> None:
        """Asserts that the most recent click occurred at (target_x, target_y) within ±tolerance."""
        last = self.last_click
        assert last is not None, "No click event has been recorded"
        assert last.x is not None and last.y is not None
        diff_x = abs(last.x - target_x)
        diff_y = abs(last.y - target_y)
        assert diff_x <= tolerance and diff_y <= tolerance, (
            f"Last click at ({last.x}, {last.y}) deviates from target "
            f"({target_x}, {target_y}) by ({diff_x}px, {diff_y}px) - tolerance is ±{tolerance}px"
        )

    def reset(self) -> None:
        """Clears all event history and resets state."""
        self.cursor_x = 0
        self.cursor_y = 0
        self.active_buttons.clear()
        self.active_keys.clear()
        self.events.clear()
        self.safety_flush_called = False
        self.flush_count = 0
