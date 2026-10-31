"""
action_macro.core.persistence
=================================
JSON scenario serialization, deserialization, strict schema validation,
cross-platform relative path handling, and schema migration handlers.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    PARAM_CLASS_MAP,
    ScenarioModel,
    ValidationError,
)

CURRENT_SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PersistenceError(Exception):
    """Base exception for scenario persistence operations."""
    pass


class ScenarioValidationError(PersistenceError, ValidationError):
    """Raised when scenario data fails schema structure or field validation."""
    pass


class ScenarioJSONDecodeError(PersistenceError):
    """Raised when scenario file contains invalid or corrupted JSON."""
    pass


class UnsupportedVersionError(PersistenceError):
    """Raised when the scenario schema version is unsupported."""
    pass


class IncompatibleVersionError(UnsupportedVersionError):
    """Raised when scenario version cannot be loaded by the current engine."""
    pass


class ScenarioFileNotFoundError(PersistenceError, FileNotFoundError):
    """Raised when scenario file or asset cannot be found."""
    pass


# ---------------------------------------------------------------------------
# Cross-Platform Relative Path Handling
# ---------------------------------------------------------------------------

def to_posix_relative_path(path_str: str, base_dir: Path) -> str:
    """
    Convert an absolute or relative path string into a relative POSIX path string
    (using forward slashes) relative to base_dir.
    """
    clean_path_str = path_str.replace("\\", "/")
    p = Path(clean_path_str)

    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(base_dir.resolve())
            return rel.as_posix()
        except ValueError:
            try:
                rel_os = os.path.relpath(str(p.resolve()), str(base_dir.resolve()))
                return PurePosixPath(Path(rel_os)).as_posix()
            except ValueError:
                return PurePosixPath(p).as_posix()
    else:
        return PurePosixPath(clean_path_str).as_posix()


def resolve_template_path(template_path: str, scenario_path: Union[str, Path]) -> Path:
    """
    Resolves a relative POSIX template path against the scenario directory.

    On Windows an absolute path (``C:\\...``, ``C:/...``, ``\\\\server\\share\\...``) is used
    as it is: split into POSIX parts it would become a drive-relative path (``C:Users``)
    joined under the scenario folder. Such paths come from Browse or drag-and-drop after
    the scenario was saved, and from saving a template that is on another drive.
    """
    if sys.platform == "win32" and PureWindowsPath(template_path).is_absolute():
        return Path(template_path).resolve()

    scen_p = Path(scenario_path).resolve()
    base_dir = scen_p.parent if (scen_p.is_file() or scen_p.suffix == ".json") else scen_p

    clean_posix = template_path.replace("\\", "/")
    parts = PurePosixPath(clean_posix).parts
    return (base_dir / Path(*parts)).resolve()


# ---------------------------------------------------------------------------
# Versioning & Migration Handler
# ---------------------------------------------------------------------------

def parse_semver(version_str: str) -> Tuple[int, int, int]:
    """Parse a semantic version string into (major, minor, patch)."""
    if not isinstance(version_str, str) or not version_str.strip():
        raise ScenarioValidationError(f"Invalid version string: {version_str!r}")
    parts = version_str.strip().split(".")
    if len(parts) != 3:
        raise ScenarioValidationError(f"Version must be in 'MAJOR.MINOR.PATCH' format, got {version_str!r}")
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as e:
        raise ScenarioValidationError(f"Version components must be integers: {version_str!r}") from e


MigrationFn = Callable[[Dict[str, Any]], Dict[str, Any]]
MIGRATION_REGISTRY: Dict[Tuple[str, str], MigrationFn] = {}


def register_migration(from_ver: str, to_ver: str) -> Callable[[MigrationFn], MigrationFn]:
    """Decorator to register a schema migration function."""
    def decorator(fn: MigrationFn) -> MigrationFn:
        MIGRATION_REGISTRY[(from_ver, to_ver)] = fn
        return fn
    return decorator


def migrate_scenario_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply necessary migrations to bring scenario dictionary up to CURRENT_SCHEMA_VERSION.
    """
    file_version = data.get("version", data.get("schema_version", "1.0.0"))
    file_major, file_minor, _ = parse_semver(file_version)
    curr_major, curr_minor, _ = parse_semver(CURRENT_SCHEMA_VERSION)

    if file_major > curr_major:
        raise UnsupportedVersionError(
            f"Scenario schema version {file_version} is newer than supported version "
            f"{CURRENT_SCHEMA_VERSION}. Please upgrade the application."
        )

    current_data = data
    current_ver = file_version

    while current_ver != CURRENT_SCHEMA_VERSION:
        step_found = False
        for (from_v, to_v), mig_fn in MIGRATION_REGISTRY.items():
            if from_v == current_ver:
                current_data = mig_fn(current_data)
                current_ver = to_v
                current_data["version"] = current_ver
                step_found = True
                break
        if not step_found:
            if file_major == curr_major:
                current_data["version"] = CURRENT_SCHEMA_VERSION
                break
            raise IncompatibleVersionError(
                f"No migration path available from scenario version {current_ver} to {CURRENT_SCHEMA_VERSION}"
            )

    return current_data


