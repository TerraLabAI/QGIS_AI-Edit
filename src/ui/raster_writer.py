from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import unicodedata

from osgeo import gdal, osr
from qgis.core import QgsProject, QgsRasterLayer

from ..core.i18n import tr
from ..core.logger import log_debug, log_warning
from ..core.prompts.prompt_presets import lookup_template_by_prompt
from .layer_groups import add_layer_to_ai_edit_top

# Formats GDAL reliably decodes across all platforms (esp. Windows OSGeo4W,
# which often ships without WebP/AVIF drivers).
_GDAL_SAFE_FORMATS = {"PNG", "JPEG", "TIFF", "GIF", "BMP"}


def _detect_image_format(data: bytes) -> str | None:
    """Identify the image format from magic bytes. Returns None if unknown."""
    if len(data) < 12:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if data[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WebP"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    if data[:2] in (b"II", b"MM") and data[2:4] in (b"\x2a\x00", b"\x00\x2a"):
        return "TIFF"
    if data[:2] == b"BM":
        return "BMP"
    # AVIF/HEIF: ftyp box at offset 4
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"avif", b"avis"):
            return "AVIF"
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            return "HEIF"
    return None


def _ascii_safe_dir(directory: str) -> str:
    """Return a directory path both GDAL (write) and the QGIS GDAL provider
    (read-back) accept on Windows.

    Accented Windows usernames put non-ASCII characters in the output path.
    GDAL writes the GeoTIFF without error, but the QGIS raster provider then
    loads it as an invalid layer ("Failed to create valid raster layer").
    Converting the directory to its 8.3 short name yields a pure-ASCII path
    both accept. No-op on non-Windows, on already-ASCII paths, or when
    conversion is unavailable. The directory must already exist.
    """
    if os.name != "nt" or directory.isascii():
        return directory

    try:
        import ctypes
        from ctypes import wintypes

        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(4096)
        n = get_short(directory, buf, len(buf))
        if 0 < n < len(buf) and buf.value.isascii():
            return buf.value
    except Exception as e:  # noqa: BLE001 - Windows-only, never block a write
        log_warning(f"short-path conversion failed: {e}")

    # 8.3 short names are disabled on this volume. C:\\Users\\Public is ASCII
    # and writable by every user; fall back to it so the layer still loads.
    # Keep the last path component (the per-generation folder, already ASCII via
    # _slugify + timestamp) as a subdirectory so each generation gets its own
    # output path. A flat fallback would route every generation to the same
    # raster.tif; once QGIS holds the first result open, the next GDAL Create on
    # that path returns None -> write_error.
    public = os.environ.get("PUBLIC")
    if public and public.isascii():
        tail = os.path.basename(directory.rstrip(os.sep))
        if tail and tail.isascii():
            safe = os.path.join(public, "terralab_ai_edit", tail)
        else:
            safe = os.path.join(public, "terralab_ai_edit")
        try:
            os.makedirs(safe, exist_ok=True)
            return safe
        except OSError:
            pass
    return directory


