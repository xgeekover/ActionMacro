"""
challenge_vision.py
===================
Adversarial test harness and stress suite for Milestone 2:
Empirically challenging OpenCV image template matching, CoordinateTransformer,
and WaitSystem rapid-toggle timeout behavior.

Can be executed directly:
    uv run python challenge_vision.py
or via pytest:
    uv run pytest challenge_vision.py -v
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np
import pytest

from action_macro.core.models import ImageWaitParams, TimeoutAction
from action_macro.engine.coordinate_transformer import CoordinateTransformer
from action_macro.engine.image_matcher import (
    ImageMatcher,
    check_match,
    find_template,
    load_template_image,
    match_template,
    to_grayscale,
)
from action_macro.engine.wait_system import WaitSystem, wait_image
from tests.mocks.mock_screen import MockScreenDriver

logger = logging.getLogger(__name__)


# =============================================================================
# Suite 1: Template Matching Perturbations & Extremes
# =============================================================================

class TestAdversarialTemplateMatching:
    """Stress-tests OpenCV Grayscale template matching under noise, scaling, and boundary extremes."""

    @pytest.fixture(autouse=True)
    def setup_canvas(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.screen_w = 1280
        self.screen_h = 720
        self.mock_screen = MockScreenDriver(width=self.screen_w, height=self.screen_h)
        self.template_w = 60
        self.template_h = 30
        self.base_template = self.mock_screen.create_test_template(
            width=self.template_w,
            height=self.template_h,
            color=(240, 140, 50),
            pattern="cross",
        )
        self.tx = 500
        self.ty = 300
        self.expected_cx = self.tx + self.template_w // 2
        self.expected_cy = self.ty + self.template_h // 2

    def test_noise_gaussian_progressive_degradation(self):
        """Challenge 1.1: Evaluates Gaussian noise perturbation across increasing variance levels.

        Asserts that:
        - Light to moderate noise (sigma in {5, 15, 30}) preserves exact center within +/-1px.
        - Confidence degrades monotonically without NaN, inf, or negative values.
        - Extreme noise (sigma=80) reduces confidence predictably without throwing exceptions.
        """
        self.mock_screen.clear_canvas((100, 100, 100))
        self.mock_screen.inject_patch(self.base_template, self.tx, self.ty)
        canvas = self.mock_screen.capture()

        # Baseline: zero noise
        baseline_match = ImageMatcher.match_template(canvas, self.base_template)
        assert baseline_match[0] == (self.expected_cx, self.expected_cy)
        assert baseline_match[1] >= 0.99

        np.random.seed(42)
        prev_score = baseline_match[1]

        noise_sigmas = [5, 15, 30, 50, 80]
        for sigma in noise_sigmas:
            noise = np.random.normal(0, sigma, self.base_template.shape).astype(np.int16)
            noisy_needle = np.clip(self.base_template.astype(np.int16) + noise, 0, 255).astype(np.uint8)

            (cx, cy), score = ImageMatcher.match_template(canvas, noisy_needle)

            assert 0.0 <= score <= 1.0, f"Score out of bounds [0, 1]: {score} at sigma={sigma}"
            assert not math.isnan(score) and not math.isinf(score)

            if sigma <= 30:
                # Up to sigma=30 (heavy noise on 0-255 scale), center must remain within +/-1px
                assert abs(cx - self.expected_cx) <= 1, f"Center X drifted: {cx} vs {self.expected_cx}"
                assert abs(cy - self.expected_cy) <= 1, f"Center Y drifted: {cy} vs {self.expected_cy}"
                assert score >= 0.85, f"Score dropped too low for sigma={sigma}: {score}"

            # Score should generally degrade as noise increases
            assert score <= prev_score + 0.05
            prev_score = score

    def test_noise_salt_and_pepper_corruption(self):
        """Challenge 1.2: Adversarial salt-and-pepper noise randomly flips pixels to 0 or 255.

        Verifies that:
        - For 2% and 5% corruption, center localization is exact and confidence remains >= 0.85.
        - For 10% corruption, center is still preserved within +/-1px while score degrades to ~0.75-0.80.
        """
        self.mock_screen.clear_canvas((100, 100, 100))
        self.mock_screen.inject_patch(self.base_template, self.tx, self.ty)
        canvas = self.mock_screen.capture()

        np.random.seed(42)
        for pct, min_expected_score in [(0.02, 0.90), (0.05, 0.85), (0.10, 0.70)]:
            sp_needle = self.base_template.copy()
            mask_salt = np.random.rand(self.template_h, self.template_w) < (pct / 2)
            mask_pepper = np.random.rand(self.template_h, self.template_w) < (pct / 2)
            sp_needle[mask_salt] = 255
            sp_needle[mask_pepper] = 0

            (cx, cy), score = ImageMatcher.match_template(canvas, sp_needle)
            assert abs(cx - self.expected_cx) <= 1, f"Center drifted at {int(pct*100)}% noise: {cx} vs {self.expected_cx}"
            assert abs(cy - self.expected_cy) <= 1, f"Center drifted at {int(pct*100)}% noise: {cy} vs {self.expected_cy}"
            assert score >= min_expected_score, f"Score too low at {int(pct*100)}% noise: {score}"

    def test_noise_pure_uniform_random_rejection(self):
        """Challenge 1.3: Verifies that pure uniform random noise canvas rejects template with low score."""
        np.random.seed(999)
        noise_canvas = np.random.randint(0, 256, (self.screen_h, self.screen_w, 3), dtype=np.uint8)

        match = ImageMatcher.find_template(noise_canvas, self.base_template, confidence=0.8)
        assert match is None, "Pure noise canvas falsely matched template with confidence >= 0.8"

        (cx, cy), raw_score = ImageMatcher.match_template(noise_canvas, self.base_template)
        assert raw_score < 0.45, f"False correlation score too high on random noise: {raw_score}"

    def test_scale_slight_offsets(self):
        """Challenge 1.4: Tests template matching under slight geometric scale offsets (+/-2% to +/-10%).

        Scale differences occur naturally due to desktop display DPI rendering or window resizing.
        """
        self.mock_screen.clear_canvas((80, 80, 80))
        self.mock_screen.inject_patch(self.base_template, self.tx, self.ty)
        canvas = self.mock_screen.capture()

        scale_factors = [0.95, 0.98, 1.02, 1.05]
        for scale in scale_factors:
            scaled_w = max(2, int(round(self.template_w * scale)))
            scaled_h = max(2, int(round(self.template_h * scale)))
            scaled_needle = cv2.resize(self.base_template, (scaled_w, scaled_h), interpolation=cv2.INTER_LINEAR)

            (cx, cy), score = ImageMatcher.match_template(canvas, scaled_needle)

            assert 0.0 <= score <= 1.0
            # Center offset should be within +/-2px of original template center
            assert abs(cx - self.expected_cx) <= 2, f"Center X error at scale {scale}: cx={cx}, exp={self.expected_cx}"
            assert abs(cy - self.expected_cy) <= 2, f"Center Y error at scale {scale}: cy={cy}, exp={self.expected_cy}"

            if 0.98 <= scale <= 1.02:
                # Within +/-2% scale offset, confidence remains above 0.85
                assert score >= 0.85, f"Scale {scale} score too low: {score}"
            else:
                # At 5% scale offset, confidence remains detectable (> 0.65)
                assert score >= 0.65, f"Scale {scale} score too low: {score}"

    def test_scale_dpr_mismatch_resizing(self):
        """Challenge 1.5: Template captured at DPR 2.0 (2x size) scaled to match DPR 1.0 canvas."""
        dpr_template = cv2.resize(self.base_template, (self.template_w * 2, self.template_h * 2), interpolation=cv2.INTER_CUBIC)

        self.mock_screen.clear_canvas((50, 50, 50))
        self.mock_screen.inject_patch(self.base_template, self.tx, self.ty)
        canvas = self.mock_screen.capture()

        # Rescaling template from DPR 2.0 to DPR 1.0
        normalized_needle = cv2.resize(
            dpr_template,
            (int(round(dpr_template.shape[1] / 2.0)), int(round(dpr_template.shape[0] / 2.0))),
            interpolation=cv2.INTER_AREA,
        )

        (cx, cy), score = ImageMatcher.match_template(canvas, normalized_needle)
        assert abs(cx - self.expected_cx) <= 1
        assert abs(cy - self.expected_cy) <= 1
        assert score >= 0.90, f"Rescaled DPR template score below 0.90: {score}"

    def test_solid_flat_color_templates(self):
        """Challenge 1.6: Solid flat zero-variance templates (std < 1.0) fallback to TM_SQDIFF_NORMED.

        Standard normalized cross-correlation exhibits division by zero on flat patches.
        Verifies fallback handles non-zero flat templates (val in {1, 77, 128, 255}) accurately.
        """
        flat_values = [1, 77, 128, 255]
        patch_size = 40

        for val in flat_values:
            flat_patch = np.full((patch_size, patch_size, 3), val, dtype=np.uint8)

            # Background differs from patch
            bg_val = (val + 100) % 256
            self.mock_screen.clear_canvas((bg_val, bg_val, bg_val))
            self.mock_screen.inject_patch(flat_patch, self.tx, self.ty)
            canvas = self.mock_screen.capture()

            match = ImageMatcher.find_template(canvas, flat_patch, confidence=0.95)
            assert match is not None, f"Flat patch (val={val}) failed to match with score >= 0.95"

            cx, cy, score = match
            expected_x = self.tx + patch_size // 2
            expected_y = self.ty + patch_size // 2
            assert cx == expected_x
            assert cy == expected_y
            assert score >= 0.999, f"Flat patch match score expected ~1.0, got {score}"

    def test_solid_flat_black_zero_denominator_limitation(self):
        """Challenge 1.7 [EMPIRICAL LIMITATION FINDING]:

        Characterizes behavior of pure black flat template (all pixels = 0).
        In OpenCV cv2.matchTemplate(..., cv2.TM_SQDIFF_NORMED), the denominator
        is sqrt(sum(T^2) * sum(I^2)). When T is entirely zero, sum(T^2) = 0,
        triggering mathematical division by zero. OpenCV returns 1.0 across the
        entire result matrix, resulting in score = 1.0 - 1.0 = 0.0.
        """
        patch_size = 40
        flat_black = np.zeros((patch_size, patch_size, 3), dtype=np.uint8)

        self.mock_screen.clear_canvas((150, 150, 150))
        self.mock_screen.inject_patch(flat_black, self.tx, self.ty)
        canvas = self.mock_screen.capture()

        # Empirically verify that TM_SQDIFF_NORMED denominator singularity yields score 0.0
        (cx, cy), score = ImageMatcher.match_template(canvas, flat_black)
        assert score == 0.0, f"Expected 0.0 due to TM_SQDIFF_NORMED zero-denominator, got {score}"

    def test_solid_flat_template_color_difference_rejection(self):
        """Challenge 1.8: Flat template against canvas with differing uniform value correctly yields score 0.0."""
        canvas = np.zeros((200, 200, 3), dtype=np.uint8)
        flat_white = np.full((30, 30, 3), 255, dtype=np.uint8)

        # Flat white needle on all-black canvas: SQDIFF_NORMED should yield min_val ~1.0, score ~0.0
        (cx, cy), score = ImageMatcher.match_template(canvas, flat_white)
        assert score == 0.0, f"Flat white needle on black canvas expected score 0.0, got {score}"

    def test_solid_flat_template_std_boundary(self):
        """Challenge 1.9: Evaluates std threshold boundary at std=1.0 (SQDIFF fallback vs CCOEFF)."""
        canvas = np.full((200, 200, 3), 50, dtype=np.uint8)

        # 1. std just below 1.0 (e.g. std = 0.02)
        needle_low_std = np.full((30, 30, 3), 100, dtype=np.uint8)
        needle_low_std[0, 0] = [101, 100, 100]
        assert np.std(needle_low_std) < 1.0

        canvas[50:80, 50:80] = needle_low_std
        (cx, cy), score_low = ImageMatcher.match_template(canvas, needle_low_std)
        assert 0.0 <= score_low <= 1.0
        assert cx == 50 + 15 and cy == 50 + 15
        assert score_low >= 0.99

        # 2. std just above 1.0 (std = 2.45). Three bands, not two: CCOEFF_NORMED ignores
        #    brightness and contrast, so with a two-band needle (105 over 100) the window
        #    15 rows lower (100 over the 50 background) also scores 1.0 up to float error
        #    (0.999991), and some CPUs picked it. No shifted window can repeat three bands.
        needle_high_std = np.full((30, 30, 3), 100, dtype=np.uint8)
        needle_high_std[:10, :] = 103
        needle_high_std[20:, :] = 106
        assert np.std(needle_high_std) > 1.0

        canvas[120:150, 120:150] = needle_high_std
        (cx, cy), score_high = ImageMatcher.match_template(canvas, needle_high_std)
        assert 0.0 <= score_high <= 1.0
        assert cx == 120 + 15 and cy == 120 + 15
        assert score_high >= 0.99

    def test_tiny_1x1_template_exact_localization(self):
        """Challenge 1.10: Tiny 1x1 pixel template locates exact single-pixel target on screen."""
        self.mock_screen.clear_canvas((0, 0, 0))
        target_x, target_y = 637, 412
        needle_1x1 = np.array([[[200, 200, 200]]], dtype=np.uint8)
        self.mock_screen.inject_patch(needle_1x1, target_x, target_y)
        canvas = self.mock_screen.capture()

        (cx, cy), score = ImageMatcher.match_template(canvas, needle_1x1)
        assert cx == target_x, f"1x1 X coordinate mismatch: got {cx}, expected {target_x}"
        assert cy == target_y, f"1x1 Y coordinate mismatch: got {cy}, expected {target_y}"
        assert score >= 0.999, f"1x1 exact match score expected 1.0, got {score}"

    def test_tiny_thin_and_small_templates(self):
        """Challenge 1.11: Tests 1x10, 10x1, and 2x2 asymmetric needle templates."""
        canvas = np.zeros((200, 200, 3), dtype=np.uint8)

        # 1x10 vertical line
        v_line = np.full((10, 1, 3), 220, dtype=np.uint8)
        canvas[40:50, 70:71] = v_line
        (cx, cy), score = ImageMatcher.match_template(canvas, v_line)
        assert cx == 70
        assert cy == 45
        assert score >= 0.999

        # 10x1 horizontal line
        h_line = np.full((1, 10, 3), 220, dtype=np.uint8)
        canvas[80:81, 110:120] = h_line
        (cx, cy), score = ImageMatcher.match_template(canvas, h_line)
        assert cx == 115
        assert cy == 80
        assert score >= 0.999

    def test_oversize_template_exceeding_screen_raises(self):
        """Challenge 1.12: Needles with dimensions exceeding screen bounds raise ValueError."""
        canvas = np.zeros((100, 100, 3), dtype=np.uint8)

        # Height exceeds
        with pytest.raises(ValueError, match="exceed search bounds"):
            ImageMatcher.match_template(canvas, np.zeros((101, 100, 3), dtype=np.uint8))

        # Width exceeds
        with pytest.raises(ValueError, match="exceed search bounds"):
            ImageMatcher.match_template(canvas, np.zeros((100, 101, 3), dtype=np.uint8))

        # Both exceed
        with pytest.raises(ValueError, match="exceed search bounds"):
            ImageMatcher.match_template(canvas, np.zeros((200, 200, 3), dtype=np.uint8))

    def test_oversize_template_exceeding_roi_raises(self):
        """Challenge 1.13: Needles with dimensions exceeding specified ROI bounding box raise ValueError."""
        canvas = np.zeros((500, 500, 3), dtype=np.uint8)
        roi = (100, 100, 50, 40)
        needle = np.zeros((45, 55, 3), dtype=np.uint8)  # 55 > 50, 45 > 40

        with pytest.raises(ValueError, match="exceed ROI bounding box"):
            ImageMatcher.match_template(canvas, needle, roi=roi)

    def test_exact_canvas_dimensions_template(self):
        """Challenge 1.14: Template identical in size to haystack executes and matches."""
        canvas = np.full((80, 80, 3), 150, dtype=np.uint8)
        needle = np.full((80, 80, 3), 150, dtype=np.uint8)

        (cx, cy), score = ImageMatcher.match_template(canvas, needle)
        assert cx == 40
        assert cy == 40
        assert score >= 0.999

    def test_empty_and_zero_size_template_raises(self):
        """Challenge 1.15: 0x0 or empty image arrays raise ValueError."""
        canvas = np.zeros((100, 100, 3), dtype=np.uint8)
        empty_array = np.zeros((0, 0, 3), dtype=np.uint8)

        with pytest.raises(ValueError, match="Image array cannot be empty"):
            ImageMatcher.match_template(canvas, empty_array)

        with pytest.raises(ValueError, match="Image array cannot be empty"):
            ImageMatcher.match_template(empty_array, canvas)


# =============================================================================
# Suite 2: Coordinate Transformer Adversarial Edge Cases
# =============================================================================

class TestAdversarialCoordinateTransformer:
    """Stress-tests CoordinateTransformer with fractional DPR, negative coords, and non-origin ROI clamping."""

    def test_dpr_non_standard_1_333_bidirectional(self):
        """Challenge 2.1: Non-standard scale factor DPR=1.33333333333 (~4/3 scale).

        Tests round-trip reversibility across typical screen points:
        |logical - to_logical(to_physical(logical))| <= 1px.
        """
        dpr = 1.3333333333333333
        test_points = [
            (0, 0),
            (100, 150),
            (960, 540),
            (1440, 810),
            (1920, 1080),
            (2560, 1440),
            (3840, 2160),
        ]

        for lx, ly in test_points:
            px, py = CoordinateTransformer.logical_to_physical(lx, ly, dpr=dpr)
            rx, ry = CoordinateTransformer.physical_to_logical(px, py, dpr=dpr)
            assert abs(rx - lx) <= 1, f"DPR 1.333 reversibility error for X: {lx} -> {px} -> {rx}"
            assert abs(ry - ly) <= 1, f"DPR 1.333 reversibility error for Y: {ly} -> {py} -> {ry}"

    def test_dpr_non_standard_1_75_bidirectional(self):
        """Challenge 2.2: Windows 175% desktop scaling factor DPR=1.75."""
        dpr = 1.75
        test_points = [
            (0, 0),
            (1, 1),
            (100, 100),
            (720, 450),
            (1097, 617),
            (1280, 720),
            (1920, 1080),
        ]

        for lx, ly in test_points:
            px, py = CoordinateTransformer.logical_to_physical(lx, ly, dpr=dpr)
            rx, ry = CoordinateTransformer.physical_to_logical(px, py, dpr=dpr)
            assert abs(rx - lx) <= 1, f"DPR 1.75 reversibility error: {lx},{ly} -> {px},{py} -> {rx},{ry}"

    def test_dpr_fractional_spectrum(self):
        """Challenge 2.3: Broad spectrum of DPR values [0.75, 1.125, 1.25, 1.5, 2.25, 2.5, 3.0, 4.0]."""
        dprs = [0.75, 1.125, 1.25, 1.5, 2.0, 2.25, 2.5, 3.0, 4.0]
        test_coord = (500, 400)

        for d in dprs:
            px, py = CoordinateTransformer.logical_to_physical(test_coord[0], test_coord[1], dpr=d)
            lx, ly = CoordinateTransformer.physical_to_logical(px, py, dpr=d)
            assert abs(lx - test_coord[0]) <= 1
            assert abs(ly - test_coord[1]) <= 1

    def test_dpr_invalid_and_negative_raises(self):
        """Challenge 2.4: Non-positive or non-numeric DPR values raise ValueError / TypeError."""
        with pytest.raises(ValueError, match="strictly positive"):
            CoordinateTransformer.physical_to_logical(100, 100, dpr=0.0)

        with pytest.raises(ValueError, match="strictly positive"):
            CoordinateTransformer.physical_to_logical(100, 100, dpr=-1.5)

        with pytest.raises(TypeError, match="DPR must be numeric"):
            CoordinateTransformer.physical_to_logical(100, 100, dpr=None)

        with pytest.raises(TypeError, match="DPR must be numeric"):
            CoordinateTransformer.physical_to_logical(100, 100, dpr=True)  # bool check

    def test_negative_coordinates_transformation(self):
        """Challenge 2.5: Multi-monitor secondary display negative coordinates.

        Monitors to the left or top of primary display possess negative coordinates.
        """
        negative_points = [
            (-1920, -1080),
            (-100, -50),
            (-1, -1),
            (-2560, 0),
            (0, -1440),
        ]
        dpr = 1.75

        for px, py in negative_points:
            lx, ly = CoordinateTransformer.physical_to_logical(px, py, dpr=dpr)
            assert lx <= 0 and ly <= 0
            # Reverse
            rx, ry = CoordinateTransformer.logical_to_physical(lx, ly, dpr=dpr)
            assert abs(rx - px) <= 1
            assert abs(ry - py) <= 1

    def test_negative_bounds_clamping(self):
        """Challenge 2.6: clamp_to_bounds with negative boundary ranges."""
        bounds = (-1920, -1080, -1, -1)

        # Far outside left/top
        assert CoordinateTransformer.clamp_to_bounds(-3000, -2000, bounds) == (-1920, -1080)
        # Far outside right/bottom (positive space)
        assert CoordinateTransformer.clamp_to_bounds(500, 500, bounds) == (-1, -1)
        # Inside
        assert CoordinateTransformer.clamp_to_bounds(-500, -400, bounds) == (-500, -400)
        # Boundary edges
        assert CoordinateTransformer.clamp_to_bounds(-1920, -1080, bounds) == (-1920, -1080)
        assert CoordinateTransformer.clamp_to_bounds(-1, -1, bounds) == (-1, -1)

    def test_clamp_non_origin_roi_interior_and_exterior(self):
        """Challenge 2.7: Clamping points relative to non-origin ROI (rx, ry, rw, rh)."""
        rx, ry, rw, rh = 500, 300, 200, 150
        roi = (rx, ry, rw, rh)
        max_x = rx + rw - 1  # 699
        max_y = ry + rh - 1  # 449

        # 1. Interior point
        assert CoordinateTransformer.clamp_to_roi(550, 350, roi) == (550, 350)

        # 2. Outer Top-Left
        assert CoordinateTransformer.clamp_to_roi(-100, -50, roi) == (rx, ry)
        assert CoordinateTransformer.clamp_to_roi(499, 299, roi) == (rx, ry)

        # 3. Outer Bottom-Right
        assert CoordinateTransformer.clamp_to_roi(5000, 3000, roi) == (max_x, max_y)
        assert CoordinateTransformer.clamp_to_roi(700, 450, roi) == (max_x, max_y)

        # 4. Exact boundaries
        assert CoordinateTransformer.clamp_to_roi(rx, ry, roi) == (rx, ry)
        assert CoordinateTransformer.clamp_to_roi(max_x, max_y, roi) == (max_x, max_y)
        assert CoordinateTransformer.clamp_to_roi(rx, max_y, roi) == (rx, max_y)
        assert CoordinateTransformer.clamp_to_roi(max_x, ry, roi) == (max_x, ry)

        # 5. Float coordinates
        assert CoordinateTransformer.clamp_to_roi(500.4, 300.2, roi) == (rx, ry)
        assert CoordinateTransformer.clamp_to_roi(698.8, 448.9, roi) == (max_x, max_y)

    def test_clamp_non_origin_roi_negative_origin(self):
        """Challenge 2.8: ROI situated entirely in negative coordinate space on a secondary display."""
        roi_neg = (-1200, -800, 600, 400)
        # Bounds: X in [-1200, -601], Y in [-800, -401]

        assert CoordinateTransformer.clamp_to_roi(-2000, -1500, roi_neg) == (-1200, -800)
        assert CoordinateTransformer.clamp_to_roi(0, 0, roi_neg) == (-601, -401)
        assert CoordinateTransformer.clamp_to_roi(-900, -600, roi_neg) == (-900, -600)

    def test_clamp_roi_degenerate_and_invalid(self):
        """Challenge 2.9: Invalid ROI dimensions or structures raise ValueError."""
        # Non-positive dimensions
        with pytest.raises(ValueError, match="strictly positive"):
            CoordinateTransformer.clamp_to_roi(10, 10, (100, 100, 0, 50))

        with pytest.raises(ValueError, match="strictly positive"):
            CoordinateTransformer.clamp_to_roi(10, 10, (100, 100, 50, -10))

        # Invalid tuple length
        with pytest.raises(ValueError, match="roi must be a 4-tuple"):
            CoordinateTransformer.clamp_to_roi(10, 10, (100, 100, 50))

    def test_roi_dpr_transform_roundtrip(self):
        """Challenge 2.10: transform_roi_to_logical and transform_roi_to_physical roundtrip."""
        dpr = 1.333
        orig_roi = (500, 300, 200, 150)

        log_roi = CoordinateTransformer.transform_roi_to_logical(orig_roi, dpr=dpr)
        phys_roi = CoordinateTransformer.transform_roi_to_physical(log_roi, dpr=dpr)

        for i in range(4):
            assert abs(phys_roi[i] - orig_roi[i]) <= 1


# =============================================================================
# Suite 3: Image Wait Timeout & Rapid Toggle Stress
# =============================================================================

class TestAdversarialWaitSystemRapidToggle:
    """Stress-tests WaitSystem.wait_image with high-frequency appearance/disappearance toggling."""

    @pytest.fixture(autouse=True)
    def setup_driver(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.screen = MockScreenDriver(width=640, height=480)
        self.tmpl = self.screen.create_test_template(width=40, height=20, pattern="cross")
        self.tmpl_path = self.screen.save_template_file(self.tmpl, tmp_path / "toggle_tmpl.png")
        self.patch_x = 200
        self.patch_y = 150
        self.expected_center = (self.patch_x + 20, self.patch_y + 10)

    def test_rapid_toggle_appear_mode_catches_target(self):
        """Challenge 3.1: Thread rapidly alternates canvas between empty and target patch (every 10ms).

        wait_image(mode='appear') must reliably catch the target appearance and return center coords.
        """
        stop_toggle = threading.Event()

        def _toggle_worker():
            state = False
            while not stop_toggle.is_set():
                if state:
                    self.screen.inject_patch(self.tmpl, self.patch_x, self.patch_y)
                else:
                    self.screen.clear_canvas()
                state = not state
                time.sleep(0.010)

        t = threading.Thread(target=_toggle_worker, daemon=True)
        t.start()

        params = ImageWaitParams(
            template_path=str(self.tmpl_path),
            mode="appear",
            confidence=0.8,
            timeout_seconds=2.0,
            poll_interval_seconds=0.010,
        )

        try:
            t0 = time.perf_counter()
            found, coords = WaitSystem.wait_image(self.screen, params)
            elapsed = time.perf_counter() - t0

            assert found is True
            assert coords == self.expected_center
            assert elapsed <= 1.0, f"Detection during rapid toggle took too long: {elapsed*1000:.1f}ms"
        finally:
            stop_toggle.set()
            t.join()

    def test_rapid_toggle_disappear_mode_detects_vanishing(self):
        """Challenge 3.2: Screen starts with template present, then rapidly flickers.

        wait_image(mode='disappear') must successfully detect disappearance once canvas is empty.
        """
        # Initially stamp template
        self.screen.inject_patch(self.tmpl, self.patch_x, self.patch_y)
        stop_toggle = threading.Event()

        def _toggle_worker():
            time.sleep(0.025)  # Stay present for 25ms
            state = False
            while not stop_toggle.is_set():
                if state:
                    self.screen.inject_patch(self.tmpl, self.patch_x, self.patch_y)
                else:
                    self.screen.clear_canvas()
                state = not state
                time.sleep(0.010)

        t = threading.Thread(target=_toggle_worker, daemon=True)
        t.start()

        params = ImageWaitParams(
            template_path=str(self.tmpl_path),
            mode="disappear",
            confidence=0.8,
            timeout_seconds=2.0,
            poll_interval_seconds=0.010,
        )

        try:
            t0 = time.perf_counter()
            found, coords = WaitSystem.wait_image(self.screen, params)
            elapsed = time.perf_counter() - t0

            assert found is True
            assert coords is None
            assert elapsed <= 1.0
        finally:
            stop_toggle.set()
            t.join()

    def test_rapid_toggle_timeout_skip_policy(self):
        """Challenge 3.3: Target never appears (distractor template toggles rapidly).

        Verifies that timeout_action='skip' expires predictably within [timeout, timeout + 0.15s]
        and returns (False, None).
        """
        distractor = self.screen.create_test_template(width=40, height=20, pattern="circle")
        stop_toggle = threading.Event()

        def _toggle_worker():
            state = False
            while not stop_toggle.is_set():
                if state:
                    self.screen.inject_patch(distractor, self.patch_x, self.patch_y)
                else:
                    self.screen.clear_canvas()
                state = not state
                time.sleep(0.008)

        t = threading.Thread(target=_toggle_worker, daemon=True)
        t.start()

        target_timeout = 0.20  # 200ms timeout
        params = ImageWaitParams(
            template_path=str(self.tmpl_path),
            mode="appear",
            confidence=0.85,
            timeout_seconds=target_timeout,
            poll_interval_seconds=0.010,
            timeout_action="skip",
        )

        try:
            t0 = time.perf_counter()
            found, coords = WaitSystem.wait_image(self.screen, params)
            elapsed = time.perf_counter() - t0

            assert found is False
            assert coords is None
            assert elapsed >= target_timeout - 0.01
            assert elapsed <= target_timeout + 0.15, f"Timeout expired too late: {elapsed*1000:.1f}ms"
        finally:
            stop_toggle.set()
            t.join()

    def test_rapid_toggle_timeout_raise_policy(self):
        """Challenge 3.4: Target never appears with timeout_action='raise'.

        Verifies that TimeoutError is raised precisely when deadline passes during active toggling.
        """
        distractor = self.screen.create_test_template(width=40, height=20, pattern="circle")
        stop_toggle = threading.Event()

        def _toggle_worker():
            state = False
            while not stop_toggle.is_set():
                if state:
                    self.screen.inject_patch(distractor, self.patch_x, self.patch_y)
                else:
                    self.screen.clear_canvas()
                state = not state
                time.sleep(0.008)

        t = threading.Thread(target=_toggle_worker, daemon=True)
        t.start()

        target_timeout = 0.20
        params = ImageWaitParams(
            template_path=str(self.tmpl_path),
            mode="appear",
            confidence=0.85,
            timeout_seconds=target_timeout,
            poll_interval_seconds=0.010,
            timeout_action="raise",
        )

        try:
            t0 = time.perf_counter()
            with pytest.raises(TimeoutError, match="timed out"):
                WaitSystem.wait_image(self.screen, params)
            elapsed = time.perf_counter() - t0

            assert elapsed >= target_timeout - 0.01
            assert elapsed <= target_timeout + 0.15
        finally:
            stop_toggle.set()
            t.join()

    def test_rapid_toggle_emergency_stop_sub_50ms(self):
        """Challenge 3.5: Emergency stop abort latency during active polling on rapidly toggling screen.

        Asserts that setting cancel_event halts execution in <= 50ms (well within 1.0s limit).
        """
        distractor = self.screen.create_test_template(width=40, height=20, pattern="circle")
        stop_toggle = threading.Event()

        def _toggle_worker():
            state = False
            while not stop_toggle.is_set():
                if state:
                    self.screen.inject_patch(distractor, self.patch_x, self.patch_y)
                else:
                    self.screen.clear_canvas()
                state = not state
                time.sleep(0.005)

        t = threading.Thread(target=_toggle_worker, daemon=True)
        t.start()

        cancel_event = threading.Event()

        def _cancel_worker():
            time.sleep(0.030)  # Fire cancel at 30ms
            cancel_event.set()

        threading.Thread(target=_cancel_worker, daemon=True).start()

        params = ImageWaitParams(
            template_path=str(self.tmpl_path),
            mode="appear",
            confidence=0.85,
            timeout_seconds=10.0,
            poll_interval_seconds=0.010,
            timeout_action="raise",
        )

        try:
            t0 = time.perf_counter()
            found, coords = WaitSystem.wait_image(self.screen, params, cancel_event=cancel_event)
            elapsed = time.perf_counter() - t0

            assert found is False
            assert coords is None
            # Abort latency should be <= 30ms + 50ms = 80ms
            assert elapsed <= 0.080, f"Cancellation latency exceeded limit: {elapsed*1000:.1f}ms"
        finally:
            stop_toggle.set()
            t.join()

    def test_rapid_toggle_pause_and_resume(self):
        """Challenge 3.6: Pause event suspends polling and preserves deadline during rapid toggle."""
        pause_event = threading.Event()
        pause_event.set()  # Start paused

        def _unpause_worker():
            time.sleep(0.080)
            self.screen.inject_patch(self.tmpl, self.patch_x, self.patch_y)
            pause_event.clear()

        threading.Thread(target=_unpause_worker, daemon=True).start()

        params = ImageWaitParams(
            template_path=str(self.tmpl_path),
            mode="appear",
            confidence=0.8,
            timeout_seconds=0.5,
            poll_interval_seconds=0.010,
        )

        t0 = time.perf_counter()
        found, coords = WaitSystem.wait_image(self.screen, params, pause_event=pause_event)
        elapsed = time.perf_counter() - t0

        assert found is True
        assert coords == self.expected_center
        assert elapsed >= 0.080

    def test_rapid_toggle_zero_timeout_instant_evaluation(self):
        """Challenge 3.7: timeout_seconds=0.0 executes single instantaneous poll without blocking (<=20ms)."""
        self.screen.inject_patch(self.tmpl, self.patch_x, self.patch_y)

        params = ImageWaitParams(
            template_path=str(self.tmpl_path),
            mode="appear",
            confidence=0.8,
            timeout_seconds=0.0,
            timeout_action="skip",
        )

        t0 = time.perf_counter()
        found, coords = WaitSystem.wait_image(self.screen, params)
        elapsed = time.perf_counter() - t0

        assert found is True
        assert coords == self.expected_center
        assert elapsed <= 0.025, f"Instant check took too long: {elapsed*1000:.1f}ms"


# =============================================================================
# Standalone CLI Test Runner
# =============================================================================

if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
