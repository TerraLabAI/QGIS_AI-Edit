from __future__ import annotations

import base64
import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsDistanceArea,
    QgsGeometry,
    QgsMapLayer,
    QgsMapRendererCustomPainterJob,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QBuffer, QSize
from qgis.PyQt.QtGui import QImage, QPainter

from ..core import qt_compat as QtC
from ..core.errors import AIEditError, ErrorCode
from ..core.i18n import tr
from ..core.logger import log_warning

# Maximum on-the-ground area accepted by the AI Edit pipeline. Above this the
# model dilutes details to the point of uselessness. Surfaces as a clean
# refusal at draw time rather than wasting a generation credit.
_MAX_AREA_KM2 = 10000.0

# Above this absolute latitude the Mercator world distortion makes
# ground_resolution estimates unreliable and most basemaps stop. Refuse to
# avoid silent corruption of the output GeoTIFF.
_POLAR_ABS_LAT_DEG = 85.0


def validate_zone(extent: QgsRectangle, map_crs, map_rotation: float = 0.0) -> None:
    """Raise AIEditError if the zone can't be exported safely (CRS, rotation, antimeridian, polar, area)."""
    if map_crs is None or not map_crs.isValid():
        raise AIEditError(
            ErrorCode.INVALID_CRS,
            tr("This project's CRS is invalid. Set a project CRS before drawing a zone."),
        )
    if not map_crs.authid():
        raise AIEditError(
            ErrorCode.INVALID_CRS,
            tr(
                "AI Edit needs a standard CRS (EPSG code). "
                "Your project uses a custom CRS without an authority ID."
            ),
        )
    if abs(float(map_rotation)) > 0.01:
        raise AIEditError(
            ErrorCode.MAP_ROTATED,
            tr(
                "Map rotation is not supported. "
                "Reset rotation to 0 in the map navigation controls and try again."
            ),
        )

    geographic_extent = extent
    if not map_crs.isGeographic():
        try:
            to_wgs = QgsCoordinateTransform(
                map_crs,
                QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance(),
            )
            geographic_extent = to_wgs.transformBoundingBox(extent)
        except Exception:
            geographic_extent = None

    if geographic_extent is not None:
        xmin = geographic_extent.xMinimum()
        xmax = geographic_extent.xMaximum()
        if xmax < xmin or (xmax - xmin) > 180.0:
            raise AIEditError(
                ErrorCode.ANTIMERIDIAN,
                tr(
                    "This zone crosses the antimeridian (180 deg longitude). "
                    "AI Edit does not support that yet. Split your zone into two."
                ),
            )
        max_abs_lat = max(abs(geographic_extent.yMinimum()), abs(geographic_extent.yMaximum()))
        if max_abs_lat > _POLAR_ABS_LAT_DEG:
            raise AIEditError(
                ErrorCode.POLAR,
                tr(
                    "Zone is too close to a pole (above {limit} degrees latitude). "
                    "AI Edit cannot estimate ground resolution there."
                ).format(limit=int(_POLAR_ABS_LAT_DEG)),
            )

    try:
        area = QgsDistanceArea()
        area.setSourceCrs(map_crs, QgsProject.instance().transformContext())
        area.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")
        m2 = area.measureArea(QgsGeometry.fromRect(extent))
        if area.lengthUnits() != QgsUnitTypes.DistanceMeters:
            m2 = area.convertAreaMeasurement(m2, QgsUnitTypes.AreaSquareMeters)
        km2 = m2 / 1_000_000.0
    except Exception:
        km2 = 0.0

    if km2 > _MAX_AREA_KM2:
        raise AIEditError(
            ErrorCode.TOO_LARGE,
            tr(
                "This zone covers {area:.0f} km², which is larger than "
                "the {limit:.0f} km² max. Pick a smaller area."
            ).format(area=km2, limit=_MAX_AREA_KM2),
        )


