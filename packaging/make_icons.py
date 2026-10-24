"""Renders the application icon files in ``packaging/assets`` from the in-app mark.

The mark is the same SVG the window and top bar use (``action_macro.gui.icons``):

- ``ActionMacro.png``: 1024 px, full bleed (README, Linux, reference).
- ``ActionMacro.ico``: Windows, 16-256 px, full bleed.
- ``ActionMacro.icns``: macOS, drawn on the macOS icon grid (an 824 px tile on a
  1024 px canvas), built with ``iconutil`` (macOS only).

The outputs are committed, so builds do not need to run this. Re-run it after
changing the mark::

    QT_QPA_PLATFORM=offscreen .venv/bin/python packaging/make_icons.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "packaging" / "assets"
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QByteArray, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from action_macro.gui.icons import _APP_MARK_SVG  # noqa: E402

# The mark's rounded square spans 60 of its 64 viewBox units.
MARK_TILE_FRACTION = 60.0 / 64.0
# macOS icon grid: an 824 px rounded tile centred on a 1024 px canvas.
MACOS_TILE = 824
MACOS_CANVAS = 1024


def render(size: int, tile: float = 1.0) -> QImage:
    """The mark at ``size`` px; ``tile`` is the rounded tile's share of the canvas."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_APP_MARK_SVG.encode("utf-8")))
    side = size * tile / MARK_TILE_FRACTION
    offset = (size - side) / 2.0
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(offset, offset, side, side))
    painter.end()
    return image


def save_png(image: QImage, path: Path) -> None:
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"could not write {path}")


def main() -> int:
    QGuiApplication([sys.argv[0]])
    ASSETS.mkdir(parents=True, exist_ok=True)

    save_png(render(1024), ASSETS / "ActionMacro.png")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        ico_sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
        frames = []
        for size in ico_sizes:
            path = tmp_dir / f"ico_{size}.png"
            save_png(render(size), path)
            frames.append(Image.open(path).convert("RGBA"))
        frames[-1].save(ASSETS / "ActionMacro.ico", format="ICO", sizes=[(s, s) for s in ico_sizes],
                        append_images=frames[:-1])

        if sys.platform == "darwin" and shutil.which("iconutil"):
            iconset = tmp_dir / "ActionMacro.iconset"
            iconset.mkdir()
            tile = MACOS_TILE / MACOS_CANVAS
            for base in (16, 32, 128, 256, 512):
                for scale in (1, 2):
                    name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
                    save_png(render(base * scale, tile=tile), iconset / name)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "ActionMacro.icns")],
                           check=True)
        else:
            print("iconutil not available: ActionMacro.icns left unchanged", file=sys.stderr)

    for name in ("ActionMacro.png", "ActionMacro.ico", "ActionMacro.icns"):
        path = ASSETS / name
        if path.exists():
            print(f"{path.relative_to(ROOT)}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
