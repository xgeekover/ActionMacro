"""
action_macro.core.models
============================
Core domain models and typed action parameters for ActionMacro automation.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Type, Union


class ValidationError(ValueError):
    """Raised when domain model fields or action parameters fail validation."""
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ActionType(str, Enum):
    """Supported macro action types."""
    MOUSE_CLICK = "MOUSE_CLICK"
    KEY_INPUT = "KEY_INPUT"
    FIXED_WAIT = "FIXED_WAIT"
    IMAGE_WAIT = "IMAGE_WAIT"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_upper = value.strip().upper()
            for member in cls:
                if member.value == val_upper or member.name == val_upper:
                    return member
        return None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other or self.value.upper() == other.strip().upper()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return str.__hash__(self.value)

    @classmethod
    def from_str(cls, value: Union[str, ActionType]) -> ActionType:
        """Parse action type case-insensitively from string or enum member."""
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise TypeError(f"ActionType must be a string or ActionType, got {type(value).__name__}")
        try:
            return cls(value)
        except ValueError:
            raise ValueError(
                f"Unknown ActionType: {value!r}. Expected one of {[m.value for m in cls]}"
            )


class MouseButton(str, Enum):
    """Supported mouse button identifiers."""
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value == val_lower or member.name.lower() == val_lower:
                    return member
        return None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other or self.value.lower() == other.strip().lower()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return str.__hash__(self.value)


class KeyAction(str, Enum):
    """Supported keyboard action modes."""
    PRESS = "press"
    DOWN = "down"
    UP = "up"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value == val_lower or member.name.lower() == val_lower:
                    return member
        return None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other or self.value.lower() == other.strip().lower()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return str.__hash__(self.value)


class ImageMode(str, Enum):
    """Supported image wait modes."""
    APPEAR = "appear"
    DISAPPEAR = "disappear"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value == val_lower or member.name.lower() == val_lower:
                    return member
        return None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other or self.value.lower() == other.strip().lower()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return str.__hash__(self.value)


class TimeoutAction(str, Enum):
    """Action to take upon image wait timeout expiry."""
    RAISE = "raise"
    SKIP = "skip"

    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            val_lower = value.strip().lower()
            for member in cls:
                if member.value == val_lower or member.name.lower() == val_lower:
                    return member
        return None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            return self.value == other or self.value.lower() == other.strip().lower()
        return super().__eq__(other)

    def __hash__(self) -> int:
        return str.__hash__(self.value)


# ---------------------------------------------------------------------------
# Base Parameter Helper
# ---------------------------------------------------------------------------

class _ParamBase:
    """Provides dictionary-like access and serialization to dataclass parameters."""

    def __getitem__(self, item: str) -> Any:
        try:
            return getattr(self, item)
        except AttributeError:
            raise KeyError(item)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)  # type: ignore[call-overload]
        if "roi" in d and d["roi"] is not None:
            d["roi"] = list(d["roi"])
        return d


# ---------------------------------------------------------------------------
# Typed Action Parameters
# ---------------------------------------------------------------------------

@dataclass
class MouseClickParams(_ParamBase):
    """Parameters for MOUSE_CLICK action."""
    x: int
    y: int
    button: str = "left"
    clicks: int = 1
    interval_ms: int = 0
    press_duration_ms: int = 50

    def __post_init__(self) -> None:
        if not isinstance(self.x, int) or isinstance(self.x, bool):
            raise TypeError(f"MouseClickParams.x must be an int, got {type(self.x).__name__}")
        if not isinstance(self.y, int) or isinstance(self.y, bool):
            raise TypeError(f"MouseClickParams.y must be an int, got {type(self.y).__name__}")

        if isinstance(self.button, MouseButton):
            self.button = self.button.value
        elif not isinstance(self.button, str):
            raise TypeError(f"MouseClickParams.button must be a string, got {type(self.button).__name__}")

        btn_lower = self.button.strip().lower()
        if btn_lower not in ("left", "right", "middle"):
            raise ValueError(f"MouseClickParams.button must be 'left', 'right', or 'middle', got {self.button!r}")
        self.button = btn_lower

        if not isinstance(self.clicks, int) or isinstance(self.clicks, bool):
            raise TypeError(f"MouseClickParams.clicks must be an int, got {type(self.clicks).__name__}")
        if self.clicks < 1:
            raise ValueError(f"MouseClickParams.clicks must be an integer >= 1, got {self.clicks!r}")

        if not isinstance(self.interval_ms, int) or isinstance(self.interval_ms, bool):
            raise TypeError(f"MouseClickParams.interval_ms must be an int, got {type(self.interval_ms).__name__}")
        if self.interval_ms < 0:
            raise ValueError(f"MouseClickParams.interval_ms must be an integer >= 0, got {self.interval_ms!r}")

        if not isinstance(self.press_duration_ms, int) or isinstance(self.press_duration_ms, bool):
            raise TypeError(f"MouseClickParams.press_duration_ms must be an int, got {type(self.press_duration_ms).__name__}")
        if self.press_duration_ms < 0:
            raise ValueError(f"MouseClickParams.press_duration_ms must be an integer >= 0, got {self.press_duration_ms!r}")


@dataclass
class KeyInputParams(_ParamBase):
    """Parameters for KEY_INPUT action."""
    keys: List[str]
    action: str = "press"
    modifiers: Optional[List[str]] = None
    press_duration_ms: int = 50

    def __post_init__(self) -> None:
        if not isinstance(self.keys, list):
            raise TypeError(f"KeyInputParams.keys must be a list, got {type(self.keys).__name__}")
        if len(self.keys) == 0:
            raise ValueError(f"KeyInputParams.keys must be a non-empty list of strings, got {self.keys!r}")

        normalized_keys: List[str] = []
        for k in self.keys:
            if not isinstance(k, str):
                raise TypeError(f"Each key in KeyInputParams.keys must be a string, got {type(k).__name__}")
            if not k.strip():
                raise ValueError(f"Each key in KeyInputParams.keys must be a non-empty string, got {k!r}")
            normalized_keys.append(k.strip())
        self.keys = normalized_keys

        if isinstance(self.action, KeyAction):
            self.action = self.action.value
        elif not isinstance(self.action, str):
            raise TypeError(f"KeyInputParams.action must be a string, got {type(self.action).__name__}")

        act_lower = self.action.strip().lower()
        if act_lower not in ("press", "down", "up"):
            raise ValueError(f"KeyInputParams.action must be 'press', 'down', or 'up', got {self.action!r}")
        self.action = act_lower

        if self.modifiers is not None:
            if not isinstance(self.modifiers, list):
                raise TypeError(f"KeyInputParams.modifiers must be a list, got {type(self.modifiers).__name__}")
            for m in self.modifiers:
                if not isinstance(m, str) or not m.strip():
                    raise ValueError(f"Invalid modifier: {m!r}")

        if not isinstance(self.press_duration_ms, int) or isinstance(self.press_duration_ms, bool):
            raise TypeError(f"KeyInputParams.press_duration_ms must be an int, got {type(self.press_duration_ms).__name__}")
        if self.press_duration_ms < 0:
            raise ValueError(f"KeyInputParams.press_duration_ms must be an integer >= 0, got {self.press_duration_ms!r}")


@dataclass
class FixedWaitParams(_ParamBase):
    """Parameters for FIXED_WAIT action."""
    duration_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.duration_ms, int) or isinstance(self.duration_ms, bool):
            raise TypeError(f"FixedWaitParams.duration_ms must be an int, got {type(self.duration_ms).__name__}")
        if self.duration_ms < 0:
            raise ValueError(f"FixedWaitParams.duration_ms must be an integer >= 0, got {self.duration_ms!r}")


@dataclass
class ImageWaitParams(_ParamBase):
    """Parameters for IMAGE_WAIT action."""
    template_path: str
    mode: str = "appear"
    confidence: float = 0.8
    timeout_seconds: float = 10.0
    roi: Optional[Tuple[int, int, int, int]] = None
    timeout_action: str = "raise"
    poll_interval_seconds: float = 0.5

    def __post_init__(self) -> None:
        if not isinstance(self.template_path, str):
            raise TypeError(f"ImageWaitParams.template_path must be a string, got {type(self.template_path).__name__}")
        if not self.template_path.strip():
            raise ValueError("ImageWaitParams.template_path must be a non-empty string")
        self.template_path = self.template_path.strip()

        if isinstance(self.mode, ImageMode):
            self.mode = self.mode.value
        elif not isinstance(self.mode, str):
            raise TypeError(f"ImageWaitParams.mode must be a string, got {type(self.mode).__name__}")
        mode_lower = self.mode.strip().lower()
        if mode_lower not in ("appear", "disappear"):
            raise ValueError(f"ImageWaitParams.mode must be 'appear' or 'disappear', got {self.mode!r}")
        self.mode = mode_lower

        if not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool):
            raise TypeError(f"ImageWaitParams.confidence must be a number between 0.0 and 1.0, got {self.confidence!r}")
        self.confidence = float(self.confidence)
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"ImageWaitParams.confidence must be between 0.0 and 1.0, got {self.confidence}")

        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(self.timeout_seconds, bool):
            raise TypeError(f"ImageWaitParams.timeout_seconds must be a number >= 0.0, got {self.timeout_seconds!r}")
        self.timeout_seconds = float(self.timeout_seconds)
        if self.timeout_seconds < 0.0:
            raise ValueError(f"ImageWaitParams.timeout_seconds must be >= 0.0, got {self.timeout_seconds}")

        if self.roi is not None:
            if not isinstance(self.roi, (tuple, list)) or len(self.roi) != 4:
                raise ValueError(f"ImageWaitParams.roi must be a 4-tuple (x, y, w, h), got {self.roi!r}")
            rx, ry, rw, rh = self.roi
            for val, name in [(rx, "x"), (ry, "y"), (rw, "w"), (rh, "h")]:
                if not isinstance(val, int) or isinstance(val, bool):
                    raise TypeError(f"ImageWaitParams.roi.{name} must be an integer, got {val!r}")
            if rw <= 0 or rh <= 0:
                raise ValueError(f"ImageWaitParams.roi width and height must be positive, got {self.roi!r}")
            self.roi = (rx, ry, rw, rh)

        if isinstance(self.timeout_action, TimeoutAction):
            self.timeout_action = self.timeout_action.value
        elif not isinstance(self.timeout_action, str):
            raise TypeError(f"ImageWaitParams.timeout_action must be a string, got {type(self.timeout_action).__name__}")
        tact_lower = self.timeout_action.strip().lower()
        if tact_lower not in ("raise", "skip"):
            raise ValueError(f"ImageWaitParams.timeout_action must be 'raise' or 'skip', got {self.timeout_action!r}")
        self.timeout_action = tact_lower

        if not isinstance(self.poll_interval_seconds, (int, float)) or isinstance(self.poll_interval_seconds, bool):
            raise TypeError(f"ImageWaitParams.poll_interval_seconds must be a number > 0.0, got {self.poll_interval_seconds!r}")
        self.poll_interval_seconds = float(self.poll_interval_seconds)
        if self.poll_interval_seconds <= 0.0:
            raise ValueError(f"ImageWaitParams.poll_interval_seconds must be > 0.0, got {self.poll_interval_seconds}")


ActionParamsType = Union[MouseClickParams, KeyInputParams, FixedWaitParams, ImageWaitParams]

PARAM_CLASS_MAP: Dict[ActionType, Type[ActionParamsType]] = {
    ActionType.MOUSE_CLICK: MouseClickParams,
    ActionType.KEY_INPUT: KeyInputParams,
    ActionType.FIXED_WAIT: FixedWaitParams,
    ActionType.IMAGE_WAIT: ImageWaitParams,
}


# ---------------------------------------------------------------------------
# ActionBlock and ScenarioModel
# ---------------------------------------------------------------------------

@dataclass
class ActionBlock:
    """Executable macro action block."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: ActionType = ActionType.FIXED_WAIT
    params: Union[ActionParamsType, Dict[str, Any]] = field(
        default_factory=lambda: FixedWaitParams(duration_ms=500)
    )
    enabled: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        if self.id is None:
            self.id = str(uuid.uuid4())
        elif not isinstance(self.id, str):
            raise TypeError(f"ActionBlock.id must be a string, got {type(self.id).__name__}")
        elif not self.id.strip():
            raise ValueError("id cannot be empty")

        if not isinstance(self.type, ActionType):
            try:
                self.type = ActionType(self.type)
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid ActionType: {self.type!r}") from e

        param_cls = PARAM_CLASS_MAP.get(self.type)
        if param_cls is None:
            raise ValueError(f"Unknown ActionType: {self.type}")

        if isinstance(self.params, dict):
            try:
                self.params = param_cls(**self.params)
            except TypeError as e:
                raise TypeError(f"Invalid parameters for {self.type.value}: {e}") from e
        elif isinstance(self.params, param_cls):
            pass
        else:
            raise TypeError(
                f"ActionBlock params type mismatch: expected {param_cls.__name__} or dict for "
                f"action type {self.type.value}, got {type(self.params).__name__}"
            )

        if not isinstance(self.enabled, bool):
            raise TypeError(f"ActionBlock.enabled must be a boolean, got {type(self.enabled).__name__}")
        if not isinstance(self.description, str):
            self.description = str(self.description)

    @property
    def params_dict(self) -> Dict[str, Any]:
        """Convenience property returning parameters as a dictionary."""
        if hasattr(self.params, "to_dict"):
            return self.params.to_dict()
        return asdict(self.params)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize ActionBlock to dictionary."""
        return {
            "id": self.id,
            "type": self.type.value,
            "params": self.params_dict,
            "enabled": self.enabled,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ActionBlock:
        """Create ActionBlock from serialized dictionary."""
        if not isinstance(data, dict):
            raise TypeError(f"ActionBlock data must be a dictionary, got {type(data).__name__}")
        if "type" not in data:
            raise ValueError("ActionBlock dictionary missing required 'type' field")
        action_type = ActionType(data["type"])
        raw_params = data.get("params", {})
        if not isinstance(raw_params, dict):
            raise TypeError(f"ActionBlock params must be a dictionary, got {type(raw_params).__name__}")

        return cls(
            id=data.get("id", str(uuid.uuid4())),
            type=action_type,
            params=raw_params,
            enabled=data.get("enabled", True),
            description=data.get("description", ""),
        )


@dataclass
class ScenarioModel:
    """Root macro scenario model holding action blocks and execution settings."""
    name: str = "Untitled Scenario"
    version: str = "1.0.0"
    loop_count: int = 1
    actions: List[ActionBlock] = field(default_factory=list)
    description: str = ""
    abort_hotkey: str = "f12"
    action_delay_ms: int = 50
    loop_delay_ms: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    target_resolution: Optional[Dict[str, int]] = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError(f"ScenarioModel.name must be a string, got {type(self.name).__name__}")
        if not self.name.strip():
            raise ValueError("ScenarioModel.name must be a non-empty string")
        self.name = self.name.strip()

        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("ScenarioModel.version must be a non-empty string")
        self.version = self.version.strip()

        if not isinstance(self.loop_count, int) or isinstance(self.loop_count, bool):
            raise TypeError(f"ScenarioModel.loop_count must be an int, got {type(self.loop_count).__name__}")
        if self.loop_count < 0:
            raise ValueError(f"ScenarioModel.loop_count must be an integer >= 0, got {self.loop_count!r}")

        if not isinstance(self.actions, list):
            raise TypeError(f"ScenarioModel.actions must be a list, got {type(self.actions).__name__}")

        seen_ids: set[str] = set()
        validated_actions: List[ActionBlock] = []
        for idx, act in enumerate(self.actions):
            if isinstance(act, ActionBlock):
                act_obj = act
            elif isinstance(act, dict):
                try:
                    act_obj = ActionBlock.from_dict(act)
                except Exception as e:
                    raise TypeError(f"Invalid action block at index {idx}: {e}") from e
            else:
                raise TypeError(f"Invalid item in actions at index {idx}: {type(act).__name__}")

            if act_obj.id in seen_ids:
                raise ValueError(f"Duplicate action block IDs found in scenario: {act_obj.id}")
            seen_ids.add(act_obj.id)
            validated_actions.append(act_obj)
        self.actions = validated_actions

    def add_action(self, action: ActionBlock, index: Optional[int] = None) -> None:
        """Add an action block at specified index or append to the end."""
        if not isinstance(action, ActionBlock):
            raise TypeError(f"Expected ActionBlock instance, got {type(action).__name__}")
        if self.get_action(action.id) is not None:
            raise ValueError(f"Duplicate action block IDs found in scenario: {action.id}")
        if index is None or index >= len(self.actions):
            self.actions.append(action)
        else:
            self.actions.insert(max(0, index), action)

    def remove_action(self, action_id: str) -> bool:
        """Remove action block by ID. Returns True if removed, False otherwise."""
        for i, act in enumerate(self.actions):
            if act.id == action_id:
                del self.actions[i]
                return True
        return False

    def move_action(self, from_index: int, to_index: int) -> None:
        """Move action block from one index to another."""
        if not (0 <= from_index < len(self.actions)):
            raise IndexError(f"from_index {from_index} out of range [0, {len(self.actions)})")
        if not (0 <= to_index < len(self.actions)):
            raise IndexError(f"to_index {to_index} out of range [0, {len(self.actions)})")
        action = self.actions.pop(from_index)
        self.actions.insert(to_index, action)

    def get_action(self, action_id: str) -> Optional[ActionBlock]:
        """Find action block by ID."""
        for act in self.actions:
            if act.id == action_id:
                return act
        return None

    def clone(self) -> ScenarioModel:
        """Create a deep copy of the scenario."""
        return copy.deepcopy(self)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize scenario to dictionary representation."""
        res: Dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "loop_count": self.loop_count,
            "description": self.description,
            "actions": [act.to_dict() for act in self.actions],
        }
        if self.abort_hotkey != "f12":
            res["abort_hotkey"] = self.abort_hotkey
        if self.action_delay_ms != 50:
            res["action_delay_ms"] = self.action_delay_ms
        if self.loop_delay_ms != 0:
            res["loop_delay_ms"] = self.loop_delay_ms
        if self.created_at:
            res["created_at"] = self.created_at
        if self.updated_at:
            res["updated_at"] = self.updated_at
        if self.target_resolution:
            res["target_resolution"] = self.target_resolution
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScenarioModel:
        """Construct ScenarioModel from dictionary."""
        if not isinstance(data, dict):
            raise TypeError(f"ScenarioModel data must be a dictionary, got {type(data).__name__}")

        name = data.get("name")
        version = data.get("version", data.get("schema_version", "1.0.0"))
        loop_count = data.get("loop_count")

        metadata = data.get("metadata", {})
        if isinstance(metadata, dict):
            if not name:
                name = metadata.get("name")

        settings = data.get("settings", {})
        if isinstance(settings, dict):
            if loop_count is None:
                loop_count = settings.get("loop_count")

        raw_actions = data.get("actions", [])
        if not isinstance(raw_actions, list):
            raise TypeError("Scenario actions must be a list")

        return cls(
            name=name if name is not None else "Untitled Scenario",
            version=version,
            loop_count=loop_count if loop_count is not None else 1,
            actions=raw_actions,
            description=data.get("description", metadata.get("description", "")),
            abort_hotkey=data.get("abort_hotkey", settings.get("abort_hotkey", "f12")),
            action_delay_ms=data.get("action_delay_ms", settings.get("action_delay_ms", 50)),
            loop_delay_ms=data.get("loop_delay_ms", settings.get("loop_delay_ms", 0)),
            created_at=data.get("created_at", metadata.get("created_at")),
            updated_at=data.get("updated_at", metadata.get("updated_at")),
            target_resolution=data.get("target_resolution", metadata.get("target_resolution")),
        )
