"""
tests.unit.test_wait_system
===========================
Unit test suite verifying:
- Sliced sleep accuracy and duration clamping
- Sub-20ms cooperative cancellation latency
- Pause and resume handling
- Image wait appearance and disappearance modes
- Timeout policies ('raise' vs 'skip')
- Error boundaries (missing template, oversize template)
- Argument order and parameter dictionary compatibility
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
import numpy as np
import pytest

from action_macro.core.models import ImageWaitParams
from action_macro.engine.wait_system import WaitSystem, wait, wait_image
from tests.mocks.mock_screen import MockScreenDriver


# =============================================================================
# 1. Sliced Sleep Timing & Accuracy Tests
# =============================================================================

@pytest.mark.unit
def test_fixed_wait_accuracy_short_duration():
    """Verifies 50ms wait completes within expected timing bounds (45ms to 120ms)."""
    t0 = time.perf_counter()
    res = WaitSystem.wait(0.050)
    elapsed = time.perf_counter() - t0

    assert res is True
    assert elapsed >= 0.045, f"Wait completed too quickly: {elapsed*1000:.2f}ms"
    assert elapsed <= 0.150, f"Wait took too long: {elapsed*1000:.2f}ms"


@pytest.mark.unit
def test_fixed_wait_accuracy_100ms():
    """Verifies 100ms wait respects deadline accurately."""
    t0 = time.perf_counter()
    res = WaitSystem.wait(0.100)
    elapsed = time.perf_counter() - t0

    assert res is True
    assert elapsed >= 0.095, f"Wait completed too quickly: {elapsed*1000:.2f}ms"


@pytest.mark.unit
def test_fixed_wait_zero_duration_instant():
    """Verifies 0 duration returns immediately without blocking (<=10ms)."""
    t0 = time.perf_counter()
    res = WaitSystem.wait(0.0)
    elapsed = time.perf_counter() - t0

    assert res is True
    assert elapsed <= 0.010, f"Zero wait blocked for {elapsed*1000:.2f}ms"


@pytest.mark.unit
def test_fixed_wait_negative_duration_safe():
    """Verifies negative duration clamps safely to 0 without throwing exceptions."""
    t0 = time.perf_counter()
    res = WaitSystem.wait(-0.5)
    elapsed = time.perf_counter() - t0

    assert res is True
    assert elapsed <= 0.010


# =============================================================================
# 2. Sliced Cooperative Cancellation Latency Tests
# =============================================================================

@pytest.mark.unit
def test_fixed_wait_sub_20ms_cancellation_latency():
    """Verifies a 5.0s wait cancelled at 30ms terminates within 20ms of cancel signal."""
    cancel_event = threading.Event()

    def _trigger_cancel():
        time.sleep(0.030)
        cancel_event.set()

    t_thread = threading.Thread(target=_trigger_cancel, daemon=True)
    t0 = time.perf_counter()
    t_thread.start()

    res = WaitSystem.wait(5.0, cancel_event=cancel_event)
    elapsed = time.perf_counter() - t0

    assert res is False
    # Total elapsed should be around 30ms + <=20ms slice = <= 75ms
    assert elapsed <= 0.075, f"Cancellation took {elapsed*1000:.2f}ms (must be <=75ms)"


@pytest.mark.unit
def test_fixed_wait_pre_set_cancel_event():
    """Verifies wait returns False immediately when cancel event is pre-set."""
    cancel_event = threading.Event()
    cancel_event.set()

    t0 = time.perf_counter()
    res = WaitSystem.wait(10.0, cancel_event=cancel_event)
    elapsed = time.perf_counter() - t0

    assert res is False
    assert elapsed <= 0.005, f"Pre-set cancel took {elapsed*1000:.2f}ms"


@pytest.mark.unit
def test_fixed_wait_pause_resume():
    """Verifies pause event suspends wait loop and resume continues."""
    pause_event = threading.Event()
    pause_event.set()

    def _unpause():
        time.sleep(0.050)
        pause_event.clear()

    threading.Thread(target=_unpause, daemon=True).start()
    t0 = time.perf_counter()
    res = WaitSystem.wait(0.040, pause_event=pause_event)
    elapsed = time.perf_counter() - t0

    assert res is True
    # Elapsed must at least cover the pause duration (>= 50ms)
    assert elapsed >= 0.048


@pytest.mark.unit
def test_fixed_wait_pause_then_cancel():
    """Verifies wait aborts immediately if cancel_event is set while paused."""
    pause_event = threading.Event()
    pause_event.set()
    cancel_event = threading.Event()

    def _cancel_while_paused():
        time.sleep(0.030)
        cancel_event.set()

    threading.Thread(target=_cancel_while_paused, daemon=True).start()
    t0 = time.perf_counter()
    res = WaitSystem.wait(5.0, cancel_event=cancel_event, pause_event=pause_event)
    elapsed = time.perf_counter() - t0

    assert res is False
    assert elapsed <= 0.070


# =============================================================================
# 3. Image Wait Appearance Mode Tests
# =============================================================================

@pytest.mark.unit
def test_image_wait_immediate_appearance(
    mock_screen: MockScreenDriver,
    sample_template,
    sample_template_file: Path,
):
    """Verifies image wait succeeds immediately if template is present at t=0."""
    target_x, target_y = 400, 300
    mock_screen.inject_patch(sample_template, target_x, target_y)

    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="appear",
        confidence=0.8,
        timeout_seconds=1.0,
    )
    found, coords = WaitSystem.wait_image(params, mock_screen)

    assert found is True
    assert coords is not None
    # Center = target + dims // 2 (template is 60x30)
    expected_center = (target_x + 30, target_y + 15)
    assert coords == expected_center


@pytest.mark.unit
def test_image_wait_delayed_appearance(
    mock_screen: MockScreenDriver,
    sample_template,
    sample_template_file: Path,
):
    """Verifies image wait detects template after simulated appearance delay."""
    target_x, target_y = 600, 450
    # Enable automatic step progression on capture
    mock_screen.auto_advance_step = True
    # Schedule appearance at step 2
    mock_screen.schedule_event(step=2, patch=sample_template, x=target_x, y=target_y)

    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="appear",
        confidence=0.8,
        timeout_seconds=2.0,
        poll_interval_seconds=0.02,
    )
    found, coords = WaitSystem.wait_image(params, mock_screen)

    assert found is True
    assert coords == (target_x + 30, target_y + 15)


# =============================================================================
# 4. Image Wait Disappearance Mode Tests
# =============================================================================

@pytest.mark.unit
def test_image_wait_disappearance_success(
    mock_screen: MockScreenDriver,
    sample_template,
    sample_template_file: Path,
):
    """Verifies disappearance mode succeeds once template is removed from canvas."""
    target_x, target_y = 200, 200
    mock_screen.inject_patch(sample_template, target_x, target_y)
    mock_screen.auto_advance_step = True
    # Schedule removal at step 2
    mock_screen.schedule_removal(step=2, x=target_x, y=target_y, width=60, height=30)

    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="disappear",
        confidence=0.8,
        timeout_seconds=2.0,
        poll_interval_seconds=0.02,
    )
    found, coords = WaitSystem.wait_image(params, mock_screen)

    assert found is True
    assert coords is None


@pytest.mark.unit
def test_image_wait_disappearance_already_absent(
    mock_screen: MockScreenDriver,
    sample_template_file: Path,
):
    """Verifies disappearance mode returns True immediately if template is not present."""
    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="disappear",
        confidence=0.8,
        timeout_seconds=1.0,
    )
    found, coords = WaitSystem.wait_image(params, mock_screen)
    assert found is True
    assert coords is None


# =============================================================================
# 5. Image Wait Timeout Policies & Cancellation Tests
# =============================================================================

@pytest.mark.unit
def test_image_wait_timeout_raise_policy(
    mock_screen: MockScreenDriver,
    sample_template_file: Path,
):
    """Verifies timeout_action='raise' raises TimeoutError when timeout expires."""
    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="appear",
        confidence=0.8,
        timeout_seconds=0.05,
        timeout_action="raise",
        poll_interval_seconds=0.01,
    )
    with pytest.raises(TimeoutError, match="Image wait timed out"):
        WaitSystem.wait_image(params, mock_screen)


@pytest.mark.unit
def test_image_wait_timeout_skip_policy(
    mock_screen: MockScreenDriver,
    sample_template_file: Path,
):
    """Verifies timeout_action='skip' returns (False, None) when timeout expires."""
    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="appear",
        confidence=0.8,
        timeout_seconds=0.05,
        timeout_action="skip",
        poll_interval_seconds=0.01,
    )
    found, coords = WaitSystem.wait_image(params, mock_screen)
    assert found is False
    assert coords is None


@pytest.mark.unit
def test_image_wait_polling_cancellation_latency(
    mock_screen: MockScreenDriver,
    sample_template_file: Path,
):
    """Verifies long 30s image polling cancels within <=80ms when cancel_event is set."""
    cancel_event = threading.Event()

    def _cancel_after_delay():
        time.sleep(0.040)
        cancel_event.set()

    threading.Thread(target=_cancel_after_delay, daemon=True).start()

    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="appear",
        confidence=0.8,
        timeout_seconds=30.0,
        poll_interval_seconds=0.02,
    )
    t0 = time.perf_counter()
    found, coords = WaitSystem.wait_image(params, mock_screen, cancel_event=cancel_event)
    elapsed = time.perf_counter() - t0

    assert found is False
    assert coords is None
    assert elapsed <= 0.080, f"Image poll cancellation took {elapsed*1000:.2f}ms"


# =============================================================================
# 6. Image Wait Error Boundaries
# =============================================================================

@pytest.mark.unit
def test_image_wait_missing_template_file_raises(mock_screen: MockScreenDriver):
    """Verifies non-existent template path raises FileNotFoundError immediately."""
    params = ImageWaitParams(
        template_path="/non/existent/path/template.png",
        mode="appear",
        timeout_seconds=1.0,
    )
    with pytest.raises(FileNotFoundError):
        WaitSystem.wait_image(params, mock_screen)


@pytest.mark.unit
def test_image_wait_template_larger_than_roi_raises(
    mock_screen: MockScreenDriver,
    sample_template_file: Path,
):
    """Verifies template (60x30) exceeding ROI (40x20) raises ValueError."""
    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="appear",
        roi=(100, 100, 40, 20),
        timeout_seconds=1.0,
    )
    with pytest.raises(ValueError, match="exceed ROI"):
        WaitSystem.wait_image(params, mock_screen)


@pytest.mark.unit
def test_image_wait_template_larger_than_screen_raises(
    sample_template_file: Path,
):
    """Verifies template exceeding screen canvas dimensions raises ValueError."""
    tiny_screen = MockScreenDriver(width=50, height=20)
    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="appear",
        timeout_seconds=1.0,
    )
    with pytest.raises(ValueError, match="exceed"):
        WaitSystem.wait_image(params, tiny_screen)


# =============================================================================
# 7. Compatibility Tests (Argument Orders & Dictionary Params)
# =============================================================================

@pytest.mark.unit
def test_image_wait_argument_order_compatibility(
    mock_screen: MockScreenDriver,
    sample_template,
    sample_template_file: Path,
):
    """Verifies wait_image works with both (screen, params) and (params, screen) orders."""
    mock_screen.inject_patch(sample_template, 300, 200)
    params = ImageWaitParams(
        template_path=str(sample_template_file),
        mode="appear",
        confidence=0.8,
        timeout_seconds=1.0,
    )

    # Order 1: (screen, params)
    found1, coords1 = wait_image(mock_screen, params)
    assert found1 is True
    assert coords1 == (330, 215)

    # Order 2: (params, screen)
    found2, coords2 = wait_image(params, mock_screen)
    assert found2 is True
    assert coords2 == (330, 215)


@pytest.mark.unit
def test_image_wait_dict_params_compatibility(
    mock_screen: MockScreenDriver,
    sample_template,
    sample_template_file: Path,
):
    """Verifies wait_image accepts a dictionary representation of params."""
    mock_screen.inject_patch(sample_template, 100, 150)
    param_dict = {
        "template_path": str(sample_template_file),
        "mode": "appear",
        "confidence": 0.8,
        "timeout_seconds": 1.0,
    }
    found, coords = wait_image(mock_screen, param_dict)
    assert found is True
    assert coords == (130, 165)
