from __future__ import annotations

import os

from qgis.core import QgsPointXY, QgsRectangle
from qgis.gui import QgsMapCanvasItem, QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from qgis.PyQt.QtWidgets import QMenu

from ...core import qt_compat as QtC
from ...core.i18n import tr

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
    _BRAND_BLUE = QColor("#1e88e5")
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
        fm = QFontMetrics(self._font)
        text_w = fm.horizontalAdvance(label)
        self._width = self._PAD_X + self._ICON + self._ICON_GAP + text_w + self._PAD_X
        self._glyph = (
            _tinted_pixmap("swipe.svg", self._WHITE)
            if kind == "compare"
            else _polygon_glyph_pixmap(self._WHITE)
        )

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


class RectangleSelectionTool(QgsMapTool):
    """Map tool for selecting a rectangular zone on the canvas.

    Stays active after selection so the user can right-click the zone
    to delete it, or draw a new one to replace it.
    """

    selection_made = pyqtSignal(QgsRectangle)
    zone_too_small = pyqtSignal()
    zone_delete_requested = pyqtSignal()
    # Emitted when the drawn zone fails the edge-case checks (antimeridian,
    # polar, area too big, invalid CRS, map rotated). Args: (error_code, message).
    zone_invalid = pyqtSignal(str, str)
    # Post-generation action pills clicked on the canvas (beside the × badge).
    # The plugin routes these to the swipe controller / Vectorize panel.
    compare_requested = pyqtSignal()
    vectorize_requested = pyqtSignal()

    MIN_SIZE_PX = 50

    def __init__(self, canvas):
        super().__init__(canvas)
        self._start_point = None
        self._rubber_band = None
        self._is_drawing = False
        self._has_zone = False
        self._zone_rect = None
        self._locked = False
        self._pending_context_menu = False
        self._is_panning = False
        self._refresh_cursor()

        # Badge anchored to the rubber band's top-right corner. Lives in the
        # canvas scene so it follows the rectangle during pan/zoom. Click is
        # forwarded by canvasPressEvent because the active map tool gets the
        # event before scene items would.
        self._delete_badge: _ZoneDeleteBadge | None = None
        # Post-generation action pills (Compare / Vectorize) shown to the left
        # of the delete badge. Created lazily, armed by the plugin once a
        # generation completes, hidden whenever the result becomes stale.
        self._compare_badge: _ZoneActionBadge | None = None
        self._vectorize_badge: _ZoneActionBadge | None = None

    def activate(self):
        super().activate()
        self._refresh_cursor()

    def _refresh_cursor(self) -> None:
        """Crosshair while no zone is selected (draw mode), open hand once
        a zone exists so left-drag pans the map like QGIS's native pan tool.
        """
        shape = Qt.CursorShape.OpenHandCursor if self._has_zone else QtC.CrossCursor
        self.setCursor(QCursor(shape))

    def canvasPressEvent(self, event):
        # Pan stays available even while locked (during generation) so the
        # user can move the map around. Drawing a new zone and deleting the
        # current one are the only actions blocked by the lock.
        badge_hit = self._delete_badge is not None and self._delete_badge.hit_test(QtC.event_pos(event))
        if not self._locked and event.button() == QtC.LeftButton and self._has_zone and badge_hit:
            self._on_delete_zone()
            return
        # Action pills (Compare / Vectorize) sit to the left of the × badge and
        # only exist after a generation. hit_test already gates on isVisible, so
        # hidden pills never match. Emit the request as the LAST statement and
        # return without touching self afterwards: the slot may deactivate this
        # very map tool synchronously (the swipe tool grabs the canvas).
        if event.button() == QtC.LeftButton:
            pos = QtC.event_pos(event)
            if self._vectorize_badge is not None and self._vectorize_badge.hit_test(pos):
                event.accept()
                self.vectorize_requested.emit()
                return
            if self._compare_badge is not None and self._compare_badge.hit_test(pos):
                event.accept()
                self.compare_requested.emit()
                return
        if event.button() == QtC.RightButton and self._has_zone and not self._locked:
            self._pending_context_menu = True
            return
        if event.button() == QtC.LeftButton:
            if self._has_zone:
                self._is_panning = True
                self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
                return
            if self._locked:
                return
            self._start_point = self.toMapCoordinates(QtC.event_pos(event))
            self._is_drawing = True
            self._create_rubber_band()

    def canvasMoveEvent(self, event):
        if self._is_panning:
            self.canvas().panAction(event)
            return
        if not self._is_drawing or self._start_point is None:
            return
        end_point = self.toMapCoordinates(QtC.event_pos(event))
        rect = QgsRectangle(self._start_point, end_point)
        rect.normalize()
        if rect.width() > 0 and rect.height() > 0:
            rect, _ = self._snap_to_ratio(rect)
            self._update_rubber_band_from_rect(rect)
        else:
            self._update_rubber_band(self._start_point, end_point)

    def canvasReleaseEvent(self, event):
        if event.button() == QtC.RightButton and getattr(self, "_pending_context_menu", False):
            self._pending_context_menu = False
            self._show_zone_context_menu(event)
            return
        if event.button() == QtC.LeftButton and self._is_panning:
            self._is_panning = False
            self.canvas().panActionEnd(QtC.event_pos(event))
            self._refresh_cursor()
            return
        if event.button() == QtC.LeftButton and self._is_drawing:
            self._is_drawing = False
            end_point = self.toMapCoordinates(QtC.event_pos(event))
            rect = QgsRectangle(self._start_point, end_point)
            rect.normalize()
            rect, ratio = self._snap_to_ratio(rect)

            p1 = self.toCanvasCoordinates(QgsPointXY(rect.xMinimum(), rect.yMinimum()))
            p2 = self.toCanvasCoordinates(QgsPointXY(rect.xMaximum(), rect.yMaximum()))
            width_px = abs(p2.x() - p1.x())
            height_px = abs(p2.y() - p1.y())

            if width_px < self.MIN_SIZE_PX or height_px < self.MIN_SIZE_PX:
                self._clear_rubber_band()
                self.zone_too_small.emit()
                return

            # Refuse antimeridian / polar / oversized / rotated / invalid-CRS at draw time.
            try:
                from ...core.errors import AIEditError
                from ..canvas_exporter import validate_zone

                canvas = self.canvas()
                map_crs = canvas.mapSettings().destinationCrs() if canvas else None
                rotation = canvas.rotation() if canvas else 0.0
                validate_zone(rect, map_crs, rotation)
            except AIEditError as err:
                self._clear_rubber_band()
                self.zone_invalid.emit(err.code.value, err.message)
                return
            except Exception:  # nosec B110
                pass

            self._clear_rubber_band()
            self._has_zone = True
            self._zone_rect = rect
            self.selection_made.emit(rect)
            self._show_delete_badge()
            self._refresh_cursor()

    def set_zone(self, rect: QgsRectangle) -> None:
        """Install a zone programmatically (restoring a past generation).
        Stores the rect, shows the delete badge, and switches to pan-mode
        cursor, exactly as a freshly drawn zone would."""
        self._clear_rubber_band()
        self._has_zone = True
        self._zone_rect = QgsRectangle(rect)
        # A restored zone is a fresh, editable zone: make sure a stale lock from
        # a previous generation does not leave the delete/resize affordances off.
        self._locked = False
        self._show_delete_badge()
        self._refresh_cursor()

    def set_has_zone(self, has_zone: bool) -> None:
        """Called by the plugin when the zone state changes externally."""
        self._has_zone = has_zone
        if not has_zone:
            self._zone_rect = None
            self._hide_delete_badge()
            self.hide_action_badges()
        elif self._zone_rect is not None:
            self._show_delete_badge()
        self._refresh_cursor()

    def set_locked(self, locked: bool) -> None:
        """Lock drawing/deletion during generation."""
        self._locked = locked
        if self._delete_badge is not None:
            self._delete_badge.set_enabled(not locked)
        # A new generation makes the previous result's actions stale; drop the
        # pills while it runs. The plugin re-arms them when the run completes.
        if locked:
            self.hide_action_badges()

    def _show_zone_context_menu(self, event) -> None:
        pos = event.globalPos() if hasattr(event, "globalPos") else event.globalPosition().toPoint()
        menu = QMenu()
        menu.addAction(tr("Clear zone"), self._on_delete_zone)
        menu.exec(pos)

    def _on_delete_zone(self) -> None:
        self._clear_rubber_band()
        self._has_zone = False
        self._zone_rect = None
        self._hide_delete_badge()
        self.hide_action_badges()
        self._refresh_cursor()
        self.zone_delete_requested.emit()

    def _snap_to_ratio(self, rect):
        """Snap rectangle to nearest supported aspect ratio."""
        if rect.width() == 0 or rect.height() == 0:
            return rect, (1, 1)

        current_ratio = rect.width() / rect.height()
        best = min(SUPPORTED_RATIOS, key=lambda r: abs(r[0] / r[1] - current_ratio))
        target_ratio = best[0] / best[1]

        new_height = rect.width() / target_ratio
        new_width = rect.height() * target_ratio
        height_delta = abs(new_height - rect.height())
        width_delta = abs(new_width - rect.width())

        sp = self._start_point
        if sp is not None:
            if height_delta <= width_delta:
                if abs(sp.y() - rect.yMinimum()) < abs(sp.y() - rect.yMaximum()):
                    rect.setYMaximum(rect.yMinimum() + new_height)
                else:
                    rect.setYMinimum(rect.yMaximum() - new_height)
            else:
                if abs(sp.x() - rect.xMinimum()) < abs(sp.x() - rect.xMaximum()):
                    rect.setXMaximum(rect.xMinimum() + new_width)
                else:
                    rect.setXMinimum(rect.xMaximum() - new_width)
        else:
            cx, cy = rect.center().x(), rect.center().y()
            if height_delta <= width_delta:
                rect.setYMinimum(cy - new_height / 2)
                rect.setYMaximum(cy + new_height / 2)
            else:
                rect.setXMinimum(cx - new_width / 2)
                rect.setXMaximum(cx + new_width / 2)

        return rect, best

    def keyPressEvent(self, event):
        # We handle no keys here (Escape is handled globally by the dock's
        # QShortcut). Ignore the event instead of calling super(): the base
        # leaves it accepted, which makes QgsMapCanvas skip its own keyboard
        # handling - in particular the hold-Space temporary pan and arrow-key
        # scroll. Ignoring lets the canvas keep that behavior while our tool is
        # active.
        event.ignore()

    def preserve_state_on_next_deactivate(self) -> None:
        """Tell the next deactivate() to keep the zone + badge alive.

        Plugin calls this right before switching the canvas to one of our
        own tools (Mark up), so the zone outline survives the transition.
        Without the flag, deactivate clears state - the right default when
        the user picks pan / measure / another plugin's tool, which would
        otherwise leave AI-Edit overlays hanging on the canvas.
        """
        self._preserve_on_deactivate = True

    def deactivate(self):
        # The in-progress drawing band is always discarded. Zone state, the
        # × badge and the persistent rectangle outline only survive when the
        # plugin explicitly asked us to keep them (i.e. switching to a Mark
        # up tool). Otherwise we drop them so unrelated map-tool switches
        # (pan, measure, other plugins) don't leave AI-Edit overlays behind.
        self._clear_rubber_band()
        if getattr(self, "_preserve_on_deactivate", False):
            # A preserved switch (Compare / Mark up): keep the zone, the ×
            # badge and the action pills alive. Compare relies on this so the
            # pills stay live while the swipe owns the canvas; Mark up already
            # pre-hides the pills at panel entry, so nothing lingers there.
            self._preserve_on_deactivate = False
        else:
            # A real tool change (pan, measure, another plugin): drop our
            # overlays so nothing hangs on the canvas.
            self._has_zone = False
            self._zone_rect = None
            self._hide_delete_badge()
            self.hide_action_badges()
        super().deactivate()

    def cleanup(self) -> None:
        """Detach from the canvas before the plugin unloads."""
        for attr in ("_delete_badge", "_compare_badge", "_vectorize_badge"):
            badge = getattr(self, attr, None)
            if badge is None:
                continue
            scene = badge.scene()
            if scene is not None:
                try:
                    scene.removeItem(badge)
                except RuntimeError:
                    pass
            setattr(self, attr, None)

    # -- delete-badge overlay --------------------------------------------------

    def _show_delete_badge(self) -> None:
        if self._zone_rect is None:
            return
        if self._delete_badge is None:
            self._delete_badge = _ZoneDeleteBadge(self.canvas())
        top_right = QgsPointXY(
            self._zone_rect.xMaximum(), self._zone_rect.yMaximum()
        )
        self._delete_badge.set_anchor(top_right)
        self._delete_badge.set_enabled(not self._locked)
        self._delete_badge.show()

    def _hide_delete_badge(self) -> None:
        if self._delete_badge is not None:
            self._delete_badge.hide()

    # -- post-generation action pills ------------------------------------------

    _BADGE_GAP_FROM_CORNER = 8  # px between the × badge edge and the first pill
    _BADGE_GAP_BETWEEN = 6  # px between two pills

    def show_action_badges(self, compare: bool, vectorize: bool) -> None:
        """Show the Compare / Vectorize pills to the left of the × badge.

        Called by the plugin once a generation completes. ``compare`` is gated
        on swipe eligibility; ``vectorize`` on the run being a detection /
        segmentation template. No-op without a zone rectangle.
        """
        if self._zone_rect is None:
            return
        if compare and self._compare_badge is None:
            self._compare_badge = _ZoneActionBadge(
                self.canvas(), "compare", tr("Compare")
            )
        if vectorize and self._vectorize_badge is None:
            self._vectorize_badge = _ZoneActionBadge(
                self.canvas(), "vectorize", tr("Vectorize")
            )
        top_right = QgsPointXY(
            self._zone_rect.xMaximum(), self._zone_rect.yMaximum()
        )
        # Lay the visible pills out leftward from the corner: Compare nearest
        # the × badge, Vectorize beyond it. Offsets are pill-centre distances.
        cursor = _ZoneDeleteBadge.RADIUS + self._BADGE_GAP_FROM_CORNER
        ordered = (
            (self._compare_badge, compare),
            (self._vectorize_badge, vectorize),
        )
        for badge, wanted in ordered:
            if badge is None:
                continue
            if not wanted:
                badge.hide()
                continue
            badge.set_anchor(top_right)
            badge.set_offset(cursor + badge.width / 2.0)
            badge.show()
            cursor += badge.width + self._BADGE_GAP_BETWEEN

    def hide_action_badges(self) -> None:
        if self._compare_badge is not None:
            self._compare_badge.hide()
        if self._vectorize_badge is not None:
            self._vectorize_badge.hide()

    def set_compare_active(self, active: bool) -> None:
        """Reflect the live compare state on the Compare pill (pressed look)."""
        if self._compare_badge is not None:
            self._compare_badge.set_active(active)

    def overlay_hit(self, canvas_pt) -> str | None:
        """Hit-test the action pills at a canvas-pixel point, NO side effects.

        Returns "vectorize" / "compare" / "delete" for the pill under the
        point, else None. Used by the swipe tool (via a plugin callback) to
        keep the pills clickable while a comparison owns the canvas. Pure
        hit-test so the caller can defer the actual action out of the event
        loop and avoid swapping the map tool mid-event.
        """
        if self._vectorize_badge is not None and self._vectorize_badge.hit_test(canvas_pt):
            return "vectorize"
        if self._compare_badge is not None and self._compare_badge.hit_test(canvas_pt):
            return "compare"
        if self._delete_badge is not None and self._delete_badge.hit_test(canvas_pt):
            return "delete"
        return None

    def _create_rubber_band(self):
        self._clear_rubber_band()
        self._rubber_band = QgsRubberBand(self.canvas(), QtC.PolygonGeometry)
        self._rubber_band.setColor(QColor(65, 105, 225, 80))
        self._rubber_band.setStrokeColor(QColor(65, 105, 225, 200))
        self._rubber_band.setWidth(2)

    def _update_rubber_band_from_rect(self, rect):
        if not self._rubber_band:
            return
        self._rubber_band.reset(QtC.PolygonGeometry)
        self._rubber_band.addPoint(QgsPointXY(rect.xMinimum(), rect.yMinimum()), False)
        self._rubber_band.addPoint(QgsPointXY(rect.xMaximum(), rect.yMinimum()), False)
        self._rubber_band.addPoint(QgsPointXY(rect.xMaximum(), rect.yMaximum()), False)
        self._rubber_band.addPoint(QgsPointXY(rect.xMinimum(), rect.yMaximum()), True)

    def _update_rubber_band(self, start, end):
        if not self._rubber_band:
            return
        self._rubber_band.reset(QtC.PolygonGeometry)
        self._rubber_band.addPoint(QgsPointXY(start.x(), start.y()), False)
        self._rubber_band.addPoint(QgsPointXY(end.x(), start.y()), False)
        self._rubber_band.addPoint(QgsPointXY(end.x(), end.y()), False)
        self._rubber_band.addPoint(QgsPointXY(start.x(), end.y()), True)

    def _clear_rubber_band(self):
        # Canvas/scene C++ side may be gone during shutdown; swallow to let exit proceed.
        if self._rubber_band:
            try:
                canvas = self.canvas()
                scene = canvas.scene() if canvas else None
                if scene is not None:
                    scene.removeItem(self._rubber_band)
            except (RuntimeError, AttributeError):
                pass
            self._rubber_band = None
