"""
action_macro.gui.icons
=========================
Embedded Lucide icon set rendered through QtSvg in any colour.

Icons are stored as the inner markup of Lucide's 24x24 stroke icons and wrapped
into a full SVG document on demand, so one definition serves every colour and
size. :func:`icon` returns a cached ``QIcon`` with crisp 1x and 2x pixmaps and
optional colours for the disabled, hover (active) and checked states.

Lucide licence notice
---------------------
The icon paths below are taken from Lucide (https://lucide.dev).

ISC License

Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of
Feather (MIT). All other copyright (c) for Lucide are held by Lucide Contributors
2022.

Permission to use, copy, modify, and/or distribute this software for any purpose
with or without fee is hereby granted, provided that the above copyright notice
and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, List, Optional

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from action_macro.gui.theme import COLORS

#: Lucide icon name -> inner SVG markup (24x24 viewBox, stroke = currentColor).
LUCIDE: Dict[str, str] = {
    "mouse-pointer-click": (
        '<path d="m9 9 5 12 1.8-5.2L21 14Z"/><path d="M7.2 2.2 8 5.1"/>'
        '<path d="m5.1 8-2.9-.8"/><path d="M14 4.1 12 6"/><path d="m6 12-1.9 2"/>'
    ),
    "keyboard": (
        '<path d="M10 8h.01"/><path d="M12 12h.01"/><path d="M14 8h.01"/>'
        '<path d="M16 12h.01"/><path d="M18 8h.01"/><path d="M6 8h.01"/>'
        '<path d="M7 16h10"/><path d="M8 12h.01"/>'
        '<rect width="20" height="16" x="2" y="4" rx="2"/>'
    ),
    "timer": (
        '<line x1="10" x2="14" y1="2" y2="2"/><line x1="12" x2="15" y1="14" y2="11"/>'
        '<circle cx="12" cy="14" r="8"/>'
    ),
    "image": (
        '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>'
        '<circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'
    ),
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "pause": (
        '<rect x="14" y="4" width="4" height="16" rx="1"/>'
        '<rect x="6" y="4" width="4" height="16" rx="1"/>'
    ),
    "square": '<rect width="18" height="18" x="3" y="3" rx="2"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "minus": '<path d="M5 12h14"/>',
    "folder-open": (
        '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1'
        '-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0'
        ' 1.67.9H18a2 2 0 0 1 2 2v2"/>'
    ),
    "file-plus": (
        '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
        '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M9 15h6"/><path d="M12 18v-6"/>'
    ),
    "file-text": (
        '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
        '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/>'
        '<path d="M16 17H8"/>'
    ),
    "file-x": (
        '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
        '<path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="m14.5 12.5-5 5"/><path d="m9.5 12.5 5 5"/>'
    ),
    "files": (
        '<path d="M20 7h-3a2 2 0 0 1-2-2V2"/>'
        '<path d="M9 18a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h7l4 4v10a2 2 0 0 1-2 2Z"/>'
        '<path d="M3 7.6v12.8A1.6 1.6 0 0 0 4.6 22h9.8"/>'
    ),
    "save": (
        '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1'
        '-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/>'
        '<path d="M7 3v4a1 1 0 0 0 1 1h7"/>'
    ),
    "settings": (
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08'
        'a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74'
        'l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1'
        ' 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0'
        'l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1'
        ' 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0'
        'l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>'
    ),
    "terminal": '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>',
    "list": (
        '<line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/>'
        '<line x1="8" x2="21" y1="18" y2="18"/><line x1="3" x2="3.01" y1="6" y2="6"/>'
        '<line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/>'
    ),
    "grip-vertical": (
        '<circle cx="9" cy="12" r="1"/><circle cx="9" cy="5" r="1"/><circle cx="9" cy="19" r="1"/>'
        '<circle cx="15" cy="12" r="1"/><circle cx="15" cy="5" r="1"/><circle cx="15" cy="19" r="1"/>'
    ),
    "more-horizontal": (
        '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>'
    ),
    "trash": (
        '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
        '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>'
        '<line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>'
    ),
    "copy": (
        '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
        '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
    ),
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "crosshair": (
        '<circle cx="12" cy="12" r="10"/><line x1="22" x2="18" y1="12" y2="12"/>'
        '<line x1="6" x2="2" y1="12" y2="12"/><line x1="12" x2="12" y1="6" y2="2"/>'
        '<line x1="12" x2="12" y1="22" y2="18"/>'
    ),
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "shield-alert": (
        '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1'
        ' 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>'
        '<path d="M12 8v4"/><path d="M12 16h.01"/>'
    ),
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "alert-triangle": (
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
        '<path d="M12 9v4"/><path d="M12 17h.01"/>'
    ),
    "alert-circle": (
        '<circle cx="12" cy="12" r="10"/><line x1="12" x2="12" y1="8" y2="12"/>'
        '<line x1="12" x2="12.01" y1="16" y2="16"/>'
    ),
    "circle-check": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "circle-dot": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="1"/>',
    "circle-help": (
        '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
        '<path d="M12 17h.01"/>'
    ),
    "infinity": (
        '<path d="M12 12c-2-2.67-4-4-6-4a4 4 0 1 0 0 8c2 0 4-1.33 6-4Zm0 0c2 2.67 4 4 6 4'
        'a4 4 0 0 0 0-8c-2 0-4 1.33-6 4Z"/>'
    ),
    "repeat": (
        '<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/>'
        '<path d="m7 22-4-4 4-4"/><path d="M21 13v1a4 4 0 0 1-4 4H3"/>'
    ),
    "history": (
        '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>'
        '<path d="M12 7v5l4 2"/>'
    ),
    "panel-left": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/>',
    "panel-bottom": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 15h18"/>',
    "power": '<path d="M12 2v10"/><path d="M18.4 6.6a9 9 0 1 1-12.77.04"/>',
    "pencil": (
        '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352'
        'a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/>'
    ),
    "external-link": (
        '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
        '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
    ),
    "filter": '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    # phase2:cards
    "list-plus": (
        '<path d="M11 12H3"/><path d="M16 6H3"/><path d="M16 18H3"/>'
        '<path d="M18 9v6"/><path d="M21 12h-6"/>'
    ),
    # phase2:console (auto-scroll toggle of the console header)
    "chevrons-down": '<path d="m7 6 5 5 5-5"/><path d="m7 13 5 5 5-5"/>',
    # phase2:perms
    "accessibility": (
        '<circle cx="16" cy="4" r="1"/><path d="m18 19 1-7-6 1"/>'
        '<path d="m5 8 3-3 5.5 3-2.36 3.5"/><path d="M4.24 14.5a5 5 0 0 0 6.88 6"/>'
        '<path d="M13.76 17.5a5 5 0 0 0-6.88-6"/>'
    ),
    "monitor": (
        '<rect width="20" height="14" x="2" y="3" rx="2"/>'
        '<line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/>'
    ),
    "refresh-cw": (
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>'
    ),
    "shield": (
        '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1'
        ' 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>'
    ),
    "shield-check": (
        '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1'
        ' 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>'
        '<path d="m9 12 2 2 4-4"/>'
    ),
}

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)

#: Filled glyphs: rendered with fill as well as stroke so they read as solid shapes.
_FILLED = {"play", "pause", "square"}

_APP_MARK_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#A78BFA"/>
      <stop offset="1" stop-color="#6D28D9"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="15" fill="url(#g)"/>
  <path d="M25 17 L25 45 L32 38.6 L36.8 48.6 L41.4 46.4 L36.6 36.6 L45.6 36 Z"
        fill="#FFFFFF" stroke="#FFFFFF" stroke-width="1.6" stroke-linejoin="round"/>
  <g stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" opacity="0.9">
    <path d="M20.5 12.5 L18 10"/>
    <path d="M18.5 19 L15 19"/>
    <path d="M25.5 11 L25.5 7.5"/>
  </g>
</svg>
"""


