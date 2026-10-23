"""
action_macro.runner.execution_engine
========================================
Main macro execution controller managing repeat loops, step-by-step
action dispatching, pause/resume/stop lifecycle, and cross-layer events.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    ImageWaitParams,
    ScenarioModel,
)
from action_macro.engine.wait_system import WaitSystem
from action_macro.runner.state_machine import EngineState, StateMachine

logger = logging.getLogger(__name__)

# Check PySide6 availability for Qt signal integration
try:
    from PySide6.QtCore import QObject, Signal
    _HAS_QT = True
except (ImportError, Exception):
    _HAS_QT = False

    class QObject:  # type: ignore
        pass

    def Signal(*args: Any, **kwargs: Any) -> Any:  # type: ignore
        return None


# -----------------------------------------------------------------------------
# Signal and Event Broadcasting System
# -----------------------------------------------------------------------------

class ExecutionSignals(QObject):
    """Dual-mode signal broker: emits PySide6 Qt signals if active,
    and maintains historical lists for headless CI/CD assertion testing.
    """

    if _HAS_QT:
        state_changed = Signal(str)
        progress_updated = Signal(int, int, int, int)
        action_started = Signal(int, str)
        action_completed = Signal(int, bool, str)
        log_emitted = Signal(str, str)
        execution_finished = Signal(bool, str)

    def __init__(self) -> None:
        if _HAS_QT:
            super().__init__()
        self.state_changes: List[str] = []
        self.progress_updates: List[Tuple[int, int, int, int]] = []
        self.logs: List[Tuple[str, str]] = []
        self.started_actions: List[Tuple[int, str]] = []
        self.completed_actions: List[Tuple[int, bool, str]] = []
        self.finished: List[Tuple[bool, str]] = []
        self._callbacks: Dict[str, List[Callable[..., None]]] = {
            "state_changed": [],
            "progress_updated": [],
            "action_started": [],
            "action_completed": [],
            "log_emitted": [],
            "execution_finished": [],
        }

    def emit_state_changed(self, state: str) -> None:
        """Broadcasts engine state change."""
        self.state_changes.append(state)
        if _HAS_QT:
            try:
                self.state_changed.emit(state)
            except Exception:
                pass
        for cb in list(self._callbacks["state_changed"]):
            try:
                cb(state)
            except Exception as err:
                logger.debug("Error in state_changed callback: %s", err)

    def emit_progress(self, cur_loop: int, tot_loops: int, cur_act: int, tot_acts: int) -> None:
        """Broadcasts loop and action progress."""
        self.progress_updates.append((cur_loop, tot_loops, cur_act, tot_acts))
        if _HAS_QT:
            try:
                self.progress_updated.emit(cur_loop, tot_loops, cur_act, tot_acts)
            except Exception:
                pass
        for cb in list(self._callbacks["progress_updated"]):
            try:
                cb(cur_loop, tot_loops, cur_act, tot_acts)
            except Exception as err:
                logger.debug("Error in progress_updated callback: %s", err)

    def emit_log(self, level: str, msg: str) -> None:
        """Broadcasts execution log messages."""
        self.logs.append((level, msg))
        if _HAS_QT:
            try:
                self.log_emitted.emit(level, msg)
            except Exception:
                pass
        for cb in list(self._callbacks["log_emitted"]):
            try:
                cb(level, msg)
            except Exception as err:
                logger.debug("Error in log_emitted callback: %s", err)

    def emit_action_started(self, idx: int, desc: str) -> None:
        """Broadcasts action step start."""
        self.started_actions.append((idx, desc))
        if _HAS_QT:
            try:
                self.action_started.emit(idx, desc)
            except Exception:
                pass
        for cb in list(self._callbacks["action_started"]):
            try:
                cb(idx, desc)
            except Exception as err:
                logger.debug("Error in action_started callback: %s", err)

    def emit_action_completed(self, idx: int, success: bool, msg: str) -> None:
        """Broadcasts action step completion."""
        self.completed_actions.append((idx, success, msg))
        if _HAS_QT:
            try:
                self.action_completed.emit(idx, success, msg)
            except Exception:
                pass
        for cb in list(self._callbacks["action_completed"]):
            try:
                cb(idx, success, msg)
            except Exception as err:
                logger.debug("Error in action_completed callback: %s", err)

    def emit_finished(self, success: bool, summary: str) -> None:
        """Broadcasts scenario execution completion or termination."""
        self.finished.append((success, summary))
        if _HAS_QT:
            try:
                self.execution_finished.emit(success, summary)
            except Exception:
                pass
        for cb in list(self._callbacks["execution_finished"]):
            try:
                cb(success, summary)
            except Exception as err:
                logger.debug("Error in execution_finished callback: %s", err)

    def connect_callback(self, event_name: str, callback: Callable[..., None]) -> None:
        """Connects a Python function callback to a specific signal event."""
        if event_name in self._callbacks and callback not in self._callbacks[event_name]:
            self._callbacks[event_name].append(callback)


# Alias for headless test compatibility
SimpleSignals = ExecutionSignals


# -----------------------------------------------------------------------------
# Execution Engine Implementation
# -----------------------------------------------------------------------------

class ExecutionEngine:
    """Deterministic Macro Execution Engine adhering to PROJECT.md specifications.

    Features:
    - Multi-loop execution (fixed N iterations or infinite loop)
    - Action step executor for MOUSE_CLICK, KEY_INPUT, FIXED_WAIT, IMAGE_WAIT
    - Dynamic coordinate binding from preceding template matching
    - Cooperative pause/resume and sub-50ms emergency abort
    - OS input safety flush guaranteeing zero stuck modifier keys
    """

    def __init__(
        self,
        scenario: ScenarioModel,
        input_driver: Optional[Any] = None,
        screen_driver: Optional[Any] = None,
        base_dir: Optional[Union[str, Path]] = None,
        action_delay_ms: Optional[int] = None,
        loop_delay_ms: Optional[int] = None,
        on_state_change: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int, int, int, int], None]] = None,
        on_action_start: Optional[Callable[[int, str], None]] = None,
        on_action_finish: Optional[Callable[[int, bool, str], None]] = None,
        on_log: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.scenario = scenario
        self.input_driver = input_driver
        self.screen_driver = screen_driver
        self.base_dir = Path(base_dir).resolve() if base_dir else None

        # Inter-action and inter-loop delays
        self.action_delay_ms = (
            action_delay_ms
            if action_delay_ms is not None
            else getattr(scenario, "action_delay_ms", 0)
        )
        self.loop_delay_ms = (
            loop_delay_ms
            if loop_delay_ms is not None
            else getattr(scenario, "loop_delay_ms", 0)
        )

        # State machine and synchronization primitives
        self.state_machine = StateMachine(initial_state=EngineState.IDLE)
        self.signals = ExecutionSignals()
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self._thread_lock = threading.RLock()
        self._worker_thread: Optional[threading.Thread] = None

        # Live telemetry
        self.current_loop: int = 0
        self.current_action_idx: int = 0
        self._last_detected_coords: Optional[Tuple[int, int]] = None

        # Wire StateMachine transitions to signals
        self.state_machine.add_callback(self._handle_state_machine_transition)

        # Connect optional functional callbacks
        if on_state_change:
            self.signals.connect_callback("state_changed", on_state_change)
        if on_progress:
            self.signals.connect_callback("progress_updated", on_progress)
        if on_action_start:
            self.signals.connect_callback("action_started", on_action_start)
        if on_action_finish:
            self.signals.connect_callback("action_completed", on_action_finish)
        if on_log:
            self.signals.connect_callback("log_emitted", on_log)

    def _handle_state_machine_transition(self, old_state: EngineState, new_state: EngineState) -> None:
        """Propagates state machine transition into signals broker."""
        self.signals.emit_state_changed(new_state.value)

    @property
    def state(self) -> EngineState:
        """Returns the current engine state."""
        return self.state_machine.state

    @state.setter
    def state(self, value: Union[EngineState, str]) -> None:
        """Setter allowing direct state assignments compatible with test mocks."""
        self.set_state(value)

    def set_state(self, new_state: Union[EngineState, str]) -> None:
        """Transitions state machine to new state."""
        target = EngineState.from_str(new_state)
        with self._thread_lock:
            if self.state_machine.can_transition(target):
                self.state_machine.transition(target)
            else:
                self.state_machine.transition(target, force=True)

    def is_running(self) -> bool:
        """Returns True if the engine is actively running."""
        return self.state_machine.is_running

    def is_paused(self) -> bool:
        """Returns True if the engine is paused."""
        return self.state_machine.is_paused

    def is_stopped(self) -> bool:
        """Returns True if the engine is stopped."""
        return self.state_machine.is_stopped

    def is_idle(self) -> bool:
        """Returns True if the engine is idle."""
        return self.state_machine.is_idle

    # -------------------------------------------------------------------------
    # Execution Control (Run, Pause, Resume, Stop)
    # -------------------------------------------------------------------------

    def stop(self) -> None:
        """Triggers emergency stop interrupt and input safety flush."""
        with self._thread_lock:
            # If already stopped, ensure safety flush still executes (idempotent T1.7.3)
            if self.state_machine.is_stopped:
                self._safe_flush_inputs()
                return

            if self.state_machine.can_transition(EngineState.STOPPING):
                self.set_state(EngineState.STOPPING)

            # Signal cancellation to wake up sliced waits and image polling loops
            self.cancel_event.set()

            # Execute safety flush immediately
            self._safe_flush_inputs()

            if self.state_machine.can_transition(EngineState.STOPPED):
                self.set_state(EngineState.STOPPED)
            else:
                self.state_machine.transition(EngineState.STOPPED, force=True)

    def pause(self) -> None:
        """Cooperatively suspends execution between actions."""
        with self._thread_lock:
            if not self.state_machine.is_running:
                # If already paused, idempotent safe no-op
                if self.state_machine.is_paused:
                    return
                # Allow pausing from idle for pre-configuration
                self.pause_event.set()
                self.set_state(EngineState.PAUSED)
                return

            self.pause_event.set()
            self.set_state(EngineState.PAUSED)

    def resume(self) -> None:
        """Resumes suspended execution."""
        with self._thread_lock:
            if not self.state_machine.is_paused:
                return
            self.pause_event.clear()
            self.set_state(EngineState.RUNNING)

    def start(self, async_exec: bool = False, on_finished: Optional[Callable[[bool, str], None]] = None) -> Union[bool, threading.Thread]:
        """Starts scenario execution.

        Args:
            async_exec: If True, executes asynchronously in a background thread.
            on_finished: Optional callback invoked when background execution finishes.

        Returns:
            Boolean completion status if synchronous, or Thread instance if async.
        """
        if async_exec:
            return self.start_async(on_finished=on_finished)
        return self.run()

    def start_async(
        self,
        on_finished: Optional[Callable[[bool, str], None]] = None,
    ) -> threading.Thread:
        """Starts scenario execution asynchronously in a background worker thread."""
        with self._thread_lock:
            if self.state_machine.is_running:
                raise RuntimeError("ExecutionEngine is already running")

            self.cancel_event.clear()
            self.pause_event.clear()

            def _thread_target() -> None:
                try:
                    res = self.run()
                    if on_finished:
                        on_finished(res, "Success" if res else "Aborted")
                except Exception as exc:
                    if on_finished:
                        on_finished(False, str(exc))

            self._worker_thread = threading.Thread(
                target=_thread_target,
                name="ExecutionEngineWorker",
                daemon=True,
            )
            self._worker_thread.start()
            return self._worker_thread

    def wait_for_completion(self, timeout: Optional[float] = None) -> bool:
        """Blocks until background worker thread terminates.

        Returns:
            True if worker thread exited, False if timeout elapsed.
        """
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
            return not self._worker_thread.is_alive()
        return True

    # -------------------------------------------------------------------------
    # Main Execution Loop
    # -------------------------------------------------------------------------

    def run(self) -> bool:
        """Executes the macro scenario loop synchronously.

        Returns:
            True if scenario completed all requested iterations normally.
            False if interrupted by emergency stop.
        """
        # Boundary Check: Immediate abort at t0 before starting
        if self.cancel_event.is_set():
            self.set_state(EngineState.STOPPED)
            self._safe_flush_inputs()
            self.signals.emit_finished(False, "Cancelled before execution")
            return False

        self.set_state(EngineState.RUNNING)
        total_actions = len(self.scenario.actions)
        loop_target = self.scenario.loop_count  # 0 indicates infinite

        # Boundary Check: Empty scenario
        if total_actions == 0:
            self.set_state(EngineState.IDLE)
            self.signals.emit_finished(True, "Empty scenario completed")
            return True

        loop = 0
        try:
            while True:
                if self.cancel_event.is_set():
                    break

                loop += 1
                self.current_loop = loop

                for idx, action in enumerate(self.scenario.actions):
                    if self.cancel_event.is_set():
                        break

                    # Handle pause state cooperatively
                    while self.pause_event.is_set() and not self.cancel_event.is_set():
                        time.sleep(0.01)

                    if self.cancel_event.is_set():
                        break

                    self.current_action_idx = idx
                    self.signals.emit_progress(loop, loop_target, idx + 1, total_actions)

                    # Check action enablement
                    if not action.enabled:
                        self.signals.emit_log("INFO", f"Action {idx} ({action.type}) skipped (disabled)")
                        continue

                    action_desc = action.description or str(getattr(action.type, "value", action.type))
                    self.signals.emit_action_started(idx, action_desc)

                    success = self._execute_action(action)
                    self.signals.emit_action_completed(idx, success, "OK" if success else "Failed")

                    if self.cancel_event.is_set():
                        break

                    # Inter-action delay between actions within same loop
                    if idx < total_actions - 1 and self.action_delay_ms > 0:
                        normal = WaitSystem.wait(
                            self.action_delay_ms / 1000.0,
                            cancel_event=self.cancel_event,
                            pause_event=self.pause_event,
                        )
                        if not normal:
                            break

                if self.cancel_event.is_set():
                    break

                # Target repetitions reached
                if loop_target > 0 and loop >= loop_target:
                    break

                # Inter-loop delay between iterations
                if self.loop_delay_ms > 0:
                    normal = WaitSystem.wait(
                        self.loop_delay_ms / 1000.0,
                        cancel_event=self.cancel_event,
                        pause_event=self.pause_event,
                    )
                    if not normal:
                        break

        except Exception as err:
            self.set_state(EngineState.ERROR)
            self.signals.emit_log("ERROR", f"Execution failed: {err}")
            self._safe_flush_inputs()
            self.signals.emit_finished(False, f"Error: {err}")
            raise

        was_cancelled = self.cancel_event.is_set()
        self._safe_flush_inputs()

        if was_cancelled:
            self.set_state(EngineState.STOPPED)
            self.signals.emit_finished(False, "Cancelled by user")
            return False

        self.set_state(EngineState.IDLE)
        self.signals.emit_finished(True, f"Completed {loop} loops")
        return True

    # -------------------------------------------------------------------------
    # Action Step Dispatcher
    # -------------------------------------------------------------------------

    def _execute_action(self, action: ActionBlock) -> bool:
        """Executes a single action block through the appropriate driver adapter."""
        act_type = action.type if isinstance(action.type, ActionType) else ActionType(action.type)

        if isinstance(action.params, dict):
            p = action.params
        elif is_dataclass(action.params):
            p = asdict(action.params)
        elif hasattr(action.params, "to_dict"):
            p = action.params.to_dict()
        else:
            p = getattr(action.params, "__dict__", {})

        if act_type == ActionType.MOUSE_CLICK:
            x = p.get("x", 0)
            y = p.get("y", 0)

            # Dynamic coordinate routing from prior image match
            if p.get("use_last_matched", False) and self._last_detected_coords is not None:
                x, y = self._last_detected_coords

            button = p.get("button", "left")
            clicks = p.get("clicks", p.get("count", 1))
            interval_ms = p.get("interval_ms", 0)

            press_duration = p.get("press_duration_ms")
            if press_duration is not None:
                press_duration_sec = float(press_duration) / 1000.0
            else:
                press_duration_sec = float(p.get("press_duration", 0.05))

            if self.input_driver is None:
                from action_macro.engine.input_adapter import PynputAdapter
                self.input_driver = PynputAdapter()

            self.input_driver.click(
                x=x,
                y=y,
                button=button,
                count=clicks,
                interval_ms=interval_ms,
                press_duration=press_duration_sec,
            )
            return True

        elif act_type == ActionType.KEY_INPUT:
            keys = p.get("keys", [])
            if isinstance(keys, str):
                keys = [keys]
            act = p.get("action", "press").lower()
            modifiers = p.get("modifiers")

            duration = p.get("press_duration_ms")
            if duration is not None:
                duration_sec = float(duration) / 1000.0
            else:
                duration_sec = float(p.get("duration", 0.05))

            if self.input_driver is None:
                from action_macro.engine.input_adapter import PynputAdapter
                self.input_driver = PynputAdapter()

            if act == "press":
                for k in keys:
                    self.input_driver.press_key(k, modifiers=modifiers, duration=duration_sec)
            elif act == "down":
                for k in keys:
                    self.input_driver.key_down(k)
            elif act == "up":
                for k in keys:
                    if hasattr(self.input_driver, "key_up"):
                        self.input_driver.key_up(k)
                    elif hasattr(self.input_driver, "release_key"):
                        self.input_driver.release_key(k)
            elif act == "hotkey":
                self.input_driver.hotkey(*keys)
            elif act == "type":
                text = p.get("text", "")
                if hasattr(self.input_driver, "type_text"):
                    self.input_driver.type_text(text)
            return True

        elif act_type == ActionType.FIXED_WAIT:
            duration_ms = p.get("duration_ms", 0)
            duration_s = max(0.0, float(duration_ms)) / 1000.0
            return WaitSystem.wait(duration_s, cancel_event=self.cancel_event, pause_event=self.pause_event)

        elif act_type == ActionType.IMAGE_WAIT:
            if not self.screen_driver:
                raise ValueError("Screen driver required for ImageWait action")

            iwp = ImageWaitParams(
                template_path=p.get("template_path", ""),
                mode=p.get("mode", "appear"),
                confidence=p.get("confidence", 0.8),
                timeout_seconds=p.get("timeout_seconds", 10.0),
                roi=p.get("roi"),
                timeout_action=p.get("timeout_action", "raise"),
                poll_interval_seconds=p.get("poll_interval_seconds", 0.05),
            )
            matched, coords = WaitSystem.wait_image(
                self.screen_driver,
                iwp,
                cancel_event=self.cancel_event,
                base_dir=self.base_dir,
                pause_event=self.pause_event,
            )
            if matched and coords:
                self._last_detected_coords = coords
            return matched

        return False

    def _safe_flush_inputs(self) -> None:
        """Releases all active mouse and keyboard hold states safely."""
        if self.input_driver is not None and hasattr(self.input_driver, "safety_flush"):
            try:
                self.input_driver.safety_flush()
            except Exception as err:
                logger.warning("Error during safety_flush: %s", err)
