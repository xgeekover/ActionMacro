"""
tests.unit.test_adversarial
===========================
Pytest integration for adversarial stress testing suite.
Imports all test cases directly from adversarial_test.py.
"""

from adversarial_test import (
    test_stress_5000_action_blocks,
    test_stress_duplicate_id_check_linear_scale,
    test_stress_deep_clone_large_scenario,
    test_circular_reference_in_target_resolution,
    test_circular_reference_in_cloning,
    test_sequential_scenario_mutations,
    test_move_action_boundary_indices,
    test_float_coordinates_rejected_with_type_error,
    test_nan_and_inf_coordinates_rejected,
    test_boolean_int_confusion_rejected,
    test_huge_arbitrary_precision_numbers,
    test_empty_and_whitespace_ids_rejected,
    test_image_wait_confidence_boundaries,
    test_image_wait_roi_strict_validation,
    test_truncated_json_payload,
    test_empty_and_whitespace_file_payloads,
    test_non_dict_root_json_types,
    test_missing_mandatory_root_fields,
    test_malformed_action_block_in_dict,
    test_semver_unsupported_future_version,
    test_semver_malformed_version_strings,
    test_unicode_and_special_character_resilience,
    test_save_scenario_make_dirs_behavior,
    test_save_scenario_target_is_existing_directory,
    test_save_scenario_permission_denied_directory,
    test_load_scenario_permission_denied_file,
    test_disk_full_enospc_simulation_atomic_guarantee,
    test_concurrent_multithreaded_readers_and_writers,
)
