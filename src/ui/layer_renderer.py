"""Render an arbitrary QGIS map layer to a flat RGB QImage for use as a
context reference image.

The backend model is image-to-image: it only consumes pixels. A DEM (.asc), a
vector layer, or any other QGIS layer must be rasterized before it can be sent
as context. We let QGIS do the rasterization so the layer's own symbology
(grayscale DEM, hillshade, color ramp, vector styling) is honored.
"""
from __future__ import annotations

import os
import time

from qgis.core import (
    QgsApplication,
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
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage, QPainter

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

# Online tile/WMS providers fetch tiles asynchronously: the first render comes
# back blank because replies arrive on the main event loop after the render
# returns. We render, pump the event loop, reload, and re-render until two
# consecutive frames match (tiles settled) or we exhaust the attempts.
_SETTLE_MAX_ATTEMPTS = 8
_SETTLE_PUMP_SECONDS = 0.6

# Providers that fetch their data over the network during render. We render
# these with the settling loop (tiles arrive async) and use the view extent
# instead of their world-sized one. "wms" covers WMS, WMTS and XYZ tiles (they
# share the wms provider). Local layers (gdal/ogr/etc.) are never in this set.
_REMOTE_PROVIDERS = frozenset(
    {"wms", "wfs", "wcs", "arcgismapserver", "arcgisfeatureserver", "oapif"}
)


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
    max_px: int = MAX_RENDER_PX,
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
    top. ``settle=False`` skips the online-tile settling loop (a multi-second
    main-thread block); pass it when the tiles are already warm on the canvas.
    Returns None on failure.
    """
    layers = [lyr for lyr in layers if lyr is not None]
    if not layers:
        return None

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
    settings.setOutputDpi(_RENDER_DPI)

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


def _pump_events(seconds: float) -> None:
    """Run the event loop briefly so async tile network replies are delivered."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QgsApplication.processEvents()
        time.sleep(0.03)


def _run_settling_render(settings: QgsMapSettings, layers: list) -> QImage | None:
    """Render online layers, waiting for tiles to settle.

    Online providers fetch tiles on the main event loop, so a single blocking
    render returns blank. We render, pump events, reload the providers, and
    repeat until two consecutive frames are identical (tiles in) or attempts run
    out. Returns the last frame either way so a slow network degrades to a
    partial reference rather than an error."""
    prev: QImage | None = None
    for _attempt in range(_SETTLE_MAX_ATTEMPTS):
        image = _run_render(settings)
        _pump_events(_SETTLE_PUMP_SECONDS)
        if image is not None and not image.isNull():
            if prev is not None and image == prev:
                return image
            prev = image
        for lyr in layers:
            try:
                provider = lyr.dataProvider()
                if provider is not None:
                    provider.reloadData()
            except Exception:  # nosec B110 - reload is best-effort per layer.
                pass
    return prev


def _run_render(settings: QgsMapSettings) -> QImage | None:
    try:
        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        if image is not None and not image.isNull():
            return image
    except Exception as err:  # nosec B110 - fall back to painter job below.
        log_warning(f"Parallel render failed, falling back: {err}")

    size = settings.outputSize()
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    try:
        job = QgsMapRendererCustomPainterJob(settings, painter)
        job.start()
        job.waitForFinished()
    except Exception as err:  # nosec B110
        log_warning(f"Painter render failed: {err}")
        painter.end()
        return None
    painter.end()
    return image
