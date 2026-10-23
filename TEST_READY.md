# Test Suite Ready: `action_macro` E2E Test Harness

**Document**: Test Delivery Specification & Readiness Publication  
**Author**: `teamwork_preview_test_writer_track_1` (E2E Testing Track)  
**Status**: COMPLETE (64/64 tests collected and verified passing)  
**Date**: 2026-10-17T13:23:45Z  

---

## 1. Test Runner & Execution Commands

The test suite runs deterministically in both local development and headless CI/CD environments without requiring physical monitors or display hardware.

### Primary Runner Command
```bash
uv run pytest tests/e2e -v
```

### Self-Contained Dependency Runner (Zero-Install)
```bash
uv run --with pytest --with pytest-qt --with opencv-python-headless --with pillow --with pynput pytest tests/e2e/ -v
```

### Test Discovery Command
```bash
uv run pytest --collect-only tests/e2e
```

---

## 2. Test Architecture & Counts Per Tier

| Tier | Focus Area | File Path | Required Count | Implemented Count | Status |
|:---|:---|:---|:---:|:---:|:---:|
| **Tier 1** | Feature Coverage ($\ge 5$ per feature across all 8 features) | `tests/e2e/test_tier1_features.py` | $\ge 40$ | **42** | PASSED |
| **Tier 2** | Boundary & Corner Cases | `tests/e2e/test_tier2_boundaries.py` | $\ge 10$ | **11** | PASSED |
| **Tier 3** | Cross-Feature Interactions | `tests/e2e/test_tier3_interactions.py` | $\ge 8$ | **8** | PASSED |
| **Tier 4** | Real-World Workload Scenarios | `tests/e2e/test_tier4_scenarios.py` | 3 | **3** | PASSED |
| **Total** | Full E2E Test Suite | `tests/e2e/` | $\ge 61$ | **64** | **100% PASS** |

---

## 3. Tier 1 Feature Checklist (All 8 Features Covered)

Each feature includes $\ge 5$ isolated, requirement-derived test cases:

### Feature 1: Mouse Click Action Block (`TestFeature1MouseClick`) — 6 tests
- [x] **T1.1.1** `test_mouse_click_single_left_precision`: Left click at (500, 300) with verified $\pm 1\text{px}$ coordinate precision.
- [x] **T1.1.2** `test_mouse_click_right_button`: Right button click at (1200, 800) recorded in event log.
- [x] **T1.1.3** `test_mouse_click_double_click`: Double click at (640, 480) with `clicks=2` and `click_type='double'`.
- [x] **T1.1.4** `test_mouse_click_middle_button`: Middle mouse button click at (960, 540) recorded.
- [x] **T1.1.5** `test_mouse_click_press_duration_hold`: Press duration parameter holds button down for specified time before release.
- [x] **T1.1.6** `test_mouse_click_consecutive_multi_target_precision`: Sequence of clicks across screen boundaries maintaining $\pm 1\text{px}$ precision.

### Feature 2: Keyboard Input Action Block (`TestFeature2KeyInput`) — 5 tests
- [x] **T1.2.1** `test_key_input_single_alpha_key`: Single key press ('a') records both key_down and key_up events in sequence.
- [x] **T1.2.2** `test_key_input_special_function_keys`: Special keys ('enter', 'esc', 'f12', 'space') dispatched and mapped correctly.
- [x] **T1.2.3** `test_key_input_modifier_combination`: Modifier combo ('Ctrl+C') dispatches modifier down $\to$ key down $\to$ key up $\to$ modifier up.
- [x] **T1.2.4** `test_key_input_unicode_korean_text`: Unicode Korean text typing ("퀘스트완료 (Daily Quest)") recorded with full fidelity.
- [x] **T1.2.5** `test_key_input_hold_and_release`: Discrete `key_down` and `key_up` operations properly track held keys in `active_keys`.

### Feature 3: Fixed Time Wait (`TestFeature3FixedWait`) — 5 tests
- [x] **T1.3.1** `test_fixed_wait_short_duration_accuracy`: 50ms wait executes and completes accurately ($\Delta t \ge 45\text{ms}$).
- [x] **T1.3.2** `test_fixed_wait_zero_duration_instant`: 0ms wait returns immediately ($\le 10\text{ms}$) with status `True`.
- [x] **T1.3.3** `test_fixed_wait_cooperative_cancellation`: 5.0s wait cancelled at 40ms aborts in $\le 100\text{ms}$ returning `False`.
- [x] **T1.3.4** `test_fixed_wait_sliced_check_latency`: Sliced wait loop ($\le 20\text{ms}$ slices) responds to pre-set cancel event in $\le 25\text{ms}$.
- [x] **T1.3.5** `test_fixed_wait_negative_duration_safe`: Negative wait duration clamps safely to 0 without throwing exceptions.

