"""The top bar as the window's title bar (macOS) and its drag / double-click behaviour.

The behaviour of the bar itself is tested on every platform. The AppKit part needs
the real ``cocoa`` platform plugin (``QT_QPA_PLATFORM=cocoa``): it is skipped
offscreen and runs in the macOS CI job.
"""

from __future__ import annotations

import platform

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from action_macro.gui import macos_title_bar, theme
from action_macro.gui.chrome import TopBar
from action_macro.gui.main_window import MainWindow


@pytest.fixture(scope="module", autouse=True)
def themed_app(qapp):
    """Runs this module against the shipped look: the bar's border and sizes come from the QSS."""
    saved_sheet, saved_palette, saved_font = qapp.styleSheet(), qapp.palette(), qapp.font()
    theme.apply_theme(qapp)
    yield qapp
    qapp.setStyleSheet(saved_sheet)
    qapp.setPalette(saved_palette)
    qapp.setFont(saved_font)


@pytest.fixture
def cocoa(qapp):
    """Skips unless the AppKit title bar can be used (it needs a QApplication to tell)."""
    if not macos_title_bar.supported():
        pytest.skip("needs macOS with the cocoa platform plugin (QT_QPA_PLATFORM=cocoa) and pyobjc")


@pytest.fixture
def window(qtbot, isolated_settings, monkeypatch):
    """A MainWindow with private QSettings; never blocks on an unsaved-changes prompt."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)

    def _make() -> MainWindow:
        win = MainWindow(settings=isolated_settings)
        qtbot.addWidget(win)
        return win

    return _make


@pytest.fixture
def bar(qtbot, monkeypatch):
    """A shown TopBar whose window moves and double clicks are recorded, not performed."""
    top_bar = TopBar()
    qtbot.addWidget(top_bar)
    top_bar.resize(900, theme.TOP_BAR_HEIGHT)
    top_bar.show()
    qtbot.waitExposed(top_bar)
    top_bar.moves = []
    top_bar.double_clicks = []
    monkeypatch.setattr(top_bar, "_start_window_move", lambda: top_bar.moves.append(1) or True)
    return top_bar


def _drag(widget, distance: int) -> None:
    start = QPoint(4, widget.height() // 2)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(widget, start + QPoint(distance, 0))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       start + QPoint(distance, 0))


# -----------------------------------------------------------------------------
# TopBar: title bar behaviour (all platforms)
# -----------------------------------------------------------------------------

def test_top_bar_does_not_move_the_window_unless_asked(bar):
    assert not bar.is_window_drag_enabled()
    _drag(bar.lbl_brand, 40)
    QTest.mouseDClick(bar.lbl_brand, Qt.MouseButton.LeftButton)
    assert bar.moves == []


def test_dragging_empty_space_or_a_label_moves_the_window(bar):
    bar.enable_window_drag(lambda: bar.double_clicks.append(1))
    _drag(bar.lbl_brand, 40)  # labels let the press through to the bar
    assert bar.moves == [1]
    _drag(bar, 40)
    assert bar.moves == [1, 1]
    # A press that barely moves is a click, not a move (so double clicks still work).
    _drag(bar.lbl_file, QApplication.startDragDistance() - 1)
    assert bar.moves == [1, 1] and bar.double_clicks == []


def test_buttons_keep_their_clicks(bar):
    bar.enable_window_drag(lambda: bar.double_clicks.append(1))
    bar.btn_permissions.show()
    clicked = []
    bar.btn_permissions.clicked.connect(lambda: clicked.append(1))
    _drag(bar.btn_permissions, 40)
    QTest.mouseClick(bar.btn_permissions, Qt.MouseButton.LeftButton)
    QTest.mouseDClick(bar.btn_permissions, Qt.MouseButton.LeftButton)
    assert bar.moves == [] and bar.double_clicks == []
    assert clicked  # the button still works


def test_double_click_on_empty_space_runs_the_title_bar_action(bar):
    bar.enable_window_drag(lambda: bar.double_clicks.append(1))
    QTest.mouseDClick(bar.lbl_file, Qt.MouseButton.LeftButton)
    assert bar.double_clicks == [1] and bar.moves == []
    QTest.mouseDClick(bar.btn_more, Qt.MouseButton.LeftButton)
    assert bar.double_clicks == [1]


def test_leading_space_keeps_the_window_buttons_corner_clear(bar):
    assert bar.leading_space() == TopBar.LEFT_MARGIN
    bar.set_leading_space(78)
    assert bar.leading_space() == 78
    bar.set_leading_space(0)  # e.g. full screen: the normal margin again
    assert bar.leading_space() == TopBar.LEFT_MARGIN


def _centre_y(item, ancestor) -> float:
    return item.mapTo(ancestor, QPoint(0, 0)).y() + item.height() / 2


def test_fit_title_bar_centres_the_items_on_the_given_line(bar, qtbot):
    bar.fit_title_bar(38, 19.0)
    # A real top-level window is resized and laid out asynchronously.
    qtbot.waitUntil(lambda: bar.height() == 38 and abs(_centre_y(bar.lbl_mark, bar) - 19.0) <= 0.5,
                    timeout=2000)
    assert abs(_centre_y(bar.btn_more, bar) - 19.0) <= 0.5
    bar.fit_title_bar(theme.TOP_BAR_HEIGHT)
    margins = bar.layout().contentsMargins()
    assert bar.height() == theme.TOP_BAR_HEIGHT and margins.top() == margins.bottom() == 0


def test_disable_window_drag_gives_the_bar_back_its_clicks(bar):
    bar.enable_window_drag(lambda: bar.double_clicks.append(1))
    bar.disable_window_drag()
    _drag(bar.lbl_brand, 40)
    QTest.mouseDClick(bar.lbl_brand, Qt.MouseButton.LeftButton)
    assert bar.moves == [] and bar.double_clicks == [] and not bar.is_window_drag_enabled()


def test_double_click_setting_mapping():
    assert macos_title_bar.double_click_action(None) == "zoom"  # never changed: Zoom
    assert macos_title_bar.double_click_action(None, legacy_minimize=True) == "minimize"
    assert macos_title_bar.double_click_action("Maximize") == "zoom"
    assert macos_title_bar.double_click_action("Fill") == "zoom"
    assert macos_title_bar.double_click_action("Minimize") == "minimize"
    assert macos_title_bar.double_click_action("None") == "none"


def test_not_supported_without_pyobjc(monkeypatch):
    monkeypatch.setattr(macos_title_bar, "_appkit", lambda: None)
    assert not macos_title_bar.supported()
    assert macos_title_bar.finish(object()) is None
    band = macos_title_bar.TitleBarBand(height=38, controls_right=66, controls_centre=19.0)
    assert macos_title_bar.band_click_target(object(), band) == macos_title_bar.CLICKS_STAY_IN_TITLE_BAR
    macos_title_bar.undo(object())  # nothing to undo, no error


# -----------------------------------------------------------------------------
# MainWindow
# -----------------------------------------------------------------------------

def test_other_platforms_keep_the_normal_title_bar(window, qtbot):
    if macos_title_bar.supported():
        pytest.skip("macOS with the cocoa plugin merges the title bar")
    win = window()
    win.show()
    qtbot.waitExposed(win)
    assert not macos_title_bar.has_expanded_client_area(win)
    assert win.top_bar.height() == theme.TOP_BAR_HEIGHT
    assert win.top_bar.leading_space() == TopBar.LEFT_MARGIN
    assert not win.top_bar.is_window_drag_enabled()


def _settled(win) -> bool:
    """The deferred click check has run: merged with a clickable band, or fallen back."""
    if not win._merges_title_bar:
        return not macos_title_bar.has_expanded_client_area(win) and win.isVisible()
    band = win._title_bar_band
    return band is not None and (macos_title_bar.band_click_target(win, band)
                                 == macos_title_bar.CLICKS_REACH_CONTENT)


def _shown(window, qtbot) -> MainWindow:
    win = window()
    win.resize(1200, 760)
    win.show()
    qtbot.waitExposed(win)
    qtbot.waitUntil(lambda: _settled(win), timeout=3000)
    qtbot.waitExposed(win)
    return win


def _macos() -> str:
    return f"macOS {platform.mac_ver()[0]}"


def _assert_normal_title_bar(win) -> None:
    import AppKit

    native = macos_title_bar.ns_window(win)
    assert not macos_title_bar.has_expanded_client_area(win)
    assert win.frameGeometry().height() > win.geometry().height()  # the title bar is back
    assert native.titleVisibility() == AppKit.NSWindowTitleVisible
    assert native.toolbar() is None
    assert win.top_bar.height() == theme.TOP_BAR_HEIGHT
    assert win.top_bar.leading_space() == TopBar.LEFT_MARGIN
    assert not win.top_bar.is_window_drag_enabled()


def test_macos_top_bar_takes_the_title_bar_place(cocoa, window, qtbot):
    import AppKit

    win = _shown(window, qtbot)
    if not win._merges_title_bar:
        pytest.skip(f"{_macos()}: the title bar keeps clicks in its band, so the window keeps it")
    native = macos_title_bar.ns_window(win)
    band = win._title_bar_band
    assert band is not None and band.height >= 28

    # One header row: the content starts at the window's top edge, under the title bar.
    assert macos_title_bar.has_expanded_client_area(win)
    assert native.styleMask() & AppKit.NSWindowStyleMaskFullSizeContentView
    assert win.frameGeometry().height() == win.geometry().height()
    assert win.top_bar.mapTo(win, QPoint(0, 0)) == QPoint(0, 0)
    assert win.top_bar.height() == band.height

    # No title text over the top bar, but the window keeps its title (Window menu, Dock).
    assert native.titleVisibility() == AppKit.NSWindowTitleHidden
    assert native.title().startswith("ActionMacro")

    # The traffic lights sit in the top bar's free corner, centred like its items.
    height = native.frame().size.height
    for kind in (AppKit.NSWindowCloseButton, AppKit.NSWindowMiniaturizeButton, AppKit.NSWindowZoomButton):
        button = native.standardWindowButton_(kind)
        rect = button.convertRect_toView_(button.bounds(), None)
        assert not button.isHidden()
        assert rect.origin.x + rect.size.width <= win.top_bar.leading_space() - 8
        assert 0 <= height - (rect.origin.y + rect.size.height) and height - rect.origin.y <= band.height
        assert abs(height - (rect.origin.y + rect.size.height / 2) - band.controls_centre) < 0.5
    mark = win.top_bar.lbl_mark
    qtbot.waitUntil(lambda: mark.mapTo(win, QPoint(0, 0)).x() >= band.controls_right + macos_title_bar.LEAD_GAP,
                    timeout=2000)  # laid out after the show
    assert abs(_centre_y(mark, win) - band.controls_centre) <= 0.5

    # Clicks in the band reach the Qt widgets, and the empty space moves the window.
    frame_view = native.contentView().superview()
    hit = frame_view.hitTest_(AppKit.NSMakePoint(win.width() / 2, height - band.height / 2))
    assert "QNSView" in type(hit).__name__
    assert win.top_bar.is_window_drag_enabled()


def test_macos_keeps_the_normal_title_bar_where_its_band_keeps_the_clicks(cocoa, window, qtbot):
    """macOS 15: the toolbar view takes clicks in the band, so the top bar's buttons would be dead."""
    win = _shown(window, qtbot)
    if win._merges_title_bar:
        pytest.skip(f"{_macos()}: clicks in the title bar band reach the Qt widgets")
    _assert_normal_title_bar(win)


