"""Mark up - on-canvas drawing tools and memory layer manager.

Stores annotations (Pencil strokes, Arrows, Circles) in a single
LineString ``QgsVectorLayer`` (memory provider) so the existing
``CanvasExporter`` automatically includes them in the PNG sent to the
AI. Lines are rendered with a thin stroke so the annotation looks like
a real hand-drawn mark, not a thick filled polygon.

Geometry per shape:

* Pencil - single LineString of the dragged cursor positions.
* Arrow - MultiLineString of 3 segments (shaft + two head sides).
* Circle - closed LineString tracing an ellipse boundary (no donut).

The marks are rendered directly onto the image sent to the model; the
pre-prompt treats hand-drawn strokes / arrows / circles as pointers to
where the edit applies and removes them from the result. The default
color is a neon magenta that is never a map class color.

The click-per-vertex Line tool lives in the sibling module
``markup_line_tool.py`` (its ``LineMapTool`` still subclasses
``_MarkupBaseMapTool`` from here), split out to keep both files in the
repo's line-count comfort zone. Import it directly from its own module,
the same convention as ``eyedropper_tool.py`` / ``selection_map_tool.py``.
"""
from __future__ import annotations

import math

from qgis.core import (
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsLineSymbol,
    QgsPointXY,
    QgsProject,
    QgsProperty,
    QgsRectangle,
    QgsSingleSymbolRenderer,
    QgsSymbolLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapCanvas, QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeySequence

from ...core import qt_compat as QtC
from ...core.canvas_export.render_set import expand_render_set
from ...core.config_store import get_export_dial, get_export_dial_str
from ...core.logger import log_debug
from ..layer_groups import MARKUP_LAYER_PROPERTY, drop_from_snapping

MARKUP_LAYER_NAME = "AI Edit guidance markup"
_MARKUP_PROPERTY = MARKUP_LAYER_PROPERTY


def _symbol_property(name: str):
    """Return ``QgsSymbolLayer.Property.<name>`` falling back to the
    legacy unscoped attribute. Works on QGIS 3.x and QGIS 4.x."""
    scope = getattr(QgsSymbolLayer, "Property", None)
    if scope is not None:
        val = getattr(scope, name, None)
        if val is not None:
            return val
    return getattr(QgsSymbolLayer, name)


# Stroke width in screen pixels. Bold enough that Nano Banana reads the
# stroke as a pointer instead of mistaking it for a thin feature on the
# underlying map.
STROKE_WIDTH_PX = 4.5

# Default annotation color: a neon magenta that is never a map class color.
# Red is avoided on purpose - the segmentation mode reads a pure-red fill as
# "color this target red #FF0000", so a red mark would be misread as a fill.
MARKUP_DEFAULT_COLOR = (230, 0, 230)

_HEX_COLOR_DIGITS = frozenset("0123456789abcdefABCDEF")


def markup_stroke_width_px() -> float:
    """Server-tunable stroke width, read at draw time so a config that lands
    mid-session applies to the next mark."""
    return get_export_dial("markup.stroke_width_px", STROKE_WIDTH_PX)


def markup_default_color() -> tuple[int, int, int]:
    """Server-tunable default mark color, served as "#RRGGBB". Anything that
    is not exactly a 6-digit hex color reads as the shipped magenta."""
    served = get_export_dial_str("markup.default_color", "").lstrip("#")
    if len(served) != 6 or any(c not in _HEX_COLOR_DIGITS for c in served):
        return MARKUP_DEFAULT_COLOR
    return (int(served[0:2], 16), int(served[2:4], 16), int(served[4:6], 16))


def _stroke_color_value(color: QColor) -> str:
    return f"{color.red()},{color.green()},{color.blue()},255"


class MarkupLayerManager(QObject):
    """Owns the LineString memory layer that stores Mark up annotations.

    Lifecycle: created lazily on first commit, then reused for every
    subsequent annotation across sessions. The layer persists across
    Markup exit / Generate / new zone selection; only Clear all (or the
    plugin unload) drops it.
    """

    annotation_count_changed = pyqtSignal(int)
    # Emitted when a stroke lands entirely outside the selected zone, so the
    # plugin can surface a "draw inside the zone" notice. The mark itself is
    # dropped (markup is only meaningful inside the generated zone).
    outside_zone_attempted = pyqtSignal()

    def __init__(self, canvas: QgsMapCanvas, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._canvas = canvas
        self._layer: QgsVectorLayer | None = None
        self._next_markup_id = 1
        # Selected-zone rectangle (canvas/map CRS). When set, every committed
        # mark is clipped to it; a mark fully outside is rejected.
        self._clip_zone: QgsRectangle | None = None
        # Selected-zone polygon (canvas/map CRS), when the zone was drawn with
        # the polygon tool. When present, clipping uses this shape instead of
        # the bbox, so a mark inside the bbox but outside the polygon is
        # rejected too. None on rect-only paths (history restore, MCP/dev).
        self._clip_polygon: QgsGeometry | None = None
        # Drop our layer reference whenever QGIS tears it down so we never
        # call methods on a dead C++ wrapper.
        project = QgsProject.instance()
        project.layersWillBeRemoved.connect(self._on_layers_removed)
        project.cleared.connect(self._on_project_cleared)
        try:
            project.crsChanged.connect(self._on_project_crs_changed)
        except (TypeError, RuntimeError):
            pass

    def _on_project_cleared(self) -> None:
        if self._layer is not None:
            self._layer = None
            try:
                self.annotation_count_changed.emit(0)
            except RuntimeError:
                pass

    def _on_project_crs_changed(self) -> None:
        if self._layer is not None:
            log_debug("Mark up: project CRS changed, rebuilding layer")
            # Remove the stale layer, don't just drop the reference: nulling it
            # alone orphans the memory layer (wrong CRS) in the project forever.
            # Deferred one event-loop turn: QgsProject applies its stored CRS
            # (emitting crsChanged) WHILE a project is still being restored, so
            # removing a layer synchronously here races the snapping-config
            # restore and leaves a dangling layer pointer in QgsSnappingConfig,
            # which then crashes the NEXT project save (often the save-on-exit;
            # upstream qgis/QGIS#37505, #42651).
            QtC.safe_single_shot(0, self, self.remove_layer)

    def _on_layers_removed(self, layer_ids: list[str]) -> None:
        if self._layer is None:
            return
        try:
            our_id = self._layer.id()
        except RuntimeError:
            self._layer = None
            return
        if our_id in layer_ids:
            self._layer = None
            self.annotation_count_changed.emit(0)

    def _alive(self) -> bool:
        if self._layer is None:
            return False
        try:
            return self._layer.isValid()
        except RuntimeError:
            self._layer = None
            return False

    # --- layer lifecycle ------------------------------------------------

    def _ensure_layer(self) -> QgsVectorLayer:
        if self._alive():
            return self._layer
        crs = QgsProject.instance().crs()
        uri = (
            f"MultiLineString?crs={crs.authid()}"
            "&field=id:integer"
            "&field=color:string(20)"
            "&field=shape:string(20)"
            "&field=created_at:string(25)"
            "&field=notes:string(255)"
        )
        layer = QgsVectorLayer(uri, MARKUP_LAYER_NAME, "memory")
        if not layer.isValid():
            raise RuntimeError("Failed to create Mark up memory layer")
        self._next_markup_id = 1
        layer.setCustomProperty("skipMemoryLayersCheck", 1)
        layer.setCustomProperty(_MARKUP_PROPERTY, True)
        self._apply_style(layer)
        # Show the guidance markup on top of the AI-Edit group so the user
        # sees their strokes immediately instead of having them hidden under
        # the opaque rasters; addMapLayer(False) blocks auto-insertion at the
        # root.
        QgsProject.instance().addMapLayer(layer, False)
        # Keep this scratch layer out of the snapping config: a dangling
        # pointer there crashes the NEXT project save (see drop_from_snapping).
        drop_from_snapping(layer)
        # Markup sits at the very top of the tree, above the AI-Edit group, so
        # its annotations always render over the generated rasters. Inside the
        # group an opaque output layer would hide them.
        QgsProject.instance().layerTreeRoot().insertLayer(0, layer)
        self._layer = layer
        log_debug(f"Mark up: layer created (crs={crs.authid()})")
        return layer

    @staticmethod
    def _apply_style(layer: QgsVectorLayer) -> None:
        symbol = QgsLineSymbol.createSimple(
            {
                "line_color": _stroke_color_value(QColor(*markup_default_color())),
                "line_width": str(markup_stroke_width_px()),
                "line_width_unit": "Pixel",
                "capstyle": "round",
                "joinstyle": "round",
            }
        )
        sl = symbol.symbolLayer(0)
        sl.setDataDefinedProperty(
            _symbol_property("PropertyStrokeColor"),
            QgsProperty.fromExpression('"color"'),
        )
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    def layer(self) -> QgsVectorLayer | None:
        return self._layer

    def markup_will_render(self) -> bool:
        """Main-thread predicate mirroring the render-set/visibility check the
        off-thread export applies in core/canvas_export/render.py: true only
        when the layer is alive AND part of the canvas's current render set
        (checked and visible in the layer tree). Call this right before
        submitting a generation, never after, since the export runs later on
        a worker thread where a fresh check is too late for the UI."""
        if not self._alive():
            return False
        try:
            layer_id = self._layer.id()
        except RuntimeError:
            self._layer = None
            return False
        # Expand first: a group rendered "as a group" hands the canvas ONE
        # QgsGroupLayer proxy and hides its members' ids, so a markup layer
        # inside such a group would read as "will not render" and abort the
        # generation with no message.
        render_ids = {
            lyr.id() for lyr in expand_render_set(self._canvas.mapSettings().layers())
        }
        return layer_id in render_ids

    def show_layer(self) -> bool:
        """Re-show the layer so it renders again: check its own and its
        parent groups' visibility boxes. Returns whether the layer now
        renders. False when there is nothing left to show, either the
        reference is stale or the layer's node was removed from the tree
        entirely: QGIS deletes a layer with no tree node on the next
        event-loop turn regardless of a fresh re-insert, so a node-less
        layer here is already unrecoverable, not a lesser case to patch
        around."""
        if not self._alive():
            return False
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(self._layer.id())
        if node is None:
            return False
        ancestor = node
        while ancestor is not None and ancestor is not root:
            try:
                ancestor.setItemVisibilityChecked(True)
            except RuntimeError:
                break
            ancestor = ancestor.parent()
        try:
            self._canvas.refreshAllLayers()
        except RuntimeError:  # nosec B110 - C++ canvas gone
            pass
        # The layer tree -> canvas bridge updates the canvas's render set
        # through a queued connection (QGIS coalesces rapid visibility
        # toggles into one canvas rebuild per event-loop turn). Drain it here
        # so the check right below, and the one the caller makes right after
        # to decide whether to submit, both see the layer we just re-showed
        # instead of a stale render set.
        try:
            QgsApplication.instance().processEvents()
        except Exception:  # nosec B110 - best-effort sync, never fatal
            pass
        return self.markup_will_render()

    def annotation_count(self) -> int:
        if not self._alive():
            return 0
        try:
            return self._layer.featureCount()
        except RuntimeError:
            self._layer = None
            return 0

    # --- commit / undo / clear -----------------------------------------

    def set_clip_zone(
        self, rect: QgsRectangle | None, polygon: QgsGeometry | None = None
    ) -> None:
        """Constrain marks to the selected zone (canvas/map CRS). Pass None for
        both to lift the constraint (no zone selected).

        ``polygon``, when given, is the actual clip shape: a mark inside
        ``rect`` but outside the polygon is rejected too. ``rect`` still tracks
        the bbox for callers that only ever pass a rectangle (history restore,
        MCP/dev extents), which keeps the plain bbox clip they have always had.
        """
        self._clip_zone = QgsRectangle(rect) if rect is not None else None
        self._clip_polygon = (
            QgsGeometry(polygon) if polygon is not None and not polygon.isEmpty() else None
        )

    def _clip_to_zone(self, geometry: QgsGeometry) -> QgsGeometry | None:
        """Clip a mark to the selected zone: overflow is cut at the zone edge.
        Uses the polygon when one is set, the bbox otherwise. Returns None when
        the mark lies entirely outside the zone."""
        if self._clip_polygon is not None:
            clip_shape = self._clip_polygon
        elif self._clip_zone is not None and not self._clip_zone.isEmpty():
            clip_shape = QgsGeometry.fromRect(self._clip_zone)
        else:
            return geometry
        clipped = geometry.intersection(clip_shape)
        if clipped is None or clipped.isEmpty():
            return None
        # The store layer is MultiLineString; keep the clipped result that type.
        clipped.convertToMultiType()
        return clipped

    def commit(self, geometry: QgsGeometry, color: QColor, shape: str) -> None:
        if geometry.isEmpty():
            return
        geometry = self._clip_to_zone(geometry)
        if geometry is None:
            self.outside_zone_attempted.emit()
            return
        import time as _time
        layer = self._ensure_layer()
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geometry)
        feat.setAttribute("id", self._next_markup_id)
        self._next_markup_id += 1
        feat.setAttribute("color", _stroke_color_value(color))
        feat.setAttribute("shape", shape)
        feat.setAttribute(
            "created_at", _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
        )
        layer.dataProvider().addFeature(feat)
        layer.updateExtents()
        layer.triggerRepaint()
        self.annotation_count_changed.emit(layer.featureCount())

    def undo_last(self) -> bool:
        if not self._alive():
            return False
        try:
            ids = [f.id() for f in self._layer.getFeatures()]
            if not ids:
                return False
            # Memory provider hands out monotonically increasing IDs; the max
            # is always the most recently added feature regardless of iteration order.
            last_id = max(ids)
            self._layer.dataProvider().deleteFeatures([last_id])
            self._layer.updateExtents()
            self._layer.triggerRepaint()
            self.annotation_count_changed.emit(self._layer.featureCount())
            return True
        except RuntimeError:
            self._layer = None
            self.annotation_count_changed.emit(0)
            return False

    def clear_all(self) -> None:
        if not self._alive():
            return
        try:
            provider = self._layer.dataProvider()
            ids = [f.id() for f in self._layer.getFeatures()]
            if ids:
                provider.deleteFeatures(ids)
            self._layer.updateExtents()
            self._layer.triggerRepaint()
        except RuntimeError:
            self._layer = None
        self.annotation_count_changed.emit(0)

    def remove_layer(self) -> None:
        """Drop the layer from the project. Re-created lazily on next commit."""
        if self._layer is None:
            return
        try:
            layer_id = self._layer.id()
            QgsProject.instance().removeMapLayer(layer_id)
        except (RuntimeError, KeyError):
            pass
        self._layer = None
        # Force a canvas redraw: removeMapLayer alone leaves the stroke
        # cached on the canvas scene until the next user-driven render.
        try:
            self._canvas.refreshAllLayers()
        except RuntimeError:  # pragma: no cover - C++ canvas gone  # nosec B110
            pass
        self.annotation_count_changed.emit(0)

    def disconnect_signals(self) -> None:
        """Drop our QgsProject signal connections. Call before discarding the
        manager. All three signals connected in __init__ must be released, or
        QgsProject (a process-long singleton) keeps firing into a dead manager
        and stacks a stale handler on every plugin reload."""
        project = QgsProject.instance()
        for signal, slot in (
            (project.layersWillBeRemoved, self._on_layers_removed),
            (project.cleared, self._on_project_cleared),
            (project.crsChanged, self._on_project_crs_changed),
        ):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):  # nosec B110
                pass


