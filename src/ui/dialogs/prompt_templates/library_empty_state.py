"""The Prompt Library's empty states, on AI Agent's (ChatGPT's) home pattern.

A round tile in the library's leaf hue with the page's glyph, one question
under it, an optional line, then a short column of quiet rows: a glyph and a muted line that turns to the ink on
a blue wash under the pointer. Each row is a way out of the empty page
(browse the top picks, clear the search), never a dead end.
"""
from __future__ import annotations

from collections.abc import Callable

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ....core import qt_compat as QtC
from ...dock import design_tokens as tk
from ...icons import icon_for, pixmap_for
from .common import tinted_svg_pixmap

_EMPTY_QUESTION_QSS = (
    f"color: {tk.INK}; font-size: {tk.FONT_BASE + 4}px; font-weight: 500;"
    " background: transparent; border: none;"
)
_EMPTY_HINT_QSS = (
    f"color: {tk.INK_2}; font-size: {tk.FONT_BODY}px;"
    " background: transparent; border: none;"
)
_EMPTY_ROW_QSS = (
    f"QPushButton {{ background: transparent; color: {tk.INK_2}; border: 1px solid transparent;"
    f" border-radius: {tk.RADIUS_PANEL}px; padding: 6px 13px;"
    f" font-size: {tk.FONT_BASE}px; text-align: left; }}"
    f"QPushButton:hover {{ background: {tk.ACCENT_TINT}; color: {tk.INK}; }}"
    f"QPushButton:pressed {{ background: {tk.ACCENT_TINT_ON}; }}"
    f"QPushButton:focus {{ border-color: {tk.ACCENT_BORDER}; color: {tk.INK}; }}"
)
_EMPTY_ROW_GLYPH_PX = 18
# The optional round tile over the question: the library's hue (leaf, the
# category helpers of ``dock/design_tokens.py``), its ink on its tint.
_EMPTY_TILE_CATEGORY = "leaf"
_EMPTY_TILE_PX = 48
_EMPTY_TILE_GLYPH_PX = 24
_EMPTY_BLOCK_MAX_W = 460

# (glyph name from src/ui/icons.py, row text, what a click does)
EmptySuggestion = tuple[str, str, Callable[[], None]]


def build_library_empty_state(
    question: str,
    hint: str = "",
    suggestions: list[EmptySuggestion] | None = None,
    top_margin: int = 64,
    glyph: str = "",
    icon=None,
) -> QWidget:
    """A centred empty page: the tile, the question, the hint, the
    suggestion rows. ``glyph`` names a painted glyph (src/ui/icons.py);
    ``icon`` is the path of an SVG file for the few glyphs that only exist
    as a file (the favorites star)."""
    outer = QWidget()
    outer.setObjectName("libraryEmpty")
    row = QHBoxLayout(outer)
    row.setContentsMargins(16, top_margin, 16, 32)
    row.addStretch(1)

    block = QWidget(outer)
    block.setMaximumWidth(_EMPTY_BLOCK_MAX_W)
    col = QVBoxLayout(block)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(tk.SPACE_OUTER)

    if glyph or icon is not None:
        tile = QLabel(block)
        tile.setFixedSize(_EMPTY_TILE_PX, _EMPTY_TILE_PX)
        tile.setAlignment(QtC.AlignCenter)
        tile.setStyleSheet(
            f"background: {tk.category_tint(_EMPTY_TILE_CATEGORY)}; border: none;"
            f" border-radius: {_EMPTY_TILE_PX // 2}px;"
        )
        if icon is not None:
            # A file icon (the favorites star) takes the tile's ink, like the
            # painted glyphs, instead of its own grey.
            tile.setPixmap(tinted_svg_pixmap(
                icon, _EMPTY_TILE_GLYPH_PX,
                tk.qcolor(tk.category_ink(_EMPTY_TILE_CATEGORY))))
        else:
            tile.setPixmap(pixmap_for(
                tile, glyph, _EMPTY_TILE_GLYPH_PX,
                tk.qcolor(tk.category_ink(_EMPTY_TILE_CATEGORY))))
        col.addWidget(tile, 0, Qt.AlignmentFlag.AlignHCenter)
        col.addSpacing(tk.SPACE_CARD)

    title = QLabel(question, block)
    title.setStyleSheet(_EMPTY_QUESTION_QSS)
    title.setAlignment(QtC.AlignCenter)
    title.setWordWrap(True)
    title.setTextFormat(QtC.PlainText)
    col.addWidget(title)

    if hint:
        sub = QLabel(hint, block)
        sub.setStyleSheet(_EMPTY_HINT_QSS)
        sub.setAlignment(QtC.AlignCenter)
        sub.setWordWrap(True)
        sub.setTextFormat(QtC.PlainText)
        col.addWidget(sub)

    if suggestions:
        col.addSpacing(tk.SPACE_OUTER)
        for glyph, text, on_click in suggestions:
            button = QPushButton(text, block)
            button.setStyleSheet(_EMPTY_ROW_QSS)
            button.setCursor(QtC.PointingHandCursor)
            button.setAutoDefault(False)
            button.setIcon(icon_for(button, glyph, _EMPTY_ROW_GLYPH_PX, tk.qcolor(tk.INK_2)))
            button.setIconSize(QSize(_EMPTY_ROW_GLYPH_PX, _EMPTY_ROW_GLYPH_PX))
            button.setAccessibleName(text)
            button.clicked.connect(lambda _c=False, cb=on_click: cb())
            col.addWidget(button)

    row.addWidget(block, 0, QtC.AlignTop)
    row.addStretch(1)
    return outer
