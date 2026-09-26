


















from __future__ import annotations

import math
import os





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
)

from .. import qt_compat as QtC
from ..errors import AIEditError, ErrorCode
from ..i18n import tr
from ..logger import log_debug
from ..raster_writer import (
    _safe_projection_wkt,
    memory_vector_driver,
)



from .vectorize_geometry import _clip_feats_to_crop, _make_measurer, repair_polygon_geometry
from .vectorize_layer import (
    AI_EDIT_GPKG_FILENAME,
    apply_class_style,
    build_vector_layer,
    friendly_vector_layer_name,
    make_layer_permanent,
    set_layer_provenance,
    transplant_features,
)
from .vectorize_masks import _numpy_fill_holes, _numpy_morphology, _refine_mask  # noqa: F401
from .vectorize_topology import simplify_shared


__all__ = [
    "AI_EDIT_GPKG_FILENAME",
    "apply_class_style",
    "build_vector_layer",
    "compute_class_features",
    "friendly_vector_layer_name",
    "make_layer_permanent",
    "np",
    "set_layer_provenance",
    "transplant_features",
    "vectorize_by_color",
]



_VECTORIZE_BYTES_PER_PIXEL = 24.0
_VECTORIZE_MEMORY_CEILING_BYTES = 1_000_000_000


def _read_rgb_bands(src, layer_has_crs: bool = False):





    if src.RasterCount < 3:
        raise AIEditError(
            ErrorCode.INVALID_RASTER,
            tr("Raster must have at least 3 bands (RGB)"),
        )
    width, height = src.RasterXSize, src.RasterYSize
    if width <= 0 or height <= 0:
        raise AIEditError(
                        ErrorCode.INVALID_RASTER,
                        tr("Could not read raster pixels (the file may be incomplete)."),
                    )



    est_bytes = float(width) * float(height) * _VECTORIZE_BYTES_PER_PIXEL
    if est_bytes > _VECTORIZE_MEMORY_CEILING_BYTES:
        raise AIEditError(
            ErrorCode.RASTER_TOO_LARGE,
            tr(
                "Raster is too large for in-memory vectorize ({mp:.0f} megapixels). "
                "Crop the layer first or run a tiled workflow."
            ).format(mp=(width * height) / 1_000_000),
        )
    gt = src.GetGeoTransform()
    proj = src.GetProjection()



    if not proj and not layer_has_crs:
        raise AIEditError(ErrorCode.INVALID_RASTER, tr("Raster has no CRS"))


    if (not gt or len(gt) != 6 or not all(math.isfinite(v) for v in gt)
            or abs(gt[1] * gt[5] - gt[2] * gt[4]) == 0):
        raise AIEditError(
            ErrorCode.INVALID_RASTER, tr("Raster has no usable georeferencing.")
        )
    r = src.GetRasterBand(1).ReadAsArray()
    g = src.GetRasterBand(2).ReadAsArray()
    b = src.GetRasterBand(3).ReadAsArray()


    if r is None or g is None or b is None:
        raise AIEditError(
            ErrorCode.INVALID_RASTER,
            tr("Could not read raster pixels (the file may be incomplete)."),
        )
    return r, g, b, gt, proj, width, height


def _polygon_spatial_ref(proj: str):




    wkt = _safe_projection_wkt(proj)
    if wkt is None:
        return None
    spatial_ref = osr.SpatialReference()
    try:
        if spatial_ref.ImportFromWkt(wkt) == 0:
            return spatial_ref
    except RuntimeError as err:
        log_debug(f"Vectorize: polygon layer left without CRS ({err})")
    return None


def _open_rgb(raster_path: str, layer_crs=None):




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
    try:
        src = gdal.Open(raster_path)
    except RuntimeError:
        src = None
    if src is None:
        raise AIEditError(ErrorCode.INVALID_RASTER, tr("Could not open raster"))
    layer_has_crs = layer_crs is not None and layer_crs.isValid()
    try:
        r, g, b, gt, proj, width, height = _read_rgb_bands(src, layer_has_crs)

        valid = np.ones((height, width), dtype=bool)
        for index in range(1, src.RasterCount + 1):
            band = src.GetRasterBand(index)
            if band.GetColorInterpretation() == gdal.GCI_AlphaBand:
                alpha = band.ReadAsArray()
                if alpha is None:
                    raise AIEditError(
                        ErrorCode.INVALID_RASTER,
                        tr("Could not read raster pixels (the file may be incomplete)."),
                    )
                valid &= alpha > 0
        mask_band = src.GetRasterBand(1).GetMaskBand()
        if mask_band is not None and src.GetRasterBand(1).GetMaskFlags() != gdal.GMF_ALL_VALID:
            mask = mask_band.ReadAsArray()
            if mask is None:
                raise AIEditError(
                    ErrorCode.INVALID_RASTER,
                    tr("Could not read raster pixels (the file may be incomplete)."),
                )
            valid &= mask > 0
        rgb = []
        for values in (r, g, b):
            valid &= np.isfinite(values) & (values >= 0) & (values <= 255)
            rgb.append(values.astype(np.int16))
        for values in rgb:
            values[~valid] = -1024
        r, g, b = rgb
    except Exception as err:


        src = None
        raise err.with_traceback(None) from None
    src = None
    return (
        r,
        g,
        b,
        gt,
        proj,
        width,
        height,
    )


