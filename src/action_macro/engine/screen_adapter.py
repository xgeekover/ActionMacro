"""
action_macro.engine.screen_adapter
======================================
Screen capture adapter interface and MSS-based implementation.

Coordinate contract: every ROI, capture origin and pixel coordinate handled by
:class:`MSSScreenAdapter` is expressed in the global virtual-desktop space that the
input adapter uses for clicks. The primary monitor's top-left corner is (0, 0);
monitors placed left of or above it have negative coordinates. On macOS this space
is measured in points (mss captures with ``kCGWindowImageNominalResolution``, one
pixel per point, and pynput posts ``CGEvent`` positions in the same global display
points). On Windows mss makes the process per-monitor DPI aware, so captures and
pynput's ``SetCursorPos`` both use physical pixels.
"""

from __future__ import annotations

import logging
import sys
from typing import Dict, Optional, Protocol, Sequence, Tuple, Union, runtime_checkable
import cv2
import mss
import numpy as np

logger = logging.getLogger(__name__)

#: Display scale factors assumed when the real ones cannot be queried.
COMMON_DISPLAY_SCALES: Tuple[float, ...] = (1.25, 1.5, 2.0)

#: Backing scale factor of Retina panels. macOS screenshots (Cmd+Shift+4) are saved
#: at the backing resolution of the display they were taken on.
RETINA_SCALE: float = 2.0


def detect_display_scale_factors(platform: Optional[str] = None) -> Optional[Tuple[float, ...]]:
    """Returns the backing scale factors of the active displays, or None if unknown.

    On macOS the factor of each display is the ratio of its current display mode's
    pixel width to its point width (Quartz ``CGDisplayModeGetPixelWidth`` /
    ``CGDisplayModeGetWidth``). These queries read display configuration only and do
    not require the Screen Recording permission.

    Args:
        platform: Platform identifier to evaluate (defaults to ``sys.platform``).

    Returns:
        Sorted tuple of distinct scale factors, or None on other platforms or when the
        query fails.
    """
    if (platform or sys.platform).lower() != "darwin":
        return None
    try:
        import Quartz  # type: ignore[import-not-found]

        err, display_ids, count = Quartz.CGGetActiveDisplayList(32, None, None)
        if err != 0 or not count:
            return None
        scales = set()
        for display_id in list(display_ids)[: int(count)]:
            mode = Quartz.CGDisplayCopyDisplayMode(display_id)
            if mode is None:
                continue
            pixel_width = float(Quartz.CGDisplayModeGetPixelWidth(mode))
            point_width = float(Quartz.CGDisplayModeGetWidth(mode))
            if pixel_width > 0 and point_width > 0:
                scales.add(round(pixel_width / point_width, 3))
        return tuple(sorted(scales)) or None
    except Exception as err:  # pyobjc missing, no window server, API failure
        logger.debug("Display scale detection failed: %s", err)
        return None


def candidate_template_scales(
    display_scales: Optional[Sequence[float]] = None,
) -> Tuple[float, ...]:
    """Builds the template resize factors to try against a capture.

    A template recorded at a display's backing resolution is ``1/scale`` times the
    size it has in a nominal-resolution capture.

    Args:
        display_scales: Known display scale factors, or None/empty when unknown, in
            which case :data:`COMMON_DISPLAY_SCALES` are assumed.

    Returns:
        ``1.0`` first, followed by ``1/scale`` for every scale factor above 1.0, from
        the mildest to the strongest reduction.
    """
    known = [float(s) for s in (display_scales or ()) if s and float(s) > 0]
    if not known:
        known = list(COMMON_DISPLAY_SCALES)
    factors = [1.0]
    for scale in sorted(set(known)):
        if scale > 1.0:
            factors.append(round(1.0 / scale, 6))
    return tuple(factors)