def write_geotiff(
    image_data: bytes,
    extent_dict: dict,
    crs_wkt: str,
    output_dir: str,
    prompt: str = "",
    ctx=None,
) -> str:
    """Write raw image bytes as a georeferenced GeoTIFF. GDAL-only so it runs on a worker thread."""
    timestamp = int(time.time())
    slug = _slugify(prompt)[:40] if prompt else "generated"
    folder_stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(timestamp))
    generation_folder_name = f"{folder_stamp}_{slug}" if slug else folder_stamp
    filename = "raster.tif"

    # Fall back to tempdir if user's output_dir is read-only so a hostile
    # folder doesn't lose the paid generation.
    primary_generation_dir = os.path.join(output_dir, generation_folder_name)
    try:
        os.makedirs(primary_generation_dir, exist_ok=True)
        resolved_dir = primary_generation_dir
    except OSError as e:
        log_warning(f"output_dir not writable ({e}); using tempdir fallback")
        resolved_dir = os.path.join(
            tempfile.gettempdir(), "terralab_ai_edit", generation_folder_name
        )
        os.makedirs(resolved_dir, exist_ok=True)

    # Reroute through an ASCII-safe path: a non-ASCII directory (accented
    # Windows username) writes fine via GDAL but loads back as an invalid
    # QGIS layer. The directory exists by now, so 8.3 short names resolve.
    resolved_dir = _ascii_safe_dir(resolved_dir)
    primary_path = os.path.join(resolved_dir, filename)
    output_path = primary_path

    xmin = extent_dict["xmin"]
    ymin = extent_dict["ymin"]
    xmax = extent_dict["xmax"]
    ymax = extent_dict["ymax"]

    if not image_data:
        raise RuntimeError(tr("Server returned an empty response (0 bytes)"))

    img_format = _detect_image_format(image_data)
    head_hex = bytes(image_data[:16]).hex()
    log_debug(
        f"GeoTIFF input: {len(image_data)} bytes, "
        f"format={img_format or 'unknown'}, head={head_hex}"
    )
    if img_format is None:
        log_warning(
            f"Unrecognized image payload ({len(image_data)} bytes, head={head_hex})"
        )
        raise RuntimeError(tr(
            "Server returned data that is not a recognized image format. "
            "This usually means the server replied with an error page. "
            "Please try again or check the QGIS log."
        ))

    # /vsimem avoids the Windows WinError 32 from the PNG driver holding the temp file.
    vsimem_path = f"/vsimem/_temp_{timestamp}.png"
    try:
        gdal.FileFromMemBuffer(vsimem_path, bytes(image_data))
        try:
            src_ds = gdal.Open(vsimem_path)
        except RuntimeError as e:
            if img_format not in _GDAL_SAFE_FORMATS:
                raise RuntimeError(tr(
                    "Image format {fmt} is not supported by your QGIS "
                    "GDAL build. Please update QGIS or contact support."
                ).format(fmt=img_format)) from e
            raise
        if src_ds is None:
            if img_format not in _GDAL_SAFE_FORMATS:
                raise RuntimeError(tr(
                    "Image format {fmt} is not supported by your QGIS "
                    "GDAL build. Please update QGIS or contact support."
                ).format(fmt=img_format))
            raise RuntimeError(
                tr("Failed to open downloaded {fmt} image with GDAL").format(fmt=img_format)
            )

        # Flat-color "semantic" maps (e.g. "2 colors in flat tints") usually
        # come back as palette (PNG-8) images: GDAL opens them as a single
        # indexed band. Writing that band as-is loses the true RGB colors,
        # leaving a 1-band raster that displays wrong and can't be vectorized
        # by color (the Vectorize panel needs >=3 bands). Expand the palette
        # to real RGB so the output is always a 3-band color raster.
        if src_ds.RasterCount == 1 and src_ds.GetRasterBand(1).GetColorTable() is not None:
            log_debug("GeoTIFF: expanding palette image to RGB")
            expanded = gdal.Translate("", src_ds, format="MEM", rgbExpand="rgb")
            if expanded is not None:
                src_ds = expanded

        recv_w = src_ds.RasterXSize
        recv_h = src_ds.RasterYSize
        src_bands = src_ds.RasterCount
        bands = min(src_bands, 3)
        log_debug(f"GeoTIFF: received {recv_w}x{recv_h}px, {bands} bands")

        ext_width = xmax - xmin
        ext_height = ymax - ymin
        log_debug(f"GeoTIFF extent: {ext_width:.2f}x{ext_height:.2f} map units")

        if ctx is not None:
            ctx.received_image_width = recv_w
            ctx.received_image_height = recv_h
            ctx.received_size_bytes = len(image_data)
            ctx.crop_offsets = (0, 0, recv_w, recv_h)

        driver = gdal.GetDriverByName("GTiff")
        dst_ds = driver.Create(
            output_path, recv_w, recv_h, bands, gdal.GDT_Byte
        )
        if dst_ds is None and output_path == primary_path:
            # Windows MAX_PATH / antivirus lock / perm denied — retry in tempdir.
            log_warning(f"GDAL Create failed at {primary_path}, retrying in tempdir")
            fallback_dir = os.path.join(
                tempfile.gettempdir(), "terralab_ai_edit", generation_folder_name
            )
            os.makedirs(fallback_dir, exist_ok=True)
            output_path = os.path.join(_ascii_safe_dir(fallback_dir), filename)
            dst_ds = driver.Create(
                output_path, recv_w, recv_h, bands, gdal.GDT_Byte
            )
        if dst_ds is None:
            raise RuntimeError(
                tr("Failed to create GeoTIFF at {path}").format(path=output_path)
            )

        x_res = ext_width / recv_w
        y_res = ext_height / recv_h
        geotransform = (xmin, x_res, 0, ymax, 0, -y_res)
        dst_ds.SetGeoTransform(geotransform)

        if ctx is not None:
            ctx.output_path = output_path
            ctx.geotransform = geotransform
            ctx.output_bands = bands
            ctx.output_dimensions = (recv_w, recv_h)

        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs_wkt)
        dst_ds.SetProjection(srs.ExportToWkt())

        timestamp_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        dst_ds.SetMetadataItem("AI_EDIT_PROMPT", prompt)
        dst_ds.SetMetadataItem("AI_EDIT_TIMESTAMP", timestamp_iso)
        dst_ds.SetMetadataItem("AI_EDIT_CRS", crs_wkt[:200])
        dst_ds.SetMetadataItem(
            "AI_EDIT_EXTENT",
            f"{xmin:.6f},{ymin:.6f},{xmax:.6f},{ymax:.6f}",
        )
        dst_ds.SetMetadataItem(
            "AI_EDIT_RESOLUTION",
            ctx.submitted_resolution if ctx else "unknown",
        )
        dst_ds.SetMetadataItem("AI_EDIT_MODEL", "AI Edit")
        # Standard tags so the file reads correctly outside the plugin.
        dst_ds.SetMetadataItem("TIFFTAG_SOFTWARE", "AI Edit by TerraLab")
        dst_ds.SetMetadataItem(
            "TIFFTAG_DATETIME", time.strftime("%Y:%m:%d %H:%M:%S", time.gmtime())
        )
        dst_ds.SetMetadataItem("TIFFTAG_IMAGEDESCRIPTION", prompt[:512])

        # Copy bands via raw GDAL buffers, not ReadAsArray/WriteArray. The array
        # path pulls in osgeo.gdal_array -> numpy; a broken numpy ABI (common on
        # Windows when another package upgrades numpy in the QGIS env) throws
        # "numpy.core.multiarray failed to import" here and the whole save fails
        # even though GDAL itself works. ReadRaster/WriteRaster are pure C.
        for i in range(1, bands + 1):
            raw = src_ds.GetRasterBand(i).ReadRaster(
                0, 0, recv_w, recv_h, recv_w, recv_h, gdal.GDT_Byte
            )
            dst_ds.GetRasterBand(i).WriteRaster(
                0, 0, recv_w, recv_h, raw, recv_w, recv_h, gdal.GDT_Byte
            )

        dst_ds.FlushCache()
        dst_ds = None
        src_ds = None
    finally:
        gdal.Unlink(vsimem_path)

    # GDAL can return a dataset and still leave no file on disk (silent driver
    # failure). Catch it here so the user gets a clear write error instead of a
    # confusing "invalid layer" later.
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(
            tr("GeoTIFF write produced no file at {path}").format(path=output_path)
        )

    # STAC-style sidecar mirrors the TIFF tags for tools that ignore GDAL metadata.
    try:
        _write_provenance_sidecar(
            output_path=output_path,
            prompt=prompt,
            crs_wkt=crs_wkt,
            extent=(xmin, ymin, xmax, ymax),
            timestamp_iso=timestamp_iso,
            ctx=ctx,
        )
    except Exception as err:  # nosec B110
        log_warning(f"Provenance sidecar write failed: {err}")

    return output_path


