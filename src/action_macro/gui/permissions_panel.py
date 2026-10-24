"""
action_macro.gui.permissions_panel
=====================================
macOS permission status and guidance.

* :class:`PermissionsPanel` - the "Permissions" dialog: an intro naming the app
  macOS attributes the permissions to, one row per permission (icon, name,
  purpose, status pill, "Open settings"), a note about restarting that app, and
  "Check again" / "Close". Off macOS it says no extra permissions are needed.
* :class:`PermissionMonitor` - runs a permission checker at startup, on demand
  and, throttled, whenever the application becomes active again (for example
  after the user changed a switch in System Settings), and emits the result.

Checks are passive (see :mod:`action_macro.engine.macos_permissions`). The
checker and the URL opener are injectable, so tests never query the system or
open System Settings.
"""

from __future__ import annotations

import html
import time
from typing import Callable, Dict, List, Mapping, Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from action_macro.engine import macos_permissions
from action_macro.engine.macos_permissions import (
    Permission,
    PermissionChecker,
    PermissionReport,
    PermissionStatus,
)
from action_macro.gui import icons, theme
from action_macro.gui.components import Separator, empty_state_badge, style_button

#: Opens a URL; returns True on success (``QDesktopServices.openUrl`` by default).
UrlOpener = Callable[[QUrl], bool]

#: Width of the panel (the height follows the content).
PANEL_WIDTH = 520

#: Lucide icon of each permission row.
PERMISSION_ICONS: Dict[Permission, str] = {
    Permission.ACCESSIBILITY: "accessibility",
    Permission.INPUT_MONITORING: "keyboard",
    Permission.SCREEN_RECORDING: "monitor",
}

NOT_REQUIRED_TEXT = "No extra permissions are needed on this system."
UNKNOWN_LAUNCHER_TEXT = "your terminal or the ActionMacro app"
UNKNOWN_STATUS_TIP = (
    "macOS did not report a status (it may not have asked yet). "
    "If this feature does not work, check it in System Settings."
)


def open_url(url: QUrl) -> bool:
    """Default opener: hands ``url`` to the desktop (System Settings for ``x-apple.systempreferences``)."""
    return bool(QDesktopServices.openUrl(url))


def _status(value: object) -> PermissionStatus:
    try:
        return PermissionStatus(value)
    except ValueError:
        return PermissionStatus.UNKNOWN


# -----------------------------------------------------------------------------
# Monitor
# -----------------------------------------------------------------------------