@runtime_checkable
class ScreenAdapterProtocol(Protocol):
    """Protocol defining screen capture and display inspection capabilities."""

    def capture_screen(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> np.ndarray:
        """Captures the screen or ROI subregion.

        Args:
            roi: Optional 4-tuple (x, y, width, height) in global screen coordinates
                (the coordinate space used for mouse clicks).

        Returns:
            np.ndarray: BGR image array with shape (H, W, 3) and dtype uint8.

        Raises:
            ValueError: If ROI has non-positive dimensions or exceeds screen bounds.
        """
        ...

    def get_screen_size(self) -> Tuple[int, int]:
        """Returns the dimensions (width, height) of the primary screen in pixels."""
        ...

    def get_pixel_color(self, x: int, y: int) -> Tuple[int, int, int]:
        """Returns the RGB color tuple (R, G, B) of the pixel at coordinate (x, y).

        Args:
            x: Horizontal pixel coordinate.
            y: Vertical pixel coordinate.

        Returns:
            Tuple[int, int, int]: RGB color values (0-255).

        Raises:
            IndexError: If (x, y) is out of screen bounds.
        """
        ...


class MSSScreenAdapter:
    """Production screen adapter using Python MSS for high-performance screen capture.

    All coordinates are global virtual-desktop coordinates (see the module docstring),
    so a region on a secondary monitor left of or above the primary one is addressed
    with negative values. The MSS connection is opened lazily on first use, on the
    calling thread, because platform resources (Windows device contexts, X11
    connections) are bound to the thread that created them.

    Supports context manager protocol for clean lifecycle management.
    """

    def __init__(self, monitor_index: int = 0, platform_name: Optional[str] = None) -> None:
        """Initializes MSSScreenAdapter.

        Args:
            monitor_index: MSS monitor captured when no ROI is given. 0 (default) is the
                whole virtual desktop spanning every monitor, 1 is the primary monitor,
                N is monitor N.
            platform_name: Platform identifier used for display scale handling
                (defaults to ``sys.platform``).
        """
        self.monitor_index = monitor_index
        self._platform = (platform_name or sys.platform).lower()
        # Support modern mss.MSS() and fallback to mss.mss()
        self._sct_factory = getattr(mss, "MSS", getattr(mss, "mss", None))
        self._sct: Optional[mss.base.MSSBase] = None
        self._template_scales: Optional[Tuple[float, ...]] = None

    @property
    def sct(self) -> mss.base.MSSBase:
        """Lazy-initialized MSS instance."""
        if self._sct is None:
            self._sct = self._sct_factory()
        return self._sct

    def __enter__(self) -> MSSScreenAdapter:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def open(self) -> MSSScreenAdapter:
        """Opens the MSS connection on the calling thread (idempotent)."""
        _ = self.sct
        return self

    def close(self) -> None:
        """Closes the MSS display connection."""
        if self._sct is not None:
            self._sct.close()
            self._sct = None

    def _monitor(self, index: int) -> Dict[str, int]:
        """Returns the MSS monitor rectangle at ``index`` in global coordinates."""
        monitors = self.sct.monitors
        if not 0 <= index < len(monitors):
            raise IndexError(
                f"Monitor index {index} out of range (found {len(monitors)} monitors)"
            )
        mon = monitors[index]
        return {
            "left": int(mon["left"]),
            "top": int(mon["top"]),
            "width": int(mon["width"]),
            "height": int(mon["height"]),
        }

    def _check_inside_virtual_desktop(self, x: int, y: int, w: int, h: int) -> None:
        """Raises ValueError if the rectangle leaves the virtual desktop (monitor 0)."""
        desktop = self._monitor(0)
        right = desktop["left"] + desktop["width"]
        bottom = desktop["top"] + desktop["height"]
        if x < desktop["left"] or y < desktop["top"] or x + w > right or y + h > bottom:
            raise ValueError(
                f"ROI ({x}, {y}, {w}, {h}) exceeds virtual desktop bounds "
                f"(left={desktop['left']}, top={desktop['top']}, "
                f"{desktop['width']}x{desktop['height']})"
            )

    def get_screen_size(self) -> Tuple[int, int]:
        """Returns the (width, height) of the monitor captured when no ROI is given."""
        monitor = self._monitor(self.monitor_index)
        return (monitor["width"], monitor["height"])

    def get_capture_origin(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> Tuple[int, int]:
        """Returns the global coordinate of the top-left pixel of ``capture_screen(roi)``."""
        if roi is not None:
            return (int(roi[0]), int(roi[1]))
        monitor = self._monitor(self.monitor_index)
        return (monitor["left"], monitor["top"])

    def get_template_scales(self) -> Tuple[float, ...]:
        """Returns the template resize factors worth trying against this adapter's captures.

        Only macOS needs more than ``1.0``: there captures are taken in points, whereas
        a template saved from a Retina screenshot holds backing pixels. ``1/scale``
        follows for each detected display scale factor, or for
        :data:`COMMON_DISPLAY_SCALES` when detection is unavailable, and the Retina
        factor is always included, because templates shot on a laptop's Retina panel
        are commonly replayed on a 1x external display.

        Elsewhere captures and screenshots are both in physical pixels, so only the
        native size is tried. Extra scales would only let a template match a smaller
        look-alike (a HUD icon, a zoom-in animation) and multiply the cost of each
        uninterruptible poll.
        """
        if self._template_scales is None:
            if self._platform != "darwin":
                self._template_scales = (1.0,)
            else:
                detected = detect_display_scale_factors(self._platform)
                if detected is not None:
                    detected = tuple(detected) + (RETINA_SCALE,)
                self._template_scales = candidate_template_scales(detected)
        return self._template_scales

    def capture_screen(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> np.ndarray:
        """Captures the screen or specified ROI subregion in BGR format.

        Args:
            roi: Optional (x, y, width, height) in global virtual-desktop coordinates;
                it may lie on any monitor, including at negative coordinates. Without
                an ROI the monitor selected by ``monitor_index`` is captured.

        Returns:
            BGR image with one pixel per global coordinate unit, so the pixel at
            (col, row) maps to ``get_capture_origin(roi) + (col, row)``.

        Raises:
            ValueError: If the ROI has non-positive dimensions or leaves the virtual desktop.
        """
        if roi is None:
            grab_box = self._monitor(self.monitor_index)
        else:
            rx, ry, rw, rh = (int(v) for v in roi)
            if rw <= 0 or rh <= 0:
                raise ValueError(f"ROI dimensions must be positive, got width={rw}, height={rh}")
            self._check_inside_virtual_desktop(rx, ry, rw, rh)
            grab_box = {"left": rx, "top": ry, "width": rw, "height": rh}

        sct_img = self.sct.grab(grab_box)
        # Convert MSS ScreenShot (BGRA) to BGR numpy array
        raw_bgra = np.array(sct_img, dtype=np.uint8)
        bgr = cv2.cvtColor(raw_bgra, cv2.COLOR_BGRA2BGR)

        # Keep the frame in click units even if the backend returned backing pixels
        # (e.g. mss.darwin.IMAGE_OPTIONS without kCGWindowImageNominalResolution).
        box_w, box_h = grab_box["width"], grab_box["height"]
        if bgr.shape[1] != box_w or bgr.shape[0] != box_h:
            bgr = cv2.resize(bgr, (box_w, box_h), interpolation=cv2.INTER_AREA)
        return bgr

    def capture(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> np.ndarray:
        """Convenience alias for capture_screen conforming to MockScreenDriver interface."""
        return self.capture_screen(roi)

    def get_pixel_color(self, x: int, y: int) -> Tuple[int, int, int]:
        """Returns RGB pixel value at global screen coordinate (x, y)."""
        try:
            self._check_inside_virtual_desktop(int(x), int(y), 1, 1)
        except ValueError as err:
            raise IndexError(f"Coordinate ({x}, {y}) out of screen bounds: {err}") from err

        grab_box = {"left": int(x), "top": int(y), "width": 1, "height": 1}
        sct_img = self.sct.grab(grab_box)
        # sct_img.pixel(x, y) returns (r, g, b)
        return sct_img.pixel(0, 0)

    def get_pixel(self, x: int, y: int) -> Tuple[int, int, int]:
        """Convenience alias for get_pixel_color conforming to MockScreenDriver interface."""
        return self.get_pixel_color(x, y)
