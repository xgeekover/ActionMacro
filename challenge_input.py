"""
challenge_input.py
==================
Adversarial Empirical Challenge Harness for Milestone 2:
- Mouse precision verification across 1,000 random coordinates (|actual - target| <= 1px)
- Sliced wait emergency cancellation latency during 10s wait (< 25ms, strictly < 1.0s)
- Stuck button and modifier hold simulation and safety_flush() cleanup verification

Executes both as standalone executable CLI script and as a pytest test suite.
"""

from __future__ import annotations

import logging
import math
import os
import random
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pytest

from action_macro.core.models import ImageWaitParams, TimeoutAction
from action_macro.engine.coordinate_transformer import CoordinateTransformer
from action_macro.engine.input_adapter import InputAdapterProtocol, PynputAdapter
from action_macro.engine.wait_system import WaitSystem
from tests.mocks.mock_input import InputEvent, MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


# ---------------------------------------------------------------------------
# Test Fakes for Controlled Controller Simulation
# ---------------------------------------------------------------------------

class FakeMouseController:
    """Mock pynput MouseController tracking position, presses, and releases."""

    def __init__(self, initial_x: float = 0.0, initial_y: float = 0.0) -> None:
        self.position: Tuple[float, float] = (float(initial_x), float(initial_y))
        self.pressed: List[Any] = []
        self.released: List[Any] = []
        self.clicks: List[Tuple[Any, int]] = []

    def press(self, button: Any) -> None:
        self.pressed.append(button)

    def release(self, button: Any) -> None:
        self.released.append(button)

    def click(self, button: Any, count: int = 1) -> None:
        self.clicks.append((button, count))


class FakeKeyboardController:
    """Mock pynput KeyboardController tracking key presses, releases, and text."""

    def __init__(self) -> None:
        self.pressed: List[Any] = []
        self.released: List[Any] = []
        self.typed: List[str] = []

    def press(self, key: Any) -> None:
        self.pressed.append(key)

    def release(self, key: Any) -> None:
        self.released.append(key)

    def type(self, text: str) -> None:
        self.typed.append(text)


class FaultyController:
    """Mock controller that raises an exception on specific releases to test resilience."""

    def __init__(self, fail_keys: List[Any]) -> None:
        self.fail_keys = set(fail_keys)
        self.pressed: List[Any] = []
        self.released: List[Any] = []

    def press(self, key: Any) -> None:
        self.pressed.append(key)

    def release(self, key: Any) -> None:
        if key in self.fail_keys:
            raise OSError(f"Simulated hardware failure releasing key: {key}")
        self.released.append(key)


# =============================================================================
# CHALLENGE SUITE 1: MOUSE PRECISION (1,000 Random Coordinates Across Boundaries)
# =============================================================================

