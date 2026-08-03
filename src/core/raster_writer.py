"""GeoTIFF write pipeline and output-directory resolution (no UI dependency).

Layer naming, styling and project insertion live in ``src/ui/raster_writer.py``,
which also re-exports every name here for backward compatibility.
"""
from __future__ import annotations

import os
import struct
import tempfile
import time

from osgeo import gdal, ogr, osr

from .i18n import tr
from .logger import log_debug, log_warning
from .slug import slugify as _slugify

# Professional GeoTIFF layout: tiled so pan/zoom only reads the visible
# blocks, DEFLATE because Vectorize matches exact pixel colors (lossy JPEG
# would smear the flat tints it relies on). Overviews are built after the
# pixel copy.
_GTIFF_CREATION_OPTIONS = [
    "COMPRESS=DEFLATE",
    "PREDICTOR=2",
    "TILED=YES",
    "BLOCKXSIZE=256",
    "BLOCKYSIZE=256",
    # DEFLATE on every core. Measured on a 4096x4096 RGBA write with overviews:
    # 4.9 s single-threaded, 1.7 s on 4 cores, 1.0 s on 16.
    "NUM_THREADS=ALL_CPUS",
    # A 4K RGBA output plus its overviews can pass the 4 GB classic-TIFF
    # ceiling, where Create() fails outright. IF_SAFER writes BigTIFF instead.
    "BIGTIFF=IF_SAFER",
]

# Formats GDAL reliably decodes across all platforms (esp. Windows OSGeo4W,
# which often ships without WebP/AVIF drivers).
_GDAL_SAFE_FORMATS = {"PNG", "JPEG", "TIFF", "GIF", "BMP"}

# Env-var values that betray a foreign package (another plugin's bundled
# rasterio/pyproj venv) having pointed PROJ at its own, version-mismatched
# proj.db. Production telemetry shows this breaking every CRS lookup
# in-process on Windows (proj_create_from_database errors), failing the
# GeoTIFF write of paid generations.
_FOREIGN_PROJ_MARKERS = ("rasterio", "pyproj", ".qgis_ai_segmentation", "venv")


def _restore_qgis_proj_paths(force: bool = False) -> None:
    """If PROJ_LIB/PROJ_DATA was hijacked by a foreign bundled GIS stack,
    point GDAL's PROJ back at QGIS's own database for all subsequent ops.
    force=True skips marker detection, for after a CRS op already failed
    (the hijack can happen via SetPROJSearchPaths with no env var trace)."""
    try:
        values = [os.environ.get(var, "") for var in ("PROJ_LIB", "PROJ_DATA")]
        hijacked = any(
            marker in value.lower() for value in values for marker in _FOREIGN_PROJ_MARKERS
        )
        if not hijacked and not force:
            return
        from qgis.core import QgsProjUtils

        paths = [p for p in QgsProjUtils.searchPaths() if p and os.path.isdir(p)]
        if paths and hasattr(osr, "SetPROJSearchPaths"):
            osr.SetPROJSearchPaths(paths)
            log_warning(
                "PROJ search paths restored to QGIS defaults "
                f"(foreign override detected: {[v[:80] for v in values if v]})"
            )
    except Exception as err:  # nosec B110 - best-effort self-heal.
        log_warning(f"PROJ path restore skipped: {err}")


def _safe_projection_wkt(crs_wkt: str) -> str | None:
    """Parse the capture CRS for embedding. A poisoned PROJ database makes
    ImportFromWkt raise "OGR Error: Corrupt data" on perfectly valid WKT
    (seen in production on Windows); retry once after forcing the QGIS PROJ
    paths back, and give up with None so a paid generation is never lost
    over CRS embedding alone."""
    if not crs_wkt:
        return None
    err = ""
    for attempt in (1, 2):
        try:
            srs = osr.SpatialReference()
            if srs.ImportFromWkt(crs_wkt) == 0:
                return srs.ExportToWkt()
            err = gdal.GetLastErrorMsg() or "import returned an error code"
        except RuntimeError as e:
            err = str(e)
        if attempt == 1:
            log_warning(f"CRS import failed ({err}); restoring PROJ paths and retrying")
            _restore_qgis_proj_paths(force=True)
    log_warning(f"CRS import still failing after PROJ restore ({err})")
    return None


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


