from __future__ import annotations

import os

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QIcon, QPainter

# ---------------------------------------------------------------------------
# Brand colors (Material Design 2 - shared with AI Segmentation)
# ---------------------------------------------------------------------------
# Primary CTA buttons (Generate / Regenerate / Launch / Login) keep the
# original material green - it reads as THE action color and stays unchanged.
# Every other green accent uses the QGIS lime below.
BTN_GREEN = "#43a047"
BTN_GREEN_HOVER = "#2e7d32"
BTN_GREEN_DISABLED = "#c8e6c9"

# Brand accent green = the QGIS green (the --qgis-green brand token). Lime
# fills use BRAND_GREEN; green text on light backgrounds uses BRAND_GREEN_TEXT
# (#8bac27 only clears ~2.5:1 on white, the darker tone clears AA).
BRAND_GREEN = "#8bac27"
BRAND_GREEN_TEXT = "#4d7c0f"
BRAND_BLUE = "#1e88e5"
BRAND_BLUE_HOVER = "#1976d2"
BRAND_RED = "#d32f2f"
BRAND_RED_HOVER = "#b71c1c"
BRAND_GRAY = "#757575"
BRAND_GRAY_HOVER = "#616161"
BRAND_DISABLED = "#b0bec5"
DISABLED_TEXT = "#666666"
ERROR_TEXT = "#ef5350"
SUCCESS_TEXT = "#66bb6a"

MAX_PROMPT_CHARS = 2000


TERRALAB_URL = (
    "https://terra-lab.ai/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dock_branding"
)
# SUPPORT_EMAIL is imported from .dialogs.error_report_dialog (single source).

# Design-system QSS constants. border: none kills the native frame on dark themes.
_BTN_GREEN = (
    f"QPushButton {{ background-color: {BTN_GREEN}; color: #000000;"
    f" padding: 8px 16px; border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BTN_GREEN_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BTN_GREEN_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_GREEN_AUTH = (
    f"QPushButton {{ background-color: {BTN_GREEN}; color: #000000;"
    f" border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BTN_GREEN_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 6px 12px; border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE_AUTH = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
)

_BTN_GRAY = (
    f"QPushButton {{ background-color: {BRAND_GRAY}; color: #000000;"
    f" padding: 4px 8px; border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GRAY_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT}; }}"
)

_BTN_DISABLED = (
    f"QPushButton {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT};"
    f" padding: 8px 16px; border: none; border-radius: 4px; }}"
)

_BTN_GHOST = (
    "QPushButton { background-color: transparent; color: palette(text);"
    " padding: 8px 16px; border-radius: 4px;"
    " border: 1px solid rgba(128, 128, 128, 0.35); }"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.15);"
    " border: 1px solid rgba(128, 128, 128, 0.5); }"
    f"QPushButton:disabled {{ background-color: rgba(128, 128, 128, 0.08);"
    f" border: 1px solid rgba(128, 128, 128, 0.15); color: {DISABLED_TEXT}; }}"
)

# Compact filled buttons for the browser-handoff waiting state. Both carry a
# soft tint (never transparent): neutral for "open again", red for "cancel".
_BTN_PAIR_NEUTRAL = (
    "QPushButton { background-color: rgba(128,128,128,0.16); color: palette(text);"
    " border: none; border-radius: 4px; }"
    "QPushButton:hover { background-color: rgba(128,128,128,0.28); }"
)
_BTN_PAIR_CANCEL = (
    f"QPushButton {{ background-color: rgba(211,47,47,0.12); color: {BRAND_RED};"
    f" border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: rgba(211,47,47,0.22); }}"
)

# Shared height for the prompt-row chips so text-only and icon chips align.
_CHIP_HEIGHT = 30

# Footer icon buttons (swipe / vectorize / gear / question mark).
# Hover, active and disabled states are all driven by the dynamic
# ``hover`` / ``active`` properties + Qt's :checked pseudo-state. The
# TerraLab leaf-green tint marks "you are inside this tool" so the user
# always knows which AI Edit action owns the canvas. Two states exist for
# the same reason: ``[active]`` lets us light buttons that drive modal
# dialogs / menus (where Qt's :checked would auto-toggle on click), while
# :checked fits the genuine toggle (swipe).
_FOOTER_ICON_BTN_STYLE = (
    "QToolButton { background: transparent; border: none; padding: 6px 10px;"
    " font-size: 22px; font-weight: 600;"
    " color: palette(text); border-radius: 4px; }"
    'QToolButton[hover="true"] { background: rgba(128,128,128,0.15); }'
    'QToolButton[active="true"] { background: rgba(139, 172, 39, 0.55); }'
    'QToolButton[active="true"][hover="true"] { background: rgba(139, 172, 39, 0.75); }'
    "QToolButton:checked { background: rgba(139, 172, 39, 0.55); }"
    "QToolButton:checked:hover { background: rgba(139, 172, 39, 0.75); }"
    "QToolButton:disabled { color: rgba(128, 128, 128, 0.4); }"
    "QToolButton::menu-indicator { image: none; width: 0; }"
)
_FOOTER_ICON_TOGGLE_STYLE = _FOOTER_ICON_BTN_STYLE  # backward-compat alias

