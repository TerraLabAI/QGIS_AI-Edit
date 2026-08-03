"""Click-per-vertex polygon zone selection, replacing the old drag-rectangle
RectangleSelectionTool.

Drawing grammar ported from AI Segmentation's PolygonZoneMapTool
(src/ui/polygon_zone_maptool.py, read-only reference, never modified) and
already shared with this plugin's own markup LineMapTool
(src/ui/tools/markup_line_tool.py): click to drop a vertex, a dashed rubber
band trails the cursor, closing happens via a click on the first (highlighted)
vertex, a double-click, or Enter; Backspace/Delete/Ctrl+Z undo a vertex;
Escape clears the in-progress shape first and only exits the tool on a second
press with nothing left to clear; right-click is inert; a left-drag pans the
map, mid-draw as well as once the zone is closed. Colors are AI Edit's
own zone blue (matching the committed rectangle outline this tool replaces),
not AI Segmentation's brand blue.

Everything AI Edit's zone chrome expects from the previous rectangle tool -
the floating delete badge, the Compare/Vectorize pills, the locked state
during generation, drag-to-pan and right-click "Clear zone" once a zone
exists - is kept unchanged; only the drawing gesture and the emitted payload
(bbox + polygon, instead of just a rectangle) changed.
"""
from __future__ import annotations

from qgis.core import QgsGeometry, QgsPointXY, QgsRectangle
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor, QKeySequence
from qgis.PyQt.QtWidgets import QMenu

from ...core import qt_compat as QtC
from ...core.i18n import tr
from .click_thresholds import is_near_point, is_step_too_small
from .selection_map_tool import _ZoneActionBadge, _ZoneDeleteBadge, supported_ratios

# AI Edit's existing zone blue (matches the rectangle tool's old drag rubber
# band and the committed zone outline in zone_versions.py), not AI
# Segmentation's brand blue.
_ZONE_BLUE = QColor(65, 105, 225)
_DRAW_LINE = QColor(65, 105, 225, 210)
_TRANSPARENT = QColor(0, 0, 0, 0)
# "Click here to close" highlight on the first vertex, matching the markup
# Line tool's close affordance so both tools share one drawing grammar.
_CLOSE_GREEN = QColor(34, 197, 94)


def _repair_polygon(geom: QgsGeometry) -> QgsGeometry | None:
    """Return a valid, purely polygonal geometry, or None if nothing areal
    survives.

    Ported from AI Segmentation's ``repair_polygon``: makeValid can turn a
    self-intersecting click path (a bowtie) into a GeometryCollection mixing
    stray lines/points with polygon parts, or split it into several polygon
    parts, so this keeps only the polygon parts and rejects anything with no
    area left (collinear points, a closed-but-zero-width shape).
    """
    if geom is None or geom.isEmpty():
        return None
    fixed = geom if geom.isGeosValid() else geom.makeValid()
    if fixed is None or fixed.isEmpty():
        fixed = geom
    parts = fixed.asGeometryCollection() if fixed.isMultipart() else [fixed]
    polygons = [p for p in parts if not p.isEmpty() and p.type() == QtC.PolygonGeometry]
    if not polygons:
        return None
    combined = QgsGeometry.collectGeometry(polygons)
    if combined is None or combined.isEmpty() or combined.area() <= 0:
        return None
    return combined


def expand_bbox_to_ratio_and_min_size(
    bbox: QgsRectangle, mupp: float, min_size_px: float
) -> QgsRectangle:
    """Grow ``bbox`` (never shrink it) to the nearest ``supported_ratios()``
    aspect ratio, then further (if still needed) to ``min_size_px`` per side.

    Both steps grow symmetrically about the original bbox's centre, so the
    drawn polygon - which is always inside its own bounding box - stays fully
    inside the returned frame. ``mupp`` (map units per canvas pixel) converts
    the pixel-based minimum size into map units; when it is falsy (no live
    canvas, e.g. a unit test), the ratio step still runs and the min-size step
    is skipped.
    """
    w0 = bbox.width()
    h0 = bbox.height()
    if w0 <= 0 or h0 <= 0:
        return QgsRectangle(bbox)
    cx = bbox.center().x()
    cy = bbox.center().y()
    current_ratio = w0 / h0
    best = min(supported_ratios(), key=lambda r: abs(r[0] / r[1] - current_ratio))
    target_ratio = best[0] / best[1]
    if current_ratio <= target_ratio:
        # Too tall for the target ratio (or already matching): widen, keep height.
        new_w = h0 * target_ratio
        new_h = h0
    else:
        # Too wide for the target ratio: heighten, keep width.
        new_w = w0
        new_h = w0 / target_ratio
    if mupp and mupp > 0:
        min_map = min_size_px * mupp
        scale = max(
            1.0,
            (min_map / new_w) if new_w > 0 else 1.0,
            (min_map / new_h) if new_h > 0 else 1.0,
        )
        new_w *= scale
        new_h *= scale
    half_w = new_w / 2.0
    half_h = new_h / 2.0
    return QgsRectangle(cx - half_w, cy - half_h, cx + half_w, cy + half_h)


