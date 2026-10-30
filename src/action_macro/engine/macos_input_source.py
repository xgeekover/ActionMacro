"""
action_macro.engine.macos_input_source
======================================
Types text with an ASCII-capable input source selected on macOS.

pynput posts each character as the key that produces it on the keyboard layout. While an
input method such as Korean 2-Set is selected, the input method turns those keys into its
own script. Measured on a real Mac (tools/device_check_macos.py --ime), "myid123" came out
as "ㅡㅛㅑㅇ123" and "Ab1 x_Y!@" as "Aㅠ1 ". Characters the layout has no key for, such
as Hangul syllables, are posted as Unicode text and come out right either way.

:func:`ascii_input_source` selects the current ASCII-capable keyboard layout (e.g. ABC)
for the duration of a text step and selects the original input source again afterwards,
so text is typed as written whichever source the user had selected.

HIToolbox's Text Input Sources (TIS) API may only be called on the main thread once the
app shows a window (see :mod:`action_macro.engine.macos_keyboard`). A text step runs on
the engine's worker thread, so the calls are queued on the main dispatch queue, which
Qt's Cocoa event loop drains, and awaited with a timeout. On the main thread itself (the
headless CLI) they run directly. If the main thread does not answer in time, the text is
typed with the current input source and a warning is logged.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys
import threading
import time
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

logger = logging.getLogger(__name__)

#: How long the worker waits for the main thread to switch the input source.
MAIN_THREAD_TIMEOUT_S = 2.0
#: Pause after switching, before the first character, so the target app sees the new source.
SWITCH_SETTLE_S = 0.05
#: Pause after the last character, before switching back: posted key events are handled by
#: the target app asynchronously and would otherwise reach it under the original source.
RESTORE_DELAY_S = 0.15

_CARBON_PATH = "/System/Library/Frameworks/Carbon.framework/Carbon"
_CORE_FOUNDATION_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_LIBSYSTEM_PATH = "/usr/lib/libSystem.B.dylib"
_UTF8 = 0x08000100  # kCFStringEncodingUTF8

_tis: Optional[Any] = None

#: ``dispatch_function_t``: ``void (*)(void *context)``.
_DISPATCH_FUNCTION = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class _Tis:
    """ctypes bindings of the few TIS / CoreFoundation / libdispatch calls used here."""

    def __init__(self) -> None:
        carbon = ctypes.cdll.LoadLibrary(_CARBON_PATH)
        cf = ctypes.cdll.LoadLibrary(_CORE_FOUNDATION_PATH)
        libsystem = ctypes.CDLL(_LIBSYSTEM_PATH)
        for lib, name, restype, argtypes in (
            (carbon, "TISCopyCurrentKeyboardInputSource", ctypes.c_void_p, []),
            (carbon, "TISCopyCurrentASCIICapableKeyboardInputSource", ctypes.c_void_p, []),
            (carbon, "TISGetInputSourceProperty", ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
            (carbon, "TISSelectInputSource", ctypes.c_int32, [ctypes.c_void_p]),
            (cf, "CFRelease", None, [ctypes.c_void_p]),
            (cf, "CFBooleanGetValue", ctypes.c_bool, [ctypes.c_void_p]),
            (cf, "CFStringGetCString", ctypes.c_bool,
             [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
        ):
            function = getattr(lib, name)
            function.restype, function.argtypes = restype, argtypes
            setattr(self, name, function)
        self.prop_id = ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceID")
        self.prop_ascii = ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceIsASCIICapable")
        self.main_queue = ctypes.addressof(ctypes.c_void_p.in_dll(libsystem, "_dispatch_main_q"))
        self.dispatch_async_f = libsystem.dispatch_async_f
        self.dispatch_async_f.restype = None
        self.dispatch_async_f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, _DISPATCH_FUNCTION]

    def source_id(self, source: int) -> str:
        ref = self.TISGetInputSourceProperty(source, self.prop_id)
        buffer = ctypes.create_string_buffer(512)
        return buffer.value.decode() if ref and self.CFStringGetCString(ref, buffer, 512, _UTF8) else ""

    def is_ascii_capable(self, source: int) -> bool:
        ref = self.TISGetInputSourceProperty(source, self.prop_ascii)
        return bool(ref) and self.CFBooleanGetValue(ref)


def _bindings() -> Any:
    global _tis
    if _tis is None:
        _tis = _Tis()
    return _tis


def available() -> bool:
    """True on macOS when the TIS bindings load (they only load system frameworks)."""
    if sys.platform != "darwin":
        return False
    try:
        _bindings()
        return True
    except Exception as err:  # missing framework or symbol
        logger.debug("Text Input Sources unavailable: %s", err)
        return False


def current_source_id() -> str:
    """ID of the selected keyboard input source, e.g. ``com.apple.keylayout.ABC``. Main thread only."""
    tis = _bindings()
    source = tis.TISCopyCurrentKeyboardInputSource()
    try:
        return tis.source_id(source)
    finally:
        tis.CFRelease(source)


# -----------------------------------------------------------------------------
# Main-thread calls
# -----------------------------------------------------------------------------

#: Callbacks queued on the main dispatch queue (by id), kept alive until they have run.
_pending: Dict[int, Any] = {}
_pending_lock = threading.Lock()


def run_on_main_thread(fn: Callable[[], Any], timeout: float = MAIN_THREAD_TIMEOUT_S,
                       abandon_on_timeout: bool = True) -> Tuple[bool, Any]:
    """Runs ``fn`` on the main thread and returns ``(True, result)``.

    Off the main thread the call is queued on the main dispatch queue and awaited for
    ``timeout`` seconds. If it has not started by then, ``(False, None)`` is returned and
    the call is abandoned (it will not run later), unless ``abandon_on_timeout`` is False:
    then it still runs once the main thread gets to it. An exception raised by ``fn`` is
    re-raised.
    """
    if threading.current_thread() is threading.main_thread():
        return True, fn()

    tis = _bindings()
    lock = threading.Lock()
    done = threading.Event()
    box: dict = {"abandoned": False}

    def _call(_context: Any) -> None:
        try:
            with lock:
                if box["abandoned"]:
                    return
                try:
                    box["result"] = fn()
                except BaseException as err:  # handed to the waiting thread
                    box["error"] = err
                done.set()
        finally:
            with _pending_lock:
                _pending.pop(id(callback), None)

    callback = _DISPATCH_FUNCTION(_call)
    with _pending_lock:
        _pending[id(callback)] = callback
    tis.dispatch_async_f(tis.main_queue, None, callback)

    if not done.wait(timeout):
        with lock:
            if not done.is_set():
                box["abandoned"] = abandon_on_timeout
                return False, None
    if "error" in box:
        raise box["error"]
    return True, box.get("result")


# -----------------------------------------------------------------------------
# Switching for a text step
# -----------------------------------------------------------------------------


def _select_ascii_capable() -> Optional[int]:
    """Selects the ASCII-capable layout if the current source is not one (main thread).

    Returns the original source (a retained TISInputSourceRef to hand to :func:`_restore`),
    or None when nothing was switched.
    """
    tis = _bindings()
    original = tis.TISCopyCurrentKeyboardInputSource()
    if not original:
        return None
    if tis.is_ascii_capable(original):
        tis.CFRelease(original)
        return None
    ascii_source = tis.TISCopyCurrentASCIICapableKeyboardInputSource()
    try:
        if not ascii_source or tis.TISSelectInputSource(ascii_source) != 0:
            tis.CFRelease(original)
            return None
        logger.debug("Input source %s -> %s for typing", tis.source_id(original), tis.source_id(ascii_source))
    finally:
        if ascii_source:
            tis.CFRelease(ascii_source)
    return original


def _restore(original: int) -> None:
    """Selects ``original`` again and releases it (main thread)."""
    tis = _bindings()
    try:
        tis.TISSelectInputSource(original)
    finally:
        tis.CFRelease(original)


@contextlib.contextmanager
def ascii_input_source(timeout: float = MAIN_THREAD_TIMEOUT_S) -> Iterator[None]:
    """Keeps an ASCII-capable keyboard layout selected while the body types text.

    Does nothing when the current input source is already ASCII-capable (ABC, U.S., …),
    off macOS, or when the switch cannot be made in time (logged; the text is then typed
    with the current source). The original source is selected again on exit, also when
    the body raises or the run is stopped.
    """
    original: Optional[int] = None
    if available():
        try:
            answered, original = run_on_main_thread(_select_ascii_capable, timeout)
            if not answered:
                logger.warning("Could not switch to an ASCII input source in time; typing with the current one")
            elif original:
                time.sleep(SWITCH_SETTLE_S)
        except Exception as err:
            logger.warning("Could not switch to an ASCII input source: %s", err)
            original = None
    try:
        yield
    finally:
        if original:
            time.sleep(RESTORE_DELAY_S)
            source = original
            try:
                # Not abandoned: the user's source is selected again as soon as the main
                # thread is free (e.g. after it stopped waiting for this run).
                answered, _ = run_on_main_thread(lambda: _restore(source), timeout, abandon_on_timeout=False)
                if not answered:
                    logger.warning("Switching the input source back is still queued on the main thread")
            except Exception as err:
                logger.warning("Could not switch the input source back: %s", err)
