# Project: ActionMacro Automation Desktop Tool (`action_macro`)

## Architecture
Cross-platform desktop automation application built in Python using PySide6 (Qt 6) with a strictly decoupled 4-layer architecture:

```
+---------------------------------------------------------------------------------+
|                                 GUI Layer (PySide6)                             |
|  - MainWindow, ScenarioBuilder (cards + drag/drop), PropertyInspector           |
|  - CoordinatePickerOverlay (translucent full-screen HUD + magnifier)            |
|  - LogConsole & ExecutionDashboard (real-time progress & active highlight)      |
+----------------------------------------+----------------------------------------+
                                         | Events / Signals (QueuedConnection)
+----------------------------------------v----------------------------------------+
|                                Runner / Controller                              |
|  - ExecutionEngine: Loop control (N repetitions / infinite), step runner        |
|  - StateMachine: IDLE, RUNNING, PAUSED, STOPPING, STOPPED, ERROR                |
|  - HotkeyListener: Global emergency stop hotkey (<1s abort via threading.Event) |
+----------------------------------------+----------------------------------------+
                                         | Invocations
+----------------------------------------v----------------------------------------+
|                                Automation Engine                                |
|  - InputAdapter: PynputAdapter (mouse ±1px precision, keyboard combos)        |
|  - CoordinateTransformer: Logical Points <-> Physical Pixels (Retina/DPI)      |
|  - WaitSystem: Sliced fixed wait (<20ms cancel) & OpenCV Image Matching         |
|  - ScreenAdapter: MSSScreenAdapter (display capture) & MockScreenDriver         |
+----------------------------------------+----------------------------------------+
                                         | Data Models
+----------------------------------------v----------------------------------------+
|                                   Core Layer                                    |
|  - ScenarioModel, ActionBlock, ActionType (Click, Key, FixedWait, ImageWait)   |
|  - ScenarioPersistence: JSON serialization/deserialization, validation, version  |
|  - KeyMapping: Cross-platform abstract key codes                                |
+---------------------------------------------------------------------------------+
```

## Feature Inventory
Every requirement from ORIGINAL_REQUEST.md is enumerated and assigned to a milestone:

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Visual Scenario Builder GUI | PySide6 desktop window with action block cards, drag-and-drop / up-down reordering, block removal | M4 | ORIGINAL_REQUEST §R1 |
| F2 | Block Property Inspector | Dynamic parameter editor for Mouse, Keyboard, Fixed Wait, and Image Wait action parameters | M4 | ORIGINAL_REQUEST §R1 |
| F3 | Screen Coordinate Picker | Translucent full-screen overlay with live hover coordinates, crosshair, pixel magnifier, click-to-capture | M4 | ORIGINAL_REQUEST §R2 |
| F4 | Precision Mouse Input | Mouse clicks accurate within ±1px across display scaling factors via logical point positioning | M2 | ORIGINAL_REQUEST §R2 |
| F5 | Keyboard Input Automation | Single keys, combination hotkeys (Ctrl/Cmd, Alt, Shift), and special keys | M2 | ORIGINAL_REQUEST §R2 |
| F6 | Fixed Duration Wait | Sliced sleep mechanism (≤20ms slices) with cancellation token and accurate timing | M2 | ORIGINAL_REQUEST §R3 |
| F7 | Image Recognition Wait | OpenCV Grayscale template matching, appearance & disappearance modes, ROI, timeout policies | M2 | ORIGINAL_REQUEST §R3 |
| F8 | Scenario File Persistence | JSON schema serialization/deserialization, relative POSIX template paths, cross-platform compatibility | M1 | ORIGINAL_REQUEST §R4 |
| F9 | Repeat Execution Control | Execution loop supporting repeat count N or infinite loop, pause/resume/stop lifecycle | M3 | ORIGINAL_REQUEST §R4 |
| F10 | Emergency Stop Hotkey | Global hotkey listener interrupting execution in <1s with OS input safety flush | M3 | ORIGINAL_REQUEST §R4 |
| F11 | Execution Dashboard & Logging | Real-time GUI log console, current iteration count, active action visual card highlight | M4 | ORIGINAL_REQUEST §R4 |
| F12 | Packaging & Test Harness | pyproject.toml setup, MockInputDriver & MockScreenDriver for deterministic headless CI testing | M1 | Acceptance Criteria |
| F13 | Cross-Platform Compatibility | Mac and Windows functional compatibility verified by automated tests | M5 | Acceptance Criteria |
| F14 | 100% E2E Test Suite Pass | Passing 100% of requirement-derived E2E test suite (Tiers 1-4) published by E2E Testing Track | M5 | Dual Track Requirement |
| F15 | Adversarial Coverage Hardening | White-box stress testing, corner cases, and forensic integrity audit pass | M5 | Dual Track Requirement |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Core Data Models, Persistence & Test Harness | Data models (`ActionBlock`, `ScenarioModel`), JSON persistence, key mapping, mock drivers (`MockInputDriver`, `MockScreenDriver`), `pyproject.toml` | none | DONE |
| M2 | Cross-Platform Input Engine & Wait System | `InputAdapter` (pynput, ±1px mouse precision), `CoordinateTransformer` (Retina/DPI), `WaitSystem` (sliced wait), `ImageMatcher` (OpenCV Grayscale) | M1 | DONE |
| M3 | Execution Controller & Emergency Stop | `ExecutionEngine` (repeat count/infinite), `StateMachine` (6 states), `HotkeyListener` (<1s abort, safety flush), event broadcasting | M1, M2 | DONE |
| M4 | Desktop GUI & Visual Scenario Builder | PySide6 app, card-based `ScenarioBuilder`, `PropertyInspector`, `CoordinatePickerOverlay` (magnifier), `LogConsole`, `ExecutionDashboard` | M1, M2, M3 | DONE |
| M5 | Final E2E Integration & Verification | Phase 1: 100% pass of E2E test suite (`TEST_READY.md`). Phase 2: Tier 5 adversarial hardening, Challenger & Forensic Auditor gate pass | M1, M2, M3, M4 | DONE |


