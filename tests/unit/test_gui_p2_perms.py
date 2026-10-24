"""Tests for the macOS permission checks, the Permissions panel and the MainWindow wiring.

Every test uses fake checkers and a fake URL opener: no real permission status is
read, System Settings is never opened and no global hotkey listener is created.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Dict, List

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.engine import macos_permissions as mp
from action_macro.engine.macos_permissions import Permission as P
from action_macro.engine.macos_permissions import PermissionStatus as S
from action_macro.gui import main_window as main_window_module
from action_macro.gui.main_window import MainWindow
from action_macro.gui.permissions_panel import (
    NOT_REQUIRED_TEXT,
    UNKNOWN_LAUNCHER_TEXT,
    PermissionMonitor,
    PermissionsPanel,
)
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver

ORDER = [P.ACCESSIBILITY, P.INPUT_MONITORING, P.SCREEN_RECORDING]

EXPECTED_URLS = {
    P.ACCESSIBILITY: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    P.INPUT_MONITORING: "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    P.SCREEN_RECORDING: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
}


class FakeChecker:
    """A mutable fake checker that counts its calls."""

    def __init__(self, statuses: Dict[P, S] = None) -> None:
        self.statuses: Dict[P, S] = dict(statuses or mp.all_granted())
        self.calls = 0

    def set(self, **statuses: S) -> None:
        for name, status in statuses.items():
            self.statuses[P(name)] = status

    def __call__(self) -> Dict[P, S]:
        self.calls += 1
        return dict(self.statuses)


class FakeOpener:
    def __init__(self, result: bool = True) -> None:
        self.urls: List[str] = []
        self.result = result

    def __call__(self, url: QUrl) -> bool:
        self.urls.append(url.toString())
        return self.result


class FakeHotkeyListener:
    """Stands in for GlobalHotkeyListener so no pynput listener is ever created."""

    instances: List["FakeHotkeyListener"] = []

    def __init__(self, engine=None, **kwargs) -> None:
        self.engine = engine
        self.started = False
        FakeHotkeyListener.instances.append(self)

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.started = False


@pytest.fixture
def window(qtbot, isolated_settings, monkeypatch):
    """MainWindow factory with private QSettings and no blocking prompts."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    windows = []

    def _make(**kwargs) -> MainWindow:
        win = MainWindow(settings=isolated_settings, **kwargs)
        qtbot.addWidget(win)
        win.permissions_url_opener = FakeOpener()
        windows.append(win)
        return win

    yield _make
    for win in windows:
        win.mark_dirty(False)
        if win.permissions_panel is not None:
            win.permissions_panel.close()


def _panel(qtbot, **kwargs) -> PermissionsPanel:
    kwargs.setdefault("opener", FakeOpener())
    kwargs.setdefault("launcher_name", "Terminal")
    panel = PermissionsPanel(**kwargs)
    qtbot.addWidget(panel)
    return panel


def _scenario(*types: ActionType, disabled: tuple = ()) -> ScenarioModel:
    params = {
        ActionType.MOUSE_CLICK: lambda: MouseClickParams(x=10, y=20),
        ActionType.KEY_INPUT: lambda: KeyInputParams(keys=["enter"]),
        ActionType.FIXED_WAIT: lambda: FixedWaitParams(duration_ms=5),
        ActionType.IMAGE_WAIT: lambda: ImageWaitParams(template_path="missing.png", timeout_seconds=0.1),
    }
    actions = [
        ActionBlock(id=f"a{i}", type=t, params=params[t](), enabled=i not in disabled)
        for i, t in enumerate(types)
    ]
    return ScenarioModel(name="Perms", loop_count=1, actions=actions)


ALL_COMBOS = list(itertools.product([S.GRANTED, S.MISSING, S.UNKNOWN], repeat=3))


# =============================================================================
# engine.macos_permissions - passive checks (fake bindings only)
# =============================================================================

