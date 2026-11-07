"""ActionMacro real-device check for Windows: Image wait against real screen captures.

It sends NO mouse or keyboard input. It shows its own windows, captures the screen with
mss (as the engine does) and runs Image wait steps through the production assembly.

The automated tests fake the screen; this check uses the real desktop at the current
display scale. Run it once per scale (100%, 125%, 150%) — on Windows the engine captures
physical pixels and matches templates at their own size only, so a template is taken at
the scale it is used at, the way a screenshot (Win+Shift+S) of the screen would be.

Usage (about 20 seconds)::

    .venv\\Scripts\\python tools\\device_check_image_windows.py [--report PATH] [--timeout S]
    .venv\\Scripts\\python tools\\device_check_image_windows.py --template-from device_check_image_template_100.png

What it checks:

- a template captured from the screen (a pattern window of this process, physical pixels)
  is found where the window is — at three places on the main monitor (corner, centre,
  opposite corner): the found centre (``ExecutionEngine._last_detected_coords``) must be
  within 1 px of the centre the window has according to Windows (GetWindowRect), which
  does not depend on Qt's logical coordinates;
- a search region (ROI) in physical pixels finds it; the same region typed in Qt's logical
  coordinates is recorded as INFO (on a scaled monitor it points elsewhere);
- appear: the step waits while the pattern is hidden and ends soon after it is shown;
- disappear: the step ends soon after the pattern is hidden;
- timeout: with the pattern hidden the step fails after its timeout (Stop with error);
- the real ``MainWindow``: an Image wait step set through the inspector form, started with
  the Run button (``btn_start.click()``, no real mouse), ends when the pattern appears;
- ``--template-from PATH`` (INFO): whether a template captured at another scale is found.

The template of this run is saved as ``device_check_image_template_<scale%>.png`` next to the
report (only the check's own pattern window is in it). Exit codes: 0 all steps passed or
skipped, 1 a step failed, 3 watchdog.
"""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import json
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes as wt
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

if sys.platform != "win32":
    sys.exit("device_check_image_windows.py runs on Windows only.")

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

import cv2  # noqa: E402
from PySide6.QtCore import QObject, QRect, Qt, Signal, Slot  # noqa: E402
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from action_macro.core.models import ActionBlock, ActionType, ImageWaitParams, ScenarioModel  # noqa: E402
from action_macro.engine.screen_adapter import MSSScreenAdapter  # noqa: E402
from action_macro.runner.execution_engine import ExecutionEngine  # noqa: E402

PID = os.getpid()
REPORT: Dict[str, Any] = {"pid": PID, "steps": []}
REPORT_PATH = "device_check_image_report.json"
T0 = time.perf_counter()

#: Pattern window size in Qt logical pixels; positions are multiples of 4 so that they are
#: whole physical pixels at 100%, 125% and 150%.
PATTERN_W, PATTERN_H = 120, 60
#: Physical pixels left out around the template (the window edge is not part of it).
INSET = 4


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
    """Records a fact that is not a verdict."""
    REPORT.setdefault("info", []).append({"info": name, **detail})
    print(f"[INFO] {name} :: {json.dumps(detail, ensure_ascii=False, default=str)}", flush=True)
    save_report()


# --------------------------------------------------------------------------- Win32

_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_USER32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
_USER32.GetWindowRect.restype = wt.BOOL
_USER32.GetDpiForWindow.argtypes = [wt.HWND]
_USER32.GetDpiForWindow.restype = wt.UINT


def window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """(x, y, w, h) of a window in physical pixels (this process is per-monitor DPI aware)."""
    r = wt.RECT()
    _USER32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


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


_FLAGS = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool


class Stage(QWidget):
    """Plain backdrop over the work area, so nothing else on the desktop looks like the pattern."""

    def __init__(self) -> None:
        super().__init__(None, _FLAGS)
        self.setWindowTitle("ActionMacro device check — image wait")

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#16324a"))
        p.setPen(QColor("white"))
        p.setFont(QFont("Segoe UI", 14))
        p.drawText(self.rect().adjusted(0, 0, 0, -200), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                   "ActionMacro device check — image wait (no mouse or keyboard input)\n"
                   "실기기 점검 중 — 이미지 대기 (마우스·키보드 입력 없음)")


