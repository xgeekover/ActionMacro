"""
action_macro.gui.chrome
==========================
Window chrome of the main window: the top bar and the left navigation rail.

* :class:`TopBar` - app mark, the menus (Windows/Linux only, see
  :meth:`TopBar.set_menu_bar`), the open document (file name and save state), a
  slot for a permissions warning and the "More" menu button. On macOS it is also
  the window's title bar (:meth:`TopBar.set_leading_space`,
  :meth:`TopBar.enable_window_drag`; see ``macos_title_bar``).
* :class:`NavRail` - a 48 px icon-only rail whose buttons are bound to checkable
  ``QAction`` objects (toggle the scenario sidebar / the console) plus a menu button.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QAction, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMenuBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from action_macro.gui import icons, theme
from action_macro.gui.components import ElidedLabel, Separator, StatusDot, make_icon_button, style_button


class TopBar(QFrame):
    """Application header: mark, menus, document name + save state, permissions, "More"."""

    #: Left padding when nothing sits at the bar's left end.
    LEFT_MARGIN = 14

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(theme.TOP_BAR_HEIGHT)

        # Title bar behaviour (enable_window_drag): off until the window asks for it.
        self._window_drag = False
        self._on_double_click: Optional[Callable[[], None]] = None
        self._press_pos: Optional[QPoint] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(self.LEFT_MARGIN, 0, 10, 0)
        layout.setSpacing(theme.SPACE_SM)

        self.lbl_mark = QLabel()
        self.lbl_mark.setPixmap(icons.app_mark_pixmap(22))
        self.lbl_mark.setFixedSize(22, 22)
        layout.addWidget(self.lbl_mark)

        self.lbl_brand = QLabel("ActionMacro")
        self.lbl_brand.setProperty("role", "brand")
        layout.addWidget(self.lbl_brand)

        # Menus sit here on platforms without a global menu bar (see set_menu_bar).
        self.menu_bar: Optional[QMenuBar] = None
        self._menu_slot = QHBoxLayout()
        self._menu_slot.setContentsMargins(0, 0, 0, 0)
        self._menu_slot.setSpacing(0)
        layout.addLayout(self._menu_slot)

        layout.addSpacing(theme.SPACE_XS)
        layout.addWidget(Separator(vertical=True, length=16))
        layout.addSpacing(theme.SPACE_XS)

        self.lbl_file_icon = QLabel()
        self.lbl_file_icon.setPixmap(icons.pixmap("file-text", theme.COLORS.text_muted, 14))
        layout.addWidget(self.lbl_file_icon)

        self.lbl_file = ElidedLabel("Not saved yet")
        self.lbl_file.setObjectName("DocumentLabel")
        self.lbl_file.setProperty("role", "muted")
        self.lbl_file.setMaximumWidth(420)
        layout.addWidget(self.lbl_file)

        layout.addSpacing(theme.SPACE_XS)
        self.save_dot = StatusDot(theme.COLORS.warning, 7)
        layout.addWidget(self.save_dot)
        self.lbl_save_state = QLabel("Unsaved changes")
        self.lbl_save_state.setObjectName("SaveStateLabel")
        layout.addWidget(self.lbl_save_state)

        layout.addStretch(1)

        # Slot for a permissions warning (e.g. macOS Accessibility / Screen Recording).
        self.btn_permissions = QPushButton("Permissions")
        self.btn_permissions.setObjectName("PermissionsButton")
        style_button(self.btn_permissions, "ghost", "shield-alert",
                     icon_color=theme.COLORS.warning, icon_px=14, height=theme.CONTROL_HEIGHT_SM)
        self.btn_permissions.setProperty("tone", "warning")
        self.btn_permissions.hide()
        layout.addWidget(self.btn_permissions)

        # "More": panel toggles, tools and About (there are no app settings yet).
        self.btn_more = make_icon_button("more-horizontal", "More", size=theme.CONTROL_HEIGHT_SM)
        self.btn_more.setObjectName("MoreButton")
        self.btn_more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        layout.addWidget(self.btn_more)

        self.set_document(None, dirty=False)

    def set_menu_bar(self, menu_bar: QMenuBar) -> None:
        """Places ``menu_bar`` after the app name, so menus and header share one row.

        Used on Windows/Linux, where the menu bar would otherwise be a second header
        above this bar. On macOS the menus live in the screen's global menu bar.
        """
        menu_bar.setObjectName("InlineMenuBar")
        menu_bar.setNativeMenuBar(False)
        menu_bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._menu_slot.addSpacing(theme.SPACE_SM)
        self._menu_slot.addWidget(menu_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        self.menu_bar = menu_bar

    def set_leading_space(self, width: int) -> None:
        """Keeps the bar's first ``width`` px clear; 0 restores the normal margin.

        On macOS the window's traffic-light buttons sit there (see ``macos_title_bar``).
        """
        margins = self.layout().contentsMargins()
        margins.setLeft(max(width, self.LEFT_MARGIN))
        self.layout().setContentsMargins(margins)

    def fit_title_bar(self, height: int, centre: Optional[float] = None) -> None:
        """Makes the bar ``height`` px tall with its items centred on ``centre``.

        ``centre`` is measured from the bar's top (the macOS window buttons' centre),
        so the items line up with the buttons despite the bar's bottom border. None
        centres them in the bar as usual.
        """
        self.setFixedHeight(height)
        shift = 0
        if centre is not None:
            self.ensurePolished()  # contentsRect() must exclude the style sheet border
            rect = self.contentsRect()
            shift = round(2 * (centre - rect.top()) - rect.height())
        margins = self.layout().contentsMargins()
        margins.setTop(max(shift, 0))
        margins.setBottom(max(-shift, 0))
        self.layout().setContentsMargins(margins)

    def leading_space(self) -> int:
        """Returns the bar's left padding (the normal margin or the space kept clear)."""
        return self.layout().contentsMargins().left()

    def enable_window_drag(self, on_double_click: Optional[Callable[[], None]] = None) -> None:
        """Makes the bar's empty space act as the window's title bar.

        Dragging it moves the window, and a double click calls ``on_double_click``.
        The move starts once the pointer has travelled the drag distance, so a plain
        click or a double click is never swallowed by it. Buttons keep their own
        clicks; labels let them through to the bar.
        """
        self._window_drag = True
        self._on_double_click = on_double_click

    def is_window_drag_enabled(self) -> bool:
        """True when the bar's empty space moves the window (see :meth:`enable_window_drag`)."""
        return self._window_drag

    def _start_window_move(self) -> bool:
        """Hands the move to the window system; False when it cannot move windows."""
        handle = self.window().windowHandle()
        return bool(handle is not None and handle.startSystemMove())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._window_drag and event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            travelled = (event.position().toPoint() - self._press_pos).manhattanLength()
            if travelled >= QApplication.startDragDistance():
                self._press_pos = None
                self._start_window_move()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._press_pos is not None and event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._window_drag and event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = None
            if self._on_double_click is not None:
                self._on_double_click()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def set_document(self, file_name: Optional[str], *, dirty: bool, tooltip: str = "") -> None:
        """Shows the open file name (or "Not saved yet") and its save state."""
        self.lbl_file.setText(file_name or "Not saved yet")
        self.lbl_file.setToolTip(tooltip)
        if dirty:
            self.save_dot.set_color(theme.COLORS.warning)
            self.lbl_save_state.setText("Unsaved changes")
            theme.set_style_prop(self.lbl_save_state, "state", "dirty")
            self.save_dot.show()
            self.lbl_save_state.show()
        elif file_name:
            self.save_dot.set_color(theme.COLORS.success)
            self.lbl_save_state.setText("Saved")
            theme.set_style_prop(self.lbl_save_state, "state", "saved")
            self.save_dot.show()
            self.lbl_save_state.show()
        else:
            self.save_dot.hide()
            self.lbl_save_state.hide()

    def set_permission_warning(self, message: Optional[str]) -> None:
        """Shows the permissions button with ``message`` as tooltip, or hides it (None)."""
        if message:
            self.btn_permissions.setToolTip(message)
            self.btn_permissions.setAccessibleDescription(message)
            self.btn_permissions.show()
        else:
            self.btn_permissions.setToolTip("")
            self.btn_permissions.setAccessibleDescription("")
            self.btn_permissions.hide()