@pytest.mark.parametrize("value, expected", [(True, S.GRANTED), (False, S.MISSING), (1, S.GRANTED), (0, S.MISSING)])
def test_accessibility_maps_trusted_flag(value, expected):
    assert mp.check_accessibility(lambda: value) is expected


@pytest.mark.parametrize("value, expected", [(True, S.GRANTED), (False, S.MISSING)])
def test_screen_recording_maps_preflight(value, expected):
    assert mp.check_screen_recording(lambda: value) is expected


@pytest.mark.parametrize("access, expected", [(0, S.GRANTED), (1, S.MISSING), (2, S.UNKNOWN), (7, S.UNKNOWN)])
def test_input_monitoring_maps_iohid_access(access, expected):
    requested = []

    def fake_check_access(request_type: int) -> int:
        requested.append(request_type)
        return access

    assert mp.check_input_monitoring(fake_check_access) is expected
    assert requested == [1]  # kIOHIDRequestTypeListenEvent


def test_each_check_returns_unknown_when_the_binding_fails():
    def boom(*_args):
        raise OSError("binding not available")

    assert mp.check_accessibility(boom) is S.UNKNOWN
    assert mp.check_screen_recording(boom) is S.UNKNOWN
    assert mp.check_input_monitoring(boom) is S.UNKNOWN


def test_check_permissions_on_macos_uses_each_passive_binding(monkeypatch):
    monkeypatch.setattr(mp, "_ax_is_process_trusted", lambda: True)
    monkeypatch.setattr(mp, "_cg_preflight_screen_capture_access", lambda: False)
    monkeypatch.setattr(mp, "_iohid_check_access", lambda request_type: 2)
    report = mp.check_permissions(platform="darwin")
    assert list(report) == ORDER
    assert report == {P.ACCESSIBILITY: S.GRANTED, P.INPUT_MONITORING: S.UNKNOWN, P.SCREEN_RECORDING: S.MISSING}


def test_check_permissions_isolates_failures(monkeypatch):
    def boom(*_args):
        raise RuntimeError("no pyobjc")

    monkeypatch.setattr(mp, "_ax_is_process_trusted", boom)
    monkeypatch.setattr(mp, "_cg_preflight_screen_capture_access", lambda: True)
    monkeypatch.setattr(mp, "_iohid_check_access", boom)
    report = mp.check_permissions(platform="darwin")
    assert report == {P.ACCESSIBILITY: S.UNKNOWN, P.INPUT_MONITORING: S.UNKNOWN, P.SCREEN_RECORDING: S.GRANTED}


@pytest.mark.parametrize("platform", ["linux", "win32", "cygwin"])
def test_check_permissions_off_macos_is_not_required_and_queries_nothing(monkeypatch, platform):
    def must_not_run(*_args):
        raise AssertionError("a macOS binding was queried off macOS")

    for name in ("_ax_is_process_trusted", "_cg_preflight_screen_capture_access", "_iohid_check_access"):
        monkeypatch.setattr(mp, name, must_not_run)
    report = mp.check_permissions(platform=platform)
    assert report == {p: S.NOT_REQUIRED for p in ORDER}
    assert mp.is_not_required(report)
    assert mp.missing_permissions(report) == []


def test_checks_never_use_the_prompting_apis():
    source = Path(mp.__file__).read_text(encoding="utf-8")
    for forbidden in ("AXIsProcessTrustedWithOptions", "kAXTrustedCheckOptionPrompt",
                      "CGRequestScreenCaptureAccess", "IOHIDRequestAccess"):
        assert forbidden not in source, forbidden
    for passive in ("AXIsProcessTrusted", "CGPreflightScreenCaptureAccess", "IOHIDCheckAccess"):
        assert passive in source


def test_settings_urls_open_the_matching_privacy_panes():
    for permission, url in EXPECTED_URLS.items():
        assert mp.settings_url(permission) == url