class PolygonSelectionTool(QgsMapTool):
    """Draw a polygon zone one vertex at a time, then keep it editable.

    Emits ``selection_made(QgsRectangle, QgsGeometry)`` on close: the
    polygon's bounding box expanded to the nearest supported ratio and
    minimum size (what gets exported and generated, exactly like today's
    rectangle), and the repaired polygon itself in canvas CRS.
    """

    selection_made = pyqtSignal(QgsRectangle, QgsGeometry)
    # Kept declared (never emitted) so the plugin's existing connect/
    # disconnect calls (lifecycle.py MAP_TOOL_SIGNALS) keep working
    # unchanged. A click-per-vertex polygon can't really end up sub-pixel:
    # each vertex is a real screen click, and _commit_polygon grows any
    # undersized frame up to MIN_SIZE_PX instead of rejecting it.
    zone_too_small = pyqtSignal()
    zone_delete_requested = pyqtSignal()
    # Edge-case zone refusal (antimeridian, polar, rotated map, invalid CRS,
    # or a degenerate/self-intersecting polygon that repairs to nothing).
    zone_invalid = pyqtSignal(str, str)
    # Post-generation action pills clicked on the canvas (beside the × badge).
    compare_requested = pyqtSignal()
    vectorize_requested = pyqtSignal()

    MIN_VERTICES = 3
    # Ignore a click within this many screen pixels of the last vertex: it is
    # a double-click's second hit or jitter, not a new vertex.
    MIN_STEP_PX = 6
    # Cursor within this many pixels of the first vertex closes the polygon.
    CLOSE_PX = 14
    # Committed zone's bounding box is never smaller than this per side.
    MIN_SIZE_PX = 50
    # A left-drag longer than this many screen pixels pans the map instead of
    # dropping a vertex, so a zone can start off screen and the user never has
    # to leave the tool to move around. Well above MIN_STEP_PX, so an ordinary
    # click can never turn into a pan.
    PAN_DRAG_PX = 8
    # Redraw budget for the cursor preview, in milliseconds. Windows delivers
    # mouse moves far faster than the canvas can repaint the fill and the
    # dashed edge, and that backlog is what makes the next click land late.
    MOVE_REDRAW_MS = 16

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self._points: list[QgsPointXY] = []
        self._markers: list[QgsVertexMarker] = []
        # The ring being enclosed, outline only, never a fill: in AI Edit the
        # imagery under the zone IS the model input, so nothing may tint it
        # (that veil came over from AI Segmentation, where the interior is the
        # result rather than the input). Thin and dashed so it reads as "not
        # placed yet" against the solid edges band underneath.
        self._ring_band = QgsRubberBand(canvas, QtC.PolygonGeometry)
        self._ring_band.setColor(_TRANSPARENT)
        self._ring_band.setStrokeColor(_DRAW_LINE)
        self._ring_band.setWidth(1)
        self._ring_band.setLineStyle(Qt.PenStyle.DashLine)
        # Solid edges through the placed vertices: visible from the 2nd on.
        self._edges_band = QgsRubberBand(canvas, QtC.LineGeometry)
        self._edges_band.setColor(_DRAW_LINE)
        self._edges_band.setWidth(3)
        # Dashed segment from the last vertex to the cursor (the next edge).
        self._preview_band = QgsRubberBand(canvas, QtC.LineGeometry)
        self._preview_band.setColor(_DRAW_LINE)
        self._preview_band.setWidth(2)
        self._preview_band.setLineStyle(Qt.PenStyle.DashLine)
        self._can_close = False
        # Where the left button went down while drawing. A drag from there
        # pans the map (see PAN_DRAG_PX); a click drops a vertex on release.
        self._press_pos = None
        # Cursor position waiting to be drawn, and the one already drawn.
        # canvasMoveEvent stores, the timer flushes (see MOVE_REDRAW_MS).
        self._pending_move_pos = None
        self._drawn_move_pos = None
        self._move_timer = QTimer(self)
        self._move_timer.setSingleShot(True)
        self._move_timer.timeout.connect(self._flush_pending_move)

        self._has_zone = False
        self._zone_rect: QgsRectangle | None = None
        # The committed (repaired) polygon outline. Badges anchor to one of
        # its vertices; None for rectangle-only zones (history restore).
        self._zone_polygon: QgsGeometry | None = None
        self._locked = False
        self._pending_context_menu = False
        self._is_panning = False

        # Badge anchored to the committed zone's top-right corner. Lives in
        # the canvas scene so it follows the zone during pan/zoom. Click is
        # forwarded by canvasPressEvent because the active map tool gets the
        # event before scene items would.
        self._delete_badge: _ZoneDeleteBadge | None = None
        # Post-generation action pills (Compare / Vectorize), same approach.
        self._compare_badge: _ZoneActionBadge | None = None
        self._vectorize_badge: _ZoneActionBadge | None = None
        # True while a before/after comparison owns the canvas. The × badge is
        # off screen for its whole duration (see set_compare_active).
        self._compare_active = False

        self._refresh_cursor()

    def activate(self):
        super().activate()
        self._refresh_cursor()

    def _refresh_cursor(self) -> None:
        """Crosshair while drawing (no zone yet), open hand once a zone
        exists so left-drag pans the map like QGIS's native pan tool."""
        shape = Qt.CursorShape.OpenHandCursor if self._has_zone else QtC.CrossCursor
        self.setCursor(QCursor(shape))

    def has_points(self) -> bool:
        """True while at least one vertex of an in-progress draw is placed.

        Used by the dock's Escape/Enter routing: the dock's global Escape
        and Return/Enter shortcuts are QShortcut(WindowShortcut) and win the
        keyboard race before this tool's own keyPressEvent ever runs, so the
        dock checks this first and delegates to :meth:`clear_in_progress_drawing`
        / :meth:`close_now` instead of doing its normal thing.
        """
        return bool(self._points)

    def clear_in_progress_drawing(self) -> None:
        """Escape's first stage: drop the in-progress vertices, stay armed."""
        self._reset_draw_visuals()
        self._points = []

    def close_now(self) -> None:
        """Enter's equivalent when nothing else claims it (see has_points)."""
        self._finish()

    # -- Mouse ----------------------------------------------------------

    def canvasPressEvent(self, event):
        # Pan stays available even while locked (during generation) so the
        # user can move the map around. Drawing a new zone and deleting the
        # current one are the only actions blocked by the lock.
        # hit_test gates on isVisible, so the × cannot be hit while a
        # comparison hides it.
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
        if event.button() == QtC.RightButton:
            if self._has_zone and not self._locked:
                self._pending_context_menu = True
            # Mid-draw or locked: nothing armed on press, release will just
            # consume the click (right-click is inert while drawing).
            return
        if event.button() == QtC.LeftButton and self._has_zone:
            self._is_panning = True
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        # Left press with no zone yet: nothing is drawn here. Vertices are
        # added on release (canvasReleaseEvent), matching the ported grammar;
        # remember the press so a drag can pan instead (canvasMoveEvent).
        if event.button() == QtC.LeftButton:
            self._press_pos = QtC.event_pos(event)

    def canvasMoveEvent(self, event):
        if self._is_panning:
            self.canvas().panAction(event)
            return
        if self._press_pos is not None and self._drag_beats_pan_threshold(
            QtC.event_pos(event)
        ):
            self._start_drag_pan(event)
            return
        if self._has_zone or not self._points:
            return
        # Throttled: draw this position now if the last redraw is old enough,
        # otherwise park it and let the timer draw the newest one. Redrawing on
        # every single move event is what makes clicks feel late on Windows.
        self._pending_move_pos = QtC.event_pos(event)
        if self._move_timer.isActive():
            return
        self._draw_cursor_preview(self._pending_move_pos)
        self._move_timer.start(self.MOVE_REDRAW_MS)

    def _drag_beats_pan_threshold(self, pos) -> bool:
        """True when the pointer has left the press point far enough that this
        is a pan, not a click (see PAN_DRAG_PX)."""
        return not is_step_too_small(
            pos.x() - self._press_pos.x(),
            pos.y() - self._press_pos.y(),
            self.PAN_DRAG_PX,
        )

    def _start_drag_pan(self, event) -> None:
        """Turn the current left-drag into a map pan. The in-progress vertices
        stay put: rubber bands and markers are canvas items, so they travel
        with the map contents."""
        self._press_pos = None
        self._is_panning = True
        self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        self.canvas().panAction(event)

    def _draw_cursor_preview(self, pos) -> None:
        """Redraw the trailing edge and the live fill for one cursor position."""
        self._drawn_move_pos = pos
        self._can_close = self._near_first(pos)
        cursor = self._points[0] if self._can_close else self.toMapCoordinates(pos)
        # Dashed edge to the cursor (snaps to the first vertex when closable).
        self._preview_band.setToGeometry(
            QgsGeometry.fromPolylineXY([self._points[-1], cursor]), None
        )
        # Live ring that rubber-bands with the cursor (from the 2nd placed
        # vertex on, counting the cursor as the next one).
        self._draw_ring(self._points + [cursor])
        self._highlight_first(self._can_close)

    def _flush_pending_move(self) -> None:
        """Draw the newest cursor position the throttle skipped, if any."""
        pos = self._pending_move_pos
        if pos is None or self._is_panning or self._has_zone or not self._points:
            return
        if pos == self._drawn_move_pos:
            return
        self._draw_cursor_preview(pos)
        self._move_timer.start(self.MOVE_REDRAW_MS)

    def canvasReleaseEvent(self, event):
        if event.button() == QtC.RightButton:
            if self._pending_context_menu:
                self._pending_context_menu = False
                self._show_zone_context_menu(event)
            else:
                # Right-click is deliberately inert mid-draw (it used to
                # finish AI Segmentation's tool, which fired on accidental
                # context-clicks): consume the event so nothing else reacts.
                event.accept()
            return
        if event.button() != QtC.LeftButton:
            return
        self._press_pos = None
        if self._is_panning:
            self._is_panning = False
            self.canvas().panActionEnd(QtC.event_pos(event))
            self._refresh_cursor()
            return
        if self._has_zone or self._locked:
            return
        pos = QtC.event_pos(event)
        # Recomputed from the release point: the move preview is throttled, so
        # the last position it saw can be a few pixels stale by now.
        self._can_close = self._near_first(pos)
        if self._can_close and len(self._points) >= self.MIN_VERTICES:
            self._finish()
            return
        self._add_point(self.toMapCoordinates(pos), pos)

    def canvasDoubleClickEvent(self, event):
        # The two single clicks of the double-click are deduped by
        # MIN_STEP_PX in canvasReleaseEvent, so by here the real vertices are
        # already in; just finish.
        if self._has_zone or self._locked:
            return
        if event.button() == QtC.LeftButton:
            self._finish()

    # -- Keyboard ---------------------------------------------------------

    def keyPressEvent(self, event):
        if self._has_zone:
            # No keys handled once committed: Escape is the dock's global
            # QShortcut. Ignoring (not accepting) lets the canvas keep its
            # own keyboard handling (hold-Space pan, arrow-key scroll).
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
            # Nothing to undo: leave the tool. In practice the dock's global
            # Escape shortcut (WindowShortcut) wins this key before it ever
            # reaches here; this covers direct calls / tests and any focus
            # edge case where it doesn't.
            self._canvas.unsetMapTool(self)

    # -- Vertex bookkeeping -------------------------------------------------

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
        self._add_marker(map_pt, first=len(self._points) == 1)
        self._draw_edges()
        self._draw_ring(self._points)

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

    def _finish(self) -> None:
        pts = list(self._points)
        if len(pts) < self.MIN_VERTICES:
            return  # not enough vertices yet, keep drawing
        geom = QgsGeometry.fromPolygonXY([pts])
        self._reset_draw_visuals()
        self._points = []
        self._commit_polygon(geom)

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
            marker.setFillColor(QColor(255, 255, 255))
        except (AttributeError, TypeError):
            pass  # older builds: outline-only marker is still clearly visible
        marker.setPenWidth(3)
        # The first vertex is a touch bigger: it is the one you click to close.
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
        # The bands are None after cleanup() (scene items removed at unload);
        # a late deactivate() must stay a no-op on them.
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
        self._pending_move_pos = None
        self._drawn_move_pos = None
        if self._move_timer is not None:
            self._move_timer.stop()

    # -- Zone derivation on close --------------------------------------------

    def _commit_polygon(self, geom: QgsGeometry) -> None:
        """Repair the closed shape, expand its bbox to a supported frame, and
        validate that frame exactly like today's rectangle zone (spec section
        3: makeValid repair, ratio + min-size expansion, then validate_zone on
        the EXPANDED bbox)."""
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
        except Exception:  # nosec B110
            pass

        self._has_zone = True
        self._zone_rect = expanded
        self._zone_polygon = repaired
        self.selection_made.emit(expanded, repaired)
        self._show_delete_badge()
        self._refresh_cursor()

    def set_zone(self, rect: QgsRectangle) -> None:
        """Install a zone programmatically (restoring a past generation).
        Stores the rect, shows the delete badge, and switches to pan-mode
        cursor, exactly as a freshly drawn zone would. No polygon: callers of
        this path (history restore, MCP/dev extents, the pills self-heal in
        tool_panels.py) never carry one, so the committed zone renders as a
        plain rectangle with no context frame."""
        self._reset_draw_visuals()
        self._points = []
        self._has_zone = True
        self._zone_rect = QgsRectangle(rect)
        self._zone_polygon = None
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
            self._zone_polygon = None
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
        # Parented to the canvas: an unparented top-level menu does not inherit
        # the QGIS main-window palette or stylesheet on Windows. deleteLater
        # because the parent would otherwise keep one menu per right-click.
        menu = QMenu(self._canvas)
        menu.addAction(tr("Clear zone"), self._on_delete_zone)
        menu.exec(pos)
        menu.deleteLater()

    def _on_delete_zone(self) -> None:
        self._has_zone = False
        self._zone_rect = None
        self._zone_polygon = None
        self._hide_delete_badge()
        self.hide_action_badges()
        self._refresh_cursor()
        self.zone_delete_requested.emit()

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
        # The in-progress drawing bands are always discarded. Zone state, the
        # × badge and the persistent outline only survive when the plugin
        # explicitly asked us to keep them (i.e. switching to a Mark up
        # tool). Otherwise we drop them so unrelated map-tool switches (pan,
        # measure, other plugins) don't leave AI-Edit overlays behind.
        self._reset_draw_visuals()
        self._points = []
        if getattr(self, "_preserve_on_deactivate", False):
            # A preserved switch (Compare / Mark up): keep the zone, the badge
            # state and the action pills alive. Compare relies on this so the
            # pills stay live while the swipe owns the canvas (the × itself is
            # hidden for the comparison's duration, see set_compare_active);
            # Mark up already pre-hides the pills at panel entry, so nothing
            # lingers there.
            self._preserve_on_deactivate = False
        else:
            # A real tool change (pan, measure, another plugin): drop our
            # overlays so nothing hangs on the canvas.
            self._has_zone = False
            self._zone_rect = None
            self._zone_polygon = None
            self._hide_delete_badge()
            self.hide_action_badges()
        super().deactivate()

    def cleanup(self) -> None:
        """Detach from the canvas before the plugin unloads."""
        if self._move_timer is not None:
            self._move_timer.stop()
            self._move_timer = None
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
        # The three persistent rubber bands are created in __init__ and only
        # ever reset()-emptied. Remove them from the scene too, or invisible
        # items pile up across plugin reloads.
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

    # -- delete-badge overlay --------------------------------------------------

    def _badge_anchor(self) -> QgsPointXY:
        """Where the × badge (and the pills beside it) attach: the polygon
        vertex nearest the bbox's top-right corner, so the badge sits ON the
        drawn outline instead of floating at a bbox corner a slanted shape
        never touches. Rectangle-only zones (history restore) have no polygon
        and keep the bbox corner, which lies on their outline anyway."""
        corner = QgsPointXY(
            self._zone_rect.xMaximum(), self._zone_rect.yMaximum()
        )
        if self._zone_polygon is None or self._zone_polygon.isEmpty():
            return corner
        best, best_d = None, float("inf")
        for vertex in self._zone_polygon.vertices():
            point = QgsPointXY(vertex.x(), vertex.y())
            d = corner.sqrDist(point)
            if d < best_d:
                best, best_d = point, d
        return best if best is not None else corner

    def _show_delete_badge(self) -> None:
        # Never while a comparison is live: the × sits right beside the Compare
        # pill, and users pressed it expecting to leave the comparison when it
        # actually tore the whole zone down. The pill is the only exit on the
        # canvas for as long as the comparison runs.
        if self._zone_rect is None or self._compare_active:
            return
        if self._delete_badge is None:
            self._delete_badge = _ZoneDeleteBadge(self.canvas())
        self._delete_badge.set_anchor(self._badge_anchor())
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

        Called by the plugin once a generation completes (and again on
        re-show). ``compare`` is gated on swipe eligibility; ``vectorize`` on
        the run being a detection / segmentation template. No-op without a zone
        rectangle.
        """
        if self._zone_rect is None:
            return
        if compare and self._compare_badge is None:
            self._compare_badge = _ZoneActionBadge(
                self.canvas(), "compare", tr("Compare")
            )
            self._compare_badge.setToolTip(tr("Before / after"))
        if vectorize and self._vectorize_badge is None:
            self._vectorize_badge = _ZoneActionBadge(
                self.canvas(), "vectorize", tr("Vectorize")
            )
        top_right = self._badge_anchor()
        # Lay the visible pills out leftward from the corner: Compare nearest
        # the × badge, then Vectorize. Offsets are pill-centre distances.
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
        # A re-show that drops the Compare pill (result no longer swipe-able)
        # must give the × back, or the zone would be left with no way out.
        self._sync_delete_badge_with_compare()

    def hide_action_badges(self) -> None:
        if self._compare_badge is not None:
            self._compare_badge.hide()
        if self._vectorize_badge is not None:
            self._vectorize_badge.hide()
        # No Compare pill on the canvas means no comparison the user could be
        # in: whatever route got here, the × comes back (no-op without a zone).
        self._sync_delete_badge_with_compare()

    def set_compare_active(self, active: bool) -> None:
        """Reflect the live compare state on the Compare pill (pressed look)
        and take the × badge off the canvas for the comparison's duration.

        The × is anchored to the zone corner with the pills laid out to its
        left, so the two read as one toolbar and users pressed × expecting to
        leave the comparison, which instead cleared the zone and tore the whole
        session down. While a comparison runs the pill itself is the exit (a
        second click on it, or Escape), and the × is not on screen at all.

        Every route out of a comparison lands here with ``active=False`` or in
        :meth:`hide_action_badges`, and both restore the × through
        :meth:`_sync_delete_badge_with_compare`.
        """
        if self._compare_badge is not None:
            self._compare_badge.set_active(active)
            self._compare_badge.setToolTip(
                tr("Click to exit the comparison") if active else tr("Before / after")
            )
        self._compare_active = bool(active)
        self._sync_delete_badge_with_compare()

    def _sync_delete_badge_with_compare(self) -> None:
        """Hide the × only while a VISIBLE Compare pill offers the way out.

        The invariant this holds: a zone on the canvas always has either the ×
        or a live Compare pill, never neither. Anything that takes the pill
        away (a new generation locking the tool, a tool panel, an ineligible
        re-show) therefore drops the compare state as well and brings the ×
        back.
        """
        live = (
            self._compare_active
            and self._compare_badge is not None
            and self._compare_badge.isVisible()
        )
        self._compare_active = live
        if live:
            self._hide_delete_badge()
        else:
            self._show_delete_badge()

    def overlay_hit(self, canvas_pt) -> str | None:
        """Hit-test the action pills at a canvas-pixel point, NO side effects.

        Returns "vectorize" / "compare" for the pill under the point, else
        None. Used by the swipe tool (via a plugin callback) to keep the pills
        clickable while a comparison owns the canvas. Pure hit-test so the
        caller can defer the actual action out of the event loop and avoid
        swapping the map tool mid-event.

        "delete" stays in the return contract but cannot come out of a live
        comparison any more: the × is hidden for its whole duration and
        ``hit_test`` gates on ``isVisible()``.
        """
        if self._vectorize_badge is not None and self._vectorize_badge.hit_test(canvas_pt):
            return "vectorize"
        if self._compare_badge is not None and self._compare_badge.hit_test(canvas_pt):
            return "compare"
        if self._delete_badge is not None and self._delete_badge.hit_test(canvas_pt):
            return "delete"
        return None
