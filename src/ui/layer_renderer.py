"""Render an arbitrary QGIS map layer to a flat RGB QImage for use as a
context reference image.

The backend model is image-to-image: it only consumes pixels. A DEM (.asc), a
vector layer, or any other QGIS layer must be rasterized before it can be sent
as context. We let QGIS do the rasterization so the layer's own symbology
(grayscale DEM, hillshade, color ramp, vector styling) is honored.
"""
from __future__ import annotations

import os

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapRendererCustomPainterJob,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProject,
    QgsProviderRegistry,
    QgsProviderSublayerDetails,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QEventLoop, QSize, QTimer
from qgis.PyQt.QtGui import QColor, QImage, QPainter

from ..core.config_store import get_export_dial
from ..core.logger import log_warning

# Matches the reference store's 1536 px target (the model's effective per-image
# input budget at default media resolution). Online basemaps fetch higher-zoom,
# more legible tiles at this size.
MAX_RENDER_PX = 1536
# Higher DPI makes QGIS request higher-zoom tiles from online providers (more
# labels, sharper roads) and renders vector symbols/labels larger, so a map
# reference stays readable after downscaling.
_RENDER_DPI = 192
_FALLBACK_CRS = "EPSG:3857"

# Online tile/WMS providers fetch tiles asynchronously and their replies land on
# the main event loop, so a render that blocks that loop comes back blank. Each
# settling pass keeps the loop turning during the render, then leaves a short
# window for the stragglers, reloads the providers and re-renders, until two
# consecutive frames match (tiles settled) or the attempts run out.
_SETTLE_MAX_ATTEMPTS = 4
# 4 attempts x 300 ms caps the BETWEEN-PASS waiting at 0.9 s (the trailing pass
# never waits), against 8 x 0.6 s = 4.8 s before. The renders themselves are on
# top of that; what the budget buys is a window that stays responsive, since the
# wait now turns the real event loop instead of busy-looping on processEvents.
_SETTLE_WAIT_MS = 300
# How long one render may hold the nested event loop. waitForFinished() waits
# for a natural finish and asks nobody to stop, so a stalled provider held the
# window for as long as its own network timeout ran.
# This is NOT a ceiling on the whole render. The job that overran it is stopped
# with QgsMapRendererJob::cancel(), documented as not returning until the job
# has terminated, so that call is itself a wait. What bounds it is how fast the
# renderers notice the stop flag cancel() sets, which waitForFinished() never
# set. Measured on a 120k-point vector job: 18 ms on QGIS 4.0.0 / Qt 6.8.1,
# 23 ms on QGIS 3.22.0 / Qt 5.15.2.
_RENDER_TIMEOUT_MS = 15000

# Providers that fetch their data over the network during render. We render
# these with the settling loop (tiles arrive async) and use the view extent
# instead of their world-sized one. "wms" covers WMS, WMTS and XYZ tiles (they
# share the wms provider). Local layers (gdal/ogr/etc.) are never in this set.
_REMOTE_PROVIDERS = frozenset(
    {"wms", "wfs", "wcs", "arcgismapserver", "arcgisfeatureserver", "oapif"}
)


def _event_loop_flag(name: str):
    """Resolve a QEventLoop.ProcessEventsFlag member on Qt6 (scoped) and on the
    PyQt5 builds that still expose it flat. Same shape as the resolver in
    src/core/qt_compat.py, which carries no QEventLoop entry."""
    scoped = getattr(getattr(QEventLoop, "ProcessEventsFlag", None), name, None)
    if scoped is not None:
        return scoped
    return getattr(QEventLoop, name)


