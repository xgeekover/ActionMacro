"""ActionMacro real-device check for macOS. It moves the REAL mouse and presses REAL keys.

The automated test suite runs offscreen with faked pynput/mss, so it cannot see what only
happens on a real desktop. On 2026-10-24 this check found two crashes the suite never could:
the app was killed with SIGTRAP on the first click/key step and again when the emergency
hotkey listener started (pynput reading the keyboard layout off the main thread). Run it
after touching input, hotkeys, the picker or the engine's threading.

Usage (about 20 seconds; keep hands off the mouse and keyboard)::

    .venv/bin/python tools/device_check_macos.py [--report PATH] [--timeout S]
    .venv/bin/python tools/device_check_macos.py --stop-key-trials 10

What it checks, through the production assembly (engines without injected drivers,
started with ``start_async()`` on the GUI thread like ``MainWindow``):

- click precision (<= 1 px) on every display, including negative coordinates;
- double click, emergency stop during a multi-click / long click hold / long key hold;
- the real ``MainWindow``: Start button -> click, with the real global hotkey listener;
- key chords (shift+a, cmd_or_ctrl+a, down/up, legacy ``modifiers`` field), verified on
  the key events themselves, so a Korean/Japanese IME does not change the verdict;
- a text step (``action: "type"``) types its text into the field exactly, Latin and
  Korean text; judged on the field's text;
- with ``--ime``: the same text steps while the Korean 2-Set input source is selected
  (switched for the check and switched back to the original source afterwards); the
  step must type the text as written and leave Korean 2-Set selected afterwards;
- the coordinate picker on every display;
- that the global hotkey listener's event tap is alive.

Safety:

- Every click lands on a window of this process: right before it, the front-most window at
  that point must belong to this PID (Dock / Notification Center click-through overlays are
  ignored, so targets stay 200 px above the bottom edge). Otherwise the step is skipped.
- Keys are sent only while this process is the front-most app and the check's line edit has
  focus; this is re-checked before every key step.
- No screen capture (the picker's magnifier snapshot is disabled): no Screen Recording prompt.
- Key events are recorded by a listen-only event tap that keeps only events posted by this
  process; the user's own typing is never recorded.
- A watchdog releases every held input and exits after ``--timeout`` seconds (default 120).
  The cursor is moved back where it was.

Permissions for the terminal app running it: Accessibility and Input Monitoring.

``--text-only`` runs only the text-step checks (with ``--ime``, also the input-source one).

Exit codes: 0 all steps passed or skipped, 1 a step failed, 3 watchdog. A crash shows up as
133 (SIGTRAP: see ~/Library/Logs/DiagnosticReports/python3*.ips) or 139 (SIGSEGV: the
faulthandler dump is written next to the report as ``<report>.crash.txt``).
"""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import json
import os
import random
import sys
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

if sys.platform != "darwin":
    sys.exit("device_check_macos.py runs on macOS only (it drives the real mouse and keyboard through Quartz).")

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

import Quartz  # noqa: E402
from AppKit import NSWorkspace  # noqa: E402
from PySide6.QtCore import QObject, Qt, Signal, Slot  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget  # noqa: E402

from action_macro.core.models import (  # noqa: E402
    ActionBlock,
    ActionType,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.engine.input_adapter import PynputAdapter  # noqa: E402
from action_macro.gui import coordinate_picker as cp  # noqa: E402
from action_macro.runner.execution_engine import ExecutionEngine  # noqa: E402
from action_macro.runner.hotkey_listener import GlobalHotkeyListener  # noqa: E402

PID = os.getpid()
REPORT: Dict[str, Any] = {"pid": PID, "steps": []}
REPORT_PATH = "device_check_report.json"
T0 = time.perf_counter()


def now() -> float:
    return time.perf_counter()


def save_report() -> None:
    with open(REPORT_PATH, "w") as fh:
        json.dump(REPORT, fh, ensure_ascii=False, indent=1, default=str)


def step(name: str, ok: Optional[bool], **detail: Any) -> None:
    """Records one verdict (True pass, False fail, None skipped) and saves the report at once."""
    REPORT["steps"].append({"step": name, "ok": ok, **detail})
    mark = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{mark}] {name} :: {json.dumps(detail, ensure_ascii=False, default=str)}", flush=True)
    save_report()


# --------------------------------------------------------------------------- input sources (TIS)

_CARBON = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
_CF = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
for _name, _res, _args in (
    ("TISCopyCurrentKeyboardInputSource", ctypes.c_void_p, []),
    ("TISCreateInputSourceList", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_bool]),
    ("TISGetInputSourceProperty", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
    ("TISSelectInputSource", ctypes.c_int32, [ctypes.c_void_p]),
):
    getattr(_CARBON, _name).restype, getattr(_CARBON, _name).argtypes = _res, _args
