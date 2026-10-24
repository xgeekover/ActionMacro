"""Integration tests for key combinations and emergency-stop interruption.

These tests assemble the input path exactly the way the shipped application does:

* parameters come from the real ``KeyInputForm`` widget (or real model classes),
* the real ``ExecutionEngine`` is built without an injected driver (as ``MainWindow()``
  and the headless CLI do), so it lazily creates the real ``PynputAdapter``,
* only the OS boundary is replaced: ``pynput.mouse.Controller`` and
  ``pynput.keyboard.Controller`` become recording fakes, so no real mouse or
  keyboard events are ever emitted.

Global hotkey hooks are also stubbed out so no system-wide listener is installed.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable, List, Optional, Tuple

import pytest

from action_macro.core.key_mapping import to_pynput_key
from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.engine.input_adapter import PynputAdapter
from action_macro.gui.property_inspector import KeyInputForm
from action_macro.gui.scenario_builder import ActionCardWidget
from action_macro.runner.execution_engine import ExecutionEngine
from action_macro.runner.hotkey_listener import GlobalHotkeyListener
from action_macro.runner.state_machine import EngineState

pytestmark = pytest.mark.e2e

# Maximum time after a stop request during which a new press may still slip out,
# held buttons/keys must be released, and the worker thread must have exited.
STOP_GRACE_S = 0.05

LogEntry = Tuple[float, str, Any]


# -----------------------------------------------------------------------------
# OS boundary fakes
# -----------------------------------------------------------------------------

class _InputLog:
    """Thread-safe chronological record of fake controller calls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.entries: List[LogEntry] = []

    def add(self, kind: str, payload: Any = None) -> None:
        with self._lock:
            self.entries.append((time.perf_counter(), kind, payload))

    def snapshot(self) -> List[LogEntry]:
        with self._lock:
            return list(self.entries)

    def first_index(self, kind: str) -> Optional[int]:
        for i, (_, k, _) in enumerate(self.snapshot()):
            if k == kind:
                return i
        return None


