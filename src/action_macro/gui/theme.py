"""
action_macro.gui.theme
=========================
Design tokens and the application style sheet (dark theme, violet accent).

Every colour, radius, spacing step and font size used by the GUI lives here so
widgets never carry ad-hoc colour literals. Widgets opt into a look through a
small set of *style roles*: an ``objectName`` (unique chrome such as ``TopBar``)
or a dynamic property that the style sheet matches:

==================  ===============================================================
Property            Values
==================  ===============================================================
``variant``         QPushButton: ``primary`` | ``secondary`` (default) | ``ghost`` |
                    ``danger`` | ``icon``; QPushButton / QCheckBox: ``chip`` (small
                    toggle or quick-pick button); QToolButton: ``rail``;
                    QProgressBar: ``thin``
``controlSize``     QPushButton (set by ``components.style_button``): ``sm`` (28 px)
                    | ``md`` (32 px); QFrame#SegmentedControl: ``sm`` (28 px)
``role``            QLabel: ``section`` | ``muted`` | ``faint`` (disabled-looking
                    text only, below 4.5:1) | ``caption`` | ``title`` | ``headline``
                    | ``kbd`` | ``brand`` | ``value`` | ``mono`` | ``count`` (count
                    badge) | ``field`` / ``help`` / ``chord`` (inspector field label,
                    help line, formatted shortcut); QFrame: ``panel`` | ``card`` |
                    ``dropzone``
``state``           status pill / save chip / validation label: ``idle`` | ``running``
                    | ``paused`` | ``stopping`` | ``stopped`` | ``error`` | ``dirty``
                    | ``saved`` | ``valid`` | ``warning`` | ``invalid``; QLineEdit:
                    ``invalid``; QLabel#StepStatusPill: ``running`` | ``paused`` |
                    ``missing``; QFrame#TemplateCard: ``empty`` | ``placeholder`` |
                    ``found`` | ``missing`` | ``invalid``; QLabel#PermissionPill:
                    ``granted`` | ``missing`` | ``unknown`` | ``not_required``
``dropActive``      QFrame#TemplateCard: ``true`` while an image file is dragged over it
``tone``            QLabel: ``success`` | ``warning`` | ``danger`` | ``info`` | ``accent``
==================  ===============================================================

After changing one of these properties at runtime call :func:`repolish` so Qt
re-evaluates the selectors.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Dict, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QFontMetrics, QPalette
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleHintReturn, QStyleOption, QWidget

from action_macro.core.models import ActionType

# -----------------------------------------------------------------------------
# Colour tokens
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ColorTokens:
    """Colour design tokens of the dark theme (see the design brief)."""

    bg: str = "#0E1014"
    surface: str = "#15171C"
    surface_2: str = "#1B1E25"
    surface_3: str = "#232731"
    border: str = "#2A2F3A"
    border_strong: str = "#3A4150"
    text: str = "#E7E9EE"
    text_muted: str = "#9BA3AF"
    text_faint: str = "#6B7280"
    #: Placeholder text of inputs: dimmer than text-muted, still 4.5:1 on surface-2.
    text_placeholder: str = "#848C99"
    accent: str = "#8B5CF6"
    accent_hover: str = "#7C3AED"
    accent_pressed: str = "#6D28D9"
    accent_soft: str = "rgba(139, 92, 246, 0.16)"
    accent_text: str = "#C4B5FD"
    on_accent: str = "#FFFFFF"
    success: str = "#22C55E"
    warning: str = "#F59E0B"
    danger: str = "#EF4444"
    danger_hover: str = "#DC2626"
    #: Danger-coloured *text* (errors on tinted or card backgrounds): 4.5:1 or more.
    danger_text: str = "#F87171"
    info: str = "#38BDF8"


COLORS = ColorTokens()

#: Tint of the icon circle of each step type (never whole cards).
STEP_TINTS: Dict[ActionType, str] = {
    ActionType.MOUSE_CLICK: "#60A5FA",
    ActionType.KEY_INPUT: "#A78BFA",
    ActionType.FIXED_WAIT: "#FBBF24",
    ActionType.IMAGE_WAIT: "#34D399",
}


@dataclass(frozen=True)
class StepTypeMeta:
    """Display metadata of one step type: label, Lucide icon, tint, help line."""

    label: str
    icon: str
    tint: str
    description: str


STEP_TYPES: Dict[ActionType, StepTypeMeta] = {
    ActionType.MOUSE_CLICK: StepTypeMeta(
        "Mouse click", "mouse-pointer-click", STEP_TINTS[ActionType.MOUSE_CLICK],
        "Click a point on the screen.",
    ),
    ActionType.KEY_INPUT: StepTypeMeta(
        "Key input", "keyboard", STEP_TINTS[ActionType.KEY_INPUT],
        "Press a key or shortcut (Ctrl+C).",
    ),
    ActionType.FIXED_WAIT: StepTypeMeta(
        "Wait", "timer", STEP_TINTS[ActionType.FIXED_WAIT],
        "Pause for a fixed amount of time.",
    ),
    ActionType.IMAGE_WAIT: StepTypeMeta(
        "Image wait", "image", STEP_TINTS[ActionType.IMAGE_WAIT],
        "Wait until an image appears or disappears on screen.",
    ),
}

#: Engine state (lower-case ``EngineState`` value) -> indicator colour.
STATE_COLORS: Dict[str, str] = {
    "idle": COLORS.text_muted,
    "running": COLORS.success,
    "paused": COLORS.warning,
    "stopping": COLORS.warning,
    "stopped": COLORS.text_muted,
    "error": COLORS.danger,
}

# -----------------------------------------------------------------------------
# Metrics
# -----------------------------------------------------------------------------

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24

RADIUS_SM = 6   # inputs, small buttons
RADIUS_MD = 8   # cards, buttons
RADIUS_LG = 10  # panels, popovers

FONT_SMALL = 11
FONT_BODY = 12
FONT_SUBTITLE = 13
FONT_TITLE = 14
FONT_HEADER = 16
FONT_HEADLINE = 18

CONTROL_HEIGHT = 32
CONTROL_HEIGHT_SM = 28
TOP_BAR_HEIGHT = 44
STATUS_BAR_HEIGHT = 28
RAIL_WIDTH = 48
SIDEBAR_WIDTH = 240
INSPECTOR_WIDTH = 340
CONSOLE_HEIGHT = 180

#: Right padding of the Run button that leaves room for its painted "F5" hint.
RUN_BUTTON_HINT_PAD = 40

# -----------------------------------------------------------------------------
# Colour helpers
# -----------------------------------------------------------------------------


def alpha(color: str, opacity: float) -> str:
    """Returns ``color`` (``#RRGGBB``) as a QSS ``rgba()`` string with ``opacity``."""
    qc = QColor(color)
    return f"rgba({qc.red()}, {qc.green()}, {qc.blue()}, {max(0.0, min(1.0, opacity)):.2f})"


def qcolor(color: str, opacity: Optional[float] = None) -> QColor:
    """Returns a QColor for a ``#RRGGBB`` token, optionally with ``opacity`` (0..1)."""
    qc = QColor(color)
    if opacity is not None:
        qc.setAlphaF(max(0.0, min(1.0, opacity)))
    return qc


def state_color(state: str) -> str:
    """Returns the indicator colour of an engine state name (case-insensitive)."""
    return STATE_COLORS.get(str(state).lower(), COLORS.text_muted)


# -----------------------------------------------------------------------------
# Fonts
# -----------------------------------------------------------------------------

_MONO_FALLBACKS: Tuple[str, ...] = (
    "SF Mono", "Menlo", "Monaco",            # macOS
    "Cascadia Mono", "Consolas",             # Windows
    "JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono",
    "Courier New",
)


def ui_font(pixel_size: int = FONT_BODY, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """Returns the platform UI font (never a hard-coded family) at ``pixel_size``."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    return font


def mono_font(pixel_size: int = FONT_BODY) -> QFont:
    """Returns the platform fixed-pitch font, falling back to a known monospace family.

    Some platform plugins (for example ``offscreen``) report a generic family that
    resolves to a proportional font, so the result is verified with ``QFontInfo``.
    """
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPixelSize(pixel_size)
    if QFontInfo(font).fixedPitch():
        return font
    families = set(QFontDatabase.families())
    for family in _MONO_FALLBACKS:
        if family in families:
            candidate = QFont(family)
            candidate.setPixelSize(pixel_size)
            return candidate
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


def count_badge_min_width() -> int:
    """Minimum width of a count badge (``role="count"``): two digits plus its padding, so the
    badge does not change width between 0 and 99 (proportional digits differ by a pixel or two)."""
    return QFontMetrics(ui_font(FONT_SMALL, QFont.Weight.DemiBold)).horizontalAdvance("00") + 2 * 6


def section_font() -> QFont:
    """Returns the small-caps style font of section labels (11 px, semibold, tracked)."""
    font = ui_font(FONT_SMALL, QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
    return font


# -----------------------------------------------------------------------------
# Style helpers
# -----------------------------------------------------------------------------


def repolish(widget: QWidget, recursive: bool = False) -> None:
    """Re-applies the style sheet after a style-role property changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    if recursive:
        for child in widget.findChildren(QWidget):
            style.unpolish(child)
            style.polish(child)
    widget.update()


def set_style_prop(widget: QWidget, name: str, value: object, recursive: bool = False) -> None:
    """Sets a style-role property (``variant``/``role``/``state``/``tone``) and repolishes."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    repolish(widget, recursive=recursive)


def build_palette() -> QPalette:
    """Returns a dark QPalette matching the tokens (for widgets the QSS does not cover)."""
    c = COLORS
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: c.bg,
        QPalette.ColorRole.WindowText: c.text,
        QPalette.ColorRole.Base: c.surface_2,
        QPalette.ColorRole.AlternateBase: c.surface,
        QPalette.ColorRole.ToolTipBase: c.surface_3,
        QPalette.ColorRole.ToolTipText: c.text,
        QPalette.ColorRole.PlaceholderText: c.text_placeholder,
        QPalette.ColorRole.Text: c.text,
        QPalette.ColorRole.Button: c.surface_2,
        QPalette.ColorRole.ButtonText: c.text,
        QPalette.ColorRole.BrightText: c.on_accent,
        QPalette.ColorRole.Highlight: c.accent,
        QPalette.ColorRole.HighlightedText: c.on_accent,
        QPalette.ColorRole.Link: c.accent_text,
        QPalette.ColorRole.Light: c.surface_3,
        QPalette.ColorRole.Midlight: c.border_strong,
        QPalette.ColorRole.Mid: c.border,
        QPalette.ColorRole.Dark: c.bg,
        QPalette.ColorRole.Shadow: "#000000",
    }
    for role, value in roles.items():
        pal.setColor(QPalette.ColorGroup.All, role, QColor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(c.text_faint))
    return pal


# -----------------------------------------------------------------------------
# Style sheet
# -----------------------------------------------------------------------------


_ICON_DIR: Optional[Path] = None


def _user_tag() -> str:
    """Returns a per-user suffix for shared temp directories (uid, or the login name)."""
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return str(getuid())
    name = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    return "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "user"


def _is_private_dir(path: Path) -> bool:
    """True when ``path`` is a writable directory owned by the current user."""
    try:
        if not path.is_dir() or path.is_symlink() or not os.access(path, os.W_OK | os.X_OK):
            return False
        getuid = getattr(os, "getuid", None)
        return getuid is None or path.stat().st_uid == getuid()
    except OSError:
        return False


def _icon_dir() -> Path:
    """Returns this user's cache directory for the QSS icon files.

    The shared temp directory may be used by several users (``/tmp`` on Linux), so
    the folder name carries the user id and is only reused when this user owns it.
    Otherwise (taken by someone else, not writable) a private ``mkdtemp`` folder is
    used for this process, so building the style sheet never fails.
    """
    global _ICON_DIR
    if _ICON_DIR is not None and _is_private_dir(_ICON_DIR):
        return _ICON_DIR
    candidate = Path(tempfile.gettempdir()) / f"actionmacro-theme-icons-{_user_tag()}"
    try:
        candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        pass
    if not _is_private_dir(candidate):
        candidate = Path(tempfile.mkdtemp(prefix="actionmacro-theme-icons-"))
    _ICON_DIR = candidate
    return candidate


def _write_icon_file(target: Path, markup: str) -> None:
    """Writes ``markup`` atomically (temp file + rename) so readers never see half a file."""
    temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temp.write_text(markup, encoding="utf-8")
    os.replace(temp, target)


def qss_icon_url(name: str, color: str) -> str:
    """Writes a Lucide icon as an SVG file and returns it as a QSS ``url()`` path.

    QSS sub-control images (spin box arrows, combo arrow, check marks) must be
    files, so the coloured SVG is cached in a per-user temp directory under a
    content hash (see :func:`_icon_dir`).
    """
    global _ICON_DIR
    from action_macro.gui import icons  # local import: icons depends on this module

    markup = icons.svg_markup(name, color)
    digest = hashlib.sha1(markup.encode("utf-8")).hexdigest()[:12]
    file_name = f"{name}-{digest}.svg"
    target = _icon_dir() / file_name
    if target.is_file():
        return target.as_posix()
    try:
        _write_icon_file(target, markup)
    except OSError:
        # The cached folder went away or became unwritable: fall back to a private one.
        _ICON_DIR = Path(tempfile.mkdtemp(prefix="actionmacro-theme-icons-"))
        target = _ICON_DIR / file_name
        _write_icon_file(target, markup)
    return target.as_posix()


_QSS = Template(r"""
/* ---------- Base ---------- */
QWidget {
    color: $text;
    selection-background-color: $accent;
    selection-color: $on_accent;
    outline: 0;
}
QMainWindow, QDialog, QMessageBox { background-color: $bg; }
QLabel { background: transparent; }
QToolTip {
    background-color: $surface_3;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 4px 8px;
}

/* ---------- Menus ---------- */
QMenuBar {
    background-color: $bg;
    color: $text_muted;
    border: none;
    border-bottom: 1px solid $border;
    padding: 2px 6px;
}
QMenuBar::item { background: transparent; padding: 4px 10px; border-radius: 6px; }
QMenuBar::item:selected, QMenuBar::item:pressed { background-color: $surface_3; color: $text; }
QMenu {
    background-color: $surface_2;
    border: 1px solid $border_strong;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item { padding: 6px 28px 6px 12px; border-radius: 6px; color: $text; background: transparent; }
QMenu::item:selected { background-color: $accent_soft; }
QMenu::item:disabled { color: $text_faint; }
QMenu::icon { padding-left: 10px; }
QMenu::separator { height: 1px; background: $border; margin: 6px 6px; }
QMenu::indicator { width: 14px; height: 14px; padding-left: 6px; }
QMenu::indicator:checked { image: url("$icon_check_accent"); }

/* ---------- App chrome ---------- */
QFrame#TopBar { background-color: $bg; border: none; border-bottom: 1px solid $border; }
QMenuBar#InlineMenuBar { background: transparent; border: none; padding: 0px; color: $text_muted; }
QMenuBar#InlineMenuBar::item { background: transparent; padding: 5px 9px; border-radius: 6px; }
QMenuBar#InlineMenuBar::item:selected, QMenuBar#InlineMenuBar::item:pressed {
    background-color: $surface_3;
    color: $text;
}
QFrame#NavRail { background-color: $bg; border: none; border-right: 1px solid $border; }
QFrame#Sidebar { background-color: $surface; border: none; }
QFrame#EditorPanel { background-color: $bg; border: none; }
QFrame#EditorHeader { background-color: $bg; border: none; border-bottom: 1px solid $border; }
QFrame#InspectorPanel, QFrame#ConsolePanel { background-color: $surface; border: none; }
QFrame[role="panel"] { background-color: $surface; border: none; }
QFrame[role="card"] { background-color: $surface_2; border: 1px solid $border; border-radius: 8px; }
QFrame[role="dropzone"], QLabel[role="dropzone"] {
    background-color: $surface_2;
    border: 1px dashed $border_strong;
    border-radius: 8px;
    color: $text_muted;
}
QFrame#VSeparator { background-color: $border; border: none; min-width: 1px; max-width: 1px; }
QFrame#HSeparator { background-color: $border; border: none; min-height: 1px; max-height: 1px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }
QWidget#InspectorScrollContent { background: transparent; }

QSplitter { background-color: $bg; }
QSplitter::handle { background-color: $border; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QSplitter::handle:hover, QSplitter::handle:pressed { background-color: $accent; }

QStatusBar {
    background-color: $bg;
    border-top: 1px solid $border;
    color: $text_muted;
    min-height: ${status_height}px;
}
QStatusBar::item { border: none; }

/* ---------- Labels ---------- */
QLabel[role="section"] { color: $text_muted; }
QLabel[role="muted"] { color: $text_muted; }
QLabel[role="faint"] { color: $text_faint; }
QLabel[role="caption"] { color: $text_muted; font-size: ${font_small}px; }
QLabel[role="title"] { font-size: ${font_title}px; font-weight: 600; }
QLabel[role="headline"] { font-size: ${font_headline}px; font-weight: 600; }
QLabel[role="brand"] { font-size: ${font_subtitle}px; font-weight: 600; color: $text; }
QLabel[role="value"] { font-size: ${font_subtitle}px; font-weight: 600; color: $text; }
QLabel[role="kbd"] {
    background-color: $surface_3;
    border: 1px solid $border_strong;
    border-radius: 4px;
    padding: 0px 5px;
    color: $text_muted;
    font-size: ${font_small}px;
}
QLabel[tone="success"] { color: $success; }
QLabel[tone="warning"] { color: $warning; }
QLabel[tone="danger"] { color: $danger; }
QLabel[tone="info"] { color: $info; }
QLabel[tone="accent"] { color: $accent_text; }
QLabel[state="valid"] { color: $success; font-weight: 600; }
QLabel[state="invalid"] { color: $danger_text; font-weight: 600; }

/* ---------- Buttons ---------- */
QPushButton {
    background-color: $surface_2;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 5px 12px;
    min-height: 20px;
}
/* Control heights (components.style_button sets "controlSize"): content + 2 x (padding 5 + border 1),
   so the focus state (padding 4 + border 2) keeps the same height. */
QPushButton[controlSize="sm"] { min-height: 16px; max-height: 16px; }
QPushButton[controlSize="md"] { min-height: 20px; max-height: 20px; }
QPushButton:hover { background-color: $surface_3; border-color: $border_strong; }
QPushButton:pressed { background-color: $surface; }
QPushButton:focus { border: 2px solid $accent; padding: 4px 11px; }
QPushButton:checked { background-color: $accent_soft; border-color: $accent; color: $accent_text; }
QPushButton:disabled { background-color: $surface; border-color: $border; color: $text_faint; }

QPushButton[variant="primary"] {
    background-color: $accent;
    border: 1px solid $accent;
    color: $on_accent;
    font-weight: 600;
}
QPushButton[variant="primary"]:hover { background-color: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background-color: $accent_pressed; border-color: $accent_pressed; }
QPushButton[variant="primary"]:focus { border: 2px solid $accent_text; padding: 4px 11px; }
QPushButton[variant="primary"]:disabled {
    background-color: $accent_disabled;
    border-color: transparent;
    color: $on_accent_disabled;
}

QPushButton[variant="danger"] { background-color: $danger; border: 1px solid $danger; color: $on_accent; font-weight: 600; }
QPushButton[variant="danger"]:hover { background-color: $danger_hover; border-color: $danger_hover; }
QPushButton[variant="danger"]:pressed { background-color: $danger_hover; }
QPushButton[variant="danger"]:focus { border: 2px solid $text; padding: 4px 11px; }

QPushButton[variant="ghost"] { background: transparent; border: 1px solid transparent; color: $text_muted; }
QPushButton[variant="ghost"]:hover { background-color: $surface_3; color: $text; }
QPushButton[variant="ghost"]:pressed { background-color: $surface_2; }
QPushButton[variant="ghost"]:disabled { background: transparent; color: $text_faint; }
QPushButton[variant="ghost"][tone="warning"] { color: $warning; }
QPushButton[variant="ghost"]:focus { border: 2px solid $accent; padding: 4px 11px; }

QPushButton[variant="icon"] { padding: 0px; min-height: 0px; }
QPushButton[variant="icon"]:focus { padding: 0px; }

QPushButton#RunButton { padding: 5px ${run_hint_pad}px 5px 14px; }
QPushButton#RunButton:focus { padding: 4px ${run_hint_pad_focus}px 4px 13px; }

QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px;
    color: $text_muted;
}
QToolButton:hover { background-color: $surface_3; color: $text; }
QToolButton:pressed { background-color: $surface_2; }
QToolButton:checked { background-color: $accent_soft; color: $accent_text; }
QToolButton:focus { border: 2px solid $accent; padding: 3px; }
QToolButton:disabled { color: $text_faint; background: transparent; }
QToolButton::menu-indicator { image: none; width: 0px; }
QToolButton[tone="danger"] { color: $danger; }
QToolButton[tone="danger"]:hover { background-color: $danger_soft; color: $danger; }

QToolButton[variant="rail"] {
    border: none;
    border-left: 2px solid transparent;
    border-radius: 0px;
    padding: 0px;
}
QToolButton[variant="rail"]:hover { background-color: $surface_2; }
QToolButton[variant="rail"]:checked { border-left: 2px solid $accent; background-color: $accent_soft; }
QToolButton[variant="rail"]:focus { border-left: 2px solid $accent_text; }

/* ---------- Inputs ---------- */
QLineEdit, QAbstractSpinBox, QComboBox {
    background-color: $surface_2;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 5px 8px;
    min-height: 20px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QLineEdit:hover, QAbstractSpinBox:hover, QComboBox:hover { border-color: $border_strong; }
QLineEdit:focus, QComboBox:focus { border: 2px solid $accent; padding: 4px 7px; }
QLineEdit:disabled, QAbstractSpinBox:disabled, QComboBox:disabled {
    background-color: $surface;
    color: $text_faint;
    border-color: $border;
}
QLineEdit[readOnly="true"] { background-color: $surface; color: $text_muted; }

QAbstractSpinBox { padding-right: 22px; }
QAbstractSpinBox:focus { border: 2px solid $accent; padding: 4px 21px 4px 7px; }
/* Qt sizes a styled spin box as min-height + 2 x border + 5 px (its padding does not count),
   so with the min-height above it is 27 px, grown to the platform's size hint: 30 px with the
   real macOS and Windows plugins next to 32 px line edits, 32 px offscreen. controlSize="md"
   (set with the fixed height in code) pins it to CONTROL_HEIGHT, the 2 px focus border too. */
QAbstractSpinBox[controlSize="md"] { min-height: ${spin_md_height}px; max-height: ${spin_md_height}px; }
QAbstractSpinBox[controlSize="md"]:focus { min-height: ${spin_md_height_focus}px; max-height: ${spin_md_height_focus}px; }
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
    subcontrol-origin: border;
    width: 18px;
    border: none;
    background: transparent;
}
QAbstractSpinBox::up-button { subcontrol-position: top right; margin: 3px 3px 0px 0px; border-top-right-radius: 4px; }
QAbstractSpinBox::down-button { subcontrol-position: bottom right; margin: 0px 3px 3px 0px; border-bottom-right-radius: 4px; }
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover { background-color: $surface_3; }
QAbstractSpinBox::up-arrow { image: url("$icon_up"); width: 10px; height: 10px; }
QAbstractSpinBox::down-arrow { image: url("$icon_down"); width: 10px; height: 10px; }
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::up-arrow:off { image: url("$icon_up_faint"); }
QAbstractSpinBox::down-arrow:disabled, QAbstractSpinBox::down-arrow:off { image: url("$icon_down_faint"); }

QComboBox { padding-right: 26px; }
QComboBox:focus { padding-right: 25px; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow { image: url("$icon_down"); width: 12px; height: 12px; }
QComboBox::down-arrow:disabled { image: url("$icon_down_faint"); }
QComboBox QAbstractItemView {
    background-color: $surface_2;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 4px;
    selection-background-color: $accent_soft;
    selection-color: $text;
    outline: 0;
}

QCheckBox, QRadioButton { spacing: 8px; background: transparent; }
QCheckBox:disabled, QRadioButton:disabled { color: $text_faint; }
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $border_strong;
    background-color: $surface_2;
}
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: $accent; }
QCheckBox::indicator:focus, QRadioButton::indicator:focus { border: 2px solid $accent; }
QCheckBox::indicator:checked { background-color: $accent; border-color: $accent; image: url("$icon_check_white"); }
QCheckBox::indicator:checked:focus { border: 2px solid $accent_text; }
QRadioButton::indicator:checked { background-color: $accent; border: 4px solid $surface_2; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled { background-color: $surface; border-color: $border; }
QCheckBox::indicator:checked:disabled { background-color: $accent_disabled; border-color: transparent; }

QGroupBox {
    background-color: transparent;
    border: 1px solid $border;
    border-radius: 8px;
    margin-top: 18px;
    padding-top: 6px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 6px;
    padding: 0px 4px;
    color: $text_muted;
}

QSlider { background: transparent; }
QSlider::groove:horizontal { height: 4px; background: $surface_3; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal {
    background: $text;
    border: 2px solid $accent;
    width: 10px;
    height: 10px;
    margin: -6px 0px;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: $accent_text; }

QProgressBar {
    background-color: $surface_3;
    border: none;
    border-radius: 3px;
    min-height: 6px;
    max-height: 6px;
    color: transparent;
    text-align: center;
}
QProgressBar::chunk { background-color: $accent; border-radius: 3px; }
QProgressBar[variant="thin"] { min-height: 4px; max-height: 4px; border-radius: 2px; }
QProgressBar[variant="thin"]::chunk { border-radius: 2px; }

/* ---------- Text areas and lists ---------- */
QPlainTextEdit, QTextEdit {
    background-color: $surface_2;
    color: $text;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 6px;
    selection-background-color: $accent;
    selection-color: $on_accent;
}
QPlainTextEdit[role="console"] { background-color: $bg; border-color: $border; }
QListView, QListWidget, QTreeView, QTableView { background-color: transparent; border: none; outline: 0; }
QListView::item, QTreeView::item { padding: 6px 8px; border-radius: 6px; }
QListView::item:hover, QTreeView::item:hover { background-color: $surface_3; }
QListView::item:selected, QTreeView::item:selected { background-color: $accent_soft; color: $text; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px 2px 2px 0px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0px 2px 2px 2px; }
QScrollBar::handle:vertical { background-color: $surface_3; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:horizontal { background-color: $surface_3; border-radius: 3px; min-width: 28px; }
QScrollBar::handle:hover { background-color: $border_strong; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; border: none; background: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ---------- Composite widgets ---------- */
QFrame#StatePill { background-color: $pill_idle; border: none; border-radius: 9px; }
QFrame#StatePill[state="running"] { background-color: $pill_running; }
QFrame#StatePill[state="paused"], QFrame#StatePill[state="stopping"] { background-color: $pill_warning; }
QFrame#StatePill[state="error"] { background-color: $pill_error; }
QLabel#StatePillText { color: $text_muted; font-size: ${font_small}px; font-weight: 700; }
QFrame#StatePill[state="running"] QLabel#StatePillText { color: $success; }
QFrame#StatePill[state="paused"] QLabel#StatePillText,
QFrame#StatePill[state="stopping"] QLabel#StatePillText { color: $warning; }
QFrame#StatePill[state="error"] QLabel#StatePillText { color: $danger; }

QLabel[role="count"] {
    background-color: $surface_3;
    color: $text_muted;
    border-radius: 9px;
    padding: 0px 6px;
    min-height: 18px;
    max-height: 18px;
    font-size: ${font_small}px;
    font-weight: 600;
}

QLabel#SaveStateLabel { color: $text_muted; font-size: ${font_small}px; }
QLabel#SaveStateLabel[state="dirty"] { color: $warning; }

QFrame#RepeatGroup { background-color: $surface_2; border: 1px solid $border; border-radius: 8px; }
QFrame#RepeatGroup:hover { border-color: $border_strong; }
QFrame#RepeatGroup QAbstractSpinBox { background: transparent; border: none; border-radius: 0px; padding: 0px 22px 0px 2px; min-width: 44px; }
QFrame#RepeatGroup QAbstractSpinBox:focus { border: none; padding: 0px 22px 0px 2px; }
QFrame#RepeatGroup QAbstractSpinBox:disabled { color: $text_faint; }

QLineEdit#ScenarioNameEdit {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 2px 6px;
}
QLineEdit#ScenarioNameEdit:hover { border-color: $border; }
QLineEdit#ScenarioNameEdit:focus { border: 2px solid $accent; padding: 1px 5px; background-color: $surface_2; }

QLineEdit#SidebarFilter { background-color: $surface_2; }

QWidget#EmptyState { background: transparent; }
QLabel#EmptyStateTitle { color: $text; font-weight: 600; }
QLabel#EmptyStateText { color: $text_muted; }

/* ---- phase2:cards ---- */
QLabel#StepStatusPill {
    background-color: $pill_running;
    color: $success;
    border-radius: 9px;
    padding: 0px 8px;
    min-height: 18px;
    max-height: 18px;
}
QLabel#StepStatusPill[state="paused"], QLabel#StepStatusPill[state="missing"] {
    background-color: $pill_warning;
    color: $warning;
}
QToolButton#StepReorderButton { padding: 0px; border-radius: 4px; }
QToolButton#StepReorderButton:disabled { background: transparent; }
QPushButton#AddStepRow {
    background: transparent;
    border: 1px dashed $border_strong;
    border-radius: 8px;
    color: $text_muted;
    padding: 0px 12px;
    min-height: 38px;
    max-height: 38px;
}
QPushButton#AddStepRow:hover { background-color: $surface_2; border-color: $accent; color: $text; }
QPushButton#AddStepRow:pressed { background-color: $surface; }
QPushButton#AddStepRow:disabled { background: transparent; border-color: $border; color: $text_faint; }
QPushButton#AddStepRow:focus { border: 2px dashed $accent; padding: 0px 11px; min-height: 36px; max-height: 36px; }
QPushButton#StepTypeTile {
    background-color: $surface_2;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 0px;
    text-align: left;
    min-height: 70px;
    max-height: 70px;
}
QPushButton#StepTypeTile:hover { background-color: $surface_3; border-color: $border_strong; }
QPushButton#StepTypeTile:pressed { background-color: $surface; }
QPushButton#StepTypeTile:focus { border: 2px solid $accent; padding: 0px; min-height: 68px; max-height: 68px; }
/* ---- end phase2:cards ---- */

