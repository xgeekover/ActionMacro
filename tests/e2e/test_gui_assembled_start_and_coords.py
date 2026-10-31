"""
tests.e2e.test_gui_assembled_start_and_coords
=============================================
Integration tests that assemble the GUI exactly like the shipped application
(``MainWindow()`` with no injected widgets or drivers) and fake only the OS
boundary: pynput mouse/keyboard controllers, pynput ``GlobalHotKeys`` and
screen capture.

Covers two defects that were invisible to tests injecting mock drivers:

1. Every "Start" entry point (Run menu, the run bar's Run button, F5 shortcut)
   must honour the dashboard Repeat Count exactly like the dashboard ``Start``
   button. (The old text toolbar that duplicated the menu was removed in the UI
   redesign; its on-screen Start entry is now the Run button of the run bar.)
   ``QAction.triggered`` delivers ``checked=False``; it used to reach
   ``start_macro(loop_count)`` as ``loop_count=False`` and run forever.
2. Negative global coordinates (monitors placed left of / above the primary
   display) must survive picker -> inspector -> model -> engine click, and
   negative ROI origins must survive the Image Wait form.

Also guards the QAction wiring itself: the no-argument slots must not keep
the window alive through a lambda capturing ``self``.
"""

from __future__ import annotations

import gc
import json
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton, QToolBar

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.core.persistence import ScenarioPersistence
from action_macro.gui import coordinate_picker
from action_macro.gui.coordinate_picker import CoordinatePickerOverlay
from action_macro.gui.main_window import MainWindow

RUN_TIMEOUT_MS = 4000


# -----------------------------------------------------------------------------
# OS boundary fakes (no real input, no real hotkey hook, no real capture)
# -----------------------------------------------------------------------------

class OsBoundaryRecorder:
    """Collects everything the application tried to send to the OS."""

    def __init__(self) -> None:
        self.mouse_events: List[Tuple[str, Any, Tuple[int, int]]] = []
        self.key_events: List[Tuple[str, Any]] = []
        self.hotkey_hooks: List["FakeGlobalHotKeys"] = []
        self.capture_calls: int = 0

    def presses(self) -> List[Tuple[str, Tuple[int, int]]]:
        """Returns (button_name, position) for every mouse button press."""
        return [(btn, pos) for kind, btn, pos in self.mouse_events if kind == "press"]


class FakeGlobalHotKeys:
    """Stand-in for pynput.keyboard.GlobalHotKeys: never installs an OS hook."""

    recorder: Optional[OsBoundaryRecorder] = None

    def __init__(self, hotkeys: Dict[str, Any], *args: Any, **kwargs: Any) -> None:
        self.hotkeys = dict(hotkeys)
        self.daemon = True
        self.started = False
        self.stopped = False
        if FakeGlobalHotKeys.recorder is not None:
            FakeGlobalHotKeys.recorder.hotkey_hooks.append(self)

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_alive(self) -> bool:
        return self.started and not self.stopped

    def join(self, timeout: Optional[float] = None) -> None:
        return None


def _button_name(button: Any) -> str:
    return str(getattr(button, "name", button))