class _ForbiddenGlobalHook:
    """Stand-in for pynput global hooks: instantiating one fails the test."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("A real global keyboard hook must not be installed in this test")


@pytest.fixture
def input_log(monkeypatch: pytest.MonkeyPatch) -> _InputLog:
    """Replaces pynput controllers with recorders and returns the shared event log.

    Also wraps ``PynputAdapter.safety_flush`` so the log shows where flushes happen
    (the real flush still runs).
    """
    import pynput.keyboard
    import pynput.mouse

    log = _InputLog()

    class FakeMouseController:
        def __init__(self) -> None:
            self._pos: Tuple[int, int] = (0, 0)

        @property
        def position(self) -> Tuple[int, int]:
            return self._pos

        @position.setter
        def position(self, value: Tuple[int, int]) -> None:
            self._pos = (int(value[0]), int(value[1]))
            log.add("mouse_move", self._pos)

        def press(self, button: Any) -> None:
            log.add("mouse_press", button)

        def release(self, button: Any) -> None:
            log.add("mouse_release", button)

        def click(self, button: Any, count: int = 1) -> None:
            for _ in range(count):
                log.add("mouse_press", button)
                log.add("mouse_release", button)

    class FakeKeyboardController:
        def press(self, key: Any) -> None:
            log.add("key_press", key)

        def release(self, key: Any) -> None:
            log.add("key_release", key)

        def type(self, text: str) -> None:
            log.add("type", text)

    monkeypatch.setattr(pynput.mouse, "Controller", FakeMouseController)
    monkeypatch.setattr(pynput.keyboard, "Controller", FakeKeyboardController)
    monkeypatch.setattr(pynput.keyboard, "GlobalHotKeys", _ForbiddenGlobalHook)
    monkeypatch.setattr(pynput.keyboard, "Listener", _ForbiddenGlobalHook)

    real_flush = PynputAdapter.safety_flush

    def recording_flush(self: PynputAdapter) -> None:
        log.add("flush")
        real_flush(self)

    monkeypatch.setattr(PynputAdapter, "safety_flush", recording_flush)
    return log


def _pk(name: str) -> Any:
    """Concrete pynput key the adapter emits for an abstract key name on this OS."""
    return to_pynput_key(name, sys.platform)


def _key_events_before_first_flush(log: _InputLog) -> List[Tuple[str, Any]]:
    entries = log.snapshot()
    flush_idx = log.first_index("flush")
    if flush_idx is not None:
        entries = entries[:flush_idx]
    return [(kind, key) for _, kind, key in entries if kind in ("key_press", "key_release")]


def _expected_combo(names: List[str]) -> List[Tuple[str, Any]]:
    keys = [_pk(n) for n in names]
    return [("key_press", k) for k in keys] + [("key_release", k) for k in reversed(keys)]


def _engine_for(*blocks: ActionBlock) -> ExecutionEngine:
    """Builds the engine the way MainWindow()/the CLI do: no injected input driver."""
    scenario = ScenarioModel(name="integration", loop_count=1, actions=list(blocks))
    return ExecutionEngine(scenario=scenario)


def _key_block(params: KeyInputParams) -> ActionBlock:
    return ActionBlock(type=ActionType.KEY_INPUT, params=params)


def _form_params(qtbot: Any, text: str, action: str = "press", duration_ms: int = 50) -> KeyInputParams:
    form = KeyInputForm()
    qtbot.addWidget(form)
    form.combo_action.setCurrentText(action)
    form.spin_duration.setValue(duration_ms)
    form.edit_keys.setText(text)
    params = form.get_values()
    assert params is not None
    return params


# -----------------------------------------------------------------------------
# (a) Key combinations from the real KeyInputForm are pressed simultaneously
# -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("typed", "names"),
    [
        ("cmd_or_ctrl+shift+s", ["cmd_or_ctrl", "shift", "s"]),
        ("ctrl+c", ["ctrl", "c"]),
    ],
)
def test_key_input_form_combo_is_pressed_as_one_chord(
    qtbot: Any, input_log: _InputLog, typed: str, names: List[str]
) -> None:
    params = _form_params(qtbot, typed)
    engine = _engine_for(_key_block(params))

    assert engine.run() is True
    assert isinstance(engine.input_driver, PynputAdapter)

    assert _key_events_before_first_flush(input_log) == _expected_combo(names)
    presses = [e for e in input_log.snapshot() if e[1] == "key_press"]
    assert len(presses) == len(names)
    assert engine.input_driver.active_keys == set()


def test_combo_helper_output_is_pressed_as_one_chord(qtbot: Any, input_log: _InputLog) -> None:
    form = KeyInputForm()
    qtbot.addWidget(form)
    form.chk_ctrl.setChecked(True)
    form.chk_alt.setChecked(True)
    form.combo_preset_keys.setCurrentText("f5")
    form._apply_combo_helper()
    params = form.get_values()
    assert params is not None

    engine = _engine_for(_key_block(params))
    assert engine.run() is True

    assert _key_events_before_first_flush(input_log) == _expected_combo(["cmd_or_ctrl", "alt", "f5"])


def test_combo_holds_last_key_for_press_duration(qtbot: Any, input_log: _InputLog) -> None:
    params = _form_params(qtbot, "shift+a", duration_ms=120)
    engine = _engine_for(_key_block(params))
    assert engine.run() is True

    entries = input_log.snapshot()
    a_down = next(t for t, kind, key in entries if kind == "key_press" and key == _pk("a"))
    a_up = next(t for t, kind, key in entries if kind == "key_release" and key == _pk("a"))
    assert a_up - a_down >= 0.1


@pytest.mark.parametrize(
    ("modifiers", "keys", "names"),
    [
        (["cmd_or_ctrl"], ["c"], ["cmd_or_ctrl", "c"]),
        (["ctrl"], ["shift", "a"], ["ctrl", "shift", "a"]),
        # Duplicates between the two fields are pressed only once.
        (["shift"], ["shift", "tab"], ["shift", "tab"]),
    ],
)
def test_explicit_modifiers_field_is_pressed_first(
    input_log: _InputLog, modifiers: List[str], keys: List[str], names: List[str]
) -> None:
    engine = _engine_for(_key_block(KeyInputParams(keys=keys, modifiers=modifiers)))
    assert engine.run() is True
    assert _key_events_before_first_flush(input_log) == _expected_combo(names)


@pytest.mark.parametrize(
    ("modifiers", "keys", "shown", "names"),
    [
        (["cmd_or_ctrl"], ["c"], "cmd_or_ctrl+c", ["cmd_or_ctrl", "c"]),
        (["shift"], ["tab"], "shift+tab", ["shift", "tab"]),
        (["ctrl"], ["ctrl", "v"], "ctrl+v", ["ctrl", "v"]),
    ],
)
def test_saved_modifiers_survive_editing_in_inspector(
    qtbot: Any, input_log: _InputLog, modifiers: List[str], keys: List[str], shown: str, names: List[str]
) -> None:
    """A file written with a ``modifiers`` field keeps its shortcut after any inspector edit.

    The inspector used to show only ``keys`` and save ``modifiers=None``, so changing
    e.g. the press duration of a saved Cmd+C turned it into a plain "c".
    """
    form = KeyInputForm()
    qtbot.addWidget(form)
    form.set_values({"keys": keys, "modifiers": modifiers, "action": "press", "press_duration_ms": 50})
    assert form.edit_keys.text() == shown

    form.spin_duration.setValue(80)  # edit an unrelated field
    params = form.get_values()
    assert params is not None

    card = ActionCardWidget(_key_block(KeyInputParams(keys=keys, modifiers=modifiers)), 0, 1)
    qtbot.addWidget(card)
    assert f"[{shown}]" in card.lbl_summary.text()

    engine = _engine_for(_key_block(params))
    assert engine.run() is True
    assert _key_events_before_first_flush(input_log) == _expected_combo(names)


def test_single_key_press_is_unchanged(qtbot: Any, input_log: _InputLog) -> None:
    params = _form_params(qtbot, "enter")
    engine = _engine_for(_key_block(params))
    assert engine.run() is True
    assert _key_events_before_first_flush(input_log) == _expected_combo(["enter"])


# -----------------------------------------------------------------------------
# (c) down / up actions
# -----------------------------------------------------------------------------

def test_down_then_up_holds_and_releases_in_reverse(qtbot: Any, input_log: _InputLog) -> None:
    down = _form_params(qtbot, "ctrl+shift", action="down")
    tap = _form_params(qtbot, "a")
    up = _form_params(qtbot, "ctrl+shift", action="up")

    engine = _engine_for(_key_block(down), _key_block(tap), _key_block(up))
    assert engine.run() is True

    ctrl, shift, a = _pk("ctrl"), _pk("shift"), _pk("a")
    assert _key_events_before_first_flush(input_log) == [
        ("key_press", ctrl),
        ("key_press", shift),
        ("key_press", a),
        ("key_release", a),
        ("key_release", shift),
        ("key_release", ctrl),
    ]


def test_down_presses_explicit_modifiers_first_and_up_releases_them_last(input_log: _InputLog) -> None:
    down = KeyInputParams(keys=["a"], modifiers=["alt"], action="down")
    up = KeyInputParams(keys=["a"], modifiers=["alt"], action="up")

    engine = _engine_for(_key_block(down), _key_block(up))
    assert engine.run() is True

    alt, a = _pk("alt"), _pk("a")
    assert _key_events_before_first_flush(input_log) == [
        ("key_press", alt),
        ("key_press", a),
        ("key_release", a),
        ("key_release", alt),
    ]


# -----------------------------------------------------------------------------
# (b) Emergency stop interrupts in-flight clicks and key holds
# -----------------------------------------------------------------------------

def _stop_via_engine(engine: ExecutionEngine) -> None:
    engine.stop()  # MainWindow.stop_macro path


def _stop_via_hotkey_listener(engine: ExecutionEngine) -> None:
    # Emergency hotkey path (Esc / Cmd+Shift+Esc). The listener is never started,
    # so no global hook is installed; we invoke its handler directly.
    GlobalHotkeyListener(engine=engine, headless=True).trigger_emergency_stop()


STOP_PATHS = [
    pytest.param(_stop_via_engine, id="engine.stop"),
    pytest.param(_stop_via_hotkey_listener, id="hotkey-listener"),
]


def _wait_for(log: _InputLog, kind: str, timeout: float = 2.0) -> None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if log.first_index(kind) is not None:
            return
        time.sleep(0.005)
    raise AssertionError(f"no {kind!r} event within {timeout}s")


def _run_and_stop(
    engine: ExecutionEngine,
    log: _InputLog,
    first_kind: str,
    stop_after_s: float,
    stop: Callable[[ExecutionEngine], None],
) -> Tuple[float, float]:
    worker = engine.start_async()
    try:
        _wait_for(log, first_kind)
        time.sleep(stop_after_s)
        t_stop = time.perf_counter()
        stop(engine)
        worker.join(timeout=5.0)
        t_end = time.perf_counter()
        assert not worker.is_alive(), "worker thread did not exit after stop"
        return t_stop, t_end - t_stop
    finally:
        engine.cancel_event.set()
        worker.join(timeout=5.0)


def _held_at_end(log: _InputLog, press: str, release: str) -> set:
    held: set = set()
    for _, kind, payload in log.snapshot():
        if kind == press:
            held.add(payload)
        elif kind == release:
            held.discard(payload)
    return held


@pytest.mark.parametrize("stop", STOP_PATHS)
def test_stop_interrupts_multi_click(input_log: _InputLog, stop: Callable[[ExecutionEngine], None]) -> None:
    block = ActionBlock(
        type=ActionType.MOUSE_CLICK,
        params=MouseClickParams(x=10, y=10, clicks=10, interval_ms=300),
    )
    engine = _engine_for(block)

    t_stop, worker_exit_s = _run_and_stop(engine, input_log, "mouse_press", 0.35, stop)

    late_presses = [e for e in input_log.snapshot() if e[1] == "mouse_press" and e[0] > t_stop + STOP_GRACE_S]
    assert late_presses == []
    assert worker_exit_s < STOP_GRACE_S, f"worker exited {worker_exit_s * 1000:.1f}ms after stop"
    assert engine.state == EngineState.STOPPED
    assert _held_at_end(input_log, "mouse_press", "mouse_release") == set()
    assert engine.input_driver.active_buttons == set()

    # Fewer clicks than requested were sent (10 clicks would take ~3.2s).
    clicks_sent = len([e for e in input_log.snapshot() if e[1] == "mouse_press"])
    assert 1 <= clicks_sent < 10


@pytest.mark.parametrize("stop", STOP_PATHS)
def test_stop_interrupts_long_click_hold(input_log: _InputLog, stop: Callable[[ExecutionEngine], None]) -> None:
    block = ActionBlock(
        type=ActionType.MOUSE_CLICK,
        params=MouseClickParams(x=10, y=10, clicks=2, press_duration_ms=1000),
    )
    engine = _engine_for(block)

    t_stop, worker_exit_s = _run_and_stop(engine, input_log, "mouse_press", 0.2, stop)

    entries = input_log.snapshot()
    late_presses = [e for e in entries if e[1] == "mouse_press" and e[0] > t_stop + STOP_GRACE_S]
    assert late_presses == []
    first_release_after_stop = next(t for t, kind, _ in entries if kind == "mouse_release" and t >= t_stop)
    assert first_release_after_stop - t_stop < STOP_GRACE_S
    assert worker_exit_s < STOP_GRACE_S, f"worker exited {worker_exit_s * 1000:.1f}ms after stop"
    assert _held_at_end(input_log, "mouse_press", "mouse_release") == set()


@pytest.mark.parametrize("stop", STOP_PATHS)
def test_stop_interrupts_long_key_hold(
    qtbot: Any, input_log: _InputLog, stop: Callable[[ExecutionEngine], None]
) -> None:
    params = _form_params(qtbot, "shift+a", duration_ms=1000)
    engine = _engine_for(_key_block(params))

    t_stop, worker_exit_s = _run_and_stop(engine, input_log, "key_press", 0.2, stop)

    entries = input_log.snapshot()
    late_presses = [e for e in entries if e[1] == "key_press" and e[0] > t_stop + STOP_GRACE_S]
    assert late_presses == []
    assert worker_exit_s < STOP_GRACE_S, f"worker exited {worker_exit_s * 1000:.1f}ms after stop"
    assert engine.state == EngineState.STOPPED

    # Both keys of the chord are released promptly and nothing stays held.
    for name in ("a", "shift"):
        rel = next(t for t, kind, key in entries if kind == "key_release" and key == _pk(name) and t >= t_stop)
        assert rel - t_stop < STOP_GRACE_S
    assert _held_at_end(input_log, "key_press", "key_release") == set()
    assert engine.input_driver.active_keys == set()


# -----------------------------------------------------------------------------
# Full application assembly: MainWindow() with no injected drivers
# -----------------------------------------------------------------------------

@pytest.fixture
def app_window(qtbot: Any, input_log: _InputLog, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A MainWindow built exactly as the app does (no drivers injected).

    The emergency hotkey listener it starts is forced into headless mode so no
    global keyboard hook is installed, and modal message boxes are answered
    automatically so they cannot block the offscreen test run.
    """
    from PySide6.QtWidgets import QMessageBox

    from action_macro.gui import main_window as main_window_module

    def headless_listener(engine: Any = None, **kwargs: Any) -> GlobalHotkeyListener:
        kwargs["headless"] = True
        return GlobalHotkeyListener(engine=engine, **kwargs)

    monkeypatch.setattr(main_window_module, "GlobalHotkeyListener", headless_listener)
    # pytest-qt closes the window before fixture finalizers run; discard unsaved edits.
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Ok)

    win = main_window_module.MainWindow()
    qtbot.addWidget(win)
    yield win

    if win.engine is not None:
        win.engine.cancel_event.set()
        win.engine.wait_for_completion(timeout=5.0)