def _write_provenance_sidecar(
    output_path: str,
    prompt: str,
    crs_wkt: str,
    extent: tuple,
    timestamp_iso: str,
    ctx,
) -> None:
    """Provenance sidecar (prompt, request ids, CRS, extent)."""
    import json as _json

    xmin, ymin, xmax, ymax = extent
    payload = {
        "type": "ai-edit-provenance",
        "version": 1,
        "created_at": timestamp_iso,
        "prompt": prompt,
        "model": "AI Edit",
        "request_id": getattr(ctx, "request_id", None),
        "parent_request_id": getattr(ctx, "parent_request_id", None),
        "template_id": getattr(ctx, "template_id", None),
        "template_name": getattr(ctx, "template_name", None),
        "resolution": getattr(ctx, "submitted_resolution", None),
        "aspect_ratio": getattr(ctx, "submitted_aspect_ratio", None),
        "extent": {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax},
        "crs_wkt": crs_wkt[:5000],
        "ground_resolution_m": getattr(ctx, "ground_resolution_m", None),
        "centroid_lat": getattr(ctx, "centroid_lat", None),
        "centroid_lon": getattr(ctx, "centroid_lon", None),
    }
    sidecar = os.path.splitext(output_path)[0] + ".ai-edit.json"
    with open(sidecar, "w", encoding="utf-8") as f:
        _json.dump(payload, f, ensure_ascii=False, indent=2)


