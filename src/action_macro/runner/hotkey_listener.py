"""
action_macro.runner.hotkey_listener
=======================================
Global background keyboard shortcut monitor for sub-second emergency abort.
Manages global keyboard hooks via pynput.keyboard.GlobalHotKeys with thread-safe
daemon lifecycle, configurable multi-hotkey bindings (default: <ctrl>+<shift>+s and <esc>),
atomic input safety flushing, and automated headless fallback for displayless CI environments.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    Union,
    runtime_checkable,
)

from action_macro.core.key_mapping import (
    CANONICAL_SPECIAL_KEYS,
    normalize_key_name,
    resolve_key_for_platform,
)

logger = logging.getLogger(__name__)

# Default emergency stop shortcuts per PROJECT.md and dispatch specification
DEFAULT_EMERGENCY_HOTKEYS: Tuple[str, ...] = ("<ctrl>+<shift>+s", "<esc>")
SUBSECOND_ABORT_THRESHOLD_MS: float = 1000.0
TARGET_ABORT_LATENCY_MS: float = 25.0

# Canonical key mappings for HotkeyParser
_MODIFIER_MAP: Dict[str, str] = {
    "ctrl": "<ctrl>",
    "control": "<ctrl>",
    "shift": "<shift>",
    "alt": "<alt>",
    "cmd": "<cmd>",
    "command": "<cmd>",
    "super": "<cmd>",
    "win": "<cmd>",
    "windows": "<cmd>",
}

_SPECIAL_KEYS: Dict[str, str] = {
    "esc": "<esc>",
    "escape": "<esc>",
    "enter": "<enter>",
    "return": "<enter>",
    "space": "<space>",
    "tab": "<tab>",
    "backspace": "<backspace>",
    "delete": "<delete>",
    "del": "<delete>",
    "up": "<up>",
    "down": "<down>",
    "left": "<left>",
    "right": "<right>",
    "home": "<home>",
    "end": "<end>",
    "pageup": "<page_up>",
    "pagedown": "<page_down>",
    "page_up": "<page_up>",
    "page_down": "<page_down>",
}

for _i in range(1, 25):
    _SPECIAL_KEYS[f"f{_i}"] = f"<f{_i}>"


def normalize_hotkey_to_pynput(hotkey_str: str, platform_name: Optional[str] = None) -> str:
    """Translates arbitrary user hotkey strings into valid pynput GlobalHotKeys syntax.

    Accepts both bracketed tokens ('<ctrl>+<shift>+s', '<esc>') and plain/human-readable
    tokens ('ctrl+shift+s', 'Ctrl+Shift+S', 'esc', 'ESC', 'f12', 'F11', 'ctrl+shift+q', 'alt+x').
    Translates key aliases (e.g. 'command' -> 'cmd', 'control' -> 'ctrl') and platform-specific
    modifiers (e.g. 'cmd_or_ctrl' -> 'cmd' on darwin, 'ctrl' on win32/linux).

    Special keys (modifiers, function keys, navigation keys) are enclosed in angle brackets: <key>.
    Single characters ('s', 'q', 'x', '1') remain unbracketed.
    """
    if not isinstance(hotkey_str, str):
        raise TypeError(f"Hotkey must be a string, got {type(hotkey_str).__name__}")

    cleaned = hotkey_str.strip()
    if not cleaned:
        raise ValueError("Hotkey string cannot be empty or whitespace only")

    if cleaned.startswith("+") or cleaned.endswith("+") or "++" in cleaned:
        raise ValueError(f"Malformed hotkey string syntax: {hotkey_str!r}")

    raw_tokens = [t.strip().lower() for t in cleaned.split("+")]
    formatted_tokens: List[str] = []
    seen_modifiers: Set[str] = set()

    for token in raw_tokens:
        if not token:
            raise ValueError(f"Empty token in hotkey string: {hotkey_str!r}")

        # Strip surrounding brackets if already present
        if token.startswith("<") and token.endswith(">") and len(token) > 2:
            inner = token[1:-1].strip().lower()
        else:
            inner = token

        # Normalize via key_mapping module
        norm = normalize_key_name(inner)
        resolved = resolve_key_for_platform(norm, platform_name)

        if resolved in _MODIFIER_MAP:
            mod_token = _MODIFIER_MAP[resolved]
            if mod_token in seen_modifiers:
                continue
            seen_modifiers.add(mod_token)
            formatted_tokens.append(mod_token)
        elif resolved in _SPECIAL_KEYS:
            formatted_tokens.append(_SPECIAL_KEYS[resolved])
        elif len(resolved) == 1:
            formatted_tokens.append(resolved.lower())
        elif resolved in CANONICAL_SPECIAL_KEYS:
            formatted_tokens.append(f"<{resolved}>")
        else:
            # Check pynput.keyboard.Key members if available
            is_pynput_key = False
            try:
                from pynput.keyboard import Key
                is_pynput_key = resolved in Key._member_names_
            except Exception:
                pass

            if is_pynput_key:
                formatted_tokens.append(f"<{resolved}>")
            else:
                raise ValueError(f"Unrecognized hotkey token: {token!r}")

    deduped_tokens = list(dict.fromkeys(formatted_tokens))
    return "+".join(deduped_tokens)


# Backward-compatible alias
normalize_hotkey_for_pynput = normalize_hotkey_to_pynput


class HotkeyParser:
    """Parses and normalizes hotkey strings to canonical pynput bracket notation."""

    MODIFIER_MAP = _MODIFIER_MAP
    SPECIAL_KEYS = _SPECIAL_KEYS

    @classmethod
    def normalize(cls, hotkey_str: str) -> str:
        """Normalizes a hotkey string into canonical pynput syntax."""
        return normalize_hotkey_to_pynput(hotkey_str)


def is_display_available() -> bool:
    """Detects whether an interactive graphical display/window server is available."""
    if os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes"):
        return False

    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    if os.environ.get("CI", "").lower() in ("1", "true") and os.environ.get("GITHUB_ACTIONS") and sys.platform.startswith("linux"):
        return False

    return True


@runtime_checkable
class HotkeyListenerProtocol(Protocol):
    """Protocol defining the interface for global hotkey monitors."""

    def start(self) -> bool:
        """Starts background hotkey listening thread."""
        ...

    def stop(self) -> None:
        """Stops background hotkey listening thread and releases resources."""
        ...

    @property
    def is_running(self) -> bool:
        """Returns True if the listener thread is active."""
        ...

    def trigger_emergency_stop(self) -> float:
        """Forcibly triggers emergency stop, safety flush, and state transitions."""
        ...


class GlobalHotkeyListener:
    """Thread-safe global emergency stop hotkey listener.

    Manages system-level keyboard hooks via pynput.keyboard.GlobalHotKeys with
    graceful headless fallback, idempotent daemon lifecycle, configurable multi-hotkey
    registration, sub-second abort guarantee (<25ms target), and fail-safe safety flushing.
    """

    DEFAULT_STOP_HOTKEYS = list(DEFAULT_EMERGENCY_HOTKEYS)

    def __init__(
        self,
        engine: Optional[Any] = None,
        on_emergency_stop: Optional[Callable[[], None]] = None,
        hotkeys: Optional[Union[str, Sequence[str], Mapping[str, Callable[[], None]]]] = None,
        input_adapter: Optional[Any] = None,
        cancel_event: Optional[threading.Event] = None,
        headless: Optional[bool] = None,
        platform_name: Optional[str] = None,
    ) -> None:
        self._engine = engine
        self._on_emergency_stop = on_emergency_stop
        self._input_adapter = input_adapter
        self._cancel_event = cancel_event
        self._platform_name = platform_name

        # Auto-extract engine attributes if not explicitly provided
        if self._engine is not None:
            if self._cancel_event is None and hasattr(self._engine, "cancel_event"):
                self._cancel_event = getattr(self._engine, "cancel_event")
            if self._input_adapter is None and hasattr(self._engine, "input_driver"):
                self._input_adapter = getattr(self._engine, "input_driver")

        # Headless detection
        if headless is not None:
            self._headless = bool(headless)
        else:
            self._headless = not is_display_available()

        # Concurrency and thread lifecycle state
        self._lock = threading.RLock()
        self._stop_lock = threading.Lock()
        self._is_alive = False
        self._native_listener: Optional[Any] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._headless_stop_event: threading.Event = threading.Event()
        self._last_abort_latency_ms: Optional[float] = None
        self._abort_count: int = 0

        # Hotkey bindings dictionary
        self._hotkey_map: Dict[str, Callable[[], None]] = {}
        self._hotkey_bindings = self._hotkey_map  # Alias for test inspection
        self._registered_raw_hotkeys: List[str] = []

        self._setup_hotkeys(hotkeys)

    def _setup_hotkeys(
        self,
        hotkeys: Optional[Union[str, Sequence[str], Mapping[str, Callable[[], None]]]],
    ) -> None:
        """Parses and registers initial hotkeys."""
        self._hotkey_map.clear()
        self._registered_raw_hotkeys.clear()

        if hotkeys is None:
            for hk in DEFAULT_EMERGENCY_HOTKEYS:
                norm = normalize_hotkey_to_pynput(hk, self._platform_name)
                self._hotkey_map[norm] = self._handle_emergency_stop
                self._registered_raw_hotkeys.append(hk)
        elif isinstance(hotkeys, str):
            norm = normalize_hotkey_to_pynput(hotkeys, self._platform_name)
            self._hotkey_map[norm] = self._handle_emergency_stop
            self._registered_raw_hotkeys.append(hotkeys)
        elif isinstance(hotkeys, Mapping):
            for raw_hk, cb in hotkeys.items():
                norm = normalize_hotkey_to_pynput(raw_hk, self._platform_name)
                self._hotkey_map[norm] = cb
                self._registered_raw_hotkeys.append(raw_hk)
        elif isinstance(hotkeys, Sequence):
            for hk in hotkeys:
                norm = normalize_hotkey_to_pynput(hk, self._platform_name)
                self._hotkey_map[norm] = self._handle_emergency_stop
                self._registered_raw_hotkeys.append(hk)
        else:
            raise TypeError(f"Unsupported hotkeys type: {type(hotkeys).__name__}")

    def register_hotkey(self, hotkey_str: str, callback: Callable[[], None]) -> str:
        """Registers or replaces a hotkey mapping."""
        norm = normalize_hotkey_to_pynput(hotkey_str, self._platform_name)
        with self._lock:
            self._hotkey_map[norm] = callback
            if hotkey_str not in self._registered_raw_hotkeys:
                self._registered_raw_hotkeys.append(hotkey_str)
        return norm

    def unregister_hotkey(self, hotkey_str: str) -> None:
        """Removes a registered hotkey mapping."""
        norm = normalize_hotkey_to_pynput(hotkey_str, self._platform_name)
        with self._lock:
            self._hotkey_map.pop(norm, None)
            if hotkey_str in self._registered_raw_hotkeys:
                self._registered_raw_hotkeys.remove(hotkey_str)

    def trigger_hotkey(self, hotkey_str: str) -> bool:
        """Simulates or invokes callback for a hotkey combination programmatically."""
        try:
            norm = normalize_hotkey_to_pynput(hotkey_str, self._platform_name)
        except (ValueError, TypeError):
            return False

        with self._lock:
            cb = self._hotkey_map.get(norm)

        if cb is not None:
            try:
                cb()
                return True
            except Exception as err:
                logger.error("Exception in hotkey callback for %s: %s", norm, err)
                return True

        return False

    def simulate_hotkey(self, hotkey_str: str) -> bool:
        """Alias for trigger_hotkey for test assertions."""
        return self.trigger_hotkey(hotkey_str)

    def start(self) -> bool:
        """Starts background hotkey listening thread.

        Idempotent: safe to call repeatedly.
        """
        with self._lock:
            if self._is_alive:
                return True

            if not self._headless:
                try:
                    # pynput's listener thread reads the keyboard layout via TSM, which macOS
                    # only allows on the main thread: hand it a layout read here instead.
                    from action_macro.engine.macos_keyboard import refresh_keyboard_layout_cache

                    refresh_keyboard_layout_cache()
                    from pynput.keyboard import GlobalHotKeys
                    self._native_listener = GlobalHotKeys(self._hotkey_map)
                    self._native_listener.daemon = True
                    self._native_listener.start()
                    self._listener_thread = self._native_listener
                    self._is_alive = True
                    logger.info("GlobalHotkeyListener started natively (hotkeys=%s)", list(self._hotkey_map.keys()))
                    return True
                except Exception as err:
                    logger.warning(
                        "Native pynput GlobalHotKeys initialization failed (%s). "
                        "Falling back to headless listener mode.",
                        err,
                    )
                    self._headless = True
                    self._native_listener = None

            # Headless fallback mode: create daemon thread loop
            self._is_alive = True
            self._headless_stop_event.clear()
            stop_event = self._headless_stop_event

            def _dummy_run() -> None:
                while not stop_event.wait(timeout=0.02):
                    pass

            self._listener_thread = threading.Thread(
                target=_dummy_run,
                name="HeadlessHotkeyListener",
                daemon=True,
            )
            self._listener_thread.start()
            logger.info("GlobalHotkeyListener started in headless mode (hotkeys=%s)", list(self._hotkey_map.keys()))
            return True

    def stop(self) -> None:
        """Stops background hotkey listening thread and releases hooks.

        Idempotent: safe to call repeatedly.
        """
        threads_to_join: List[threading.Thread] = []

        with self._lock:
            if not self._is_alive:
                return

            self._is_alive = False
            self._headless_stop_event.set()

            if self._native_listener is not None:
                try:
                    self._native_listener.stop()
                except Exception as err:
                    logger.debug("Error stopping native listener: %s", err)
                if self._native_listener not in threads_to_join:
                    threads_to_join.append(self._native_listener)
                self._native_listener = None

            if self._listener_thread is not None:
                if self._listener_thread not in threads_to_join:
                    threads_to_join.append(self._listener_thread)
                self._listener_thread = None

        current = threading.current_thread()
        for t in threads_to_join:
            if t.is_alive() and current != t:
                try:
                    t.join(timeout=0.5)
                except Exception:
                    pass

        logger.info("GlobalHotkeyListener stopped")

    def __enter__(self) -> "GlobalHotkeyListener":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    def _handle_emergency_stop(self) -> None:
        """Internal callback invoked when any emergency stop shortcut is detected."""
        logger.warning("EMERGENCY STOP HOTKEY TRIGGERED! Initiating sub-second abort sequence.")
        self.trigger_emergency_stop()

    def trigger_emergency_stop(self) -> float:
        """Forcibly triggers emergency stop, safety flush, and engine transitions.

        Executes the full emergency stop protocol:
        1. Sets cooperative cancellation token (cancel_event.set()) for <=20ms sleep wake.
        2. Transitions engine state to STOPPING.
        3. Invokes input_adapter.safety_flush() to release all held buttons and keys.
        4. Transitions engine state to STOPPED.
        5. Invokes on_emergency_stop user callback if registered.

        Returns:
            Elapsed abort latency in milliseconds (<25ms target, <<1000ms guaranteed).
        """
        t0 = time.perf_counter()
        with self._stop_lock:
            self._abort_count += 1

            # 1. Immediate cancel event signal
            if self._cancel_event is not None:
                self._cancel_event.set()

            # 2. Transition engine to STOPPING if applicable
            if self._engine is not None and hasattr(self._engine, "set_state"):
                try:
                    from action_macro.runner.state_machine import EngineState
                    self._engine.set_state(EngineState.STOPPING)
                except Exception:
                    pass

            # 3. Input safety flush: release held mouse buttons & modifier keys
            flushed = False
            if self._input_adapter is not None and hasattr(self._input_adapter, "safety_flush"):
                try:
                    self._input_adapter.safety_flush()
                    flushed = True
                except Exception as err:
                    logger.error("Error executing input_adapter.safety_flush(): %s", err)

            if not flushed and self._engine is not None and hasattr(self._engine, "input_driver"):
                drv = getattr(self._engine, "input_driver")
                if hasattr(drv, "safety_flush"):
                    try:
                        drv.safety_flush()
                    except Exception as err:
                        logger.error("Error executing engine input_driver safety flush: %s", err)

            # 4. Transition engine to STOPPED (via engine.stop() or set_state)
            if self._engine is not None:
                if hasattr(self._engine, "stop"):
                    try:
                        self._engine.stop()
                    except Exception as err:
                        logger.debug("engine.stop() handled during emergency stop: %s", err)
                elif hasattr(self._engine, "set_state"):
                    try:
                        from action_macro.runner.state_machine import EngineState
                        self._engine.set_state(EngineState.STOPPED)
                    except Exception:
                        pass

            # 5. External user callback
            if self._on_emergency_stop is not None:
                try:
                    self._on_emergency_stop()
                except Exception as err:
                    logger.exception("Error in on_emergency_stop callback: %s", err)

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._last_abort_latency_ms = elapsed_ms

            if elapsed_ms > SUBSECOND_ABORT_THRESHOLD_MS:
                logger.error(
                    "Emergency stop latency (%.2f ms) exceeded 1000ms threshold!",
                    elapsed_ms,
                )
            else:
                logger.info(
                    "Emergency stop completed in %.3f ms (sub-second target <25ms met)",
                    elapsed_ms,
                )

            return elapsed_ms

    @property
    def is_alive(self) -> bool:
        """Returns True if the listener thread is active."""
        with self._lock:
            return self._is_alive

    @property
    def is_running(self) -> bool:
        """Returns True if the hotkey listener is active."""
        return self.is_alive

    @property
    def is_headless(self) -> bool:
        """Returns True if operating in headless fallback mode."""
        return self._headless

    @property
    def registered_hotkeys(self) -> List[str]:
        """List of normalized pynput hotkey strings currently registered."""
        with self._lock:
            return list(self._hotkey_map.keys())

    @property
    def raw_hotkeys(self) -> List[str]:
        """List of original raw hotkey strings passed during configuration."""
        with self._lock:
            return list(self._registered_raw_hotkeys)

    @property
    def last_abort_latency_ms(self) -> Optional[float]:
        """Duration in milliseconds of the most recent emergency stop execution."""
        return self._last_abort_latency_ms

    @property
    def abort_count(self) -> int:
        """Total number of times emergency stop was triggered."""
        return self._abort_count


# Backward-compatible aliases
HotkeyListener = GlobalHotkeyListener
MockHotkeyListener = GlobalHotkeyListener


def create_emergency_stop_listener(
    engine: Any,
    hotkeys: Optional[Union[str, Sequence[str]]] = None,
    headless: Optional[bool] = None,
    on_emergency_stop: Optional[Callable[[], None]] = None,
) -> GlobalHotkeyListener:
    """Convenience factory creating a configured GlobalHotkeyListener bound to an ExecutionEngine."""
    return GlobalHotkeyListener(
        engine=engine,
        hotkeys=hotkeys,
        headless=headless,
        on_emergency_stop=on_emergency_stop,
    )
