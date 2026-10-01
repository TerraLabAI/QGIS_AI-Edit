






















from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsRasterLayer,
    QgsRectangle,
)

from ..core.config_store import get_export_dial, get_export_dial_ratio
from ..core.extent_transform import has_crs_transform, transform_extent




VIEW_SHARE_FILLS = 0.5
VIEW_SHARE_PARTIAL = 0.05





_WORLD_SPAN_LON = 300.0
_WORLD_SPAN_LAT = 130.0

TIER_FILLS_VIEW = 4
TIER_IN_VIEW = 3
TIER_BACKDROP = 2
TIER_SLIVER = 1
TIER_OUT_OF_VIEW = 0


def _comparable_extent(extent, source_crs, target_crs) -> QgsRectangle | None:









    if extent is None or extent.isEmpty():
        return None
    if not has_crs_transform(source_crs, target_crs):
        return None
    reprojected = transform_extent(extent, source_crs, target_crs)
    return None if reprojected is None or reprojected.isEmpty() else reprojected


def measure_view_share(layer: QgsRasterLayer, view_extent, view_crs) -> float:

    if view_extent is None or view_extent.isEmpty():
        return 0.0
    view_area = view_extent.width() * view_extent.height()
    if view_area <= 0:
        return 0.0
    layer_extent = _comparable_extent(layer.extent(), layer.crs(), view_crs)
    if layer_extent is None:
        return 0.0
    overlap = layer_extent.intersect(view_extent)
    if overlap.isEmpty():
        return 0.0
    return min(1.0, (overlap.width() * overlap.height()) / view_area)


def raster_is_world_backdrop(layer: QgsRasterLayer) -> bool:

    extent = _comparable_extent(
        layer.extent(), layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"))
    if extent is None:
        return False
    span_lon = get_export_dial("widgets.raster_view_pick.world_span_lon_deg", _WORLD_SPAN_LON)
    span_lat = get_export_dial("widgets.raster_view_pick.world_span_lat_deg", _WORLD_SPAN_LAT)
    return extent.width() >= span_lon and extent.height() >= span_lat


def view_fit_tier(layer: QgsRasterLayer, view_extent, view_crs) -> int:

    share = measure_view_share(layer, view_extent, view_crs)
    if share <= 0.0:
        return TIER_OUT_OF_VIEW
    if raster_is_world_backdrop(layer):
        return TIER_BACKDROP
    if share >= get_export_dial_ratio("widgets.raster_view_pick.view_share_fills", VIEW_SHARE_FILLS):
        return TIER_FILLS_VIEW
    if share >= get_export_dial_ratio("widgets.raster_view_pick.view_share_partial", VIEW_SHARE_PARTIAL):
        return TIER_IN_VIEW
    return TIER_SLIVER


def rank_raster_for_view(
    layer: QgsRasterLayer,
    view_extent,
    view_crs,
    tree_order: int,
    active_layer_id: str | None = None,
) -> tuple[int, int, int, int]:





    tier = view_fit_tier(layer, view_extent, view_crs)
    is_active = 1 if active_layer_id and layer.id() == active_layer_id else 0
    try:
        looks_like_imagery = 1 if layer.bandCount() >= 3 else 0
    except Exception:
        looks_like_imagery = 0
    return (tier, is_active, looks_like_imagery, -tree_order)
