"""
action_macro.gui.dashboard
==============================
Execution controller of the main window.

``ExecutionDashboard`` *is* the editor **run bar** (repeat stepper, infinite
toggle, the violet Run button, Pause and Stop). It also builds the run read-outs
that live in the window's **status bar** (engine state pill, iteration and action
counters with thin progress bars) as :attr:`ExecutionDashboard.status_widget`,
which the main window places into its status bar.

Both halves are driven by the same slots (``set_state``, ``update_progress``,
``on_action_started``, ``on_execution_finished``), so the engine signals only have
to reach this one object.
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QWidget,
)

from action_macro.gui import icons, theme
from action_macro.gui.components import ElidedLabel, KeyHintButton, StatusDot, style_button
from action_macro.runner.state_machine import EngineState


class ExecutionDashboard(QWidget):
    """Run bar (controls) plus the status-bar run read-outs it owns."""

    start_requested = Signal(int)  # effective loop count (0 indicates infinite)
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()
    loop_count_changed = Signal(int)
    state_display_changed = Signal(str)  # lower-case state name, e.g. "running"

    #: Engine state -> text of the status pill.
    STATE_TEXT: Dict[EngineState, str] = {
        EngineState.IDLE: "IDLE",
        EngineState.RUNNING: "RUNNING",
        EngineState.PAUSED: "PAUSED",
        EngineState.STOPPING: "STOPPING",
        EngineState.STOPPED: "STOPPED",
        EngineState.ERROR: "ERROR",
    }

    #: Run button tooltip while the scenario can be run.
    RUN_TOOLTIP = "Run the scenario with the repeat count (F5)"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("RunBar")
        self._current_state = EngineState.IDLE
        self._runnable = True
        self._not_runnable_reason = ""
        self._setup_run_bar()
        self._setup_status_widget()
        self.set_state(EngineState.IDLE)

    # -------------------------------------------------------------------------
    # UI construction
    # -------------------------------------------------------------------------

    def _setup_run_bar(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)

        # Repeat stepper: [Repeat | 1 ^v]
        self.repeat_group = QFrame()
        self.repeat_group.setObjectName("RepeatGroup")
        self.repeat_group.setFixedHeight(theme.CONTROL_HEIGHT)
        self.repeat_group.setToolTip("How many times the whole scenario runs")
        group_layout = QHBoxLayout(self.repeat_group)
        group_layout.setContentsMargins(10, 1, 1, 1)
        group_layout.setSpacing(4)

        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap("repeat", theme.COLORS.text_muted, 14))
        group_layout.addWidget(icon_label)

        self.lbl_repeat = QLabel("Repeat")
        self.lbl_repeat.setProperty("role", "muted")
        group_layout.addWidget(self.lbl_repeat)

        self.spin_loops = QSpinBox()
        self.spin_loops.setRange(1, 999999)
        self.spin_loops.setValue(1)
        self.spin_loops.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.spin_loops.setToolTip("Repeat count")
        self.spin_loops.setAccessibleName("Repeat count")
        self.spin_loops.setFixedHeight(theme.CONTROL_HEIGHT - 2)
        self.spin_loops.setFixedWidth(64)
        self.spin_loops.valueChanged.connect(self._on_loop_config_changed)
        group_layout.addWidget(self.spin_loops)
        layout.addWidget(self.repeat_group)

        # Infinite toggle (checkable icon button)
        self.cb_infinite = QPushButton()
        self.cb_infinite.setObjectName("InfiniteToggle")
        self.cb_infinite.setCheckable(True)
        style_button(self.cb_infinite, "icon", height=theme.CONTROL_HEIGHT)
        self.cb_infinite.setFixedWidth(theme.CONTROL_HEIGHT)
        self.cb_infinite.setIcon(icons.icon(
            "infinity", theme.COLORS.text_muted, 16,
            active_color=theme.COLORS.text, checked_color=theme.COLORS.accent_text,
        ))
        self.cb_infinite.setIconSize(QSize(16, 16))
        self.cb_infinite.setToolTip("Repeat until stopped")
        self.cb_infinite.setAccessibleName("Repeat until stopped")
        self.cb_infinite.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.cb_infinite.toggled.connect(self._on_infinite_toggled)
        layout.addWidget(self.cb_infinite)

        layout.addSpacing(theme.SPACE_SM)

        # Run (primary), Pause, Stop
        self.btn_start = KeyHintButton("Run", hint="F5")
        self.btn_start.setObjectName("RunButton")
        style_button(self.btn_start, "primary", "play", icon_px=14)
        self.btn_start.setMinimumWidth(116)
        self.btn_start.setToolTip(self.RUN_TOOLTIP)
        self.btn_start.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_start.clicked.connect(self._on_start_clicked)
        layout.addWidget(self.btn_start)

        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setObjectName("PauseButton")
        style_button(self.btn_pause, "secondary", "pause", icon_px=14)
        self.btn_pause.setMinimumWidth(88)
        self.btn_pause.setToolTip("Pause or resume the run (F6)")
        self.btn_pause.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_pause.clicked.connect(self._on_pause_clicked)
        layout.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("StopButton")
        style_button(self.btn_stop, "secondary", "square", icon_px=12)
        self.btn_stop.setMinimumWidth(80)
        self.btn_stop.setToolTip("Stop the run (F12, Esc or Ctrl+Shift+S)")
        self.btn_stop.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_stop.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self.btn_stop)

        layout.addStretch(1)

    def _setup_status_widget(self) -> None:
        """Builds the status-bar read-outs; the main window re-parents them."""
        self.status_widget = QWidget(self)
        self.status_widget.setObjectName("RunStatus")
        self.status_widget.hide()  # not part of the run bar; shown once placed
        layout = QHBoxLayout(self.status_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)

        # State pill: (dot) RUNNING
        self.state_pill = QFrame()
        self.state_pill.setObjectName("StatePill")
        self.state_pill.setFixedHeight(18)
        pill_layout = QHBoxLayout(self.state_pill)
        pill_layout.setContentsMargins(8, 0, 9, 0)
        pill_layout.setSpacing(6)
        self.state_dot = StatusDot(theme.COLORS.text_muted, 7)
        pill_layout.addWidget(self.state_dot)
        self.banner_label = QLabel("IDLE")
        self.banner_label.setObjectName("StatePillText")
        pill_layout.addWidget(self.banner_label)
        layout.addWidget(self.state_pill)
        self.banner_frame = self.state_pill  # backwards-compatible alias

        layout.addSpacing(theme.SPACE_XS)

        self.loop_label = QLabel("Iteration: 0 / 1")
        self.loop_label.setProperty("role", "muted")
        layout.addWidget(self.loop_label)
        self.loop_bar = self._make_thin_bar("Iteration progress")
        layout.addWidget(self.loop_bar)

        layout.addSpacing(theme.SPACE_SM)

        self.action_label = ElidedLabel("Action: —")
        self.action_label.setProperty("role", "muted")
        self.action_label.setMaximumWidth(280)
        layout.addWidget(self.action_label)
        self.action_bar = self._make_thin_bar("Action progress")
        layout.addWidget(self.action_bar)

    @staticmethod
    def _make_thin_bar(name: str) -> QProgressBar:
        bar = QProgressBar()
        bar.setProperty("variant", "thin")
        bar.setRange(0, 1)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedWidth(64)
        bar.setAccessibleName(name)
        return bar

    # -------------------------------------------------------------------------
    # State & Signal Handling
    # -------------------------------------------------------------------------

    @Slot(str)
    def set_state(self, state: str) -> None:
        """Transitions the run bar and the status read-outs to ``state``."""
        try:
            self._current_state = EngineState.from_str(state)
        except Exception:
            self._current_state = EngineState.IDLE

        state_name = self._current_state.value.lower()
        self.banner_label.setText(self.STATE_TEXT.get(self._current_state, str(state)))
        theme.set_style_prop(self.state_pill, "state", state_name, recursive=True)
        self.state_dot.set_color(theme.state_color(state_name))

        # Button Enablement Matrix
        if self._current_state in (EngineState.IDLE, EngineState.STOPPED, EngineState.ERROR):
            self.btn_start.setEnabled(self._runnable)
            self.btn_pause.setEnabled(False)
            self._show_pause_as_resume(False)
            self.btn_stop.setEnabled(False)
            self.spin_loops.setEnabled(not self.cb_infinite.isChecked())
            self.cb_infinite.setEnabled(True)

        elif self._current_state == EngineState.RUNNING:
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(True)
            self._show_pause_as_resume(False)
            self.btn_stop.setEnabled(True)
            self.spin_loops.setEnabled(False)
            self.cb_infinite.setEnabled(False)

        elif self._current_state == EngineState.PAUSED:
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(True)
            self._show_pause_as_resume(True)
            self.btn_stop.setEnabled(True)
            self.spin_loops.setEnabled(False)
            self.cb_infinite.setEnabled(False)

        elif self._current_state == EngineState.STOPPING:
            self.btn_start.setEnabled(False)
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self.spin_loops.setEnabled(False)
            self.cb_infinite.setEnabled(False)

        # Stop turns into a red danger button only while it can actually stop a run.
        stop_variant = "danger" if self.btn_stop.isEnabled() else "secondary"
        if self.btn_stop.property("variant") != stop_variant:
            style_button(self.btn_stop, stop_variant, "square", icon_px=12)
            theme.repolish(self.btn_stop)

        self._update_run_tooltip()
        self.state_display_changed.emit(state_name)

    def is_runnable(self) -> bool:
        """True when the scenario has something to run (see :meth:`set_runnable`)."""
        return self._runnable

    def set_runnable(self, runnable: bool, reason: str = "") -> None:
        """Enables Run only when there is something to run.

        While not runnable (for example a scenario without steps) the Run button is
        disabled and its tooltip explains why with ``reason``. Running, paused and
        stopping states keep their own enablement rules.
        """
        self._runnable = bool(runnable)
        self._not_runnable_reason = reason
        if self._current_state in (EngineState.IDLE, EngineState.STOPPED, EngineState.ERROR):
            self.btn_start.setEnabled(self._runnable)
        self._update_run_tooltip()

    def _update_run_tooltip(self) -> None:
        if not self._runnable and self._not_runnable_reason:
            self.btn_start.setToolTip(self._not_runnable_reason)
        else:
            self.btn_start.setToolTip(self.RUN_TOOLTIP)

    def _show_pause_as_resume(self, paused: bool) -> None:
        self.btn_pause.setText("Resume" if paused else "Pause")
        style_button(self.btn_pause, "secondary", "play" if paused else "pause", icon_px=14)

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
        self._show_idle_iterations()
        self.loop_count_changed.emit(self.get_effective_loop_count())

    def _on_loop_config_changed(self, value: int) -> None:
        self._show_idle_iterations()
        self.loop_count_changed.emit(self.get_effective_loop_count())

    def _show_idle_iterations(self) -> None:
        """Before a run, show the configured repeat count in the iteration read-out."""
        if self._current_state in (EngineState.RUNNING, EngineState.PAUSED, EngineState.STOPPING):
            return
        total = self.get_effective_loop_count()
        if total == 0:
            self.loop_label.setText("Iteration: 0 (Infinite Loop)")
            self.loop_bar.setRange(0, 1)
        else:
            self.loop_label.setText(f"Iteration: 0 / {total}")
            self.loop_bar.setRange(0, total)
        self.loop_bar.setValue(0)
