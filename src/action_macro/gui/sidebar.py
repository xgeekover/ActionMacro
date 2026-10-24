"""
action_macro.gui.sidebar
===========================
Scenario sidebar: New / Open, a filter box and the list of recent scenario files.

Recent files are persisted with ``QSettings`` (organisation/application
"ActionMacro") through :class:`RecentScenarioStore`. The sidebar highlights the
scenario being edited (or an unsaved draft), greys out files that no longer exist
and lets the user remove entries from the list.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Union

from PySide6.QtCore import QModelIndex, QPoint, QRect, QSettings, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QFont, QKeyEvent, QKeySequence, QMouseEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from action_macro.gui import icons, theme
from action_macro.gui.components import SectionLabel, make_icon_button, style_button, with_shortcut

PathLike = Union[str, Path]

# Item data roles
PATH_ROLE = Qt.ItemDataRole.UserRole + 1
NAME_ROLE = Qt.ItemDataRole.UserRole + 2
MISSING_ROLE = Qt.ItemDataRole.UserRole + 3
CURRENT_ROLE = Qt.ItemDataRole.UserRole + 4
DRAFT_ROLE = Qt.ItemDataRole.UserRole + 5


def _normalize(path: PathLike) -> str:
    """Returns an absolute, user-expanded path string used as the list identity."""
    expanded = Path(path).expanduser()
    try:
        return str(expanded.resolve())
    except OSError:
        return str(expanded.absolute())


class RecentScenarioStore:
    """Most-recently-used scenario files persisted in ``QSettings``."""

    ORGANIZATION = "ActionMacro"
    APPLICATION = "ActionMacro"
    KEY = "recent/scenarios"
    MAX_ENTRIES = 15

    def __init__(self, settings: Optional[QSettings] = None) -> None:
        self._settings = settings if settings is not None else self.default_settings()

    @classmethod
    def default_settings(cls) -> QSettings:
        """Returns the application's user-scope settings (INI format, all platforms)."""
        return QSettings(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            cls.ORGANIZATION,
            cls.APPLICATION,
        )

    @property
    def settings(self) -> QSettings:
        """The underlying QSettings object."""
        return self._settings

    def paths(self) -> List[str]:
        """Returns the stored paths, most recent first, without duplicates."""
        raw = self._settings.value(self.KEY, [])
        if raw is None:
            raw = []
        elif isinstance(raw, str):
            raw = [raw] if raw else []
        result: List[str] = []
        for entry in raw:
            text = str(entry).strip()
            if text and text not in result:
                result.append(text)
        return result[: self.MAX_ENTRIES]

    def add(self, path: PathLike) -> str:
        """Moves ``path`` to the top of the list (adding it if new); returns its key."""
        key = _normalize(path)
        entries = [p for p in self.paths() if p != key]
        entries.insert(0, key)
        self._write(entries[: self.MAX_ENTRIES])
        return key

    def remove(self, path: PathLike) -> bool:
        """Removes ``path`` from the list; returns True if it was present."""
        key = str(path)
        entries = self.paths()
        if key not in entries:
            key = _normalize(path)
        if key not in entries:
            return False
        entries.remove(key)
        self._write(entries)
        return True

    def clear(self) -> None:
        """Forgets every recent scenario."""
        self._write([])

    def _write(self, entries: List[str]) -> None:
        if entries:
            self._settings.setValue(self.KEY, entries)
        else:
            self._settings.remove(self.KEY)
        self._settings.sync()


