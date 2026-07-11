from __future__ import annotations

import base64
import math

from qgis.core import (
    QgsMapLayer,
    QgsMapRendererCustomPainterJob,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QBuffer, QSize
from qgis.PyQt.QtGui import QColor, QImage, QPainter

from .. import qt_compat as QtC
from ..logger import log_debug, log_warning
from .export_config import _get_align, _get_max_dimension, chosen_input_format
from .render_set import drop_layers
from .sizing import (
    _RESOLUTION_TARGET_PX,
    _adjust_extent_to_aspect,
    _aspect_dims,
)


class ExportPrep:


    __slots__ = (
        "settings",
        "out_w",
        "out_h",
        "actual_extent",
        "background_color",
        "map_crs",
        "clean_base_settings",
        "markup_overlay",
        "clean_base_encoded",
    )

    def __init__(
        self,
        settings,
        out_w,
        out_h,
        actual_extent,
        background_color,
        map_crs,
        clean_base_settings=None,
        markup_overlay=None,
    ):
        self.settings = settings
        self.out_w = out_w
        self.out_h = out_h
        self.actual_extent = actual_extent
        self.background_color = background_color
        self.map_crs = map_crs



        self.clean_base_settings = clean_base_settings



        self.markup_overlay = markup_overlay




        self.clean_base_encoded: tuple[str, str] | None = None


def _is_usable_size(size, align: int, max_dim: int) -> bool:

    if not isinstance(size, (tuple, list)) or len(size) != 2:
        return False
    return all(
        isinstance(v, int) and not isinstance(v, bool) and align <= v <= max_dim for v in size
    )


def prepare_export(
    map_settings: QgsMapSettings,
    extent: QgsRectangle,
    target_resolution: str | None = None,
    markup_layer: QgsMapLayer | None = None,
    exclude_layer_ids: set[str] | None = None,
    layers: list | None = None,
    size: tuple[int, int] | None = None,
) -> ExportPrep:

























    bounds = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    if extent.width() <= 0 or extent.height() <= 0 or not all(math.isfinite(v) for v in bounds):
        raise ValueError("Invalid extent: width and height must be positive")



    if layers is not None:
        map_settings = _clone_map_settings(map_settings)
        map_settings.setLayers([lyr for lyr in layers if lyr is not None])
    elif exclude_layer_ids:

        map_settings = _clone_map_settings(map_settings)



        map_settings.setLayers(drop_layers(map_settings.layers(), exclude_layer_ids))

    max_dim = _get_max_dimension()
    align = _get_align()

    if max_dim is None or align is None:
        raise RuntimeError(
            "Export config not loaded from server. "
            "Check your internet connection and restart QGIS."
        )

    if align > max_dim:
        raise ValueError("Pixel alignment exceeds maximum export dimension")
    map_crs = map_settings.destinationCrs()
    if _is_usable_size(size, align, max_dim):
        out_w, out_h = int(size[0]), int(size[1])
    elif target_resolution and target_resolution in _RESOLUTION_TARGET_PX:
        out_w, out_h = _aspect_dims(
            extent, min(_RESOLUTION_TARGET_PX[target_resolution], max_dim), align, max_dim
        )
    else:
        out_w, out_h = _aspect_dims(extent, max_dim, align, max_dim)
    adjusted_extent = _adjust_extent_to_aspect(extent, out_w, out_h)

    settings = _clone_map_settings(map_settings)
    settings.setExtent(adjusted_extent)
    settings.setOutputSize(QSize(out_w, out_h))

    clean_base_settings = None
    markup_overlay = None
    if markup_layer is not None:
        try:
            markup_id = markup_layer.id()
        except RuntimeError:
            markup_id = None
        if markup_id is not None:
            all_layers = settings.layers()
            clean_layers = [lyr for lyr in all_layers if lyr.id() != markup_id]
            markup_in_canvas = len(clean_layers) != len(all_layers)




            clean_base_settings = _clone_map_settings(map_settings)
            clean_base_settings.setLayers(clean_layers)
            clean_base_settings.setExtent(adjusted_extent)
            clean_base_settings.setOutputSize(QSize(out_w, out_h))
            if markup_in_canvas:







                markup_overlay = _render_markup_overlay(
                    map_settings, markup_layer, adjusted_extent, out_w, out_h
                )
                settings.setLayers(clean_layers)
            else:



                log_warning(
                    "Markup: markup layer not in canvas render set (hidden?); "
                    "main image carries no marks"
                )
            log_debug(
                f"Markup prep: markup_in_canvas={markup_in_canvas}, "
                f"overlay={'yes' if markup_overlay is not None else 'no'}, "
                f"main_layers={len(all_layers)}, "
                f"clean_base_layers={len(clean_layers)}, "
                f"out={out_w}x{out_h}"
            )
        else:
            log_warning(
                "Markup: markup layer reference is stale; no clean base rendered"
            )

    return ExportPrep(
        settings=settings,
        out_w=out_w,
        out_h=out_h,
        actual_extent=settings.visibleExtent(),
        background_color=map_settings.backgroundColor(),
        map_crs=map_crs,
        clean_base_settings=clean_base_settings,
        markup_overlay=markup_overlay,
    )


def _render_settings_to_image(
    settings: QgsMapSettings,
    out_w: int,
    out_h: int,
    background_color,
    progress_cb=None,
) -> QImage:

    job = QgsMapRendererParallelJob(settings)
    if progress_cb is not None:
        try:
            job.renderingLayersFinished.connect(lambda: progress_cb(80))
        except Exception:  # nosec B110
            pass
    job.start()
    job.waitForFinished()

    image = job.renderedImage()
    if image is None or image.isNull():

        image = QImage(QSize(out_w, out_h), QtC.FormatARGB32)
        if image.isNull():
            raise RuntimeError("Could not allocate export image")
        image.fill(background_color)
        painter = QPainter(image)
        try:
            fallback = QgsMapRendererCustomPainterJob(settings, painter)
            fallback.start()
            fallback.waitForFinished()
        finally:
            painter.end()
    return image


def _render_markup_overlay(
    base_settings: QgsMapSettings,
    markup_layer,
    extent: QgsRectangle,
    out_w: int,
    out_h: int,
) -> QImage | None:








    settings = _clone_map_settings(base_settings)
    settings.setLayers([markup_layer])
    settings.setExtent(extent)
    settings.setOutputSize(QSize(out_w, out_h))
    transparent = QColor(0, 0, 0, 0)
    settings.setBackgroundColor(transparent)
    image = _render_settings_to_image(settings, out_w, out_h, transparent)
    if image is None or image.isNull():
        return None
    return image


def _encode_image(image: QImage, out_w: int, out_h: int) -> tuple[str, int, str]:

    if image is None or image.isNull() or image.width() != out_w or image.height() != out_h:
        raise ValueError("Export image dimensions do not match the prepared extent")
    fmt_qt, fmt_token, quality = chosen_input_format()
    buffer = QBuffer()
    if not buffer.open(QtC.WriteOnly):
        raise RuntimeError("Could not open image encoding buffer")
    ok = image.save(buffer, fmt_qt, quality)
    if not ok and fmt_qt != "PNG":



        log_warning(f"{fmt_token} encode failed; falling back to PNG")
        buffer.close()
        buffer = QBuffer()
        if not buffer.open(QtC.WriteOnly):
            raise RuntimeError("Could not open image encoding buffer")
        ok = image.save(buffer, "PNG")
        fmt_token = "png"  # nosec B105
    raw = buffer.data().data()
    buffer.close()
    if not ok or not raw:
        raise RuntimeError("Could not encode the export image")
    b64 = base64.b64encode(raw).decode("ascii")


    log_debug(
        f"Input export encoded: format={fmt_token} q={quality} "
        f"dims={out_w}x{out_h} raw_bytes={len(raw)} b64_bytes={len(b64)}"
    )
    return b64, len(raw), fmt_token


def _marks_are_baked(prep: ExportPrep) -> bool:


    overlay = prep.markup_overlay
    return overlay is not None and not overlay.isNull()


def render_export(
    prep: ExportPrep,
    progress_cb=None,
) -> tuple[str, int, QgsRectangle, str]:











    prep.clean_base_encoded = None
    image = _render_settings_to_image(
        prep.settings, prep.out_w, prep.out_h, prep.background_color, progress_cb
    )
    if _marks_are_baked(prep):
        prep.clean_base_encoded = _encode_clean_base(image, prep)


        painter = QPainter(image)
        try:
            painter.drawImage(0, 0, prep.markup_overlay)
        finally:
            painter.end()
    b64, raw_len, fmt_token = _encode_image(image, prep.out_w, prep.out_h)
    return b64, raw_len, prep.actual_extent, fmt_token


def _encode_clean_base(image: QImage, prep: ExportPrep) -> tuple[str, str]:



    b64, raw_len, fmt_token = _encode_image(image, prep.out_w, prep.out_h)
    log_debug(
        f"Clean base image encoded: dims={prep.out_w}x{prep.out_h} "
        f"format={fmt_token} raw_bytes={raw_len} b64_bytes={len(b64)}"
    )
    return b64, fmt_token


def render_clean_base(prep: ExportPrep) -> tuple[str, str] | None:









    if prep.clean_base_settings is None or not _marks_are_baked(prep):
        return None
    if prep.clean_base_encoded is not None:
        return prep.clean_base_encoded


    image = _render_settings_to_image(
        prep.clean_base_settings, prep.out_w, prep.out_h, prep.background_color
    )
    prep.clean_base_encoded = _encode_clean_base(image, prep)
    return prep.clean_base_encoded


def _clone_map_settings(src: QgsMapSettings) -> QgsMapSettings:



    try:
        dst = QgsMapSettings(src)
        dst.setDevicePixelRatio(1.0)
        return dst
    except (TypeError, AttributeError):
        dst = QgsMapSettings()
    dst.setLayers(src.layers())
    dst.setDestinationCrs(src.destinationCrs())
    dst.setBackgroundColor(src.backgroundColor())
    for setter, getter in (
        ("setRotation", "rotation"),
        ("setEllipsoid", "ellipsoid"),
        ("setOutputDpi", "outputDpi"),
        ("setLayerStyleOverrides", "layerStyleOverrides"),
        ("setFlags", "flags"),
        ("setTransformContext", "transformContext"),
        ("setTemporalRange", "temporalRange"),
    ):
        try:
            getattr(dst, setter)(getattr(src, getter)())
        except Exception as err:  # nosec B112


            log_warning(f"_clone_map_settings skipped {setter}: {err}")
            continue



    try:
        dst.setDevicePixelRatio(1.0)
    except Exception:  # nosec B110
        pass
    return dst
