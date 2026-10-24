"""
action_macro.engine.input_adapter
====================================
Cross-platform mouse and keyboard input automation adapter utilizing pynput.
Enforces <=1px mouse precision, key combination translation via core.key_mapping,
discrete hold tracking, and fail-safe safety flushing.

Blocking operations (multi-click intervals, press/hold durations, typing intervals)
accept an optional ``cancel_event``. When it is set, no further press events are
emitted and anything already held by the call is released before it returns.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Protocol, Set, Tuple, Union, runtime_checkable

from action_macro.core.key_mapping import (
    normalize_key_name,
    parse_key_combo,
    resolve_key_for_platform,
    to_pynput_key,
)
from action_macro.engine.wait_system import WaitSystem

logger = logging.getLogger(__name__)


def _is_cancelled(cancel_event: Optional[threading.Event]) -> bool:
    """Returns True if an optional cancellation event has been set."""
    return cancel_event is not None and cancel_event.is_set()


# ---------------------------------------------------------------------------
# Input Adapter Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class InputAdapterProtocol(Protocol):
    """Structural protocol defining the contract for mouse and keyboard drivers."""

    def move_to(self, x: Union[int, float], y: Union[int, float]) -> None:
        """Moves cursor to logical screen coordinates (x, y)."""
        ...

    def click(
        self,
        x: Union[int, float],
        y: Union[int, float],
        button: str = "left",
        count: int = 1,
        interval_ms: int = 0,
        press_duration: float = 0.05,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Executes mouse click at (x, y) with specified button and count.

        Stops early (releasing the button) once ``cancel_event`` is set.
        """
        ...

    def mouse_down(
        self,
        button: str = "left",
        x: Optional[Union[int, float]] = None,
        y: Optional[Union[int, float]] = None,
    ) -> None:
        """Presses and holds a mouse button."""
        ...

    def mouse_up(
        self,
        button: str = "left",
        x: Optional[Union[int, float]] = None,
        y: Optional[Union[int, float]] = None,
    ) -> None:
        """Releases a held mouse button."""
        ...

    def get_position(self) -> Tuple[int, int]:
        """Returns the current cursor position as integer coordinates (x, y)."""
        ...

    def get_cursor_position(self) -> Tuple[int, int]:
        """Alias for get_position."""
        ...

    def press_key(
        self,
        key: str,
        modifiers: Optional[List[str]] = None,
        duration: float = 0.05,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Simulates a complete key press with optional modifiers held as one chord.

        Modifiers go down in order, ``key`` is held for ``duration``, then everything
        is released in reverse order. Stops early (still releasing) once
        ``cancel_event`` is set.
        """
        ...

    def key_down(self, key: str) -> None:
        """Presses and holds a keyboard key."""
        ...

    def key_up(self, key: str) -> None:
        """Releases a held keyboard key."""
        ...

    def release_key(self, key: str) -> None:
        """Alias for key_up conforming to InputAdapterProtocol."""
        ...

    def hotkey(self, *keys: str, cancel_event: Optional[threading.Event] = None) -> None:
        """Dispatches a multi-key shortcut combination in strict order."""
        ...

    def type_text(
        self,
        text: str,
        interval: float = 0.02,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Types unicode or ASCII text string."""
        ...

    def safety_flush(self) -> None:
        """Releases all actively held mouse buttons and keyboard keys."""
        ...


# ---------------------------------------------------------------------------
# Pynput Adapter Implementation
# ---------------------------------------------------------------------------

class PynputAdapter:
    """Production cross-platform input adapter powered by pynput.

    Provides mouse click precision within +/-1px, platform-aware keyboard shortcut
    resolution, active state tracking, and failsafe safety flushing.
    """

    def __init__(
        self,
        mouse_controller: Optional[Any] = None,
        keyboard_controller: Optional[Any] = None,
        platform_name: Optional[str] = None,
    ) -> None:
        self._platform = (platform_name or sys.platform).lower()
        self._active_buttons: Set[str] = set()
        self._active_keys: Set[str] = set()
        self._active_pynput_keys: Dict[str, Any] = {}
        self._last_click: Optional[Dict[str, Any]] = None

        # Lazy / injectable controller initialization
        if mouse_controller is not None:
            self._mouse = mouse_controller
        else:
            try:
                from pynput.mouse import Controller as MouseController
                self._mouse = MouseController()
            except Exception as err:
                logger.warning("Could not initialize native pynput MouseController: %s", err)
                self._mouse = None

        if keyboard_controller is not None:
            self._keyboard = keyboard_controller
        else:
            try:
                from pynput.keyboard import Controller as KeyboardController
                self._keyboard = KeyboardController()
            except Exception as err:
                logger.warning("Could not initialize native pynput KeyboardController: %s", err)
                self._keyboard = None

    # -------------------------------------------------------------------------
    # Internal Button Helpers
    # -------------------------------------------------------------------------

    def _resolve_button(self, button: str) -> Any:
        try:
            from pynput.mouse import Button
        except (ImportError, Exception):
            class Button:  # Fallback stub for headless mock injection
                left = "left"
                right = "right"
                middle = "middle"

        btn_str = button.strip().lower()
        mapping = {
            "left": Button.left,
            "right": Button.right,
            "middle": Button.middle,
        }
        if btn_str not in mapping:
            raise ValueError(f"Unsupported mouse button: {button!r}. Expected 'left', 'right', or 'middle'.")
        return mapping[btn_str]

    def _check_controllers(self) -> None:
        if self._mouse is None or self._keyboard is None:
            raise RuntimeError(
                "pynput controllers are not available in this environment. "
                "In headless test environments, pass mock controllers or use MockInputDriver."
            )

    # -------------------------------------------------------------------------
    # Mouse Operations
    # -------------------------------------------------------------------------

    def move_to(self, x: Union[int, float], y: Union[int, float]) -> None:
        """Moves cursor to logical screen coordinates (x, y) with <=1px precision."""
        self._check_controllers()
        target_x = int(round(float(x)))
        target_y = int(round(float(y)))
        self._mouse.position = (target_x, target_y)

        # Precision stabilization loop: verify position settles within ±1px
        try:
            curr_x, curr_y = self.get_position()
            if abs(curr_x - target_x) > 1 or abs(curr_y - target_y) > 1:
                time.sleep(0.005)
                self._mouse.position = (target_x, target_y)
        except Exception:
            pass

    def click(
        self,
        x: Union[int, float],
        y: Union[int, float],
        button: str = "left",
        count: int = 1,
        interval_ms: int = 0,
        press_duration: float = 0.05,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Executes mouse click at (x, y) with specified button, count, and press duration.

        Hold and inter-click waits wake up as soon as ``cancel_event`` is set: no
        further button presses are issued and a held button is released immediately.
        """
        self._check_controllers()
        if _is_cancelled(cancel_event):
            return
        self.move_to(x, y)
        norm_button = button.strip().lower()
        btn_enum = self._resolve_button(norm_button)

        for i in range(count):
            if _is_cancelled(cancel_event):
                break

            if press_duration > 0.0:
                self.mouse_down(norm_button)
                try:
                    WaitSystem.wait(press_duration, cancel_event=cancel_event)
                finally:
                    self.mouse_up(norm_button)
            else:
                self._mouse.click(btn_enum, 1)

            if count > 1 and interval_ms > 0 and i < count - 1:
                if not WaitSystem.wait(interval_ms / 1000.0, cancel_event=cancel_event):
                    break

        self._last_click = {
            "x": int(round(float(x))),
            "y": int(round(float(y))),
            "button": norm_button,
            "clicks": count,
            "count": count,
            "press_duration": press_duration,
        }

    def mouse_down(
        self,
        button: str = "left",
        x: Optional[Union[int, float]] = None,
        y: Optional[Union[int, float]] = None,
    ) -> None:
        """Presses and holds a mouse button."""
        self._check_controllers()
        if x is not None and y is not None:
            self.move_to(x, y)

        norm_button = button.strip().lower()
        btn_enum = self._resolve_button(norm_button)
        self._mouse.press(btn_enum)
        self._active_buttons.add(norm_button)

    def mouse_up(
        self,
        button: str = "left",
        x: Optional[Union[int, float]] = None,
        y: Optional[Union[int, float]] = None,
    ) -> None:
        """Releases a held mouse button."""
        self._check_controllers()
        if x is not None and y is not None:
            self.move_to(x, y)

        norm_button = button.strip().lower()
        btn_enum = self._resolve_button(norm_button)
        self._mouse.release(btn_enum)
        self._active_buttons.discard(norm_button)

    def get_position(self) -> Tuple[int, int]:
        """Returns the current cursor position as integer coordinates (x, y)."""
        self._check_controllers()
        pos = self._mouse.position
        return (int(round(float(pos[0]))), int(round(float(pos[1]))))

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
        """Simulates a complete key press with optional modifiers held down.

        Modifiers are pressed in order and kept down, ``key`` is held for
        ``duration`` seconds, then every pressed key is released in reverse order.
        The release is guaranteed (try/finally) even if a press fails or
        ``cancel_event`` is set mid-way; once cancelled, no further key is pressed.
        """
        self._check_controllers()
        if _is_cancelled(cancel_event):
            return
        mods = [normalize_key_name(m) for m in modifiers] if modifiers else []

        pressed: List[str] = []
        try:
            # 1. Press modifiers down in order, then the target key
            for k in [*mods, key]:
                if _is_cancelled(cancel_event):
                    return
                # Track before pressing: key_down marks the key active even on failure.
                pressed.append(k)
                self.key_down(k)

            # 2. Hold duration (wakes immediately on cancellation)
            if duration > 0.0:
                WaitSystem.wait(duration, cancel_event=cancel_event)
        finally:
            # 3. Release target key, then modifiers, in reverse order
            self._release_keys_reversed(pressed)

    def _release_keys_reversed(self, keys: List[str]) -> None:
        """Releases ``keys`` in reverse order, attempting every key even if one fails."""
        first_error: Optional[BaseException] = None
        for k in reversed(keys):
            try:
                self.key_up(k)
            except Exception as err:
                logger.warning("Failed to release key %r: %s", k, err)
                if first_error is None:
                    first_error = err
        if first_error is not None:
            raise first_error

    def key_down(self, key: str) -> None:
        """Presses and holds a keyboard key."""
        self._check_controllers()
        norm_key = normalize_key_name(key)
        resolved_name = resolve_key_for_platform(norm_key, self._platform)
        pynput_key = to_pynput_key(resolved_name, self._platform)

        try:
            self._keyboard.press(pynput_key)
        finally:
            self._active_keys.add(norm_key)
            self._active_pynput_keys[norm_key] = pynput_key

    def key_up(self, key: str) -> None:
        """Releases a held keyboard key."""
        self._check_controllers()
        norm_key = normalize_key_name(key)
        pynput_key = self._active_pynput_keys.pop(norm_key, None)
        if pynput_key is None:
            resolved_name = resolve_key_for_platform(norm_key, self._platform)
            pynput_key = to_pynput_key(resolved_name, self._platform)

        try:
            self._keyboard.release(pynput_key)
        finally:
            self._active_keys.discard(norm_key)

    def release_key(self, key: str) -> None:
        """Alias for key_up."""
        self.key_up(key)

    def hotkey(self, *keys: str, cancel_event: Optional[threading.Event] = None) -> None:
        """Dispatches a multi-key shortcut combination in strict order."""
        self._check_controllers()
        if not keys:
            return

        resolved_tokens: List[str] = []
        for token in keys:
            if isinstance(token, str) and "+" in token and len(token) > 1 and not token.endswith("++"):
                resolved_tokens.extend(parse_key_combo(token))
            else:
                resolved_tokens.append(normalize_key_name(token))

        if not resolved_tokens:
            return

        # Press down in order, hold briefly for the OS window manager to register
        # the combo, then release in reverse order.
        self.press_key(
            resolved_tokens[-1],
            modifiers=resolved_tokens[:-1],
            duration=0.02,
            cancel_event=cancel_event,
        )

    def type_text(
        self,
        text: str,
        interval: float = 0.02,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        """Types unicode or ASCII text string, stopping early once ``cancel_event`` is set."""
        self._check_controllers()
        if _is_cancelled(cancel_event):
            return
        if interval > 0.0:
            for ch in text:
                if _is_cancelled(cancel_event):
                    return
                self._keyboard.type(ch)
                if not WaitSystem.wait(interval, cancel_event=cancel_event):
                    return
        else:
            self._keyboard.type(text)

    # -------------------------------------------------------------------------
    # Safety Flush & State Inspection
    # -------------------------------------------------------------------------

    def safety_flush(self) -> None:
        """Emergency teardown: releases all actively held mouse buttons and keys.

        Guarantees that emergency stops leave no stuck modifier keys (Ctrl/Shift/Alt/Cmd)
        or dragging mouse states.
        """
        if self._mouse is not None:
            # Release all tracked mouse buttons
            for btn_name in list(self._active_buttons):
                try:
                    self._mouse.release(self._resolve_button(btn_name))
                except Exception as err:
                    logger.debug("safety_flush tracked mouse release failed: %s", err)
            self._active_buttons.clear()

            # Fallback release of all standard physical buttons
            try:
                from pynput.mouse import Button
                for btn in (Button.left, Button.right, Button.middle):
                    try:
                        self._mouse.release(btn)
                    except Exception:
                        pass
            except Exception:
                pass

        if self._keyboard is not None:
            # Release all tracked keyboard keys
            for norm_key in list(self._active_keys):
                try:
                    pk = self._active_pynput_keys.pop(norm_key, None)
                    if pk is None:
                        res = resolve_key_for_platform(norm_key, self._platform)
                        pk = to_pynput_key(res, self._platform)
                    self._keyboard.release(pk)
                except Exception as err:
                    logger.debug("safety_flush tracked key release failed: %s", err)
            self._active_keys.clear()
            self._active_pynput_keys.clear()

            # Fallback release of standard modifier keys across platforms
            for mod in ("ctrl", "alt", "shift", "cmd"):
                try:
                    pk = to_pynput_key(mod, self._platform)
                    self._keyboard.release(pk)
                except Exception:
                    pass

    @property
    def active_buttons(self) -> Set[str]:
        """Set of currently held mouse buttons."""
        return set(self._active_buttons)

    @property
    def active_keys(self) -> Set[str]:
        """Set of currently held keyboard keys."""
        return set(self._active_keys)

    @property
    def last_click(self) -> Optional[Dict[str, Any]]:
        """Most recent click event details."""
        return self._last_click
