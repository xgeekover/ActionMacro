"""
tests.unit.test_persistence
===========================
Unit tests covering all persistence specifications (TP01 - TP20) for action_macro.core.persistence.
"""

from pathlib import Path
import json
import sys
import pytest

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageWaitParams,
    KeyInputParams,
    MouseClickParams,
    ScenarioModel,
    ValidationError,
)
from action_macro.core.persistence import (
    PersistenceError,
    ScenarioJSONDecodeError,
    ScenarioValidationError,
    UnsupportedVersionError,
    load_scenario,
    resolve_template_path,
    save_scenario,
    scenario_from_dict,
    scenario_to_dict,
)


# ---------------------------------------------------------------------------
# TP01: test_scenario_to_dict_empty
# ---------------------------------------------------------------------------

def test_scenario_to_dict_empty():
    scen = ScenarioModel(name="Empty")
    d = scenario_to_dict(scen)
    assert d["name"] == "Empty"
    assert d["version"] == "1.0.0"
    assert d["loop_count"] == 1
    assert d["actions"] == []


# ---------------------------------------------------------------------------
# TP02: test_scenario_to_dict_all_action_types
# ---------------------------------------------------------------------------

def test_scenario_to_dict_all_action_types():
    scen = ScenarioModel(
        name="All Actions",
        actions=[
            ActionBlock(
                id="act-click",
                type=ActionType.MOUSE_CLICK,
                params=MouseClickParams(x=100, y=200, button="left", clicks=1),
                description="Click test",
            ),
            ActionBlock(
                id="act-key",
                type=ActionType.KEY_INPUT,
                params=KeyInputParams(keys=["ctrl", "c"], action="press"),
                description="Key test",
            ),
            ActionBlock(
                id="act-wait",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=500),
                description="Wait test",
            ),
            ActionBlock(
                id="act-img",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(template_path="templates/btn.png", mode="appear"),
                description="Image wait test",
            ),
        ],
    )
    d = scenario_to_dict(scen)
    assert len(d["actions"]) == 4
    assert d["actions"][0]["type"] == "MOUSE_CLICK"
    assert d["actions"][1]["type"] == "KEY_INPUT"
    assert d["actions"][2]["type"] == "FIXED_WAIT"
    assert d["actions"][3]["type"] == "IMAGE_WAIT"
    assert d["actions"][0]["params"]["x"] == 100
    assert d["actions"][1]["params"]["keys"] == ["ctrl", "c"]
    assert d["actions"][2]["params"]["duration_ms"] == 500
    assert d["actions"][3]["params"]["template_path"] == "templates/btn.png"


# ---------------------------------------------------------------------------
# TP03: test_scenario_from_dict_valid
# ---------------------------------------------------------------------------

def test_scenario_from_dict_valid():
    raw_data = {
        "name": "Reconstructed Scenario",
        "version": "1.0.0",
        "loop_count": 2,
        "description": "Valid reconstruction",
        "actions": [
            {
                "id": "act-1",
                "type": "MOUSE_CLICK",
                "params": {"x": 300, "y": 400, "button": "right", "clicks": 2},
                "enabled": True,
                "description": "Right click twice",
            },
            {
                "id": "act-2",
                "type": "KEY_INPUT",
                "params": {"keys": ["enter"], "action": "press"},
                "enabled": True,
                "description": "Press enter",
            },
            {
                "id": "act-3",
                "type": "FIXED_WAIT",
                "params": {"duration_ms": 250},
                "enabled": False,
                "description": "Disabled wait",
            },
            {
                "id": "act-4",
                "type": "IMAGE_WAIT",
                "params": {
                    "template_path": "templates/icon.png",
                    "mode": "disappear",
                    "confidence": 0.9,
                    "timeout_seconds": 3.0,
                    "roi": [0, 0, 800, 600],
                    "timeout_action": "skip",
                    "poll_interval_seconds": 0.2,
                },
                "enabled": True,
                "description": "Wait for icon to disappear",
            },
        ],
    }
    scen = scenario_from_dict(raw_data)
    assert scen.name == "Reconstructed Scenario"
    assert scen.version == "1.0.0"
    assert scen.loop_count == 2
    assert len(scen.actions) == 4
    assert scen.actions[0].type == ActionType.MOUSE_CLICK
    assert isinstance(scen.actions[0].params, MouseClickParams)
    assert scen.actions[0].params.x == 300
    assert scen.actions[1].type == ActionType.KEY_INPUT
    assert isinstance(scen.actions[1].params, KeyInputParams)
    assert scen.actions[2].type == ActionType.FIXED_WAIT
    assert scen.actions[2].enabled is False
    assert scen.actions[3].type == ActionType.IMAGE_WAIT
    assert scen.actions[3].params.roi == (0, 0, 800, 600)


