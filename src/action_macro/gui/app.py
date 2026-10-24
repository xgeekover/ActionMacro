"""
action_macro.gui.app
========================
PySide6 application instance initialization and the dark/violet theme.
"""

from __future__ import annotations

import sys
from typing import List, Optional

from PySide6.QtWidgets import QApplication

from action_macro import __version__
from action_macro.gui.icons import app_icon
from action_macro.gui.theme import apply_theme, build_stylesheet


def __getattr__(name: str) -> str:
    """Lazily provides ``DARK_STYLESHEET`` (the theme style sheet) for older callers.

    The style sheet is built on demand because it writes small SVG files for the
    QSS sub-control images; importing this module must stay side-effect free.
    """
    if name == "DARK_STYLESHEET":
        return build_stylesheet()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def create_app(argv: Optional[List[str]] = None) -> QApplication:
    """Creates and configures the PySide6 QApplication singleton."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or sys.argv)

    app.setApplicationName("ActionMacro")
    app.setOrganizationName("ActionMacro")
    app.setApplicationVersion(__version__)

    # Fusion style + dark palette + platform UI font + the token-based style sheet.
    apply_theme(app)
    app.setWindowIcon(app_icon())
    return app


def run_app(argv: Optional[List[str]] = None, scenario_path: Optional[str] = None) -> int:
    """Launches the full interactive desktop application."""
    app = create_app(argv)

    # Read the keyboard layout on the main thread so pynput never has to (macOS).
    from action_macro.engine.macos_keyboard import refresh_keyboard_layout_cache

    refresh_keyboard_layout_cache()

    from action_macro.gui.main_window import MainWindow
    window = MainWindow()

    if scenario_path:
        window.open_scenario(scenario_path)

    window.show()
    return app.exec()
