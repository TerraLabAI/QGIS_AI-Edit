from __future__ import annotations

import base64
import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDataSourceUri,
    QgsDistanceArea,
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
from qgis.PyQt.QtGui import QImage, QImageWriter, QPainter

from ..core import qt_compat as QtC
from ..core.errors import AIEditError, ErrorCode
from ..core.i18n import tr
from ..core.logger import log_debug, log_warning

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
    crosses_antimeridian = False
    if not map_crs.isGeographic():
        try:
            to_wgs = QgsCoordinateTransform(
                map_crs,
                QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance(),
            )
            geographic_extent = to_wgs.transformBoundingBox(extent)
            # transformBoundingBox collapses the box to [min_lon, max_lon],
            # which makes a true dateline crossing look identical to a merely
            # very wide zone (both report a > 180 deg span). Compare the actual
            # left and right edges instead: when the zone wraps past 180 deg,
            # proj normalizes the east edge to a longitude west of the west edge.
            y_mid = (extent.yMinimum() + extent.yMaximum()) / 2.0
            left_lon = to_wgs.transform(QgsPointXY(extent.xMinimum(), y_mid)).x()
            right_lon = to_wgs.transform(QgsPointXY(extent.xMaximum(), y_mid)).x()
            crosses_antimeridian = right_lon < left_lon
        except Exception:
            geographic_extent = None
    else:
        # Geographic project: only a narrow zone can genuinely wrap the dateline.
        # A span >= 180 deg is just a very wide (or out-of-range) zone, not a
        # crossing - mirror the projected path and don't flag it. A true wrap is
        # a narrow zone whose edges land in different 360-deg longitude cells.
        raw_width = extent.xMaximum() - extent.xMinimum()
        crosses_antimeridian = raw_width < 180.0 and (
            math.floor((extent.xMinimum() + 180.0) / 360.0)
            != math.floor((extent.xMaximum() + 180.0) / 360.0)
        )

    if geographic_extent is not None:
        max_abs_lat = max(abs(geographic_extent.yMinimum()), abs(geographic_extent.yMaximum()))
        # The antimeridian and polar guards only make sense for real lat/lon.
        # When the data sits outside valid geographic bounds (a layer in meters
        # or a non-georeferenced layer tagged EPSG:4326, so latitude exceeds
        # +/-90 deg), neither concept applies - skip the guards and let the zone
        # through rather than block the user with a misleading refusal.
        coords_in_range = (
            max_abs_lat <= 90.0
            and geographic_extent.xMinimum() >= -540.0
            and geographic_extent.xMaximum() <= 540.0
        )
        if coords_in_range and crosses_antimeridian:
            raise AIEditError(
                ErrorCode.ANTIMERIDIAN,
                tr(
                    "This zone crosses the antimeridian (180 deg longitude). "
                    "AI Edit does not support that yet. Split your zone into two."
                ),
            )
        if coords_in_range and max_abs_lat > _POLAR_ABS_LAT_DEG:
            raise AIEditError(
                ErrorCode.POLAR,
                tr(
                    "Zone is too close to a pole (above {limit} degrees latitude). "
                    "AI Edit cannot estimate ground resolution there."
                ).format(limit=int(_POLAR_ABS_LAT_DEG)),
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


# Input image encoding. The canvas render is photographic/satellite content;
# encoding it lossless runs tens of MB at 4K and inflates a further ~33% as
# base64, which slows uploads for users and runs up egress on our side. We
# encode a high-quality lossy format instead: the input is only a reference the
# model re-renders, so quality 90 is visually indistinguishable while ~15-25x
# smaller. Format and quality come from server config so they stay tunable
# without a plugin re-release; WebP is preferred (smaller than JPEG at equal
# quality) with a JPEG fallback when the Qt WebP codec is absent (it is not
# bundled on every platform).
_DEFAULT_INPUT_FORMAT = "webp"
_DEFAULT_INPUT_QUALITY = 90
_supported_write_formats_cache: set[str] | None = None


def _supported_write_formats() -> set[str]:
    """Lowercased set of image formats this Qt build can write. Cached."""
    global _supported_write_formats_cache
    if _supported_write_formats_cache is None:
        try:
            _supported_write_formats_cache = {
                bytes(f).decode("ascii", "ignore").lower()
                for f in QImageWriter.supportedImageFormats()
            }
        except Exception:
            _supported_write_formats_cache = set()
    return _supported_write_formats_cache


def chosen_input_format() -> tuple[str, str, int]:
    """Pick the encode format for the canvas input as ``(qt_name, token, quality)``.

    ``token`` is the wire identifier ('webp' | 'jpeg' | 'png') the server uses
    to sign the upload with a matching content-type. Reads server config
    ``input_format`` / ``input_quality`` with safe defaults so an older config
    (or none) never breaks a generation. Falls back webp -> jpeg when the WebP
    codec is unavailable in this Qt build (JPEG is always present in Qt).
    """
    cfg = _get_server_config() or {}
    pref = str(cfg.get("input_format") or _DEFAULT_INPUT_FORMAT).lower()
    try:
        quality = int(cfg.get("input_quality") or _DEFAULT_INPUT_QUALITY)
    except (TypeError, ValueError):
        quality = _DEFAULT_INPUT_QUALITY
    quality = max(1, min(100, quality))

    supported = _supported_write_formats()
    if pref == "webp" and "webp" in supported:
        return ("WEBP", "webp", quality)
    if pref == "png":
        return ("PNG", "png", quality)
    # 'jpeg'/'jpg', or 'webp' requested without the codec, or an unknown token.
    return ("JPEG", "jpeg", quality)


class ExportPrep:
    """Render+encode snapshot. Built on main thread, consumed by a worker."""

    __slots__ = (
        "settings",
        "out_w",
        "out_h",
        "actual_extent",
        "background_color",
        "map_crs",
        "guidance_settings",
    )

    def __init__(
        self,
        settings,
        out_w,
        out_h,
        actual_extent,
        background_color,
        map_crs,
        guidance_settings=None,
    ):
        self.settings = settings
        self.out_w = out_w
        self.out_h = out_h
        self.actual_extent = actual_extent
        self.background_color = background_color
        self.map_crs = map_crs
        # When markup annotations exist, a second QgsMapSettings rendering the
        # SAME zone with the markup layer on top. None when there is no markup.
        self.guidance_settings = guidance_settings


def prepare_export(
    map_settings: QgsMapSettings,
    extent: QgsRectangle,
    target_resolution: str | None = None,
    markup_layer: QgsMapLayer | None = None,
) -> ExportPrep:
    """Pick output size and clone settings. Cheap, main-thread.

    When ``markup_layer`` is given (the user drew guidance annotations), the
    MAIN image excludes that layer so the model sees the original pixels
    untouched, and a second ``guidance_settings`` is built that renders the
    same zone WITH the markup on top. Both share the identical adjusted extent
    and output size so the overlay registers pixel-for-pixel with the main
    image.
    """
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

    guidance_settings = None
    if markup_layer is not None:
        try:
            markup_id = markup_layer.id()
        except RuntimeError:
            markup_id = None
        if markup_id is not None:
            # Main image: drop the markup layer so the model gets clean pixels.
            all_layers = settings.layers()
            main_layers = [lyr for lyr in all_layers if lyr.id() != markup_id]
            markup_in_canvas = len(main_layers) != len(all_layers)
            settings.setLayers(main_layers)
            # Guidance image: clone again from the same source (markup still in
            # the layer list, on top) and apply the SAME adjusted_extent +
            # out_w/out_h. Do not recompute these or the overlay would drift
            # out of registration with the main image.
            guidance_settings = _clone_map_settings(map_settings)
            guidance_settings.setExtent(adjusted_extent)
            guidance_settings.setOutputSize(QSize(out_w, out_h))
            log_debug(
                f"Markup guidance prep: markup_in_canvas={markup_in_canvas}, "
                f"main_layers={len(main_layers)}, "
                f"guidance_layers={len(guidance_settings.layers())}, "
                f"out={out_w}x{out_h}"
            )
            if not markup_in_canvas:
                # Markup drawn but its layer is not in the canvas render set
                # (user hid it): the guidance overlay would be identical to the
                # clean main image, so the feature silently no-ops.
                log_warning(
                    "Markup guidance: markup layer not in canvas render set "
                    "(hidden?); guidance overlay matches the main image"
                )
        else:
            log_warning(
                "Markup guidance: markup layer reference is stale; "
                "no guidance overlay rendered"
            )

    return ExportPrep(
        settings=settings,
        out_w=out_w,
        out_h=out_h,
        actual_extent=settings.visibleExtent(),
        background_color=map_settings.backgroundColor(),
        map_crs=map_crs,
        guidance_settings=guidance_settings,
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
    b64, raw_len, fmt_token = _encode_image(image, prep.out_w, prep.out_h)
    return b64, raw_len, prep.actual_extent, fmt_token


def render_guidance(prep: ExportPrep) -> tuple[str, str] | None:
    """Render the markup-overlay guidance image (original zone + markup on top).

    Returns ``(b64, format_token)``, or ``None`` when there is no markup to
    render. The format token is the guidance render's OWN actual format so the
    upload content-type stays correct even if its encode falls back to PNG
    independently of the main image.
    """
    if prep.guidance_settings is None:
        return None
    image = _render_settings_to_image(
        prep.guidance_settings, prep.out_w, prep.out_h, prep.background_color
    )
    b64, raw_len, fmt_token = _encode_image(image, prep.out_w, prep.out_h)
    log_debug(
        f"Guidance image rendered: dims={prep.out_w}x{prep.out_h} "
        f"format={fmt_token} raw_bytes={raw_len} b64_bytes={len(b64)}"
    )
    return b64, fmt_token


def apply_export_context(
    ctx,
    prep: ExportPrep,
    actual_extent: QgsRectangle,
    image_size_bytes: int,
    input_format: str | None = None,
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
    ctx.bbox_wgs84 = _bbox_wgs84(actual_extent, prep.map_crs)
    ctx.basemap = _detect_basemap(prep.settings.layers())
    ctx.ground_resolution_m = _compute_ground_resolution_m(
        actual_extent, prep.out_w, prep.out_h, prep.map_crs
    )
    ctx.export_width = prep.out_w
    ctx.export_height = prep.out_h
    ctx.image_size_bytes = image_size_bytes
    ctx.input_format = input_format


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


# A little more than the model's output budget so the input never ends up
# smaller than the output (which would make the model upscale) after aspect
# skew and pixel alignment. 1.2x linear ~= 1.44x area; still a small upload.
_INPUT_BUDGET_HEADROOM = 1.2


def _budget_dims(
    extent: QgsRectangle, ref: int, align: int, max_dim: int
) -> tuple[int, int]:
    """Derive (out_w, out_h) targeting ~``ref``^2 total pixels (the model's
    output budget for the tier), aspect-preserved, so the input matches the
    output instead of undershooting it on non-square zones.

    A small headroom keeps the input >= the output; the long side is capped at
    ``max_dim`` proportionally so the aspect is preserved even when clamped.
    """
    ext_ratio = extent.width() / extent.height()
    budget = (float(ref) * _INPUT_BUDGET_HEADROOM) ** 2
    out_h = (budget / ext_ratio) ** 0.5
    out_w = out_h * ext_ratio

    longest = max(out_w, out_h)
    if longest > max_dim:
        scale = max_dim / longest
        out_w *= scale
        out_h *= scale

    out_w = max(align, int(round(out_w / align)) * align)
    out_h = max(align, int(round(out_h / align)) * align)
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


def _bbox_wgs84(extent: QgsRectangle, src_crs) -> dict | None:
    """Rendered extent reprojected to WGS84 (EPSG:4326) as W/S/E/N degrees.

    Reprojected client-side so the backend stores an exact footprint without a
    proj library. transformBoundingBox densifies the edges, so the envelope of
    a rotated or conformal source CRS stays tight rather than clipping corners.
    """
    try:
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if src_crs == wgs84:
            box = extent
        else:
            transform = QgsCoordinateTransform(src_crs, wgs84, QgsProject.instance())
            box = transform.transformBoundingBox(extent)
        return {
            "west": box.xMinimum(),
            "south": box.yMinimum(),
            "east": box.xMaximum(),
            "north": box.yMaximum(),
        }
    except Exception:
        return None


# Known basemap tile hosts -> friendly label, so a generation reads "Google" or
# "IGN" rather than a raw tile URL. Substring match on the sanitized host.
_BASEMAP_HOSTS = (
    ("google", "Google"),
    ("gstatic", "Google"),
    ("virtualearth", "Bing"),
    ("bing", "Bing"),
    ("geopf.fr", "IGN"),
    ("ign.fr", "IGN"),
    ("geoportail", "IGN"),
    ("arcgisonline", "Esri"),
    ("esri", "Esri"),
    ("mapbox", "Mapbox"),
    ("openstreetmap", "OSM"),
    ("tile.osm", "OSM"),
    ("cartocdn", "Carto"),
    ("swisstopo", "Swisstopo"),
)


def _basemap_label(layer) -> str | None:
    """Sanitized identity of one raster basemap layer.

    Friendly name for known tile hosts, else '<kind>:<host>' with the host only,
    else the provider kind. Never returns a full source, file path, or URL query:
    those can carry local disk paths or embedded auth tokens.
    """
    try:
        provider = (layer.providerType() or "").lower()
    except Exception:
        return None
    if provider == "gdal":
        return "local raster"
    if provider not in ("wms", "wmts", "arcgismapserver"):
        return provider or None
    try:
        from urllib.parse import parse_qs, urlsplit

        params = parse_qs(layer.source() or "")
        url = (params.get("url") or [""])[0]
        kind = "XYZ" if (params.get("type") or [""])[0] == "xyz" else "WMS"
        host = (urlsplit(url).hostname or "").lower()
        for needle, label in _BASEMAP_HOSTS:
            if needle in host:
                return label
        return (f"{kind}:{host}" if host else kind)[:64]
    except Exception:
        return None


def _detect_basemap(layers) -> str | None:
    """Sanitized identity of the bottom-most raster basemap under the zone."""
    try:
        from qgis.core import QgsRasterLayer

        # layers() is top-to-bottom; the basemap is normally the lowest raster.
        for layer in reversed(list(layers)):
            if isinstance(layer, QgsRasterLayer):
                label = _basemap_label(layer)
                if label:
                    return label
    except Exception:
        return None
    return None


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
