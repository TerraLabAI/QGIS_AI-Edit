from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsDistanceArea,
    QgsMapLayer,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

# Optional in QGIS < 3.14; vector tile sizing falls back to "unconstrained" if missing.
try:
    from qgis.core import QgsVectorTileLayer
except ImportError:
    QgsVectorTileLayer = None

# Web Mercator m/px at z=0 for a 256-px tile, at the equator.
_WEBMERC_M_PX_Z0 = 156543.03392


def _best_native_longest_px(
    layers, zone_extent: QgsRectangle, map_crs, max_dim: int
) -> int:
    """Compute the optimal output 'longest side' in pixels from the data.

    Picks the longest ground span and the finest m/px across layers. Returns
    ``max_dim`` when no visible layer carries a usable native resolution.
    """
    zw_m, zh_m = _zone_dims_meters(zone_extent, map_crs)
    if not zw_m or not zh_m or zw_m <= 0 or zh_m <= 0:
        return max_dim

    finest_mpp: float | None = None
    for layer in layers or []:
        try:
            xy = _native_pixel_size_xy_m(layer, zone_extent, map_crs)
        except Exception:
            xy = None
        if xy:
            mpp_x, mpp_y = xy
            if mpp_x and mpp_x > 0:
                finest_mpp = mpp_x if finest_mpp is None else min(finest_mpp, mpp_x)
            if mpp_y and mpp_y > 0:
                finest_mpp = mpp_y if finest_mpp is None else min(finest_mpp, mpp_y)

    if finest_mpp is None:
        return max_dim

    longest_m = max(zw_m, zh_m)
    optimal = int(math.ceil(longest_m / finest_mpp))
    return max(1, min(optimal, max_dim))


def _native_pixel_size_xy_m(
    layer, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Native ``(mpp_x, mpp_y)`` at the zone, or None if the layer doesn't constrain output.

    Vector tile layers use ``sourceMaxZoom()``. Raster layers use
    ``rasterUnitsPerPixelX/Y()`` for any non-XYZ provider, and zmax for XYZ
    tiles. WMS, mesh, point cloud, and vector layers return None.
    """
    if layer is None:
        return None

    if QgsVectorTileLayer is not None and isinstance(layer, QgsVectorTileLayer):
        return _vector_tile_native_mpp_xy(layer, zone_extent, map_crs)

    if layer.type() != QgsMapLayer.LayerType.RasterLayer:
        return None

    if not _intersects_zone(layer, zone_extent, map_crs):
        return None

    provider = layer.dataProvider()
    if provider is None:
        return None

    source = (layer.source() or "").lower()
    provider_name = provider.name() or ""

    if "type=xyz" in source:
        return _xyz_native_mpp_xy(layer, zone_extent, map_crs)

    # Plain WMS / WMTS without a usable pixel grid: no client-side native res.
    if provider_name == "wms":
        return None

    return _raster_native_mpp_xy(layer, zone_extent, map_crs)


def _intersects_zone(layer, zone_extent: QgsRectangle, map_crs) -> bool:
    """Whether the layer's extent intersects the zone, transformed if needed."""
    try:
        layer_crs = layer.crs()
        if layer_crs == map_crs:
            return layer.extent().intersects(zone_extent)
        transform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
        zone_in_layer = transform.transformBoundingBox(zone_extent)
        return layer.extent().intersects(zone_in_layer)
    except Exception:
        return True


def _raster_native_mpp_xy(
    layer, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Native (m/px X, m/px Y) of a raster layer at the zone."""
    try:
        px_x = float(layer.rasterUnitsPerPixelX() or 0)
        px_y = float(layer.rasterUnitsPerPixelY() or 0)
        if px_x <= 0 or px_y <= 0:
            return None
        return _layer_units_to_meters_xy(layer, px_x, px_y, zone_extent, map_crs)
    except Exception:
        return None


def _layer_units_to_meters_xy(
    layer, px_x: float, px_y: float, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Convert pixel size (in layer CRS units) to meters at the zone centroid."""
    try:
        layer_crs = layer.crs()
        if layer_crs == map_crs:
            center = zone_extent.center()
        else:
            transform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
            center = transform.transform(zone_extent.center())

        da = QgsDistanceArea()
        da.setSourceCrs(layer_crs, QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")

        m_per_unit_x = da.measureLine(
            QgsPointXY(center.x(), center.y()),
            QgsPointXY(center.x() + 1.0, center.y()),
        )
        m_per_unit_y = da.measureLine(
            QgsPointXY(center.x(), center.y()),
            QgsPointXY(center.x(), center.y() + 1.0),
        )
        if m_per_unit_x <= 0 or m_per_unit_y <= 0:
            return None
        return (px_x * m_per_unit_x, px_y * m_per_unit_y)
    except Exception:
        return None


def _xyz_native_mpp_xy(
    layer, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Native m/px of an XYZ tile layer at its zmax for the zone centroid."""
    zmax = _xyz_zmax(layer)
    if zmax is None:
        return None
    mpp = _webmerc_mpp_at_lat(zone_extent, map_crs, zmax)
    if mpp is None:
        return None
    return (mpp, mpp)


def _xyz_zmax(layer) -> int | None:
    """Parse zmax from the XYZ URI. Returns None when not present."""
    try:
        uri = QgsDataSourceUri()
        uri.setEncodedUri(layer.source())
        z = uri.param("zmax")
        return int(z) if z else None
    except Exception:
        return None


def _vector_tile_native_mpp_xy(
    layer, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Native m/px of a vector tile layer at its source max zoom."""
    try:
        zmax = int(layer.sourceMaxZoom())
    except Exception:
        return None
    if zmax <= 0:
        return None
    mpp = _webmerc_mpp_at_lat(zone_extent, map_crs, zmax)
    if mpp is None:
        return None
    return (mpp, mpp)


def _webmerc_mpp_at_lat(zone_extent, map_crs, zoom: int) -> float | None:
    """Web Mercator meters/pixel at the zone centroid latitude for the given zoom."""
    try:
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if map_crs == wgs84:
            lat = zone_extent.center().y()
        else:
            transform = QgsCoordinateTransform(map_crs, wgs84, QgsProject.instance())
            lat = transform.transform(zone_extent.center()).y()
        lat = max(-85.0, min(85.0, lat))
        return _WEBMERC_M_PX_Z0 * math.cos(math.radians(lat)) / (2 ** zoom)
    except Exception:
        return None


def _zone_dims_meters(
    zone_extent: QgsRectangle, map_crs
) -> tuple[float | None, float | None]:
    """Zone (width_m, height_m) measured geodesically."""
    try:
        da = QgsDistanceArea()
        da.setSourceCrs(map_crs, QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")
        center_y = zone_extent.center().y()
        center_x = zone_extent.center().x()
        width_m = da.measureLine(
            QgsPointXY(zone_extent.xMinimum(), center_y),
            QgsPointXY(zone_extent.xMaximum(), center_y),
        )
        height_m = da.measureLine(
            QgsPointXY(center_x, zone_extent.yMinimum()),
            QgsPointXY(center_x, zone_extent.yMaximum()),
        )
        if width_m <= 0 or height_m <= 0:
            return (None, None)
        return (width_m, height_m)
    except Exception:
        return (None, None)