for _name, _res, _args in (
    ("CFArrayGetCount", ctypes.c_long, [ctypes.c_void_p]),
    ("CFArrayGetValueAtIndex", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_long]),
    ("CFRelease", None, [ctypes.c_void_p]),
    ("CFStringGetCString", ctypes.c_bool, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
):
    getattr(_CF, _name).restype, getattr(_CF, _name).argtypes = _res, _args
_TIS_ID = ctypes.c_void_p.in_dll(_CARBON, "kTISPropertyInputSourceID")
KOREAN_2SET = "com.apple.inputmethod.Korean.2SetKorean"


def _source_id(source: int) -> str:
    ref = _CARBON.TISGetInputSourceProperty(source, _TIS_ID)
    buf = ctypes.create_string_buffer(512)
    return buf.value.decode() if ref and _CF.CFStringGetCString(ref, buf, 512, 0x08000100) else ""


def current_input_source() -> str:
    """ID of the selected keyboard input source. Main thread only (HIToolbox)."""
    source = _CARBON.TISCopyCurrentKeyboardInputSource()
    try:
        return _source_id(source)
    finally:
        _CF.CFRelease(source)


def select_input_source(source_id: str) -> bool:
    """Selects an enabled input source by ID. Main thread only (HIToolbox)."""
    sources = _CARBON.TISCreateInputSourceList(None, False)
    try:
        for i in range(_CF.CFArrayGetCount(sources)):
            source = _CF.CFArrayGetValueAtIndex(sources, i)
            if _source_id(source) == source_id:
                return _CARBON.TISSelectInputSource(source) == 0
        return False
    finally:
        _CF.CFRelease(sources)


# --------------------------------------------------------------------------- safety checks

#: System overlays that cover a whole display but let clicks through (Dock 20, Notification
#: Center 21). Menu bar / status items (24/25) stay blockers; targets never sit there.
CLICK_THROUGH_LAYERS = {20, 21}


def window_owner_at(x: float, y: float) -> Tuple[Optional[int], Optional[str], Optional[int]]:
    """Front-most on-screen window containing (x, y): (owner pid, owner name, layer)."""
    infos = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    for w in infos or []:
        b = w.get("kCGWindowBounds")
        if not b or float(w.get("kCGWindowAlpha", 1.0)) == 0.0:
            continue
        if int(w.get("kCGWindowLayer", 0)) in CLICK_THROUGH_LAYERS:
            continue
        bx, by, bw, bh = b["X"], b["Y"], b["Width"], b["Height"]
        if bx <= x < bx + bw and by <= y < by + bh:
            return int(w.get("kCGWindowOwnerPID", -1)), w.get("kCGWindowOwnerName"), int(w.get("kCGWindowLayer", 0))
    return None, None, None