### Feature 4: Image Recognition Wait (`TestFeature4ImageWait`) — 5 tests
- [x] **T1.4.1** `test_image_wait_immediate_appearance`: Template present on canvas at $t=0$ matched on first frame ($R \ge 0.8$) returning exact center coordinates.
- [x] **T1.4.2** `test_image_wait_delayed_appearance_at_step`: Template injected at step 2 is detected when virtual clock advances to step 2.
- [x] **T1.4.3** `test_image_wait_disappearance_mode`: Template present at start and removed at step 2 completes once template vanishes.
- [x] **T1.4.4** `test_image_wait_roi_subregion_bounding_box`: Template inside ROI bounding box maps accurately to absolute screen coordinates.
- [x] **T1.4.5** `test_image_wait_timeout_raise_and_skip_policies`: Absent template raises `TimeoutError` when `timeout_action='raise'` and returns `(False, None)` when `'skip'`.

### Feature 5: Scenario File Persistence (`TestFeature5Persistence`) — 5 tests
- [x] **T1.5.1** `test_persistence_roundtrip_all_action_types`: Scenario containing Click, Key, FixedWait, and ImageWait roundtrips identically through JSON save/load.
- [x] **T1.5.2** `test_persistence_unicode_korean_content`: Korean metadata and action descriptions preserve UTF-8 encoding without corruption.
- [x] **T1.5.3** `test_persistence_relative_template_paths`: Relative template paths serialized with POSIX forward slashes (`/`).
- [x] **T1.5.4** `test_persistence_pretty_json_format`: Saved JSON file formatted with 2-space indentation and trailing newline.
- [x] **T1.5.5** `test_persistence_reorder_and_delete_actions`: Modifying action sequence (delete, reorder) is accurately persisted and reloaded.

### Feature 6: Repeat Execution Loop (`TestFeature6ExecutionLoop`) — 5 tests
- [x] **T1.6.1** `test_loop_fixed_count_n_executions`: Setting `loop_count=3` executes entire action sequence exactly 3 times.
- [x] **T1.6.2** `test_loop_single_iteration`: Setting `loop_count=1` executes action sequence once.
- [x] **T1.6.3** `test_loop_inter_iteration_delay`: Inter-loop delay (`loop_delay_ms`) respected between iterations.
- [x] **T1.6.4** `test_loop_infinite_loop_abort`: Setting `loop_count=0` (infinite) runs until `stop()` signal is received, exiting cleanly.
- [x] **T1.6.5** `test_loop_progress_signals_emitted`: Progress signals broadcast `(current_loop, total_loops, current_action, total_actions)`.

### Feature 7: Emergency Stop Hotkey (`TestFeature7EmergencyStop`) — 6 tests
- [x] **T1.7.1** `test_emergency_stop_subsecond_abort_latency`: Emergency stop terminates running macro in $\le 50\text{ms}$ (well under $1.0\text{s}$ limit).
- [x] **T1.7.2** `test_emergency_stop_safety_flush_active_holds`: Emergency stop forcibly dispatches `safety_flush()` to release all held buttons and keys.
- [x] **T1.7.3** `test_emergency_stop_idempotent_multiple_calls`: Rapid consecutive `stop()` calls handled cleanly without crashes or deadlocks.
- [x] **T1.7.4** `test_emergency_stop_state_transitions`: State transitions cleanly through `RUNNING` $\to$ `STOPPING` $\to$ `STOPPED`.
- [x] **T1.7.5** `test_emergency_stop_during_sleep_wake`: Sliced wait loop wakes up and aborts within $\le 20\text{ms}$ of cancel signal.
- [x] **T1.7.6** `test_emergency_stop_custom_hotkey_configuration`: Custom hotkey tokens ('ctrl+shift+q', 'f11', etc.) validated.

### Feature 8: Coordinate Picker / Math (`TestFeature8CoordinatePicker`) — 5 tests
- [x] **T1.8.1** `test_coordinate_picker_realtime_tracking`: Cursor movement updates live coordinate display in real time.
- [x] **T1.8.2** `test_coordinate_picker_capture_into_action_block`: Capturing coordinate automatically injects $(X, Y)$ into target ActionBlock.
- [x] **T1.8.3** `test_coordinate_picker_retina_dpr_transform`: `CoordinateTransformer` converts physical pixels (2880, 1800) with DPR=2.0 to logical points (1440, 900).
- [x] **T1.8.4** `test_coordinate_picker_pixel_color_query`: Screen driver queries RGB pixel values and formats as `#RRGGBB` hex string.
- [x] **T1.8.5** `test_coordinate_picker_cancellation_preserves_action`: Cancelling picker (Escape) leaves existing action coordinates untouched.

---