# ----------------------------------------------------------------------
# Map tools - Pencil / Arrow / Circle
# ----------------------------------------------------------------------


class _MarkupBaseMapTool(QgsMapTool):
    """Shared plumbing for Mark up map tools."""

    def __init__(
        self,
        canvas: QgsMapCanvas,
        manager: MarkupLayerManager,
        shape: str,
    ) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self._manager = manager
        self._shape = shape
        self._color = QColor(*markup_default_color())
        self._rubber: QgsRubberBand | None = None
        self._active = False
        self.setCursor(QtC.CrossCursor)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)

    def _begin_rubber(self, geometry_type=None) -> QgsRubberBand:
        gtype = geometry_type if geometry_type is not None else QtC.LineGeometry
        rb = QgsRubberBand(self._canvas, gtype)
        rb.setStrokeColor(QColor(self._color))
        rb.setColor(QColor(self._color))
        rb.setWidth(markup_stroke_width_px())
        return rb

    def _discard_rubber(self) -> None:
        if self._rubber is not None:
            try:
                self._canvas.scene().removeItem(self._rubber)
            except RuntimeError:
                pass
            self._rubber = None

    def deactivate(self) -> None:  # noqa: D401
        self._discard_rubber()
        self._active = False
        super().deactivate()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == QtC.Key_Escape and self._active:
            self._discard_rubber()
            self._active = False
            event.accept()
            return
        # Cmd/Ctrl+Z while the canvas has focus: undo the last annotation.
        # The map tool sees this BEFORE QGIS's main-window event filter (key
        # events don't bubble to parents), which is why the dock-level filter
        # alone misses the very first strokes after the panel opens.
        if event.matches(QKeySequence.StandardKey.Undo):
            self._manager.undo_last()
            event.accept()
            return
        # Keys we don't handle: ignore so the canvas keeps its keyboard nav
        # (hold-Space temporary pan, arrow-key scroll). Calling super() leaves
        # the event accepted and suppresses that.
        event.ignore()


