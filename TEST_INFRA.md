# E2E Test Infra: action_macro

## Test Philosophy
- Opaque-box, requirement-driven: Derived directly from `ORIGINAL_REQUEST.md`. No dependence on private internal implementation details.
- Methodology: Category-Partition (Tier 1) + Boundary Value Analysis (Tier 2) + Pairwise Interaction (Tier 3) + Real-World Workload Testing (Tier 4).
- Headless Determinism: Automated test harness utilizes `MockInputDriver` (recording clicks/keys and asserting coordinates) and `MockScreenDriver` (synthetic frame injection and step clock) to run seamlessly in CI and headless environments without requiring physical display hardware.

## Feature Inventory & Test Matrix
| # | Feature | Source | Tier 1 (Coverage) | Tier 2 (Boundary) | Tier 3 (Cross-Feature) | Tier 4 (Scenario) |
|---|---------|--------|:-----------------:|:-----------------:|:----------------------:|:-----------------:|
| F1 | Mouse Click Action Block | ORIGINAL_REQUEST §R1, R2 | 5 test cases | 3 test cases | ✓ | ✓ |
| F2 | Keyboard Input Action Block | ORIGINAL_REQUEST §R1, R2 | 5 test cases | 2 test cases | ✓ | ✓ |
| F3 | Fixed Time Wait | ORIGINAL_REQUEST §R1, R3 | 5 test cases | 2 test cases | ✓ | ✓ |
| F4 | Image Recognition Wait | ORIGINAL_REQUEST §R1, R3 | 5 test cases | 3 test cases | ✓ | ✓ |
| F5 | Scenario File Persistence | ORIGINAL_REQUEST §R4 | 5 test cases | 3 test cases | ✓ | ✓ |
| F6 | Repeat Execution Loop | ORIGINAL_REQUEST §R4 | 5 test cases | 2 test cases | ✓ | ✓ |
| F7 | Emergency Stop Hotkey | ORIGINAL_REQUEST §R4 | 5 test cases | 2 test cases | ✓ | ✓ |
| F8 | Coordinate Picker / Math | ORIGINAL_REQUEST §R2 | 5 test cases | 2 test cases | ✓ | ✓ |

## Test Architecture
- Test runner: `pytest`
- Execution command: `uv run --with pytest --with pytest-qt --with opencv-python-headless --with pillow --with pynput pytest tests/e2e/ -v`
- Pass/Fail Semantics: 100% tests pass with exit code 0.
- Directory Layout:
  - `tests/conftest.py` — shared fixtures and driver configuration
  - `tests/mocks/` — `MockInputDriver`, `MockScreenDriver`
  - `tests/e2e/test_tier1_features.py` — Tier 1 Feature Coverage (40 tests)
  - `tests/e2e/test_tier2_boundaries.py` — Tier 2 Boundary & Corner Cases (10 tests)
  - `tests/e2e/test_tier3_interactions.py` — Tier 3 Cross-Feature Interactions (8 tests)
  - `tests/e2e/test_tier4_scenarios.py` — Tier 4 Real-World Application Scenarios (3 end-to-end games)

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | BlueStacks Daily Quest Loop | ImageWait (quest icon) -> Click (claim) -> FixedWait -> KeyInput (Esc) -> Repeat | High |
| 2 | Stage Auto-Repeat with Branching | Click (Start) -> ImageWait (Victory or Defeat template) -> Click (Replay/Lobby) | High |
| 3 | Attendance Reward Claim with Abort | Loop through daily reward grid -> Global Hotkey emergency stop during wait | High |

## Coverage Thresholds
- Tier 1: ≥5 per feature (Total ≥ 40 tests)
- Tier 2: Boundary conditions (Total ≥ 10 tests)
- Tier 3: Cross-feature combinations (Total ≥ 8 tests)
- Tier 4: Realistic application workflows (Total ≥ 3 scenarios)
- Mouse precision automated assertion: $|click.x - target.x| \le 1$ and $|click.y - target.y| \le 1$
- Emergency stop abort latency assertion: $t_{abort} \le 1.0\text{s}$ (target $\le 0.05\text{s}$)
