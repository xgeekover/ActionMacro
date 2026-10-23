"""
tests.unit.test_challenger_m1_2
===============================
Adversarial empirical challenge suite targeting:
1. Key mapping (action_macro.core.key_mapping):
   - Complex modifier chains
   - Weird casing strings & aliases (cTrL+AlT+dEL, PRIMARY_MODIFIER)
   - Non-ASCII keys (Korean, European accents, CJK, Emojis)
   - Symbol keys (+, -, /, [, ], \\, etc.)
   - Boundary inputs, invalid types, and headless/pynput translation
2. Cross-platform path resolution & persistence (action_macro.core.persistence):
   - Windows backslashes (..\\templates\\icon.png) vs Unix forward slashes (../templates/icon.png)
   - Path traversal injection attempts (../../../../etc/passwd, ..\\..\\..\\Windows\\System32\\cmd.exe)
   - Root paths, drive letters, and UNC paths
   - Unicode, spaces, and special characters in paths
   - Atomic scenario save/load roundtripping with path normalization
"""

import json
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath
import pytest

from action_macro.core.key_mapping import (
    CANONICAL_SPECIAL_KEYS,
    KEY_ALIASES,
    format_key_combo,
    is_valid_key,
    normalize_key_name,
    parse_key_combo,
    parse_key_combination,
    resolve_key_for_platform,
    to_pynput_key,
)
from action_macro.core.models import (
    ActionBlock,
    ActionType,
    ImageWaitParams,
    KeyInputParams,
    ScenarioModel,
)
from action_macro.core.persistence import (
    ScenarioPersistence,
    load_scenario,
    resolve_template_path,
    save_scenario,
    scenario_from_dict,
    scenario_from_json,
    scenario_to_dict,
    scenario_to_json,
    to_posix_relative_path,
)


# ===========================================================================
# 1. KEY MAPPING CHALLENGES
# ===========================================================================