class Pattern(QWidget):
    """A small window with a high-contrast, asymmetric picture: the thing the template shows."""

    def __init__(self) -> None:
        super().__init__(None, _FLAGS)
        self.setWindowTitle("ActionMacro device check — pattern")
        self.setFixedSize(PATTERN_W, PATTERN_H)

    def paintEvent(self, _e: Any) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#f5f5f5"))
        cells = ["#d62828", "#003049", "#fcbf49", "#2a9d8f", "#6a4c93", "#1d3557"]
        for i, colour in enumerate(cells):
            p.fillRect(QRect(6 + i * 18, 6, 14, 14), QColor(colour))
        p.fillRect(QRect(6, 26, 30, 28), QColor("#111111"))
        p.fillRect(QRect(12, 32, 10, 10), QColor("#f5f5f5"))
        p.setPen(QColor("#111111"))
        p.setFont(QFont("Segoe UI", 14, QFont.Weight.Bold))
        p.drawText(QRect(40, 24, 76, 32), Qt.AlignmentFlag.AlignCenter, "AM 7Q")


# --------------------------------------------------------------------------- helpers

KEEP: List[Any] = []


def main_screen() -> Dict[str, Any]:
    def _read() -> Dict[str, Any]:
        s = QGuiApplication.primaryScreen()
        g, a = s.geometry(), s.availableGeometry()
        return {"name": s.name(), "geometry": (g.x(), g.y(), g.width(), g.height()),
                "work": (a.x(), a.y(), a.width(), a.height()), "dpr": s.devicePixelRatio()}
    return gui(_read)


def show_at(widget: QWidget, x: int, y: int, w: Optional[int] = None, h: Optional[int] = None) -> None:
    def _do() -> None:
        if w is not None and h is not None:
            widget.setGeometry(x, y, w, h)
        else:
            widget.move(x, y)
        widget.show()
        widget.raise_()
    gui(_do)
    time.sleep(0.6)  # the compositor shows the window


def hide(widget: QWidget) -> None:
    gui(widget.hide)


def pattern_rect(pattern: Pattern) -> Tuple[int, int, int, int]:
    return window_rect(gui(lambda: int(pattern.winId())))


def expected_centre(rect: Tuple[int, int, int, int], templ: Tuple[int, int]) -> Tuple[int, int]:
    """Where the engine reports a match of the inset template: top-left + size // 2."""
    x, y, _, _ = rect
    tw, th = templ
    return x + INSET + tw // 2, y + INSET + th // 2


def wait_block(template: str, mode: str = "appear", timeout: float = 3.0,
               roi: Optional[Tuple[int, int, int, int]] = None, timeout_action: str = "raise") -> ActionBlock:
    return ActionBlock(type=ActionType.IMAGE_WAIT,
                       params=ImageWaitParams(template_path=template, mode=mode, confidence=0.9,
                                              timeout_seconds=timeout, roi=roi, timeout_action=timeout_action))


class Run:
    """An engine started like MainWindow does (start_async on the GUI thread), with its outcome."""

    def __init__(self, *blocks: ActionBlock) -> None:
        self.eng = ExecutionEngine(scenario=ScenarioModel(name="device-check-image", loop_count=1,
                                                          actions=list(blocks)))
        self.done = threading.Event()
        self.result: Optional[Tuple[bool, str]] = None
        self.t_start = 0.0
        self.t_end = 0.0

    def _finished(self, ok: bool, msg: str) -> None:
        self.t_end = now()
        self.result = (ok, msg)
        self.done.set()

    def start(self) -> "Run":
        self.t_start = now()
        gui(lambda: self.eng.start_async(on_finished=self._finished))
        return self

    def wait(self, timeout: float = 10.0) -> "Run":
        self.done.wait(timeout)
        return self

    @property
    def ok(self) -> bool:
        return bool(self.result and self.result[0])

    @property
    def found(self) -> Optional[Tuple[int, int]]:
        c = self.eng._last_detected_coords
        return (int(c[0]), int(c[1])) if c else None


def error_px(a: Optional[Tuple[int, int]], b: Tuple[int, int]) -> Optional[int]:
    return None if a is None else max(abs(a[0] - b[0]), abs(a[1] - b[1]))


# --------------------------------------------------------------------------- checks