def _add_and_select(win: Any, action_type: ActionType) -> ActionBlock:
    block = win.scenario_builder.add_action_block(action_type)
    win.scenario_builder.select_action(block.id)
    return block


def test_main_window_runs_inspector_combo_as_one_chord(app_window: Any, input_log: _InputLog) -> None:
    win = app_window
    _add_and_select(win, ActionType.KEY_INPUT)
    form = win.property_inspector.form_key
    assert win.property_inspector.stacked_widget.currentWidget() is form
    form.edit_keys.setText("cmd_or_ctrl+shift+s")
    win.dashboard.set_loop_count(1)

    win.dashboard.btn_start.click()
    assert win.engine is not None
    assert win.engine.wait_for_completion(timeout=5.0)

    assert isinstance(win.engine.input_driver, PynputAdapter)
    assert _key_events_before_first_flush(input_log) == _expected_combo(["cmd_or_ctrl", "shift", "s"])


@pytest.fixture
def adapter_threads(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    """Records the name of every thread that constructs a PynputAdapter."""
    threads: List[str] = []
    original_init = PynputAdapter.__init__

    def recording_init(self: PynputAdapter, *args: Any, **kwargs: Any) -> None:
        threads.append(threading.current_thread().name)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(PynputAdapter, "__init__", recording_init)
    return threads


@pytest.mark.parametrize("action_type", [ActionType.MOUSE_CLICK, ActionType.KEY_INPUT])
def test_main_window_builds_input_driver_on_gui_thread(
    app_window: Any, input_log: _InputLog, adapter_threads: List[str], action_type: ActionType
) -> None:
    """The input driver must not be first built in the worker thread.

    On macOS pynput's keyboard controller reads the keyboard layout through
    TSMGetInputSourceProperty, which HIToolbox only allows on the main thread once
    the app shows a window. Building it in the worker killed the real app with
    SIGTRAP ("Python quit unexpectedly") on the first click or key step. Offscreen
    Qt cannot reproduce the trap, so this pins the thread instead.
    """
    win = app_window
    _add_and_select(win, action_type)
    win.dashboard.set_loop_count(1)

    win.dashboard.btn_start.click()
    assert win.engine is not None
    assert win.engine.wait_for_completion(timeout=5.0)

    assert isinstance(win.engine.input_driver, PynputAdapter)
    assert adapter_threads == [threading.main_thread().name]


def test_engine_start_async_builds_input_driver_on_calling_thread(
    input_log: _InputLog, adapter_threads: List[str]
) -> None:
    engine = _engine_for(ActionBlock(type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=5, y=5)))
    engine.start_async()
    assert engine.wait_for_completion(timeout=5.0)
    assert adapter_threads == [threading.main_thread().name]