@pytest.fixture
def os_boundary(monkeypatch: pytest.MonkeyPatch) -> OsBoundaryRecorder:
    """Replaces pynput controllers, GlobalHotKeys and screen capture with recorders."""
    import mss
    import pynput.keyboard
    import pynput.mouse

    recorder = OsBoundaryRecorder()

    class FakeMouseController:
        def __init__(self) -> None:
            self._position = (0, 0)

        @property
        def position(self) -> Tuple[int, int]:
            return self._position

        @position.setter
        def position(self, pos: Tuple[float, float]) -> None:
            self._position = (int(pos[0]), int(pos[1]))
            recorder.mouse_events.append(("move", None, self._position))

        def press(self, button: Any) -> None:
            recorder.mouse_events.append(("press", _button_name(button), self._position))

        def release(self, button: Any) -> None:
            recorder.mouse_events.append(("release", _button_name(button), self._position))

        def click(self, button: Any, count: int = 1) -> None:
            for _ in range(count):
                recorder.mouse_events.append(("press", _button_name(button), self._position))
                recorder.mouse_events.append(("release", _button_name(button), self._position))

        def scroll(self, dx: int, dy: int) -> None:
            recorder.mouse_events.append(("scroll", (dx, dy), self._position))

    class FakeKeyboardController:
        def press(self, key: Any) -> None:
            recorder.key_events.append(("press", key))

        def release(self, key: Any) -> None:
            recorder.key_events.append(("release", key))

    def _forbidden_capture(*args: Any, **kwargs: Any) -> Any:
        recorder.capture_calls += 1
        raise AssertionError("Real screen capture (mss) must not run in tests")

    monkeypatch.setattr(pynput.mouse, "Controller", FakeMouseController)
    monkeypatch.setattr(pynput.keyboard, "Controller", FakeKeyboardController)
    monkeypatch.setattr(pynput.keyboard, "GlobalHotKeys", FakeGlobalHotKeys)
    monkeypatch.setattr(FakeGlobalHotKeys, "recorder", recorder)
    monkeypatch.setattr(mss, "mss", _forbidden_capture, raising=False)
    monkeypatch.setattr(mss, "MSS", _forbidden_capture, raising=False)
    # The picker grabs a desktop snapshot for its magnifier; keep it off the real screen.
    monkeypatch.setattr(CoordinatePickerOverlay, "_capture_screen_snapshot", lambda self: None)

    # Never block on modal dialogs during teardown or guards.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: QMessageBox.StandardButton.Ok)
    return recorder


@pytest.fixture
def make_window(qtbot, os_boundary: OsBoundaryRecorder):
    """Builds MainWindow exactly like gui/app.py does (no arguments)."""
    windows: List[MainWindow] = []

    def _factory() -> MainWindow:
        win = MainWindow()
        qtbot.addWidget(win)
        windows.append(win)
        return win

    yield _factory

    for win in windows:
        try:
            win.stop_macro()
            win._cleanup_runner()
        finally:
            win.mark_dirty(False)


# -----------------------------------------------------------------------------
# Helpers driving the real widgets
# -----------------------------------------------------------------------------

def _builder_add_step(win: MainWindow, label: str) -> QAction:
    """The entry ``label`` of the step list's "+ Add step" menu (what the user picks there).

    The four "+ <type>" toolbar buttons were replaced by this menu in the UI redesign;
    triggering the entry runs the same ``add_action_block`` path the buttons did.
    """
    builder = win.scenario_builder
    for action in builder.add_step_menu.actions():
        if action.text() == label:
            assert action.isEnabled() and action.isVisible()
            assert action in builder.add_step_actions.values()
            return action
    raise AssertionError(f"ScenarioBuilder add-step menu entry {label!r} not found")


def _compose_click_and_wait(win: MainWindow, x: int, y: int) -> ActionBlock:
    """Builds [Mouse Click(x, y), Fixed Wait(10ms)] through builder + inspector widgets."""
    _builder_add_step(win, "Mouse click").trigger()
    click = win.scenario.actions[-1]
    assert win.property_inspector.current_action is click
    win.property_inspector.form_mouse.spin_x.setValue(x)
    win.property_inspector.form_mouse.spin_y.setValue(y)

    _builder_add_step(win, "Wait").trigger()
    wait = win.scenario.actions[-1]
    assert win.property_inspector.current_action is wait
    win.property_inspector.form_wait.spin_duration.setValue(10)

    assert click.params.x == x and click.params.y == y
    assert wait.params.duration_ms == 10
    return click


def _menu_action(win: MainWindow, menu_title: str, action_text: str):
    for top in win.menuBar().actions():
        if top.text() == menu_title and top.menu() is not None:
            for act in top.menu().actions():
                if act.text() == action_text:
                    return act
    raise AssertionError(f"Menu entry {menu_title} > {action_text} not found")


def _start_via(win: MainWindow, qtbot, entry: str) -> None:
    if entry == "dashboard_button":
        win.dashboard.btn_start.click()
    elif entry == "run_menu":
        _menu_action(win, "&Run", "&Start Execution").trigger()
    elif entry == "run_bar":
        # The redesign replaced the text toolbar (it duplicated the menu) with the
        # editor run bar. Click its Run button the way a user does: located in the
        # shown window by object name and clicked through a synthetic Qt mouse event.
        assert win.findChild(QToolBar) is None, "the duplicate text toolbar should be gone"
        run_button = win.findChild(QPushButton, "RunButton")
        assert run_button is not None and run_button is win.dashboard.btn_start
        win.show()
        qtbot.waitExposed(win)
        assert run_button.isVisible() and run_button.isEnabled()
        QTest.mouseClick(run_button, Qt.MouseButton.LeftButton)
    elif entry == "f5_shortcut":
        assert win.act_run.shortcut() == QKeySequence("F5")
        win.show()
        qtbot.waitExposed(win)
        win.activateWindow()
        qtbot.waitUntil(lambda: QApplication.activeWindow() is win, timeout=2000)
        QTest.keyClick(win, Qt.Key.Key_F5)
    else:  # pragma: no cover - guard against typos in parametrize
        raise ValueError(entry)


