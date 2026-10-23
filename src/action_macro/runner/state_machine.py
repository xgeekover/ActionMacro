"""
action_macro.runner.state_machine
====================================
Thread-safe execution state machine with transition validation,
reentrant locking, enter/exit hooks, and isolated callback broadcasting.
"""

from __future__ import annotations

import inspect
import logging
import threading
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)


class EngineState(str, Enum):
    """Execution engine state enumeration."""

    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"

    @classmethod
    def from_str(cls, value: Union[str, EngineState]) -> EngineState:
        """Parses state string in a case-insensitive manner.

        Args:
            value: EngineState instance or state string (e.g. 'idle', 'RUNNING').

        Returns:
            Matching EngineState member.

        Raises:
            TypeError: If value is neither a str nor EngineState.
            ValueError: If value is not a recognized EngineState.
        """
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise TypeError(f"Expected str or EngineState, got {type(value).__name__}")

        val_upper = value.strip().upper()
        for member in cls:
            if member.value == val_upper or member.name == val_upper:
                return member
        raise ValueError(f"Unknown EngineState: {value!r}")

    @classmethod
    def _missing_(cls, value: object) -> Optional[EngineState]:
        if isinstance(value, str):
            try:
                return cls.from_str(value)
            except (ValueError, TypeError):
                return None
        return None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other or self.value.upper() == other.strip().upper()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return str.__hash__(self.value)


