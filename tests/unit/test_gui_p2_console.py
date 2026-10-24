"""Phase-2 tests: the console panel (header, chips, empty state) and the picker overlay styling."""

from __future__ import annotations

import gc
import re
import weakref
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QRegion, QTextCursor
from PySide6.QtWidgets import QApplication, QComboBox, QToolButton, QWidget

from action_macro.gui import coordinate_picker as picker_module
from action_macro.gui import log_console as console_module
from action_macro.gui import theme
from action_macro.gui.coordinate_picker import CoordinatePickerOverlay
from action_macro.gui.log_console import FILTER_LEVELS, LogConsole
from action_macro.gui.main_window import MainWindow

C = theme.COLORS


def _make_console(qtbot, shown: bool = False) -> LogConsole:
    console = LogConsole()
    qtbot.addWidget(console)
    if shown:
        console.resize(900, 200)
        console.show()
        QApplication.processEvents()
    return console


def _color_at(console: LogConsole, block_number: int, column: int) -> str:
    """Foreground colour of the character at ``column`` of display line ``block_number``."""
    doc = console.text_edit.document()
    block = doc.findBlockByNumber(block_number)
    cursor = QTextCursor(doc)
    cursor.setPosition(block.position() + column + 1)  # charFormat() is the char before
    return cursor.charFormat().foreground().color().name().upper()


def _checked_chips(console: LogConsole) -> list[str]:
    return [value for value, chip in console.filter_chips.items() if chip.isChecked()]


# -----------------------------------------------------------------------------
# Console header
# -----------------------------------------------------------------------------


def test_header_has_title_count_chips_and_icon_buttons(qtbot):
    console = _make_console(qtbot, shown=True)

    assert console.lbl_title.text() == "CONSOLE"
    assert console.lbl_title.property("role") == "section"
    assert [chip.text() for chip in console.filter_chips.values()] == ["All", "Info", "Action", "Warning", "Error"]
    # The level filter is a compact segmented control (single choice, same look as the
    # inspector's segments); filter_chips maps each combo item to its segment button.
    assert all(chip.isCheckable() and chip.parent() is console.filter_segments
               for chip in console.filter_chips.values())
    assert console.filter_segments.is_compact()
    assert all(chip.cursor().shape() == Qt.CursorShape.PointingHandCursor for chip in console.filter_chips.values())
    assert _checked_chips(console) == ["All Levels"]

    # The combo is kept (and keeps its items) but is not shown.
    assert isinstance(console.filter_combo, QComboBox)
    assert [console.filter_combo.itemText(i) for i in range(console.filter_combo.count())] == list(FILTER_LEVELS)
    assert console.filter_combo.isHidden()

    for button, tip in ((console.auto_scroll_cb, "Auto-scroll"), (console.btn_copy, "Copy"),
                        (console.btn_clear, "Clear"), (console.btn_collapse, "Hide console")):
        assert isinstance(button, QToolButton)
        assert not button.icon().isNull()
        assert button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert button.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert tip.lower() in button.toolTip().lower()
    assert console.auto_scroll_cb.isCheckable()
    assert console.findChild(QWidget, "ConsoleHeader").height() == console_module.HEADER_HEIGHT


def test_chip_click_drives_filter_combo_and_view(qtbot):
    console = _make_console(qtbot)
    console.append_log("INFO", "info line")
    console.append_log("ERROR", "error line")
    console.append_log("ACTION", "action line")

    console.filter_chips["ERROR"].click()
    assert console.filter_combo.currentText() == "ERROR"
    assert _checked_chips(console) == ["ERROR"]
    text = console.text_edit.toPlainText()
    assert "error line" in text and "info line" not in text and "action line" not in text

    console.filter_chips["ACTION"].click()
    assert console.filter_combo.currentText() == "ACTION"
    assert _checked_chips(console) == ["ACTION"]
    assert console.text_edit.toPlainText().strip().endswith("action line")

    console.filter_chips["All Levels"].click()
    assert console.filter_combo.currentText() == "All Levels"
    assert len(console.text_edit.toPlainText().splitlines()) == 3


