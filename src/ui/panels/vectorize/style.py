"""Shared colors and button stylesheets for the Vectorize panel."""
from __future__ import annotations

# Design-system tokens from the dock single source (one-way import,
# dock.style has no back-edge). Re-exported for panel.py.
from ...dock.style import _BTN_GHOST as _BTN_GHOST_QSS  # noqa: F401
from ...dock.style import _BTN_GREEN, _BTN_LABEL_WEIGHT, ERROR_TEXT, SUCCESS_TEXT  # noqa: F401

# Muted text link (canonical design-system constant, mirrored from the AI
# Segmentation socle): the quiet escape hatch under a filled primary.
_BTN_LINK_MUTED_QSS = (
    "QPushButton { background: transparent; border: none;"
    f" color: rgba(128, 128, 128, 0.9); font-size: 11px; padding: 4px 8px;"
    f" {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ color: {ERROR_TEXT}; text-decoration: underline; }}"
)
