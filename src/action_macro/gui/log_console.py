"""
action_macro.gui.log_console
================================
Real-time, thread-safe, color-coded execution log console widget.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass
class LogEntry:
    """Internal model for recorded log items."""
    timestamp: datetime
    level: str
    message: str

    def to_formatted_html(self) -> str:
        """Converts entry to formatted HTML with level-specific styling."""
        ts_str = self.timestamp.strftime("%H:%M:%S.%f")[:-3]
        lvl = self.level.upper()
        escaped_msg = html.escape(self.message)

        color_map = {
            "INFO": "#4A90E2",
            "WARNING": "#FFA726",
            "ERROR": "#EF5350",
            "ACTION": "#66BB6A",
            "DEBUG": "#90A4AE",
        }
        color = color_map.get(lvl, "#B0BEC5")

        return (
            f'<span style="color: #78909C;">[{ts_str}]</span> '
            f'<span style="color: {color}; font-weight: bold;">[{lvl:<7}]</span> '
            f'<span style="color: #ECEFF1;">{escaped_msg}</span>'
        )

    def to_plain_text(self) -> str:
        """Converts entry to clean plain text for clipboard copying."""
        ts_str = self.timestamp.strftime("%H:%M:%S.%f")[:-3]
        return f"[{ts_str}] [{self.level.upper():<7}] {self.message}"


class LogConsole(QWidget):
    """Real-time log console widget displaying color-coded automation logs."""

    MAX_BLOCK_COUNT = 5000

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._entries: List[LogEntry] = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header toolbar
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        title_label = QLabel("Execution Log")
        font = title_label.font()
        font.setBold(True)
        title_label.setFont(font)
        header_layout.addWidget(title_label)

        header_layout.addSpacing(10)

        # Filter combo
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All Levels", "INFO", "WARNING", "ERROR", "ACTION"])
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        header_layout.addWidget(self.filter_combo)

        # Auto-scroll checkbox
        self.auto_scroll_cb = QCheckBox("Auto-scroll")
        self.auto_scroll_cb.setChecked(True)
        header_layout.addWidget(self.auto_scroll_cb)

        header_layout.addStretch()

        # Action buttons
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setToolTip("Copy displayed logs to clipboard")
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        header_layout.addWidget(self.btn_copy)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setToolTip("Clear all console logs")
        self.btn_clear.clicked.connect(self.clear_logs)
        header_layout.addWidget(self.btn_clear)

        layout.addLayout(header_layout)

        # Log display area
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(self.MAX_BLOCK_COUNT)

        # Configure monospace font
        fixed_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed_font.setPointSize(10)
        self.text_edit.setFont(fixed_font)

        # Styling
        self.text_edit.setStyleSheet(
            "QPlainTextEdit {"
            "  background-color: #181825;"
            "  color: #CDD6F4;"
            "  border: 1px solid #313244;"
            "  border-radius: 4px;"
            "  padding: 4px;"
            "}"
        )
        layout.addWidget(self.text_edit)

    def set_max_block_count(self, count: int) -> None:
        """Configures the maximum lines in both the text edit and internal buffer."""
        self.MAX_BLOCK_COUNT = count
        self.text_edit.setMaximumBlockCount(count)


    @Slot(str, str)
    def append_log(self, level: str, message: str) -> None:
        """Thread-safe slot to append a log message."""
        entry = LogEntry(timestamp=datetime.now(), level=level, message=message)
        self._entries.append(entry)

        # Trim internal list to prevent unbounded growth
        if len(self._entries) > self.MAX_BLOCK_COUNT:
            self._entries = self._entries[-self.MAX_BLOCK_COUNT :]

        current_filter = self.filter_combo.currentText()
        if current_filter == "All Levels" or current_filter.upper() == entry.level.upper():
            self.text_edit.appendHtml(entry.to_formatted_html())

            if self.auto_scroll_cb.isChecked():
                self.text_edit.moveCursor(QTextCursor.MoveOperation.End)

    @Slot()
    def clear_logs(self) -> None:
        """Clears both the text display and internal entry buffer."""
        self._entries.clear()
        self.text_edit.clear()

    @Slot()
    def copy_to_clipboard(self) -> None:
        """Copies the currently filtered log contents to the system clipboard."""
        current_filter = self.filter_combo.currentText()
        lines: List[str] = []
        for e in self._entries:
            if current_filter == "All Levels" or current_filter.upper() == e.level.upper():
                lines.append(e.to_plain_text())

        content = "\n".join(lines)
        QApplication.clipboard().setText(content)

    @Slot(int)
    def _on_filter_changed(self, index: int) -> None:
        """Re-renders the display when the level filter changes."""
        self.text_edit.clear()
        current_filter = self.filter_combo.currentText()
        for e in self._entries:
            if current_filter == "All Levels" or current_filter.upper() == e.level.upper():
                self.text_edit.appendHtml(e.to_formatted_html())

        if self.auto_scroll_cb.isChecked():
            self.text_edit.moveCursor(QTextCursor.MoveOperation.End)
