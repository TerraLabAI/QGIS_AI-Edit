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
from qgis.PyQt.QtWidgets import (
    QApplication,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

BRAND_BLUE = "#1e88e5"

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
    # setIndent(0) on both: a styled QLabel otherwise picks up a small
    # automatic text indent on the bold title only, leaving it a few px
    # right of the subtitle below it.
    title_lbl = make_section_header(title)
    title_lbl.setIndent(0)
    col.addWidget(title_lbl)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setWordWrap(True)
        sub.setIndent(0)
        sub.setStyleSheet(
            "font-size: 11px; color: palette(text);"
            " background: transparent; border: none; padding: 0px;"
        )
        col.addWidget(sub)
    return bar


def build_info_box(text: str) -> QLabel:
    """Blue-tinted info box used as a panel-footer hint. Mirrors the
    "Info Box" pattern in PLUGIN_DESIGN_SYSTEM.md (blue 8% bg, blue 20%
    border) — used by Vectorize/Markup to surface the tool description
    at the bottom of the panel instead of above the controls."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(
        "QLabel {"
        " background-color: rgba(30, 136, 229, 0.08);"
        " border: 1px solid rgba(30, 136, 229, 0.2);"
        " border-radius: 4px;"
        " padding: 8px;"
        " font-size: 11px;"
        " color: palette(text);"
        "}"
    )
    return label


def panel_section_label(text: str) -> QLabel:
    """Small bold section label (Tool / Color / Detection / etc.), normal case."""
    label = QLabel(text)
    label.setStyleSheet(
        "font-size: 10px; font-weight: bold; color: palette(text);"
        " background: transparent; border: none;"
        " border-bottom: 1px solid rgba(128, 128, 128, 0.35);"
        " padding: 6px 0px 2px 0px; margin-bottom: 4px;"
    )
    return label


# Native-feeling group box: subtle border, title sitting on the frame.
# Shared between Markup / Vectorize / Swipe so the three tool panels feel
# like one coherent surface instead of three different visual styles.
GROUP_BOX_QSS = (
    "QGroupBox {"
    " font-weight: bold; font-size: 11px;"
    " color: palette(text);"
    " border: 1px solid rgba(128, 128, 128, 0.30);"
    " border-radius: 6px;"
    " margin-top: 10px;"
    " padding: 10px 8px 8px 8px;"
    "}"
    "QGroupBox::title {"
    " subcontrol-origin: margin;"
    " subcontrol-position: top left;"
    " padding: 0 4px;"
    " left: 8px;"
    " background-color: palette(window);"
    "}"
)


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


def screen_device_pixel_ratio() -> float:
    """Device pixel ratio of the primary screen (2.0 on Retina, 1.25/1.5/2.0
    on scaled Windows). Falls back to 1.0 before a screen exists."""
    app = QApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        if screen is not None:
            ratio = screen.devicePixelRatio()
            if ratio > 0:
                return float(ratio)
    return 1.0


def make_hidpi_pixmap(logical_px: int, dpr: float | None = None) -> QPixmap:
    """Transparent pixmap sized for the current display scale.

    Renders into ``logical_px * dpr`` physical pixels and tags the pixmap
    with its dpr, so a QPainter draws in logical coordinates while the
    output stays crisp at any scale (Retina, Windows 125/150/200%). Without
    this, a fixed-size pixmap gets stretched by Qt and looks pixelated.
    """
    if dpr is None:
        dpr = screen_device_pixel_ratio()
    physical = max(1, round(logical_px * dpr))
    pm = QPixmap(physical, physical)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    return pm


def make_color_dot_icon(
    color: QColor,
    selected: bool,
    is_dark: bool,
    dot_px: int = 22,
) -> QIcon:
    """Solid circle icon used as a color-picker swatch."""
    s = dot_px
    pm = make_hidpi_pixmap(s)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    center = QPointF(s / 2, s / 2)
    if selected:
        ring = QPen(QColor("#1f1f1f") if is_dark else QColor("#ffffff"))
        ring.setWidthF(1.5)
        p.setPen(ring)
        p.setBrush(QBrush(color))
        p.drawEllipse(center, s * 0.42, s * 0.42)
        halo = QPen(QColor(BRAND_BLUE))
        halo.setWidthF(1.25)
        p.setPen(halo)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(center, s * 0.48, s * 0.48)
    else:
        edge = QColor(0, 0, 0, 60)
        p.setPen(QPen(edge, 0.6))
        p.setBrush(QBrush(color))
        p.drawEllipse(center, s * 0.42, s * 0.42)
    p.end()
    return QIcon(pm)


def make_custom_color_icon(is_dark: bool, dot_px: int = 22) -> QIcon:
    """Plus-icon swatch that opens the system color dialog."""
    s = dot_px
    pm = make_hidpi_pixmap(s)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    center = QPointF(s / 2, s / 2)
    bg = QColor(128, 128, 128, 50)
    p.setBrush(QBrush(bg))
    p.setPen(QPen(QColor(128, 128, 128, 110), 0.6))
    p.drawEllipse(center, s * 0.42, s * 0.42)
    ink = QColor("#bbbbbb" if is_dark else "#555555")
    pen = QPen(ink)
    pen.setWidthF(1.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx = s / 2
    arm = s * 0.27
    p.drawLine(QPointF(cx - arm, cx), QPointF(cx + arm, cx))
    p.drawLine(QPointF(cx, cx - arm), QPointF(cx, cx + arm))
    p.end()
    return QIcon(pm)
