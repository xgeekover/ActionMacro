"""
action_macro.gui.step_text
=============================
Human-readable titles and one-sentence summaries of steps (action blocks).

The step list shows each step as a short sentence, so a user can read a
scenario top to bottom without opening the inspector::

    Click left at (1280, 720)
    Double-click left at (640, 360)
    Click right 3 times at (10, 20), 100 ms apart
    Press ⌘ + C                (Ctrl + C on Windows and Linux)
    Hold down ⇧                (Shift)
    Release ⇧
    Wait 1.0 s
    Wait until quest_button.png appears · timeout 10 s
    Wait until loading.png disappears · skip after 5 s

Keys are shown exactly like the inspector's shortcut chip
(:func:`action_macro.core.key_mapping.format_key_combo`), never as the internal
tokens (``cmd_or_ctrl``, ``page_up``) typed into the Keys field.

The text functions only read the action's parameters; they never modify them.
The template helpers at the end resolve an image-wait template exactly like the
execution engine, so the step card and the inspector agree with a run.
"""

from __future__ import annotations

from pathlib import Path, PurePath
from typing import Any, Optional, Union

from action_macro.core.key_mapping import build_key_chord, format_key_combo
from action_macro.core.models import ActionBlock, ActionType
from action_macro.core.persistence import resolve_template_path
from action_macro.gui import theme

#: Separator between the main sentence and a secondary detail ("… appears · timeout 10 s").
DETAIL_SEPARATOR = " · "

#: Prefix of the summary of a step that is switched off (it is skipped when the scenario runs).
DISABLED_PREFIX = "Disabled"
#: Former name of :data:`DISABLED_PREFIX`.
SKIPPED_PREFIX = DISABLED_PREFIX

#: Template path a new image-wait step starts with. The model needs a non-empty path,
#: so this placeholder stands for "no image chosen yet" while the file does not exist.
DEFAULT_TEMPLATE_PATH = "templates/target.png"


def _param(params: Any, name: str, default: Any = None) -> Any:
    """Reads ``name`` from a params dataclass or a plain dict."""
    if isinstance(params, dict):
        return params.get(name, default)
    return getattr(params, name, default)


def _action_type(action: ActionBlock) -> ActionType:
    value = action.type
    return value if isinstance(value, ActionType) else ActionType.from_str(str(value))


def _number(value: float, max_decimals: int = 2) -> str:
    """Formats ``value`` with up to ``max_decimals`` decimals and no trailing zeros."""
    text = f"{value:.{max_decimals}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def format_seconds(seconds: float) -> str:
    """Formats a timeout such as ``10 s`` or ``2.5 s`` (no forced decimal)."""
    return f"{_number(float(seconds))} s"


def format_wait_seconds(duration_ms: int) -> str:
    """A wait duration in seconds for the inspector (its spin box already shows ms):
    ``0.25 s``, ``1.0 s``, ``2.5 s``, ``1 min 30 s``. Same wording as the card from 1 s up."""
    ms = max(0, int(duration_ms))
    if ms >= 1000:
        return format_duration_ms(ms)
    text = _number(ms / 1000.0, 3)
    return f"{text if '.' in text else text + '.0'} s"


def format_duration_ms(duration_ms: int) -> str:
    """Formats a wait duration for people: ``250 ms``, ``1.0 s``, ``2.25 s``, ``1 min 30 s``.

    Below one second the value stays in milliseconds; below one minute it is shown
    in seconds with at least one decimal (``1.0 s`` reads as a duration, ``1 s``
    looks like a count); longer waits are split into minutes and seconds.
    """
    ms = max(0, int(duration_ms))
    if ms < 1000:
        return f"{ms} ms"
    if ms < 60_000:
        text = _number(ms / 1000.0)
        if "." not in text:
            text += ".0"
        return f"{text} s"
    minutes, rest_ms = divmod(ms, 60_000)
    if rest_ms == 0:
        return f"{minutes} min"
    return f"{minutes} min {_number(rest_ms / 1000.0)} s"


def click_summary(params: Any) -> str:
    """``Click left at (x, y)``, ``Double-click left at (x, y)``, ``Click right 3 times at (x, y), 100 ms apart``."""
    button = str(_param(params, "button", "left") or "left").strip().lower()
    clicks = int(_param(params, "clicks", 1) or 1)
    interval_ms = int(_param(params, "interval_ms", 0) or 0)
    point = f"({_param(params, 'x', 0)}, {_param(params, 'y', 0)})"
    if clicks == 1:
        return f"Click {button} at {point}"
    sentence = f"Double-click {button} at {point}" if clicks == 2 else f"Click {button} {clicks} times at {point}"
    if interval_ms > 0:
        sentence += f", {interval_ms} ms apart"
    return sentence


def key_chord_text(params: Any) -> str:
    """The chord of a key step in the Keys-field syntax (modifiers first), e.g. ``ctrl+shift+s``."""
    chord = build_key_chord(_param(params, "modifiers"), _param(params, "keys"))
    return "+".join(str(key) for key in chord)