def ascii_safe_dir(directory: str) -> str:
    """Return a directory path both GDAL (write) and the QGIS GDAL provider
    (read-back) accept on Windows.

    Accented Windows usernames put non-ASCII characters in the output path.
    GDAL writes the GeoTIFF without error, but the QGIS raster provider then
    loads it as an invalid layer ("Failed to create valid raster layer").
    Converting the directory to its 8.3 short name yields a pure-ASCII path
    both accept. No-op on non-Windows, on already-ASCII paths, or when
    conversion is unavailable.

    Precondition: ``directory`` already exists (``os.makedirs`` it first). A
    path that is not there cannot be resolved, so it comes back untouched.
    """
    if os.name != "nt" or directory.isascii():
        return directory

    if not os.path.isdir(directory):
        # GetShortPathNameW answers 0 for a missing path, and 0 again on a
        # volume with 8.3 names off. Reading the first as the second sends a
        # caller that has not made its output directory yet to the shared
        # Public folder, and the user's file lands where they never chose.
        log_warning("ascii_safe_dir ran before the directory existed; path unchanged")
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
    # Output filenames are unique per generation (slug + timestamp), so a
    # single shared folder cannot collide.
    public = os.environ.get("PUBLIC")
    if public and public.isascii():
        safe = os.path.join(public, "terralab_ai_edit")
        try:
            os.makedirs(safe, exist_ok=True)
            return safe
        except OSError:
            pass
    return directory


def _create_gtiff(driver, path: str, w: int, h: int, bands: int):
    """driver.Create normally returns None on failure, but raises RuntimeError
    when any plugin in the process has called gdal.UseExceptions(). Normalize
    both so the tempdir fallback always gets a chance. Returns (ds, error)."""
    try:
        ds = driver.Create(path, w, h, bands, gdal.GDT_Byte, options=_GTIFF_CREATION_OPTIONS)
    except RuntimeError as e:
        return None, str(e)
    if ds is None:
        return None, gdal.GetLastErrorMsg() or "driver returned no dataset"
    return ds, ""


def _rasterize_crop_alpha(
    dst_ds, alpha_band_index: int, polygon_wkt: str, geotransform: tuple, projection_wkt: str | None
) -> None:
    """Burn ``polygon_wkt`` into a 0/255 mask on ``dst_ds``'s exact pixel grid
    and write it as the alpha band (P3 reversible crop).

    ``polygon_wkt`` is already in the output raster's own CRS: the zone
    polygon is captured in the canvas CRS (context_metadata.apply_export_context),
    and the raster writer never reprojects between capture and output, so no
    transform is needed here. Raises on any failure so the caller can fall
    back to a fully opaque band rather than silently losing pixels.
    """
    w = dst_ds.RasterXSize
    h = dst_ds.RasterYSize
    mask_ds = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_Byte)
    mask_ds.SetGeoTransform(geotransform)

    srs = None
    if projection_wkt:
        candidate = osr.SpatialReference()
        if candidate.ImportFromWkt(projection_wkt) == 0:
            srs = candidate
            mask_ds.SetProjection(projection_wkt)

    ogr_ds = ogr.GetDriverByName("Memory").CreateDataSource("crop_mask")
    ogr_layer = ogr_ds.CreateLayer("zone", srs, ogr.wkbPolygon)
    geom = ogr.CreateGeometryFromWkt(polygon_wkt)
    if geom is None:
        raise RuntimeError("crop polygon WKT failed to parse")
    feature = ogr.Feature(ogr_layer.GetLayerDefn())
    feature.SetGeometry(geom)
    ogr_layer.CreateFeature(feature)

    mask_band = mask_ds.GetRasterBand(1)
    gdal.RasterizeLayer(mask_ds, [1], ogr_layer, burn_values=[255])
    raw = mask_band.ReadRaster(0, 0, w, h, w, h, gdal.GDT_Byte)

    alpha_band = dst_ds.GetRasterBand(alpha_band_index)
    alpha_band.WriteRaster(0, 0, w, h, raw, w, h, gdal.GDT_Byte)
    alpha_band.SetColorInterpretation(gdal.GCI_AlphaBand)

    mask_ds = None
    ogr_ds = None


