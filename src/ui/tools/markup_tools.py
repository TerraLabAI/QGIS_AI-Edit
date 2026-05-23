
























from __future__ import annotations

import math
import time

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
from ...core.qt_compat import LineGeometry
from ..layer_groups import MARKUP_LAYER_PROPERTY, drop_from_snapping



MARKUP_LAYER_NAME = "AI Edit drawing"
_MARKUP_PROPERTY = MARKUP_LAYER_PROPERTY


def _symbol_property(name: str):


    scope = getattr(QgsSymbolLayer, "Property", None)
    if scope is not None:
        val = getattr(scope, name, None)
        if val is not None:
            return val
    return getattr(QgsSymbolLayer, name)





STROKE_WIDTH_PX = 4.5




MARKUP_DEFAULT_COLOR = (230, 0, 230)

_HEX_COLOR_DIGITS = frozenset("0123456789abcdefABCDEF")


def markup_stroke_width_px() -> float:


    return get_export_dial("markup.stroke_width_px", STROKE_WIDTH_PX)


def markup_default_color() -> tuple[int, int, int]:


    served = get_export_dial_str("markup.default_color", "").lstrip("#")
    if len(served) != 6 or any(c not in _HEX_COLOR_DIGITS for c in served):
        return MARKUP_DEFAULT_COLOR
    return (int(served[0:2], 16), int(served[2:4], 16), int(served[4:6], 16))


def preview_width_px(stroke_px: float) -> int:



    return max(1, int(math.floor(float(stroke_px) + 0.5)))


def _stroke_color_value(color: QColor) -> str:
    return f"{color.red()},{color.green()},{color.blue()},255"




MIN_STROKE_PX = 4.0

MIN_ARROW_PX = 10.0

MIN_CIRCLE_PX = 8.0

SNAP_ANGLE_DEG = 45.0


def _shift_held(event) -> bool:
    try:
        return bool(event.modifiers() & QtC.ShiftModifier)
    except (AttributeError, TypeError):
        return False


def snap_to_angle(anchor: QgsPointXY, point: QgsPointXY,
                  step_deg: float = SNAP_ANGLE_DEG) -> QgsPointXY:



    dx, dy = point.x() - anchor.x(), point.y() - anchor.y()
    if dx == 0 and dy == 0:
        return QgsPointXY(point)
    step = math.radians(step_deg)
    angle = round(math.atan2(dy, dx) / step) * step
    ux, uy = math.cos(angle), math.sin(angle)
    reach = dx * ux + dy * uy
    return QgsPointXY(anchor.x() + ux * reach, anchor.y() + uy * reach)


def square_corner(anchor: QgsPointXY, current: QgsPointXY) -> QgsPointXY:


    dx, dy = current.x() - anchor.x(), current.y() - anchor.y()
    side = max(abs(dx), abs(dy))
    return QgsPointXY(anchor.x() + math.copysign(side, dx), anchor.y() + math.copysign(side, dy))


