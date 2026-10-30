"""Key input steps that type text (``action: "type"``): model, engine, adapter, card text, inspector."""

from __future__ import annotations

import ctypes
import importlib.util
import json
import sys
import threading
import time
import types

import pytest

from action_macro.core.models import ActionBlock, ActionType, KeyAction, KeyInputParams, ScenarioModel
from action_macro.core.persistence import load_scenario, save_scenario
from action_macro.engine import input_adapter
from action_macro.engine.input_adapter import PynputAdapter, make_win32_unicode_sender, utf16_code_units
from action_macro.gui import step_text
from action_macro.runner.execution_engine import ExecutionEngine
from tests.mocks.mock_input import MockInputDriver


def _type_step(text: str = "myid123", **params) -> ActionBlock:
    return ActionBlock(id="t", type=ActionType.KEY_INPUT,
                       params=KeyInputParams(keys=params.pop("keys", []), action="type", text=text, **params))


# -----------------------------------------------------------------------------
# Model and file format
# -----------------------------------------------------------------------------

def test_type_mode_needs_text_but_no_keys():
    params = KeyInputParams(keys=[], action="TYPE", text="myid123")
    assert params.action == "type" == KeyAction.TYPE
    assert params.keys == [] and params.text == "myid123"
    assert KeyInputParams(keys=[], action="type", text=" ").text == " "  # typed as written, not stripped

    with pytest.raises(ValueError, match="non-empty string in 'type' mode"):
        KeyInputParams(keys=["a"], action="type", text="")
    with pytest.raises(TypeError, match="must be a string in 'type' mode"):
        KeyInputParams(keys=["a"], action="type")
    with pytest.raises(ValueError, match="non-empty list"):
        KeyInputParams(keys=[], action="press")  # the key modes still need keys
    # A hand-written type step needs no "keys" at all.
    block = ActionBlock.from_dict({"type": "KEY_INPUT", "params": {"action": "type", "text": "id"}})
    assert block.params.keys == [] and block.params.text == "id"


def test_only_type_steps_write_text_so_other_key_steps_keep_their_old_format():
    press = KeyInputParams(keys=["enter"], action="press", text="left over")
    assert press.to_dict() == {"keys": ["enter"], "action": "press", "modifiers": None, "press_duration_ms": 50}
    typed = KeyInputParams(keys=["enter"], action="type", text="myid123")
    assert typed.to_dict()["text"] == "myid123" and typed.to_dict()["keys"] == ["enter"]


def test_type_step_survives_save_and_load(tmp_path):
    scenario = ScenarioModel(name="login", actions=[_type_step("내 아이디 id_01 😀")])
    path = tmp_path / "login.json"
    save_scenario(scenario, path)
    raw = json.loads(path.read_text(encoding="utf-8"))["actions"][0]["params"]
    assert raw["action"] == "type" and raw["text"] == "내 아이디 id_01 😀"
    loaded = load_scenario(path).actions[0].params
    assert (loaded.action, loaded.text, loaded.keys) == ("type", "내 아이디 id_01 😀", [])


# -----------------------------------------------------------------------------
# Engine
# -----------------------------------------------------------------------------

def test_engine_types_the_text_and_presses_no_key():
    driver = MockInputDriver()
    scenario = ScenarioModel(name="t", actions=[_type_step("myid123", keys=["enter"])])
    assert ExecutionEngine(scenario, input_driver=driver).run() is True
    assert [(e.event_type, e.text) for e in driver.events if e.event_type != "safety_flush"] == [("text", "myid123")]


def test_engine_hands_its_pause_and_stop_events_to_type_text():
    seen = {}

    class Driver(MockInputDriver):
        def type_text(self, text, interval=0.02, cancel_event=None, pause_event=None):
            seen.update(text=text, cancel=cancel_event, pause=pause_event)

    engine = ExecutionEngine(ScenarioModel(name="t", actions=[_type_step("id")]), input_driver=Driver())
    assert engine.run() is True
    assert seen == {"text": "id", "cancel": engine.cancel_event, "pause": engine.pause_event}


