from __future__ import annotations

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)

from .export_config import _get_align, _get_max_dimension
from .native_resolution import _best_native_longest_px, _zone_dims_meters
from .render import ExportPrep
from .sizing import _aspect_dims


def apply_export_context(
    ctx,
    prep: ExportPrep,
    actual_extent: QgsRectangle,
    image_size_bytes: int,
    input_format: str | None = None,
) -> None:
    """Main-thread ctx mutation after the worker returns extent + size."""
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


def _centroid_wgs84(extent: QgsRectangle, src_crs) -> tuple[float | None, float | None]:
    """Centroid of the rendered extent, projected to WGS84 (EPSG:4326)."""
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
    """Rendered extent reprojected to WGS84 (EPSG:4326) as W/S/E/N degrees.

    Reprojected client-side so the backend stores an exact footprint without a
    proj library. transformBoundingBox densifies the edges, so the envelope of
    a rotated or conformal source CRS stays tight rather than clipping corners.
    """
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


# Known basemap tile hosts -> friendly label, so a generation reads "Google" or
# "IGN" rather than a raw tile URL. Substring match on the sanitized host.
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


def _basemap_label(layer) -> str | None:
    """Sanitized identity of one raster basemap layer.

    Friendly name for known tile hosts, else '<kind>:<host>' with the host only,
    else the provider kind. Never returns a full source, file path, or URL query:
    those can carry local disk paths or embedded auth tokens.
    """
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
        for needle, label in _BASEMAP_HOSTS:
            if needle in host:
                return label
        return (f"{kind}:{host}" if host else kind)[:64]
    except Exception:
        return None


def _detect_basemap(layers) -> str | None:
    """Sanitized identity of the bottom-most raster basemap under the zone."""
    try:
        from qgis.core import QgsRasterLayer

        # layers() is top-to-bottom; the basemap is normally the lowest raster.
        for layer in reversed(list(layers)):
            if isinstance(layer, QgsRasterLayer):
                label = _basemap_label(layer)
                if label:
                    return label
    except Exception:
        return None
    return None


def _compute_ground_resolution_m(extent, out_w: int, out_h: int, crs) -> float | None:
    """Meters per pixel at the centroid of the rendered zone.

    Measured geodesically (not via a flat unit factor) so it stays accurate at
    high latitude, where Web Mercator meters and degree CRS both diverge from
    true ground distance."""
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


def estimate_native_ground_resolution_m(map_settings, extent) -> float | None:
    """Predicted meters-per-pixel of the export at its native sizing.

    Used at zone-draw time to warn (softly) when the zone is so zoomed out the
    model cannot resolve small features. Mirrors the default export sizing path
    so the estimate matches what the user would actually get. Returns None when
    the export config is not loaded yet or the estimate cannot be computed."""
    try:
        max_dim = _get_max_dimension()
        align = _get_align()
        if max_dim is None or align is None:
            return None
        if extent is None or extent.width() <= 0 or extent.height() <= 0:
            return None
        crs = map_settings.destinationCrs()
        longest = _best_native_longest_px(map_settings.layers(), extent, crs, max_dim)
        out_w, out_h = _aspect_dims(extent, longest, align, max_dim)
        return _compute_ground_resolution_m(extent, out_w, out_h, crs)
    except Exception:
        return None
