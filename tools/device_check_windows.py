"""ActionMacro real-device check for Windows. It moves the REAL mouse and presses REAL keys.

The automated test suite runs offscreen with faked pynput/mss, so it cannot see what only
happens on a real desktop: how the Korean IME, Caps Lock and the foreground rules treat the
input the engine sends. Run it after touching input, hotkeys, the picker or the engine's
threading. It mirrors tools/device_check_macos.py.

Usage (about 40 seconds; keep hands off the mouse and keyboard)::

    .venv\\Scripts\\python tools\\device_check_windows.py [--report PATH] [--timeout S]
    .venv\\Scripts\\python tools\\device_check_windows.py --text-only
    .venv\\Scripts\\python tools\\device_check_windows.py --stop-key-trials 10

What it checks, through the production assembly (engines without injected drivers,
started with ``start_async()`` on the GUI thread like ``MainWindow``):

- a text step (``action: "type"``, Send: Text) types its text into a Qt field of this
  process exactly — ``myid123``, ``Ab1 x_Y!@``, ``안녕하세요 홍길동 id123`` and a text with
  line breaks and a tab — judged on the field's text, in four keyboard states: Korean IME
  in English mode, Korean IME in Hangul mode (IME_CMODE_NATIVE), Caps Lock on, and both.
  The step must also leave the IME mode and Caps Lock as they were;
- that the IME mode of a window can be read and set through its default IME window
  (WM_IME_CONTROL), the way another process's window would be;
- (not with ``--text-only``) click precision (<= 1 px) on every monitor, including negative
  coordinates; double click; emergency stop during a multi-click / long click hold / long
  key hold; key chords (shift+a, cmd_or_ctrl+a, down/up, legacy ``modifiers`` field)
  verified on the key events themselves; the real ``MainWindow`` (Start button -> click
  with the real global hotkey listener); the coordinate picker; the hotkey listener's hook;
- (also alone with ``--context-menu-only``) which key steps Windows turns into a context-menu
  request, the "Key Input looks like a right click" report: ``menu`` (VK_APPS) and
  ``shift+f10``. ``cmd+x`` is Win+X there (the Start button's right-click menu); it is not
  sent, because the shell opens that menu outside this process.

Keyboard states: the Hangul mode is switched on the check window's own input context
(ImmSetConversionStatus, falling back to WM_IME_CONTROL on its default IME window). If
neither works, ``--manual-ime`` asks you to press 한/영 once while the check window has
focus. Caps Lock is toggled with an injected Caps Lock key. Both are put back as they were
at the end, also after a failure or the watchdog.

Safety:

- Every click lands on a window of this process: right before it, the top-level window at
  that point (WindowFromPoint -> GetAncestor(GA_ROOT)) must belong to this PID. Otherwise
  the step is skipped. Targets stay inside the monitor's work area (above the taskbar).
- Keys (Caps Lock included) are sent only while this process owns the foreground window
  and the check's field has focus; this is re-checked before every key step.
- No screen capture (the picker's magnifier snapshot is disabled).
- Key events are recorded by a low-level keyboard hook that keeps only injected events
  (LLKHF_INJECTED): what you type yourself is never recorded. Only class names of other
  processes' windows are reported, never their titles.
- A watchdog releases every held input, puts the IME mode and Caps Lock back and exits
  after ``--timeout`` seconds (default 120). The cursor is moved back where it was.

Exit codes: 0 all steps passed or skipped, 1 a step failed, 3 watchdog. A Python-level
crash dump goes next to the report as ``<report>.crash.txt``.
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
from ctypes import wintypes as wt
from typing import Any, Callable, Dict, List, Optional, Tuple

if sys.platform != "win32":
    sys.exit("device_check_windows.py runs on Windows only (it drives the real mouse and keyboard through SendInput).")

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

from PySide6.QtCore import QObject, Qt, Signal, Slot  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget  # noqa: E402

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
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(REPORT, fh, ensure_ascii=False, indent=1, default=str)


def step(name: str, ok: Optional[bool], **detail: Any) -> None:
    """Records one verdict (True pass, False fail, None skipped) and saves the report at once."""
    REPORT["steps"].append({"step": name, "ok": ok, **detail})
    mark = {True: "PASS", False: "FAIL", None: "SKIP"}[ok]
    print(f"[{mark}] {name} :: {json.dumps(detail, ensure_ascii=False, default=str)}", flush=True)
    save_report()


def info(name: str, **detail: Any) -> None:
    """Records a fact that is not a verdict (not counted as a pass, failure or skip)."""
    REPORT.setdefault("info", []).append({"info": name, **detail})
    print(f"[INFO] {name} :: {json.dumps(detail, ensure_ascii=False, default=str)}", flush=True)
    save_report()


# --------------------------------------------------------------------------- Win32 bindings

# Own WinDLL instances: pynput sets argtypes on ctypes.windll.user32's shared function objects.
_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_IMM32 = ctypes.WinDLL("imm32", use_last_error=True)
HIMC = wt.HANDLE


def _bind(lib: Any, name: str, restype: Any, *argtypes: Any) -> Any:
    fn = getattr(lib, name)
    fn.restype, fn.argtypes = restype, list(argtypes)
    return fn


WindowFromPoint = _bind(_USER32, "WindowFromPoint", wt.HWND, wt.POINT)
GetAncestor = _bind(_USER32, "GetAncestor", wt.HWND, wt.HWND, wt.UINT)
GetWindowThreadProcessId = _bind(_USER32, "GetWindowThreadProcessId", wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
GetForegroundWindow = _bind(_USER32, "GetForegroundWindow", wt.HWND)
GetClassNameW = _bind(_USER32, "GetClassNameW", ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
GetKeyState = _bind(_USER32, "GetKeyState", wt.SHORT, ctypes.c_int)
GetAsyncKeyState = _bind(_USER32, "GetAsyncKeyState", wt.SHORT, ctypes.c_int)
GetKeyboardLayout = _bind(_USER32, "GetKeyboardLayout", wt.HKL, wt.DWORD)
SendMessageTimeoutW = _bind(_USER32, "SendMessageTimeoutW", wt.LPARAM, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                            wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t))
ImmGetContext = _bind(_IMM32, "ImmGetContext", HIMC, wt.HWND)
ImmReleaseContext = _bind(_IMM32, "ImmReleaseContext", wt.BOOL, wt.HWND, HIMC)
ImmGetOpenStatus = _bind(_IMM32, "ImmGetOpenStatus", wt.BOOL, HIMC)
ImmSetOpenStatus = _bind(_IMM32, "ImmSetOpenStatus", wt.BOOL, HIMC, wt.BOOL)
ImmGetConversionStatus = _bind(_IMM32, "ImmGetConversionStatus", wt.BOOL, HIMC,
                               ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD))
ImmSetConversionStatus = _bind(_IMM32, "ImmSetConversionStatus", wt.BOOL, HIMC, wt.DWORD, wt.DWORD)
ImmGetDefaultIMEWnd = _bind(_IMM32, "ImmGetDefaultIMEWnd", wt.HWND, wt.HWND)
ImmGetCompositionStringW = _bind(_IMM32, "ImmGetCompositionStringW", wt.LONG, HIMC, wt.DWORD, wt.LPVOID, wt.DWORD)

GA_ROOT = 2
VK_CAPITAL, VK_SHIFT, VK_LBUTTON = 0x14, 0x10, 0x01
IME_CMODE_NATIVE = 0x0001
WM_IME_CONTROL = 0x0283
IMC_GETCONVERSIONMODE, IMC_SETCONVERSIONMODE = 0x0001, 0x0002
SMTO_ABORTIFHUNG = 0x0002
GCS_COMPSTR = 0x0008
LANG_KOREAN = 0x0412


# --------------------------------------------------------------------------- safety checks

def _pid_of(hwnd: Optional[int]) -> Optional[int]:
    if not hwnd:
        return None
    pid = wt.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _class_of(hwnd: Optional[int]) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_at(x: float, y: float) -> Dict[str, Any]:
    """Top-level window under (x, y): owner pid, its handle and window class (never the title)."""
    hwnd = WindowFromPoint(wt.POINT(int(round(x)), int(round(y))))
    root = GetAncestor(hwnd, GA_ROOT) if hwnd else None
    return {"pid": _pid_of(root), "root": int(root or 0), "class": _class_of(root)}


def ours_at(x: float, y: float) -> bool:
    return window_at(x, y)["pid"] == PID


def we_are_frontmost() -> bool:
    return _pid_of(GetForegroundWindow()) == PID


def left_button_down() -> bool:
    return bool(GetAsyncKeyState(VK_LBUTTON) & 0x8000)


def shift_down() -> bool:
    return bool(GetAsyncKeyState(VK_SHIFT) & 0x8000)


def caps_lock_on() -> bool:
    return bool(GetKeyState(VK_CAPITAL) & 1)


# Virtual-key codes of the keys the checks send, by the names the engine uses.
VK_NAMES: Dict[int, str] = {
    0x10: "shift", 0xA0: "shift", 0xA1: "shift", 0x11: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
    0x12: "alt", 0xA4: "alt", 0xA5: "alt", 0x5B: "cmd", 0x5C: "cmd", 0x0D: "enter", 0x09: "tab",
    0x14: "caps_lock", 0x1B: "esc", 0x5D: "menu", 0x15: "hangul", 0x20: "space",
}
VK_PACKET = 0xE7
LLKHF_INJECTED = 0x10
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104


def key_name(vk: int, scan: int) -> str:
    if vk == VK_PACKET:
        return "text:" + chr(scan)  # a KEYEVENTF_UNICODE packet carries the UTF-16 unit in wScan
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x7B:
        return f"f{vk - 0x6F}"
    return f"vk{vk:#04x}"


class KeyTap:
    """Low-level keyboard hook (pynput's Listener) recording injected key events only.

    ``LLKHF_INJECTED`` is set on events sent with SendInput; a physical key press never has
    it, so the user's own typing is dropped before anything is stored.
    """

    def __init__(self) -> None:
        self.events: List[Tuple[float, str, str]] = []
        self.lock = threading.Lock()
        self.listener: Any = None
        self.ok = False

    def _filter(self, msg: int, data: Any) -> bool:
        if data.flags & LLKHF_INJECTED:
            kind = "down" if msg in (WM_KEYDOWN, WM_SYSKEYDOWN) else "up"
            with self.lock:
                self.events.append((now(), kind, key_name(int(data.vkCode), int(data.scanCode))))
        return False  # nothing for pynput's callbacks; the event itself goes on unchanged

    def start(self) -> bool:
        from pynput.keyboard import Listener

        self.listener = Listener(win32_event_filter=self._filter)
        self.listener.daemon = True
        self.listener.start()
        self.listener.wait()
        self.ok = self.listener.is_alive()
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


# --------------------------------------------------------------------------- IME (Korean) state

def imm_status(hwnd: int) -> Optional[Dict[str, Any]]:
    """Open status and conversion mode of ``hwnd``'s input context. GUI thread only."""
    himc = ImmGetContext(hwnd)
    if not himc:
        return None
    try:
        conv, sentence = wt.DWORD(), wt.DWORD()
        if not ImmGetConversionStatus(himc, ctypes.byref(conv), ctypes.byref(sentence)):
            return None
        return {"open": bool(ImmGetOpenStatus(himc)), "conversion": int(conv.value), "sentence": int(sentence.value),
                "composition_bytes": int(ImmGetCompositionStringW(himc, GCS_COMPSTR, None, 0))}
    finally:
        ImmReleaseContext(hwnd, himc)


def imm_set_native(hwnd: int, native: bool) -> bool:
    """Turns IME_CMODE_NATIVE (Hangul) on or off in ``hwnd``'s input context. GUI thread only."""
    himc = ImmGetContext(hwnd)
    if not himc:
        return False
    try:
        conv, sentence = wt.DWORD(), wt.DWORD()
        if not ImmGetConversionStatus(himc, ctypes.byref(conv), ctypes.byref(sentence)):
            return False
        if native and not ImmGetOpenStatus(himc):
            ImmSetOpenStatus(himc, True)
        mode = (conv.value | IME_CMODE_NATIVE) if native else (conv.value & ~IME_CMODE_NATIVE)
        return bool(ImmSetConversionStatus(himc, mode, sentence.value))
    finally:
        ImmReleaseContext(hwnd, himc)


def ime_window_mode(hwnd: int) -> Optional[int]:
    """Conversion mode read through ``hwnd``'s default IME window (works from any thread/process)."""
    ime = ImmGetDefaultIMEWnd(hwnd)
    result = ctypes.c_size_t()
    if not ime or not SendMessageTimeoutW(ime, WM_IME_CONTROL, IMC_GETCONVERSIONMODE, 0,
                                          SMTO_ABORTIFHUNG, 500, ctypes.byref(result)):
        return None
    return int(result.value)


def ime_window_set_mode(hwnd: int, mode: int) -> bool:
    ime = ImmGetDefaultIMEWnd(hwnd)
    result = ctypes.c_size_t()
    return bool(ime) and bool(SendMessageTimeoutW(ime, WM_IME_CONTROL, IMC_SETCONVERSIONMODE, mode,
                                                  SMTO_ABORTIFHUNG, 500, ctypes.byref(result)))


#: What the checks changed and must put back (also read by the watchdog).
RESTORE: Dict[str, Any] = {}


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
        p.setFont(QFont("Segoe UI", 16))
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


class KeyField(QLineEdit):
    """Line edit that records context-menu requests (and their reason) instead of showing the menu."""

    def __init__(self) -> None:
        super().__init__()
        self.context_menus: List[str] = []

    def contextMenuEvent(self, e: Any) -> None:
        self.context_menus.append(e.reason().name)  # Keyboard (menu key, Shift+F10) or Mouse
        e.accept()


class KeyPad(QWidget):
    """Always-on-top window whose fields receive the key steps: one line, and several lines."""

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("ActionMacro device check — keys (hands off / 손 떼 주세요)")
        self.edit = KeyField()
        self.edit.setFont(QFont("Consolas", 16))
        self.lines = QPlainTextEdit()  # line breaks and tabs: Enter and Tab insert them here
        self.lines.setFont(QFont("Consolas", 14))
        layout = QVBoxLayout(self)
        layout.addWidget(self.edit)
        layout.addWidget(self.lines)

    def text_of(self, field: QWidget) -> str:
        return field.toPlainText() if isinstance(field, QPlainTextEdit) else field.text()


# --------------------------------------------------------------------------- controller helpers

ADAPTER: Optional[PynputAdapter] = None  # built on the main thread in main()
#: Qt widgets created for a step. Kept alive until exit: dropping the last Python reference
#: on the controller thread would delete the C++ widget off the GUI thread.
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


def text_block(text: str) -> ActionBlock:
    return ActionBlock(type=ActionType.KEY_INPUT, params=KeyInputParams(keys=[], action="type", text=text))


def wait_until(pred: Callable[[], bool], timeout: float, interval: float = 0.01) -> bool:
    end = now() + timeout
    while now() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


def displays() -> List[Dict[str, Any]]:
    def _read() -> List[Dict[str, Any]]:
        primary = QGuiApplication.primaryScreen()
        out = []
        for s in QGuiApplication.screens():
            g, a = s.geometry(), s.availableGeometry()
            out.append({"name": s.name(), "main": s == primary, "x": g.x(), "y": g.y(), "w": g.width(),
                        "h": g.height(), "work": (a.x(), a.y(), a.width(), a.height()),
                        "dpr": s.devicePixelRatio()})
        return out
    return gui(_read)


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
    """Top-left of a w x h pad in the lower middle of a monitor's work area (above the taskbar)."""
    ax, ay, aw, ah = d["work"]
    return ax + (aw - w) // 2, ay + ah - h - 80


def centred(d: Dict[str, Any], w: int, h: int) -> Tuple[int, int]:
    ax, ay, aw, ah = d["work"]
    return ax + (aw - w) // 2, ay + (ah - h) // 2


# --------------------------------------------------------------------------- checks: mouse

def check_click_precision(pad: ClickTarget) -> None:
    rng = random.Random(20261031)
    for d in displays():
        w, h = 700, 300
        x0, y0 = lower_middle(d, w, h)
        place(pad, x0, y0, w, h)
        label = f"display {d['name']}{'(main)' if d['main'] else ''} origin=({d['x']},{d['y']}) dpr={d['dpr']}"
        cx, cy = x0 + w // 2, y0 + h // 2
        if not ours_at(cx, cy):
            step(f"click precision — {label}", None, reason="click pad not on top", owner=window_at(cx, cy))
            continue
        run(engine_for(click_block(cx, cy)))  # warm-up: the first click may only activate the window
        time.sleep(0.2)
        errors, misses, results = [], 0, []
        for _ in range(10):
            tx = rng.randint(x0 + 20, x0 + w - 21)
            ty = rng.randint(y0 + 20, y0 + h - 21)
            if not ours_at(tx, ty):
                step(f"click precision — {label}", None, reason="target not ours", target=(tx, ty),
                     owner=window_at(tx, ty))
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
             negative_coords=any(r["target"][0] < 0 or r["target"][1] < 0 for r in results), sample=results[:3])