# Settling turns a nested event loop, which would otherwise deliver a queued
# click straight back into the caller mid-loop. User input stays queued instead,
# and that is ALL this flag holds back. Measured on QGIS 3.22.0 / Qt 5.15.2 and
# QGIS 4.0.0 / Qt 6.8.1, both the same: a QTimer slot armed before the nested
# loop DOES run inside it, and a queued signal emitted before it IS delivered
# inside it. So safe_single_shot callbacks, the progress ticker, poll tasks and
# QgsTask.finished all re-enter plugin code here. The one thing that stays out
# is a deleteLater() posted at the outer loop level, which Qt holds until that
# loop resumes. `_settling_in_progress` below is the guard that follows from it.
_EXCLUDE_USER_INPUT = _event_loop_flag("ExcludeUserInputEvents")

# True while a settling render holds a nested event loop. Re-entering would
# stack a second loop inside the first, and the outer one could then only end
# after the inner one.
_settling_in_progress = False


def is_remote_layer(layer) -> bool:
    """True if the layer is backed by a network provider that blocks on I/O
    during render. Provider-name match first, with an http(s) source fallback
    for keys the denylist misses (e.g. remote vector tiles, provider-key drift
    across QGIS versions). Local files always return False."""
    if layer is None:
        return False
    try:
        provider = layer.dataProvider()
        name = (provider.name() if provider is not None else "") or ""
    except Exception:  # nosec B110 - treat an unreadable provider as local.
        name = ""
    if name.lower() in _REMOTE_PROVIDERS:
        return True
    try:
        raw_source = layer.source() or ""
    except Exception:  # nosec B110 - no readable source means nothing to flag.
        raw_source = ""
    source = raw_source.lower()
    if source.startswith(("http://", "https://")) or "url=http" in source:
        return True
    # A VRT loads as a local "gdal" provider with a local-path source, so the
    # checks above miss VRTs whose XML references remote rasters. Render would
    # block on the network. Read the first 64 KB of the VRT and flag if it
    # contains an http(s) URL. Use the original-case path for the filesystem so
    # detection still works on case-sensitive volumes (Linux, case-sensitive APFS).
    if source.endswith(".vrt") and os.path.isfile(raw_source):
        try:
            with open(raw_source, encoding="utf-8", errors="replace") as f:
                head = f.read(64 * 1024).lower()
            if "http://" in head or "https://" in head:
                return True
        except OSError:  # nosec B110 - unreadable VRT treated as local.
            pass
    return False


def render_layers_at_extent(layers: list, extent: QgsRectangle, crs, settle: bool = True) -> QImage | None:
    """Render ``layers`` at EXACTLY ``extent`` (in ``crs``), no fallback.

    ``render_layers_to_qimage`` is built for a layer reference: when the
    layers miss the zone it renders them whole at their own extent, so the
    reference is still usable. A map capture is the opposite contract: the
    user dragged a rectangle over what they see, and only that rectangle may
    come back. On a tile basemap the only local layers are the plugin's own
    outputs, so the fallback would hand back a past result instead of the
    map, which is what the first live test of the Map chip did.
    """
    layers = [lyr for lyr in layers if lyr is not None]
    if not layers or extent is None or not _usable(extent):
        return None
    if crs is None or not crs.isValid():
        crs = _resolve_crs(layers[0])
    max_px = get_export_dial("render.max_px", MAX_RENDER_PX)
    return _render_at_extent(layers, QgsRectangle(extent), crs, max_px, settle=settle)


def layer_misses_zone(layers: list, zone_extent, zone_crs) -> bool:
    """Whether a layer reference will be sent WHOLE instead of aligned.

    True when the combined extent of the local layers does not touch the zone
    (in the zone's CRS): ``render_layers_to_qimage`` then renders them at
    their own extent and the widget must say so. False for online layers
    (world extent, always aligned), with no zone, or when nothing can be
    compared. Same rule as the renderer's forced-extent path, exposed so the
    UI can badge the thumbnail without touching the renderer's contract.
    """
    layers = [lyr for lyr in layers if lyr is not None]
    if not layers or zone_extent is None or not _usable(zone_extent):
        return False
    dest_crs = zone_crs if (zone_crs is not None and zone_crs.isValid()) else _resolve_crs(layers[0])
    zone = _reproject_extent(QgsRectangle(zone_extent), zone_crs, dest_crs)
    if zone is None or not _usable(zone):
        return False
    own = _combined_layer_extent(layers, dest_crs)
    return own is not None and not own.intersects(zone)