## Interface Contracts

### 1. `action_macro.core.models` ↔ `action_macro.engine`
- `ActionBlock(id: str, type: ActionType, params: Dict[str, Any], enabled: bool, description: str)`
- `ActionType`: `MOUSE_CLICK`, `KEY_INPUT`, `FIXED_WAIT`, `IMAGE_WAIT`
- `ScenarioModel(name: str, version: str, loop_count: int, actions: List[ActionBlock])`
- `MouseClickParams`: `x: int`, `y: int`, `button: str` ("left", "right", "middle"), `clicks: int` (default 1), `interval_ms: int` (default 0)
- `KeyInputParams`: `keys: List[str]`, `action: str` ("press", "down", "up")
- `FixedWaitParams`: `duration_ms: int`
- `ImageWaitParams`: `template_path: str`, `mode: str` ("appear", "disappear"), `confidence: float` (0.0 - 1.0, default 0.8), `timeout_seconds: float`, `roi: Optional[Tuple[int, int, int, int]]` (x, y, w, h), `timeout_action: str` ("raise", "skip")

### 2. `action_macro.engine.input_adapter` ↔ `action_macro.runner`
- `InputAdapterProtocol`:
  - `move_to(x: int, y: int) -> None`
  - `click(x: int, y: int, button: str = "left", count: int = 1) -> None`
  - `get_position() -> Tuple[int, int]` (used for ±1px precision assertion)
  - `press_key(key: str) -> None`
  - `release_key(key: str) -> None`
  - `hotkey(*keys: str) -> None`
  - `safety_flush() -> None` (releases all buttons/modifiers)

### 3. `action_macro.engine.wait_system` ↔ `action_macro.runner`
- `WaitSystemProtocol`:
  - `wait(duration_seconds: float, cancel_event: threading.Event) -> bool` (returns False if cancelled)
  - `wait_image(params: ImageWaitParams, cancel_event: threading.Event) -> Tuple[bool, Optional[Tuple[int, int]]]` (returns (found, (x, y)))

### 4. `action_macro.runner.execution_engine` ↔ `action_macro.gui`
- `ExecutionSignals(QObject)`:
  - `state_changed(str)`: Emits new engine state ("IDLE", "RUNNING", "PAUSED", "STOPPING", "STOPPED", "ERROR")
  - `progress_updated(int current_loop, int total_loops, int current_action_idx, int total_actions)`
  - `action_started(int action_idx, str action_desc)`
  - `action_completed(int action_idx, bool success, str message)`
  - `log_emitted(str level, str message)`
  - `execution_finished(bool success, str summary)`

## Code Layout
```
action_macro/
├── pyproject.toml
├── README.md
├── src/
│   └── action_macro/
│       ├── __init__.py
│       ├── __main__.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── models.py
│       │   ├── persistence.py
│       │   └── key_mapping.py
│       ├── engine/
│       │   ├── __init__.py
│       │   ├── input_adapter.py
│       │   ├── screen_adapter.py
│       │   ├── coordinate_transformer.py
│       │   ├── wait_system.py
│       │   └── image_matcher.py
│       ├── runner/
│       │   ├── __init__.py
│       │   ├── execution_engine.py
│       │   ├── state_machine.py
│       │   └── hotkey_listener.py
│       └── gui/
│           ├── __init__.py
│           ├── app.py
│           ├── main_window.py
│           ├── scenario_builder.py
│           ├── property_inspector.py
│           ├── coordinate_picker.py
│           └── log_console.py
└── tests/
    ├── conftest.py
    ├── mocks/
    │   ├── mock_input.py
    │   └── mock_screen.py
    ├── unit/
    │   ├── test_models.py
    │   ├── test_persistence.py
    │   ├── test_input_precision.py
    │   ├── test_wait_system.py
    │   └── test_image_matcher.py
    └── e2e/
        ├── test_tier1_features.py
        ├── test_tier2_boundaries.py
        ├── test_tier3_interactions.py
        └── test_tier4_scenarios.py
```
