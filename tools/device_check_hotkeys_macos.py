"""ActionMacro emergency-stop check with PHYSICAL keys (macOS, interactive).

The global hotkey listener deliberately ignores injected key events (pynput's GlobalHotKeys
skips them), so the only way to verify "a human presses Esc and the macro stops" is to have
a human press it. This opens the real MainWindow, runs an infinite FIXED_WAIT macro (it
sends no mouse or keyboard input) with the real global hotkey listener, and asks you to press:

1. Esc — anywhere, whichever app has focus;
2. Ctrl+Shift+S — after the macro restarts by itself about 1.5 s later.

Usage::

    .venv/bin/python tools/device_check_hotkeys_macos.py [--report PATH] [--timeout S]

Only the times of those two physical key-downs are recorded (a listen-only event tap that
keeps key codes 53 and 1 from other processes); nothing else you type is logged.

Exit codes: 0 both keys stopped the macro, 1 a key did not stop it as expected, 2 timeout.
2026-10-24 baseline (macOS 26.6): Esc stopped it in 24 ms, Ctrl+Shift+S in under 1 ms.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "darwin":
    sys.exit("device_check_hotkeys_macos.py runs on macOS only.")

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

import Quartz  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

from action_macro.core.models import ActionType  # noqa: E402
from action_macro.engine.macos_keyboard import refresh_keyboard_layout_cache  # noqa: E402
from action_macro.gui.app import create_app  # noqa: E402
from action_macro.gui.main_window import MainWindow  # noqa: E402

PID = os.getpid()
EXPECTED = ["esc", "ctrl+shift+s"]
KC_ESC, KC_S = 53, 1

key_times: List[Tuple[float, str]] = []


def tap_thread() -> None:
    """Records the times of physical Esc and Ctrl+Shift+S key-downs (other processes only)."""

    def cb(proxy: Any, etype: int, event: Any, refcon: Any) -> Any:
        if etype != Quartz.kCGEventKeyDown:
            return event
        if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID) == PID:
            return event
        kc = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        flags = Quartz.CGEventGetFlags(event)
        if kc == KC_ESC:
            key_times.append((time.perf_counter(), "esc"))
        elif kc == KC_S and flags & Quartz.kCGEventFlagMaskControl and flags & Quartz.kCGEventFlagMaskShift:
            key_times.append((time.perf_counter(), "ctrl+shift+s"))
        return event

    tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                  Quartz.kCGEventTapOptionListenOnly, Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
                                  cb, None)
    if not tap:
        print("Could not create the key event tap (Input Monitoring permission?)", flush=True)
        return
    src = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    Quartz.CFRunLoopRun()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", default="device_check_hotkeys_report.json", help="JSON report path")
    parser.add_argument("--timeout", type=float, default=240.0, help="seconds to wait for the key presses")
    args = parser.parse_args(argv)

    app = create_app(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    refresh_keyboard_layout_cache()  # as run_app() does
    threading.Thread(target=tap_thread, name="KeyTap", daemon=True).start()

    win = MainWindow()
    win.resize(900, 560)
    win.show()
    win.raise_()
    win.scenario_builder.add_action_block(ActionType.FIXED_WAIT)  # 1000 ms, sends no input
    win.dashboard.set_loop_count(0)  # infinite

    results: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {"trial": 0, "started_at": None, "seen": 0,
                             "deadline": time.perf_counter() + args.timeout}

    def start_trial() -> None:
        state["started_at"] = time.perf_counter()
        state["seen"] = len(key_times)
        win.dashboard.btn_start.click()
        print(f">>> Macro running (infinite). Press {EXPECTED[state['trial']].upper()} now.", flush=True)

    def finish() -> None:
        timer.stop()
        with open(args.report, "w") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)
        win.hide()
        app.quit()

    def poll() -> None:
        eng = win.engine
        now = time.perf_counter()
        if now > state["deadline"]:
            results.append({"trial": state["trial"] + 1, "timeout": True,
                            "physical_keys_seen": [k for _, k in key_times[state["seen"]:]],
                            "engine_state_at_timeout": eng.state.name if eng is not None else None})
            if eng is not None:
                eng.stop()
            finish()
            return
        if eng is None or state["started_at"] is None:
            return
        if eng.state.name in ("STOPPED", "STOPPING", "IDLE") and now - state["started_at"] > 0.3:
            keys = key_times[state["seen"]:]
            first = keys[0] if keys else None
            results.append({
                "trial": state["trial"] + 1,
                "expected_key": EXPECTED[state["trial"]],
                "physical_key": first[1] if first else None,
                # Measured to the first observed stop state; 2 ms polling.
                "stop_latency_ms": round((now - first[0]) * 1000, 1) if first else None,
                "engine_state": eng.state.name,
                "worker_exited": eng.wait_for_completion(timeout=0.5),
            })
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
            state["trial"] += 1
            state["started_at"] = None
            if state["trial"] < len(EXPECTED):
                QTimer.singleShot(1500, start_trial)
            else:
                finish()

    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(2)
    QTimer.singleShot(800, start_trial)
    app.exec()

    if any(r.get("timeout") for r in results):
        code = 2
    elif len(results) == len(EXPECTED) and all(
        r["physical_key"] == r["expected_key"] and r["worker_exited"] for r in results
    ):
        code = 0
    else:
        code = 1
    print(f"done — report: {args.report} (exit {code})", flush=True)
    os._exit(code)  # skip Qt teardown of the hidden window and the listener threads


if __name__ == "__main__":
    sys.exit(main())
