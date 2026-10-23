"""Unit and integration tests for MainWindow, app.py, and __main__.py."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from action_macro.__main__ import main as cli_main
from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.core.persistence import ScenarioPersistence
from action_macro.gui.app import create_app
from action_macro.gui.coordinate_picker import CoordinatePickerOverlay
from action_macro.gui.main_window import MainWindow
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


def test_main_window_initialization(qtbot):
    scenario = ScenarioModel(name="Test Window Scenario", loop_count=2)
    win = MainWindow(scenario=scenario)
    qtbot.addWidget(win)

    assert win.scenario is scenario
    assert win.dashboard.get_effective_loop_count() == 2
    assert win.scenario_builder is not None
    assert win.property_inspector is not None
    assert win.log_console is not None

    # Check menus
    menubar = win.menuBar()
    actions = [a.text() for a in menubar.actions()]
    assert any("File" in a for a in actions)
    assert any("Run" in a for a in actions)
    assert any("Tools" in a for a in actions)

    # Check status bar
    assert win.status_bar.currentMessage() == "Ready"


def test_main_window_dirty_tracking_and_title(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    assert "*" not in win.windowTitle()
    assert win._is_dirty is False

    win.mark_dirty(True)
    assert "*" in win.windowTitle()
    assert win._is_dirty is True

    win.mark_dirty(False)
    assert "*" not in win.windowTitle()
    assert win._is_dirty is False


def test_main_window_new_scenario(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    win.scenario.name = "Old Scenario"
    win.mark_dirty(True)

    with patch.object(win, "confirm_discard_changes", return_value=True):
        win.new_scenario()

    assert win.scenario.name == "Untitled Scenario"
    assert win._is_dirty is False
    assert win.current_file_path is None


def test_main_window_save_and_open_scenario(tmp_path: Path, qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    save_path = tmp_path / "test_scenario.json"
    win.scenario.name = "Persistent Scenario"
    win.scenario.add_action(
        ActionBlock(
            id="act-1",
            type=ActionType.FIXED_WAIT,
            params=FixedWaitParams(duration_ms=250),
        )
    )
    win.current_file_path = save_path
    win.mark_dirty(True)

    # Save
    success = win.save_scenario()
    assert success is True
    assert save_path.exists()
    assert win._is_dirty is False

    # Open in fresh window
    win2 = MainWindow()
    qtbot.addWidget(win2)

    with patch.object(win2, "confirm_discard_changes", return_value=True):
        loaded_ok = win2.open_scenario(str(save_path))

    assert loaded_ok is True
    assert win2.scenario.name == "Persistent Scenario"
    assert len(win2.scenario.actions) == 1
    assert win2.current_file_path == save_path
    assert win2._is_dirty is False


def test_main_window_coordinate_picker_integration(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)

    act = ActionBlock(
        id="act-mouse",
        type=ActionType.MOUSE_CLICK,
        params=MouseClickParams(x=10, y=20),
    )
    win.scenario.add_action(act)
    win.scenario_builder.populate_from_scenario(win.scenario)
    win.property_inspector.set_action(act)

    class DummyPicker(CoordinatePickerOverlay):
        def start(self):
            return self

    dummy_picker = DummyPicker(parent=None)
    win.coordinate_picker_factory = lambda parent: dummy_picker

    win.trigger_coordinate_picker()

    # Emit coordinate from picker
    dummy_picker.coordinate_selected.emit(850, 420)

    assert act.params.x == 850
    assert act.params.y == 420
    assert win._is_dirty is True


def test_main_window_macro_execution(qtbot):
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    scenario = ScenarioModel(
        name="Exec Scenario",
        loop_count=1,
        actions=[
            ActionBlock(
                id="act-w",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=10),
            )
        ],
    )

    win = MainWindow(
        scenario=scenario,
        input_driver=mock_input,
        screen_driver=mock_screen,
    )
    qtbot.addWidget(win)

    # Start macro
    win.start_macro(loop_count=1)
    assert win.engine is not None

    # Wait for completion
    finished = win.engine.wait_for_completion(timeout=5.0)
    assert finished is True
    assert len(win.engine.signals.finished) > 0


def test_main_window_pause_resume_stop(qtbot):
    mock_input = MockInputDriver()
    mock_screen = MockScreenDriver()

    scenario = ScenarioModel(
        name="Control Scenario",
        loop_count=1,
        actions=[
            ActionBlock(
                id="act-w1",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=1000),
            )
        ],
    )

    win = MainWindow(
        scenario=scenario,
        input_driver=mock_input,
        screen_driver=mock_screen,
    )
    qtbot.addWidget(win)

    win.start_macro()
    assert win.engine is not None

    win.pause_macro()
    assert win.engine.is_paused()

    win.resume_macro()
    assert not win.engine.is_paused()

    win.stop_macro()
    win.engine.wait_for_completion(timeout=3.0)
    assert win.engine.cancel_event.is_set()


def test_app_create_app(qtbot):
    app = create_app()
    assert isinstance(app, QApplication)
    assert app.applicationName() == "ActionMacro"


def test_cli_headless_execution(tmp_path: Path):
    scenario = ScenarioModel(
        name="CLI Headless Scenario",
        loop_count=1,
        actions=[
            ActionBlock(
                id="act-cli",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=10),
            )
        ],
    )
    file_path = tmp_path / "cli_scenario.json"
    ScenarioPersistence.save(scenario, file_path)

    exit_code = cli_main(["--headless", str(file_path), "--loops", "1"])
    assert exit_code == 0
