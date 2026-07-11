from __future__ import annotations

import base64

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
from .native_resolution import _best_native_longest_px
from .sizing import (
    _RESOLUTION_TARGET_PX,
    _adjust_extent_to_aspect,
    _aspect_dims,
    _budget_dims,
)


class ExportPrep:
    """Render+encode snapshot. Built on main thread, consumed by a worker."""

    __slots__ = (
        "settings",
        "out_w",
        "out_h",
        "actual_extent",
        "background_color",
        "map_crs",
        "clean_base_settings",
        "markup_overlay",
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
        # When markup annotations exist, a second QgsMapSettings rendering the
        # SAME zone WITHOUT the marks (the marks ride on the main image). None
        # when there is no markup.
        self.clean_base_settings = clean_base_settings
        # Marks rasterized on the main thread into a transparent QImage. The
        # worker composites it onto the clean main render (the live markup
        # memory layer must never be rendered off-thread). None when no markup.
        self.markup_overlay = markup_overlay


def prepare_export(
    map_settings: QgsMapSettings,
    extent: QgsRectangle,
    target_resolution: str | None = None,
    markup_layer: QgsMapLayer | None = None,
    exclude_layer_ids: set[str] | None = None,
) -> ExportPrep:
    """Pick output size and clone settings. Cheap, main-thread.

    When ``markup_layer`` is given (the user drew guidance annotations), the
    marks are rasterized on THIS (main) thread into ``markup_overlay`` and the
    worker composites them onto the clean main render, so the marks bake into
    the image the model edits without rendering the live markup memory layer
    off-thread (a parallel render job spun up on the export worker thread over a
    main-thread memory layer deadlocks). A second ``clean_base_settings``
    renders the SAME zone WITHOUT the marks at the identical adjusted extent and
    output size, so the clean base registers pixel-for-pixel and the model
    restores the pixels under each mark, leaving no stroke in the result.
    """
    if extent.width() <= 0 or extent.height() <= 0:
        raise ValueError("Invalid extent: width and height must be positive")

    # "Original" base: render without the AI-Edit result layers. Filter a
    # CLONE so the live canvas's own settings (and on-screen layer
    # visibility) are never touched - only the exported base image changes.
    if exclude_layer_ids:
        map_settings = _clone_map_settings(map_settings)
        map_settings.setLayers(
            [lyr for lyr in map_settings.layers() if lyr.id() not in exclude_layer_ids]
        )

    max_dim = _get_max_dimension()
    align = _get_align()

    if max_dim is None or align is None:
        raise RuntimeError(
            "Export config not loaded from server. "
            "Check your internet connection and restart QGIS."
        )

    map_crs = map_settings.destinationCrs()
    if target_resolution and target_resolution in _RESOLUTION_TARGET_PX:
        # Size by the tier's PIXEL BUDGET, not its longest side. The model
        # (nano-banana-2) outputs ~ref^2 pixels with the input's aspect (1K ~=
        # 1 MP, 2K ~= 4 MP, 4K ~= 16 MP). Sizing by longest side undershoots
        # that budget on non-square zones (a 21:9 1K input would be ~0.45 MP vs
        # a ~1.06 MP output), forcing the model to upscale and softening the
        # result. Matching the budget keeps the input >= the output so it never
        # upscales, while staying far smaller than the full native zone.
        ref = min(_RESOLUTION_TARGET_PX[target_resolution], max_dim)
        out_w, out_h = _budget_dims(extent, ref, align, max_dim)
    else:
        longest = _best_native_longest_px(
            map_settings.layers(), extent, map_crs, max_dim
        )
        out_w, out_h = _aspect_dims(extent, longest, align, max_dim)
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
            # Clean base: the SAME zone with the markup dropped, sent as a
            # second image so the model can restore the pixels under each mark.
            # Apply the SAME adjusted_extent + out_w/out_h so it registers
            # pixel-for-pixel with the marked main image.
            clean_base_settings = _clone_map_settings(map_settings)
            clean_base_settings.setLayers(clean_layers)
            clean_base_settings.setExtent(adjusted_extent)
            clean_base_settings.setOutputSize(QSize(out_w, out_h))
            if markup_in_canvas:
                # The marks must bake into the main image, but the markup layer
                # is an in-memory QgsVectorLayer with main-thread affinity.
                # Rendering it inside the export QgsTask (a worker thread) via
                # QgsMapRendererParallelJob deadlocks (it never finishes). So
                # rasterize the marks HERE on the main thread, drop the live
                # layer from the worker's render set, and let render_export()
                # composite the overlay onto the clean main render.
                markup_overlay = _render_markup_overlay(
                    map_settings, markup_layer, adjusted_extent, out_w, out_h
                )
                settings.setLayers(clean_layers)
            else:
                # Markup drawn but its layer is not in the canvas render set
                # (user hid it): the main image carries no marks, so the feature
                # silently no-ops.
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
    """Render one QgsMapSettings off-screen to a QImage. Worker-thread safe."""
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
        # CustomPainter fallback for layer providers ParallelJob can't handle.
        image = QImage(QSize(out_w, out_h), QtC.FormatARGB32)
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
    """Rasterize ONLY the markup layer to a transparent image on the CALLING thread.

    The markup layer is an in-memory vector layer with main-thread affinity, so
    rendering it inside the off-thread export task deadlocks (see prepare_export).
    Called from the main thread, this returns a transparent overlay the worker
    composites onto the clean main render so the marks still bake into the image.
    Returns None when the render produces nothing usable.
    """
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
    """Encode a rendered QImage to ``(b64, raw_bytes, format_token)``."""
    fmt_qt, fmt_token, quality = chosen_input_format()
    buffer = QBuffer()
    buffer.open(QtC.WriteOnly)
    ok = image.save(buffer, fmt_qt, quality)
    if not ok and fmt_qt != "PNG":
        # Encoder failed despite a positive capability check (rare). PNG is
        # always available; fall back so the generation still goes out, and
        # report PNG so the upload's content-type matches the bytes.
        log_warning(f"{fmt_token} encode failed; falling back to PNG")
        buffer.close()
        buffer = QBuffer()
        buffer.open(QtC.WriteOnly)
        image.save(buffer, "PNG")
        fmt_token = "png"  # nosec B105 - format token, not a credential
    raw = buffer.data().data()
    b64 = base64.b64encode(raw).decode("ascii")
    # Diagnostic, production-safe (dimensions + sizes only). Always logged so a
    # bloated input is visible in the Log Messages panel without DEBUG.
    log_debug(
        f"Input export encoded: format={fmt_token} q={quality} "
        f"dims={out_w}x{out_h} raw_bytes={len(raw)} b64_bytes={len(b64)}"
    )
    return b64, len(raw), fmt_token


def render_export(
    prep: ExportPrep,
    progress_cb=None,
) -> tuple[str, int, QgsRectangle, str]:
    """Render the MAIN input off-screen, encode, base64. Worker-thread safe.

    Returns ``(b64, raw_bytes, extent, format_token)`` where ``format_token`` is
    the actual format written ('webp' | 'jpeg' | 'png'), used so the upload is
    labeled with a matching content-type.
    """
    image = _render_settings_to_image(
        prep.settings, prep.out_w, prep.out_h, prep.background_color, progress_cb
    )
    if prep.markup_overlay is not None and not prep.markup_overlay.isNull():
        # Marks were rasterized on the main thread (the live markup memory layer
        # cannot be rendered off-thread); bake them onto the clean main render.
        painter = QPainter(image)
        try:
            painter.drawImage(0, 0, prep.markup_overlay)
        finally:
            painter.end()
    b64, raw_len, fmt_token = _encode_image(image, prep.out_w, prep.out_h)
    return b64, raw_len, prep.actual_extent, fmt_token


def render_clean_base(prep: ExportPrep) -> tuple[str, str] | None:
    """Render the clean base image (the zone with the markup removed).

    Sent as a second image alongside the marked main image so the model can
    restore the pixels under each mark and leave no stroke in the result.
    Returns ``(b64, format_token)``, or ``None`` when there is no markup. The
    format token is this render's OWN actual format so the upload content-type
    stays correct even if its encode falls back to PNG independently of the
    main image.
    """
    if prep.clean_base_settings is None:
        return None
    image = _render_settings_to_image(
        prep.clean_base_settings, prep.out_w, prep.out_h, prep.background_color
    )
    b64, raw_len, fmt_token = _encode_image(image, prep.out_w, prep.out_h)
    log_debug(
        f"Clean base image rendered: dims={prep.out_w}x{prep.out_h} "
        f"format={fmt_token} raw_bytes={raw_len} b64_bytes={len(b64)}"
    )
    return b64, fmt_token


def _clone_map_settings(src: QgsMapSettings) -> QgsMapSettings:
    """Copy enough of ``src`` to preserve canvas render state for off-screen export."""
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
            # Surface skipped setters so missing temporal/DPI don't silently
            # corrupt the rendered export on older QGIS versions.
            log_warning(f"_clone_map_settings skipped {setter}: {err}")
            continue
    # Off-screen export renders to an explicit pixel budget (setOutputSize), so
    # force DPR 1.0: inheriting a HiDPI canvas's 2x would emit an image at twice
    # the budgeted dimensions (bigger upload, wrong resolution tier).
    try:
        dst.setDevicePixelRatio(1.0)
    except Exception:  # nosec B110 - older QGIS defaults to 1.0 anyway.
        pass
    return dst
