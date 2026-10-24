"""
action_macro.gui.log_console
================================
Real-time, thread-safe, colour-coded execution log console (the bottom panel).

Layout: a 36 px header row (``CONSOLE`` label, entry count, the level filter as
a compact segmented control, auto-scroll / copy / clear icon buttons and a
chevron that asks the window to hide the panel) above a monospace log view. Each
line shows a muted timestamp, a level tag coloured from the theme tokens and the
message.

The hidden ``filter_combo`` stays the single source of truth for the level
filter (callers and tests drive it with ``setCurrentText``); the segmented
control (:class:`components.SegmentedControl`, bound to the combo) mirrors it in
both directions. ``filter_chips`` maps each combo item to its segment button.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QIcon, QPainter, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from action_macro.gui import icons, theme
from action_macro.gui.components import SectionLabel, SegmentedControl, Separator, make_icon_button

#: Items of ``filter_combo`` (order and wording are part of the public contract).
ALL_LEVELS = "All Levels"
FILTER_LEVELS: Tuple[str, ...] = (ALL_LEVELS, "INFO", "WARNING", "ERROR", "ACTION")

#: Filter chips in display order: (chip label, ``filter_combo`` item).
FILTER_CHIPS: Tuple[Tuple[str, str], ...] = (
    ("All", ALL_LEVELS),
    ("Info", "INFO"),
    ("Action", "ACTION"),
    ("Warning", "WARNING"),
    ("Error", "ERROR"),
)
_CHIP_LABELS: Dict[str, str] = {value: label for label, value in FILTER_CHIPS}

#: Level tag colour per log level (theme tokens only).
LEVEL_COLORS: Dict[str, str] = {
    "INFO": theme.COLORS.info,
    "ACTION": theme.COLORS.success,
    "WARNING": theme.COLORS.warning,
    "ERROR": theme.COLORS.danger,
    "DEBUG": theme.COLORS.text_muted,  # informative text stays at 4.5:1 or more (never text-faint)
}

#: Width of the level column in characters ("WARNING" is the longest built-in level).
LEVEL_TAG_WIDTH = 7

#: Height of the console header row.
HEADER_HEIGHT = 36

#: Pixel size of the two spaces between the columns of a log line.
#: ``QPlainTextEdit`` ignores block line-height and margins, so these slightly larger
#: (invisible) separator spaces give the 12 px text a comfortable ~19 px line height
#: without adding any character to the text (``toPlainText`` shows plain spaces).
_COLUMN_GAP_PX = theme.FONT_BODY + 4

#: How long the Copy button shows its "copied" check mark.
_COPY_FEEDBACK_MS = 1200


def level_color(level: str) -> str:
    """Returns the tag colour of ``level`` (case-insensitive); unknown levels are muted."""
    return LEVEL_COLORS.get(str(level).upper(), theme.COLORS.text_muted)


@dataclass
class LogEntry:
    """Internal model for recorded log items."""
    timestamp: datetime
    level: str
    message: str

    def matches(self, level_filter: str) -> bool:
        """True when the entry passes ``level_filter`` (a ``filter_combo`` item)."""
        return level_filter == ALL_LEVELS or level_filter.upper() == self.level.upper()

    def to_formatted_html(self) -> str:
        """Converts the entry to one console line: muted timestamp, coloured level tag, message."""
        c = theme.COLORS
        ts_str = self.timestamp.strftime("%H:%M:%S.%f")[:-3]
        lvl = self.level.upper()
        tag = html.escape(lvl) + "&nbsp;" * max(0, LEVEL_TAG_WIDTH - len(lvl))
        gap = f'<span style="font-size: {_COLUMN_GAP_PX}px;">&nbsp;&nbsp;</span>'
        return (
            f'<span style="color: {c.text_muted};">{ts_str}</span>{gap}'
            f'<span style="color: {level_color(lvl)}; font-weight: 600;">{tag}</span>{gap}'
            f'<span style="color: {c.text};">{html.escape(self.message)}</span>'
        )

    def to_plain_text(self) -> str:
        """Converts entry to clean plain text for clipboard copying."""
        ts_str = self.timestamp.strftime("%H:%M:%S.%f")[:-3]
        return f"[{ts_str}] [{self.level.upper():<7}] {self.message}"


#: Level marker of a filter chip: dot diameter and the transparent gap to its label.
_CHIP_DOT = 6
_CHIP_DOT_GAP = 4


def _dot_icon(color: str, diameter: int = _CHIP_DOT, gap: int = _CHIP_DOT_GAP) -> QIcon:
    """Returns a small filled circle in ``color`` followed by ``gap`` transparent pixels.

    The gap separates the dot from the chip label (QPushButton has no QSS for the
    icon-text spacing).
    """
    dpr = 2.0
    pixmap = QPixmap(int((diameter + gap) * dpr), int(diameter * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(theme.qcolor(color))
    painter.drawEllipse(0, 0, diameter, diameter)
    painter.end()
    return QIcon(pixmap)


class LogConsole(QWidget):
    """Real-time log console widget displaying color-coded automation logs."""

    MAX_BLOCK_COUNT = 5000

    #: Emitted by the header chevron: the owner should hide the console panel.
    collapse_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("LogConsole")
        self._entries: List[LogEntry] = []
        self._shown = 0  # entries that pass the current filter (i.e. are displayed)
        self._count_text: Optional[str] = None
        self._setup_ui()
        self._refresh_state()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_XS)

        layout.addWidget(self._build_header())

        # Log display area
        self.text_edit = QPlainTextEdit()
        self.text_edit.setObjectName("ConsoleView")
        self.text_edit.setReadOnly(True)
        self.text_edit.setMaximumBlockCount(self.MAX_BLOCK_COUNT)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Monospace font and console styling come from the theme
        self.text_edit.setFont(theme.mono_font(theme.FONT_BODY))
        self.text_edit.setProperty("role", "console")
        layout.addWidget(self.text_edit, stretch=1)

        # Empty-state line, centred in the view like the app's other empty states. It is
        # a label on the viewport, so it never becomes part of the log text.
        self.lbl_empty = QLabel("No log entries yet.", self.text_edit.viewport())
        self.lbl_empty.setObjectName("ConsoleEmpty")
        self.lbl_empty.setFont(theme.ui_font(theme.FONT_BODY))
        self.lbl_empty.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lbl_empty.adjustSize()
        self.text_edit.viewport().installEventFilter(self)
        self._place_empty_label()

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("ConsoleHeader")
        header.setFixedHeight(HEADER_HEIGHT)
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.SPACE_SM)

        self.lbl_title = SectionLabel("Console")
        row.addWidget(self.lbl_title)

        # Entry count: the same badge as the STEPS count. It keeps its place while hidden
        # (empty console), so the filter does not shift when the first entry arrives.
        self.lbl_count = QLabel("0")
        self.lbl_count.setObjectName("ConsoleCount")
        self.lbl_count.setProperty("role", "count")
        self.lbl_count.setFont(theme.ui_font(theme.FONT_SMALL, QFont.Weight.DemiBold))
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_count.setFixedHeight(18)
        self.lbl_count.setMinimumWidth(theme.count_badge_min_width())
        policy = self.lbl_count.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.lbl_count.setSizePolicy(policy)
        row.addWidget(self.lbl_count)
        row.addSpacing(theme.SPACE_SM)

        # Source of truth for the filter (kept for callers and tests; not shown).
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(list(FILTER_LEVELS))
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        self.filter_combo.setVisible(False)

        # Level filter: a compact segmented control (single choice) bound to filter_combo.
        self.filter_segments = SegmentedControl(
            [(value, label, "Show all entries" if value == ALL_LEVELS else f"Show only {label.lower()} entries")
             for label, value in FILTER_CHIPS],
            compact=True,
        )
        self.filter_chips: Dict[str, QPushButton] = {}
        for _label, value in FILTER_CHIPS:
            chip = self.filter_segments.button(value)
            assert chip is not None
            if value != ALL_LEVELS:
                chip.setIcon(_dot_icon(level_color(value)))
                chip.setIconSize(QSize(_CHIP_DOT + _CHIP_DOT_GAP, _CHIP_DOT))
            self.filter_chips[value] = chip
        self.filter_segments.bind_combo(self.filter_combo)
        row.addWidget(self.filter_segments)
        row.addWidget(self.filter_combo)

        row.addStretch(1)

        # Auto-scroll toggle (checked = follow the newest entry). Checked shows a tinted
        # icon only: an accent background made it the loudest control of the header.
        self.auto_scroll_cb = make_icon_button("chevrons-down", "Auto-scroll to the newest entry",
                                               checkable=True)
        self.auto_scroll_cb.setObjectName("AutoScrollButton")
        self.auto_scroll_cb.setChecked(True)
        self.auto_scroll_cb.toggled.connect(self._on_auto_scroll_toggled)
        row.addWidget(self.auto_scroll_cb)

        # Action buttons
        self.btn_copy = make_icon_button("copy", "Copy the shown entries")
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        row.addWidget(self.btn_copy)
        self._copy_icon = self.btn_copy.icon()
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.setInterval(_COPY_FEEDBACK_MS)
        self._copy_timer.timeout.connect(self._reset_copy_feedback)

        self.btn_clear = make_icon_button("trash", "Clear the console")
        self.btn_clear.clicked.connect(self.clear_logs)
        row.addWidget(self.btn_clear)

        row.addWidget(Separator(vertical=True, length=16))

        self.btn_collapse = make_icon_button("chevron-down", "Hide console")
        self.btn_collapse.clicked.connect(self.collapse_requested)
        row.addWidget(self.btn_collapse)
        return header

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_max_block_count(self, count: int) -> None:
        """Configures the maximum lines in both the text edit and internal buffer."""
        self.MAX_BLOCK_COUNT = count
        current_filter = self.filter_combo.currentText()
        excess = len(self._entries) - count
        if count > 0 and excess > 0:
            removed = self._entries[:excess]
            del self._entries[:excess]
            self._drop_view_lines(sum(1 for e in removed if e.matches(current_filter)))
        self.text_edit.setMaximumBlockCount(count)
        self._shown = sum(1 for e in self._entries if e.matches(current_filter))
        self._refresh_state()

    @Slot(str, str)
    def append_log(self, level: str, message: str) -> None:
        """Thread-safe slot to append a log message."""
        entry = LogEntry(timestamp=datetime.now(), level=level, message=message)
        self._entries.append(entry)
        current_filter = self.filter_combo.currentText()

        # Trim internal list to prevent unbounded growth (0 means "no limit", as in Qt)
        excess = len(self._entries) - self.MAX_BLOCK_COUNT
        if self.MAX_BLOCK_COUNT > 0 and excess > 0:
            removed = self._entries[:excess]
            del self._entries[:excess]
            dropped = sum(1 for e in removed if e.matches(current_filter))
            self._shown -= dropped
            self._drop_view_lines(dropped)

        if entry.matches(current_filter):
            self.text_edit.appendHtml(entry.to_formatted_html())
            self._shown += 1

            if self.auto_scroll_cb.isChecked():
                self.text_edit.moveCursor(QTextCursor.MoveOperation.End)

        self._refresh_state()

    @Slot()
    def clear_logs(self) -> None:
        """Clears both the text display and internal entry buffer."""
        self._entries.clear()
        self.text_edit.clear()
        self._shown = 0
        self._refresh_state()

    @Slot()
    def copy_to_clipboard(self) -> None:
        """Copies the currently filtered log contents to the system clipboard."""
        current_filter = self.filter_combo.currentText()
        lines: List[str] = [e.to_plain_text() for e in self._entries if e.matches(current_filter)]

        content = "\n".join(lines)
        QApplication.clipboard().setText(content)
        self._show_copy_feedback()

    def entry_count(self) -> int:
        """Returns the number of buffered entries (all levels)."""
        return len(self._entries)

    def shown_count(self) -> int:
        """Returns the number of entries that pass the current level filter."""
        return self._shown

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _drop_view_lines(self, count: int) -> None:
        """Removes the first ``count`` displayed lines (their entries just left the buffer).

        Unfiltered, the view holds every entry and its own block limit already drops
        the oldest line on the next append; filtered, it holds fewer lines than the
        limit, so trimmed entries would otherwise stay on screen.
        """
        if count <= 0 or self.filter_combo.currentText() == ALL_LEVELS:
            return
        doc = self.text_edit.document()
        if count >= doc.blockCount() or not doc.toPlainText():
            self.text_edit.clear()
            return
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.NextBlock, QTextCursor.MoveMode.KeepAnchor, count)
        cursor.removeSelectedText()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self.text_edit.viewport() and event.type() == QEvent.Type.Resize:
            self._place_empty_label()
        return super().eventFilter(watched, event)

    def _place_empty_label(self) -> None:
        """Centres the empty-state line in the log view."""
        viewport = self.text_edit.viewport().rect()
        label = self.lbl_empty
        label.move(viewport.center().x() - label.width() // 2, viewport.center().y() - label.height() // 2)

    @Slot(int)
    def _on_filter_changed(self, index: int) -> None:
        """Re-renders the display when the level filter changes."""
        self.text_edit.clear()
        current_filter = self.filter_combo.currentText()
        shown = 0
        for e in self._entries:
            if e.matches(current_filter):
                self.text_edit.appendHtml(e.to_formatted_html())
                shown += 1
        self._shown = shown

        if self.auto_scroll_cb.isChecked():
            self.text_edit.moveCursor(QTextCursor.MoveOperation.End)
        self._refresh_state()

    @Slot(bool)
    def _on_auto_scroll_toggled(self, checked: bool) -> None:
        if checked:
            self.text_edit.moveCursor(QTextCursor.MoveOperation.End)

    def _refresh_state(self) -> None:
        """Updates the count badge, the empty-state line and the Copy / Clear buttons."""
        total = len(self._entries)
        current_filter = self.filter_combo.currentText()
        filtered = current_filter != ALL_LEVELS

        # Hidden while empty, but keeping a "0" so its reserved width matches "1".."9".
        count_text = (f"{self._shown} / {total}" if filtered else str(total)) if total else "0"
        if count_text != self._count_text:
            self._count_text = count_text
            self.lbl_count.setText(count_text)
            self.lbl_count.setToolTip(f"{self._shown} of {total} entries shown" if filtered else
                                      f"{total} {'entry' if total == 1 else 'entries'}")
        if self.lbl_count.isHidden() == bool(total):
            self.lbl_count.setVisible(bool(total))

        is_empty = self._shown == 0
        if self.lbl_empty.isHidden() == is_empty:
            if is_empty:
                # Scrolling the log moves viewport children (QWidget.scroll), so re-anchor it.
                self._place_empty_label()
            self.lbl_empty.setVisible(is_empty)
        if is_empty:
            if total == 0:
                text = "No log entries yet."
            else:
                label = _CHIP_LABELS.get(current_filter, current_filter)
                text = f"No {label.lower()} entries."
            if self.lbl_empty.text() != text:
                self.lbl_empty.setText(text)
                self.lbl_empty.adjustSize()
                self._place_empty_label()

        if self.btn_copy.isEnabled() == is_empty:
            self.btn_copy.setEnabled(not is_empty)
        if self.btn_clear.isEnabled() == (total == 0):
            self.btn_clear.setEnabled(total != 0)

    def _show_copy_feedback(self) -> None:
        self.btn_copy.setIcon(icons.icon("check", theme.COLORS.success, 16))
        self.btn_copy.setToolTip("Copied")
        self._copy_timer.start()

    @Slot()
    def _reset_copy_feedback(self) -> None:
        self.btn_copy.setIcon(self._copy_icon)
        self.btn_copy.setToolTip("Copy the shown entries")
