"""
tests.unit.test_device_check_tools
==================================
The real-device check scripts in tools/ cannot run here (they move the real mouse), but
they must keep importing against the current code and refuse to run off macOS.
Only ``--help`` is invoked: it parses arguments and exits before any window or input.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
SCRIPTS = ["device_check_macos.py", "device_check_hotkeys_macos.py"]


@pytest.mark.unit
@pytest.mark.parametrize("script", SCRIPTS)
def test_device_check_script_help(script: str) -> None:
    proc = subprocess.run(
        [sys.executable, str(TOOLS / script), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    if sys.platform == "darwin":
        assert proc.returncode == 0, proc.stderr
        assert "usage:" in proc.stdout
        assert "--report" in proc.stdout
    else:
        assert proc.returncode != 0
        assert "macOS only" in proc.stderr