class _RecentItemDelegate(QStyledItemDelegate):
    """Paints a recent-scenario row: icon, name, folder (or "File not found"), remove button."""

    ROW_HEIGHT = 44
    REMOVE_SIZE = 22

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    @classmethod
    def row_rect(cls, rect: QRect) -> QRect:
        return rect.adjusted(6, 2, -6, -2)

    @classmethod
    def remove_rect(cls, rect: QRect) -> QRect:
        row = cls.row_rect(rect)
        size = cls.REMOVE_SIZE
        return QRect(row.right() - size - 4, row.center().y() - size // 2, size, size)

    @staticmethod
    def show_remove(option: QStyleOptionViewItem, index: QModelIndex) -> bool:
        if index.data(DRAFT_ROLE):
            return False
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        return hovered or bool(index.data(MISSING_ROLE))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        c = theme.COLORS
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        row = self.row_rect(option.rect)
        current = bool(index.data(CURRENT_ROLE))
        missing = bool(index.data(MISSING_ROLE))
        draft = bool(index.data(DRAFT_ROLE))
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        has_focus = bool(option.state & QStyle.StateFlag.State_HasFocus)

        # Row background
        background = None
        if current:
            background = theme.qcolor(c.accent, 0.16)
        elif selected and has_focus:
            background = theme.qcolor(c.surface_3)
        elif hovered:
            background = theme.qcolor(c.surface_2)
        if background is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(row, theme.RADIUS_SM, theme.RADIUS_SM)

        # Leading icon
        if current:
            icon_name, icon_color = "circle-dot", c.accent_text
        elif missing:
            icon_name, icon_color = "file-x", c.text_faint
        elif draft:
            icon_name, icon_color = "file-plus", c.text_muted
        else:
            icon_name, icon_color = "file-text", c.text_muted
        icon_rect = QRect(row.left() + 10, row.center().y() - 8, 16, 16)
        painter.drawPixmap(icon_rect, icons.pixmap(icon_name, icon_color, 16))

        # Text column
        show_remove = self.show_remove(option, index)
        text_left = icon_rect.right() + 10
        text_right = (self.remove_rect(option.rect).left() - 6) if show_remove else row.right() - 10
        text_width = max(0, text_right - text_left)

        name_font = theme.ui_font(theme.FONT_BODY, QFont.Weight.DemiBold if current else QFont.Weight.Medium)
        painter.setFont(name_font)
        name_color = c.text_faint if missing else c.text
        painter.setPen(theme.qcolor(name_color))
        name = str(index.data(NAME_ROLE) or "")
        name_rect = QRect(text_left, row.top() + 5, text_width, 18)
        painter.drawText(name_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, text_width))

        sub_font = theme.ui_font(theme.FONT_SMALL)
        painter.setFont(sub_font)
        if missing:
            subtitle, sub_color = "File not found", c.danger
        elif draft:
            subtitle, sub_color = "Not saved yet", c.text_muted
        else:
            subtitle, sub_color = self._folder_text(str(index.data(PATH_ROLE) or "")), c.text_muted
        painter.setPen(theme.qcolor(sub_color))
        sub_rect = QRect(text_left, name_rect.bottom() + 1, text_width, 16)
        painter.drawText(sub_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideMiddle, text_width))

        # Remove button
        if show_remove:
            remove = self.remove_rect(option.rect)
            view = option.widget
            cursor_pos = (view.viewport().mapFromGlobal(QCursor.pos())
                          if isinstance(view, QAbstractItemView) else QPoint(-1, -1))
            if hovered and remove.contains(cursor_pos):
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(theme.qcolor(c.surface_3))
                painter.drawRoundedRect(remove, 4, 4)
            glyph = QRect(remove.center().x() - 7, remove.center().y() - 7, 14, 14)
            painter.drawPixmap(glyph, icons.pixmap("x", c.text_muted if hovered else c.text_faint, 14))

        painter.restore()

    @staticmethod
    def _folder_text(path: str) -> str:
        if not path:
            return ""
        folder = str(Path(path).parent)
        home = str(Path.home())
        if folder == home or folder.startswith(home + os.sep):
            folder = "~" + folder[len(home):]
        return folder