def test_normalize_report_fills_gaps_and_orders():
    report = mp.normalize_report({P.SCREEN_RECORDING: "missing", "accessibility": S.GRANTED,
                                  P.INPUT_MONITORING: "bogus"})
    assert list(report) == ORDER
    assert report == {P.ACCESSIBILITY: S.GRANTED, P.INPUT_MONITORING: S.UNKNOWN, P.SCREEN_RECORDING: S.MISSING}
    assert mp.normalize_report(None) == {p: S.UNKNOWN for p in ORDER}


@pytest.mark.parametrize("combo", ALL_COMBOS)
def test_missing_permissions_for_every_combination(combo):
    report = dict(zip(ORDER, combo))
    assert mp.missing_permissions(report) == [p for p, s in report.items() if s is S.MISSING]


def test_join_titles():
    assert mp.join_titles([]) == ""
    assert mp.join_titles([P.ACCESSIBILITY]) == "Accessibility"
    assert mp.join_titles([P.ACCESSIBILITY, P.SCREEN_RECORDING]) == "Accessibility and Screen Recording"
    assert mp.join_titles(ORDER) == "Accessibility, Input Monitoring and Screen Recording"


def test_required_permissions_by_step_type():
    assert mp.required_permissions([ActionType.MOUSE_CLICK]) == [P.ACCESSIBILITY, P.INPUT_MONITORING]
    assert mp.required_permissions(["KEY_INPUT"]) == [P.ACCESSIBILITY, P.INPUT_MONITORING]
    assert mp.required_permissions([ActionType.IMAGE_WAIT]) == [P.INPUT_MONITORING, P.SCREEN_RECORDING]
    assert mp.required_permissions([ActionType.FIXED_WAIT]) == [P.INPUT_MONITORING]
    assert mp.required_permissions([ActionType.FIXED_WAIT], stop_listener=False) == []
    assert mp.required_permissions(["nonsense", None]) == [P.INPUT_MONITORING]


def test_required_for_scenario_ignores_disabled_steps():
    scenario = _scenario(ActionType.MOUSE_CLICK, ActionType.IMAGE_WAIT, disabled=(0,))
    assert mp.required_for_scenario(scenario) == [P.INPUT_MONITORING, P.SCREEN_RECORDING]


@pytest.mark.parametrize("env, expected", [
    ({"__CFBundleIdentifier": "com.apple.Terminal"}, "Terminal"),
    ({"__CFBundleIdentifier": "com.googlecode.iterm2", "TERM_PROGRAM": "iTerm.app"}, "iTerm"),
    ({"__CFBundleIdentifier": "dev.warp.Warp-Stable"}, "Warp"),
    ({"__CFBundleIdentifier": "com.example.unknown", "TERM_PROGRAM": "vscode"}, "Visual Studio Code"),
    ({"TERM_PROGRAM": "Apple_Terminal"}, "Terminal"),
    ({"TERM_PROGRAM": "tmux"}, None),
    ({}, None),
])
def test_launcher_app_name_from_environment(env, expected):
    assert mp.launcher_app_name(env, frozen=False) == expected


def test_launcher_app_name_of_a_packaged_app():
    env = {"__CFBundleIdentifier": "com.apple.Terminal"}
    exe = "/Applications/ActionMacro.app/Contents/MacOS/ActionMacro"
    assert mp.launcher_app_name(env, executable=exe, frozen=True) == "ActionMacro"
    # Not frozen (a venv python): the launching terminal answers.
    assert mp.launcher_app_name(env, executable=exe, frozen=False) == "Terminal"


def test_conftest_fakes_the_default_checker():
    assert mp.default_checker is not mp.check_permissions
    assert mp.default_checker() == mp.all_granted()


# =============================================================================
# PermissionMonitor
# =============================================================================