def our_window_bounds() -> List[Tuple[int, int, int, int, int]]:
    infos = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
    out = []
    for w in infos or []:
        if int(w.get("kCGWindowOwnerPID", -1)) == PID:
            b = w["kCGWindowBounds"]
            out.append((int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"]), int(w.get("kCGWindowLayer", 0))))
    return out


def ours_at(x: float, y: float) -> bool:
    return window_owner_at(x, y)[0] == PID


def we_are_frontmost() -> bool:
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return app is not None and int(app.processIdentifier()) == PID


def left_button_down() -> bool:
    return bool(Quartz.CGEventSourceButtonState(Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGMouseButtonLeft))


def key_down(keycode: int) -> bool:
    return bool(Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, keycode))


KC_SHIFT = 56
KEYCODES = {0: "a", 11: "b", 8: "c", 2: "d", 14: "e", 56: "shift", 60: "shift", 55: "cmd", 54: "cmd",
            59: "ctrl", 62: "ctrl", 58: "alt", 61: "alt"}
MOD_FLAGS = {"shift": Quartz.kCGEventFlagMaskShift, "cmd": Quartz.kCGEventFlagMaskCommand,
             "ctrl": Quartz.kCGEventFlagMaskControl, "alt": Quartz.kCGEventFlagMaskAlternate}


class KeyTap:
    """Listen-only session event tap recording key events posted by THIS process only.

    The keyboard layout / IME (e.g. Korean 2-set turns the 'a' key into 'ㅁ') changes the
    text a key produces, not the key events, so chords are verified here. Events from other
    sources (the user's own typing) are dropped by their source PID and never recorded.
    """

    def __init__(self) -> None:
        self.events: List[Tuple[float, str, str]] = []
        self.lock = threading.Lock()
        self.loop: Any = None
        self.tap: Any = None
        self.ok = False

    def _cb(self, proxy: Any, etype: int, event: Any, refcon: Any) -> Any:
        if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self.tap, True)
            return event
        if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID) != PID:
            return event
        kc = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
        name = KEYCODES.get(kc, f"kc{kc}")
        if etype == Quartz.kCGEventKeyDown:
            kind = "down"
        elif etype == Quartz.kCGEventKeyUp:
            kind = "up"
        else:  # flagsChanged: a modifier went down if its flag is now set
            flag = MOD_FLAGS.get(name)
            kind = "down" if flag and (Quartz.CGEventGetFlags(event) & flag) else "up"
        with self.lock:
            self.events.append((now(), kind, name))
        return event

    def start(self) -> bool:
        ready = threading.Event()

        def _run() -> None:
            mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                    | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
            self.tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                               Quartz.kCGEventTapOptionListenOnly, mask, self._cb, None)
            if not self.tap:
                ready.set()
                return
            src = Quartz.CFMachPortCreateRunLoopSource(None, self.tap, 0)
            self.loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(self.loop, src, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(self.tap, True)
            self.ok = True
            ready.set()
            Quartz.CFRunLoopRun()

        threading.Thread(target=_run, name="KeyTap", daemon=True).start()
        ready.wait(3.0)
        return self.ok

    def mark(self) -> int:
        with self.lock:
            return len(self.events)

    def since(self, n: int) -> List[Tuple[str, str]]:
        with self.lock:
            return [(k, name) for _, k, name in self.events[n:]]

    def timed_since(self, n: int) -> List[Tuple[float, str, str]]:
        with self.lock:
            return list(self.events[n:])


TAP = KeyTap()

#: The engine's end-of-run safety flush releases every modifier, pressed or not.
FLUSH_TAIL = [("up", "ctrl"), ("up", "alt"), ("up", "shift"), ("up", "cmd")]


def strip_flush(events: List[Tuple[str, str]]) -> Tuple[List[Tuple[str, str]], int]:
    flushes = 0
    while len(events) >= 4 and events[-4:] == FLUSH_TAIL:
        events = events[:-4]
        flushes += 1
    return events, flushes


def chord(*names: str) -> List[Tuple[str, str]]:
    """Expected events of one chord: press in order, release in reverse."""
    return [("down", n) for n in names] + [("up", n) for n in reversed(names)]


# --------------------------------------------------------------------------- GUI side

class Bridge(QObject):
    """Runs callables on the GUI thread for the controller thread."""

    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, job: Any) -> None:
        fn, done, box = job
        try:
            box["r"] = fn()
        except Exception as err:  # reported to the controller
            box["e"] = err
        done.set()


BRIDGE: Optional[Bridge] = None


def gui(fn: Callable[[], Any], timeout: float = 5.0) -> Any:
    done = threading.Event()
    box: Dict[str, Any] = {}
    assert BRIDGE is not None
    BRIDGE.call.emit((fn, done, box))
    if not done.wait(timeout):
        raise TimeoutError("GUI call timed out")
    if "e" in box:
        raise box["e"]
    return box.get("r")


class ClickTarget(QWidget):
    """Frameless always-on-top pad that records every mouse event with its global position."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("ActionMacro device check — click pad")
        self.events: List[Tuple[float, str, float, float]] = []
        self.lock = threading.Lock()

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#16324a"))
        p.setPen(QColor("white"))
        p.setFont(QFont("Arial", 18))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                   "ActionMacro device check — hands off the mouse and keyboard\n"
                   "실기기 점검 중 — 마우스·키보드를 만지지 마세요")

    def _rec(self, kind: str, e: Any) -> None:
        g = e.globalPosition()
        with self.lock:
            self.events.append((now(), kind, g.x(), g.y()))

    def mousePressEvent(self, e: Any) -> None:
        self._rec("press", e)

    def mouseReleaseEvent(self, e: Any) -> None:
        self._rec("release", e)

    def mouseDoubleClickEvent(self, e: Any) -> None:
        self._rec("dblclick", e)

    def snapshot(self) -> List[Tuple[float, str, float, float]]:
        with self.lock:
            return list(self.events)


class KeyPad(QWidget):
    """Always-on-top window whose line edit receives the key steps."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("ActionMacro device check — keys")
        self.edit = QLineEdit()
        self.edit.setFont(QFont("Menlo", 20))
        QVBoxLayout(self).addWidget(self.edit)


# --------------------------------------------------------------------------- controller helpers

ADAPTER: Optional[PynputAdapter] = None  # built on the main thread in main()
#: Qt widgets created for a step. Kept alive until exit: dropping the last Python reference
#: on the controller thread would delete the C++ widget off the GUI thread (SIGSEGV).
KEEP: List[Any] = []


def engine_for(*blocks: ActionBlock, loops: int = 1) -> ExecutionEngine:
    """The production assembly: no injected drivers (the engine builds PynputAdapter itself)."""
    return ExecutionEngine(scenario=ScenarioModel(name="device-check", loop_count=loops, actions=list(blocks)))


