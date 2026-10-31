"""
action_macro.gui.coordinate_picker
======================================
Screen Coordinate Picker Overlay for ActionMacro automation.

Provides a semi-transparent, full-screen overlay with real-time mouse tracking,
full-screen crosshairs, 8x pixel magnifier preview, dynamic coordinate & color HUD,
multi-monitor coordination, Retina/HiDPI display scaling, and headless CI testability.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QScreen,
    QTransform,
)
from PySide6.QtWidgets import QApplication, QWidget

from action_macro.engine.coordinate_transformer import CoordinateTransformer
from action_macro.engine.input_adapter import InputAdapterProtocol
from action_macro.engine.screen_adapter import ScreenAdapterProtocol
from action_macro.gui import theme

# Overlay styling (colours come from the theme tokens; these are opacities and metrics)
VEIL_OPACITY = 0.34            # bg token over the whole screen
CROSSHAIR_OPACITY = 0.95       # accent token, 1 px lines
MAGNIFIER_GRID_OPACITY = 0.30  # bg token, lines between the zoomed pixels
MAGNIFIER_CAPTION_HEIGHT = 18  # colour swatch + hex strip at the bottom of the magnifier
HUD_OFFSET = 24                # distance of the magnifier group from the cursor
HUD_MARGIN = 12                # minimum distance of the group from the screen edges
HUD_GAP = 8                    # magnifier -> readout pill
HUD_PILL_HEIGHT = 28
HUD_PILL_PAD_X = 12
HUD_HINT_GAP = 6               # readout pill -> hint line
HUD_HINT_HEIGHT = 22
HUD_HINT_PAD_X = 8
HUD_HINT_TEXT = "Click to pick · Esc to cancel"

#: Whether picked coordinates are physical pixels instead of Qt's logical points. They must be
#: in the space the engine clicks in and the screen adapter captures (see
#: action_macro.engine.screen_adapter): global display points on macOS, which Qt uses as well,
#: and physical pixels elsewhere. On Windows the process is per-monitor DPI aware, so pynput's
#: SetCursorPos and mss use physical pixels while Qt divides each screen by its scale factor
#: (1.25 at 125 %).
PICKS_PHYSICAL_PIXELS = sys.platform != "darwin"


class CoordinatePickerOverlay(QWidget):
    """Semi-transparent full-screen coordinate picker overlay.

    Features:
    - Real-time mouse position tracking with thin crosshairs.
    - 8x Pixel Magnifier zoom box showing the 15x15 physical pixel area around cursor.
    - Live HUD displaying the picked coordinates, RGB pixel color, and hex code.
    - Multi-monitor master-slave synchronization.
    - Retina / HiDPI awareness via CoordinateTransformer.
    - Picked coordinates are the ones a click step uses: physical pixels on Windows,
      where a scaled monitor's Qt coordinates differ (see PICKS_PHYSICAL_PIXELS).
    - Headless programmatic simulation API for automated CI testing.
    """

    coordinate_selected = Signal(int, int)
    cancelled = Signal()
    mouse_moved = Signal(int, int)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        screen_adapter: Optional[ScreenAdapterProtocol] = None,
        input_adapter: Optional[InputAdapterProtocol] = None,
        callback: Optional[Callable[[int, int], None]] = None,
        cancel_callback: Optional[Callable[[], None]] = None,
        target_screen: Optional[Any] = None,
        is_slave: bool = False,
        zoom_factor: int = 8,
        sample_radius: int = 7,
    ) -> None:
        """Initialize CoordinatePickerOverlay.

        Args:
            parent: Optional parent QWidget.
            screen_adapter: Optional ScreenAdapterProtocol for pixel sampling.
            input_adapter: Optional InputAdapterProtocol for position inspection.
            callback: Optional callback invoked on coordinate selection: callback(x, y).
            cancel_callback: Optional callback invoked on cancellation: cancel_callback().
            target_screen: Optional target QScreen instance for this overlay.
            is_slave: Whether this instance is a secondary overlay in a multi-monitor setup.
            zoom_factor: Magnifier zoom factor (default 8x).
            sample_radius: Radius of physical pixels to sample around cursor (default 7 -> 15x15).
        """
        super().__init__(parent)
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        if sys.platform != "darwin":
            flags |= Qt.WindowType.Tool
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self.screen_adapter = screen_adapter
        self.input_adapter = input_adapter
        self.callback = callback
        self.cancel_callback = cancel_callback
        self.is_slave = is_slave
        self.zoom_factor = max(2, min(zoom_factor, 16))
        self.sample_radius = max(2, min(sample_radius, 15))
        self.sample_grid_size = self.sample_radius * 2 + 1

        self.target_screen: Optional[Any] = target_screen or QGuiApplication.primaryScreen()
        if self.target_screen:
            self.setGeometry(self.target_screen.geometry())

        # State variables
        self.current_local_x: int = 0
        self.current_local_y: int = 0
        self.current_global_x: int = 0
        self.current_global_y: int = 0
        self.current_rgb: Tuple[int, int, int] = (0, 0, 0)
        self.selected_coord: Optional[Tuple[int, int]] = None
        self.is_cancelled: bool = False

        # Multi-monitor slaves
        self._slaves: List[CoordinatePickerOverlay] = []
        # Pre-captured clean desktop snapshot for 0ms latency magnifier
        self._desktop_snapshot: Optional[QImage] = None

    # -------------------------------------------------------------------------
    # Lifecycle & Display Control
    # -------------------------------------------------------------------------

    def start(self) -> CoordinatePickerOverlay:
        """Activates and displays the coordinate picker overlay.

        In multi-monitor environments, automatically spawns slave overlays
        on all connected secondary displays.
        """
        # 1. Pre-capture desktop snapshot before showing translucent veil
        self._capture_screen_snapshot()

        # 2. Master initializes slaves for multi-monitor setups
        if not self.is_slave:
            screens = QGuiApplication.screens()
            for s in screens:
                if s != self.target_screen:
                    slave = CoordinatePickerOverlay(
                        screen_adapter=self.screen_adapter,
                        input_adapter=self.input_adapter,
                        target_screen=s,
                        is_slave=True,
                        zoom_factor=self.zoom_factor,
                        sample_radius=self.sample_radius,
                    )
                    slave.coordinate_selected.connect(self._on_slave_selected)
                    slave.cancelled.connect(self._on_slave_cancelled)
                    slave.start()
                    self._slaves.append(slave)

        # 3. Show and activate window
        self.show()
        try:
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
            if sys.platform != "darwin":
                self.grabKeyboard()
        except Exception as err:
            logger.debug("Non-critical window activation / keyboard grab notice: %s", err)

        # 4. Initialize cursor position from QCursor or InputAdapter
        self._sync_initial_cursor_position()
        return self

    def _sync_initial_cursor_position(self) -> None:
        """Synchronizes overlay position with initial cursor coordinates."""
        if not self.target_screen:
            return
        s_geom = self.target_screen.geometry()
        pos = QCursor.pos()
        local = (pos.x() - s_geom.x(), pos.y() - s_geom.y())
        if self.input_adapter and hasattr(self.input_adapter, "get_position"):
            try:
                local = self._local_from_engine(*self.input_adapter.get_position())
            except Exception:
                pass

        if 0 <= local[0] < s_geom.width() and 0 <= local[1] < s_geom.height():
            self.update_cursor_position(local[0], local[1])

    def _capture_screen_snapshot(self) -> None:
        """Captures a pristine, un-tinted desktop snapshot for instant magnifier cropping."""
        if not self.target_screen:
            return
        try:
            pixmap = self.target_screen.grabWindow(0)
            if not pixmap.isNull():
                self._desktop_snapshot = pixmap.toImage()
        except Exception as err:
            logger.debug("Desktop snapshot capture notice: %s", err)
            self._desktop_snapshot = None

    def close_all(self) -> None:
        """Closes this overlay and all associated multi-screen slave overlays."""
        try:
            self.releaseKeyboard()
        except Exception:
            pass

        for slave in self._slaves:
            slave.close()
        self._slaves.clear()
        self._desktop_snapshot = None
        self.close()

    def closeEvent(self, event: Any) -> None:
        """Ensures keyboard release on window close."""
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        super().closeEvent(event)

    # -------------------------------------------------------------------------
    # Master-Slave Coordination Handlers
    # -------------------------------------------------------------------------

    def _on_slave_selected(self, x: int, y: int) -> None:
        """Handles coordinate selection forwarded from a slave overlay."""
        self._finish_selection(x, y)

    def _on_slave_cancelled(self) -> None:
        """Handles cancellation forwarded from a slave overlay."""
        self._finish_cancellation()

    def _finish_selection(self, x: int, y: int) -> None:
        """Commits selected coordinate, emits signal, invokes callback, and closes."""
        self.selected_coord = (int(round(x)), int(round(y)))
        self.coordinate_selected.emit(self.selected_coord[0], self.selected_coord[1])
        if self.callback:
            try:
                self.callback(self.selected_coord[0], self.selected_coord[1])
            except Exception as err:
                logger.error("Error in coordinate_selected callback: %s", err)
        self.close_all()

    def _finish_cancellation(self) -> None:
        """Aborts selection, emits cancelled signal, invokes callback, and closes."""
        self.is_cancelled = True
        self.cancelled.emit()
        if self.cancel_callback:
            try:
                self.cancel_callback()
            except Exception as err:
                logger.error("Error in cancelled callback: %s", err)
        self.close_all()

    # -------------------------------------------------------------------------
    # Position & Color Sampling
    # -------------------------------------------------------------------------

    def _physical_scale(self) -> float:
        """Physical pixels per Qt point on this overlay's screen (1.0 when picking points)."""
        if not PICKS_PHYSICAL_PIXELS or not self.target_screen:
            return 1.0
        return float(self.target_screen.devicePixelRatio())

    def _engine_from_global(self, global_x: float, global_y: float) -> Tuple[int, int]:
        """Returns the click-step coordinates of a point given in Qt's global coordinates.

        Qt keeps a screen's top-left corner and divides the distance from it by the scale
        factor, so the physical pixel is ``origin + (global - origin) * scale``.
        """
        scale = self._physical_scale()
        if scale == 1.0:
            return (int(round(global_x)), int(round(global_y)))
        origin = self.target_screen.geometry().topLeft()
        return (int(round(origin.x() + (global_x - origin.x()) * scale)),
                int(round(origin.y() + (global_y - origin.y()) * scale)))

    def _local_from_engine(self, x: float, y: float) -> Tuple[float, float]:
        """Returns the overlay position of click-step coordinates (x, y)."""
        origin = self.target_screen.geometry().topLeft() if self.target_screen else QPoint(0, 0)
        scale = self._physical_scale()
        return ((x - origin.x()) / scale, (y - origin.y()) / scale)

    def update_cursor_position(
        self,
        local_x: int,
        local_y: int,
        global_x: Optional[int] = None,
        global_y: Optional[int] = None,
    ) -> None:
        """Updates internal cursor tracking coordinates and queries pixel color.

        ``global_x``/``global_y`` are click-step coordinates (what a pick returns); without
        them they are worked out from the overlay position.
        """
        self.current_local_x = int(round(local_x))
        self.current_local_y = int(round(local_y))

        if global_x is None or global_y is None:
            origin = self.target_screen.geometry().topLeft() if self.target_screen else QPoint(0, 0)
            global_x, global_y = self._engine_from_global(origin.x() + local_x, origin.y() + local_y)
        self.current_global_x = int(round(global_x))
        self.current_global_y = int(round(global_y))

        # Query pixel color at cursor
        self.current_rgb = self._query_pixel_color(self.current_local_x, self.current_local_y)

        self.mouse_moved.emit(self.current_global_x, self.current_global_y)
        self.update()

    def _query_pixel_color(self, local_x: int, local_y: int) -> Tuple[int, int, int]:
        """Queries the RGB color of the pixel under the cursor."""
        # 1. Check injected ScreenAdapterProtocol (e.g. MockScreenDriver)
        if self.screen_adapter:
            try:
                if hasattr(self.screen_adapter, "get_pixel_color"):
                    return self.screen_adapter.get_pixel_color(self.current_global_x, self.current_global_y)
                elif hasattr(self.screen_adapter, "get_pixel"):
                    return self.screen_adapter.get_pixel(self.current_global_x, self.current_global_y)
            except Exception:
                pass

        # 2. Check cached desktop snapshot
        if self._desktop_snapshot is not None:
            dpr = self.target_screen.devicePixelRatio() if self.target_screen else 1.0
            phys_x, phys_y = CoordinateTransformer.logical_to_physical(local_x, local_y, dpr)
            if 0 <= phys_x < self._desktop_snapshot.width() and 0 <= phys_y < self._desktop_snapshot.height():
                col = self._desktop_snapshot.pixelColor(phys_x, phys_y)
                return (col.red(), col.green(), col.blue())

        return (0, 0, 0)

    # -------------------------------------------------------------------------
    # Qt Event Handlers
    # -------------------------------------------------------------------------

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Handles mouse hover events to update crosshairs, HUD, and magnifier."""
        loc = event.position()
        glob = event.globalPosition()
        self.update_cursor_position(loc.x(), loc.y(), *self._engine_from_global(glob.x(), glob.y()))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handles mouse click selection (Left) and cancellation (Right)."""
        if event.button() == Qt.MouseButton.LeftButton:
            glob = event.globalPosition()
            self._finish_selection(*self._engine_from_global(glob.x(), glob.y()))
        elif event.button() == Qt.MouseButton.RightButton:
            self._finish_cancellation()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handles ESC key cancellation."""
        if event.key() == Qt.Key.Key_Escape:
            self._finish_cancellation()
        else:
            super().keyPressEvent(event)

    # -------------------------------------------------------------------------
    # Visual Rendering Pipeline (paintEvent)
    # -------------------------------------------------------------------------

    def paintEvent(self, event: Any) -> None:
        """Paints the darkening veil, crosshairs, pixel magnifier, coordinate readout and hint."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        cur_x = self.current_local_x
        cur_y = self.current_local_y
        w = self.width()
        h = self.height()

        # 1. Translucent dark veil over entire screen
        painter.fillRect(self.rect(), theme.qcolor(theme.COLORS.bg, VEIL_OPACITY))

        # 2. Full-screen crosshair lines (crisp 1px accent, skipping a small gap around the cursor)
        painter.setPen(QPen(theme.qcolor(theme.COLORS.accent, CROSSHAIR_OPACITY), 1))

        # Horizontal line
        if cur_x > 4:
            painter.drawLine(0, cur_y, cur_x - 4, cur_y)
        if cur_x + 5 < w:
            painter.drawLine(cur_x + 5, cur_y, w, cur_y)

        # Vertical line
        if cur_y > 4:
            painter.drawLine(cur_x, 0, cur_x, cur_y - 4)
        if cur_y + 5 < h:
            painter.drawLine(cur_x, cur_y + 5, cur_x, h)

        # Centre point
        painter.setPen(QPen(theme.qcolor(theme.COLORS.accent_text), 1))
        painter.drawPoint(cur_x, cur_y)

        # 3. Magnifier, readout pill and hint, kept on screen near the edges
        self._paint_magnifier_and_hud(painter, cur_x, cur_y, w, h)
        painter.end()

    def _hud_fonts(self) -> Tuple[QFont, QFont, QFont]:
        """Returns the (readout, hint, caption) fonts, created once from the theme."""
        fonts = getattr(self, "_hud_font_cache", None)
        if fonts is None:
            readout = theme.mono_font(theme.FONT_BODY)
            readout.setWeight(QFont.Weight.DemiBold)
            fonts = (readout, theme.ui_font(theme.FONT_SMALL), theme.mono_font(theme.FONT_SMALL))
            self._hud_font_cache = fonts
        return fonts

    def _readout_parts(self) -> List[Tuple[str, bool]]:
        """Returns the readout text as (text, is_label) runs: ``X 1280   Y 720``."""
        return [
            ("X", True), (f" {self.current_global_x}", False),
            ("   ", False),
            ("Y", True), (f" {self.current_global_y}", False),
        ]

    def readout_text(self) -> str:
        """Returns the coordinate readout shown in the pill, e.g. ``"X 1280   Y 720"``."""
        return "".join(text for text, _ in self._readout_parts())

    def hud_rects(self, cur_x: int, cur_y: int, screen_w: int, screen_h: int) -> Tuple[QRect, QRect, QRect]:
        """Returns the local rectangles of the (magnifier, readout pill, hint) group.

        The group sits below-right of the cursor, flips to the other side of the
        cursor when it would cross the right or bottom edge, and is finally clamped
        so it stays on screen (as long as the screen is larger than the group).
        Elements are aligned to the side facing the cursor, so the magnifier does not
        jump while the readout width changes with the numbers.
        """
        readout_font, hint_font, _ = self._hud_fonts()
        view = self.sample_grid_size * self.zoom_factor
        pill_w = HUD_PILL_PAD_X * 2 + QFontMetrics(readout_font).horizontalAdvance(self.readout_text())
        hint_w = HUD_HINT_PAD_X * 2 + QFontMetrics(hint_font).horizontalAdvance(HUD_HINT_TEXT)
        group_w = max(view, pill_w, hint_w)
        group_h = view + HUD_GAP + HUD_PILL_HEIGHT + HUD_HINT_GAP + HUD_HINT_HEIGHT

        on_right = cur_x + HUD_OFFSET + group_w + HUD_MARGIN <= screen_w
        gx = cur_x + HUD_OFFSET if on_right else cur_x - HUD_OFFSET - group_w
        gy = cur_y + HUD_OFFSET
        if gy + group_h + HUD_MARGIN > screen_h:
            gy = cur_y - HUD_OFFSET - group_h
        gx = max(HUD_MARGIN, min(gx, screen_w - HUD_MARGIN - group_w))
        gy = max(HUD_MARGIN, min(gy, screen_h - HUD_MARGIN - group_h))

        def place(width: int, top: int, height: int) -> QRect:
            left = gx if on_right else gx + group_w - width
            return QRect(left, top, width, height)

        mag = place(view, gy, view)
        pill = place(pill_w, mag.bottom() + 1 + HUD_GAP, HUD_PILL_HEIGHT)
        hint = place(hint_w, pill.bottom() + 1 + HUD_HINT_GAP, HUD_HINT_HEIGHT)
        return mag, pill, hint

    def _paint_magnifier_and_hud(
        self,
        painter: QPainter,
        cur_x: int,
        cur_y: int,
        screen_w: int,
        screen_h: int,
    ) -> None:
        """Draws the pixel magnifier, the coordinate readout pill and the hint line."""
        mag, pill, hint = self.hud_rects(cur_x, cur_y, screen_w, screen_h)
        self._paint_magnifier_grid(painter, mag.x(), mag.y(), cur_x, cur_y, self.sample_grid_size, self.zoom_factor)
        self._paint_hud_text(painter, pill, hint)

    def _magnifier_cell_color(self, px: int, py: int, row: int, col: int) -> QColor:
        """Returns the colour of physical pixel (px, py) for the magnifier cell (row, col)."""
        col_val = theme.qcolor(theme.COLORS.bg)  # outside the captured screen
        if self._desktop_snapshot is not None:
            if 0 <= px < self._desktop_snapshot.width() and 0 <= py < self._desktop_snapshot.height():
                col_val = self._desktop_snapshot.pixelColor(px, py)
        elif self.screen_adapter and hasattr(self.screen_adapter, "get_pixel"):
            try:
                r, g, b = self.screen_adapter.get_pixel(px, py)
                col_val = QColor(r, g, b)
            except Exception:
                col_val = self._placeholder_cell_color(row, col)
        else:
            col_val = self._placeholder_cell_color(row, col)
        return col_val

    @staticmethod
    def _placeholder_cell_color(row: int, col: int) -> QColor:
        """Checkerboard shown when no pixels can be sampled."""
        token = theme.COLORS.surface_3 if (row + col) % 2 == 0 else theme.COLORS.border_strong
        return theme.qcolor(token)

    def _paint_magnifier_grid(
        self,
        painter: QPainter,
        view_x: int,
        view_y: int,
        cur_x: int,
        cur_y: int,
        grid_dim: int,
        zoom: int,
    ) -> None:
        """Renders the magnifier: zoomed pixels in a rounded square, pixel grid, centre marker."""
        radius = grid_dim // 2
        dpr = self.target_screen.devicePixelRatio() if self.target_screen else 1.0
        phys_cx, phys_cy = CoordinateTransformer.logical_to_physical(cur_x, cur_y, dpr)

        # One image pixel per sampled physical pixel, then a nearest-neighbour zoom
        samples = QImage(grid_dim, grid_dim, QImage.Format.Format_RGB32)
        for row in range(grid_dim):
            for col in range(grid_dim):
                px = phys_cx - radius + col
                py = phys_cy - radius + row
                samples.setPixelColor(col, row, self._magnifier_cell_color(px, py, row, col))

        view = grid_dim * zoom
        zoomed = samples.scaled(view, view, Qt.AspectRatioMode.IgnoreAspectRatio,
                                Qt.TransformationMode.FastTransformation)

        grid = QPainter(zoomed)
        grid.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        # Pixel grid
        grid.setPen(QPen(theme.qcolor(theme.COLORS.bg, MAGNIFIER_GRID_OPACITY), 1))
        for i in range(1, grid_dim):
            grid.drawLine(i * zoom, 0, i * zoom, view)
            grid.drawLine(0, i * zoom, view, i * zoom)
        # Centre pixel marker: a light ring on the cell edges with a dark outline around it,
        # visible on any pixel colour
        center = radius * zoom
        grid.setBrush(Qt.BrushStyle.NoBrush)
        grid.setPen(QPen(theme.qcolor(theme.COLORS.bg), 1))
        grid.drawRect(center - 1, center - 1, zoom + 2, zoom + 2)
        grid.setPen(QPen(theme.qcolor(theme.COLORS.on_accent), 1))
        grid.drawRect(center, center, zoom, zoom)
        grid.end()

        rect = QRectF(view_x, view_y, view, view)
        corner = float(theme.RADIUS_MD)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._paint_soft_shadow(painter, rect, corner)

        brush = QBrush(zoomed)
        brush.setTransform(QTransform.fromTranslate(view_x, view_y))
        painter.setBrush(brush)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, corner, corner)

        # Colour caption (swatch + hex) along the bottom edge of the magnifier
        _, _, caption_font = self._hud_fonts()
        cap_h = MAGNIFIER_CAPTION_HEIGHT
        frame = QPainterPath()
        frame.addRoundedRect(rect, corner, corner)
        strip = QPainterPath()
        strip.addRect(QRectF(rect.left(), rect.bottom() - cap_h, rect.width(), cap_h))
        painter.fillPath(frame.intersected(strip), theme.qcolor(theme.COLORS.surface, 0.88))

        r, g, b = self.current_rgb
        swatch = QRectF(rect.left() + 8, rect.bottom() - cap_h + (cap_h - 8) / 2, 8, 8)
        painter.setBrush(QColor(r, g, b))
        painter.setPen(QPen(theme.qcolor(theme.COLORS.text, 0.55), 1))
        painter.drawRoundedRect(swatch, 2, 2)
        painter.setFont(caption_font)
        painter.setPen(theme.qcolor(theme.COLORS.text_muted))
        text_rect = QRectF(swatch.right() + 6, rect.bottom() - cap_h, rect.width() - 30, cap_h)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                         f"#{r:02X}{g:02X}{b:02X}")

        # 1px border on top of everything
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(theme.qcolor(theme.COLORS.border_strong), 1))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), corner, corner)
        painter.restore()

    @staticmethod
    def _paint_soft_shadow(painter: QPainter, rect: QRectF, corner: float) -> None:
        """Paints a small, soft drop shadow under ``rect`` (a few translucent rings)."""
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, opacity in ((4, 0.10), (3, 0.14), (2, 0.20), (1, 0.30)):
            painter.setBrush(theme.qcolor(theme.COLORS.bg, opacity))
            painter.drawRoundedRect(rect.adjusted(-spread, -spread + 1, spread, spread + 1),
                                    corner + spread, corner + spread)

    def _paint_hud_text(self, painter: QPainter, pill: QRect, hint: QRect) -> None:
        """Renders the coordinate readout pill (``X 1280   Y 720``) and the hint line."""
        readout_font, hint_font, _ = self._hud_fonts()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Readout pill
        pill_rect = QRectF(pill)
        self._paint_soft_shadow(painter, pill_rect, float(theme.RADIUS_MD))
        painter.setBrush(theme.qcolor(theme.COLORS.surface))
        painter.setPen(QPen(theme.qcolor(theme.COLORS.border_strong), 1))
        painter.drawRoundedRect(pill_rect.adjusted(0.5, 0.5, -0.5, -0.5), theme.RADIUS_MD, theme.RADIUS_MD)

        painter.setFont(readout_font)
        fm = QFontMetrics(readout_font)
        x = pill.left() + HUD_PILL_PAD_X
        baseline = pill.top() + (pill.height() - fm.height()) // 2 + fm.ascent()
        for text, is_label in self._readout_parts():
            painter.setPen(theme.qcolor(theme.COLORS.text_muted if is_label else theme.COLORS.text))
            painter.drawText(x, baseline, text)
            x += fm.horizontalAdvance(text)

        # Hint line on a translucent chip, legible over any screen content
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.qcolor(theme.COLORS.bg, 0.82))
        painter.drawRoundedRect(QRectF(hint), theme.RADIUS_SM, theme.RADIUS_SM)
        painter.setFont(hint_font)
        painter.setPen(theme.qcolor(theme.COLORS.text_muted))
        painter.drawText(QRectF(hint), int(Qt.AlignmentFlag.AlignCenter), HUD_HINT_TEXT)
        painter.restore()

    # -------------------------------------------------------------------------
    # Programmatic Headless Simulation API (For Automated CI Testing)
    # -------------------------------------------------------------------------

    def simulate_mouse_move(self, x: int, y: int) -> None:
        """Simulates cursor hover movement to click-step coordinates (x, y)."""
        local_x, local_y = self._local_from_engine(x, y)
        self.update_cursor_position(local_x, local_y, x, y)

    def simulate_click(self, x: int, y: int, button: str = "left") -> None:
        """Simulates a mouse click at (x, y) with specified button."""
        if button.lower() == "left":
            self._finish_selection(x, y)
        else:
            self._finish_cancellation()

    def simulate_cancel(self) -> None:
        """Simulates cancellation via ESC key or Right Click."""
        self._finish_cancellation()

    def get_current_coordinates(self) -> Tuple[int, int]:
        """Returns the currently tracked click-step coordinates (X, Y)."""
        return (self.current_global_x, self.current_global_y)

    def get_current_pixel_color(self) -> Tuple[int, int, int]:
        """Returns the currently sampled RGB pixel color (R, G, B)."""
        return self.current_rgb

    def get_current_hex_color(self) -> str:
        """Returns the currently sampled pixel color formatted as #RRGGBB."""
        r, g, b = self.current_rgb
        return f"#{r:02X}{g:02X}{b:02X}"

    # -------------------------------------------------------------------------
    # Static / Class Convenience Helpers
    # -------------------------------------------------------------------------

    @classmethod
    def pick(
        cls,
        callback: Optional[Callable[[int, int], None]] = None,
        cancel_callback: Optional[Callable[[], None]] = None,
        screen_adapter: Optional[ScreenAdapterProtocol] = None,
        input_adapter: Optional[InputAdapterProtocol] = None,
        parent: Optional[QWidget] = None,
    ) -> CoordinatePickerOverlay:
        """Convenience method to instantiate and launch the coordinate picker overlay."""
        overlay = cls(
            parent=parent,
            screen_adapter=screen_adapter,
            input_adapter=input_adapter,
            callback=callback,
            cancel_callback=cancel_callback,
        )
        overlay.start()
        return overlay


# Alias for backward compatibility and clean ergonomics
CoordinatePicker = CoordinatePickerOverlay