# Optional in QGIS < 3.14; vector tile sizing falls back to "unconstrained" if missing.
try:
    from qgis.core import QgsVectorTileLayer
except ImportError:
    QgsVectorTileLayer = None

# Map user-facing resolution labels to target pixel counts (longest side)
_RESOLUTION_TARGET_PX = {"1K": 1024, "2K": 2048, "4K": 4096}

# Web Mercator m/px at z=0 for a 256-px tile, at the equator.
_WEBMERC_M_PX_Z0 = 156543.03392

# Warn if a tile layer's zmax forces the rendered m/px to be this much coarser
# than the m/px the selection would otherwise allow.
_CAPPED_WARN_RATIO = 1.1


def set_server_config(config: dict):
    """Set server export config fetched at plugin startup."""
    from ..core.config_store import get_store
    store = get_store()
    if store is not None:
        store.set_server_export_config(config)


def has_server_config() -> bool:
    """Check if server config has been loaded."""
    from ..core.config_store import get_store
    store = get_store()
    return store is not None and store.has_server_export_config()


def _get_server_config() -> dict | None:
    from ..core.config_store import get_store
    store = get_store()
    return store.get_server_export_config() if store is not None else None


def _get_max_dimension() -> int | None:
    """Get max dimension from server config. Returns None if unavailable."""
    cfg = _get_server_config()
    return cfg.get("max_dimension") if cfg else None


def _get_align() -> int | None:
    """Get pixel alignment from server config. Returns None if unavailable."""
    cfg = _get_server_config()
    return cfg.get("align") if cfg else None


class ExportPrep:
    """Render+encode snapshot. Built on main thread, consumed by a worker."""

    __slots__ = (
        "settings",
        "out_w",
        "out_h",
        "actual_extent",
        "background_color",
        "map_crs",
        "xyz_cap_warning",
    )

    def __init__(self, settings, out_w, out_h, actual_extent, background_color, map_crs,
                 xyz_cap_warning: str | None = None):
        self.settings = settings
        self.out_w = out_w
        self.out_h = out_h
        self.actual_extent = actual_extent
        self.background_color = background_color
        self.map_crs = map_crs
        self.xyz_cap_warning = xyz_cap_warning


def prepare_export(
    map_settings: QgsMapSettings,
    extent: QgsRectangle,
    target_resolution: str | None = None,
) -> ExportPrep:
    """Pick output size, clone settings, warn on caps. Cheap, main-thread."""
    if extent.width() <= 0 or extent.height() <= 0:
        raise ValueError("Invalid extent: width and height must be positive")

    max_dim = _get_max_dimension()
    align = _get_align()

    if max_dim is None or align is None:
        raise RuntimeError(
            "Export config not loaded from server. "
            "Check your internet connection and restart QGIS."
        )

    map_crs = map_settings.destinationCrs()
    if target_resolution and target_resolution in _RESOLUTION_TARGET_PX:
        longest = min(_RESOLUTION_TARGET_PX[target_resolution], max_dim)
    else:
        longest = _best_native_longest_px(
            map_settings.layers(), extent, map_crs, max_dim
        )

    out_w, out_h = _aspect_dims(extent, longest, align, max_dim)
    adjusted_extent = _adjust_extent_to_aspect(extent, out_w, out_h)

    settings = _clone_map_settings(map_settings)
    settings.setExtent(adjusted_extent)
    settings.setOutputSize(QSize(out_w, out_h))

    xyz_warning = _warn_if_xyz_capped(
        map_settings.layers(), adjusted_extent, map_crs, out_w
    )

    return ExportPrep(
        settings=settings,
        out_w=out_w,
        out_h=out_h,
        actual_extent=settings.visibleExtent(),
        background_color=map_settings.backgroundColor(),
        map_crs=map_crs,
        xyz_cap_warning=xyz_warning,
    )