def test_engine_without_input_actions_builds_no_input_driver(
    input_log: _InputLog, adapter_threads: List[str]
) -> None:
    engine = _engine_for(ActionBlock(type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=1)))
    engine.start_async()
    assert engine.wait_for_completion(timeout=5.0)
    assert adapter_threads == []
    assert engine.input_driver is None


def test_main_window_stop_action_interrupts_multi_click(app_window: Any, input_log: _InputLog) -> None:
    win = app_window
    _add_and_select(win, ActionType.MOUSE_CLICK)
    form = win.property_inspector.form_mouse
    assert win.property_inspector.stacked_widget.currentWidget() is form
    form.spin_x.setValue(10)
    form.spin_y.setValue(10)
    form.spin_clicks.setValue(10)
    form.spin_interval.setValue(300)
    win.dashboard.set_loop_count(1)

    win.dashboard.btn_start.click()
    engine = win.engine
    assert engine is not None
    _wait_for(input_log, "mouse_press")
    time.sleep(0.35)

    t_stop = time.perf_counter()
    win.act_stop.trigger()  # Run > Emergency Stop (F12)
    assert engine.wait_for_completion(timeout=5.0)
    worker_exit_s = time.perf_counter() - t_stop

    late_presses = [e for e in input_log.snapshot() if e[1] == "mouse_press" and e[0] > t_stop + STOP_GRACE_S]
    assert late_presses == []
    assert worker_exit_s < STOP_GRACE_S, f"worker exited {worker_exit_s * 1000:.1f}ms after stop"
    assert _held_at_end(input_log, "mouse_press", "mouse_release") == set()


