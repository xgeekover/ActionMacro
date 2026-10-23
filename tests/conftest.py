"""Shared pytest fixtures and test configuration for ActionMacro automation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Generator

import pytest

# Ensure project root and src/ are on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Ensure headless Qt operation in test runs
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.mocks.contract import (
    ActionBlock,
    ActionType,
    CoordinateTransformer,
    EngineState,
    ExecutionEngine,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
    ScenarioPersistence,
    WaitSystem,
)
from tests.mocks.mock_input import InputEvent, MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


@pytest.fixture
def mock_input() -> MockInputDriver:
    """Provides a clean, isolated MockInputDriver instance."""
    driver = MockInputDriver()
    yield driver
    driver.reset()


@pytest.fixture
def mock_screen() -> MockScreenDriver:
    """Provides a fresh 1920x1080 MockScreenDriver instance."""
    return MockScreenDriver(width=1920, height=1080)


@pytest.fixture
def sample_template(mock_screen: MockScreenDriver):
    """Provides a synthetic 60x30 test template image."""
    return mock_screen.create_test_template(width=60, height=30, pattern="cross")


@pytest.fixture
def sample_template_file(tmp_path: Path, mock_screen: MockScreenDriver, sample_template) -> Path:
    """Writes a sample template PNG file to temporary directory and returns its path."""
    dest = tmp_path / "templates" / "test_btn.png"
    return mock_screen.save_template_file(sample_template, dest)


@pytest.fixture
def sample_scenario(sample_template_file: Path) -> ScenarioModel:
    """Provides a well-formed 4-action scenario model for testing."""
    return ScenarioModel(
        name="Test Scenario",
        version="1.0.0",
        loop_count=1,
        actions=[
            ActionBlock(
                id="act-1",
                type=ActionType.MOUSE_CLICK,
                params={"x": 500, "y": 300, "button": "left", "clicks": 1},
                description="Click at 500, 300",
            ),
            ActionBlock(
                id="act-2",
                type=ActionType.KEY_INPUT,
                params={"keys": ["enter"], "action": "press"},
                description="Press Enter",
            ),
            ActionBlock(
                id="act-3",
                type=ActionType.FIXED_WAIT,
                params={"duration_ms": 50},
                description="Wait 50ms",
            ),
            ActionBlock(
                id="act-4",
                type=ActionType.IMAGE_WAIT,
                params={
                    "template_path": str(sample_template_file),
                    "mode": "appear",
                    "confidence": 0.8,
                    "timeout_seconds": 1.0,
                },
                description="Wait for button image",
            ),
        ],
    )
