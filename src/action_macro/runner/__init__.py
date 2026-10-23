"""
action_macro.runner
=======================
Execution orchestration layer containing state machine, loop controller,
and emergency hotkey monitoring.
"""

from action_macro.runner.execution_engine import (
    ExecutionEngine,
    ExecutionSignals,
    SimpleSignals,
)
from action_macro.runner.hotkey_listener import (
    DEFAULT_EMERGENCY_HOTKEYS,
    GlobalHotkeyListener,
    HotkeyListener,
    HotkeyParser,
    MockHotkeyListener,
    create_emergency_stop_listener,
    normalize_hotkey_for_pynput,
    normalize_hotkey_to_pynput,
)
from action_macro.runner.state_machine import (
    EngineState,
    InvalidStateTransitionError,
    StateMachine,
)

__all__ = [
    "DEFAULT_EMERGENCY_HOTKEYS",
    "EngineState",
    "ExecutionEngine",
    "ExecutionSignals",
    "GlobalHotkeyListener",
    "HotkeyListener",
    "HotkeyParser",
    "InvalidStateTransitionError",
    "MockHotkeyListener",
    "SimpleSignals",
    "StateMachine",
    "create_emergency_stop_listener",
    "normalize_hotkey_for_pynput",
    "normalize_hotkey_to_pynput",
]
