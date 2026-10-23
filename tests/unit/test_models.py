"""
tests.unit.test_models
======================
Unit tests covering all specifications (TM01 - TM34) for action_macro.core.models.
"""

import uuid
import pytest

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    FixedWaitParams,
    ImageMode,
    ImageWaitParams,
    KeyAction,
    KeyInputParams,
    MouseButton,
    MouseClickParams,
    ScenarioModel,
    TimeoutAction,
    ValidationError,
)


# ---------------------------------------------------------------------------
# TM01: ActionType Enum
# ---------------------------------------------------------------------------

def test_action_type_enum_values():
    assert ActionType.MOUSE_CLICK.value == "MOUSE_CLICK"
    assert ActionType.KEY_INPUT.value == "KEY_INPUT"
    assert ActionType.FIXED_WAIT.value == "FIXED_WAIT"
    assert ActionType.IMAGE_WAIT.value == "IMAGE_WAIT"
    assert isinstance(ActionType.MOUSE_CLICK, str)
    assert isinstance(ActionType.KEY_INPUT, str)
    assert isinstance(ActionType.FIXED_WAIT, str)
    assert isinstance(ActionType.IMAGE_WAIT, str)

    # from_str parsing
    assert ActionType.from_str("mouse_click") == ActionType.MOUSE_CLICK
    assert ActionType.from_str("MOUSE_CLICK") == ActionType.MOUSE_CLICK
    assert ActionType.from_str(ActionType.MOUSE_CLICK) == ActionType.MOUSE_CLICK

    with pytest.raises(ValueError):
        ActionType.from_str("unknown_action")

    with pytest.raises(TypeError):
        ActionType.from_str(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TM02 - TM07: MouseClickParams
# ---------------------------------------------------------------------------

def test_mouse_click_params_default_values():
    p = MouseClickParams(x=100, y=200)
    assert p.x == 100
    assert p.y == 200
    assert p.button == "left"
    assert p.clicks == 1
    assert p.interval_ms == 0
    assert p.press_duration_ms == 50


def test_mouse_click_params_custom_values():
    p = MouseClickParams(x=500, y=600, button="right", clicks=2, interval_ms=50, press_duration_ms=100)
    assert p.x == 500
    assert p.y == 600
    assert p.button == "right"
    assert p.clicks == 2
    assert p.interval_ms == 50
    assert p.press_duration_ms == 100


def test_mouse_click_params_invalid_button():
    with pytest.raises(ValueError):
        MouseClickParams(x=10, y=20, button="middle_click")


def test_mouse_click_params_invalid_clicks():
    with pytest.raises(ValueError):
        MouseClickParams(x=10, y=20, clicks=0)

    with pytest.raises(ValueError):
        MouseClickParams(x=10, y=20, clicks=-1)


def test_mouse_click_params_negative_interval():
    with pytest.raises(ValueError):
        MouseClickParams(x=10, y=20, interval_ms=-10)


def test_mouse_click_params_non_integer_coords():
    with pytest.raises(TypeError):
        MouseClickParams(x="100", y=200)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        MouseClickParams(x=100, y="200")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        MouseClickParams(x=True, y=200)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        MouseClickParams(x=100, y=False)  # type: ignore[arg-type]


def test_mouse_click_params_negative_coords_multi_monitor():
    # Edge case 3: negative coordinates are valid for multi-monitor setups
    p = MouseClickParams(x=-50, y=-100)
    assert p.x == -50
    assert p.y == -100


# ---------------------------------------------------------------------------
# TM08 - TM12: KeyInputParams
# ---------------------------------------------------------------------------

def test_key_input_params_default_values():
    p = KeyInputParams(keys=["enter"])
    assert p.keys == ["enter"]
    assert p.action == "press"


def test_key_input_params_combination_keys():
    p = KeyInputParams(keys=["ctrl", "shift", "esc"], action="down")
    assert p.keys == ["ctrl", "shift", "esc"]
    assert p.action == "down"


def test_key_input_params_empty_keys():
    with pytest.raises(ValueError):
        KeyInputParams(keys=[])


def test_key_input_params_invalid_action():
    with pytest.raises(ValueError):
        KeyInputParams(keys=["a"], action="hold")


def test_key_input_params_invalid_key_elements():
    with pytest.raises(ValueError):
        KeyInputParams(keys=["a", ""])

    with pytest.raises(ValueError):
        KeyInputParams(keys=["a", "   "])

    with pytest.raises(TypeError):
        KeyInputParams(keys=["a", None])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# TM13 - TM16: FixedWaitParams
# ---------------------------------------------------------------------------

def test_fixed_wait_params_valid():
    p = FixedWaitParams(duration_ms=1500)
    assert p.duration_ms == 1500


def test_fixed_wait_params_zero_duration():
    p = FixedWaitParams(duration_ms=0)
    assert p.duration_ms == 0


def test_fixed_wait_params_negative_duration():
    with pytest.raises(ValueError):
        FixedWaitParams(duration_ms=-100)


def test_fixed_wait_params_non_int_duration():
    with pytest.raises(TypeError):
        FixedWaitParams(duration_ms=1.5)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        FixedWaitParams(duration_ms=False)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TM17 - TM24: ImageWaitParams
# ---------------------------------------------------------------------------

def test_image_wait_params_default_values():
    p = ImageWaitParams(template_path="templates/btn.png")
    assert p.template_path == "templates/btn.png"
    assert p.mode == "appear"
    assert p.confidence == 0.8
    assert p.timeout_seconds == 10.0
    assert p.roi is None
    assert p.timeout_action == "raise"
    assert p.poll_interval_seconds == 0.5


def test_image_wait_params_custom_values():
    p = ImageWaitParams(
        template_path="t.png",
        mode="disappear",
        confidence=0.95,
        timeout_seconds=5.0,
        roi=(10, 20, 100, 200),
        timeout_action="skip",
        poll_interval_seconds=0.1,
    )
    assert p.template_path == "t.png"
    assert p.mode == "disappear"
    assert p.confidence == 0.95
    assert p.timeout_seconds == 5.0
    assert p.roi == (10, 20, 100, 200)
    assert p.timeout_action == "skip"
    assert p.poll_interval_seconds == 0.1


def test_image_wait_params_empty_path():
    with pytest.raises(ValueError):
        ImageWaitParams(template_path="")

    with pytest.raises(ValueError):
        ImageWaitParams(template_path="   ")


def test_image_wait_params_invalid_mode():
    with pytest.raises(ValueError):
        ImageWaitParams(template_path="t.png", mode="exists")


def test_image_wait_params_confidence_out_of_range():
    with pytest.raises(ValueError):
        ImageWaitParams(template_path="t.png", confidence=-0.1)

    with pytest.raises(ValueError):
        ImageWaitParams(template_path="t.png", confidence=1.05)


def test_image_wait_params_negative_timeout():
    with pytest.raises(ValueError):
        ImageWaitParams(template_path="t.png", timeout_seconds=-1.0)


def test_image_wait_params_invalid_roi():
    # Width 0 rejected
    with pytest.raises(ValueError):
        ImageWaitParams(template_path="t.png", roi=(0, 0, 0, 100))

    # Height 0 rejected
    with pytest.raises(ValueError):
        ImageWaitParams(template_path="t.png", roi=(0, 0, 100, 0))

    # Length 3 rejected
    with pytest.raises(ValueError):
        ImageWaitParams(template_path="t.png", roi=(10, 20, 50))  # type: ignore[arg-type]


def test_image_wait_params_invalid_timeout_action():
    with pytest.raises(ValueError):
        ImageWaitParams(template_path="t.png", timeout_action="ignore")


# ---------------------------------------------------------------------------
# TM25 - TM28: ActionBlock
# ---------------------------------------------------------------------------

def test_action_block_auto_generates_uuid():
    block = ActionBlock(type=ActionType.FIXED_WAIT, params={"duration_ms": 100})
    assert isinstance(block.id, str)
    assert len(block.id) == 36
    # Verify valid UUID string
    parsed = uuid.UUID(block.id)
    assert str(parsed) == block.id


def test_action_block_explicit_id_preserved():
    block = ActionBlock(id="custom-1", type=ActionType.FIXED_WAIT, params={"duration_ms": 100})
    assert block.id == "custom-1"


def test_action_block_empty_id_rejected():
    with pytest.raises(ValueError):
        ActionBlock(id="", type=ActionType.FIXED_WAIT, params={"duration_ms": 100})

    with pytest.raises(ValueError):
        ActionBlock(id="   ", type=ActionType.FIXED_WAIT, params={"duration_ms": 100})


def test_action_block_defaults():
    block = ActionBlock(type=ActionType.MOUSE_CLICK, params={"x": 0, "y": 0})
    assert block.enabled is True
    assert block.description == ""
    assert isinstance(block.params, MouseClickParams)
    assert block.params.x == 0
    assert block.params.y == 0


# ---------------------------------------------------------------------------
# TM29 - TM34: ScenarioModel
# ---------------------------------------------------------------------------

def test_scenario_model_default_values():
    s = ScenarioModel(name="My Macro")
    assert s.name == "My Macro"
    assert s.version == "1.0.0"
    assert s.loop_count == 1
    assert s.actions == []


def test_scenario_model_infinite_loop():
    s = ScenarioModel(name="Loop", loop_count=0)
    assert s.loop_count == 0


def test_scenario_model_empty_name_rejected():
    with pytest.raises(ValueError):
        ScenarioModel(name="")

    with pytest.raises(ValueError):
        ScenarioModel(name="   ")


def test_scenario_model_negative_loop_count_rejected():
    with pytest.raises(ValueError):
        ScenarioModel(name="Test", loop_count=-5)


def test_scenario_model_duplicate_action_ids_rejected():
    act1 = ActionBlock(id="duplicate-id", type=ActionType.FIXED_WAIT, params={"duration_ms": 10})
    act2 = ActionBlock(id="duplicate-id", type=ActionType.FIXED_WAIT, params={"duration_ms": 20})
    with pytest.raises(ValueError, match="Duplicate action block IDs"):
        ScenarioModel(name="Test", actions=[act1, act2])


def test_scenario_model_invalid_actions_list():
    with pytest.raises(TypeError):
        ScenarioModel(name="Test", actions=["not_an_action_block"])  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# Additional Helper Methods & Manipulation Tests
# ---------------------------------------------------------------------------

def test_scenario_model_crud_and_clone():
    scenario = ScenarioModel(name="CRUD Test")
    act1 = ActionBlock(id="act-1", type=ActionType.FIXED_WAIT, params={"duration_ms": 100})
    act2 = ActionBlock(id="act-2", type=ActionType.MOUSE_CLICK, params={"x": 50, "y": 50})

    scenario.add_action(act1)
    scenario.add_action(act2)
    assert len(scenario.actions) == 2
    assert scenario.get_action("act-1") == act1
    assert scenario.get_action("non-existent") is None

    # Move action
    scenario.move_action(0, 1)
    assert scenario.actions[0].id == "act-2"
    assert scenario.actions[1].id == "act-1"

    # Deep copy clone
    cloned = scenario.clone()
    assert cloned.name == scenario.name
    assert len(cloned.actions) == 2
    cloned.actions[0].description = "Modified"
    assert scenario.actions[0].description == ""

    # Remove action
    assert scenario.remove_action("act-1") is True
    assert len(scenario.actions) == 1
    assert scenario.remove_action("non-existent") is False


# ---------------------------------------------------------------------------
# Key Mapping Unit Tests
# ---------------------------------------------------------------------------

def test_key_mapping_normalization_and_resolution():
    from action_macro.core.key_mapping import (
        format_key_combo,
        is_valid_key,
        normalize_key_name,
        parse_key_combination,
        resolve_key_for_platform,
        to_pynput_key,
    )

    assert normalize_key_name("return") == "enter"
    assert normalize_key_name("escape") == "esc"
    assert normalize_key_name("control") == "ctrl"
    assert normalize_key_name("option") == "alt"
    assert normalize_key_name("command") == "cmd"
    assert normalize_key_name("primary_modifier") == "cmd_or_ctrl"

    # Platform resolution
    assert resolve_key_for_platform("cmd_or_ctrl", platform_name="darwin") == "cmd"
    assert resolve_key_for_platform("cmd_or_ctrl", platform_name="win32") == "ctrl"
    assert resolve_key_for_platform("cmd_or_ctrl", platform_name="linux") == "ctrl"

    # Combination parsing
    assert parse_key_combination("ctrl+shift+s") == ["ctrl", "shift", "s"]
    assert parse_key_combination("cmd_or_ctrl + c") == ["cmd_or_ctrl", "c"]
    assert parse_key_combination("ctrl++") == ["ctrl", "+"]
    assert parse_key_combination("") == []

    # Validity
    assert is_valid_key("enter") is True
    assert is_valid_key("a") is True
    assert is_valid_key("nonexistent_special_key") is False

    # Formatting
    mac_fmt = format_key_combo(["cmd", "shift", "a"], platform_name="darwin")
    assert "⌘" in mac_fmt
    assert "⇧" in mac_fmt
    win_fmt = format_key_combo(["ctrl", "shift", "a"], platform_name="win32")
    assert "Ctrl" in win_fmt
    assert "Shift" in win_fmt

    # Pynput bridge
    k = to_pynput_key("enter")
    assert k is not None