def available_icons() -> List[str]:
    """Returns the sorted names of all embedded icons."""
    return sorted(LUCIDE)


def svg_markup(name: str, color: str = COLORS.text_muted, stroke_width: float = 2.0) -> str:
    """Returns a complete SVG document for icon ``name`` drawn in ``color``.

    Raises:
        KeyError: if ``name`` is not an embedded icon.
    """
    if name not in LUCIDE:
        raise KeyError(f"Unknown icon {name!r}; available: {', '.join(available_icons())}")
    body = LUCIDE[name]
    if name in _FILLED:
        body = f'<g fill="{color}">{body}</g>'
    return _SVG_TEMPLATE.format(color=color, stroke=f"{stroke_width:g}", body=body)


def _render_svg(markup: str, size: int, dpr: float) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(markup.encode("utf-8")))
    px = max(1, int(round(size * dpr)))
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, px, px))
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _pad_right(pixmap: QPixmap, gap: int) -> QPixmap:
    """Returns ``pixmap`` followed by ``gap`` transparent logical pixels (same device pixel ratio)."""
    if gap <= 0:
        return pixmap
    dpr = pixmap.devicePixelRatio()
    padded = QPixmap(pixmap.width() + int(round(gap * dpr)), pixmap.height())
    padded.setDevicePixelRatio(dpr)
    padded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(padded)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return padded


