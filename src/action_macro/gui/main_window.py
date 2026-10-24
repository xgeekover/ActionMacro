"""
action_macro.gui.main_window
================================
Main application window integrating ScenarioBuilder, PropertyInspector,
ExecutionDashboard, LogConsole, the scenario sidebar and ScenarioPersistence.

Layout (Postman-style workspace with a Docker-style status bar)::

    +--------------------------------------------------------------------+
    | TopBar: mark [menus] | file name + save state    [Permissions] [...] |
    +----+-------------+--------------------------------+----------------+
    |rail| Sidebar     | EditorHeader: name / run bar    | Inspector      |
    |    | (recent     | ScenarioBuilder (step list)     | (scrollable)   |
    |    |  scenarios) +--------------------------------+----------------+
    |    |             | Console (LogConsole, collapsible)                |
    +----+-------------+-------------------------------------------------+
    | status: state pill, iteration/action progress, message, stop hint   |
    +--------------------------------------------------------------------+

The menu bar and its QActions stay. On macOS the menus live in the screen's global
menu bar; on Windows/Linux they are shown inside the top bar (one header row).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QSettings, QSize, Qt, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QFont, QFontMetrics, QKeySequence, QShowEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from action_macro import __version__
from action_macro.core.models import (
    ActionBlock,
    ActionType,
    ScenarioModel,
)
from action_macro.core.persistence import ScenarioPersistence, resolve_template_path, to_posix_relative_path
from action_macro.engine import macos_permissions
from action_macro.gui import icons, step_text, theme
from action_macro.gui.chrome import NavRail, TopBar
from action_macro.gui.components import (
    ElidedLabel,
    InlineEdit,
    Separator,
    StatusDot,
    shortcut_text,
    style_button,
    with_shortcut,
)
from action_macro.gui.coordinate_picker import CoordinatePickerOverlay
from action_macro.gui.dashboard import ExecutionDashboard
from action_macro.gui.log_console import LogConsole
from action_macro.gui.permissions_panel import PermissionMonitor, PermissionsPanel, UrlOpener
from action_macro.gui.property_inspector import PropertyInspector
from action_macro.gui.scenario_builder import ScenarioBuilder
from action_macro.gui.sidebar import RecentScenarioStore, ScenarioSidebar
from action_macro.runner.execution_engine import ExecutionEngine
from action_macro.runner.hotkey_listener import GlobalHotkeyListener
from action_macro.runner.state_machine import EngineState


class MainWindow(QMainWindow):
    """Main application window for ActionMacro Desktop Tool."""

    #: Emitted when the user clicks the top-bar permissions warning.
    permissions_requested = Signal()

    #: Run button tooltip while the scenario has no steps.
    NO_STEPS_HINT = "Add a step to run this scenario"
    #: Engine states (lower-case) during which a run is in progress.
    ACTIVE_RUN_STATES = ("running", "paused", "stopping")

    def __init__(
        self,
        scenario: Optional[ScenarioModel] = None,
        scenario_builder_widget: Optional[QWidget] = None,
        property_inspector_widget: Optional[QWidget] = None,
        coordinate_picker_factory: Optional[object] = None,
        input_driver: Optional[object] = None,
        screen_driver: Optional[object] = None,
        parent: Optional[QWidget] = None,
        settings: Optional[QSettings] = None,
        permission_checker: Optional[macos_permissions.PermissionChecker] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("ActionMacro")
        self.setWindowIcon(icons.app_icon())
        self.resize(1400, 900)
        self.setMinimumSize(1100, 680)
        self._initial_focus_done = False

        # Dependency injections & drivers
        self.input_driver = input_driver
        self.screen_driver = screen_driver
        self.coordinate_picker_factory = coordinate_picker_factory

        # Scenario and dirty state
        self.scenario: ScenarioModel = scenario if scenario is not None else ScenarioModel(name="Untitled Scenario")
        self.current_file_path: Optional[Path] = None
        self._is_dirty: bool = False
        self._name_source: Optional[ScenarioModel] = None  # scenario shown in the name field

        # Execution runner & thread management
        self.engine: Optional[ExecutionEngine] = None
        self.hotkey_listener: Optional[GlobalHotkeyListener] = None
        self._worker_thread: Optional[object] = None
        self._builder_action_started_cb: Optional[Any] = None
        self._builder_execution_finished_cb: Optional[Any] = None
        self._runner_signals_connected: bool = False
        self._run_actions: list = []  # steps of the running scenario copy (status bar text)

        # Recent scenario files (QSettings; injectable so tests stay isolated)
        self.recent_store = RecentScenarioStore(settings)
        self._sidebar_document: Optional[tuple] = None

        # Build UI
        self._setup_subwidgets(scenario_builder_widget, property_inspector_widget)
        self._setup_menus()
        self._setup_chrome()
        self._setup_statusbar()
        self._setup_signals()
        self._setup_permissions(permission_checker)
        self._sync_initial_selection()

        self._update_window_title()

    # -------------------------------------------------------------------------
    # Layout
    # -------------------------------------------------------------------------

    def _setup_subwidgets(
        self,
        custom_builder: Optional[QWidget],
        custom_inspector: Optional[QWidget],
    ) -> None:
        # Core widgets (public names are part of the compatibility contract)
        if custom_builder is not None:
            self.scenario_builder = custom_builder
        else:
            self.scenario_builder = ScenarioBuilder(self.scenario)

        if custom_inspector is not None:
            self.property_inspector = custom_inspector
        else:
            self.property_inspector = PropertyInspector()

        self.dashboard = ExecutionDashboard()
        self.dashboard.set_loop_count(self.scenario.loop_count)

        self.log_console = LogConsole()
        self.sidebar = ScenarioSidebar(self.recent_store)

        # Root: top bar above [rail | splitter]
        root = QWidget()
        root.setObjectName("AppRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.top_bar = TopBar()
        root_layout.addWidget(self.top_bar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self.nav_rail = NavRail()
        body_layout.addWidget(self.nav_rail)

        # Sidebar | work area
        self.main_splitter = self._make_splitter(Qt.Orientation.Horizontal, "MainSplitter")
        self.main_splitter.addWidget(self.sidebar)

        # Work area: (editor | inspector) above the console
        self.work_splitter = self._make_splitter(Qt.Orientation.Vertical, "WorkSplitter")
        self.editor_splitter = self._make_splitter(Qt.Orientation.Horizontal, "EditorSplitter")

        self.editor_panel = self._build_editor_panel()
        self.inspector_panel = self._build_inspector_panel()
        self.editor_splitter.addWidget(self.editor_panel)
        self.editor_splitter.addWidget(self.inspector_panel)

        self.console_panel = QFrame()
        self.console_panel.setObjectName("ConsolePanel")
        console_layout = QVBoxLayout(self.console_panel)
        # Same 16 px side gutter as the editor column, so "CONSOLE" lines up with "STEPS".
        console_layout.setContentsMargins(theme.SPACE_LG, theme.SPACE_SM, theme.SPACE_LG, theme.SPACE_SM)
        console_layout.setSpacing(0)
        console_layout.addWidget(self.log_console)
        # The console's own chevron hides the panel (same as the rail / Ctrl+J toggle).
        # A bound method, not a lambda: a lambda capturing self would keep the window alive.
        self.log_console.collapse_requested.connect(self._on_console_collapse_requested)

        self.work_splitter.addWidget(self.editor_splitter)
        self.work_splitter.addWidget(self.console_panel)
        self.main_splitter.addWidget(self.work_splitter)
        body_layout.addWidget(self.main_splitter, stretch=1)
        root_layout.addWidget(body, stretch=1)

        # Panel sizes: sidebar ~240, inspector ~340, console ~180 (all resizable)
        # (The editor keeps its layout minimum so the run bar is never squeezed.)
        self.sidebar.setMinimumWidth(200)
        self.inspector_panel.setMinimumWidth(300)
        self.console_panel.setMinimumHeight(110)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.editor_splitter.setStretchFactor(0, 1)
        self.editor_splitter.setStretchFactor(1, 0)
        self.work_splitter.setStretchFactor(0, 1)
        self.work_splitter.setStretchFactor(1, 0)
        self.main_splitter.setSizes([theme.SIDEBAR_WIDTH, 1400 - theme.RAIL_WIDTH - theme.SIDEBAR_WIDTH])
        work_width = 1400 - theme.RAIL_WIDTH - theme.SIDEBAR_WIDTH
        self.editor_splitter.setSizes([work_width - theme.INSPECTOR_WIDTH, theme.INSPECTOR_WIDTH])
        self.work_splitter.setSizes([900 - theme.CONSOLE_HEIGHT, theme.CONSOLE_HEIGHT])

        self.setCentralWidget(root)

    @staticmethod
    def _make_splitter(orientation: Qt.Orientation, name: str) -> QSplitter:
        splitter = QSplitter(orientation)
        splitter.setObjectName(name)
        splitter.setHandleWidth(1)
        splitter.setChildrenCollapsible(False)
        return splitter

    def _build_editor_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("EditorPanel")
        # Clicking empty editor space takes focus away from inputs (no stray focus rings).
        panel.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.editor_header = QFrame()
        self.editor_header.setObjectName("EditorHeader")
        header_layout = QVBoxLayout(self.editor_header)
        header_layout.setContentsMargins(theme.SPACE_LG, theme.SPACE_MD, theme.SPACE_LG, theme.SPACE_MD)
        header_layout.setSpacing(theme.SPACE_SM + 2)

        # Row 1: scenario name, dirty dot, step count ... Save
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(theme.SPACE_SM)

        # In-place rename: typing shows "unsaved" right away; Enter or leaving the field
        # applies the name, Esc puts the saved name back. Save, Run, New, Open and
        # closing the window apply a name that is still being typed (see
        # _commit_scenario_name_edit), so it is never silently lost.
        self.edit_scenario_name = InlineEdit(self.scenario.name)
        self.edit_scenario_name.setObjectName("ScenarioNameEdit")
        self.edit_scenario_name.setFont(theme.ui_font(theme.FONT_HEADER, QFont.Weight.DemiBold))
        self.edit_scenario_name.setPlaceholderText("Scenario name")
        self.edit_scenario_name.setToolTip("Scenario name (click to rename, Enter to apply, Esc to cancel)")
        self.edit_scenario_name.setAccessibleName("Scenario name")
        self.edit_scenario_name.editingFinished.connect(self._on_scenario_name_edited)
        self.edit_scenario_name.returnPressed.connect(self._leave_scenario_name_edit)
        self.edit_scenario_name.edit_cancelled.connect(self._on_scenario_name_edit_cancelled)
        self.edit_scenario_name.textEdited.connect(self._on_scenario_name_typed)
        self.edit_scenario_name.textChanged.connect(self._fit_scenario_name_width)
        title_row.addWidget(self.edit_scenario_name)
        self._name_source = self.scenario

        self.editor_dirty_dot = StatusDot(theme.COLORS.warning, 7)
        self.editor_dirty_dot.setToolTip("Unsaved changes")
        title_row.addWidget(self.editor_dirty_dot)

        # The step count is shown by the STEPS badge right below; this label keeps the
        # text (and serves as the name field's accessible description) but is not shown,
        # so the count is not repeated twice in a row.
        self.lbl_step_count = QLabel()
        self.lbl_step_count.setProperty("role", "muted")
        self.lbl_step_count.setVisible(False)
        title_row.addWidget(self.lbl_step_count)
        title_row.addStretch(1)

        self.btn_save = QPushButton("Save")
        style_button(self.btn_save, "ghost", "save", icon_px=14, height=theme.CONTROL_HEIGHT_SM)
        self.btn_save.setToolTip(with_shortcut("Save scenario", QKeySequence.StandardKey.Save))
        self.btn_save.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_save.clicked.connect(self._on_save_button_clicked)
        title_row.addWidget(self.btn_save)
        header_layout.addLayout(title_row)

        # Row 2: run bar (the dashboard's controls)
        header_layout.addWidget(self.dashboard)
        layout.addWidget(self.editor_header)

        # Step list
        layout.addWidget(self.scenario_builder, stretch=1)
        self._fit_scenario_name_width(self.edit_scenario_name.text())
        return panel

    def _build_inspector_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("InspectorPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.inspector_scroll = QScrollArea()
        self.inspector_scroll.setObjectName("InspectorScroll")
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inspector_scroll.setWidget(self.property_inspector)
        layout.addWidget(self.inspector_scroll)
        return panel

    def _setup_menus(self) -> None:
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("&File")

        self.act_new = QAction("&New Scenario", self)
        self.act_new.setShortcut(QKeySequence.StandardKey.New)
        self.act_new.setIcon(icons.icon("file-plus", theme.COLORS.text_muted, 16))
        self.act_new.triggered.connect(self.new_scenario)
        file_menu.addAction(self.act_new)

        self.act_open = QAction("&Open Scenario…", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.setIcon(icons.icon("folder-open", theme.COLORS.text_muted, 16))
        # Not open_scenario directly: QAction.triggered(bool checked) would land in
        # ``file_path``. A bound no-argument slot (not a lambda capturing self)
        # also avoids a reference cycle that would keep the window alive.
        self.act_open.triggered.connect(self._on_open_action_triggered)
        file_menu.addAction(self.act_open)

        self.act_save = QAction("&Save Scenario", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save.setIcon(icons.icon("save", theme.COLORS.text_muted, 16))
        self.act_save.triggered.connect(self.save_scenario)
        file_menu.addAction(self.act_save)

        self.act_save_as = QAction("Save &As…", self)
        self.act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.act_save_as.triggered.connect(self.save_scenario_as)
        file_menu.addAction(self.act_save_as)

        file_menu.addSeparator()

        self.act_exit = QAction("E&xit", self)
        self.act_exit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_exit.triggered.connect(self.close)
        file_menu.addAction(self.act_exit)

        # Edit Menu
        edit_menu = menubar.addMenu("&Edit")
        self.act_undo_delete = QAction("&Undo Delete Step", self)
        self.act_undo_delete.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo_delete.setToolTip("Put the last deleted step back")
        self.act_undo_delete.setEnabled(False)
        self.act_undo_delete.triggered.connect(self._on_undo_delete_triggered)
        edit_menu.addAction(self.act_undo_delete)

        # Run Menu
        run_menu = menubar.addMenu("&Run")

        self.act_run = QAction("&Start Execution", self)
        self.act_run.setShortcut(QKeySequence("F5"))
        self.act_run.setToolTip("Start execution using the dashboard Repeat Count (F5)")
        self.act_run.setIcon(icons.icon("play", theme.COLORS.text_muted, 16))
        # Not start_macro directly: QAction.triggered(bool checked) would land in
        # ``loop_count`` (False -> infinite loop). The no-argument slot starts with
        # the dashboard Repeat Count, exactly like the dashboard Start button.
        self.act_run.triggered.connect(self._on_run_action_triggered)
        run_menu.addAction(self.act_run)

        self.act_pause = QAction("&Pause / Resume", self)
        self.act_pause.setShortcut(QKeySequence("F6"))
        self.act_pause.setIcon(icons.icon("pause", theme.COLORS.text_muted, 16))
        self.act_pause.triggered.connect(self.pause_resume_macro)
        run_menu.addAction(self.act_pause)

        self.act_stop = QAction("&Emergency Stop", self)
        self.act_stop.setShortcut(QKeySequence("F12"))
        self.act_stop.setIcon(icons.icon("square", theme.COLORS.text_muted, 16))
        self.act_stop.triggered.connect(self.stop_macro)
        run_menu.addAction(self.act_stop)

        # Tools Menu
        tools_menu = menubar.addMenu("&Tools")
        # Same name as the inspector's "Pick from screen" button (and the picker's own hint).
        self.act_picker = QAction("&Pick from Screen", self)
        self.act_picker.setShortcut(QKeySequence("Ctrl+P"))
        self.act_picker.setIcon(icons.icon("crosshair", theme.COLORS.text_muted, 16))
        self.act_picker.triggered.connect(self.trigger_coordinate_picker)
        tools_menu.addAction(self.act_picker)

        # View Menu (panel toggles shared with the navigation rail)
        view_menu = menubar.addMenu("&View")
        self.act_toggle_sidebar = QAction("Scenarios &Sidebar", self)
        self.act_toggle_sidebar.setCheckable(True)
        self.act_toggle_sidebar.setChecked(True)
        self.act_toggle_sidebar.setShortcut(QKeySequence("Ctrl+B"))
        self.act_toggle_sidebar.setToolTip(with_shortcut("Scenarios", self.act_toggle_sidebar.shortcut()))
        self.act_toggle_sidebar.setIcon(self._rail_icon("files"))
        self.act_toggle_sidebar.toggled.connect(self.set_sidebar_visible)
        view_menu.addAction(self.act_toggle_sidebar)

        self.act_toggle_console = QAction("&Console", self)
        self.act_toggle_console.setCheckable(True)
        self.act_toggle_console.setChecked(True)
        self.act_toggle_console.setShortcut(QKeySequence("Ctrl+J"))
        self.act_toggle_console.setToolTip(with_shortcut("Console", self.act_toggle_console.shortcut()))
        self.act_toggle_console.setIcon(self._rail_icon("terminal"))
        self.act_toggle_console.toggled.connect(self.set_console_visible)
        view_menu.addAction(self.act_toggle_console)
        # The console's own chevron hides it too: name the same shortcut as the rail button.
        self.log_console.btn_collapse.setToolTip(with_shortcut("Hide console", self.act_toggle_console.shortcut()))

        # Help Menu
        help_menu = menubar.addMenu("&Help")
        self.act_about = QAction("&About ActionMacro", self)
        self.act_about.setMenuRole(QAction.MenuRole.AboutRole)
        self.act_about.triggered.connect(self.show_about)
        help_menu.addAction(self.act_about)

        self.act_clear_recent = QAction("Clear Recent Scenarios", self)
        self.act_clear_recent.triggered.connect(self._on_clear_recent_triggered)

        # "More" menu shared by the top bar and the rail: panel toggles, tools, About.
        # (Named "More", not "Settings": the app has no settings to change yet.)
        self.more_menu = QMenu("More", self)
        self.more_menu.addAction(self.act_toggle_sidebar)
        self.more_menu.addAction(self.act_toggle_console)
        self.more_menu.addSeparator()
        self.more_menu.addAction(self.act_picker)
        self.more_menu.addAction(self.act_clear_recent)
        self.more_menu.addSeparator()
        self.more_menu.addAction(self.act_about)

        self._place_menu_bar(menubar)

    def _place_menu_bar(self, menubar: QMenuBar) -> None:
        """Shows the menus inside the top bar when there is no global (native) menu bar.

        The window's own menu bar keeps owning the menus (``menuBar()`` still lists
        them) but is hidden, so it does not form a second header above the top bar.
        The inline bar shares the same QMenu objects, so every QAction and its
        shortcut keeps working.
        """
        self.inline_menu_bar: Optional[QMenuBar] = None
        if menubar.isNativeMenuBar():
            return
        inline = QMenuBar()
        for action in menubar.actions():
            inline.addAction(action)
        self.top_bar.set_menu_bar(inline)
        self.inline_menu_bar = inline
        menubar.hide()

    @staticmethod
    def _rail_icon(name: str):
        return icons.icon(name, theme.COLORS.text_muted, NavRail.ICON_PX,
                          active_color=theme.COLORS.text, checked_color=theme.COLORS.accent_text)

    def _setup_chrome(self) -> None:
        """Populates the navigation rail and the top bar with their actions."""
        self.rail_btn_scenarios = self.nav_rail.add_action(self.act_toggle_sidebar)
        self.rail_btn_console = self.nav_rail.add_action(self.act_toggle_console)
        self.nav_rail.add_stretch()
        self.rail_btn_more = self.nav_rail.add_menu_button("more-horizontal", "More", self.more_menu)

        self.top_bar.btn_more.setMenu(self.more_menu)
        self.top_bar.btn_permissions.clicked.connect(self._on_permissions_clicked)

    def _setup_statusbar(self) -> None:
        self.status_bar = QStatusBar()
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)

        content = QWidget()
        content.setObjectName("StatusContent")
        layout = QHBoxLayout(content)
        layout.setContentsMargins(theme.SPACE_MD, 0, theme.SPACE_SM, 0)
        layout.setSpacing(theme.SPACE_SM)

        # Engine state pill + iteration/action progress (owned by the dashboard)
        layout.addWidget(self.dashboard.status_widget)
        self.dashboard.status_widget.show()

        layout.addSpacing(theme.SPACE_XS)
        layout.addWidget(Separator(vertical=True, length=14))
        layout.addSpacing(theme.SPACE_XS)

        # Transient messages from QStatusBar.showMessage() (currentMessage() still works)
        self.lbl_status_message = ElidedLabel("")
        self.lbl_status_message.setProperty("role", "muted")
        layout.addWidget(self.lbl_status_message, stretch=1)

        # Emergency-stop hint: "Stop a run  [Esc] or [Ctrl+Shift+S]"
        hint_icon = QLabel()
        hint_icon.setPixmap(icons.pixmap("keyboard", theme.COLORS.text_muted, 14))
        layout.addWidget(hint_icon)
        hint_text = QLabel("Stop a run")
        hint_text.setProperty("role", "muted")
        layout.addWidget(hint_text)
        for index, key in enumerate(("Esc", "Ctrl+Shift+S")):
            if index:
                or_label = QLabel("or")
                or_label.setProperty("role", "muted")
                layout.addWidget(or_label)
            key_label = QLabel(key)
            key_label.setProperty("role", "kbd")
            layout.addWidget(key_label)
        self.stop_hint_widgets = hint_text

        layout.addSpacing(theme.SPACE_XS)
        layout.addWidget(Separator(vertical=True, length=14))

        # Console toggle (Postman-style footer button)
        self.btn_status_console = QToolButton()
        self.btn_status_console.setDefaultAction(self.act_toggle_console)
        self.btn_status_console.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_status_console.setIconSize(QSize(14, 14))
        self.btn_status_console.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_status_console.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_status_console.setFixedHeight(22)
        layout.addWidget(self.btn_status_console)

        self.lbl_version = QLabel(f"v{__version__}")
        self.lbl_version.setProperty("role", "muted")
        layout.addWidget(self.lbl_version)

        self.status_bar.addPermanentWidget(content, 1)
        self.status_bar.messageChanged.connect(self.lbl_status_message.setText)
        self.status_bar.showMessage("Ready")

    def _setup_signals(self) -> None:
        # Dashboard controls
        self.dashboard.start_requested.connect(self.start_macro)
        self.dashboard.pause_requested.connect(self.pause_macro)
        self.dashboard.resume_requested.connect(self.resume_macro)
        self.dashboard.stop_requested.connect(self.stop_macro)
        self.dashboard.loop_count_changed.connect(self._on_dashboard_loop_count_changed)
        self.dashboard.state_display_changed.connect(self._on_run_state_changed)

        # Connect ScenarioBuilder & PropertyInspector
        if hasattr(self.scenario_builder, "block_selected"):
            self.scenario_builder.block_selected.connect(self._on_builder_block_selected)
        if hasattr(self.scenario_builder, "action_enabled_changed"):
            self.scenario_builder.action_enabled_changed.connect(self._on_builder_action_enabled_changed)
        if hasattr(self.scenario_builder, "undo_delete_available"):
            self.scenario_builder.undo_delete_available.connect(self._sync_undo_delete_action)
        if hasattr(self.scenario_builder, "action_deleted"):
            self.scenario_builder.action_deleted.connect(self._on_builder_action_deleted)
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

        # Scenario sidebar
        self.sidebar.new_requested.connect(self.new_scenario)
        self.sidebar.open_requested.connect(self._on_open_action_triggered)
        self.sidebar.scenario_activated.connect(self._on_recent_scenario_activated)
        self.sidebar.missing_scenario_activated.connect(self._on_recent_scenario_missing)

    # -------------------------------------------------------------------------
    # Panels (navigation rail / View menu)
    # -------------------------------------------------------------------------

    @Slot(bool)
    def set_sidebar_visible(self, visible: bool) -> None:
        """Shows or hides the scenario sidebar (rail "Scenarios", Ctrl+B)."""
        self.sidebar.setVisible(bool(visible))
        if self.act_toggle_sidebar.isChecked() != bool(visible):
            self.act_toggle_sidebar.setChecked(bool(visible))

    @Slot(bool)
    def set_console_visible(self, visible: bool) -> None:
        """Shows or hides the bottom console panel (rail "Console", Ctrl+J)."""
        self.console_panel.setVisible(bool(visible))
        if self.act_toggle_console.isChecked() != bool(visible):
            self.act_toggle_console.setChecked(bool(visible))

    @Slot()
    def _on_console_collapse_requested(self) -> None:
        """The console header's chevron hides the console panel."""
        self.set_console_visible(False)

    def is_sidebar_visible(self) -> bool:
        """True when the scenario sidebar is shown (independent of window visibility)."""
        return not self.sidebar.isHidden()

    def is_console_visible(self) -> bool:
        """True when the console panel is shown (independent of window visibility)."""
        return not self.console_panel.isHidden()

    def set_permission_warning(self, message: Optional[str]) -> None:
        """Shows a permissions warning in the top bar (``None`` hides it)."""
        self.top_bar.set_permission_warning(message)

    @Slot()
    def _on_permissions_clicked(self) -> None:
        message = self.top_bar.btn_permissions.toolTip()
        if message:
            self.status_bar.showMessage(message, 6000)
        self.permissions_requested.emit()

    # -------------------------------------------------------------------------
    # macOS permissions (Accessibility, Input Monitoring, Screen Recording)
    # -------------------------------------------------------------------------

    def _setup_permissions(self, checker: Optional[macos_permissions.PermissionChecker]) -> None:
        """Checks the permissions now and whenever the app becomes active (throttled).

        A missing permission shows the amber "Permissions" button in the top bar;
        the button and More > Permissions… open :class:`PermissionsPanel`.
        """
        self.permissions_panel: Optional[PermissionsPanel] = None
        #: Opens System Settings URLs from the panel (None: QDesktopServices). Tests replace it.
        self.permissions_url_opener: Optional[UrlOpener] = None
        self.permission_monitor = PermissionMonitor(checker, parent=self)
        self.permission_monitor.report_changed.connect(self._on_permission_report)
        self.permissions_requested.connect(self.show_permissions_panel)

        self.act_permissions = QAction("Permissions…", self)
        self.act_permissions.setIcon(icons.icon("shield", theme.COLORS.text_muted, 16))
        self.act_permissions.setToolTip("macOS permissions ActionMacro needs")
        self.act_permissions.triggered.connect(self._on_permissions_action_triggered)
        self.more_menu.insertAction(self.act_about, self.act_permissions)

        self.permission_monitor.watch_application()
        self.permission_monitor.check()

    @Slot(object)
    def _on_permission_report(self, report: dict) -> None:
        missing = macos_permissions.missing_permissions(report)
        if missing:
            noun = "permission" if len(missing) == 1 else "permissions"
            self.set_permission_warning(
                f"Missing macOS {noun}: {macos_permissions.join_titles(missing)}. Click to review."
            )
        else:
            self.set_permission_warning(None)
        panel = self.permissions_panel
        if panel is not None and panel.isVisible():
            panel.set_report(report)

    @Slot()
    def _on_permissions_action_triggered(self) -> None:
        """More > Permissions…; drops QAction's ``checked`` argument."""
        self.show_permissions_panel()

    @Slot()
    def show_permissions_panel(self) -> PermissionsPanel:
        """Checks again and shows the Permissions panel (non-modal)."""
        report = self.permission_monitor.check()
        panel = self.permissions_panel
        if panel is None:
            panel = PermissionsPanel(checker=self.permission_monitor.check, report=report, parent=self)
            self.permissions_panel = panel
        else:
            panel.set_report(report)
        panel.set_opener(self.permissions_url_opener)
        panel.show()
        panel.raise_()
        panel.activateWindow()
        return panel

    def _warn_missing_run_permissions(self) -> None:
        """Before a run: warns when the run needs a macOS permission that is missing.

        Click/key steps need Accessibility, image-wait steps Screen Recording and
        the emergency-stop listener Input Monitoring (enabled steps only). One
        WARNING line in the console plus a status-bar message; never modal, never
        blocks the run.
        """
        try:
            report = self.permission_monitor.check()
            needed = macos_permissions.required_for_scenario(self.scenario)
            missing = [p for p in needed if report.get(p) == macos_permissions.PermissionStatus.MISSING]
        except Exception:
            return
        if not missing:
            return
        noun = "permission" if len(missing) == 1 else "permissions"
        details = "; ".join(
            f"{macos_permissions.info(p).title} ({macos_permissions.info(p).used_for})" for p in missing
        )
        self.log_console.append_log(
            "WARNING",
            f"Missing macOS {noun}: {details}. The run continues; see Permissions in the top bar.",
        )
        titles = ", ".join(macos_permissions.info(p).title for p in missing)
        self.status_bar.showMessage(f"Missing {noun}: {titles}", 8000)

    @Slot()
    def show_about(self) -> None:
        """Shows the About dialog (version, emergency stop keys, licences)."""
        QMessageBox.about(
            self,
            "About ActionMacro",
            f"<h3>ActionMacro {__version__}</h3>"
            "<p>Build a scenario of steps (mouse clicks, key presses, waits and image waits) "
            "and run it as many times as you need.</p>"
            "<p><b>Stop a run:</b> Esc or Ctrl+Shift+S anywhere, or F12 in the app.</p>"
            "<p>Icons: Lucide (ISC License).</p>",
        )

    @Slot()
    def _on_clear_recent_triggered(self) -> None:
        self.sidebar.clear_recent()
        self.status_bar.showMessage("Recent scenarios cleared", 3000)

    # -------------------------------------------------------------------------
    # Dirty Tracking & Window Title
    # -------------------------------------------------------------------------

    def mark_dirty(self, dirty: bool = True) -> None:
        """Sets the dirty modification flag and updates the window title."""
        self._is_dirty = dirty
        self._update_window_title()

    def has_unsaved_changes(self) -> bool:
        """True when the model is dirty or a new scenario name is still being typed."""
        return self._is_dirty or self._pending_scenario_name() is not None

    def _update_window_title(self) -> None:
        dirty_flag = " *" if self.has_unsaved_changes() else ""
        file_str = f" - {self.current_file_path.name}" if self.current_file_path else f" - {self.scenario.name}"
        self.setWindowTitle(f"ActionMacro{file_str}{dirty_flag}")
        self._refresh_document_chrome()

    def _refresh_document_chrome(self) -> None:
        """Syncs the top bar, editor header and sidebar with the open document."""
        path = self.current_file_path
        unsaved = self.has_unsaved_changes()
        self.top_bar.set_document(path.name if path else None, dirty=unsaved,
                                  tooltip=str(path) if path else "")

        # Show the model's name unless the user is typing a new one. A different
        # scenario (New / Open) always replaces whatever was in the field.
        name_field = self.edit_scenario_name
        if self.scenario is not self._name_source or (
            not name_field.isModified() and name_field.text() != self.scenario.name
        ):
            self._name_source = self.scenario
            name_field.setText(self.scenario.name)
        self.editor_dirty_dot.setVisible(unsaved)

        count = len(getattr(self.scenario, "actions", []) or [])
        self.lbl_step_count.setText(f"{count} step" if count == 1 else f"{count} steps")
        self.edit_scenario_name.setAccessibleDescription(self.lbl_step_count.text())
        self._sync_run_availability(count)

        document = (str(path) if path else None, self.scenario.name)
        if document != self._sidebar_document:
            self._sidebar_document = document
            self.sidebar.set_current(path, self.scenario.name)

    def _fit_scenario_name_width(self, text: str) -> None:
        metrics = QFontMetrics(self.edit_scenario_name.font())
        sample = text or self.edit_scenario_name.placeholderText()
        self.edit_scenario_name.setFixedWidth(max(80, min(520, metrics.horizontalAdvance(sample) + 24)))

    def _pending_scenario_name(self) -> Optional[str]:
        """Returns the name the user is typing in the header, if it is not applied yet."""
        field = getattr(self, "edit_scenario_name", None)
        if field is None or not field.isModified() or self.scenario is not self._name_source:
            return None
        name = field.text().strip()
        return name if name and name != self.scenario.name else None

    def _commit_scenario_name_edit(self) -> None:
        """Applies a scenario name that is still being typed in the header.

        Called before anything reads or replaces the model (Save, Save As, Run, the
        unsaved-changes prompt of New / Open / close): keyboard shortcuts and menus do
        not move the focus out of the field, so ``editingFinished`` has not fired yet.
        Only text the user typed counts (``isModified()``), never a stale field.
        """
        name = self._pending_scenario_name()
        if name is not None:
            self.scenario.name = name
            self.edit_scenario_name.setModified(False)
            self.mark_dirty(True)

    @Slot()
    def _on_scenario_name_edited(self) -> None:
        """Enter or focus out: apply the typed name (an empty name restores the old one)."""
        field = self.edit_scenario_name
        name = field.text().strip()
        if not name:
            field.setText(self.scenario.name)
            self._refresh_document_chrome()
            return
        field.setModified(False)
        if name != self.scenario.name:
            self.scenario.name = name
            self.mark_dirty(True)
        else:
            self._update_window_title()

    @Slot(str)
    def _on_scenario_name_typed(self, _text: str) -> None:
        """Shows "unsaved" while a new name is typed (it is applied on Enter / focus out)."""
        self._update_window_title()

    @Slot()
    def _on_scenario_name_edit_cancelled(self) -> None:
        """Esc in the name field: put the scenario's name back and leave the field."""
        self.edit_scenario_name.setText(self.scenario.name)
        self._update_window_title()
        self._leave_scenario_name_edit()

    @Slot()
    def _leave_scenario_name_edit(self) -> None:
        """Moves the keyboard focus from the name field back to the step list."""
        step_list = getattr(self.scenario_builder, "list_widget", None)
        target = step_list if step_list is not None and step_list.isVisible() else self.editor_panel
        target.setFocus(Qt.FocusReason.OtherFocusReason)

    @Slot()
    def _on_save_button_clicked(self) -> None:
        """Editor-header Save button; drops ``clicked``'s ``checked`` argument."""
        self.save_scenario()

    def _remember_recent(self, path: Path) -> None:
        """Adds ``path`` to the recent scenarios (QSettings) and refreshes the sidebar."""
        try:
            self.sidebar.add_recent(path)
        except Exception as exc:  # settings storage problems must never break file I/O
            self.log_console.append_log("WARNING", f"Could not update recent scenarios: {exc}")

    @Slot(str)
    def _on_recent_scenario_activated(self, file_path: str) -> None:
        if self.current_file_path is not None and Path(file_path).resolve() == self.current_file_path:
            return
        self.open_scenario(file_path)

    @Slot(str)
    def _on_recent_scenario_missing(self, file_path: str) -> None:
        name = Path(file_path).name
        self.log_console.append_log("WARNING", f"Scenario file not found: {file_path}")
        self.status_bar.showMessage(f"File not found: {name}. Remove it from the list with the x button.", 6000)

    def confirm_discard_changes(self) -> bool:
        """Prompts user to save unsaved modifications. Returns True to proceed."""
        self._commit_scenario_name_edit()
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

    def showEvent(self, event: QShowEvent) -> None:
        """Gives the step list the initial keyboard focus (not the first chrome button)."""
        super().showEvent(event)
        if not self._initial_focus_done:
            self._initial_focus_done = True
            step_list = getattr(self.scenario_builder, "list_widget", None)
            target = step_list if step_list is not None and step_list.isVisible() else self.editor_panel
            target.setFocus(Qt.FocusReason.OtherFocusReason)

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
        self._sync_inspector_base_dir()
        self.dashboard.set_loop_count(self.scenario.loop_count)

        if hasattr(self.scenario_builder, "set_scenario"):
            self.scenario_builder.set_scenario(self.scenario)

        self.mark_dirty(False)
        self.log_console.append_log("INFO", "Created new blank scenario")
        self.status_bar.showMessage("New scenario created", 3000)

    @Slot()
    def _on_open_action_triggered(self) -> None:
        """File > Open / sidebar handler; drops QAction's ``checked`` argument."""
        self.open_scenario()

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
            self._sync_inspector_base_dir()
            self.dashboard.set_loop_count(loaded.loop_count)

            if hasattr(self.scenario_builder, "set_scenario"):
                self.scenario_builder.set_scenario(loaded)

            self.mark_dirty(False)
            self._remember_recent(path)
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

        self._sync_inspector_base_dir()
        try:
            self._sync_model_from_views()
            ScenarioPersistence.save(self.scenario, self.current_file_path)
            self.mark_dirty(False)
            self._remember_recent(self.current_file_path)
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
        self._commit_scenario_name_edit()
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save Scenario As",
            str(self.current_file_path) if self.current_file_path else self._suggested_file_name(),
            "JSON Scenarios (*.json);;All Files (*)",
        )
        if not file_name:
            return False

        path = Path(file_name).resolve()
        old_dir = self.current_file_path.parent if self.current_file_path else None
        self._rebase_relative_templates(old_dir, path.parent)
        self.current_file_path = path
        return self.save_scenario()

    def _rebase_relative_templates(self, old_dir: Optional[Path], new_dir: Path) -> None:
        """Keeps relative template paths pointing at the same files after Save As elsewhere.

        Relative paths start at the scenario file's folder (as in the engine). A path
        that finds its file from the old folder but not from the new one is rewritten
        relative to the new folder (``../A/templates/t.png``), so the saved scenario
        still runs. Paths that work from the new folder, or did not work before, stay.
        A scenario that was never saved (``old_dir`` None) resolves them from the
        working directory, as the engine does.
        """
        if old_dir is None:
            old_dir = Path.cwd()
        if old_dir.resolve() == new_dir.resolve():
            return
        changed = []
        for action in getattr(self.scenario, "actions", []) or []:
            act_type = action.type if isinstance(action.type, ActionType) else ActionType.from_str(str(action.type))
            if act_type != ActionType.IMAGE_WAIT:
                continue
            raw = str(action.params.get("template_path", "") or "").strip()
            if not raw or Path(raw.replace("\\", "/")).is_absolute():
                continue
            try:
                old_file = resolve_template_path(raw, old_dir)
                if not old_file.is_file() or resolve_template_path(raw, new_dir).is_file():
                    continue
                action.params.template_path = to_posix_relative_path(str(old_file), new_dir)
            except (OSError, ValueError, AttributeError):
                continue
            changed.append(action)
        if not changed:
            return
        for action in changed:
            if hasattr(self.scenario_builder, "update_action_block"):
                self.scenario_builder.update_action_block(action)
        current = getattr(self.property_inspector, "current_action", None)
        if current in changed and hasattr(self.property_inspector, "set_action"):
            index = self.scenario.actions.index(current) + 1
            self.property_inspector.set_action(current, index)
        self.log_console.append_log(
            "INFO", f"Updated {len(changed)} template path(s) so they still point at the same images.")

    def _sync_inspector_base_dir(self) -> None:
        """Relative template paths start at the scenario file's folder, as in the engine."""
        base_dir = self.current_file_path.parent if self.current_file_path else None
        if hasattr(self.property_inspector, "set_base_dir"):
            self.property_inspector.set_base_dir(base_dir)
        if hasattr(self.scenario_builder, "set_base_dir"):
            self.scenario_builder.set_base_dir(base_dir)

    def _suggested_file_name(self) -> str:
        """Returns ``<scenario name>.json`` with characters invalid in file names replaced."""
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", self.scenario.name).strip(" ._")
        return f"{stem or 'scenario'}.json"

    def _sync_model_from_views(self) -> None:
        """Synchronizes the header (name being typed) and the dashboard into the model."""
        self._commit_scenario_name_edit()
        self.scenario.loop_count = self.dashboard.get_effective_loop_count()

    # -------------------------------------------------------------------------
    # Run availability
    # -------------------------------------------------------------------------

    def _sync_run_availability(self, step_count: Optional[int] = None) -> None:
        """Enables Run (button, menu item, F5) only when the scenario has steps."""
        if step_count is None:
            step_count = len(getattr(self.scenario, "actions", []) or [])
        has_steps = step_count > 0
        self.dashboard.set_runnable(has_steps, self.NO_STEPS_HINT)
        act_run = getattr(self, "act_run", None)
        if act_run is not None:
            act_run.setEnabled(has_steps)

    @Slot(str)
    def _on_run_state_changed(self, state: str) -> None:
        """Suspends the Save As shortcut while a run is in progress.

        On Windows/Linux Save As is Ctrl+Shift+S, which is also the global
        emergency-stop hotkey: stopping a run must not open a file dialog as well.
        The menu item itself stays available.
        """
        active = state in self.ACTIVE_RUN_STATES
        self._set_editing_locked(active)
        if hasattr(self.scenario_builder, "set_paused"):
            self.scenario_builder.set_paused(state == "paused")
        act_save_as = getattr(self, "act_save_as", None)
        if act_save_as is None:
            return
        if active:
            act_save_as.setShortcut(QKeySequence())
        else:
            act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)

    def _set_editing_locked(self, locked: bool) -> None:
        """Steps are read-only while a run is active.

        The run executes a copy of the scenario and highlights steps by position, so
        edits would not reach it and structural changes would move the highlight.
        """
        if hasattr(self.scenario_builder, "set_editable"):
            self.scenario_builder.set_editable(not locked)
        self.property_inspector.setEnabled(not locked)
        self.act_picker.setEnabled(not locked)  # picking writes into the selected step
        self._sync_undo_delete_action()

    # -------------------------------------------------------------------------
    # Screen Coordinate Picker Integration
    # -------------------------------------------------------------------------

    @Slot()
    def trigger_coordinate_picker(self) -> None:
        """Launches the built-in screen coordinate picker overlay."""
        self.log_console.append_log("INFO", "Coordinate picker started. Click to pick · Esc to cancel")

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
        self.log_console.append_log("INFO", "Coordinate picker cancelled.")
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
    def _on_run_action_triggered(self) -> None:
        """Run menu / F5 handler; uses the dashboard Repeat Count.

        QAction.triggered carries ``checked`` (False for a non-checkable action).
        Passing it on as ``loop_count`` would mean 0 = infinite, so it is dropped.
        """
        self.start_macro()

    @Slot()
    def start_macro(self, loop_count: Optional[int] = None) -> None:
        """Starts macro execution in a background worker thread.

        Args:
            loop_count: Explicit repeat count (0 = infinite). ``None`` uses the
                dashboard Repeat Count. A ``bool`` (the ``checked`` argument of
                ``QAction.triggered`` / ``QAbstractButton.clicked``) or a negative
                value is not a repeat count and is treated as ``None``.
        """
        # bool is a subclass of int, so it must be rejected before the int check.
        if isinstance(loop_count, bool) or not isinstance(loop_count, int) or loop_count < 0:
            loop_count = None

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
            # The Run button, Run menu item and F5 are disabled while there are no steps
            # (see _sync_run_availability), so only programmatic calls reach this guard.
            QMessageBox.warning(self, "Empty Scenario", "Add at least one step before running the scenario.")
            return

        effective_loops = loop_count if loop_count is not None else self.dashboard.get_effective_loop_count()
        run_scenario = self.scenario.clone() if hasattr(self.scenario, "clone") else self.scenario
        run_scenario.loop_count = effective_loops
        self._warn_missing_run_permissions()

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
        self._run_actions = list(getattr(run_scenario, "actions", []) or [])
        sig.action_started.connect(self._on_engine_action_started, Qt.ConnectionType.QueuedConnection)
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

    @Slot(int, str)
    def _on_engine_action_started(self, idx: int, desc: str) -> None:
        """Shows the running step in the status bar as the step list words it.

        The engine sends the description or the internal type (``key_input``); a step
        without a description is shown with its summary sentence ("Press ⌘ + C").
        """
        if 0 <= idx < len(self._run_actions):
            action = self._run_actions[idx]
            desc = (action.description or "").strip() or step_text.step_summary(action)
        self.dashboard.on_action_started(idx, desc)

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
        if hasattr(self.property_inspector, "set_step_index"):
            actions = list(getattr(self.scenario, "actions", None) or [])
            index = next((i + 1 for i, act in enumerate(actions) if act is block), None)
            self.property_inspector.set_step_index(index)

    def _sync_initial_selection(self) -> None:
        """Shows the step the builder selected while it was built (before the signals existed)."""
        selected = getattr(self.scenario_builder, "selected_action", None)
        if callable(selected):
            block = selected()
            if block is not None:
                self._on_builder_block_selected(block)

    @Slot(object)
    def _on_builder_action_enabled_changed(self, block: ActionBlock) -> None:
        """A card switch changed ``enabled``: the inspector shows the same flag."""
        if hasattr(self.property_inspector, "refresh_common_fields"):
            self.property_inspector.refresh_common_fields(block)

    @Slot(str)
    def _on_builder_action_deleted(self, _action_id: str) -> None:
        if getattr(self.scenario_builder, "can_undo_delete", lambda: False)():
            undo = shortcut_text(QKeySequence.StandardKey.Undo)
            self.status_bar.showMessage(f"Step deleted. Undo with {undo} or Edit > Undo Delete Step.", 8000)

    @Slot()
    def _sync_undo_delete_action(self, *_args: object) -> None:
        builder = self.scenario_builder
        available = bool(getattr(builder, "can_undo_delete", lambda: False)())
        editable = bool(getattr(builder, "is_editable", lambda: True)())
        self.act_undo_delete.setEnabled(available and editable)

    @Slot()
    def _on_undo_delete_triggered(self) -> None:
        """Edit > Undo Delete Step; drops QAction's ``checked`` argument."""
        undo = getattr(self.scenario_builder, "undo_delete", None)
        block = undo() if callable(undo) else None
        if block is not None:
            self.status_bar.showMessage("Step restored", 3000)
            step_list = getattr(self.scenario_builder, "list_widget", None)
            if step_list is not None and step_list.isVisible():
                step_list.setFocus(Qt.FocusReason.OtherFocusReason)
        self._sync_undo_delete_action()

    @Slot(object)
    def _on_inspector_block_updated(self, block: ActionBlock) -> None:
        self.mark_dirty(True)
        if hasattr(self.scenario_builder, "update_action_block"):
            self.scenario_builder.update_action_block(block)
        elif hasattr(self.scenario_builder, "update_block_display"):
            self.scenario_builder.update_block_display(block)
