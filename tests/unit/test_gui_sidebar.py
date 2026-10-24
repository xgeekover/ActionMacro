"""Unit tests for the scenario sidebar and its QSettings-backed recent-files store."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest

from action_macro.gui.sidebar import (
    CURRENT_ROLE,
    DRAFT_ROLE,
    MISSING_ROLE,
    RecentScenarioStore,
    ScenarioSidebar,
    _RecentItemDelegate,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    return path


# -----------------------------------------------------------------------------
# RecentScenarioStore
# -----------------------------------------------------------------------------

def test_default_settings_are_redirected_away_from_real_user_settings():
    """conftest redirects user-scope INI settings so tests never touch real ones."""
    settings = RecentScenarioStore.default_settings()
    assert settings.format() == QSettings.Format.IniFormat
    assert settings.organizationName() == "ActionMacro"
    temp_root = Path(tempfile.gettempdir()).resolve()
    assert Path(settings.fileName()).resolve().is_relative_to(temp_root)


def test_store_add_moves_to_front_and_dedupes(isolated_settings, tmp_path):
    store = RecentScenarioStore(isolated_settings)
    a, b, c = (_touch(tmp_path / f"{n}.json") for n in "abc")

    store.add(a)
    store.add(b)
    store.add(c)
    assert store.paths() == [str(c.resolve()), str(b.resolve()), str(a.resolve())]

    store.add(a)  # re-opening moves it to the top without duplicating it
    assert store.paths() == [str(a.resolve()), str(c.resolve()), str(b.resolve())]


def test_store_persists_and_survives_a_single_entry(isolated_settings, tmp_path):
    store = RecentScenarioStore(isolated_settings)
    only = _touch(tmp_path / "only.json")
    store.add(only)

    reopened = QSettings(isolated_settings.fileName(), QSettings.Format.IniFormat)
    assert RecentScenarioStore(reopened).paths() == [str(only.resolve())]


def test_store_caps_entries_and_removes(isolated_settings, tmp_path):
    store = RecentScenarioStore(isolated_settings)
    files = [_touch(tmp_path / f"s{i:02d}.json") for i in range(RecentScenarioStore.MAX_ENTRIES + 5)]
    for f in files:
        store.add(f)
    paths = store.paths()
    assert len(paths) == RecentScenarioStore.MAX_ENTRIES
    assert paths[0] == str(files[-1].resolve())

    assert store.remove(paths[0]) is True
    assert paths[0] not in store.paths()
    assert store.remove(tmp_path / "never-added.json") is False

    store.clear()
    assert store.paths() == []


def test_store_tolerates_odd_stored_values(isolated_settings):
    isolated_settings.setValue(RecentScenarioStore.KEY, "/tmp/one.json")
    assert RecentScenarioStore(isolated_settings).paths() == ["/tmp/one.json"]
    isolated_settings.setValue(RecentScenarioStore.KEY, ["", "/tmp/a.json", "/tmp/a.json"])
    assert RecentScenarioStore(isolated_settings).paths() == ["/tmp/a.json"]


# -----------------------------------------------------------------------------
# ScenarioSidebar
# -----------------------------------------------------------------------------

@pytest.fixture
def sidebar_env(qtbot, isolated_settings, tmp_path):
    store = RecentScenarioStore(isolated_settings)
    daily = _touch(tmp_path / "daily_quest.json")
    arena = _touch(tmp_path / "arena_farm.json")
    missing = tmp_path / "gone.json"
    for path in (missing, arena, daily):
        store.add(path)
    sidebar = ScenarioSidebar(store)
    qtbot.addWidget(sidebar)
    sidebar.resize(260, 500)
    sidebar.show()
    qtbot.waitExposed(sidebar)
    return sidebar, store, daily, arena, missing


def test_sidebar_lists_recent_files_with_draft_and_missing_flags(sidebar_env):
    sidebar, store, daily, arena, missing = sidebar_env
    assert sidebar.visible_paths() == store.paths()

    draft = sidebar.list_widget.item(0)
    assert draft.data(DRAFT_ROLE) is True and draft.data(CURRENT_ROLE) is True
    assert sidebar.item_for_path(missing).data(MISSING_ROLE) is True
    assert sidebar.item_for_path(daily).data(MISSING_ROLE) is False
    assert not sidebar.lbl_hint.isVisible()


def test_sidebar_highlights_current_file(sidebar_env):
    sidebar, _, daily, arena, _ = sidebar_env
    sidebar.set_current(daily, "Daily quest")
    assert sidebar.item_for_path(daily).data(CURRENT_ROLE) is True
    assert sidebar.item_for_path(arena).data(CURRENT_ROLE) is False
    assert not any(sidebar.list_widget.item(r).data(DRAFT_ROLE) for r in range(sidebar.list_widget.count()))


def test_sidebar_filter_and_no_match_state(sidebar_env):
    sidebar, _, daily, arena, _ = sidebar_env
    sidebar.edit_filter.setText("arena")
    assert sidebar.visible_paths() == [str(arena.resolve())]
    assert not sidebar.lbl_no_match.isVisible()

    sidebar.edit_filter.setText("nothing-matches-this")
    assert sidebar.visible_paths() == []
    assert sidebar.lbl_no_match.isVisible()

    sidebar.edit_filter.clear()
    assert len(sidebar.visible_paths()) == 3


def _click_row(qtbot, sidebar, item, pos: QPoint = None):
    rect = sidebar.list_widget.visualItemRect(item)
    QTest.mouseClick(sidebar.list_widget.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, pos or rect.center())


def test_sidebar_click_opens_existing_file(qtbot, sidebar_env):
    sidebar, _, daily, _, _ = sidebar_env
    with qtbot.waitSignal(sidebar.scenario_activated) as blocker:
        _click_row(qtbot, sidebar, sidebar.item_for_path(daily))
    assert blocker.args == [str(daily.resolve())]


def test_sidebar_click_on_missing_file_reports_it(qtbot, sidebar_env):
    sidebar, _, _, _, missing = sidebar_env
    with qtbot.assertNotEmitted(sidebar.scenario_activated):
        with qtbot.waitSignal(sidebar.missing_scenario_activated) as blocker:
            _click_row(qtbot, sidebar, sidebar.item_for_path(missing))
    assert blocker.args == [str(missing.resolve())]


def test_sidebar_remove_button_removes_without_opening(qtbot, sidebar_env):
    sidebar, store, _, _, missing = sidebar_env
    item = sidebar.item_for_path(missing)
    remove_at = _RecentItemDelegate.remove_rect(sidebar.list_widget.visualItemRect(item)).center()
    with qtbot.assertNotEmitted(sidebar.scenario_activated), \
            qtbot.assertNotEmitted(sidebar.missing_scenario_activated):
        with qtbot.waitSignal(sidebar.recent_removed):
            _click_row(qtbot, sidebar, item, remove_at)
    assert str(missing.resolve()) not in store.paths()
    assert sidebar.item_for_path(missing) is None


def test_sidebar_delete_key_removes_selected_entry(qtbot, sidebar_env):
    sidebar, store, daily, _, _ = sidebar_env
    sidebar.list_widget.setCurrentItem(sidebar.item_for_path(daily))
    sidebar.list_widget.setFocus()
    QTest.keyClick(sidebar.list_widget, Qt.Key.Key_Delete)
    assert str(daily.resolve()) not in store.paths()


def test_sidebar_new_and_open_buttons(qtbot, sidebar_env):
    sidebar = sidebar_env[0]
    with qtbot.waitSignal(sidebar.new_requested):
        sidebar.btn_new.click()
    with qtbot.waitSignal(sidebar.open_requested):
        sidebar.btn_open.click()


def test_sidebar_empty_store_shows_hint(qtbot, isolated_settings):
    sidebar = ScenarioSidebar(RecentScenarioStore(isolated_settings))
    qtbot.addWidget(sidebar)
    sidebar.show()
    qtbot.waitExposed(sidebar)
    assert sidebar.visible_paths() == []
    assert sidebar.list_widget.count() == 1  # the unsaved draft row
    assert sidebar.lbl_hint.isVisible()
    sidebar.grab()  # the custom delegate paints without errors