def _write_opaque_alpha_band(dst_ds, alpha_band_index: int) -> None:
    """Fallback when rasterizing the crop polygon fails: a fully opaque alpha
    band, so the file still opens with every pixel visible instead of a
    broken/missing band."""
    w, h = dst_ds.RasterXSize, dst_ds.RasterYSize
    alpha_band = dst_ds.GetRasterBand(alpha_band_index)
    alpha_band.WriteRaster(0, 0, w, h, b"\xff" * (w * h), w, h, gdal.GDT_Byte)
    alpha_band.SetColorInterpretation(gdal.GCI_AlphaBand)


def read_crop_polygon_wkt(geotiff_path: str) -> str | None:
    """Read back the ``AI_EDIT_CROP_POLYGON_WKT`` tag written by the P3 alpha
    crop (output CRS). None on a file with no crop: old generations, or
    MCP/dev extents that never carried a polygon. The crop tags are written
    before the rasterize attempt, so a rasterization failure that fell back
    to a fully opaque alpha band still returns the WKT (the shape the user
    drew stays authoritative for vectorize clipping)."""
    try:
        ds = gdal.Open(geotiff_path)
        if ds is None:
            return None
        return ds.GetMetadataItem("AI_EDIT_CROP_POLYGON_WKT") or None
    except Exception:  # nosec B110 - crop detection is best-effort
        return None


def _unique_output_path(directory: str, base: str, ext: str = "tif") -> str:
    """First free <base>.<ext> in directory; _2, _3... on same-second collisions."""
    path = os.path.join(directory, f"{base}.{ext}")
    counter = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{base}_{counter}.{ext}")
        counter += 1
    return path


def replace_staged_file(staged: str, destination: str) -> None:
    """Move a fully-written staging file onto its destination, or delete it.

    Windows refuses to overwrite a file another process holds open: a .tif
    already loaded as a layer, a .png open in the Photos app. Without the
    cleanup every failed attempt leaves a stray ``.part`` next to the user's
    file, and without ``os.replace`` the destination would have to be unlinked
    first, destroying the old copy when the new one cannot be put in place.
    """
    from .errors import AIEditError, ErrorCode

    try:
        os.replace(staged, destination)
    except OSError as err:
        try:
            os.remove(staged)
        except OSError:  # nosec B110 - the staging file is disposable
            pass
        raise AIEditError(
            ErrorCode.WRITE_ERROR,
            tr(
                "Could not write {name}. It may be open in QGIS or in another "
                "program. Close it, or pick a different name, and try again."
            ).format(name=os.path.basename(destination)),
            cause=err,
        ) from err


_FALLBACK_EXT = {
    "PNG": "png",
    "JPEG": "jpg",
    "GIF": "gif",
    "BMP": "bmp",
    "TIFF": "tif",
    "WebP": "webp",
    "AVIF": "avif",
    "HEIF": "heif",
}


def _image_dimensions(data: bytes, fmt: str | None) -> tuple[int, int]:
    """Width/height parsed straight from the bytes, no GDAL: the rescue path
    cannot trust GDAL since GDAL may be what just failed. (0, 0) if unknown."""
    try:
        if fmt == "PNG" and len(data) >= 24 and data[12:16] == b"IHDR":
            w, h = struct.unpack(">II", data[16:24])
            return int(w), int(h)
        if fmt == "JPEG":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return int(w), int(h)
                i += 2 + seg_len
    except Exception:  # nosec B110 - dimensions are best-effort
        pass
    return 0, 0


def _output_file_base(prompt: str) -> str:
    slug = (_slugify(prompt)[:40] if prompt else "") or "ai_edit"
    return f"{slug}_{time.strftime('%Y%m%d_%H%M%S')}"


