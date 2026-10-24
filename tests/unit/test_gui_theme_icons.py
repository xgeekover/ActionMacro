"""Unit tests for the design system: theme tokens, style sheet and Lucide icons."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFontInfo, QIcon, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QStyle

from action_macro.core.models import ActionType
from action_macro.gui import icons, theme
from action_macro.gui.components import (
    ElidedLabel,
    EmptyState,
    InlineEdit,
    KeyHintButton,
    StatusDot,
    shortcut_text,
    with_shortcut,
)


# -----------------------------------------------------------------------------
# Icons
# -----------------------------------------------------------------------------

REQUIRED_ICONS = [
    "mouse-pointer-click", "keyboard", "timer", "image", "play", "pause", "square", "plus",
    "folder-open", "file-plus", "save", "settings", "terminal", "list", "grip-vertical",
    "more-horizontal", "trash", "copy", "chevron-up", "chevron-down", "crosshair", "search",
    "shield-alert", "check", "x", "info", "alert-triangle", "circle-dot",
]


def _opaque_pixels(image) -> list:
    return [
        image.pixelColor(x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 200
    ]


def test_required_icon_set_is_embedded():
    missing = [name for name in REQUIRED_ICONS if name not in icons.available_icons()]
    assert missing == []
    assert len(icons.available_icons()) >= 30


def test_licence_notice_is_kept_in_icons_module():
    doc = icons.__doc__ or ""
    assert "Lucide" in doc and "ISC License" in doc


@pytest.mark.parametrize("name", icons.available_icons())
def test_every_icon_renders_in_the_requested_colour(qapp, name):
    pixmap = icons.render_pixmap(name, "#FF0000", 24, dpr=2.0)
    assert not pixmap.isNull()
    assert pixmap.devicePixelRatio() == 2.0
    opaque = _opaque_pixels(pixmap.toImage())
    assert opaque, f"icon {name!r} rendered no visible pixels"
    # Strokes are drawn in the requested colour (anti-aliased edges aside).
    assert all(c.red() > 200 and c.green() < 60 and c.blue() < 60 for c in opaque)


def test_icon_returns_cached_qicon_with_state_variants(qapp):
    first = icons.icon("play", "#8B5CF6", 16, checked_color="#FFFFFF")
    second = icons.icon("play", "#8B5CF6", 16, checked_color="#FFFFFF")
    assert first is second
    assert isinstance(first, QIcon) and not first.isNull()
    assert QSize(16, 16) in first.availableSizes()

    normal = first.pixmap(16, 16, QIcon.Mode.Normal, QIcon.State.Off).toImage()
    checked = first.pixmap(16, 16, QIcon.Mode.Normal, QIcon.State.On).toImage()
    disabled = first.pixmap(16, 16, QIcon.Mode.Disabled, QIcon.State.Off).toImage()
    center = (normal.width() // 2, normal.height() // 2)
    assert normal.pixelColor(*center).name().upper() == "#8B5CF6"
    assert checked.pixelColor(*center).name().upper() == "#FFFFFF"
    assert disabled.pixelColor(*center).name().upper() == theme.COLORS.text_faint.upper()


def test_unknown_icon_raises_key_error(qapp):
    with pytest.raises(KeyError):
        icons.svg_markup("does-not-exist")
    with pytest.raises(KeyError):
        icons.icon("does-not-exist")


def test_app_icon_and_mark(qapp):
    app_icon = icons.app_icon()
    assert not app_icon.isNull()
    assert len(app_icon.availableSizes()) >= 4
    mark = icons.app_mark_pixmap(22)
    assert not mark.isNull()
    image = mark.toImage()
    # The mark is violet: the corner region near the gradient start is clearly blue/red-heavy.
    sample = image.pixelColor(image.width() // 5, image.height() // 2)
    assert sample.blue() > sample.green() and sample.red() > sample.green()


# -----------------------------------------------------------------------------
# Theme
# -----------------------------------------------------------------------------

def test_tokens_match_design_brief():
    c = theme.COLORS
    assert (c.bg, c.surface, c.surface_2, c.surface_3, c.border) == (
        "#0E1014", "#15171C", "#1B1E25", "#232731", "#2A2F3A")
    assert (c.text, c.text_muted, c.text_faint) == ("#E7E9EE", "#9BA3AF", "#6B7280")
    assert (c.accent, c.accent_hover, c.accent_pressed) == ("#8B5CF6", "#7C3AED", "#6D28D9")
    assert (c.success, c.warning, c.danger, c.info) == ("#22C55E", "#F59E0B", "#EF4444", "#38BDF8")
    assert theme.STEP_TINTS == {
        ActionType.MOUSE_CLICK: "#60A5FA",
        ActionType.KEY_INPUT: "#A78BFA",
        ActionType.FIXED_WAIT: "#FBBF24",
        ActionType.IMAGE_WAIT: "#34D399",
    }
    assert set(theme.STEP_TYPES) == set(ActionType)
    assert all(meta.icon in icons.available_icons() for meta in theme.STEP_TYPES.values())


def test_alpha_and_state_colour_helpers():
    assert theme.alpha("#8B5CF6", 0.16) == "rgba(139, 92, 246, 0.16)"
    assert theme.alpha("#000000", 2.0).endswith("1.00)")
    assert theme.state_color("RUNNING") == theme.COLORS.success
    assert theme.state_color("paused") == theme.COLORS.warning
    assert theme.state_color("error") == theme.COLORS.danger
    assert theme.state_color("unknown") == theme.COLORS.text_muted
    assert theme.qcolor("#FFFFFF", 0.5).alphaF() == pytest.approx(0.5, abs=0.01)


def test_build_stylesheet_is_fully_resolved_and_uses_tokens(qapp):
    qss = theme.build_stylesheet()
    assert "$" not in qss, "unresolved template placeholder in the style sheet"
    for token in (theme.COLORS.bg, theme.COLORS.accent, theme.COLORS.surface_2, theme.COLORS.danger):
        assert token in qss
    for selector in ('QPushButton[variant="primary"]', 'QPushButton[variant="ghost"]',
                     'QPushButton[variant="danger"]', 'QLabel[role="section"]',
                     "QFrame#StatePill", "QFrame#TopBar", "QFrame#NavRail", "QStatusBar"):
        assert selector in qss
    assert "Arial" not in qss
    # Every sub-control image the style sheet references exists on disk.
    urls = re.findall(r'url\("([^"]+)"\)', qss)
    assert urls and all(Path(u).is_file() for u in urls)


def test_fonts_are_platform_fonts(qapp):
    ui = theme.ui_font(theme.FONT_BODY)
    assert ui.pixelSize() == theme.FONT_BODY
    assert ui.family() != "Arial"
    assert QFontInfo(theme.mono_font()).fixedPitch()


def test_set_style_prop_updates_property(qtbot):
    button = QPushButton("x")
    qtbot.addWidget(button)
    theme.set_style_prop(button, "variant", "primary")
    assert button.property("variant") == "primary"
    theme.set_style_prop(button, "variant", "danger")
    assert button.property("variant") == "danger"


def test_palette_is_dark():
    pal = theme.build_palette()
    assert pal.color(pal.ColorRole.Window) == QColor(theme.COLORS.bg)
    assert pal.color(pal.ColorRole.Highlight) == QColor(theme.COLORS.accent)


# -----------------------------------------------------------------------------
# Components
# -----------------------------------------------------------------------------

def test_status_dot_colour(qtbot):
    dot = StatusDot(theme.COLORS.text_muted, 8)
    qtbot.addWidget(dot)
    dot.set_color(theme.COLORS.success)
    assert dot.color() == QColor(theme.COLORS.success)
    image = dot.grab().toImage()
    assert image.pixelColor(4, 4).name().upper() == theme.COLORS.success.upper()


def test_elided_label_keeps_full_text(qtbot):
    label = ElidedLabel("Action 3: " + "very long step description " * 5)
    qtbot.addWidget(label)
    label.setFixedWidth(80)
    label.show()
    qtbot.waitExposed(label)
    assert label.text().startswith("Action 3: very long")
    label.grab()  # paints the elided text
    assert label.toolTip() == label.text()


def test_key_hint_button_paints_hint(qtbot):
    button = KeyHintButton("Run", hint="F5")
    qtbot.addWidget(button)
    button.resize(120, 32)
    assert button.hint() == "F5" and button.text() == "Run"
    assert not button.grab().isNull()


def test_elided_label_keeps_an_explicit_tooltip_and_does_not_set_it_while_painting(qtbot):
    label = ElidedLabel("daily_quest.json")
    qtbot.addWidget(label)
    label.setToolTip("/Users/me/scenarios/daily_quest.json")
    label.resize(400, 20)
    label.show()
    qtbot.waitExposed(label)
    label.grab()
    assert label.toolTip() == "/Users/me/scenarios/daily_quest.json"  # fits, explicit tooltip wins

    auto = ElidedLabel("Action 3: " + "long text " * 10)
    qtbot.addWidget(auto)
    auto.resize(60, 20)
    auto.show()
    qtbot.waitExposed(auto)
    assert auto.is_elided() and auto.toolTip() == auto.text()
    auto.resize(2000, 20)
    assert not auto.is_elided() and auto.toolTip() == ""
    auto.setText("short")
    assert auto.toolTip() == ""


def test_inline_edit_escape_restores_and_signals(qtbot):
    edit = InlineEdit("Daily quest")
    qtbot.addWidget(edit)
    edit.show()
    qtbot.waitExposed(edit)
    edit.activateWindow()
    edit.setFocus()
    qtbot.waitUntil(edit.hasFocus, timeout=2000)
    edit.selectAll()
    QTest.keyClicks(edit, "Renamed")
    assert edit.text() == "Renamed" and edit.isModified()
    with qtbot.waitSignal(edit.edit_cancelled):
        QTest.keyClick(edit, Qt.Key.Key_Escape)
    assert edit.text() == "Daily quest" and not edit.isModified()


def test_empty_state_block(qtbot):
    state = EmptyState("list", "No steps yet", "Add a step to get started.", action_text="Add step",
                       action_icon="plus")
    qtbot.addWidget(state)
    assert state.title() == "No steps yet" and state.text() == "Add a step to get started."
    assert state.btn_action is not None and state.btn_action.property("variant") == "primary"
    assert not state.lbl_icon.pixmap().isNull()
    assert state.lbl_title.font().pixelSize() == theme.FONT_HEADLINE
    compact = EmptyState("list", "Nothing here", compact=True)
    qtbot.addWidget(compact)
    assert compact.btn_action is None and compact.lbl_text.isHidden()
    assert compact.lbl_title.font().pixelSize() == theme.FONT_TITLE


def test_shortcut_text_uses_native_wording(qapp):
    native = QKeySequence("Ctrl+B").toString(QKeySequence.SequenceFormat.NativeText)
    assert shortcut_text("Ctrl+B") == native
    assert shortcut_text(QKeySequence.StandardKey.Save) == QKeySequence(
        QKeySequence.StandardKey.Save).toString(QKeySequence.SequenceFormat.NativeText)
    assert with_shortcut("Scenarios", "Ctrl+B") == f"Scenarios ({native})"
    assert with_shortcut("Scenarios", None) == "Scenarios"
    assert with_shortcut("Scenarios", QKeySequence()) == "Scenarios"


# -----------------------------------------------------------------------------
# App style, contrast, QSS icon cache
# -----------------------------------------------------------------------------

def test_app_style_keyboard_only_button_focus_and_no_mnemonic_underlines(qapp):
    style = theme.app_style()
    assert style is theme.app_style()  # one instance for the whole application
    assert style.baseStyle().name().lower() == "fusion"
    assert style.styleHint(QStyle.StyleHint.SH_Button_FocusPolicy) == Qt.FocusPolicy.TabFocus.value
    assert style.styleHint(QStyle.StyleHint.SH_UnderlineShortcut) == 0
    # Other hints still come from Fusion.
    fusion = style.baseStyle()
    hint = QStyle.StyleHint.SH_Menu_SubMenuPopupDelay
    assert style.styleHint(hint) == fusion.styleHint(hint)


def _contrast(foreground: str, background: str) -> float:
    def luminance(hex_color: str) -> float:
        channels = []
        for value in QColor(hex_color).getRgbF()[:3]:
            channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
        r, g, b = channels
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_secondary_text_colour_meets_wcag_aa_on_every_surface():
    c = theme.COLORS
    for surface in (c.bg, c.surface, c.surface_2, c.surface_3):
        assert _contrast(c.text_muted, surface) >= 4.5, surface
        assert _contrast(c.text, surface) >= 7.0, surface
    # Why informative text must not use text-faint: it stays below 4.5:1.
    assert _contrast(c.text_faint, c.surface) < 4.5


def test_qss_icon_cache_is_per_user(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(theme.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(theme, "_ICON_DIR", None)
    folder = theme._icon_dir()
    assert folder.parent == tmp_path and folder.name == f"actionmacro-theme-icons-{theme._user_tag()}"
    url = theme.qss_icon_url("check", theme.COLORS.accent)
    assert Path(url).is_file() and Path(url).parent == folder
    assert not list(folder.glob(".*.tmp"))  # atomic write leaves no temp file behind


def test_qss_icon_cache_falls_back_when_the_shared_folder_is_not_ours(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(theme.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(theme, "_ICON_DIR", None)
    taken = tmp_path / f"actionmacro-theme-icons-{theme._user_tag()}"
    taken.mkdir()
    real_check = theme._is_private_dir
    monkeypatch.setattr(theme, "_is_private_dir", lambda path: path != taken and real_check(path))
    url = Path(theme.qss_icon_url("chevron-down", theme.COLORS.text_muted))
    assert url.is_file() and url.parent != taken
    assert "$" not in theme.build_stylesheet()  # building the style sheet still succeeds


def test_qss_icon_cache_recovers_from_a_failed_write(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(theme.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(theme, "_ICON_DIR", None)
    real_write = theme._write_icon_file
    calls = []

    def flaky_write(target, markup):
        calls.append(target)
        if len(calls) == 1:
            raise PermissionError("read-only")
        real_write(target, markup)

    monkeypatch.setattr(theme, "_write_icon_file", flaky_write)
    url = Path(theme.qss_icon_url("chevron-up", "#123456"))
    assert url.is_file() and len(calls) == 2 and calls[0].parent != url.parent