# -----------------------------------------------------------------------------
# Headless CLI assembly: `ActionMacro --headless scenario.json`
# -----------------------------------------------------------------------------

@pytest.fixture
def headless_cli(input_log: _InputLog, monkeypatch: pytest.MonkeyPatch) -> Callable[..., int]:
    """Returns the real CLI ``main`` with its emergency hotkey listener forced headless.

    ``main()`` imports ``GlobalHotkeyListener`` from its module at call time, so
    patching the module attribute keeps the CLI's own assembly intact while making
    sure no global keyboard hook is installed.
    """
    from action_macro import __main__ as cli_module
    from action_macro.runner import hotkey_listener as hotkey_listener_module

    def headless_listener(engine: Any = None, **kwargs: Any) -> GlobalHotkeyListener:
        kwargs["headless"] = True
        return GlobalHotkeyListener(engine=engine, **kwargs)

    monkeypatch.setattr(hotkey_listener_module, "GlobalHotkeyListener", headless_listener)
    return cli_module.main


def test_headless_cli_presses_saved_combos_as_chords(
    headless_cli: Callable[..., int], input_log: _InputLog, tmp_path: Any
) -> None:
    from action_macro.core.persistence import save_scenario

    scenario = ScenarioModel(
        name="cli-combos",
        loop_count=1,
        actions=[
            # Format the GUI saves: the whole combo lives in ``keys``.
            _key_block(KeyInputParams(keys=["ctrl", "c"])),
            # Legacy/hand-written format: explicit ``modifiers`` field.
            _key_block(KeyInputParams(keys=["v"], modifiers=["cmd_or_ctrl"])),
        ],
    )
    path = tmp_path / "combos.json"
    save_scenario(scenario, path)

    assert headless_cli(["--headless", str(path), "--loops", "1"]) == 0

    assert _key_events_before_first_flush(input_log) == (
        _expected_combo(["ctrl", "c"]) + _expected_combo(["cmd_or_ctrl", "v"])
    )