class InvalidStateTransitionError(RuntimeError, ValueError):
    """Raised when an illegal state transition is attempted."""

    def __init__(
        self,
        from_state: Union[EngineState, str],
        to_state: Union[EngineState, str],
        msg: Optional[str] = None,
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.current_state = from_state
        self.target_state = to_state

        from_str = getattr(from_state, "value", str(from_state))
        to_str = getattr(to_state, "value", str(to_state))
        default_msg = f"Invalid state transition from {from_str} to {to_str}"
        super().__init__(msg or default_msg)


# Permissible transition graph (16 valid transitions)
VALID_TRANSITIONS: Dict[EngineState, Set[EngineState]] = {
    EngineState.IDLE: {
        EngineState.RUNNING,
    },
    EngineState.RUNNING: {
        EngineState.PAUSED,
        EngineState.STOPPING,
        EngineState.STOPPED,
        EngineState.IDLE,
        EngineState.ERROR,
    },
    EngineState.PAUSED: {
        EngineState.RUNNING,
        EngineState.STOPPING,
        EngineState.STOPPED,
        EngineState.ERROR,
    },
    EngineState.STOPPING: {
        EngineState.STOPPED,
        EngineState.ERROR,
    },
    EngineState.STOPPED: {
        EngineState.IDLE,
        EngineState.RUNNING,
    },
    EngineState.ERROR: {
        EngineState.IDLE,
        EngineState.RUNNING,
    },
}


class StateMachine:
    """Thread-safe state manager for the macro execution engine.

    Guarantees deterministic transitions, protects against illegal state
    jumps, and broadcasts notifications to registered listeners and hooks safely.
    """

    VALID_TRANSITIONS = VALID_TRANSITIONS

    def __init__(self, initial_state: Union[EngineState, str] = EngineState.IDLE) -> None:
        self._lock = threading.RLock()
        self._state: EngineState = self._coerce_state(initial_state)
        self._callbacks: List[Callable[..., None]] = []
        self._enter_hooks: Dict[EngineState, List[Callable[[], None]]] = {s: [] for s in EngineState}
        self._exit_hooks: Dict[EngineState, List[Callable[[], None]]] = {s: [] for s in EngineState}

    def _coerce_state(self, state: Union[EngineState, str]) -> EngineState:
        if isinstance(state, EngineState):
            return state
        return EngineState.from_str(state)

    @property
    def state(self) -> EngineState:
        """Returns the current engine state."""
        with self._lock:
            return self._state

    @property
    def is_idle(self) -> bool:
        """Returns True if engine is in IDLE state."""
        with self._lock:
            return self._state == EngineState.IDLE

    @property
    def is_running(self) -> bool:
        """Returns True if engine is actively RUNNING."""
        with self._lock:
            return self._state == EngineState.RUNNING

    @property
    def is_paused(self) -> bool:
        """Returns True if engine is PAUSED."""
        with self._lock:
            return self._state == EngineState.PAUSED

    @property
    def is_stopping(self) -> bool:
        """Returns True if engine is STOPPING."""
        with self._lock:
            return self._state == EngineState.STOPPING

    @property
    def is_stopped(self) -> bool:
        """Returns True if engine is STOPPED."""
        with self._lock:
            return self._state == EngineState.STOPPED

    @property
    def is_error(self) -> bool:
        """Returns True if engine is in ERROR state."""
        with self._lock:
            return self._state == EngineState.ERROR

    def can_transition(self, target_state: Union[EngineState, str]) -> bool:
        """Checks whether transitioning to target state is legally allowed.

        Self-transitions return False.
        """
        try:
            target = self._coerce_state(target_state)
        except (ValueError, TypeError):
            return False

        with self._lock:
            if target == self._state:
                return False
            valid_targets = self.VALID_TRANSITIONS.get(self._state, set())
            return target in valid_targets

    def transition(
        self,
        target_state: Union[EngineState, str],
        force: bool = False,
    ) -> EngineState:
        """Transitions state to target state if valid.

        Args:
            target_state: Target EngineState or state string.
            force: If True, bypasses validity checks (used only in emergency teardown/reset).

        Returns:
            The newly adopted EngineState.

        Raises:
            InvalidStateTransitionError: If transition is disallowed and force is False.
        """
        target = self._coerce_state(target_state)

        with self._lock:
            if not force and not self.can_transition(target):
                raise InvalidStateTransitionError(self._state, target)

            old_state = self._state
            self._state = target

            # Capture snapshots of callbacks and hooks to invoke outside lock
            exit_hooks = list(self._exit_hooks.get(old_state, []))
            enter_hooks = list(self._enter_hooks.get(target, []))
            callbacks = list(self._callbacks)

        # 1. Dispatch exit hooks (isolated)
        for hook in exit_hooks:
            try:
                hook()
            except Exception as err:
                logger.exception("Error in StateMachine exit hook for %s: %s", old_state, err)

        # 2. Dispatch enter hooks (isolated)
        for hook in enter_hooks:
            try:
                hook()
            except Exception as err:
                logger.exception("Error in StateMachine enter hook for %s: %s", target, err)

        # 3. Dispatch transition callbacks (isolated)
        for cb in callbacks:
            try:
                sig = inspect.signature(cb)
                param_count = len(sig.parameters)
                if param_count >= 2:
                    cb(old_state, target)
                elif param_count == 1:
                    cb(target)
                else:
                    cb()
            except Exception as err:
                logger.exception("Error in StateMachine callback %r: %s", cb, err)

        return self._state

    def transition_to(
        self,
        target_state: Union[EngineState, str],
        force: bool = False,
    ) -> bool:
        """Transitions state to target state if valid.

        Returns:
            True if state transitioned, False if already in target state.
        """
        target = self._coerce_state(target_state)
        with self._lock:
            if not force and target == self._state:
                return False

        self.transition(target, force=force)
        return True

    def try_transition(self, target_state: Union[EngineState, str]) -> bool:
        """Attempts transition to target state without raising exceptions.

        Returns:
            True if transition succeeded, False otherwise.
        """
        try:
            self.transition(target_state)
            return True
        except (InvalidStateTransitionError, ValueError, TypeError):
            return False

    def reset(self) -> None:
        """Resets state machine back to IDLE state."""
        with self._lock:
            if self._state != EngineState.IDLE:
                self.transition(EngineState.IDLE, force=True)

    def add_callback(self, callback: Callable[..., None]) -> Callable[[], None]:
        """Registers a state change transition callback.

        Args:
            callback: Callable accepting (old_state, new_state), (new_state), or ().

        Returns:
            A zero-argument unsubscribe function.
        """
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

        def unsubscribe() -> None:
            self.remove_callback(callback)

        return unsubscribe

    def add_listener(self, callback: Callable[..., None]) -> Callable[[], None]:
        """Alias for add_callback."""
        return self.add_callback(callback)

    def remove_callback(self, callback: Callable[..., None]) -> None:
        """Removes a registered state change callback."""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def remove_listener(self, callback: Callable[..., None]) -> None:
        """Alias for remove_callback."""
        self.remove_callback(callback)

    def on_enter(self, state: Union[EngineState, str], hook: Callable[[], None]) -> None:
        """Registers a hook invoked when entering the specified state."""
        target = self._coerce_state(state)
        with self._lock:
            self._enter_hooks[target].append(hook)

    def on_exit(self, state: Union[EngineState, str], hook: Callable[[], None]) -> None:
        """Registers a hook invoked when exiting the specified state."""
        target = self._coerce_state(state)
        with self._lock:
            self._exit_hooks[target].append(hook)
