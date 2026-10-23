"""Unit tests for Screen Coordinate Picker Overlay."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest

from action_macro.gui.coordinate_picker import CoordinatePicker, CoordinatePickerOverlay
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver


def test_picker_initialization(qtbot):
    picker = CoordinatePickerOverlay()
    qtbot.addWidget(picker)

    flags = picker.windowFlags()
    assert bool(flags & Qt.WindowType.FramelessWindowHint)
    assert bool(flags & Qt.WindowType.WindowStaysOnTopHint)
    assert bool(flags & Qt.WindowType.Tool)

    assert picker.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert picker.hasMouseTracking()
    assert picker.cursor().shape() == Qt.CursorShape.CrossCursor


def test_realtime_mouse_tracking(qtbot):
    picker = CoordinatePickerOverlay()
    qtbot.addWidget(picker)

    with qtbot.waitSignal(picker.mouse_moved) as blocker:
        picker.simulate_mouse_move(550, 320)

    assert blocker.args == [550, 320]
    assert picker.get_current_coordinates() == (550, 320)


def test_pixel_color_query_and_hex(qtbot):
    mock_screen = MockScreenDriver(width=1920, height=1080)
    # Paint synthetic pixel color (255, 87, 51) at (500, 300)
    mock_screen._canvas[300, 500] = (255, 87, 51)

    picker = CoordinatePickerOverlay(screen_adapter=mock_screen)
    qtbot.addWidget(picker)

    picker.simulate_mouse_move(500, 300)
    assert picker.get_current_pixel_color() == (255, 87, 51)
    assert picker.get_current_hex_color() == "#FF5733"


def test_left_click_selection_emits_and_closes(qtbot):
    captured: list[tuple[int, int]] = []

    def on_picked(x: int, y: int) -> None:
        captured.append((x, y))

    picker = CoordinatePickerOverlay(callback=on_picked)
    qtbot.addWidget(picker)
    picker.start()

    with qtbot.waitSignal(picker.coordinate_selected) as blocker:
        picker.simulate_click(720, 480, button="left")

    assert blocker.args == [720, 480]
    assert captured == [(720, 480)]
    assert picker.selected_coord == (720, 480)
    assert not picker.isVisible()


def test_esc_key_cancellation(qtbot):
    cancelled_called = []

    def on_cancel() -> None:
        cancelled_called.append(True)

    picker = CoordinatePickerOverlay(cancel_callback=on_cancel)
    qtbot.addWidget(picker)
    picker.start()

    with qtbot.waitSignal(picker.cancelled):
        picker.simulate_cancel()

    assert cancelled_called == [True]
    assert picker.is_cancelled is True
    assert picker.selected_coord is None
    assert not picker.isVisible()


def test_qtest_synthetic_events(qtbot):
    picker = CoordinatePickerOverlay()
    qtbot.addWidget(picker)
    picker.start()

    # Synthetic mouse move via QTest
    QTest.mouseMove(picker, QPoint(400, 250))
    assert picker.current_local_x == 400
    assert picker.current_local_y == 250

    # Synthetic left click via QTest
    with qtbot.waitSignal(picker.coordinate_selected):
        QTest.mouseClick(picker, Qt.MouseButton.LeftButton, pos=QPoint(400, 250))

    assert not picker.isVisible()


def test_paint_event_execution(qtbot):
    picker = CoordinatePickerOverlay()
    qtbot.addWidget(picker)
    picker.resize(800, 600)
    picker.update_cursor_position(200, 150)

    # Paint into an offscreen image to test painter logic
    img = QImage(800, 600, QImage.Format.Format_ARGB32)
    painter = QPainter(img)
    picker.paintEvent(None)
    painter.end()


def test_pick_convenience_method(qtbot):
    recorded = []
    overlay = CoordinatePickerOverlay.pick(callback=lambda x, y: recorded.append((x, y)))
    qtbot.addWidget(overlay)

    overlay.simulate_click(640, 360, button="left")
    assert recorded == [(640, 360)]
