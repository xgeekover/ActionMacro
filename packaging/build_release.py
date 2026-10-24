"""Builds, self-tests and packages ActionMacro for the current platform.

Outputs go to ``dist/release/``:

=========  =================================================  ==========================
Platform   Installer                                          Portable
=========  =================================================  ==========================
macOS      ``ActionMacro-<v>-macos-<arch>.dmg``               ``…-macos-<arch>-portable.zip``
           (drag ActionMacro to Applications)                 (ActionMacro.app)
Windows    ``ActionMacro-<v>-windows-x64-setup.exe``          ``…-windows-x64-portable.zip``
           (Inno Setup, per-user by default)                  (folder with ActionMacro.exe)
=========  =================================================  ==========================

Steps: PyInstaller (``packaging/ActionMacro.spec``) → run the frozen app with
``--self-test`` on the offscreen Qt platform (no input, no screen capture) → macOS:
ad-hoc code signature, zip with ``ditto``, DMG with ``hdiutil``; Windows: zip and the
Inno Setup installer (``ISCC.exe``).

Usage (from the repository root, in an environment with the app and PyInstaller)::

    python packaging/build_release.py [--skip-pyinstaller] [--expect-version X.Y.Z]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
PYI_DIST = DIST / "pyinstaller"
PYI_WORK = ROOT / "build" / "pyinstaller"
RELEASE = DIST / "release"
APP_NAME = "ActionMacro"


def app_version() -> str:
    text = (ROOT / "src" / "action_macro" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def arch_label() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x64"
    return machine


def run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    print("$ " + " ".join(str(part) for part in cmd), flush=True)
    return subprocess.run([str(part) for part in cmd], check=True, **kwargs)


def build_pyinstaller() -> None:
    shutil.rmtree(PYI_DIST, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", PACKAGING / "ActionMacro.spec", "--noconfirm", "--clean",
         "--distpath", PYI_DIST, "--workpath", PYI_WORK])


def frozen_executable() -> Path:
    if sys.platform == "darwin":
        return PYI_DIST / f"{APP_NAME}.app" / "Contents" / "MacOS" / APP_NAME
    if sys.platform == "win32":
        return PYI_DIST / APP_NAME / f"{APP_NAME}.exe"
    return PYI_DIST / APP_NAME / APP_NAME


def self_test() -> dict:
    """Runs the frozen app's start-up self-test and returns its report."""
    report_path = DIST / "selftest.json"
    report_path.unlink(missing_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    result = subprocess.run([str(frozen_executable()), "--self-test", str(report_path)], env=env,
                            capture_output=True, text=True, timeout=180)
    if not report_path.exists():
        print(result.stdout, result.stderr, sep="\n")
        raise SystemExit(f"self-test wrote no report (exit code {result.returncode})")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for name, check in report["checks"].items():
        print(f"  self-test {name}: {'ok' if check['ok'] else 'FAILED - ' + check.get('error', '')}")
    if result.returncode != 0 or not report.get("ok"):
        print(json.dumps(report, indent=2))
        raise SystemExit("self-test failed")
    return report


def zip_folder(folder: Path, target: Path) -> None:
    """Zips ``folder`` (kept as the top-level entry) without following symlinks."""
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(folder.rglob("*")):
            archive.write(path, path.relative_to(folder.parent))


def package_macos(version: str, arch: str) -> List[Path]:
    app = PYI_DIST / f"{APP_NAME}.app"
    # One ad-hoc signature over the whole bundle, so macOS sees an intact (unsigned by a
    # developer, but not "damaged") app after download. No Developer ID: Gatekeeper asks
    # the user to allow it once (see the release notes).
    run(["codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", app])
    run(["codesign", "--verify", "--deep", "--strict", app])

    base = f"{APP_NAME}-{version}-macos-{arch}"
    portable = RELEASE / f"{base}-portable.zip"
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, portable])

    staging = DIST / "dmg"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    run(["ditto", app, staging / f"{APP_NAME}.app"])
    (staging / "Applications").symlink_to("/Applications")
    dmg = RELEASE / f"{base}.dmg"
    run(["hdiutil", "create", "-volname", f"{APP_NAME} {version}", "-srcfolder", staging, "-fs", "HFS+",
         "-format", "UDZO", "-imagekey", "zlib-level=9", "-ov", dmg])
    shutil.rmtree(staging, ignore_errors=True)
    return [dmg, portable]


def find_iscc() -> Optional[str]:
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.expandvars(r"%LOCALAPPDATA%\Programs")):
        if base:
            candidate = Path(base) / "Inno Setup 6" / "ISCC.exe"
            if candidate.exists():
                return str(candidate)
    return None


def package_windows(version: str, arch: str) -> List[Path]:
    base = f"{APP_NAME}-{version}-windows-{arch}"
    portable = RELEASE / f"{base}-portable.zip"
    zip_folder(PYI_DIST / APP_NAME, portable)

    iscc = find_iscc()
    if not iscc:
        raise SystemExit("Inno Setup (ISCC.exe) not found; install it (choco install innosetup)")
    run([iscc, f"/DAppVersion={version}", f"/DSourceDir={PYI_DIST / APP_NAME}", f"/DOutputDir={RELEASE}",
         f"/DOutputBaseFilename={base}-setup", f"/DIconFile={PACKAGING / 'assets' / 'ActionMacro.ico'}",
         PACKAGING / "windows" / "ActionMacro.iss"])
    return [RELEASE / f"{base}-setup.exe", portable]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--skip-pyinstaller", action="store_true", help="reuse dist/pyinstaller")
    parser.add_argument("--expect-version", help="fail unless the app version equals this (tag check)")
    args = parser.parse_args(argv)

    version = app_version()
    expected = (args.expect_version or "").lstrip("v")
    if expected and expected != version:
        raise SystemExit(f"tag version {expected} does not match the app version {version}")
    arch = arch_label()
    print(f"{APP_NAME} {version} for {sys.platform}/{arch}", flush=True)

    if not args.skip_pyinstaller:
        build_pyinstaller()
    report = self_test()

    RELEASE.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        outputs = package_macos(version, arch)
    elif sys.platform == "win32":
        outputs = package_windows(version, arch)
    else:
        raise SystemExit("release packages are built on macOS and Windows only")

    (RELEASE / f"selftest-{sys.platform}-{arch}.json").write_text(json.dumps(report, indent=2) + "\n",
                                                                  encoding="utf-8")
    for path in outputs:
        print(f"{path.name}  {path.stat().st_size / 1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