/* ---- phase2:inspector ---- */
QWidget#InspectorContent { background: transparent; }
QWidget#InspectorContent QAbstractSpinBox { padding-top: 3px; padding-bottom: 2px; }
QWidget#InspectorContent QAbstractSpinBox:focus { padding-top: 2px; padding-bottom: 1px; }
QLabel#InspectorTypeLabel { color: $text; font-size: ${font_title}px; font-weight: 600; }
QLabel[role="field"] { color: $text; font-size: ${font_body}px; font-weight: 500; }
QLabel[role="field"]:disabled { color: $text_faint; }
QLabel[role="help"] { color: $text_muted; font-size: ${font_small}px; }
QLabel[role="help"]:disabled { color: $text_faint; }
QLabel[role="help"][tone="warning"] { color: $warning; }
QLabel[role="help"][tone="danger"] { color: $danger; }
QLabel[role="chord"] {
    background-color: $surface_3;
    border: 1px solid $border_strong;
    border-radius: 4px;
    padding: 0px 6px;
    color: $text;
    font-size: ${font_small}px;
    font-weight: 600;
}

QFrame#SegmentedControl { background-color: $bg; border: 1px solid $border; border-radius: 8px; }
QFrame#SegmentedControl:hover { border-color: $border_strong; }
/* Segments (32 px; 28 px with controlSize="sm"): the checked one uses the same violet tint as
   checked chips, so "selected" reads the same across the app. */