@pytest.mark.parametrize("value", list(FILTER_LEVELS))
def test_filter_combo_drives_the_chips(qtbot, value):
    console = _make_console(qtbot)
    console.filter_combo.setCurrentText("ERROR" if value == "All Levels" else "All Levels")
    console.filter_combo.setCurrentText(value)
    assert _checked_chips(console) == [value]


def test_count_badge_counts_entries_and_filtered_matches(qtbot):
    console = _make_console(qtbot, shown=True)
    assert console.lbl_count.isHidden()  # nothing to count yet

    for level in ("INFO", "ERROR", "INFO"):
        console.append_log(level, f"{level} entry")
    assert not console.lbl_count.isHidden()
    assert console.lbl_count.text() == "3"

    console.filter_combo.setCurrentText("ERROR")
    assert console.lbl_count.text() == "1 / 3"
    console.append_log("ERROR", "second error")
    console.append_log("INFO", "not shown")
    assert console.lbl_count.text() == "2 / 5"
    assert (console.shown_count(), console.entry_count()) == (2, 5)

    console.clear_logs()
    assert console.lbl_count.isHidden()


def test_set_max_block_count_trims_the_buffer_and_keeps_counts_right(qtbot):
    console = _make_console(qtbot)
    for i in range(10):
        console.append_log("ERROR" if i % 2 else "INFO", f"Message {i}")
    console.filter_combo.setCurrentText("ERROR")
    console.set_max_block_count(4)
    assert [e.message for e in console._entries] == ["Message 6", "Message 7", "Message 8", "Message 9"]
    assert console.lbl_count.text() == "2 / 4"

    console.append_log("ERROR", "Message 10")  # pushes out "Message 6" (INFO)
    assert console.lbl_count.text() == "3 / 4"
    console.append_log("INFO", "Message 11")   # pushes out "Message 7" (ERROR)
    assert console.lbl_count.text() == "2 / 4"


def test_zero_max_block_count_means_unlimited(qtbot):
    console = _make_console(qtbot)
    console.set_max_block_count(0)  # Qt semantics: no limit
    for i in range(12):
        console.append_log("INFO", f"Message {i}")
    assert len(console._entries) == 12
    assert "Message 11" in console.text_edit.toPlainText()


# -----------------------------------------------------------------------------
# Console body
# -----------------------------------------------------------------------------


def test_empty_state_line_is_shown_but_never_part_of_the_log_text(qtbot):
    console = _make_console(qtbot, shown=True)
    assert console.lbl_empty.isVisible()
    assert console.lbl_empty.text() == "No log entries yet."
    assert console.text_edit.toPlainText() == ""
    assert not console.btn_copy.isEnabled() and not console.btn_clear.isEnabled()

    console.append_log("INFO", "first entry")
    assert not console.lbl_empty.isVisible()
    assert console.btn_copy.isEnabled() and console.btn_clear.isEnabled()

    console.filter_combo.setCurrentText("WARNING")  # no matches
    assert console.lbl_empty.isVisible()
    assert console.lbl_empty.text() == "No warning entries."
    assert console.text_edit.toPlainText() == ""
    assert not console.btn_copy.isEnabled() and console.btn_clear.isEnabled()

    console.filter_combo.setCurrentText("All Levels")
    assert not console.lbl_empty.isVisible()
    console.clear_logs()
    assert console.lbl_empty.isVisible() and console.lbl_empty.text() == "No log entries yet."
    assert console.text_edit.toPlainText() == ""


def test_empty_state_line_returns_to_its_place_after_the_log_scrolled(qtbot):
    """Scrolling moves viewport children; the empty line must be re-anchored when shown again."""
    console = _make_console(qtbot, shown=True)
    for i in range(200):
        console.append_log("INFO", f"Message {i}")
    QApplication.processEvents()
    assert console.text_edit.verticalScrollBar().value() > 0

    console.clear_logs()
    QApplication.processEvents()
    # The line is centred in the view (like the other empty states), not scrolled away.
    viewport = console.text_edit.viewport().rect()
    label = console.lbl_empty.geometry()
    assert console.lbl_empty.isVisible()
    assert abs(label.center().x() - viewport.center().x()) <= 1
    assert abs(label.center().y() - viewport.center().y()) <= 1
    assert viewport.contains(label)


