"""
action_macro.engine.wait_system
===================================
High-precision sliced sleep wait and OpenCV image recognition wait system.
Provides sub-20ms emergency cancellation, pause support, and flexible timeout policies.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from action_macro.core.models import ImageWaitParams, TimeoutAction
from action_macro.core.persistence import resolve_template_path
from action_macro.engine.image_matcher import ImageMatcher, load_template_image, scaled_template_size

logger = logging.getLogger(__name__)


def _template_scales(screen_adapter: Any) -> Tuple[float, ...]:
    """Template resize factors the adapter's captures call for (native size if it has no opinion)."""
    getter = getattr(screen_adapter, "get_template_scales", None)
    if callable(getter):
        scales = tuple(float(s) for s in getter() if float(s) > 0.0)
        if scales:
            return scales
    return (1.0,)


def _fits_at_any_scale(
    templ_w: int, templ_h: int, area_w: int, area_h: int, scales: Tuple[float, ...]
) -> bool:
    """True if the template, resized by at least one factor, fits inside the area."""
    for scale in scales:
        scaled_w, scaled_h = scaled_template_size(templ_w, templ_h, scale)
        if scaled_w <= area_w and scaled_h <= area_h:
            return True
    return False


class WaitSystem:
    """Synchronization and timing engine for macro execution."""

    @staticmethod
    def wait(
        duration_seconds: float,
        cancel_event: Optional[threading.Event] = None,
        pause_event: Optional[threading.Event] = None,
        slice_interval: float = 0.02,
    ) -> bool:
        """Interruptible, high-precision fixed duration wait.

        Uses sliced sleep intervals (default <= 20ms) to ensure emergency stop
        cancellation latency is <= 20ms while maintaining timing accuracy.

        Args:
            duration_seconds: Target wait duration. Negative values clamp safely to 0.
            cancel_event: Threading event signalling immediate abort.
            pause_event: Threading event indicating engine paused state.
            slice_interval: Maximum slice sleep duration (default 20ms = 0.02s).

        Returns:
            True if duration elapsed normally, False if cancelled by cancel_event.
        """
        # Safe clamp for zero or negative durations
        if duration_seconds <= 0.0:
            return True

        if cancel_event is not None and cancel_event.is_set():
            return False

        deadline = time.perf_counter() + duration_seconds
        slice_sec = max(0.001, min(0.02, slice_interval))

        while True:
            # Check cooperative cancellation
            if cancel_event is not None and cancel_event.is_set():
                return False

            # Check cooperative pause
            while pause_event is not None and pause_event.is_set():
                if cancel_event is not None and cancel_event.is_set():
                    return False
                time.sleep(0.01)
                # Adjust deadline during pause to preserve total active wait time
                deadline += 0.01

            remaining = deadline - time.perf_counter()
            if remaining <= 0.0:
                break

            wait_slice = min(slice_sec, remaining)
            if cancel_event is not None:
                # wait() immediately returns True if cancel_event is set
                if cancel_event.wait(timeout=wait_slice):
                    return False
            else:
                time.sleep(wait_slice)

        return True

    @classmethod
    def wait_image(
        cls,
        first_arg: Any,
        second_arg: Any,
        cancel_event: Optional[threading.Event] = None,
        base_dir: Optional[Union[str, Path]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> Tuple[bool, Optional[Tuple[int, int]]]:
        """Polls screen adapter until target image appears or disappears.

        Supports both standard argument orders:
          - wait_image(screen_adapter, params, cancel_event=...)
          - wait_image(params, screen_adapter, cancel_event=...)

        If the adapter provides ``get_template_scales()``, the template is also matched
        at those resize factors (a HiDPI screenshot against a nominal-resolution
        capture) and the best-scoring size wins; sizes that do not fit the capture are
        skipped. If the adapter provides ``get_capture_origin(roi)``, returned
        coordinates are translated into the adapter's global (click) coordinate space.

        Args:
            first_arg: Either screen_adapter or params (ImageWaitParams or dict).
            second_arg: Either params or screen_adapter.
            cancel_event: Optional emergency stop event.
            base_dir: Base directory to resolve relative template paths.
            pause_event: Optional pause event.

        Returns:
            Tuple of (success_found, (center_x, center_y) or None).

        Raises:
            FileNotFoundError: If template file does not exist.
            ValueError: If the template exceeds the screen or ROI bounds at every
                candidate scale.
            TimeoutError: If wait times out and timeout_action is 'raise'.
        """
        # 1. Flexible argument order normalization
        if isinstance(first_arg, (ImageWaitParams, dict)) or (
            hasattr(first_arg, "template_path") and not hasattr(first_arg, "capture") and not hasattr(first_arg, "capture_screen")
        ):
            raw_params = first_arg
            screen_adapter = second_arg
        else:
            screen_adapter = first_arg
            raw_params = second_arg

        if screen_adapter is None:
            raise ValueError("Screen adapter instance required for ImageWait action")

        # Normalize params to ImageWaitParams
        if isinstance(raw_params, dict):
            params = ImageWaitParams(**raw_params)
        elif isinstance(raw_params, ImageWaitParams):
            params = raw_params
        else:
            raise TypeError(f"Invalid params type for wait_image: {type(raw_params).__name__}")

        # 2. Resolve template path
        template_str = params.template_path
        if base_dir is not None:
            template_file = resolve_template_path(template_str, base_dir)
        else:
            p = Path(template_str)
            template_file = p.resolve() if p.exists() else p

        if not template_file.exists():
            raise FileNotFoundError(f"Template image file not found: {template_file}")

        # Pre-load template image and check dimensions
        template_gray = load_template_image(template_file)
        templ_h, templ_w = template_gray.shape[:2]
        scales = _template_scales(screen_adapter)
        multiscale = scales != (1.0,)

        if params.roi is not None:
            _, _, roi_w, roi_h = params.roi
            if not _fits_at_any_scale(templ_w, templ_h, roi_w, roi_h, scales):
                raise ValueError("Template dimensions exceed ROI bounding box")

        # Adapters that report their capture origin return frames that are exactly the
        # captured region; match on the whole frame and translate to global coordinates.
        origin_getter = getattr(screen_adapter, "get_capture_origin", None)
        has_origin = callable(origin_getter)
        match_roi = None if has_origin else params.roi
        origin_x, origin_y = origin_getter(params.roi) if has_origin else (0, 0)

        # 3. Setup polling timeout deadline
        timeout_sec = float(params.timeout_seconds)
        deadline = (time.perf_counter() + timeout_sec) if timeout_sec > 0.0 else float("inf")
        poll_interval = max(0.005, float(params.poll_interval_seconds))

        # 4. Polling loop
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return (False, None)

            while pause_event is not None and pause_event.is_set():
                if cancel_event is not None and cancel_event.is_set():
                    return (False, None)
                time.sleep(0.01)

            # Capture frame via screen_adapter
            if hasattr(screen_adapter, "capture_screen"):
                frame = screen_adapter.capture_screen(roi=params.roi)
            elif hasattr(screen_adapter, "capture"):
                frame = screen_adapter.capture(roi=params.roi)
            else:
                raise AttributeError(
                    f"Screen adapter {type(screen_adapter).__name__} lacks capture or capture_screen method"
                )

            # Validate frame bounds against template
            fh, fw = frame.shape[:2]
            if not _fits_at_any_scale(templ_w, templ_h, fw, fh, scales):
                raise ValueError("Template dimensions exceed screen or ROI bounds")

            # Check matching condition via ImageMatcher
            matched, coords, score = ImageMatcher.check_match(
                haystack=frame,
                needle=template_gray,
                confidence=params.confidence,
                roi=match_roi,
                mode=params.mode,
                scales=scales if multiscale else None,
            )

            if matched:
                if coords is not None:
                    coords = (coords[0] + origin_x, coords[1] + origin_y)
                return (True, coords)

            # Check if timeout deadline passed
            now = time.perf_counter()
            if timeout_sec > 0.0 and now >= deadline:
                break
            elif timeout_sec == 0.0:
                # timeout_seconds=0 means single instant poll
                break

            # Sliced sleep before next poll
            remaining_to_deadline = deadline - now if timeout_sec > 0.0 else poll_interval
            sleep_duration = min(poll_interval, max(0.0, remaining_to_deadline))

            if cancel_event is not None:
                if cancel_event.wait(timeout=sleep_duration):
                    return (False, None)
            else:
                time.sleep(sleep_duration)

        # 5. Timeout expired handling
        action = params.timeout_action
        if isinstance(action, TimeoutAction):
            action = action.value
        act_lower = str(action).strip().lower()

        if act_lower == "raise":
            raise TimeoutError(
                f"Image wait timed out after {params.timeout_seconds}s for template '{params.template_path}' (mode={params.mode})"
            )
        else:  # "skip"
            return (False, None)


# Module-level convenience functions
wait = WaitSystem.wait
wait_image = WaitSystem.wait_image