def input_layer_kind(layer) -> str:
    """Provider family of the raster an edit starts from, for telemetry only:
    "local" (a file on disk), "xyz" (tile basemap), "wms" (WMS/WMTS/other
    network raster) or "other". Never the source, never the name."""
    if layer is None:
        return "other"
    try:
        provider = layer.dataProvider()
        name = ((provider.name() if provider is not None else "") or "").lower()
        source = (layer.source() or "").lower()
    except Exception:  # nosec B110 - an unreadable provider is just "other".
        return "other"
    if name == "gdal" and not is_remote_layer(layer):
        return "local"
    if name == "wms":
        return "xyz" if "type=xyz" in source else "wms"
    if is_remote_layer(layer):
        return "wms"
    return "local" if name == "gdal" else "other"


def load_transient_layers(path: str) -> list:
    """Load a file as one or more QGIS layers WITHOUT adding them to the project.

    Tries raster first. For vector / container files it enumerates sublayers, so
    a multi-layer GeoPackage yields ALL its layers rather than just the first.
    Returns a list of QgsMapLayer; the caller must hold a reference to them until
    rendering completes (they are not parented to the project).
    """
    name = os.path.splitext(os.path.basename(path))[0]
    raster = QgsRasterLayer(path, name)
    if raster.isValid():
        return [raster]

    # Vector or multi-sublayer container (e.g. a GeoPackage with many layers).
    layers: list = []
    try:
        details = QgsProviderRegistry.instance().querySublayers(path)
        if details:
            options = QgsProviderSublayerDetails.LayerOptions(
                QgsProject.instance().transformContext()
            )
            for detail in details:
                lyr = detail.toLayer(options)
                if lyr is not None and lyr.isValid():
                    layers.append(lyr)
    except Exception as err:  # nosec B110 - fall back to a single-layer open below.
        log_warning(f"Sublayer query failed: {err}")
    if layers:
        # PDFs expose one sublayer per page; stacking them in a single render
        # overlays pages on top of each other, producing visual noise. Keep
        # only the first page as the context reference.
        if path.lower().endswith(".pdf") and len(layers) > 1:
            return layers[:1]
        return layers

    vector = QgsVectorLayer(path, name, "ogr")
    if vector.isValid():
        return [vector]
    log_warning(f"Could not load as raster or vector layer: {os.path.basename(path)}")
    return []


def _resolve_crs(layer) -> QgsCoordinateReferenceSystem:
    """Return a valid CRS for rendering. If the layer has none (e.g. a bare
    .asc with no .prj), assign the fallback TO the layer so source and
    destination CRS agree - otherwise QgsMapSettings reprojects from "no CRS"
    and the render comes back empty/warped."""
    crs = layer.crs()
    if crs is not None and crs.isValid():
        return crs
    fallback = QgsCoordinateReferenceSystem(_FALLBACK_CRS)
    try:
        layer.setCrs(fallback)
    except Exception:  # nosec B110 - some layers reject setCrs; render still attempts.
        pass
    return fallback


def _reproject_extent(extent, src_crs, dest_crs):
    """Transform a fallback extent from its source CRS into the render CRS.
    Returns the (possibly unchanged) extent, or None if the transform fails."""
    if src_crs is None or not src_crs.isValid() or src_crs == dest_crs:
        return extent
    try:
        xform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
        return xform.transformBoundingBox(extent)
    except Exception:  # nosec B110 - unrenderable fallback is handled by the caller.
        log_warning("Fallback extent reprojection failed")
        return None


