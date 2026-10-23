"""
action_macro.engine.coordinate_transformer
==============================================
Bidirectional coordinate transformations between physical display pixels and
logical desktop points across display scale factors (DPR / Retina), with
strict boundary checking and bounding-box clamping.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple, Union


class CoordinateTransformer:
    """Transforms coordinates between physical screen pixels and logical points.

    Supports arbitrary display pixel ratios (e.g. 1.0, 1.25, 1.5, 2.0),
    screen canvas boundaries, and region-of-interest (ROI) bounding boxes.
    """

    @staticmethod
    def physical_to_logical(
        x: Union[int, float],
        y: Union[int, float],
        dpr: float = 1.0,
    ) -> Tuple[int, int]:
        """Convert physical screen pixels to logical desktop points.

        Args:
            x: Physical X pixel coordinate.
            y: Physical Y pixel coordinate.
            dpr: Device pixel ratio (must be > 0.0, default 1.0).

        Returns:
            Tuple[int, int]: (logical_x, logical_y) rounded to nearest integer.

        Raises:
            TypeError: If coordinates or dpr are not numeric or are boolean.
            ValueError: If dpr is non-positive.
        """
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            raise TypeError(f"x coordinate must be numeric, got {type(x).__name__}")
        if not isinstance(y, (int, float)) or isinstance(y, bool):
            raise TypeError(f"y coordinate must be numeric, got {type(y).__name__}")
        if not isinstance(dpr, (int, float)) or isinstance(dpr, bool):
            raise TypeError(f"DPR must be numeric, got {type(dpr).__name__}")
        if dpr <= 0.0:
            raise ValueError(f"DPR must be strictly positive, got {dpr}")

        return (int(round(float(x) / float(dpr))), int(round(float(y) / float(dpr))))

    @staticmethod
    def logical_to_physical(
        x: Union[int, float],
        y: Union[int, float],
        dpr: float = 1.0,
    ) -> Tuple[int, int]:
        """Convert logical desktop points to physical screen pixels.

        Args:
            x: Logical X coordinate.
            y: Logical Y coordinate.
            dpr: Device pixel ratio (must be > 0.0, default 1.0).

        Returns:
            Tuple[int, int]: (physical_x, physical_y) rounded to nearest integer.

        Raises:
            TypeError: If coordinates or dpr are not numeric or are boolean.
            ValueError: If dpr is non-positive.
        """
        if not isinstance(x, (int, float)) or isinstance(x, bool):
            raise TypeError(f"x coordinate must be numeric, got {type(x).__name__}")
        if not isinstance(y, (int, float)) or isinstance(y, bool):
            raise TypeError(f"y coordinate must be numeric, got {type(y).__name__}")
        if not isinstance(dpr, (int, float)) or isinstance(dpr, bool):
            raise TypeError(f"DPR must be numeric, got {type(dpr).__name__}")
        if dpr <= 0.0:
            raise ValueError(f"DPR must be strictly positive, got {dpr}")

        return (int(round(float(x) * float(dpr))), int(round(float(y) * float(dpr))))

    # Exact aliases for backward compatibility with tests/mocks/contract.py
    to_logical = physical_to_logical
    to_physical = logical_to_physical

    @staticmethod
    def clamp_to_bounds(
        x: Union[int, float],
        y: Union[int, float],
        bounds: Union[Tuple[int, int, int, int], Tuple[int, int], Sequence[int]],
    ) -> Tuple[int, int]:
        """Clamp coordinate (x, y) to within specified rectangular boundary.

        Args:
            x: Target X coordinate.
            y: Target Y coordinate.
            bounds: Either a 4-tuple (min_x, min_y, max_x, max_y) or a 2-tuple (width, height)
                    where min=(0, 0) and max=(width - 1, height - 1).

        Returns:
            Tuple[int, int]: (clamped_x, clamped_y)

        Raises:
            TypeError: If bounds is not a tuple/sequence or contains non-integers.
            ValueError: If bounds format is invalid or max < min.
        """
        if not isinstance(bounds, (tuple, list)):
            raise TypeError(f"bounds must be a tuple or list, got {type(bounds).__name__}")

        if len(bounds) == 4:
            min_x, min_y, max_x, max_y = bounds
        elif len(bounds) == 2:
            min_x, min_y = 0, 0
            max_x, max_y = int(bounds[0]) - 1, int(bounds[1]) - 1
        else:
            raise ValueError(
                f"bounds must have length 2 (w, h) or 4 (min_x, min_y, max_x, max_y), got length {len(bounds)}"
            )

        if max_x < min_x:
            raise ValueError(f"Invalid X bounds: max_x ({max_x}) < min_x ({min_x})")
        if max_y < min_y:
            raise ValueError(f"Invalid Y bounds: max_y ({max_y}) < min_y ({min_y})")

        ix = int(round(float(x)))
        iy = int(round(float(y)))
        clamped_x = max(min_x, min(ix, max_x))
        clamped_y = max(min_y, min(iy, max_y))
        return (clamped_x, clamped_y)

    @staticmethod
    def clamp_to_roi(
        x: Union[int, float],
        y: Union[int, float],
        roi: Tuple[int, int, int, int],
    ) -> Tuple[int, int]:
        """Clamp coordinate (x, y) to within an ROI 4-tuple (rx, ry, rw, rh).

        Args:
            x: Target X coordinate.
            y: Target Y coordinate.
            roi: Region of interest tuple (rx, ry, rw, rh).

        Returns:
            Tuple[int, int]: (clamped_x, clamped_y) within ROI bounds.
        """
        if not isinstance(roi, (tuple, list)) or len(roi) != 4:
            raise ValueError(f"roi must be a 4-tuple (rx, ry, rw, rh), got {roi!r}")
        rx, ry, rw, rh = roi
        if rw <= 0 or rh <= 0:
            raise ValueError(f"ROI dimensions must be strictly positive, got width={rw}, height={rh}")
        return CoordinateTransformer.clamp_to_bounds(x, y, (rx, ry, rx + rw - 1, ry + rh - 1))

    @classmethod
    def transform_roi_to_logical(
        cls,
        roi: Tuple[int, int, int, int],
        dpr: float = 1.0,
    ) -> Tuple[int, int, int, int]:
        """Convert a physical pixel ROI (x, y, w, h) to logical desktop points."""
        rx, ry, rw, rh = roi
        log_x, log_y = cls.physical_to_logical(rx, ry, dpr)
        log_w = max(1, int(round(rw / dpr)))
        log_h = max(1, int(round(rh / dpr)))
        return (log_x, log_y, log_w, log_h)

    @classmethod
    def transform_roi_to_physical(
        cls,
        roi: Tuple[int, int, int, int],
        dpr: float = 1.0,
    ) -> Tuple[int, int, int, int]:
        """Convert a logical desktop point ROI (x, y, w, h) to physical screen pixels."""
        rx, ry, rw, rh = roi
        phys_x, phys_y = cls.logical_to_physical(rx, ry, dpr)
        phys_w = max(1, int(round(rw * dpr)))
        phys_h = max(1, int(round(rh * dpr)))
        return (phys_x, phys_y, phys_w, phys_h)
