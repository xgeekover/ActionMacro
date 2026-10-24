"""
tests.unit.test_macos_keyboard
==============================
The keyboard-layout cache that keeps pynput's TSM calls on the main thread (macOS).

On the device, pynput's keyboard Listener thread called TSMGetInputSourceProperty and
macOS killed the GUI with SIGTRAP as soon as the emergency hotkey listener started.
Offscreen tests cannot reproduce the trap, so these pin the mechanism instead: the
layout is read on the main thread only, and every later pynput lookup — from any
thread — reuses it.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import types
from typing import Any, Iterator, List

import pytest

from action_macro.engine import macos_keyboard
from action_macro.runner.hotkey_listener import GlobalHotkeyListener


@pytest.fixture
def fake_pynput_darwin(monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Stands in for pynput._util.darwin / pynput.keyboard._darwin and records TSM reads."""
    # Initialise the real packages first, so swapping their darwin submodules below does
    # not make pynput.keyboard re-import its backend from the fake.
    try:
        import pynput._util as pynput_util
        import pynput.keyboard as pynput_keyboard
    except Exception as err:  # pragma: no cover - no usable pynput backend on this host
        pytest.skip(f"pynput unavailable: {err}")
    reads: List[str] = []
    layouts = iter([(40, b"layout-1"), (40, b"layout-2"), (40, b"layout-3")])

    @contextlib.contextmanager
    def keycode_context() -> Iterator[Any]:
        reads.append(threading.current_thread().name)
        yield next(layouts)

    util = types.ModuleType("pynput._util.darwin")
    util.keycode_context = keycode_context
    kb = types.ModuleType("pynput.keyboard._darwin")
    kb.keycode_context = keycode_context
    monkeypatch.setitem(sys.modules, "pynput._util.darwin", util)
    monkeypatch.setitem(sys.modules, "pynput.keyboard._darwin", kb)
    monkeypatch.setattr(pynput_util, "darwin", util, raising=False)
    monkeypatch.setattr(pynput_keyboard, "_darwin", kb, raising=False)
    monkeypatch.setattr(macos_keyboard, "_cached_context", None)
    monkeypatch.setattr(macos_keyboard, "_original_keycode_context", None)
    return types.SimpleNamespace(util=util, kb=kb, reads=reads, original=keycode_context)


def _in_worker(fn: Any) -> Any:
    box: dict = {}

    def run() -> None:
        try:
            box["r"] = fn()
        except BaseException as err:  # pragma: no cover - surfaced below
            box["e"] = err

    t = threading.Thread(target=run, name="worker")
    t.start()
    t.join(5)
    if "e" in box:
        raise box["e"]
    return box.get("r")


@pytest.mark.unit
def test_other_platforms_are_left_alone(fake_pynput_darwin: types.SimpleNamespace) -> None:
    assert macos_keyboard.refresh_keyboard_layout_cache(platform="win32") is False
    assert fake_pynput_darwin.util.keycode_context is fake_pynput_darwin.original
    assert fake_pynput_darwin.reads == []


@pytest.mark.unit
def test_layout_is_read_on_main_thread_and_reused_from_any_thread(fake_pynput_darwin: types.SimpleNamespace) -> None:
    assert macos_keyboard.refresh_keyboard_layout_cache(platform="darwin") is True
    assert fake_pynput_darwin.reads == ["MainThread"]

    def lookup() -> Any:
        # What pynput's Listener._run and Controller.__init__ do.
        with fake_pynput_darwin.kb.keycode_context() as a, fake_pynput_darwin.util.keycode_context() as b:
            return a, b

    a, b = _in_worker(lookup)
    assert a == b == (40, b"layout-1")
    assert fake_pynput_darwin.reads == ["MainThread"], "a worker thread must never read the layout itself"


@pytest.mark.unit
def test_refresh_off_main_thread_does_not_read_the_layout(fake_pynput_darwin: types.SimpleNamespace) -> None:
    assert _in_worker(lambda: macos_keyboard.refresh_keyboard_layout_cache(platform="darwin")) is False
    assert fake_pynput_darwin.reads == []
    assert fake_pynput_darwin.util.keycode_context is fake_pynput_darwin.original


@pytest.mark.unit
def test_refresh_again_picks_up_a_new_layout(fake_pynput_darwin: types.SimpleNamespace) -> None:
    macos_keyboard.refresh_keyboard_layout_cache(platform="darwin")
    patched = fake_pynput_darwin.util.keycode_context
    macos_keyboard.refresh_keyboard_layout_cache(platform="darwin")
    assert fake_pynput_darwin.util.keycode_context is patched  # patched once, not stacked
    with patched() as ctx:
        assert ctx == (40, b"layout-2")
    assert fake_pynput_darwin.reads == ["MainThread", "MainThread"]


@pytest.mark.unit
def test_hotkey_listener_caches_layout_before_creating_the_pynput_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[str] = []

    class FakeGlobalHotKeys:
        def __init__(self, hotkeys: Any) -> None:
            calls.append("GlobalHotKeys")
            self.daemon = False

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def is_alive(self) -> bool:
            return True

    import pynput.keyboard

    monkeypatch.setattr(pynput.keyboard, "GlobalHotKeys", FakeGlobalHotKeys)
    monkeypatch.setattr(macos_keyboard, "refresh_keyboard_layout_cache", lambda *a, **k: calls.append("cache") or True)

    listener = GlobalHotkeyListener(on_emergency_stop=lambda: None, headless=False)
    assert listener.start() is True
    listener.stop()
    assert calls == ["cache", "GlobalHotKeys"]