def _wait_until_finished(win: MainWindow, qtbot) -> None:
    """Waits until the run finished and MainWindow cleaned up its worker."""
    qtbot.waitUntil(lambda: win.engine is not None, timeout=2000)
    try:
        qtbot.waitUntil(lambda: win._worker_thread is None, timeout=RUN_TIMEOUT_MS)
    except Exception:
        engine = win.engine
        raise AssertionError(
            f"Macro still running after {RUN_TIMEOUT_MS} ms: "
            f"engine loop_count={engine.scenario.loop_count!r}, "
            f"current_loop={engine.current_loop}, "
            f"dashboard={win.dashboard.loop_label.text()!r}"
        ) from None


def _run_outcome(win: MainWindow, recorder: OsBoundaryRecorder, qtbot) -> Dict[str, Any]:
    _wait_until_finished(win, qtbot)
    presses_at_finish = list(recorder.presses())
    qtbot.wait(150)  # nothing may keep clicking after completion
    assert recorder.presses() == presses_at_finish

    loop_count = win.engine.scenario.loop_count
    return {
        "loop_count": loop_count,
        "loop_count_type": type(loop_count).__name__,
        "presses": presses_at_finish,
        "finished": list(win.engine.signals.finished),
        "loop_label": win.dashboard.loop_label.text(),
    }


# -----------------------------------------------------------------------------
# 1. Start entry points honour Repeat Count
# -----------------------------------------------------------------------------

@pytest.mark.parametrize("entry", ["run_menu", "run_bar", "f5_shortcut"])
def test_start_entry_points_honour_repeat_count_like_dashboard(
    entry: str, make_window, os_boundary: OsBoundaryRecorder, qtbot
) -> None:
    """Repeat Count=1 via menu/run bar/F5 must run exactly one iteration, like the Start button."""
    # Baseline: dashboard ▶ Start button (start_requested(int) path).
    base_win = make_window()
    _compose_click_and_wait(base_win, 640, 360)
    base_win.dashboard.spin_loops.setValue(1)
    _start_via(base_win, qtbot, "dashboard_button")
    baseline = _run_outcome(base_win, os_boundary, qtbot)

    assert baseline == {
        "loop_count": 1,
        "loop_count_type": "int",
        "presses": [("left", (640, 360))],
        "finished": [(True, "Completed 1 loops")],
        "loop_label": "Iteration: 1 / 1",
    }

    # Same composition, started through the QAction entry point under test.
    os_boundary.mouse_events.clear()
    win = make_window()
    _compose_click_and_wait(win, 640, 360)
    win.dashboard.spin_loops.setValue(1)
    _start_via(win, qtbot, entry)
    outcome = _run_outcome(win, os_boundary, qtbot)

    assert outcome == baseline
    assert "Macro started (Loops: 1)" in win.log_console.text_edit.toPlainText()
    # The emergency-stop hook went through the (fake) pynput boundary and was released.
    assert os_boundary.hotkey_hooks and all(h.stopped for h in os_boundary.hotkey_hooks)
    assert os_boundary.capture_calls == 0


def test_run_action_uses_multi_iteration_repeat_count(
    make_window, os_boundary: OsBoundaryRecorder, qtbot
) -> None:
    """Repeat Count=3 via the Run action runs exactly three iterations."""
    win = make_window()
    _compose_click_and_wait(win, 10, 20)
    win.dashboard.spin_loops.setValue(3)
    win.act_run.trigger()
    outcome = _run_outcome(win, os_boundary, qtbot)

    assert outcome["loop_count"] == 3 and outcome["loop_count_type"] == "int"
    assert outcome["presses"] == [("left", (10, 20))] * 3
    assert outcome["finished"] == [(True, "Completed 3 loops")]
    assert outcome["loop_label"] == "Iteration: 3 / 3"


