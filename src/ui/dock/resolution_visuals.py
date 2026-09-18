# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

from qgis.PyQt.QtWidgets import QLabel

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ..icons import pixmap_for
from . import design_tokens as tokens



RESOLUTION_VISUALS: dict[str, tuple[str, str]] = {
    "1K": ("grid_one", "teal"),
    "2K": ("grid_four", "sky"),
    "4K": ("grid_nine", "violet"),
}


DEFAULT_RESOLUTION_VISUAL: tuple[str, str] = ("grid_four", "teal")


RESOLUTION_TILE_PX = 26
RESOLUTION_TILE_GLYPH_PX = 15
RESOLUTION_CHIP_GLYPH_PX = 14


def resolution_visual(resolution: str | None) -> tuple[str, str]:

    return RESOLUTION_VISUALS.get(str(resolution or ""), DEFAULT_RESOLUTION_VISUAL)


def resolution_note(resolution: str | None) -> str:

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
