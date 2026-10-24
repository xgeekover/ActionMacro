"""
action_macro.core.key_mapping
=================================
Platform-neutral key naming, alias normalization, and OS-specific key translation.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Canonical Abstract Key Definitions & Aliases
# ---------------------------------------------------------------------------

CANONICAL_SPECIAL_KEYS: Set[str] = {
    # Modifiers
    "cmd_or_ctrl",
    "ctrl",
    "alt",
    "shift",
    "cmd",
    # Navigation & Editing
    "enter",
    "esc",
    "space",
    "tab",
    "backspace",
    "delete",
    "insert",
    "home",
    "end",
    "page_up",
    "page_down",
    "up",
    "down",
    "left",
    "right",
    # Function keys
    "f1", "f2", "f3", "f4", "f5", "f6",
    "f7", "f8", "f9", "f10", "f11", "f12",
    # Locks
    "caps_lock",
    "num_lock",
    "scroll_lock",
    # Miscellaneous
    "print_screen",
    "pause",
    "menu",
}

KEY_ALIASES: Dict[str, str] = {
    # Enter / Return
    "return": "enter",
    # Escape
    "escape": "esc",
    # Control
    "control": "ctrl",
    # Option / Alt
    "option": "alt",
    "opt": "alt",
    # Command / Windows / Super
    "command": "cmd",
    "win": "cmd",
    "windows": "cmd",
    "super": "cmd",
    # Adaptive modifier
    "ctrl_or_cmd": "cmd_or_ctrl",
    "cmd_ctrl": "cmd_or_ctrl",
    "primary_modifier": "cmd_or_ctrl",
    # Editing
    "del": "delete",
    "back": "backspace",
    "bksp": "backspace",
    "spc": "space",
    "pgup": "page_up",
    "pgdn": "page_down",
    "pageup": "page_up",
    "pagedown": "page_down",
    "ins": "insert",
    "prtscr": "print_screen",
    "prtsc": "print_screen",
}


# ---------------------------------------------------------------------------
# Key Normalization & Platform Resolution
# ---------------------------------------------------------------------------

def normalize_key_name(key: str) -> str:
    """
    Normalize key identifier: strips whitespace, lowercases, and maps known aliases.
    """
    if not isinstance(key, str):
        raise TypeError(f"Key name must be a string, got {type(key).__name__}")
    cleaned = key.strip().lower()
    return KEY_ALIASES.get(cleaned, cleaned)


def is_valid_key(key: str) -> bool:
    """
    Check if a key string represents a recognized canonical key or single printable character.
    """
    norm = normalize_key_name(key)
    if norm in CANONICAL_SPECIAL_KEYS:
        return True
    if len(norm) == 1:
        return True
    return False


def resolve_key_for_platform(key: str, platform_name: Optional[str] = None) -> str:
    """
    Resolve an abstract key name into the concrete OS key identifier.

    Args:
        key: Abstract key name (e.g. 'cmd_or_ctrl', 'enter', 'alt').
        platform_name: Target OS platform ('darwin', 'win32', 'linux'). Defaults to sys.platform.
    """
    target = (platform_name or sys.platform).lower()
    norm = normalize_key_name(key)

    if norm == "cmd_or_ctrl":
        if target == "darwin":
            return "cmd"
        return "ctrl"

    return norm


def parse_key_combo(combo_str: str) -> List[str]:
    """
    Parse a key combination string like 'Ctrl+Shift+Esc' or 'cmd_or_ctrl + c'
    into a list of normalized key names.
    """
    if not isinstance(combo_str, str) or not combo_str.strip():
        return []

    tokens = [k.strip() for k in combo_str.split("+")]
    result: List[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "" and i > 0 and tokens[i - 1] != "":
            # Trailing empty token from '+' split indicates '+' was the key itself (e.g., 'ctrl++')
            result.append("+")
        elif t != "":
            result.append(normalize_key_name(t))
        i += 1

    return result


# Alias per dispatch instruction
parse_key_combination = parse_key_combo


def build_key_chord(modifiers: Any, keys: Any) -> List[str]:
    """Combines a KEY_INPUT action's ``modifiers`` and ``keys`` into one ordered chord.

    Explicit modifiers come first, then keys, each in the order given. A key that
    appears more than once (e.g. in both fields, or as an alias such as
    ``control``/``ctrl``) is kept only at its first position. A single string is
    accepted for either field. The engine executes this chord, and the GUI shows and
    edits it, so a saved ``modifiers`` field survives editing in the inspector.
    """
    if isinstance(modifiers, str):
        modifiers = [modifiers]
    if isinstance(keys, str):
        keys = [keys]
    chord: List[str] = []
    seen: Set[str] = set()
    for k in [*(modifiers or []), *(keys or [])]:
        norm = normalize_key_name(k)
        if norm in seen:
            continue
        seen.add(norm)
        chord.append(k)
    return chord


def format_key_combo(keys: List[str], platform_name: Optional[str] = None) -> str:
    """
    Format a list of keys for user display based on platform conventions.
    """
    target = (platform_name or sys.platform).lower()
    resolved = [resolve_key_for_platform(k, target) for k in keys]

    if target == "darwin":
        display_map = {
            "cmd": "⌘",
            "ctrl": "⌃",
            "alt": "⌥",
            "shift": "⇧",
            "enter": "⏎",
            "esc": "Esc",
            "backspace": "⌫",
            "delete": "⌦",
            "tab": "⇥",
            "space": "Space",
        }
        return " + ".join([display_map.get(k, k.upper() if len(k) <= 2 else k.capitalize()) for k in resolved])
    else:
        display_map = {
            "cmd": "Win",
            "ctrl": "Ctrl",
            "alt": "Alt",
            "shift": "Shift",
            "enter": "Enter",
            "esc": "Esc",
            "space": "Space",
            "tab": "Tab",
        }
        return " + ".join([display_map.get(k, k.upper() if len(k) <= 2 else k.capitalize()) for k in resolved])


# ---------------------------------------------------------------------------
# Pynput Translation Bridge
# ---------------------------------------------------------------------------

def to_pynput_key(key_name: str, platform_name: Optional[str] = None) -> Any:
    """
    Translate an abstract key name into a pynput.keyboard.Key enum or character string.
    If pynput is not installed or display server is unavailable, returns resolved string identifier.
    """
    resolved = resolve_key_for_platform(key_name, platform_name)

    try:
        from pynput.keyboard import Key
    except (ImportError, Exception):
        # Decoupled headless fallback
        return resolved

    special_key_map: Dict[str, Any] = {
        "ctrl": getattr(Key, "ctrl", "ctrl"),
        "alt": getattr(Key, "alt", "alt"),
        "shift": getattr(Key, "shift", "shift"),
        "cmd": getattr(Key, "cmd", "cmd"),
        "enter": getattr(Key, "enter", "enter"),
        "esc": getattr(Key, "esc", "esc"),
        "space": getattr(Key, "space", "space"),
        "tab": getattr(Key, "tab", "tab"),
        "backspace": getattr(Key, "backspace", "backspace"),
        "delete": getattr(Key, "delete", "delete"),
        "insert": getattr(Key, "insert", "insert"),
        "home": getattr(Key, "home", "home"),
        "end": getattr(Key, "end", "end"),
        "page_up": getattr(Key, "page_up", "page_up"),
        "page_down": getattr(Key, "page_down", "page_down"),
        "up": getattr(Key, "up", "up"),
        "down": getattr(Key, "down", "down"),
        "left": getattr(Key, "left", "left"),
        "right": getattr(Key, "right", "right"),
        "caps_lock": getattr(Key, "caps_lock", "caps_lock"),
        "num_lock": getattr(Key, "num_lock", "num_lock"),
        "scroll_lock": getattr(Key, "scroll_lock", "scroll_lock"),
        "print_screen": getattr(Key, "print_screen", "print_screen"),
        "pause": getattr(Key, "pause", "pause"),
        "menu": getattr(Key, "menu", "menu"),
        "f1": getattr(Key, "f1", "f1"),
        "f2": getattr(Key, "f2", "f2"),
        "f3": getattr(Key, "f3", "f3"),
        "f4": getattr(Key, "f4", "f4"),
        "f5": getattr(Key, "f5", "f5"),
        "f6": getattr(Key, "f6", "f6"),
        "f7": getattr(Key, "f7", "f7"),
        "f8": getattr(Key, "f8", "f8"),
        "f9": getattr(Key, "f9", "f9"),
        "f10": getattr(Key, "f10", "f10"),
        "f11": getattr(Key, "f11", "f11"),
        "f12": getattr(Key, "f12", "f12"),
    }

    if resolved in special_key_map:
        return special_key_map[resolved]

    if len(resolved) == 1:
        return resolved

    return resolved