@pytest.mark.parametrize("cause", ["appkit refused", "band keeps the clicks", "band never settles",
                                   "band keeps the clicks once placed"])
def test_macos_falls_back_to_the_normal_title_bar(cocoa, window, qtbot, monkeypatch, cause):
    pending, title_bar = macos_title_bar.CLICKS_NOT_PLACED_YET, macos_title_bar.CLICKS_STAY_IN_TITLE_BAR
    if cause == "appkit refused":
        monkeypatch.setattr(macos_title_bar, "finish", lambda w: None)
    elif cause == "band keeps the clicks":
        monkeypatch.setattr(macos_title_bar, "band_click_target", lambda w, band: title_bar)
    elif cause == "band never settles":
        monkeypatch.setattr(macos_title_bar, "band_click_target", lambda w, band: pending)
        monkeypatch.setattr(MainWindow, "_TITLE_BAR_CHECK_MS", 5)
    else:
        answers = iter([pending, pending])
        monkeypatch.setattr(macos_title_bar, "band_click_target", lambda w, band: next(answers, title_bar))
    win = window()
    win.show()
    qtbot.waitUntil(lambda: not macos_title_bar.has_expanded_client_area(win) and win.isVisible(),
                    timeout=3000)
    qtbot.waitExposed(win)
    _assert_normal_title_bar(win)