def _rescue_plain_image(
    image_data: bytes,
    img_format: str | None,
    extent_dict: dict,
    crs_wkt: str,
    file_base: str,
    width: int,
    height: int,
) -> str | None:
    """Last-resort save when the GeoTIFF pipeline fails: the paid pixels are
    already in memory, so write them untouched to tempdir with a PAM sidecar
    (.aux.xml) carrying the georeferencing. Pure file I/O, no GDAL dataset,
    so it survives a broken GDAL/PROJ stack. Returns None if even this fails.

    Dimensions are needed only to compute the geotransform for the sidecar.
    When they cannot be parsed (a format _image_dimensions does not size, e.g.
    WebP from a GDAL build without the WebP driver), still write the raw bytes
    without the sidecar rather than losing the paid generation: the file is a
    valid image and add_geotiff_to_project re-attaches the CRS from the capture
    WKT at load time."""
    ext = _FALLBACK_EXT.get(img_format or "")
    if ext is None:
        return None
    try:
        rescue_dir = tempfile.mkdtemp(prefix="terralab_ai_edit_")
        path = _unique_output_path(ascii_safe_dir(rescue_dir), file_base, ext)
        with open(path, "wb") as f:
            f.write(image_data)
        if width <= 0 or height <= 0:
            # No dimensions -> no geotransform. Skip the sidecar; the bytes are
            # saved and the layer still loads (un-georeferenced, CRS recovered
            # from the capture WKT). Better than re-raising and losing it.
            log_warning(
                "plain-image rescue saved without georeferencing "
                f"(dimensions unknown for {img_format})"
            )
            return path
        x_res = (extent_dict["xmax"] - extent_dict["xmin"]) / width
        y_res = (extent_dict["ymax"] - extent_dict["ymin"]) / height
        gt = ", ".join(
            f"{v:.16e}"
            for v in (extent_dict["xmin"], x_res, 0.0, extent_dict["ymax"], 0.0, -y_res)
        )
        # Minimal XML text escaping; avoids importing xml.sax (flagged by
        # security scanners) for three character replacements.
        wkt_xml = crs_wkt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        srs_xml = f"  <SRS>{wkt_xml}</SRS>\n" if crs_wkt else ""
        with open(path + ".aux.xml", "w", encoding="utf-8") as f:
            f.write(
                f"<PAMDataset>\n{srs_xml}  <GeoTransform>{gt}</GeoTransform>\n</PAMDataset>\n"
            )
        return path
    except Exception as e:  # noqa: BLE001 - last resort, never raise past here
        log_warning(f"plain-image rescue failed: {e}")
        return None


# Custom property on a result raster layer: absolute path of the
# georeferenced copy of the imagery the generation ran FROM. The swipe reads
# it to paint the true "before" side instead of whatever sits beneath.
BEFORE_PATH_PROPERTY = "ai_edit/before_path"


def before_file_base(result_path: str) -> str:
    """File base of the before GeoTIFF written next to ``result_path``."""
    return os.path.splitext(os.path.basename(result_path))[0] + "_before"


def write_geotiff(
    image_data: bytes,
    extent_dict: dict,
    crs_wkt: str,
    output_dir: str,
    prompt: str = "",
    ctx=None,
    file_base: str | None = None,
) -> str:
    """Write raw image bytes as a georeferenced GeoTIFF, falling back to the
    plain image plus a PAM sidecar when the whole GDAL pipeline fails. Once
    the pixels are in memory a paid generation must never be lost to a write
    error. Runs on a worker thread. ``file_base`` overrides the prompt-derived
    filename (the before GeoTIFF names itself after its result)."""
    try:
        return _write_geotiff_gdal(
            image_data, extent_dict, crs_wkt, output_dir, prompt=prompt, ctx=ctx,
            file_base=file_base,
        )
    except Exception as gtiff_err:
        img_format = _detect_image_format(image_data)
        width, height = _image_dimensions(image_data, img_format)
        rescued = _rescue_plain_image(
            image_data, img_format, extent_dict, crs_wkt,
            file_base or _output_file_base(prompt), width, height,
        )
        if rescued is None:
            raise
        log_warning(f"GeoTIFF write failed ({gtiff_err}); rescued plain image to {rescued}")
        try:
            from . import telemetry
            from . import telemetry_events as te
            from .log_scrub import scrub_user_paths

            scrubbed = scrub_user_paths(str(gtiff_err))
            telemetry.track(te.PLUGIN_ERROR, {
                "stage": "write",
                "error_code": "write_geotiff_rescued",
                "error_message": scrubbed[:200],
            })
        except Exception:  # nosec B110
            pass
        if ctx is not None:
            ctx.output_path = rescued
            ctx.output_rescued = True
        return rescued


