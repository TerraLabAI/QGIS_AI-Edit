from __future__ import annotations

import os

from qgis.PyQt.QtCore import QSize, QUrl
from qgis.PyQt.QtGui import QColor, QIcon, QPainter

from ...core.auth.activation_manager import build_utm_url

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
# Keyboard focus ring. One hue for the whole plugin, and it is BRAND_BLUE
# rather than a colour of its own: measured (WCAG 2.1 relative luminance)
# 3.68:1 on white, 3.43:1 on the #f7f7f7 card fill, and 3.43 / 3.85 / 3.00:1 on
# the #333333 / #2b2b2b / #3c3c3c the QGIS dark themes paint. That is the only
# brand token clearing SC 1.4.11's 3:1 on both themes; Material Blue 800
# (#1565c0) reads 5.75:1 on white but 2.20 / 2.46 / 1.92:1 on those same greys.
# The buttons below set `border: none`, which drops the native Windows focus
# rectangle, so every button constant paints its own ring on :focus. Where the
# resting rule has padding, the :focus rule subtracts the ring width from it,
# so taking focus never resizes the button. Four rules have no resting padding
# to subtract from - _BTN_GREEN_AUTH, _BTN_BLUE_AUTH, _BTN_PAIR_NEUTRAL,
# _BTN_PAIR_CANCEL - and their size hint does grow 4x4 on focus. Nothing moves
# on screen: each of those buttons carries a setMinimumHeight (28 to 38) far
# above the focused hint and takes its width from the layout row it sits in.
FOCUS_RING = BRAND_BLUE
# A ring drawn INSIDE a filled brand button has that fill as its only
# neighbour, and no brand colour clears 3:1 against those fills (BRAND_BLUE
# measures 1.00 on its own fill, 1.11 on BTN_GREEN, 1.25 on BRAND_GRAY). White
# clears all three - 3.68 on BRAND_BLUE, 3.30 on BTN_GREEN, 4.61 on BRAND_GRAY
# - and is achromatic, so it adds no hue. Transparent and tinted buttons keep
# FOCUS_RING: their ring sits on the panel background, not on a fill.
FOCUS_RING_ON_FILL = "#ffffff"
DISABLED_TEXT = "#666666"
ERROR_TEXT = "#ef5350"
SUCCESS_TEXT = "#66bb6a"
# Filled favorite star, same hue as the library's Favorites tab glyph
# (src/ui/dialogs/prompt_templates/common.py).
FAVORITE_STAR_COLOR = "#e57373"

MAX_PROMPT_CHARS = 2000


DOCK_BRANDING_URL = build_utm_url("/ai-edit", "dock_branding")
# SUPPORT_EMAIL is imported from .dialogs.error_report_dialog (single source).

# Bundled icon paths, defined here because this module is the plugin's style
# home: eight modules outside dock/ import it. The widgets that used to compute
# the same paths from their own __file__ depth now alias these.
_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ICONS_DIR = os.path.join(_PLUGIN_ROOT, "resources", "icons")
STAR_OUTLINE_SVG = os.path.join(ICONS_DIR, "star.svg")
STAR_FILLED_SVG = os.path.join(ICONS_DIR, "star-filled.svg")
COPY_SVG = os.path.join(ICONS_DIR, "copy.svg")
# Open book: the same two-pages-on-a-spine shape the footer Tutorial button
# paints, as an SVG, so a sentence can point at that button by showing it.
BOOK_SVG = os.path.join(ICONS_DIR, "book.svg")


def svg_url(path: str) -> str:
    """file:// URL for an inline ``<img>`` in a rich-text QLabel."""
    return QUrl.fromLocalFile(path).toString()


# The flat copy button, shared by the version strip and the generation detail
# dialog. Same widget, same look, so one stylesheet.
COPY_BTN_QSS = (
    "QPushButton { background: transparent; border: none; "
    "color: rgba(128,128,128,0.95); font-size: 11px; font-weight: 600; "
    "padding: 1px 6px; border-radius: 4px; }"
    "QPushButton:hover { background: rgba(128,128,128,0.14); color: palette(text); }"
    f"QPushButton:focus {{ border: 1px solid {FOCUS_RING}; padding: 0px 5px; }}"
)

# Weight of every button label. Windows draws the default UI font much
# thinner than macOS does, and a regular-weight label on a filled button
# was the first thing that stopped being readable there.
_BTN_LABEL_WEIGHT = "font-weight: 600;"

# Design-system QSS constants. border: none kills the native frame on dark themes.
_BTN_GREEN = (
    f"QPushButton {{ background-color: {BTN_GREEN}; color: #000000;"
    f" padding: 8px 16px; border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BTN_GREEN_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BTN_GREEN_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING_ON_FILL};"
    f" padding: 6px 14px; }}"
)