def render_export(
    prep: ExportPrep,
    progress_cb=None,
) -> tuple[str, int, QgsRectangle]:
    """Render off-screen + PNG encode + base64. Worker-thread safe. Returns (b64, bytes, extent)."""
    job = QgsMapRendererParallelJob(prep.settings)
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
        image = QImage(QSize(prep.out_w, prep.out_h), QtC.FormatARGB32)
        image.fill(prep.background_color)
        painter = QPainter(image)
        try:
            fallback = QgsMapRendererCustomPainterJob(prep.settings, painter)
            fallback.start()
            fallback.waitForFinished()
        finally:
            painter.end()

    buffer = QBuffer()
    buffer.open(QtC.WriteOnly)
    image.save(buffer, "PNG")
    raw = buffer.data().data()
    b64 = base64.b64encode(raw).decode("ascii")
    return b64, len(raw), prep.actual_extent


def apply_export_context(
    ctx,
    prep: ExportPrep,
    actual_extent: QgsRectangle,
    image_size_bytes: int,
) -> None:
    """Main-thread ctx mutation after the worker returns extent + size."""
    if ctx is None:
        return
    ctx.extent = {
        "xmin": actual_extent.xMinimum(),
        "ymin": actual_extent.yMinimum(),
        "xmax": actual_extent.xMaximum(),
        "ymax": actual_extent.yMaximum(),
    }
    ctx.crs_wkt = prep.map_crs.toWkt()
    ctx.crs_authid = prep.map_crs.authid() or None
    ctx.centroid_lat, ctx.centroid_lon = _centroid_wgs84(actual_extent, prep.map_crs)
    ctx.ground_resolution_m = _compute_ground_resolution_m(
        actual_extent, prep.out_w, prep.out_h, prep.map_crs
    )
    ctx.export_width = prep.out_w
    ctx.export_height = prep.out_h
    ctx.image_size_bytes = image_size_bytes


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
        ("setDevicePixelRatio", "devicePixelRatio"),
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
    return dst


def _centroid_wgs84(extent: QgsRectangle, src_crs) -> tuple[float | None, float | None]:
    """Centroid of the rendered extent, projected to WGS84 (EPSG:4326)."""
    try:
        cx = (extent.xMinimum() + extent.xMaximum()) / 2
        cy = (extent.yMinimum() + extent.yMaximum()) / 2
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if src_crs == wgs84:
            return cy, cx
        transform = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
        pt = transform.transform(QgsPointXY(cx, cy))
        return pt.y(), pt.x()
    except Exception:
        return None, None


def _compute_ground_resolution_m(extent, out_w: int, out_h: int, crs) -> float | None:
    """Meters per pixel at the centroid of the rendered zone."""
    try:
        map_units = crs.mapUnits()
        px_size_x = extent.width() / max(out_w, 1)
        px_size_y = extent.height() / max(out_h, 1)
        avg_px_size = (px_size_x + px_size_y) / 2
        factor = QgsUnitTypes.fromUnitToUnitFactor(map_units, QgsUnitTypes.DistanceMeters)
        result = avg_px_size * factor
        if result > 0 and result < 1_000_000:
            return float(result)
    except Exception:
        pass  # nosec B110
    return None


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


# ---------------------------------------------------------------------------
# Adaptive native-resolution detection
# ---------------------------------------------------------------------------


def _best_native_longest_px(
    layers, zone_extent: QgsRectangle, map_crs, max_dim: int
) -> int:
    """Compute the optimal output 'longest side' in pixels from the data.

    Picks the longest ground span and the finest m/px across layers. Returns
    ``max_dim`` when no visible layer carries a usable native resolution.
    """
    zw_m, zh_m = _zone_dims_meters(zone_extent, map_crs)
    if not zw_m or not zh_m or zw_m <= 0 or zh_m <= 0:
        return max_dim

    finest_mpp: float | None = None
    for layer in layers or []:
        try:
            xy = _native_pixel_size_xy_m(layer, zone_extent, map_crs)
        except Exception:
            xy = None
        if xy:
            mpp_x, mpp_y = xy
            if mpp_x and mpp_x > 0:
                finest_mpp = mpp_x if finest_mpp is None else min(finest_mpp, mpp_x)
            if mpp_y and mpp_y > 0:
                finest_mpp = mpp_y if finest_mpp is None else min(finest_mpp, mpp_y)

    if finest_mpp is None:
        return max_dim

    longest_m = max(zw_m, zh_m)
    optimal = int(math.ceil(longest_m / finest_mpp))
    return max(1, min(optimal, max_dim))


