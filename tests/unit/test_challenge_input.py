"""
tests.unit.test_challenge_input
===============================
Pytest integration for Milestone 2 adversarial challenge suite.
Imports all test cases directly from challenge_input.py.
"""

from challenge_input import (
    test_challenge_coordinate_transformer_reversibility_1000_points,
    test_challenge_image_wait_cancellation_latency,
    test_challenge_mock_input_driver_stuck_holds_cleared,
    test_challenge_mouse_precision_1000_mock_driver,
    test_challenge_mouse_precision_1000_pynput_adapter,
    test_challenge_pynput_adapter_stuck_holds_cleared,
    test_challenge_safety_flush_controller_hardware_error_resilience,
    test_challenge_safety_flush_idempotency_and_empty_state,
    test_challenge_safety_flush_multi_threaded_concurrency,
    test_challenge_wait_cancellation_during_pause_state,
    test_challenge_wait_cancellation_latency_during_10s_wait,
    test_challenge_wait_cancellation_latency_preset_event,
    test_challenge_wait_cancellation_multi_trial_stress,
)
