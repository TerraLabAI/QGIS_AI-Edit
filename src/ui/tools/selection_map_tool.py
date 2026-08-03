from __future__ import annotations

import os

from qgis.core import QgsPointXY
from qgis.gui import QgsMapCanvasItem
from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)

from ...core.config_store import get_export_dial_seq
from ...core.i18n import tr
from ..dock.style import BRAND_BLUE

# Aspect ratios the export/generation pipeline supports. The polygon zone
# tool (polygon_selection_tool.py) expands a drawn shape's bounding box to
# the nearest one of these on close; this used to also be the rectangle
# tool's live drag-snap target before the polygon tool replaced it.
SUPPORTED_RATIOS = [
    (1, 1),
    (5, 4),
    (4, 5),
    (4, 3),
    (3, 4),
    (3, 2),
    (2, 3),
    (16, 9),
    (9, 16),
    (21, 9),
]

_MAX_RATIO_SIDE = 100


def supported_ratios() -> list[tuple[int, int]]:
    """The shipped ratios plus any the server adds at ``zone.ratios``, served
    as "w:h" strings. Additive union only: a deploy adds a ratio, it never
    removes a shipped one. Read at snap time, so a config arriving mid-session
    applies to the next zone."""
    base = [f"{w}:{h}" for w, h in SUPPORTED_RATIOS]
    ratios: list[tuple[int, int]] = []
    for entry in get_export_dial_seq("zone.ratios", base, max_len=24):
        parts = entry.split(":")
        if len(parts) != 2 or not all(p.strip().isdecimal() for p in parts):
            continue
        width, height = int(parts[0]), int(parts[1])
        if 0 < width < _MAX_RATIO_SIDE and 0 < height < _MAX_RATIO_SIDE:
            ratios.append((width, height))
    return ratios or list(SUPPORTED_RATIOS)


class _ZoneDeleteBadge(QgsMapCanvasItem):
    """Floating × badge anchored to a map point.

    Lives in the canvas's QGraphicsScene so it follows the canvas during
    pan/zoom (the scene gets pixel-shifted during pan, so anything in it
    stays aligned with the rubber band - a plain widget parented to the
    viewport would not). Click handling is done by the parent map tool's
    canvasPressEvent via :meth:`hit_test`, because an active map tool eats
    mouse events before the scene items see them.
    """

    RADIUS = 12
    _BRAND_BLUE = QColor(BRAND_BLUE)
    _DISABLED_BG = QColor(30, 136, 229, 115)

    def __init__(self, canvas):
        super().__init__(canvas)
        self._anchor: QgsPointXY | None = None
        self._enabled = True
        self.setZValue(10000)

    def set_anchor(self, point: QgsPointXY) -> None:
        self._anchor = point
        self.updatePosition()
        self.update()

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled == enabled:
            return
        self._enabled = enabled
        if enabled:
            self.setToolTip(tr("Clear this zone"))
        else:
            self.setToolTip(tr("Cancel the running generation first (close the dock)"))
        self.update()

    def hit_test(self, canvas_pt) -> bool:
        """True when a canvas-pixel point lands inside the badge circle."""
        if self._anchor is None or not self.isVisible():
            return False
        center = self.toCanvasCoordinates(self._anchor)
        dx = canvas_pt.x() - center.x()
        dy = canvas_pt.y() - center.y()
        return (dx * dx + dy * dy) <= (self.RADIUS * self.RADIUS)

    def updatePosition(self) -> None:  # noqa: N802 (Qt API)
        if self._anchor is None:
            return
        self.setPos(self.toCanvasCoordinates(self._anchor))

    def boundingRect(self):  # noqa: N802 (Qt API)
        r = self.RADIUS + 2
        return QRectF(-r, -r, 2 * r, 2 * r)

    def paint(self, painter, option, widget):
        if self._anchor is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(self._BRAND_BLUE if self._enabled else self._DISABLED_BG)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(0, 0), self.RADIUS, self.RADIUS)
        line_color = (
            QColor(255, 255, 255) if self._enabled else QColor(255, 255, 255, 153)
        )
        pen = QPen(line_color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        d = self.RADIUS * 0.45
        painter.drawLine(QPointF(-d, -d), QPointF(d, d))
        painter.drawLine(QPointF(-d, d), QPointF(d, -d))


def _tinted_pixmap(filename: str, color: QColor, size: int = 28) -> QPixmap:
    """Rasterise a bundled SVG and recolour every opaque pixel to ``color``.

    Mirrors the dock's footer-icon tinting so the canvas badges carry the
    same glyphs. Rendered at 2x and downscaled at paint time for crispness.
    """
    plugin_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    )
    path = os.path.join(plugin_root, "resources", "icons", filename)
    pm = QIcon(path).pixmap(QSize(size, size))
    p = QPainter(pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), color)
    p.end()
    return pm


