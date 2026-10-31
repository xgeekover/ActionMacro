"""Unit tests for Screen Coordinate Picker Overlay."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtTest import QTest

from action_macro.gui import coordinate_picker
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


# -----------------------------------------------------------------------------
# Scaled monitors: picks are click-step coordinates (physical pixels on Windows)
# -----------------------------------------------------------------------------


class _ScaledScreen:
    """A 1920 x 1080 monitor at 125 % left of the primary one, as Qt reports it on Windows.

    Qt keeps its physical top-left corner (-1920, 0) and divides its size by 1.25.
    """

    def geometry(self) -> QRect:
        return QRect(-1920, 0, 1536, 864)

    def devicePixelRatio(self) -> float:
        return 1.25

    def grabWindow(self, *_args):  # noqa: N802 - Qt naming
        raise AssertionError("real screen capture must not run in tests")


def _mouse(kind: QEvent.Type, local: QPointF, button: Qt.MouseButton = Qt.MouseButton.NoButton) -> QMouseEvent:
    """A mouse event on the _ScaledScreen overlay at ``local`` (global = origin + local)."""
    glob = QPointF(-1920 + local.x(), local.y())
    return QMouseEvent(kind, local, glob, button, button, Qt.KeyboardModifier.NoModifier)


@pytest.mark.parametrize("physical", [(-1920, 0), (-1795, 250), (-1, 1079)])
def test_pick_on_a_scaled_monitor_returns_the_physical_pixel(qtbot, monkeypatch, physical):
    """Windows: the engine (pynput) and mss use physical pixels, so the pick must be one too."""
    monkeypatch.setattr(coordinate_picker, "PICKS_PHYSICAL_PIXELS", True)
    picker = CoordinatePickerOverlay(target_screen=_ScaledScreen())
    qtbot.addWidget(picker)
    # Where Qt reports that physical pixel: 1.25 physical pixels per point from the origin.
    local = QPointF((physical[0] + 1920) / 1.25, physical[1] / 1.25)

    picker.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, local))
    assert picker.get_current_coordinates() == physical
    assert picker.readout_text() == f"X {physical[0]}   Y {physical[1]}"

    with qtbot.waitSignal(picker.coordinate_selected) as blocker:
        picker.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, local, Qt.MouseButton.LeftButton))
    assert tuple(blocker.args) == physical
    assert picker.selected_coord == physical


def test_pick_on_a_scaled_monitor_returns_qt_points_on_macos(qtbot, monkeypatch):
    """macOS: Qt's global points are the click coordinates, whatever the backing scale."""
    monkeypatch.setattr(coordinate_picker, "PICKS_PHYSICAL_PIXELS", False)
    picker = CoordinatePickerOverlay(target_screen=_ScaledScreen())
    qtbot.addWidget(picker)
    local = QPointF(100, 200)

    picker.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, local))
    assert picker.get_current_coordinates() == (-1820, 200)
    with qtbot.waitSignal(picker.coordinate_selected) as blocker:
        picker.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, local, Qt.MouseButton.LeftButton))
    assert blocker.args == [-1820, 200]


def test_click_coordinates_place_the_crosshair_on_a_scaled_monitor(qtbot, monkeypatch):
    """Moves and the initial cursor come in click coordinates; the crosshair is drawn in points."""
    monkeypatch.setattr(coordinate_picker, "PICKS_PHYSICAL_PIXELS", True)
    picker = CoordinatePickerOverlay(target_screen=_ScaledScreen())
    qtbot.addWidget(picker)
    picker.simulate_mouse_move(-1795, 250)
    assert (picker.current_local_x, picker.current_local_y) == (100, 200)
    assert picker.get_current_coordinates() == (-1795, 250)

    # The input adapter reports the cursor in click coordinates too (pynput on Windows).
    at_start = CoordinatePickerOverlay(target_screen=_ScaledScreen(), input_adapter=MockInputDriver(-1420, 500))
    qtbot.addWidget(at_start)
    at_start._sync_initial_cursor_position()
    assert (at_start.current_local_x, at_start.current_local_y) == (400, 400)
    assert at_start.get_current_coordinates() == (-1420, 500)

    elsewhere = CoordinatePickerOverlay(target_screen=_ScaledScreen(), input_adapter=MockInputDriver(10, 10))
    qtbot.addWidget(elsewhere)
    elsewhere._sync_initial_cursor_position()  # the cursor is on another monitor
    assert (elsewhere.current_local_x, elsewhere.current_local_y) == (0, 0)