def check_double_click(pad: ClickTarget) -> None:
    w, h = 700, 450
    x0, y0 = centred(main_display(), w, h)
    place(pad, x0, y0, w, h)
    cx, cy = x0 + w // 2, y0 + h // 2
    if not ours_at(cx, cy):
        step("double click (clicks=2)", None, reason="pad not on top", owner=window_at(cx, cy))
        return
    time.sleep(0.6)  # apart from earlier clicks
    before = len(pad.snapshot())
    run(engine_for(click_block(cx, cy, clicks=2, interval_ms=80)))
    time.sleep(0.4)
    ev = pad.snapshot()[before:]
    step("double click (clicks=2) — Qt sees a double click", any(e[1] == "dblclick" for e in ev),
         presses=sum(1 for e in ev if e[1] == "press"), dblclick_events=sum(1 for e in ev if e[1] == "dblclick"))


def check_stop_multi_click(pad: ClickTarget) -> None:
    w, h = 700, 450
    x0, y0 = centred(main_display(), w, h)
    place(pad, x0, y0, w, h)
    cx, cy = x0 + w // 2, y0 + h // 2
    if not ours_at(cx, cy):
        step("emergency stop during multi-click", None, reason="pad not on top", owner=window_at(cx, cy))
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
        step("emergency stop during long click hold", None, reason="pad not on top", owner=window_at(cx, cy))
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
        ax, ay = d["work"][:2]
        win.move(ax + 80, ay + 40)
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
    pad_hwnd = gui(lambda: int(pad.winId()))
    if window_at(tx, ty)["root"] != pad_hwnd:  # the floating pad, not MainWindow
        step("real app end-to-end (MainWindow → Start → click)", None, reason="pad not on top",
             owner=window_at(tx, ty))
        gui(lambda: holder["win"].hide())
        return
    before = len(pad.snapshot())
    gui(lambda: holder["win"].dashboard.btn_start.click())
    eng = gui(lambda: holder["win"].engine)
    time.sleep(0.4)  # mid-run: the listener thread has installed its hook by now
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


