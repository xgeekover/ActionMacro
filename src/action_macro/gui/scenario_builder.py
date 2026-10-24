"""
action_macro.gui.scenario_builder
====================================
The step list of the editor: one card per action block, plus the ways to add,
reorder, duplicate, switch off and delete steps.

Layout (top to bottom): a header row ("STEPS" and the count, "+ Add step"), the
cards, and a dashed "+ Add step" row right below the last card. An empty
scenario shows an empty state whose four tiles add the first step instead.

Each card shows a drag handle, the step number, the step type's icon in a tinted
circle, a title (the user's description or the type label), a one-sentence
summary (see :mod:`action_macro.gui.step_text`), a status pill (Running, Paused or
"Image not found"), reorder chevrons (on hover and selection), an enable switch and
an overflow menu (Duplicate, Move up, Move down, Delete). On the focused list,
Delete/Backspace deletes the selected step (:meth:`ScenarioBuilder.undo_delete`
brings it back) and Ctrl+D (Cmd+D on macOS) duplicates it.

While a run is active the owner calls :meth:`ScenarioBuilder.set_editable` with
``False``: the run works on a copy of the scenario and highlights steps by
position, so the list stays read-only until the run ends.
"""

from __future__ import annotations

import copy
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QContextMenuEvent,
    QCursor,
    QDrag,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.gui import icons, step_text, theme
from action_macro.gui.components import (  # noqa: F401 - ToggleSwitch is re-exported here
    ElidedLabel,
    EmptyState,
    SectionLabel,
    ToggleSwitch,
    empty_state_badge,
    make_icon_button,
    style_button,
)

#: Order in which step types are offered (menus, empty-state tiles).
STEP_TYPE_ORDER = (ActionType.MOUSE_CLICK, ActionType.KEY_INPUT, ActionType.FIXED_WAIT, ActionType.IMAGE_WAIT)

#: Native shortcut of "Duplicate" on the focused step list (Cmd+D on macOS).
DUPLICATE_SHORTCUT = QKeySequence("Ctrl+D")

#: Keys that delete the selected step on the focused step list.
DELETE_KEYS = (Qt.Key.Key_Delete, Qt.Key.Key_Backspace)

#: The delete key shown in menus: Backspace is the key labelled "delete" on Mac keyboards.
DELETE_HINT = QKeySequence(Qt.Key.Key_Backspace if sys.platform == "darwin" else Qt.Key.Key_Delete)

#: Tooltip of the editing controls while a run is active (see ScenarioBuilder.set_editable).
READ_ONLY_HINT = "Stop the run to edit steps"

#: Text of the card status pill per state (``lbl_status``). "missing" covers both a template
#: file that does not exist and a new step whose default path was never replaced.
STATUS_TEXT = {"running": "Running", "paused": "Paused", "missing": "Image not found"}
NO_IMAGE_TEXT = "No image"


def _as_action_type(value: object) -> ActionType:
    return value if isinstance(value, ActionType) else ActionType.from_str(str(value))


# -----------------------------------------------------------------------------
# Small painted widgets
# -----------------------------------------------------------------------------


