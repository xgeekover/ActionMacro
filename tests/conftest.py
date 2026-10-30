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

# Isolate QSettings: the GUI remembers recent scenario files in user-scope INI
# settings (organisation "ActionMacro"). Redirect that scope to a throw-away
# directory before any QSettings object exists so tests never read or write the
# developer's real settings.
import atexit as _atexit
import shutil as _shutil
import tempfile as _tempfile

from PySide6.QtCore import QSettings as _QSettings

_QSETTINGS_DIR = _tempfile.mkdtemp(prefix="actionmacro-test-settings-")
for _scope in (_QSettings.Scope.UserScope, _QSettings.Scope.SystemScope):
    _QSettings.setPath(_QSettings.Format.IniFormat, _scope, _QSETTINGS_DIR)
_atexit.register(_shutil.rmtree, _QSETTINGS_DIR, True)

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


#: Real permission bindings a test tried to call (they never reach the host; see below).
_BLOCKED_PERMISSION_QUERIES: list = []


@pytest.fixture(scope="session", autouse=True)
def _macos_permissions_guard():
    """Session-wide: no test ever queries this machine's real macOS permission state.

    * ``default_checker`` reports everything granted from the first fixture on, so
      a module- or session-scoped fixture that builds a MainWindow before the
      function-scoped fake below runs still never asks the host.
    * The three low-level bindings (AXIsProcessTrusted, CGPreflightScreenCaptureAccess,
      IOHIDCheckAccess) are replaced by guards that record the call and raise, so the
      check reports UNKNOWN instead of reading the host. A test that injects its own
      bindings (monkeypatch or the ``is_trusted=`` / ``preflight=`` arguments) is not
      affected; any recorded call fails the test that made it (see the fixture below).
    """
    from action_macro.engine import macos_permissions

    def _guard(name: str):
        def _blocked(*_args, **_kwargs):
            _BLOCKED_PERMISSION_QUERIES.append(name)
            raise RuntimeError(f"tests must not query the real macOS permission binding {name}")

        _blocked._test_guard = True  # type: ignore[attr-defined]
        _blocked.__name__ = f"_blocked_{name.lstrip('_')}"
        return _blocked

    patcher = pytest.MonkeyPatch()
    patcher.setattr(macos_permissions, "default_checker", macos_permissions.all_granted)
    for name in ("_ax_is_process_trusted", "_cg_preflight_screen_capture_access", "_iohid_check_access"):
        patcher.setattr(macos_permissions, name, _guard(name))
    yield
    patcher.undo()


@pytest.fixture(autouse=True)
def _no_real_permission_queries():
    """Fails the test that reached a real permission binding (recorded by the guard above)."""
    _BLOCKED_PERMISSION_QUERIES.clear()
    yield
    calls = list(_BLOCKED_PERMISSION_QUERIES)
    _BLOCKED_PERMISSION_QUERIES.clear()
    assert not calls, f"real macOS permission bindings were called: {calls}"


#: Real input-source switches a test tried to make (see the guard below).
_BLOCKED_INPUT_SOURCE_CALLS: list = []


@pytest.fixture(scope="session", autouse=True)
def _macos_input_source_guard():
    """Session-wide: no test switches this Mac's real keyboard input source.

    A text step selects an ASCII input source while it types (macos_input_source). The
    two calls that change the selection are replaced by guards that record the call and
    raise; ascii_input_source() then types without switching, and the fixture below fails
    the test. Tests of the module patch these functions themselves.
    """
    from action_macro.engine import macos_input_source

    def _guard(name: str):
        def _blocked(*_args, **_kwargs):
            _BLOCKED_INPUT_SOURCE_CALLS.append(name)
            raise RuntimeError(f"tests must not switch the real input source ({name})")

        return _blocked

    patcher = pytest.MonkeyPatch()
    for name in ("_select_ascii_capable", "_restore"):
        patcher.setattr(macos_input_source, name, _guard(name))
    yield
    patcher.undo()


@pytest.fixture(autouse=True)
def _no_real_input_source_switch():
    """Fails the test that tried to switch the real input source (recorded by the guard above)."""
    _BLOCKED_INPUT_SOURCE_CALLS.clear()
    yield
    calls = list(_BLOCKED_INPUT_SOURCE_CALLS)
    _BLOCKED_INPUT_SOURCE_CALLS.clear()
    assert not calls, f"the real keyboard input source was switched: {calls}"


@pytest.fixture(autouse=True)
def _macos_permissions_all_granted(monkeypatch):
    """Makes the default macOS permission checker report every permission as granted.

    MainWindow checks Accessibility / Input Monitoring / Screen Recording at startup
    and when the app becomes active; with the real (passive) checker the top-bar
    warning would depend on the machine running the tests. Tests that need other
    statuses inject ``MainWindow(permission_checker=...)`` (see test_gui_p2_perms.py).
    """
    from action_macro.engine import macos_permissions

    monkeypatch.setattr(macos_permissions, "default_checker", macos_permissions.all_granted)


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


@pytest.fixture
def isolated_settings(tmp_path: Path) -> _QSettings:
    """Provides a QSettings object backed by a private INI file under tmp_path."""
    settings = _QSettings(str(tmp_path / "ActionMacro-test.ini"), _QSettings.Format.IniFormat)
    yield settings
    settings.clear()
    settings.sync()