def render_pixmap(name: str, color: str = COLORS.text_muted, size: int = 16, dpr: float = 1.0) -> QPixmap:
    """Renders icon ``name`` in ``color`` as a ``size`` x ``size`` (logical) pixmap."""
    return _render_svg(svg_markup(name, color), size, dpr)


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = COLORS.text_muted, size: int = 16) -> QPixmap:
    """Returns a cached 2x pixmap of ``name`` (sharp on Retina, scaled down elsewhere)."""
    return render_pixmap(name, color, size, dpr=2.0)


@lru_cache(maxsize=512)
def icon(
    name: str,
    color: Optional[str] = None,
    size: int = 16,
    *,
    active_color: Optional[str] = None,
    checked_color: Optional[str] = None,
    disabled_color: Optional[str] = None,
    trailing_gap: int = 0,
) -> QIcon:
    """Returns a cached QIcon of Lucide icon ``name``.

    Args:
        name: Icon name (see :func:`available_icons`).
        color: Normal colour; defaults to the muted text token.
        size: Logical size in pixels the icon is designed for.
        active_color: Colour while hovered (``QIcon.Mode.Active``); defaults to ``color``.
        checked_color: Colour for the checked (``On``) state; defaults to ``color``.
        disabled_color: Colour when disabled; defaults to the faint text token.
        trailing_gap: Transparent logical pixels after the glyph. QPushButton has no
            style-sheet property for the icon-text spacing, so text buttons use this
            gap (and a matching ``setIconSize``) to keep the label off the icon.
    """
    color = color or COLORS.text_muted
    variants = (
        (QIcon.Mode.Normal, QIcon.State.Off, color),
        (QIcon.Mode.Active, QIcon.State.Off, active_color or color),
        (QIcon.Mode.Selected, QIcon.State.Off, active_color or color),
        (QIcon.Mode.Disabled, QIcon.State.Off, disabled_color or COLORS.text_faint),
        (QIcon.Mode.Normal, QIcon.State.On, checked_color or color),
        (QIcon.Mode.Active, QIcon.State.On, checked_color or active_color or color),
        (QIcon.Mode.Selected, QIcon.State.On, checked_color or active_color or color),
        (QIcon.Mode.Disabled, QIcon.State.On, disabled_color or COLORS.text_faint),
    )
    result = QIcon()
    for mode, state, variant_color in variants:
        markup = svg_markup(name, variant_color)
        for dpr in (1.0, 2.0):
            result.addPixmap(_pad_right(_render_svg(markup, size, dpr), trailing_gap), mode, state)
    return result


@lru_cache(maxsize=16)
def app_mark_pixmap(size: int = 20) -> QPixmap:
    """Returns the violet ActionMacro mark as a 2x pixmap of logical ``size``."""
    return _render_svg(_APP_MARK_SVG, size, 2.0)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Returns the application/window icon (violet mark) at the usual sizes."""
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        result.addPixmap(_render_svg(_APP_MARK_SVG, size, 1.0))
    return result


def icon_size(size: int = 16) -> QSize:
    """Convenience: a square QSize for ``setIconSize``."""
    return QSize(size, size)