def start(eng: ExecutionEngine) -> None:
    """Starts like MainWindow does: start_async() on the GUI thread (input driver built there)."""
    gui(lambda: eng.start_async())


def run(eng: ExecutionEngine, timeout: float = 15.0) -> bool:
    start(eng)
    eng.wait_for_completion(timeout=timeout)
    return eng.state.name in ("STOPPED", "IDLE")


def click_block(x: int, y: int, **kw: Any) -> ActionBlock:
    return ActionBlock(type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=x, y=y, **kw))


def key_block(keys: List[str], action: str = "press", **kw: Any) -> ActionBlock:
    return ActionBlock(type=ActionType.KEY_INPUT, params=KeyInputParams(keys=keys, action=action, **kw))


def wait_until(pred: Callable[[], bool], timeout: float, interval: float = 0.01) -> bool:
    end = now() + timeout
    while now() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def displays() -> List[Dict[str, Any]]:
    _err, ids, n = Quartz.CGGetActiveDisplayList(16, None, None)
    main_id = Quartz.CGMainDisplayID()
    out = []
    for d in list(ids)[:n]:
        b = Quartz.CGDisplayBounds(d)
        out.append({"id": int(d), "main": d == main_id, "x": int(b.origin.x), "y": int(b.origin.y),
                    "w": int(b.size.width), "h": int(b.size.height)})
    return out


def main_display() -> Dict[str, Any]:
    return next(d for d in displays() if d["main"])


def place(widget: QWidget, x: int, y: int, w: int, h: int) -> None:
    def _do() -> None:
        widget.setGeometry(x, y, w, h)
        widget.show()
        widget.raise_()
    gui(_do)
    time.sleep(0.5)


def lower_middle(d: Dict[str, Any], w: int, h: int) -> Tuple[int, int]:
    """Top-left of a w x h pad in the lower middle of a display.

    Clear of alert dialogs that sit near the centre, and 200 px above the bottom edge so a
    Dock (layer 20, ignored by ``ours_at``) is never under a target.
    """
    return d["x"] + (d["w"] - w) // 2, d["y"] + d["h"] - h - 200


def centred(d: Dict[str, Any], w: int, h: int) -> Tuple[int, int]:
    return d["x"] + (d["w"] - w) // 2, d["y"] + (d["h"] - h) // 2


# --------------------------------------------------------------------------- checks

def check_click_precision(pad: ClickTarget) -> None:
    rng = random.Random(20261024)
    for d in displays():
        w, h = 700, 300
        x0, y0 = lower_middle(d, w, h)
        place(pad, x0, y0, w, h)
        label = f"display {d['id']}{'(main)' if d['main'] else ''} origin=({d['x']},{d['y']})"
        cx, cy = x0 + w // 2, y0 + h // 2
        if not ours_at(cx, cy):
            step(f"click precision — {label}", None, reason="click pad not front-most", owner=window_owner_at(cx, cy))
            continue
        run(engine_for(click_block(cx, cy)))  # warm-up: the first click may only activate the app
        time.sleep(0.2)
        errors, misses, results = [], 0, []
        for _ in range(10):
            tx = rng.randint(x0 + 20, x0 + w - 21)
            ty = rng.randint(y0 + 20, y0 + h - 21)
            if not ours_at(tx, ty):
                step(f"click precision — {label}", None, reason="target not ours", target=(tx, ty),
                     owner=window_owner_at(tx, ty), our_windows=our_window_bounds())
                return
            before = len(pad.snapshot())
            eng = engine_for(click_block(tx, ty))
            ok = run(eng)
            got = wait_until(lambda: any(e[1] == "press" for e in pad.snapshot()[before:]), 1.0)
            pos = eng.input_driver.get_position() if eng.input_driver else None
            if not (ok and got):
                misses += 1
                results.append({"target": (tx, ty), "received": None})
                continue
            press = next(e for e in pad.snapshot()[before:] if e[1] == "press")
            errors.append(max(abs(press[2] - tx), abs(press[3] - ty)))
            results.append({"target": (tx, ty), "received": (press[2], press[3]), "cursor": pos})
        worst = max(errors) if errors else None
        step(f"click precision — {label}", misses == 0 and worst is not None and worst <= 1,
             clicks=len(results), missed=misses, worst_error_px=worst,
             negative_x=any(r["target"][0] < 0 for r in results), sample=results[:3])


def check_double_click(pad: ClickTarget) -> None:
    w, h = 700, 450
    x0, y0 = centred(main_display(), w, h)
    place(pad, x0, y0, w, h)
    cx, cy = x0 + w // 2, y0 + h // 2
    if not ours_at(cx, cy):
        step("double click (clicks=2)", None, reason="pad not front-most")
        return
    time.sleep(0.6)  # apart from earlier clicks
    before = len(pad.snapshot())
    run(engine_for(click_block(cx, cy, clicks=2, interval_ms=80)))
    time.sleep(0.4)
    ev = pad.snapshot()[before:]
    # Qt recognises it; native apps that read kCGMouseEventClickState may still see two singles.
    step("double click (clicks=2) — Qt sees a double click", any(e[1] == "dblclick" for e in ev),
         presses=sum(1 for e in ev if e[1] == "press"), dblclick_events=sum(1 for e in ev if e[1] == "dblclick"))


