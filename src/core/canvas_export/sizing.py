from __future__ import annotations

import math

from qgis.core import QgsMapSettings, QgsRectangle

from ..config_store import ServerDialMap


__all__ = [
    "_adjust_extent_to_aspect",
    "_aspect_dims",
    "_RESOLUTION_TARGET_PX",
    "get_zone_pixel_size",
]




_RESOLUTION_TARGET_PX = ServerDialMap(
    "resolution_targets_px", {"1K": 1024, "2K": 2048, "4K": 4096}
)


def _aspect_dims(
    extent: QgsRectangle, longest: int, align: int, max_dim: int
) -> tuple[int, int]:

    max_dim = _aligned_limit(extent, align, max_dim)
    longest = min(max_dim, max(align, longest))
    ext_ratio = extent.width() / extent.height()
    if ext_ratio >= 1:
        out_w = longest
        out_h = max(align, int(round(longest / ext_ratio)))
    else:
        out_h = longest
        out_w = max(align, int(round(longest * ext_ratio)))

    out_w = max(align, (out_w // align) * align)
    out_h = max(align, (out_h // align) * align)
    out_w = min(max_dim, out_w)
    out_h = min(max_dim, out_h)
    return out_w, out_h


def _adjust_extent_to_aspect(
    extent: QgsRectangle, out_w: int, out_h: int
) -> QgsRectangle:

    ext_ratio = extent.width() / extent.height()
    pixel_ratio = out_w / out_h
    cx = extent.center().x()
    cy = extent.center().y()
    if pixel_ratio >= ext_ratio:
        new_half_w = (extent.height() * pixel_ratio) / 2
        return QgsRectangle(
            cx - new_half_w,
            extent.yMinimum(),
            cx + new_half_w,
            extent.yMaximum(),
        )
    new_half_h = (extent.width() / pixel_ratio) / 2
    return QgsRectangle(
        extent.xMinimum(),
        cy - new_half_h,
        extent.xMaximum(),
        cy + new_half_h,
    )


def get_zone_pixel_size(
    map_settings: QgsMapSettings, extent: QgsRectangle
) -> tuple[int, int]:

    canvas_extent = map_settings.extent()
    canvas_size = map_settings.outputSize()

    dimensions = (canvas_extent.width(), canvas_extent.height(), extent.width(), extent.height())
    if not all(math.isfinite(v) and v > 0 for v in dimensions):
        return (0, 0)

    px_per_map_unit_x = canvas_size.width() / canvas_extent.width()
    px_per_map_unit_y = canvas_size.height() / canvas_extent.height()

    return (
        round(abs(extent.width() * px_per_map_unit_x)),
        round(abs(extent.height() * px_per_map_unit_y)),
    )


def _aligned_limit(extent, align, max_dim):

    if not all(math.isfinite(v) and v > 0 for v in (extent.width(), extent.height())):
        raise ValueError("Invalid extent: width and height must be positive")
    if align <= 0 or max_dim < align:
        raise ValueError("Invalid pixel alignment")
    return (max_dim // align) * align