## 4. Tier 2: Boundary & Corner Cases Checklist (11 tests)
- [x] **T2.1** `test_boundary_zero_and_sub_millisecond_wait`: Zero duration wait returns instantly ($\le 10\text{ms}$) without blocking.
- [x] **T2.2** `test_boundary_negative_wait_duration`: Negative duration clamps to 0 safely without errors.
- [x] **T2.3** `test_boundary_missing_template_file`: Non-existent template path raises `FileNotFoundError` immediately.
- [x] **T2.4** `test_boundary_out_of_bounds_screen_coordinates`: Coordinates exceeding canvas bounds raise `IndexError` / `ValueError`.
- [x] **T2.5** `test_boundary_empty_scenario_execution`: Scenario with `actions=[]` completes cleanly with `success=True`.
- [x] **T2.6** `test_boundary_immediate_abort_at_t0`: Abort signal pre-set before `run()` starts handled safely without race condition.
- [x] **T2.7** `test_boundary_malformed_json_persistence`: Corrupted JSON file raises `ValueError` cleanly without unhandled crash.
- [x] **T2.8** `test_boundary_template_larger_than_roi_or_screen`: Template dimensions larger than screen or ROI raise `ValueError`.
- [x] **T2.9** `test_boundary_flat_zero_variance_template`: Flat solid single-color template handled gracefully via fallback algorithm.
- [x] **T2.10** `test_boundary_unsupported_schema_version`: Future unsupported schema version raises `ValueError`.
- [x] **T2.11** `test_boundary_rapid_stop_toggle`: Multi-threaded rapid toggling between run and stop does not deadlock.

---

## 5. Tier 3: Cross-Feature Interactions Checklist (8 tests)
- [x] **T3.1** `test_interaction_image_wait_appear_then_click_matched_coordinates`: Image wait detects button; next action clicks center within $\pm 1\text{px}$.
- [x] **T3.2** `test_interaction_image_wait_disappear_then_fast_key_combo`: Image wait for spinner disappearance immediately triggers key combo.
- [x] **T3.3** `test_interaction_emergency_stop_during_image_polling`: Long 30s image poll interrupted at 40ms aborts in $\le 100\text{ms}$.
- [x] **T3.4** `test_interaction_emergency_stop_during_fixed_wait`: 60s fixed wait interrupted at 30ms exits in $\le 100\text{ms}$.
- [x] **T3.5** `test_interaction_multi_loop_with_image_and_click`: 3 iterations of [ImageWait $\to$ Click $\to$ FixedWait] execute in sequence.
- [x] **T3.6** `test_interaction_disabled_action_in_sequence`: Disabled action in sequence is bypassed immediately without executing.
- [x] **T3.7** `test_interaction_coordinate_picker_capture_into_click_action`: Picker captures coordinate $\to$ updates ActionBlock $\to$ engine executes click.
- [x] **T3.8** `test_interaction_pause_resume_during_action_sequence`: Engine paused between actions, resumed, completes sequence cleanly.

---

## 6. Tier 4: Real-World Workloads & E2E Scenarios (3 scenarios)
- [x] **Scenario 1** `test_scenario_bluestacks_daily_quest_loop`: 2 full iterations of BlueStacks Daily Quest & Reward Collection (ImageWait Quest Icon $\to$ Click Icon $\to$ FixedWait $\to$ ImageWait Claim All $\to$ Click Claim $\to$ FixedWait $\to$ ImageWait Confirm Dialog $\to$ Click Confirm $\to$ ImageWait Dismiss $\to$ KeyInput Esc $\to$ FixedWait).
- [x] **Scenario 2** `test_scenario_stage_auto_repeat_with_branching`: Stage farming automation distinguishing between Clear (Victory banner detected $\to$ click Next Stage 1100, 900) and Wipe (Defeat banner detected $\to$ click Return to Lobby 800, 900).
- [x] **Scenario 3** `test_scenario_attendance_reward_claim_with_abort`: Daily calendar 7-tile attendance claim interrupted halfway through by Global Emergency Stop hotkey; aborts in $\le 50\text{ms}$, releases holds, and executes `safety_flush()`.

---

## 7. Deliverables & Artifacts Index

1. `tests/mocks/mock_input.py` — `MockInputDriver` asserting $\pm 1\text{px}$ mouse precision, event log, active hold tracking, safety flush.
2. `tests/mocks/mock_screen.py` — `MockScreenDriver` supplying synthetic test frames, virtual step clock, scheduled frame mutations, ROI cropping, template generators.
3. `tests/mocks/contract.py` — Dynamic interface contract binding to `src/action_macro` when implemented, with specification reference oracle.
4. `tests/mocks/__init__.py` — Package export for mock drivers.
5. `tests/conftest.py` — Shared pytest fixtures (`mock_input`, `mock_screen`, `sample_template`, `sample_template_file`, `sample_scenario`).
6. `tests/e2e/test_tier1_features.py` — 42 Tier 1 feature tests.
7. `tests/e2e/test_tier2_boundaries.py` — 11 Tier 2 boundary tests.
8. `tests/e2e/test_tier3_interactions.py` — 8 Tier 3 interaction tests.
9. `tests/e2e/test_tier4_scenarios.py` — 3 Tier 4 realistic game scenarios.
10. `TEST_READY.md` — This publication document.