def test_log_lines_use_theme_token_colours_and_clean_plain_text(qtbot):
    console = _make_console(qtbot)
    expected = {
        "INFO": C.info,
        "ACTION": C.success,
        "WARNING": C.warning,
        "ERROR": C.danger,
        "DEBUG": C.text_muted,  # informative text never uses text-faint (below 4.5:1)
        "CUSTOM": C.text_muted,
    }
    for level in expected:
        console.append_log(level, f"{level.lower()} message")

    lines = console.text_edit.toPlainText().splitlines()
    assert len(lines) == len(expected)
    for number, (level, colour) in enumerate(expected.items()):
        line = lines[number]
        # "HH:MM:SS.mmm  LEVEL    message": no brackets, no hidden characters, aligned columns
        assert re.fullmatch(r"\d\d:\d\d:\d\d\.\d{3}  [A-Z]{1,7} *  [a-z]+ message", line), line
        assert line[14:14 + 7].rstrip() == level
        assert line[23:] == f"{level.lower()} message"
        assert _color_at(console, number, 0) == C.text_muted.upper()                    # timestamp
        assert _color_at(console, number, 14) == colour.upper()                          # level tag
        assert _color_at(console, number, line.index("message")) == C.text.upper()      # message


def test_level_and_message_markup_is_shown_literally(qtbot):
    console = _make_console(qtbot)
    console.append_log("<b>", "<i>not italic</i> & more")
    text = console.text_edit.toPlainText()
    assert "<B>" in text
    assert "<i>not italic</i> & more" in text


def test_log_lines_have_a_comfortable_line_height(qtbot):
    console = _make_console(qtbot, shown=True)
    console.append_log("INFO", "one")
    console.append_log("ERROR", "two")
    console.append_log("ACTION", "three")
    QApplication.processEvents()
    doc = console.text_edit.document()
    text_height = QFontMetrics(console.text_edit.font()).height()
    # (the last block also carries the document's bottom margin, so compare the first two)
    first = console.text_edit.blockBoundingGeometry(doc.findBlockByNumber(0)).height()
    second = console.text_edit.blockBoundingGeometry(doc.findBlockByNumber(1)).height()
    assert first == second
    assert first >= text_height + 4


def test_auto_scroll_toggle_keeps_its_behaviour(qtbot):
    console = _make_console(qtbot, shown=True)
    assert console.auto_scroll_cb.isChecked()
    for i in range(30):
        console.append_log("INFO", f"Message {i}")
    assert console.text_edit.textCursor().atEnd()

    console.auto_scroll_cb.setChecked(False)
    cursor = console.text_edit.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    console.text_edit.setTextCursor(cursor)
    console.append_log("INFO", "while reading older lines")
    assert console.text_edit.textCursor().position() == 0  # the reader's position is kept

    console.auto_scroll_cb.setChecked(True)  # turning it back on jumps to the newest entry
    assert console.text_edit.textCursor().atEnd()


def test_copy_shows_brief_feedback(qtbot):
    console = _make_console(qtbot)
    console.append_log("INFO", "Clipboard item")
    normal_tip = console.btn_copy.toolTip()
    console._copy_timer.setInterval(20)
    console.btn_copy.click()
    assert "Clipboard item" in QApplication.clipboard().text()
    assert console.btn_copy.toolTip() == "Copied"
    qtbot.waitUntil(lambda: console.btn_copy.toolTip() == normal_tip, timeout=2000)


# -----------------------------------------------------------------------------
# Collapse wiring in MainWindow
# -----------------------------------------------------------------------------


def test_collapse_chevron_emits_signal(qtbot):
    console = _make_console(qtbot)
    with qtbot.waitSignal(console.collapse_requested, timeout=1000):
        console.btn_collapse.click()


