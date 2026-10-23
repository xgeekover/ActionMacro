"""
tests.unit.test_image_matcher
=============================
Unit test suite verifying:
- Template matching confidence score accuracy
- Subregion ROI cropping and absolute screen offset mapping
- Flat zero-variance template fallback (TM_SQDIFF_NORMED)
- Color mismatch protection & channel conversions (BGR, RGB, BGRA, Grayscale)
- Boundary protections (oversized template, empty inputs, invalid ROI)
- Appearance / disappearance check_match modes
- CoordinateTransformer integration
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple, Union
import cv2
import numpy as np
import pytest

from action_macro.engine.coordinate_transformer import CoordinateTransformer
from action_macro.engine.image_matcher import (
    ImageMatcher,
    check_match,
    find_template,
    load_template_image,
    match_template,
    to_grayscale,
)
from tests.mocks.mock_screen import MockScreenDriver


# =============================================================================
# 1. Template Matching Confidence Tests
# =============================================================================

@pytest.mark.unit
def test_find_template_high_confidence(mock_screen: MockScreenDriver, sample_template):
    """Verifies that an exact template match returns peak confidence >= 0.99."""
    tx, ty = 400, 250
    mock_screen.inject_patch(sample_template, tx, ty)
    canvas = mock_screen.capture()

    match = ImageMatcher.find_template(canvas, sample_template, confidence=0.8)
    assert match is not None
    cx, cy, score = match
    assert score >= 0.99
    # sample_template is 60x30
    assert cx == tx + 30
    assert cy == ty + 15


@pytest.mark.unit
def test_find_template_confidence_threshold_filtering(mock_screen: MockScreenDriver, sample_template):
    """Verifies that match passes when confidence <= score and fails when confidence > score."""
    tx, ty = 400, 250
    mock_screen.inject_patch(sample_template, tx, ty)
    canvas = mock_screen.capture()

    # Pass with confidence=0.8
    assert ImageMatcher.find_template(canvas, sample_template, confidence=0.8) is not None

    # Artificially alter needle to lower score
    needle_altered = sample_template.copy()
    needle_altered[10:20, 10:20] = (0, 0, 0)

    # Moderate threshold passes
    match_mod = ImageMatcher.find_template(canvas, needle_altered, confidence=0.6)
    assert match_mod is not None

    # Strict threshold 0.99 fails
    match_strict = ImageMatcher.find_template(canvas, needle_altered, confidence=0.99)
    assert match_strict is None


@pytest.mark.unit
def test_find_template_background_noise_rejection(mock_screen: MockScreenDriver, sample_template):
    """Verifies that an empty canvas without template returns None."""
    mock_screen.clear_canvas((128, 128, 128))
    canvas = mock_screen.capture()
    match = ImageMatcher.find_template(canvas, sample_template, confidence=0.8)
    assert match is None


# =============================================================================
# 2. ROI Offset Mapping Tests
# =============================================================================

@pytest.mark.unit
def test_roi_subregion_crop_and_offset_mapping(mock_screen: MockScreenDriver, sample_template):
    """Verifies ROI local match coordinates map accurately to absolute screen coordinates."""
    # Place template at absolute (650, 420)
    tx, ty = 650, 420
    mock_screen.inject_patch(sample_template, tx, ty)
    canvas = mock_screen.capture()

    # Define ROI enclosing the template: (600, 400, 200, 150)
    roi = (600, 400, 200, 150)
    match = ImageMatcher.find_template(canvas, sample_template, confidence=0.8, roi=roi)

    assert match is not None
    cx, cy, score = match
    assert score >= 0.99
    assert cx == tx + 30
    assert cy == ty + 15


@pytest.mark.unit
def test_roi_exclusion_when_template_outside_roi(mock_screen: MockScreenDriver, sample_template):
    """Verifies that template placed outside ROI is NOT detected."""
    # Place template at (1000, 800)
    mock_screen.inject_patch(sample_template, 1000, 800)
    canvas = mock_screen.capture()

    # Search in different ROI: (100, 100, 300, 300)
    roi = (100, 100, 300, 300)
    match = ImageMatcher.find_template(canvas, sample_template, confidence=0.8, roi=roi)
    assert match is None


# =============================================================================
# 3. Flat Zero-Variance Template Fallback Tests
# =============================================================================

@pytest.mark.unit
def test_flat_zero_variance_template_fallback(mock_screen: MockScreenDriver):
    """Verifies zero-variance solid color template falls back to TM_SQDIFF_NORMED."""
    flat_needle = mock_screen.create_flat_template(width=40, height=40, fill_value=210)
    tx, ty = 350, 300
    mock_screen.inject_patch(flat_needle, tx, ty)
    canvas = mock_screen.capture()

    match = ImageMatcher.find_template(canvas, flat_needle, confidence=0.8)
    assert match is not None
    cx, cy, score = match
    assert score >= 0.99
    assert cx == tx + 20
    assert cy == ty + 20


@pytest.mark.unit
def test_flat_template_absence(mock_screen: MockScreenDriver):
    """Verifies absent flat template does not produce false positives."""
    flat_needle = mock_screen.create_flat_template(width=40, height=40, fill_value=240)
    mock_screen.clear_canvas((50, 50, 50))
    canvas = mock_screen.capture()

    match = ImageMatcher.find_template(canvas, flat_needle, confidence=0.8)
    assert match is None


# =============================================================================
# 4. Color Mismatch Protection & Channel Conversions
# =============================================================================

@pytest.mark.unit
def test_color_mismatch_protection_bgr_vs_rgb(mock_screen: MockScreenDriver, sample_template):
    """Verifies Grayscale normalization provides channel order invariance."""
    tx, ty = 200, 200
    mock_screen.inject_patch(sample_template, tx, ty)
    canvas_bgr = cv2.cvtColor(mock_screen.capture(), cv2.COLOR_RGB2BGR)

    # Haystack is BGR, template is RGB
    match = ImageMatcher.find_template(canvas_bgr, sample_template, confidence=0.8)
    assert match is not None
    assert match[0] == tx + 30
    assert match[1] == ty + 15


@pytest.mark.unit
def test_color_mismatch_protection_4channel_bgra(mock_screen: MockScreenDriver, sample_template):
    """Verifies 4-channel BGRA arrays convert cleanly to Grayscale."""
    needle_bgra = cv2.cvtColor(sample_template, cv2.COLOR_RGB2BGRA)
    tx, ty = 300, 300
    mock_screen.inject_patch(sample_template, tx, ty)
    canvas = mock_screen.capture()

    match = ImageMatcher.find_template(canvas, needle_bgra, confidence=0.8)
    assert match is not None
    assert match[0] == tx + 30
    assert match[1] == ty + 15


@pytest.mark.unit
def test_single_channel_grayscale_haystack_and_needle(mock_screen: MockScreenDriver, sample_template):
    """Verifies 1-channel Grayscale inputs pass without redundant conversion errors."""
    tx, ty = 150, 150
    mock_screen.inject_patch(sample_template, tx, ty)
    canvas_gray = mock_screen.capture_grayscale()
    needle_gray = cv2.cvtColor(sample_template, cv2.COLOR_RGB2GRAY)

    match = ImageMatcher.find_template(canvas_gray, needle_gray, confidence=0.8)
    assert match is not None
    assert match[0] == tx + 30
    assert match[1] == ty + 15


# =============================================================================
# 5. Boundary & Error Condition Tests
# =============================================================================

@pytest.mark.unit
def test_template_larger_than_haystack_raises_value_error():
    """Verifies needle larger than haystack raises ValueError."""
    haystack = np.zeros((100, 100, 3), dtype=np.uint8)
    oversized_needle = np.zeros((120, 80, 3), dtype=np.uint8)

    with pytest.raises(ValueError, match="exceed"):
        ImageMatcher.find_template(haystack, oversized_needle)


@pytest.mark.unit
def test_template_larger_than_roi_raises_value_error():
    """Verifies needle larger than ROI bounds raises ValueError."""
    haystack = np.zeros((500, 500, 3), dtype=np.uint8)
    needle = np.zeros((60, 60, 3), dtype=np.uint8)

    with pytest.raises(ValueError, match="exceed"):
        ImageMatcher.find_template(haystack, needle, roi=(10, 10, 40, 40))


@pytest.mark.unit
def test_invalid_roi_bounds_raises_value_error():
    """Verifies out-of-bounds or negative ROI raises ValueError."""
    haystack = np.zeros((200, 200, 3), dtype=np.uint8)
    needle = np.zeros((20, 20, 3), dtype=np.uint8)

    # Negative ROI x
    with pytest.raises(ValueError):
        ImageMatcher.find_template(haystack, needle, roi=(-10, 0, 50, 50))

    # Exceeds bounds
    with pytest.raises(ValueError):
        ImageMatcher.find_template(haystack, needle, roi=(150, 150, 100, 100))


@pytest.mark.unit
def test_zero_dimension_template_raises_value_error():
    """Verifies empty / zero dimension image raises ValueError."""
    haystack = np.zeros((200, 200, 3), dtype=np.uint8)
    empty_needle = np.zeros((0, 0, 3), dtype=np.uint8)

    with pytest.raises(ValueError):
        ImageMatcher.find_template(haystack, empty_needle)


@pytest.mark.unit
def test_missing_template_file_raises_file_not_found():
    """Verifies non-existent template path raises FileNotFoundError."""
    haystack = np.zeros((200, 200, 3), dtype=np.uint8)
    with pytest.raises(FileNotFoundError):
        ImageMatcher.find_template(haystack, "/non/existent/image.png")


# =============================================================================
# 6. Check Match Modes (Appear / Disappear) Tests
# =============================================================================

@pytest.mark.unit
def test_check_match_appear_mode_matched(mock_screen: MockScreenDriver, sample_template):
    """Verifies check_match returns (True, (cx, cy), score) when present in appear mode."""
    tx, ty = 250, 180
    mock_screen.inject_patch(sample_template, tx, ty)
    canvas = mock_screen.capture()

    matched, coords, score = check_match(canvas, sample_template, confidence=0.8, mode="appear")
    assert matched is True
    assert coords == (tx + 30, ty + 15)
    assert score >= 0.8


@pytest.mark.unit
def test_check_match_appear_mode_not_matched(mock_screen: MockScreenDriver, sample_template):
    """Verifies check_match returns (False, None, score) when absent in appear mode."""
    mock_screen.clear_canvas((10, 10, 10))
    canvas = mock_screen.capture()

    matched, coords, score = check_match(canvas, sample_template, confidence=0.8, mode="appear")
    assert matched is False
    assert coords is None


@pytest.mark.unit
def test_check_match_disappear_mode_present(mock_screen: MockScreenDriver, sample_template):
    """Verifies check_match returns (False, None, score) when present in disappear mode."""
    mock_screen.inject_patch(sample_template, 100, 100)
    canvas = mock_screen.capture()

    matched, coords, score = check_match(canvas, sample_template, confidence=0.8, mode="disappear")
    assert matched is False
    assert coords is None


@pytest.mark.unit
def test_check_match_disappear_mode_absent(mock_screen: MockScreenDriver, sample_template):
    """Verifies check_match returns (True, None, score) when absent in disappear mode."""
    mock_screen.clear_canvas((0, 0, 0))
    canvas = mock_screen.capture()

    matched, coords, score = check_match(canvas, sample_template, confidence=0.8, mode="disappear")
    assert matched is True
    assert coords is None


@pytest.mark.unit
def test_check_match_invalid_mode_raises(sample_template):
    """Verifies invalid mode raises ValueError."""
    canvas = np.zeros((100, 100, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="Invalid mode"):
        check_match(canvas, sample_template, mode="invalid_mode")


# =============================================================================
# 7. Image Loading and Conversion Utility Tests
# =============================================================================

@pytest.mark.unit
def test_to_grayscale_unsupported_type_raises():
    """Verifies to_grayscale raises TypeError on invalid types."""
    with pytest.raises(TypeError):
        to_grayscale(12345)


@pytest.mark.unit
def test_load_template_image_from_file_path(sample_template_file: Path):
    """Verifies load_template_image successfully loads PNG image from disk."""
    loaded = load_template_image(sample_template_file)
    assert isinstance(loaded, np.ndarray)
    assert loaded.ndim == 2
    assert loaded.shape == (30, 60)


# =============================================================================
# 8. Coordinate Transformer Integration Tests
# =============================================================================

@pytest.mark.unit
@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_coordinate_transformer_dpr_scaling(dpr: float):
    """Verifies DPR forward and backward conversions maintain <=1px tolerance."""
    points = [(0, 0), (1920, 1080), (1440, 900), (2560, 1440)]
    for ox, oy in points:
        px, py = CoordinateTransformer.logical_to_physical(ox, oy, dpr=dpr)
        lx, ly = CoordinateTransformer.physical_to_logical(px, py, dpr=dpr)
        assert abs(lx - ox) <= 1
        assert abs(ly - oy) <= 1

    # Bounds clamping
    clamped = CoordinateTransformer.clamp_to_bounds(-10, 2000, (1920, 1080))
    assert clamped == (0, 1079)