def generate_1000_coordinates() -> List[Tuple[float, float, str]]:
    """Generates 1,000 diverse coordinates including extreme screen boundaries and subpixels."""
    rng = random.Random(42)  # Deterministic seed for reproducible adversarial challenge
    coords: List[Tuple[float, float, str]] = []

    resolutions = [
        (1366, 768),   # Laptop
        (1440, 900),   # MacBook Air
        (1920, 1080),  # Full HD
        (2560, 1440),  # QHD
        (2880, 1800),  # MacBook Pro Retina
        (3840, 2160),  # 4K UHD
        (7680, 2160),  # Dual 4K Ultra-wide
    ]

    # 1. Screen boundaries and edges (100 points)
    for w, h in resolutions:
        coords.append((0.0, 0.0, "origin"))
        coords.append((float(w - 1), float(h - 1), "bottom_right"))
        coords.append((0.0, float(h - 1), "bottom_left"))
        coords.append((float(w - 1), 0.0, "top_right"))
        coords.append((float(w // 2), float(h // 2), "center"))
        coords.append((1.0, 1.0, "near_origin"))
        coords.append((float(w), float(h), "boundary_edge"))

    # 2. Subpixel floating-point offsets (200 points)
    subpixel_deltas = [-0.499, -0.4, -0.1, 0.0, 0.1, 0.4, 0.499, 0.5, 0.501, 0.999]
    for _ in range(20):
        w, h = rng.choice(resolutions)
        base_x = rng.randint(10, w - 20)
        base_y = rng.randint(10, h - 20)
        for dx in subpixel_deltas:
            coords.append((base_x + dx, base_y + rng.choice(subpixel_deltas), "subpixel"))

    # 3. High-entropy random points across screen dimensions (fill remaining to 1000)
    while len(coords) < 1000:
        w, h = rng.choice(resolutions)
        # 50% integer, 50% floating point
        if rng.random() < 0.5:
            x = float(rng.randint(0, w - 1))
            y = float(rng.randint(0, h - 1))
            category = "random_integer"
        else:
            x = rng.uniform(0.0, float(w - 1))
            y = rng.uniform(0.0, float(h - 1))
            category = "random_float"
        coords.append((x, y, category))

    return coords[:1000]


def check_mouse_precision_1000_mock_driver() -> Tuple[float, float]:
    """Challenge: 1,000 random coordinates on MockInputDriver asserting |actual - target| <= 1px."""
    driver = MockInputDriver()
    coords = generate_1000_coordinates()
    assert len(coords) == 1000

    max_diff_x = 0.0
    max_diff_y = 0.0

    for idx, (tx, ty, cat) in enumerate(coords):
        driver.move_to(tx, ty)
        ax, ay = driver.get_position()

        diff_x = abs(ax - tx)
        diff_y = abs(ay - ty)
        max_diff_x = max(max_diff_x, diff_x)
        max_diff_y = max(max_diff_y, diff_y)

        assert diff_x <= 1.0 + 1e-9, f"[{idx}] Failed precision on X ({cat}): target={tx}, actual={ax}, diff={diff_x}"
        assert diff_y <= 1.0 + 1e-9, f"[{idx}] Failed precision on Y ({cat}): target={ty}, actual={ay}, diff={diff_y}"

        # Test click precision on sample
        if idx % 10 == 0:
            driver.click(tx, ty, button="left")
            driver.assert_last_click_precision(int(round(tx)), int(round(ty)), tolerance=1)

    return max_diff_x, max_diff_y


def test_challenge_mouse_precision_1000_mock_driver():
    check_mouse_precision_1000_mock_driver()


def check_mouse_precision_1000_pynput_adapter() -> Tuple[float, float]:
    """Challenge: 1,000 random coordinates on PynputAdapter asserting |actual - target| <= 1px."""
    fake_mouse = FakeMouseController()
    fake_keyboard = FakeKeyboardController()
    adapter = PynputAdapter(mouse_controller=fake_mouse, keyboard_controller=fake_keyboard)

    coords = generate_1000_coordinates()
    assert len(coords) == 1000

    max_diff_x = 0.0
    max_diff_y = 0.0

    for idx, (tx, ty, cat) in enumerate(coords):
        adapter.move_to(tx, ty)
        ax, ay = adapter.get_position()

        diff_x = abs(ax - tx)
        diff_y = abs(ay - ty)
        max_diff_x = max(max_diff_x, diff_x)
        max_diff_y = max(max_diff_y, diff_y)

        assert diff_x <= 1.0 + 1e-9, f"[{idx}] Failed PynputAdapter X ({cat}): target={tx}, actual={ax}, diff={diff_x}"
        assert diff_y <= 1.0 + 1e-9, f"[{idx}] Failed PynputAdapter Y ({cat}): target={ty}, actual={ay}, diff={diff_y}"

        # Test click records within 1px
        if idx % 10 == 0:
            adapter.click(tx, ty, button="left")
            last = adapter.last_click
            assert last is not None
            assert abs(last["x"] - tx) <= 1.0 + 1e-9
            assert abs(last["y"] - ty) <= 1.0 + 1e-9

    return max_diff_x, max_diff_y


def test_challenge_mouse_precision_1000_pynput_adapter():
    check_mouse_precision_1000_pynput_adapter()


def check_coordinate_transformer_reversibility_1000_points() -> float:
    """Challenge: Reversibility of logical <-> physical across variable DPI scaling."""
    rng = random.Random(99)
    scaling_factors = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]

    max_reversibility_error = 0.0

    for scale in scaling_factors:
        for _ in range(200):
            orig_lx = rng.randint(0, 1920)
            orig_ly = rng.randint(0, 1080)

            px, py = CoordinateTransformer.logical_to_physical(orig_lx, orig_ly, scale)
            rec_lx, rec_ly = CoordinateTransformer.physical_to_logical(px, py, scale)

            err_x = abs(rec_lx - orig_lx)
            err_y = abs(rec_ly - orig_ly)
            max_reversibility_error = max(max_reversibility_error, err_x, err_y)

            # Integer discretization round-trip error must be <= 1px
            assert err_x <= 1, f"Scale {scale} failed X reversibility: {orig_lx} -> ({px},{py}) -> {rec_lx}"
            assert err_y <= 1, f"Scale {scale} failed Y reversibility: {orig_ly} -> ({px},{py}) -> {rec_ly}"

    return max_reversibility_error


def test_challenge_coordinate_transformer_reversibility_1000_points():
    check_coordinate_transformer_reversibility_1000_points()


# =============================================================================
# CHALLENGE SUITE 2: SLICED WAIT CANCELLATION LATENCY (< 25ms DURING 10s WAIT)
# =============================================================================

def check_wait_cancellation_latency_preset_event() -> float:
    """Challenge: Wait with pre-set cancel_event aborts in < 25ms (instantaneous)."""
    cancel_event = threading.Event()
    cancel_event.set()

    t0 = time.perf_counter()
    res = WaitSystem.wait(10.0, cancel_event=cancel_event)
    elapsed = time.perf_counter() - t0

    assert res is False
    assert elapsed < 0.025, f"Pre-set cancellation took {elapsed*1000:.3f}ms (must be < 25ms)"
    return elapsed * 1000.0


def test_challenge_wait_cancellation_latency_preset_event():
    check_wait_cancellation_latency_preset_event()


def check_wait_cancellation_latency_during_10s_wait() -> float:
    """Challenge: Trigger emergency cancel during 10.0s wait and verify abort happens in <25ms."""
    cancel_event = threading.Event()
    trigger_time_box: List[float] = []
    abort_time_box: List[float] = []

    def _trigger_cancel():
        # Sleep 50ms into the 10s wait
        time.sleep(0.050)
        trigger_time_box.append(time.perf_counter())
        cancel_event.set()

    thread = threading.Thread(target=_trigger_cancel, daemon=True)
    thread.start()

    res = WaitSystem.wait(10.0, cancel_event=cancel_event)
    abort_time_box.append(time.perf_counter())
    thread.join(timeout=1.0)

    assert res is False
    assert len(trigger_time_box) == 1 and len(abort_time_box) == 1
    latency = abort_time_box[0] - trigger_time_box[0]
    latency_ms = latency * 1000.0

    # Requirement: Must abort in < 25ms (well within the < 1.0s requirement)
    assert latency_ms < 25.0, f"Emergency abort latency {latency_ms:.2f}ms exceeded 25ms threshold"
    return latency_ms


def test_challenge_wait_cancellation_latency_during_10s_wait():
    check_wait_cancellation_latency_during_10s_wait()


def check_wait_cancellation_multi_trial_stress() -> Dict[str, Any]:
    """Challenge: 50 trials of asynchronous emergency cancellation during 10s wait."""
    latencies: List[float] = []
    rng = random.Random(123)

    for trial in range(50):
        cancel_event = threading.Event()
        trigger_box: List[float] = []
        abort_box: List[float] = []
        delay = rng.uniform(0.005, 0.040)  # Random interrupt timing

        def _worker(d: float, ce: threading.Event, tb: List[float]):
            time.sleep(d)
            tb.append(time.perf_counter())
            ce.set()

        t = threading.Thread(target=_worker, args=(delay, cancel_event, trigger_box), daemon=True)
        t.start()

        res = WaitSystem.wait(10.0, cancel_event=cancel_event)
        abort_box.append(time.perf_counter())
        t.join(timeout=1.0)

        assert res is False
        assert len(trigger_box) == 1
        lat_ms = (abort_box[0] - trigger_box[0]) * 1000.0
        assert lat_ms < 25.0, f"Trial {trial} latency {lat_ms:.2f}ms exceeded 25ms limit"
        latencies.append(lat_ms)

    return {
        "count": len(latencies),
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "mean_ms": statistics.mean(latencies),
        "median_ms": statistics.median(latencies),
        "p95_ms": np.percentile(latencies, 95),
        "p99_ms": np.percentile(latencies, 99),
    }


def test_challenge_wait_cancellation_multi_trial_stress():
    check_wait_cancellation_multi_trial_stress()


def check_wait_cancellation_during_pause_state() -> float:
    """Challenge: Cancel during a paused state in a 10s wait and verify abort < 25ms."""
    cancel_event = threading.Event()
    pause_event = threading.Event()
    pause_event.set()  # Already paused

    trigger_box: List[float] = []
    abort_box: List[float] = []

    def _trigger():
        time.sleep(0.030)
        trigger_box.append(time.perf_counter())
        cancel_event.set()

    t = threading.Thread(target=_trigger, daemon=True)
    t.start()

    res = WaitSystem.wait(10.0, cancel_event=cancel_event, pause_event=pause_event)
    abort_box.append(time.perf_counter())
    t.join(timeout=1.0)

    assert res is False
    lat_ms = (abort_box[0] - trigger_box[0]) * 1000.0
    assert lat_ms < 25.0, f"Paused wait cancel latency {lat_ms:.2f}ms exceeded 25ms threshold"
    return lat_ms


def test_challenge_wait_cancellation_during_pause_state():
    check_wait_cancellation_during_pause_state()


def check_image_wait_cancellation_latency(tmp_path: Path) -> float:
    """Challenge: Image wait polling interrupted by emergency cancel aborts in < 25ms."""
    # Create synthetic screen and template
    screen = MockScreenDriver(width=800, height=600)
    screen.clear_canvas((0, 0, 0))

    templ = np.full((50, 50, 3), 255, dtype=np.uint8)
    templ_path = tmp_path / "target_absent.png"
    import cv2
    cv2.imwrite(str(templ_path), templ)

    params = ImageWaitParams(
        template_path=str(templ_path),
        mode="appear",
        confidence=0.8,
        timeout_seconds=10.0,
        poll_interval_seconds=0.01,
        timeout_action="skip",
    )

    cancel_event = threading.Event()
    trigger_box: List[float] = []
    abort_box: List[float] = []

    def _trigger():
        time.sleep(0.040)
        trigger_box.append(time.perf_counter())
        cancel_event.set()

    t = threading.Thread(target=_trigger, daemon=True)
    t.start()

    found, coords = WaitSystem.wait_image(screen, params, cancel_event=cancel_event)
    abort_box.append(time.perf_counter())
    t.join(timeout=1.0)

    assert found is False
    assert coords is None
    lat_ms = (abort_box[0] - trigger_box[0]) * 1000.0
    assert lat_ms < 25.0, f"Image wait cancel latency {lat_ms:.2f}ms exceeded 25ms threshold"
    return lat_ms


def test_challenge_image_wait_cancellation_latency(tmp_path: Path):
    check_image_wait_cancellation_latency(tmp_path)


# =============================================================================
# CHALLENGE SUITE 3: SAFETY FLUSH & STUCK STATE CLEARANCE
# =============================================================================

def test_challenge_mock_input_driver_stuck_holds_cleared():
    """Challenge: Simulate all stuck buttons and modifiers on MockInputDriver and verify flush."""
    driver = MockInputDriver()

    # Simulate stuck mouse buttons
    for btn in ("left", "right", "middle"):
        driver.mouse_down(btn)

    # Simulate stuck keyboard modifiers and keys
    stuck_keys = ["ctrl", "alt", "shift", "cmd", "space", "enter", "f1", "a", "tab", "esc"]
    for k in stuck_keys:
        driver.key_down(k)

    assert len(driver.active_buttons) == 3
    assert len(driver.active_keys) == len(stuck_keys)
    assert driver.safety_flush_called is False

    driver.safety_flush()

    assert len(driver.active_buttons) == 0, f"Active buttons not cleared: {driver.active_buttons}"
    assert len(driver.active_keys) == 0, f"Active keys not cleared: {driver.active_keys}"
    assert driver.safety_flush_called is True
    assert driver.flush_count == 1

    # Verify every held item generated an explicit release event
    event_types = [e.event_type for e in driver.events]
    assert event_types.count("mouse_up") >= 3
    assert event_types.count("key_up") >= len(stuck_keys)
    assert event_types[-1] == "safety_flush"


def test_challenge_pynput_adapter_stuck_holds_cleared():
    """Challenge: Simulate stuck buttons/modifiers on PynputAdapter and verify controller releases."""
    fake_mouse = FakeMouseController()
    fake_keyboard = FakeKeyboardController()
    adapter = PynputAdapter(mouse_controller=fake_mouse, keyboard_controller=fake_keyboard)

    # Simulate stuck mouse buttons
    adapter.mouse_down("left")
    adapter.mouse_down("right")
    adapter.mouse_down("middle")

    # Simulate stuck modifiers and regular keys
    stuck_keys = ["ctrl", "alt", "shift", "cmd", "space", "z", "enter", "f5"]
    for k in stuck_keys:
        adapter.key_down(k)

    assert len(adapter.active_buttons) == 3
    assert len(adapter.active_keys) == len(stuck_keys)

    adapter.safety_flush()

    assert len(adapter.active_buttons) == 0, f"Active buttons remaining: {adapter.active_buttons}"
    assert len(adapter.active_keys) == 0, f"Active keys remaining: {adapter.active_keys}"

    # Verify underlying fake mouse controller received releases
    assert len(fake_mouse.released) >= 3

    # Verify underlying fake keyboard controller received releases
    assert len(fake_keyboard.released) >= len(stuck_keys)


def test_challenge_safety_flush_idempotency_and_empty_state():
    """Challenge: Calling safety_flush() repeatedly or on empty state is clean and non-crashing."""
    fake_mouse = FakeMouseController()
    fake_keyboard = FakeKeyboardController()
    adapter = PynputAdapter(mouse_controller=fake_mouse, keyboard_controller=fake_keyboard)

    # 1. Flush when completely idle
    assert len(adapter.active_buttons) == 0
    assert len(adapter.active_keys) == 0
    adapter.safety_flush()
    assert len(adapter.active_buttons) == 0
    assert len(adapter.active_keys) == 0

    # 2. Call 10 times consecutively
    for _ in range(10):
        adapter.safety_flush()

    assert len(adapter.active_buttons) == 0
    assert len(adapter.active_keys) == 0


def test_challenge_safety_flush_controller_hardware_error_resilience():
    """Challenge: Even if controller throws on releasing a specific key, flush clears remaining holds."""
    # Controller that fails specifically when releasing 'ctrl'
    faulty_keyboard = FaultyController(fail_keys=["ctrl"])
    fake_mouse = FakeMouseController()
    adapter = PynputAdapter(mouse_controller=fake_mouse, keyboard_controller=faulty_keyboard)

    adapter.key_down("ctrl")
    adapter.key_down("shift")
    adapter.key_down("alt")

    assert len(adapter.active_keys) == 3

    # safety_flush must not crash unhandled, and must clear tracking sets
    adapter.safety_flush()

    assert len(adapter.active_keys) == 0
    # shift and alt should still have been released despite ctrl failing
    assert len(faulty_keyboard.released) >= 2


def test_challenge_safety_flush_multi_threaded_concurrency():
    """Challenge: Concurrent key/button down operations interrupted by safety_flush."""
    fake_mouse = FakeMouseController()
    fake_keyboard = FakeKeyboardController()
    adapter = PynputAdapter(mouse_controller=fake_mouse, keyboard_controller=fake_keyboard)

    stop_flag = threading.Event()
    errors: List[Exception] = []

    def _spam_input():
        keys = ["ctrl", "alt", "shift", "a", "b", "c", "space", "enter"]
        while not stop_flag.is_set():
            try:
                k = random.choice(keys)
                adapter.key_down(k)
                time.sleep(0.001)
                adapter.key_up(k)
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=_spam_input, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()

    # Trigger periodic flushes while threads are hammering the adapter
    for _ in range(10):
        time.sleep(0.005)
        adapter.safety_flush()

    stop_flag.set()
    for t in threads:
        t.join(timeout=1.0)

    # Final definitive flush
    adapter.safety_flush()

    assert len(errors) == 0, f"Concurrent input errors: {errors}"
    assert len(adapter.active_buttons) == 0
    assert len(adapter.active_keys) == 0


# =============================================================================
# Standalone CLI Entrypoint & Reporting
# =============================================================================

def run_all_challenges() -> Dict[str, Any]:
    """Executes all challenge suites and compiles comprehensive empirical metrics."""
    print("=" * 80)
    print("EMPIRICAL ADVERSARIAL CHALLENGE HARNESS — MILESTONE 2")
    print("=" * 80)
    t_start = time.perf_counter()

    results: Dict[str, Any] = {}

    # Suite 1
    print("\n[SUITE 1] Challenging Mouse Precision (1,000 Coordinates across Boundaries)...")
    m1_x, m1_y = check_mouse_precision_1000_mock_driver()
    print(f"  ✓ MockInputDriver: 1,000 points verified | max_diff_x={m1_x:.4f}px, max_diff_y={m1_y:.4f}px (target <= 1.0px)")

    m2_x, m2_y = check_mouse_precision_1000_pynput_adapter()
    print(f"  ✓ PynputAdapter:   1,000 points verified | max_diff_x={m2_x:.4f}px, max_diff_y={m2_y:.4f}px (target <= 1.0px)")

    m3_err = check_coordinate_transformer_reversibility_1000_points()
    print(f"  ✓ CoordinateTransformer: 1,400 DPI scale tests | max_reversibility_error={m3_err:.1f}px (target <= 1.0px)")

    results["mouse_precision"] = {
        "status": "PASS",
        "mock_driver_max_diff": max(m1_x, m1_y),
        "pynput_adapter_max_diff": max(m2_x, m2_y),
        "transformer_reversibility_max_err": m3_err,
    }

    # Suite 2
    print("\n[SUITE 2] Challenging Sliced Wait Cancellation Latency (< 25ms during 10s Wait)...")
    preset_ms = check_wait_cancellation_latency_preset_event()
    print(f"  ✓ Pre-set Cancel:         {preset_ms:.3f}ms (target < 25ms)")

    single_10s_ms = check_wait_cancellation_latency_during_10s_wait()
    print(f"  ✓ Single 10s Wait Abort:  {single_10s_ms:.3f}ms (target < 25ms)")

    stats = check_wait_cancellation_multi_trial_stress()
    print(f"  ✓ 50 Trials Stress:       mean={stats['mean_ms']:.2f}ms, max={stats['max_ms']:.2f}ms, p99={stats['p99_ms']:.2f}ms (all < 25ms)")

    pause_ms = check_wait_cancellation_during_pause_state()
    print(f"  ✓ Paused Wait Cancel:     {pause_ms:.3f}ms (target < 25ms)")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        img_ms = check_image_wait_cancellation_latency(Path(td))
    print(f"  ✓ Image Wait Cancel:      {img_ms:.3f}ms (target < 25ms)")

    results["wait_cancellation"] = {
        "status": "PASS",
        "preset_cancel_ms": preset_ms,
        "single_10s_abort_ms": single_10s_ms,
        "multi_trial_stats": stats,
        "paused_wait_cancel_ms": pause_ms,
        "image_wait_cancel_ms": img_ms,
    }

    # Suite 3
    print("\n[SUITE 3] Challenging Safety Flush & Stuck Holds Cleanup...")
    test_challenge_mock_input_driver_stuck_holds_cleared()
    print("  ✓ MockInputDriver:        All 3 buttons & 10 modifier keys cleared")

    test_challenge_pynput_adapter_stuck_holds_cleared()
    print("  ✓ PynputAdapter:          All 3 buttons & 8 modifier keys cleared on controller")

    test_challenge_safety_flush_idempotency_and_empty_state()
    print("  ✓ Idempotency:            10 consecutive flushes clean with 0 leaks")

    test_challenge_safety_flush_controller_hardware_error_resilience()
    print("  ✓ Fault Resilience:       Controller release exceptions handled gracefully")

    test_challenge_safety_flush_multi_threaded_concurrency()
    print("  ✓ Concurrency Stress:     4 hammer threads + periodic flush zero errors/holds")

    results["safety_flush"] = {
        "status": "PASS",
        "all_holds_cleared": True,
        "idempotent": True,
        "fault_resilient": True,
        "concurrent_safe": True,
    }

    elapsed_total = time.perf_counter() - t_start
    print("\n" + "=" * 80)
    print(f"VERDICT: ALL 13 ADVERSARIAL CHALLENGES PASSED in {elapsed_total:.2f}s")
    print("FORMAL RECOMMENDATION: APPROVE")
    print("=" * 80)

    results["total_time_s"] = elapsed_total
    results["verdict"] = "APPROVE"
    return results


if __name__ == "__main__":
    res = run_all_challenges()
    sys.exit(0 if res["verdict"] == "APPROVE" else 1)
