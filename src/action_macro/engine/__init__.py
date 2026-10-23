"""
action_macro.engine
=======================
Subsystem providing mouse and keyboard automation, coordinate geometry transformations,
screen display capture, and high-precision waiting/image-matching.
"""

from action_macro.engine.coordinate_transformer import CoordinateTransformer
from action_macro.engine.image_matcher import (
    ImageMatcher,
    check_match,
    find_template,
    match_template,
)
from action_macro.engine.input_adapter import InputAdapterProtocol, PynputAdapter
from action_macro.engine.screen_adapter import MSSScreenAdapter, ScreenAdapterProtocol
from action_macro.engine.wait_system import WaitSystem, wait, wait_image

__all__ = [
    "CoordinateTransformer",
    "ImageMatcher",
    "InputAdapterProtocol",
    "MSSScreenAdapter",
    "PynputAdapter",
    "ScreenAdapterProtocol",
    "WaitSystem",
    "check_match",
    "find_template",
    "match_template",
    "wait",
    "wait_image",
]