def positions(work: Tuple[int, int, int, int]) -> Dict[str, Tuple[int, int]]:
    ax, ay, aw, ah = work
    r4 = lambda v: v - v % 4  # noqa: E731
    return {
        "corner": (ax + 48, ay + 48),
        "centre": (ax + r4(aw // 2 - PATTERN_W // 2), ay + r4(ah // 2 - PATTERN_H // 2)),
        "opposite corner": (ax + r4(aw - 48 - PATTERN_W), ay + r4(ah - 48 - PATTERN_H)),
    }


def capture_template(pattern: Pattern, path: Path) -> Tuple[int, int]:
    """Captures the pattern window from the screen (physical pixels) and saves it as a PNG."""
    x, y, w, h = pattern_rect(pattern)
    with MSSScreenAdapter() as screen:
        frame = screen.capture_screen(roi=(x + INSET, y + INSET, w - 2 * INSET, h - 2 * INSET))
    if not cv2.imwrite(str(path), frame):
        raise RuntimeError(f"could not write {path}")
    return frame.shape[1], frame.shape[0]


def check_found_positions(pattern: Pattern, template: str, templ: Tuple[int, int],
                          places: Dict[str, Tuple[int, int]]) -> None:
    for label, (lx, ly) in places.items():
        show_at(pattern, lx, ly)
        rect = pattern_rect(pattern)
        want = expected_centre(rect, templ)
        run = Run(wait_block(template)).start().wait()
        err = error_px(run.found, want)
        step(f"found position — {label}", run.ok and err is not None and err <= 1,
             logical=(lx, ly), window_physical=rect, expected=want, found=run.found, error_px=err,
             seconds=round(run.t_end - run.t_start, 3), result=run.result)


def check_roi(pattern: Pattern, template: str, templ: Tuple[int, int], place: Tuple[int, int],
              dpr: float) -> None:
    show_at(pattern, *place)
    rect = pattern_rect(pattern)
    want = expected_centre(rect, templ)
    x, y, w, h = rect
    roi = (x - 20, y - 20, w + 40, h + 40)
    run = Run(wait_block(template, roi=roi)).start().wait()
    err = error_px(run.found, want)
    step("ROI in physical pixels finds it", run.ok and err is not None and err <= 1,
         roi=roi, expected=want, found=run.found, error_px=err, result=run.result)
    # The same region typed in Qt's logical coordinates (what the window reports to Qt).
    lroi = (place[0] - 20, place[1] - 20, PATTERN_W + 40, PATTERN_H + 40)
    if dpr != 1.0:
        lrun = Run(wait_block(template, roi=lroi, timeout=0.5, timeout_action="skip")).start().wait()
        info("ROI in logical coordinates (wrong units on a scaled monitor)", roi=lroi,
             found=lrun.found, result=lrun.result)


def check_appear_after_delay(pattern: Pattern, template: str, templ: Tuple[int, int],
                             place: Tuple[int, int]) -> None:
    show_at(pattern, *place)
    want = expected_centre(pattern_rect(pattern), templ)
    hide(pattern)
    time.sleep(0.6)
    run = Run(wait_block(template, timeout=8.0)).start()
    time.sleep(1.5)
    early = run.done.is_set()
    gui(lambda: (pattern.show(), pattern.raise_()))
    t_show = now()
    run.wait()
    after = round(run.t_end - t_show, 3) if run.done.is_set() else None
    err = error_px(run.found, want)
    step("appear — waits while hidden, ends after it is shown",
         (not early) and run.ok and after is not None and after < 1.0 and err is not None and err <= 1,
         ended_before_shown=early, seconds_after_shown=after, expected=want, found=run.found,
         error_px=err, result=run.result)


def check_disappear(pattern: Pattern, template: str, place: Tuple[int, int]) -> None:
    show_at(pattern, *place)
    run = Run(wait_block(template, mode="disappear", timeout=8.0)).start()
    time.sleep(1.5)
    early = run.done.is_set()
    hide(pattern)
    t_hide = now()
    run.wait()
    after = round(run.t_end - t_hide, 3) if run.done.is_set() else None
    step("disappear — waits while shown, ends after it is hidden",
         (not early) and run.ok and after is not None and after < 1.0,
         ended_before_hidden=early, seconds_after_hidden=after, result=run.result)


def check_timeout(pattern: Pattern, template: str) -> None:
    hide(pattern)
    time.sleep(0.6)
    run = Run(wait_block(template, timeout=1.0)).start().wait()
    took = round(run.t_end - run.t_start, 3) if run.done.is_set() else None
    step("timeout — hidden pattern fails after 1 s (Stop with error)",
         run.result is not None and not run.ok and took is not None and 0.9 <= took <= 3.0,
         seconds=took, result=run.result, engine_state=str(run.eng.state))


def check_real_main_window(pattern: Pattern, template: str, templ: Tuple[int, int],
                           place: Tuple[int, int], work: Tuple[int, int, int, int]) -> None:
    """The real MainWindow: Image wait set through the inspector form, Run button, pattern appears."""
    from action_macro.gui.main_window import MainWindow

    show_at(pattern, *place)
    want = expected_centre(pattern_rect(pattern), templ)
    hide(pattern)
    holder: Dict[str, Any] = {}

    def build() -> None:
        win = MainWindow()
        win.resize(1000, 650)
        win.move(work[0] + 80, work[1] + 40)
        win.show()
        block = win.scenario_builder.add_action_block(ActionType.IMAGE_WAIT)
        win.scenario_builder.select_action(block.id)
        form = win.property_inspector.form_image
        form.edit_path.setText(template)
        form.spin_timeout.setValue(8.0)
        win.dashboard.set_loop_count(1)
        holder["win"] = win
        KEEP.append(win)
    gui(build)
    time.sleep(0.8)
    params = gui(lambda: holder["win"].scenario.actions[0].params.to_dict())
    if params.get("template_path") != template:
        step("real app (MainWindow → Image wait → Run)", False, reason="form did not reach the step",
             params=params)
        gui(lambda: holder["win"].hide())
        return
    gui(lambda: holder["win"].dashboard.btn_start.click())
    eng = gui(lambda: holder["win"].engine)
    time.sleep(1.5)
    running = gui(lambda: str(holder["win"].engine.state)) if eng is not None else None
    gui(lambda: (pattern.show(), pattern.raise_()))
    finished = eng.wait_for_completion(timeout=8.0) if eng is not None else False
    found = eng._last_detected_coords if eng is not None else None
    found = (int(found[0]), int(found[1])) if found else None
    err = error_px(found, want)
    state = str(eng.state) if eng is not None else None
    step("real app (MainWindow → Image wait → Run), ends when the pattern appears",
         bool(finished and err is not None and err <= 1 and "ERROR" not in str(state)),
         state_while_hidden=running, engine_state=state, expected=want, found=found, error_px=err)
    gui(lambda: holder["win"].hide())


def check_template_from(pattern: Pattern, other: Path, place: Tuple[int, int]) -> None:
    show_at(pattern, *place)
    rect = pattern_rect(pattern)
    run = Run(wait_block(str(other), timeout=1.0, timeout_action="skip")).start().wait()
    img = cv2.imread(str(other))
    info("template from another scale (Windows matches at the template's own size only)",
         template=str(other), template_size=(img.shape[1], img.shape[0]) if img is not None else None,
         window_physical=rect, found=run.found, result=run.result)


# --------------------------------------------------------------------------- main

def controller(app: QApplication, stage: Stage, pattern: Pattern, template_path: Path,
               template_from: Optional[Path]) -> None:
    def guarded(check: Callable[..., None], *args: Any) -> None:
        try:
            check(*args)
        except Exception as err:
            step(check.__name__, False, error=repr(err), tb=traceback.format_exc()[-800:])

    try:
        time.sleep(0.8)
        scr = main_screen()
        REPORT["screen"] = scr
        REPORT["scale_percent"] = round(scr["dpr"] * 100)
        work = scr["work"]
        show_at(stage, *work)
        places = positions(work)
        show_at(pattern, *places["corner"])
        templ = capture_template(pattern, template_path)
        info("template captured from the screen", path=str(template_path), size=templ,
             window_physical=pattern_rect(pattern), inset=INSET)
        template = str(template_path.resolve())
        guarded(check_found_positions, pattern, template, templ, places)
        guarded(check_roi, pattern, template, templ, places["opposite corner"], scr["dpr"])
        guarded(check_appear_after_delay, pattern, template, templ, places["centre"])
        guarded(check_disappear, pattern, template, places["centre"])
        guarded(check_timeout, pattern, template)
        guarded(check_real_main_window, pattern, template, templ, places["centre"], work)
        if template_from is not None:
            guarded(check_template_from, pattern, template_from, places["centre"])
    finally:
        REPORT["elapsed_s"] = round(now() - T0, 1)
        try:
            gui(lambda: (pattern.hide(), stage.hide()))
        except Exception:
            pass
        gui(app.quit)


def main(argv: Optional[List[str]] = None) -> int:
    global BRIDGE, REPORT_PATH
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", default="device_check_image_report.json", help="JSON report path")
    parser.add_argument("--timeout", type=float, default=90.0, help="watchdog seconds (exits 3)")
    parser.add_argument("--template-from", type=Path, default=None, metavar="PNG",
                        help="also try a template captured at another display scale (INFO only)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    REPORT_PATH = args.report
    faulthandler.enable(open(REPORT_PATH + ".crash.txt", "w"), all_threads=True)

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    BRIDGE = Bridge()
    stage, pattern = Stage(), Pattern()
    scale = round(app.primaryScreen().devicePixelRatio() * 100)
    template_path = Path(REPORT_PATH).resolve().parent / f"device_check_image_template_{scale}.png"
    if args.template_from is not None and not args.template_from.is_file():
        parser.error(f"--template-from: no such file {args.template_from}")

    def watchdog() -> None:
        time.sleep(args.timeout)
        print("WATCHDOG: aborting", flush=True)
        REPORT["watchdog"] = True
        save_report()
        os._exit(3)

    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=controller, args=(app, stage, pattern, template_path, args.template_from),
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