class StepIconBadge(QWidget):
    """A step type's Lucide icon inside a softly tinted circle (faded for skipped steps)."""

    def __init__(self, diameter: int = 32, glyph: int = 16, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._diameter = diameter
        self._glyph = glyph
        self._icon_name = "circle-dot"
        self._tint = theme.COLORS.text_muted
        self._faded = False
        self.setFixedSize(diameter, diameter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_step_type(self, action_type: ActionType) -> None:
        """Shows the icon and tint of ``action_type`` (theme.STEP_TYPES)."""
        meta = theme.STEP_TYPES.get(_as_action_type(action_type))
        if meta is not None:
            self.set_icon(meta.icon, meta.tint)

    def set_icon(self, icon_name: str, tint: str) -> None:
        """Changes the glyph and tint colour."""
        if (icon_name, tint) != (self._icon_name, self._tint):
            self._icon_name, self._tint = icon_name, tint
            self.update()

    def icon_name(self) -> str:
        """Returns the Lucide icon name shown in the circle."""
        return self._icon_name

    def tint(self) -> str:
        """Returns the tint colour of the circle and glyph."""
        return self._tint

    def is_faded(self) -> bool:
        """True while drawn faded (the step is switched off)."""
        return self._faded

    def set_faded(self, faded: bool) -> None:
        """Draws the badge faded (``True``) or at full strength."""
        if faded != self._faded:
            self._faded = faded
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        # The same tinted circle as every other step-type badge (components.empty_state_badge);
        # its glyph is half the diameter, so ``glyph`` only matters for odd custom sizes.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._faded:
            painter.setOpacity(0.4)
        if self._glyph * 2 == self._diameter:
            painter.drawPixmap(0, 0, empty_state_badge(self._icon_name, self._tint, self._diameter))
        else:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor(self._tint, 0.14))
            painter.drawEllipse(QRectF(0, 0, self._diameter, self._diameter))
            offset = (self._diameter - self._glyph) / 2.0
            painter.drawPixmap(QRectF(offset, offset, self._glyph, self._glyph).toRect(),
                               icons.pixmap(self._icon_name, self._tint, self._glyph))
        painter.end()


# -----------------------------------------------------------------------------
# Step card
# -----------------------------------------------------------------------------


class ActionCardWidget(QFrame):
    """Visual card widget representing a single ActionBlock.

    The visible card is inset by :attr:`INSET` pixels inside the widget: the inset
    is the gap between cards in the list and the room for the running glow, which
    is painted by the card itself so it also shows up in ``grab()``.
    """

    clicked = Signal(str)             # action_id
    move_up_requested = Signal(str)   # action_id
    move_down_requested = Signal(str) # action_id
    delete_requested = Signal(str)    # action_id
    duplicate_requested = Signal(str) # action_id
    enabled_toggled = Signal(str, bool)  # action_id, new_state

    CARD_HEIGHT = 56   # height of the visible card
    INSET = 4          # transparent margin around the visible card (gap + glow)
    PADDING = 6        # inner horizontal padding of the visible card (icons add their own air)
    GLOW_ALPHAS = (0.34, 0.16, 0.06)  # running glow rings, innermost first
    RUNNING_BAR = 3    # width of the accent bar of the running step

    def __init__(self, action: ActionBlock, index: int, total_count: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.action_id = action.id
        self.action = action
        self.index = index
        self.total_count = total_count
        self.is_executing = False
        self.is_paused = False
        self.is_selected = False
        self.is_hovered = False
        self._editable = True
        self._base_dir: Optional[Path] = None
        self._template_state = "found"  # image-wait steps only: see step_text.template_state

        self._setup_ui()
        self.update_content(action, index, total_count)

    # -- construction ---------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setObjectName("StepCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.total_height())
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        side = self.INSET + self.PADDING
        layout.setContentsMargins(side, self.INSET, side, self.INSET)
        layout.setSpacing(0)

        # Drag handle: presses fall through to the list, which starts the drag.
        self.lbl_grip = QLabel()
        self.lbl_grip.setPixmap(icons.pixmap("grip-vertical", theme.COLORS.text_faint, 16))
        self.lbl_grip.setFixedWidth(16)
        self.lbl_grip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_grip.setCursor(Qt.CursorShape.OpenHandCursor)
        self.lbl_grip.setToolTip("Drag to reorder")
        layout.addWidget(self.lbl_grip)
        layout.addSpacing(theme.SPACE_XS)

        self.lbl_index = QLabel(f"#{self.index + 1}")
        index_font = theme.mono_font(theme.FONT_SMALL)
        self.lbl_index.setFont(index_font)
        self.lbl_index.setProperty("role", "muted")
        self.lbl_index.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_index.setFixedWidth(self.lbl_index.fontMetrics().horizontalAdvance("#00") + 2)
        layout.addWidget(self.lbl_index)
        layout.addSpacing(theme.SPACE_MD)

        self.badge = StepIconBadge(32, 16)
        layout.addWidget(self.badge)
        layout.addSpacing(theme.SPACE_MD)

        # Title + summary. Elided (full text in the tooltip) so a narrow list never
        # scrolls sideways or pushes the controls out of view.
        content_box = QVBoxLayout()
        content_box.setContentsMargins(0, 0, 0, 0)
        content_box.setSpacing(2)
        content_box.addStretch(1)
        self.lbl_title = ElidedLabel()
        self.lbl_title.setFont(theme.ui_font(theme.FONT_BODY, QFont.Weight.DemiBold))
        self.lbl_title.setProperty("role", "")
        content_box.addWidget(self.lbl_title)
        self.lbl_summary = ElidedLabel()
        self.lbl_summary.setFont(theme.ui_font(theme.FONT_SMALL))
        self.lbl_summary.setProperty("role", "muted")
        content_box.addWidget(self.lbl_summary)
        content_box.addStretch(1)
        layout.addLayout(content_box, stretch=1)
        layout.addSpacing(theme.SPACE_SM)

        # Text status for states that colour alone would not explain: Running / Paused
        # (the run) and "Image not found" (an image-wait step without its template file).
        self.lbl_status = QLabel(STATUS_TEXT["running"])
        self.lbl_status.setObjectName("StepStatusPill")
        self.lbl_status.setProperty("state", "running")
        self.lbl_status.setFont(theme.ui_font(theme.FONT_SMALL, QFont.Weight.DemiBold))
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_status.setFixedHeight(18)
        self.lbl_status.hide()
        layout.addWidget(self.lbl_status, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addSpacing(theme.SPACE_XS)

        # Reorder chevrons: quiet, shown while the card is hovered or selected.
        reorder = QVBoxLayout()
        reorder.setContentsMargins(0, 0, 0, 0)
        reorder.setSpacing(0)
        reorder.addStretch(1)
        self.btn_up = make_icon_button("chevron-up", "Move up", size=20, icon_px=14)
        self.btn_up.clicked.connect(lambda: self.move_up_requested.emit(self.action_id))
        self.btn_down = make_icon_button("chevron-down", "Move down", size=20, icon_px=14)
        self.btn_down.clicked.connect(lambda: self.move_down_requested.emit(self.action_id))
        for button in (self.btn_up, self.btn_down):
            button.setObjectName("StepReorderButton")
            button.setFixedSize(20, 14)
            policy = button.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            button.setSizePolicy(policy)
            reorder.addWidget(button)
        reorder.addStretch(1)
        layout.addLayout(reorder)
        layout.addSpacing(theme.SPACE_SM)

        # Enable switch
        self.chk_enabled = ToggleSwitch()
        self.chk_enabled.setAccessibleName("Enabled")
        self.chk_enabled.setChecked(self.action.enabled)
        self.chk_enabled.toggled.connect(self._on_toggled)
        layout.addWidget(self.chk_enabled)
        layout.addSpacing(theme.SPACE_XS)

        # Overflow menu
        self.btn_more = make_icon_button("more-horizontal", "More actions", size=theme.CONTROL_HEIGHT_SM, icon_px=16)
        self.btn_more.setObjectName("StepMoreButton")
        self.btn_more.clicked.connect(self._show_more_menu)
        layout.addWidget(self.btn_more)

        self.menu_more = QMenu(self)
        self.menu_more.setObjectName("StepMenu")
        self.act_duplicate = self._menu_action(
            "Duplicate", "copy", lambda: self.duplicate_requested.emit(self.action_id), DUPLICATE_SHORTCUT)
        self.act_move_up = self._menu_action(
            "Move up", "arrow-up", lambda: self.move_up_requested.emit(self.action_id))
        self.act_move_down = self._menu_action(
            "Move down", "arrow-down", lambda: self.move_down_requested.emit(self.action_id))
        self.menu_more.addSeparator()
        self.act_delete = self._menu_action(
            "Delete", "trash", lambda: self.delete_requested.emit(self.action_id),
            DELETE_HINT, icon_color=theme.COLORS.danger)

        self._sync_reorder_visibility()

    def _menu_action(self, text: str, icon_name: str, slot, shortcut: Optional[QKeySequence] = None,
                     icon_color: Optional[str] = None) -> QAction:
        action = QAction(icons.icon(icon_name, icon_color or theme.COLORS.text_muted, 16,
                                    active_color=icon_color or theme.COLORS.text), text, self.menu_more)
        if shortcut is not None:
            # Shown as a hint only; the step list handles the key itself.
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.setShortcutVisibleInContextMenu(True)
        action.triggered.connect(slot)
        self.menu_more.addAction(action)
        return action

    def total_height(self) -> int:
        """Height of the whole widget: the visible card plus the inset above and below."""
        return self.CARD_HEIGHT + 2 * self.INSET

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().sizeHint().width(), self.total_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().minimumSizeHint().width(), self.total_height())

    # -- content and state ----------------------------------------------------

    def update_content(self, action: ActionBlock, index: int, total_count: int) -> None:
        """Refreshes the number, icon, title, summary and the enabled state of the controls."""
        self.action = action
        self.index = index
        self.total_count = total_count

        self.lbl_index.setText(f"#{index + 1}")
        can_up, can_down = index > 0, index < total_count - 1
        self.btn_up.setEnabled(can_up and self._editable)
        self.btn_down.setEnabled(can_down and self._editable)
        self.act_move_up.setEnabled(can_up)
        self.act_move_down.setEnabled(can_down)
        self.chk_enabled.blockSignals(True)
        self.chk_enabled.setChecked(action.enabled)
        self.chk_enabled.blockSignals(False)

        self.badge.set_step_type(_as_action_type(action.type))
        self.lbl_title.setText(step_text.step_title(action))
        self._refresh_template_state()  # also sets the summary (it depends on the template state)
        self._apply_enabled_look()

    def _apply_enabled_look(self) -> None:
        """Dims a switched-off step: muted title, faded icon, darker card and the
        "Disabled ·" prefix. Summary and number stay text-muted (readable, 4.5:1+)."""
        enabled = bool(self.action.enabled)
        theme.set_style_prop(self.lbl_title, "role", "" if enabled else "muted")
        theme.set_style_prop(self.lbl_summary, "role", "muted")
        theme.set_style_prop(self.lbl_index, "role", "muted")
        self.badge.set_faded(not enabled)
        if not self._editable:
            self.chk_enabled.setToolTip(READ_ONLY_HINT)
        else:
            self.chk_enabled.setToolTip("Disable this step (disabled steps are skipped when the scenario runs)"
                                        if enabled else "Enable this step")
        self.update()

    # -- status pill ------------------------------------------------------------

    def set_base_dir(self, base_dir: Optional[Path]) -> None:
        """Folder relative template paths start from (the scenario file's folder; None: cwd)."""
        if base_dir != self._base_dir:
            self._base_dir = base_dir
            self._refresh_template_state()

    def template_state(self) -> str:
        """Image-wait steps: ``found``, ``missing``, ``placeholder`` or ``empty``; others ``found``."""
        return self._template_state

    def _refresh_template_state(self) -> None:
        state = "found"
        if _as_action_type(self.action.type) == ActionType.IMAGE_WAIT:
            params = self.action.params
            path = params.get("template_path", "") if isinstance(params, dict) else getattr(params, "template_path", "")
            state = step_text.template_state(str(path or ""), self._base_dir)
        self._template_state = state
        self.lbl_summary.setText(self._summary_text())
        self._refresh_status_pill()

    def _summary_text(self) -> str:
        return step_text.card_summary(self.action, image_chosen=self._template_state != "placeholder")

    def status_state(self) -> Optional[str]:
        """The pill shown now: ``running``, ``paused``, ``missing`` or None (hidden).

        A switched-off step is skipped, so its missing template is not flagged.
        """
        if self.is_executing:
            return "paused" if self.is_paused else "running"
        if self._template_state in ("missing", "placeholder") and self.action.enabled:
            return "missing"
        return None

    def _refresh_status_pill(self) -> None:
        state = self.status_state()
        if state is not None:
            placeholder = state == "missing" and self._template_state == "placeholder"
            self.lbl_status.setText(NO_IMAGE_TEXT if placeholder else STATUS_TEXT[state])
            theme.set_style_prop(self.lbl_status, "state", state)
            if placeholder:
                self.lbl_status.setToolTip("Choose the image this step waits for (select the step).")
            elif state == "missing":
                self.lbl_status.setToolTip("The template image file does not exist; this step fails when it runs.")
            else:
                self.lbl_status.setToolTip("")
        if self.lbl_status.isHidden() == (state is not None):
            self.lbl_status.setVisible(state is not None)

    def set_executing(self, executing: bool, paused: bool = False) -> None:
        """Sets active execution highlight (accent bar, glow and a "Running" / "Paused" pill)."""
        self.is_executing = executing
        self.is_paused = bool(executing and paused)
        self._refresh_status_pill()
        self.update()

    def set_editable(self, editable: bool) -> None:
        """Enables or disables the switch, chevrons and overflow menu (read-only during a run)."""
        self._editable = bool(editable)
        for widget in (self.chk_enabled, self.btn_up, self.btn_down, self.btn_more):
            widget.setEnabled(self._editable)
        if self._editable:
            self.btn_up.setEnabled(self.index > 0)
            self.btn_down.setEnabled(self.index < self.total_count - 1)
        self.btn_more.setToolTip("More actions" if self._editable else READ_ONLY_HINT)
        self._sync_reorder_visibility()
        self._apply_enabled_look()

    def set_selected(self, selected: bool) -> None:
        """Sets visual selection highlight."""
        self.is_selected = selected
        self._sync_reorder_visibility()
        self.update()

    def _sync_reorder_visibility(self) -> None:
        show = (self.is_selected or self.is_hovered) and self._editable
        self.btn_up.setVisible(show)
        self.btn_down.setVisible(show)

    def visual_rect(self) -> QRectF:
        """The visible card inside the widget (without the gap/glow inset)."""
        return QRectF(self.rect()).adjusted(self.INSET, self.INSET, -self.INSET, -self.INSET)

    def border_color(self) -> str:
        """The current border colour token (accent when selected or running)."""
        c = theme.COLORS
        if self.is_executing or self.is_selected:
            return c.accent
        if self.is_hovered:
            return c.border_strong
        return c.border

    def background_color(self) -> str:
        """The current fill colour token (before the accent tint of selection/running)."""
        c = theme.COLORS
        if self.is_hovered and not (self.is_selected or self.is_executing):
            return c.surface_3
        return c.surface_2 if self.action.enabled else c.surface

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        c = theme.COLORS
        radius = float(theme.RADIUS_MD)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        card = self.visual_rect()
        edge = card.adjusted(0.5, 0.5, -0.5, -0.5)

        if self.is_executing:
            # Soft glow: thin accent rings fading out into the inset.
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for step, opacity in enumerate(self.GLOW_ALPHAS, start=1):
                ring = edge.adjusted(-step, -step, step, step)
                painter.setPen(QPen(theme.qcolor(c.accent, opacity), 1.0))
                painter.drawRoundedRect(ring, radius + step, radius + step)

        path = QPainterPath()
        path.addRoundedRect(edge, radius, radius)
        painter.fillPath(path, theme.qcolor(self.background_color()))
        if self.is_executing:
            # Only the running step is tinted; selection is the accent border alone, so a
            # running step and a different selected step never look alike.
            painter.fillPath(path, theme.qcolor(c.accent, 0.12))

        if self.is_executing:
            painter.save()
            painter.setClipPath(path)
            painter.fillRect(QRectF(card.left(), card.top(), self.RUNNING_BAR, card.height()), theme.qcolor(c.accent))
            painter.restore()

        painter.setPen(QPen(theme.qcolor(self.border_color()), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()

    # -- interaction ------------------------------------------------------------

    def _show_more_menu(self) -> None:
        if not self._editable:
            return
        self.clicked.emit(self.action_id)  # actions apply to the step the user sees selected
        menu = self.menu_more
        width = menu.sizeHint().width()
        anchor = self.btn_more.mapToGlobal(QPoint(self.btn_more.width() - width, self.btn_more.height() + 4))
        menu.popup(anchor)

    def _on_toggled(self, checked: bool) -> None:
        self.action.enabled = checked
        self.lbl_summary.setText(self._summary_text())
        self._apply_enabled_look()
        self._refresh_status_pill()
        self.enabled_toggled.emit(self.action_id, checked)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self.is_hovered = True
        self._sync_reorder_visibility()
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.is_hovered = False
        self._sync_reorder_visibility()
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.action_id)
        super().mousePressEvent(event)  # ignored: the list sees it and can start a drag

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - Qt override
        self.clicked.emit(self.action_id)
        if self._editable:
            self.menu_more.popup(event.globalPos())
        event.accept()


# -----------------------------------------------------------------------------
# Step list
# -----------------------------------------------------------------------------


class ActionListWidget(QListWidget):
    """Specialized list widget supporting internal drag & drop and keyboard navigation.

    Its height follows its content (up to the space it gets), so the "Add step"
    row sits right below the last card. Delete/Backspace and Ctrl+D (Cmd+D on
    macOS) are reported through :attr:`delete_requested` / :attr:`duplicate_requested`.
    """

    order_changed = Signal()
    delete_requested = Signal()
    duplicate_requested = Signal()
    viewport_resized = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._editable = True
        self.setObjectName("StepList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSpacing(0)  # the cards carry their own gap (ActionCardWidget.INSET)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setStyleSheet("""
            QListWidget {
                background-color: transparent;
                border: none;
                padding: 0px;
            }
            QListWidget::item, QListWidget::item:hover, QListWidget::item:selected {
                background: transparent;
                border: none;
                margin: 0px;
                padding: 0px;
            }
        """)

    # -- size follows content ---------------------------------------------------

    def content_height(self) -> int:
        """Height of all rows (the list never needs more than this)."""
        rows = sum(self.item(i).sizeHint().height() for i in range(self.count()))
        return rows + 2 * self.spacing() * self.count() + 2 * self.frameWidth()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().sizeHint().width(), self.content_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        first_row = self.item(0).sizeHint().height() if self.count() else 0
        return QSize(super().minimumSizeHint().width(), min(self.content_height(), first_row + 2 * self.frameWidth()))

    def updateGeometries(self) -> None:  # noqa: N802 - Qt override
        super().updateGeometries()
        # With the horizontal scroll bar off, QListView shrinks its contents to the
        # viewport only *after* it placed the row widgets, so a scroll bar that just
        # appeared would leave the cards wider than the viewport (controls under the
        # bar). Place them again against the corrected width.
        self.updateEditorGeometries()

    def viewportEvent(self, event: QEvent) -> bool:  # noqa: N802 - Qt override
        handled = super().viewportEvent(event)
        if event.type() == QEvent.Type.Resize:
            self.viewport_resized.emit()
        return handled

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # A shorter list (console shown again, window shrunk) keeps the selected card in view.
        item = self.currentItem()
        if item is not None and event.size().height() < event.oldSize().height():
            self.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)

    # -- read-only mode -----------------------------------------------------------

    def set_editable(self, editable: bool) -> None:
        """Turns drag and drop and the Delete / duplicate keys on or off."""
        self._editable = bool(editable)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove if self._editable
                             else QAbstractItemView.DragDropMode.NoDragDrop)

    def is_editable(self) -> bool:
        """False while the list is read-only (a run is active)."""
        return self._editable

    # -- keyboard -------------------------------------------------------------

    @staticmethod
    def _is_delete_key(event: QKeyEvent) -> bool:
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        return event.key() in DELETE_KEYS and mods == Qt.KeyboardModifier.NoModifier

    @staticmethod
    def _is_duplicate_key(event: QKeyEvent) -> bool:
        return QKeySequence(event.keyCombination()) == DUPLICATE_SHORTCUT

    def event(self, event: QEvent) -> bool:  # noqa: A003 - Qt override
        # Claim our keys before any window shortcut sees them.
        if event.type() == QEvent.Type.ShortcutOverride and isinstance(event, QKeyEvent):
            if self._editable and self.currentItem() is not None and (
                    self._is_delete_key(event) or self._is_duplicate_key(event)):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if self._editable and self.currentItem() is not None:
            if self._is_delete_key(event):
                event.accept()
                self.delete_requested.emit()
                return
            if self._is_duplicate_key(event):
                event.accept()
                self.duplicate_requested.emit()
                return
        super().keyPressEvent(event)

    # -- drag & drop ----------------------------------------------------------

    def drag_pixmap(self, card: QWidget) -> QPixmap:
        """A slightly transparent picture of ``card`` used as the drag image."""
        snapshot = card.grab()
        pixmap = QPixmap(snapshot.size())
        pixmap.setDevicePixelRatio(snapshot.devicePixelRatio())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setOpacity(0.9)
        painter.drawPixmap(0, 0, snapshot)
        painter.end()
        return pixmap

    def startDrag(self, supported_actions: Qt.DropAction) -> None:  # noqa: N802 - Qt override
        """Drags the card's picture (the default image of a widget row is empty).

        The rows are moved by ``QListView.dropEvent`` (internal move), exactly as
        with the default implementation, and :meth:`dropEvent` reports the new order.
        """
        item = self.currentItem()
        card = self.itemWidget(item) if item is not None else None
        indexes = self.selectedIndexes()
        if card is None or not indexes:
            super().startDrag(supported_actions)
            return
        mime = self.model().mimeData(indexes)
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self.drag_pixmap(card))
        hot = card.mapFromGlobal(QCursor.pos())
        hot.setX(max(0, min(hot.x(), card.width() - 1)))
        hot.setY(max(0, min(hot.y(), card.height() - 1)))
        drag.setHotSpot(hot)
        self._run_drag(drag, supported_actions)

    def _run_drag(self, drag: QDrag, supported_actions: Qt.DropAction) -> Qt.DropAction:
        """Runs the (blocking) drag; a separate method so tests can stand in for it."""
        return drag.exec(supported_actions, self.defaultDropAction())

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().dropEvent(event)
        self.order_changed.emit()


# -----------------------------------------------------------------------------
# Adding steps: menu rows and empty-state tiles
# -----------------------------------------------------------------------------


class StepTypeOption(QWidget):
    """One row of the "Add step" menu: icon circle, label and a one-line description.

    It is the default widget of a ``QWidgetAction``. Mouse events pass through to
    the menu, so hovering, clicking and the arrow/Enter keys work like a normal
    menu item; the row paints the highlight of the menu's active action itself.
    """

    def __init__(self, action_type: ActionType, menu: QMenu, action: QAction, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.action_type = action_type
        self._menu = menu
        self._action = action
        meta = theme.STEP_TYPES[action_type]
        self.setObjectName("StepTypeOption")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_SM, 7, theme.SPACE_LG, 7)
        layout.setSpacing(theme.SPACE_MD)
        self.badge = StepIconBadge(28, 14)
        self.badge.set_step_type(action_type)
        layout.addWidget(self.badge, alignment=Qt.AlignmentFlag.AlignVCenter)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(1)
        self.lbl_label = QLabel(meta.label)
        self.lbl_label.setFont(theme.ui_font(theme.FONT_BODY, QFont.Weight.DemiBold))
        texts.addWidget(self.lbl_label)
        self.lbl_description = QLabel(meta.description)
        self.lbl_description.setFont(theme.ui_font(theme.FONT_SMALL))
        self.lbl_description.setProperty("role", "muted")
        texts.addWidget(self.lbl_description)
        layout.addLayout(texts, stretch=1)

    def is_highlighted(self) -> bool:
        """True while this row is the menu's active (hovered / arrow-key) item."""
        return self._menu.activeAction() is self._action

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        if not self.is_highlighted():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.qcolor(theme.COLORS.accent, 0.16))
        painter.drawRoundedRect(QRectF(self.rect()), theme.RADIUS_SM, theme.RADIUS_SM)
        painter.end()


def build_add_step_menu(parent: QWidget) -> Tuple[QMenu, Dict[ActionType, QAction]]:
    """Creates the "Add step" menu: one row per step type with icon, label and description.

    Returns ``(menu, actions)`` where ``actions`` maps each ActionType to its QAction
    (a ``QWidgetAction`` whose ``text()`` is the type label).
    """
    menu = QMenu(parent)
    menu.setObjectName("AddStepMenu")
    actions: Dict[ActionType, QAction] = {}
    options: List[StepTypeOption] = []
    for action_type in STEP_TYPE_ORDER:
        meta = theme.STEP_TYPES[action_type]
        action = QWidgetAction(menu)
        action.setText(meta.label)
        action.setToolTip(meta.description)
        option = StepTypeOption(action_type, menu, action)
        action.setDefaultWidget(option)
        menu.addAction(action)
        actions[action_type] = action
        options.append(option)

    def _repaint_rows(*_args) -> None:
        for row in options:
            row.update()

    menu.hovered.connect(_repaint_rows)
    menu.aboutToShow.connect(_repaint_rows)
    menu.aboutToHide.connect(_repaint_rows)
    return menu, actions


class StepTypeTile(QPushButton):
    """A clickable empty-state tile that adds one step type (icon circle, label, description)."""

    TILE_HEIGHT = 72

    def __init__(self, action_type: ActionType, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.action_type = action_type
        meta = theme.STEP_TYPES[action_type]
        self.setObjectName("StepTypeTile")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"Add {meta.label.lower()} step")
        self.setToolTip(f"Add a {meta.label.lower()} step: {meta.description}")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.TILE_HEIGHT)

        title_font = theme.ui_font(theme.FONT_BODY, QFont.Weight.DemiBold)
        text_font = theme.ui_font(theme.FONT_SMALL)
        # The text block is top-aligned at the offset that centres a two-line description,
        # so every tile title in a row sits on the same baseline (one or two lines below it).
        block = QFontMetrics(title_font).height() + 1 + 2 * QFontMetrics(text_font).lineSpacing()
        top = max(theme.SPACE_SM, (self.TILE_HEIGHT - block) // 2)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_MD, top, theme.SPACE_MD, theme.SPACE_SM)
        layout.setSpacing(theme.SPACE_MD)
        self.badge = StepIconBadge(32, 16)
        self.badge.set_step_type(action_type)
        badge_box = QVBoxLayout()
        badge_box.setContentsMargins(0, (self.TILE_HEIGHT - 32) // 2 - top, 0, 0)
        badge_box.addWidget(self.badge)
        badge_box.addStretch(1)
        layout.addLayout(badge_box)

        texts = QVBoxLayout()
        texts.setContentsMargins(0, 0, 0, 0)
        texts.setSpacing(1)
        self.lbl_label = QLabel(meta.label)
        self.lbl_label.setFont(title_font)
        texts.addWidget(self.lbl_label)
        self.lbl_description = QLabel(meta.description)
        self.lbl_description.setFont(text_font)
        self.lbl_description.setProperty("role", "muted")
        self.lbl_description.setWordWrap(True)  # up to two lines in a narrow editor
        self.lbl_description.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        texts.addWidget(self.lbl_description)
        texts.addStretch(1)
        layout.addLayout(texts, stretch=1)
        for child in (self.badge, self.lbl_label, self.lbl_description):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.layout().sizeHint().width(), self.TILE_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self.layout().minimumSize().width(), self.TILE_HEIGHT)


class StepsEmptyState(EmptyState):
    """Empty step list: "Add your first step" with one tile per step type (2 x 2 grid)."""

    step_type_chosen = Signal(object)  # ActionType

    GRID_MAX_WIDTH = 560
    COMPACT_HEIGHT = 300  # below this height the block goes compact to keep the tiles visible
    _MARGIN = (theme.SPACE_LG, theme.SPACE_SM)     # top/bottom margin: full, compact
    _TILES_GAP = (theme.SPACE_LG, theme.SPACE_SM)  # gap above the tiles: full, compact

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(
            "list-plus",
            "Add your first step",
            "A scenario runs its steps from top to bottom.",
            parent=parent,
        )
        self._compact = False
        layout = self.layout()
        layout.setContentsMargins(theme.SPACE_XL, self._MARGIN[0], theme.SPACE_XL, self._MARGIN[0])

        self.tiles_host = QWidget()
        self.tiles_host.setObjectName("StepTypeTiles")
        self.tiles_host.setMaximumWidth(self.GRID_MAX_WIDTH)
        self.tiles_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(self.tiles_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.SPACE_SM)
        self.tiles: Dict[ActionType, StepTypeTile] = {}
        for position, action_type in enumerate(STEP_TYPE_ORDER):
            tile = StepTypeTile(action_type)
            tile.clicked.connect(lambda _checked=False, t=action_type: self.step_type_chosen.emit(t))
            grid.addWidget(tile, position // 2, position % 2)
            self.tiles[action_type] = tile
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # Centred, as wide as the panel allows up to GRID_MAX_WIDTH (the side stretches take the rest).
        tiles_row = QHBoxLayout()
        tiles_row.setContentsMargins(0, 0, 0, 0)
        tiles_row.addStretch(1)
        tiles_row.addWidget(self.tiles_host, 1000)
        tiles_row.addStretch(1)
        before_last_stretch = layout.count() - 1
        layout.insertSpacing(before_last_stretch, self._TILES_GAP[0])
        self._tiles_gap = layout.itemAt(before_last_stretch).spacerItem()
        layout.insertLayout(before_last_stretch + 1, tiles_row)

    def is_compact(self) -> bool:
        """True below COMPACT_HEIGHT: no icon, tighter margins (the tiles stay fully visible)."""
        return self._compact

    def _compact_saving(self) -> int:
        """Height the compact layout saves: the icon (with its spacing) and the tighter gaps."""
        icon_block = self.lbl_icon.sizeHint().height() + theme.SPACE_XS + self.layout().spacing()
        return icon_block + 2 * (self._MARGIN[0] - self._MARGIN[1]) + (self._TILES_GAP[0] - self._TILES_GAP[1])

    def _set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.lbl_icon.setVisible(not compact)
        margin = self._MARGIN[1 if compact else 0]
        layout = self.layout()
        layout.setContentsMargins(theme.SPACE_XL, margin, theme.SPACE_XL, margin)
        self._tiles_gap.changeSize(0, self._TILES_GAP[1 if compact else 0])
        layout.invalidate()
        layout.activate()  # place the tiles now, not on the next event loop pass

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Always the compact minimum (the block goes compact by itself below COMPACT_HEIGHT).

        Counting the full layout made the editor ask for more height than a short window
        has, so the splitter shrank the console while the scenario was empty and grew it
        back when the first step replaced this block.
        """
        hint = super().minimumSizeHint()
        if not self._compact:
            hint.setHeight(max(0, hint.height() - self._compact_saving()))
        return hint

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Short editor (small window, console open): drop the decorative icon and tighten.
        self._set_compact(self.height() < self.COMPACT_HEIGHT)


# -----------------------------------------------------------------------------
# Scenario builder
# -----------------------------------------------------------------------------


class ScenarioBuilder(QWidget):
    """Main Scenario Builder widget managing the list of ActionBlocks."""

    block_selected = Signal(object)  # Optional[ActionBlock]
    scenario_changed = Signal()
    model_changed = Signal()         # Alias for MainWindow integration
    action_added = Signal(object)    # ActionBlock
    action_deleted = Signal(str)     # action_id
    #: A card's enable switch changed ``action.enabled`` (the inspector shows the same flag).
    action_enabled_changed = Signal(object)  # ActionBlock
    #: Whether :meth:`undo_delete` has a step to bring back.
    undo_delete_available = Signal(bool)

    def __init__(self, scenario: Optional[ScenarioModel] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.scenario = scenario or ScenarioModel(name="New Scenario")
        self._card_map: Dict[str, ActionCardWidget] = {}
        self._item_map: Dict[str, QListWidgetItem] = {}
        self._selected_id: Optional[str] = None
        self._active_index: Optional[int] = None
        self._paused = False
        self._editable = True
        self._base_dir: Optional[Path] = None
        self._last_deleted: Optional[Tuple[ActionBlock, int]] = None
        self._menu_appends = False  # the add-step menu was opened from the row under the cards

        self._setup_ui()
        self.populate_from_scenario(self.scenario)

    def _setup_ui(self) -> None:
        inset = ActionCardWidget.INSET
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_LG - inset, theme.SPACE_MD, theme.SPACE_LG - inset, theme.SPACE_SM)
        layout.setSpacing(theme.SPACE_XS)

        # "+ Add step" menu shared by the header button and the row under the cards.
        self.add_step_menu, self.add_step_actions = build_add_step_menu(self)
        for action_type, action in self.add_step_actions.items():
            action.triggered.connect(lambda _checked=False, t=action_type: self._add_from_menu(t))

        # Header: STEPS · count ............ + Add step
        header = QHBoxLayout()
        header.setContentsMargins(inset, 0, inset, 0)
        header.setSpacing(theme.SPACE_SM)
        self.lbl_section = SectionLabel("Steps")
        header.addWidget(self.lbl_section)
        self.lbl_count = QLabel("0")
        self.lbl_count.setObjectName("StepCountBadge")
        self.lbl_count.setProperty("role", "count")  # the same badge as the console count
        self.lbl_count.setFont(theme.ui_font(theme.FONT_SMALL, QFont.Weight.DemiBold))
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_count.setFixedHeight(18)
        self.lbl_count.setMinimumWidth(theme.count_badge_min_width())
        header.addWidget(self.lbl_count)
        header.addStretch(1)
        self.btn_add_step = QPushButton("Add step")
        style_button(self.btn_add_step, "ghost", "plus", icon_px=14, height=theme.CONTROL_HEIGHT_SM)
        self.btn_add_step.setObjectName("AddStepButton")
        self.btn_add_step.setToolTip("Add a step after the selected one")
        self.btn_add_step.clicked.connect(lambda: self._popup_add_step_menu(self.btn_add_step, align_right=True))
        header.addWidget(self.btn_add_step)
        layout.addLayout(header)

        # Cards + the dashed "Add step" row right below the last card.
        self.steps_view = QWidget()
        self.steps_view.setObjectName("StepsView")
        steps_layout = QVBoxLayout(self.steps_view)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setSpacing(0)

        self.list_widget = ActionListWidget()
        self.list_widget.order_changed.connect(self._sync_model_from_list_order)
        self.list_widget.currentItemChanged.connect(self._on_list_item_changed)
        self.list_widget.delete_requested.connect(self._delete_selected)
        self.list_widget.duplicate_requested.connect(self._duplicate_selected)
        self.list_widget.viewport_resized.connect(self._align_add_row)
        steps_layout.addWidget(self.list_widget)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(inset, inset, inset, 0)
        self._add_row_layout = add_row
        self.btn_add_step_inline = QPushButton("Add step")
        style_button(self.btn_add_step_inline, "ghost", "plus", icon_px=14, height=40)
        self.btn_add_step_inline.setObjectName("AddStepRow")
        self.btn_add_step_inline.setToolTip("Add a step at the end")
        self.btn_add_step_inline.clicked.connect(self._on_add_row_clicked)
        add_row.addWidget(self.btn_add_step_inline)
        steps_layout.addLayout(add_row)
        steps_layout.addStretch(1)
        layout.addWidget(self.steps_view, stretch=1)

        # Empty state (kept as ``lbl_empty`` for compatibility; it is the whole block).
        self.lbl_empty = StepsEmptyState()
        self.lbl_empty.step_type_chosen.connect(self.add_action_block)
        self.add_step_tiles: Dict[ActionType, StepTypeTile] = self.lbl_empty.tiles
        layout.addWidget(self.lbl_empty, stretch=1)
        self.lbl_empty.hide()

    def _align_add_row(self) -> None:
        """Keeps the dashed row's right edge in line with the cards when the list shows a scroll bar."""
        inset = ActionCardWidget.INSET
        scroll_bar = max(0, self.list_widget.width() - self.list_widget.viewport().width())
        self._add_row_layout.setContentsMargins(inset, inset, inset + scroll_bar, 0)

    def _popup_add_step_menu(self, anchor: QWidget, align_right: bool = False, append: bool = False) -> None:
        """Opens the add-step menu under ``anchor``; ``append`` adds the chosen step at the end."""
        self._menu_appends = append
        menu = self.add_step_menu
        x = anchor.width() - menu.sizeHint().width() if align_right else 0
        menu.popup(anchor.mapToGlobal(QPoint(x, anchor.height() + 4)))

    def _on_add_row_clicked(self) -> None:
        # The row sits under the last card, so its steps go to the end of the scenario.
        self._popup_add_step_menu(self.btn_add_step_inline, append=True)

    def _add_from_menu(self, action_type: ActionType) -> ActionBlock:
        append, self._menu_appends = self._menu_appends, False
        return self.add_action_block(action_type, index=len(self.scenario.actions) if append else None)

    def set_scenario(self, scenario: ScenarioModel) -> None:
        """Sets active scenario and refreshes entire list."""
        self.scenario = scenario
        self._set_last_deleted(None)  # an undo never crosses into another scenario
        self.populate_from_scenario(scenario)

    # -- context: base folder, run state, read-only mode ---------------------------

    def set_base_dir(self, base_dir: Optional[Union[str, Path]]) -> None:
        """Folder relative template paths start from (the scenario file's folder; None: cwd).

        Image-wait cards resolve their template like the engine and show "Image not
        found" when the file is missing.
        """
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None
        for card in self._card_map.values():
            card.set_base_dir(self._base_dir)

    def base_dir(self) -> Optional[Path]:
        """Returns the folder relative template paths start from (None: the working folder)."""
        return self._base_dir

    def set_paused(self, paused: bool) -> None:
        """Shows "Paused" instead of "Running" on the active card while the run is paused."""
        self._paused = bool(paused)
        for card in self._card_map.values():
            if card.is_executing:
                card.set_executing(True, self._paused)

    def set_editable(self, editable: bool) -> None:
        """Makes the step list read-only (False) while a run is active, or editable again.

        Read-only: no adding, deleting, duplicating, reordering (drag, chevrons, menu)
        or switching steps on/off. A run executes a copy of the scenario and highlights
        steps by position, so edits would neither reach the run nor keep the highlight right.
        """
        editable = bool(editable)
        if editable == self._editable:
            return
        self._editable = editable
        self.list_widget.set_editable(editable)
        for button, tip in ((self.btn_add_step, "Add a step after the selected one"),
                            (self.btn_add_step_inline, "Add a step at the end")):
            button.setEnabled(editable)
            button.setToolTip(tip if editable else READ_ONLY_HINT)
        for tile in self.add_step_tiles.values():
            tile.setEnabled(editable)
        for card in self._card_map.values():
            card.set_editable(editable)

    def is_editable(self) -> bool:
        """False while the list is read-only (a run is active)."""
        return self._editable

    def selected_action(self) -> Optional[ActionBlock]:
        """Returns the selected step, or None."""
        action_id = self._current_action_id()
        return self.scenario.get_action(action_id) if action_id else None

    # -- undo of the last delete ----------------------------------------------------

    def can_undo_delete(self) -> bool:
        """True when :meth:`undo_delete` can bring a deleted step back."""
        return self._last_deleted is not None

    def undo_delete(self) -> Optional[ActionBlock]:
        """Puts the last deleted step back at its old position (same id) and selects it."""
        if self._last_deleted is None or not self._editable:
            return None
        block, index = self._last_deleted
        self._set_last_deleted(None)
        if self.scenario.get_action(block.id) is not None:
            return None
        self.scenario.add_action(block, index=min(index, len(self.scenario.actions)))
        self.populate_from_scenario(self.scenario)
        self.select_action(block.id)
        self.action_added.emit(block)
        self.scenario_changed.emit()
        self.model_changed.emit()
        return block

    def _set_last_deleted(self, entry: Optional[Tuple[ActionBlock, int]]) -> None:
        had = self._last_deleted is not None
        self._last_deleted = entry
        if had != (entry is not None):
            self.undo_delete_available.emit(entry is not None)

    def get_scenario(self) -> ScenarioModel:
        """Returns current scenario model."""
        return self.scenario

    def populate_from_scenario(self, scenario: ScenarioModel) -> None:
        """Reconstructs all card items from scenario actions."""
        self.clear_active_action()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        self._card_map.clear()
        self._item_map.clear()
        self.list_widget.blockSignals(False)
        self._refresh_count(len(scenario.actions))

        if not scenario.actions:
            self.lbl_empty.show()
            self.list_widget.hide()
            self.steps_view.hide()
            self._selected_id = None
            self.block_selected.emit(None)
            return

        self.lbl_empty.hide()
        self.steps_view.show()
        self.list_widget.show()

        tot = len(scenario.actions)
        for idx, act in enumerate(scenario.actions):
            self._create_card_item(act, idx, tot)
        self.list_widget.updateGeometry()

        # Select first card if none selected
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _refresh_count(self, count: int) -> None:
        self.lbl_count.setText(str(count))
        self.lbl_count.setVisible(count > 0)

    def _new_card(self, action: ActionBlock, index: int, total_count: int) -> ActionCardWidget:
        card = ActionCardWidget(action, index, total_count)
        card.set_base_dir(self._base_dir)
        if not self._editable:
            card.set_editable(False)
        card.clicked.connect(self.select_action)
        card.move_up_requested.connect(self.move_action_up)
        card.move_down_requested.connect(self.move_action_down)
        card.delete_requested.connect(self.delete_action)
        card.duplicate_requested.connect(self.duplicate_action)
        card.enabled_toggled.connect(self._on_card_enabled_toggled)
        return card

    def _create_card_item(self, action: ActionBlock, index: int, total_count: int) -> None:
        card = self._new_card(action, index, total_count)

        item = QListWidgetItem(self.list_widget)
        item.setSizeHint(self._card_size_hint(card))
        item.setData(Qt.ItemDataRole.UserRole, action.id)

        self.list_widget.setItemWidget(item, card)
        self._card_map[action.id] = card
        self._item_map[action.id] = item

    @staticmethod
    def _card_size_hint(card: ActionCardWidget) -> QSize:
        """Row size for ``card``: its height, but only its *minimum* width.

        A vertical list stretches every row to the viewport width, so the card fills
        the list and elides its text when narrow, instead of the list scrolling sideways.
        """
        return QSize(card.minimumSizeHint().width(), card.sizeHint().height())

    def add_action_block(self, action_type: ActionType, index: Optional[int] = None) -> ActionBlock:
        """Creates a new action block with valid factory defaults and selects it.

        It goes right after the selected step (or at the end when nothing is
        selected), or at ``index`` when given.
        """
        defaults = {
            ActionType.MOUSE_CLICK: MouseClickParams(x=500, y=500, button="left", clicks=1, interval_ms=0, press_duration_ms=50),
            ActionType.KEY_INPUT: KeyInputParams(keys=["enter"], action="press", modifiers=None, press_duration_ms=50),
            ActionType.FIXED_WAIT: FixedWaitParams(duration_ms=1000),
            ActionType.IMAGE_WAIT: ImageWaitParams(template_path=step_text.DEFAULT_TEMPLATE_PATH, mode="appear", confidence=0.8, timeout_seconds=10.0, roi=None, timeout_action="raise"),
        }
        new_act = ActionBlock(
            id=str(uuid.uuid4()),
            type=action_type,
            params=defaults[action_type],
            enabled=True,
            description="",
        )

        if index is None:
            curr_row = self.list_widget.currentRow()
            index = (curr_row + 1) if curr_row >= 0 else len(self.scenario.actions)
        self.scenario.add_action(new_act, index=index)

        self.populate_from_scenario(self.scenario)
        self.select_action(new_act.id)

        self.action_added.emit(new_act)
        self.scenario_changed.emit()
        self.model_changed.emit()
        return new_act

    def duplicate_action(self, action_id: str) -> Optional[ActionBlock]:
        """Inserts a deep copy (new id) right after ``action_id`` and selects it.

        Emits the same signals as adding a step, so the scenario is marked dirty.
        Returns the copy, or ``None`` when ``action_id`` is unknown.
        """
        source = self.scenario.get_action(action_id)
        if source is None:
            return None
        clone = copy.deepcopy(source)
        clone.id = str(uuid.uuid4())
        self.scenario.add_action(clone, index=self.scenario.actions.index(source) + 1)

        self.populate_from_scenario(self.scenario)
        self.select_action(clone.id)

        self.action_added.emit(clone)
        self.scenario_changed.emit()
        self.model_changed.emit()
        return clone

    def delete_action(self, action_id: str) -> None:
        """Deletes action block by ID and updates selection.

        If the deleted step was selected, the step that takes its place (or the new
        last step) is selected; otherwise the selection stays where it was.
        """
        act = self.scenario.get_action(action_id)
        if act is None:
            return
        index = self.scenario.actions.index(act)
        keep_id = self._selected_id if self._selected_id != action_id else None
        if not self.scenario.remove_action(action_id):
            return
        self._last_deleted = None
        self._set_last_deleted((act, index))

        self.populate_from_scenario(self.scenario)
        if keep_id and keep_id in self._item_map:
            self.select_action(keep_id)
        elif self.list_widget.count():
            self.list_widget.setCurrentRow(min(index, self.list_widget.count() - 1))
        self.action_deleted.emit(action_id)
        self.scenario_changed.emit()
        self.model_changed.emit()

    def _current_action_id(self) -> Optional[str]:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _delete_selected(self) -> None:
        """Delete/Backspace on the list: deletes the selected step, keeps focus in the list."""
        action_id = self._current_action_id()
        if action_id:
            self.delete_action(action_id)
            if self.list_widget.isVisible():
                self.list_widget.setFocus(Qt.FocusReason.OtherFocusReason)

    def _duplicate_selected(self) -> None:
        """Ctrl+D / Cmd+D on the list: duplicates the selected step."""
        action_id = self._current_action_id()
        if action_id:
            self.duplicate_action(action_id)
            self.list_widget.setFocus(Qt.FocusReason.OtherFocusReason)

    def move_action_up(self, action_id: str) -> None:
        """Moves action block up by 1 position."""
        act = self.scenario.get_action(action_id)
        if not act:
            return
        idx = self.scenario.actions.index(act)
        if idx > 0:
            self.scenario.move_action(idx, idx - 1)
            self.populate_from_scenario(self.scenario)
            self.select_action(action_id)
            self.scenario_changed.emit()
            self.model_changed.emit()

    def move_action_down(self, action_id: str) -> None:
        """Moves action block down by 1 position."""
        act = self.scenario.get_action(action_id)
        if not act:
            return
        idx = self.scenario.actions.index(act)
        if idx < len(self.scenario.actions) - 1:
            self.scenario.move_action(idx, idx + 1)
            self.populate_from_scenario(self.scenario)
            self.select_action(action_id)
            self.scenario_changed.emit()
            self.model_changed.emit()

    def select_action(self, action_id: str) -> None:
        """Selects a card and emits block_selected."""
        if action_id not in self._item_map:
            return
        item = self._item_map[action_id]
        if self.list_widget.currentItem() == item:
            act = self.scenario.get_action(action_id)
            self.block_selected.emit(act)
        else:
            self.list_widget.setCurrentItem(item)

    def _on_list_item_changed(self, current: Optional[QListWidgetItem], previous: Optional[QListWidgetItem]) -> None:
        if previous:
            prev_id = previous.data(Qt.ItemDataRole.UserRole)
            if prev_id in self._card_map:
                self._card_map[prev_id].set_selected(False)

        if current:
            curr_id = current.data(Qt.ItemDataRole.UserRole)
            self._selected_id = curr_id
            if curr_id in self._card_map:
                self._card_map[curr_id].set_selected(True)
            act = self.scenario.get_action(curr_id)
            self.block_selected.emit(act)
        else:
            self._selected_id = None
            self.block_selected.emit(None)

    def update_action_block(self, action: ActionBlock) -> None:
        """Synchronizes edits made from PropertyInspector back to the card."""
        if action.id in self._card_map:
            card = self._card_map[action.id]
            card.update_content(action, card.index, card.total_count)
            self.scenario_changed.emit()
            self.model_changed.emit()

    def update_block_display(self, block: ActionBlock) -> None:
        """Alias for update_action_block for MainWindow integration."""
        self.update_action_block(block)

    def set_active_action(self, index: Optional[int]) -> None:
        """Highlights card currently executing."""
        self.clear_active_action()
        if index is None or index < 0 or index >= self.list_widget.count():
            return
        self._active_index = index
        item = self.list_widget.item(index)
        if item:
            act_id = item.data(Qt.ItemDataRole.UserRole)
            if act_id in self._card_map:
                self._card_map[act_id].set_executing(True, self._paused)
            self.list_widget.scrollToItem(item)

    def clear_active_action(self) -> None:
        """Clears active execution highlight from all cards."""
        if self._active_index is not None and 0 <= self._active_index < self.list_widget.count():
            item = self.list_widget.item(self._active_index)
            if item:
                act_id = item.data(Qt.ItemDataRole.UserRole)
                if act_id in self._card_map:
                    self._card_map[act_id].set_executing(False)
        for card in self._card_map.values():
            if card.is_executing:
                card.set_executing(False)
        self._active_index = None

    def highlight_active_action(self, index: Optional[int]) -> None:
        """Sets active executing action or clears if index is None or < 0."""
        if index is None or index < 0:
            self.clear_active_action()
        else:
            self.set_active_action(index)

    def _sync_model_from_list_order(self) -> None:
        """Synchronizes scenario.actions order after Drag & Drop move and re-attaches card widgets."""
        curr_item = self.list_widget.currentItem()
        curr_id = curr_item.data(Qt.ItemDataRole.UserRole) if curr_item else self._selected_id
        new_order: List[ActionBlock] = []
        tot = self.list_widget.count()
        self._item_map.clear()

        for i in range(tot):
            item = self.list_widget.item(i)
            act_id = item.data(Qt.ItemDataRole.UserRole)
            act = self.scenario.get_action(act_id)
            if act:
                new_order.append(act)
                self._item_map[act.id] = item
                if act.id in self._card_map:
                    card = self._card_map[act.id]
                    card.update_content(act, i, tot)
                else:
                    card = self._new_card(act, i, tot)
                    self._card_map[act.id] = card
                self.list_widget.setItemWidget(item, card)
                item.setSizeHint(self._card_size_hint(card))

        self.scenario.actions = new_order
        if curr_id and curr_id in self._item_map:
            self.select_action(curr_id)
            if curr_id in self._card_map:
                self._card_map[curr_id].set_selected(True)
        elif self.list_widget.count() > 0:
            first_item = self.list_widget.item(0)
            if first_item:
                first_id = first_item.data(Qt.ItemDataRole.UserRole)
                if first_id:
                    self.select_action(first_id)
        self.list_widget.updateGeometry()

        self.scenario_changed.emit()
        self.model_changed.emit()

    def _on_card_enabled_toggled(self, action_id: str, enabled: bool) -> None:
        action = self.scenario.get_action(action_id)
        if action is not None:
            self.action_enabled_changed.emit(action)
        self.scenario_changed.emit()
        self.model_changed.emit()