QFrame#SegmentedControl QPushButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 2px 8px;
    min-height: 20px;
    max-height: 20px;
    color: $text_muted;
}
QFrame#SegmentedControl[controlSize="sm"] QPushButton {
    min-height: 16px;
    max-height: 16px;
    font-size: ${font_small}px;
    font-weight: 600;
}
QFrame#SegmentedControl QPushButton:hover { background-color: $surface_2; color: $text; }
QFrame#SegmentedControl QPushButton:checked {
    background-color: $accent_soft;
    border-color: $accent;
    color: $accent_text;
    font-weight: 600;
}
QFrame#SegmentedControl QPushButton:focus { border: 2px solid $accent; padding: 1px 7px; }
QFrame#SegmentedControl QPushButton:checked:focus { border-color: $accent_text; }
QFrame#SegmentedControl QPushButton:disabled { color: $text_faint; }

QCheckBox[variant="chip"], QPushButton[variant="chip"] {
    background-color: $surface_2;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 3px 10px;
    min-height: 20px;
    max-height: 20px;
    spacing: 0px;
    color: $text_muted;
}
QCheckBox[variant="chip"]:hover, QPushButton[variant="chip"]:hover {
    background-color: $surface_3;
    border-color: $border_strong;
    color: $text;
}
QCheckBox[variant="chip"]:checked { background-color: $accent_soft; border-color: $accent; color: $accent_text; }
QCheckBox[variant="chip"]:focus, QPushButton[variant="chip"]:focus { border: 2px solid $accent; padding: 2px 9px; }
QCheckBox[variant="chip"]:checked:focus { border-color: $accent_text; }
QCheckBox[variant="chip"]::indicator, QCheckBox[variant="chip"]::indicator:checked {
    width: 0px;
    height: 0px;
    border: none;
    background: transparent;
    image: none;
}

