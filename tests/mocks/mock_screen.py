"""Mock screen driver supplying synthetic frames for deterministic template matching.

Supports virtual canvas management, step-based frame mutation, ROI extraction,
and synthetic template generation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image


class MockScreenDriver:
    """Deterministic mock screen driver for vision and wait testing.

    Provides an in-memory virtual desktop canvas, virtual step clock,
    scheduled frame mutations, and synthetic template generators.
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        bg_color: Tuple[int, int, int] = (128, 128, 128),
        dpr: float = 1.0,
    ) -> None:
        self.width = width
        self.height = height
        self.bg_color = bg_color
        self.dpr = dpr
        self.step: int = 0
        self.auto_advance_step: bool = False

        # Virtual screen canvas: shape (H, W, 3), RGB uint8
        self._canvas: np.ndarray = np.zeros((height, width, 3), dtype=np.uint8)
        self.clear_canvas(bg_color)

        # Scheduled step events: step_number -> list of callbacks/mutations
        self._scheduled_events: Dict[int, List[Callable[[MockScreenDriver], None]]] = {}

    # -------------------------------------------------------------------------
    # Virtual Clock & Step Scheduling
    # -------------------------------------------------------------------------

    def set_step(self, step: int) -> None:
        """Sets the virtual step counter directly."""
        self.step = step
        self._apply_scheduled_events(self.step)

    def advance_step(self, delta: int = 1) -> int:
        """Advances virtual step and triggers any events scheduled for the new step."""
        for _ in range(delta):
            self.step += 1
            self._apply_scheduled_events(self.step)
        return self.step

    def schedule_event(
        self,
        step: int,
        patch: np.ndarray,
        x: int,
        y: int,
    ) -> None:
        """Schedules injection of an image patch at the designated step and position."""
        def _mutation(driver: MockScreenDriver) -> None:
            driver.inject_patch(patch, x, y)

        self._scheduled_events.setdefault(step, []).append(_mutation)

    def schedule_removal(
        self,
        step: int,
        x: int,
        y: int,
        width: int,
        height: int,
        fill_color: Optional[Tuple[int, int, int]] = None,
    ) -> None:
        """Schedules erasing a rectangular area at designated step."""
        color = fill_color or self.bg_color

        def _mutation(driver: MockScreenDriver) -> None:
            driver.remove_patch(x, y, width, height, color)

        self._scheduled_events.setdefault(step, []).append(_mutation)

    def schedule_action(
        self,
        step: int,
        action: Callable[[MockScreenDriver], None],
    ) -> None:
        """Schedules an arbitrary canvas mutation callback at designated step."""
        self._scheduled_events.setdefault(step, []).append(action)

    def _apply_scheduled_events(self, step: int) -> None:
        """Applies mutations registered for the current step."""
        if step in self._scheduled_events:
            for action in self._scheduled_events[step]:
                action(self)

    # -------------------------------------------------------------------------
    # Canvas Modification Operations
    # -------------------------------------------------------------------------

    def clear_canvas(self, fill_color: Optional[Tuple[int, int, int]] = None) -> None:
        """Fills entire canvas with solid color."""
        color = fill_color or self.bg_color
        self._canvas[:] = color

    def inject_patch(self, patch: np.ndarray, x: int, y: int) -> None:
        """Stamps an image patch onto canvas at (x, y).

        Accepts RGB patch of shape (H, W, 3) or (H, W).
        """
        if patch.ndim == 2:
            patch = np.stack([patch] * 3, axis=-1)

        ph, pw, _ = patch.shape
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(self.width, x + pw), min(self.height, y + ph)

        if x1 >= x2 or y1 >= y2:
            return

        patch_x1 = x1 - x
        patch_y1 = y1 - y
        patch_x2 = patch_x1 + (x2 - x1)
        patch_y2 = patch_y1 + (y2 - y1)

        self._canvas[y1:y2, x1:x2] = patch[patch_y1:patch_y2, patch_x1:patch_x2]

    def remove_patch(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        fill_color: Optional[Tuple[int, int, int]] = None,
    ) -> None:
        """Restores a rectangular area of canvas to background color."""
        color = fill_color or self.bg_color
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(self.width, x + width), min(self.height, y + height)
        if x1 < x2 and y1 < y2:
            self._canvas[y1:y2, x1:x2] = color

    # -------------------------------------------------------------------------
    # Capture & Frame Querying
    # -------------------------------------------------------------------------

    def capture(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> np.ndarray:
        """Captures the virtual screen canvas, optionally cropped to ROI (x, y, w, h).

        Returns an RGB numpy array (H, W, 3).
        """
        self._apply_scheduled_events(self.step)

        if roi is None:
            frame = self._canvas.copy()
        else:
            rx, ry, rw, rh = roi
            if rx < 0 or ry < 0 or rx + rw > self.width or ry + rh > self.height:
                raise ValueError(
                    f"ROI ({rx}, {ry}, {rw}, {rh}) exceeds screen bounds ({self.width}x{self.height})"
                )
            frame = self._canvas[ry:ry + rh, rx:rx + rw].copy()

        if self.auto_advance_step:
            self.advance_step(1)

        return frame

    def capture_grayscale(
        self,
        roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> np.ndarray:
        """Captures canvas directly as single-channel Grayscale array."""
        rgb = self.capture(roi)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    def get_pixel(self, x: int, y: int) -> Tuple[int, int, int]:
        """Returns RGB pixel value at screen coordinate (x, y)."""
        if 0 <= x < self.width and 0 <= y < self.height:
            p = self._canvas[y, x]
            return (int(p[0]), int(p[1]), int(p[2]))
        raise IndexError(f"Coordinate ({x}, {y}) out of screen bounds ({self.width}x{self.height})")

    def get_screen_size(self) -> Tuple[int, int]:
        """Returns logical screen dimensions (width, height) accounting for DPR."""
        return (int(round(self.width / self.dpr)), int(round(self.height / self.dpr)))

    def get_physical_size(self) -> Tuple[int, int]:
        """Returns native physical pixel dimensions."""
        return (self.width, self.height)

    # -------------------------------------------------------------------------
    # Synthetic Template Generation Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def create_test_template(
        width: int = 60,
        height: int = 30,
        color: Tuple[int, int, int] = (255, 120, 40),
        border_color: Tuple[int, int, int] = (10, 10, 10),
        border_width: int = 2,
        pattern: str = "cross",
    ) -> np.ndarray:
        """Creates a distinctive synthetic template image with high cross-correlation contrast."""
        patch = np.zeros((height, width, 3), dtype=np.uint8)
        patch[:] = color

        # Draw border
        if border_width > 0:
            patch[:border_width, :] = border_color
            patch[-border_width:, :] = border_color
            patch[:, :border_width] = border_color
            patch[:, -border_width:] = border_color

        # Draw interior pattern to guarantee high variance
        if pattern == "cross":
            mid_y = height // 2
            mid_x = width // 2
            cv2.line(patch, (mid_x - 10, mid_y), (mid_x + 10, mid_y), (255, 255, 255), 2)
            cv2.line(patch, (mid_x, mid_y - 8), (mid_x, mid_y + 8), (255, 255, 255), 2)
        elif pattern == "circle":
            mid_y = height // 2
            mid_x = width // 2
            cv2.circle(patch, (mid_x, mid_y), min(width, height) // 4, (255, 255, 255), -1)

        return patch

    @staticmethod
    def create_flat_template(
        width: int = 40,
        height: int = 40,
        fill_value: int = 128,
    ) -> np.ndarray:
        """Creates a zero-variance / flat solid color template for edge case testing."""
        return np.full((height, width, 3), fill_value, dtype=np.uint8)

    @staticmethod
    def save_template_file(patch: np.ndarray, filepath: Union[str, Path]) -> Path:
        """Saves a NumPy patch to an image file on disk."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.fromarray(patch, mode="RGB")
        img.save(path)
        return path