# ---------------------------------------------------------------------------
# TP04: test_round_trip_fidelity
# ---------------------------------------------------------------------------

def test_round_trip_fidelity(tmp_path: Path):
    file_path = tmp_path / "round_trip.json"
    original = ScenarioModel(
        name="Round Trip Scenario",
        version="1.0.0",
        loop_count=5,
        description="Fidelity test",
        actions=[
            ActionBlock(
                id="act-1",
                type=ActionType.MOUSE_CLICK,
                params=MouseClickParams(x=120, y=240, button="left", clicks=1, interval_ms=10, press_duration_ms=60),
                enabled=True,
                description="Click block",
            ),
            ActionBlock(
                id="act-2",
                type=ActionType.KEY_INPUT,
                params=KeyInputParams(keys=["esc"], action="press", press_duration_ms=40),
                enabled=True,
                description="Escape key",
            ),
            ActionBlock(
                id="act-3",
                type=ActionType.FIXED_WAIT,
                params=FixedWaitParams(duration_ms=300),
                enabled=False,
                description="Sleep",
            ),
            ActionBlock(
                id="act-4",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(template_path="templates/btn.png", mode="appear", confidence=0.85, timeout_seconds=4.0),
                enabled=True,
                description="Image wait",
            ),
        ],
    )
    save_scenario(original, file_path)
    loaded = load_scenario(file_path)

    assert loaded.name == original.name
    assert loaded.version == original.version
    assert loaded.loop_count == original.loop_count
    assert loaded.description == original.description
    assert len(loaded.actions) == len(original.actions)
    for orig_act, load_act in zip(original.actions, loaded.actions):
        assert orig_act.id == load_act.id
        assert orig_act.type == load_act.type
        assert orig_act.enabled == load_act.enabled
        assert orig_act.description == load_act.description
        assert orig_act.params.to_dict() == load_act.params.to_dict()


# ---------------------------------------------------------------------------
# TP05: test_round_trip_with_unicode
# ---------------------------------------------------------------------------

def test_round_trip_with_unicode(tmp_path: Path):
    file_path = tmp_path / "korean_macro.json"
    original = ScenarioModel(
        name="일일 퀘스트 자동화",
        description="블루스택 반복 퀘스트 자동 클리어 매크로",
        actions=[
            ActionBlock(
                id="act-kr-1",
                type=ActionType.MOUSE_CLICK,
                params=MouseClickParams(x=500, y=300),
                description="보상 수령 버튼 클릭",
            )
        ],
    )
    save_scenario(original, file_path)
    loaded = load_scenario(file_path)
    assert loaded.name == "일일 퀘스트 자동화"
    assert loaded.description == "블루스택 반복 퀘스트 자동 클리어 매크로"
    assert loaded.actions[0].description == "보상 수령 버튼 클릭"


# ---------------------------------------------------------------------------
# TP06: test_round_trip_with_disabled_actions
# ---------------------------------------------------------------------------