QFrame#TemplateCard { background-color: $surface_2; border: 1px solid $border; border-radius: 8px; }
QFrame#TemplateCard[state="empty"], QFrame#TemplateCard[state="placeholder"] {
    background-color: transparent;
    border: 1px dashed $border_strong;
}
/* A template that cannot be used is a warning (amber), like the card pill and the status line:
   the step is saved as typed but would fail when it runs. */
QFrame#TemplateCard[state="missing"], QFrame#TemplateCard[state="invalid"] { border-color: $warning; }
QFrame#TemplateCard[dropActive="true"] { background-color: $accent_soft; border: 1px dashed $accent; }
QFrame#TemplateThumb { background-color: $bg; border: 1px solid $border; border-radius: 6px; }
QFrame#TemplateCard[state="missing"] QFrame#TemplateThumb,
QFrame#TemplateCard[state="invalid"] QFrame#TemplateThumb { background-color: $pill_warning; border-color: transparent; }
QLabel#TemplateTitle { color: $text; font-weight: 600; }
QFrame#TemplateCard[state="missing"] QLabel#TemplateTitle,
QFrame#TemplateCard[state="invalid"] QLabel#TemplateTitle { color: $warning; }
QLabel#TemplateMeta { color: $text_muted; font-size: ${font_small}px; }

