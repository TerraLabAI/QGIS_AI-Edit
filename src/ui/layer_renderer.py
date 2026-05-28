







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



MAX_RENDER_PX = 1536



_RENDER_DPI = 192
_FALLBACK_CRS = "EPSG:3857"






_SETTLE_MAX_ATTEMPTS = 4




_SETTLE_WAIT_MS = 300









_RENDER_TIMEOUT_MS = 15000


_VRT_SNIFF_BYTES = 64 * 1024





_REMOTE_PROVIDERS = frozenset(
    {"wms", "wfs", "wcs", "arcgismapserver", "arcgisfeatureserver", "oapif"}
)


def _event_loop_flag(name: str):



    scoped = getattr(getattr(QEventLoop, "ProcessEventsFlag", None), name, None)
    if scoped is not None:
        return scoped
    return getattr(QEventLoop, name)











_EXCLUDE_USER_INPUT = _event_loop_flag("ExcludeUserInputEvents")




_settling = {"active": False}


def is_remote_layer(layer) -> bool:




    if layer is None:
        return False
    try:
        provider = layer.dataProvider()
        name = (provider.name() if provider is not None else "") or ""
    except Exception:  # nosec B110
        name = ""
    if name.lower() in _REMOTE_PROVIDERS:
        return True
    try:
        raw_source = layer.source() or ""
    except Exception:  # nosec B110
        raw_source = ""
    source = raw_source.lower()
    if source.startswith(("http://", "https://")) or "url=http" in source:
        return True





    if source.endswith(".vrt") and os.path.isfile(raw_source):
        try:
            with open(raw_source, encoding="utf-8", errors="replace") as f:
                head = f.read(get_export_dial("widgets.layer_renderer.vrt_sniff_bytes", _VRT_SNIFF_BYTES)).lower()
            if "http://" in head or "https://" in head:
                return True
        except OSError:  # nosec B110
            pass
    return False


def render_layers_at_extent(layers: list, extent: QgsRectangle, crs, settle: bool = True) -> QImage | None:










    layers = [lyr for lyr in layers if lyr is not None]
    if not layers or extent is None or not _usable(extent):
        return None
    if crs is None or not crs.isValid():
        crs = _resolve_crs(layers[0])
    max_px = get_export_dial("render.max_px", MAX_RENDER_PX)
    return _render_at_extent(layers, QgsRectangle(extent), crs, max_px, settle=settle)


def layer_misses_zone(layers: list, zone_extent, zone_crs) -> bool:









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



    if layer is None:
        return "other"
    try:
        provider = layer.dataProvider()
        name = ((provider.name() if provider is not None else "") or "").lower()
        source = (layer.source() or "").lower()
    except Exception:  # nosec B110
        return "other"
    if name == "gdal" and not is_remote_layer(layer):
        return "local"
    if name == "wms":
        return "xyz" if "type=xyz" in source else "wms"
    if is_remote_layer(layer):
        return "wms"
    return "local" if name == "gdal" else "other"


def load_transient_layers(path: str) -> list:







    layers = _load_transient_layers_at(path)
    if layers or path.isascii():
        return layers


    folder, base = os.path.split(path)
    if not base.isascii():
        return layers
    from ..core.output_paths import ascii_safe_dir

    safe_path = os.path.join(ascii_safe_dir(folder), base)
    if safe_path == path:
        return layers
    return _load_transient_layers_at(safe_path)


def _load_transient_layers_at(path: str) -> list:

    name = os.path.splitext(os.path.basename(path))[0]
    raster = QgsRasterLayer(path, name)
    if raster.isValid():
        return [raster]


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
    except Exception as err:  # nosec B110
        log_warning(f"Sublayer query failed: {err}")
    if layers:



        if path.lower().endswith(".pdf") and len(layers) > 1:
            return layers[:1]
        return layers

    vector = QgsVectorLayer(path, name, "ogr")
    if vector.isValid():
        return [vector]
    log_warning(f"Could not load as raster or vector layer: {os.path.basename(path)}")
    return []


def _resolve_crs(layer) -> QgsCoordinateReferenceSystem:




    crs = layer.crs()
    if crs is not None and crs.isValid():
        return crs
    fallback = QgsCoordinateReferenceSystem(_FALLBACK_CRS)
    try:
        layer.setCrs(fallback)
    except Exception:  # nosec B110
        pass
    return fallback


def _reproject_extent(extent, src_crs, dest_crs):


    if src_crs is None or not src_crs.isValid() or src_crs == dest_crs:
        return extent
    try:
        xform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance())
        return xform.transformBoundingBox(extent)
    except Exception:  # nosec B110
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

    return not (hasattr(extent, "isFinite") and not extent.isFinite())


def _combined_layer_extent(layers: list, dest_crs) -> QgsRectangle | None:






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

    settings = QgsMapSettings()
    settings.setLayers(layers)
    settings.setDestinationCrs(dest_crs)
    settings.setExtent(extent)
    settings.setOutputSize(_output_size(extent, max_px))
    settings.setBackgroundColor(QColor(255, 255, 255))
    settings.setFlag(QgsMapSettings.Flag.Antialiasing, True)

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



    for lyr in layers:
        try:
            provider = lyr.dataProvider()
            if provider is None or not hasattr(provider, "enableProviderResampling"):
                continue
            provider.enableProviderResampling(True)
            method = provider.ResamplingMethod.Bilinear
            provider.setZoomedInResamplingMethod(method)
            provider.setZoomedOutResamplingMethod(method)
        except Exception:  # nosec B112
            continue


def _spin_event_loop(msec: int) -> None:




    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(max(0, int(msec)))
    loop.exec(_EXCLUDE_USER_INPUT)
    timer.stop()


def _run_settling_render(settings: QgsMapSettings, layers: list) -> QImage | None:












    if _settling["active"]:



        log_warning("Settling render re-entered; rendering without settling")
        return _run_render(settings)
    _settling["active"] = True
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
                except Exception:  # nosec B110
                    pass
        return prev
    finally:
        _settling["active"] = False


def _stop_render_job(job) -> None:






    if job is None:
        return
    try:
        if job.isActive():
            job.cancel()
    except Exception as err:  # nosec B110
        log_warning(f"Could not stop the render job: {err}")


def _run_live_render(settings: QgsMapSettings) -> QImage | None:















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
    except Exception as err:  # nosec B110
        log_warning(f"Live render failed, falling back: {err}")
    finally:


        _stop_render_job(job)
    return _run_painter_render(settings)


def _run_render(settings: QgsMapSettings) -> QImage | None:

    job = None
    try:
        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        if image is not None and not image.isNull():
            return image
    except Exception as err:  # nosec B110
        log_warning(f"Parallel render failed, falling back: {err}")
    finally:
        _stop_render_job(job)
    return _run_painter_render(settings)


def _run_painter_render(settings: QgsMapSettings) -> QImage | None:

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


        _stop_render_job(job)
        painter.end()
    return None if failed else image
