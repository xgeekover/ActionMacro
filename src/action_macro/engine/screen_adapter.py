"""
action_macro.engine.screen_adapter
======================================
Screen capture adapter interface and MSS-based implementation.
"""

from __future__ import annotations

from typing import Optional, Protocol, Tuple, Union, runtime_checkable
import cv2
import mss
import numpy as np


@runtime_checkable
class ScreenAdapterProtocol(Protocol):
    """Protocol defining screen capture and display inspection capabilities."""

    def capture_screen(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> np.ndarray:
        """Captures the screen or ROI subregion.

        Args:
            roi: Optional 4-tuple (x, y, width, height) in screen coordinates.

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

    Supports context manager protocol for clean lifecycle management.
    """

    def __init__(self, monitor_index: int = 1) -> None:
        """Initializes MSSScreenAdapter.

        Args:
            monitor_index: MSS monitor index (1 is primary monitor, 0 is all monitors combined).
        """
        self.monitor_index = monitor_index
        # Support modern mss.MSS() and fallback to mss.mss()
        self._sct_factory = getattr(mss, "MSS", getattr(mss, "mss", None))
        self._sct: Optional[mss.base.MSSBase] = None

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

    def close(self) -> None:
        """Closes the MSS display connection."""
        if self._sct is not None:
            self._sct.close()
            self._sct = None

    def get_screen_size(self) -> Tuple[int, int]:
        """Returns the primary monitor dimensions (width, height)."""
        monitors = self.sct.monitors
        if self.monitor_index >= len(monitors):
            raise IndexError(
                f"Monitor index {self.monitor_index} out of range (found {len(monitors)} monitors)"
            )
        monitor = monitors[self.monitor_index]
        return (int(monitor["width"]), int(monitor["height"]))

    def capture_screen(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> np.ndarray:
        """Captures the screen or specified ROI subregion in BGR format."""
        monitors = self.sct.monitors
        if self.monitor_index >= len(monitors):
            raise IndexError(
                f"Monitor index {self.monitor_index} out of range (found {len(monitors)} monitors)"
            )
        monitor = monitors[self.monitor_index]

        mon_left = monitor["left"]
        mon_top = monitor["top"]
        mon_w = monitor["width"]
        mon_h = monitor["height"]

        if roi is None:
            grab_box = monitor
        else:
            rx, ry, rw, rh = roi
            if rw <= 0 or rh <= 0:
                raise ValueError(f"ROI dimensions must be positive, got width={rw}, height={rh}")
            if rx < 0 or ry < 0 or rx + rw > mon_w or ry + rh > mon_h:
                raise ValueError(
                    f"ROI ({rx}, {ry}, {rw}, {rh}) exceeds monitor bounds ({mon_w}x{mon_h})"
                )
            grab_box = {
                "left": mon_left + rx,
                "top": mon_top + ry,
                "width": rw,
                "height": rh,
            }

        sct_img = self.sct.grab(grab_box)
        # Convert MSS ScreenShot (BGRA) to BGR numpy array
        raw_bgra = np.array(sct_img, dtype=np.uint8)
        bgr = cv2.cvtColor(raw_bgra, cv2.COLOR_BGRA2BGR)
        return bgr

    def capture(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> np.ndarray:
        """Convenience alias for capture_screen conforming to MockScreenDriver interface."""
        return self.capture_screen(roi)

    def get_pixel_color(self, x: int, y: int) -> Tuple[int, int, int]:
        """Returns RGB pixel value at screen coordinate (x, y)."""
        w, h = self.get_screen_size()
        if not (0 <= x < w and 0 <= y < h):
            raise IndexError(f"Coordinate ({x}, {y}) out of screen bounds ({w}x{h})")

        mon = self.sct.monitors[self.monitor_index]
        grab_box = {
            "left": mon["left"] + x,
            "top": mon["top"] + y,
            "width": 1,
            "height": 1,
        }
        sct_img = self.sct.grab(grab_box)
        # sct_img.pixel(x, y) returns (r, g, b)
        return sct_img.pixel(0, 0)

    def get_pixel(self, x: int, y: int) -> Tuple[int, int, int]:
        """Convenience alias for get_pixel_color conforming to MockScreenDriver interface."""
        return self.get_pixel_color(x, y)
