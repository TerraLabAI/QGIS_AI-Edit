"""Icon paths and stylesheet constants for the generation detail dialog."""
from __future__ import annotations

import os

from ...dock.design_tokens import (
    ACCENT_BORDER,
    BTN_DANGER_GHOST_QSS,
    BTN_GHOST_QSS,
    BTN_PRIMARY_WIDE_QSS,
    CANVAS,
    FIELD,
    FONT_BODY,
    FONT_MICRO,
    HEADLINE_QSS,
    HOVER,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    INSET,
    LINE,
    LINE_STRONG,
    MICRO_QSS,
    RADIUS_CARD,
    RADIUS_CHIP,
    RADIUS_CONTROL,
    RADIUS_PILL_WIDE,
    SURFACE,
    TOOLTIP_QSS,
)
from ...dock.style import (
    COPY_BTN_QSS,
    COPY_SVG,
    ICONS_DIR,
    STAR_FILLED_SVG,
    STAR_OUTLINE_SVG,
)

# Private names are listed too: other modules import them from here.
__all__ = [
    "_ACTION_BTN",
    "_CHIP_CAPTION",
    "_CHIP_STYLE",
    "_CHIP_VALUE",
    "_COPY_BTN",
    "_COPY_SVG",
    "_DANGER_BTN",
    "_DETAIL_DIALOG_QSS",
    "_DETAIL_BADGE_STYLE",
    "_DETAIL_SECTION_STYLE",
    "_DETAIL_TITLE_STYLE",
    "_DOWNLOAD_SVG",
    "_FS_BTN",
    "_LIGHTBOX_QSS",
    "_PRIMARY_BTN",
    "_PROMPT_STYLE",
    "_REF_OVERLAY_BTN",
    "_REF_THUMB",
    "_SEPARATOR",
    "_STAR_BTN",
    "_STAR_FILLED_SVG",
    "_STAR_OUTLINE_SVG",
]

# The dock's style module owns the icon paths and the copy button. Re-exported
# under the local names this package and its facade already import.
_STAR_OUTLINE_SVG = STAR_OUTLINE_SVG
_STAR_FILLED_SVG = STAR_FILLED_SVG
_COPY_SVG = COPY_SVG
_COPY_BTN = COPY_BTN_QSS
_DOWNLOAD_SVG = os.path.join(ICONS_DIR, "download.svg")

# The window itself sits on the canvas step, as the Prompt Library does, so the
# surface cards and ghost pills read above it instead of on the native grey.
_DETAIL_DIALOG_QSS = f"QDialog#generationDetail {{ background: {CANVAS}; }}" + TOOLTIP_QSS
# The reference viewer sits on the same canvas step as the window it opens
# from, instead of the native grey.
_LIGHTBOX_QSS = f"QDialog#referenceLightbox {{ background: {CANVAS}; }}" + TOOLTIP_QSS
# The keyboard focus ring for the round icon buttons: the same 2 px focus blue
# every other control wears, which they had none of.
_ROUND_FOCUS = f"QToolButton:focus {{ border: 2px solid {ACCENT_BORDER}; }}"

# The AI Agent dialog line: a 600 headline, micro section headers, the prompt
# in a well, facts on small cards, pill buttons.
_DETAIL_TITLE_STYLE = HEADLINE_QSS
_DETAIL_SECTION_STYLE = MICRO_QSS
# Type tag above the title: a field chip. The tag is metadata, not an action,
# so it carries no brand colour.
_DETAIL_BADGE_STYLE = (
    f"QLabel {{ color: {INK_2}; background: {FIELD}; border: none;"
    f" border-radius: {RADIUS_CHIP}px; font-size: {FONT_MICRO}px; font-weight: 600;"
    " padding: 2px 8px; }"
)
_SEPARATOR = f"background: {LINE}; border: none;"
_PROMPT_STYLE = (
    f"QLabel {{ color: {INK}; font-size: {FONT_BODY}px;"
    f" background: {INSET}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 8px 10px; }}"
)
# One fact (resolution, duration, date): a small card. Keyed on the object
# name so the rule never cascades into the labels inside (a QLabel is a QFrame).
_CHIP_STYLE = (
    f"QFrame#detailFactCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
)
_CHIP_CAPTION = (
    f"color: {INK_3}; font-size: {FONT_MICRO}px; font-weight: 600;"
    " background: transparent; border: none;"
)
_CHIP_VALUE = (
    f"color: {INK}; font-size: {FONT_BODY}px; font-weight: 600;"
    " background: transparent; border: none;"
)
_ACTION_BTN = BTN_GHOST_QSS
_PRIMARY_BTN = BTN_PRIMARY_WIDE_QSS
# Destructive secondary (session Delete): red text on the ghost pill, never a
# red fill.
_DANGER_BTN = BTN_DANGER_GHOST_QSS
# Round icon button floating over the picture (fullscreen): a field chip,
# ringed in the focus blue while the picture is maximised.
_FS_BTN = (
    f"QToolButton {{ background: {FIELD}; border: none; border-radius: 15px; padding: 0; }}"
    f"QToolButton:hover {{ background: {SURFACE}; }}"
    f"QToolButton:pressed {{ background: {HOVER_ON}; }}"
    f"QToolButton:checked {{ background: {SURFACE}; border: 2px solid {ACCENT_BORDER}; }}"
    + _ROUND_FOCUS
)
# The favourite toggle beside the primary: a round ghost at the primary's height.
_STAR_BTN = (
    f"QToolButton {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {RADIUS_PILL_WIDE}px; padding: 0; }}"
    f"QToolButton:hover {{ background: {HOVER}; border-color: {INK_3}; }}"
    f"QToolButton:pressed {{ background: {HOVER_ON}; }}"
    + _ROUND_FOCUS
)
_REF_THUMB = (
    f"QLabel {{ border: 1px solid {LINE}; border-radius: {RADIUS_CONTROL}px;"
    f" background: {FIELD}; }}"
)
# The overlay buttons' side: the 28 px minimum click target.
_REF_OVERLAY_BTN_PX = 28
_REF_OVERLAY_BTN = (
    f"QToolButton {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {_REF_OVERLAY_BTN_PX // 2}px; padding: 0; }}"
    f"QToolButton:hover {{ background: {HOVER}; }}"
    + _ROUND_FOCUS
)
