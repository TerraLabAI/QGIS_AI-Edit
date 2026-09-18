from __future__ import annotations

import os

from qgis.PyQt.QtCore import QSize, QUrl
from qgis.PyQt.QtGui import QColor, QIcon, QPainter

from ...core.auth.activation_manager import build_utm_url
from . import design_tokens as tokens
from .design_tokens import (  # noqa: F401 - re-exported: style.py is the style home
    ACCENT,
    ACCENT_BORDER,
    ACCENT_BORDER_SOFT,
    ACCENT_DARK,
    ACCENT_INK,
    ACCENT_TINT,
    ACCENT_TINT_ON,
    BODY_QSS,
    BTN_DANGER_GHOST_QSS,
    BTN_GHOST_QSS,
    BTN_ICON_QSS,
    BTN_LINK_QSS,
    BTN_PRIMARY_QSS,
    BTN_PRIMARY_WIDE_QSS,
    BTN_PX,
    BTN_QUIET_QSS,
    CARD_QSS,
    FIELD,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    HINT_QSS,
    HOVER,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    INPUT_QSS,
    LINE,
    LINE_STRONG,
    LINK_INK,
    MENU_QSS,
    MICRO_QSS,
    ON_ACCENT,
    PROGRESS_QSS,
    RADIUS_CARD,
    RADIUS_CHIP,
    RADIUS_CONTROL,
    RADIUS_PANEL,
    RADIUS_PILL,
    RADIUS_PILL_WIDE,
    SCROLL_AREA_QSS,
    SURFACE,
    TITLE_QSS,
    qcolor,
    repolish_widget,
)

# Private names are listed too: other modules import them from here.
__all__ = [
    "_BTN_BLUE",
    "_BTN_BLUE_AUTH",
    "_BTN_BLUE_OUTLINE",
    "_BTN_DISABLED",
    "_BTN_GHOST",
    "_BTN_GRAY",
    "_BTN_GREEN",
    "_BTN_GREEN_AUTH",
    "_BTN_GREEN_OUTLINE",
    "_BTN_LABEL_WEIGHT",
    "_BTN_PAIR_CANCEL",
    "_BTN_PAIR_NEUTRAL",
    "_BTN_RED",
    "_btn_start_qss",
    "_CHIP_HEIGHT",
    "_FOOTER_ICON_BTN_STYLE",
    "_FOOTER_MENU_STYLE",
    "_INSTRUCTION_BOX",
    "_pencil_icon",
    "_picture_plus_icon",
    "_tinted_svg_icon",
    "BOOK_SVG",
    "BRAND_BLUE",
    "BRAND_BLUE_HOVER",
    "BRAND_DISABLED",
    "BRAND_GRAY",
    "BRAND_GRAY_HOVER",
    "BRAND_GREEN",
    "BRAND_GREEN_TEXT",
    "BRAND_RED",
    "BRAND_RED_HOVER",
    "BTN_GREEN",
    "BTN_GREEN_DISABLED",
    "BTN_GREEN_HOVER",
    "COPY_BTN_QSS",
    "COPY_SVG",
    "DISABLED_TEXT",
    "DOCK_BRANDING_URL",
    "ERROR_TEXT",
    "FAVORITE_STAR_COLOR",
    "FOCUS_RING",
    "FOCUS_RING_ON_FILL",
    "ICONS_DIR",
    "MAX_PROMPT_CHARS",
    "STAR_FILLED_SVG",
    "STAR_OUTLINE_SVG",
    "SUCCESS_TEXT",
    "svg_url",
]

# ---------------------------------------------------------------------------
# Brand colours under their historical names (Material, shared with AI
# Segmentation). The shapes around them follow the AI Agent line since
# 2026-09-17 (design_tokens.py); new code imports the design_tokens names.
# ---------------------------------------------------------------------------
BTN_GREEN = tokens.ACCENT
BTN_GREEN_HOVER = tokens.ACCENT_DARK
BTN_GREEN_DISABLED = tokens.PRIMARY_DISABLED_FILL

BRAND_GREEN = tokens.BRAND_GREEN
BRAND_GREEN_TEXT = "#4d7c0f"
BRAND_BLUE = tokens.BRAND_BLUE
BRAND_BLUE_HOVER = tokens.BRAND_BLUE_HOVER
BRAND_RED = "#d32f2f"
BRAND_RED_HOVER = "#b71c1c"
BRAND_GRAY = "#757575"
BRAND_GRAY_HOVER = "#616161"
BRAND_DISABLED = "#b0bec5"
# Keyboard focus ring: BRAND_BLUE clears 3:1 on white and on the QGIS dark
# greys. On a filled button the ring is white (the only ink that clears 3:1
# against every brand fill).
FOCUS_RING = BRAND_BLUE
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
    "QPushButton { background: transparent; border: 1px solid transparent;"
    f" color: {tokens.INK_2}; font-size: {tokens.FONT_HINT}px; font-weight: 500;"
    f" padding: 1px 7px; border-radius: {tokens.RADIUS_CHIP}px; }}"
    f"QPushButton:hover {{ background: {tokens.HOVER}; color: {tokens.INK}; }}"
    f"QPushButton:focus {{ border-color: {FOCUS_RING}; color: {tokens.INK}; }}"
)