class _RecentList(QListWidget):
    """List view that turns clicks on a row's remove button into ``remove_clicked``."""

    remove_clicked = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecentList")
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delegate = _RecentItemDelegate(self)
        self.setItemDelegate(self._delegate)

    def _remove_hit(self, pos: QPoint) -> Optional[str]:
        item = self.itemAt(pos)
        if item is None or item.data(DRAFT_ROLE):
            return None
        rect = self.visualItemRect(item)
        if _RecentItemDelegate.remove_rect(rect).contains(pos):
            return str(item.data(PATH_ROLE))
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self._remove_hit(event.position().toPoint()):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            path = self._remove_hit(event.position().toPoint())
            if path:
                event.accept()
                self.remove_clicked.emit(path)
                return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        super().mouseMoveEvent(event)
        self.viewport().update()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        item = self.currentItem()
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and item is not None and not item.data(DRAFT_ROLE):
            self.remove_clicked.emit(str(item.data(PATH_ROLE)))
            event.accept()
            return
        super().keyPressEvent(event)


class ScenarioSidebar(QFrame):
    """Collapsible sidebar listing recent scenarios with New / Open and a filter."""

    new_requested = Signal()
    open_requested = Signal()
    scenario_activated = Signal(str)          # path of an existing recent file
    missing_scenario_activated = Signal(str)  # path of a recent file that no longer exists
    recent_removed = Signal(str)

    def __init__(self, store: Optional[RecentScenarioStore] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.store = store if store is not None else RecentScenarioStore()
        self._current_path: Optional[str] = None
        self._current_name: str = "Untitled Scenario"
        self._setup_ui()
        self.refresh()

    # -------------------------------------------------------------------------
    # UI
    # -------------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD)
        layout.setSpacing(theme.SPACE_SM)

        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 0, 0)
        header.setSpacing(2)
        header.addWidget(SectionLabel("Scenarios"))
        header.addStretch(1)
        self.btn_new = make_icon_button("plus", with_shortcut("New scenario", QKeySequence.StandardKey.New))
        self.btn_new.clicked.connect(self.new_requested.emit)
        header.addWidget(self.btn_new)
        layout.addLayout(header)

        self.edit_filter = QLineEdit()
        self.edit_filter.setObjectName("SidebarFilter")
        self.edit_filter.setPlaceholderText("Filter scenarios")
        self.edit_filter.setClearButtonEnabled(True)
        self.edit_filter.addAction(icons.icon("search", theme.COLORS.text_faint, 14),
                                   QLineEdit.ActionPosition.LeadingPosition)
        self.edit_filter.setFixedHeight(theme.CONTROL_HEIGHT)
        self.edit_filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.edit_filter)

        # The list is only as tall as its rows (it scrolls once the panel is full), so
        # the hints below sit right under the last row instead of at the panel bottom.
        self.list_widget = _RecentList()
        self.list_widget.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.list_widget.itemClicked.connect(self._on_item_activated)
        self.list_widget.itemActivated.connect(self._on_item_activated)
        self.list_widget.remove_clicked.connect(self.remove_path)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.list_widget)

        self.lbl_hint = QLabel("Scenarios you open or save appear here.")
        self.lbl_hint.setProperty("role", "caption")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setContentsMargins(10, 2, 6, 0)
        layout.addWidget(self.lbl_hint)

        self.lbl_no_match = QLabel("No scenarios match the filter.")
        self.lbl_no_match.setProperty("role", "caption")
        self.lbl_no_match.setWordWrap(True)
        self.lbl_no_match.setContentsMargins(10, 2, 6, 0)
        self.lbl_no_match.hide()
        layout.addWidget(self.lbl_no_match)
        layout.addStretch(1)

        self.btn_open = QPushButton("Open file…")
        style_button(self.btn_open, "secondary", "folder-open", icon_px=14)
        self.btn_open.setToolTip(with_shortcut("Open a scenario file", QKeySequence.StandardKey.Open))
        self.btn_open.clicked.connect(self.open_requested.emit)
        layout.addWidget(self.btn_open)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuilds the list from the store, marking the current and missing files."""
        self.list_widget.clear()
        entries = self.store.paths()

        if self._current_path is None:
            draft = QListWidgetItem(self._current_name)
            draft.setData(NAME_ROLE, self._current_name)
            draft.setData(PATH_ROLE, "")
            draft.setData(DRAFT_ROLE, True)
            draft.setData(CURRENT_ROLE, True)
            draft.setData(MISSING_ROLE, False)
            draft.setToolTip("Unsaved scenario. Save it to keep it in this list.")
            self.list_widget.addItem(draft)

        for path in entries:
            name = Path(path).stem or path
            item = QListWidgetItem(name)
            item.setData(NAME_ROLE, name)
            item.setData(PATH_ROLE, path)
            item.setData(DRAFT_ROLE, False)
            item.setData(CURRENT_ROLE, path == self._current_path)
            missing = not Path(path).is_file()
            item.setData(MISSING_ROLE, missing)
            item.setToolTip(f"{path}\n(file not found)" if missing else path)
            self.list_widget.addItem(item)

        self.lbl_hint.setVisible(not entries)
        self._apply_filter(self.edit_filter.text())

    def set_current(self, path: Optional[PathLike], name: str = "Untitled Scenario") -> None:
        """Marks the scenario being edited; ``path=None`` shows an unsaved draft row."""
        self._current_path = _normalize(path) if path else None
        self._current_name = name or "Untitled Scenario"
        self.refresh()

    def add_recent(self, path: PathLike) -> None:
        """Records ``path`` as the most recent scenario and refreshes the list."""
        self.store.add(path)
        self.refresh()

    def remove_path(self, path: str) -> None:
        """Removes ``path`` from the recent list (the file itself is untouched)."""
        if self.store.remove(path):
            self.refresh()
            self.recent_removed.emit(path)

    def clear_recent(self) -> None:
        """Forgets every recent scenario."""
        self.store.clear()
        self.refresh()

    def visible_paths(self) -> List[str]:
        """Returns the paths of the rows currently shown (draft row excluded)."""
        result: List[str] = []
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if not item.isHidden() and not item.data(DRAFT_ROLE):
                result.append(str(item.data(PATH_ROLE)))
        return result

    def item_for_path(self, path: PathLike) -> Optional[QListWidgetItem]:
        """Returns the list item showing ``path`` (or None)."""
        key = _normalize(path)
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(PATH_ROLE) in (str(path), key):
                return item
        return None

    # -------------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        shown = 0
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            haystack = f"{item.data(NAME_ROLE)} {item.data(PATH_ROLE)}".lower()
            hidden = bool(needle) and needle not in haystack
            item.setHidden(hidden)
            shown += 0 if hidden else 1
        self.lbl_no_match.setVisible(bool(needle) and shown == 0)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        if item is None or item.data(DRAFT_ROLE) or item.data(CURRENT_ROLE):
            return
        path = str(item.data(PATH_ROLE))
        if Path(path).is_file():
            self.scenario_activated.emit(path)
        else:
            item.setData(MISSING_ROLE, True)
            self.list_widget.viewport().update()
            self.missing_scenario_activated.emit(path)

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self.list_widget.itemAt(pos)
        if item is None or item.data(DRAFT_ROLE):
            return
        path = str(item.data(PATH_ROLE))
        menu = QMenu(self)
        act_open = menu.addAction(icons.icon("folder-open", theme.COLORS.text_muted, 14), "Open")
        act_open.setEnabled(not item.data(MISSING_ROLE) and not item.data(CURRENT_ROLE))
        act_reveal = menu.addAction(icons.icon("external-link", theme.COLORS.text_muted, 14), "Show in Folder")
        act_reveal.setEnabled(Path(path).parent.is_dir())
        menu.addSeparator()
        act_remove = menu.addAction(icons.icon("x", theme.COLORS.text_muted, 14), "Remove from List")
        chosen = menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self._on_item_activated(item)
        elif chosen is act_reveal:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))
        elif chosen is act_remove:
            self.remove_path(path)