# -----------------------------------------------------------------------------
# Adapter: one character at a time; Unicode packets on Windows
# -----------------------------------------------------------------------------

class _Keyboard:
    def __init__(self):
        self.typed = []

    def type(self, text):
        self.typed.append(text)

    def press(self, key):
        pass

    def release(self, key):
        pass


class _Mouse:
    position = (0, 0)


def test_type_text_goes_through_the_text_sender_except_enter_and_tab():
    keyboard, sent = _Keyboard(), []
    adapter = PynputAdapter(mouse_controller=_Mouse(), keyboard_controller=keyboard,
                            platform_name="win32", text_sender=sent.append)
    adapter.type_text("id\t한😀\n", interval=0)
    assert sent == ["i", "d", "한", "😀"]
    assert keyboard.typed == ["\t", "\n"]  # Tab and Enter keys


def test_a_windows_line_break_is_one_enter():
    keyboard, sent = _Keyboard(), []
    adapter = PynputAdapter(mouse_controller=_Mouse(), keyboard_controller=keyboard, text_sender=sent.append)
    adapter.type_text("myid123\r\n", interval=0)  # a spreadsheet cell pasted into the field
    assert sent == list("myid123") and keyboard.typed == ["\n"]
    keyboard.typed.clear()
    adapter.type_text("a\rb\nc", interval=0)
    assert keyboard.typed == ["\r", "\n"]  # lone CR and LF stay one Enter each


def test_type_text_waits_while_paused_and_resumes_where_it_stopped():
    pause, cancel, sent = threading.Event(), threading.Event(), []

    def send(char):
        sent.append(char)
        if char == "b":
            pause.set()
            threading.Timer(0.15, pause.clear).start()

    adapter = PynputAdapter(mouse_controller=_Mouse(), keyboard_controller=_Keyboard(), text_sender=send)
    started = time.perf_counter()
    adapter.type_text("abcd", interval=0, cancel_event=cancel, pause_event=pause)
    assert sent == ["a", "b", "c", "d"]
    assert time.perf_counter() - started >= 0.14  # "c" waited for the resume

    sent.clear()
    pause.set()
    threading.Timer(0.05, cancel.set).start()
    adapter.type_text("xy", interval=0, cancel_event=cancel, pause_event=pause)
    assert sent == []  # stopped while paused: nothing typed


def test_type_text_without_a_sender_types_each_character_with_pynput():
    keyboard = _Keyboard()
    adapter = PynputAdapter(mouse_controller=_Mouse(), keyboard_controller=keyboard, platform_name="darwin")
    adapter.type_text("ab", interval=0)
    assert keyboard.typed == ["a", "b"]


def test_type_text_waits_between_characters_and_stops_when_cancelled(monkeypatch):
    cancel = threading.Event()
    sent, waits = [], []

    def wait(seconds, cancel_event=None, **_kwargs):
        waits.append(seconds)
        if len(sent) == 2:
            cancel.set()
            return False
        return True

    monkeypatch.setattr(input_adapter.WaitSystem, "wait", staticmethod(wait))
    adapter = PynputAdapter(mouse_controller=_Mouse(), keyboard_controller=_Keyboard(), text_sender=sent.append)
    adapter.type_text("abcd", interval=0.02, cancel_event=cancel)
    assert sent == ["a", "b"] and waits == [0.02, 0.02]

    sent.clear()
    adapter.type_text("xy", cancel_event=cancel)  # already cancelled: nothing typed
    assert sent == []