def check_stop_multi_click(pad: ClickTarget) -> None:
    w, h = 700, 450
    x0, y0 = centred(main_display(), w, h)
    place(pad, x0, y0, w, h)
    cx, cy = x0 + w // 2, y0 + h // 2
    if not ours_at(cx, cy):
        step("emergency stop during multi-click", None, reason="pad not front-most")
        return
    time.sleep(0.6)
    before = len(pad.snapshot())
    eng = engine_for(click_block(cx, cy, clicks=10, interval_ms=300))
    start(eng)
    wait_until(lambda: sum(1 for e in pad.snapshot()[before:] if e[1] == "press") >= 2, 3.0)
    t_stop = now()
    eng.stop()
    eng.wait_for_completion(timeout=3.0)
    t_exit = now()
    time.sleep(0.6)
    ev = pad.snapshot()[before:]
    late = [e for e in ev if e[1] == "press" and e[0] > t_stop + 0.05]
    step("emergency stop during multi-click (clicks=10, 300ms)",
         not late and eng.wait_for_completion(timeout=0) and not left_button_down(),
         presses_total=sum(1 for e in ev if e[1] == "press"), presses_after_stop_plus_50ms=len(late),
         worker_exit_ms=round((t_exit - t_stop) * 1000, 1), left_button_still_down=left_button_down())


def check_stop_long_click_hold(pad: ClickTarget) -> None:
    w, h = 700, 450
    x0, y0 = centred(main_display(), w, h)
    place(pad, x0, y0, w, h)
    cx, cy = x0 + w // 2, y0 + h // 2
    if not ours_at(cx, cy):
        step("emergency stop during long click hold", None, reason="pad not front-most")
        return
    time.sleep(0.6)
    before = len(pad.snapshot())
    eng = engine_for(click_block(cx, cy, press_duration_ms=3000))
    start(eng)
    wait_until(lambda: any(e[1] == "press" for e in pad.snapshot()[before:]), 2.0)
    time.sleep(0.2)
    t_stop = now()
    eng.stop()
    released = wait_until(lambda: any(e[1] == "release" for e in pad.snapshot()[before:]), 2.0)
    rel = next((e for e in pad.snapshot()[before:] if e[1] == "release"), None)
    eng.wait_for_completion(timeout=2.0)
    step("emergency stop during 3s click hold — button released",
         released and rel is not None and rel[0] - t_stop < 0.1 and not left_button_down(),
         release_after_stop_ms=round((rel[0] - t_stop) * 1000, 1) if rel else None,
         left_button_still_down=left_button_down())


def check_real_app_end_to_end(pad: ClickTarget) -> None:
    """The real MainWindow, its real Start button and real global hotkey listener, clicking the pad."""
    from action_macro.gui.main_window import MainWindow

    d = main_display()
    w, h = 700, 300
    x0, y0 = lower_middle(d, w, h)
    place(pad, x0, y0, w, h)
    tx, ty = x0 + w // 2 + 41, y0 + h // 2 - 17
    holder: Dict[str, Any] = {}

    def build() -> None:
        win = MainWindow()
        win.resize(1000, 650)
        win.move(d["x"] + 80, d["y"] + 80)
        win.show()
        block = win.scenario_builder.add_action_block(ActionType.MOUSE_CLICK)
        win.scenario_builder.select_action(block.id)
        form = win.property_inspector.form_mouse
        form.spin_x.setValue(tx)
        form.spin_y.setValue(ty)
        win.scenario_builder.add_action_block(ActionType.FIXED_WAIT)  # keeps the run going a moment
        win.dashboard.set_loop_count(1)
        holder["win"] = win
        KEEP.append(win)
    gui(build)
    time.sleep(0.8)
    gui(lambda: pad.raise_())
    time.sleep(0.3)
    if not ours_at(tx, ty) or window_owner_at(tx, ty)[2] not in (3, 8):  # the floating pad, not MainWindow
        step("real app end-to-end (MainWindow → Start → click)", None, reason="pad not front-most",
             owner=window_owner_at(tx, ty))
        gui(lambda: holder["win"].hide())
        return
    before = len(pad.snapshot())
    gui(lambda: holder["win"].dashboard.btn_start.click())
    eng = gui(lambda: holder["win"].engine)
    time.sleep(0.4)  # mid-run: the listener thread has entered pynput's _run by now
    lst = gui(lambda: getattr(holder["win"], "hotkey_listener", None))
    native = getattr(lst, "_native_listener", None) if lst is not None else None
    listener_alive = bool(native is not None and native.is_alive())
    finished = eng.wait_for_completion(timeout=8.0) if eng is not None else False
    wait_until(lambda: any(e[1] == "press" for e in pad.snapshot()[before:]), 2.0)
    press = next((e for e in pad.snapshot()[before:] if e[1] == "press"), None)
    step("real app end-to-end (MainWindow → Start → click), no crash",
         bool(finished and press and abs(press[2] - tx) <= 1 and abs(press[3] - ty) <= 1 and listener_alive),
         target=(tx, ty), received=(press[2], press[3]) if press else None,
         engine_state=str(getattr(eng, "state", None)), global_hotkey_listener_alive_mid_run=listener_alive)
    gui(lambda: holder["win"].hide())


