"""Icon paths and stylesheet constants for the generation detail dialog."""
from __future__ import annotations

import os

_PLUGIN_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
_ICONS_DIR = os.path.join(_PLUGIN_DIR, "resources", "icons")
_STAR_OUTLINE_SVG = os.path.join(_ICONS_DIR, "star.svg")
_STAR_FILLED_SVG = os.path.join(_ICONS_DIR, "star-filled.svg")
_DOWNLOAD_SVG = os.path.join(_ICONS_DIR, "download.svg")
_COPY_SVG = os.path.join(_ICONS_DIR, "copy.svg")


_TITLE_STYLE = (
    "color: palette(text); font-size: 18px; font-weight: 800; "
    "letter-spacing: -0.2px; background: transparent; border: none;"
)
_SECTION_STYLE = (
    "color: rgba(128,128,128,0.95); font-size: 10px; font-weight: 700; "
    "letter-spacing: 1.2px; background: transparent; border: none;"
)
# Type/category tag above the title. Brand-green tint, hugs its content.
_BADGE_STYLE = (
    "QLabel { color: #6f8c1e; background: rgba(139,172,39,0.13); "
    "border: 1px solid rgba(139,172,39,0.40); border-radius: 9px; "
    "font-size: 10px; font-weight: 800; letter-spacing: 1.0px; "
    "padding: 2px 9px; }"
)
_SEPARATOR = "background: rgba(128,128,128,0.20); border: none;"
_PROMPT_STYLE = (
    "QLabel { color: palette(text); font-size: 12px; "
    "background: rgba(128,128,128,0.05); border: 1px solid rgba(128,128,128,0.15); "
    "border-radius: 4px; padding: 8px 10px; }"
)
# Tiny flat "Copy" affordance sitting on the PROMPT section header.
_COPY_BTN = (
    "QPushButton { background: transparent; border: none; "
    "color: rgba(128,128,128,0.95); font-size: 11px; font-weight: 600; "
    "padding: 1px 6px; border-radius: 4px; }"
    "QPushButton:hover { background: rgba(128,128,128,0.14); color: palette(text); }"
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
    "border-radius: 4px; padding: 7px 12px; font-size: 12px; color: palette(text); }"
    "QPushButton:hover { background: rgba(128,128,128,0.12); "
    "border-color: rgba(128,128,128,0.55); }"
    "QPushButton:disabled { color: rgba(128,128,128,0.5); "
    "border-color: rgba(128,128,128,0.15); }"
)
_PRIMARY_BTN = (
    "QPushButton { background: #8bac27; border: none; border-radius: 4px; "
    "padding: 8px 14px; font-size: 12px; font-weight: 600; color: #14210A; }"
    "QPushButton:hover { background: #76a32a; }"
    "QPushButton:disabled { background: rgba(128,128,128,0.25); color: rgba(128,128,128,0.6); }"
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
