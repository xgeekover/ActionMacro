"""Clicking where the last Image wait step found its image (``MouseClickParams.use_last_matched``).

These tests use the real models, persistence, engine and inspector form. The older
``use_last_matched`` tests run the engine on plain dict params from
``tests/mocks/contract.py``, which is why the app's model could reject the field
without any test noticing (found on the 0.1.3 Windows check).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from action_macro.core.models import (
    ActionBlock,
    ActionType,
    ImageWaitParams,
    MouseClickParams,
    ScenarioModel,
)
from action_macro.core.persistence import ScenarioPersistence
from action_macro.gui import step_text
from action_macro.gui.property_inspector import MouseClickForm, PropertyInspector
from action_macro.runner.execution_engine import ExecutionEngine
from tests.mocks.mock_input import MockInputDriver
from tests.mocks.mock_screen import MockScreenDriver

#: Where the sample template (60 x 30) is drawn, and the centre a found click lands on.
PATCH_X, PATCH_Y = 700, 350
FOUND_CENTRE = (PATCH_X + 30, PATCH_Y + 15)


def _found_click(**kwargs) -> ActionBlock:
    params = dict(x=10, y=20, use_last_matched=True)
    params.update(kwargs)
    return ActionBlock(type=ActionType.MOUSE_CLICK, params=MouseClickParams(**params))


def _image_wait(template: Path, **kwargs) -> ActionBlock:
    params = dict(template_path=str(template), mode="appear", timeout_seconds=1.0)
    params.update(kwargs)
    return ActionBlock(type=ActionType.IMAGE_WAIT, params=ImageWaitParams(**params))


def _run(actions, mock_input: MockInputDriver, mock_screen: MockScreenDriver) -> ExecutionEngine:
    scenario = ScenarioModel(name="Found image", actions=actions, action_delay_ms=0)
    engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
    assert engine.run() is True
    return engine


def _warnings(engine: ExecutionEngine) -> list:
    return [msg for level, msg in engine.signals.logs if level == "WARNING"]


# -----------------------------------------------------------------------------
# Model and file format
# -----------------------------------------------------------------------------

def test_the_click_model_accepts_the_found_image_target():
    params = MouseClickParams(x=1, y=2, use_last_matched=True)
    assert params.use_last_matched is True
    assert MouseClickParams(x=1, y=2).use_last_matched is False
    block = ActionBlock.from_dict({"type": "mouse_click", "params": {"x": 1, "y": 2, "use_last_matched": True}})
    assert block.params.use_last_matched is True


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_the_found_image_flag_must_be_a_bool(value):
    with pytest.raises(TypeError, match="use_last_matched"):
        MouseClickParams(x=1, y=2, use_last_matched=value)


def test_only_found_image_clicks_write_the_field(tmp_path):
    """Other click steps are saved exactly as before, so 0.1.3 and earlier still open them."""
    assert "use_last_matched" not in MouseClickParams(x=1, y=2).to_dict()
    assert MouseClickParams(x=1, y=2, use_last_matched=True).to_dict()["use_last_matched"] is True

    scenario = ScenarioModel(name="Round trip", actions=[
        ActionBlock(id="point", type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=5, y=6)),
        ActionBlock(id="found", type=ActionType.MOUSE_CLICK,
                    params=MouseClickParams(x=7, y=8, use_last_matched=True)),
    ])
    path = tmp_path / "found.json"
    ScenarioPersistence.save(scenario, path)

    raw = {a["id"]: a["params"] for a in json.loads(path.read_text(encoding="utf-8"))["actions"]}
    assert "use_last_matched" not in raw["point"]
    assert raw["found"]["use_last_matched"] is True

    loaded = ScenarioPersistence.load(path)
    by_id = {a.id: a.params for a in loaded.actions}
    assert by_id["point"].use_last_matched is False
    assert by_id["found"].use_last_matched is True
    assert (by_id["found"].x, by_id["found"].y) == (7, 8)  # the point is kept for switching back


# -----------------------------------------------------------------------------
# Engine
# -----------------------------------------------------------------------------

def test_a_found_image_click_lands_on_the_image(mock_input, mock_screen, sample_template, sample_template_file):
    mock_screen.inject_patch(sample_template, PATCH_X, PATCH_Y)
    engine = _run([_image_wait(sample_template_file), _found_click()], mock_input, mock_screen)
    mock_input.assert_last_click_precision(*FOUND_CENTRE, tolerance=1)
    assert _warnings(engine) == []


def test_a_point_click_still_uses_its_point(mock_input, mock_screen, sample_template, sample_template_file):
    mock_screen.inject_patch(sample_template, PATCH_X, PATCH_Y)
    _run([_image_wait(sample_template_file), _found_click(use_last_matched=False)], mock_input, mock_screen)
    mock_input.assert_last_click_precision(10, 20, tolerance=0)


def test_without_a_found_image_the_click_is_skipped_not_sent_to_its_point(mock_input, mock_screen):
    engine = _run([_found_click()], mock_input, mock_screen)
    assert mock_input.clicks == []
    assert any("no image has been found" in msg for msg in _warnings(engine))


def test_an_image_that_did_not_appear_forgets_the_earlier_match(
    mock_input, mock_screen, sample_template, sample_template_file, tmp_path
):
    """Wait A finds A, wait B times out and the run goes on: the next found click must not hit A."""
    mock_screen.inject_patch(sample_template, PATCH_X, PATCH_Y)
    absent = MockScreenDriver.save_template_file(
        MockScreenDriver.create_test_template(width=40, height=40, pattern="circle", color=(20, 160, 60)),
        tmp_path / "templates" / "absent.png",
    )
    engine = _run([
        _image_wait(sample_template_file),
        _found_click(),
        _image_wait(absent, timeout_seconds=0.1, timeout_action="skip"),
        _found_click(),
    ], mock_input, mock_screen)
    assert len(mock_input.clicks) == 1  # only the click after the image that was found
    mock_input.assert_last_click_precision(*FOUND_CENTRE, tolerance=1)
    assert len(_warnings(engine)) == 1


def test_a_disappear_wait_keeps_the_found_position(
    mock_input, mock_screen, sample_template, sample_template_file, tmp_path
):
    """Waiting for something else to go away does not change where the found image was."""
    mock_screen.inject_patch(sample_template, PATCH_X, PATCH_Y)
    gone = MockScreenDriver.save_template_file(
        MockScreenDriver.create_test_template(width=40, height=40, pattern="circle", color=(20, 160, 60)),
        tmp_path / "templates" / "gone.png",
    )
    _run([
        _image_wait(sample_template_file),
        _image_wait(gone, mode="disappear", timeout_seconds=0.5),
        _found_click(),
    ], mock_input, mock_screen)
    mock_input.assert_last_click_precision(*FOUND_CENTRE, tolerance=1)


def test_each_run_starts_without_a_found_image(mock_input, mock_screen):
    scenario = ScenarioModel(name="Second run", actions=[_found_click()], action_delay_ms=0)
    engine = ExecutionEngine(scenario, input_driver=mock_input, screen_driver=mock_screen)
    engine._last_detected_coords = (300, 400)  # left over from an earlier run
    assert engine.run() is True
    assert mock_input.clicks == []


# -----------------------------------------------------------------------------
# Inspector form and step text
# -----------------------------------------------------------------------------

def test_the_click_form_switches_between_a_point_and_the_found_image(qtbot):
    form = MouseClickForm()
    qtbot.addWidget(form)
    form.show()
    modified = []
    form.modified.connect(lambda: modified.append(1))

    def shown(widget) -> bool:
        return widget.isVisibleTo(form)

    form.set_values(MouseClickParams(x=10, y=20))
    assert not form.uses_found_image()
    assert shown(form.row_point) and shown(form.row_pick) and not shown(form.lbl_found_help)
    assert form.get_values().use_last_matched is False

    form.combo_target.setCurrentIndex(1)  # the "Found image" segment
    assert modified, "changing the target must mark the step as edited"
    assert not shown(form.row_point) and not shown(form.row_pick) and shown(form.lbl_found_help)
    values = form.get_values()
    assert values.use_last_matched is True and (values.x, values.y) == (10, 20)

    form.set_values(MouseClickParams(x=1, y=2, use_last_matched=True))
    assert form.uses_found_image()
    form.set_coordinates(300, 400)  # a picked point means "click this point"
    assert not form.uses_found_image() and (form.get_values().x, form.get_values().y) == (300, 400)


def test_the_inspector_saves_the_found_image_target_on_the_step(qtbot):
    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    action = ActionBlock(type=ActionType.MOUSE_CLICK, params=MouseClickParams(x=10, y=20))
    inspector.set_action(action)
    inspector.form_mouse.combo_target.setCurrentIndex(1)
    assert action.params.use_last_matched is True
    assert (action.params.x, action.params.y) == (10, 20)


@pytest.mark.parametrize(("clicks", "expected"), [
    (1, "Click left at the found image"),
    (2, "Double-click left at the found image"),
])
def test_the_step_reads_the_found_image(clicks, expected):
    params = MouseClickParams(x=10, y=20, clicks=clicks, use_last_matched=True)
    assert step_text.click_summary(params) == expected
    assert step_text.click_summary(MouseClickParams(x=10, y=20)) == "Click left at (10, 20)"
