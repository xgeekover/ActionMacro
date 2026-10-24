"""Tests for the packaged-build self-test (``ActionMacro --self-test REPORT``)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from action_macro import __main__ as cli
from action_macro import selftest

ROOT = Path(__file__).resolve().parents[2]


def _stub_checks(monkeypatch, failing: str = "") -> None:
    def make(name):
        def check(*_args):
            if name == failing:
                raise RuntimeError(f"{name} is broken")
            return {"detail": name}
        return check

    for name in ("_check_opencv", "_check_input_backends", "_check_screen_capture_module",
                 "_check_keyboard_layout", "_check_gui"):
        monkeypatch.setattr(selftest, name, make(name))


def test_report_and_exit_code_when_every_check_passes(monkeypatch, tmp_path):
    _stub_checks(monkeypatch)
    report_path = tmp_path / "report.json"
    assert cli.main(["--self-test", str(report_path)]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert list(report["checks"]) == ["opencv", "input_backends", "screen_capture_module", "keyboard_layout", "gui"]
    assert report["checks"]["gui"] == {"ok": True, "detail": "_check_gui"}
    assert report["frozen"] is False and report["app_version"]


def test_a_failing_check_fails_the_run_and_the_rest_still_run(monkeypatch, tmp_path):
    _stub_checks(monkeypatch, failing="_check_input_backends")
    report_path = tmp_path / "report.json"
    assert selftest.run_self_test(str(report_path)) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["checks"]["input_backends"]["ok"] is False
    assert report["checks"]["input_backends"]["error"] == "RuntimeError: _check_input_backends is broken"
    assert report["checks"]["gui"]["ok"] is True  # later checks still ran


def test_self_test_flag_without_a_path_prints_the_report(monkeypatch, capsys):
    _stub_checks(monkeypatch)
    assert cli.main(["--self-test"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_real_self_test_passes_from_source(tmp_path):
    """The real checks in a fresh process (offscreen Qt; no input, no screen capture)."""
    report_path = tmp_path / "report.json"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
               PYTHONPATH=os.pathsep.join([str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]))
    result = subprocess.run([sys.executable, "-m", "action_macro", "--self-test", str(report_path)],
                            env=env, capture_output=True, text=True, timeout=120, cwd=tmp_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    failed = {name: check.get("error") for name, check in report["checks"].items() if not check["ok"]}
    assert result.returncode == 0 and report["ok"], failed
    assert report["checks"]["gui"]["qpa_platform"] == "offscreen"