# --------------------------------------------------------------------------- checks: keys

def show_keypad(kp: KeyPad) -> None:
    d = main_display()
    w, h = 640, 260
    ax, ay, aw, ah = d["work"]
    place(kp, ax + (aw - w) // 2, ay + ah // 2 + 40, w, h)


def focus_field(kp: KeyPad, field: QWidget) -> bool:
    """Clicks into ``field`` (after checking the window there is ours) until it has focus."""
    if not gui(lambda: kp.isVisible()):
        show_keypad(kp)
    geo = gui(lambda: field.mapToGlobal(field.rect().center()))
    ex, ey = geo.x(), geo.y()
    if not ours_at(ex, ey):
        step("key pad focus", None, reason="key pad not on top", owner=window_at(ex, ey))
        return False
    assert ADAPTER is not None
    ADAPTER.click(ex, ey)
    focused = wait_until(lambda: keys_safe(kp, field), 2.0, 0.05)
    if not focused:
        step("key pad focus (this process in front, field focused)", False, frontmost=we_are_frontmost(),
             foreground=window_at(ex, ey))
    return focused


def keys_safe(kp: KeyPad, field: Optional[QWidget] = None) -> bool:
    target = kp.edit if field is None else field
    return we_are_frontmost() and gui(lambda: QApplication.focusWidget() is target)


def kp_hwnd(kp: KeyPad) -> int:
    return gui(lambda: int(kp.winId()))


def keyboard_state(kp: KeyPad) -> Dict[str, Any]:
    """IME mode of the key pad and the Caps Lock toggle, read on the GUI thread."""
    hwnd = kp_hwnd(kp)
    imm = gui(lambda: imm_status(hwnd))
    return {
        "hangul": bool(imm and imm["open"] and imm["conversion"] & IME_CMODE_NATIVE),
        "ime_open": imm["open"] if imm else None,
        "conversion": hex(imm["conversion"]) if imm else None,
        "composition_bytes": imm["composition_bytes"] if imm else None,
        "ime_window_mode": hex(m) if (m := ime_window_mode(hwnd)) is not None else None,
        "caps_lock": gui(caps_lock_on),
    }


def set_hangul(kp: KeyPad, native: bool, manual: bool) -> Tuple[bool, str]:
    """Puts the key pad's IME in Hangul (native) or English mode. Returns (done, method)."""
    hwnd = kp_hwnd(kp)

    def is_set() -> bool:
        return keyboard_state(kp)["hangul"] == native

    if is_set():
        return True, "already"
    gui(lambda: imm_set_native(hwnd, native))
    if wait_until(is_set, 1.0, 0.05):
        return True, "ImmSetConversionStatus"
    mode = ime_window_mode(hwnd)
    if mode is not None:
        ime_window_set_mode(hwnd, (mode | IME_CMODE_NATIVE) if native else (mode & ~IME_CMODE_NATIVE))
        if wait_until(is_set, 1.0, 0.05):
            return True, "WM_IME_CONTROL"
    if manual:
        want = "한글 (가)" if native else "영문 (A)"
        print(f"\n>>> 점검 창에 포커스를 둔 채 한/영 키를 한 번 눌러 {want} 상태로 바꿔 주세요. (30초 기다림)\n"
              f">>> Press the Han/Eng key once with the check window focused ({'Hangul' if native else 'English'}).",
              flush=True)
        if wait_until(is_set, 30.0, 0.1):
            print(">>> 확인했습니다. 다시 손을 떼 주세요 (2초 뒤 시작). / Got it — hands off again.", flush=True)
            time.sleep(2.0)
            return True, "manual Han/Eng key"
    return False, "not switched"


def set_caps_lock(kp: KeyPad, on: bool) -> Tuple[bool, Dict[str, Any]]:
    """Toggles Caps Lock with an injected Caps Lock key (only while the key pad has focus).

    Returns (done, detail). A key remapper (an AutoHotkey ``CapsLock::`` hotkey, PowerToys)
    may swallow the key and send something else, e.g. the Han/Eng key: the detail then lists
    the keys another program injected in response.
    """
    if gui(caps_lock_on) == on:
        return True, {}
    if not keys_safe(kp) and not focus_field(kp, kp.edit):
        return False, {"reason": "could not focus the key pad"}
    hangul_before = keyboard_state(kp)["hangul"]
    n = TAP.mark()
    assert ADAPTER is not None
    ADAPTER.press_key("caps_lock", duration=0.02)
    if wait_until(lambda: gui(caps_lock_on) == on, 1.0, 0.05):
        return True, {}
    others = [name for kind, name in TAP.since(n) if kind == "down" and name != "caps_lock"]
    detail: Dict[str, Any] = {"reason": "Caps Lock did not toggle", "keys_injected_in_response": others,
                              "hangul_before": hangul_before, "hangul_after": keyboard_state(kp)["hangul"]}
    if others or detail["hangul_after"] != hangul_before:
        detail["reason"] = "Caps Lock is remapped by another program (it sent other keys instead)"
        if detail["hangul_after"] != hangul_before:  # e.g. Caps Lock -> Han/Eng: switch the mode back
            set_hangul(kp, hangul_before, manual=False)
    return False, detail


def restore_keyboard(kp: KeyPad) -> None:
    """Puts back the IME mode and Caps Lock recorded before the first change."""
    if "hangul" in RESTORE:
        done, method = set_hangul(kp, RESTORE["hangul"], manual=False)
        hwnd = kp_hwnd(kp)
        if "conversion" in RESTORE and ime_window_mode(hwnd) != RESTORE["conversion"]:
            ime_window_set_mode(hwnd, RESTORE["conversion"])
        step("IME mode restored", done, method=method, state=keyboard_state(kp),
             original_conversion=hex(RESTORE.get("conversion", 0)))
    if "caps_lock" in RESTORE:
        ok, detail = set_caps_lock(kp, RESTORE["caps_lock"])
        step("Caps Lock restored", ok and gui(caps_lock_on) == RESTORE["caps_lock"],
             caps_lock=gui(caps_lock_on), original=RESTORE["caps_lock"], **detail)


#: Typed by the text step: a user name, then upper case, digits, a space and symbols that
#: need Shift on a US layout, then Korean syllables with a Latin name and digits, then line
#: breaks (LF and CRLF, each one Enter) and a tab.
TYPED_TEXTS: List[Tuple[str, str]] = [
    ("myid123", "line"),
    ("Ab1 x_Y!@", "line"),
    ("안녕하세요 홍길동 id123", "line"),
    ("첫 줄 line1\nsecond 둘째\r\n\tTab id9", "lines"),
]
#: (key for --states, label, Hangul mode, Caps Lock)
KEYBOARD_STATES: List[Tuple[str, str, bool, bool]] = [
    ("english", "English mode", False, False),
    ("hangul", "Hangul mode", True, False),
    ("caps", "Caps Lock on", False, True),
    ("hangul_caps", "Hangul mode + Caps Lock", True, True),
]


def type_into(kp: KeyPad, field: QWidget, name: str, text: str, native: bool, caps: bool) -> None:
    """Runs one text step into the cleared field and judges the field's text."""
    if not keys_safe(kp, field) and not focus_field(kp, field):
        step(name, None, reason="could not focus the field — key input skipped")
        return
    gui(lambda: field.clear())
    if keyboard_state(kp)["hangul"] != native:  # moving the focus may have reset the IME mode
        set_hangul(kp, native, manual=False)
    if not keys_safe(kp, field):
        step(name, None, reason="focus left the key pad — key input skipped")
        return
    before = keyboard_state(kp)
    if before["hangul"] != native or before["caps_lock"] != caps:
        step(name, None, reason="keyboard state is not the one wanted", state=before,
             wanted={"hangul": native, "caps_lock": caps})
        return
    n = TAP.mark()
    run(engine_for(text_block(text)))
    time.sleep(0.4)
    got = gui(lambda: kp.text_of(field))
    after = keyboard_state(kp)
    events, _ = strip_flush(TAP.since(n))
    expected = text.replace("\r\n", "\n")
    unchanged = after["hangul"] == before["hangul"] and after["caps_lock"] == before["caps_lock"]
    step(name, got == expected and unchanged, field_text=got, expected=expected,
         hangul=after["hangul"], caps_lock=after["caps_lock"], conversion=after["conversion"],
         composition_bytes=after["composition_bytes"], state_unchanged=unchanged,
         unicode_packets=sum(1 for k, nm in events if k == "down" and nm.startswith("text:")),
         keys=[nm for k, nm in events if k == "down" and not nm.startswith("text:")],
         shift_still_down=shift_down())


def check_ime_control(kp: KeyPad) -> None:
    """The IME mode can be read and set through a window's default IME window (WM_IME_CONTROL).

    That is how a text step could switch another process's window to English while it types.
    """
    hwnd = kp_hwnd(kp)
    layout = gui(lambda: GetKeyboardLayout(0)) or 0
    original = ime_window_mode(hwnd)
    if original is None:
        step("WM_IME_CONTROL reads the IME mode", False, keyboard_layout=hex(layout), state=keyboard_state(kp))
        return
    results = {}
    try:
        for native in (True, False):
            mode = (original | IME_CMODE_NATIVE) if native else (original & ~IME_CMODE_NATIVE)
            sent = ime_window_set_mode(hwnd, mode)
            seen = wait_until(lambda: keyboard_state(kp)["hangul"] == native, 1.0, 0.05)
            results["hangul" if native else "english"] = {"sent": sent, "imm_agrees": seen,
                                                          "mode_now": hex(ime_window_mode(hwnd) or 0)}
    finally:
        ime_window_set_mode(hwnd, original)
    step("WM_IME_CONTROL switches the IME mode (seen by ImmGetConversionStatus)",
         all(r["sent"] and r["imm_agrees"] for r in results.values()),
         keyboard_layout=hex(layout), korean_layout=(layout & 0xFFFF) == LANG_KOREAN,
         original_mode=hex(original), results=results, mode_after_restore=hex(ime_window_mode(hwnd) or 0))


def check_type_text_states(kp: KeyPad, manual_ime: bool, states: List[str]) -> None:
    """Every text in every chosen keyboard state, then the keyboard is put back as it was."""
    if not TAP.ok:
        step("key event hook", False, reason="the low-level keyboard hook did not start")
    show_keypad(kp)
    if not focus_field(kp, kp.edit):
        return
    original = keyboard_state(kp)
    RESTORE["hangul"] = original["hangul"]
    RESTORE["caps_lock"] = original["caps_lock"]
    hwnd = kp_hwnd(kp)
    if (mode := ime_window_mode(hwnd)) is not None:
        RESTORE["conversion"] = mode
    info("keyboard state before the checks", **original,
         keyboard_layout=hex(gui(lambda: GetKeyboardLayout(0)) or 0))
    check_ime_control(kp)
    try:
        for key, label, native, caps in KEYBOARD_STATES:
            if key not in states:
                continue
            if not keys_safe(kp) and not focus_field(kp, kp.edit):
                step(f"[{label}]", None, reason="could not focus the key pad")
                return
            done, method = set_hangul(kp, native, manual_ime)
            if not done:
                step(f"[{label}] switch the IME mode", None, reason="IME mode did not switch (try --manual-ime)",
                     state=keyboard_state(kp))
                continue
            caps_done, caps_detail = set_caps_lock(kp, caps)
            if not caps_done:
                step(f"[{label}] switch Caps Lock", None, **caps_detail, state=keyboard_state(kp))
                continue
            state = keyboard_state(kp)
            step(f"[{label}] keyboard state set", state["hangul"] == native and state["caps_lock"] == caps,
                 method=method, **state)
            for text, kind in TYPED_TEXTS:
                field = kp.lines if kind == "lines" else kp.edit
                type_into(kp, field, f"[{label}] text step types {text!r}", text, native, caps)
    finally:
        restore_keyboard(kp)


def check_key_chords(kp: KeyPad) -> None:
    if not TAP.ok:
        step("key event hook", False, reason="the low-level keyboard hook did not start")
        return
    if not focus_field(kp, kp.edit):
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
             field_text=gui(lambda: kp.edit.text()), shift_still_down=shift_down())

    expect("chord 'shift+a' — shift held while a is pressed", [key_block(["shift", "a"])], chord("shift", "a"))
    expect("down shift → b → up shift (one scenario)",
           [key_block(["shift"], action="down"), key_block(["b"]), key_block(["shift"], action="up")],
           [("down", "shift"), ("down", "b"), ("up", "b"), ("up", "shift")])
    expect("chord 'cmd_or_ctrl+a' — ctrl held while a is pressed", [key_block(["cmd_or_ctrl", "a"])],
           chord("ctrl", "a"))
    sel, full = gui(lambda: (kp.edit.selectedText(), kp.edit.text()))
    step("chord 'cmd_or_ctrl+a' really selected all in the field", bool(full) and sel == full, selected=sel, text=full)
    expect("legacy file modifiers=['shift'] + keys=['d']",
           [ActionBlock(type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["d"], modifiers=["shift"]))],
           chord("shift", "d"))