def test_monitor_throttles_activation_rechecks(qapp):
    checker = FakeChecker()
    now = [100.0]
    monitor = PermissionMonitor(checker, min_interval=3.0, clock=lambda: now[0])
    seen = []
    monitor.report_changed.connect(seen.append)

    assert monitor.check_if_stale() is True       # never checked: runs
    assert checker.calls == 1
    now[0] = 102.9
    monitor._on_application_state_changed(Qt.ApplicationState.ApplicationActive)
    assert checker.calls == 1                      # too soon
    now[0] = 103.0
    monitor._on_application_state_changed(Qt.ApplicationState.ApplicationInactive)
    assert checker.calls == 1                      # only "active" counts
    monitor._on_application_state_changed(Qt.ApplicationState.ApplicationActive)
    assert checker.calls == 2
    monitor.check()                                # explicit checks are never throttled
    assert checker.calls == 3
    assert len(seen) == 3 and seen[-1] == mp.all_granted()


def test_monitor_survives_a_failing_checker(qapp):
    def broken():
        raise RuntimeError("boom")

    monitor = PermissionMonitor(broken)
    assert monitor.check() == {p: S.UNKNOWN for p in ORDER}
    assert monitor.missing() == []


def test_monitor_without_checker_uses_module_default(qapp, monkeypatch):
    fake = FakeChecker({P.ACCESSIBILITY: S.MISSING, P.INPUT_MONITORING: S.GRANTED, P.SCREEN_RECORDING: S.GRANTED})
    monkeypatch.setattr(mp, "default_checker", fake)
    monitor = PermissionMonitor()
    assert monitor.check()[P.ACCESSIBILITY] is S.MISSING
    assert monitor.missing() == [P.ACCESSIBILITY]
    assert fake.calls == 1


# =============================================================================
# PermissionsPanel
# =============================================================================

def test_panel_layout_and_texts(qtbot):
    panel = _panel(qtbot, report={P.ACCESSIBILITY: S.GRANTED, P.INPUT_MONITORING: S.MISSING,
                                  P.SCREEN_RECORDING: S.UNKNOWN})
    assert panel.windowTitle() == "Permissions"
    assert panel.width() == 520
    assert list(panel.rows) == ORDER
    assert panel.intro_text() == "macOS asks you to allow these for the app that started ActionMacro: Terminal."
    assert "Screen Recording" in panel.lbl_note.text() and "Input Monitoring" in panel.lbl_note.text()
    assert "quit and reopen Terminal" in panel.lbl_note.text()
    for permission, row in panel.rows.items():
        assert row.lbl_name.text() == mp.info(permission).title
        assert row.lbl_purpose.text() == mp.info(permission).purpose
        assert row.btn_open.text() == "Open settings"  # sentence case, like "Check again"
        assert not row.lbl_icon.pixmap().isNull()
    assert panel.btn_check.text() == "Check again"
    assert panel.btn_close.text() == "Close"


@pytest.mark.parametrize("combo", ALL_COMBOS)
def test_panel_pills_for_every_combination(qtbot, combo):
    report = dict(zip(ORDER, combo))
    panel = _panel(qtbot, report=report)
    labels = {S.GRANTED: "Granted", S.MISSING: "Missing", S.UNKNOWN: "Unknown"}
    for permission, status in report.items():
        row = panel.rows[permission]
        assert row.status() is status
        assert row.lbl_pill.text() == labels[status]
        assert row.lbl_pill.property("state") == status.value
        # A granted row keeps a quiet button; anything else gets a bordered one.
        assert row.btn_open.property("variant") == ("ghost" if status is S.GRANTED else "secondary")
    assert panel.list_frame.isHidden() is False
    missing = [p for p, s in report.items() if s is S.MISSING]
    if missing:
        assert panel.summary().startswith("Missing")
    elif all(s is S.GRANTED for s in combo):
        assert panel.summary() == "All permissions are granted."
    else:
        assert panel.summary() == "Some statuses could not be read."


def test_unknown_pill_explains_itself(qtbot):
    panel = _panel(qtbot, report={P.INPUT_MONITORING: S.UNKNOWN})
    assert panel.rows[P.INPUT_MONITORING].lbl_pill.toolTip()
    assert panel.rows[P.ACCESSIBILITY].status() is S.UNKNOWN  # missing entry -> unknown


