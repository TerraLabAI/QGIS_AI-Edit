"""Icon paths and stylesheet constants for the generation detail dialog."""
from __future__ import annotations

import os

from ...dock.style import (
    _BTN_LABEL_WEIGHT,
    BRAND_GREEN,
    COPY_BTN_QSS,
    COPY_SVG,
    ICONS_DIR,
    STAR_FILLED_SVG,
    STAR_OUTLINE_SVG,
)

# The dock's style module owns the icon paths and the copy button. Re-exported
# under the local names this package and its facade already import.
_ICONS_DIR = ICONS_DIR
_STAR_OUTLINE_SVG = STAR_OUTLINE_SVG
_STAR_FILLED_SVG = STAR_FILLED_SVG
_COPY_SVG = COPY_SVG
_COPY_BTN = COPY_BTN_QSS
_DOWNLOAD_SVG = os.path.join(ICONS_DIR, "download.svg")


_DETAIL_TITLE_STYLE = (
    "color: palette(text); font-size: 18px; font-weight: 800; "
    "letter-spacing: -0.2px; background: transparent; border: none;"
)
_DETAIL_SECTION_STYLE = (
    "color: rgba(128,128,128,0.95); font-size: 10px; font-weight: 700; "
    "letter-spacing: 1.2px; background: transparent; border: none;"
)
# Type/category tag above the title. Neutral pill: the tag is metadata, not
# an action, so it carries no brand colour (green is reserved for actions).
_DETAIL_BADGE_STYLE = (
    "QLabel { color: palette(text); background: rgba(128,128,128,0.10); "
    "border: 1px solid rgba(128,128,128,0.30); border-radius: 9px; "
    "font-size: 10px; font-weight: 800; letter-spacing: 1.0px; "
    "padding: 2px 9px; }"
)
_SEPARATOR = "background: rgba(128,128,128,0.20); border: none;"
_PROMPT_STYLE = (
    "QLabel { color: palette(text); font-size: 12px; "
    "background: rgba(128,128,128,0.05); border: 1px solid rgba(128,128,128,0.15); "
    "border-radius: 4px; padding: 8px 10px; }"
)
_CHIP_STYLE = (
    "QFrame { background: rgba(128,128,128,0.06); "
    "border: 1px solid rgba(128,128,128,0.15); border-radius: 4px; }"
)
_CHIP_CAPTION = (
    "color: rgba(128,128,128,0.95); font-size: 9px; font-weight: 600; "
    "letter-spacing: 0.5px; background: transparent; border: none;"
)
_CHIP_VALUE = (
    "color: palette(text); font-size: 12px; font-weight: 600; "
    "background: transparent; border: none;"
)
_ACTION_BTN = (
    "QPushButton { background: transparent; border: 1px solid rgba(128,128,128,0.35); "
    f"border-radius: 4px; padding: 7px 12px; font-size: 12px; color: palette(text); "
    f"{_BTN_LABEL_WEIGHT} }}"
    "QPushButton:hover { background: rgba(128,128,128,0.12); "
    "border-color: rgba(128,128,128,0.55); }"
    "QPushButton:disabled { color: rgba(128,128,128,0.5); "
    "border-color: rgba(128,128,128,0.15); }"
)
_PRIMARY_BTN = (
    f"QPushButton {{ background: {BRAND_GREEN}; border: none; border-radius: 4px; "
    "padding: 8px 14px; font-size: 12px; font-weight: 600; color: #14210A; }"
    "QPushButton:hover { background: #76a32a; }"
    "QPushButton:disabled { background: rgba(128,128,128,0.25); color: rgba(128,128,128,0.6); }"
)
# Destructive secondary (session Delete): the shape of _ACTION_BTN with the
# brand red as text + a soft tint on hover, never a full red fill (design
# system rule). Hues are the existing BRAND_RED rgb(211,47,47) / ERROR_TEXT.
_DANGER_BTN = (
    "QPushButton { background: transparent; border: 1px solid rgba(211,47,47,0.35); "
    "border-radius: 4px; padding: 7px 12px; font-size: 12px; color: #ef5350; }"
    "QPushButton:hover { background: rgba(211,47,47,0.10); "
    "border-color: rgba(211,47,47,0.55); }"
)
_FS_BTN = (
    "QToolButton { background: rgba(0,0,0,0.55); color: white; border: none; "
    "border-radius: 15px; font-size: 15px; }"
    "QToolButton:hover { background: rgba(0,0,0,0.8); }"
)
_REF_THUMB = (
    "QLabel { border: 1px solid rgba(128,128,128,0.3); border-radius: 4px; "
    "background: rgba(128,128,128,0.06); }"
)
_REF_OVERLAY_BTN = (
    "QToolButton { background: rgba(255,255,255,0.92); border: none; "
    "border-radius: 5px; font-size: 13px; color: #14210A; }"
    "QToolButton:hover { background: #ffffff; }"
)
