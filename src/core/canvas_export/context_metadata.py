from __future__ import annotations

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

from ..config_store import get_export_dial_seq
from .native_resolution import _zone_dims_meters, finest_native_mpp
from .render import ExportPrep


def apply_export_context(
    ctx,
    prep: ExportPrep,
    actual_extent: QgsRectangle,
    image_size_bytes: int,
    input_format: str | None = None,
    zone_polygon: QgsGeometry | None = None,
) -> None:









    if ctx is None:
        return
    ctx.extent = {
        "xmin": actual_extent.xMinimum(),
        "ymin": actual_extent.yMinimum(),
        "xmax": actual_extent.xMaximum(),
        "ymax": actual_extent.yMaximum(),
    }
    ctx.crs_wkt = prep.map_crs.toWkt()
    ctx.crs_authid = prep.map_crs.authid() or None
    ctx.centroid_lat, ctx.centroid_lon = _centroid_wgs84(actual_extent, prep.map_crs)
    ctx.bbox_wgs84 = _bbox_wgs84(actual_extent, prep.map_crs)
    ctx.basemap = _detect_basemap(prep.settings.layers())
    ctx.ground_resolution_m = _compute_ground_resolution_m(
        actual_extent, prep.out_w, prep.out_h, prep.map_crs
    )
    ctx.export_width = prep.out_w
    ctx.export_height = prep.out_h
    ctx.image_size_bytes = image_size_bytes
    ctx.input_format = input_format
    ctx.zone_polygon_wkt = None
    if zone_polygon is not None and not zone_polygon.isEmpty():
        ctx.zone_polygon_wkt = zone_polygon.asWkt()


def _centroid_wgs84(extent: QgsRectangle, src_crs) -> tuple[float | None, float | None]:

    try:
        cx = (extent.xMinimum() + extent.xMaximum()) / 2
        cy = (extent.yMinimum() + extent.yMaximum()) / 2
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if src_crs == wgs84:
            return cy, cx
        transform = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
        pt = transform.transform(QgsPointXY(cx, cy))
        return pt.y(), pt.x()
    except Exception:
        return None, None


def _bbox_wgs84(extent: QgsRectangle, src_crs) -> dict | None:






    try:
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if src_crs == wgs84:
            box = extent
        else:
            transform = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
            box = transform.transformBoundingBox(extent)
        return {
            "west": box.xMinimum(),
            "south": box.yMinimum(),
            "east": box.xMaximum(),
            "north": box.yMaximum(),
        }
    except Exception:
        return None




_BASEMAP_HOSTS = (
    ("google", "Google"),
    ("gstatic", "Google"),
    ("virtualearth", "Bing"),
    ("bing", "Bing"),
    ("geopf.fr", "IGN"),
    ("ign.fr", "IGN"),
    ("geoportail", "IGN"),
    ("arcgisonline", "Esri"),
    ("esri", "Esri"),
    ("mapbox", "Mapbox"),
    ("openstreetmap", "OSM"),
    ("tile.osm", "OSM"),
    ("cartocdn", "Carto"),
    ("swisstopo", "Swisstopo"),
)




_MAX_BASEMAP_HOST_CHARS = 40
_MAX_BASEMAP_HOSTS = 40


def _basemap_host_pairs() -> tuple[tuple[str, str], ...]:







    base = tuple(f"{needle}|{label}" for needle, label in _BASEMAP_HOSTS)
    pairs: list[tuple[str, str]] = []
    for entry in get_export_dial_seq(
        "basemap_hosts_extra", base, max_len=_MAX_BASEMAP_HOSTS
    ):
        needle, sep, label = entry.partition("|")
        needle = needle.strip().lower()
        label = label.strip()
        if not sep or not needle or not label:
            continue
        if (
            len(needle) > _MAX_BASEMAP_HOST_CHARS
            or len(label) > _MAX_BASEMAP_HOST_CHARS
        ):
            continue
        pairs.append((needle, label))
    return tuple(pairs)


def _basemap_label(layer) -> str | None:






    try:
        provider = (layer.providerType() or "").lower()
    except Exception:
        return None
    if provider == "gdal":
        return "local raster"
    if provider not in ("wms", "wmts", "arcgismapserver"):
        return provider or None
    try:
        from urllib.parse import parse_qs, urlsplit

        params = parse_qs(layer.source() or "")
        url = (params.get("url") or [""])[0]
        kind = "XYZ" if (params.get("type") or [""])[0] == "xyz" else "WMS"
        host = (urlsplit(url).hostname or "").lower()
        for needle, label in _basemap_host_pairs():
            if needle in host:
                return label
        return (f"{kind}:{host}" if host else kind)[:64]
    except Exception:
        return None


def _detect_basemap(layers) -> str | None:

    try:
        from qgis.core import QgsRasterLayer


        for layer in reversed(list(layers)):
            if isinstance(layer, QgsRasterLayer):
                label = _basemap_label(layer)
                if label:
                    return label
    except Exception:
        return None
    return None


def _compute_ground_resolution_m(extent, out_w: int, out_h: int, crs) -> float | None:





    try:
        width_m, height_m = _zone_dims_meters(extent, crs)
        if width_m is None or height_m is None:
            return None
        result = ((width_m / max(out_w, 1)) + (height_m / max(out_h, 1))) / 2
        if result > 0 and result < 1_000_000:
            return float(result)
    except Exception:
        pass  # nosec B110
    return None


def estimate_zone_area_km2(extent, crs) -> float | None:




    try:
        width_m, height_m = _zone_dims_meters(extent, crs)
        if width_m is None or height_m is None:
            return None
        area = (width_m * height_m) / 1_000_000.0
        if area >= 0:
            return float(area)
    except Exception:
        pass  # nosec B110
    return None


def native_size_inputs(map_settings, extent) -> dict | None:




    try:
        if extent is None or extent.width() <= 0 or extent.height() <= 0:
            return None
        crs = map_settings.destinationCrs()
        zw_m, zh_m = _zone_dims_meters(extent, crs)
        out = {"ratio": extent.width() / extent.height()}
        if zw_m and zh_m and all(math.isfinite(v) and v > 0 for v in (zw_m, zh_m)):
            out["zone_w_m"] = float(zw_m)
            out["zone_h_m"] = float(zh_m)
            finest = finest_native_mpp(map_settings.layers(), extent, crs)
            if finest is not None:
                out["finest_mpp"] = float(finest)
        return out
    except Exception:
        return None


def ground_resolution_for_size(map_settings, extent, width: int, height: int) -> float | None:

    try:
        return _compute_ground_resolution_m(extent, width, height, map_settings.destinationCrs())
    except Exception:
        return None