class PencilMapTool(_MarkupBaseMapTool):
    """Freehand stroke: capture cursor positions during drag, commit on release.

    The raw cursor path carries hand tremor, which used to reach the guidance
    image as a shaky line. Every build cleans the stroke (see _smoothed_points):
    a moving average low-passes the jitter, then a light simplify drops the
    redundant vertices. Both the live preview and the committed geometry run
    through it, so what the user draws, sees, and sends is the same clean line.
    """

    # Tremor cleanup dials. SMOOTH_WINDOW is the half-width of the moving
    # average, in samples: each output point is the mean of up to 2*w+1 raw
    # points centred on it, so per-sample shake cancels while a deliberate
    # curve (which moves the same way across many samples) survives. Measured
    # on synthetic strokes, w=4 takes ~3px of tremor down to ~0.85px along the
    # stroke and leaves a real arc (40px) or a sharp corner (30px) intact. A
    # plain moving average beats Chaikin here: Chaikin interpolates between
    # vertices, so it barely touches high-frequency tremor (3.2px to 3.85px in
    # the same test) and multiplies the vertex count into the hundreds.
    # SIMPLIFY_PX then trims the smoothed run back to a lean polyline; it is in
    # screen pixels (via mapUnitsPerPixel) so it holds at any zoom.
    _SMOOTH_WINDOW = 4
    _SIMPLIFY_PX = 1.2

    def __init__(self, canvas: QgsMapCanvas, manager: MarkupLayerManager) -> None:
        super().__init__(canvas, manager, shape="pencil")
        self._points: list[QgsPointXY] = []

    def canvasPressEvent(self, event):  # noqa: N802
        if event.button() != QtC.LeftButton:
            return
        self._points = [self.toMapCoordinates(QtC.event_pos(event))]
        self._discard_rubber()
        self._rubber = self._begin_rubber(QtC.LineGeometry)
        self._active = True

    def canvasMoveEvent(self, event):  # noqa: N802
        if not self._active or self._rubber is None:
            return
        pt = self.toMapCoordinates(QtC.event_pos(event))
        if self._points and pt == self._points[-1]:
            return
        self._points.append(pt)
        geom = self._build_geometry()
        if geom is not None and not geom.isEmpty():
            self._rubber.setToGeometry(geom, None)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if not self._active or event.button() != QtC.LeftButton:
            return
        self._active = False
        geom = self._build_geometry()
        self._discard_rubber()
        if geom is not None and not geom.isEmpty():
            self._manager.commit(geom, self._color, self._shape)
        self._points = []

    def _build_geometry(self) -> QgsGeometry | None:
        pts = self._smoothed_points()
        if pts is None:
            return None
        line = QgsGeometry.fromPolylineXY(pts)
        if line.isEmpty():
            return None
        # Light Douglas-Peucker to drop the near-collinear points the average
        # leaves behind. Falls back to the smoothed line if simplify empties it.
        simplified = line.simplify(self._canvas.mapUnitsPerPixel() * self._SIMPLIFY_PX)
        if simplified is None or simplified.isEmpty():
            return line
        return simplified

    def _smoothed_points(self) -> list[QgsPointXY] | None:
        """Moving-average low-pass over the raw cursor path.

        Each point becomes the mean of its neighbours within SMOOTH_WINDOW, so
        per-sample tremor cancels while a deliberate curve is kept. The window
        stays centred on the point and shrinks near the ends (half-width 0 at
        the first and last sample), which does two things: the average never
        leans to one side and drags the line inward, and the two endpoints keep
        their exact position, so the stroke still starts and ends on the cursor.
        Under four points there is nothing to average, so the raw path passes
        through.
        """
        n = len(self._points)
        if n < 2:
            return None
        if n < 4:
            return list(self._points)
        smoothed: list[QgsPointXY] = []
        for i in range(n):
            half = min(self._SMOOTH_WINDOW, i, n - 1 - i)
            window = self._points[i - half:i + half + 1]
            count = len(window)
            smoothed.append(
                QgsPointXY(
                    sum(p.x() for p in window) / count,
                    sum(p.y() for p in window) / count,
                )
            )
        return smoothed


