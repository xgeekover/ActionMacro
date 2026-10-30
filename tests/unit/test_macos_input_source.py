"""Text steps type with an ASCII input source selected on macOS (macos_input_source).

The real input source is never switched here (see the guard in conftest): the switching
functions are replaced, and only the main-thread dispatch runs for real (on macOS).
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import threading
import time

import pytest

from action_macro.engine import input_adapter, macos_input_source
from action_macro.engine.input_adapter import PynputAdapter

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="libdispatch main queue (macOS)")


@pytest.fixture
def fake_switch(monkeypatch):
    """Records select/restore instead of touching the real input source."""
    calls = []
    state = {"original": 7}

    def select():
        calls.append(("select", threading.current_thread() is threading.main_thread()))
        return state["original"]

    def restore(original):
        calls.append(("restore", original))

    monkeypatch.setattr(macos_input_source, "available", lambda: True)
    monkeypatch.setattr(macos_input_source, "_select_ascii_capable", select)
    monkeypatch.setattr(macos_input_source, "_restore", restore)
    monkeypatch.setattr(macos_input_source, "SWITCH_SETTLE_S", 0)
    monkeypatch.setattr(macos_input_source, "RESTORE_DELAY_S", 0)
    return calls, state


def test_switches_for_the_body_and_back_afterwards(fake_switch):
    calls, _ = fake_switch
    with macos_input_source.ascii_input_source():
        calls.append(("body",))
    assert calls == [("select", True), ("body",), ("restore", 7)]


def test_nothing_to_restore_when_the_source_already_types_ascii(fake_switch):
    calls, state = fake_switch
    state["original"] = None
    with macos_input_source.ascii_input_source():
        pass
    assert calls == [("select", True)]


def test_switches_back_when_the_body_fails(fake_switch):
    calls, _ = fake_switch
    with pytest.raises(KeyError):
        with macos_input_source.ascii_input_source():
            raise KeyError("stop")
    assert calls[-1] == ("restore", 7)


def test_types_anyway_when_the_main_thread_does_not_answer(fake_switch, monkeypatch):
    calls, _ = fake_switch
    monkeypatch.setattr(macos_input_source, "run_on_main_thread", lambda fn, timeout, **kw: (False, None))
    with macos_input_source.ascii_input_source():
        calls.append(("body",))
    assert calls == [("body",)]


def test_does_nothing_off_macos(monkeypatch):
    monkeypatch.setattr(macos_input_source, "sys", type("S", (), {"platform": "win32"}))
    assert macos_input_source.available() is False
    with macos_input_source.ascii_input_source():
        pass  # the conftest guard fails the test if anything was switched


# -----------------------------------------------------------------------------
# Main-thread dispatch (the real libdispatch main queue, pumped by a CFRunLoop here)
# -----------------------------------------------------------------------------

def _pump_main_run_loop(until, timeout=3.0):
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFRunLoopRunInMode.restype = ctypes.c_int32
    cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
    mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
    end = time.monotonic() + timeout
    while not until() and time.monotonic() < end:
        cf.CFRunLoopRunInMode(mode, 0.02, False)


@darwin_only
def test_a_worker_thread_gets_the_result_from_the_main_thread():
    box = {}

    def worker():
        box["answer"] = macos_input_source.run_on_main_thread(
            lambda: threading.current_thread() is threading.main_thread())

    thread = threading.Thread(target=worker)
    thread.start()
    _pump_main_run_loop(lambda: "answer" in box)
    thread.join(2)
    assert box["answer"] == (True, True)


@darwin_only
def test_an_error_on_the_main_thread_reaches_the_worker():
    box = {}

    def fail():
        raise ValueError("boom")

    def worker():
        try:
            macos_input_source.run_on_main_thread(fail)
        except ValueError as err:
            box["error"] = str(err)

    thread = threading.Thread(target=worker)
    thread.start()
    _pump_main_run_loop(lambda: "error" in box)
    thread.join(2)
    assert box["error"] == "boom"


@darwin_only
def test_a_call_the_main_thread_did_not_reach_in_time_is_dropped_or_kept():
    ran = []
    answers = {}

    def worker():
        answers["dropped"] = macos_input_source.run_on_main_thread(lambda: ran.append("dropped"), timeout=0.1)
        answers["kept"] = macos_input_source.run_on_main_thread(
            lambda: ran.append("kept"), timeout=0.1, abandon_on_timeout=False)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(2)  # the main thread is busy here: neither call can run yet
    assert answers == {"dropped": (False, None), "kept": (False, None)}
    _pump_main_run_loop(lambda: "kept" in ran, timeout=1.0)
    assert ran == ["kept"]


# -----------------------------------------------------------------------------
# The adapter types the whole text inside one scope
# -----------------------------------------------------------------------------

class _Keyboard:
    def __init__(self):
        self.typed = []

    def type(self, char):
        self.typed.append(char)


class _Mouse:
    position = (0, 0)


def test_type_text_enters_the_scope_once_around_the_whole_text():
    events = []

    @contextlib.contextmanager
    def scope():
        events.append("enter")
        yield
        events.append("exit")

    keyboard = _Keyboard()
    adapter = PynputAdapter(mouse_controller=_Mouse(), keyboard_controller=keyboard, text_input_scope=scope)
    adapter.type_text("id", interval=0)
    assert events == ["enter", "exit"] and keyboard.typed == ["i", "d"]
    adapter.type_text("", interval=0)
    assert events == ["enter", "exit"]  # nothing to type: no switch


def test_the_real_keyboard_on_macos_uses_the_ascii_input_source(monkeypatch):
    import pynput.keyboard

    monkeypatch.setattr(pynput.keyboard, "Controller", _Keyboard)
    monkeypatch.setattr(input_adapter, "sys", type("S", (), {"platform": "darwin"}))
    assert PynputAdapter(mouse_controller=_Mouse())._text_input_scope is macos_input_source.ascii_input_source
    assert PynputAdapter(mouse_controller=_Mouse(), keyboard_controller=_Keyboard())._text_input_scope \
        is contextlib.nullcontext
