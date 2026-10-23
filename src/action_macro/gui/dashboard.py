"""
action_macro.gui.dashboard
==============================
Mission control execution dashboard displaying state banner, dual progress bars,
and execution control buttons.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from action_macro.runner.state_machine import EngineState


class ExecutionDashboard(QWidget):
    """Macro execution controller dashboard widget."""

    start_requested = Signal(int)  # effective loop count (0 indicates infinite)
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    loop_count_changed = Signal(int)

    STATE_COLORS = {
        EngineState.IDLE: ("#37474F", "#546E7A", "#ECEFF1", "IDLE - READY"),
        EngineState.RUNNING: ("#1B5E20", "#4CAF50", "#E8F5E9", "RUNNING"),
        EngineState.PAUSED: ("#E65100", "#FFB74D", "#FFF3E0", "PAUSED"),
        EngineState.STOPPING: ("#BF360C", "#FF7043", "#FBE9E7", "STOPPING..."),
        EngineState.STOPPED: ("#3E2723", "#8D6E63", "#EFEBE9", "STOPPED"),
        EngineState.ERROR: ("#B71C1C", "#EF5350", "#FFEBEE", "ERROR"),
    }

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_state = EngineState.IDLE
        self._setup_ui()
        self.set_state(EngineState.IDLE)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # 1. State Banner Frame
        self.banner_frame = QFrame()
        self.banner_frame.setFrameShape(QFrame.Shape.StyledPanel)
        banner_layout = QHBoxLayout(self.banner_frame)
        banner_layout.setContentsMargins(12, 8, 12, 8)

        self.banner_label = QLabel("IDLE - READY")
        banner_font = self.banner_label.font()
        banner_font.setPointSize(13)
        banner_font.setBold(True)
        self.banner_label.setFont(banner_font)
        self.banner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        banner_layout.addWidget(self.banner_label)

        layout.addWidget(self.banner_frame)

        # 2. Progress GroupBox
        progress_group = QGroupBox("Execution Progress")
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setSpacing(4)

        # Loop Progress
        self.loop_label = QLabel("Iteration: 0 / 1")
        progress_layout.addWidget(self.loop_label)
        self.loop_bar = QProgressBar()
        self.loop_bar.setRange(0, 1)
        self.loop_bar.setValue(0)
        self.loop_bar.setTextVisible(True)
        progress_layout.addWidget(self.loop_bar)

        # Action Progress
        self.action_label = QLabel("Current Action: None")
        progress_layout.addWidget(self.action_label)
        self.action_bar = QProgressBar()
        self.action_bar.setRange(0, 1)
        self.action_bar.setValue(0)
        self.action_bar.setTextVisible(True)
        progress_layout.addWidget(self.action_bar)

        layout.addWidget(progress_group)

        # 3. Controls GroupBox
        controls_group = QGroupBox("Execution Controls")
        controls_layout = QVBoxLayout(controls_group)
        controls_layout.setSpacing(6)

        # Button Row
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_start = QPushButton("▶ Start")
        self.btn_start.setStyleSheet(
            "QPushButton { background-color: #2E7D32; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #388E3C; }"
            "QPushButton:disabled { background-color: #424242; color: #757575; }"
        )
        self.btn_start.clicked.connect(self._on_start_clicked)
        btn_layout.addWidget(self.btn_start)

        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_pause.setStyleSheet(
            "QPushButton { background-color: #F57F17; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #FBC02D; }"
            "QPushButton:disabled { background-color: #424242; color: #757575; }"
        )
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        btn_layout.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("⏹ Stop")
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #C62828; color: white; font-weight: bold; padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #D32F2F; }"
            "QPushButton:disabled { background-color: #424242; color: #757575; }"
        )
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        btn_layout.addWidget(self.btn_stop)

        controls_layout.addLayout(btn_layout)

        # Loop Configuration Row
        config_layout = QHBoxLayout()
        config_layout.addWidget(QLabel("Repeat Count:"))

        self.spin_loops = QSpinBox()
        self.spin_loops.setRange(1, 999999)
        self.spin_loops.setValue(1)
        self.spin_loops.valueChanged.connect(self._on_loop_config_changed)
        config_layout.addWidget(self.spin_loops)

        self.cb_infinite = QCheckBox("Infinite Loop")
        self.cb_infinite.toggled.connect(self._on_infinite_toggled)
        config_layout.addWidget(self.cb_infinite)

        config_layout.addStretch()
        controls_layout.addLayout(config_layout)

        # Emergency Stop Banner Callout
        self.hotkey_banner = QLabel("⚡ Emergency Stop: Press [ESC] or [Ctrl+Shift+S] to Abort")
        self.hotkey_banner.setStyleSheet(
            "QLabel {"
            "  background-color: #261E14;"
            "  color: #FFA726;"
            "  border: 1px solid #FF9800;"
            "  border-radius: 4px;"
            "  padding: 4px;"
            "  font-size: 11px;"
            "}"
        )
        self.hotkey_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        controls_layout.addWidget(self.hotkey_banner)

        layout.addWidget(controls_group)
        layout.addStretch()

    # -------------------------------------------------------------------------
    # State & Signal Handling
    # -------------------------------------------------------------------------

    @Slot(str)
    def set_state(self, state: str) -> None:
        """Transitions dashboard UI to the specified engine state."""
        try:
            self._current_state = EngineState.from_str(state)
        except Exception:
            self._current_state = EngineState.IDLE

        bg, border, text_color, display_text = self.STATE_COLORS.get(
            self._current_state, ("#37474F", "#546E7A", "#ECEFF1", str(state))
        )

        self.banner_frame.setStyleSheet(
            f"QFrame {{"
            f"  background-color: {bg};"
            f"  border: 2px solid {border};"
            f"  border-radius: 6px;"
            f"}}"
        )
        self.banner_label.setStyleSheet(f"color: {text_color};")
        self.banner_label.setText(display_text)

        # Button Enablement Matrix
        if self._current_state in (EngineState.IDLE, EngineState.STOPPED, EngineState.ERROR):
            self.btn_start.setEnabled(True)
            self.btn_pause.setEnabled(False)
            self.btn_pause.setText("⏸ Pause")
            self.btn_stop.setEnabled(False)
            self.spin_loops.setEnabled(not self.cb_infinite.isChecked())
            self.cb_infinite.setEnabled(True)

        elif self._current_state == EngineState.RUNNING:
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(True)
            self.btn_pause.setText("⏸ Pause")
            self.btn_stop.setEnabled(True)
            self.spin_loops.setEnabled(False)
            self.cb_infinite.setEnabled(False)

        elif self._current_state == EngineState.PAUSED:
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(True)
            self.btn_pause.setText("▶ Resume")
            self.btn_stop.setEnabled(True)
            self.spin_loops.setEnabled(False)
            self.cb_infinite.setEnabled(False)

        elif self._current_state == EngineState.STOPPING:
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.spin_loops.setEnabled(False)
            self.cb_infinite.setEnabled(False)

    @Slot(int, int, int, int)
    def update_progress(self, cur_loop: int, tot_loops: int, cur_act: int, tot_acts: int) -> None:
        """Updates loop and action progress displays."""
        # Loop progress
        if tot_loops == 0:
            self.loop_label.setText(f"Iteration: {cur_loop} (Infinite Loop)")
            self.loop_bar.setRange(0, 0)  # Indeterminate pulse
        else:
            self.loop_label.setText(f"Iteration: {cur_loop} / {tot_loops}")
            self.loop_bar.setRange(0, tot_loops)
            self.loop_bar.setValue(cur_loop)

        # Action progress
        self.action_label.setText(f"Action: {cur_act} / {tot_acts}")
        self.action_bar.setRange(0, tot_acts)
        self.action_bar.setValue(cur_act)

    @Slot(int, str)
    def on_action_started(self, idx: int, desc: str) -> None:
        """Updates current action description label."""
        self.action_label.setText(f"Action {idx + 1}: {desc}")

    @Slot(bool, str)
    def on_execution_finished(self, success: bool, summary: str) -> None:
        """Handles scenario completion or cancellation."""
        if self._current_state != EngineState.ERROR:
            self.set_state(EngineState.IDLE if success else EngineState.STOPPED)

    # -------------------------------------------------------------------------
    # UI Event Triggers
    # -------------------------------------------------------------------------

    def get_effective_loop_count(self) -> int:
        """Returns 0 if infinite loop is checked, otherwise the spinbox value."""
        return 0 if self.cb_infinite.isChecked() else self.spin_loops.value()

    def set_loop_count(self, count: int) -> None:
        """Configures loop count from external scenario model."""
        if count <= 0:
            self.cb_infinite.setChecked(True)
            self.spin_loops.setEnabled(False)
        else:
            self.cb_infinite.setChecked(False)
            self.spin_loops.setEnabled(True)
            self.spin_loops.setValue(count)

    def _on_start_clicked(self) -> None:
        self.start_requested.emit(self.get_effective_loop_count())

    def _on_pause_clicked(self) -> None:
        if self._current_state == EngineState.PAUSED:
            self.resume_requested.emit()
        else:
            self.pause_requested.emit()

    def _on_stop_clicked(self) -> None:
        self.stop_requested.emit()

    def _on_infinite_toggled(self, checked: bool) -> None:
        self.spin_loops.setEnabled(not checked)
        self.loop_count_changed.emit(self.get_effective_loop_count())

    def _on_loop_config_changed(self, value: int) -> None:
        self.loop_count_changed.emit(self.get_effective_loop_count())