@pytest.mark.parametrize("bogus", [False, True, -1])
def test_start_macro_ignores_checked_bool_and_negative_loop_count(
    bogus: Any, make_window, os_boundary: OsBoundaryRecorder, qtbot
) -> None:
    """A stray ``checked`` bool (or negative count) falls back to the dashboard Repeat Count."""
    win = make_window()
    _compose_click_and_wait(win, 5, 6)
    win.dashboard.spin_loops.setValue(2)
    win.start_macro(bogus)
    outcome = _run_outcome(win, os_boundary, qtbot)

    assert outcome["loop_count"] == 2 and outcome["loop_count_type"] == "int"
    assert outcome["presses"] == [("left", (5, 6))] * 2
    assert outcome["finished"] == [(True, "Completed 2 loops")]


# -----------------------------------------------------------------------------
# 2. Negative coordinates: picker -> inspector -> model -> engine
# -----------------------------------------------------------------------------

class _FakeSecondaryScreen:
    """A monitor placed up-left of the primary display (virtual desktop origin < 0)."""

    def __init__(self, rect: QRect) -> None:
        self._rect = rect

    def geometry(self) -> QRect:
        return QRect(self._rect)

    def devicePixelRatio(self) -> float:
        return 2.0

    def name(self) -> str:
        return "FakeLeftTopMonitor"


