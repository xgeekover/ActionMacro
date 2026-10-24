# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ActionMacro desktop app (macOS .app bundle, Windows folder).

Run through ``packaging/build_release.py``, which also self-tests and packages the
result. Directly::

    python -m PyInstaller packaging/ActionMacro.spec --noconfirm \
        --distpath dist/pyinstaller --workpath build/pyinstaller

One-folder build (not one-file): it starts fast, antivirus tools flag it less, and the
Qt libraries stay separate, replaceable files (LGPL).
"""

import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - provided by PyInstaller
SRC = ROOT / "src"
ASSETS = ROOT / "packaging" / "assets"
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "action_macro" / "__init__.py").read_text()).group(1)
APP_NAME = "ActionMacro"
BUNDLE_ID = "io.github.xgeekover.actionmacro"

# pynput picks its platform back end at import time with importlib, which PyInstaller
# cannot see. The self-test (``--self-test``) fails if one of these is missing.
hiddenimports = []
if sys.platform == "darwin":
    hiddenimports += ["pynput.keyboard._darwin", "pynput.mouse._darwin"]
elif sys.platform == "win32":
    hiddenimports += ["pynput.keyboard._win32", "pynput.mouse._win32"]

# Large modules the app never imports (some come in through optional imports).
excludes = [
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtPdf",
    "PySide6.QtNetwork",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(ROOT / "packaging" / "THIRD_PARTY_NOTICES.txt"), ".")],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# Qt parts that come in only through plugins the app never uses (the QML virtual
# keyboard input context, the PDF image format, network information and TLS back ends)
# and their libraries: about 20 MB. The self-test checks the window still starts.
_UNUSED_QT = ("qml", "quick", "virtualkeyboard", "pdf", "networkinformation", "/tls/", "uiotouch",
              "qtnetwork", "qt6network")


def _keep(entry):
    name = entry[0].replace("\\", "/").lower()
    # macOS also gets top-level symlinks (Frameworks/QtQml -> PySide6/Qt/lib/QtQml.framework/…).
    target = str(entry[1]).replace("\\", "/").lower() if entry[2] == "SYMLINK" else ""
    in_qt = name.startswith(("pyside6/", "qt")) or "pyside6/" in target
    return not (in_qt and any(token in name or token in target for token in _UNUSED_QT))


a.binaries = [entry for entry in a.binaries if _keep(entry)]
a.datas = [entry for entry in a.datas if _keep(entry)]
pyz = PYZ(a.pure)  # noqa: F821

exe_options = {}
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    numbers = tuple(int(part) for part in (VERSION.split(".") + ["0", "0", "0"])[:4])
    exe_options["version"] = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", APP_NAME),
                StringStruct("FileDescription", "ActionMacro desktop automation"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", APP_NAME),
                StringStruct("OriginalFilename", f"{APP_NAME}.exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
    exe_options["icon"] = str(ASSETS / "ActionMacro.ico")
elif sys.platform == "darwin":
    exe_options["icon"] = str(ASSETS / "ActionMacro.icns")

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed Qt libraries break and trip antivirus heuristics
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    **exe_options,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,
)

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ASSETS / "ActionMacro.icns"),
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSApplicationCategoryType": "public.app-category.productivity",
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSHumanReadableCopyright": "ActionMacro contributors. MIT License.",
        },
    )
