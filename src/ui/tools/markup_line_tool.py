









from __future__ import annotations

from qgis.core import QgsGeometry, QgsPointXY
from qgis.gui import QgsMapCanvas, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QKeySequence

from ...core import qt_compat as QtC
from .click_thresholds import is_near_point, is_step_too_small
from .markup_tools import (
    MarkupLayerManager,
    _MarkupBaseMapTool,
    _shift_held,
    snap_to_angle,
)
from .polygon_selection_tool import _CLOSE_GREEN




_LINE_CLOSE_COLOR = _CLOSE_GREEN


class LineMapTool(_MarkupBaseMapTool):


























    MIN_VERTICES_OPEN = 2
    MIN_VERTICES_CLOSE = 3


    MIN_STEP_PX = 6

    CLOSE_PX = 14

    def __init__(self, canvas: QgsMapCanvas, manager: MarkupLayerManager) -> None:
        super().__init__(canvas, manager, shape="line")
        self._points: list[QgsPointXY] = []
        self._markers: list[QgsVertexMarker] = []
        self._edges_band: QgsRubberBand | None = None
        self._preview_band: QgsRubberBand | None = None
        self._can_close = False



        self._swallow_release = False



    def canvasPressEvent(self, event):  # noqa: N802



        if event.button() == QtC.LeftButton:
            self._swallow_release = False

    def canvasMoveEvent(self, event):  # noqa: N802
        if not self._points:
            return
        pos = QtC.event_pos(event)
        self._can_close = self._near_first(pos)
        cursor = self._points[0] if self._can_close else self._cursor_point(event)
        if self._preview_band is not None:
            self._preview_band.setToGeometry(
                QgsGeometry.fromPolylineXY([self._points[-1], cursor]), None
            )
        self._highlight_first(self._can_close)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if event.button() == QtC.RightButton:


            event.accept()
            return
        if event.button() != QtC.LeftButton:
            return
        if self._swallow_release:
            self._swallow_release = False
            return
        if self._can_close and len(self._points) >= self.MIN_VERTICES_CLOSE:
            self._finish(closed=True)
            return
        map_pt = self._cursor_point(event)
        self._add_point(map_pt, self.toCanvasCoordinates(map_pt))

    def canvasDoubleClickEvent(self, event):  # noqa: N802



        if event.button() == QtC.LeftButton:
            self._swallow_release = True
            self._finish(closed=False)

    def _cursor_point(self, event) -> QgsPointXY:


        pt = self.toMapCoordinates(QtC.event_pos(event))
        if self._points and _shift_held(event):
            return snap_to_angle(self._points[-1], pt)
        return pt

    def set_color(self, color: QColor) -> None:


        super().set_color(color)
        for band in (self._edges_band, self._preview_band):
            if band is None:
                continue
            try:
                band.setStrokeColor(QColor(self._color))
                band.setColor(QColor(self._color))
            except RuntimeError:
                pass
        self._restyle_markers()



    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key == QtC.Key_Escape:
            self._escape_or_deactivate()
            event.accept()
            return
        if key in (QtC.Key_Return, QtC.Key_Enter):
            self._finish(closed=False)
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Undo) and not self._points:


            self._manager.undo_last()
            event.accept()
            return
        if key in (QtC.Key_Backspace, QtC.Key_Delete) or event.matches(
            QKeySequence.StandardKey.Undo
        ):
            self._undo_last_vertex()
            event.accept()
            return


        event.ignore()







    def has_points(self) -> bool:

        return bool(self._points)

    def clear_in_progress_drawing(self) -> None:

        self._clear_in_progress()

    def close_now(self) -> None:

        self._finish(closed=False)

    def escape_step(self) -> None:


        self._escape_or_deactivate()



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
        if self._edges_band is None:
            self._edges_band = self._begin_rubber(QtC.LineGeometry)
        if self._preview_band is None:
            self._preview_band = self._begin_rubber(QtC.LineGeometry)
            self._preview_band.setWidth(2)
            self._preview_band.setLineStyle(Qt.PenStyle.DashLine)
        self._add_marker(map_pt, first=len(self._points) == 1)
        self._draw_edges()

    def _draw_edges(self) -> None:
        if self._edges_band is None:
            return
        if len(self._points) >= 2:
            self._edges_band.setToGeometry(
                QgsGeometry.fromPolylineXY(self._points), None
            )
        else:
            self._edges_band.reset(QtC.LineGeometry)

    def _add_marker(self, pt: QgsPointXY, first: bool) -> None:
        marker = QgsVertexMarker(self._canvas)
        marker.setCenter(pt)
        if QtC.VertexIconCircle is not None:
            marker.setIconType(QtC.VertexIconCircle)
        marker.setColor(self._color)
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
            try:
                marker.setIconSize(13 if i == 0 else 10)
                marker.setColor(self._color)
            except RuntimeError:
                pass

    def _highlight_first(self, hot: bool) -> None:
        if not self._markers or len(self._points) < self.MIN_VERTICES_CLOSE:
            return
        first = self._markers[0]
        first.setColor(_LINE_CLOSE_COLOR if hot else self._color)
        first.setIconSize(18 if hot else 13)

    def _near_first(self, screen_pt) -> bool:
        if len(self._points) < self.MIN_VERTICES_CLOSE:
            return False
        first_screen = self.toCanvasCoordinates(self._points[0])
        return is_near_point(
            screen_pt.x() - first_screen.x(),
            screen_pt.y() - first_screen.y(),
            self.CLOSE_PX,
        )

    def _undo_last_vertex(self) -> None:
        if not self._points:
            return
        self._points.pop()
        if self._markers:
            marker = self._markers.pop()
            try:
                self._canvas.scene().removeItem(marker)
            except RuntimeError:
                pass
        self._restyle_markers()
        self._draw_edges()
        if not self._points:
            self._can_close = False
            if self._preview_band is not None:
                self._preview_band.reset(QtC.LineGeometry)

    def _escape_or_deactivate(self) -> None:
        if self._points:
            self._clear_in_progress()
        else:


            self._canvas.unsetMapTool(self)

    def _clear_in_progress(self) -> None:
        self._reset_visuals()
        self._points = []

    def _finish(self, closed: bool) -> None:
        min_vertices = self.MIN_VERTICES_CLOSE if closed else self.MIN_VERTICES_OPEN
        if len(self._points) < min_vertices:
            return
        geom = _line_geometry(self._points, closed)
        self._reset_visuals()
        self._points = []
        if geom.isEmpty():
            return
        self._manager.commit(geom, self._color, self._shape)

    def _reset_visuals(self) -> None:
        for band in (self._edges_band, self._preview_band):
            if band is None:
                continue
            try:
                self._canvas.scene().removeItem(band)
            except RuntimeError:
                pass
        self._edges_band = None
        self._preview_band = None
        for marker in self._markers:
            try:
                self._canvas.scene().removeItem(marker)
            except RuntimeError:
                pass
        self._markers = []
        self._can_close = False

    def deactivate(self):  # noqa: D401


        if len(self._points) >= self.MIN_VERTICES_OPEN:
            try:
                self._finish(closed=False)
            except RuntimeError:
                pass
        self._reset_visuals()
        self._points = []
        super().deactivate()







def _line_geometry(points: list[QgsPointXY], closed: bool) -> QgsGeometry:



    if len(points) < 2:
        return QgsGeometry()
    pts = list(points)
    if closed:
        pts = pts + [pts[0]]
    return QgsGeometry.fromPolylineXY(pts)
