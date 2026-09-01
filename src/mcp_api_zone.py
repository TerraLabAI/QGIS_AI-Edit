






from __future__ import annotations

import math
from typing import Any

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
)

from .mcp_api_support import _never_raises


class ZoneMixin:




    def _canvas_extent(self):
        from qgis.utils import iface
        return iface.mapCanvas().extent()

    def _canvas_crs(self):
        from qgis.utils import iface
        return iface.mapCanvas().mapSettings().destinationCrs()

    def _canvas_units_per_pixel(self) -> float:

        try:
            from qgis.utils import iface
            return float(iface.mapCanvas().mapSettings().mapUnitsPerPixel())
        except Exception:
            return 0.0

    def _resolve_extent(self, bbox):





        if isinstance(bbox, dict):
            corners = [bbox.get(key) for key in ("xmin", "ymin", "xmax", "ymax")]
        elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            corners = list(bbox)
        else:
            return {"_error": "bbox must be 4 numbers [xmin, ymin, xmax, ymax], or the same 4 as a dict."}
        try:
            if any(isinstance(value, bool) for value in corners):
                raise ValueError("Boolean coordinate")
            values = [float(value) for value in corners]
            if not all(math.isfinite(value) for value in values):
                return {"_error": "bbox values must be finite numbers."}
            if values[0] >= values[2] or values[1] >= values[3]:
                return {"_error": "bbox must have xmin < xmax and ymin < ymax."}
            return QgsRectangle(*values)
        except (TypeError, ValueError, OverflowError):
            return {"_error": "bbox values must all be numbers."}

    def _reproject_to_canvas(self, geometry, crs_authid: str | None):

        if not crs_authid:
            return geometry
        source = QgsCoordinateReferenceSystem(str(crs_authid))
        if not source.isValid():
            return {"_error": f"crs '{crs_authid}' is not a coordinate system QGIS knows."}
        target = self._canvas_crs()
        if source == target:
            return geometry
        transform = QgsCoordinateTransform(source, target, QgsProject.instance())
        moved = QgsGeometry(geometry)
        if moved.transform(transform) != 0:
            return {"_error": f"Could not convert the shape from {crs_authid} to the map CRS."}
        return moved

    def _reproject_extent_to_canvas(self, extent, crs_authid: str | None):






        if not crs_authid:
            return extent
        source = QgsCoordinateReferenceSystem(str(crs_authid))
        if not source.isValid():
            return {"_error": f"crs '{crs_authid}' is not a coordinate system QGIS knows."}
        target = self._canvas_crs()
        if source == target:
            return extent
        transform = QgsCoordinateTransform(source, target, QgsProject.instance())
        try:
            return transform.transformBoundingBox(QgsRectangle(extent))
        except Exception:
            return {"_error": f"Could not convert the area from {crs_authid} to the map CRS."}

    def _resolve_polygon(self, polygon_wkt: str, crs_authid: str | None = None):







        from .ui.tools.polygon_selection_tool import (
            PolygonSelectionTool,
            _repair_polygon,
            expand_bbox_to_ratio_and_min_size,
        )

        raw = QgsGeometry.fromWkt(str(polygon_wkt or ""))
        if raw is None or raw.isEmpty():
            return {"_error": "polygon_wkt is not a readable geometry."}
        moved = self._reproject_to_canvas(raw, crs_authid)
        if isinstance(moved, dict):
            return moved
        shape = _repair_polygon(moved)
        if shape is None:
            return {
                "_error": (
                    "That shape is too thin, too small, or crosses itself. "
                    "Send a simple closed polygon with real area."
                )
            }
        extent = expand_bbox_to_ratio_and_min_size(
            shape.boundingBox(),
            self._canvas_units_per_pixel(),
            PolygonSelectionTool.MIN_SIZE_PX,
        )
        return extent, shape

    def _validate_extent(self, extent) -> dict | None:

        try:
            from qgis.utils import iface

            from .core.errors import AIEditError
            from .ui.canvas_exporter import validate_zone
        except ImportError:
            return {"_error": "Zone validation is unavailable in this QGIS session."}
        try:
            canvas = iface.mapCanvas()
            validate_zone(extent, canvas.mapSettings().destinationCrs(), canvas.rotation())
        except AIEditError as err:
            return {
                "_error": err.message or "This zone cannot be used.",
                "code": getattr(err.code, "value", None),
            }
        except Exception:
            return {"_error": "The map zone could not be validated. Check the canvas CRS and try again."}
        return None

    def _install_zone(self, extent, polygon=None) -> bool:

        select_zone = getattr(self._plugin, "_on_zone_selected", None)
        if callable(select_zone):
            select_zone(extent, polygon)
            return True
        self._plugin._selected_extent = extent
        self._plugin._selected_polygon = polygon
        return False

    def _zone_summary(self) -> dict:

        extent = getattr(self._plugin, "_selected_extent", None)
        polygon = getattr(self._plugin, "_selected_polygon", None)
        out: dict[str, Any] = {"has_zone": bool(extent)}
        if extent:
            out["bbox"] = {
                "xmin": extent.xMinimum(),
                "ymin": extent.yMinimum(),
                "xmax": extent.xMaximum(),
                "ymax": extent.yMaximum(),
            }
            out["width"] = extent.width()
            out["height"] = extent.height()
        try:
            out["crs"] = self._canvas_crs().authid()
        except Exception:  # nosec B110
            pass
        has_shape = polygon is not None and not polygon.isEmpty()
        out["free_shape"] = has_shape
        out["polygon_wkt"] = polygon.asWkt() if has_shape else None
        return out



    @_never_raises
    def set_zone(
        self,
        bbox=None,
        polygon_wkt: str | None = None,
        crs: str | None = None,
    ) -> dict:


















        if getattr(self, "_busy", lambda: False)():
            return {"_error": "Wait for the current generation before changing its zone.", "busy": True}
        if bbox is not None and polygon_wkt:
            return {"_error": "Pass bbox or polygon_wkt, not both."}
        polygon = None
        if polygon_wkt:
            resolved = self._resolve_polygon(polygon_wkt, crs)
            if isinstance(resolved, dict):
                return resolved
            extent, polygon = resolved
        elif bbox is not None:
            extent = self._resolve_extent(bbox)
            if isinstance(extent, dict):
                return extent
            if crs:
                moved = self._reproject_extent_to_canvas(extent, crs)
                if isinstance(moved, dict):
                    return moved
                extent = moved
        else:
            extent = self._canvas_extent()

        refused = self._validate_extent(extent)
        if refused is not None:
            return refused

        self._open_dock()
        self._install_zone(extent, polygon)
        summary = self._zone_summary()
        summary["ok"] = True
        summary["hint"] = (
            "Call generate(prompt) to edit this zone, or markup('draw', geometry_wkt) "
            "first to point at the part you mean."
        )
        return summary

    @_never_raises
    def get_zone(self) -> dict:






        summary = self._zone_summary()
        if not summary.get("has_zone"):
            summary["hint"] = (
                "Call set_zone(bbox) to choose a zone, or set_zone() with nothing "
                "to use the current map view."
            )
        return summary

    @_never_raises
    def clear_zone(self) -> dict:





        if getattr(self, "_busy", lambda: False)():
            return {"_error": "Wait for the current generation or cancel it before clearing its zone.", "busy": True}
        handler = getattr(self._plugin, "_on_zone_delete_requested", None)
        if callable(handler):
            handler()
        else:
            self._plugin._selected_extent = None
            self._plugin._selected_polygon = None
        summary = self._zone_summary()
        summary["ok"] = not summary.get("has_zone")
        summary["hint"] = "Call set_zone(bbox) to choose the next zone."
        return summary
