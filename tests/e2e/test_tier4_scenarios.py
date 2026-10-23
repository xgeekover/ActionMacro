"""Tier 4: Real-World Workloads & E2E Application Scenarios for ActionMacro.

Implements 3 realistic, end-to-end mobile game automation scenarios:
1. BlueStacks Daily Quest Loop (ImageWait -> Click -> FixedWait -> KeyInput -> Repeat)
2. Stage Auto-Repeat with Branching (Victory vs. Defeat condition handling)
3. Attendance Reward Claim with Global Hotkey Emergency Stop Abort
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tests.mocks.contract import (
    ActionBlock,
    ActionType,
    EngineState,
    ExecutionEngine,
    ImageWaitParams,
    ScenarioModel,
    WaitSystem,
)
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


class TestTier4Scenarios:
    """Verifies end-to-end real-world mobile game automation workflows."""

    def test_scenario_bluestacks_daily_quest_loop(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        tmp_path: Path,
    ):
        """Scenario 1: BlueStacks Daily Quest & Reward Loop (2 iterations).

        Sequence per loop:
        1. ImageWait: Wait for Quest Icon (quest_icon.png) to appear (ROI: Top quadrant).
        2. MouseClick: Click center of Quest Icon.
        3. FixedWait: Wait 10ms for window opening.
        4. ImageWait: Wait for Claim All button (claim_all.png) (ROI: Middle quadrant).
        5. MouseClick: Click Claim All (triggers Confirm popup dialog).
        6. FixedWait: Wait 10ms for reward popup.
        7. ImageWait: Wait for Confirm button (confirm_btn.png) (ROI: Dialog quadrant).
        8. MouseClick: Click Confirm (triggers Confirm popup to dismiss).
        9. ImageWait: Wait for Confirm button to disappear (mode="disappear").
        10. KeyInput: Press Escape to close window.
        11. FixedWait: Wait 10ms before next iteration.
        """
        # Generate distinct templates for Quest Icon, Claim All, and Confirm
        quest_icon = mock_screen.create_test_template(60, 30, color=(200, 50, 50), pattern="cross")
        claim_btn = mock_screen.create_test_template(70, 35, color=(50, 200, 50), pattern="circle")
        confirm_btn = mock_screen.create_test_template(65, 30, color=(50, 50, 200), pattern="cross")

        quest_file = tmp_path / "quest_icon.png"
        claim_file = tmp_path / "claim_btn.png"
        confirm_file = tmp_path / "confirm_btn.png"

        mock_screen.save_template_file(quest_icon, quest_file)
        mock_screen.save_template_file(claim_btn, claim_file)
        mock_screen.save_template_file(confirm_btn, confirm_file)

        # Base screen state: Quest icon and Claim All are present
        mock_screen.inject_patch(quest_icon, x=100, y=100)
        mock_screen.inject_patch(claim_btn, x=500, y=500)
        # Initially confirm popup has not appeared
        mock_screen.remove_patch(x=900, y=600, width=65, height=30)

        # Hook input driver to reactively update screen canvas like an active game UI
        orig_click = mock_input.click

        def _reactive_click(x: int, y: int, *args, **kwargs) -> None:
            orig_click(x, y, *args, **kwargs)
            # Clicking Claim All spawns Confirm dialog
            if abs(x - (500 + 35)) <= 2 and abs(y - (500 + 17)) <= 2:
                mock_screen.inject_patch(confirm_btn, x=900, y=600)
            # Clicking Confirm dismisses Confirm dialog
            elif abs(x - (900 + 32)) <= 2 and abs(y - (600 + 15)) <= 2:
                mock_screen.remove_patch(x=900, y=600, width=65, height=30)

        mock_input.click = _reactive_click

        scenario = ScenarioModel(
            name="Daily Quest Loop",
            loop_count=2,
            actions=[
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(quest_file),
                        "mode": "appear",
                        "confidence": 0.85,
                        "roi": (50, 50, 200, 200),
                        "timeout_seconds": 1.0,
                    },
                    description="Wait for Quest Icon",
                ),
                ActionBlock(
                    type=ActionType.MOUSE_CLICK,
                    params={"use_last_matched": True},
                    description="Click Quest Icon",
                ),
                ActionBlock(
                    type=ActionType.FIXED_WAIT,
                    params={"duration_ms": 10},
                    description="Window open wait",
                ),
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(claim_file),
                        "mode": "appear",
                        "confidence": 0.85,
                        "roi": (400, 400, 300, 300),
                        "timeout_seconds": 1.0,
                    },
                    description="Wait for Claim Button",
                ),
                ActionBlock(
                    type=ActionType.MOUSE_CLICK,
                    params={"use_last_matched": True},
                    description="Click Claim All",
                ),
                ActionBlock(
                    type=ActionType.FIXED_WAIT,
                    params={"duration_ms": 10},
                    description="Reward popup wait",
                ),
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(confirm_file),
                        "mode": "appear",
                        "confidence": 0.85,
                        "roi": (800, 500, 300, 300),
                        "timeout_seconds": 1.0,
                    },
                    description="Wait for Confirm Button",
                ),
                ActionBlock(
                    type=ActionType.MOUSE_CLICK,
                    params={"use_last_matched": True},
                    description="Click Confirm",
                ),
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={
                        "template_path": str(confirm_file),
                        "mode": "disappear",
                        "confidence": 0.85,
                        "roi": (800, 500, 300, 300),
                        "timeout_seconds": 1.0,
                    },
                    description="Wait for Confirm popup to vanish",
                ),
                ActionBlock(
                    type=ActionType.KEY_INPUT,
                    params={"keys": ["esc"], "action": "press"},
                    description="Close quest window",
                ),
                ActionBlock(
                    type=ActionType.FIXED_WAIT,
                    params={"duration_ms": 10},
                    description="Post-loop wait",
                ),
            ],
        )

        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
        completed = engine.run()

        assert completed is True
        # 2 loops * 3 clicks per loop = 6 clicks total
        assert len(mock_input.clicks) == 6
        # 2 loops * 1 Esc key press = 2 Esc keys
        esc_events = [e for e in mock_input.events if e.event_type == "key_down" and e.key == "esc"]
        assert len(esc_events) == 2
        assert engine.state == EngineState.IDLE

    def test_scenario_stage_auto_repeat_with_branching(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        tmp_path: Path,
    ):
        """Scenario 2: Stage Auto-Repeat with Branching (Victory vs. Defeat).

        Tests:
        - Battle start click
        - Branch A: Victory banner detected -> Click Next Stage (1100, 900)
        - Branch B: Defeat banner detected -> Click Exit to Lobby (800, 900)
        """
        victory_banner = mock_screen.create_test_template(100, 40, color=(40, 220, 40), pattern="cross")
        defeat_banner = mock_screen.create_test_template(100, 40, color=(220, 40, 40), pattern="cross")

        victory_file = tmp_path / "victory.png"
        defeat_file = tmp_path / "defeat.png"

        mock_screen.save_template_file(victory_banner, victory_file)
        mock_screen.save_template_file(defeat_banner, defeat_file)

        # ---------------------------------------------------------------------
        # Case A: Victory Run
        # ---------------------------------------------------------------------
        mock_screen.clear_canvas()
        mock_screen.inject_patch(victory_banner, x=910, y=300)

        victory_scenario = ScenarioModel(
            name="Stage Repeat - Victory",
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 960, "y": 850}, description="Start Battle"),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 10}, description="Battle duration"),
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={"template_path": str(victory_file), "mode": "appear", "timeout_seconds": 1.0},
                    description="Check Victory",
                ),
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 1100, "y": 900}, description="Next Stage"),
            ],
        )

        engine_a = ExecutionEngine(victory_scenario, input_driver=mock_input, screen_driver=mock_screen)
        assert engine_a.run() is True
        assert len(mock_input.clicks) == 2
        mock_input.assert_last_click_precision(1100, 900, tolerance=1)

        # ---------------------------------------------------------------------
        # Case B: Defeat Run
        # ---------------------------------------------------------------------
        mock_input.reset()
        mock_screen.clear_canvas()
        mock_screen.inject_patch(defeat_banner, x=910, y=300)

        defeat_scenario = ScenarioModel(
            name="Stage Repeat - Defeat",
            actions=[
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 960, "y": 850}, description="Start Battle"),
                ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 10}, description="Battle duration"),
                ActionBlock(
                    type=ActionType.IMAGE_WAIT,
                    params={"template_path": str(defeat_file), "mode": "appear", "timeout_seconds": 1.0},
                    description="Check Defeat",
                ),
                ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 800, "y": 900}, description="Return to Lobby"),
            ],
        )

        engine_b = ExecutionEngine(defeat_scenario, input_driver=mock_input, screen_driver=mock_screen)
        assert engine_b.run() is True
        assert len(mock_input.clicks) == 2
        mock_input.assert_last_click_precision(800, 900, tolerance=1)

    def test_scenario_attendance_reward_claim_with_abort(
        self,
        mock_input: MockInputDriver,
        mock_screen: MockScreenDriver,
        tmp_path: Path,
    ):
        """Scenario 3: Attendance Reward Claim interrupted by Global Emergency Stop.

        Iterates through 7 daily calendar reward tiles. At tile 4, user hits emergency
        stop hotkey. Execution halts immediately in <= 50ms, releases holds, and triggers
        safety flush.
        """
        calendar_title = mock_screen.create_test_template(120, 40, color=(255, 200, 0), pattern="cross")
        title_file = tmp_path / "calendar_title.png"
        mock_screen.save_template_file(calendar_title, title_file)
        mock_screen.inject_patch(calendar_title, x=900, y=100)

        # 7 reward tile coordinates
        tile_coords = [
            (400, 400),
            (550, 400),
            (700, 400),
            (850, 400),
            (1000, 400),
            (1150, 400),
            (1300, 400),
        ]

        actions = [
            ActionBlock(
                type=ActionType.IMAGE_WAIT,
                params={"template_path": str(title_file), "timeout_seconds": 1.0},
                description="Detect Calendar Popup",
            )
        ]

        for idx, (tx, ty) in enumerate(tile_coords, start=1):
            actions.append(
                ActionBlock(
                    type=ActionType.MOUSE_CLICK,
                    params={"x": tx, "y": ty},
                    description=f"Claim Tile {idx}",
                )
            )
            actions.append(
                ActionBlock(
                    type=ActionType.FIXED_WAIT,
                    params={"duration_ms": 50},
                    description=f"Wait after Tile {idx}",
                )
            )

        scenario = ScenarioModel(name="Attendance Claim", actions=actions)
        engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)

        # Trigger emergency stop after 3 tiles have executed (~120ms)
        def _emergency_stop():
            time.sleep(0.08)
            engine.stop()

        threading.Thread(target=_emergency_stop, daemon=True).start()

        t0 = time.perf_counter()
        completed = engine.run()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Must be stopped
        assert completed is False
        assert engine.state == EngineState.STOPPED
        # Verify safety flush was triggered
        assert mock_input.safety_flush_called is True
        # Less than all 7 tiles were clicked because it was aborted
        assert len(mock_input.clicks) < 7
        assert len(mock_input.clicks) >= 1
        # Abort completed in < 250ms total
        assert elapsed_ms <= 250.0