def test_adapter_uses_unicode_packets_only_on_windows_with_the_real_keyboard(monkeypatch):
    import pynput.keyboard

    monkeypatch.setattr(pynput.keyboard, "Controller", _Keyboard)  # no OS keyboard is touched
    monkeypatch.setattr(input_adapter, "make_win32_unicode_sender", lambda: "sender")
    # Only the adapter module's view of the OS changes (pynput keeps its own back end).
    monkeypatch.setattr(input_adapter, "sys", types.SimpleNamespace(platform="win32"))
    assert PynputAdapter(mouse_controller=_Mouse())._text_sender == "sender"
    assert PynputAdapter(mouse_controller=_Mouse(), keyboard_controller=_Keyboard())._text_sender is None
    monkeypatch.setattr(input_adapter, "sys", types.SimpleNamespace(platform="darwin"))
    assert PynputAdapter(mouse_controller=_Mouse())._text_sender is None


def test_utf16_code_units():
    assert utf16_code_units("a") == [0x61]
    assert utf16_code_units("한") == [0xD55C]
    assert utf16_code_units("😀") == [0xD83D, 0xDE00]  # surrogate pair


@pytest.fixture
def pynput_win32(monkeypatch):
    """pynput's real win32 structures, loaded on any OS with user32 stubbed out."""

    class _Function:
        def __call__(self, *args):
            return 0

    class _Library:
        def __getattr__(self, name):
            return _Function()

    class _Windll:
        def __getattr__(self, name):
            return _Library()

    monkeypatch.setattr(ctypes, "windll", _Windll(), raising=False)
    if not hasattr(ctypes, "WINFUNCTYPE"):
        monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    spec = importlib.util.find_spec("pynput._util.win32")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "pynput._util.win32", module)
    monkeypatch.setattr(importlib.import_module("pynput._util"), "win32", module, raising=False)
    return module


def test_windows_sender_builds_unicode_key_down_then_up_events(pynput_win32):
    calls = []

    def send_input(count, events, size):
        calls.append([(e.type, e.value.ki.wVk, e.value.ki.wScan, e.value.ki.dwFlags) for e in events[:count]])
        assert size == ctypes.sizeof(pynput_win32.INPUT)
        return count

    send = make_win32_unicode_sender(send_input=send_input)
    send("a")
    send("😀")
    keyboard, unicode, keyup = pynput_win32.INPUT.KEYBOARD, pynput_win32.KEYBDINPUT.UNICODE, pynput_win32.KEYBDINPUT.KEYUP
    assert calls[0] == [(keyboard, 0, 0x61, unicode), (keyboard, 0, 0x61, unicode | keyup)]
    assert calls[1] == [
        (keyboard, 0, 0xD83D, unicode), (keyboard, 0, 0xDE00, unicode),
        (keyboard, 0, 0xD83D, unicode | keyup), (keyboard, 0, 0xDE00, unicode | keyup),
    ]


def test_windows_sender_reports_rejected_input_without_the_character(pynput_win32):
    send = make_win32_unicode_sender(send_input=lambda count, events, size: 0)
    with pytest.raises(OSError, match="accepted 0 of 2") as error:
        send("q")
    assert "'q'" not in str(error.value)  # the text may be a secret


# -----------------------------------------------------------------------------
# Step card text
# -----------------------------------------------------------------------------

def test_card_summary_quotes_the_text():
    assert step_text.step_summary(_type_step("myid123")) == 'Type "myid123"'
    assert step_text.key_summary({"action": "type", "text": ""}) == "No text set"
    long_text = "x" * 50
    assert step_text.key_summary({"action": "type", "text": long_text}) == f'Type "{"x" * 31}…"'
    assert step_text.typed_text_display("a\r\nb\tc") == "a⏎b⇥c"
    disabled = _type_step("id")
    disabled.enabled = False
    assert step_text.card_summary(disabled) == 'Disabled · Type "id"'


# -----------------------------------------------------------------------------
# Inspector: Send [Key or shortcut | Text]
# -----------------------------------------------------------------------------

def _inspector(qtbot, action):
    from action_macro.gui.property_inspector import PropertyInspector

    inspector = PropertyInspector()
    qtbot.addWidget(inspector)
    inspector.resize(340, 900)
    inspector.show()
    inspector.set_action(action)
    return inspector


