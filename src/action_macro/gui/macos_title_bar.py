"""
action_macro.gui.macos_title_bar
====================================
One header row on macOS: the window content runs under the title bar and the top
bar takes its place, with the traffic-light buttons at its left end (as in VS Code).

Qt does the first half (``Qt.ExpandedClientAreaHint`` with
``Qt.NoTitleBarBackgroundHint``). On its own that leaves a 28 pt title bar with the
buttons near its top and the window title still drawn over the content, so AppKit
(pyobjc, installed with pynput) finishes it:

* an empty ``NSToolbar`` in the compact unified style makes the title bar 38 pt tall
  and centres the buttons in it;
* the title text is hidden. The window title still names the window in the Window
  menu, Mission Control and the Dock;
* no separator line under the title bar, because the top bar draws its own border.

On macOS 27 clicks in the title bar band reach the Qt widgets (only the three
buttons stay AppKit's), so the top bar moves the window itself (see
``TopBar.enable_window_drag``) and a double click does what System Settings > Desktop
& Dock asks for (:func:`double_click_title_bar`). On macOS 15 the toolbar view keeps
those clicks, so the top bar's buttons would not work: :func:`band_click_target`
tells, and the window then goes back to the normal title bar (:func:`undo`).
"""

from __future__ import annotations

import contextlib
import math
import sys
import warnings
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

#: Gap between the zoom (green) button and the first item of the top bar.
LEAD_GAP = 12

#: :func:`band_click_target` results.
CLICKS_REACH_CONTENT = "content"
CLICKS_STAY_IN_TITLE_BAR = "title bar"
CLICKS_NOT_PLACED_YET = "pending"

_TOOLBAR_ID = "ActionMacroTitleBar"


@dataclass(frozen=True)
class TitleBarBand:
    """The title bar strip at the top of the window, in Qt logical pixels (points)."""

    #: Height of the strip (38 with the compact unified toolbar).
    height: int
    #: Right edge of the zoom button, from the window's left edge.
    controls_right: int
    #: Vertical centre of the buttons, from the window's top edge.
    controls_centre: float


def _appkit() -> Optional[tuple[Any, Any]]:
    """Returns the ``(AppKit, objc)`` modules, or None when pyobjc is missing."""
    try:
        import AppKit
        import objc
    except Exception:  # pyobjc missing or broken
        return None
    return AppKit, objc


def supported() -> bool:
    """True on macOS with the real ``cocoa`` platform plugin and pyobjc available."""
    if sys.platform != "darwin" or QGuiApplication.platformName() != "cocoa":
        return False
    return _appkit() is not None


@contextlib.contextmanager
def _expanded_client_area_flags() -> Iterator[tuple[Qt.WindowType, Qt.WindowType]]:
    # PySide6 6.12 warns whenever ExpandedClientAreaHint is used, because the
    # deprecated MaximizeUsingFullscreenGeometryHint shares its value.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        yield Qt.WindowType.ExpandedClientAreaHint, Qt.WindowType.NoTitleBarBackgroundHint


def request_expanded_client_area(window: QWidget) -> None:
    """Asks Qt to run ``window``'s content under the title bar. Call it before the first show."""
    with _expanded_client_area_flags() as flags:
        for flag in flags:
            window.setWindowFlag(flag, True)
    # The top bar keeps the buttons' corner clear itself. With the default, Qt pushes
    # the whole content below the title bar again.
    window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)


def release_expanded_client_area(window: QWidget) -> None:
    """Undoes :func:`request_expanded_client_area`: the normal title bar comes back.

    Changing window flags hides a visible window; the caller shows it again.
    """
    with _expanded_client_area_flags() as flags:
        for flag in flags:
            window.setWindowFlag(flag, False)
    window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, True)


def has_expanded_client_area(window: QWidget) -> bool:
    """True while ``window`` has the flags set by :func:`request_expanded_client_area`."""
    with _expanded_client_area_flags() as flags:
        return all(window.windowFlags() & flag for flag in flags)


def ns_window(window: QWidget) -> Any:
    """Returns the ``NSWindow`` of a shown top-level ``window``, or None."""
    modules = _appkit()
    if modules is None:
        return None
    _, objc = modules
    view = objc.objc_object(c_void_p=int(window.winId()))  # the window's QNSView
    return view.window()