def _native_pixel_size_xy_m(
    layer, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Native ``(mpp_x, mpp_y)`` at the zone, or None if the layer doesn't constrain output.

    Vector tile layers use ``sourceMaxZoom()``. Raster layers use
    ``rasterUnitsPerPixelX/Y()`` for any non-XYZ provider, and zmax for XYZ
    tiles. WMS, mesh, point cloud, and vector layers return None.
    """
    if layer is None:
        return None

    if QgsVectorTileLayer is not None and isinstance(layer, QgsVectorTileLayer):
        return _vector_tile_native_mpp_xy(layer, zone_extent, map_crs)

    if layer.type() != QgsMapLayer.LayerType.RasterLayer:
        return None

    if not _intersects_zone(layer, zone_extent, map_crs):
        return None

    provider = layer.dataProvider()
    if provider is None:
        return None

    source = (layer.source() or "").lower()
    provider_name = provider.name() or ""

    if "type=xyz" in source:
        return _xyz_native_mpp_xy(layer, zone_extent, map_crs)

    # Plain WMS / WMTS without a usable pixel grid: no client-side native res.
    if provider_name == "wms":
        return None

    return _raster_native_mpp_xy(layer, zone_extent, map_crs)


def _intersects_zone(layer, zone_extent: QgsRectangle, map_crs) -> bool:
    """Whether the layer's extent intersects the zone, transformed if needed."""
    try:
        layer_crs = layer.crs()
        if layer_crs == map_crs:
            return layer.extent().intersects(zone_extent)
        transform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
        zone_in_layer = transform.transformBoundingBox(zone_extent)
        return layer.extent().intersects(zone_in_layer)
    except Exception:
        return True


def _raster_native_mpp_xy(
    layer, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Native (m/px X, m/px Y) of a raster layer at the zone."""
    try:
        px_x = float(layer.rasterUnitsPerPixelX() or 0)
        px_y = float(layer.rasterUnitsPerPixelY() or 0)
        if px_x <= 0 or px_y <= 0:
            return None
        return _layer_units_to_meters_xy(layer, px_x, px_y, zone_extent, map_crs)
    except Exception:
        return None


def _layer_units_to_meters_xy(
    layer, px_x: float, px_y: float, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Convert pixel size (in layer CRS units) to meters at the zone centroid."""
    try:
        layer_crs = layer.crs()
        if layer_crs == map_crs:
            center = zone_extent.center()
        else:
            transform = QgsCoordinateTransform(map_crs, layer_crs, QgsProject.instance())
            center = transform.transform(zone_extent.center())

        da = QgsDistanceArea()
        da.setSourceCrs(layer_crs, QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")

        m_per_unit_x = da.measureLine(
            QgsPointXY(center.x(), center.y()),
            QgsPointXY(center.x() + 1.0, center.y()),
        )
        m_per_unit_y = da.measureLine(
            QgsPointXY(center.x(), center.y()),
            QgsPointXY(center.x(), center.y() + 1.0),
        )
        if m_per_unit_x <= 0 or m_per_unit_y <= 0:
            return None
        return (px_x * m_per_unit_x, px_y * m_per_unit_y)
    except Exception:
        return None


def _xyz_native_mpp_xy(
    layer, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Native m/px of an XYZ tile layer at its zmax for the zone centroid."""
    zmax = _xyz_zmax(layer)
    if zmax is None:
        return None
    mpp = _webmerc_mpp_at_lat(zone_extent, map_crs, zmax)
    if mpp is None:
        return None
    return (mpp, mpp)


def _xyz_zmax(layer) -> int | None:
    """Parse zmax from the XYZ URI. Returns None when not present."""
    try:
        uri = QgsDataSourceUri()
        uri.setEncodedUri(layer.source())
        z = uri.param("zmax")
        return int(z) if z else None
    except Exception:
        return None


def _vector_tile_native_mpp_xy(
    layer, zone_extent: QgsRectangle, map_crs
) -> tuple[float, float] | None:
    """Native m/px of a vector tile layer at its source max zoom."""
    try:
        zmax = int(layer.sourceMaxZoom())
    except Exception:
        return None
    if zmax <= 0:
        return None
    mpp = _webmerc_mpp_at_lat(zone_extent, map_crs, zmax)
    if mpp is None:
        return None
    return (mpp, mpp)


def _webmerc_mpp_at_lat(zone_extent, map_crs, zoom: int) -> float | None:
    """Web Mercator meters/pixel at the zone centroid latitude for the given zoom."""
    try:
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if map_crs == wgs84:
            lat = zone_extent.center().y()
        else:
            transform = QgsCoordinateTransform(map_crs, wgs84, QgsProject.instance())
            lat = transform.transform(zone_extent.center()).y()
        lat = max(-85.0, min(85.0, lat))
        return _WEBMERC_M_PX_Z0 * math.cos(math.radians(lat)) / (2 ** zoom)
    except Exception:
        return None


def _zone_dims_meters(
    zone_extent: QgsRectangle, map_crs
) -> tuple[float | None, float | None]:
    """Zone (width_m, height_m) measured geodesically."""
    try:
        da = QgsDistanceArea()
        da.setSourceCrs(map_crs, QgsProject.instance().transformContext())
        da.setEllipsoid(QgsProject.instance().ellipsoid() or "WGS84")
        center_y = zone_extent.center().y()
        center_x = zone_extent.center().x()
        width_m = da.measureLine(
            QgsPointXY(zone_extent.xMinimum(), center_y),
            QgsPointXY(zone_extent.xMaximum(), center_y),
        )
        height_m = da.measureLine(
            QgsPointXY(center_x, zone_extent.yMinimum()),
            QgsPointXY(center_x, zone_extent.yMaximum()),
        )
        if width_m <= 0 or height_m <= 0:
            return (None, None)
        return (width_m, height_m)
    except Exception:
        return (None, None)


def _warn_if_xyz_capped(
    layers, zone_extent: QgsRectangle, map_crs, out_w: int
) -> str | None:
    """Returns a localized message when an XYZ layer's zmax forces softer output."""
    zw_m, _ = _zone_dims_meters(zone_extent, map_crs)
    if not zw_m or out_w <= 0:
        return None
    requested_mpp = zw_m / out_w
    if requested_mpp <= 0:
        return None

    for layer in layers or []:
        if layer is None or layer.type() != QgsMapLayer.LayerType.RasterLayer:
            continue
        if "type=xyz" not in (layer.source() or "").lower():
            continue
        zmax = _xyz_zmax(layer)
        if zmax is None:
            continue
        actual_mpp = _webmerc_mpp_at_lat(zone_extent, map_crs, zmax)
        if actual_mpp is None or actual_mpp <= requested_mpp * _CAPPED_WARN_RATIO:
            continue
        loss = max(0.0, 1.0 - requested_mpp / actual_mpp)
        log_warning(
            f"Layer '{layer.name()}' is capped at zoom {zmax}; "
            f"output will be ~{int(loss * 100)}% softer than the selection allows. "
            "Re-add this XYZ layer with a higher zmax for full resolution."
        )
        return tr(
            "Basemap '{name}' is capped at zoom {z}. Output will be ~{loss}% softer "
            "than the selection allows. Re-add the XYZ layer with a higher zmax for "
            "full resolution."
        ).format(name=layer.name(), z=zmax, loss=int(loss * 100))
    return None