def _output_size(extent: QgsRectangle, max_px: int) -> QSize:
    w = extent.width()
    h = extent.height()
    if w <= 0 or h <= 0:
        return QSize(max_px, max_px)
    if w >= h:
        return QSize(max_px, max(1, round(max_px * h / w)))
    return QSize(max(1, round(max_px * w / h)), max_px)


def _usable(extent: QgsRectangle) -> bool:
    if extent is None or extent.isEmpty() or extent.isNull():
        return False
    # Guard against world-sized WMS extents when isFinite is available.
    return not (hasattr(extent, "isFinite") and not extent.isFinite())


def _combined_layer_extent(layers: list, dest_crs) -> QgsRectangle | None:
    """Combined extent of the local layers, reprojected to ``dest_crs``.

    Online basemaps report a world-sized extent that is useless as a reference,
    so they are skipped here and handled via the view-extent fallback instead.
    Returns None when no layer has a usable extent.
    """
    extent = None
    for lyr in layers:
        if is_remote_layer(lyr):
            continue
        layer_extent = QgsRectangle(lyr.extent())
        if not _usable(layer_extent):
            continue
        layer_extent = _reproject_extent(layer_extent, lyr.crs(), dest_crs)
        if layer_extent is None or not _usable(layer_extent):
            continue
        if extent is None:
            extent = QgsRectangle(layer_extent)
        else:
            extent.combineExtentWith(layer_extent)
    return extent


def render_layers_to_qimage(
    layers: list,
    *,
    max_px: int | None = None,
    fallback_extent: QgsRectangle | None = None,
    fallback_crs: QgsCoordinateReferenceSystem | None = None,
    force_extent: QgsRectangle | None = None,
    force_crs: QgsCoordinateReferenceSystem | None = None,
    settle: bool = True,
) -> QImage | None:
    """Render one or more layers, isolated, on white, to a single QImage.

    ``force_extent`` (in ``force_crs``) renders every layer at exactly that
    extent, so a reference lines up pixel-for-pixel with the generation zone.
    Without it, the combined extent of all layers is used, falling back to
    ``fallback_extent`` (typical WMS/XYZ). Layers are drawn stacked, first on
    top. ``settle=False`` skips the online-tile settling passes; pass it when
    the tiles are already warm on the canvas.

    Returns None on failure, and a render that overran its time ceiling counts
    as one: the half-painted frame such a render leaves behind is never handed
    back as a reference.
    """
    layers = [lyr for lyr in layers if lyr is not None]
    if not layers:
        return None
    if max_px is None:
        max_px = get_export_dial("render.max_px", MAX_RENDER_PX)

    if force_extent is not None and _usable(force_extent):
        dest_crs = force_crs if (force_crs is not None and force_crs.isValid()) else _resolve_crs(layers[0])
        extent = _reproject_extent(QgsRectangle(force_extent), force_crs, dest_crs)
        if extent is None or not _usable(extent):
            log_warning("Forced extent could not be reprojected to the render CRS")
            return None
        # Cropping to the zone only makes sense when the layer actually covers
        # it. A layer dropped from elsewhere (e.g. a past generation in another
        # area) doesn't intersect the zone, so the forced crop renders pure
        # white. Detect that and render the layer at its own extent instead, so
        # it still works as a standalone reference image.
        own = _combined_layer_extent(layers, dest_crs)
        if own is not None and not own.intersects(extent):
            log_warning("Dropped layer is outside the zone; rendering at its own extent")
            extent = own
        return _render_at_extent(layers, extent, dest_crs, max_px, settle=settle)

    dest_crs = _resolve_crs(layers[0])
    extent = _combined_layer_extent(layers, dest_crs)

    if extent is None or not _usable(extent):
        if fallback_extent is not None and _usable(fallback_extent):
            extent = _reproject_extent(QgsRectangle(fallback_extent), fallback_crs, dest_crs)
            if extent is None or not _usable(extent):
                log_warning("Fallback extent could not be reprojected to the layer CRS")
                return None
        else:
            log_warning("Layers have no usable extent and no fallback was provided")
            return None

    return _render_at_extent(layers, extent, dest_crs, max_px, settle=settle)