def check_context_menu_keys(kp: KeyPad) -> None:
    """Key steps that Windows turns into a context-menu request, i.e. what looks like a right click.

    ``menu`` is VK_APPS and Shift+F10 is the standard keyboard shortcut for the context menu.
    ``cmd+x`` (Win+X, the Start button's right-click menu) is not sent: the shell opens it,
    outside this process. Recorded as facts, not verdicts.
    """
    if not TAP.ok or not focus_field(kp, kp.edit):
        return
    for keys in (["menu"], ["shift", "f10"]):
        name = "+".join(keys)
        if not keys_safe(kp):
            step(f"key step {name!r} — context menu", None, reason="focus left the key pad — key input skipped")
            return
        before = gui(lambda: len(kp.edit.context_menus))
        n = TAP.mark()
        run(engine_for(key_block(keys)))
        time.sleep(0.3)
        requests = gui(lambda: kp.edit.context_menus[before:])
        events, _ = strip_flush(TAP.since(n))
        popup = gui(lambda: QApplication.activePopupWidget() is not None)
        info(f"key step {name!r} on Windows", context_menu_requested=bool(requests), reasons=requests,
             events=events, popup_left_open=popup, still_in_front=we_are_frontmost())
        if popup:
            gui(lambda: QApplication.activePopupWidget().close())


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
    shift_after_done = shift_down()
    time.sleep(0.3)
    ev = TAP.timed_since(n)
    ups = {name: t for t, k, name in ev if k == "up" and t >= t_stop}
    return {
        "e_release_ms": round((ups["e"] - t_stop) * 1000, 1) if "e" in ups else None,
        "shift_release_ms": round((ups["shift"] - t_stop) * 1000, 1) if "shift" in ups else None,
        "presses_after_stop": [name for t, k, name in ev if k == "down" and t > t_stop + 0.05],
        "shift_down_when_done": shift_after_done,
        "shift_down_400ms": shift_down(),
        "events_ms": [(round((t - t_stop) * 1000, 1), k, name) for t, k, name in ev],
    }