class NavRail(QFrame):
    """Icon-only navigation rail; the active (checked) item gets a violet indicator."""

    BUTTON_HEIGHT = 40
    ICON_PX = 20

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("NavRail")
        self.setFixedWidth(theme.RAIL_WIDTH)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, theme.SPACE_SM, 0, theme.SPACE_SM)
        self._layout.setSpacing(theme.SPACE_XS)

    def _make_button(self) -> QToolButton:
        button = QToolButton()
        button.setProperty("variant", "rail")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(self.ICON_PX, self.ICON_PX))
        button.setFixedSize(theme.RAIL_WIDTH, self.BUTTON_HEIGHT)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return button

    def add_action(self, action: QAction) -> QToolButton:
        """Adds a button bound to ``action`` (its icon, tooltip and checked state)."""
        button = self._make_button()
        button.setDefaultAction(action)
        button.setAccessibleName(action.text().replace("&", ""))
        self._layout.addWidget(button)
        return button

    def add_menu_button(self, icon_name: str, tooltip: str, menu: QMenu) -> QToolButton:
        """Adds a button that pops up ``menu``."""
        button = self._make_button()
        button.setIcon(icons.icon(icon_name, theme.COLORS.text_muted, self.ICON_PX,
                                  active_color=theme.COLORS.text))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._layout.addWidget(button)
        return button

    def add_stretch(self) -> None:
        """Pushes the following buttons to the bottom of the rail."""
        self._layout.addStretch(1)
