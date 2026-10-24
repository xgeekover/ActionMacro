"""
action_macro.engine.macos_permissions
========================================
Passive status checks of the macOS privacy permissions ActionMacro relies on.

=================  ==========================================================
Permission         Needed for
=================  ==========================================================
Accessibility      sending mouse clicks and key presses (click / key steps)
Input Monitoring   the global emergency-stop listener (Esc / Ctrl+Shift+S)
Screen Recording   image-wait steps and the coordinate picker's magnifier
=================  ==========================================================

Every check here is *passive*: it only reads the current status and never makes
macOS show a permission prompt. Accessibility uses ``AXIsProcessTrusted`` (not
the variant with the prompt option), Screen Recording uses
``CGPreflightScreenCaptureAccess`` (not the request call) and Input Monitoring
uses IOKit's ``IOHIDCheckAccess`` (not the request call).

macOS grants these permissions to the app that started ActionMacro (Terminal,
iTerm, Warp, an IDE, or a packaged ActionMacro app), see
:func:`launcher_app_name`. Changes to Screen Recording and Input Monitoring only
take effect after that app is restarted.

A *checker* is any callable that returns ``{Permission: PermissionStatus}``.
:func:`check_permissions` is the real one; the GUI uses :data:`default_checker`
unless a checker is injected, so tests can replace it (see ``tests/conftest.py``).
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional

from action_macro.core.models import ActionType

logger = logging.getLogger(__name__)


class Permission(str, Enum):
    """A macOS privacy permission ActionMacro relies on."""

    ACCESSIBILITY = "accessibility"
    INPUT_MONITORING = "input_monitoring"
    SCREEN_RECORDING = "screen_recording"


class PermissionStatus(str, Enum):
    """Status of one permission as reported by a passive check."""

    GRANTED = "granted"
    MISSING = "missing"
    UNKNOWN = "unknown"            # the check failed, or macOS has not decided yet
    NOT_REQUIRED = "not_required"  # not macOS: nothing to allow


PermissionReport = Dict[Permission, PermissionStatus]
PermissionChecker = Callable[[], Mapping[Permission, PermissionStatus]]


@dataclass(frozen=True)
class PermissionInfo:
    """Display texts and the System Settings pane of one permission."""

    title: str
    purpose: str      # one line for the permissions panel
    used_for: str     # short phrase for log lines ("click and key steps")
    pane: str         # anchor of the Privacy & Security pane
    needs_restart: bool  # changes apply only after the launching app restarts

    @property
    def settings_url(self) -> str:
        """URL that opens this permission's list in System Settings."""
        return f"{SETTINGS_URL_BASE}?{self.pane}"


SETTINGS_URL_BASE = "x-apple.systempreferences:com.apple.preference.security"

#: Display order and texts of the permissions (Accessibility first).
PERMISSIONS: Dict[Permission, PermissionInfo] = {
    Permission.ACCESSIBILITY: PermissionInfo(
        title="Accessibility",
        purpose="Lets click and key steps control the mouse and keyboard.",
        used_for="click and key steps",
        pane="Privacy_Accessibility",
        needs_restart=False,
    ),
    Permission.INPUT_MONITORING: PermissionInfo(
        title="Input Monitoring",
        purpose="Lets Esc or Ctrl+Shift+S stop a run from any app.",
        used_for="Esc / Ctrl+Shift+S stop keys",
        pane="Privacy_ListenEvent",
        needs_restart=True,
    ),
    Permission.SCREEN_RECORDING: PermissionInfo(
        title="Screen Recording",
        purpose="Lets image-wait steps and the picker's magnifier see the screen.",
        used_for="image-wait steps",
        pane="Privacy_ScreenCapture",
        needs_restart=True,
    ),
}

#: Short labels of the statuses (status pills).
STATUS_LABELS: Dict[PermissionStatus, str] = {
    PermissionStatus.GRANTED: "Granted",
    PermissionStatus.MISSING: "Missing",
    PermissionStatus.UNKNOWN: "Unknown",
    PermissionStatus.NOT_REQUIRED: "Not needed",
}


def info(permission: Permission) -> PermissionInfo:
    """Returns the display texts of ``permission``."""
    return PERMISSIONS[Permission(permission)]


def settings_url(permission: Permission) -> str:
    """Returns the System Settings URL of ``permission`` (Privacy & Security pane)."""
    return info(permission).settings_url


def is_macos(platform: Optional[str] = None) -> bool:
    """True on macOS (``platform`` defaults to ``sys.platform``)."""
    return (platform if platform is not None else sys.platform) == "darwin"


# -----------------------------------------------------------------------------
# Passive checks (each one returns UNKNOWN instead of raising)
# -----------------------------------------------------------------------------

