"""Shared colors and button stylesheets for the Vectorize panel."""
from __future__ import annotations

# Design-system tokens from the dock single source (one-way import, dock.style has no back-edge).
from ...dock.style import (  # noqa: F401
    _BTN_GREEN,
    BRAND_BLUE,
    BRAND_BLUE_HOVER,
    BRAND_DISABLED,
    DISABLED_TEXT,
    ERROR_TEXT,
    SUCCESS_TEXT,
)

# Muted text link (canonical design-system constant, mirrored from the AI
# Segmentation socle): the quiet escape hatch under a filled primary.
_BTN_LINK_MUTED_QSS = (
    "QPushButton { background: transparent; border: none;"
    " color: rgba(128, 128, 128, 0.9); font-size: 11px; padding: 4px 8px; }"
    f"QPushButton:hover {{ color: {ERROR_TEXT}; text-decoration: underline; }}"
)

_BTN_BLUE_QSS = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 6px 12px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_GHOST_QSS = (
    "QPushButton { background-color: transparent; color: palette(text);"
    " padding: 8px 16px; border-radius: 4px;"
    " border: 1px solid rgba(128, 128, 128, 0.35); }"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.15);"
    " border: 1px solid rgba(128, 128, 128, 0.5); }"
    f"QPushButton:disabled {{ background-color: rgba(128, 128, 128, 0.08);"
    f" border: 1px solid rgba(128, 128, 128, 0.15); color: {DISABLED_TEXT}; }}"
)