# -----------------------------------------------------------------------------
# Adapter-level guarantees (real PynputAdapter, fake controllers)
# -----------------------------------------------------------------------------

def test_adapter_click_returns_promptly_when_cancelled(input_log: _InputLog) -> None:
    adapter = PynputAdapter()
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()

    t0 = time.perf_counter()
    adapter.click(5, 5, count=10, interval_ms=500, press_duration=0.05, cancel_event=cancel)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.1 + STOP_GRACE_S
    assert adapter.active_buttons == set()
    assert _held_at_end(input_log, "mouse_press", "mouse_release") == set()


def test_adapter_press_key_releases_chord_in_reverse_when_cancelled(input_log: _InputLog) -> None:
    # No engine and no safety_flush here: the adapter itself must let go of the chord.
    adapter = PynputAdapter()
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()

    t0 = time.perf_counter()
    adapter.press_key("s", modifiers=["ctrl", "shift"], duration=1.0, cancel_event=cancel)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.1 + STOP_GRACE_S
    events = [(kind, key) for _, kind, key in input_log.snapshot() if kind in ("key_press", "key_release")]
    assert events == _expected_combo(["ctrl", "shift", "s"])
    assert adapter.active_keys == set()


def test_adapter_does_nothing_when_already_cancelled(input_log: _InputLog) -> None:
    adapter = PynputAdapter()
    cancel = threading.Event()
    cancel.set()

    adapter.click(5, 5, count=3, cancel_event=cancel)
    adapter.press_key("a", modifiers=["shift"], duration=1.0, cancel_event=cancel)

    kinds = {kind for _, kind, _ in input_log.snapshot()}
    assert "mouse_press" not in kinds
    assert "key_press" not in kinds


def test_adapter_press_key_releases_held_modifiers_when_target_key_fails(
    input_log: _InputLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = PynputAdapter()
    real_press = adapter._keyboard.press

    def failing_press(key: Any) -> None:
        if key == _pk("x"):
            raise RuntimeError("injected OS failure")
        real_press(key)

    monkeypatch.setattr(adapter._keyboard, "press", failing_press)

    with pytest.raises(RuntimeError, match="injected OS failure"):
        adapter.press_key("x", modifiers=["ctrl", "shift"], duration=0.0)

    released = [key for _, kind, key in input_log.snapshot() if kind == "key_release"]
    assert _pk("shift") in released and _pk("ctrl") in released
    assert released.index(_pk("shift")) < released.index(_pk("ctrl"))
    assert adapter.active_keys == set()


def test_adapter_hotkey_still_presses_in_order_and_releases_in_reverse(input_log: _InputLog) -> None:
    adapter = PynputAdapter()
    adapter.hotkey("ctrl", "alt", "1")
    events = [(kind, key) for _, kind, key in input_log.snapshot() if kind in ("key_press", "key_release")]
    assert events == _expected_combo(["ctrl", "alt", "1"])