#: IOKit's ``kIOHIDRequestTypeListenEvent`` (Input Monitoring).
IOHID_REQUEST_TYPE_LISTEN_EVENT = 1
#: IOKit's ``IOHIDAccessType`` values: granted, denied, unknown (not decided yet).
_IOHID_ACCESS_STATUS: Dict[int, PermissionStatus] = {
    0: PermissionStatus.GRANTED,
    1: PermissionStatus.MISSING,
    2: PermissionStatus.UNKNOWN,
}
_IOKIT_PATH = "/System/Library/Frameworks/IOKit.framework/IOKit"


def _ax_is_process_trusted() -> bool:
    from ApplicationServices import AXIsProcessTrusted  # pyobjc, installed with pynput

    return bool(AXIsProcessTrusted())


def _cg_preflight_screen_capture_access() -> bool:
    from Quartz import CGPreflightScreenCaptureAccess  # pyobjc, macOS 10.15+

    return bool(CGPreflightScreenCaptureAccess())


def _iohid_check_access(request_type: int) -> int:
    iokit = ctypes.CDLL(_IOKIT_PATH)
    function = iokit.IOHIDCheckAccess  # macOS 10.15+
    function.argtypes = [ctypes.c_uint32]
    function.restype = ctypes.c_uint32
    return int(function(request_type))


def check_accessibility(is_trusted: Optional[Callable[[], bool]] = None) -> PermissionStatus:
    """Accessibility: may this process send mouse and keyboard events?

    Args:
        is_trusted: Replacement for ``AXIsProcessTrusted`` (tests).
    """
    try:
        trusted = (is_trusted or _ax_is_process_trusted)()
    except Exception as exc:
        logger.debug("Accessibility status could not be read: %s", exc)
        return PermissionStatus.UNKNOWN
    return PermissionStatus.GRANTED if trusted else PermissionStatus.MISSING


def check_screen_recording(preflight: Optional[Callable[[], bool]] = None) -> PermissionStatus:
    """Screen Recording: may this process capture the screen?

    Args:
        preflight: Replacement for ``CGPreflightScreenCaptureAccess`` (tests).
    """
    try:
        allowed = (preflight or _cg_preflight_screen_capture_access)()
    except Exception as exc:
        logger.debug("Screen Recording status could not be read: %s", exc)
        return PermissionStatus.UNKNOWN
    return PermissionStatus.GRANTED if allowed else PermissionStatus.MISSING


def check_input_monitoring(check_access: Optional[Callable[[int], int]] = None) -> PermissionStatus:
    """Input Monitoring: may this process listen to global key events?

    Args:
        check_access: Replacement for ``IOHIDCheckAccess`` (tests); it receives
            ``IOHID_REQUEST_TYPE_LISTEN_EVENT`` and returns 0 (granted),
            1 (denied) or 2 (unknown).
    """
    try:
        access = int((check_access or _iohid_check_access)(IOHID_REQUEST_TYPE_LISTEN_EVENT))
    except Exception as exc:
        logger.debug("Input Monitoring status could not be read: %s", exc)
        return PermissionStatus.UNKNOWN
    return _IOHID_ACCESS_STATUS.get(access, PermissionStatus.UNKNOWN)


def check_permissions(platform: Optional[str] = None) -> PermissionReport:
    """Returns the status of every permission (passive checks only).

    Off macOS every permission is ``NOT_REQUIRED`` and nothing is queried.
    """
    if not is_macos(platform):
        return {permission: PermissionStatus.NOT_REQUIRED for permission in PERMISSIONS}
    return {
        Permission.ACCESSIBILITY: check_accessibility(),
        Permission.INPUT_MONITORING: check_input_monitoring(),
        Permission.SCREEN_RECORDING: check_screen_recording(),
    }


#: The checker the GUI uses when none is injected. Tests replace it with a fake.
default_checker: PermissionChecker = check_permissions


def static_checker(statuses: Mapping[Permission, PermissionStatus]) -> PermissionChecker:
    """Returns a checker that always reports ``statuses`` (fakes, previews)."""
    fixed = normalize_report(statuses)

    def _checker() -> PermissionReport:
        return dict(fixed)

    return _checker


def all_granted() -> PermissionReport:
    """A report in which every permission is granted."""
    return {permission: PermissionStatus.GRANTED for permission in PERMISSIONS}


# -----------------------------------------------------------------------------
# Report helpers
# -----------------------------------------------------------------------------


def normalize_report(report: Optional[Mapping[Permission, PermissionStatus]]) -> PermissionReport:
    """Returns ``report`` in display order with every permission present.

    Missing entries and values that are not a status become ``UNKNOWN``.
    """
    result: PermissionReport = {}
    source = dict(report or {})
    for permission in PERMISSIONS:
        value = source.get(permission, source.get(permission.value))
        try:
            result[permission] = PermissionStatus(value)
        except ValueError:
            result[permission] = PermissionStatus.UNKNOWN
    return result


def missing_permissions(report: Optional[Mapping[Permission, PermissionStatus]]) -> List[Permission]:
    """Returns the permissions reported ``MISSING``, in display order."""
    normalized = normalize_report(report)
    return [p for p, status in normalized.items() if status == PermissionStatus.MISSING]


