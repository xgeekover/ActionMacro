"""
action_macro.gui.property_inspector
=======================================
Property inspector of the selected step (the right-hand panel).

Layout: a header ("STEP n" and the step type with its tinted icon), the step's
description and "Enabled" switch (the same switch as on the step card), then one
form per step type. Forms are made of
small-caps sections with the label above each input; numeric inputs take half
the panel width, text inputs and segmented controls the full width.

Small enums are shown as segmented controls that mirror hidden combo boxes. The
combos (``combo_button``, ``combo_action``, ``combo_mode``,
``combo_timeout_action``) stay the source of truth, so code and tests can keep
using ``setCurrentText``/``setCurrentIndex``.

Relative template paths resolve against the scenario file's folder exactly as
the execution engine resolves them (see :meth:`PropertyInspector.set_base_dir`).
The validation line under the form has three states: ``valid`` (✓), ``warning``
(⚠, the step is saved but will fail when it runs, e.g. a missing template file)
and ``invalid`` (⚠, the edit is not applied).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Union

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QPixmap, QValidator
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from action_macro.core.key_mapping import (
    build_key_chord,
    format_key_combo,
    parse_key_combo,
)
from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
)
from action_macro.gui import icons, step_text, theme
from action_macro.gui.components import (
    ElidedLabel,
    EmptyState,
    SectionLabel,
    SegmentedControl,
    Separator,
    ToggleSwitch,
    empty_state_badge,
    style_button,
    with_shortcut,
)

# Global (virtual desktop) coordinate bounds. Monitors placed left of or above
# the primary display have negative origins, so the lower bound must be negative;
# clamping to 0 would silently retarget clicks onto the primary display.
COORD_MIN = -99999
COORD_MAX = 99999
COORD_TOOLTIP = (
    "Global desktop coordinate. Negative values address monitors placed "
    "left of or above the primary display."
)
ROI_ORIGIN_TOOLTIP = "Top-left corner of the search region in global desktop coordinates."

COORD_HELP = (
    "Screen position in logical points; negative values reach monitors left of "
    "or above the main one."
)
KEYS_HELP = "Join keys with + to press them together, e.g. ctrl+c or cmd_or_ctrl+shift+t."
CONFIDENCE_HELP = (
    "How closely the screen must match the image. Lower it when the image is not "
    "found; raise it when a wrong spot matches."
)
ROI_HELP = (
    "Search only inside this rectangle (screen coordinates in logical points). "
    "A smaller area is faster and avoids matches elsewhere."
)

#: Same shortcut as MainWindow.act_picker (shown in the "Pick from screen" tooltip).
PICK_SHORTCUT = "Ctrl+P"
#: Image files the template field accepts (Browse filter and drag and drop).
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")
#: Widest the inspector content grows when the panel is dragged wider.
CONTENT_MAX_WIDTH = 460
#: Logical size of the template thumbnail box.
THUMB_SIZE = QSize(88, 60)

_ROW_GAP = theme.SPACE_MD      # between fields of a section
_SECTION_GAP = theme.SPACE_XL  # between sections
_SPIN_TEXT_INDENT = 9          # border (1) + input padding (8), as in QLineEdit
_SPIN_MIN_WIDTH = 80           # "-99999" still fits; two spins share a 300 px panel

#: Validation line texts of an image-wait step whose template cannot be used (state "warning").
TEMPLATE_WARNINGS = {
    "placeholder": "⚠ Choose the image to wait for: drop a file on the card or use Browse…",
    "missing": "⚠ Image file not found. The step fails when it runs unless the file is there.",
    "invalid": "⚠ This file is not a readable image. The step fails when it runs.",
}


# -----------------------------------------------------------------------------
# Layout helpers
# -----------------------------------------------------------------------------


def _field_label(text: str, buddy: Optional[QWidget] = None) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "field")
    if buddy is not None:
        label.setBuddy(buddy)
    return label


def _help_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "help")
    label.setWordWrap(True)
    return label


def _field(
    label: Union[QLabel, QWidget, str, None],
    body: Union[QWidget, QLayout],
    help_label: Optional[QLabel] = None,
) -> QWidget:
    """A field: label above the input, optional help text under it."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.SPACE_XS + 2)
    if isinstance(label, str):
        label = _field_label(label, body if isinstance(body, QWidget) else None)
    if label is not None:
        layout.addWidget(label)
    if isinstance(body, QLayout):
        layout.addLayout(body)
    else:
        layout.addWidget(body)
    if help_label is not None:
        layout.addWidget(help_label)
    return box


def _pair(left: QWidget, right: Optional[QWidget] = None) -> QWidget:
    """Two equal columns (the right one may stay empty, giving a half-width field)."""
    box = QWidget()
    grid = QGridLayout(box)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(_ROW_GAP)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 1)
    grid.addWidget(left, 0, 0)
    if right is not None:
        grid.addWidget(right, 0, 1)
    return box


def _polish_spin(spin: QAbstractSpinBox) -> QAbstractSpinBox:
    """Lines the spin box text up with line edits (the style sheet's left padding
    does not reach the embedded editor of a spin box) and lets it shrink to half
    of the narrowest panel (its size hint is sized for the widest value)."""
    spin.lineEdit().setTextMargins(_SPIN_TEXT_INDENT, 0, 0, 0)
    spin.setMinimumWidth(_SPIN_MIN_WIDTH)
    return spin


def _spin(minimum: int, maximum: int, *, step: int = 1, value: int = 0,
          suffix: str = "", mono: bool = False) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setValue(value)
    if suffix:
        spin.setSuffix(suffix)
    if mono:
        spin.setFont(theme.mono_font(theme.FONT_BODY))
    _polish_spin(spin)
    return spin


def _hidden_combo(parent: QWidget, items: List[str]) -> QComboBox:
    """A combo box that stays the source of truth behind a segmented control."""
    combo = QComboBox(parent)
    combo.addItems(items)
    combo.hide()
    return combo


