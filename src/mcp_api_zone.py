"""The zone group of the AI Edit public API.

A zone is the piece of the map an edit applies to. AI Edit holds exactly one at
a time. It is always a rectangle, and it may additionally carry a free shape
drawn inside that rectangle: the rectangle is what the model is shown, the free
shape is what the result is cut to once it comes back.
"""
from __future__ import annotations

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
    """Reading and setting the zone an edit applies to."""

    # --- internals --------------------------------------------------------

    def _canvas_extent(self):
        from qgis.utils import iface
        return iface.mapCanvas().extent()

    def _canvas_crs(self):
        from qgis.utils import iface
        return iface.mapCanvas().mapSettings().destinationCrs()

    def _canvas_units_per_pixel(self) -> float:
        """Map units one screen pixel covers, or 0.0 with no live canvas."""
        try:
            from qgis.utils import iface
            return float(iface.mapCanvas().mapSettings().mapUnitsPerPixel())
        except Exception:
            return 0.0

    def _resolve_extent(self, bbox):
        """Build the zone rectangle. Returns a rectangle or an error dict.

        ``bbox`` accepts ``[xmin, ymin, xmax, ymax]`` or the same four numbers
        as a dict. Coordinates are read in the map canvas CRS.
        """
        if isinstance(bbox, dict):
            corners = [bbox.get(key) for key in ("xmin", "ymin", "xmax", "ymax")]
        elif isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            corners = list(bbox)
        else:
            return {"_error": "bbox must be 4 numbers [xmin, ymin, xmax, ymax], or the same 4 as a dict."}
        try:
            return QgsRectangle(*[float(value) for value in corners])
        except (TypeError, ValueError):
            return {"_error": "bbox values must all be numbers."}

    def _reproject_to_canvas(self, geometry, crs_authid: str | None):
        """Move a geometry into the canvas CRS. Returns it, or an error dict."""
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
        """Move a rectangle into the canvas CRS. Returns it, or an error dict.

        Through transformBoundingBox, which walks the edges. Moving the four
        corners and taking their bounding box under-covers on a curved
        projection, so the zone would come out smaller than the one asked for.
        """
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
        """Turn a polygon WKT into the (rectangle, shape) pair a zone is made of.

        Repeats exactly what the panel's drawing tool does when a human closes a
        shape: repair the outline, then grow its bounding box to the nearest
        supported picture shape and to the smallest zone the plugin accepts. The
        grown rectangle is what gets edited, and the drawn shape rides along.
        """
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
        """The panel's own zone check. Returns an error dict, or None when fine."""
        try:
            from qgis.utils import iface

            from .core.errors import AIEditError
            from .ui.canvas_exporter import validate_zone
        except ImportError:
            return None
        try:
            canvas = iface.mapCanvas()
            validate_zone(extent, canvas.mapSettings().destinationCrs(), canvas.rotation())
        except AIEditError as err:
            return {
                "_error": err.message or "This zone cannot be used.",
                "code": getattr(err.code, "value", None),
            }
        except Exception:  # nosec B110 - only a real refusal stops the caller.
            return None
        return None

    def _install_zone(self, extent, polygon=None) -> bool:
        """Hand the zone to the plugin the way a finished drawing does."""
        select_zone = getattr(self._plugin, "_on_zone_selected", None)
        if callable(select_zone):
            select_zone(extent, polygon)
            return True
        self._plugin._selected_extent = extent
        self._plugin._selected_polygon = polygon
        return False

    def _zone_summary(self) -> dict:
        """The zone as it stands, in the map canvas CRS."""
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
        except Exception:  # nosec B110 - the CRS is a nicety, the numbers are not.
            pass
        has_shape = polygon is not None and not polygon.isEmpty()
        out["free_shape"] = has_shape
        out["polygon_wkt"] = polygon.asWkt() if has_shape else None
        return out

    # --- public -----------------------------------------------------------

    @_never_raises
    def set_zone(
        self,
        bbox=None,
        polygon_wkt: str | None = None,
        crs: str | None = None,
    ) -> dict:
        """Choose the piece of map the next edit works on. Costs nothing.

        Pass ``bbox`` as ``[xmin, ymin, xmax, ymax]`` or a dict of those four
        keys for a plain rectangle. Pass ``polygon_wkt`` instead for a free
        shape, the same thing a person draws point by point in the panel: the
        rectangle around your shape is what gets edited, and the result is then
        cut back to the shape you sent, so the map outside it is untouched.
        Pass neither and the current map view becomes the zone. ``crs`` names
        the coordinate system your numbers are in, for example "EPSG:4326";
        leave it out and they are read in the map canvas CRS.

        Setting a zone starts a fresh piece of work: it clears the version
        history of the previous zone and any prompt template you had armed.

        Returns ``ok``, ``bbox``, ``crs``, ``free_shape`` and ``polygon_wkt``.
        A shape with no real area, or one the plugin refuses, comes back under
        ``_error`` and nothing changes.
        """
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
        """Report the zone AI Edit is holding. Costs nothing, no network call.

        Returns ``has_zone``, and when there is one: ``bbox``, ``width``,
        ``height``, ``crs``, ``free_shape`` (True when a drawn shape is
        attached) and ``polygon_wkt`` (that shape, or None).
        """
        summary = self._zone_summary()
        if not summary.get("has_zone"):
            summary["hint"] = (
                "Call set_zone(bbox) to choose a zone, or set_zone() with nothing "
                "to use the current map view."
            )
        return summary

    @_never_raises
    def clear_zone(self) -> dict:
        """Drop the zone and the versions built on it, as deleting it does.

        The layers already produced stay in the project. Returns ``ok`` and
        ``has_zone``, which is False afterwards.
        """
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
