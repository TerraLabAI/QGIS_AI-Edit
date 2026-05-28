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
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor, QImage, QPainter

from ..core.logger import log_warning

MAX_RENDER_PX = 1024
_FALLBACK_CRS = "EPSG:3857"

# Providers that do blocking network I/O during render. Rendering one on the
# main thread freezes QGIS until the remote request times out, so we refuse
# these as reference images. "wms" covers WMS, WMTS and XYZ tiles (they share
# the wms provider). Local layers (gdal/ogr/etc.) are never in this set.
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
        source = (layer.source() or "").lower()
    except Exception:  # nosec B110 - no readable source means nothing to flag.
        source = ""
    if source.startswith(("http://", "https://")) or "url=http" in source:
        return True
    # A VRT loads as a local "gdal" provider with a local-path source, so the
    # checks above miss VRTs whose XML references remote rasters. Render would
    # block on the network. Read the first 64 KB of the VRT and flag if it
    # contains an http(s) URL.
    if source.endswith(".vrt") and os.path.isfile(source):
        try:
            with open(source, encoding="utf-8", errors="replace") as f:
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


def render_layers_to_qimage(
    layers: list,
    *,
    max_px: int = MAX_RENDER_PX,
    fallback_extent: QgsRectangle | None = None,
    fallback_crs: QgsCoordinateReferenceSystem | None = None,
) -> QImage | None:
    """Render one or more layers, isolated, on white, to a single QImage.

    Uses the combined extent of all layers (each reprojected into the render
    CRS). Falls back to ``fallback_extent`` (given in ``fallback_crs``) when no
    layer has a usable extent (typical WMS/XYZ). Multiple layers are drawn
    stacked, the first on top. Returns None on failure.
    """
    layers = [lyr for lyr in layers if lyr is not None]
    if not layers:
        return None

    dest_crs = _resolve_crs(layers[0])
    extent = None
    for lyr in layers:
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

    if extent is None or not _usable(extent):
        if fallback_extent is not None and _usable(fallback_extent):
            extent = _reproject_extent(QgsRectangle(fallback_extent), fallback_crs, dest_crs)
            if extent is None or not _usable(extent):
                log_warning("Fallback extent could not be reprojected to the layer CRS")
                return None
        else:
            log_warning("Layers have no usable extent and no fallback was provided")
            return None

    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setDestinationCrs(dest_crs)
    settings.setExtent(extent)
    settings.setOutputSize(_output_size(extent, max_px))
    settings.setBackgroundColor(QColor(255, 255, 255))
    settings.setFlag(QgsMapSettings.Flag.Antialiasing, True)
    settings.setOutputDpi(96)

    image = _run_render(settings)
    if image is None or image.isNull():
        log_warning("Layer render produced no image")
        return None
    return image


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