class _Section(QWidget):
    """A small-caps section title followed by its fields."""

    def __init__(self, title: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.title = SectionLabel(title)
        self._layout.addWidget(self.title)
        self._first = True

    def add_row(self, row: Union[QWidget, QLayout], gap: Optional[int] = None) -> None:
        self._layout.addSpacing(theme.SPACE_SM + 2 if self._first else (_ROW_GAP if gap is None else gap))
        self._first = False
        if isinstance(row, QLayout):
            self._layout.addLayout(row)
        else:
            self._layout.addWidget(row)


class PercentSpinBox(QDoubleSpinBox):
    """A 0..1 spin box shown as a whole percentage ("80%"); ``value()`` stays 0..1."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setDecimals(2)
        self.setRange(0.0, 1.0)
        self.setSingleStep(0.01)
        self.setSuffix("%")

    def textFromValue(self, value: float) -> str:  # noqa: N802 - Qt override
        return str(int(round(value * 100)))

    def valueFromText(self, text: str) -> float:  # noqa: N802 - Qt override
        core = text.replace(self.suffix(), "").replace("%", "").strip()
        try:
            return max(0.0, min(100.0, float(core))) / 100.0
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int) -> object:  # noqa: D102 - Qt override
        core = text.replace(self.suffix(), "").replace("%", "").strip()
        if not core:
            return (QValidator.State.Intermediate, text, pos)
        if not core.isdigit():
            return (QValidator.State.Invalid, text, pos)
        state = QValidator.State.Acceptable if 0 <= int(core) <= 100 else QValidator.State.Invalid
        return (state, text, pos)


class _FormPage(QWidget):
    """Base of the step forms.

    ``QStackedLayout.heightForWidth`` takes the maximum over *all* pages, whatever
    their size policy, so the word-wrapped help texts of a hidden form would make
    the inspector as tall as the longest form (a scrollbar even on the empty page).
    A page that :meth:`PropertyInspector._fit_stack_to_current_page` parked with
    the ``Ignored`` policy therefore reports no height of its own.
    """

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        if self.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Ignored:
            return 0
        return super().heightForWidth(width)


# -----------------------------------------------------------------------------
# Mouse click
# -----------------------------------------------------------------------------


class MouseClickForm(_FormPage):
    """Parameter editor sub-form for MOUSE_CLICK action."""

    modified = Signal()
    pick_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._block_signals = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_SECTION_GAP)

        # Target position
        target = _Section("Target position")
        self.spin_x = _spin(COORD_MIN, COORD_MAX, mono=True)
        self.spin_x.setToolTip(COORD_TOOLTIP)
        self.spin_x.valueChanged.connect(self._on_changed)
        self.spin_y = _spin(COORD_MIN, COORD_MAX, mono=True)
        self.spin_y.setToolTip(COORD_TOOLTIP)
        self.spin_y.valueChanged.connect(self._on_changed)
        target.add_row(_pair(_field("X", self.spin_x), _field("Y", self.spin_y)))
        self.lbl_coord_help = _help_label(COORD_HELP)
        target.add_row(self.lbl_coord_help, gap=theme.SPACE_SM - 2)

        self.btn_pick = QPushButton("Pick from screen")
        style_button(self.btn_pick, "secondary", "crosshair", icon_px=14)
        self.btn_pick.setToolTip(with_shortcut("Pick a point on the screen", PICK_SHORTCUT))
        self.btn_pick.clicked.connect(self.pick_requested.emit)
        pick_row = QHBoxLayout()
        pick_row.setContentsMargins(0, 0, 0, 0)
        pick_row.addWidget(self.btn_pick)
        pick_row.addStretch(1)
        target.add_row(pick_row)
        layout.addWidget(target)

        # Click options
        click = _Section("Click")
        self.combo_button = _hidden_combo(click, ["left", "right", "middle"])
        self.combo_button.currentIndexChanged.connect(self._on_changed)
        self.seg_button = SegmentedControl([
            ("left", "Left", "Click with the left (primary) button."),
            ("right", "Right", "Click with the right (secondary) button."),
            ("middle", "Middle", "Click with the middle button or wheel."),
        ])
        self.seg_button.bind_combo(self.combo_button)
        click.add_row(_field("Button", self.seg_button))

        self.spin_clicks = _spin(1, 10, value=1)
        self.spin_clicks.setToolTip("How many times to click (2 = double-click).")
        self.spin_clicks.valueChanged.connect(self._on_changed)
        self.spin_interval = _spin(0, 60000, step=10, suffix=" ms")
        self.spin_interval.setToolTip("Pause between two clicks.")
        self.spin_interval.valueChanged.connect(self._on_changed)
        click.add_row(_pair(_field("Clicks", self.spin_clicks), _field("Interval", self.spin_interval)))

        self.spin_duration = _spin(0, 10000, step=10, value=50, suffix=" ms")
        self.spin_duration.setToolTip("How long the button stays down for each click.")
        self.spin_duration.valueChanged.connect(self._on_changed)
        click.add_row(_pair(_field("Press duration", self.spin_duration)))
        layout.addWidget(click)
        layout.addStretch()

    def set_values(self, params: Any) -> None:
        self._block_signals = True
        self.spin_x.setValue(int(params["x"]))
        self.spin_y.setValue(int(params["y"]))
        btn_str = str(params.get("button", "left")).lower()
        idx = self.combo_button.findText(btn_str)
        if idx >= 0:
            self.combo_button.setCurrentIndex(idx)
        self.spin_clicks.setValue(int(params.get("clicks", 1)))
        self.spin_interval.setValue(int(params.get("interval_ms", 0)))
        self.spin_duration.setValue(int(params.get("press_duration_ms", 50)))
        self._block_signals = False

    def get_values(self) -> MouseClickParams:
        return MouseClickParams(
            x=self.spin_x.value(),
            y=self.spin_y.value(),
            button=self.combo_button.currentText(),
            clicks=self.spin_clicks.value(),
            interval_ms=self.spin_interval.value(),
            press_duration_ms=self.spin_duration.value(),
        )

    def set_coordinates(self, x: int, y: int) -> None:
        self.spin_x.setValue(x)
        self.spin_y.setValue(y)

    def _on_changed(self) -> None:
        if not self._block_signals:
            self.modified.emit()


# -----------------------------------------------------------------------------
# Key input
# -----------------------------------------------------------------------------


class KeyInputForm(_FormPage):
    """Parameter editor sub-form for KEY_INPUT action."""

    modified = Signal()

    COMMON_KEYS = [
        "enter", "esc", "space", "tab", "backspace", "delete",
        "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
        "up", "down", "left", "right", "home", "end", "page_up", "page_down",
    ]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._block_signals = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_SECTION_GAP)

        # Keys
        keys = _Section("Keys")
        self.edit_keys = QLineEdit()
        self.edit_keys.setPlaceholderText("e.g. enter, a or ctrl+c")
        self.edit_keys.setToolTip(
            "Keys joined with '+' form one shortcut and are pressed together: "
            "they go down in order, the last key is held for the press duration, "
            "then all are released in reverse order."
        )
        self.edit_keys.textChanged.connect(self._on_changed)

        # The formatted chord, shown at the right end of the "Keys" label row.
        self.lbl_human = QLabel()
        self.lbl_human.setProperty("role", "chord")
        self.lbl_human.setToolTip("How this shortcut reads on this computer.")
        self.lbl_preview = self.lbl_human  # former name of the formatted-chord label
        label_row = QWidget()
        label_layout = QHBoxLayout(label_row)
        label_layout.setContentsMargins(0, 0, 0, 0)
        label_layout.setSpacing(theme.SPACE_SM)
        label_layout.addWidget(_field_label("Key or shortcut", self.edit_keys))
        label_layout.addStretch(1)
        label_layout.addWidget(self.lbl_human)

        self.lbl_keys_help = _help_label(KEYS_HELP)
        keys.add_row(_field(label_row, self.edit_keys, self.lbl_keys_help))

        # Shortcut builder: modifier chips, a preset key and Insert.
        self.chk_ctrl = QCheckBox("Ctrl/Cmd")
        self.chk_ctrl.setToolTip("Cmd on macOS, Ctrl on Windows and Linux.")
        self.chk_alt = QCheckBox("Alt/Opt")
        self.chk_alt.setToolTip("Option on macOS, Alt on Windows and Linux.")
        self.chk_shift = QCheckBox("Shift")
        chips = QHBoxLayout()
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(theme.SPACE_XS + 2)
        for chk in (self.chk_ctrl, self.chk_alt, self.chk_shift):
            chk.setProperty("variant", "chip")
            chk.setCursor(Qt.CursorShape.PointingHandCursor)
            chips.addWidget(chk)
        chips.addStretch(1)

        self.combo_preset_keys = QComboBox()
        self.combo_preset_keys.addItems(self.COMMON_KEYS)
        self.combo_preset_keys.setToolTip("The main key of the shortcut.")
        self.btn_insert = QPushButton("Insert")
        style_button(self.btn_insert, "secondary", height=None)
        self.btn_insert.setToolTip("Replace Keys with the selected modifiers and key.")
        self.btn_insert.clicked.connect(self._apply_combo_helper)
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.setSpacing(theme.SPACE_SM)
        preset_row.addWidget(self.combo_preset_keys, 1)
        preset_row.addWidget(self.btn_insert)

        builder = QVBoxLayout()
        builder.setContentsMargins(0, 0, 0, 0)
        builder.setSpacing(theme.SPACE_SM)
        builder.addLayout(chips)
        builder.addLayout(preset_row)
        keys.add_row(_field("Build a shortcut", builder))
        layout.addWidget(keys)

        # Behaviour
        behaviour = _Section("Behavior")
        self.combo_action = _hidden_combo(behaviour, ["press", "down", "up"])
        self.combo_action.setToolTip(
            "press: tap the key or shortcut once.\n"
            "down: press all keys in order and keep holding them.\n"
            "up: release all keys in reverse order."
        )
        self.combo_action.currentIndexChanged.connect(self._on_changed)
        self.combo_action.currentIndexChanged.connect(self._sync_duration_enabled)
        self.seg_action = SegmentedControl([
            ("press", "Press", "Tap the key or shortcut once."),
            ("down", "Hold down", "Press the keys in order and keep holding them (until a Release step)."),
            ("up", "Release", "Release the keys in reverse order."),
        ])
        self.seg_action.bind_combo(self.combo_action)
        behaviour.add_row(_field("Mode", self.seg_action))

        self.spin_duration = _spin(0, 10000, step=10, value=50, suffix=" ms")
        self.spin_duration.setToolTip("How long the last key of the shortcut is held (press mode only).")
        self.spin_duration.valueChanged.connect(self._on_changed)
        self.lbl_duration = _field_label("Press duration", self.spin_duration)
        self.lbl_duration_help = _help_label("How long the last key stays down. Used by Press only.")
        behaviour.add_row(_field(self.lbl_duration, _pair(self.spin_duration), self.lbl_duration_help))
        layout.addWidget(behaviour)
        layout.addStretch()
        self._update_preview()

    def _apply_combo_helper(self) -> None:
        mods: List[str] = []
        if self.chk_ctrl.isChecked():
            mods.append("cmd_or_ctrl")
        if self.chk_alt.isChecked():
            mods.append("alt")
        if self.chk_shift.isChecked():
            mods.append("shift")

        k = self.combo_preset_keys.currentText()
        mods.append(k)
        combo_str = "+".join(mods)
        self.edit_keys.setText(combo_str)

    def set_values(self, params: Any) -> None:
        self._block_signals = True
        act_str = str(params.get("action", "press")).lower()
        idx = self.combo_action.findText(act_str)
        if idx >= 0:
            self.combo_action.setCurrentIndex(idx)

        # A saved ``modifiers`` field is shown in front of the keys as one combo. The
        # engine runs modifiers + keys as one chord, so saving the combo back as keys
        # keeps the shortcut instead of silently dropping the modifiers.
        chord = build_key_chord(params.get("modifiers"), params.get("keys", ["enter"]))
        self.edit_keys.setText("+".join(str(k) for k in chord))

        self.spin_duration.setValue(int(params.get("press_duration_ms", 50)))
        self._block_signals = False
        self._update_preview()
        self._sync_duration_enabled()

    def get_parsed_keys(self) -> List[str]:
        """Return parsed list of normalized keys from the current input text."""
        raw_text = self.edit_keys.text().strip()
        return parse_key_combo(raw_text)

    def is_valid(self) -> bool:
        """Return True if the keys input parses to a non-empty list."""
        return len(self.get_parsed_keys()) > 0

    def get_values(self) -> Optional[KeyInputParams]:
        parsed_keys = self.get_parsed_keys()
        if not parsed_keys:
            return None
        return KeyInputParams(
            keys=parsed_keys,
            action=self.combo_action.currentText(),
            press_duration_ms=self.spin_duration.value(),
        )

    def _update_preview(self) -> None:
        keys = self.get_parsed_keys()
        if keys:
            self.lbl_human.setText(format_key_combo(keys))
            self.lbl_human.show()
        else:
            self.lbl_human.setText("")
            self.lbl_human.hide()

    def _sync_duration_enabled(self, *_args: object) -> None:
        enabled = self.combo_action.currentText() == "press"
        for widget in (self.spin_duration, self.lbl_duration, self.lbl_duration_help):
            widget.setEnabled(enabled)

    def _on_changed(self) -> None:
        self._update_preview()
        if not self._block_signals:
            self.modified.emit()


# -----------------------------------------------------------------------------
# Fixed wait
# -----------------------------------------------------------------------------


class FixedWaitForm(_FormPage):
    """Parameter editor sub-form for FIXED_WAIT action."""

    modified = Signal()

    PRESETS = [(100, "0.1 s"), (500, "0.5 s"), (1000, "1 s"), (2000, "2 s"), (5000, "5 s")]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._block_signals = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_SECTION_GAP)

        duration = _Section("Duration")
        self.spin_duration = _spin(0, 3600000, step=100, suffix=" ms")
        self.spin_duration.setToolTip("How long to wait, in milliseconds.")
        self.spin_duration.valueChanged.connect(self._on_spin_changed)

        self.lbl_human = QLabel(step_text.format_wait_seconds(1000))
        self.lbl_human.setProperty("role", "muted")
        self.lbl_human.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        duration.add_row(_field("Wait for", _pair(self.spin_duration, self.lbl_human)))

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10000)
        self.slider.setSingleStep(100)
        self.slider.setPageStep(1000)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.setToolTip("Drag to set up to 10 s; type a value for longer waits.")
        self.slider.valueChanged.connect(self._on_slider_changed)
        duration.add_row(self.slider, gap=theme.SPACE_SM)

        presets = QHBoxLayout()
        presets.setContentsMargins(0, 0, 0, 0)
        presets.setSpacing(theme.SPACE_XS + 2)
        self.preset_buttons: List[QPushButton] = []
        for ms, label in self.PRESETS:
            btn = QPushButton(label)
            btn.setProperty("variant", "chip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumWidth(32)  # without the style sheet a push button asks for 80 px
            btn.setToolTip(f"Wait {ms} ms")
            btn.clicked.connect(lambda checked=False, val=ms: self.spin_duration.setValue(val))
            presets.addWidget(btn)
            self.preset_buttons.append(btn)
        presets.addStretch(1)
        duration.add_row(_field("Quick picks", presets))
        self.lbl_duration_help = _help_label("The slider covers up to 10 s; type a larger value for longer waits.")
        duration.add_row(self.lbl_duration_help, gap=theme.SPACE_SM)
        layout.addWidget(duration)
        layout.addStretch()

    def _on_spin_changed(self, val: int) -> None:
        self.lbl_human.setText(step_text.format_wait_seconds(val))  # same wording as the step card
        if self.slider.value() != min(val, 10000):
            self.slider.blockSignals(True)
            self.slider.setValue(min(val, 10000))
            self.slider.blockSignals(False)
        if not self._block_signals:
            self.modified.emit()

    def _on_slider_changed(self, val: int) -> None:
        self.spin_duration.setValue(val)

    def set_values(self, params: Any) -> None:
        self._block_signals = True
        dur = int(params.get("duration_ms", 1000))
        self.spin_duration.setValue(dur)
        self._block_signals = False
        self._on_spin_changed(dur)

    def get_values(self) -> FixedWaitParams:
        return FixedWaitParams(duration_ms=self.spin_duration.value())


# -----------------------------------------------------------------------------
# Image wait
# -----------------------------------------------------------------------------


def _image_file_from(mime: QMimeData) -> Optional[str]:
    """Returns the first local image file of a drag payload, if any."""
    if not mime.hasUrls():
        return None
    for url in mime.urls():
        if url.isLocalFile() and url.toLocalFile().lower().endswith(IMAGE_SUFFIXES):
            return url.toLocalFile()
    return None


class _PathLabel(ElidedLabel):
    """One line showing a file path; when it is too long the *folder* is elided in the
    middle and the file name stays whole (``/Volumes/…/templates/claim_reward.png``).
    ``text()`` keeps the full path."""

    def elided_text(self, width: int) -> str:
        metrics = self.fontMetrics()
        text = self.text()
        if metrics.horizontalAdvance(text) <= width:
            return text
        cut = max(text.rfind("/"), text.rfind("\\"))
        folder, tail = text[:cut], text[cut:]
        room = width - metrics.horizontalAdvance(tail)
        if cut <= 0 or room < metrics.horizontalAdvance("…") * 3:
            return metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, width)
        return metrics.elidedText(folder, Qt.TextElideMode.ElideMiddle, room) + tail


class TemplateCard(QFrame):
    """Preview card of the template image; also accepts an image file dropped on it."""

    file_dropped = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TemplateCard")
        self.setAcceptDrops(True)

    def _set_drop_active(self, active: bool) -> None:
        theme.set_style_prop(self, "dropActive", "true" if active else "false")

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt override
        if _image_file_from(event.mimeData()):
            event.acceptProposedAction()
            self._set_drop_active(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802 - Qt override
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt override
        self._set_drop_active(False)
        path = _image_file_from(event.mimeData())
        if not path:
            event.ignore()
            return
        event.acceptProposedAction()
        self.file_dropped.emit(path)


class ImageWaitForm(_FormPage):
    """Parameter editor sub-form for IMAGE_WAIT action."""

    modified = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._block_signals = False
        self._base_dir: Optional[Path] = None
        self._preview_state = "empty"
        self._setup_ui()

    # -- UI --------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_SECTION_GAP)

        # Template image: preview card + path field
        template = _Section("Template image")
        template.add_row(self._build_card())

        self.edit_path = QLineEdit()
        self.edit_path.setPlaceholderText("templates/button.png")
        self.edit_path.setToolTip("Image file to look for on the screen.")
        self.edit_path.textChanged.connect(self._on_path_changed)
        self.lbl_path_help = _help_label("A relative path starts at the scenario file's folder.")
        template.add_row(_field("File", self.edit_path, self.lbl_path_help))
        layout.addWidget(template)

        # Matching
        matching = _Section("Matching")
        self.combo_mode = _hidden_combo(matching, ["appear", "disappear"])
        self.combo_mode.currentIndexChanged.connect(self._on_changed)
        self.seg_mode = SegmentedControl([
            ("appear", "Appears", "Continue as soon as the image is on the screen."),
            ("disappear", "Disappears", "Continue as soon as the image is gone from the screen."),
        ])
        self.seg_mode.bind_combo(self.combo_mode)
        matching.add_row(_field("Wait until the image", self.seg_mode))

        self.spin_conf = PercentSpinBox()
        _polish_spin(self.spin_conf)
        self.spin_conf.setValue(0.80)
        self.spin_conf.setFixedWidth(84)
        self.spin_conf.valueChanged.connect(self._on_conf_spin_changed)
        self.spin_conf.valueChanged.connect(self._on_changed)
        self.slider_conf = QSlider(Qt.Orientation.Horizontal)
        self.slider_conf.setRange(0, 100)
        self.slider_conf.setPageStep(5)
        self.slider_conf.setValue(80)
        self.slider_conf.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider_conf.setToolTip("Required match score")
        self.slider_conf.valueChanged.connect(self._on_conf_slider_changed)
        conf_row = QHBoxLayout()
        conf_row.setContentsMargins(0, 0, 0, 0)
        conf_row.setSpacing(_ROW_GAP)
        conf_row.addWidget(self.slider_conf, 1)
        conf_row.addWidget(self.spin_conf)
        self.lbl_conf_help = _help_label(CONFIDENCE_HELP)
        matching.add_row(_field("Confidence", conf_row, self.lbl_conf_help))

        self.spin_timeout = QDoubleSpinBox()
        self.spin_timeout.setRange(0.0, 3600.0)
        self.spin_timeout.setSingleStep(0.5)
        self.spin_timeout.setDecimals(1)
        self.spin_timeout.setSuffix(" s")
        self.spin_timeout.setValue(10.0)
        _polish_spin(self.spin_timeout)
        self.spin_timeout.setToolTip("How long to keep looking before the step times out.")
        self.spin_timeout.valueChanged.connect(self._on_changed)
        matching.add_row(_field("Timeout", _pair(self.spin_timeout)))

        self.combo_timeout_action = _hidden_combo(matching, ["raise", "skip"])
        self.combo_timeout_action.currentIndexChanged.connect(self._on_changed)
        self.seg_timeout = SegmentedControl([
            ("raise", "Stop with error", "Stop the run and log an error."),
            ("skip", "Skip and continue", "Log a warning and go on with the next step."),
        ])
        self.seg_timeout.bind_combo(self.combo_timeout_action)
        matching.add_row(_field("If it times out", self.seg_timeout))
        layout.addWidget(matching)

        # Region of interest
        roi = _Section("Search area")
        self.chk_roi = QCheckBox("Only search inside a region")
        self.chk_roi.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chk_roi.toggled.connect(self._toggle_roi)
        roi.add_row(self.chk_roi)

        self.spin_rx = _spin(COORD_MIN, COORD_MAX, mono=True)
        self.spin_rx.setToolTip(ROI_ORIGIN_TOOLTIP)
        self.spin_ry = _spin(COORD_MIN, COORD_MAX, mono=True)
        self.spin_ry.setToolTip(ROI_ORIGIN_TOOLTIP)
        self.spin_rw = _spin(1, 99999, value=100, mono=True)
        self.spin_rw.setToolTip("Width of the search region.")
        self.spin_rh = _spin(1, 99999, value=100, mono=True)
        self.spin_rh.setToolTip("Height of the search region.")
        self._roi_labels: List[QLabel] = []
        fields = []
        for text, spin in (("X", self.spin_rx), ("Y", self.spin_ry),
                           ("Width", self.spin_rw), ("Height", self.spin_rh)):
            spin.valueChanged.connect(self._on_changed)
            label = _field_label(text, spin)
            self._roi_labels.append(label)
            fields.append(_field(label, spin))
        roi.add_row(_pair(fields[0], fields[1]))
        roi.add_row(_pair(fields[2], fields[3]))
        self.lbl_roi_help = _help_label(ROI_HELP)
        roi.add_row(self.lbl_roi_help, gap=theme.SPACE_SM)
        layout.addWidget(roi)
        layout.addStretch()
        self._toggle_roi(False)
        self._refresh_preview()

    def _build_card(self) -> TemplateCard:
        self.card = TemplateCard()
        self.card.file_dropped.connect(self._set_path)  # edit_path is created after the card
        row = QHBoxLayout(self.card)
        row.setContentsMargins(theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD)
        row.setSpacing(theme.SPACE_MD)

        self.thumb = QFrame()
        self.thumb.setObjectName("TemplateThumb")
        self.thumb.setFixedSize(THUMB_SIZE)
        thumb_layout = QVBoxLayout(self.thumb)
        thumb_layout.setContentsMargins(2, 2, 2, 2)
        self.lbl_preview = QLabel()  # holds the template pixmap when the file was found
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb_icon = QLabel()  # state icon when there is no preview
        self.lbl_thumb_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_layout.addWidget(self.lbl_preview)
        thumb_layout.addWidget(self.lbl_thumb_icon)
        row.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(2)
        self.lbl_template_name = ElidedLabel(elide_mode=Qt.TextElideMode.ElideMiddle)
        self.lbl_template_name.setObjectName("TemplateTitle")
        self.lbl_template_meta = QLabel()
        self.lbl_template_meta.setObjectName("TemplateMeta")
        self.lbl_template_meta.setWordWrap(True)
        # The file looked for (missing / unreadable states): one line, elided in the middle
        # so the file name stays visible; the full path is in the tooltip.
        self.lbl_missing_path = _PathLabel(elide_mode=Qt.TextElideMode.ElideMiddle)
        self.lbl_missing_path.setObjectName("TemplateMeta")
        info.addWidget(self.lbl_template_name)
        info.addWidget(self.lbl_template_meta)
        info.addWidget(self.lbl_missing_path)
        info.addSpacing(theme.SPACE_SM - 2)
        self.btn_browse = QPushButton("Browse…")
        style_button(self.btn_browse, "secondary", "folder-open", icon_px=14, height=theme.CONTROL_HEIGHT_SM)
        self.btn_browse.setToolTip("Choose the image file to wait for.")
        self.btn_browse.clicked.connect(self._browse_image)
        browse_row = QHBoxLayout()
        browse_row.setContentsMargins(0, 0, 0, 0)
        browse_row.addWidget(self.btn_browse)
        browse_row.addStretch(1)
        info.addLayout(browse_row)
        info.addStretch(1)
        row.addLayout(info, 1)
        return self.card

    def _set_path(self, path: str) -> None:
        self.edit_path.setText(path)

    # -- base folder and preview -----------------------------------------------

    def set_base_dir(self, base_dir: Optional[Union[str, Path]]) -> None:
        """Sets the folder relative template paths start from (None: the working folder)."""
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None
        self._refresh_preview()

    def base_dir(self) -> Optional[Path]:
        """Returns the folder relative template paths start from (None: the working folder)."""
        return self._base_dir

    def resolved_template_path(self) -> Optional[Path]:
        """Returns the file the engine will open for the current path (None when empty).

        Mirrors ``WaitSystem.wait_image`` (see :func:`step_text.template_file`): with a
        scenario folder the path goes through ``resolve_template_path``; without one
        it is relative to the current working directory.
        """
        return step_text.template_file(self.edit_path.text(), self._base_dir)

    def preview_state(self) -> str:
        """Returns ``"empty"``, ``"found"``, ``"missing"``, ``"invalid"`` (unreadable image) or
        ``"placeholder"`` (a new step's default path whose file does not exist yet)."""
        return self._preview_state

    def _refresh_preview(self) -> None:
        path = self.resolved_template_path()
        pixmap = QPixmap()
        state = step_text.template_state(self.edit_path.text(), self._base_dir)
        if state == "found" and path is not None:
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                state = "invalid"
        self._preview_state = state

        warning = theme.COLORS.warning
        if state == "found":
            dpr = max(2.0, self.devicePixelRatioF())  # sharp on Retina even before the form is shown
            box = QSize(THUMB_SIZE.width() - 6, THUMB_SIZE.height() - 6)
            shown = pixmap
            if pixmap.width() > box.width() or pixmap.height() > box.height():
                shown = pixmap.scaled(box * dpr, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
                shown.setDevicePixelRatio(dpr)
            self.lbl_preview.setPixmap(shown)
            self.lbl_preview.show()
            self.lbl_thumb_icon.hide()
            self.lbl_template_name.setText(Path(self.edit_path.text().strip()).name)
            self.lbl_template_meta.setText(f"{pixmap.width()} × {pixmap.height()} px")
            self.lbl_missing_path.clear()
            self.card.setToolTip(str(path))
        else:
            self.lbl_preview.clear()
            self.lbl_preview.hide()
            icon_name, color = {
                "empty": ("image", theme.COLORS.text_muted),
                "placeholder": ("image", theme.COLORS.text_muted),
                "missing": ("file-x", warning),
                "invalid": ("alert-triangle", warning),
            }[state]
            self.lbl_thumb_icon.setPixmap(icons.pixmap(icon_name, color, 22))
            self.lbl_thumb_icon.show()
            self.card.setToolTip("")
            if state in ("empty", "placeholder"):
                # A new step's untouched default path counts as "no image chosen yet":
                # the friendly drop zone, not an error with a long absolute path.
                self.lbl_template_name.setText("No template image")
                self.lbl_template_meta.setText("Drop an image here or browse for one.")
                self.lbl_missing_path.clear()
            else:
                self.lbl_template_name.setText("Image not found" if state == "missing" else "Can't read this image")
                self.lbl_template_meta.setText("Looked for:" if state == "missing" else "File:")
                self.lbl_missing_path.setText(str(path))
        self.lbl_missing_path.setToolTip(f"Looked for: {path}" if state in ("missing", "invalid") else "")
        self.lbl_missing_path.setVisible(state in ("missing", "invalid"))
        theme.set_style_prop(self.card, "state", state, recursive=True)

    # -- events ------------------------------------------------------------------

    def _toggle_roi(self, checked: bool) -> None:
        for spin in (self.spin_rx, self.spin_ry, self.spin_rw, self.spin_rh):
            spin.setEnabled(checked)
        for label in self._roi_labels:
            label.setEnabled(checked)
        self._on_changed()

    def _on_conf_spin_changed(self, value: float) -> None:
        percent = int(round(value * 100))
        if self.slider_conf.value() != percent:
            self.slider_conf.blockSignals(True)
            self.slider_conf.setValue(percent)
            self.slider_conf.blockSignals(False)

    def _on_conf_slider_changed(self, percent: int) -> None:
        self.spin_conf.setValue(percent / 100.0)

    def _browse_image(self) -> None:
        start = ""
        resolved = self.resolved_template_path()
        if resolved is not None and resolved.parent.is_dir():
            start = str(resolved.parent)
        elif self._base_dir is not None:
            start = str(self._base_dir)
        patterns = " ".join(f"*{suffix}" for suffix in IMAGE_SUFFIXES)
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Template Image", start, f"Images ({patterns})"
        )
        if file_path:
            self.edit_path.setText(file_path)

    def _on_path_changed(self, _text: str) -> None:
        self._refresh_preview()
        self._on_changed()

    def set_values(self, params: Any) -> None:
        self._block_signals = True
        tmpl = str(params.get("template_path", ""))
        self.edit_path.setText(tmpl)

        mode_str = str(params.get("mode", "appear")).lower()
        idx_m = self.combo_mode.findText(mode_str)
        if idx_m >= 0:
            self.combo_mode.setCurrentIndex(idx_m)

        self.spin_conf.setValue(float(params.get("confidence", 0.8)))
        self.spin_timeout.setValue(float(params.get("timeout_seconds", 10.0)))

        to_act_str = str(params.get("timeout_action", "raise")).lower()
        idx_t = self.combo_timeout_action.findText(to_act_str)
        if idx_t >= 0:
            self.combo_timeout_action.setCurrentIndex(idx_t)

        roi = params.get("roi")
        if roi is not None:
            self.chk_roi.setChecked(True)
            rx, ry, rw, rh = roi
            self.spin_rx.setValue(rx)
            self.spin_ry.setValue(ry)
            self.spin_rw.setValue(rw)
            self.spin_rh.setValue(rh)
        else:
            # Show the form defaults, not the region of the previously shown step.
            self.chk_roi.setChecked(False)
            self._toggle_roi(False)
            self.spin_rx.setValue(0)
            self.spin_ry.setValue(0)
            self.spin_rw.setValue(100)
            self.spin_rh.setValue(100)

        self._block_signals = False
        self._refresh_preview()

    def get_values(self) -> Optional[ImageWaitParams]:
        path = self.edit_path.text().strip()
        if not path:
            return None

        roi = None
        if self.chk_roi.isChecked():
            roi = (self.spin_rx.value(), self.spin_ry.value(), self.spin_rw.value(), self.spin_rh.value())

        return ImageWaitParams(
            template_path=path,
            mode=self.combo_mode.currentText(),
            confidence=float(self.spin_conf.value()),
            timeout_seconds=float(self.spin_timeout.value()),
            roi=roi,
            timeout_action=self.combo_timeout_action.currentText(),
        )

    def _on_changed(self) -> None:
        if not self._block_signals:
            self.modified.emit()


# -----------------------------------------------------------------------------
# Inspector
# -----------------------------------------------------------------------------


def _action_type(action: ActionBlock) -> ActionType:
    return action.type if isinstance(action.type, ActionType) else ActionType.from_str(str(action.type))


class PropertyInspector(QWidget):
    """Dynamic Property Inspector displaying and editing selected ActionBlock."""

    action_modified = Signal(object)      # ActionBlock
    block_updated = Signal(object)        # Alias for MainWindow integration
    pick_coordinate_requested = Signal()
    coordinate_pick_requested = Signal()  # Alias
    validation_state_changed = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.current_action: Optional[ActionBlock] = None
        self._block_updates = False
        self._step_index: Optional[int] = None

        self._setup_ui()
        self.set_action(None)

    def _setup_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.content = QWidget()
        self.content.setObjectName("InspectorContent")
        self.content.setMaximumWidth(CONTENT_MAX_WIDTH)
        outer.addWidget(self.content, 1)
        outer.addStretch(0)

        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG, theme.SPACE_LG)
        layout.setSpacing(0)
        self._layout = layout

        # Header + common fields (hidden together while no step is selected)
        self.common_frame = QFrame()
        cbox = QVBoxLayout(self.common_frame)
        cbox.setContentsMargins(0, 0, 0, _SECTION_GAP - theme.SPACE_XS)
        cbox.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(theme.SPACE_MD)
        self.lbl_type_icon = QLabel()
        self.lbl_type_icon.setFixedSize(36, 36)
        header.addWidget(self.lbl_type_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        self.lbl_step = SectionLabel("Step")
        self.lbl_type = QLabel()
        self.lbl_type.setObjectName("InspectorTypeLabel")
        self.lbl_header = self.lbl_type  # former "Action Properties" title label
        titles.addWidget(self.lbl_step)
        titles.addWidget(self.lbl_type)
        header.addLayout(titles, 1)
        cbox.addLayout(header)
        cbox.addSpacing(theme.SPACE_LG)

        self.edit_description = QLineEdit()
        self.edit_description.setPlaceholderText("Describe this step (optional)")
        self.edit_description.setToolTip("A note for yourself; it is shown on the step card.")
        self.edit_description.setAccessibleName("Description")
        self.edit_description.textChanged.connect(self._on_description_changed)
        cbox.addWidget(self.edit_description)
        cbox.addSpacing(theme.SPACE_MD)

        # The same switch as on the step card (one control for one flag). Each common
        # field writes only its own attribute, so a stale view never overwrites the model.
        self.chk_enabled = ToggleSwitch("Enabled")
        self.chk_enabled.setToolTip("Disabled steps are skipped when the scenario runs.")
        self.chk_enabled.toggled.connect(self._on_enabled_toggled)
        cbox.addWidget(self.chk_enabled)
        cbox.addSpacing(theme.SPACE_LG + theme.SPACE_XS)
        cbox.addWidget(Separator(vertical=False))

        layout.addWidget(self.common_frame)

        # Stacked Parameter Forms
        self.stacked_widget = QStackedWidget()

        # Page 0: Empty state
        self.page_empty = EmptyState(
            "mouse-pointer-click",
            "No step selected",
            "Select a step in the list to see and edit its settings here.",
            compact=True,
        )
        self.stacked_widget.addWidget(self.page_empty)

        # Page 1: Mouse
        self.form_mouse = MouseClickForm()
        self.form_mouse.modified.connect(self._commit_changes)
        self.form_mouse.pick_requested.connect(self._on_pick_requested)
        self.stacked_widget.addWidget(self.form_mouse)

        # Page 2: Key
        self.form_key = KeyInputForm()
        self.form_key.modified.connect(self._commit_changes)
        self.stacked_widget.addWidget(self.form_key)

        # Page 3: Fixed Wait
        self.form_wait = FixedWaitForm()
        self.form_wait.modified.connect(self._commit_changes)
        self.stacked_widget.addWidget(self.form_wait)

        # Page 4: Image Wait
        self.form_image = ImageWaitForm()
        self.form_image.modified.connect(self._commit_changes)
        self.stacked_widget.addWidget(self.form_image)

        layout.addWidget(self.stacked_widget, stretch=1)

        # Validation status: an inline row under the form (the ✓ / ⚠ prefix is the
        # machine-readable state, the colours come from the "state" property).
        self.status_row = QWidget()
        status_layout = QVBoxLayout(self.status_row)
        status_layout.setContentsMargins(0, _SECTION_GAP, 0, 0)
        status_layout.setSpacing(0)
        self.lbl_status = QLabel("✓ Valid")
        self.lbl_status.setObjectName("ValidationStatus")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setProperty("state", "valid")
        status_layout.addWidget(self.lbl_status)
        layout.addWidget(self.status_row)
        layout.addStretch(0)  # takes the spare height below a form (see _fit_stack_to_current_page)

        # Size the stack to the visible page only (QStackedWidget otherwise reserves
        # the largest form, which forced a scrollbar even on the empty page).
        self.stacked_widget.currentChanged.connect(self._fit_stack_to_current_page)
        self._fit_stack_to_current_page()

    def _fit_stack_to_current_page(self, _index: int = -1) -> None:
        current = self.stacked_widget.currentWidget()
        for index in range(self.stacked_widget.count()):
            page = self.stacked_widget.widget(index)
            policy = QSizePolicy.Policy.Preferred if page is current else QSizePolicy.Policy.Ignored
            page.setSizePolicy(policy, policy)
        # The empty state is centred in the panel; a form stays at the top with its
        # validation line right below it, and the spare height goes underneath.
        empty = current is self.page_empty
        self._layout.setStretch(self._layout.indexOf(self.stacked_widget), 1 if empty else 0)
        self._layout.setStretch(self._layout.count() - 1, 0 if empty else 1)
        self.stacked_widget.updateGeometry()

    # -- header ------------------------------------------------------------------

    def set_step_index(self, index: Optional[int]) -> None:
        """Shows the 1-based position of the step in the header ("STEP 2"); None hides the number."""
        self._step_index = index if index is None else int(index)
        self.lbl_step.setText(f"STEP {self._step_index}" if self._step_index is not None else "STEP")

    def step_index(self) -> Optional[int]:
        """Returns the 1-based step position shown in the header (None when unknown)."""
        return self._step_index

    def _update_header(self, act_type: ActionType) -> None:
        meta = theme.STEP_TYPES.get(act_type)
        if meta is None:
            self.lbl_type.setText(str(act_type))
            self.lbl_type_icon.clear()
            return
        self.lbl_type.setText(meta.label)
        self.lbl_type.setToolTip(meta.description)
        self.lbl_type_icon.setPixmap(empty_state_badge(meta.icon, meta.tint, 36))
        self.lbl_type_icon.setToolTip(meta.description)

    # -- base folder -------------------------------------------------------------

    def set_base_dir(self, base_dir: Optional[Union[str, Path]]) -> None:
        """Sets the folder that relative template paths start from.

        MainWindow passes the scenario file's folder (open / save / save as) and
        None for a new, unsaved scenario; None falls back to the current working
        directory. This is the same folder the execution engine resolves against,
        so the preview and a run agree on which file is used.
        """
        self.form_image.set_base_dir(base_dir)
        if self.current_action is not None and not self._block_updates:
            self._validate_and_update_status()

    def base_dir(self) -> Optional[Path]:
        """Returns the folder relative template paths start from (None: the working folder)."""
        return self.form_image.base_dir()

    # -- selection ---------------------------------------------------------------

    def _on_pick_requested(self) -> None:
        self.pick_coordinate_requested.emit()
        self.coordinate_pick_requested.emit()

    def set_action(self, action: Optional[ActionBlock], index: Optional[int] = None) -> None:
        """Loads ActionBlock into inspector forms; ``index`` is its 1-based position."""
        self._block_updates = True
        self.current_action = action
        self.set_step_index(index)

        if action is None:
            self.common_frame.hide()
            self.stacked_widget.setCurrentWidget(self.page_empty)
            self.lbl_status.hide()
            self.status_row.hide()
            self._block_updates = False
            return

        self.common_frame.show()
        self.status_row.show()
        self.lbl_status.show()
        self.edit_description.setText(action.description)
        self.chk_enabled.setChecked(action.enabled)

        act_type = _action_type(action)
        self._update_header(act_type)

        if act_type == ActionType.MOUSE_CLICK:
            self.stacked_widget.setCurrentWidget(self.form_mouse)
            self.form_mouse.set_values(action.params)
        elif act_type == ActionType.KEY_INPUT:
            self.stacked_widget.setCurrentWidget(self.form_key)
            self.form_key.set_values(action.params)
        elif act_type == ActionType.FIXED_WAIT:
            self.stacked_widget.setCurrentWidget(self.form_wait)
            self.form_wait.set_values(action.params)
        elif act_type == ActionType.IMAGE_WAIT:
            self.stacked_widget.setCurrentWidget(self.form_image)
            self.form_image.set_values(action.params)

        self._block_updates = False
        self._validate_and_update_status()

    def inspect_block(self, action: Optional[ActionBlock]) -> None:
        """Alias for set_action."""
        self.set_action(action)

    def set_coordinates(self, x: int, y: int) -> None:
        """Injects coordinates from CoordinatePickerOverlay into MOUSE_CLICK form."""
        if self.current_action and _action_type(self.current_action) == ActionType.MOUSE_CLICK:
            self.form_mouse.set_coordinates(x, y)
            self._commit_changes()

    def refresh_common_fields(self, action: Optional[ActionBlock]) -> None:
        """Shows ``action``'s description and enabled flag again if it is the step on display.

        Called when the step was changed elsewhere (the card's switch), so the
        inspector never shows, and later writes back, a stale value.
        """
        if action is None or action is not self.current_action:
            return
        self._block_updates = True
        try:
            if self.edit_description.text().strip() != (action.description or "").strip():
                self.edit_description.setText(action.description)
            self.chk_enabled.setChecked(bool(action.enabled))
        finally:
            self._block_updates = False

    def _on_description_changed(self, _text: str = "") -> None:
        if self._block_updates or not self.current_action:
            return
        self.current_action.description = self.edit_description.text().strip()
        self._emit_modified()

    def _on_enabled_toggled(self, checked: bool) -> None:
        if self._block_updates or not self.current_action:
            return
        self.current_action.enabled = bool(checked)
        self._emit_modified()

    def _on_common_changed(self) -> None:
        """Former combined slot (kept for callers): writes the description and the enabled flag."""
        if self._block_updates or not self.current_action:
            return
        self.current_action.description = self.edit_description.text().strip()
        self.current_action.enabled = self.chk_enabled.isChecked()
        self._emit_modified()

    def _emit_modified(self) -> None:
        self.action_modified.emit(self.current_action)
        self.block_updated.emit(self.current_action)

    def _commit_changes(self) -> None:
        if self._block_updates or not self.current_action:
            return

        valid = self._validate_and_update_status()
        if not valid:
            return

        act_type = _action_type(self.current_action)

        new_params = None
        if act_type == ActionType.MOUSE_CLICK:
            new_params = self.form_mouse.get_values()
        elif act_type == ActionType.KEY_INPUT:
            new_params = self.form_key.get_values()
        elif act_type == ActionType.FIXED_WAIT:
            new_params = self.form_wait.get_values()
        elif act_type == ActionType.IMAGE_WAIT:
            new_params = self.form_image.get_values()

        if new_params is not None:
            self.current_action.params = new_params
            self.action_modified.emit(self.current_action)
            self.block_updated.emit(self.current_action)

    def _validate_and_update_status(self) -> bool:
        if not self.current_action:
            return True

        valid = True
        err_msg = ""
        warning = ""
        invalid_field: Optional[QWidget] = None
        act_type = _action_type(self.current_action)

        if act_type == ActionType.IMAGE_WAIT:
            path = self.form_image.edit_path.text().strip()
            if not path:
                valid = False
                err_msg = "Template image path cannot be empty"
                invalid_field = self.form_image.edit_path
            else:
                # Saved as typed (the file may be added later), but a run would fail now.
                warning = TEMPLATE_WARNINGS.get(self.form_image.preview_state(), "")
        elif act_type == ActionType.KEY_INPUT:
            keys = self.form_key.edit_keys.text().strip()
            parsed_keys = parse_key_combo(keys)
            if not parsed_keys:
                valid = False
                err_msg = "Invalid key combination (e.g. ctrl+c or enter)"
                invalid_field = self.form_key.edit_keys

        for field in (self.form_image.edit_path, self.form_key.edit_keys):
            theme.set_style_prop(field, "state", "invalid" if field is invalid_field else "")

        if valid and warning:
            self.lbl_status.setText(warning)
            theme.set_style_prop(self.lbl_status, "state", "warning")
        elif valid:
            self.lbl_status.setText("✓ Parameters valid")
            theme.set_style_prop(self.lbl_status, "state", "valid")
        else:
            self.lbl_status.setText(f"⚠ {err_msg}")
            theme.set_style_prop(self.lbl_status, "state", "invalid")

        self.validation_state_changed.emit(valid)
        return valid
