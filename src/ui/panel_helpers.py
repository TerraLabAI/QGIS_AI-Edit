"""Pure-function helpers shared by the dock widget and tool panels.

Used by AIEditDockWidget, MarkupPanel, and VectorizePanel. Helpers take
their dependencies as arguments so they have no implicit ties to any
widget state.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from qgis.PyQt.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

BRAND_BLUE = "#1976d2"

SECTION_HEADER_QSS = (
    "font-weight: bold; font-size: 12px; color: palette(text);"
    " margin: 0px; padding: 0px 0px 2px 0px;"
)

SECTION_HEADER_EXTRA_TOP_QSS = (
    "font-weight: bold; font-size: 12px; color: palette(text); padding-top: 6px;"
)


def make_section_header(text: str, extra_top: bool = False) -> QLabel:
    """Section header label (bold, 12px, palette-aware)."""
    label = QLabel(text)
    label.setStyleSheet(SECTION_HEADER_EXTRA_TOP_QSS if extra_top else SECTION_HEADER_QSS)
    label.setContentsMargins(0, 0, 0, 0)
    return label


def build_panel_header(title: str, subtitle: str | None = None) -> QWidget:
    """Tool-panel header: bold title plus an optional one-line subtitle."""
    bar = QWidget()
    col = QVBoxLayout(bar)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(2)
    col.addWidget(make_section_header(title))
    if subtitle:
        sub = QLabel(subtitle)
        sub.setWordWrap(True)
        sub.setStyleSheet(
            "font-size: 11px; color: palette(text);"
            " background: transparent; border: none; padding: 0px;"
        )
        col.addWidget(sub)
    return bar


def panel_section_label(text: str) -> QLabel:
    """Small-caps section label (TOOL / COLOR / DETECTION / etc.)."""
    label = QLabel(text.upper())
    label.setStyleSheet(
        "font-size: 10px; font-weight: bold; color: palette(text);"
        " background: transparent; border: none;"
        " border-bottom: 1px solid rgba(128, 128, 128, 0.35);"
        " padding: 6px 0px 2px 0px; margin-bottom: 4px;"
        " letter-spacing: 1px;"
    )
    return label


def apply_swatch_style(button: QPushButton, color: QColor) -> None:
    """Paint a square button with a solid color swatch."""
    button.setStyleSheet(
        "QPushButton {"
        f" background-color: rgb({color.red()}, {color.green()}, {color.blue()});"
        " border: 1px solid rgba(128, 128, 128, 0.5);"
        " border-radius: 4px;"
        "}"
        "QPushButton:hover { border: 1px solid rgba(128, 128, 128, 0.85); }"
    )


def is_dark_palette(widget: QWidget) -> bool:
    """Return True when the widget's window color reads as dark."""
    c = widget.palette().color(QPalette.ColorRole.Window)
    return (c.red() + c.green() + c.blue()) / 3 < 128


def make_color_dot_icon(
    color: QColor,
    selected: bool,
    is_dark: bool,
    dot_px: int = 22,
) -> QIcon:
    """Solid circle icon used as a color-picker swatch."""
    size = dot_px * 2  # 2x for crisp rendering
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if selected:
        ring = QPen(QColor("#1f1f1f") if is_dark else QColor("#ffffff"))
        ring.setWidthF(3)
        p.setPen(ring)
        p.setBrush(QBrush(color))
        p.drawEllipse(QPointF(size / 2, size / 2), size * 0.42, size * 0.42)
        halo = QPen(QColor(BRAND_BLUE))
        halo.setWidthF(2.5)
        p.setPen(halo)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(size / 2, size / 2), size * 0.48, size * 0.48)
    else:
        edge = QColor(0, 0, 0, 60)
        p.setPen(QPen(edge, 1.2))
        p.setBrush(QBrush(color))
        p.drawEllipse(QPointF(size / 2, size / 2), size * 0.42, size * 0.42)
    p.end()
    return QIcon(pm)


def make_custom_color_icon(is_dark: bool, dot_px: int = 22) -> QIcon:
    """Plus-icon swatch that opens the system color dialog."""
    size = dot_px * 2
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    bg = QColor(128, 128, 128, 50)
    p.setBrush(QBrush(bg))
    p.setPen(QPen(QColor(128, 128, 128, 110), 1.2))
    p.drawEllipse(QPointF(size / 2, size / 2), size * 0.42, size * 0.42)
    ink = QColor("#bbbbbb" if is_dark else "#555555")
    pen = QPen(ink)
    pen.setWidthF(2.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx = size / 2
    p.drawLine(QPointF(cx - 6, cx), QPointF(cx + 6, cx))
    p.drawLine(QPointF(cx, cx - 6), QPointF(cx, cx + 6))
    p.end()
    return QIcon(pm)
