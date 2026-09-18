"""The colour pass for the Settings window: which hue each topic wears, the
icon tile (a glyph in the hue's ink on its tint, like the macOS Settings rows)
and the account avatar's hue.

Mirrored from AI Segmentation's ``src/ui/settings/category_tile.py`` so the
two plugins keep one map. The hues live in ``dock/design_tokens.py``
(``category_*``). One topic keeps one hue everywhere: the Billing page and its
rail row are amber, the Tutorials coral, and so on.
"""
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

# The rail glyph of a page or action row -> its hue. A glyph not listed keeps
# the neutral ink and the grey selection.
NAV_GLYPH_CATEGORIES = {
    "person": "green",       # Account
    "gem": "amber",          # Billing
    "play": "coral",         # Tutorials
    "terminal": "leaf",      # Keyboard shortcuts
    "puzzle": "violet",      # More plugins
    "chat_bubble": "sky",    # Contact us
    "warning": "coral",      # Report a problem
}

# Coral is kept for danger, so an address never draws a red avatar.
_AVATAR_CATEGORIES = ("green", "leaf", "amber", "teal", "violet", "sky")
# A tile with no hue: the field step under the second ink (a token, so it
# follows the theme like every other ground).
_NEUTRAL_TILE_GROUND = FIELD

TILE_PX = 30
TILE_GLYPH_PX = 17


def category_icon_tile(glyph: str, category: str | None, parent: QWidget | None = None,
                       tile_px: int = TILE_PX, glyph_px: int = TILE_GLYPH_PX,
                       radius: int | None = None) -> QLabel:
    """A rounded square in the hue's tint with the glyph in the hue's ink.

    ``category`` None draws a neutral tile. ``radius`` defaults to the row
    radius; half the size makes it round. Decorative: the words beside it
    carry the meaning.
    """
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
        pass  # nosec B110 - an empty tile still reads as a tile
    tile.setAccessibleName("")
    return tile


def tile_beside(tile: QLabel, widget: QWidget, spacing: int = 12) -> QHBoxLayout:
    """A row with the tile at left, vertically centred, and ``widget`` after it."""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    row.addWidget(tile, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addWidget(widget, 1)
    return row


def avatar_category(email: str) -> str:
    """A hue picked from the address, the same one on every open."""
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
