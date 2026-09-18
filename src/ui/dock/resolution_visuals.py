# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One visual per output-size tier, shared by the footer chip and its rows.

AI Agent gives every permission mode one glyph and one accent and shows that
same pair on the chip and in the popover (``src/ui/permission_chip.py``,
``MODE_VISUALS``). Output size had neither: a bare word and a chevron, and a
flat list of identical grey rows behind it. Each tier now owns a glyph that
reads as pixel density (one block, four, nine) and one category hue, so the
picked level is readable without opening the menu.

The tier list is served, so an unknown key must still render: it falls back to
the middle glyph in the first hue rather than an empty tile.
"""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QLabel

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ..icons import pixmap_for
from . import design_tokens as tokens

# (glyph, category hue) per tier. Three hues, a cool ramp: none of them is the
# primary green, the amber of a warning or the coral of an error.
RESOLUTION_VISUALS: dict[str, tuple[str, str]] = {
    "1K": ("grid_one", "teal"),
    "2K": ("grid_four", "sky"),
    "4K": ("grid_nine", "violet"),
}

# What a tier served by a newer backend wears until it gets a visual of its own.
DEFAULT_RESOLUTION_VISUAL: tuple[str, str] = ("grid_four", "teal")

# The tile beside a row's name, and the smaller glyph the chip carries.
RESOLUTION_TILE_PX = 26
RESOLUTION_TILE_GLYPH_PX = 15
RESOLUTION_CHIP_GLYPH_PX = 14


def resolution_visual(resolution: str | None) -> tuple[str, str]:
    """``(glyph name, category hue)`` for a tier key."""
    return RESOLUTION_VISUALS.get(str(resolution or ""), DEFAULT_RESOLUTION_VISUAL)


def resolution_note(resolution: str | None) -> str:
    """One muted line saying what a tier is for, eight words at most."""
    key = str(resolution or "")
    if key == "1K":
        return get_export_copy(
            "dock.resolution_visuals.quality_note_1k", tr("Good for a quick test"))
    if key == "2K":
        return get_export_copy(
            "dock.resolution_visuals.quality_note_2k", tr("Sharp, clean result for real maps"))
    if key == "4K":
        return get_export_copy(
            "dock.resolution_visuals.quality_note_4k", tr("Best quality, crisp when zoomed or printed"))
    return get_export_copy(
        "dock.resolution_visuals.quality_note_default", tr("Another quality level"))


def resolution_tile(
    parent,
    resolution: str | None,
    size: int = RESOLUTION_TILE_PX,
    glyph_size: int = RESOLUTION_TILE_GLYPH_PX,
) -> QLabel:
    """A small rounded tile: the tier's glyph in its ink on a tint of that hue."""
    glyph, hue = resolution_visual(resolution)
    tile = QLabel(parent)
    tile.setObjectName("resolutionTile")
    tile.setFixedSize(size, size)
    tile.setAlignment(QtC.AlignCenter)
    tile.setStyleSheet(
        f"QLabel#resolutionTile {{ background: {tokens.category_tint(hue, strong=True)};"
        f" border: none; border-radius: {size // 3 + 2}px; }}"
    )
    tile.setPixmap(pixmap_for(tile, glyph, glyph_size, tokens.qcolor(tokens.category_ink(hue))))
    tile.setAttribute(QtC.WA_TransparentForMouseEvents, True)
    return tile