def _polygon_glyph_pixmap(color: QColor, size: int = 28) -> QPixmap:
    """Square-in-square Vectorize glyph, painted in ``color``.

    Same shape as the dock footer Vectorize button so the two entry points
    read as the same action.
    """
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(2.2)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    m = size * 0.16
    p.drawRect(QRectF(m, m, size - 2 * m, size - 2 * m))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    inner = size * 0.30
    p.drawRect(QRectF((size - inner) / 2, (size - inner) / 2, inner, inner))
    p.end()
    return pm


class _ZoneActionBadge(QgsMapCanvasItem):
    """Floating brand-green action pill anchored beside the delete badge.

    Same scene-item approach as :class:`_ZoneDeleteBadge` so the pill tracks
    the rubber band during pan/zoom. Carries an icon glyph + a text label.
    Click is forwarded by the parent map tool's ``canvasPressEvent`` via
    :meth:`hit_test`, because an active map tool eats mouse events before the
    scene items see them.

    Positioned by a horizontal pixel offset from the zone's top-right corner:
    the tool measures each visible pill and assigns the offset of its centre,
    so the pills line up to the left of the × badge.
    """

    HEIGHT = 22
    _PAD_X = 9
    _ICON = 14
    _ICON_GAP = 5
    # Resting pills stay muted/translucent so they sit lightly on the imagery;
    # the active (comparing) pill turns solid vivid blue with a crisp white
    # ring so the on/off state reads at a glance. Blue matches the × badge.
    _BLUE_ACTIVE = QColor(30, 136, 229, 255)
    _BLUE_RESTING = QColor(30, 136, 229, 140)
    _WHITE = QColor(255, 255, 255)
    _OUTLINE = QColor(255, 255, 255, 235)
    _SHADOW = QColor(0, 0, 0, 90)

    def __init__(self, canvas, kind: str, label: str):
        super().__init__(canvas)
        self._anchor: QgsPointXY | None = None
        self._offset_px = 0.0
        self._kind = kind  # "compare" | "vectorize"
        self._label = label
        self._active = False
        self.setZValue(10000)
        self._font = QFont()
        self._font.setPixelSize(11)
        self._font.setBold(True)
        self._recompute_width()
        if kind == "compare":
            self._glyph = _tinted_pixmap("swipe.svg", self._WHITE)
        else:
            self._glyph = _polygon_glyph_pixmap(self._WHITE)

    def _recompute_width(self) -> None:
        fm = QFontMetrics(self._font)
        text_w = fm.horizontalAdvance(self._label)
        self._width = self._PAD_X + self._ICON + self._ICON_GAP + text_w + self._PAD_X

    @property
    def width(self) -> float:
        return self._width

    def set_anchor(self, point: QgsPointXY) -> None:
        self._anchor = point
        self.updatePosition()
        self.update()

    def set_offset(self, offset_px: float) -> None:
        self._offset_px = offset_px
        self.updatePosition()
        self.update()

    def set_active(self, active: bool) -> None:
        """Toggle the pressed/on look (used while Compare is live)."""
        if self._active == active:
            return
        self._active = active
        self.update()

    def hit_test(self, canvas_pt) -> bool:
        """True when a canvas-pixel point lands inside the pill rectangle."""
        if self._anchor is None or not self.isVisible():
            return False
        center = self.toCanvasCoordinates(self._anchor)
        cx = center.x() - self._offset_px
        cy = center.y()
        return abs(canvas_pt.x() - cx) <= self._width / 2.0 and abs(canvas_pt.y() - cy) <= self.HEIGHT / 2.0

    def updatePosition(self) -> None:  # noqa: N802 (Qt API)
        if self._anchor is None:
            return
        center = self.toCanvasCoordinates(self._anchor)
        self.setPos(QPointF(center.x() - self._offset_px, center.y()))

    def boundingRect(self):  # noqa: N802 (Qt API)
        w, h = self._width, self.HEIGHT
        return QRectF(-w / 2 - 2, -h / 2 - 2, w + 4, h + 4)

    def paint(self, painter, option, widget):
        if self._anchor is None:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        w, h = self._width, self.HEIGHT
        rect = QRectF(-w / 2, -h / 2, w, h)
        radius = h / 2.0
        # Soft shadow so the pill stays legible over any imagery.
        shadow = QRectF(rect)
        shadow.translate(0, 1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._SHADOW)
        painter.drawRoundedRect(shadow, radius, radius)
        # Pill body: muted when idle, solid + ring when comparing.
        painter.setBrush(self._BLUE_ACTIVE if self._active else self._BLUE_RESTING)
        painter.drawRoundedRect(rect, radius, radius)
        if self._active:
            ring = QPen(self._OUTLINE)
            ring.setWidthF(1.5)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)
        # Glyph (downscaled from the 2x pixmap).
        icon_x = -w / 2 + self._PAD_X
        painter.drawPixmap(
            QRectF(icon_x, -self._ICON / 2, self._ICON, self._ICON),
            self._glyph,
            QRectF(self._glyph.rect()),
        )
        # Label.
        text_x = icon_x + self._ICON + self._ICON_GAP
        painter.setFont(self._font)
        painter.setPen(QPen(self._WHITE))
        painter.drawText(
            QRectF(text_x, -h / 2, w / 2 - text_x, h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