def released_promptly(r: Dict[str, Any]) -> bool:
    return (r["e_release_ms"] is not None and r["shift_release_ms"] is not None
            and max(r["e_release_ms"], r["shift_release_ms"]) < 100
            and not r["presses_after_stop"] and not r["shift_down_400ms"])


def check_stop_long_key_hold(kp: KeyPad) -> None:
    if not TAP.ok or (not keys_safe(kp) and not focus_field(kp, kp.edit)):
        return
    r = stop_during_key_hold(kp)
    r.pop("events_ms")
    step("emergency stop during 3s 'shift+e' hold — both keys released", released_promptly(r), **r)


def repeat_stop_key_hold(kp: KeyPad, trials: int) -> None:
    show_keypad(kp)
    if not TAP.ok or not focus_field(kp, kp.edit):
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


# --------------------------------------------------------------------------- checks: picker, listener

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
        label = f"display {d['name']}{'(main)' if d['main'] else ''}"
        if not ours_at(tx, ty):
            step(f"coordinate picker — {label}", None, reason="overlay not on top", owner=window_at(tx, ty))
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
    step("global hotkey listener — keyboard hook thread alive", started and alive, start_returned=started,
         native_thread_alive=alive)
    lst.stop()


# --------------------------------------------------------------------------- main

