"""
action_macro.selftest
=========================
Start-up self-test for packaged builds (``ActionMacro --self-test REPORT.json``).

A frozen app (PyInstaller) can build fine and still fail on a user's machine because
a native library, a Qt plugin or one of pynput's platform back ends was left out.
The release pipeline runs this check on every packaged binary before publishing it.

It never generates input or captures the screen: the Qt window is built on the
``offscreen`` platform, pynput back ends are only imported, mss is only imported and
OpenCV matches two in-memory arrays. Settings go to a temporary INI file, so the
user's settings are not touched. macOS permission state is read passively (no prompt).

The report is JSON (``ok``, per-check results, versions); the exit code is 0 when
every check passed and 1 otherwise. Windows GUI builds have no console, so the report
file is the way to read the result there.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


def _check_opencv() -> Dict[str, Any]:
    import cv2
    import numpy as np

    haystack = np.zeros((40, 60), dtype=np.uint8)
    haystack[10:20, 30:45] = 255
    needle = haystack[8:22, 28:47].copy()
    result = cv2.matchTemplate(haystack, needle, cv2.TM_CCOEFF_NORMED)
    _, score, _, location = cv2.minMaxLoc(result)
    if location != (28, 8) or score < 0.99:
        raise RuntimeError(f"template matching returned {location} (score {score:.3f})")
    return {"opencv": cv2.__version__, "numpy": np.__version__}


def _check_input_backends() -> Dict[str, Any]:
    import pynput.keyboard
    import pynput.mouse

    names = {
        "keyboard": pynput.keyboard.Controller.__module__,
        "mouse": pynput.mouse.Controller.__module__,
    }
    expected = {"darwin": "_darwin", "win32": "_win32"}.get(sys.platform)
    if expected and not all(name.endswith(expected) for name in names.values()):
        raise RuntimeError(f"unexpected pynput back ends: {names}")
    if sys.platform == "win32":
        # Text steps type through SendInput with pynput's win32 structures. Build the
        # events for one character (a surrogate pair) and hand them to a stub, not to
        # SendInput, so nothing is typed.
        from action_macro.engine.input_adapter import make_win32_unicode_sender

        built: List[int] = []
        make_win32_unicode_sender(send_input=lambda count, _events, _size: built.append(count) or count)("😀")
        if built != [4]:
            raise RuntimeError(f"unexpected Unicode input events: {built}")
        names["text"] = "unicode packets"
    return names


def _check_screen_capture_module() -> Dict[str, Any]:
    import mss

    return {"mss": getattr(mss, "__version__", "?")}


def _check_keyboard_layout() -> Dict[str, Any]:
    from action_macro.engine.macos_keyboard import refresh_keyboard_layout_cache

    refresh_keyboard_layout_cache()
    if sys.platform == "darwin":
        # Text steps switch to an ASCII input source through these bindings; reading
        # the current source loads them without changing anything.
        from action_macro.engine import macos_input_source

        return {"input_source": macos_input_source.current_source_id()}
    return {}


def _check_gui(settings_dir: Path) -> Dict[str, Any]:
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import QSettings, qVersion
    from PySide6.QtWidgets import QApplication

    from action_macro.gui import icons
    from action_macro.gui.app import create_app
    from action_macro.gui.main_window import MainWindow

    app = create_app([sys.argv[0]])
    if icons.app_icon().isNull():
        raise RuntimeError("the application icon did not render (QtSvg)")
    settings = QSettings(str(settings_dir / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    window.resize(1400, 900)
    window.show()
    QApplication.processEvents()
    image = window.grab().toImage()
    if image.isNull() or image.width() < 100:
        raise RuntimeError("the main window did not render")
    window.mark_dirty(False)
    window.close()
    QApplication.processEvents()
    return {
        "qt": qVersion(),
        "pyside6": pyside_version,
        "qpa_platform": app.platformName(),
        "styles_applied": bool(app.styleSheet()),
    }


def run_self_test(report_path: Optional[str] = None) -> int:
    """Runs every check, writes the JSON report and returns the exit code."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from action_macro import __version__

    report: Dict[str, Any] = {
        "app_version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "checks": {},
    }
    with tempfile.TemporaryDirectory(prefix="actionmacro-selftest-") as tmp:
        checks: List[tuple[str, Callable[[], Dict[str, Any]]]] = [
            ("opencv", _check_opencv),
            ("input_backends", _check_input_backends),
            ("screen_capture_module", _check_screen_capture_module),
            ("keyboard_layout", _check_keyboard_layout),
            ("gui", lambda: _check_gui(Path(tmp))),
        ]
        for name, check in checks:
            try:
                report["checks"][name] = {"ok": True, **check()}
            except Exception as exc:  # report every failure, keep checking the rest
                report["checks"][name] = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=8),
                }
    report["ok"] = all(result["ok"] for result in report["checks"].values())

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if report_path:
        Path(report_path).write_text(text + "\n", encoding="utf-8")
    if sys.stdout is not None:
        print(text)
    return 0 if report["ok"] else 1