def _inset_border(mask, expand_value: int) -> None:




    border_inset = max(2, int(expand_value) + 2) if expand_value > 0 else 2
    if mask.shape[0] > 2 * border_inset and mask.shape[1] > 2 * border_inset:
        mask[:border_inset, :] = 0
        mask[-border_inset:, :] = 0
        mask[:, :border_inset] = 0
        mask[:, -border_inset:] = 0


def _emit_traced_polygon(
    geom: QgsGeometry,
    *,
    min_area: float,
    simplify_tol: float,
    round_corners: bool,
    class_label: str,
    class_color_hex: str,
    measurer: QgsDistanceArea,
    next_fid: int,
    feats: list,
) -> int:



    if simplify_tol > 0:
        simplified = geom.simplify(simplify_tol)
        if not simplified.isEmpty():
            geom = simplified
    if round_corners:

        smoothed = geom.smooth(5, 0.25)
        if not smoothed.isEmpty():
            geom = smoothed



    if geom.isEmpty() or geom.type() != QtC.PolygonGeometry or geom.area() < min_area:
        return next_fid
    geoms = [geom]
    if not geom.isGeosValid():
        fixed = repair_polygon_geometry(geom)
        source_parts = fixed.asGeometryCollection() if fixed.isMultipart() else [fixed]
        parts = []
        for part in source_parts:
            if part.isEmpty():
                continue
            if part.type() == QtC.PolygonGeometry and part.area() >= min_area:
                parts.append(part)
        geoms = parts
    for part in geoms:
        area_m2 = float(measurer.measureArea(part))
        if not math.isfinite(area_m2) or area_m2 <= 0:
            continue
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
    return next_fid


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




        gdal.SieveFilter(
            srcBand=mask_band,
            maskBand=None,
            dstBand=mask_band,
            threshold=int(sieve_threshold),
            connectedness=4,
        )

    spatial_ref = _polygon_spatial_ref(proj)

    ogr_driver = memory_vector_driver()
    ogr_ds = ogr_driver.CreateDataSource("vec")
    ogr_layer = ogr_ds.CreateLayer("polys", spatial_ref, ogr.wkbPolygon)
    ogr_layer.CreateField(ogr.FieldDefn("value", ogr.OFTInteger))

    gdal.Polygonize(mask_band, mask_band, ogr_layer, 0, [])


    if is_cancelled is not None and is_cancelled():
        return None

    pixel_area = abs(gt[1] * gt[5] - gt[2] * gt[4])
    min_area = pixel_area * float(min_pixels)

    raw = []
    ogr_layer.ResetReading()
    for seen, ogr_feat in enumerate(ogr_layer):
        if seen % 256 == 0 and is_cancelled is not None and is_cancelled():
            return None
        if ogr_feat.GetField("value") != 1:
            continue
        geom_ref = ogr_feat.GetGeometryRef()
        if geom_ref is None or geom_ref.GetArea() < min_area:
            continue
        raw.append(geom_ref.Clone())

    if simplify_factor > 0 or round_corners:
        geoms = simplify_shared(
            raw, gt, float(simplify_factor), round_corners, is_cancelled, (width, height)
        )
        if geoms is None:
            return None
    else:
        geoms = [QgsGeometry.fromWkt(g.ExportToWkt()) for g in raw]

    feats: list[QgsFeature] = []
    for geom in geoms:
        next_fid = _emit_traced_polygon(
            geom,
            min_area=min_area,
            simplify_tol=0.0,
            round_corners=False,
            class_label=class_label,
            class_color_hex=class_color_hex,
            measurer=measurer,
            next_fid=next_fid,
            feats=feats,
        )

    del mask_ds
    del ogr_ds
    return feats, next_fid


def _class_mask(best_idx, assigned, class_idx: int, expand_value: int, fill_holes: bool):

    mask = ((best_idx == class_idx) & assigned).astype(np.uint8)
    if not np.any(mask):
        return None


    if expand_value != 0 or fill_holes:
        mask = _refine_mask(mask, expand_value=expand_value, fill_holes=fill_holes)
        if not np.any(mask):
            return None
    _inset_border(mask, expand_value)
    return mask if np.any(mask) else None




_MAX_BATCHED_CLASSES = 254


