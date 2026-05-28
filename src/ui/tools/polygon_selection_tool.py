






















from __future__ import annotations

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import QPoint, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QKeySequence

from ...core import qt_compat as QtC
from ...core.i18n import tr
from ..dock.design_tokens import GREEN, qcolor
from .click_thresholds import is_near_point, is_step_too_small
from .selection_map_tool import _ZoneActionBadge, _ZoneDeleteBadge
from .zone_badge_overlay import ZoneBadgeOverlayMixin
from .zone_draw_hint import ZoneDrawHint
from .zone_frame_geometry import _repair_polygon, expand_bbox_to_ratio_and_min_size




_ZONE_BLUE = QColor(65, 105, 225)
_DRAW_LINE = QColor(65, 105, 225, 210)
_TRANSPARENT = QColor(Qt.GlobalColor.transparent)



_CLOSE_GREEN = qcolor(GREEN)




_MOVE_REDRAW_MS = 16


class PolygonSelectionTool(ZoneBadgeOverlayMixin, QgsMapTool):








    selection_made = pyqtSignal(QgsRectangle, QgsGeometry)





    zone_too_small = pyqtSignal()
    zone_delete_requested = pyqtSignal()


    zone_invalid = pyqtSignal(str, str)

    compare_requested = pyqtSignal()
    vectorize_requested = pyqtSignal()

    MIN_VERTICES = 3


    MIN_STEP_PX = 6

    CLOSE_PX = 14

    MIN_SIZE_PX = 50




    PAN_DRAG_PX = 8


    MIN_BOX_PX = 24

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._points: list[QgsPointXY] = []
        self._markers: list[QgsVertexMarker] = []





        self._ring_band = QgsRubberBand(canvas, QtC.PolygonGeometry)
        self._ring_band.setColor(_TRANSPARENT)
        self._ring_band.setStrokeColor(_DRAW_LINE)
        self._ring_band.setWidth(1)
        self._ring_band.setLineStyle(Qt.PenStyle.DashLine)

        self._edges_band = QgsRubberBand(canvas, QtC.LineGeometry)
        self._edges_band.setColor(_DRAW_LINE)
        self._edges_band.setWidth(3)

        self._preview_band = QgsRubberBand(canvas, QtC.LineGeometry)
        self._preview_band.setColor(_DRAW_LINE)
        self._preview_band.setWidth(2)
        self._preview_band.setLineStyle(Qt.PenStyle.DashLine)
        self._can_close = False


        self._press_pos = None


        self._box_start = None

        self._swallow_release = False


        self._pending_move_pos = None
        self._drawn_move_pos = None
        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(self._flush_pending_move)

        self._has_zone = False
        self._zone_rect: QgsRectangle | None = None


        self._zone_polygon: QgsGeometry | None = None
        self._locked = False
        self._pending_context_menu = False
        self._is_panning = False





        self._delete_badge: _ZoneDeleteBadge | None = None

        self._compare_badge: _ZoneActionBadge | None = None
        self._vectorize_badge: _ZoneActionBadge | None = None


        self._compare_active = False

        self._hint = ZoneDrawHint(canvas)

        self._refresh_cursor()

    def activate(self):
        super().activate()
        self._refresh_cursor()
        self._sync_hint()

    def _sync_hint(self) -> None:

        if self._hint is None:
            return
        if self.isActive() and not self._has_zone and not self._locked:
            self._hint.show_state(len(self._points))
        else:
            self._hint.hide()

    def _refresh_cursor(self) -> None:


        shape = Qt.CursorShape.OpenHandCursor if self._has_zone else QtC.CrossCursor
        self.setCursor(QCursor(shape))

    def has_points(self) -> bool:









        return bool(self._points) or self._box_start is not None

    def clear_in_progress_drawing(self) -> None:

        if self._box_start is not None:
            self._swallow_release = True
        self._reset_draw_visuals()
        self._points = []
        self._sync_hint()

    def close_now(self) -> None:

        self._finish()



    def canvasPressEvent(self, event):





        badge_hit = self._delete_badge is not None and self._delete_badge.hit_test(QtC.event_pos(event))
        if not self._locked and event.button() == QtC.LeftButton and self._has_zone and badge_hit:
            self._on_delete_zone()
            return





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
        if event.button() == QtC.RightButton:
            if self._has_zone and not self._locked:
                self._pending_context_menu = True


            return
        if event.button() == QtC.LeftButton and self._has_zone:
            self._is_panning = True
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return



        if event.button() == QtC.LeftButton:
            self._press_pos = QtC.event_pos(event)

    def canvasMoveEvent(self, event):
        if self._is_panning:
            self.canvas().panAction(event)
            return
        if self._box_start is not None:

            self._pending_move_pos = QtC.event_pos(event)
            if not self._move_timer.isActive():
                self._draw_box(self._pending_move_pos)
                self._move_timer.start(_MOVE_REDRAW_MS)
            return
        if self._press_pos is not None and self._drag_beats_pan_threshold(
            QtC.event_pos(event)
        ):
            if not self._points and not self._has_zone and not self._locked:


                self._box_start = self._press_pos
                self._press_pos = None
                self._draw_box(QtC.event_pos(event))
                self._sync_hint()
                return
            self._start_drag_pan(event)
            return
        if self._has_zone or not self._points:
            return



        self._pending_move_pos = QtC.event_pos(event)
        if self._move_timer.isActive():
            return
        self._draw_cursor_preview(self._pending_move_pos)
        self._move_timer.start(_MOVE_REDRAW_MS)

    def _drag_beats_pan_threshold(self, pos) -> bool:


        return not is_step_too_small(
            pos.x() - self._press_pos.x(),
            pos.y() - self._press_pos.y(),
            self.PAN_DRAG_PX,
        )

    def _start_drag_pan(self, event) -> None:



        self._press_pos = None
        self._is_panning = True
        self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        self.canvas().panAction(event)

    def _draw_cursor_preview(self, pos) -> None:

        self._drawn_move_pos = pos
        self._can_close = self._near_first(pos)
        cursor = self._points[0] if self._can_close else self.toMapCoordinates(pos)

        self._preview_band.setToGeometry(
            QgsGeometry.fromPolylineXY([self._points[-1], cursor]), None
        )


        self._draw_ring(self._points + [cursor])
        self._highlight_first(self._can_close)

    def _flush_pending_move(self) -> None:

        pos = self._pending_move_pos
        if pos is None or self._is_panning or self._has_zone:
            return
        if pos == self._drawn_move_pos:
            return
        if self._box_start is not None:
            self._draw_box(pos)
        elif self._points:
            self._draw_cursor_preview(pos)
        else:
            return
        self._move_timer.start(_MOVE_REDRAW_MS)

    def canvasReleaseEvent(self, event):
        if event.button() == QtC.RightButton:
            if self._pending_context_menu:
                self._pending_context_menu = False
                self._show_zone_context_menu(event)
            else:




                event.accept()
                if not self._has_zone and not self._locked and self._box_start is None:
                    self._finish()
            return
        if event.button() != QtC.LeftButton:
            return
        self._press_pos = None
        if self._swallow_release:

            self._swallow_release = False
            return
        if self._box_start is not None:
            self._finish_box(QtC.event_pos(event))
            return
        if self._is_panning:
            self._is_panning = False
            self.canvas().panActionEnd(QtC.event_pos(event))
            self._refresh_cursor()
            return
        if self._has_zone or self._locked:
            return
        pos = QtC.event_pos(event)


        self._can_close = self._near_first(pos)
        if self._can_close and len(self._points) >= self.MIN_VERTICES:
            self._finish()
            return
        self._add_point(self.toMapCoordinates(pos), pos)

    def canvasDoubleClickEvent(self, event):



        if self._has_zone or self._locked:
            return
        if event.button() == QtC.LeftButton:
            self._finish()



    def keyPressEvent(self, event):
        if self._has_zone:



            event.ignore()
            return
        key = event.key()
        if key == QtC.Key_Escape:
            self._cancel_or_exit()
            event.accept()
        elif key in (QtC.Key_Return, QtC.Key_Enter):
            self._finish()
            event.accept()
        elif key in (QtC.Key_Backspace, QtC.Key_Delete) or event.matches(
            QKeySequence.StandardKey.Undo
        ):
            self._undo_last()
            event.accept()
        else:
            event.ignore()

    def _cancel_or_exit(self) -> None:
        if self._points:
            self.clear_in_progress_drawing()
        else:




            self._canvas.unsetMapTool(self)



    def _add_point(self, map_pt: QgsPointXY, screen_pt) -> None:
        if self._points:
            last_screen = self.toCanvasCoordinates(self._points[-1])
            if is_step_too_small(
                screen_pt.x() - last_screen.x(),
                screen_pt.y() - last_screen.y(),
                self.MIN_STEP_PX,
            ):
                return
        self._points.append(map_pt)
        self._add_marker(map_pt, first=len(self._points) == 1)
        self._draw_edges()
        self._draw_ring(self._points)
        self._sync_hint()

    def _undo_last(self) -> None:
        if not self._points:
            return
        self._points.pop()
        if self._markers:
            marker = self._markers.pop()
            try:
                self._canvas.scene().removeItem(marker)
            except (RuntimeError, AttributeError):
                pass
        self._restyle_markers()
        self._draw_edges()
        self._draw_ring(self._points)
        if not self._points:
            self._can_close = False
            self._preview_band.reset(QtC.LineGeometry)
        self._sync_hint()

    def _finish(self) -> None:
        pts = list(self._points)
        if len(pts) < self.MIN_VERTICES:


            if pts and self._hint is not None and not self._has_zone and not self._locked:
                self._hint.flash_too_few()
            return
        geom = QgsGeometry.fromPolygonXY([pts])
        self._reset_draw_visuals()
        self._points = []
        self._commit_polygon(geom)
        self._sync_hint()

    def _box_ring(self, end) -> list:


        a = self._box_start
        corners = ((a.x(), a.y()), (end.x(), a.y()), (end.x(), end.y()), (a.x(), end.y()))
        return [self.toMapCoordinates(QPoint(int(x), int(y))) for x, y in corners]

    def _draw_box(self, end) -> None:
        self._drawn_move_pos = end
        ring = self._box_ring(end)
        self._edges_band.setToGeometry(
            QgsGeometry.fromPolylineXY(ring + [ring[0]]), None
        )

    def _finish_box(self, end) -> None:



        start = self._box_start
        small = (
            abs(end.x() - start.x()) < self.MIN_BOX_PX
            or abs(end.y() - start.y()) < self.MIN_BOX_PX
        )
        ring = None if small else self._box_ring(end)
        self._reset_draw_visuals()
        self._points = []
        if ring is None:
            self._add_point(self.toMapCoordinates(start), start)
            return
        self._commit_polygon(QgsGeometry.fromPolygonXY([ring]))
        self._sync_hint()

    def _draw_edges(self) -> None:
        if len(self._points) >= 2:
            self._edges_band.setToGeometry(
                QgsGeometry.fromPolylineXY(self._points), None
            )
        else:
            self._edges_band.reset(QtC.LineGeometry)

    def _draw_ring(self, pts: list) -> None:
        if len(pts) >= 3:
            self._ring_band.setToGeometry(
                QgsGeometry.fromPolygonXY([list(pts)]), None
            )
        else:
            self._ring_band.reset(QtC.PolygonGeometry)

    def _add_marker(self, pt: QgsPointXY, first: bool) -> None:
        marker = QgsVertexMarker(self._canvas)
        marker.setCenter(pt)
        if QtC.VertexIconCircle is not None:
            marker.setIconType(QtC.VertexIconCircle)
        marker.setColor(_ZONE_BLUE)
        try:
            marker.setFillColor(QColor(Qt.GlobalColor.white))
        except (AttributeError, TypeError):
            pass
        marker.setPenWidth(3)

        marker.setIconSize(13 if first else 10)
        marker.setZValue(1000)
        self._markers.append(marker)

    def _restyle_markers(self) -> None:
        for i, marker in enumerate(self._markers):
            marker.setIconSize(13 if i == 0 else 10)
            marker.setColor(_ZONE_BLUE)

    def _highlight_first(self, hot: bool) -> None:
        if not self._markers or len(self._points) < self.MIN_VERTICES:
            return
        first = self._markers[0]
        first.setColor(_CLOSE_GREEN if hot else _ZONE_BLUE)
        first.setIconSize(18 if hot else 13)

    def _near_first(self, screen_pt) -> bool:
        if len(self._points) < self.MIN_VERTICES:
            return False
        first_screen = self.toCanvasCoordinates(self._points[0])
        return is_near_point(
            screen_pt.x() - first_screen.x(), screen_pt.y() - first_screen.y(), self.CLOSE_PX
        )

    def _reset_draw_visuals(self) -> None:


        if self._edges_band is not None:
            self._edges_band.reset(QtC.LineGeometry)
        if self._ring_band is not None:
            self._ring_band.reset(QtC.PolygonGeometry)
        if self._preview_band is not None:
            self._preview_band.reset(QtC.LineGeometry)
        for m in self._markers:
            try:
                self._canvas.scene().removeItem(m)
            except (RuntimeError, AttributeError):
                pass
        self._markers = []
        self._can_close = False
        self._press_pos = None
        self._box_start = None
        self._pending_move_pos = None
        self._drawn_move_pos = None
        if self._move_timer is not None:
            self._move_timer.stop()



    def _commit_polygon(self, geom: QgsGeometry) -> None:




        repaired = _repair_polygon(geom)
        if repaired is None:
            self.zone_invalid.emit(
                "ZONE_INVALID_POLYGON",
                tr(
                        "This shape is too thin, too small, or crosses itself. "
                        "Draw it again."
                    ),
            )
            return

        canvas = self.canvas()
        mupp = canvas.mapSettings().mapUnitsPerPixel() if canvas else 0.0
        expanded = expand_bbox_to_ratio_and_min_size(
            repaired.boundingBox(), mupp, self.MIN_SIZE_PX
        )

        try:
            from ...core.errors import AIEditError
            from ..canvas_exporter import validate_zone

            map_crs = canvas.mapSettings().destinationCrs() if canvas else None
            rotation = canvas.rotation() if canvas else 0.0
            validate_zone(expanded, map_crs, rotation)
        except AIEditError as err:
            self.zone_invalid.emit(err.code.value, err.message)
            return
        except Exception as err:  # noqa: BLE001

            from ...core.logger import log_warning

            log_warning(f"Zone check failed: {err}")
            self.zone_invalid.emit(
                "ZONE_CHECK_FAILED",
                tr("This zone could not be checked. Draw it again."),
            )
            return

        self._has_zone = True
        self._zone_rect = expanded
        self._zone_polygon = repaired
        self.selection_made.emit(expanded, repaired)
        self._show_delete_badge()
        self._refresh_cursor()

    def set_zone(self, rect: QgsRectangle, polygon: QgsGeometry | None = None) -> None:






        self._reset_draw_visuals()
        self._points = []
        self._has_zone = True
        self._zone_rect = QgsRectangle(rect)
        self._zone_polygon = QgsGeometry(polygon) if polygon is not None and not polygon.isEmpty() else None


        self._locked = False
        self._show_delete_badge()
        self._refresh_cursor()
        self._sync_hint()

    def set_has_zone(self, has_zone: bool) -> None:

        self._has_zone = has_zone
        if not has_zone:
            self._zone_rect = None
            self._zone_polygon = None
            self._hide_delete_badge()
            self.hide_action_badges()
        elif self._zone_rect is not None:
            self._show_delete_badge()
        self._refresh_cursor()
        self._sync_hint()

    def set_locked(self, locked: bool) -> None:

        self._locked = locked
        self._sync_hint()
        if self._delete_badge is not None:
            self._delete_badge.set_enabled(not locked)


        if locked:
            self.hide_action_badges()

    def _on_delete_zone(self) -> None:
        self._has_zone = False
        self._zone_rect = None
        self._zone_polygon = None
        self._hide_delete_badge()
        self.hide_action_badges()
        self._refresh_cursor()
        self._sync_hint()
        self.zone_delete_requested.emit()

    def preserve_state_on_next_deactivate(self) -> None:








        self._preserve_on_deactivate = True

    def deactivate(self):





        self._reset_draw_visuals()
        self._points = []
        if getattr(self, "_preserve_on_deactivate", False):






            self._preserve_on_deactivate = False
        else:


            self._has_zone = False
            self._zone_rect = None
            self._zone_polygon = None
            self._hide_delete_badge()
            self.hide_action_badges()
        if self._hint is not None:
            self._hint.hide()
        super().deactivate()

    def cleanup(self) -> None:

        if self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None
        if self._hint is not None:
            self._hint.dispose()
            self._hint = None
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



        for attr in ("_ring_band", "_edges_band", "_preview_band"):
            band = getattr(self, attr, None)
            if band is None:
                continue
            scene = band.scene()
            if scene is not None:
                try:
                    scene.removeItem(band)
                except RuntimeError:
                    pass
            setattr(self, attr, None)
