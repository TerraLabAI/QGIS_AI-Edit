"""Draw panel - LineMapTool: click-per-vertex straight polyline drawing.

Split out of ``markup_tools.py`` (which still owns MarkupLayerManager,
_MarkupBaseMapTool, and the Pencil/Arrow/Circle tools) to keep both files in
the repo's comfort zone. Imported directly by its own path, the same
convention as the other single-tool modules in this package
(``eyedropper_tool.py``, ``selection_map_tool.py``): consumers do
``from ...tools.markup_line_tool import LineMapTool`` rather than going
through ``markup_tools``.
"""
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

# "Click here to close" highlight for the first vertex of an in-progress Line,
# matching AI Segmentation's PolygonZoneMapTool close affordance (CLOSE_DOT_OK
# in its canvas_palette.py) so both plugins share one drawing grammar.
_LINE_CLOSE_COLOR = _CLOSE_GREEN


class LineMapTool(_MarkupBaseMapTool):
    """Click-per-vertex straight polyline: precise design geometry (lane axes,
    curb lines, plot boundaries) where the Pencil's freehand jitter is a
    liability. A direct answer to field feedback: hand cramp on long, straight
    strokes.

    Interaction grammar ported from AI Segmentation's PolygonZoneMapTool
    (src/ui/polygon_zone_maptool.py), adapted to commit an open or closed
    MultiLineString into MarkupManager instead of a filled polygon zone:

    * Left click drops a vertex (clicks within MIN_STEP_PX of the last vertex
      are ignored, killing the double-click's second hit).
    * A dashed rubber band trails the cursor from the last vertex.
    * Click within CLOSE_PX of the first vertex closes the shape as a ring
      (min MIN_VERTICES_CLOSE points). Double-click or Enter commits an open
      polyline (min MIN_VERTICES_OPEN points).
    * Shift keeps the next segment on a 45 degree step from the last vertex.
    * Backspace, Delete and Ctrl+Z remove the last vertex; with no line
      under way Ctrl+Z undoes the last stroke, as the other tools do.
    * Escape clears the in-progress vertices and stays armed. Escape again,
      with nothing left to clear, deactivates the tool (two-stage escape).
    * Leaving the tool any other way (Done, another tool) keeps a line that
      already has two vertices, as a drawing app does: only Escape throws
      the vertices away.
    * Right click is inert and consumed, same as AI Segmentation.
    """

    MIN_VERTICES_OPEN = 2
    MIN_VERTICES_CLOSE = 3
    # Ignore a click within this many screen pixels of the last vertex: it is
    # a double-click's second hit or jitter, not a new vertex.
    MIN_STEP_PX = 6
    # Cursor within this many pixels of the first vertex closes the shape.
    CLOSE_PX = 14

    def __init__(self, canvas: QgsMapCanvas, manager: MarkupLayerManager) -> None:
        super().__init__(canvas, manager, shape="line")
        self._points: list[QgsPointXY] = []
        self._markers: list[QgsVertexMarker] = []
        self._edges_band: QgsRubberBand | None = None
        self._preview_band: QgsRubberBand | None = None
        self._can_close = False
        # Qt sends a double-click as press, release, double-click, release:
        # the last release arrives after the line is finished and would start
        # a new line with a stray first vertex under the cursor.
        self._swallow_release = False

    # -- Mouse -------------------------------------------------------

    def canvasPressEvent(self, event):  # noqa: N802
        # The second press of a double-click arrives as the double-click
        # event, never here, so any press here starts a fresh click and a
        # tail that never came (released off the canvas) is forgotten.
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
            # Inert on purpose (AI Segmentation convention): avoids an
            # accidental commit from a context-menu click.
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
        # The two single clicks of the double-click are deduped by
        # MIN_STEP_PX in canvasReleaseEvent, so by here the real vertices are
        # already in place, so just finish as an open polyline.
        if event.button() == QtC.LeftButton:
            self._swallow_release = True
            self._finish(closed=False)

    def _cursor_point(self, event) -> QgsPointXY:
        """The next vertex under the cursor; Shift snaps it to 45 degrees
        from the last one."""
        pt = self.toMapCoordinates(QtC.event_pos(event))
        if self._points and _shift_held(event):
            return snap_to_angle(self._points[-1], pt)
        return pt

    def set_color(self, color: QColor) -> None:
        """A colour picked mid-line recolours the line being drawn too, not
        only the next one: the preview must show what will be committed."""
        super().set_color(color)
        for band in (self._edges_band, self._preview_band):
            if band is None:
                continue
            try:
                band.setStrokeColor(QColor(self._color))
                band.setColor(QColor(self._color))
            except RuntimeError:  # band already deleted by Qt
                pass
        self._restyle_markers()

    # -- Keyboard ------------------------------------------------------

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
            # No line under way: undo the last stroke, as Pencil, Arrow and
            # Circle do. It used to do nothing with Line armed.
            self._manager.undo_last()
            event.accept()
            return
        if key in (QtC.Key_Backspace, QtC.Key_Delete) or event.matches(
            QKeySequence.StandardKey.Undo
        ):
            self._undo_last_vertex()
            event.accept()
            return
        # Keys we don't handle: ignore so the canvas keeps its keyboard nav
        # (hold-Space temporary pan, arrow-key scroll).
        event.ignore()

    # -- Dock delegation protocol -----------------------------------------
    # The dock's Escape and Return/Enter shortcuts are QShortcut(WindowShortcut)
    # and win the keyboard race before this tool's own keyPressEvent ever runs,
    # so the dock delegates mid-draw keys here. Same protocol names as
    # PolygonSelectionTool (has_points / clear_in_progress_drawing / close_now).

    def has_points(self) -> bool:
        """True while at least one vertex of an in-progress line is placed."""
        return bool(self._points)

    def clear_in_progress_drawing(self) -> None:
        """Escape's first stage: drop the in-progress vertices, stay armed."""
        self._clear_in_progress()

    def close_now(self) -> None:
        """Enter's equivalent: commit the open polyline (no-op below 2 points)."""
        self._finish(closed=False)

    def escape_step(self) -> None:
        """One Escape press routed from the dock: clear points if any, else
        deactivate (the panel button un-checks through mapToolSet)."""
        self._escape_or_deactivate()

    # -- Internals -------------------------------------------------------

    def _add_point(self, map_pt: QgsPointXY, screen_pt) -> None:
        if self._points:
            last_screen = self.toCanvasCoordinates(self._points[-1])
            if is_step_too_small(
                screen_pt.x() - last_screen.x(),
                screen_pt.y() - last_screen.y(),
                self.MIN_STEP_PX,
            ):
                return  # duplicate / double-click second hit
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
            pass  # older builds: outline-only marker is still clearly visible
        marker.setPenWidth(3)
        # The first vertex is a touch bigger: it is the one you click to close.
        marker.setIconSize(13 if first else 10)
        marker.setZValue(1000)
        self._markers.append(marker)

    def _restyle_markers(self) -> None:
        for i, marker in enumerate(self._markers):
            try:
                marker.setIconSize(13 if i == 0 else 10)
                marker.setColor(self._color)
            except RuntimeError:  # marker already deleted by Qt
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
            except RuntimeError:  # marker or scene already deleted by Qt
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
            # Two-stage escape, mirroring AI Segmentation's zone tool: nothing
            # left to clear, so this Escape leaves the tool altogether.
            self._canvas.unsetMapTool(self)

    def _clear_in_progress(self) -> None:
        self._reset_visuals()
        self._points = []

    def _finish(self, closed: bool) -> None:
        min_vertices = self.MIN_VERTICES_CLOSE if closed else self.MIN_VERTICES_OPEN
        if len(self._points) < min_vertices:
            return  # not enough vertices yet, keep drawing
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
            except RuntimeError:  # band or scene already deleted by Qt
                pass
        self._edges_band = None
        self._preview_band = None
        for marker in self._markers:
            try:
                self._canvas.scene().removeItem(marker)
            except RuntimeError:  # marker or scene already deleted by Qt
                pass
        self._markers = []
        self._can_close = False

    def deactivate(self):  # noqa: D401
        # Done or another tool while a line is under way: keep it once it is
        # a line (two vertices), instead of silently dropping the clicks.
        if len(self._points) >= self.MIN_VERTICES_OPEN:
            try:
                self._finish(closed=False)
            except RuntimeError:  # canvas or layer torn down (unload)
                pass
        self._reset_visuals()
        self._points = []
        super().deactivate()


# ----------------------------------------------------------------------
# Geometry builders - dedicated to LineMapTool
# ----------------------------------------------------------------------


def _line_geometry(points: list[QgsPointXY], closed: bool) -> QgsGeometry:
    """Build the committed Line geometry: an open polyline, or (closed=True)
    a closed LineString ring, the exact representation CircleMapTool's
    _ellipse_ring already produces (first vertex repeated as the last)."""
    if len(points) < 2:
        return QgsGeometry()
    pts = list(points)
    if closed:
        pts = pts + [pts[0]]
    return QgsGeometry.fromPolylineXY(pts)