def test_open_settings_buttons_open_the_matching_pane(qtbot):
    opener = FakeOpener()
    panel = _panel(qtbot, report=mp.all_granted(), opener=opener)
    for permission in ORDER:
        panel.rows[permission].btn_open.click()
    assert opener.urls == [EXPECTED_URLS[p] for p in ORDER]


def test_open_settings_reports_failures_without_raising(qtbot):
    panel = _panel(qtbot, report=mp.all_granted(), opener=FakeOpener(result=False))
    assert panel.open_settings(P.ACCESSIBILITY) is False

    def broken(_url):
        raise RuntimeError("no desktop services")

    panel.set_opener(broken)
    assert panel.open_settings(P.SCREEN_RECORDING) is False


def test_check_again_reruns_the_checker_and_updates_rows(qtbot):
    checker = FakeChecker({P.ACCESSIBILITY: S.MISSING, P.INPUT_MONITORING: S.GRANTED,
                           P.SCREEN_RECORDING: S.GRANTED})
    panel = _panel(qtbot, checker=checker)
    assert checker.calls == 1  # no report given: checks once on open
    assert panel.rows[P.ACCESSIBILITY].status() is S.MISSING
    checker.set(accessibility=S.GRANTED)
    with qtbot.waitSignal(panel.report_changed) as blocker:
        panel.btn_check.click()
    assert checker.calls == 2
    assert blocker.args[0][P.ACCESSIBILITY] is S.GRANTED
    assert panel.rows[P.ACCESSIBILITY].status() is S.GRANTED
    assert panel.lbl_checked.text().startswith("Checked at")


def test_check_again_without_checker_is_disabled(qtbot):
    panel = _panel(qtbot, report=mp.all_granted())
    assert not panel.btn_check.isEnabled()


def test_unknown_launcher_uses_generic_wording(qtbot):
    panel = _panel(qtbot, report=mp.all_granted(), launcher_name="")
    assert panel.intro_text().endswith(f"ActionMacro: {UNKNOWN_LAUNCHER_TEXT}.")
    assert "quit and reopen the app that started ActionMacro" in panel.lbl_note.text()


def test_launcher_is_detected_when_not_given(qtbot, monkeypatch):
    monkeypatch.setattr(mp, "launcher_app_name", lambda *a, **k: "iTerm")
    panel = PermissionsPanel(report=mp.all_granted(), opener=FakeOpener())
    qtbot.addWidget(panel)
    assert panel.intro_text().endswith("ActionMacro: iTerm.")


def test_launcher_name_is_escaped_in_rich_text(qtbot):
    panel = _panel(qtbot, report=mp.all_granted(), launcher_name="<b>Evil</b>")
    assert "&lt;b&gt;Evil&lt;/b&gt;" in panel.lbl_intro.text()
    assert panel.intro_text().endswith("ActionMacro: <b>Evil</b>.")


def test_panel_off_macos_says_nothing_is_needed(qtbot):
    panel = _panel(qtbot, checker=lambda: mp.check_permissions(platform="linux"))
    panel.show()
    assert panel.intro_text() == NOT_REQUIRED_TEXT == "No extra permissions are needed on this system."
    assert panel.list_frame.isHidden()
    assert panel.note.isHidden()
    assert panel.btn_check.isHidden()
    assert panel.btn_close.isVisible()
    assert panel.btn_close.width() < 200  # stays a button, not a full-width bar


def test_panel_does_not_open_with_a_focus_ring_on_a_button(qtbot):
    panel = _panel(qtbot, report=mp.all_granted())
    panel.show()
    panel.activateWindow()
    qtbot.waitUntil(panel.isActiveWindow)
    qtbot.wait(20)
    assert panel.focusWidget() is panel  # not "Close" (the default button) or "Check again"
    assert panel.btn_close.isDefault()


