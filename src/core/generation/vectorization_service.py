"""Color-based raster vectorization using direct GDAL API.

Avoids QGIS Processing chaining (TEMPORARY_OUTPUT references can vanish
between gdal:* steps on macOS). Everything runs in-memory via GDAL MEM /
OGR Memory drivers; filtering + simplification happen in Python on
QgsGeometry objects.
"""
from __future__ import annotations

import os
import time

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
    QgsDefaultValue,
    QgsDistanceArea,
    QgsEditorWidgetSetup,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QDate, QLocale, Qt

from ..errors import AIEditError, ErrorCode
from ..i18n import tr
from ..logger import log_debug, log_warning

# One GeoPackage next to the generated rasters holds every vectorize run (one
# table per run). Hoisted to a constant so the filename lives in exactly one
# place instead of being spelled inline at the call site.
AI_EDIT_GPKG_FILENAME = "ai_edit.gpkg"


def _plugin_version() -> str:
    """Plugin version from metadata.txt (best-effort, '' if unreadable)."""
    root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    )
    try:
        with open(os.path.join(root, "metadata.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass  # nosec B110 - version is cosmetic provenance, never block a run
    return ""


def _project_layer_names() -> set[str]:
    """Names of every layer currently in the project (for name dedup)."""
    names: set[str] = set()
    try:
        for layer in QgsProject.instance().mapLayers().values():
            if layer is not None:
                names.add(layer.name())
    except Exception:  # nosec B110 - dedup is best-effort, never block a run
        pass
    return names


def friendly_vector_layer_name(class_label: str, raster_name: str) -> str:
    """Friendly, dated tree name for a vectorize result, e.g. "Buildings (3 Jul)".

    Uses the class label when known (only the first letter is capitalized, the
    rest stays as typed), else falls back to "<raster> (vector)". The date is
    locale-short, and a same-name-same-day rerun becomes "Buildings 2 (3 Jul)"
    by scanning existing project layer names. Mirrors AI Segmentation's
    friendly_layer_name."""
    label = (class_label or "").strip()
    if label:
        base = label[0].upper() + label[1:]
    else:
        raster = (raster_name or "").strip()
        base = f"{raster} (vector)" if raster else tr("Vector")
    date_str = QLocale().toString(QDate.currentDate(), "d MMM")
    existing = _project_layer_names()
    candidate = f"{base} ({date_str})"
    counter = 2
    while candidate in existing:
        candidate = f"{base} {counter} ({date_str})"
        counter += 1
    return candidate


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
    """Heavy, thread-safe core of Vectorize: GDAL read + mask + sieve +
    polygonize + per-feature geometry build/measure.

    Builds NO ``QgsVectorLayer`` and reads NO ``QgsProject`` (the CRS,
    transform context and ellipsoid are passed in), so it is safe to run inside
    a ``QgsTask`` off the main thread. ``QgsGeometry``/``QgsFeature``/
    ``QgsDistanceArea`` are value classes, fine off-thread.

    Returns a list of ``QgsFeature``, or ``None`` if ``is_cancelled()`` fired.
    Raises ``AIEditError`` on invalid input or an empty result.
    """
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

    log_debug(f"Vectorize: src={raster_path} rgb={target_rgb} tol={tolerance}")

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

    tr_r, tg_g, tb_b = target_rgb
    ri = r.astype(np.int16)
    gi = g.astype(np.int16)
    bi = b.astype(np.int16)
    if match_mode == "nearest":
        # Foreground extraction. Assign each pixel to the nearer of the target
        # color and the background (default white), so an anti-aliased boundary
        # splits exactly down the middle: edges land at their true position
        # (no eroded footprints, no pinhole gaps), and the match is robust to the
        # model drifting off the requested hue (a "red" that renders as #D37523
        # is still far closer to red than to white). `tolerance` becomes a max
        # color distance guard (summed over channels) so genuinely different
        # hues in a multi-color map are still rejected instead of swept in.
        bg = background_rgb if background_rgb is not None else (255, 255, 255)
        d_fg = np.abs(ri - tr_r) + np.abs(gi - tg_g) + np.abs(bi - tb_b)
        d_bg = np.abs(ri - bg[0]) + np.abs(gi - bg[1]) + np.abs(bi - bg[2])
        max_dist = int(tolerance) * 3
        mask = ((d_fg < d_bg) & (d_fg <= max_dist)).astype(np.uint8)
    else:
        mask_r = np.abs(ri - tr_r) <= tolerance
        mask_g = np.abs(gi - tg_g) <= tolerance
        mask_b = np.abs(bi - tb_b) <= tolerance
        mask = (mask_r & mask_g & mask_b).astype(np.uint8)
    if int(mask.sum()) == 0:
        raise AIEditError(
            ErrorCode.NO_PIXELS_MATCHED,
            tr("No pixels matched the selected color"),
        )

    # Mask-level morphological refinement (expand/contract then fill holes).
    # Same order as AI Segmentation's apply_mask_refinement so behavior matches.
    if expand_value != 0 or fill_holes:
        mask = _refine_mask(mask, expand_value=expand_value, fill_holes=fill_holes)
        if int(mask.sum()) == 0:
            raise AIEditError(
                ErrorCode.NO_PIXELS_MATCHED,
                tr("No pixels matched the selected color"),
            )

    # Erase a thin border of pixels so Polygonize can never trace the AI Edit
    # zone's bounding rectangle. Without this, dilate or fill_holes pushes the
    # mask to the raster edge and produces a giant frame-shaped polygon.
    # Inset scales with expand_value so a heavy dilate still gets cropped back.
    border_inset = max(2, int(expand_value) + 2) if expand_value > 0 else 2
    if mask.shape[0] > 2 * border_inset and mask.shape[1] > 2 * border_inset:
        mask[:border_inset, :] = 0
        mask[-border_inset:, :] = 0
        mask[:, :border_inset] = 0
        mask[:, -border_inset:] = 0

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

    # Geodesic measurer: true metres even when the layer CRS is in degrees.
    # Built from the passed-in project context so this stays off-thread safe.
    measurer = QgsDistanceArea()
    if raster_crs is not None and raster_crs.isValid():
        measurer.setSourceCrs(raster_crs, transform_context)
    # QgsProject.ellipsoid() returns "NONE" (truthy) when no measurement
    # ellipsoid is set; treat it like empty so area_m2 stays geodesic metres.
    if not ellipsoid or ellipsoid == "NONE":
        ellipsoid = "EPSG:7030"
    measurer.setEllipsoid(ellipsoid)

    class_color_hex = "#{:02X}{:02X}{:02X}".format(*target_rgb)

    feats: list[QgsFeature] = []
    next_fid = 1
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

    if not feats:
        raise AIEditError(
            ErrorCode.NO_PIXELS_MATCHED,
            tr(
                "No polygons remained after filtering "
                "(try a wider tolerance or smaller min size)"
            ),
        )
    log_debug(f"Vectorize computed {len(feats)} polygons (min_area={min_area:.2f} map_units²)")
    return feats


def _build_vector_layer(
    feats: list,
    raster_crs,
    layer_name: str,
    target_rgb: tuple[int, int, int],
    output_rgb: tuple[int, int, int] | None,
    class_label: str,
    source_raster_name: str = "",
) -> QgsVectorLayer:
    """Build the styled in-memory polygon layer from precomputed features.

    Creates a ``QgsVectorLayer`` and adds features, so it MUST run on the main
    thread (call from a QgsTask's ``finished()``, never from ``run()``)."""
    # Minimal per-feature schema (industry pattern: stable machine code in
    # class_color + free-text label in class_name, plus the geodesic measure).
    # Run-level provenance lives in the layer metadata, not repeated per row.
    # CRS-agnostic URI + explicit setCrs(): EPSG:4326 fallback would corrupt alignment.
    mem_layer = QgsVectorLayer(
        (
            "Polygon"
            "?field=feature_id:integer"
            # Generous length: the memory provider silently DROPS any feature
            # whose value overflows a field, so a too-short field makes the
            # whole layer come back empty. class_name is user-editable.
            "&field=class_name:string(254)"
            "&field=class_color:string(9)"
            "&field=area_m2:double"
        ),
        layer_name,
        "memory",
    )
    if raster_crs is not None and raster_crs.isValid():
        mem_layer.setCrs(raster_crs)
    mem_provider = mem_layer.dataProvider()
    mem_provider.addFeatures(feats)
    mem_layer.updateExtents()
    # The memory provider drops features whose attributes overflow a field
    # instead of raising. Guard against that so we never hand back an empty
    # layer while reporting success ("Vectorize done: N" but 0 on the map).
    if mem_layer.featureCount() != len(feats):
        raise AIEditError(
            ErrorCode.WRITE_ERROR,
            tr("Could not store the vectorized polygons (internal field error)."),
        )
    _configure_attribute_table(mem_layer, class_label)
    _set_layer_provenance(mem_layer, source_raster_name, target_rgb, class_label)
    # Style the polygons in the colour the user picked, so a red selection traces
    # red. The source raster is hidden after vectorizing, so a same-colour vector
    # never blends into it.
    style_rgb = output_rgb if output_rgb is not None else target_rgb
    _apply_style(mem_layer, style_rgb)
    log_debug(f"Vectorize layer built: {mem_layer.featureCount()} polygons")
    return mem_layer


def make_layer_permanent(
    mem_layer: QgsVectorLayer,
    gpkg_path: str,
    table_name: str,
    target_rgb: tuple[int, int, int],
    class_label: str,
    source_raster_name: str = "",
) -> QgsVectorLayer | None:
    """Persist the freshly built vector layer into the output GeoPackage and
    return the disk-backed replacement, or None to keep the memory layer.

    Memory layers silently vanish when the project closes; GeoPackage is the
    QGIS-native container, so each run becomes one table in ai_edit.gpkg next
    to the generated rasters. Best-effort: any failure (locked file, read-only
    folder) keeps the volatile in-memory layer instead of failing the run.
    Main-thread only (creates layers, reads QgsProject)."""
    from qgis.core import QgsVectorFileWriter

    try:
        os.makedirs(os.path.dirname(gpkg_path), exist_ok=True)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = table_name
        options.actionOnExistingFile = (
            QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
            if os.path.exists(gpkg_path)
            else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile
        )
        writer = (
            QgsVectorFileWriter.writeAsVectorFormatV3
            if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3")
            else QgsVectorFileWriter.writeAsVectorFormatV2
        )
        res = writer(
            mem_layer, gpkg_path, QgsProject.instance().transformContext(), options
        )
        code = res[0] if isinstance(res, tuple) else res
        if code != QgsVectorFileWriter.WriterError.NoError:
            log_warning(f"Vectorize: GeoPackage write failed ({res}), keeping memory layer")
            return None
        layer = QgsVectorLayer(
            f"{gpkg_path}|layername={table_name}", mem_layer.name(), "ogr"
        )
        if not layer.isValid() or layer.featureCount() != mem_layer.featureCount():
            log_warning("Vectorize: GeoPackage layer failed to load back, keeping memory layer")
            return None
    except Exception as err:  # noqa: BLE001 - persistence is best-effort
        log_warning(f"Vectorize: GeoPackage persist skipped ({err})")
        return None

    _configure_attribute_table(layer, class_label)
    _set_layer_provenance(layer, source_raster_name, target_rgb, class_label)
    _apply_style(layer, target_rgb)
    try:
        # Stored inside the GeoPackage, so the style survives outside this project.
        layer.saveStyleToDatabase(table_name, "AI Edit Vectorize", True, "")
    except Exception:  # nosec B110 - cosmetic only
        pass
    log_debug(f"Vectorize layer persisted: {gpkg_path}|{table_name}")
    return layer


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

    ``match_mode="box"`` keeps the legacy per-channel ±tolerance match.
    ``match_mode="nearest"`` extracts every pixel closer to ``target_rgb`` than
    to ``background_rgb`` (default white) within a ``tolerance``-scaled distance,
    which tracks the true class boundary and tolerates the model drifting off the
    requested hue. Synchronous (main-thread) convenience wrapper over the split
    compute/build helpers, for callers not running inside a QgsTask.
    ``output_rgb`` controls the outline colour. Returns a styled in-memory
    ``QgsVectorLayer`` not yet added to the project.
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
    return _build_vector_layer(
        feats or [], raster_layer.crs(), layer_name or "Vector",
        target_rgb, output_rgb, class_label,
        source_raster_name=raster_layer.name() or "",
    )


def dominant_palette(
    raster_path: str,
    max_colors: int = 4,
    quant: int = 24,
    merge_l1: int = 140,
    min_fraction: float = 0.03,
    sample_max: int = 1_000_000,
) -> list[tuple[tuple[int, int, int], float]]:
    """Detect the dominant flat colors in a generated map.

    A "2-color" map actually carries thousands of anti-aliased shades, so this
    quantizes, then merges near shades (within ``merge_l1`` summed-channel
    distance) into their most-common base color. Returns ``[((r,g,b), fraction),
    ...]`` sorted by coverage. Lets the Vectorize panel offer one-click
    "extract this color" chips instead of an eyedropper hunt. Pure numpy + GDAL,
    decimated to stay fast on 4K rasters; safe to call on the main thread.
    """
    if np is None or not raster_path or not os.path.exists(raster_path):
        return []
    ds = gdal.Open(raster_path)
    if ds is None or ds.RasterCount < 3:
        return []
    width, height = ds.RasterXSize, ds.RasterYSize
    # The palette is scale-invariant, so read a decimated buffer (at most
    # ~sample_max px) to keep detection near-instant even on a 4K output.
    scale = max(1, int((width * height / float(sample_max)) ** 0.5))
    bw, bh = max(1, width // scale), max(1, height // scale)
    r = ds.GetRasterBand(1).ReadAsArray(buf_xsize=bw, buf_ysize=bh)
    g = ds.GetRasterBand(2).ReadAsArray(buf_xsize=bw, buf_ysize=bh)
    b = ds.GetRasterBand(3).ReadAsArray(buf_xsize=bw, buf_ysize=bh)
    ds = None
    if r is None or g is None or b is None:
        return []
    a = np.stack([r, g, b], axis=-1).reshape(-1, 3).astype(np.int32)
    q = (a // quant) * quant + quant // 2
    keys = (q[:, 0] << 16) | (q[:, 1] << 8) | q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    order = np.argsort(-counts)
    total = int(keys.size) or 1
    kept: list[list] = []  # [ [(r,g,b), count], ... ], representative = most common shade
    for idx in order:
        key = int(vals[idx])
        rr, gg, bb = (key >> 16) & 255, (key >> 8) & 255, key & 255
        cnt = int(counts[idx])
        merged = False
        for entry in kept:
            kr, kg, kb = entry[0]
            if abs(kr - rr) + abs(kg - gg) + abs(kb - bb) <= merge_l1:
                entry[1] += cnt
                merged = True
                break
        if not merged:
            kept.append([(rr, gg, bb), cnt])
        if len(kept) >= max_colors and cnt < total * 0.01:
            break
    out = [(rgb, c / total) for rgb, c in kept if c / total >= min_fraction]
    out.sort(key=lambda t: -t[1])
    return out


def _refine_mask(
    mask: np.ndarray,
    expand_value: int = 0,
    fill_holes: bool = False,
) -> np.ndarray:
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


def _numpy_morphology(mask: np.ndarray, iterations: int, expand: bool) -> np.ndarray:
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


def _numpy_fill_holes(mask: np.ndarray) -> np.ndarray:
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


def _configure_attribute_table(layer: QgsVectorLayer, class_label: str) -> None:
    """Set displayExpression, default value, editor widget and default sort
    so the user gets a readable attribute table out of the box.

    - displayExpression makes the form-view feature list show
      `<id> - <class> (<area> m2)` instead of repeating the same color hex.
    - QgsDefaultValue gives rows added manually via the table a sensible default.
    - TextEdit widget on class_name unlocks QGIS's per-column unique-values
      autocomplete so the user types once then picks from prior values.
    - Default sort by area_m2 descending puts large polygons at the top.
    """
    layer.setDisplayExpression(
        "format('%1 - %2 (%3 m²)', \"feature_id\","
        " coalesce(\"class_name\", ''), round(\"area_m2\"))"
    )

    idx = layer.fields().indexOf("class_name")
    if idx >= 0:
        escaped = (class_label or "").replace("'", "''")
        layer.setDefaultValueDefinition(idx, QgsDefaultValue(f"'{escaped}'"))
        layer.setEditorWidgetSetup(idx, QgsEditorWidgetSetup("TextEdit", {}))

    config = layer.attributeTableConfig()
    config.setSortExpression('"area_m2"')
    config.setSortOrder(Qt.SortOrder.DescendingOrder)
    layer.setAttributeTableConfig(config)


def _set_layer_provenance(
    layer: QgsVectorLayer,
    source_raster_name: str,
    target_rgb: tuple[int, int, int],
    class_label: str,
) -> None:
    """Record run-level provenance on the LAYER (Properties > Metadata), the
    QGIS convention, instead of repeating it as an attribute on every row."""
    color_hex = "#{:02X}{:02X}{:02X}".format(*target_rgb)
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    md = layer.metadata()
    md.setTitle(layer.name())
    abstract = f"Polygons traced from the color {color_hex} by AI Edit (TerraLab) Vectorize."
    if source_raster_name:
        abstract += f" Source raster: {source_raster_name}."
    if class_label:
        abstract += f" Class: {class_label}."
    version = _plugin_version()
    if version:
        abstract += f" Plugin version: {version}."
    md.setAbstract(abstract)
    history = [f"{created} vectorized from '{source_raster_name}' (color {color_hex})"]
    md.setHistory(history)
    # Keywords make the layer discoverable in the QGIS Metadata search; kept
    # defensive so a metadata quirk can never block a (paid) vectorize.
    try:
        keywords = [k for k in ("AI Edit", "Vectorize", class_label, source_raster_name) if k]
        md.addKeywords("AI Edit", keywords)
    except Exception:  # nosec B110 - metadata is cosmetic, never block a run
        pass
    layer.setMetadata(md)


def _apply_style(layer: QgsVectorLayer, output_rgb: tuple[int, int, int]) -> None:
    """Filled style in the picked color: a strong fill plus a solid outline, so
    each traced region reads as a complete (solid) polygon. The source raster is
    hidden after vectorizing, so the fill is mostly opaque (~80%) for legibility
    while still hinting at any basemap underneath."""
    r, g, b = output_rgb
    symbol = QgsFillSymbol.createSimple(
        {
            "color": f"{r},{g},{b},205",  # ~80% so the polygons read clearly
            "style": "solid",
            "outline_color": f"{r},{g},{b},255",
            "outline_width": "0.4",
            "outline_width_unit": "MM",
            "outline_style": "solid",
        }
    )
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()
