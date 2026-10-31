"""ActionMacro emergency-stop check with PHYSICAL keys (Windows, interactive).

The global hotkey listener deliberately ignores injected key events (pynput's GlobalHotKeys
skips them), so the only way to verify "a human presses Esc and the macro stops" is to have
a human press it. This opens the real MainWindow, runs an infinite FIXED_WAIT macro (it
sends no mouse or keyboard input) with the real global hotkey listener, and asks you to press:

1. Esc — anywhere, whichever app has focus;
2. Ctrl+Shift+S — after the macro restarts by itself about 1.5 s later.

Usage::

    .venv\\Scripts\\python tools\\device_check_hotkeys_windows.py [--report PATH] [--timeout S]

Only the times of those two physical key-downs are recorded (a low-level keyboard hook that
keeps non-injected Esc and Ctrl+Shift+S only); nothing else you type is logged.

Exit codes: 0 both keys stopped the macro, 1 a key did not stop it as expected, 2 timeout.
It mirrors tools/device_check_hotkeys_macos.py.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

if sys.platform != "win32":
    sys.exit("device_check_hotkeys_windows.py runs on Windows only.")

os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QLabel  # noqa: E402

from action_macro.core.models import ActionType  # noqa: E402
from action_macro.gui.app import create_app  # noqa: E402
from action_macro.gui.main_window import MainWindow  # noqa: E402

EXPECTED = ["esc", "ctrl+shift+s"]
VK_ESCAPE, VK_S, VK_SHIFT, VK_CONTROL = 0x1B, 0x53, 0x10, 0x11
LLKHF_INJECTED = 0x10
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104

_USER32 = ctypes.WinDLL("user32")
_USER32.GetAsyncKeyState.restype, _USER32.GetAsyncKeyState.argtypes = ctypes.c_short, [ctypes.c_int]

key_times: List[Tuple[float, str]] = []


def _held(vk: int) -> bool:
    return bool(_USER32.GetAsyncKeyState(vk) & 0x8000)


def _filter(msg: int, data: Any) -> bool:
    """Records the times of physical Esc and Ctrl+Shift+S key-downs (injected events are dropped)."""
    if msg in (WM_KEYDOWN, WM_SYSKEYDOWN) and not data.flags & LLKHF_INJECTED:
        if data.vkCode == VK_ESCAPE:
            key_times.append((time.perf_counter(), "esc"))
        elif data.vkCode == VK_S and _held(VK_CONTROL) and _held(VK_SHIFT):
            key_times.append((time.perf_counter(), "ctrl+shift+s"))
    return False  # nothing for pynput's callbacks; the event itself goes on unchanged


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", default="device_check_hotkeys_report.json", help="JSON report path")
    parser.add_argument("--timeout", type=float, default=240.0, help="seconds to wait for the key presses")
    args = parser.parse_args(argv)
    # Redirected to a file, stdout uses the ANSI code page (cp949 on Korean Windows), which
    # has no em dash.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    app = create_app(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    from pynput.keyboard import Listener

    tap = Listener(win32_event_filter=_filter)
    tap.daemon = True
    tap.start()
    tap.wait()

    win = MainWindow()
    win.resize(900, 560)
    win.show()
    win.raise_()

    # The instructions in a window of their own, on top: run from a background shell, the
    # console output is not on screen, and Esc pressed in a terminal goes to that terminal.
    prompt = QLabel()
    prompt.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
    prompt.setWindowTitle("ActionMacro — emergency stop check / 긴급 정지 점검")
    prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
    prompt.setFont(QFont("Segoe UI", 18))
    prompt.setStyleSheet("background:#16324a; color:white; padding:24px;")
    prompt.resize(760, 220)
    screen = app.primaryScreen().availableGeometry()
    prompt.move(screen.x() + (screen.width() - 760) // 2, screen.y() + 60)

    def say(text: str) -> None:
        prompt.setText(text)
        prompt.show()
        prompt.raise_()
        prompt.activateWindow()
    win.scenario_builder.add_action_block(ActionType.FIXED_WAIT)  # 1000 ms, sends no input
    win.dashboard.set_loop_count(0)  # infinite

    results: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {"trial": 0, "started_at": None, "seen": 0,
                             "deadline": time.perf_counter() + args.timeout}

    def start_trial() -> None:
        state["started_at"] = time.perf_counter()
        state["seen"] = len(key_times)
        win.dashboard.btn_start.click()
        key = EXPECTED[state["trial"]].upper()
        print(f">>> Macro running (infinite). Press {key} now. "
              f"/ 매크로 실행 중 — 지금 {key} 를 한 번 누르세요.", flush=True)
        say(f"매크로 실행 중 ({state['trial'] + 1}/{len(EXPECTED)})\n\n지금 {key} 를 한 번 누르세요\n"
            f"Macro running — press {key} once now\n\n(이 창을 클릭해 포커스를 둔 채 누르세요)")

    def finish() -> None:
        timer.stop()
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)
        win.hide()
        QTimer.singleShot(1200, app.quit)  # leave the last message on screen a moment

    def poll() -> None:
        eng = win.engine
        now = time.perf_counter()
        if now > state["deadline"]:
            results.append({"trial": state["trial"] + 1, "timeout": True,
                            "physical_keys_seen": [k for _, k in key_times[state["seen"]:]],
                            "engine_state_at_timeout": eng.state.name if eng is not None else None})
            if eng is not None:
                eng.stop()
            say("시간 초과 — 점검을 끝냅니다\nTimed out")
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
            say(f"멈춤 확인 ({results[-1]['physical_key']}, {results[-1]['stop_latency_ms']} ms)\n"
                f"Stopped" + ("\n\n곧 다시 시작합니다 — restarting…" if state["trial"] + 1 < len(EXPECTED)
                              else "\n\n끝났습니다 — done"))
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