def test_panel_close_button_and_escape_close_it(qtbot):
    panel = _panel(qtbot, report=mp.all_granted())
    panel.show()
    panel.btn_close.click()
    assert not panel.isVisible()
    panel.show()
    QTest.keyClick(panel, Qt.Key.Key_Escape)  # synthetic Qt events, nothing reaches the OS
    assert not panel.isVisible()
    panel.show()
    panel.setFocus()
    QTest.keyClick(panel, Qt.Key.Key_Return)  # Enter clicks the default button (Close)
    assert not panel.isVisible()


# =============================================================================
# MainWindow wiring
# =============================================================================

def test_default_window_hides_the_warning(window):
    win = window()  # conftest: the default checker reports everything granted
    assert win.top_bar.btn_permissions.isHidden()
    assert win.permission_monitor.report() == mp.all_granted()


def test_window_without_injected_checker_uses_the_module_default(window, monkeypatch):
    fake = FakeChecker({P.ACCESSIBILITY: S.GRANTED, P.INPUT_MONITORING: S.GRANTED,
                        P.SCREEN_RECORDING: S.MISSING})
    monkeypatch.setattr(mp, "default_checker", fake)
    win = window()
    assert fake.calls == 1
    assert not win.top_bar.btn_permissions.isHidden()


@pytest.mark.parametrize("combo", ALL_COMBOS + [(S.NOT_REQUIRED,) * 3])
def test_startup_check_drives_the_top_bar_warning(window, combo):
    checker = FakeChecker(dict(zip(ORDER, combo)))
    win = window(permission_checker=checker)
    assert checker.calls == 1  # checked once at startup
    missing = [p for p, s in zip(ORDER, combo) if s is S.MISSING]
    button = win.top_bar.btn_permissions
    assert button.isHidden() is (not missing)
    if missing:
        assert mp.join_titles(missing) in button.toolTip()
        assert button.toolTip().startswith("Missing macOS permission")
        assert button.accessibleDescription() == button.toolTip()
        for other in set(ORDER) - set(missing):
            assert mp.info(other).title not in button.toolTip()
    else:
        assert button.toolTip() == ""


def test_failing_checker_never_breaks_the_window(window):
    def broken():
        raise RuntimeError("boom")

    win = window(permission_checker=broken)
    assert win.top_bar.btn_permissions.isHidden()
    assert win.permission_monitor.report() == {p: S.UNKNOWN for p in ORDER}


def test_warning_button_opens_the_panel(window, qtbot):
    checker = FakeChecker({P.ACCESSIBILITY: S.MISSING, P.INPUT_MONITORING: S.GRANTED,
                           P.SCREEN_RECORDING: S.UNKNOWN})
    win = window(permission_checker=checker)
    assert win.permissions_panel is None
    with qtbot.waitSignal(win.permissions_requested):
        win.top_bar.btn_permissions.click()
    panel = win.permissions_panel
    assert isinstance(panel, PermissionsPanel) and panel.isVisible()
    assert checker.calls == 2  # opening the panel reads the status again
    assert panel.rows[P.ACCESSIBILITY].status() is S.MISSING
    assert panel.rows[P.SCREEN_RECORDING].status() is S.UNKNOWN
    assert win.status_bar.currentMessage() == "Missing macOS permission: Accessibility. Click to review."
    assert panel.parent() is win


def test_panel_open_settings_goes_through_the_window_opener(window):
    win = window(permission_checker=FakeChecker())
    panel = win.show_permissions_panel()
    panel.rows[P.INPUT_MONITORING].btn_open.click()
    assert win.permissions_url_opener.urls == [EXPECTED_URLS[P.INPUT_MONITORING]]


