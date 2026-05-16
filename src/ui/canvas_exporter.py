from __future__ import annotations

import base64

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapRendererCustomPainterJob,
    QgsMapSettings,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QBuffer, QSize
from qgis.PyQt.QtGui import QImage, QPainter

from ..core import qt_compat as QtC

# Global server config (set by plugin.py at startup)
# Plugin cannot export without server config
_server_config: dict | None = None

# Map user-facing resolution labels to target pixel counts (longest side)
_RESOLUTION_TARGET_PX = {"1K": 1024, "2K": 2048, "4K": 4096}


def set_server_config(config: dict):
    """Set server export config fetched at plugin startup."""
    global _server_config
    _server_config = config


def has_server_config() -> bool:
    """Check if server config has been loaded."""
    return _server_config is not None


def _get_max_dimension() -> int | None:
    """Get max dimension from server config. Returns None if unavailable."""
    if _server_config:
        return _server_config.get("max_dimension")
    return None


def _get_align() -> int | None:
    """Get pixel alignment from server config. Returns None if unavailable."""
    if _server_config:
        return _server_config.get("align")
    return None


def export_canvas_zone(
    map_settings: QgsMapSettings,
    extent: QgsRectangle,
    ctx=None,
    target_resolution: str | None = None,
) -> tuple[str, int, int, QgsRectangle]:
    """Export a zone of the QGIS canvas as a base64-encoded PNG string.

    Args:
        map_settings: Current map settings (layers, CRS, etc.)
        extent: The rectangle to export in map coordinates

    Returns:
        Tuple of (base64_png_string, width_px, height_px, actual_extent)
        where actual_extent is the extent QGIS actually rendered (may be
        slightly larger than requested to match the output aspect ratio).
    """
    if extent.width() <= 0 or extent.height() <= 0:
        raise ValueError("Invalid extent: width and height must be positive")

    # Use canvas scale to determine resolution (zoom level),
    # but derive pixel aspect ratio from the geographic extent (the truth).
    max_dim = _get_max_dimension()
    align = _get_align()

    if max_dim is None or align is None:
        raise RuntimeError(
            "Export config not loaded from server. "
            "Check your internet connection and restart QGIS."
        )

    px_w, px_h = get_zone_pixel_size(map_settings, extent)
    if target_resolution and target_resolution in _RESOLUTION_TARGET_PX:
        longest = _RESOLUTION_TARGET_PX[target_resolution]
    else:
        longest = max(max(px_w, px_h), 1)
    longest = min(longest, max_dim)

    ext_ratio = extent.width() / extent.height()
    if ext_ratio >= 1:
        out_w = longest
        out_h = max(align, int(round(longest / ext_ratio)))
    else:
        out_h = longest
        out_w = max(align, int(round(longest * ext_ratio)))

    # Round to multiples of align so AI model doesn't need to pad
    out_w = max(align, (out_w // align) * align)
    out_h = max(align, (out_h // align) * align)

    # Final clamp
    out_w = min(max_dim, out_w)
    out_h = min(max_dim, out_h)

    # Adjust the extent to exactly match the output pixel aspect ratio.
    # This prevents QGIS from expanding the extent, ensuring the rendered
    # area matches the user's selection as closely as possible.
    pixel_ratio = out_w / out_h
    cx = extent.center().x()
    cy = extent.center().y()
    if pixel_ratio >= ext_ratio:
        # Pixels are wider than extent: expand extent width
        new_half_w = (extent.height() * pixel_ratio) / 2
        adjusted_extent = QgsRectangle(
            cx - new_half_w,
            extent.yMinimum(),
            cx + new_half_w,
            extent.yMaximum(),
        )
    else:
        # Pixels are taller than extent: expand extent height
        new_half_h = (extent.width() / pixel_ratio) / 2
        adjusted_extent = QgsRectangle(
            extent.xMinimum(),
            cy - new_half_h,
            extent.xMaximum(),
            cy + new_half_h,
        )

    # Configure render settings
    settings = QgsMapSettings()
    settings.setLayers(map_settings.layers())
    settings.setDestinationCrs(map_settings.destinationCrs())
    settings.setExtent(adjusted_extent)
    settings.setOutputSize(QSize(out_w, out_h))
    settings.setBackgroundColor(map_settings.backgroundColor())

    # Use visibleExtent(): the actual geographic area QGIS rendered,
    # after any internal adjustment for pixel grid alignment.
    actual_extent = settings.visibleExtent()

    # Render to QImage
    image = QImage(QSize(out_w, out_h), QtC.FormatARGB32)
    image.fill(map_settings.backgroundColor())
    painter = QPainter(image)
    job = QgsMapRendererCustomPainterJob(settings, painter)
    job.start()
    job.waitForFinished()
    painter.end()

    # Serialize as lossless PNG. Iterative edits on the same zone re-eat the
    # previous output, so any encoding loss compounds — PNG keeps the canvas
    # bit-exact through the round-trip to the model and back.
    buffer = QBuffer()
    buffer.open(QtC.WriteOnly)
    image.save(buffer, "PNG")
    b64 = base64.b64encode(buffer.data().data()).decode("ascii")

    # Populate pipeline context
    if ctx is not None:
        ctx.extent = {
            "xmin": actual_extent.xMinimum(),
            "ymin": actual_extent.yMinimum(),
            "xmax": actual_extent.xMaximum(),
            "ymax": actual_extent.yMaximum(),
        }
        dest_crs = map_settings.destinationCrs()
        ctx.crs_wkt = dest_crs.toWkt()
        ctx.crs_authid = dest_crs.authid() or None
        # Compute centroid in WGS84 client-side so the server doesn't need
        # proj4 — QGIS already has every CRS definition loaded.
        ctx.centroid_lat, ctx.centroid_lon = _centroid_wgs84(actual_extent, dest_crs)
        ctx.ground_resolution_m = _compute_ground_resolution_m(
            actual_extent, out_w, out_h, dest_crs
        )
        ctx.export_width = out_w
        ctx.export_height = out_h
        ctx.image_size_bytes = len(buffer.data().data())

    return b64, out_w, out_h, actual_extent


def _centroid_wgs84(extent: QgsRectangle, src_crs) -> tuple[float | None, float | None]:
    """Centroid of the rendered extent, projected to WGS84 (EPSG:4326).

    Returns (lat, lon) or (None, None) if the transform fails (unknown CRS,
    out-of-bounds projection, etc.). Server sees None and skips the geo
    enrichment cleanly.
    """
    try:
        cx = (extent.xMinimum() + extent.xMaximum()) / 2
        cy = (extent.yMinimum() + extent.yMaximum()) / 2
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if src_crs == wgs84:
            return cy, cx  # already lat/lon
        transform = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
        pt = transform.transform(QgsPointXY(cx, cy))
        return pt.y(), pt.x()  # QGIS returns (x=lon, y=lat) for WGS84
    except Exception:
        return None, None


def _compute_ground_resolution_m(extent, out_w: int, out_h: int, crs) -> float | None:
    """Meters per pixel at the centroid of the rendered zone.

    Returns None if the CRS is not projected and we cannot reliably convert
    map units to meters (geographic CRS like EPSG:4326 need a transform that
    we'd rather do server-side).
    """
    try:
        from qgis.core import QgsUnitTypes
        map_units = crs.mapUnits()
        # mean pixel size in map units
        px_size_x = extent.width() / max(out_w, 1)
        px_size_y = extent.height() / max(out_h, 1)
        avg_px_size = (px_size_x + px_size_y) / 2
        factor = QgsUnitTypes.fromUnitToUnitFactor(map_units, QgsUnitTypes.DistanceMeters)
        result = avg_px_size * factor
        if result > 0 and result < 1_000_000:
            return float(result)
    except Exception:
        pass  # nosec B110
    return None


def get_zone_pixel_size(
    map_settings: QgsMapSettings, extent: QgsRectangle
) -> tuple[int, int]:
    """Get the approximate pixel dimensions of a zone based on current map scale.

    Returns:
        (width_px, height_px)
    """
    canvas_extent = map_settings.extent()
    canvas_size = map_settings.outputSize()

    if canvas_extent.width() <= 0 or canvas_extent.height() <= 0:
        return (0, 0)

    px_per_map_unit_x = canvas_size.width() / canvas_extent.width()
    px_per_map_unit_y = canvas_size.height() / canvas_extent.height()

    return (
        round(abs(extent.width() * px_per_map_unit_x)),
        round(abs(extent.height() * px_per_map_unit_y)),
    )