QLabel#ValidationStatus { border-radius: 6px; font-weight: 500; }
QLabel#ValidationStatus[state="valid"] { color: $success; background: transparent; padding: 2px 0px; }
QLabel#ValidationStatus[state="warning"] { color: $warning; background-color: $pill_warning; padding: 6px 8px; }
QLabel#ValidationStatus[state="invalid"] { color: $danger_text; background-color: $danger_soft; padding: 6px 8px; }
QLineEdit[state="invalid"] { border-color: $danger; }
QLineEdit[state="invalid"]:focus { border: 2px solid $danger; padding: 4px 7px; }
/* ---- end phase2:inspector ---- */

/* ---- phase2:console ---- */
QWidget#ConsoleHeader { background: transparent; }
QToolButton#AutoScrollButton:checked { background: transparent; }
QToolButton#AutoScrollButton:checked:hover { background-color: $surface_3; }
QPlainTextEdit#ConsoleView { background-color: $bg; border: 1px solid $border; border-radius: 8px; padding: 4px 6px; }
QLabel#ConsoleEmpty { color: $text_muted; }
/* ---- end phase2:console ---- */

/* ---- phase2:perms ---- */
QPushButton#PermissionsButton { background-color: $pill_warning; border: 1px solid transparent; color: $warning; }
QPushButton#PermissionsButton:hover { background-color: $pill_warning; border-color: $warning; color: $warning; }
QPushButton#PermissionsButton:pressed { background-color: $pill_warning; border-color: $warning; }
QPushButton#PermissionsButton:focus { border: 2px solid $accent; padding: 4px 11px; }
QDialog#PermissionsPanel { background-color: $surface; }
QLabel#PermissionsIntro { color: $text_muted; }
QFrame#PermissionList { background-color: $surface_2; border: 1px solid $border; border-radius: 10px; }
QFrame#PermissionRow { background: transparent; border: none; }
QLabel#PermissionName { color: $text; }
QLabel#PermissionPurpose { color: $text_muted; }
QLabel#PermissionPill {
    background-color: $pill_idle;
    color: $text_muted;
    border: none;
    border-radius: 9px;
    padding: 0px 8px;
    min-height: 18px;
    max-height: 18px;
    font-size: ${font_small}px;
    font-weight: 600;
}
QLabel#PermissionPill[state="granted"] { background-color: $pill_running; color: $success; }
QLabel#PermissionPill[state="missing"] { background-color: $pill_warning; color: $warning; }
QLabel#PermissionsNote { color: $text_muted; }
QLabel#PermissionsCheckedLabel { color: $text_muted; font-size: ${font_small}px; }
/* ---- end phase2:perms ---- */
""")


def build_stylesheet() -> str:
    """Builds the complete application style sheet from the design tokens."""
    c = COLORS
    values = {name: getattr(c, name) for name in c.__dataclass_fields__}
    values.update(
        accent_disabled=alpha(c.accent, 0.30),
        on_accent_disabled=alpha(c.on_accent, 0.55),
        pill_idle=alpha(c.text_muted, 0.14),
        pill_running=alpha(c.success, 0.15),
        pill_warning=alpha(c.warning, 0.15),
        pill_error=alpha(c.danger, 0.16),
        danger_soft=alpha(c.danger, 0.14),
        font_small=FONT_SMALL,
        font_body=FONT_BODY,
        font_subtitle=FONT_SUBTITLE,
        font_title=FONT_TITLE,
        font_header=FONT_HEADER,
        font_headline=FONT_HEADLINE,
        status_height=STATUS_BAR_HEIGHT,
        spin_md_height=CONTROL_HEIGHT - 2 * 1 - 5,
        spin_md_height_focus=CONTROL_HEIGHT - 2 * 2 - 5,
        run_hint_pad=RUN_BUTTON_HINT_PAD,
        run_hint_pad_focus=RUN_BUTTON_HINT_PAD - 1,
        icon_up=qss_icon_url("chevron-up", c.text_muted),
        icon_down=qss_icon_url("chevron-down", c.text_muted),
        icon_up_faint=qss_icon_url("chevron-up", c.border_strong),
        icon_down_faint=qss_icon_url("chevron-down", c.border_strong),
        icon_check_white=qss_icon_url("check", c.on_accent),
        icon_check_accent=qss_icon_url("check", c.accent_text),
    )
    return _QSS.substitute(values)


class AppStyle(QProxyStyle):
    """Fusion with two desktop-app behaviours the style sheet cannot express.

    * Buttons, check boxes and radio buttons take focus from the keyboard (Tab)
      only, like native macOS controls. A mouse click no longer leaves the 2 px
      focus ring behind, while keyboard users still see it.
    * Mnemonic underlines (``&File``) are not drawn; Alt shortcuts keep working.
    """

    def __init__(self) -> None:
        super().__init__("Fusion")

    def styleHint(  # noqa: N802 - Qt override
        self,
        hint: QStyle.StyleHint,
        option: Optional[QStyleOption] = None,
        widget: Optional[QWidget] = None,
        return_data: Optional[QStyleHintReturn] = None,
    ) -> int:
        if hint == QStyle.StyleHint.SH_Button_FocusPolicy:
            return int(Qt.FocusPolicy.TabFocus.value)
        if hint == QStyle.StyleHint.SH_UnderlineShortcut:
            return 0
        return super().styleHint(hint, option, widget, return_data)


_APP_STYLE: Optional[AppStyle] = None


def app_style() -> AppStyle:
    """Returns the single :class:`AppStyle` instance (kept alive for the application)."""
    global _APP_STYLE
    if _APP_STYLE is None:
        _APP_STYLE = AppStyle()
    return _APP_STYLE


def apply_theme(app: QApplication) -> None:
    """Applies the app style (Fusion based), the dark palette, the UI font and the style sheet."""
    app.setStyle(app_style())
    app.setPalette(build_palette())
    app.setFont(ui_font(FONT_BODY))
    app.setStyleSheet(build_stylesheet())