def is_not_required(report: Optional[Mapping[Permission, PermissionStatus]]) -> bool:
    """True when the report says no permission is needed on this system (not macOS)."""
    normalized = normalize_report(report)
    return all(status == PermissionStatus.NOT_REQUIRED for status in normalized.values())


def join_titles(permissions: Iterable[Permission]) -> str:
    """Returns "A", "A and B" or "A, B and C" from the permissions' titles."""
    titles = [info(p).title for p in permissions]
    if len(titles) <= 1:
        return "".join(titles)
    return f"{', '.join(titles[:-1])} and {titles[-1]}"


def required_permissions(
    action_types: Iterable[object],
    *,
    stop_listener: bool = True,
) -> List[Permission]:
    """Returns the permissions a run with these step types needs, in display order.

    Click and key steps need Accessibility, image-wait steps need Screen
    Recording, and the emergency-stop listener (on for every run) needs Input
    Monitoring. Unknown step types are ignored.
    """
    needed = set()
    for action_type in action_types:
        try:
            kind = action_type if isinstance(action_type, ActionType) else ActionType(action_type)
        except ValueError:
            continue
        if kind in (ActionType.MOUSE_CLICK, ActionType.KEY_INPUT):
            needed.add(Permission.ACCESSIBILITY)
        elif kind == ActionType.IMAGE_WAIT:
            needed.add(Permission.SCREEN_RECORDING)
    if stop_listener:
        needed.add(Permission.INPUT_MONITORING)
    return [permission for permission in PERMISSIONS if permission in needed]


def required_for_scenario(scenario: object, *, stop_listener: bool = True) -> List[Permission]:
    """Returns the permissions a run of ``scenario`` needs (enabled steps only)."""
    types = [
        getattr(action, "type", None)
        for action in (getattr(scenario, "actions", None) or [])
        if getattr(action, "enabled", True)
    ]
    return required_permissions(types, stop_listener=stop_listener)


# -----------------------------------------------------------------------------
# The app macOS attributes the permissions to
# -----------------------------------------------------------------------------

#: Bundle identifiers (``__CFBundleIdentifier`` of the launching app) -> app name.
_BUNDLE_APP_NAMES: Dict[str, str] = {
    "com.apple.Terminal": "Terminal",
    "com.googlecode.iterm2": "iTerm",
    "dev.warp.Warp-Stable": "Warp",
    "dev.warp.Warp-Preview": "Warp Preview",
    "com.microsoft.VSCode": "Visual Studio Code",
    "com.microsoft.VSCodeInsiders": "Visual Studio Code - Insiders",
    "com.mitchellh.ghostty": "Ghostty",
    "com.github.wez.wezterm": "WezTerm",
    "net.kovidgoyal.kitty": "kitty",
    "org.alacritty": "Alacritty",
    "co.zeit.hyper": "Hyper",
    "com.jetbrains.pycharm": "PyCharm",
    "com.jetbrains.pycharm.ce": "PyCharm CE",
}

#: ``TERM_PROGRAM`` values -> app name (used when the bundle id is unknown).
_TERM_PROGRAM_NAMES: Dict[str, str] = {
    "Apple_Terminal": "Terminal",
    "iTerm.app": "iTerm",
    "WarpTerminal": "Warp",
    "vscode": "Visual Studio Code",
    "ghostty": "Ghostty",
    "WezTerm": "WezTerm",
    "Hyper": "Hyper",
}


def _bundle_name(executable: str) -> Optional[str]:
    """Returns ``Foo`` for an executable inside ``…/Foo.app/Contents/MacOS/``."""
    parts = Path(executable).parts
    for index in range(len(parts) - 2):
        if parts[index].endswith(".app") and parts[index + 1] == "Contents" and parts[index + 2] == "MacOS":
            return parts[index][: -len(".app")] or None
    return None


def launcher_app_name(
    environ: Optional[Mapping[str, str]] = None,
    *,
    executable: Optional[str] = None,
    frozen: Optional[bool] = None,
) -> Optional[str]:
    """Best guess of the app macOS attributes ActionMacro's permissions to.

    A packaged ActionMacro app (frozen executable inside an ``.app`` bundle)
    answers for itself. Otherwise the app that started the process is read from
    the environment it passed on (``__CFBundleIdentifier``, then
    ``TERM_PROGRAM``). Returns ``None`` when it cannot be told.
    """
    env = os.environ if environ is None else environ
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        name = _bundle_name(executable if executable is not None else sys.executable)
        if name:
            return name
    bundle = env.get("__CFBundleIdentifier", "")
    if bundle in _BUNDLE_APP_NAMES:
        return _BUNDLE_APP_NAMES[bundle]
    return _TERM_PROGRAM_NAMES.get(env.get("TERM_PROGRAM", ""))