def focus_keypad(kp: KeyPad) -> bool:
    d = main_display()
    place(kp, d["x"] + d["w"] // 2 - 300, d["y"] + d["h"] // 2 + 260, 600, 90)
    geo = gui(lambda: kp.edit.mapToGlobal(kp.edit.rect().center()))
    ex, ey = geo.x(), geo.y()
    if not ours_at(ex, ey):
        step("key pad focus", None, reason="key pad not front-most", owner=window_owner_at(ex, ey))
        return False
    assert ADAPTER is not None
    ADAPTER.click(ex, ey)
    focused = wait_until(lambda: we_are_frontmost() and gui(lambda: QApplication.focusWidget() is kp.edit), 2.0, 0.05)
    step("key pad focus (app front-most, line edit focused)", focused, frontmost=we_are_frontmost())
    return focused


def keys_safe(kp: KeyPad) -> bool:
    return we_are_frontmost() and gui(lambda: QApplication.focusWidget() is kp.edit)


def check_key_chords(kp: KeyPad) -> None:
    if not TAP.ok:
        step("key event tap", False, reason="CGEventTapCreate failed (Input Monitoring permission?)")
        return
    if not focus_keypad(kp):
        return
    gui(lambda: kp.edit.clear())

    def expect(name: str, blocks: List[ActionBlock], expected: List[Tuple[str, str]]) -> None:
        if not keys_safe(kp):
            step(name, None, reason="focus left the key pad — key input skipped")
            return
        n = TAP.mark()
        run(engine_for(*blocks))
        time.sleep(0.25)
        own, flushes = strip_flush(TAP.since(n))
        step(name, own == expected, events=own, expected=expected, end_of_run_flushes=flushes,
             field_text=gui(lambda: kp.edit.text()), shift_still_down=key_down(KC_SHIFT))

    expect("chord 'shift+a' — shift held while a is pressed", [key_block(["shift", "a"])], chord("shift", "a"))
    expect("down shift → b → up shift (one scenario)",
           [key_block(["shift"], action="down"), key_block(["b"]), key_block(["shift"], action="up")],
           [("down", "shift"), ("down", "b"), ("up", "b"), ("up", "shift")])
    expect("chord 'cmd_or_ctrl+a' — cmd held while a is pressed", [key_block(["cmd_or_ctrl", "a"])], chord("cmd", "a"))
    sel, full = gui(lambda: (kp.edit.selectedText(), kp.edit.text()))
    step("chord 'cmd_or_ctrl+a' really selected all in the field", bool(full) and sel == full, selected=sel, text=full)
    expect("legacy file modifiers=['shift'] + keys=['d']",
           [ActionBlock(type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["d"], modifiers=["shift"]))],
           chord("shift", "d"))


#: Typed by the text step: upper case, digits, a space and symbols that need Shift on a US layout.
TYPED_TEXT = "Ab1 x_Y!@"
#: Korean syllables, a Latin user name and digits in one text.
TYPED_KOREAN = "안녕하세요 홍길동 id123"


def type_into_keypad(kp: KeyPad, name: str, text: str, **detail: Any) -> None:
    """Runs one text step into the cleared key pad and judges the field's text."""
    if not keys_safe(kp) and not focus_keypad(kp):
        return
    gui(lambda: kp.edit.clear())
    if not keys_safe(kp):
        step(name, None, reason="focus left the key pad — key input skipped")
        return
    run(engine_for(ActionBlock(type=ActionType.KEY_INPUT,
                               params=KeyInputParams(keys=[], action="type", text=text))))
    time.sleep(0.4)
    field = gui(lambda: kp.edit.text())
    preedit = gui(lambda: kp.edit.inputMethodQuery(Qt.InputMethodQuery.ImSurroundingText))
    # The step selects an ASCII input source while typing and must select the user's again.
    source_after = gui(current_input_source)
    restored = "input_source" not in detail or source_after == detail["input_source"]
    step(name, field == text and restored, field_text=field, expected=text, surrounding_text=str(preedit),
         source_after=source_after, shift_still_down=key_down(KC_SHIFT), **detail)


