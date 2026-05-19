from __future__ import annotations

from qgis.core import QgsPointXY, QgsRectangle
from qgis.gui import QgsMapCanvasItem, QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import QPointF, QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QPainter, QPen
from qgis.PyQt.QtWidgets import QMenu

from ..core import qt_compat as QtC
from ..core.i18n import tr

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
    _BRAND_BLUE = QColor("#1976d2")
    _DISABLED_BG = QColor(25, 118, 210, 115)

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


class RectangleSelectionTool(QgsMapTool):
    """Map tool for selecting a rectangular zone on the canvas.

    Stays active after selection so the user can right-click the zone
    to delete it, or draw a new one to replace it.
    """

    selection_made = pyqtSignal(QgsRectangle)
    zone_too_small = pyqtSignal()
    zone_delete_requested = pyqtSignal()

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
        self._refresh_cursor()

        # Badge anchored to the rubber band's top-right corner. Lives in the
        # canvas scene so it follows the rectangle during pan/zoom. Click is
        # forwarded by canvasPressEvent because the active map tool gets the
        # event before scene items would.
        self._delete_badge: _ZoneDeleteBadge | None = None

    def activate(self):
        super().activate()
        self._refresh_cursor()

    def _refresh_cursor(self) -> None:
        """Crosshair while no zone is selected (draw mode), open hand once
        a zone exists so the user feels they can move around again. Panning
        from inside the tool stays delegated to QGIS's built-in middle-mouse
        and Space+drag - left-drag is reserved for drawing, which is the
        primary action and what users expect after a Launch.
        """
        shape = Qt.CursorShape.OpenHandCursor if self._has_zone else QtC.CrossCursor
        self.setCursor(QCursor(shape))

    def canvasPressEvent(self, event):
        if self._locked:
            return
        if (
            event.button() == QtC.LeftButton
            and self._has_zone  # noqa: W503
            and self._delete_badge is not None  # noqa: W503
            and self._delete_badge.hit_test(QtC.event_pos(event))  # noqa: W503
        ):
            self._on_delete_zone()
            return
        if event.button() == QtC.RightButton and self._has_zone:
            self._pending_context_menu = True
            return
        if event.button() == QtC.LeftButton:
            if self._has_zone:
                return
            self._start_point = self.toMapCoordinates(QtC.event_pos(event))
            self._is_drawing = True
            self._create_rubber_band()

    def canvasMoveEvent(self, event):
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

            self._clear_rubber_band()
            self._has_zone = True
            self._zone_rect = rect
            self.selection_made.emit(rect)
            self._show_delete_badge()
            self._refresh_cursor()

    def set_has_zone(self, has_zone: bool) -> None:
        """Called by the plugin when the zone state changes externally."""
        self._has_zone = has_zone
        if not has_zone:
            self._zone_rect = None
            self._hide_delete_badge()
        elif self._zone_rect is not None:
            self._show_delete_badge()
        self._refresh_cursor()

    def set_locked(self, locked: bool) -> None:
        """Lock drawing/deletion during generation."""
        self._locked = locked
        if self._delete_badge is not None:
            self._delete_badge.set_enabled(not locked)

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
        # Escape is handled globally by the dock's QShortcut so a single key
        # exits the whole flow from anywhere. We don't touch the event here,
        # otherwise the map-tool consumption would beat the shortcut and the
        # user would only see the zone get cleared instead of exiting.
        super().keyPressEvent(event)

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
            self._preserve_on_deactivate = False
        else:
            self._has_zone = False
            self._zone_rect = None
            self._hide_delete_badge()
        super().deactivate()

    def cleanup(self) -> None:
        """Detach from the canvas before the plugin unloads."""
        if self._delete_badge is not None:
            scene = self._delete_badge.scene()
            if scene is not None:
                try:
                    scene.removeItem(self._delete_badge)
                except RuntimeError:
                    pass
            self._delete_badge = None

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
        if self._rubber_band:
            self.canvas().scene().removeItem(self._rubber_band)
            self._rubber_band = None