class ArrowMapTool(_MarkupBaseMapTool):
    """Drag-to-arrow: press = start, drag = preview, release = commit.

    Head is deliberately oversized so the tip reads as the pointer target
    even after the canvas is rasterised to PNG at the model's input size.
    """

    HEAD_LEN_PX = 32.0
    HEAD_ANGLE_DEG = 32.0

    def __init__(self, canvas: QgsMapCanvas, manager: MarkupLayerManager) -> None:
        super().__init__(canvas, manager, shape="arrow")
        self._start: QgsPointXY | None = None

    def canvasPressEvent(self, event):  # noqa: N802
        if event.button() != QtC.LeftButton:
            return
        self._start = self.toMapCoordinates(QtC.event_pos(event))
        self._discard_rubber()
        self._rubber = self._begin_rubber(QtC.LineGeometry)
        self._active = True

    def canvasMoveEvent(self, event):  # noqa: N802
        if not self._active or self._start is None or self._rubber is None:
            return
        end = self.toMapCoordinates(QtC.event_pos(event))
        geom = self._arrow_geometry(self._start, end)
        if not geom.isEmpty():
            self._rubber.setToGeometry(geom, None)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if not self._active or self._start is None or event.button() != QtC.LeftButton:
            return
        self._active = False
        end = self.toMapCoordinates(QtC.event_pos(event))
        geom = self._arrow_geometry(self._start, end)
        self._discard_rubber()
        if not geom.isEmpty():
            self._manager.commit(geom, self._color, self._shape)
        self._start = None

    def _arrow_geometry(self, start: QgsPointXY, end: QgsPointXY) -> QgsGeometry:
        return _arrow_multiline(
            start,
            end,
            head_len_map=self.HEAD_LEN_PX * self._canvas.mapUnitsPerPixel(),
            head_angle_rad=math.radians(self.HEAD_ANGLE_DEG),
        )


