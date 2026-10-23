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
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QScreen,
)
from PySide6.QtWidgets import QApplication, QWidget

from action_macro.engine.coordinate_transformer import CoordinateTransformer
from action_macro.engine.input_adapter import InputAdapterProtocol
from action_macro.engine.screen_adapter import ScreenAdapterProtocol


class CoordinatePickerOverlay(QWidget):
    """Semi-transparent full-screen coordinate picker overlay.

    Features:
    - Real-time mouse position tracking with thin crosshairs.
    - 8x Pixel Magnifier zoom box showing the 15x15 physical pixel area around cursor.
    - Live HUD displaying logical coordinates, RGB pixel color, and hex code.
    - Multi-monitor master-slave synchronization.
    - Retina / HiDPI awareness via CoordinateTransformer.
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
        pos = QCursor.pos()
        if self.input_adapter and hasattr(self.input_adapter, "get_position"):
            try:
                ix, iy = self.input_adapter.get_position()
                pos = QPoint(ix, iy)
            except Exception:
                pass

        if self.target_screen and self.target_screen.geometry().contains(pos):
            s_geom = self.target_screen.geometry()
            self.update_cursor_position(pos.x() - s_geom.x(), pos.y() - s_geom.y(), pos.x(), pos.y())

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

    def update_cursor_position(
        self,
        local_x: int,
        local_y: int,
        global_x: Optional[int] = None,
        global_y: Optional[int] = None,
    ) -> None:
        """Updates internal cursor tracking coordinates and queries pixel color."""
        self.current_local_x = int(round(local_x))
        self.current_local_y = int(round(local_y))

        if global_x is not None and global_y is not None:
            self.current_global_x = int(round(global_x))
            self.current_global_y = int(round(global_y))
        elif self.target_screen:
            s_geom = self.target_screen.geometry()
            self.current_global_x = self.current_local_x + s_geom.x()
            self.current_global_y = self.current_local_y + s_geom.y()
        else:
            self.current_global_x = self.current_local_x
            self.current_global_y = self.current_local_y

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
        self.update_cursor_position(loc.x(), loc.y(), glob.x(), glob.y())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Handles mouse click selection (Left) and cancellation (Right)."""
        if event.button() == Qt.MouseButton.LeftButton:
            glob = event.globalPosition()
            gx = int(round(glob.x()))
            gy = int(round(glob.y()))
            self._finish_selection(gx, gy)
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
        """Paints the translucent veil, crosshairs, pixel magnifier, and HUD."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        cur_x = self.current_local_x
        cur_y = self.current_local_y
        w = self.width()
        h = self.height()

        # 1. Translucent dark veil over entire screen
        painter.fillRect(self.rect(), QColor(15, 23, 42, 60))

        # 2. Full-screen crosshair lines (Sky-Blue #38BDF8, 1px, skipping 5px center gap)
        crosshair_pen = QPen(QColor(56, 189, 248, 200), 1)
        painter.setPen(crosshair_pen)

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

        # Center reticle dots
        painter.setPen(QPen(QColor(239, 68, 68, 220), 1))
        painter.drawPoint(cur_x, cur_y)

        # 3. Magnifier and HUD Card
        self._paint_magnifier_and_hud(painter, cur_x, cur_y, w, h)
        painter.end()

    def _paint_magnifier_and_hud(
        self,
        painter: QPainter,
        cur_x: int,
        cur_y: int,
        screen_w: int,
        screen_h: int,
    ) -> None:
        """Draws the 8x pixel magnifier box and attached metadata HUD."""
        grid_dim = self.sample_grid_size  # e.g. 15
        zoom = self.zoom_factor            # e.g. 8
        view_size = grid_dim * zoom        # e.g. 120px

        card_padding = 10
        card_w = view_size + card_padding * 2  # 140px
        hud_h = 75
        card_h = view_size + card_padding * 2 + hud_h  # ~215px

        offset_x, offset_y = 25, 25
        box_x = cur_x + offset_x
        box_y = cur_y + offset_y

        # Dynamic boundary edge flipping
        if box_x + card_w + 10 > screen_w:
            box_x = cur_x - offset_x - card_w
        if box_y + card_h + 10 > screen_h:
            box_y = cur_y - offset_y - card_h

        box_x = max(10, min(box_x, max(10, screen_w - card_w - 10)))
        box_y = max(10, min(box_y, max(10, screen_h - card_h - 10)))

        # Card Container (Dark Slate #0F172A with Cyan Border #38BDF8)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(56, 189, 248, 220), 1.5))
        painter.setBrush(QBrush(QColor(15, 23, 42, 230)))
        painter.drawRoundedRect(QRectF(box_x, box_y, card_w, card_h), 8.0, 8.0)

        # Magnifier Viewport Grid
        view_x = box_x + card_padding
        view_y = box_y + card_padding
        self._paint_magnifier_grid(painter, view_x, view_y, cur_x, cur_y, grid_dim, zoom)

        # HUD Metadata Section
        self._paint_hud_text(painter, box_x + card_padding, view_y + view_size + 12, card_w - card_padding * 2)

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
        """Renders the magnified pixel grid with center target highlight."""
        radius = grid_dim // 2
        dpr = self.target_screen.devicePixelRatio() if self.target_screen else 1.0
        phys_cx, phys_cy = CoordinateTransformer.logical_to_physical(cur_x, cur_y, dpr)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        for row in range(grid_dim):
            for col in range(grid_dim):
                px = phys_cx - radius + col
                py = phys_cy - radius + row

                col_val = QColor(0, 0, 0)
                if self._desktop_snapshot is not None:
                    if 0 <= px < self._desktop_snapshot.width() and 0 <= py < self._desktop_snapshot.height():
                        col_val = self._desktop_snapshot.pixelColor(px, py)
                elif self.screen_adapter and hasattr(self.screen_adapter, "get_pixel"):
                    try:
                        r, g, b = self.screen_adapter.get_pixel(px, py)
                        col_val = QColor(r, g, b)
                    except Exception:
                        col_val = QColor(80, 80, 80) if (row + col) % 2 == 0 else QColor(110, 110, 110)
                else:
                    col_val = QColor(80, 80, 80) if (row + col) % 2 == 0 else QColor(110, 110, 110)

                cell_x = view_x + col * zoom
                cell_y = view_y + row * zoom
                painter.fillRect(QRectF(cell_x, cell_y, zoom, zoom), col_val)

                # Subtle grid line
                painter.setPen(QPen(QColor(0, 0, 0, 40), 0.5))
                painter.drawRect(QRectF(cell_x, cell_y, zoom, zoom))

        # Center target pixel reticle highlight (Bright Red #EF4444)
        center_cell_x = view_x + radius * zoom
        center_cell_y = view_y + radius * zoom
        painter.setPen(QPen(QColor(239, 68, 68, 255), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(center_cell_x - 0.5, center_cell_y - 0.5, zoom + 1, zoom + 1))

    def _paint_hud_text(
        self,
        painter: QPainter,
        text_x: int,
        text_y: int,
        content_w: int,
    ) -> None:
        """Renders coordinate labels, RGB/Hex values, color swatch, and user hints."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 1. Coordinates: X: {x}   Y: {y}
        font = QFont("Monospace", 9)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(248, 250, 252))
        painter.drawText(
            text_x,
            text_y + 10,
            f"X: {self.current_global_x}   Y: {self.current_global_y}",
        )

        # 2. Color Swatch and Hex / RGB
        r, g, b = self.current_rgb
        hex_str = f"#{r:02X}{g:02X}{b:02X}"

        # Swatch rect
        swatch_size = 14
        swatch_y = text_y + 18
        painter.fillRect(QRectF(text_x, swatch_y, swatch_size, swatch_size), QColor(r, g, b))
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
        painter.drawRect(QRectF(text_x, swatch_y, swatch_size, swatch_size))

        font_sm = QFont("Monospace", 8)
        painter.setFont(font_sm)
        painter.setPen(QColor(226, 232, 240))
        painter.drawText(text_x + swatch_size + 8, swatch_y + 11, f"{hex_str}")

        # 3. Action Hint
        font_hint = QFont("Sans-Serif", 7)
        painter.setFont(font_hint)
        painter.setPen(QColor(148, 163, 184))
        painter.drawText(text_x, swatch_y + 26, "Click: Pick | ESC: Cancel")

    # -------------------------------------------------------------------------
    # Programmatic Headless Simulation API (For Automated CI Testing)
    # -------------------------------------------------------------------------

    def simulate_mouse_move(self, x: int, y: int) -> None:
        """Simulates cursor hover movement to global coordinates (x, y)."""
        s_geom_x = self.target_screen.geometry().x() if self.target_screen else 0
        s_geom_y = self.target_screen.geometry().y() if self.target_screen else 0
        local_x = x - s_geom_x
        local_y = y - s_geom_y
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
        """Returns the currently tracked global coordinates (X, Y)."""
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