# Weight of every button label. Windows draws the default UI font thinner
# than macOS does, so labels keep a medium-to-semibold weight.
_BTN_LABEL_WEIGHT = "font-weight: 600;"

# The four buttons of the line, under the names the call sites use.
_BTN_GREEN = tokens.BTN_PRIMARY_QSS
_BTN_GREEN_AUTH = tokens.BTN_PRIMARY_WIDE_QSS

# The one button that starts a session: the wide primary already carries the
# larger label.
_BTN_START_FONT_PX = tokens.FONT_BASE


def _btn_start_qss(base: str) -> str:
    """A primary button constant, with the label size a screen's Start carries."""
    return base + f"QPushButton {{ font-size: {_BTN_START_FONT_PX}px; }}"


# The blue pill: same shape as the primary, filled in the brand blue, as in
# AI Segmentation. A second emphasis beside the green one, never alone on a
# screen that has a green primary.
_BTN_BLUE = (
    f"QPushButton {{ background: {BRAND_BLUE}; color: #000000;"
    f" border: none; border-radius: {tokens.RADIUS_PILL}px; padding: 0 16px;"
    f" min-height: {tokens.BTN_PX}px; font-size: {tokens.FONT_BODY}px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:pressed {{ background: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING_ON_FILL}; padding: 0 14px; }}"
    f"QPushButton:disabled {{ background: {BRAND_DISABLED}; color: {DISABLED_TEXT}; }}"
)
_BTN_BLUE_AUTH = _BTN_BLUE
# Outlines were the old secondary; the ghost pill is the secondary now.
_BTN_BLUE_OUTLINE = tokens.BTN_GHOST_QSS
_BTN_GREEN_OUTLINE = tokens.BTN_GHOST_QSS
_BTN_GRAY = tokens.BTN_GHOST_QSS
# No red fill in the line: a destructive confirm is the danger ghost.
_BTN_RED = tokens.BTN_DANGER_GHOST_QSS
# A primary that cannot run yet: grey fill, muted words.
_BTN_DISABLED = (
    f"QPushButton {{ background: {BTN_GREEN_DISABLED}; color: {tokens.PRIMARY_DISABLED_INK};"
    f" border: none; border-radius: {tokens.RADIUS_PILL_WIDE}px; padding: 0 18px;"
    f" min-height: {tokens.BTN_PRIMARY_WIDE_PX}px; font-size: {tokens.FONT_BASE}px; font-weight: 600; }}"
)
_BTN_GHOST = tokens.BTN_GHOST_QSS

# The browser-handoff waiting pair: open again (ghost), cancel (danger ghost).
_BTN_PAIR_NEUTRAL = tokens.BTN_GHOST_QSS
_BTN_PAIR_CANCEL = tokens.BTN_DANGER_GHOST_QSS

# Shared height for the prompt-row chips so text-only and icon chips align.
_CHIP_HEIGHT = 30

# Footer icon buttons (swipe / vectorize / gear / help). Hover, active and
# disabled are driven by the dynamic ``hover`` / ``active`` properties and
# Qt's :checked. The accent tint marks "you are inside this tool".
_FOOTER_ICON_BTN_STYLE = (
    "QToolButton { background: transparent; border: 1px solid transparent; padding: 4px 7px;"
    f" font-size: {tokens.FONT_BODY}px; font-weight: 500;"
    f" color: {tokens.INK_2}; border-radius: {tokens.RADIUS_CONTROL}px; }}"
    f'QToolButton[hover="true"] {{ background: {tokens.HOVER}; color: {tokens.INK}; }}'
    f'QToolButton[active="true"] {{ background: {tokens.ACCENT_TINT_ON}; color: {tokens.INK}; }}'
    f'QToolButton[active="true"][hover="true"] {{ background: {tokens.ACCENT_BORDER_SOFT}; }}'
    f"QToolButton:checked {{ background: {tokens.ACCENT_TINT_ON}; color: {tokens.INK}; }}"
    f"QToolButton:checked:hover {{ background: {tokens.ACCENT_BORDER_SOFT}; }}"
    f"QToolButton:disabled {{ color: {tokens.INK_3}; }}"
    f"QToolButton:focus {{ border-color: {FOCUS_RING}; }}"
    "QToolButton::menu-indicator { image: none; width: 0; }"
)

_FOOTER_MENU_STYLE = tokens.MENU_QSS

_INSTRUCTION_BOX = (
    "QLabel {"
    f"  background-color: {tokens.INSET};"
    f"  border: 1px solid {tokens.LINE};"
    f"  border-radius: {tokens.RADIUS_CONTROL}px;"
    "  padding: 8px 10px;"
    f"  font-size: {tokens.FONT_BODY}px;"
    f"  color: {tokens.INK};"
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
    p.setBrush(QBrush(QColor(tokens.ACCENT)))
    p.drawEllipse(QPointF(cx, cy), r, r)
    pen = QPen(QColor(tokens.ON_ACCENT))
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