def controller(app: QApplication, pad: ClickTarget, kp: KeyPad, start_pos: Tuple[int, int],
               stop_key_trials: int, text_only: bool = False, manual_ime: bool = False,
               states: Optional[List[str]] = None, context_menu_only: bool = False) -> None:
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
        if context_menu_only:
            show_keypad(kp)
            guarded(check_context_menu_keys, kp)
            return
        guarded(check_type_text_states, kp, manual_ime, states or [key for key, *_ in KEYBOARD_STATES])
        if text_only:
            return
        guarded(check_key_chords, kp)
        guarded(check_context_menu_keys, kp)
        guarded(check_stop_long_key_hold, kp)
        gui(lambda: kp.hide())
        for check in (check_click_precision, check_double_click, check_stop_multi_click,
                      check_stop_long_click_hold, check_real_app_end_to_end):
            guarded(check, pad)
        gui(lambda: pad.hide())
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


def watchdog_restore(kp_hwnd_value: Optional[int]) -> None:
    """Best effort from the watchdog thread: IME mode through WM_IME_CONTROL, Caps Lock by key."""
    if kp_hwnd_value and "conversion" in RESTORE:
        ime_window_set_mode(kp_hwnd_value, RESTORE["conversion"])
    if "caps_lock" in RESTORE and caps_lock_on() != RESTORE["caps_lock"] and ADAPTER is not None:
        ADAPTER.press_key("caps_lock", duration=0.02)