def test_check_again_in_the_panel_updates_the_top_bar(window, qtbot):
    checker = FakeChecker({P.ACCESSIBILITY: S.MISSING, P.INPUT_MONITORING: S.GRANTED,
                           P.SCREEN_RECORDING: S.GRANTED})
    win = window(permission_checker=checker)
    panel = win.show_permissions_panel()
    assert not win.top_bar.btn_permissions.isHidden()
    checker.set(accessibility=S.GRANTED)
    panel.btn_check.click()
    assert win.top_bar.btn_permissions.isHidden()
    assert panel.rows[P.ACCESSIBILITY].status() is S.GRANTED
    # Reopening reuses the same panel.
    assert win.show_permissions_panel() is panel


def test_more_menu_has_permissions_item_before_about(window):
    win = window(permission_checker=FakeChecker())
    actions = win.more_menu.actions()
    assert win.act_permissions in actions
    assert win.act_permissions.text() == "Permissions…"
    assert actions.index(win.act_permissions) == actions.index(win.act_about) - 1
    win.act_permissions.trigger()
    assert win.permissions_panel is not None and win.permissions_panel.isVisible()


def test_application_activation_rechecks_with_throttle(window, qapp):
    checker = FakeChecker()
    win = window(permission_checker=checker)
    monitor = win.permission_monitor
    assert checker.calls == 1
    now = [500.0]
    monitor.clock = lambda: now[0]
    monitor.check()  # restart the throttle on the fake clock
    assert checker.calls == 2

    checker.set(screen_recording=S.MISSING)
    app = QGuiApplication.instance()
    now[0] = 501.0
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert checker.calls == 2                       # within the throttle window
    assert win.top_bar.btn_permissions.isHidden()

    now[0] = 503.5
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
    assert checker.calls == 2                       # leaving the app does not check
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert checker.calls == 3                       # back in the app after the interval
    assert not win.top_bar.btn_permissions.isHidden()
    assert "Screen Recording" in win.top_bar.btn_permissions.toolTip()

    now[0] = 504.0
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert checker.calls == 3


def test_activation_recheck_refreshes_an_open_panel(window, qapp):
    checker = FakeChecker({P.ACCESSIBILITY: S.MISSING, P.INPUT_MONITORING: S.GRANTED,
                           P.SCREEN_RECORDING: S.GRANTED})
    win = window(permission_checker=checker)
    panel = win.show_permissions_panel()
    win.permission_monitor.min_interval = 0.0
    checker.set(accessibility=S.GRANTED)
    QGuiApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert panel.rows[P.ACCESSIBILITY].status() is S.GRANTED
    assert win.top_bar.btn_permissions.isHidden()


# -----------------------------------------------------------------------------
# Pre-run warning
# -----------------------------------------------------------------------------

def _warnings(win: MainWindow) -> List[str]:
    return [e.message for e in win.log_console._entries if e.level == "WARNING"]


@pytest.fixture
def no_modals(monkeypatch):
    """Fails the test if anything opens a modal box or runs a dialog loop."""
    def fail(*_args, **_kwargs):
        raise AssertionError("a modal dialog was opened")

    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name, fail)
    monkeypatch.setattr(QDialog, "exec", fail)


def test_run_warns_about_missing_permissions_but_still_starts(window, qtbot, monkeypatch, no_modals):
    FakeHotkeyListener.instances.clear()
    monkeypatch.setattr(main_window_module, "GlobalHotkeyListener", FakeHotkeyListener)
    checker = FakeChecker({P.ACCESSIBILITY: S.MISSING, P.INPUT_MONITORING: S.GRANTED,
                           P.SCREEN_RECORDING: S.MISSING})
    win = window(
        scenario=_scenario(ActionType.MOUSE_CLICK, ActionType.FIXED_WAIT),
        input_driver=MockInputDriver(),
        screen_driver=MockScreenDriver(),
        permission_checker=checker,
    )
    calls_before = checker.calls
    win.start_macro()
    try:
        assert checker.calls == calls_before + 1   # fresh check right before the run
        assert win.engine is not None              # the run was not blocked
        warnings = _warnings(win)
        assert len(warnings) == 1
        assert warnings[0].startswith("Missing macOS permission: Accessibility (click and key steps).")
        assert "Screen Recording" not in warnings[0]  # no image step in this scenario
        assert "The run continues" in warnings[0]
        assert win.status_bar.currentMessage() == "Missing permission: Accessibility"
        assert any("Macro started" in e.message for e in win.log_console._entries)
        assert FakeHotkeyListener.instances and FakeHotkeyListener.instances[-1].started
        assert win.engine.wait_for_completion(timeout=5.0)
    finally:
        win._cleanup_runner()