def _write_geotiff_gdal(
    image_data: bytes,
    extent_dict: dict,
    crs_wkt: str,
    output_dir: str,
    prompt: str = "",
    ctx=None,
    file_base: str | None = None,
) -> str:
    """GDAL GeoTIFF pipeline (tiled, compressed, overviews, provenance tags)."""
    _restore_qgis_proj_paths()
    timestamp = int(time.time())
    file_base = file_base or _output_file_base(prompt)

    # Fall back to tempdir if user's output_dir is read-only so a hostile
    # folder doesn't lose the paid generation.
    try:
        os.makedirs(output_dir, exist_ok=True)
        resolved_dir = output_dir
    except OSError as e:
        log_warning(f"output_dir not writable ({e}); using tempdir fallback")
        resolved_dir = tempfile.mkdtemp(prefix="terralab_ai_edit_")

    # Reroute through an ASCII-safe path: a non-ASCII directory (accented
    # Windows username) writes fine via GDAL but loads back as an invalid
    # QGIS layer. The directory exists by now, so 8.3 short names resolve.
    resolved_dir = ascii_safe_dir(resolved_dir)
    primary_path = _unique_output_path(resolved_dir, file_base)
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
            else:
                log_warning("palette->RGB expansion failed; output may show incorrect colors")

        recv_w = src_ds.RasterXSize
        recv_h = src_ds.RasterYSize
        src_bands = src_ds.RasterCount
        bands = min(src_bands, 3)
        # Reversible polygon crop (P3): when the zone was drawn with the
        # polygon tool, ctx carries its WKT (canvas CRS, same as crs_wkt/the
        # output raster's own CRS below, so no reprojection is needed here).
        # An extra alpha band is appended and rasterized from it; a bbox-only
        # zone (ctx is None, or zone_polygon_wkt is None: history restore,
        # MCP/dev extents) writes RGB exactly as before, byte-identical.
        crop_polygon_wkt = getattr(ctx, "zone_polygon_wkt", None) if ctx is not None else None
        dst_bands = bands + 1 if crop_polygon_wkt else bands
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
        dst_ds, create_err = _create_gtiff(driver, output_path, recv_w, recv_h, dst_bands)
        if dst_ds is None:
            # Windows MAX_PATH / antivirus lock / network-share perm denied,
            # retry in tempdir.
            log_warning(
                f"GDAL Create failed at {primary_path} ({create_err}); retrying in tempdir"
            )
            fallback_dir = tempfile.mkdtemp(prefix="terralab_ai_edit_")
            output_path = _unique_output_path(ascii_safe_dir(fallback_dir), file_base)
            dst_ds, create_err = _create_gtiff(driver, output_path, recv_w, recv_h, dst_bands)
        if dst_ds is None:
            msg = tr("Failed to create GeoTIFF at {path}").format(path=output_path)
            raise RuntimeError(f"{msg} ({create_err})")

        x_res = ext_width / recv_w
        y_res = ext_height / recv_h
        geotransform = (xmin, x_res, 0, ymax, 0, -y_res)
        dst_ds.SetGeoTransform(geotransform)

        if ctx is not None:
            ctx.output_path = output_path
            ctx.geotransform = geotransform
            ctx.output_bands = dst_bands
            ctx.output_dimensions = (recv_w, recv_h)

        projection_wkt = _safe_projection_wkt(crs_wkt)
        if projection_wkt:
            dst_ds.SetProjection(projection_wkt)
        else:
            # Pixels and geotransform are intact; add_geotiff_to_project
            # re-attaches the CRS at load time from the capture WKT.
            log_warning("CRS embedding failed; writing GeoTIFF without projection")

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
            (getattr(ctx, "submitted_resolution", None) or "unknown") if ctx else "unknown",
        )
        dst_ds.SetMetadataItem(
            "AI_EDIT_MODEL",
            (getattr(ctx, "model_name", None) or "AI Edit") if ctx else "AI Edit",
        )
        # Provenance lives in the GeoTIFF itself (no sidecar files): request
        # ids for support, template + geometry context for external tools.
        if ctx is not None:
            for tag, value in (
                ("AI_EDIT_REQUEST_ID", getattr(ctx, "request_id", None)),
                ("AI_EDIT_PARENT_REQUEST_ID", getattr(ctx, "parent_request_id", None)),
                ("AI_EDIT_TEMPLATE_ID", getattr(ctx, "template_id", None)),
                ("AI_EDIT_TEMPLATE_NAME", getattr(ctx, "template_name", None)),
                ("AI_EDIT_ASPECT_RATIO", getattr(ctx, "submitted_aspect_ratio", None)),
                ("AI_EDIT_GROUND_RESOLUTION_M", getattr(ctx, "ground_resolution_m", None)),
            ):
                if value is not None:
                    dst_ds.SetMetadataItem(tag, str(value))
        # Standard tags so the file reads correctly outside the plugin.
        dst_ds.SetMetadataItem("TIFFTAG_SOFTWARE", "AI Edit by TerraLab")
        dst_ds.SetMetadataItem(
            "TIFFTAG_DATETIME", time.strftime("%Y:%m:%d %H:%M:%S", time.gmtime())
        )
        dst_ds.SetMetadataItem("TIFFTAG_IMAGEDESCRIPTION", prompt[:512])
        # Machine-readable AI provenance: greppable today, and the kind of
        # disclosure the EU AI Act (art. 50) expects from August 2026.
        dst_ds.SetMetadataItem("AI_GENERATED", "TRUE")
        dst_ds.SetMetadataItem(
            "AI_EDIT_DISCLAIMER",
            "Synthetic imagery generated by AI. Not survey data or ground truth.",
        )
        if crop_polygon_wkt:
            dst_ds.SetMetadataItem("AI_EDIT_CROP", "POLYGON")
            dst_ds.SetMetadataItem("AI_EDIT_CROP_POLYGON_WKT", crop_polygon_wkt)

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

        if crop_polygon_wkt:
            # Reversible polygon crop (P3): the alpha band appended above.
            # Never fails the write over cosmetic cropping; a rasterization
            # error falls back to a fully opaque band instead.
            try:
                _rasterize_crop_alpha(
                    dst_ds, bands + 1, crop_polygon_wkt, geotransform, projection_wkt
                )
            except Exception as err:  # noqa: BLE001 - crop is best-effort
                log_warning(f"crop alpha rasterization failed ({err}); using opaque band")
                _write_opaque_alpha_band(dst_ds, bands + 1)

        # Internal overviews so big outputs pan/zoom instantly everywhere the
        # file travels (QGIS, ArcGIS...). Best-effort: never fail the paid
        # generation over pyramids.
        try:
            levels = []
            factor = 2
            while max(recv_w, recv_h) / factor >= 256:
                levels.append(factor)
                factor *= 2
            if levels:
                gdal.SetConfigOption("COMPRESS_OVERVIEW", "DEFLATE")
                try:
                    dst_ds.BuildOverviews("AVERAGE", levels)
                finally:
                    gdal.SetConfigOption("COMPRESS_OVERVIEW", None)
        except Exception as err:  # noqa: BLE001 - cosmetic, file is already valid
            log_warning(f"overview build skipped: {err}")

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

    return output_path


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
