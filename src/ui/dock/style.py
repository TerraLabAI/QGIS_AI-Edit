from __future__ import annotations

import os

from qgis.PyQt.QtCore import QSize, QUrl
from qgis.PyQt.QtGui import QColor, QIcon, QPainter

from . import design_tokens as tokens
from .design_tokens import (  # noqa: F401
    ACCENT,
    ACCENT_BORDER,
    ACCENT_BORDER_SOFT,
    ACCENT_DARK,
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


__all__ = [
    "_BTN_BLUE",
    "_BTN_BLUE_OUTLINE",
    "_BTN_GHOST",
    "_BTN_GRAY",
    "_BTN_GREEN",
    "_BTN_RED",
    "_tinted_svg_icon",
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
    "COPY_BTN_QSS",
    "DISABLED_TEXT",
    "DOCK_BRANDING_URL",
    "FAVORITE_STAR_COLOR",
    "FOCUS_RING",
    "FOCUS_RING_ON_FILL",
    "ICONS_DIR",
    "STAR_FILLED_SVG",
    "STAR_OUTLINE_SVG",
    "svg_url",
]






BTN_GREEN = tokens.ACCENT
BRAND_GREEN = tokens.BRAND_GREEN
BRAND_GREEN_TEXT = "#4d7c0f"
BRAND_BLUE = tokens.BRAND_BLUE
BRAND_BLUE_HOVER = tokens.BRAND_BLUE_HOVER
BRAND_RED = "#d32f2f"
BRAND_RED_HOVER = "#b71c1c"
BRAND_GRAY = "#757575"
BRAND_GRAY_HOVER = "#616161"
BRAND_DISABLED = "#b0bec5"



FOCUS_RING = BRAND_BLUE
FOCUS_RING_ON_FILL = "#ffffff"
DISABLED_TEXT = "#666666"


FAVORITE_STAR_COLOR = "#e57373"



DOCK_BRANDING_URL = "https://terra-lab.ai/ai-edit"





_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ICONS_DIR = os.path.join(_PLUGIN_ROOT, "resources", "icons")
STAR_OUTLINE_SVG = os.path.join(ICONS_DIR, "star.svg")
STAR_FILLED_SVG = os.path.join(ICONS_DIR, "star-filled.svg")


def svg_url(path: str) -> str:

    return QUrl.fromLocalFile(path).toString()




COPY_BTN_QSS = (
    "QPushButton { background: transparent; border: 1px solid transparent;"
    f" color: {tokens.INK_2}; font-size: {tokens.FONT_HINT}px; font-weight: 500;"
    f" padding: 1px 7px; border-radius: {tokens.RADIUS_CHIP}px; }}"
    f"QPushButton:hover {{ background: {tokens.HOVER}; color: {tokens.INK}; }}"
    f"QPushButton:focus {{ border-color: {FOCUS_RING}; color: {tokens.INK}; }}"
)


_BTN_GREEN = tokens.BTN_PRIMARY_QSS




_BTN_BLUE = (
    f"QPushButton {{ background: {BRAND_BLUE}; color: #000000;"
    f" border: none; border-radius: {tokens.RADIUS_PILL}px; padding: 0 16px;"
    f" min-height: {tokens.BTN_PX}px; font-size: {tokens.FONT_BODY}px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:pressed {{ background: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:focus {{ border: 2px solid {FOCUS_RING_ON_FILL}; padding: 0 14px; }}"
    f"QPushButton:disabled {{ background: {BRAND_DISABLED}; color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE_OUTLINE = tokens.BTN_GHOST_QSS
_BTN_GRAY = tokens.BTN_GHOST_QSS

_BTN_RED = tokens.BTN_DANGER_GHOST_QSS
_BTN_GHOST = tokens.BTN_GHOST_QSS


def _tinted_svg_icon(filename: str, ink: QColor) -> QIcon:









    path = os.path.join(ICONS_DIR, filename)




    pm = QIcon(path).pixmap(QSize(40, 40))
    p = QPainter(pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), ink)
    p.end()
    return QIcon(pm)