@pytest.mark.parametrize("types, statuses, expected", [
    ((ActionType.IMAGE_WAIT,), {P.SCREEN_RECORDING: S.MISSING},
     "Missing macOS permission: Screen Recording (image-wait steps)."),
    ((ActionType.FIXED_WAIT,), {P.INPUT_MONITORING: S.MISSING},
     "Missing macOS permission: Input Monitoring (Esc / Ctrl+Shift+S stop keys)."),
    ((ActionType.KEY_INPUT, ActionType.IMAGE_WAIT), {P.ACCESSIBILITY: S.MISSING, P.INPUT_MONITORING: S.MISSING,
                                                     P.SCREEN_RECORDING: S.MISSING},
     "Missing macOS permissions: Accessibility (click and key steps); Input Monitoring (Esc / Ctrl+Shift+S "
     "stop keys); Screen Recording (image-wait steps)."),
])
def test_prerun_warning_names_what_the_scenario_needs(window, types, statuses, expected):
    report = mp.all_granted()
    report.update(statuses)
    win = window(scenario=_scenario(*types), permission_checker=FakeChecker(report))
    win._warn_missing_run_permissions()
    warnings = _warnings(win)
    assert len(warnings) == 1 and warnings[0].startswith(expected)
    titles = ", ".join(mp.info(p).title for p in mp.missing_permissions(report)
                       if p in mp.required_for_scenario(win.scenario))
    noun = "permission" if "," not in titles else "permissions"
    assert win.status_bar.currentMessage() == f"Missing {noun}: {titles}"


@pytest.mark.parametrize("statuses", [
    mp.all_granted(),
    {P.ACCESSIBILITY: S.UNKNOWN, P.INPUT_MONITORING: S.UNKNOWN, P.SCREEN_RECORDING: S.UNKNOWN},
    {p: S.NOT_REQUIRED for p in ORDER},
])
def test_no_prerun_warning_when_nothing_is_missing(window, statuses):
    win = window(scenario=_scenario(ActionType.MOUSE_CLICK, ActionType.IMAGE_WAIT),
                 permission_checker=FakeChecker(statuses))
    message_before = win.status_bar.currentMessage()
    win._warn_missing_run_permissions()
    assert _warnings(win) == []
    assert win.status_bar.currentMessage() == message_before


def test_disabled_steps_do_not_need_permissions(window):
    report = mp.all_granted()
    report[P.ACCESSIBILITY] = S.MISSING
    win = window(scenario=_scenario(ActionType.MOUSE_CLICK, ActionType.FIXED_WAIT, disabled=(0,)),
                 permission_checker=FakeChecker(report))
    win._warn_missing_run_permissions()
    assert _warnings(win) == []


def test_prerun_check_failure_does_not_block_the_run(window, monkeypatch, no_modals):
    monkeypatch.setattr(main_window_module, "GlobalHotkeyListener", FakeHotkeyListener)
    state = {"fail": False}

    def flaky():
        if state["fail"]:
            raise RuntimeError("boom")
        return mp.all_granted()

    win = window(scenario=_scenario(ActionType.FIXED_WAIT), input_driver=MockInputDriver(),
                 screen_driver=MockScreenDriver(), permission_checker=flaky)
    state["fail"] = True
    win.start_macro()
    try:
        assert win.engine is not None
        assert _warnings(win) == []
        assert win.engine.wait_for_completion(timeout=5.0)
    finally:
        win._cleanup_runner()