def _render_at_extent(
    layers: list,
    extent: QgsRectangle,
    dest_crs: QgsCoordinateReferenceSystem,
    max_px: int,
    settle: bool = True,
) -> QImage | None:
    """Build the map settings and render `layers` at `extent` to a QImage."""
    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setDestinationCrs(dest_crs)
    settings.setExtent(extent)
    settings.setOutputSize(_output_size(extent, max_px))
    settings.setBackgroundColor(QColor(255, 255, 255))
    settings.setFlag(QgsMapSettings.Flag.Antialiasing, True)
    # Cross-version guard: the flag was renamed/added across QGIS releases.
    _hq_flag = getattr(QgsMapSettings.Flag, "HighQualityImageTransforms", None)
    if _hq_flag is not None:
        settings.setFlag(_hq_flag, True)
    settings.setOutputDpi(get_export_dial("render.dpi", _RENDER_DPI))

    if settle and any(is_remote_layer(lyr) for lyr in layers):
        _enable_online_resampling(layers)
        image = _run_settling_render(settings, layers)
    else:
        image = _run_render(settings)
    if image is None or image.isNull():
        log_warning("Layer render produced no image")
        return None
    return image


def _enable_online_resampling(layers: list) -> None:
    """Turn on bilinear resampling for online raster layers so downscaled tiles
    stay smooth instead of blocky. Best-effort: silently skips layers whose
    provider doesn't support it."""
    for lyr in layers:
        try:
            provider = lyr.dataProvider()
            if provider is None or not hasattr(provider, "enableProviderResampling"):
                continue
            provider.enableProviderResampling(True)
            method = provider.ResamplingMethod.Bilinear
            provider.setZoomedInResamplingMethod(method)
            provider.setZoomedOutResamplingMethod(method)
        except Exception:  # nosec B112 - resampling is a quality nicety, never fatal.
            continue


def _spin_event_loop(msec: int) -> None:
    """Turn the real event loop for ``msec`` so async tile replies are delivered.

    A processEvents() + sleep() busy-wait never let the window repaint, so the
    whole settle read as "Not Responding" on Windows."""
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(max(0, int(msec)))
    loop.exec(_EXCLUDE_USER_INPUT)
    timer.stop()


def _run_settling_render(settings: QgsMapSettings, layers: list) -> QImage | None:
    """Render online layers, waiting for tiles to settle.

    Each pass renders with the event loop live, then leaves the stragglers a
    short window and reloads the providers, until two consecutive frames are
    identical (tiles in) or attempts run out. The last pass never waits: its
    frame is what we return. A finished-but-partial frame is still returned, so
    a slow network degrades to a partial reference rather than an error.

    A pass that fails ends the loop instead of feeding it. `_run_live_render`
    answers None for a render that overran its ceiling, so the near-blank frame
    such a render leaves behind can never become `prev`, and the loop cannot
    converge on two identical blank frames and hand one back as a reference."""
    global _settling_in_progress
    if _settling_in_progress:
        # `_run_render` on purpose: waitForFinished() runs no Python and
        # delivers no events, so it cannot re-enter here in turn. Anything that
        # turns a loop could, and the recursion would have no floor.
        log_warning("Settling render re-entered; rendering without settling")
        return _run_render(settings)
    _settling_in_progress = True
    attempts = get_export_dial("render.settle_attempts", _SETTLE_MAX_ATTEMPTS)
    wait_ms = get_export_dial("render.settle_wait_ms", _SETTLE_WAIT_MS)
    try:
        prev: QImage | None = None
        for attempt in range(attempts):
            image = _run_live_render(settings)
            if image is None or image.isNull():
                break
            if prev is not None and image == prev:
                return image
            prev = image
            if attempt == attempts - 1:
                break
            _spin_event_loop(wait_ms)
            for lyr in layers:
                try:
                    provider = lyr.dataProvider()
                    if provider is not None:
                        provider.reloadData()
                except Exception:  # nosec B110 - reload is best-effort per layer.
                    pass
        return prev
    finally:
        _settling_in_progress = False