class TestKeyMappingAdversarial:
    """Stress tests and boundary checks for key_mapping."""

    def test_weird_case_strings_and_aliases(self):
        """Verify casing variations and aliases normalize to canonical keys."""
        cases = [
            ("cTrL+AlT+dEL", ["ctrl", "alt", "delete"]),
            ("sHiFt + EsC", ["shift", "esc"]),
            ("CmD_oR_cTrL + c", ["cmd_or_ctrl", "c"]),
            ("ReTuRn", ["enter"]),
            ("cOnTrOl + oPtIoN + CoMmAnD", ["ctrl", "alt", "cmd"]),
            ("   cTrL  +  AlT + dEL   ", ["ctrl", "alt", "delete"]),
            ("PRIMARY_MODIFIER + v", ["cmd_or_ctrl", "v"]),
            ("wInDoWs + r", ["cmd", "r"]),
            ("SuPeR + L", ["cmd", "l"]),
            ("BkSp", ["backspace"]),
            ("PrTsC", ["print_screen"]),
            ("PrtScr", ["print_screen"]),
            ("PgUp", ["page_up"]),
            ("PgDn", ["page_down"]),
            ("Ins", ["insert"]),
            ("Spc", ["space"]),
            ("ESCAPE", ["esc"]),
            ("RETURN", ["enter"]),
            ("OPTION", ["alt"]),
            ("COMMAND", ["cmd"]),
        ]
        for raw, expected in cases:
            parsed = parse_key_combo(raw)
            assert parsed == expected, f"Failed parsing {raw!r}: got {parsed}, expected {expected}"
            # Ensure alias parse_key_combination is identical
            assert parse_key_combination(raw) == expected

    def test_complex_modifier_chains(self):
        """Verify long and complex modifier chains parse and format without failure."""
        chains = [
            ("ctrl+alt+shift+cmd+f12", ["ctrl", "alt", "shift", "cmd", "f12"]),
            ("cmd_or_ctrl+alt+shift+space", ["cmd_or_ctrl", "alt", "shift", "space"]),
            ("ctrl+ctrl+alt+shift+a", ["ctrl", "ctrl", "alt", "shift", "a"]),
            ("ctrl+alt+shift+cmd", ["ctrl", "alt", "shift", "cmd"]),
            ("+".join(["ctrl"] * 8 + ["z"]), ["ctrl"] * 8 + ["z"]),
            ("+".join([f"f{i}" for i in range(1, 13)]), [f"f{i}" for i in range(1, 13)]),
        ]
        for raw, expected in chains:
            parsed = parse_key_combo(raw)
            assert parsed == expected

            # Test formatting on both platforms
            fmt_mac = format_key_combo(parsed, "darwin")
            fmt_win = format_key_combo(parsed, "win32")
            assert isinstance(fmt_mac, str) and len(fmt_mac) > 0
            assert isinstance(fmt_win, str) and len(fmt_win) > 0

    def test_symbol_keys(self):
        """Verify handling of symbol keys (+, -, /, [, ], etc.) in parsing and validity."""
        symbols = ["+", "-", "/", "[", "]", "\\", "=", ";", "'", ",", ".", "`", "~", "!", "@", "#", "$", "%", "^", "&", "*", "(", ")"]

        # All single-character symbols must be valid keys
        for sym in symbols:
            assert is_valid_key(sym) is True, f"Symbol {sym!r} should be valid"
            resolved = resolve_key_for_platform(sym, "darwin")
            assert resolved == sym
            resolved_win = resolve_key_for_platform(sym, "win32")
            assert resolved_win == sym

            # Formatting single symbols
            f_mac = format_key_combo([sym], "darwin")
            f_win = format_key_combo([sym], "win32")
            assert f_mac != ""
            assert f_win != ""

        # Test combinations with modifiers
        assert parse_key_combo("ctrl++") == ["ctrl", "+"]
        assert parse_key_combo("ctrl+-") == ["ctrl", "-"]
        assert parse_key_combo("ctrl+/") == ["ctrl", "/"]
        assert parse_key_combo("ctrl+[") == ["ctrl", "["]
        assert parse_key_combo("ctrl+]") == ["ctrl", "]"]
        assert parse_key_combo("ctrl+\\") == ["ctrl", "\\"]
        assert parse_key_combo("alt + +") == ["alt", "+"]
        assert parse_key_combo("shift + =") == ["shift", "="]

        # Multi-plus behavior
        # Note: 'ctrl+++' -> tokens ['ctrl', '', '', ''] -> result ['ctrl', '+']
        assert parse_key_combo("ctrl+++") == ["ctrl", "+"]

        # KeyInputParams model accepts symbol keys
        p = KeyInputParams(keys=["+", "-", "/", "[", "]"])
        assert p.keys == ["+", "-", "/", "[", "]"]

    def test_non_ascii_keys(self):
        """Verify non-ASCII, Korean, accented, and Unicode characters."""
        non_ascii_keys = [
            "ㄱ", "ㅏ", "가", "한", "글",
            "é", "ü", "ñ", "ç", "ß", "ø", "å",
            "α", "Ω", "д",
            "中", "文",
            "🔥", "⭐",
        ]
        for k in non_ascii_keys:
            # Must normalize cleanly
            norm = normalize_key_name(k)
            assert norm == k.strip().lower()

            # Must be recognized as valid single key
            assert is_valid_key(k) is True, f"Key {k!r} should be valid"

            # Translation bridge must return char safely
            pynput_key = to_pynput_key(k)
            assert pynput_key == k.strip().lower()

            # Key combo parsing
            parsed = parse_key_combo(f"ctrl + {k}")
            assert parsed == ["ctrl", k.strip().lower()]

            # Formatting
            fmt_mac = format_key_combo(parsed, "darwin")
            fmt_win = format_key_combo(parsed, "win32")
            assert k.strip().casefold() in fmt_mac.casefold()
            assert k.strip().casefold() in fmt_win.casefold()

            # Model accepts non-ASCII keys
            param = KeyInputParams(keys=[k])
            assert param.keys == [k]

    def test_key_mapping_edge_inputs_and_crash_safety(self):
        """Verify robust handling of empty, None, non-string, or malformed inputs without crashing."""
        # parse_key_combo returns [] on empty or non-string inputs
        assert parse_key_combo("") == []
        assert parse_key_combo("   ") == []
        assert parse_key_combo(None) == []  # type: ignore[arg-type]
        assert parse_key_combo(123) == []  # type: ignore[arg-type]
        assert parse_key_combo(True) == []  # type: ignore[arg-type]
        assert parse_key_combo([]) == []  # type: ignore[arg-type]

        # normalize_key_name raises TypeError on non-string
        with pytest.raises(TypeError):
            normalize_key_name(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            normalize_key_name(None)  # type: ignore[arg-type]

        # is_valid_key raises TypeError on non-string
        with pytest.raises(TypeError):
            is_valid_key(123)  # type: ignore[arg-type]

        # to_pynput_key raises TypeError on non-string
        with pytest.raises(TypeError):
            to_pynput_key(123)  # type: ignore[arg-type]

        # format_key_combo on empty list
        assert format_key_combo([], "darwin") == ""
        assert format_key_combo([], "win32") == ""

    def test_platform_resolution_darwin_vs_win32_vs_linux(self):
        """Verify adaptive cmd_or_ctrl resolves accurately across platforms."""
        assert resolve_key_for_platform("cmd_or_ctrl", platform_name="darwin") == "cmd"
        assert resolve_key_for_platform("cmd_or_ctrl", platform_name="DARWIN") == "cmd"
        assert resolve_key_for_platform("cmd_or_ctrl", platform_name="win32") == "ctrl"
        assert resolve_key_for_platform("cmd_or_ctrl", platform_name="WIN32") == "ctrl"
        assert resolve_key_for_platform("cmd_or_ctrl", platform_name="linux") == "ctrl"
        assert resolve_key_for_platform("cmd_or_ctrl", platform_name="LINUX") == "ctrl"
        # Unknown fallback
        assert resolve_key_for_platform("cmd_or_ctrl", platform_name="freebsd") == "ctrl"

        # Formatter output differences
        keys = ["cmd_or_ctrl", "shift", "enter"]
        mac_fmt = format_key_combo(keys, "darwin")
        win_fmt = format_key_combo(keys, "win32")
        assert "⌘" in mac_fmt
        assert "⇧" in mac_fmt
        assert "⏎" in mac_fmt
        assert "Ctrl" in win_fmt
        assert "Shift" in win_fmt
        assert "Enter" in win_fmt


# ===========================================================================
# 2. PATH RESOLUTION & PERSISTENCE CHALLENGES
# ===========================================================================

class TestPathResolutionAdversarial:
    """Stress tests for cross-platform template path resolution and traversal resilience."""

    def test_windows_backslashes_vs_unix_slashes_resolution(self):
        """Verify Windows backslashes and Unix slashes resolve to identical target paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "project" / "scenarios"
            base_dir.mkdir(parents=True)
            scenario_file = base_dir / "my_scenario.json"

            # Create mock template file in project/templates/icon.png
            templates_dir = Path(tmpdir) / "project" / "templates"
            templates_dir.mkdir(parents=True)
            icon_file = templates_dir / "icon.png"
            icon_file.write_bytes(b"\x89PNG\r\n\x1a\nfake_image_data")

            # Resolve using Windows backslash syntax
            win_rel = "..\\templates\\icon.png"
            resolved_win = resolve_template_path(win_rel, scenario_file)

            # Resolve using Unix forward slash syntax
            unix_rel = "../templates/icon.png"
            resolved_unix = resolve_template_path(unix_rel, scenario_file)

            assert resolved_win == resolved_unix
            assert resolved_win == icon_file.resolve()
            assert resolved_win.is_file()

            # Resolve using directory as scenario_path instead of file
            resolved_from_dir = resolve_template_path(win_rel, base_dir)
            assert resolved_from_dir == icon_file.resolve()

    def test_mixed_and_redundant_slashes(self):
        """Verify path resolution cleans up mixed, double, and redundant slashes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scen_path = Path(tmpdir) / "scenario.json"

            p1 = resolve_template_path("templates\\\\sub\\\\icon.png", scen_path)
            p2 = resolve_template_path("templates//sub//icon.png", scen_path)
            p3 = resolve_template_path("templates\\sub/icon.png", scen_path)
            p4 = resolve_template_path("./templates/sub/icon.png", scen_path)
            p5 = resolve_template_path(".\\templates\\sub\\icon.png", scen_path)

            assert p1 == p2 == p3 == p4 == p5
            assert p1 == (Path(tmpdir) / "templates" / "sub" / "icon.png").resolve()

    def test_path_traversal_attempts_safety(self):
        """Verify deep path traversal attempts do not cause unhandled crashes or exceptions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scen_path = Path(tmpdir) / "scenario.json"

            traversal_cases = [
                "../../../../../../etc/passwd",
                "..\\..\\..\\..\\..\\..\\Windows\\System32\\cmd.exe",
                "../../../../../../etc/shadow",
                "templates/../../../../secret.key",
                "templates/./../../../../secret.key",
                "..\\..\\..\\..\\..\\..\\secret.key",
                "....//....//....//etc//passwd",
            ]

            for tc in traversal_cases:
                # Must resolve to a valid Path instance without crashing
                resolved = resolve_template_path(tc, scen_path)
                assert isinstance(resolved, Path)

                # Conversion to posix relative path must also not crash
                posix_rel = to_posix_relative_path(tc, Path(tmpdir))
                assert isinstance(posix_rel, str)
                assert "\\" not in posix_rel, f"Backslash found in converted posix path: {posix_rel}"

    def test_drive_letters_and_root_paths(self):
        """Verify Windows drive letter and root path strings do not crash path resolution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scen_path = Path(tmpdir) / "scenario.json"

            root_cases = [
                "/var/tmp/icon.png",
                "C:\\Users\\Admin\\Desktop\\template.png",
                "C:/Users/Admin/Desktop/template.png",
                "D:\\data\\icon.png",
                "\\\\server\\share\\template.png",
            ]

            for rc in root_cases:
                resolved = resolve_template_path(rc, scen_path)
                assert isinstance(resolved, Path)

                posix_rel = to_posix_relative_path(rc, Path(tmpdir))
                assert isinstance(posix_rel, str)
                assert "\\" not in posix_rel

    def test_unicode_and_special_character_paths(self):
        """Verify Korean, emoji, spaces, and brackets in paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            scen_path = Path(tmpdir) / "scenario.json"

            unicode_cases = [
                "템플릿/버튼_이미지.png",
                "..\\템플릿\\버튼_확인.png",
                "templates/my button name with spaces.png",
                "templates/btn (1).png",
                "templates/[ui]/btn.png",
                "templates/📁/quest_🎯.png",
            ]

            for uc in unicode_cases:
                resolved = resolve_template_path(uc, scen_path)
                assert isinstance(resolved, Path)

                posix_rel = to_posix_relative_path(uc, Path(tmpdir))
                assert isinstance(posix_rel, str)
                assert "\\" not in posix_rel

    def test_save_and_load_roundtrip_with_windows_and_traversal_paths(self):
        """
        Verify that a scenario saved with Windows backslashes:
        1. Serializes to normalized POSIX paths in the JSON file.
        2. Loads cleanly on any platform.
        3. Resolves to the correct absolute template path.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            scen_dir = Path(tmpdir) / "scenarios"
            scen_dir.mkdir(parents=True)
            scen_file = scen_dir / "cross_platform_macro.json"

            # Create mock template
            tpl_dir = Path(tmpdir) / "templates"
            tpl_dir.mkdir(parents=True)
            btn_file = tpl_dir / "start_btn.png"
            btn_file.write_bytes(b"PNG_DATA")

            # Action with Windows backslashes
            act1 = ActionBlock(
                id="act-win-path",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(
                    template_path="..\\templates\\start_btn.png",
                    mode="appear",
                    confidence=0.85,
                ),
                description="Windows backslash path action",
            )

            # Action with Unix traversal
            act2 = ActionBlock(
                id="act-unix-path",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(
                    template_path="../templates/start_btn.png",
                    mode="appear",
                    confidence=0.85,
                ),
                description="Unix slash path action",
            )

            # Action with nested Korean directory path using Windows backslashes
            act3 = ActionBlock(
                id="act-korean-win-path",
                type=ActionType.IMAGE_WAIT,
                params=ImageWaitParams(
                    template_path="..\\템플릿\\시작버튼.png",
                    mode="appear",
                ),
                description="Korean Windows path action",
            )

            scenario = ScenarioModel(
                name="Cross-Platform Path Test",
                version="1.0.0",
                loop_count=2,
                actions=[act1, act2, act3],
            )

            # Save to JSON
            save_scenario(scenario, scen_file)
            assert scen_file.is_file()

            # Inspect raw saved JSON
            with open(scen_file, "r", encoding="utf-8") as f:
                saved_json = json.load(f)

            # In the saved JSON, backslashes must be normalized to forward slashes
            for action_dict in saved_json["actions"]:
                tpl_path = action_dict["params"]["template_path"]
                assert "\\" not in tpl_path, f"Backslash found in serialized JSON: {tpl_path}"
                assert "/" in tpl_path

            # Load the scenario back
            loaded = load_scenario(scen_file)
            assert len(loaded.actions) == 3

            # Verify resolved paths match expected target file
            resolved_act1 = resolve_template_path(loaded.actions[0].params.template_path, scen_file)
            resolved_act2 = resolve_template_path(loaded.actions[1].params.template_path, scen_file)
            assert resolved_act1 == resolved_act2 == btn_file.resolve()
            assert resolved_act1.is_file()

            # Verify ScenarioPersistence namespace compatibility
            ns_resolved = ScenarioPersistence.resolve_template_path(
                loaded.actions[0].params.template_path, scen_file
            )
            assert ns_resolved == resolved_act1