_BTN_GREEN_AUTH = (
    f"QPushButton {{ background-color: {BTN_GREEN}; color: #000000;"
    f" border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BTN_GREEN_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING_ON_FILL}; }}"
)


# The one button that starts a session. It is alone on its screen and it is the
# only thing there is to click, so its label is larger than a button sitting in
# a row with others. Mirrors _btn_start_qss in AI Segmentation.
_BTN_START_FONT_PX = 13


def _btn_start_qss(base: str) -> str:
    """A primary button constant, with the label size a screen's Start carries."""
    return base + f"QPushButton {{ font-size: {_BTN_START_FONT_PX}px; }}"


_BTN_BLUE = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 6px 12px; border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING_ON_FILL};"
    f" padding: 4px 10px; }}"
)

_BTN_BLUE_AUTH = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING_ON_FILL}; }}"
)

# Blue-outline secondary (canonical design-system constant, mirrored from the
# AI Segmentation socle): a prominent secondary sitting on a screen whose one
# filled primary is the green Generate.
_BTN_BLUE_OUTLINE = (
    f"QPushButton {{ background-color: transparent; color: {BRAND_BLUE};"
    f" border: 1px solid {BRAND_BLUE}; border-radius: 4px; font-weight: 600;"
    " padding: 6px 12px; }"
    "QPushButton:hover { background-color: rgba(30, 136, 229, 0.12); }"
    f"QPushButton:disabled {{ color: {DISABLED_TEXT};"
    f" border-color: {DISABLED_TEXT}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING}; padding: 5px 11px; }}"
)

# Leaving a finished session is a positive act, so the button wears the CTA
# green - but as an OUTLINE, one weight below the filled Generate beside it,
# because continuing to iterate stays the primary action and a screen carries
# exactly one filled primary. BTN_GREEN as ink measures 3.30:1 on white and
# 4.28:1 on the #2b2b2b QGIS dark theme, the same band as the blue outline
# above (3.68:1 on white).
_BTN_GREEN_OUTLINE = (
    f"QPushButton {{ background-color: transparent; color: {BTN_GREEN};"
    f" border: 1px solid {BTN_GREEN}; border-radius: 4px; font-weight: 600;"
    " padding: 6px 12px; }"
    "QPushButton:hover { background-color: rgba(67, 160, 71, 0.12); }"
    f"QPushButton:disabled {{ color: {DISABLED_TEXT};"
    f" border-color: {DISABLED_TEXT}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING}; padding: 5px 11px; }}"
)

_BTN_GRAY = (
    f"QPushButton {{ background-color: {BRAND_GRAY}; color: #000000;"
    f" padding: 4px 8px; border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: {BRAND_GRAY_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING_ON_FILL};"
    f" padding: 2px 6px; }}"
)

_BTN_DISABLED = (
    f"QPushButton {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT};"
    f" padding: 8px 16px; border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
)

_BTN_GHOST = (
    "QPushButton { background-color: transparent; color: palette(text);"
    " padding: 8px 16px; border-radius: 4px;"
    f" border: 1px solid rgba(128, 128, 128, 0.35); {_BTN_LABEL_WEIGHT} }}"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.15);"
    " border: 1px solid rgba(128, 128, 128, 0.5); }"
    f"QPushButton:disabled {{ background-color: rgba(128, 128, 128, 0.08);"
    f" border: 1px solid rgba(128, 128, 128, 0.15); color: {DISABLED_TEXT}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING}; padding: 7px 15px; }}"
)

# Compact filled buttons for the browser-handoff waiting state. Both carry a
# soft tint (never transparent): neutral for "open again", red for "cancel".
_BTN_PAIR_NEUTRAL = (
    "QPushButton { background-color: rgba(128,128,128,0.16); color: palette(text);"
    f" border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    "QPushButton:hover { background-color: rgba(128,128,128,0.28); }"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING}; }}"
)
_BTN_PAIR_CANCEL = (
    f"QPushButton {{ background-color: rgba(211,47,47,0.12); color: {BRAND_RED};"
    f" border: none; border-radius: 4px; {_BTN_LABEL_WEIGHT} }}"
    f"QPushButton:hover {{ background-color: rgba(211,47,47,0.22); }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING}; }}"
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
    f"QToolButton:focus {{ border: 2px solid {FOCUS_RING}; padding: 4px 8px; }}"
    "QToolButton::menu-indicator { image: none; width: 0; }"
)

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
    path = os.path.join(ICONS_DIR, filename)
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
        os.path.join(ICONS_DIR, "image.svg")
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