def _humanize_prompt(prompt: str, max_chars: int = 30) -> str:
    text = re.sub(r"\s+", " ", (prompt or "")).strip()
    if not text:
        return ""
    if len(text) > max_chars:
        cut = text[:max_chars].rsplit(" ", 1)[0] or text[:max_chars]
        text = cut.rstrip(" ,.;:-") + "..."
    return text[:1].upper() + text[1:]


def _build_layer_name(prompt: str) -> str:
    match = lookup_template_by_prompt(prompt) if prompt else None
    if match is not None:
        return match[1]
    humanized = _humanize_prompt(prompt)
    return humanized or "AI Edit result"


def _reload_from_ascii_copy(src_path: str, display_name: str) -> QgsRasterLayer | None:
    """Last-ditch recovery when a GeoTIFF loads as an invalid layer.

    Copies the file to an ASCII-safe temp path under a plain ASCII filename and
    retries the load. This rescues files written to an accented directory
    (older builds, or volumes where 8.3 short names are disabled), where the
    QGIS GDAL provider refuses the original path.
    """
    try:
        base = os.path.join(tempfile.gettempdir(), "terralab_ai_edit")
        os.makedirs(base, exist_ok=True)
        safe_dir = _ascii_safe_dir(base)
        # Keep the original (ASCII, _slugify-guaranteed) name so two recoveries
        # don't collide; only synthesize one if the basename isn't ASCII.
        name = os.path.basename(src_path)
        if not name.isascii():
            name = f"ai_edit_{int(time.time())}.tif"
        safe_path = os.path.join(safe_dir, name)
        shutil.copyfile(src_path, safe_path)
        layer = QgsRasterLayer(safe_path, display_name)
        if layer.isValid():
            log_warning("raster recovered via ASCII-safe copy after invalid path")
            return layer
    except Exception as e:  # noqa: BLE001 - best-effort recovery
        log_warning(f"ASCII-safe reload failed: {e}")
    return None


def add_geotiff_to_project(
    geotiff_path: str,
    prompt: str = "",
) -> QgsRasterLayer:
    """Add GeoTIFF as a flat child of AI-Edit. Sub-group is created lazily on first vectorize."""
    display_name = _build_layer_name(prompt)

    existing_names = {lyr.name() for lyr in QgsProject.instance().mapLayers().values()}
    if display_name in existing_names:
        counter = 2
        while f"{display_name} ({counter})" in existing_names:
            counter += 1
        display_name = f"{display_name} ({counter})"

    # Build the layer from the absolute path so it is always valid. QGIS stores
    # it relative to the project on save (the default), so the .qgz stays
    # portable. Passing a project-relative path here instead would make
    # QgsRasterLayer resolve it against the current working directory, not the
    # project, and the layer would fail to load whenever they differ.
    project = QgsProject.instance()
    layer = QgsRasterLayer(geotiff_path, display_name)
    if not layer.isValid():
        layer = _reload_from_ascii_copy(geotiff_path, display_name)
    if layer is None or not layer.isValid():
        exists = os.path.exists(geotiff_path)
        size = os.path.getsize(geotiff_path) if exists else -1
        msg = tr("Failed to create valid raster layer from {path}").format(path=geotiff_path)
        raise RuntimeError(f"{msg} (exists={exists}, size={size} bytes)")

    _apply_default_raster_style(layer)

    project.addMapLayer(layer, False)
    node = add_layer_to_ai_edit_top(layer)
    if node is not None:
        node.setExpanded(False)

    return layer