class CircleMapTool(_MarkupBaseMapTool):
    """Drag-to-ellipse: bounding box from press to release.

    Stored as a closed LineString tracing the ellipse boundary so the
    rendered stroke is a thin clean ring (no donut fill).
    """

    SEGMENTS = 72

    def __init__(self, canvas: QgsMapCanvas, manager: MarkupLayerManager) -> None:
        super().__init__(canvas, manager, shape="circle")
        self._anchor: QgsPointXY | None = None

    def canvasPressEvent(self, event):  # noqa: N802
        if event.button() != QtC.LeftButton:
            return
        self._anchor = self.toMapCoordinates(QtC.event_pos(event))
        self._discard_rubber()
        self._rubber = self._begin_rubber(QtC.LineGeometry)
        self._active = True

    def canvasMoveEvent(self, event):  # noqa: N802
        if not self._active or self._anchor is None or self._rubber is None:
            return
        cur = self.toMapCoordinates(QtC.event_pos(event))
        geom = _ellipse_ring(self._anchor, cur, self.SEGMENTS)
        if not geom.isEmpty():
            self._rubber.setToGeometry(geom, None)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if not self._active or self._anchor is None or event.button() != QtC.LeftButton:
            return
        self._active = False
        cur = self.toMapCoordinates(QtC.event_pos(event))
        geom = _ellipse_ring(self._anchor, cur, self.SEGMENTS)
        self._discard_rubber()
        if not geom.isEmpty():
            self._manager.commit(geom, self._color, self._shape)
        self._anchor = None


