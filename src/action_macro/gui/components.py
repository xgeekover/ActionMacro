"""
action_macro.gui.components
==============================
Small reusable widgets of the design system (status dot, elided label, section
label, separators, icon buttons, the Run button with its shortcut hint, an
in-place rename field, the empty-state block, the toggle switch and the segmented
control) plus shortcut-text helpers.

They take their colours from :mod:`action_macro.gui.theme` and their look from
the application style sheet, so they stay consistent wherever they are placed.
"""

from __future__ import annotations

from typing import Optional, Union

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFocusEvent,
    QFont,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from action_macro.gui import icons, theme


class StatusDot(QWidget):
    """A small filled circle used as a state indicator (engine state, dirty flag)."""

    def __init__(self, color: str = theme.COLORS.text_muted, diameter: int = 8, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._diameter = diameter
        self.setFixedSize(diameter, diameter)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def color(self) -> QColor:
        """Returns the current dot colour."""
        return QColor(self._color)

    def set_color(self, color: str) -> None:
        """Changes the dot colour (a ``#RRGGBB`` token)."""
        new_color = QColor(color)
        if new_color != self._color:
            self._color = new_color
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect().adjusted(0, 0, -1, -1))
        painter.end()


class ElidedLabel(QLabel):
    """A single-line label that elides its text instead of growing the layout.

    ``text()`` always returns the full text; only the painting is elided. When the
    text does not fit, the full text is offered as a tooltip, unless a tooltip was
    set explicitly with :meth:`setToolTip` (that one always wins).
    """

    def __init__(self, text: str = "", parent: Optional[QWidget] = None,
                 elide_mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight) -> None:
        super().__init__(text, parent)
        self._elide_mode = elide_mode
        self._explicit_tooltip = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._sync_tooltip()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), self.fontMetrics().horizontalAdvance("…") * 4), hint.height())

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        super().setText(text)
        self._sync_tooltip()

    def setToolTip(self, text: str) -> None:  # noqa: N802 - Qt override
        self._explicit_tooltip = text or ""
        self._sync_tooltip()

    def is_elided(self) -> bool:
        """True when the text is wider than the label and is painted with an ellipsis."""
        return self.fontMetrics().horizontalAdvance(self.text()) > self.contentsRect().width()

    def _sync_tooltip(self) -> None:
        tooltip = self._explicit_tooltip or (self.text() if self.is_elided() else "")
        if tooltip != super().toolTip():
            super().setToolTip(tooltip)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_tooltip()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self._sync_tooltip()

    def elided_text(self, width: int) -> str:
        """The text as painted in ``width`` pixels (subclasses may elide differently)."""
        return self.fontMetrics().elidedText(self.text(), self._elide_mode, width)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        rect = self.contentsRect()
        elided = self.elided_text(rect.width())
        self.style().drawItemText(
            painter, rect, int(self.alignment() | Qt.AlignmentFlag.AlignVCenter),
            self.palette(), self.isEnabled(), elided, QPalette.ColorRole.WindowText,
        )
        painter.end()


