"""Color-based raster vectorization using direct GDAL API.

Avoids QGIS Processing chaining (TEMPORARY_OUTPUT references can vanish
between gdal:* steps on macOS). Everything runs in-memory via GDAL MEM /
OGR Memory drivers; filtering + simplification happen in Python on
QgsGeometry objects.

The heavy pixel compute lives here (thread-safe, no QgsProject access);
layer build/persist/style live in ``vectorize_layer``; palette detection
and class naming live in ``vectorize_palette``.
"""
from __future__ import annotations

import os

# numpy is guarded: a broken numpy ABI (common on Windows OSGeo4W after an
# unrelated package upgrade) must NOT break this module's import (which would
# take down the whole dock / plugin load). When None, the Vectorize entry point
# fails that one click with a clean localized error instead.
try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from osgeo import gdal, ogr, osr
from qgis.core import (
    QgsDistanceArea,
    QgsFeature,
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..errors import AIEditError, ErrorCode
from ..i18n import tr
from ..logger import log_debug

# Back-compat re-exports: callers and tests historically found the whole
# Vectorize surface on this module before it was split.
from .vectorize_layer import (  # noqa: F401
    AI_EDIT_GPKG_FILENAME,
    apply_class_style,
    build_vector_layer,
    friendly_vector_layer_name,
    make_layer_permanent,
    set_layer_provenance,
    transplant_features,
)
from .vectorize_palette import detect_classes, dominant_palette  # noqa: F401


def _open_rgb(raster_path: str):
    """Read the raster's RGB bands with all input validation applied.

    Returns ``(r, g, b, geotransform, projection_wkt, width, height)`` as
    numpy int16 arrays. Raises ``AIEditError`` on any unusable input."""
    if np is None:
        raise AIEditError(
            ErrorCode.INVALID_RASTER,
            tr("Vectorize needs numpy, which failed to load. Please update QGIS or contact support."),
        )
    if not raster_path or not os.path.exists(raster_path):
        raise AIEditError(
            ErrorCode.INVALID_RASTER,
            tr("Raster layer has no on-disk source file"),
        )
    src = gdal.Open(raster_path)
    if src is None:
        raise AIEditError(ErrorCode.INVALID_RASTER, tr("Could not open raster"))
    if src.RasterCount < 3:
        raise AIEditError(
            ErrorCode.INVALID_RASTER,
            tr("Raster must have at least 3 bands (RGB)"),
        )
    width, height = src.RasterXSize, src.RasterYSize
    # Defensive memory ceiling. We read 3 bands and build int16 + mask transients,
    # so peak RAM is roughly width*height*12 bytes. Refuse above ~1 GB of that
    # estimate (~83 megapixels) early, with a clear localized message, rather
    # than OOM the QGIS process.
    est_bytes = float(width) * float(height) * 3.0 * 4.0
    if est_bytes > 1_000_000_000:
        raise AIEditError(
            ErrorCode.RASTER_TOO_LARGE,
            tr(
                "Raster is too large for in-memory vectorize ({mp:.0f} megapixels). "
                "Crop the layer first or run a tiled workflow."
            ).format(mp=(width * height) / 1_000_000),
        )
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    if not proj:
        raise AIEditError(ErrorCode.INVALID_RASTER, tr("Raster has no CRS"))
    # A degenerate geotransform (no real pixel size) would make min_area and
    # simplify_tol collapse to 0 and emit thousands of single-pixel polygons.
    if not gt or gt[1] == 0 or gt[5] == 0:
        raise AIEditError(
            ErrorCode.INVALID_RASTER, tr("Raster has no usable georeferencing.")
        )
    r = src.GetRasterBand(1).ReadAsArray()
    g = src.GetRasterBand(2).ReadAsArray()
    b = src.GetRasterBand(3).ReadAsArray()
    src = None
    # GDAL returns None (it does not raise) when a band read fails on a corrupt
    # or truncated file; guard so we surface a clean error, not an AttributeError.
    if r is None or g is None or b is None:
        raise AIEditError(
            ErrorCode.INVALID_RASTER,
            tr("Could not read raster pixels (the file may be incomplete)."),
        )
    return (
        r.astype(np.int16),
        g.astype(np.int16),
        b.astype(np.int16),
        gt,
        proj,
        width,
        height,
    )


def _make_measurer(raster_crs, transform_context, ellipsoid: str) -> QgsDistanceArea:
    """Geodesic measurer: true metres even when the layer CRS is in degrees.
    Built from the passed-in project context so this stays off-thread safe."""
    measurer = QgsDistanceArea()
    if raster_crs is not None and raster_crs.isValid():
        measurer.setSourceCrs(raster_crs, transform_context)
    # QgsProject.ellipsoid() returns "NONE" (truthy) when no measurement
    # ellipsoid is set; treat it like empty so area_m2 stays geodesic metres.
    if not ellipsoid or ellipsoid == "NONE":
        ellipsoid = "EPSG:7030"
    measurer.setEllipsoid(ellipsoid)
    return measurer


def _inset_border(mask, expand_value: int) -> None:
    """Erase a thin border of pixels IN PLACE so Polygonize can never trace the
    AI Edit zone's bounding rectangle. Without this, dilate or fill_holes pushes
    the mask to the raster edge and produces a giant frame-shaped polygon.
    Inset scales with expand_value so a heavy dilate still gets cropped back."""
    border_inset = max(2, int(expand_value) + 2) if expand_value > 0 else 2
    if mask.shape[0] > 2 * border_inset and mask.shape[1] > 2 * border_inset:
        mask[:border_inset, :] = 0
        mask[-border_inset:, :] = 0
        mask[:, :border_inset] = 0
        mask[:, -border_inset:] = 0


def _trace_mask(
    mask,
    gt,
    proj,
    *,
    sieve_threshold: int,
    min_pixels: int,
    simplify_factor: float,
    round_corners: bool,
    class_label: str,
    class_color_hex: str,
    measurer: QgsDistanceArea,
    next_fid: int,
    is_cancelled=None,
):
    """Sieve + polygonize one class mask and build its features.

    Returns ``(features, next_fid)`` or ``None`` if cancelled mid-way."""
    width = mask.shape[1]
    height = mask.shape[0]
    mem_raster_driver = gdal.GetDriverByName("MEM")
    mask_ds = mem_raster_driver.Create("", width, height, 1, gdal.GDT_Byte)
    mask_ds.SetGeoTransform(gt)
    mask_ds.SetProjection(proj)
    mask_band = mask_ds.GetRasterBand(1)
    mask_band.WriteArray(mask)
    mask_band.FlushCache()

    if sieve_threshold > 0:
        # In-place noise removal - drops connected components smaller than threshold.
        gdal.SieveFilter(
            srcBand=mask_band,
            maskBand=None,
            dstBand=mask_band,
            threshold=int(sieve_threshold),
            connectedness=8,
        )

    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(proj)

    ogr_driver = ogr.GetDriverByName("Memory")
    ogr_ds = ogr_driver.CreateDataSource("vec")
    ogr_layer = ogr_ds.CreateLayer("polys", spatial_ref, ogr.wkbPolygon)
    ogr_layer.CreateField(ogr.FieldDefn("value", ogr.OFTInteger))

    gdal.Polygonize(mask_band, None, ogr_layer, 0, ["8CONNECTED=8"])

    # Cancellation checkpoint after the expensive GDAL polygonize.
    if is_cancelled is not None and is_cancelled():
        return None

    pixel_area = abs(gt[1] * gt[5])
    min_area = pixel_area * float(min_pixels)
    simplify_tol = (pixel_area ** 0.5) * simplify_factor

    feats: list[QgsFeature] = []
    ogr_layer.ResetReading()
    for ogr_feat in ogr_layer:
        if next_fid % 256 == 0 and is_cancelled is not None and is_cancelled():
            return None
        if ogr_feat.GetField("value") != 1:
            continue
        geom_ref = ogr_feat.GetGeometryRef()
        if geom_ref is None:
            continue
        geom = QgsGeometry.fromWkt(geom_ref.ExportToWkt())
        if geom.isEmpty() or geom.area() < min_area:
            continue
        if simplify_tol > 0:
            simplified = geom.simplify(simplify_tol)
            if not simplified.isEmpty():
                geom = simplified
        if round_corners:
            # Chaikin smoothing - 5 iterations matches AI Segmentation.
            smoothed = geom.smooth(5, 0.25)
            if not smoothed.isEmpty():
                geom = smoothed
        # Simplify/smooth can self-intersect; downstream tools and GeoPackage
        # expect valid rings. makeValid may split a bowtie into several
        # polygons: emit one feature per part.
        geoms = [geom]
        if not geom.isGeosValid():
            fixed = geom.makeValid()
            source_parts = fixed.asGeometryCollection() if fixed.isMultipart() else [fixed]
            parts = []
            for part in source_parts:
                if part.isEmpty():
                    continue
                if part.type() == QgsWkbTypes.GeometryType.PolygonGeometry and part.area() >= min_area:
                    parts.append(part)
            geoms = parts or [geom]
        for part in geoms:
            area_m2 = float(measurer.measureArea(part))
            feat = QgsFeature()
            feat.setGeometry(part)
            feat.setAttributes([
                next_fid,
                class_label,
                class_color_hex,
                area_m2,
            ])
            feats.append(feat)
            next_fid += 1

    mask_ds = None
    ogr_ds = None
    return feats, next_fid


def compute_class_features(
    *,
    raster_path: str,
    raster_crs,
    transform_context,
    ellipsoid: str,
    classes: list[dict],
    competitors: list[tuple[int, int, int]] | tuple = (),
    tolerance: int = 90,
    sieve_threshold: int = 10,
    min_pixels: int = 50,
    simplify_factor: float = 1.0,
    round_corners: bool = False,
    expand_value: int = 0,
    fill_holes: bool = False,
    is_cancelled=None,
) -> list | None:
    """Heavy, thread-safe core of Vectorize: extract EVERY selected class in one
    pass by nearest-color assignment.

    Each pixel goes to the closest color among ``classes`` (traced) and
    ``competitors`` (the map's other colors, absorbed but not traced), so class
    boundaries split at their true position and one class can never bleed into
    its neighbor - the fix for "the output contained a lot of extra geometry".
    ``tolerance`` is a max color distance guard (per-channel scale, 0-255) that
    leaves genuinely foreign pixels (photo areas, gradients) unassigned.

    ``classes`` is ``[{"rgb": (r,g,b), "label": str}, ...]``. Builds NO
    ``QgsVectorLayer`` and reads NO ``QgsProject``, so it is safe inside a
    ``QgsTask``. Returns a list of ``QgsFeature`` (feature_id continuous across
    classes), or ``None`` if ``is_cancelled()`` fired. Raises ``AIEditError``
    on invalid input or an all-empty result.
    """
    if not classes:
        raise AIEditError(
            ErrorCode.NO_PIXELS_MATCHED,
            tr("Select at least one color to vectorize."),
        )
    ri, gi, bi, gt, proj, _w, _h = _open_rgb(raster_path)
    log_debug(
        f"Vectorize: src={raster_path} classes={len(classes)} "
        f"competitors={len(competitors)} tol={tolerance}"
    )

    palette = [tuple(c["rgb"]) for c in classes] + [tuple(c) for c in competitors]
    best_dist = np.full(ri.shape, 32767, dtype=np.int16)
    best_idx = np.full(ri.shape, 255, dtype=np.uint8)
    for idx, (cr, cg, cb) in enumerate(palette):
        d = np.abs(ri - cr) + np.abs(gi - cg) + np.abs(bi - cb)
        better = d < best_dist
        best_dist[better] = d[better]
        best_idx[better] = idx
        if is_cancelled is not None and is_cancelled():
            return None
    # Guard scaled to the summed-channel distance; pixels farther than this
    # from every palette color stay unassigned (photo textures, gradients).
    assigned = best_dist <= int(tolerance) * 3

    measurer = _make_measurer(raster_crs, transform_context, ellipsoid)

    feats: list[QgsFeature] = []
    next_fid = 1
    for class_idx, cls in enumerate(classes):
        if is_cancelled is not None and is_cancelled():
            return None
        mask = ((best_idx == class_idx) & assigned).astype(np.uint8)
        if int(mask.sum()) == 0:
            continue
        # Mask-level morphological refinement (expand/contract then fill holes).
        # Same order as AI Segmentation's apply_mask_refinement.
        if expand_value != 0 or fill_holes:
            mask = _refine_mask(mask, expand_value=expand_value, fill_holes=fill_holes)
            if int(mask.sum()) == 0:
                continue
        _inset_border(mask, expand_value)
        traced = _trace_mask(
            mask,
            gt,
            proj,
            sieve_threshold=sieve_threshold,
            min_pixels=min_pixels,
            simplify_factor=simplify_factor,
            round_corners=round_corners,
            class_label=cls.get("label", ""),
            class_color_hex="#{:02X}{:02X}{:02X}".format(*cls["rgb"]),
            measurer=measurer,
            next_fid=next_fid,
            is_cancelled=is_cancelled,
        )
        if traced is None:
            return None
        class_feats, next_fid = traced
        feats.extend(class_feats)

    if not feats:
        raise AIEditError(
            ErrorCode.NO_PIXELS_MATCHED,
            tr(
                "No polygons found for the selected colors "
                "(try a wider tolerance or smaller min size)"
            ),
        )
    log_debug(f"Vectorize computed {len(feats)} polygons across {len(classes)} classes")
    return feats


def _compute_vector_features(
    *,
    raster_path: str,
    raster_crs,
    transform_context,
    ellipsoid: str,
    target_rgb: tuple[int, int, int],
    tolerance: int,
    sieve_threshold: int,
    min_pixels: int,
    simplify_factor: float,
    round_corners: bool,
    expand_value: int,
    fill_holes: bool,
    class_label: str,
    match_mode: str = "box",
    background_rgb: tuple[int, int, int] | None = None,
    is_cancelled=None,
) -> list | None:
    """Single-color compute kept for ``vectorize_by_color`` callers.

    ``match_mode="nearest"`` is the multi-class engine with one traced class and
    the background as sole competitor. ``match_mode="box"`` keeps the legacy
    per-channel ±tolerance match (exact-color workflows).
    """
    if match_mode == "nearest":
        bg = background_rgb if background_rgb is not None else (255, 255, 255)
        return compute_class_features(
            raster_path=raster_path,
            raster_crs=raster_crs,
            transform_context=transform_context,
            ellipsoid=ellipsoid,
            classes=[{"rgb": target_rgb, "label": class_label}],
            competitors=[bg],
            tolerance=tolerance,
            sieve_threshold=sieve_threshold,
            min_pixels=min_pixels,
            simplify_factor=simplify_factor,
            round_corners=round_corners,
            expand_value=expand_value,
            fill_holes=fill_holes,
            is_cancelled=is_cancelled,
        )

    ri, gi, bi, gt, proj, _w, _h = _open_rgb(raster_path)
    tr_r, tg_g, tb_b = target_rgb
    mask_r = np.abs(ri - tr_r) <= tolerance
    mask_g = np.abs(gi - tg_g) <= tolerance
    mask_b = np.abs(bi - tb_b) <= tolerance
    mask = (mask_r & mask_g & mask_b).astype(np.uint8)
    if int(mask.sum()) == 0:
        raise AIEditError(
            ErrorCode.NO_PIXELS_MATCHED,
            tr("No pixels matched the selected color"),
        )
    if expand_value != 0 or fill_holes:
        mask = _refine_mask(mask, expand_value=expand_value, fill_holes=fill_holes)
        if int(mask.sum()) == 0:
            raise AIEditError(
                ErrorCode.NO_PIXELS_MATCHED,
                tr("No pixels matched the selected color"),
            )
    _inset_border(mask, expand_value)
    measurer = _make_measurer(raster_crs, transform_context, ellipsoid)
    traced = _trace_mask(
        mask,
        gt,
        proj,
        sieve_threshold=sieve_threshold,
        min_pixels=min_pixels,
        simplify_factor=simplify_factor,
        round_corners=round_corners,
        class_label=class_label,
        class_color_hex="#{:02X}{:02X}{:02X}".format(*target_rgb),
        measurer=measurer,
        next_fid=1,
        is_cancelled=is_cancelled,
    )
    if traced is None:
        return None
    feats, _ = traced
    if not feats:
        raise AIEditError(
            ErrorCode.NO_PIXELS_MATCHED,
            tr(
                "No polygons remained after filtering "
                "(try a wider tolerance or smaller min size)"
            ),
        )
    return feats


def vectorize_by_color(
    raster_layer: QgsRasterLayer,
    target_rgb: tuple[int, int, int],
    tolerance: int = 40,
    sieve_threshold: int = 10,
    min_pixels: int = 50,
    simplify_factor: float = 1.5,
    layer_name: str | None = None,
    output_rgb: tuple[int, int, int] | None = None,
    round_corners: bool = False,
    expand_value: int = 0,
    fill_holes: bool = False,
    class_label: str = "",
    match_mode: str = "box",
    background_rgb: tuple[int, int, int] | None = None,
) -> QgsVectorLayer:
    """Extract pixels for ``target_rgb`` as polygons.

    Synchronous (main-thread) convenience wrapper over the split compute/build
    helpers, for callers not running inside a QgsTask. ``output_rgb`` overrides
    the style colour. Returns a styled in-memory ``QgsVectorLayer`` not yet
    added to the project.
    """
    project = QgsProject.instance()
    feats = _compute_vector_features(
        raster_path=(raster_layer.source() or "").split("|", 1)[0],
        raster_crs=raster_layer.crs(),
        transform_context=project.transformContext(),
        ellipsoid=project.ellipsoid() or "EPSG:7030",
        target_rgb=target_rgb,
        tolerance=tolerance,
        sieve_threshold=sieve_threshold,
        min_pixels=min_pixels,
        simplify_factor=simplify_factor,
        round_corners=round_corners,
        expand_value=expand_value,
        fill_holes=fill_holes,
        class_label=class_label,
        match_mode=match_mode,
        background_rgb=background_rgb,
    )
    return build_vector_layer(
        feats or [],
        raster_layer.crs(),
        layer_name or "Vector",
        [{"rgb": output_rgb or target_rgb, "label": class_label}],
        source_raster_name=raster_layer.name() or "",
    )


def _refine_mask(
    mask,
    expand_value: int = 0,
    fill_holes: bool = False,
):
    """Dilate/erode then optionally fill interior holes. scipy fast-path,
    pure-numpy fallback. Same order as AI Segmentation's apply_mask_refinement.
    """
    result = mask.astype(np.uint8).copy()
    if expand_value != 0:
        iterations = abs(int(expand_value))
        try:
            from scipy import ndimage
            structure = ndimage.generate_binary_structure(2, 1)
            if expand_value > 0:
                result = ndimage.binary_dilation(
                    result, structure=structure, iterations=iterations
                ).astype(np.uint8)
            else:
                result = ndimage.binary_erosion(
                    result, structure=structure, iterations=iterations
                ).astype(np.uint8)
        except ImportError:
            result = _numpy_morphology(result, iterations, expand=expand_value > 0)
    if fill_holes:
        try:
            from scipy import ndimage
            result = ndimage.binary_fill_holes(result).astype(np.uint8)
        except ImportError:
            result = _numpy_fill_holes(result)
    return result


def _numpy_morphology(mask, iterations: int, expand: bool):
    """Pure-numpy 4-connected dilation/erosion fallback when scipy missing."""
    result = mask.copy()
    for _ in range(iterations):
        shifted = result.copy()
        shifted[1:, :] |= result[:-1, :]
        shifted[:-1, :] |= result[1:, :]
        shifted[:, 1:] |= result[:, :-1]
        shifted[:, :-1] |= result[:, 1:]
        if expand:
            result = shifted
        else:
            shrunk = result.copy()
            shrunk[1:, :] &= result[:-1, :]
            shrunk[:-1, :] &= result[1:, :]
            shrunk[:, 1:] &= result[:, :-1]
            shrunk[:, :-1] &= result[:, 1:]
            result = shrunk
    return result


def _numpy_fill_holes(mask):
    """Pure-numpy flood-fill from borders to mark exterior, invert for holes."""
    h, w = mask.shape
    padded = np.zeros((h + 2, w + 2), dtype=np.uint8)
    padded[1:-1, 1:-1] = mask
    exterior = np.zeros_like(padded, dtype=bool)
    exterior[0, :] = padded[0, :] == 0
    exterior[-1, :] = padded[-1, :] == 0
    exterior[:, 0] = padded[:, 0] == 0
    exterior[:, -1] = padded[:, -1] == 0
    background = padded == 0
    for _ in range(min(max(h, w), 2048)):
        expanded = exterior.copy()
        expanded[1:, :] |= exterior[:-1, :]
        expanded[:-1, :] |= exterior[1:, :]
        expanded[:, 1:] |= exterior[:, :-1]
        expanded[:, :-1] |= exterior[:, 1:]
        expanded &= background
        if np.array_equal(expanded, exterior):
            break
        exterior = expanded
    result = padded.copy()
    result[(padded == 0) & (~exterior)] = 1
    return result[1:-1, 1:-1]