def _apply_default_raster_style(layer: QgsRasterLayer) -> None:
    """3-band RGB renderer + .qml sidecar so the file renders the same standalone."""
    try:
        from qgis.core import QgsMultiBandColorRenderer

        provider = layer.dataProvider()
        if provider is None or provider.bandCount() < 3:
            return
        renderer = QgsMultiBandColorRenderer(provider, 1, 2, 3)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except Exception as err:  # nosec B110
        log_warning(f"Default raster renderer skipped: {err}")
        return

    try:
        path = layer.source() or ""
        if "|" in path:
            path = path.split("|", 1)[0]
        if path and os.path.isfile(path):
            qml_path = os.path.splitext(path)[0] + ".qml"
            layer.saveNamedStyle(qml_path)
    except Exception as err:  # nosec B110
        log_warning(f".qml sidecar write skipped: {err}")


OUTPUT_DIR_SETTING = "AIEdit/output_dir"


def _documents_default_dir() -> str:
    """~/Documents/AI Edit via QStandardPaths so the OS picks the localized folder."""
    try:
        from qgis.PyQt.QtCore import QStandardPaths

        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
    except Exception:
        base = ""
    if not base:
        base = os.path.expanduser("~")
    return os.path.join(base, "AI Edit")


def get_output_dir() -> str:
    """1) QSettings override, 2) <project_dir>/ai_edit_outputs/, 3) ~/Documents/AI Edit/."""
    from qgis.core import QgsProject, QgsSettings

    settings = QgsSettings()
    override = (settings.value(OUTPUT_DIR_SETTING, "", type=str) or "").strip()
    if override:
        return override

    project = QgsProject.instance()
    project_path = project.absoluteFilePath()
    if project_path:
        return os.path.join(os.path.dirname(project_path), "ai_edit_outputs")

    return _documents_default_dir()


def extent_and_crs_from_job(job: dict) -> tuple[dict, str] | None:
    """Map a history job's stored location back to (extent_dict, crs_wkt) so a
    past output can be re-added as a georeferenced layer via write_geotiff.

    Prefers the native capture CRS + bbox (pixel-faithful), falls back to the
    WGS84 footprint. Returns None when the job carries no usable location
    (legacy rows) so the caller can disable "Add to map".

    Main-thread only: QgsCoordinateReferenceSystem reads the CRS database.
    """
    from qgis.core import QgsCoordinateReferenceSystem

    authid = (job.get("crs_authid") or "").strip()
    bbox = job.get("bbox")
    if authid and isinstance(bbox, dict) and all(k in bbox for k in ("xmin", "ymin", "xmax", "ymax")):
        crs = QgsCoordinateReferenceSystem(authid)
        if crs.isValid():
            return {
                "xmin": float(bbox["xmin"]),
                "ymin": float(bbox["ymin"]),
                "xmax": float(bbox["xmax"]),
                "ymax": float(bbox["ymax"]),
            }, crs.toWkt()

    wgs = job.get("bbox_wgs84")
    if isinstance(wgs, dict) and all(k in wgs for k in ("west", "south", "east", "north")):
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
        if crs.isValid():
            return {
                "xmin": float(wgs["west"]),
                "ymin": float(wgs["south"]),
                "xmax": float(wgs["east"]),
                "ymax": float(wgs["north"]),
            }, crs.toWkt()

    return None


def set_output_dir(path: str) -> None:
    from qgis.core import QgsSettings

    settings = QgsSettings()
    settings.setValue(OUTPUT_DIR_SETTING, (path or "").strip())
    try:
        settings.sync()
    except Exception:  # nosec B110
        pass


def _slugify(text: str) -> str:
    """ASCII-only slug. GDAL mishandles unicode in some Windows locales -> WRITE_ERROR."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "_", text)
    return text.strip("_")