def check_type_text(kp: KeyPad) -> None:
    source = gui(current_input_source)
    type_into_keypad(kp, f"text step types {TYPED_TEXT!r} into the field", TYPED_TEXT, input_source=source)
    type_into_keypad(kp, f"text step types {TYPED_KOREAN!r} into the field", TYPED_KOREAN, input_source=source)


def check_type_text_korean_ime(kp: KeyPad) -> None:
    """The text steps again while the Korean 2-Set input source is selected; restores the source."""
    original = gui(current_input_source)
    if not gui(lambda: select_input_source(KOREAN_2SET)):
        step("text step with Korean 2-Set selected", None, reason=f"{KOREAN_2SET} is not enabled",
             original_source=original)
        return
    try:
        if not wait_until(lambda: gui(current_input_source) == KOREAN_2SET, 2.0, 0.05):
            step("text step with Korean 2-Set selected", None, reason="input source did not switch",
                 current=gui(current_input_source))
            return
        for text in ("myid123", TYPED_TEXT, TYPED_KOREAN):
            type_into_keypad(kp, f"[Korean 2-Set] text step types {text!r}", text, input_source=KOREAN_2SET)
    finally:
        gui(lambda: select_input_source(original))
        restored = wait_until(lambda: gui(current_input_source) == original, 2.0, 0.05)
        step("input source restored", restored, original=original, current=gui(current_input_source))


def stop_during_key_hold(kp: KeyPad, hold_ms: int = 3000, pause_s: float = 0.2) -> Dict[str, Any]:
    """Stops a 'shift+e' hold and returns the key timeline relative to stop()."""
    n = TAP.mark()
    eng = engine_for(key_block(["shift", "e"], press_duration_ms=hold_ms))
    start(eng)
    wait_until(lambda: ("down", "e") in TAP.since(n), 2.0)
    time.sleep(pause_s)
    t_stop = now()
    eng.stop()
    wait_until(lambda: ("up", "e") in TAP.since(n) and ("up", "shift") in TAP.since(n), 2.0)
    eng.wait_for_completion(timeout=2.0)
    shift_after_done = key_down(KC_SHIFT)
    time.sleep(0.3)
    ev = TAP.timed_since(n)
    ups = {name: t for t, k, name in ev if k == "up" and t >= t_stop}
    return {
        "e_release_ms": round((ups["e"] - t_stop) * 1000, 1) if "e" in ups else None,
        "shift_release_ms": round((ups["shift"] - t_stop) * 1000, 1) if "shift" in ups else None,
        "presses_after_stop": [name for t, k, name in ev if k == "down" and t > t_stop + 0.05],
        # The session key state can read 'down' for a few ms after our release; 400 ms is the verdict.
        "shift_down_when_done": shift_after_done,
        "shift_down_400ms": key_down(KC_SHIFT),
        "events_ms": [(round((t - t_stop) * 1000, 1), k, name) for t, k, name in ev],
    }


def released_promptly(r: Dict[str, Any]) -> bool:
    return (r["e_release_ms"] is not None and r["shift_release_ms"] is not None
            and max(r["e_release_ms"], r["shift_release_ms"]) < 100
            and not r["presses_after_stop"] and not r["shift_down_400ms"])


def check_stop_long_key_hold(kp: KeyPad) -> None:
    if not TAP.ok or (not keys_safe(kp) and not focus_keypad(kp)):
        return
    r = stop_during_key_hold(kp)
    r.pop("events_ms")
    step("emergency stop during 3s 'shift+e' hold — both keys released", released_promptly(r), **r)


def repeat_stop_key_hold(kp: KeyPad, trials: int) -> None:
    if not TAP.ok or not focus_keypad(kp):
        return
    failures = 0
    for i in range(trials):
        if not keys_safe(kp):
            step(f"stop/key trial {i}", None, reason="focus lost")
            return
        r = stop_during_key_hold(kp, hold_ms=2000, pause_s=0.15 + 0.05 * (i % 4))
        ok = released_promptly(r)
        failures += 0 if ok else 1
        step(f"stop/key trial {i}", ok, **r)
    step(f"stop during key hold — {trials} trials", failures == 0, trials=trials, failures=failures)


