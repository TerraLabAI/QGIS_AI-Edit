from __future__ import annotations

from qgis.core import QgsMapSettings, QgsRectangle

# Map user-facing resolution labels to target pixel counts (longest side)
_RESOLUTION_TARGET_PX = {"1K": 1024, "2K": 2048, "4K": 4096}


def _aspect_dims(
    extent: QgsRectangle, longest: int, align: int, max_dim: int
) -> tuple[int, int]:
    """Derive (out_w, out_h) from the longest side and the extent's aspect."""
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


# A little more than the model's output budget so the input never ends up
# smaller than the output (which would make the model upscale) after aspect
# skew and pixel alignment. 1.2x linear ~= 1.44x area; still a small upload.
_INPUT_BUDGET_HEADROOM = 1.2


def _budget_dims(
    extent: QgsRectangle, ref: int, align: int, max_dim: int
) -> tuple[int, int]:
    """Derive (out_w, out_h) targeting ~``ref``^2 total pixels (the model's
    output budget for the tier), aspect-preserved, so the input matches the
    output instead of undershooting it on non-square zones.

    A small headroom keeps the input >= the output; the long side is capped at
    ``max_dim`` proportionally so the aspect is preserved even when clamped.
    """
    ext_ratio = extent.width() / extent.height()
    budget = (float(ref) * _INPUT_BUDGET_HEADROOM) ** 2
    out_h = (budget / ext_ratio) ** 0.5
    out_w = out_h * ext_ratio

    longest = max(out_w, out_h)
    if longest > max_dim:
        scale = max_dim / longest
        out_w *= scale
        out_h *= scale

    out_w = max(align, int(round(out_w / align)) * align)
    out_h = max(align, int(round(out_h / align)) * align)
    out_w = min(max_dim, out_w)
    out_h = min(max_dim, out_h)
    return out_w, out_h


def _adjust_extent_to_aspect(
    extent: QgsRectangle, out_w: int, out_h: int
) -> QgsRectangle:
    """Expand the extent on one axis so it matches the output pixel aspect."""
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
    """Approximate pixel dimensions of a zone at the current canvas scale."""
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