def _stop_render_job(job) -> None:
    """Stop a job that is still running, before the last reference to it goes.

    A job left active is a job whose worker threads are still painting into an
    image the caller is about to drop. cancel() is the blocking variant on
    purpose: cancelWithoutBlocking() would hand back a job still rendering
    layers that the caller releases as soon as this function returns."""
    if job is None:
        return
    try:
        if job.isActive():
            job.cancel()
    except Exception as err:  # nosec B110 - a job we cannot stop is not fatal.
        log_warning(f"Could not stop the render job: {err}")


def _run_live_render(settings: QgsMapSettings) -> QImage | None:
    """Render while the event loop keeps turning. None when the render overran
    ``_RENDER_TIMEOUT_MS``.

    waitForFinished() blocks the main loop, so the tile replies an online
    provider is waiting on cannot be delivered and the frame comes back blank:
    that is what forced a render per settling pass. Waiting on the job's
    finished signal instead lets those replies land DURING the render, so the
    tiles are usually in on the first pass.

    A render that overruns the ceiling is a failure, never a result.
    renderedImage() on a stopped job hands back whatever was painted so far,
    and that reads as a success: measured on QGIS 3.22.0 / Qt 5.15.2 and on
    QGIS 4.0.0 / Qt 6.8.1, a job stopped after 1 ms returns a non-null image
    with 0% of its pixels painted. A reference that renders blank quietly ruins
    the generation, so the caller gets None and can say so."""
    job = None
    try:
        job = QgsMapRendererParallelJob(settings)
        loop = QEventLoop()
        job.finished.connect(loop.quit)
        guard = QTimer()
        guard.setSingleShot(True)
        guard.timeout.connect(loop.quit)
        job.start()
        if job.isActive():
            guard.start(get_export_dial("render.timeout_ms", _RENDER_TIMEOUT_MS))
            loop.exec(_EXCLUDE_USER_INPUT)
            guard.stop()
        if job.isActive():
            log_warning("Layer render timed out; the partial frame is discarded")
            return None
        image = job.renderedImage()
        if image is not None and not image.isNull():
            return image
    except Exception as err:  # nosec B110 - fall back to painter job below.
        log_warning(f"Live render failed, falling back: {err}")
    finally:
        # loop.exec() runs the whole application event loop, so anything in it
        # can raise and leave the job running under a local about to be freed.
        _stop_render_job(job)
    return _run_painter_render(settings)


def _run_render(settings: QgsMapSettings) -> QImage | None:
    """Blocking render, for layers whose pixels need nothing from the main loop."""
    job = None
    try:
        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        if image is not None and not image.isNull():
            return image
    except Exception as err:  # nosec B110 - fall back to painter job below.
        log_warning(f"Parallel render failed, falling back: {err}")
    finally:
        _stop_render_job(job)
    return _run_painter_render(settings)


def _run_painter_render(settings: QgsMapSettings) -> QImage | None:
    """Last-resort render for providers the parallel job cannot handle."""
    size = settings.outputSize()
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    job = None
    failed = False
    try:
        job = QgsMapRendererCustomPainterJob(settings, painter)
        job.start()
        job.waitForFinished()
    except Exception as err:  # nosec B110
        log_warning(f"Painter render failed: {err}")
        failed = True
    finally:
        # The painter has to outlive the job: ending it while the job still
        # draws would leave it painting into a device that is gone.
        _stop_render_job(job)
        painter.end()
    return None if failed else image
