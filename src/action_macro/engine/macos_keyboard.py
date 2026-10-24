"""
action_macro.engine.macos_keyboard
==================================
Keeps pynput's keyboard-layout lookups on the main thread on macOS.

pynput reads the current keyboard layout through ``TSMGetInputSourceProperty`` in
``pynput._util.darwin.keycode_context()``. HIToolbox only allows that call on the main
thread once the process shows a window; anywhere else libdispatch traps and macOS kills
the app ("Python quit unexpectedly", SIGTRAP). pynput makes the call in two places that
are off the main thread in ActionMacro:

- the keyboard ``Listener`` thread itself (``Listener._run``), i.e. the global emergency
  stop hotkey, no matter which thread starts it;
- ``keyboard.Controller()``, whenever it is built outside the main thread.

The layout context is plain data (keyboard type + layout bytes). This module reads it
once on the main thread and makes pynput reuse it, so no thread ever calls TSM again.
Calling :func:`refresh_keyboard_layout_cache` again on the main thread picks up a
layout the user switched to since.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

_cached_context: Optional[Any] = None
_original_keycode_context: Optional[Any] = None


def is_installed() -> bool:
    """Returns True once pynput has been switched to the cached layout context."""
    return _original_keycode_context is not None


def refresh_keyboard_layout_cache(platform: Optional[str] = None) -> bool:
    """Reads the keyboard layout on the main thread and makes pynput reuse it.

    Must be called on the main thread before any pynput keyboard controller or
    listener is created (the app and the CLI do this at start-up and again before
    each run). Off the main thread, or on other platforms, this does nothing.

    Returns:
        True if pynput now uses the cached context.
    """
    global _cached_context, _original_keycode_context
    if (platform or sys.platform) != "darwin":
        return False
    if threading.current_thread() is not threading.main_thread():
        logger.debug("Keyboard layout cache can only be refreshed on the main thread")
        return is_installed()
    try:
        from pynput._util import darwin as util_darwin
        from pynput.keyboard import _darwin as keyboard_darwin
    except Exception as err:  # pynput missing or not the darwin backend
        logger.debug("pynput darwin backend unavailable: %s", err)
        return False

    original = _original_keycode_context or util_darwin.keycode_context
    try:
        with original() as context:
            _cached_context = context
    except Exception as err:
        logger.warning("Could not read the keyboard layout: %s", err)
        return is_installed()

    if _original_keycode_context is None:
        _original_keycode_context = original

        @contextlib.contextmanager
        def cached_keycode_context() -> Iterator[Any]:
            yield _cached_context

        # get_unicode_to_keycode_map() looks the name up in util_darwin; Listener._run()
        # uses the name imported into keyboard_darwin.
        util_darwin.keycode_context = cached_keycode_context
        keyboard_darwin.keycode_context = cached_keycode_context
    return True