# ----------------------------------------------------------------------
# Geometry builders
# ----------------------------------------------------------------------


def _arrow_multiline(
    start: QgsPointXY,
    end: QgsPointXY,
    head_len_map: float,
    head_angle_rad: float,
) -> QgsGeometry:
    """Build a MultiLineString = shaft + two head sides, all same width."""
    dx, dy = end.x() - start.x(), end.y() - start.y()
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return QgsGeometry()
    ux, uy = dx / length, dy / length

    head_len = min(head_len_map, length * 0.6)
    cos_a, sin_a = math.cos(head_angle_rad), math.sin(head_angle_rad)

    # Rotate the back-pointing unit vector by ±head_angle to get head sides.
    bx, by = -ux, -uy
    left_x = bx * cos_a - by * sin_a
    left_y = bx * sin_a + by * cos_a
    right_x = bx * cos_a + by * sin_a
    right_y = -bx * sin_a + by * cos_a

    left_tip = QgsPointXY(end.x() + left_x * head_len, end.y() + left_y * head_len)
    right_tip = QgsPointXY(end.x() + right_x * head_len, end.y() + right_y * head_len)

    return QgsGeometry.fromMultiPolylineXY(
        [
            [start, end],
            [end, left_tip],
            [end, right_tip],
        ]
    )


def _ellipse_ring(
    anchor: QgsPointXY,
    current: QgsPointXY,
    segments: int = 72,
) -> QgsGeometry:
    """Build a closed LineString tracing the ellipse bounded by anchor→current."""
    cx = (anchor.x() + current.x()) / 2.0
    cy = (anchor.y() + current.y()) / 2.0
    rx = abs(current.x() - anchor.x()) / 2.0
    ry = abs(current.y() - anchor.y()) / 2.0
    if rx <= 0 or ry <= 0:
        return QgsGeometry()
    pts: list[QgsPointXY] = []
    for i in range(segments):
        a = 2.0 * math.pi * i / segments
        pts.append(QgsPointXY(cx + rx * math.cos(a), cy + ry * math.sin(a)))
    pts.append(pts[0])
    return QgsGeometry.fromPolylineXY(pts)


# Kept exported for the layer-type check in CanvasExporter.
_ = QgsWkbTypes