def check_picker() -> None:
    cp.CoordinatePickerOverlay._capture_screen_snapshot = lambda self: None  # no screen capture
    for d in displays():
        tx, ty = d["x"] + d["w"] // 2 + 37, d["y"] + d["h"] // 2 + 23
        box: Dict[str, Any] = {}
        holder: Dict[str, Any] = {}

        def _open() -> None:
            ov = cp.CoordinatePickerOverlay(callback=lambda x, y: box.setdefault("sel", (x, y)),
                                            cancel_callback=lambda: box.setdefault("cancel", True))
            holder["ov"] = ov
            KEEP.append(ov)
            ov.start()
        gui(_open)
        time.sleep(0.8)
        label = f"display {d['id']}{'(main)' if d['main'] else ''}"
        if not ours_at(tx, ty):
            step(f"coordinate picker — {label}", None, reason="overlay not front-most", owner=window_owner_at(tx, ty))
            gui(lambda: holder["ov"].close_all())
            continue
        assert ADAPTER is not None
        ADAPTER.click(tx, ty)
        wait_until(lambda: "sel" in box or "cancel" in box, 2.0)
        step(f"coordinate picker — {label}", box.get("sel") == (tx, ty), target=(tx, ty), picked=box.get("sel"))
        try:
            gui(lambda: holder["ov"].close_all())
        except Exception:
            pass
        time.sleep(0.3)


def check_listener_health() -> None:
    lst = GlobalHotkeyListener(on_emergency_stop=lambda: None, headless=False)
    started = gui(lambda: lst.start())  # MainWindow starts it on the GUI thread
    time.sleep(0.7)
    native = getattr(lst, "_native_listener", None)
    alive = bool(native is not None and native.is_alive())
    step("global hotkey listener — event tap alive", started and alive, start_returned=started,
         native_thread_alive=alive, pynput_IS_TRUSTED=getattr(native, "IS_TRUSTED", None) if native else None)
    lst.stop()


# --------------------------------------------------------------------------- main

def controller(app: QApplication, pad: ClickTarget, kp: KeyPad, start_pos: Tuple[int, int],
               stop_key_trials: int, text_only: bool = False, ime: bool = False) -> None:
    def guarded(check: Callable[..., None], *args: Any) -> None:
        try:
            check(*args)
        except Exception as err:
            step(check.__name__, False, error=repr(err), tb=traceback.format_exc()[-800:])

    try:
        time.sleep(1.0)
        REPORT["displays"] = displays()
        if stop_key_trials:
            guarded(repeat_stop_key_hold, kp, stop_key_trials)
            return
        if text_only:
            guarded(check_type_text, kp)
            if ime:
                guarded(check_type_text_korean_ime, kp)
            return
        for check in (check_click_precision, check_double_click, check_stop_multi_click,
                      check_stop_long_click_hold, check_real_app_end_to_end):
            guarded(check, pad)
        gui(lambda: pad.hide())
        for check in (check_key_chords, check_type_text, check_stop_long_key_hold):
            guarded(check, kp)
        if ime:
            guarded(check_type_text_korean_ime, kp)
        gui(lambda: kp.hide())
        guarded(check_picker)
        guarded(check_listener_health)
    finally:
        try:
            assert ADAPTER is not None
            ADAPTER.safety_flush()
            ADAPTER.move_to(*start_pos)
        except Exception:
            pass
        REPORT["elapsed_s"] = round(now() - T0, 1)
        gui(app.quit)


def main(argv: Optional[List[str]] = None) -> int:
    global BRIDGE, ADAPTER, REPORT_PATH
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", default="device_check_report.json", help="JSON report path")
    parser.add_argument("--timeout", type=float, default=120.0, help="watchdog seconds (releases inputs, exits 3)")
    parser.add_argument("--stop-key-trials", type=int, default=0, metavar="N",
                        help="only repeat the stop-during-key-hold check N times")
    parser.add_argument("--text-only", action="store_true", help="only the text-step checks")
    parser.add_argument("--ime", action="store_true",
                        help="also type with the Korean 2-Set input source selected (restored afterwards)")
    args = parser.parse_args(argv)
    REPORT_PATH = args.report
    faulthandler.enable(open(REPORT_PATH + ".crash.txt", "w"), all_threads=True)

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)  # the check hides all its windows between phases
    BRIDGE = Bridge()
    ADAPTER = PynputAdapter()  # the main thread: pynput reads the keyboard layout here
    pad, kp = ClickTarget(), KeyPad()
    start_pos = ADAPTER.get_position()

    def watchdog() -> None:
        time.sleep(args.timeout)
        print("WATCHDOG: releasing inputs and aborting", flush=True)
        try:
            if ADAPTER is not None:
                ADAPTER.safety_flush()
        finally:
            REPORT["watchdog"] = True
            save_report()
            os._exit(3)

    TAP.start()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=controller, args=(app, pad, kp, start_pos, args.stop_key_trials, args.text_only, args.ime),
                     daemon=True).start()
    app.exec()
    save_report()
    fails = [s for s in REPORT["steps"] if s["ok"] is False]
    skips = [s for s in REPORT["steps"] if s["ok"] is None]
    print(f"done in {REPORT.get('elapsed_s')}s — {len(REPORT['steps'])} steps, "
          f"{len(fails)} failed, {len(skips)} skipped — report: {REPORT_PATH}", flush=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