def test_negative_picker_coordinate_reaches_engine_click(
    make_window, os_boundary: OsBoundaryRecorder, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pick on a monitor at (-3840, -2160) keeps its negative coordinates end to end."""
    # Qt points are the click coordinates here (macOS). Windows picks physical pixels, which
    # the scaled fake monitor would turn into other numbers: test_gui_coordinate_picker.py.
    monkeypatch.setattr(coordinate_picker, "PICKS_PHYSICAL_PIXELS", False)
    primary = QGuiApplication.primaryScreen()
    left_top = _FakeSecondaryScreen(QRect(-3840, -2160, 3840, 2160))
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: [primary, left_top]))

    win = make_window()
    _builder_add_step(win, "Mouse click").trigger()
    click = win.scenario.actions[-1]
    assert win.property_inspector.current_action is click

    # Real "📍 Pick Screen Coordinate" button -> MainWindow -> CoordinatePickerOverlay
    win.property_inspector.form_mouse.btn_pick.click()
    master = win._active_picker_overlay
    assert isinstance(master, CoordinatePickerOverlay)
    slaves = [s for s in master._slaves if s.target_screen is left_top]
    assert len(slaves) == 1, "picker must cover the secondary monitor with its own overlay"
    slave = slaves[0]

    # Left click on the secondary monitor overlay at global (-1200, -300).
    local = QPointF(-1200 - (-3840), -300 - (-2160))
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        local,
        QPointF(-1200, -300),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(slave, press)

    form = win.property_inspector.form_mouse
    assert (form.spin_x.value(), form.spin_y.value()) == (-1200, -300)
    assert (click.params.x, click.params.y) == (-1200, -300)

    # Re-open the block in the inspector and edit an unrelated field: still negative.
    _builder_add_step(win, "Wait").trigger()
    win.property_inspector.form_wait.spin_duration.setValue(10)
    win.scenario_builder.select_action(click.id)
    assert win.property_inspector.current_action is click
    assert (form.spin_x.value(), form.spin_y.value()) == (-1200, -300)
    form.combo_button.setCurrentText("right")
    assert (click.params.x, click.params.y, click.params.button) == (-1200, -300, "right")

    # Execute through the (fixed) Run action: the OS boundary receives the negative point.
    win.dashboard.spin_loops.setValue(1)
    win.act_run.trigger()
    outcome = _run_outcome(win, os_boundary, qtbot)

    assert outcome["presses"] == [("right", (-1200, -300))]
    assert ("move", None, (-1200, -300)) in os_boundary.mouse_events
    assert outcome["finished"] == [(True, "Completed 1 loops")]
    assert os_boundary.capture_calls == 0


def _save_multi_monitor_scenario(path: Path) -> None:
    """Writes a scenario whose click and ROI origins sit on a left/top monitor."""
    scenario = ScenarioModel(
        name="Left monitor",
        loop_count=1,
        actions=[
            ActionBlock(
                id="click-left",
                type=ActionType.MOUSE_CLICK,
                params=MouseClickParams(x=-3840, y=-2160),
            ),
            ActionBlock(
                id="wait-roi",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(
                    template_path="templates/missing.png",
                    roi=(-1920, -1080, 400, 300),
                ),
            ),
            ActionBlock(
                id="pause",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=10),
            ),
        ],
    )
    ScenarioPersistence.save(scenario, path)


def _saved_params(path: Path) -> Dict[str, Dict[str, Any]]:
    saved = json.loads(path.read_text(encoding="utf-8"))
    return {a["id"]: a["params"] for a in saved["actions"]}


def test_negative_click_coordinates_survive_open_edit_save(
    make_window, os_boundary: OsBoundaryRecorder, tmp_path: Path
) -> None:
    """Opening a block with negative X/Y and editing another field must not clamp them."""
    src = tmp_path / "multi_monitor.json"
    _save_multi_monitor_scenario(src)

    win = make_window()
    assert win.open_scenario(str(src)) is True

    click = win.scenario.get_action("click-left")
    win.scenario_builder.select_action("click-left")
    form_mouse = win.property_inspector.form_mouse
    assert (form_mouse.spin_x.value(), form_mouse.spin_y.value()) == (-3840, -2160)
    form_mouse.spin_clicks.setValue(2)
    assert (click.params.x, click.params.y, click.params.clicks) == (-3840, -2160, 2)

    assert win.save_scenario() is True
    saved = _saved_params(src)
    assert (saved["click-left"]["x"], saved["click-left"]["y"]) == (-3840, -2160)
    assert saved["click-left"]["clicks"] == 2


def test_negative_roi_origin_survives_open_edit_save(
    make_window, os_boundary: OsBoundaryRecorder, tmp_path: Path
) -> None:
    """Opening an Image Wait with a negative ROI origin and editing must not clamp it."""
    src = tmp_path / "multi_monitor.json"
    _save_multi_monitor_scenario(src)

    win = make_window()
    assert win.open_scenario(str(src)) is True

    image = win.scenario.get_action("wait-roi")
    win.scenario_builder.select_action("wait-roi")
    form_image = win.property_inspector.form_image
    assert form_image.chk_roi.isChecked()
    assert (form_image.spin_rx.value(), form_image.spin_ry.value()) == (-1920, -1080)
    form_image.combo_mode.setCurrentText("disappear")
    assert image.params.mode == "disappear"
    assert tuple(image.params.roi) == (-1920, -1080, 400, 300)

    # ROI width/height stay strictly positive.
    assert form_image.spin_rw.minimum() >= 1 and form_image.spin_rh.minimum() >= 1

    assert win.save_scenario() is True
    saved = _saved_params(src)
    assert saved["wait-roi"]["mode"] == "disappear"
    assert list(saved["wait-roi"]["roi"]) == [-1920, -1080, 400, 300]


# -----------------------------------------------------------------------------
# 3. Action wiring guards
# -----------------------------------------------------------------------------

def test_open_action_shows_file_dialog_instead_of_using_checked_flag(
    make_window, os_boundary: OsBoundaryRecorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File > Open must ask for a file; QAction's ``checked`` flag is not a path."""
    src = tmp_path / "picked.json"
    _save_multi_monitor_scenario(src)
    dialog_calls: List[Tuple[Any, ...]] = []

    def _fake_get_open_file_name(*args: Any, **kwargs: Any) -> Tuple[str, str]:
        dialog_calls.append(args)
        return str(src), "JSON Scenarios (*.json)"

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(_fake_get_open_file_name))

    win = make_window()
    _menu_action(win, "&File", "&Open Scenario…").trigger()  # U+2026, like every other "…" in the UI

    assert len(dialog_calls) == 1
    assert win.current_file_path == src.resolve()
    assert win.scenario.name == "Left monitor"


def test_main_window_is_not_kept_alive_by_its_action_wiring(
    qapp, os_boundary: OsBoundaryRecorder
) -> None:
    """Menu/toolbar slots must not capture the window in a reference cycle.

    A ``lambda: self.start_macro()`` connected to a child QAction is owned by
    the C++ connection, which Python's GC cannot see, so the window would never
    be released (and pytest-qt would later close it, popping a modal
    "Unsaved Changes" dialog that blocks the test run).
    """
    win = MainWindow()
    ref = weakref.ref(win)
    del win
    gc.collect()
    assert ref() is None