class MarkupLayerManager(QObject):








    annotation_count_changed = pyqtSignal(int)



    outside_zone_attempted = pyqtSignal()

    def __init__(self, canvas: QgsMapCanvas, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._canvas = canvas
        self._layer: QgsVectorLayer | None = None
        self._next_markup_id = 1


        self._clip_zone: QgsRectangle | None = None




        self._clip_polygon: QgsGeometry | None = None


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
            log_debug("Draw: project CRS changed, rebuilding layer")








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
            raise RuntimeError("Failed to create the Draw stroke layer")
        self._next_markup_id = 1
        layer.setCustomProperty("skipMemoryLayersCheck", 1)
        layer.setCustomProperty(_MARKUP_PROPERTY, True)
        self._apply_style(layer)




        QgsProject.instance().addMapLayer(layer, False)


        drop_from_snapping(layer)



        QgsProject.instance().layerTreeRoot().insertLayer(0, layer)
        self._layer = layer
        log_debug(f"Draw: layer created (crs={crs.authid()})")
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






        if not self._alive():
            return False
        try:
            layer_id = self._layer.id()
        except RuntimeError:
            self._layer = None
            return False




        render_ids = {
            lyr.id() for lyr in expand_render_set(self._canvas.mapSettings().layers())
        }
        return layer_id in render_ids

    def show_layer(self) -> bool:








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
        except RuntimeError:  # nosec B110
            pass






        try:
            QgsApplication.instance().processEvents()
        except Exception:  # nosec B110
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



    def set_clip_zone(
        self, rect: QgsRectangle | None, polygon: QgsGeometry | None = None
    ) -> None:








        self._clip_zone = QgsRectangle(rect) if rect is not None else None
        self._clip_polygon = (
            QgsGeometry(polygon) if polygon is not None and not polygon.isEmpty() else None
        )

    def _clip_to_zone(self, geometry: QgsGeometry) -> QgsGeometry | None:



        if self._clip_polygon is not None:
            clip_shape = self._clip_polygon
        elif self._clip_zone is not None and not self._clip_zone.isEmpty():
            clip_shape = QgsGeometry.fromRect(self._clip_zone)
        else:
            return geometry
        clipped = geometry.intersection(clip_shape)
        if clipped is None or clipped.isEmpty():
            return None




        if clipped.type() != LineGeometry:
            lines = clipped.convertToType(LineGeometry, True)
            if lines is None or lines.isEmpty():
                return None
            clipped = lines
        clipped.convertToMultiType()
        return clipped

    def commit(self, geometry: QgsGeometry, color: QColor, shape: str) -> None:
        if geometry.isEmpty():
            return
        geometry = self._clip_to_zone(geometry)
        if geometry is None:
            self.outside_zone_attempted.emit()
            return
        try:
            layer = self._ensure_layer()
        except RuntimeError as err:
            log_debug(f"Draw: no stroke layer ({err})")
            return


        self._check_layer_visible(layer)
        feat = QgsFeature(layer.fields())
        feat.setGeometry(geometry)
        feat.setAttribute("id", self._next_markup_id)
        self._next_markup_id += 1
        feat.setAttribute("color", _stroke_color_value(color))
        feat.setAttribute("shape", shape)
        feat.setAttribute(
            "created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )
        if not layer.dataProvider().addFeature(feat):
            log_debug("Draw: the stroke layer refused a stroke")
            return
        layer.updateExtents()
        layer.triggerRepaint()
        self.annotation_count_changed.emit(layer.featureCount())

    @staticmethod
    def _check_layer_visible(layer: QgsVectorLayer) -> None:

        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id())
        while node is not None and node is not root:
            try:
                if not node.itemVisibilityChecked():
                    node.setItemVisibilityChecked(True)
            except RuntimeError:
                return
            node = node.parent()

    def undo_last(self) -> bool:
        if not self._alive():
            return False
        try:
            ids = [f.id() for f in self._layer.getFeatures()]
            if not ids:
                return False


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

        if self._layer is None:
            return
        try:
            layer_id = self._layer.id()
            QgsProject.instance().removeMapLayer(layer_id)
        except (RuntimeError, KeyError):
            pass
        self._layer = None


        try:
            self._canvas.refreshAllLayers()
        except RuntimeError:  # pragma: no cover  # nosec B110
            pass
        self.annotation_count_changed.emit(0)

    def disconnect_signals(self) -> None:




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







class _MarkupBaseMapTool(QgsMapTool):


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


        if self._rubber is not None:
            try:
                self._rubber.setStrokeColor(QColor(self._color))
                self._rubber.setColor(QColor(self._color))
            except RuntimeError:
                self._rubber = None

    def _begin_rubber(self, geometry_type=None) -> QgsRubberBand:
        gtype = geometry_type if geometry_type is not None else QtC.LineGeometry
        rb = QgsRubberBand(self._canvas, gtype)
        rb.setStrokeColor(QColor(self._color))
        rb.setColor(QColor(self._color))
        rb.setWidth(preview_width_px(markup_stroke_width_px()))
        return rb

    def _screen_px(self, map_distance: float) -> float:

        try:
            per_px = self._canvas.mapUnitsPerPixel()
        except RuntimeError:
            return 0.0
        return map_distance / per_px if per_px > 0 else 0.0

    def _discard_rubber(self) -> None:
        if self._rubber is not None:
            try:
                self._canvas.scene().removeItem(self._rubber)
            except RuntimeError:
                pass
            self._rubber = None

    def _cancel_drag(self) -> None:

        self._discard_rubber()
        self._active = False

    def deactivate(self) -> None:  # noqa: D401
        self._cancel_drag()
        super().deactivate()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == QtC.Key_Escape and self._active:
            self._cancel_drag()
            event.accept()
            return




        if event.matches(QKeySequence.StandardKey.Undo):


            if self._active:
                self._cancel_drag()
            else:
                self._manager.undo_last()
            event.accept()
            return



        event.ignore()


class PencilMapTool(_MarkupBaseMapTool):




















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
        geom = self._build_geometry() if self._drag_reach_px() >= MIN_STROKE_PX else None
        self._discard_rubber()
        if geom is not None and not geom.isEmpty():
            self._manager.commit(geom, self._color, self._shape)
        self._points = []

    def _cancel_drag(self) -> None:
        super()._cancel_drag()
        self._points = []

    def _drag_reach_px(self) -> float:

        if len(self._points) < 2:
            return 0.0
        start = self._points[0]
        reach = max(math.hypot(p.x() - start.x(), p.y() - start.y()) for p in self._points)
        return self._screen_px(reach)

    def _build_geometry(self) -> QgsGeometry | None:
        pts = self._smoothed_points()
        if pts is None:
            return None
        line = QgsGeometry.fromPolylineXY(pts)
        if line.isEmpty():
            return None


        simplified = line.simplify(self._canvas.mapUnitsPerPixel() * self._SIMPLIFY_PX)
        if simplified is None or simplified.isEmpty():
            return line
        return simplified

    def _smoothed_points(self) -> list[QgsPointXY] | None:











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
        end = self._end_point(event)
        geom = self._arrow_geometry(self._start, end)
        if not geom.isEmpty():
            self._rubber.setToGeometry(geom, None)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if not self._active or self._start is None or event.button() != QtC.LeftButton:
            return
        self._active = False
        end = self._end_point(event)
        length = math.hypot(end.x() - self._start.x(), end.y() - self._start.y())
        geom = self._arrow_geometry(self._start, end)
        self._discard_rubber()
        if self._screen_px(length) >= MIN_ARROW_PX and not geom.isEmpty():
            self._manager.commit(geom, self._color, self._shape)
        self._start = None

    def _end_point(self, event) -> QgsPointXY:

        end = self.toMapCoordinates(QtC.event_pos(event))
        if self._start is not None and _shift_held(event):
            return snap_to_angle(self._start, end)
        return end

    def _cancel_drag(self) -> None:
        super()._cancel_drag()
        self._start = None

    def _arrow_geometry(self, start: QgsPointXY, end: QgsPointXY) -> QgsGeometry:
        return _arrow_multiline(
            start,
            end,
            head_len_map=self.HEAD_LEN_PX * self._canvas.mapUnitsPerPixel(),
            head_angle_rad=math.radians(self.HEAD_ANGLE_DEG),
        )


class CircleMapTool(_MarkupBaseMapTool):






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
        cur = self._corner_point(event)
        geom = _ellipse_ring(self._anchor, cur, self.SEGMENTS)
        if not geom.isEmpty():
            self._rubber.setToGeometry(geom, None)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if not self._active or self._anchor is None or event.button() != QtC.LeftButton:
            return
        self._active = False
        cur = self._corner_point(event)
        span = max(abs(cur.x() - self._anchor.x()), abs(cur.y() - self._anchor.y()))
        geom = _ellipse_ring(self._anchor, cur, self.SEGMENTS)
        self._discard_rubber()
        if self._screen_px(span) >= MIN_CIRCLE_PX and not geom.isEmpty():
            self._manager.commit(geom, self._color, self._shape)
        self._anchor = None

    def _corner_point(self, event) -> QgsPointXY:


        cur = self.toMapCoordinates(QtC.event_pos(event))
        if self._anchor is not None and _shift_held(event):
            return square_corner(self._anchor, cur)
        return cur

    def _cancel_drag(self) -> None:
        super()._cancel_drag()
        self._anchor = None







def _arrow_multiline(
    start: QgsPointXY,
    end: QgsPointXY,
    head_len_map: float,
    head_angle_rad: float,
) -> QgsGeometry:

    dx, dy = end.x() - start.x(), end.y() - start.y()
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return QgsGeometry()
    ux, uy = dx / length, dy / length

    head_len = min(head_len_map, length * 0.6)
    cos_a, sin_a = math.cos(head_angle_rad), math.sin(head_angle_rad)



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



_ = QgsWkbTypes