def test_round_trip_with_disabled_actions(tmp_path: Path):
    file_path = tmp_path / "disabled.json"
    original = ScenarioModel(
        name="Toggle Test",
        actions=[
            ActionBlock(id="a1", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=10), enabled=True),
            ActionBlock(id="a2", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=20), enabled=False),
            ActionBlock(id="a3", type=ActionType.FIXED_WAIT, params=FixedWaitParams(duration_ms=30), enabled=True),
        ],
    )
    save_scenario(original, file_path)
    loaded = load_scenario(file_path)
    assert [a.enabled for a in loaded.actions] == [True, False, True]


# ---------------------------------------------------------------------------
# TP07: test_round_trip_infinite_loop
# ---------------------------------------------------------------------------

def test_round_trip_infinite_loop(tmp_path: Path):
    file_path = tmp_path / "infinite.json"
    original = ScenarioModel(name="Infinite Loop", loop_count=0)
    save_scenario(original, file_path)
    loaded = load_scenario(file_path)
    assert loaded.loop_count == 0


# ---------------------------------------------------------------------------
# TP08: test_relative_path_storage_posix
# ---------------------------------------------------------------------------

def test_relative_path_storage_posix():
    scen = ScenarioModel(
        name="Path Test",
        actions=[
            ActionBlock(
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(template_path=r"templates\sub\btn.png"),
            )
        ],
    )
    d = scenario_to_dict(scen)
    stored_path = d["actions"][0]["params"]["template_path"]
    assert stored_path == "templates/sub/btn.png"
    assert "\\" not in stored_path


# ---------------------------------------------------------------------------
# TP09: test_absolute_path_relativization_on_save
# ---------------------------------------------------------------------------

def test_absolute_path_relativization_on_save(tmp_path: Path):
    scenario_dir = tmp_path / "my_macro"
    scenario_dir.mkdir(parents=True, exist_ok=True)
    template_file = scenario_dir / "templates" / "btn.png"
    template_file.parent.mkdir(parents=True, exist_ok=True)
    template_file.write_text("fake image")

    scenario_file = scenario_dir / "test.json"
    scen = ScenarioModel(
        name="Relativization Test",
        actions=[
            ActionBlock(
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(template_path=str(template_file.resolve())),
            )
        ],
    )
    save_scenario(scen, scenario_file)

    # Inspect stored JSON directly
    with open(scenario_file, "r", encoding="utf-8") as f:
        stored_data = json.load(f)
    assert stored_data["actions"][0]["params"]["template_path"] == "templates/btn.png"


# ---------------------------------------------------------------------------
# TP10: test_resolve_template_path
# ---------------------------------------------------------------------------

def test_resolve_template_path():
    scenario_path = Path("/path/to/scenario.json")
    resolved = resolve_template_path("templates/btn.png", scenario_path)
    assert resolved == Path("/path/to/templates/btn.png").resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive and UNC paths")
@pytest.mark.parametrize("template", [
    "{tmp}\\btn.png",       # chosen with Browse after the scenario was saved (backslashes)
    "{tmp_posix}/btn.png",  # the same path as Qt's file dialog and drag-and-drop give it
    "Z:/images/btn.png",    # another drive than the scenario: saved as an absolute path
    "\\\\server\\share\\btn.png",
])
def test_resolve_template_path_keeps_a_windows_absolute_path(tmp_path, template):
    template = template.format(tmp=tmp_path, tmp_posix=tmp_path.as_posix())
    scenario_dir = tmp_path / "scenarios"
    assert resolve_template_path(template, scenario_dir / "s.json") == Path(template).resolve()


# ---------------------------------------------------------------------------
# TP11: test_load_scenario_nonexistent_file
# ---------------------------------------------------------------------------

def test_load_scenario_nonexistent_file():
    with pytest.raises(FileNotFoundError):
        load_scenario("/non/existent/path/macro.json")


# ---------------------------------------------------------------------------
# TP12: test_load_scenario_malformed_json_syntax
# ---------------------------------------------------------------------------

def test_load_scenario_malformed_json_syntax(tmp_path: Path):
    broken_file = tmp_path / "broken.json"
    broken_file.write_text('{ "name": "Broken", actions: [ ')
    with pytest.raises(PersistenceError):
        load_scenario(broken_file)