# ---------------------------------------------------------------------------
# Serialization & Deserialization Functions
# ---------------------------------------------------------------------------

def scenario_to_dict(scenario: ScenarioModel, base_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Converts a ScenarioModel instance to a JSON-serializable dictionary.
    Converts template paths to normalized POSIX paths.
    """
    data = scenario.to_dict()
    resolved_base = base_dir.resolve() if base_dir is not None else None

    for action_data in data.get("actions", []):
        act_type = action_data.get("type", "")
        if act_type in (ActionType.IMAGE_WAIT.value, "image_wait", "IMAGE_WAIT"):
            params = action_data.get("params", {})
            raw_path = params.get("template_path", "")
            if raw_path:
                if resolved_base is not None:
                    params["template_path"] = to_posix_relative_path(raw_path, resolved_base)
                else:
                    # Normalize backslashes to forward slashes
                    params["template_path"] = PurePosixPath(raw_path.replace("\\", "/")).as_posix()

    return data


def scenario_from_dict(data: Dict[str, Any], base_dir: Optional[Path] = None) -> ScenarioModel:
    """
    Reconstructs and validates a ScenarioModel instance from a dictionary.
    """
    if not isinstance(data, dict):
        raise ScenarioValidationError(f"Scenario root must be a dictionary, got {type(data).__name__}")

    # Validate required root fields
    for required_field in ("name", "version", "loop_count", "actions"):
        if required_field not in data:
            raise ScenarioValidationError(f"Missing required root field: {required_field!r}")

    # Validate root field types
    name = data["name"]
    if not isinstance(name, str) or not name.strip():
        raise ScenarioValidationError(f"name must be a non-empty string, got {name!r}")

    version = data["version"]
    if not isinstance(version, str):
        raise ScenarioValidationError(f"version must be a string, got {type(version).__name__}")

    loop_count = data["loop_count"]
    if not isinstance(loop_count, int) or isinstance(loop_count, bool):
        raise ScenarioValidationError(f"loop_count must be an integer, got {type(loop_count).__name__}")
    if loop_count < 0:
        raise ScenarioValidationError(f"loop_count must be >= 0, got {loop_count!r}")

    raw_actions = data["actions"]
    if not isinstance(raw_actions, list):
        raise ScenarioValidationError(f"actions must be a list, got {type(raw_actions).__name__}")

    # Migration and SemVer check
    migrated_data = migrate_scenario_data(data)

    # Validate each action block
    seen_ids: set[str] = set()
    validated_actions: List[ActionBlock] = []

    for idx, act_data in enumerate(raw_actions):
        if not isinstance(act_data, dict):
            raise ScenarioValidationError(f"Action at index {idx} must be a dictionary, got {type(act_data).__name__}")

        if "id" not in act_data:
            raise ScenarioValidationError(f"Action at index {idx} missing required 'id' field")
        act_id = act_data["id"]
        if not isinstance(act_id, str) or not act_id.strip():
            raise ScenarioValidationError(f"Action 'id' at index {idx} must be a non-empty string")

        if act_id in seen_ids:
            raise ScenarioValidationError(f"Duplicate action block IDs found in scenario: {act_id!r}")
        seen_ids.add(act_id)

        if "type" not in act_data:
            raise ScenarioValidationError(f"Action at index {idx} missing required 'type' field")
        raw_type = act_data["type"]

        try:
            action_type = ActionType(raw_type)
        except (ValueError, KeyError):
            raise ScenarioValidationError(f"Unknown action type at index {idx}: {raw_type!r}")

        params = act_data.get("params", {})
        if not isinstance(params, dict):
            raise ScenarioValidationError(f"Action params at index {idx} must be a dictionary")

        param_cls = PARAM_CLASS_MAP.get(action_type)
        if param_cls is None:
            raise ScenarioValidationError(f"Unsupported action type: {action_type}")

        try:
            param_obj = param_cls(**params)
        except (TypeError, ValueError) as e:
            raise ScenarioValidationError(f"Invalid parameters for action {action_type.value} at index {idx}: {e}") from e

        enabled = act_data.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ScenarioValidationError(f"Action 'enabled' at index {idx} must be a boolean, got {enabled!r}")

        desc = act_data.get("description", "")
        if not isinstance(desc, str):
            desc = str(desc)

        validated_actions.append(
            ActionBlock(
                id=act_id,
                type=action_type,
                params=param_obj,
                enabled=enabled,
                description=desc,
            )
        )

    return ScenarioModel(
        name=name.strip(),
        version=migrated_data.get("version", version),
        loop_count=loop_count,
        actions=validated_actions,
        description=data.get("description", ""),
        abort_hotkey=data.get("abort_hotkey", "f12"),
        action_delay_ms=data.get("action_delay_ms", 50),
        loop_delay_ms=data.get("loop_delay_ms", 0),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        target_resolution=data.get("target_resolution"),
    )


def scenario_to_json(scenario: ScenarioModel, base_dir: Optional[Path] = None, indent: int = 2) -> str:
    """Serialize ScenarioModel to a formatted UTF-8 JSON string."""
    data = scenario_to_dict(scenario, base_dir=base_dir)
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"


def scenario_from_json(json_str: str, base_dir: Optional[Path] = None) -> ScenarioModel:
    """Deserialize ScenarioModel from a JSON string with strict validation."""
    if not isinstance(json_str, str) or not json_str.strip():
        raise ScenarioJSONDecodeError("Scenario JSON string is empty")
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ScenarioJSONDecodeError(f"Malformed JSON: {e.msg} (line {e.lineno}, col {e.colno})") from e

    return scenario_from_dict(data, base_dir=base_dir)


def save_scenario(scenario: ScenarioModel, file_path: Union[str, Path], make_dirs: bool = True) -> None:
    """
    Serializes a ScenarioModel to a formatted UTF-8 JSON file using atomic write.
    """
    path = Path(file_path).resolve()
    base_dir = path.parent

    if make_dirs:
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise PersistenceError(f"Cannot create directory '{base_dir}': {e}") from e
    else:
        if not base_dir.exists():
            raise PersistenceError(FileNotFoundError(f"Directory '{base_dir}' does not exist"))

    try:
        content = scenario_to_json(scenario, base_dir=base_dir, indent=2)
    except Exception as e:
        raise PersistenceError(f"Serialization failed: {e}") from e

    temp_path = path.with_suffix(path.suffix + f".tmp_{uuid.uuid4().hex[:8]}")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        temp_path.replace(path)
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        if isinstance(e, PersistenceError):
            raise
        raise PersistenceError(f"Failed to write scenario file '{path}': {e}") from e


def load_scenario(file_path: Union[str, Path]) -> ScenarioModel:
    """
    Loads, parses, and validates a ScenarioModel from a JSON file.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Scenario file not found: '{path}'")

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        raise PersistenceError(f"Failed to read scenario file '{path}': {e}") from e

    if not content.strip():
        raise ScenarioJSONDecodeError(f"Scenario file is empty: '{path}'")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ScenarioJSONDecodeError(f"Malformed scenario JSON in '{path}': {e}") from e

    return scenario_from_dict(data, base_dir=path.parent)


# Aliases for backwards and alternative naming compatibility
save_scenario_to_file = save_scenario
load_scenario_from_file = load_scenario


class ScenarioPersistence:
    """Namespace class matching contract and alternative interface styles."""
    save = staticmethod(save_scenario)
    load = staticmethod(load_scenario)
    to_dict = staticmethod(scenario_to_dict)
    from_dict = staticmethod(scenario_from_dict)
    resolve_template_path = staticmethod(resolve_template_path)