def finish(window: QWidget) -> Optional[TitleBarBand]:
    """Finishes the title bar of a shown ``window`` in AppKit and returns its band.

    Safe to call again (on every show). Returns None when AppKit is not available or
    did not take the changes; the caller should then go back to the normal title bar.
    """
    modules = _appkit()
    if modules is None:
        return None
    appkit, _ = modules
    try:
        native = ns_window(window)
        if native is None:
            return None
        toolbar = native.toolbar()
        if toolbar is None or toolbar.identifier() != _TOOLBAR_ID:
            toolbar = appkit.NSToolbar.alloc().initWithIdentifier_(_TOOLBAR_ID)
            toolbar.setAllowsUserCustomization_(False)
            native.setToolbar_(toolbar)
        native.setToolbarStyle_(appkit.NSWindowToolbarStyleUnifiedCompact)
        native.setTitleVisibility_(appkit.NSWindowTitleHidden)
        native.setTitlebarSeparatorStyle_(appkit.NSTitlebarSeparatorStyleNone)
        return _band(appkit, native)
    except Exception:  # an AppKit call failed: keep the normal title bar
        return None


def band_click_target(window: QWidget, band: TitleBarBand) -> str:
    """Where AppKit sends a click in the band beside the buttons (no input is sent).

    Returns :data:`CLICKS_REACH_CONTENT` when the Qt view gets it (macOS 27),
    :data:`CLICKS_STAY_IN_TITLE_BAR` when a title bar view keeps it (the toolbar view
    on macOS 15, where the top bar's buttons would be dead), or
    :data:`CLICKS_NOT_PLACED_YET` while AppKit has not laid the window out yet (still
    the case in the first showEvent): ask again later.
    """
    modules = _appkit()
    native = ns_window(window) if modules is not None else None
    if native is None:
        return CLICKS_STAY_IN_TITLE_BAR
    appkit, _ = modules
    try:
        frame_view = native.contentView().superview()
        content = native.contentView()
        size = native.frame().size
        y = size.height - band.height / 2  # AppKit window coordinates: origin bottom left
        result = CLICKS_REACH_CONTENT
        for x in (band.controls_right + 4 * LEAD_GAP, size.width - 2 * LEAD_GAP):
            hit = frame_view.hitTest_(appkit.NSMakePoint(x, y))
            if hit is None or hit == frame_view:
                result = CLICKS_NOT_PLACED_YET
            elif not (hit == content or hit.isDescendantOf_(content)):
                return CLICKS_STAY_IN_TITLE_BAR
        return result
    except Exception:  # an AppKit call failed: assume the clicks do not get through
        return CLICKS_STAY_IN_TITLE_BAR


def undo(window: QWidget) -> None:
    """Takes back what :func:`finish` did (the toolbar, the hidden title, no separator)."""
    modules = _appkit()
    native = ns_window(window) if modules is not None else None
    if native is None:
        return
    appkit, _ = modules
    try:
        toolbar = native.toolbar()
        if toolbar is not None and toolbar.identifier() == _TOOLBAR_ID:
            native.setToolbar_(None)
        native.setToolbarStyle_(appkit.NSWindowToolbarStyleAutomatic)
        native.setTitleVisibility_(appkit.NSWindowTitleVisible)
        native.setTitlebarSeparatorStyle_(appkit.NSTitlebarSeparatorStyleAutomatic)
    except Exception:  # best effort: the flags are dropped either way
        pass


def _band(appkit: Any, native: Any) -> Optional[TitleBarBand]:
    frame_height = native.frame().size.height
    layout = native.contentLayoutRect()  # window coordinates, origin bottom left
    height = round(frame_height - (layout.origin.y + layout.size.height))
    zoom = native.standardWindowButton_(appkit.NSWindowZoomButton)
    if height <= 0 or zoom is None:
        return None
    rect = zoom.convertRect_toView_(zoom.bounds(), None)
    return TitleBarBand(height=height, controls_right=math.ceil(rect.origin.x + rect.size.width),
                        controls_centre=frame_height - (rect.origin.y + rect.size.height / 2))


def double_click_action(action: Optional[str], legacy_minimize: bool = False) -> str:
    """Maps the ``AppleActionOnDoubleClick`` setting to "zoom", "minimize" or "none".

    ``action`` is unset until the user changes the setting; older systems only had
    ``AppleMiniaturizeOnDoubleClick`` (``legacy_minimize``). "Fill" (macOS 15+) has
    no public AppKit call, so it zooms like "Maximize".
    """
    if action is None:
        return "minimize" if legacy_minimize else "zoom"
    if action == "Minimize":
        return "minimize"
    if action == "None":
        return "none"
    return "zoom"


def double_click_title_bar(window: QWidget) -> None:
    """Does what a double click on a title bar does (System Settings > Desktop & Dock)."""
    modules = _appkit()
    native = ns_window(window) if modules is not None else None
    if native is None:
        return
    appkit, _ = modules
    defaults = appkit.NSUserDefaults.standardUserDefaults()
    action = double_click_action(defaults.stringForKey_("AppleActionOnDoubleClick"),
                                 bool(defaults.boolForKey_("AppleMiniaturizeOnDoubleClick")))
    if action == "minimize":
        native.performMiniaturize_(None)
    elif action == "zoom":
        native.performZoom_(None)
