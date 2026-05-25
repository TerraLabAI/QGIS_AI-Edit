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

The pre-prompt already treats red circles / arrows as AOI hints.
"""
from __future__ import annotations

import math

from qgis.core import (
    QgsFeature,
    QgsGeometry,
    QgsLineSymbol,
    QgsPointXY,
    QgsProject,
    QgsProperty,
    QgsSingleSymbolRenderer,
    QgsSymbolLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapCanvas, QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeySequence

from ...core import qt_compat as QtC
from ...core.logger import log_debug
from ..layer_groups import MARKUP_LAYER_PROPERTY

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

    def __init__(self, canvas: QgsMapCanvas, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._canvas = canvas
        self._layer: QgsVectorLayer | None = None
        self._next_markup_id = 1
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
            self._layer = None

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
        # Pin at the bottom of the AI-Edit group; addMapLayer(False) blocks
        # auto-insertion at the root.
        QgsProject.instance().addMapLayer(layer, False)
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
                "line_color": _stroke_color_value(QColor(230, 51, 51)),
                "line_width": str(STROKE_WIDTH_PX),
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

    def annotation_count(self) -> int:
        if not self._alive():
            return 0
        try:
            return self._layer.featureCount()
        except RuntimeError:
            self._layer = None
            return 0

    # --- commit / undo / clear -----------------------------------------

    def commit(self, geometry: QgsGeometry, color: QColor, shape: str) -> None:
        if geometry.isEmpty():
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
        """Drop our QgsProject signal connection. Call before discarding the manager."""
        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(self._on_layers_removed)
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
        self._color = QColor(230, 51, 51)
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
        rb.setWidth(STROKE_WIDTH_PX)
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
        super().keyPressEvent(event)


class PencilMapTool(_MarkupBaseMapTool):
    """Freehand stroke: capture cursor positions during drag, commit on release."""

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
        if len(self._points) < 2:
            return None
        line = QgsGeometry.fromPolylineXY(self._points)
        if line.isEmpty():
            return None
        # Simplify to drop 1000-vertex jitter strokes.
        return line.simplify(self._canvas.mapUnitsPerPixel() * 0.6)


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
        if (
            not self._active or
            self._anchor is None or
            event.button() != QtC.LeftButton
        ):
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