def test_switching_to_text_shows_the_text_field_and_types_after_it_is_filled(qtbot):
    act = ActionBlock(id="k", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["enter"], action="down"))
    inspector = _inspector(qtbot, act)
    form = inspector.form_key
    assert form.seg_kind.value() == "keys"
    assert form.section_keys.isVisible() and form.section_behaviour.isVisible()
    assert not form.section_text.isVisible()

    form.seg_kind.button("text").click()
    assert form.section_text.isVisible()
    assert not form.section_keys.isVisible() and not form.section_behaviour.isVisible()
    assert act.params.action == "down"  # nothing to type yet: not applied
    assert inspector.lbl_status.text() == "⚠ Enter the text to type"
    assert form.edit_text.property("state") == "invalid"
    assert form.lbl_text_warning.property("tone") == "warning"
    assert "password" in form.lbl_text_warning.text()

    form.edit_text.setText("myid123")
    assert (act.params.action, act.params.text, act.params.keys) == ("type", "myid123", ["enter"])
    assert inspector.lbl_status.text() == "✓ Parameters valid"

    # Back to keys: the shortcut is still there and the step presses it again.
    form.seg_kind.button("keys").click()
    assert act.params.keys == ["enter"] and act.params.action == "down"
    assert act.params.to_dict().get("text") is None


def test_line_breaks_in_the_text_are_pointed_out(qtbot):
    inspector = _inspector(qtbot, _type_step("myid123"))
    form = inspector.form_key
    assert not form.lbl_text_breaks.isVisible()
    form.edit_text.setText("myid123\r\n")
    assert form.lbl_text_breaks.isVisible() and "Enter" in form.lbl_text_breaks.text()
    assert inspector.current_action.params.text == "myid123\r\n"  # typed as one Enter at the end


def test_text_help_mentions_the_input_source_only_on_macos():
    from action_macro.gui.property_inspector import text_help

    assert "input source" in text_help("darwin")
    assert "input source" not in text_help("win32")


def test_loading_a_type_step_selects_text(qtbot):
    inspector = _inspector(qtbot, _type_step("myid123"))
    form = inspector.form_key
    assert form.seg_kind.value() == "text" and form.combo_kind.currentText() == "text"
    assert form.edit_text.text() == "myid123"
    assert form.combo_action.currentText() == "press"  # what "Key or shortcut" starts with
    assert form.section_text.isVisible() and not form.section_keys.isVisible()

    inspector.set_action(ActionBlock(id="p", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["f5"])))
    assert form.seg_kind.value() == "keys" and form.edit_text.text() == ""


def test_a_word_in_keys_is_rejected_with_a_pointer_to_text(qtbot):
    act = ActionBlock(id="k", type=ActionType.KEY_INPUT, params=KeyInputParams(keys=["enter"]))
    inspector = _inspector(qtbot, act)
    inspector.form_key.edit_keys.setText("myid123")
    assert act.params.keys == ["enter"]  # not applied: the engine cannot press "myid123"
    assert inspector.lbl_status.text() == '⚠ "myid123" is not a key name. To type text, choose Text under Send.'
    form = inspector.form_key
    assert form.edit_keys.property("state") == "invalid"
    assert not form.is_valid()
    # Explained under the field itself; the chip does not show the word as if it were a key.
    assert form.lbl_keys_help.text() == '"myid123" is not a key name. To type text, choose Text under Send.'
    assert form.lbl_keys_help.property("tone") == "danger"
    assert not form.lbl_human.isVisible()

    form.edit_keys.setText("ctrl+shift+page_up")
    assert act.params.keys == ["ctrl", "shift", "page_up"]
    assert form.lbl_keys_help.text().startswith("Join keys with +")
    assert form.lbl_keys_help.property("tone") == ""
    assert form.lbl_human.isVisible()
