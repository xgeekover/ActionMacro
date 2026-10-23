"""
action_macro.engine.image_matcher
=====================================
Grayscale OpenCV template matching engine with flat template fallback,
ROI offset mapping, and appearance/disappearance presence modes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Tuple, Union

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def to_grayscale(image: Union[np.ndarray, Any]) -> np.ndarray:
    """Converts a numpy array or image object to single-channel 8-bit Grayscale.

    Supports 2D (already grayscale), 3D BGR/RGB, 4D BGRA/RGBA arrays, and PIL Images.

    Raises:
        TypeError: If input is not a numpy array or PIL Image.
        ValueError: If image is empty or has unsupported dimensions/channels.
    """
    if hasattr(image, "convert") and not isinstance(image, np.ndarray):
        # PIL Image compatibility
        image = np.array(image)

    if not isinstance(image, np.ndarray):
        raise TypeError(f"Expected numpy.ndarray or PIL.Image, got {type(image).__name__}")

    if image.size == 0 or image.shape[0] == 0 or image.shape[1] == 0:
        raise ValueError(f"Image array cannot be empty: shape={image.shape}")

    if image.ndim == 2:
        return image if image.dtype == np.uint8 else image.astype(np.uint8)

    if image.ndim == 3:
        channels = image.shape[2]
        if channels == 1:
            return image[:, :, 0] if image.dtype == np.uint8 else image[:, :, 0].astype(np.uint8)
        elif channels == 3:
            # OpenCV standard is BGR; COLOR_BGR2GRAY and COLOR_RGB2GRAY produce
            # identical luminance for grayscale template matching.
            return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        elif channels == 4:
            return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        else:
            raise ValueError(f"Unsupported number of image channels: {channels}")

    raise ValueError(f"Invalid image dimensions: {image.shape}")


def load_template_image(
    template: Union[str, Path, np.ndarray, Any],
) -> np.ndarray:
    """Loads and decodes a template image from disk or validates an in-memory array.

    Returns an 8-bit Grayscale numpy array.

    Raises:
        FileNotFoundError: If template file does not exist.
        ValueError: If image file could not be decoded or is empty.
    """
    if isinstance(template, (str, Path)):
        tmpl_path = Path(template)
        if not tmpl_path.exists():
            raise FileNotFoundError(f"Template image file not found: {tmpl_path}")

        # Load with unchanged channels
        img = cv2.imread(str(tmpl_path), cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Could not decode template image: {tmpl_path}")
        return to_grayscale(img)

    return to_grayscale(template)


class ImageMatcher:
    """High-performance template matching engine using OpenCV."""

    @staticmethod
    def match_template(
        haystack: Union[np.ndarray, str, Path],
        needle: Union[np.ndarray, str, Path],
        roi: Optional[Tuple[int, int, int, int]] = None,
        verify_color: bool = False,
    ) -> Tuple[Tuple[int, int], float]:
        """Performs raw template matching returning ((center_x, center_y), score).

        Args:
            haystack: Screen capture array or image path.
            needle: Template patch array or image path.
            roi: Optional bounding box (x, y, width, height).
            verify_color: Whether to enforce additional RGB color distance check.

        Returns:
            Tuple of ((center_x, center_y), confidence_score).

        Raises:
            FileNotFoundError: If needle or haystack image file does not exist.
            ValueError: If template exceeds haystack or ROI dimensions, or invalid ROI.
        """
        raw_haystack = haystack if isinstance(haystack, np.ndarray) else load_template_image(haystack)
        raw_needle = needle if isinstance(needle, np.ndarray) else load_template_image(needle)

        gray_haystack = to_grayscale(raw_haystack)
        gray_needle = to_grayscale(raw_needle)

        templ_h, templ_w = gray_needle.shape[:2]
        hay_h, hay_w = gray_haystack.shape[:2]

        offset_x = 0
        offset_y = 0

        # Handle ROI
        if roi is not None:
            rx, ry, rw, rh = roi
            if rw <= 0 or rh <= 0:
                raise ValueError(f"Invalid ROI bounding box dimensions: {roi}")
            if rx < 0 or ry < 0:
                raise ValueError(f"Invalid ROI bounding box position: {roi}")

            # Check if haystack is already cropped to ROI (e.g. from screen adapter)
            if hay_w == rw and hay_h == rh:
                if templ_w > rw or templ_h > rh:
                    raise ValueError(
                        f"Template dimensions ({templ_w}x{templ_h}) exceed ROI bounding box ({rw}x{rh})"
                    )
                offset_x = rx
                offset_y = ry
                search_area = gray_haystack
            else:
                if rx + rw > hay_w or ry + rh > hay_h:
                    raise ValueError(
                        f"ROI ({rx}, {ry}, {rw}, {rh}) exceeds haystack bounds ({hay_w}x{hay_h})"
                    )
                if templ_w > rw or templ_h > rh:
                    raise ValueError(
                        f"Template dimensions ({templ_w}x{templ_h}) exceed ROI bounding box ({rw}x{rh})"
                    )
                offset_x = rx
                offset_y = ry
                search_area = gray_haystack[ry : ry + rh, rx : rx + rw]
        else:
            search_area = gray_haystack

        search_h, search_w = search_area.shape[:2]
        if templ_w > search_w or templ_h > search_h:
            raise ValueError(
                f"Template dimensions ({templ_w}x{templ_h}) exceed search bounds ({search_w}x{search_h})"
            )

        # Flat / Zero-variance template detection
        template_std = float(np.std(gray_needle))
        is_flat = template_std < 1.0

        if is_flat:
            # Fallback to normalized square difference for flat templates
            res = cv2.matchTemplate(search_area, gray_needle, cv2.TM_SQDIFF_NORMED)
            min_val, _, min_loc, _ = cv2.minMaxLoc(res)
            score = max(0.0, min(1.0, 1.0 - float(min_val)))
            best_loc = min_loc
        else:
            # Standard normalized cross-correlation
            res = cv2.matchTemplate(search_area, gray_needle, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            score = max(0.0, min(1.0, float(max_val))) if not np.isnan(max_val) else 0.0
            best_loc = max_loc

        # Optional color verification for color mismatch protection
        if verify_color and score > 0.0 and raw_haystack.ndim == 3 and raw_needle.ndim == 3:
            bx, by = best_loc
            matched_region = raw_haystack[offset_y + by : offset_y + by + templ_h, offset_x + bx : offset_x + bx + templ_w]
            if matched_region.shape == raw_needle.shape:
                mean_diff = float(np.mean(np.abs(matched_region.astype(float) - raw_needle.astype(float))))
                color_score = max(0.0, 1.0 - (mean_diff / 255.0))
                score = min(score, color_score)

        center_x = offset_x + best_loc[0] + templ_w // 2
        center_y = offset_y + best_loc[1] + templ_h // 2

        return ((center_x, center_y), score)

    @classmethod
    def find_template(
        cls,
        haystack: Union[np.ndarray, str, Path],
        needle: Union[np.ndarray, str, Path],
        confidence: float = 0.8,
        roi: Optional[Tuple[int, int, int, int]] = None,
        verify_color: bool = False,
    ) -> Optional[Tuple[int, int, float]]:
        """Searches for needle template in haystack.

        Returns (center_x, center_y, confidence) if matched with score >= confidence,
        otherwise None.
        """
        (center_x, center_y), score = cls.match_template(
            haystack=haystack,
            needle=needle,
            roi=roi,
            verify_color=verify_color,
        )

        if score >= confidence:
            return (center_x, center_y, score)
        return None

    @classmethod
    def check_match(
        cls,
        haystack: Union[np.ndarray, str, Path],
        needle: Union[np.ndarray, str, Path],
        confidence: float = 0.8,
        roi: Optional[Tuple[int, int, int, int]] = None,
        mode: str = "appear",
        verify_color: bool = False,
    ) -> Tuple[bool, Optional[Tuple[int, int]], float]:
        """Checks match condition based on appearance or disappearance mode.

        Returns:
            Tuple of (condition_met, center_coordinates_or_None, score).
        """
        mode_lower = mode.strip().lower()
        if mode_lower not in ("appear", "disappear"):
            raise ValueError(f"Invalid mode: {mode!r}. Must be 'appear' or 'disappear'")

        (center_x, center_y), score = cls.match_template(
            haystack=haystack,
            needle=needle,
            roi=roi,
            verify_color=verify_color,
        )

        is_present = score >= confidence

        if mode_lower == "appear":
            if is_present:
                return (True, (center_x, center_y), score)
            return (False, None, score)
        else:  # disappear
            if not is_present:
                return (True, None, score)
            return (False, None, score)


# Convenience module-level aliases
find_template = ImageMatcher.find_template
match_template = ImageMatcher.match_template
check_match = ImageMatcher.check_match