_FOOTER_MENU_STYLE = (
    "QMenu { background: palette(base); border: 1px solid rgba(128,128,128,0.35);"
    " border-radius: 6px; padding: 4px; }"
    "QMenu::item { background: transparent; padding: 6px 14px; border-radius: 4px;"
    " color: palette(text); }"
    "QMenu::item:selected { background: rgba(128,128,128,0.18); }"
)

_INSTRUCTION_BOX = (
    "QLabel {"
    "  background-color: rgba(128, 128, 128, 0.12);"
    "  border: 1px solid rgba(128, 128, 128, 0.25);"
    "  border-radius: 4px;"
    "  padding: 8px;"
    "  font-size: 12px;"
    "  color: palette(text);"
    "}"
)


def _tinted_svg_icon(filename: str, ink: QColor) -> QIcon:
    """Render a bundled footer SVG and recolour every opaque pixel to ``ink``.

    The gear and help glyphs are palette-text-coloured button text, so on a
    dark theme they read as bright near-white. The SVG icons (attach image,
    Before/after) ship with a fixed mid-grey stroke, which next to them looks
    dim - almost transparent. Tinting them to the same palette text colour
    (SourceIn keeps the glyph shape, swaps the colour) lines their weight up
    with the rest and keeps them legible on light themes too.
    """
    plugin_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    path = os.path.join(plugin_root, "resources", "icons", filename)
    # Let QIcon's SVG engine rasterise at 2x for a crisp 20px button, then
    # recolour the pixmap IN PLACE. Copying it into a fresh QPixmap dropped
    # the device-pixel-ratio QIcon had baked in, which shrank the glyph to a
    # quarter of its size on Retina displays.
    pm = QIcon(path).pixmap(QSize(40, 40))
    p = QPainter(pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), ink)
    p.end()
    return QIcon(pm)


def _picture_plus_icon(ink: QColor) -> QIcon:
    """Reference-image glyph with a small accent ``+`` badge in the corner, so
    it reads as 'add an image' at a glance (the Krea-style affordance). The
    image stroke is tinted to ``ink`` to match the other footer glyphs; the
    badge uses the brand green so the 'add' intent pops on both themes.
    """
    from qgis.PyQt.QtCore import QPointF, Qt
    from qgis.PyQt.QtGui import QBrush, QPainter, QPen

    pm = QIcon(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "resources", "icons", "image.svg",
        )
    ).pixmap(QSize(40, 40))
    # Tint the image glyph to palette text weight.
    p = QPainter(pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), ink)
    p.end()
    # Paint the green "+" badge on top, in the upper-right corner.
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cx, cy, r = 30.0, 10.0, 9.0
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#8BAC27")))
    p.drawEllipse(QPointF(cx, cy), r, r)
    pen = QPen(QColor("#14210A"))
    pen.setWidthF(2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(cx - 4, cy), QPointF(cx + 4, cy))
    p.drawLine(QPointF(cx, cy - 4), QPointF(cx, cy + 4))
    p.end()
    return QIcon(pm)


def _pencil_icon(ink: QColor) -> QIcon:
    """Tilted pencil outline used by both the prompt-row chip and the
    bottom-bar Mark up button. Drawn at 2x for crisp rendering at 20px.
    """
    from qgis.PyQt.QtCore import QPointF, Qt
    from qgis.PyQt.QtGui import QPainter, QPen, QPixmap, QPolygonF
    size = 40
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(ink)
    pen.setWidthF(2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    body = QPolygonF([
        QPointF(29, 7),
        QPointF(34, 12),
        QPointF(14, 32),
        QPointF(7, 34),
        QPointF(9, 27),
    ])
    p.drawPolygon(body)
    p.drawLine(QPointF(24, 12), QPointF(29, 17))
    p.end()
    return QIcon(pm)
