"""
action_macro.gui.app
========================
PySide6 application instance initialization and modern dark styling.
"""

from __future__ import annotations

import sys
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

DARK_STYLESHEET = """
QMainWindow {
    background-color: #1E1E2E;
}
QWidget {
    background-color: #1E1E2E;
    color: #CDD6F4;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 12px;
}
QGroupBox {
    border: 1px solid #313244;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 4px;
    color: #89B4FA;
}
QToolBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    spacing: 4px;
    padding: 2px;
}
QMenuBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
}
QMenuBar::item:selected {
    background-color: #313244;
    border-radius: 4px;
}
QMenu {
    background-color: #181825;
    border: 1px solid #313244;
}
QMenu::item:selected {
    background-color: #45475A;
}
QProgressBar {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 4px;
    text-align: center;
    color: #CDD6F4;
}
QProgressBar::chunk {
    background-color: #89B4FA;
    border-radius: 3px;
}
QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 4px;
    padding: 3px 6px;
    color: #CDD6F4;
}
QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QComboBox:focus {
    border: 1px solid #89B4FA;
}
QSplitter::handle {
    background-color: #313244;
}
QSplitter::handle:horizontal {
    width: 2px;
}
QSplitter::handle:vertical {
    height: 2px;
}
QStatusBar {
    background-color: #181825;
    border-top: 1px solid #313244;
    color: #A6ADC8;
}
"""


def create_app(argv: Optional[List[str]] = None) -> QApplication:
    """Creates and configures the PySide6 QApplication singleton."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or sys.argv)

    app.setApplicationName("ActionMacro")
    app.setOrganizationName("ActionMacro")
    app.setApplicationVersion("0.1.0")

    app.setStyleSheet(DARK_STYLESHEET)
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