def test_collapse_chevron_hides_the_console_panel(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    assert win.is_console_visible()

    win.log_console.btn_collapse.click()
    assert not win.is_console_visible()
    assert not win.act_toggle_console.isChecked()
    assert not win.rail_btn_console.isChecked()
    assert not win.btn_status_console.isChecked()

    win.act_toggle_console.setChecked(True)  # rail / status bar / Ctrl+J bring it back
    assert win.is_console_visible()


def test_console_wiring_does_not_keep_the_main_window_alive(qtbot):
    """A lambda capturing the window in the collapse connection leaked every MainWindow."""
    win = MainWindow()
    ref = weakref.ref(win)
    del win
    gc.collect()
    assert ref() is None


# -----------------------------------------------------------------------------
# Coordinate picker overlay (offscreen, synthetic snapshot, never started)
# -----------------------------------------------------------------------------


class _FakeScreen:
    """Target screen for the overlay; asking it for a real capture fails the test."""

    def __init__(self, w: int, h: int, dpr: float = 1.0) -> None:
        self._rect = QRect(0, 0, w, h)
        self._dpr = dpr

    def geometry(self) -> QRect:
        return QRect(self._rect)

    def devicePixelRatio(self) -> float:
        return self._dpr

    def grabWindow(self, *_args):  # noqa: N802 - Qt naming
        raise AssertionError("real screen capture must not run in tests")


def _overlay(qtbot, w: int, h: int, dpr: float = 1.0, snapshot: QImage | None = None) -> CoordinatePickerOverlay:
    picker = CoordinatePickerOverlay(target_screen=_FakeScreen(w, h, dpr))
    qtbot.addWidget(picker)
    picker.resize(w, h)
    if snapshot is not None:
        picker._desktop_snapshot = snapshot  # injected; start() (and its grab) is never called
    return picker


def _render(picker: CoordinatePickerOverlay) -> QImage:
    canvas = QImage(picker.width(), picker.height(), QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor(0, 0, 0, 0))
    painter = QPainter(canvas)
    picker.render(painter, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    painter.end()
    return canvas


def _close(a: QColor, b: QColor, tol: int = 10) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))


def test_readout_text_shows_global_coordinates(qtbot):
    picker = _overlay(qtbot, 1400, 900)
    picker.update_cursor_position(640, 360, 1280, 720)
    assert picker.readout_text() == "X 1280   Y 720"
    picker.simulate_mouse_move(-1200, -300)
    assert picker.readout_text() == "X -1200   Y -300"