def main(argv: Optional[List[str]] = None) -> int:
    global BRIDGE, ADAPTER, REPORT_PATH
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", default="device_check_report.json", help="JSON report path")
    parser.add_argument("--timeout", type=float, default=120.0, help="watchdog seconds (releases inputs, exits 3)")
    parser.add_argument("--stop-key-trials", type=int, default=0, metavar="N",
                        help="only repeat the stop-during-key-hold check N times")
    parser.add_argument("--text-only", action="store_true", help="only the text-step checks")
    parser.add_argument("--context-menu-only", action="store_true",
                        help="only the key steps that open a context menu (menu, shift+f10)")
    parser.add_argument("--manual-ime", action="store_true",
                        help="if the IME mode cannot be switched by the check, ask for the Han/Eng key")
    parser.add_argument("--countdown", type=float, default=3.0, metavar="S",
                        help="seconds to wait before the first input, to take your hands off (default 3)")
    state_keys = [key for key, *_ in KEYBOARD_STATES]
    parser.add_argument("--states", default=",".join(state_keys),
                        help=f"keyboard states for the text checks, comma-separated (default: {','.join(state_keys)})")
    args = parser.parse_args(argv)
    states = [s.strip() for s in args.states.split(",") if s.strip()]
    if unknown := [s for s in states if s not in state_keys]:
        parser.error(f"unknown --states {unknown}; choose from {state_keys}")
    for left in range(int(args.countdown), 0, -1):
        print(f"starting in {left}s — hands off the mouse and keyboard / {left}초 뒤 시작 — 손을 떼 주세요", flush=True)
        time.sleep(1.0)
    time.sleep(args.countdown - int(args.countdown))
    REPORT_PATH = args.report
    faulthandler.enable(open(REPORT_PATH + ".crash.txt", "w"), all_threads=True)

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)  # the check hides all its windows between phases
    BRIDGE = Bridge()
    ADAPTER = PynputAdapter()
    pad, kp = ClickTarget(), KeyPad()
    start_pos = ADAPTER.get_position()
    hwnd_for_watchdog = int(kp.winId())

    def watchdog() -> None:
        time.sleep(args.timeout)
        print("WATCHDOG: releasing inputs, restoring the keyboard state and aborting", flush=True)
        try:
            if ADAPTER is not None:
                ADAPTER.safety_flush()
            watchdog_restore(hwnd_for_watchdog)
        finally:
            REPORT["watchdog"] = True
            save_report()
            os._exit(3)

    TAP.start()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=controller,
                     args=(app, pad, kp, start_pos, args.stop_key_trials, args.text_only, args.manual_ime, states,
                           args.context_menu_only),
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
