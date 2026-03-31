import base64
from typing import Tuple

from qgis.PyQt.QtCore import QSize, QBuffer, QIODevice
from qgis.PyQt.QtGui import QImage, QPainter
from qgis.core import QgsMapSettings, QgsMapRendererCustomPainterJob, QgsRectangle


MAX_DIMENSION = 1024


def export_canvas_zone(
    map_settings: QgsMapSettings, extent: QgsRectangle, ctx=None
) -> Tuple[str, int, int, QgsRectangle]:
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
    px_w, px_h = get_zone_pixel_size(map_settings, extent)
    longest = max(max(px_w, px_h), 1)
    longest = min(longest, MAX_DIMENSION)

    ext_ratio = extent.width() / extent.height()
    if ext_ratio >= 1:
        out_w = longest
        out_h = max(1, int(round(longest / ext_ratio)))
    else:
        out_h = longest
        out_w = max(1, int(round(longest * ext_ratio)))

    # Final clamp
    out_w = min(MAX_DIMENSION, max(1, out_w))
    out_h = min(MAX_DIMENSION, max(1, out_h))

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

    # Use our pre-adjusted extent as the spatial truth — not settings.extent()
    # which may shift by up to half a pixel due to QGIS grid snapping.
    actual_extent = adjusted_extent

    # Render to QImage
    image = QImage(QSize(out_w, out_h), QImage.Format_ARGB32)
    image.fill(map_settings.backgroundColor())
    painter = QPainter(image)
    job = QgsMapRendererCustomPainterJob(settings, painter)
    job.start()
    job.waitForFinished()
    painter.end()

    # Convert to base64 PNG
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
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
        ctx.crs_wkt = map_settings.destinationCrs().toWkt()
        ctx.export_width = out_w
        ctx.export_height = out_h
        ctx.image_size_bytes = len(buffer.data().data())

    return b64, out_w, out_h, actual_extent


def get_zone_pixel_size(
    map_settings: QgsMapSettings, extent: QgsRectangle
) -> Tuple[int, int]:
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


def calculate_suggested_resolution(
    map_settings: QgsMapSettings, extent: QgsRectangle
) -> str:
    """Calculate ideal resolution based on native pixel density of visible data.

    Returns one of: "0.5K", "1K", "2K", "4K"
    """
    px_w, px_h = get_zone_pixel_size(map_settings, extent)
    native_px = max(px_w, px_h)

    if native_px <= 600:
        return "0.5K"
    elif native_px <= 1200:
        return "1K"
    elif native_px <= 2400:
        return "2K"
    return "4K"