# ---------------------------------------------------------------------------
# TP13: test_load_scenario_missing_required_root_field
# ---------------------------------------------------------------------------

def test_load_scenario_missing_required_root_field():
    # Missing name
    with pytest.raises(ValidationError):
        scenario_from_dict({"version": "1.0.0", "loop_count": 1, "actions": []})

    # Missing actions
    with pytest.raises(ValidationError):
        scenario_from_dict({"name": "Test", "version": "1.0.0", "loop_count": 1})


# ---------------------------------------------------------------------------
# TP14: test_load_scenario_invalid_root_field_types
# ---------------------------------------------------------------------------

def test_load_scenario_invalid_root_field_types():
    # Non-integer loop_count
    with pytest.raises(ValidationError):
        scenario_from_dict({"name": "Test", "version": "1.0.0", "loop_count": "three", "actions": []})

    # Non-list actions
    with pytest.raises(ValidationError):
        scenario_from_dict({"name": "Test", "version": "1.0.0", "loop_count": 1, "actions": "not_a_list"})


# ---------------------------------------------------------------------------
# TP15: test_load_scenario_unsupported_version
# ---------------------------------------------------------------------------

def test_load_scenario_unsupported_version():
    with pytest.raises(UnsupportedVersionError):
        scenario_from_dict({"name": "Test", "version": "99.0.0", "loop_count": 1, "actions": []})


# ---------------------------------------------------------------------------
# TP16: test_load_scenario_unknown_action_type
# ---------------------------------------------------------------------------

def test_load_scenario_unknown_action_type():
    with pytest.raises(ValidationError):
        scenario_from_dict(
            {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {"id": "a1", "type": "VOICE_COMMAND", "params": {}}
                ],
            }
        )


# ---------------------------------------------------------------------------
# TP17: test_load_scenario_invalid_action_params
# ---------------------------------------------------------------------------

def test_load_scenario_invalid_action_params():
    # MOUSE_CLICK missing required coords
    with pytest.raises(ValidationError):
        scenario_from_dict(
            {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {"id": "a1", "type": "MOUSE_CLICK", "params": {"button": "left"}}
                ],
            }
        )

    # MOUSE_CLICK with negative clicks
    with pytest.raises(ValidationError):
        scenario_from_dict(
            {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {"id": "a1", "type": "MOUSE_CLICK", "params": {"x": 10, "y": 20, "clicks": -2}}
                ],
            }
        )


# ---------------------------------------------------------------------------
# TP18: test_load_scenario_duplicate_action_ids
# ---------------------------------------------------------------------------

def test_load_scenario_duplicate_action_ids():
    with pytest.raises(ValidationError, match="Duplicate action block IDs"):
        scenario_from_dict(
            {
                "name": "Test",
                "version": "1.0.0",
                "loop_count": 1,
                "actions": [
                    {"id": "uuid-123", "type": "FIXED_WAIT", "params": {"duration_ms": 100}},
                    {"id": "uuid-123", "type": "FIXED_WAIT", "params": {"duration_ms": 200}},
                ],
            }
        )


# ---------------------------------------------------------------------------
# TP19: test_save_scenario_directory_not_found
# ---------------------------------------------------------------------------

def test_save_scenario_directory_not_found():
    scen = ScenarioModel(name="Test")
    # Non-existent parent directory with make_dirs=False must raise PersistenceError
    with pytest.raises(PersistenceError):
        save_scenario(scen, Path("/non_existent_folder_abc_xyz/macro.json"), make_dirs=False)


# ---------------------------------------------------------------------------
# TP20: test_round_trip_empty_actions
# ---------------------------------------------------------------------------

def test_round_trip_empty_actions(tmp_path: Path):
    file_path = tmp_path / "empty_actions.json"
    original = ScenarioModel(name="Empty Actions", actions=[])
    save_scenario(original, file_path)
    loaded = load_scenario(file_path)
    assert loaded.actions == []
    assert loaded.name == "Empty Actions"