class SectionLabel(QLabel):
    """Small upper-case, tracked label that titles a panel section (e.g. "SCENARIOS")."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text.upper(), parent)
        self.setProperty("role", "section")
        self.setFont(theme.section_font())


class Separator(QFrame):
    """A 1 px divider line in the border colour (vertical by default)."""

    def __init__(self, vertical: bool = True, length: Optional[int] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("VSeparator" if vertical else "HSeparator")
        if length is not None:
            if vertical:
                self.setFixedHeight(length)
            else:
                self.setFixedWidth(length)


class ToggleSwitch(QCheckBox):
    """A QCheckBox drawn as a small toggle switch (accent track when on), optionally labelled.

    It keeps the whole QCheckBox API (``isChecked``, ``setChecked``, ``toggled``,
    ``click``, ``text``), so code and tests treat it as a check box. With a text the
    label is drawn right of the track and clicking it toggles the switch too.
    """

    TRACK_WIDTH = 28
    TRACK_HEIGHT = 16
    MARGIN = 3  # room for the 2 px keyboard focus ring (1 px gap + 2 px pen)
    LABEL_GAP = theme.SPACE_SM

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = self.TRACK_WIDTH + 2 * self.MARGIN
        height = self.TRACK_HEIGHT + 2 * self.MARGIN
        if self.text():
            width += self.LABEL_GAP + self.fontMetrics().horizontalAdvance(self.text())
            height = max(height, self.fontMetrics().height())
        return QSize(width, height)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def hitButton(self, pos: QPoint) -> bool:  # noqa: N802 - Qt override
        return self.rect().contains(pos)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self.update()

    def track_rect(self) -> QRectF:
        """The switch track: centred without a label, at the left edge with one."""
        x = float(self.MARGIN) if self.text() else (self.width() - self.TRACK_WIDTH) / 2.0
        return QRectF(x, (self.height() - self.TRACK_HEIGHT) / 2.0, self.TRACK_WIDTH, self.TRACK_HEIGHT)

    def knob_rect(self) -> QRectF:
        """The round knob: at the right end when on, at the left end when off."""
        track = self.track_rect()
        diameter = self.TRACK_HEIGHT - 4.0
        x = track.right() - 2.0 - diameter if self.isChecked() else track.left() + 2.0
        return QRectF(x, track.top() + 2.0, diameter, diameter)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        c = theme.COLORS
        hovered = self.underMouse() and self.isEnabled()
        if self.isChecked():
            track_color = theme.qcolor(c.accent_hover if hovered else c.accent)
            knob_color = theme.qcolor(c.on_accent)
        else:
            track_color = theme.qcolor(c.border_strong if hovered else c.border)
            knob_color = theme.qcolor(c.text if hovered else c.text_muted)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.text():
            text_rect = self.rect().adjusted(int(self.track_rect().right()) + self.LABEL_GAP, 0, 0, 0)
            self.style().drawItemText(
                painter, text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                self.palette(), self.isEnabled(), self.text(), QPalette.ColorRole.WindowText,
            )
        if not self.isEnabled():
            painter.setOpacity(0.4)
        track = self.track_rect()
        radius = track.height() / 2.0
        if self.hasFocus():
            painter.setPen(QPen(theme.qcolor(c.accent_text), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            ring = track.adjusted(-2.0, -2.0, 2.0, 2.0)  # the pen covers 1-3 px outside the track
            painter.drawRoundedRect(ring, ring.height() / 2.0, ring.height() / 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, radius, radius)
        painter.setBrush(knob_color)
        painter.drawEllipse(self.knob_rect())
        painter.end()


def make_icon_button(
    icon_name: str,
    tooltip: str,
    *,
    size: int = theme.CONTROL_HEIGHT_SM,
    icon_px: int = 16,
    checkable: bool = False,
    color: Optional[str] = None,
    checked_color: Optional[str] = None,
    parent: Optional[QWidget] = None,
) -> QToolButton:
    """Creates a square, icon-only QToolButton with a tooltip and hover state."""
    button = QToolButton(parent)
    button.setIcon(icons.icon(
        icon_name,
        color or theme.COLORS.text_muted,
        icon_px,
        active_color=theme.COLORS.text,
        checked_color=checked_color or theme.COLORS.accent_text,
    ))
    button.setIconSize(QSize(icon_px, icon_px))
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setCheckable(checkable)
    button.setFixedSize(size, size)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    button.setAutoRaise(True)
    return button


#: Room between a text button's icon and its label (QPushButton has no QSS for it).
ICON_TEXT_GAP = 4

#: ``controlSize`` style property per control height: the style sheet's ``min-height`` would
#: otherwise override ``setFixedHeight`` (QSS minimums win over a smaller fixed height).
_BUTTON_SIZES = {theme.CONTROL_HEIGHT_SM: "sm", theme.CONTROL_HEIGHT: "md"}

#: Variants drawn with the standard button padding (the ``size`` heights assume it).
_PADDED_VARIANTS = ("primary", "secondary", "ghost", "danger")


def style_button(
    button: QPushButton,
    variant: str = "secondary",
    icon_name: Optional[str] = None,
    *,
    icon_color: Optional[str] = None,
    icon_px: int = 16,
    height: Optional[int] = theme.CONTROL_HEIGHT,
) -> QPushButton:
    """Applies a button variant, an optional Lucide icon, the hand cursor and height.

    ``theme.CONTROL_HEIGHT`` (32) and ``theme.CONTROL_HEIGHT_SM`` (28) are also set
    through the ``controlSize`` style property, so the style sheet honours them.
    """
    button.setProperty("variant", variant)
    if icon_name:
        default_color = {
            "primary": theme.COLORS.on_accent,
            "danger": theme.COLORS.on_accent,
            "ghost": theme.COLORS.text_muted,
        }.get(variant, theme.COLORS.text)
        gap = ICON_TEXT_GAP if button.text() else 0
        button.setIcon(icons.icon(icon_name, icon_color or default_color, icon_px,
                                  active_color=icon_color or (theme.COLORS.text if variant == "ghost" else None),
                                  trailing_gap=gap))
        button.setIconSize(QSize(icon_px + gap, icon_px))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if height is not None:
        button.setFixedHeight(height)
    size = _BUTTON_SIZES.get(height) if variant in _PADDED_VARIANTS else None
    if button.property("controlSize") != size:
        button.setProperty("controlSize", size)
    return button


class KeyHintButton(QPushButton):
    """A push button that paints a keyboard-shortcut hint (e.g. "F5") at its right edge.

    The style sheet reserves the space through ``theme.RUN_BUTTON_HINT_PAD`` on the
    ``RunButton`` object name, so the icon and label stay centred in the remaining area.
    """

    def __init__(self, text: str, hint: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._hint = hint

    def hint(self) -> str:
        """Returns the shortcut hint text."""
        return self._hint

    def set_hint(self, hint: str) -> None:
        """Changes the shortcut hint text."""
        self._hint = hint
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        if not self._hint:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(theme.ui_font(theme.FONT_SMALL, QFont.Weight.DemiBold))
        opacity = 0.70 if self.isEnabled() else 0.40
        painter.setPen(theme.qcolor(theme.COLORS.on_accent, opacity))
        hint_rect = QRect(self.width() - theme.RUN_BUTTON_HINT_PAD, 0, theme.RUN_BUTTON_HINT_PAD - 10, self.height())
        painter.drawText(hint_rect, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), self._hint)
        painter.end()


# -----------------------------------------------------------------------------
# Shortcut text
# -----------------------------------------------------------------------------

KeySpec = Union[str, QKeySequence, QKeySequence.StandardKey]


def shortcut_text(sequence: KeySpec) -> str:
    """Returns ``sequence`` in the platform's own wording (``⌘B`` on macOS, ``Ctrl+B`` elsewhere).

    Use it for tooltips and hints of *in-app* shortcuts (QAction shortcuts), so
    they match what the menus show. Global hotkeys read by pynput (the emergency
    stop) are physical keys and must not go through this function.
    """
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


def with_shortcut(text: str, sequence: Optional[KeySpec]) -> str:
    """Returns ``"text (shortcut)"`` in native wording, or ``text`` when there is no shortcut."""
    if sequence is None:
        return text
    hint = shortcut_text(sequence)
    return f"{text} ({hint})" if hint else text


# -----------------------------------------------------------------------------
# In-place rename field
# -----------------------------------------------------------------------------


class InlineEdit(QLineEdit):
    """A line edit for renaming in place (Postman-style title field).

    Enter confirms (``editingFinished`` / ``returnPressed`` as usual). Esc restores
    the text the field had when it gained focus, clears the modified flag and then
    emits :attr:`edit_cancelled`, so the owner can put back its committed value
    (which may differ, for example after a save during the edit) and move focus.
    """

    edit_cancelled = Signal()

    def __init__(self, text: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(text, parent)
        self._text_at_focus = text

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt override
        self._text_at_focus = self.text()
        super().focusInEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.setText(self._text_at_focus)  # setText() also clears isModified()
            event.accept()
            self.edit_cancelled.emit()
            return
        super().keyPressEvent(event)


# -----------------------------------------------------------------------------
# Empty state
# -----------------------------------------------------------------------------


def empty_state_badge(icon_name: str, tint: str = theme.COLORS.accent, diameter: int = 48) -> QPixmap:
    """Returns a 2x pixmap of ``icon_name`` inside a softly tinted circle."""
    dpr = 2.0
    pixmap = QPixmap(int(diameter * dpr), int(diameter * dpr))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(theme.qcolor(tint, 0.14))
    painter.drawEllipse(0, 0, diameter, diameter)
    glyph = diameter // 2
    offset = (diameter - glyph) // 2
    painter.drawPixmap(QRect(offset, offset, glyph, glyph), icons.pixmap(icon_name, tint, glyph))
    painter.end()
    return pixmap


class EmptyState(QWidget):
    """Centred empty-state block: tinted icon, headline, one-line explanation, optional action.

    ``compact=True`` uses a smaller icon and a 14 px headline for side panels;
    the default uses the 18 px empty-state headline of the design brief.
    """

    def __init__(
        self,
        icon_name: str,
        title: str,
        text: str = "",
        *,
        action_text: Optional[str] = None,
        action_icon: Optional[str] = None,
        tint: str = theme.COLORS.accent,
        compact: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL)
        layout.setSpacing(theme.SPACE_SM)
        layout.addStretch(1)

        self.lbl_icon = QLabel()
        self.lbl_icon.setObjectName("EmptyStateIcon")
        self.lbl_icon.setPixmap(empty_state_badge(icon_name, tint, 40 if compact else 52))
        self.lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_icon)
        layout.addSpacing(theme.SPACE_XS)

        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("EmptyStateTitle")
        self.lbl_title.setFont(theme.ui_font(theme.FONT_TITLE if compact else theme.FONT_HEADLINE,
                                             QFont.Weight.DemiBold))
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_title.setWordWrap(True)
        layout.addWidget(self.lbl_title)

        self.lbl_text = QLabel(text)
        self.lbl_text.setObjectName("EmptyStateText")
        self.lbl_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_text.setWordWrap(True)
        self.lbl_text.setVisible(bool(text))
        layout.addWidget(self.lbl_text)

        self.btn_action: Optional[QPushButton] = None
        if action_text:
            layout.addSpacing(theme.SPACE_SM)
            self.btn_action = QPushButton(action_text)
            style_button(self.btn_action, "primary", action_icon, icon_px=14)
            layout.addWidget(self.btn_action, alignment=Qt.AlignmentFlag.AlignHCenter)

        layout.addStretch(1)

    def title(self) -> str:
        """Returns the headline text."""
        return self.lbl_title.text()

    def text(self) -> str:
        """Returns the explanation text."""
        return self.lbl_text.text()


# ---- phase2:inspector ----
# Imports of this block stay here (not at the top) so parallel phase-2 edits of the
# module merge cleanly.
from typing import List, Sequence, Tuple  # noqa: E402

from PySide6.QtWidgets import QButtonGroup, QComboBox, QHBoxLayout  # noqa: E402

#: One option of a :class:`SegmentedControl`: ``(value, label)`` or ``(value, label, tooltip)``.
SegmentOption = Union[Tuple[str, str], Tuple[str, str, str]]


class SegmentedControl(QFrame):
    """A row of mutually exclusive segments for a small enum (e.g. Left | Right | Middle).

    :meth:`value` returns the selected option value; :attr:`value_changed` fires
    when the selection really changes, by a click or through :meth:`set_value`.

    :meth:`bind_combo` makes an existing ``QComboBox`` (whose item texts are the
    option values) the source of truth: a click selects the matching combo item,
    and any change of the combo, including ``setCurrentText``/``setCurrentIndex``
    from code, is mirrored by the segments. The combo can then stay hidden.
    """

    value_changed = Signal(str)

    def __init__(self, options: Sequence[SegmentOption], parent: Optional[QWidget] = None, *,
                 compact: bool = False) -> None:
        """``compact``: 28 px tall, small labels, as wide as its content (toolbars, headers);
        otherwise 32 px tall and the segments share the full width (forms)."""
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        self._compact = compact
        if compact:
            self.setProperty("controlSize", "sm")
            self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._values: List[str] = []
        self._value: Optional[str] = None
        self._combo: Optional[QComboBox] = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for index, option in enumerate(options):
            value, label = option[0], option[1]
            tooltip = option[2] if len(option) > 2 else ""
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if compact:
                button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                button.setMinimumWidth(0)
            else:
                button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setAccessibleName(label)
            if tooltip:
                button.setToolTip(tooltip)
            self._group.addButton(button, index)
            layout.addWidget(button, 0 if compact else 1)
            self._values.append(value)
        self._group.idClicked.connect(self._on_segment_clicked)

    # -- queries ---------------------------------------------------------------

    def values(self) -> List[str]:
        """Returns the option values in display order."""
        return list(self._values)

    def value(self) -> Optional[str]:
        """Returns the selected value, or None when nothing is selected."""
        return self._value

    def is_compact(self) -> bool:
        """True for the 28 px, content-wide variant."""
        return self._compact

    def buttons(self) -> List[QPushButton]:
        """Returns the segment buttons in display order."""
        return [self._group.button(index) for index in range(len(self._values))]  # type: ignore[misc]

    def button(self, value: str) -> Optional[QPushButton]:
        """Returns the segment button of ``value`` (None for an unknown value)."""
        if value not in self._values:
            return None
        return self._group.button(self._values.index(value))  # type: ignore[return-value]

    # -- changes -----------------------------------------------------------------

    def set_value(self, value: Optional[str]) -> None:
        """Selects ``value`` (through the bound combo, when there is one)."""
        if self._combo is not None and value is not None:
            index = self._combo.findText(value)
            if index >= 0:
                self._combo.setCurrentIndex(index)  # mirrored back by _sync_from_combo
                self._apply(value)
            return
        self._apply(value)

    def bind_combo(self, combo: QComboBox) -> None:
        """Mirrors ``combo`` both ways; the combo stays the source of truth."""
        self._combo = combo
        combo.currentIndexChanged.connect(self._sync_from_combo)
        self._sync_from_combo()

    def _sync_from_combo(self, *_args: object) -> None:
        if self._combo is not None:
            self._apply(self._combo.currentText())

    def _on_segment_clicked(self, index: int) -> None:
        if 0 <= index < len(self._values):
            self.set_value(self._values[index])

    def _apply(self, value: Optional[str]) -> None:
        known = value in self._values
        target = self._group.button(self._values.index(value)) if known else None
        if target is not None and not target.isChecked():
            target.setChecked(True)
        elif target is None and self._group.checkedButton() is not None:
            self._group.setExclusive(False)  # an exclusive group cannot uncheck its last button
            self._group.checkedButton().setChecked(False)
            self._group.setExclusive(True)
        new_value = value if known else None
        if new_value != self._value:
            self._value = new_value
            if new_value is not None:
                self.value_changed.emit(new_value)