def _trace_classes_batched(
    best_idx,
    assigned,
    classes: list[dict],
    gt,
    proj,
    *,
    sieve_threshold: int,
    min_pixels: int,
    simplify_factor: float,
    round_corners: bool,
    expand_value: int,
    fill_holes: bool,
    measurer: QgsDistanceArea,
    is_cancelled=None,
) -> tuple[bool, list | None]:





















    if len(classes) > _MAX_BATCHED_CLASSES:
        return False, None
    height, width = best_idx.shape
    label_array = np.zeros(best_idx.shape, dtype=np.uint8)
    label_classes: dict[int, tuple[str, str]] = {}

    for class_idx, cls in enumerate(classes):
        if is_cancelled is not None and is_cancelled():
            return True, None
        stamp = (best_idx == class_idx) & assigned
        if not np.any(stamp):
            continue
        label_value = len(label_classes) + 1
        np.copyto(label_array, np.uint8(label_value), where=stamp)
        label_classes[label_value] = (
            cls.get("label", ""),
            "#{:02X}{:02X}{:02X}".format(*cls["rgb"]),
        )
    if not label_classes:
        return True, []


    other_label = len(label_classes) + 1
    others = assigned & (best_idx >= len(classes))
    np.copyto(label_array, np.uint8(other_label), where=others)
    del others

    mem_raster_driver = gdal.GetDriverByName("MEM")
    label_ds = mem_raster_driver.Create("", width, height, 1, gdal.GDT_Byte)
    label_ds.SetGeoTransform(gt)
    label_ds.SetProjection(proj)
    label_band = label_ds.GetRasterBand(1)
    label_band.WriteArray(label_array)






    threshold = max(int(sieve_threshold), int(min_pixels))
    if threshold > 0:
        gdal.SieveFilter(
            srcBand=label_band,
            maskBand=None,
            dstBand=label_band,
            threshold=threshold,
            connectedness=4,
        )
        if is_cancelled is not None and is_cancelled():
            return True, None
        label_array = label_band.ReadAsArray()
    if expand_value != 0 or fill_holes:

        for label_value in label_classes:
            if is_cancelled is not None and is_cancelled():
                return True, None
            mask = label_array == label_value
            refined = _refine_mask(mask, expand_value=expand_value, fill_holes=fill_holes) != 0
            label_array[mask & ~refined] = 0
            free = (label_array == 0) | (label_array == other_label)
            label_array[refined & ~mask & free] = label_value
        label_band.WriteArray(label_array)
    label_band.FlushCache()
    del label_array

    spatial_ref = _polygon_spatial_ref(proj)
    ogr_driver = memory_vector_driver()
    ogr_ds = ogr_driver.CreateDataSource("vec")
    ogr_layer = ogr_ds.CreateLayer("polys", spatial_ref, ogr.wkbPolygon)
    ogr_layer.CreateField(ogr.FieldDefn("value", ogr.OFTInteger))


    gdal.Polygonize(label_band, label_band, ogr_layer, 0, [])


    if is_cancelled is not None and is_cancelled():
        return True, None






    values: list[int] = []
    raw: list = []
    seen = 0
    ogr_layer.ResetReading()
    for ogr_feat in ogr_layer:
        seen += 1
        if seen % 256 == 0 and is_cancelled is not None and is_cancelled():
            return True, None
        value = ogr_feat.GetField("value")
        if value not in label_classes:
            continue
        geom_ref = ogr_feat.GetGeometryRef()
        if geom_ref is None:
            continue
        values.append(value)
        raw.append(geom_ref.Clone())

    if simplify_factor > 0 or round_corners:
        geoms = simplify_shared(
            raw, gt, float(simplify_factor), round_corners, is_cancelled, (width, height)
        )
        if geoms is None:
            return True, None
    else:
        geoms = [QgsGeometry.fromWkt(g.ExportToWkt()) for g in raw]
    del raw
    if is_cancelled is not None and is_cancelled():
        return True, None
    by_label: dict[int, list[QgsGeometry]] = {}
    for value, geom in zip(values, geoms):
        by_label.setdefault(value, []).append(geom)

    feats: list[QgsFeature] = []
    next_fid = 1
    for value, (class_label, class_color_hex) in label_classes.items():
        for geom in by_label.get(value, []):
            if next_fid % 256 == 0 and is_cancelled is not None and is_cancelled():
                return True, None



            next_fid = _emit_traced_polygon(
                geom,
                min_area=0.0,
                simplify_tol=0.0,
                round_corners=False,
                class_label=class_label,
                class_color_hex=class_color_hex,
                measurer=measurer,
                next_fid=next_fid,
                feats=feats,
            )

    del label_ds
    del ogr_ds
    return True, feats


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
















    if is_cancelled is not None and is_cancelled():
        return None
    if not classes:
        raise AIEditError(
            ErrorCode.NO_PIXELS_MATCHED,
            tr("Select at least one color to vectorize."),
        )
    ri, gi, bi, gt, proj, _w, _h = _open_rgb(raster_path, raster_crs)
    log_debug(
        f"Vectorize: src={raster_path} classes={len(classes)} "
        f"competitors={len(competitors)} tol={tolerance}"
    )

    palette = [tuple(c["rgb"]) for c in classes] + [tuple(c) for c in competitors]
    best_dist = np.full(ri.shape, 32767, dtype=np.int16)

    index_dtype = np.uint8 if len(palette) <= 255 else np.uint32
    best_idx = np.full(ri.shape, np.iinfo(index_dtype).max, dtype=index_dtype)


    d = np.empty(ri.shape, dtype=np.int16)
    channel = np.empty(ri.shape, dtype=np.int16)
    better = np.empty(ri.shape, dtype=bool)
    for idx, (cr, cg, cb) in enumerate(palette):
        np.subtract(ri, cr, out=d)
        np.abs(d, out=d)
        np.subtract(gi, cg, out=channel)
        np.abs(channel, out=channel)
        np.add(d, channel, out=d)
        np.subtract(bi, cb, out=channel)
        np.abs(channel, out=channel)
        np.add(d, channel, out=d)
        np.less(d, best_dist, out=better)
        np.copyto(best_dist, d, where=better)
        best_idx[better] = idx
        if is_cancelled is not None and is_cancelled():
            return None


    assigned = (best_dist <= int(tolerance) * 3) & (ri >= 0)


    del ri, gi, bi, best_dist, d, channel, better

    measurer = _make_measurer(raster_crs, transform_context, ellipsoid)

    handled, feats = _trace_classes_batched(
        best_idx,
        assigned,
        classes,
        gt,
        proj,
        sieve_threshold=sieve_threshold,
        min_pixels=min_pixels,
        simplify_factor=simplify_factor,
        round_corners=round_corners,
        expand_value=expand_value,
        fill_holes=fill_holes,
        measurer=measurer,
        is_cancelled=is_cancelled,
    )
    if handled and feats is None:
        return None
    if not handled:
        feats = []
        next_fid = 1
        for class_idx, cls in enumerate(classes):
            if is_cancelled is not None and is_cancelled():
                return None
            mask = _class_mask(best_idx, assigned, class_idx, expand_value, fill_holes)
            if mask is None:
                continue
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

    empty_msg = tr(
        "No polygons found for the selected colors "
        "(try a wider tolerance or smaller min size)"
    )
    if not feats:
        raise AIEditError(ErrorCode.NO_PIXELS_MATCHED, empty_msg)
    feats = _clip_feats_to_crop(feats, raster_path, raster_crs, transform_context, ellipsoid)
    if not feats:
        raise AIEditError(ErrorCode.NO_PIXELS_MATCHED, empty_msg)
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

    ri, gi, bi, gt, proj, _w, _h = _open_rgb(raster_path, raster_crs)
    tr_r, tg_g, tb_b = target_rgb
    mask_r = np.abs(ri - tr_r) <= tolerance
    mask_g = np.abs(gi - tg_g) <= tolerance
    mask_b = np.abs(bi - tb_b) <= tolerance
    mask = (mask_r & mask_g & mask_b).astype(np.uint8)
    if not np.any(mask):
        raise AIEditError(
            ErrorCode.NO_PIXELS_MATCHED,
            tr("No pixels matched the selected color"),
        )
    if expand_value != 0 or fill_holes:
        mask = _refine_mask(mask, expand_value=expand_value, fill_holes=fill_holes)
        if not np.any(mask):
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
    empty_msg = tr(
        "No polygons remained after filtering "
        "(try a wider tolerance or smaller min size)"
    )
    if not feats:
        raise AIEditError(ErrorCode.NO_PIXELS_MATCHED, empty_msg)
    feats = _clip_feats_to_crop(feats, raster_path, raster_crs, transform_context, ellipsoid)
    if not feats:
        raise AIEditError(ErrorCode.NO_PIXELS_MATCHED, empty_msg)
    return feats


def vectorize_by_color(
    raster_layer: QgsRasterLayer,
    target_rgb: tuple[int, int, int],
    tolerance: int = 40,
    sieve_threshold: int = 10,
    min_pixels: int = 50,
    simplify_factor: float = 1.0,
    layer_name: str | None = None,
    output_rgb: tuple[int, int, int] | None = None,
    round_corners: bool = False,
    expand_value: int = 0,
    fill_holes: bool = False,
    class_label: str = "",
    match_mode: str = "box",
    background_rgb: tuple[int, int, int] | None = None,
) -> QgsVectorLayer:







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