class PermissionMonitor(QObject):
    """Runs a permission checker and remembers the last report.

    Args:
        checker: Callable returning ``{Permission: PermissionStatus}``. ``None``
            uses :data:`macos_permissions.default_checker`, looked up at every
            check (tests replace it).
        min_interval: Seconds that must pass after a check before the
            application becoming active triggers another one.
        clock: Monotonic clock in seconds (injectable for tests).
    """

    #: Emitted after every check with the new report (``Dict[Permission, PermissionStatus]``).
    report_changed = Signal(object)

    DEFAULT_MIN_INTERVAL = 3.0

    def __init__(
        self,
        checker: Optional[PermissionChecker] = None,
        *,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._checker = checker
        self.min_interval = float(min_interval)
        #: Monotonic clock in seconds used for the throttle (replaceable in tests).
        self.clock = clock
        self._report: PermissionReport = {}
        self._last_check: Optional[float] = None
        self._watched_app: Optional[QGuiApplication] = None

    @property
    def checker(self) -> PermissionChecker:
        """The checker in use (the injected one or the module default)."""
        return self._checker or macos_permissions.default_checker

    def report(self) -> PermissionReport:
        """Returns the last report (empty before the first check)."""
        return dict(self._report)

    def missing(self) -> List[Permission]:
        """Returns the permissions the last report lists as ``MISSING``."""
        return macos_permissions.missing_permissions(self._report) if self._report else []

    def check(self) -> PermissionReport:
        """Runs the checker now and emits :attr:`report_changed`.

        A checker that raises yields an all-``UNKNOWN`` report instead.
        """
        try:
            raw: Optional[Mapping[Permission, PermissionStatus]] = self.checker()
        except Exception:
            raw = None
        report = macos_permissions.normalize_report(raw)
        self._report = report
        self._last_check = self.clock()
        self.report_changed.emit(dict(report))
        return dict(report)

    def check_if_stale(self) -> bool:
        """Checks again unless the last check is less than ``min_interval`` old.

        Returns True when a check ran.
        """
        if self._last_check is not None and self.clock() - self._last_check < self.min_interval:
            return False
        self.check()
        return True

    def watch_application(self, app: Optional[QGuiApplication] = None) -> bool:
        """Re-checks (throttled) whenever ``app`` becomes active. Returns False without an app."""
        app = app or QGuiApplication.instance()
        if app is None or self._watched_app is app:
            return app is not None
        self.stop_watching()
        app.applicationStateChanged.connect(self._on_application_state_changed)
        self._watched_app = app
        return True

    def stop_watching(self) -> None:
        """Stops reacting to application state changes."""
        if self._watched_app is not None:
            try:
                self._watched_app.applicationStateChanged.disconnect(self._on_application_state_changed)
            except (RuntimeError, TypeError):
                pass
            self._watched_app = None

    @Slot(Qt.ApplicationState)
    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state == Qt.ApplicationState.ApplicationActive:
            self.check_if_stale()


# -----------------------------------------------------------------------------
# Panel
# -----------------------------------------------------------------------------


class PermissionRow(QFrame):
    """One permission: icon, name, status pill, one-line purpose and "Open settings"."""

    #: Emitted with the row's :class:`Permission` when "Open settings" is clicked.
    open_settings_requested = Signal(object)

    ICON_SIZE = 32

    def __init__(self, permission: Permission, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.permission = Permission(permission)
        self._info = macos_permissions.info(self.permission)
        self._status = PermissionStatus.UNKNOWN
        self.setObjectName("PermissionRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_LG, theme.SPACE_MD, theme.SPACE_MD, theme.SPACE_MD)
        layout.setSpacing(theme.SPACE_MD)

        self.lbl_icon = QLabel()
        self.lbl_icon.setPixmap(empty_state_badge(PERMISSION_ICONS[self.permission], theme.COLORS.accent,
                                                  self.ICON_SIZE))
        self.lbl_icon.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        layout.addWidget(self.lbl_icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(theme.SPACE_SM)
        self.lbl_name = QLabel(self._info.title)
        self.lbl_name.setObjectName("PermissionName")
        self.lbl_name.setFont(theme.ui_font(theme.FONT_SUBTITLE, QFont.Weight.DemiBold))
        name_row.addWidget(self.lbl_name)
        self.lbl_pill = QLabel()
        self.lbl_pill.setObjectName("PermissionPill")
        self.lbl_pill.setFixedHeight(18)  # same pill height as the step status pill
        self.lbl_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_row.addWidget(self.lbl_pill)
        name_row.addStretch(1)
        text.addLayout(name_row)

        self.lbl_purpose = QLabel(self._info.purpose)
        self.lbl_purpose.setObjectName("PermissionPurpose")
        self.lbl_purpose.setWordWrap(True)
        text.addWidget(self.lbl_purpose)
        layout.addLayout(text, 1)

        self.btn_open = QPushButton("Open settings")
        self.btn_open.setObjectName("OpenSettingsButton")
        self.btn_open.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_open.setAccessibleName(f"Open {self._info.title} settings")
        self.btn_open.setToolTip(f"Open System Settings > Privacy & Security > {self._info.title}")
        self.btn_open.clicked.connect(self._on_open_clicked)
        layout.addWidget(self.btn_open, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_status(PermissionStatus.UNKNOWN, force=True)

    def status(self) -> PermissionStatus:
        """Returns the status shown in the pill."""
        return self._status

    def set_status(self, status: PermissionStatus, *, force: bool = False) -> None:
        """Shows ``status`` in the pill; a missing permission gets a bordered button."""
        status = _status(status)
        if status == self._status and not force:
            return
        self._status = status
        self.lbl_pill.setText(macos_permissions.STATUS_LABELS[status])
        self.lbl_pill.setToolTip(UNKNOWN_STATUS_TIP if status == PermissionStatus.UNKNOWN else "")
        self.lbl_pill.setAccessibleName(f"{self._info.title}: {macos_permissions.STATUS_LABELS[status]}")
        theme.set_style_prop(self.lbl_pill, "state", status.value)
        variant = "ghost" if status == PermissionStatus.GRANTED else "secondary"
        style_button(self.btn_open, variant, "external-link", icon_px=14, height=theme.CONTROL_HEIGHT_SM)
        theme.repolish(self.btn_open)

    @Slot()
    def _on_open_clicked(self) -> None:
        self.open_settings_requested.emit(self.permission)


class PermissionsPanel(QDialog):
    """The "Permissions" dialog (status of each macOS permission and how to grant it).

    Args:
        checker: Runs a check for "Check again" (``None`` disables the button).
        report: Initial report; ``None`` runs ``checker`` once (or shows
            everything as unknown without a checker).
        opener: Opens the System Settings URL (``QDesktopServices.openUrl`` by default).
        launcher_name: Name of the app macOS attributes the permissions to.
            ``None`` detects it (:func:`macos_permissions.launcher_app_name`);
            an empty string means it is not known.
    """

    #: Emitted with the new report after "Check again".
    report_changed = Signal(object)

    def __init__(
        self,
        checker: Optional[PermissionChecker] = None,
        *,
        report: Optional[Mapping[Permission, PermissionStatus]] = None,
        opener: Optional[UrlOpener] = None,
        launcher_name: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PermissionsPanel")
        self.setWindowTitle("Permissions")
        self.setModal(False)
        self.setFixedWidth(PANEL_WIDTH)
        # The dialog itself holds the focus when it opens, so no button shows a focus
        # ring until the user presses Tab (Enter still clicks Close, Esc closes).
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._checker = checker
        self._opener: UrlOpener = opener or open_url
        if launcher_name is None:
            launcher_name = macos_permissions.launcher_app_name() or ""
        self.launcher_name = launcher_name
        self._report: PermissionReport = {}
        self._intro_plain = ""
        self.rows: Dict[Permission, PermissionRow] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_XL, theme.SPACE_LG + 4)
        layout.setSpacing(theme.SPACE_LG)

        # Header: badge, title, intro
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(theme.SPACE_MD + 2)
        self.lbl_badge = QLabel()
        self.lbl_badge.setFixedSize(40, 40)
        header.addWidget(self.lbl_badge, 0, Qt.AlignmentFlag.AlignTop)
        heading = QVBoxLayout()
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(theme.SPACE_XS)
        self.lbl_title = QLabel("Permissions")
        self.lbl_title.setObjectName("PermissionsTitle")
        self.lbl_title.setFont(theme.ui_font(theme.FONT_HEADER, QFont.Weight.DemiBold))
        heading.addWidget(self.lbl_title)
        self.lbl_intro = QLabel()
        self.lbl_intro.setObjectName("PermissionsIntro")
        self.lbl_intro.setWordWrap(True)
        self.lbl_intro.setTextFormat(Qt.TextFormat.RichText)
        heading.addWidget(self.lbl_intro)
        header.addLayout(heading, 1)
        layout.addLayout(header)

        # One row per permission inside a rounded list
        self.list_frame = QFrame()
        self.list_frame.setObjectName("PermissionList")
        list_layout = QVBoxLayout(self.list_frame)
        list_layout.setContentsMargins(1, 1, 1, 1)
        list_layout.setSpacing(0)
        for index, permission in enumerate(macos_permissions.PERMISSIONS):
            if index:
                list_layout.addWidget(Separator(vertical=False))
            row = PermissionRow(permission)
            row.open_settings_requested.connect(self.open_settings)
            list_layout.addWidget(row)
            self.rows[permission] = row
        layout.addWidget(self.list_frame)

        # Restart note
        self.note = QWidget()
        note_layout = QHBoxLayout(self.note)
        note_layout.setContentsMargins(theme.SPACE_XS, 0, 0, 0)
        note_layout.setSpacing(theme.SPACE_SM)
        note_icon = QLabel()
        note_icon.setPixmap(icons.pixmap("info", theme.COLORS.text_muted, 14))
        note_icon.setFixedSize(14, 18)
        note_layout.addWidget(note_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.lbl_note = QLabel()
        self.lbl_note.setObjectName("PermissionsNote")
        self.lbl_note.setWordWrap(True)
        note_layout.addWidget(self.lbl_note, 1)
        layout.addWidget(self.note)

        # Footer: Check again (+ when) ... Close
        layout.addSpacing(theme.SPACE_XS)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(theme.SPACE_MD)
        self.btn_check = QPushButton("Check again")
        style_button(self.btn_check, "secondary", "refresh-cw", icon_px=14)
        self.btn_check.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_check.setToolTip("Read the permission status again")
        self.btn_check.setEnabled(checker is not None)
        self.btn_check.clicked.connect(self._on_check_clicked)
        footer.addWidget(self.btn_check)
        self.lbl_checked = QLabel()
        self.lbl_checked.setObjectName("PermissionsCheckedLabel")
        footer.addWidget(self.lbl_checked)
        footer.addStretch(1)  # keeps Close at its own size when the left side is hidden
        self.btn_close = QPushButton("Close")
        style_button(self.btn_close, "primary")
        self.btn_close.setMinimumWidth(88)
        self.btn_close.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.btn_close.setDefault(True)
        self.btn_close.clicked.connect(self.close)
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        if report is not None:
            self.set_report(report)
        elif checker is not None:
            self.check_again()
        else:
            self.set_report({})

    # -- data -----------------------------------------------------------------

    def report(self) -> PermissionReport:
        """Returns the report the panel shows."""
        return dict(self._report)

    def set_report(self, report: Optional[Mapping[Permission, PermissionStatus]]) -> None:
        """Shows ``report`` (missing entries count as unknown) and stamps the check time."""
        self._report = macos_permissions.normalize_report(report)
        not_required = macos_permissions.is_not_required(self._report)
        for permission, row in self.rows.items():
            row.set_status(self._report[permission])
        self.list_frame.setVisible(not not_required)
        self.note.setVisible(not not_required)
        self.btn_check.setVisible(not not_required)
        self.lbl_checked.setVisible(not not_required)
        self._update_texts()
        self.lbl_checked.setText(f"Checked at {time.strftime('%H:%M:%S')}")
        self._fit_height()

    @Slot()
    def check_again(self) -> PermissionReport:
        """Runs the checker, shows the result and emits :attr:`report_changed`."""
        if self._checker is None:
            return self.report()
        try:
            raw: Optional[Mapping[Permission, PermissionStatus]] = self._checker()
        except Exception:
            raw = None
        self.set_report(raw)
        report = self.report()
        self.report_changed.emit(report)
        return report

    def set_opener(self, opener: Optional[UrlOpener]) -> None:
        """Replaces the URL opener (``None``: ``QDesktopServices.openUrl``)."""
        self._opener = opener or open_url

    @Slot(object)
    def open_settings(self, permission: Permission) -> bool:
        """Opens the System Settings pane of ``permission``; returns the opener's result."""
        url = QUrl(macos_permissions.settings_url(Permission(permission)))
        try:
            return bool(self._opener(url))
        except Exception:
            return False

    # -- texts ----------------------------------------------------------------

    def summary(self) -> str:
        """One-line summary of the report (used as the badge tooltip)."""
        if macos_permissions.is_not_required(self._report):
            return NOT_REQUIRED_TEXT
        missing = macos_permissions.missing_permissions(self._report)
        if missing:
            noun = "permission" if len(missing) == 1 else "permissions"
            return f"Missing {noun}: {macos_permissions.join_titles(missing)}."
        if all(status == PermissionStatus.GRANTED for status in self._report.values()):
            return "All permissions are granted."
        return "Some statuses could not be read."

    def _update_texts(self) -> None:
        if self._report and macos_permissions.is_not_required(self._report):
            self._intro_plain = NOT_REQUIRED_TEXT
            self.lbl_intro.setText(html.escape(NOT_REQUIRED_TEXT))
        else:
            lead = "macOS asks you to allow these for the app that started ActionMacro: "
            if self.launcher_name:
                self._intro_plain = f"{lead}{self.launcher_name}."
                self.lbl_intro.setText(
                    f"{html.escape(lead)}<span style='color:{theme.COLORS.text}; font-weight:600'>"
                    f"{html.escape(self.launcher_name)}</span>."
                )
            else:
                self._intro_plain = f"{lead}{UNKNOWN_LAUNCHER_TEXT}."
                self.lbl_intro.setText(html.escape(self._intro_plain))
        app = self.launcher_name or "the app that started ActionMacro"
        self.lbl_note.setText(
            f"Changes to Screen Recording or Input Monitoring take effect after you quit and reopen {app}."
        )
        missing = macos_permissions.missing_permissions(self._report)
        if missing:
            badge = ("shield-alert", theme.COLORS.warning)
        elif self._report and all(s in (PermissionStatus.GRANTED, PermissionStatus.NOT_REQUIRED)
                                  for s in self._report.values()):
            badge = ("shield-check", theme.COLORS.success)
        else:
            badge = ("shield", theme.COLORS.accent)
        self.lbl_badge.setPixmap(empty_state_badge(badge[0], badge[1], 40))
        self.lbl_badge.setToolTip(self.summary() if self._report else "")

    def intro_text(self) -> str:
        """The intro sentence as plain text."""
        return self._intro_plain

    def _fit_height(self) -> None:
        layout = self.layout()
        if layout is None:
            return
        layout.activate()
        height = layout.totalHeightForWidth(PANEL_WIDTH) if layout.hasHeightForWidth() else layout.sizeHint().height()
        self.setFixedHeight(max(height, layout.minimumSize().height()))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    @Slot()
    def _on_check_clicked(self) -> None:
        self.check_again()