@pytest.mark.parametrize("size", [(1400, 900), (1100, 680), (1920, 1080)])
def test_hud_group_never_leaves_the_screen(qtbot, size):
    w, h = size
    picker = _overlay(qtbot, w, h)
    screen = QRect(0, 0, w, h)
    xs = (-500, 0, 1, 5, 24, w // 2, w - 200, w - 30, w - 6, w - 1, w, w + 300)
    ys = (-500, 0, 1, 5, 24, h // 2, h - 200, h - 30, h - 6, h - 1, h, h + 300)
    for x in xs:
        for y in ys:
            picker.update_cursor_position(x, y, x - 3840, y - 2160)  # long negative readout
            mag, pill, hint = picker.hud_rects(x, y, w, h)
            for rect in (mag, pill, hint):
                assert screen.contains(rect), (x, y, rect)
            assert not mag.intersects(pill) and not pill.intersects(hint)
            assert mag.width() == mag.height() == picker.sample_grid_size * picker.zoom_factor
            if screen.contains(QPoint(x, y)):
                assert not mag.united(pill).united(hint).contains(QPoint(x, y)), (x, y)


def test_overlay_paints_veil_crosshair_magnifier_and_readout_from_the_snapshot(qtbot):
    w, h = 800, 600
    snapshot = QImage(w, h, QImage.Format.Format_ARGB32)
    snapshot.fill(QColor(C.text_muted))
    marker = QColor(C.danger)
    painter = QPainter(snapshot)
    painter.fillRect(QRect(300 - 7, 200 - 7, 15, 15), marker)  # the 15x15 sampled area
    painter.end()

    picker = _overlay(qtbot, w, h, snapshot=snapshot)
    picker.update_cursor_position(300, 200)
    assert picker.get_current_hex_color() == marker.name().upper()
    canvas = _render(picker)

    # Veil: the bg token at the veil opacity (the snapshot itself is not drawn by the overlay)
    veil = canvas.pixelColor(40, 40)
    assert abs(veil.alpha() - round(picker_module.VEIL_OPACITY * 255)) <= 2
    assert _close(veil, QColor(C.bg))

    # Crisp 1 px accent crosshair: on the cursor row/column, not on the neighbouring rows
    assert _close(canvas.pixelColor(100, 200), QColor(C.accent), tol=14)
    assert _close(canvas.pixelColor(300, 80), QColor(C.accent), tol=14)
    assert canvas.pixelColor(100, 199).alpha() < 120 and canvas.pixelColor(100, 201).alpha() < 120

    mag, pill, hint = picker.hud_rects(300, 200, w, h)
    zoom = picker.zoom_factor
    # A zoomed cell (row 3, col 3) shows the sampled colour, fully opaque
    cell = canvas.pixelColor(mag.x() + 3 * zoom + zoom // 2, mag.y() + 3 * zoom + zoom // 2)
    assert cell.alpha() == 255 and _close(cell, marker, tol=2)
    # Centre pixel marker: light ring on the centre cell edge
    centre = picker.sample_radius * zoom
    assert _close(canvas.pixelColor(mag.x() + centre, mag.y() + centre + zoom // 2), QColor(C.on_accent), tol=2)
    # Readout pill: opaque surface between the text and its rounded edge
    pill_px = canvas.pixelColor(pill.right() - 4, pill.center().y())
    assert pill_px.alpha() == 255 and _close(pill_px, QColor(C.surface), tol=3)
    # Hint chip
    assert canvas.pixelColor(hint.left() + 3, hint.center().y()).alpha() > 150


def test_magnifier_samples_physical_pixels_on_hidpi(qtbot):
    w, h = 400, 300
    snapshot = QImage(w * 2, h * 2, QImage.Format.Format_ARGB32)
    snapshot.fill(QColor(C.bg))
    snapshot.setPixelColor(2 * 100, 2 * 80, QColor(C.success))  # physical pixel under the cursor
    picker = _overlay(qtbot, w, h, dpr=2.0, snapshot=snapshot)
    picker.update_cursor_position(100, 80)
    canvas = _render(picker)
    mag, _, _ = picker.hud_rects(100, 80, w, h)
    zoom = picker.zoom_factor
    centre = picker.sample_radius * zoom
    inside_centre = canvas.pixelColor(mag.x() + centre + zoom // 2, mag.y() + centre + zoom // 2)
    assert _close(inside_centre, QColor(C.success), tol=2)


def test_magnifier_cell_colours_outside_the_snapshot_and_without_one(qtbot):
    snapshot = QImage(10, 10, QImage.Format.Format_ARGB32)
    snapshot.fill(QColor(C.info))
    picker = _overlay(qtbot, 100, 100, snapshot=snapshot)
    assert picker._magnifier_cell_color(5, 5, 0, 0).name().upper() == C.info.upper()
    assert picker._magnifier_cell_color(-1, 5, 0, 0).name().upper() == C.bg.upper()

    bare = _overlay(qtbot, 100, 100)
    colours = {bare._magnifier_cell_color(0, 0, r, c).name().upper() for r in range(2) for c in range(2)}
    assert colours == {C.surface_3.upper(), C.border_strong.upper()}


@pytest.mark.parametrize("module", [console_module, picker_module])
def test_no_ad_hoc_colour_literals(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert not re.findall(r"#[0-9A-Fa-f]{6}\b", source)
    assert not re.findall(r"QColor\(\s*\d", source)
    assert "Arial" not in source and 'QFont("' not in source
