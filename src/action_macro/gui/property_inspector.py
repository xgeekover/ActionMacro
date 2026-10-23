"""
action_macro.gui.property_inspector
=======================================
Dynamic parameter inspector and editor widget for ActionMacro automation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from action_macro.core.key_mapping import (
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


class MouseClickForm(QWidget):
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
        layout.setSpacing(10)

        # Target Coordinates
        grp_coord = QGroupBox("Target Coordinates (Pixels / Logical Points)")
        grid = QGridLayout(grp_coord)

        grid.addWidget(QLabel("X Coordinate:"), 0, 0)
        self.spin_x = QSpinBox()
        self.spin_x.setRange(0, 99999)
        self.spin_x.valueChanged.connect(self._on_changed)
        grid.addWidget(self.spin_x, 0, 1)

        grid.addWidget(QLabel("Y Coordinate:"), 1, 0)
        self.spin_y = QSpinBox()
        self.spin_y.setRange(0, 99999)
        self.spin_y.valueChanged.connect(self._on_changed)
        grid.addWidget(self.spin_y, 1, 1)

        self.btn_pick = QPushButton("📍 Pick Screen Coordinate")
        self.btn_pick.setStyleSheet("background-color: #E3F2FD; color: #1976D2; font-weight: bold; padding: 6px;")
        self.btn_pick.clicked.connect(self.pick_requested.emit)
        grid.addWidget(self.btn_pick, 2, 0, 1, 2)

        layout.addWidget(grp_coord)

        # Mouse Options
        grp_opts = QGroupBox("Click Options")
        fgrid = QGridLayout(grp_opts)

        fgrid.addWidget(QLabel("Button:"), 0, 0)
        self.combo_button = QComboBox()
        self.combo_button.addItems(["left", "right", "middle"])
        self.combo_button.currentIndexChanged.connect(self._on_changed)
        fgrid.addWidget(self.combo_button, 0, 1)

        fgrid.addWidget(QLabel("Clicks Count:"), 1, 0)
        self.spin_clicks = QSpinBox()
        self.spin_clicks.setRange(1, 10)
        self.spin_clicks.setValue(1)
        self.spin_clicks.valueChanged.connect(self._on_changed)
        fgrid.addWidget(self.spin_clicks, 1, 1)

        fgrid.addWidget(QLabel("Interval (ms):"), 2, 0)
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(0, 60000)
        self.spin_interval.setSingleStep(10)
        self.spin_interval.valueChanged.connect(self._on_changed)
        fgrid.addWidget(self.spin_interval, 2, 1)

        fgrid.addWidget(QLabel("Press Duration (ms):"), 3, 0)
        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(0, 10000)
        self.spin_duration.setSingleStep(10)
        self.spin_duration.setValue(50)
        self.spin_duration.valueChanged.connect(self._on_changed)
        fgrid.addWidget(self.spin_duration, 3, 1)

        layout.addWidget(grp_opts)
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


class KeyInputForm(QWidget):
    """Parameter editor sub-form for KEY_INPUT action."""

    modified = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._block_signals = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        grp_key = QGroupBox("Keyboard Action Configuration")
        grid = QGridLayout(grp_key)

        grid.addWidget(QLabel("Action Mode:"), 0, 0)
        self.combo_action = QComboBox()
        self.combo_action.addItems(["press", "down", "up"])
        self.combo_action.currentIndexChanged.connect(self._on_changed)
        grid.addWidget(self.combo_action, 0, 1)

        grid.addWidget(QLabel("Keys:"), 1, 0)
        self.edit_keys = QLineEdit()
        self.edit_keys.setPlaceholderText("e.g. enter or ctrl+c or a")
        self.edit_keys.textChanged.connect(self._on_changed)
        grid.addWidget(self.edit_keys, 1, 1)

        grid.addWidget(QLabel("Press Duration (ms):"), 2, 0)
        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(0, 10000)
        self.spin_duration.setSingleStep(10)
        self.spin_duration.setValue(50)
        self.spin_duration.valueChanged.connect(self._on_changed)
        grid.addWidget(self.spin_duration, 2, 1)

        layout.addWidget(grp_key)

        # Key Combo Helper Group
        grp_helper = QGroupBox("Key Combination Helper")
        hlayout = QVBoxLayout(grp_helper)

        mod_box = QHBoxLayout()
        self.chk_ctrl = QCheckBox("Ctrl/Cmd")
        self.chk_alt = QCheckBox("Alt/Opt")
        self.chk_shift = QCheckBox("Shift")
        for chk in (self.chk_ctrl, self.chk_alt, self.chk_shift):
            mod_box.addWidget(chk)
        hlayout.addLayout(mod_box)

        key_box = QHBoxLayout()
        self.combo_preset_keys = QComboBox()
        common_keys = [
            "enter", "esc", "space", "tab", "backspace", "delete",
            "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
            "up", "down", "left", "right", "home", "end", "page_up", "page_down",
        ]
        self.combo_preset_keys.addItems(common_keys)
        key_box.addWidget(self.combo_preset_keys, stretch=1)

        btn_apply_combo = QPushButton("Insert Combo")
        btn_apply_combo.clicked.connect(self._apply_combo_helper)
        key_box.addWidget(btn_apply_combo)
        hlayout.addLayout(key_box)

        self.lbl_preview = QLabel("Preview: N/A")
        self.lbl_preview.setStyleSheet("color: #666666; font-style: italic;")
        hlayout.addWidget(self.lbl_preview)

        layout.addWidget(grp_helper)
        layout.addStretch()

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

        keys_raw = params.get("keys", ["enter"])
        if isinstance(keys_raw, list):
            self.edit_keys.setText("+".join(keys_raw))
        else:
            self.edit_keys.setText(str(keys_raw))

        self.spin_duration.setValue(int(params.get("press_duration_ms", 50)))
        self._block_signals = False
        self._update_preview()

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
            self.lbl_preview.setText(f"Formatted: {format_key_combo(keys)}")
        else:
            self.lbl_preview.setText("Formatted: None")

    def _on_changed(self) -> None:
        self._update_preview()
        if not self._block_signals:
            self.modified.emit()


class FixedWaitForm(QWidget):
    """Parameter editor sub-form for FIXED_WAIT action."""

    modified = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._block_signals = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        grp_wait = QGroupBox("Wait Duration")
        vbox = QVBoxLayout(grp_wait)

        hbox = QHBoxLayout()
        hbox.addWidget(QLabel("Duration (ms):"))
        self.spin_duration = QSpinBox()
        self.spin_duration.setRange(0, 3600000)
        self.spin_duration.setSingleStep(100)
        self.spin_duration.valueChanged.connect(self._on_spin_changed)
        hbox.addWidget(self.spin_duration)
        vbox.addLayout(hbox)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 10000)
        self.slider.setSingleStep(100)
        self.slider.valueChanged.connect(self._on_slider_changed)
        vbox.addWidget(self.slider)

        self.lbl_human = QLabel("1.00 seconds (1000 ms)")
        self.lbl_human.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        self.lbl_human.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_human.setStyleSheet("color: #E65100; padding: 4px;")
        vbox.addWidget(self.lbl_human)

        # Quick preset buttons
        preset_box = QHBoxLayout()
        for ms, label in [(100, "100ms"), (500, "500ms"), (1000, "1s"), (2000, "2s"), (5000, "5s")]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked=False, val=ms: self.spin_duration.setValue(val))
            preset_box.addWidget(btn)
        vbox.addLayout(preset_box)

        layout.addWidget(grp_wait)
        layout.addStretch()

    def _on_spin_changed(self, val: int) -> None:
        sec = val / 1000.0
        self.lbl_human.setText(f"{sec:.2f} seconds ({val} ms)")
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


class ImageWaitForm(QWidget):
    """Parameter editor sub-form for IMAGE_WAIT action."""

    modified = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._block_signals = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # Template Selection
        grp_tmpl = QGroupBox("Template Image")
        grid = QGridLayout(grp_tmpl)

        grid.addWidget(QLabel("Template Path:"), 0, 0)
        self.edit_path = QLineEdit()
        self.edit_path.textChanged.connect(self._on_path_changed)
        grid.addWidget(self.edit_path, 0, 1)

        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse_image)
        grid.addWidget(self.btn_browse, 0, 2)

        # Thumbnail Preview Box
        self.lbl_preview = QLabel("No Image Loaded")
        self.lbl_preview.setFixedSize(140, 80)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setStyleSheet("border: 1px dashed #AAAAAA; background-color: #FAFAFA;")
        grid.addWidget(self.lbl_preview, 1, 0, 1, 3, Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(grp_tmpl)

        # Detection Parameters
        grp_params = QGroupBox("Matching & Wait Parameters")
        pgrid = QGridLayout(grp_params)

        pgrid.addWidget(QLabel("Wait Mode:"), 0, 0)
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["appear", "disappear"])
        self.combo_mode.currentIndexChanged.connect(self._on_changed)
        pgrid.addWidget(self.combo_mode, 0, 1)

        pgrid.addWidget(QLabel("Confidence:"), 1, 0)
        self.spin_conf = QDoubleSpinBox()
        self.spin_conf.setRange(0.0, 1.0)
        self.spin_conf.setSingleStep(0.05)
        self.spin_conf.setValue(0.80)
        self.spin_conf.valueChanged.connect(self._on_changed)
        pgrid.addWidget(self.spin_conf, 1, 1)

        pgrid.addWidget(QLabel("Timeout (seconds):"), 2, 0)
        self.spin_timeout = QDoubleSpinBox()
        self.spin_timeout.setRange(0.0, 3600.0)
        self.spin_timeout.setSingleStep(0.5)
        self.spin_timeout.setValue(10.0)
        self.spin_timeout.valueChanged.connect(self._on_changed)
        pgrid.addWidget(self.spin_timeout, 2, 1)

        pgrid.addWidget(QLabel("Timeout Policy:"), 3, 0)
        self.combo_timeout_action = QComboBox()
        self.combo_timeout_action.addItems(["raise", "skip"])
        self.combo_timeout_action.currentIndexChanged.connect(self._on_changed)
        pgrid.addWidget(self.combo_timeout_action, 3, 1)

        layout.addWidget(grp_params)

        # Region of Interest (ROI)
        grp_roi = QGroupBox("Region of Interest (ROI)")
        rgrid = QGridLayout(grp_roi)

        self.chk_roi = QCheckBox("Enable ROI Bounding Box")
        self.chk_roi.toggled.connect(self._toggle_roi)
        rgrid.addWidget(self.chk_roi, 0, 0, 1, 2)

        rgrid.addWidget(QLabel("X:"), 1, 0)
        self.spin_rx = QSpinBox()
        self.spin_rx.setRange(0, 99999)
        self.spin_rx.valueChanged.connect(self._on_changed)
        rgrid.addWidget(self.spin_rx, 1, 1)

        rgrid.addWidget(QLabel("Y:"), 2, 0)
        self.spin_ry = QSpinBox()
        self.spin_ry.setRange(0, 99999)
        self.spin_ry.valueChanged.connect(self._on_changed)
        rgrid.addWidget(self.spin_ry, 2, 1)

        rgrid.addWidget(QLabel("Width:"), 3, 0)
        self.spin_rw = QSpinBox()
        self.spin_rw.setRange(1, 99999)
        self.spin_rw.setValue(100)
        self.spin_rw.valueChanged.connect(self._on_changed)
        rgrid.addWidget(self.spin_rw, 3, 1)

        rgrid.addWidget(QLabel("Height:"), 4, 0)
        self.spin_rh = QSpinBox()
        self.spin_rh.setRange(1, 99999)
        self.spin_rh.setValue(100)
        self.spin_rh.valueChanged.connect(self._on_changed)
        rgrid.addWidget(self.spin_rh, 4, 1)

        layout.addWidget(grp_roi)
        layout.addStretch()

    def _toggle_roi(self, checked: bool) -> None:
        for spin in (self.spin_rx, self.spin_ry, self.spin_rw, self.spin_rh):
            spin.setEnabled(checked)
        self._on_changed()

    def _browse_image(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Template Image", "", "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_path:
            self.edit_path.setText(file_path)

    def _on_path_changed(self, text: str) -> None:
        clean = text.strip()
        if clean and os.path.isfile(clean):
            pixmap = QPixmap(clean)
            if not pixmap.isNull():
                scaled = pixmap.scaled(130, 70, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self.lbl_preview.setPixmap(scaled)
            else:
                self.lbl_preview.setText("Invalid Image")
        else:
            self.lbl_preview.setText("Image Not Found")
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
            self.chk_roi.setChecked(False)
            self._toggle_roi(False)

        self._block_signals = False
        self._on_path_changed(tmpl)

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

        self._setup_ui()
        self.set_action(None)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Header Title
        self.lbl_header = QLabel("Action Properties")
        self.lbl_header.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(self.lbl_header)

        # Common Fields Frame
        self.common_frame = QFrame()
        cbox = QVBoxLayout(self.common_frame)
        cbox.setContentsMargins(0, 0, 0, 0)
        cbox.setSpacing(6)

        desc_box = QHBoxLayout()
        desc_box.addWidget(QLabel("Description:"))
        self.edit_description = QLineEdit()
        self.edit_description.setPlaceholderText("Optional action description...")
        self.edit_description.textChanged.connect(self._on_common_changed)
        desc_box.addWidget(self.edit_description)
        cbox.addLayout(desc_box)

        self.chk_enabled = QCheckBox("Enable this action")
        self.chk_enabled.toggled.connect(self._on_common_changed)
        cbox.addWidget(self.chk_enabled)

        layout.addWidget(self.common_frame)

        # Stacked Parameter Forms
        self.stacked_widget = QStackedWidget()

        # Page 0: Empty state
        self.page_empty = QLabel("No action selected.\nClick an action card in the scenario builder to edit its parameters.")
        self.page_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_empty.setStyleSheet("color: #888888; font-size: 13px; padding: 40px;")
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

        # Validation status label
        self.lbl_status = QLabel("✓ Valid")
        self.lbl_status.setStyleSheet("color: #2E7D32; font-weight: bold;")
        layout.addWidget(self.lbl_status)

    def _on_pick_requested(self) -> None:
        self.pick_coordinate_requested.emit()
        self.coordinate_pick_requested.emit()

    def set_action(self, action: Optional[ActionBlock]) -> None:
        """Loads ActionBlock into inspector forms."""
        self._block_updates = True
        self.current_action = action

        if action is None:
            self.common_frame.hide()
            self.stacked_widget.setCurrentWidget(self.page_empty)
            self.lbl_status.hide()
            self._block_updates = False
            return

        self.common_frame.show()
        self.lbl_status.show()
        self.edit_description.setText(action.description)
        self.chk_enabled.setChecked(action.enabled)

        act_type = action.type if isinstance(action.type, ActionType) else ActionType.from_str(str(action.type))

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
        if self.current_action:
            act_type = self.current_action.type if isinstance(self.current_action.type, ActionType) else ActionType.from_str(str(self.current_action.type))
            if act_type == ActionType.MOUSE_CLICK:
                self.form_mouse.set_coordinates(x, y)
                self._commit_changes()

    def _on_common_changed(self) -> None:
        if self._block_updates or not self.current_action:
            return
        self.current_action.description = self.edit_description.text().strip()
        self.current_action.enabled = self.chk_enabled.isChecked()
        self.action_modified.emit(self.current_action)
        self.block_updated.emit(self.current_action)

    def _commit_changes(self) -> None:
        if self._block_updates or not self.current_action:
            return

        valid = self._validate_and_update_status()
        if not valid:
            return

        act_type = self.current_action.type if isinstance(self.current_action.type, ActionType) else ActionType.from_str(str(self.current_action.type))

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
        act_type = self.current_action.type if isinstance(self.current_action.type, ActionType) else ActionType.from_str(str(self.current_action.type))

        if act_type == ActionType.IMAGE_WAIT:
            path = self.form_image.edit_path.text().strip()
            if not path:
                valid = False
                err_msg = "Template image path cannot be empty"
        elif act_type == ActionType.KEY_INPUT:
            keys = self.form_key.edit_keys.text().strip()
            parsed_keys = parse_key_combo(keys)
            if not parsed_keys:
                valid = False
                err_msg = "Invalid key combination (e.g., Ctrl+C or Enter)"

        if valid:
            self.lbl_status.setText("✓ Parameters valid")
            self.lbl_status.setStyleSheet("color: #2E7D32; font-weight: bold;")
        else:
            self.lbl_status.setText(f"⚠ {err_msg}")
            self.lbl_status.setStyleSheet("color: #D32F2F; font-weight: bold;")

        self.validation_state_changed.emit(valid)
        return valid
