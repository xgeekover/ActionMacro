"""
tests.unit.test_challenge_vision
================================
Pytest integration for Milestone 2 adversarial vision and coordinate challenge suite.
Imports test classes directly from challenge_vision.py.
"""

from challenge_vision import (
    TestAdversarialCoordinateTransformer,
    TestAdversarialTemplateMatching,
    TestAdversarialWaitSystemRapidToggle,
)

__all__ = [
    "TestAdversarialCoordinateTransformer",
    "TestAdversarialTemplateMatching",
    "TestAdversarialWaitSystemRapidToggle",
]
