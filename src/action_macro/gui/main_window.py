"""
action_macro.gui.main_window
================================
Main application window integrating ScenarioBuilder, PropertyInspector,
ExecutionDashboard, LogConsole, and ScenarioPersistence.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QToolBar,
    QWidget,
)

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    ScenarioModel,
)
from action_macro.core.persistence import ScenarioPersistence
from action_macro.gui.coordinate_picker import CoordinatePickerOverlay
from action_macro.gui.dashboard import ExecutionDashboard
from action_macro.gui.log_console import LogConsole
from action_macro.gui.property_inspector import PropertyInspector
from action_macro.gui.scenario_builder import ScenarioBuilder
from action_macro.runner.execution_engine import ExecutionEngine
from action_macro.runner.hotkey_listener import GlobalHotkeyListener
from action_macro.runner.state_machine import EngineState


class MainWindow(QMainWindow):
    """Main application window for ActionMacro Desktop Tool."""

    def __init__(
        self,
        scenario: Optional[ScenarioModel] = None,
        scenario_builder_widget: Optional[QWidget] = None,
        property_inspector_widget: Optional[QWidget] = None,
        coordinate_picker_factory: Optional[object] = None,
        input_driver: Optional[object] = None,
        screen_driver: Optional[object] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("ActionMacro")
        self.resize(1200, 800)

        # Dependency injections & drivers
        self.input_driver = input_driver
        self.screen_driver = screen_driver
        self.coordinate_picker_factory = coordinate_picker_factory

        # Scenario and dirty state
        self.scenario: ScenarioModel = scenario if scenario is not None else ScenarioModel(name="Untitled Scenario")
        self.current_file_path: Optional[Path] = None
        self._is_dirty: bool = False

        # Execution runner & thread management
        self.engine: Optional[ExecutionEngine] = None
        self.hotkey_listener: Optional[GlobalHotkeyListener] = None
        self._worker_thread: Optional[object] = None
        self._builder_action_started_cb: Optional[Any] = None
        self._builder_execution_finished_cb: Optional[Any] = None
        self._runner_signals_connected: bool = False

        # Build UI
        self._setup_subwidgets(scenario_builder_widget, property_inspector_widget)
        self._setup_menus()
        self._setup_toolbar()
        self._setup_statusbar()
        self._setup_signals()

        self._update_window_title()

    def _setup_subwidgets(
        self,
        custom_builder: Optional[QWidget],
        custom_inspector: Optional[QWidget],
    ) -> None:
        # Central Horizontal Splitter
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: Scenario Builder
        if custom_builder is not None:
            self.scenario_builder = custom_builder
        else:
            self.scenario_builder = ScenarioBuilder(self.scenario)
        main_splitter.addWidget(self.scenario_builder)

        # Right: Vertical Splitter (Inspector, Dashboard, Console)
        right_splitter = QSplitter(Qt.Orientation.Vertical)

        # Top Right: Property Inspector
        if custom_inspector is not None:
            self.property_inspector = custom_inspector
        else:
            self.property_inspector = PropertyInspector()
        right_splitter.addWidget(self.property_inspector)

        # Middle Right: Execution Dashboard
        self.dashboard = ExecutionDashboard()
        self.dashboard.set_loop_count(self.scenario.loop_count)
        right_splitter.addWidget(self.dashboard)

        # Bottom Right: Log Console
        self.log_console = LogConsole()
        right_splitter.addWidget(self.log_console)

        # Splitter sizing ratios
        right_splitter.setStretchFactor(0, 3)
        right_splitter.setStretchFactor(1, 3)
        right_splitter.setStretchFactor(2, 4)

        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 5)
        main_splitter.setStretchFactor(1, 5)

        self.setCentralWidget(main_splitter)

    def _setup_menus(self) -> None:
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("&File")

        self.act_new = QAction("&New Scenario", self)
        self.act_new.setShortcut(QKeySequence.StandardKey.New)
        self.act_new.triggered.connect(self.new_scenario)
        file_menu.addAction(self.act_new)

        self.act_open = QAction("&Open Scenario...", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_scenario)
        file_menu.addAction(self.act_open)

        self.act_save = QAction("&Save Scenario", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save.triggered.connect(self.save_scenario)
        file_menu.addAction(self.act_save)

        self.act_save_as = QAction("Save &As...", self)
        self.act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.act_save_as.triggered.connect(self.save_scenario_as)
        file_menu.addAction(self.act_save_as)

        file_menu.addSeparator()

        self.act_exit = QAction("E&xit", self)
        self.act_exit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_exit.triggered.connect(self.close)
        file_menu.addAction(self.act_exit)

        # Run Menu
        run_menu = menubar.addMenu("&Run")

        self.act_run = QAction("&Start Execution", self)
        self.act_run.setShortcut(QKeySequence("F5"))
        self.act_run.triggered.connect(self.start_macro)
        run_menu.addAction(self.act_run)

        self.act_pause = QAction("&Pause / Resume", self)
        self.act_pause.setShortcut(QKeySequence("F6"))
        self.act_pause.triggered.connect(self.pause_resume_macro)
        run_menu.addAction(self.act_pause)

        self.act_stop = QAction("&Emergency Stop", self)
        self.act_stop.setShortcut(QKeySequence("F12"))
        self.act_stop.triggered.connect(self.stop_macro)
        run_menu.addAction(self.act_stop)

        # Tools Menu
        tools_menu = menubar.addMenu("&Tools")
        self.act_picker = QAction("&Screen Coordinate Picker", self)
        self.act_picker.setShortcut(QKeySequence("Ctrl+P"))
        self.act_picker.triggered.connect(self.trigger_coordinate_picker)
        tools_menu.addAction(self.act_picker)

    def _setup_toolbar(self) -> None:
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction(self.act_new)
        toolbar.addAction(self.act_open)
        toolbar.addAction(self.act_save)
        toolbar.addSeparator()
        toolbar.addAction(self.act_picker)
        toolbar.addSeparator()
        toolbar.addAction(self.act_run)
        toolbar.addAction(self.act_pause)
        toolbar.addAction(self.act_stop)

    def _setup_statusbar(self) -> None:
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

    def _setup_signals(self) -> None:
        # Dashboard controls
        self.dashboard.start_requested.connect(self.start_macro)
        self.dashboard.pause_requested.connect(self.pause_macro)
        self.dashboard.resume_requested.connect(self.resume_macro)
        self.dashboard.stop_requested.connect(self.stop_macro)
        self.dashboard.loop_count_changed.connect(self._on_dashboard_loop_count_changed)

        # Connect ScenarioBuilder & PropertyInspector
        if hasattr(self.scenario_builder, "block_selected"):
            self.scenario_builder.block_selected.connect(self._on_builder_block_selected)
        if hasattr(self.scenario_builder, "scenario_changed"):
            self.scenario_builder.scenario_changed.connect(self.mark_dirty)
        elif hasattr(self.scenario_builder, "model_changed"):
            self.scenario_builder.model_changed.connect(self.mark_dirty)

        if hasattr(self.property_inspector, "action_modified"):
            self.property_inspector.action_modified.connect(self._on_inspector_block_updated)
        elif hasattr(self.property_inspector, "block_updated"):
            self.property_inspector.block_updated.connect(self._on_inspector_block_updated)

        if hasattr(self.property_inspector, "pick_coordinate_requested"):
            self.property_inspector.pick_coordinate_requested.connect(self.trigger_coordinate_picker)
        elif hasattr(self.property_inspector, "coordinate_pick_requested"):
            self.property_inspector.coordinate_pick_requested.connect(self.trigger_coordinate_picker)

    # -------------------------------------------------------------------------
    # Dirty Tracking & Window Title
    # -------------------------------------------------------------------------

    def mark_dirty(self, dirty: bool = True) -> None:
        """Sets the dirty modification flag and updates the window title."""
        self._is_dirty = dirty
        self._update_window_title()

    def _update_window_title(self) -> None:
        dirty_flag = " *" if self._is_dirty else ""
        file_str = f" - {self.current_file_path.name}" if self.current_file_path else f" - {self.scenario.name}"
        self.setWindowTitle(f"ActionMacro{file_str}{dirty_flag}")

    def confirm_discard_changes(self) -> bool:
        """Prompts user to save unsaved modifications. Returns True to proceed."""
        if not self._is_dirty:
            return True

        reply = QMessageBox.question(
            self,
            "Unsaved Changes",
            "The current scenario has unsaved modifications.\nDo you want to save your changes?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

        if reply == QMessageBox.StandardButton.Save:
            return self.save_scenario()
        elif reply == QMessageBox.StandardButton.Discard:
            return True
        else:
            return False

    def closeEvent(self, event: QCloseEvent) -> None:
        """Safely cleans up runner threads and confirms unsaved changes on close."""
        if not self.confirm_discard_changes():
            event.ignore()
            return

        self._cleanup_runner()
        event.accept()

    # -------------------------------------------------------------------------
    # File Operations (ScenarioPersistence)
    # -------------------------------------------------------------------------

    @Slot()
    def new_scenario(self) -> None:
        """Creates a new blank scenario."""
        if not self.confirm_discard_changes():
            return

        self.scenario = ScenarioModel(name="Untitled Scenario")
        self.current_file_path = None
        self.dashboard.set_loop_count(self.scenario.loop_count)

        if hasattr(self.scenario_builder, "set_scenario"):
            self.scenario_builder.set_scenario(self.scenario)

        self.mark_dirty(False)
        self.log_console.append_log("INFO", "Created new blank scenario")
        self.status_bar.showMessage("New scenario created", 3000)

    @Slot()
    def open_scenario(self, file_path: Optional[str] = None) -> bool:
        """Loads a scenario from a JSON file."""
        if not self.confirm_discard_changes():
            return False

        if not file_path:
            file_name, _ = QFileDialog.getOpenFileName(
                self,
                "Open Scenario",
                "",
                "JSON Scenarios (*.json);;All Files (*)",
            )
            if not file_name:
                return False
            file_path = file_name

        path = Path(file_path).resolve()
        try:
            loaded = ScenarioPersistence.load(path)
            self.scenario = loaded
            self.current_file_path = path
            self.dashboard.set_loop_count(loaded.loop_count)

            if hasattr(self.scenario_builder, "set_scenario"):
                self.scenario_builder.set_scenario(loaded)

            self.mark_dirty(False)
            self.log_console.append_log("INFO", f"Loaded scenario: {path.name}")
            self.status_bar.showMessage(f"Loaded: {path.name}", 3000)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to load scenario:\n{exc}")
            self.log_console.append_log("ERROR", f"Failed to load scenario: {exc}")
            return False

    @Slot()
    def save_scenario(self) -> bool:
        """Saves scenario to current file path or invokes Save As."""
        if self.current_file_path is None:
            return self.save_scenario_as()

        try:
            self._sync_model_from_views()
            ScenarioPersistence.save(self.scenario, self.current_file_path)
            self.mark_dirty(False)
            self.log_console.append_log("INFO", f"Saved scenario to {self.current_file_path.name}")
            self.status_bar.showMessage(f"Saved: {self.current_file_path.name}", 3000)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Failed to save scenario:\n{exc}")
            self.log_console.append_log("ERROR", f"Failed to save scenario: {exc}")
            return False

    @Slot()
    def save_scenario_as(self) -> bool:
        """Prompts for target path and saves scenario."""
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save Scenario As",
            str(self.current_file_path) if self.current_file_path else "scenario.json",
            "JSON Scenarios (*.json);;All Files (*)",
        )
        if not file_name:
            return False

        path = Path(file_name).resolve()
        self.current_file_path = path
        return self.save_scenario()

    def _sync_model_from_views(self) -> None:
        """Synchronizes dashboard configuration into the scenario model before saving."""
        self.scenario.loop_count = self.dashboard.get_effective_loop_count()

    # -------------------------------------------------------------------------
    # Screen Coordinate Picker Integration
    # -------------------------------------------------------------------------

    @Slot()
    def trigger_coordinate_picker(self) -> None:
        """Launches the built-in screen coordinate picker overlay."""
        self.log_console.append_log("INFO", "Coordinate Picker activated (Click to capture, ESC to cancel)")

        # Clean up any existing active picker overlay
        if hasattr(self, "_active_picker_overlay") and self._active_picker_overlay is not None:
            try:
                self._active_picker_overlay.close_all()
            except Exception:
                pass
            self._active_picker_overlay = None

        if self.coordinate_picker_factory is not None:
            self._active_picker_overlay = self.coordinate_picker_factory(self)
        else:
            self._active_picker_overlay = CoordinatePickerOverlay(
                parent=None,
                screen_adapter=self.screen_driver,
                input_adapter=self.input_driver,
            )

        if self._active_picker_overlay is not None:
            if hasattr(self._active_picker_overlay, "coordinate_selected"):
                self._active_picker_overlay.coordinate_selected.connect(self.on_coordinate_picked)
            if hasattr(self._active_picker_overlay, "cancelled"):
                self._active_picker_overlay.cancelled.connect(self._on_picker_cancelled)
            if hasattr(self._active_picker_overlay, "start"):
                self._active_picker_overlay.start()
            else:
                self._active_picker_overlay.show()
        else:
            self.log_console.append_log("WARNING", "CoordinatePickerOverlay not available")

    @Slot()
    def _on_picker_cancelled(self) -> None:
        self.log_console.append_log("INFO", "Coordinate Picker cancelled.")
        self._active_picker_overlay = None

    @Slot(int, int)
    def on_coordinate_picked(self, x: int, y: int) -> None:
        """Captures coordinates from picker and routes them to PropertyInspector."""
        self.log_console.append_log("ACTION", f"Captured screen coordinates: ({x}, {y})")
        if hasattr(self.property_inspector, "set_coordinates"):
            self.property_inspector.set_coordinates(x, y)
        self.mark_dirty(True)
        self._active_picker_overlay = None

    # -------------------------------------------------------------------------
    # Execution Runner & Threading Integration
    # -------------------------------------------------------------------------

    @Slot()
    def start_macro(self, loop_count: Optional[int] = None) -> None:
        """Starts macro execution in a background worker thread."""
        worker_alive = (
            self._worker_thread is not None
            and hasattr(self._worker_thread, "is_alive")
            and self._worker_thread.is_alive()
        )
        engine_active = self.engine is not None and not self.engine.is_stopped()
        if worker_alive or engine_active:
            self.log_console.append_log("WARNING", "Macro execution is already in progress.")
            return

        self._sync_model_from_views()

        if len(self.scenario.actions) == 0:
            QMessageBox.warning(self, "Empty Scenario", "Scenario contains no action blocks to execute.")
            return

        effective_loops = loop_count if loop_count is not None else self.dashboard.get_effective_loop_count()
        run_scenario = self.scenario.clone() if hasattr(self.scenario, "clone") else self.scenario
        run_scenario.loop_count = effective_loops

        # Instantiate engine
        base_dir = self.current_file_path.parent if self.current_file_path else None
        self.engine = ExecutionEngine(
            scenario=run_scenario,
            input_driver=self.input_driver,
            screen_driver=self.screen_driver,
            base_dir=base_dir,
        )

        # Wire ExecutionSignals to GUI slots
        sig = self.engine.signals
        sig.state_changed.connect(self.dashboard.set_state, Qt.ConnectionType.QueuedConnection)
        sig.progress_updated.connect(self.dashboard.update_progress, Qt.ConnectionType.QueuedConnection)
        sig.action_started.connect(self.dashboard.on_action_started, Qt.ConnectionType.QueuedConnection)
        sig.log_emitted.connect(self.log_console.append_log, Qt.ConnectionType.QueuedConnection)
        sig.execution_finished.connect(self.dashboard.on_execution_finished, Qt.ConnectionType.QueuedConnection)
        sig.execution_finished.connect(self._on_execution_finished, Qt.ConnectionType.QueuedConnection)

        # Highlight active card in ScenarioBuilder if supported
        def _on_builder_action_started(idx: int, _: str) -> None:
            try:
                if hasattr(self.scenario_builder, "set_active_action"):
                    self.scenario_builder.set_active_action(idx)
                elif hasattr(self.scenario_builder, "highlight_active_action"):
                    self.scenario_builder.highlight_active_action(idx)
            except Exception:
                pass

        def _on_builder_execution_finished(*_: Any) -> None:
            try:
                if hasattr(self.scenario_builder, "clear_active_action"):
                    self.scenario_builder.clear_active_action()
                elif hasattr(self.scenario_builder, "highlight_active_action"):
                    self.scenario_builder.highlight_active_action(-1)
            except Exception:
                pass

        self._builder_action_started_cb = _on_builder_action_started
        self._builder_execution_finished_cb = _on_builder_execution_finished

        if self._builder_action_started_cb is not None:
            sig.action_started.connect(
                self._builder_action_started_cb,
                Qt.ConnectionType.QueuedConnection,
            )
        if self._builder_execution_finished_cb is not None:
            sig.execution_finished.connect(
                self._builder_execution_finished_cb,
                Qt.ConnectionType.QueuedConnection,
            )
        self._runner_signals_connected = True

        # Start emergency hotkey listener
        try:
            self.hotkey_listener = GlobalHotkeyListener(engine=self.engine)
            self.hotkey_listener.start()
        except Exception as err:
            self.log_console.append_log("WARNING", f"Hotkey listener could not start: {err}")

        # Launch engine in async worker thread
        self.log_console.append_log("INFO", f"Macro started (Loops: {effective_loops or 'Infinite'})")
        self._worker_thread = self.engine.start_async()

    @Slot()
    def pause_macro(self) -> None:
        if self.engine is not None:
            self.engine.pause()
            self.log_console.append_log("INFO", "Macro paused by user")

    @Slot()
    def resume_macro(self) -> None:
        if self.engine is not None:
            self.engine.resume()
            self.log_console.append_log("INFO", "Macro resumed by user")

    @Slot()
    def pause_resume_macro(self) -> None:
        if self.engine is not None:
            if self.engine.is_paused():
                self.resume_macro()
            elif self.engine.is_running():
                self.pause_macro()

    @Slot()
    def stop_macro(self) -> None:
        if self.engine is not None:
            self.engine.stop()
            self.log_console.append_log("WARNING", "Stop requested by user")

    @Slot(bool, str)
    def _on_execution_finished(self, success: bool, summary: str) -> None:
        self.log_console.append_log("INFO" if success else "WARNING", f"Macro finished: {summary}")
        self._cleanup_runner()

    def _cleanup_runner(self) -> None:
        # 1. Disconnect scenario_builder callbacks and all engine signals
        if getattr(self, "_runner_signals_connected", False) and self.engine is not None and hasattr(self.engine, "signals"):
            self._runner_signals_connected = False
            sig = self.engine.signals
            if getattr(self, "_builder_action_started_cb", None) is not None:
                try:
                    sig.action_started.disconnect(self._builder_action_started_cb)
                except Exception:
                    pass
                self._builder_action_started_cb = None

            if getattr(self, "_builder_execution_finished_cb", None) is not None:
                try:
                    sig.execution_finished.disconnect(self._builder_execution_finished_cb)
                except Exception:
                    pass
                self._builder_execution_finished_cb = None

            for signal_name in (
                "state_changed",
                "progress_updated",
                "action_started",
                "log_emitted",
                "execution_finished",
            ):
                signal_obj = getattr(sig, signal_name, None)
                if signal_obj is not None and hasattr(signal_obj, "disconnect"):
                    try:
                        signal_obj.disconnect()
                    except Exception:
                        pass

        # 2. Stop running or paused engine and wait for completion
        if self.engine is not None:
            try:
                self.engine.stop()
                self.engine.wait_for_completion(timeout=1.0)
            except Exception:
                pass

        if self._worker_thread is not None and hasattr(self._worker_thread, "join"):
            try:
                if hasattr(self._worker_thread, "is_alive") and self._worker_thread.is_alive():
                    self._worker_thread.join(timeout=1.0)
            except Exception:
                pass
            self._worker_thread = None

        # 3. Stop global hotkey listener
        if self.hotkey_listener is not None:
            try:
                self.hotkey_listener.stop()
            except Exception:
                pass
            self.hotkey_listener = None

    @Slot(int)
    def _on_dashboard_loop_count_changed(self, count: int) -> None:
        if self.scenario.loop_count != count:
            self.scenario.loop_count = count
            self.mark_dirty(True)

    @Slot(object)
    def _on_builder_block_selected(self, block: Optional[ActionBlock]) -> None:
        if hasattr(self.property_inspector, "set_action"):
            self.property_inspector.set_action(block)
        elif hasattr(self.property_inspector, "inspect_block"):
            self.property_inspector.inspect_block(block)

    @Slot(object)
    def _on_inspector_block_updated(self, block: ActionBlock) -> None:
        self.mark_dirty(True)
        if hasattr(self.scenario_builder, "update_action_block"):
            self.scenario_builder.update_action_block(block)
        elif hasattr(self.scenario_builder, "update_block_display"):
            self.scenario_builder.update_block_display(block)
