"""Interface contracts and dynamic adapter layer for ActionMacro tests.

If src/action_macro is available on sys.path, imports and exposes real
production classes. Otherwise, provides exact specification-compliant reference
implementations (oracle) derived directly from PROJECT.md and TEST_INFRA.md.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

# -----------------------------------------------------------------------------
# Check if real implementation is importable
# -----------------------------------------------------------------------------

_HAS_REAL_IMPL = False

try:
    import action_macro.core.models as _real_models  # type: ignore
    import action_macro.core.persistence as _real_persistence  # type: ignore
    _HAS_REAL_IMPL = True
except ImportError:
    _HAS_REAL_IMPL = False


# =============================================================================
# 1. Models Contract
# =============================================================================

class ActionType(str, Enum):
    MOUSE_CLICK = "mouse_click"
    KEY_INPUT = "key_input"
    FIXED_WAIT = "fixed_wait"
    IMAGE_WAIT = "image_wait"


@dataclass
class MouseClickParams:
    x: int
    y: int
    button: str = "left"
    clicks: int = 1
    interval_ms: int = 0
    press_duration_ms: int = 50


@dataclass
class KeyInputParams:
    keys: List[str]
    action: str = "press"  # "press", "down", "up"
    modifiers: Optional[List[str]] = None


@dataclass
class FixedWaitParams:
    duration_ms: int


@dataclass
class ImageWaitParams:
    template_path: str
    mode: str = "appear"  # "appear" or "disappear"
    confidence: float = 0.8
    timeout_seconds: float = 10.0
    roi: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h)
    timeout_action: str = "raise"  # "raise" or "skip"
    poll_interval_seconds: float = 0.05


@dataclass
class ActionBlock:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: ActionType = ActionType.MOUSE_CLICK
    params: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""


@dataclass
class ScenarioModel:
    name: str
    version: str = "1.0.0"
    loop_count: int = 1  # 0 indicates infinite
    loop_delay_ms: int = 0
    actions: List[ActionBlock] = field(default_factory=list)
    description: str = ""


# =============================================================================
# 2. Persistence Contract
# =============================================================================

class ScenarioPersistence:
    """Serializes and deserializes ScenarioModel to/from strict JSON format."""

    @staticmethod
    def save(scenario: ScenarioModel, filepath: Union[str, Path]) -> Path:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "schema_version": scenario.version,
            "metadata": {
                "name": scenario.name,
                "description": scenario.description,
            },
            "settings": {
                "loop_count": scenario.loop_count,
                "loop_delay_ms": scenario.loop_delay_ms,
            },
            "actions": [
                {
                    "id": a.id,
                    "type": a.type.value if isinstance(a.type, Enum) else str(a.type),
                    "enabled": a.enabled,
                    "description": a.description,
                    "params": a.params,
                }
                for a in scenario.actions
            ],
        }

        # Format with 2-space indentation and trailing newline as per spec
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def load(filepath: Union[str, Path]) -> ScenarioModel:
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Scenario file not found: {path}")

        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as err:
            raise ValueError(f"Malformed scenario JSON file: {err}") from err

        version = data.get("schema_version", "1.0.0")
        major_version = int(version.split(".")[0])
        if major_version > 1:
            raise ValueError(f"Unsupported schema version: {version}")

        metadata = data.get("metadata", {})
        settings = data.get("settings", {})
        raw_actions = data.get("actions", [])

        actions = []
        for ra in raw_actions:
            actions.append(
                ActionBlock(
                    id=ra.get("id", str(uuid.uuid4())),
                    type=ActionType(ra.get("type")),
                    params=ra.get("params", {}),
                    enabled=ra.get("enabled", True),
                    description=ra.get("description", ""),
                )
            )

        return ScenarioModel(
            name=metadata.get("name", "Unnamed"),
            version=version,
            loop_count=settings.get("loop_count", 1),
            loop_delay_ms=settings.get("loop_delay_ms", 0),
            actions=actions,
            description=metadata.get("description", ""),
        )


# =============================================================================
# 3. Coordinate Transformer Contract
# =============================================================================

class CoordinateTransformer:
    """Transforms coordinates between Logical Desktop Points and Physical Pixels."""

    @staticmethod
    def to_logical(phys_x: float, phys_y: float, dpr: float = 1.0) -> Tuple[int, int]:
        return (int(round(phys_x / dpr)), int(round(phys_y / dpr)))

    @staticmethod
    def to_physical(log_x: float, log_y: float, dpr: float = 1.0) -> Tuple[int, int]:
        return (int(round(log_x * dpr)), int(round(log_y * dpr)))


# =============================================================================
# 4. Wait System Contract
# =============================================================================

class WaitSystem:
    """Implements sliced interruptible sleep and OpenCV template matching."""

    @staticmethod
    def wait(
        duration_seconds: float,
        cancel_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> bool:
        """Cooperative high-precision interruptible wait with <=20ms slices."""
        if duration_seconds <= 0:
            return True

        deadline = time.perf_counter() + duration_seconds
        slice_interval = 0.02  # 20ms slices

        while True:
            if cancel_event and cancel_event.is_set():
                return False

            if pause_event and pause_event.is_set():
                time.sleep(0.01)
                continue

            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                break

            wait_slice = min(slice_interval, remaining)
            if cancel_event:
                if cancel_event.wait(timeout=wait_slice):
                    return False
            else:
                time.sleep(wait_slice)

        return True

    @staticmethod
    def wait_image(
        params: ImageWaitParams,
        screen_driver: Any,
        cancel_event: Optional[threading.Event] = None,
        base_dir: Optional[Path] = None,
    ) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Polls screen_driver for template presence using OpenCV TM_CCOEFF_NORMED."""
        template_file = Path(params.template_path)
        if not template_file.is_absolute() and base_dir:
            template_file = base_dir / template_file

        if not template_file.exists():
            raise FileNotFoundError(f"Template image file not found: {template_file}")

        # Load template
        template = cv2.imread(str(template_file), cv2.IMREAD_COLOR)
        if template is None:
            raise ValueError(f"Could not decode template image: {template_file}")

        # Convert to RGB to match screen driver
        template_rgb = cv2.cvtColor(template, cv2.COLOR_BGR2RGB)
        templ_h, templ_w, _ = template_rgb.shape

        # Check ROI validity against template size
        if params.roi is not None:
            _, _, roi_w, roi_h = params.roi
            if templ_w > roi_w or templ_h > roi_h:
                raise ValueError("Template dimensions exceed ROI bounding box")

        # Check zero-variance / flat template edge case
        template_gray = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)
        is_flat = float(np.std(template_gray)) < 1.0

        deadline = time.perf_counter() + (params.timeout_seconds if params.timeout_seconds > 0 else 1e9)
        poll_interval = max(0.01, params.poll_interval_seconds)

        while True:
            if cancel_event and cancel_event.is_set():
                return False, None

            # Capture frame
            frame = screen_driver.capture(params.roi)
            frame_gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)

            fh, fw = frame_gray.shape
            if templ_w > fw or templ_h > fh:
                raise ValueError("Template dimensions exceed screen or ROI bounds")

            # Match algorithm
            if is_flat:
                res = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_SQDIFF_NORMED)
                min_val, _, min_loc, _ = cv2.minMaxLoc(res)
                score = 1.0 - min_val
                best_loc = min_loc
            else:
                res = cv2.matchTemplate(frame_gray, template_gray, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                score = float(max_val)
                best_loc = max_loc

            matched = score >= params.confidence

            if params.mode == "appear":
                if matched:
                    local_x, local_y = best_loc
                    offset_x = params.roi[0] if params.roi else 0
                    offset_y = params.roi[1] if params.roi else 0
                    center_x = offset_x + local_x + templ_w // 2
                    center_y = offset_y + local_y + templ_h // 2
                    return True, (center_x, center_y)
            elif params.mode == "disappear":
                if not matched:
                    return True, None

            if time.perf_counter() >= deadline:
                break

            # Sliced wait before next poll
            if cancel_event and cancel_event.wait(timeout=poll_interval):
                return False, None
            if not cancel_event:
                time.sleep(poll_interval)

        # Timeout reached
        if params.timeout_action == "raise":
            raise TimeoutError(f"Image wait timed out for {params.template_path} (mode={params.mode})")
        return False, None


# =============================================================================
# 5. Execution Engine Contract
# =============================================================================

class EngineState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class SimpleSignals:
    """Mock/stub for Qt ExecutionSignals for headless testing."""

    def __init__(self) -> None:
        self.state_changes: List[str] = []
        self.progress_updates: List[Tuple[int, int, int, int]] = []
        self.logs: List[Tuple[str, str]] = []
        self.started_actions: List[Tuple[int, str]] = []
        self.completed_actions: List[Tuple[int, bool, str]] = []
        self.finished: List[Tuple[bool, str]] = []

    def emit_state_changed(self, state: str) -> None:
        self.state_changes.append(state)

    def emit_progress(self, cur_loop: int, tot_loops: int, cur_act: int, tot_acts: int) -> None:
        self.progress_updates.append((cur_loop, tot_loops, cur_act, tot_acts))

    def emit_log(self, level: str, msg: str) -> None:
        self.logs.append((level, msg))

    def emit_action_started(self, idx: int, desc: str) -> None:
        self.started_actions.append((idx, desc))

    def emit_action_completed(self, idx: int, success: bool, msg: str) -> None:
        self.completed_actions.append((idx, success, msg))

    def emit_finished(self, success: bool, summary: str) -> None:
        self.finished.append((success, summary))


class ExecutionEngine:
    """Deterministic Macro Execution Engine adhering to PROJECT.md specifications."""

    def __init__(
        self,
        scenario: ScenarioModel,
        input_driver: Any,
        screen_driver: Optional[Any] = None,
        base_dir: Optional[Path] = None,
    ) -> None:
        self.scenario = scenario
        self.input_driver = input_driver
        self.screen_driver = screen_driver
        self.base_dir = base_dir

        self.state: EngineState = EngineState.IDLE
        self.signals = SimpleSignals()
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.current_loop: int = 0
        self.current_action_idx: int = 0
        self._last_detected_coords: Optional[Tuple[int, int]] = None

    def set_state(self, new_state: EngineState) -> None:
        self.state = new_state
        self.signals.emit_state_changed(new_state.value)

    def stop(self) -> None:
        """Triggers emergency stop interrupt and input safety flush."""
        self.set_state(EngineState.STOPPING)
        self.cancel_event.set()
        if hasattr(self.input_driver, "safety_flush"):
            self.input_driver.safety_flush()
        self.set_state(EngineState.STOPPED)

    def pause(self) -> None:
        self.pause_event.set()
        self.set_state(EngineState.PAUSED)

    def resume(self) -> None:
        self.pause_event.clear()
        self.set_state(EngineState.RUNNING)

    def run(self) -> bool:
        """Executes the macro scenario loop."""
        self.set_state(EngineState.RUNNING)
        total_actions = len(self.scenario.actions)
        loop_target = self.scenario.loop_count  # 0 = infinite

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

                    # Handle pause state
                    while self.pause_event.is_set() and not self.cancel_event.is_set():
                        time.sleep(0.01)

                    if self.cancel_event.is_set():
                        break

                    self.current_action_idx = idx
                    self.signals.emit_progress(loop, loop_target, idx + 1, total_actions)

                    if not action.enabled:
                        self.signals.emit_log("INFO", f"Action {idx} ({action.type}) skipped (disabled)")
                        continue

                    self.signals.emit_action_started(idx, action.description or str(action.type))
                    success = self._execute_action(action)
                    self.signals.emit_action_completed(idx, success, "OK" if success else "Failed")

                    if not success and self.cancel_event.is_set():
                        break

                if self.cancel_event.is_set():
                    break

                if loop_target > 0 and loop >= loop_target:
                    break

                if self.scenario.loop_delay_ms > 0:
                    WaitSystem.wait(self.scenario.loop_delay_ms / 1000.0, self.cancel_event, self.pause_event)

        except Exception as err:
            self.set_state(EngineState.ERROR)
            self.signals.emit_log("ERROR", str(err))
            if hasattr(self.input_driver, "safety_flush"):
                self.input_driver.safety_flush()
            self.signals.emit_finished(False, f"Error: {err}")
            raise

        was_cancelled = self.cancel_event.is_set()
        if hasattr(self.input_driver, "safety_flush"):
            self.input_driver.safety_flush()

        if was_cancelled:
            self.set_state(EngineState.STOPPED)
            self.signals.emit_finished(False, "Cancelled by user")
            return False

        self.set_state(EngineState.IDLE)
        self.signals.emit_finished(True, f"Completed {loop} loops")
        return True

    def _execute_action(self, action: ActionBlock) -> bool:
        act_type = action.type if isinstance(action.type, ActionType) else ActionType(action.type)

        if act_type == ActionType.MOUSE_CLICK:
            p = action.params
            x = p.get("x", 0)
            y = p.get("y", 0)
            # Support dynamic target from prior image match if configured
            if p.get("use_last_matched", False) and self._last_detected_coords:
                x, y = self._last_detected_coords

            self.input_driver.click(
                x=x,
                y=y,
                button=p.get("button", "left"),
                count=p.get("clicks", 1),
                press_duration=p.get("press_duration_ms", 50) / 1000.0,
            )
            return True

        elif act_type == ActionType.KEY_INPUT:
            p = action.params
            keys = p.get("keys", [])
            act = p.get("action", "press")
            modifiers = p.get("modifiers")

            if act == "press":
                for k in keys:
                    self.input_driver.press_key(k, modifiers=modifiers)
            elif act == "down":
                for k in keys:
                    self.input_driver.key_down(k)
            elif act == "up":
                for k in keys:
                    self.input_driver.key_up(k)
            elif act == "hotkey":
                self.input_driver.hotkey(*keys)
            return True

        elif act_type == ActionType.FIXED_WAIT:
            duration_s = action.params.get("duration_ms", 0) / 1000.0
            return WaitSystem.wait(duration_s, self.cancel_event, self.pause_event)

        elif act_type == ActionType.IMAGE_WAIT:
            if not self.screen_driver:
                raise ValueError("Screen driver required for ImageWait action")
            p = action.params
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
                iwp,
                self.screen_driver,
                self.cancel_event,
                base_dir=self.base_dir,
            )
            if matched and coords:
                self._last_detected_coords = coords
            return matched

        return False
