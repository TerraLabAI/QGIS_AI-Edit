








from __future__ import annotations

import zlib

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QWidget

from ...dock.design_tokens import (
    FIELD,
    INK_2,
    RADIUS_CONTROL,
    category_ink,
    category_tint,
    qcolor,
)



NAV_GLYPH_CATEGORIES = {
    "person": "green",
    "gem": "amber",
    "play": "coral",
    "terminal": "leaf",
    "puzzle": "violet",
    "chat_bubble": "sky",
    "warning": "coral",
}


_AVATAR_CATEGORIES = ("green", "leaf", "amber", "teal", "violet", "sky")


_NEUTRAL_TILE_GROUND = FIELD

TILE_PX = 30
TILE_GLYPH_PX = 17


def category_icon_tile(glyph: str, category: str | None, parent: QWidget | None = None,
                       tile_px: int = TILE_PX, glyph_px: int = TILE_GLYPH_PX,
                       radius: int | None = None) -> QLabel:






    tile = QLabel(parent)
    tile.setObjectName("categoryTile")
    tile.setFixedSize(tile_px, tile_px)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    ground = category_tint(category) if category else _NEUTRAL_TILE_GROUND
    ink = category_ink(category) if category else INK_2
    corner = RADIUS_CONTROL if radius is None else radius
    tile.setStyleSheet(
        f"QLabel#categoryTile {{ background: {ground}; border: none;"
        f" border-radius: {corner}px; }}")
    try:
        from ...icons import pixmap_for

        tile.setPixmap(pixmap_for(tile, glyph, glyph_px, qcolor(ink)))
    except (ImportError, RuntimeError, AttributeError):
        pass  # nosec B110
    tile.setAccessibleName("")
    return tile


def tile_beside(tile: QLabel, widget: QWidget, spacing: int = 12) -> QHBoxLayout:

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    row.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(widget, 1)
    return row


def avatar_category(email: str) -> str:

    text = (email or "").strip().lower()
    if not text or text == "-":
        return "green"
    return _AVATAR_CATEGORIES[zlib.crc32(text.encode("utf-8")) % len(_AVATAR_CATEGORIES)]


__all__ = [
    "NAV_GLYPH_CATEGORIES",
    "avatar_category",
    "category_icon_tile",
    "tile_beside",
]