def key_chord_display(params: Any, platform_name: Optional[str] = None) -> str:
    """The chord as people read it on this platform: ``⌘ + ⇧ + T`` on macOS, ``Ctrl + Shift + T``
    elsewhere (the inspector's shortcut chip shows the same text)."""
    chord = build_key_chord(_param(params, "modifiers"), _param(params, "keys"))
    return format_key_combo([str(key) for key in chord], platform_name) if chord else ""


def key_summary(params: Any) -> str:
    """``Press ⌘ + C``, ``Hold down ⇧`` or ``Release ⇧`` (``Ctrl + C`` / ``Shift`` off macOS)."""
    chord = key_chord_display(params)
    if not chord:
        return "No key set"
    mode = str(_param(params, "action", "press") or "press").strip().lower()
    verb = {"down": "Hold down", "up": "Release"}.get(mode, "Press")
    return f"{verb} {chord}"


def wait_summary(params: Any) -> str:
    """``Wait 1.0 s``."""
    return f"Wait {format_duration_ms(int(_param(params, 'duration_ms', 0) or 0))}"


def image_wait_summary(params: Any, image_chosen: bool = True) -> str:
    """``Wait until quest_button.png appears · timeout 10 s`` (or ``disappears``, ``skip after``).

    ``image_chosen=False`` (the ``placeholder`` template state) says "an image" instead
    of naming the default file of a new step, which does not exist.
    """
    template = str(_param(params, "template_path", "") or "").strip() if image_chosen else ""
    name = PurePath(template.replace("\\", "/")).name if template else ""
    subject = name or "an image"
    mode = str(_param(params, "mode", "appear") or "appear").strip().lower()
    verb = "disappears" if mode == "disappear" else "appears"
    timeout = format_seconds(float(_param(params, "timeout_seconds", 10.0) or 0.0))
    on_timeout = str(_param(params, "timeout_action", "raise") or "raise").strip().lower()
    detail = f"skip after {timeout}" if on_timeout == "skip" else f"timeout {timeout}"
    return f"Wait until {subject} {verb}{DETAIL_SEPARATOR}{detail}"


def step_summary(action: ActionBlock, image_chosen: bool = True) -> str:
    """The one-sentence summary of ``action`` (without the "Disabled" prefix)."""
    act_type = _action_type(action)
    params = action.params
    if act_type == ActionType.MOUSE_CLICK:
        return click_summary(params)
    if act_type == ActionType.KEY_INPUT:
        return key_summary(params)
    if act_type == ActionType.FIXED_WAIT:
        return wait_summary(params)
    if act_type == ActionType.IMAGE_WAIT:
        return image_wait_summary(params, image_chosen)
    return type_label(act_type)


def card_summary(action: ActionBlock, image_chosen: bool = True) -> str:
    """The summary line of a step card: the sentence, prefixed with "Disabled · " when switched off."""
    sentence = step_summary(action, image_chosen)
    if action.enabled:
        return sentence
    return f"{DISABLED_PREFIX}{DETAIL_SEPARATOR}{sentence}"


def type_label(action_type: ActionType) -> str:
    """The display label of a step type ("Mouse click", "Key input", "Wait", "Image wait")."""
    meta = theme.STEP_TYPES.get(action_type)
    return meta.label if meta else str(getattr(action_type, "value", action_type)).replace("_", " ").capitalize()


def step_title(action: ActionBlock) -> str:
    """The card title: the user's description, or the step type's label."""
    description = (action.description or "").strip()
    return description or type_label(_action_type(action))


# -----------------------------------------------------------------------------
# Template files (image wait)
# -----------------------------------------------------------------------------


def template_file(template_path: str, base_dir: Optional[Union[str, Path]] = None) -> Optional[Path]:
    """The file the engine opens for ``template_path`` (None when the path is empty).

    Mirrors ``WaitSystem.wait_image``: with a scenario folder the path goes through
    :func:`resolve_template_path`; without one it is relative to the working directory.
    """
    text = (template_path or "").strip()
    if not text:
        return None
    try:
        if base_dir is not None:
            return resolve_template_path(text, Path(base_dir))
        return Path(text).resolve()
    except (OSError, ValueError):
        return Path(text)


def template_state(template_path: str, base_dir: Optional[Union[str, Path]] = None) -> str:
    """``"empty"``, ``"found"``, ``"missing"`` or ``"placeholder"`` (the untouched default path
    of a new step whose file does not exist). Only checks that the file exists."""
    path = template_file(template_path, base_dir)
    if path is None:
        return "empty"
    try:
        exists = path.is_file()
    except (OSError, ValueError):
        exists = False
    if exists:
        return "found"
    return "placeholder" if (template_path or "").strip() == DEFAULT_TEMPLATE_PATH else "missing"
