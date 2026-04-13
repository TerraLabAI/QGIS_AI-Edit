from __future__ import annotations

import base64
import io
import os
import re
import time

import numpy as np
from osgeo import gdal, osr
from qgis.core import QgsProject, QgsRasterLayer

from ..core.logger import log_debug


def _detect_pixel_offset(sent_b64: str, recv_ds, recv_w: int, recv_h: int) -> tuple[float, float]:
    """Detect subpixel offset between sent and received images using phase correlation.

    Returns (dy, dx) in pixels. Positive = received content shifted right/down.
    """
    from PIL import Image

    # Decode sent image to grayscale numpy array
    sent_bytes = base64.b64decode(sent_b64)
    sent_img = Image.open(io.BytesIO(sent_bytes)).convert("L")
    sent_w, sent_h = sent_img.size
    sent_arr = np.array(sent_img, dtype=np.float32) / 255.0

    # Read received as grayscale: average RGB bands
    recv_arr = np.zeros((recv_h, recv_w), dtype=np.float32)
    n_bands = min(recv_ds.RasterCount, 3)
    for b in range(1, n_bands + 1):
        recv_arr += recv_ds.GetRasterBand(b).ReadAsArray().astype(np.float32)
    recv_arr = recv_arr / (n_bands * 255.0)

    # Resize received to sent dimensions for comparison
    recv_img = Image.fromarray((recv_arr * 255).astype(np.uint8))
    recv_resized = np.array(
        recv_img.resize((sent_w, sent_h), Image.LANCZOS), dtype=np.float32
    ) / 255.0

    # Apply Hanning window to reduce edge artifacts in FFT
    win_y = np.hanning(sent_h).reshape(-1, 1)
    win_x = np.hanning(sent_w).reshape(1, -1)
    window = win_y * win_x

    a = sent_arr * window
    b_arr = recv_resized * window

    # Phase correlation via FFT
    fa = np.fft.rfft2(a)
    fb = np.fft.rfft2(b_arr)
    cross = fa * np.conj(fb)
    cross_norm = cross / (np.abs(cross) + 1e-10)
    corr = np.fft.irfft2(cross_norm, s=(sent_h, sent_w))

    # Find integer peak
    peak_y, peak_x = np.unravel_index(np.argmax(corr), corr.shape)

    # Convert to signed offset
    dy = peak_y if peak_y < sent_h // 2 else peak_y - sent_h
    dx = peak_x if peak_x < sent_w // 2 else peak_x - sent_w

    # Subpixel refinement via parabola fitting
    dy_sub, dx_sub = float(dy), float(dx)
    if 1 <= peak_y < sent_h - 1 and 1 <= peak_x < sent_w - 1:
        # Vertical parabola
        c_top = corr[peak_y - 1, peak_x]
        c_mid = corr[peak_y, peak_x]
        c_bot = corr[peak_y + 1, peak_x]
        denom_y = c_top - 2 * c_mid + c_bot
        if abs(denom_y) > 1e-12:
            dy_sub = dy - (c_bot - c_top) / (2 * denom_y)

        # Horizontal parabola
        c_left = corr[peak_y, peak_x - 1]
        c_right = corr[peak_y, peak_x + 1]
        denom_x = c_left - 2 * c_mid + c_right
        if abs(denom_x) > 1e-12:
            dx_sub = dx - (c_right - c_left) / (2 * denom_x)

    # Scale offset from sent dimensions to received dimensions
    scale_x = recv_w / sent_w
    scale_y = recv_h / sent_h

    return dy_sub * scale_y, dx_sub * scale_x


def write_geotiff(
    image_data: bytes,
    extent_dict: dict,
    crs_wkt: str,
    output_dir: str,
    prompt: str = "",
    ctx=None,
    sent_image_b64: str | None = None,
) -> str:
    """Write raw image bytes as a georeferenced GeoTIFF.

    Uses only GDAL (no QgsRectangle or QgsCoordinateReferenceSystem)
    so it can safely run on a worker thread.

    Args:
        image_data: Raw image bytes (already downloaded from model)
        extent_dict: Dict with xmin, ymin, xmax, ymax in map coordinates
        crs_wkt: CRS as WKT string
        output_dir: Directory to save the GeoTIFF
        prompt: Original prompt (used in filename)
        sent_image_b64: Base64 of the image sent to the model (for alignment)

    Returns:
        Path to the created GeoTIFF file
    """
    os.makedirs(output_dir, exist_ok=True)

    timestamp = int(time.time())
    slug = _slugify(prompt)[:40] if prompt else "generated"
    filename = f"{timestamp}_{slug}.tif"
    output_path = os.path.join(output_dir, filename)

    xmin = extent_dict["xmin"]
    ymin = extent_dict["ymin"]
    xmax = extent_dict["xmax"]
    ymax = extent_dict["ymax"]

    temp_png = os.path.join(output_dir, f"_temp_{timestamp}.png")
    try:
        with open(temp_png, "wb") as f:
            f.write(image_data)

        src_ds = gdal.Open(temp_png)
        if src_ds is None:
            raise RuntimeError("Failed to open downloaded image with GDAL")

        recv_w = src_ds.RasterXSize
        recv_h = src_ds.RasterYSize
        src_bands = src_ds.RasterCount
        bands = min(src_bands, 3)
        log_debug(f"GeoTIFF: received {recv_w}x{recv_h}px, {bands} bands")

        ext_width = xmax - xmin
        ext_height = ymax - ymin
        log_debug(f"GeoTIFF extent: {ext_width:.2f}x{ext_height:.2f} map units")

        # Detect spatial offset between sent and received images
        offset_dy_px, offset_dx_px = 0.0, 0.0
        if sent_image_b64:
            try:
                offset_dy_px, offset_dx_px = _detect_pixel_offset(
                    sent_image_b64, src_ds, recv_w, recv_h
                )
                log_debug(
                    f"GeoTIFF: phase correlation offset: "
                    f"dx={offset_dx_px:.2f}px, dy={offset_dy_px:.2f}px"
                )
            except Exception as e:
                log_debug(f"GeoTIFF: phase correlation failed ({e}), using no offset")

        if ctx is not None:
            ctx.received_image_width = recv_w
            ctx.received_image_height = recv_h
            ctx.received_size_bytes = len(image_data)
            ctx.crop_offsets = (0, 0, recv_w, recv_h)

        # Keep full received resolution
        driver = gdal.GetDriverByName("GTiff")
        dst_ds = driver.Create(
            output_path, recv_w, recv_h, bands, gdal.GDT_Byte
        )
        if dst_ds is None:
            raise RuntimeError(f"Failed to create GeoTIFF at {output_path}")

        # Geotransform with offset correction.
        # The offset is in received-image pixels. Convert to map units
        # and shift the origin so the content aligns with the source.
        x_res = ext_width / recv_w
        y_res = ext_height / recv_h
        corrected_xmin = xmin - offset_dx_px * x_res
        corrected_ymax = ymax + offset_dy_px * y_res
        geotransform = (corrected_xmin, x_res, 0, corrected_ymax, 0, -y_res)
        dst_ds.SetGeoTransform(geotransform)

        if offset_dx_px != 0.0 or offset_dy_px != 0.0:
            log_debug(
                f"GeoTIFF: origin corrected by "
                f"dx={offset_dx_px * x_res:.4f}, "
                f"dy={offset_dy_px * y_res:.4f} map units"
            )

        if ctx is not None:
            ctx.output_path = output_path
            ctx.geotransform = geotransform
            ctx.output_bands = bands
            ctx.output_dimensions = (recv_w, recv_h)

        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs_wkt)
        dst_ds.SetProjection(srs.ExportToWkt())

        dst_ds.SetMetadataItem("AI_EDIT_PROMPT", prompt)
        dst_ds.SetMetadataItem(
            "AI_EDIT_TIMESTAMP",
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
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

        for i in range(1, bands + 1):
            band_data = src_ds.GetRasterBand(i).ReadAsArray()
            dst_ds.GetRasterBand(i).WriteArray(band_data)

        dst_ds.FlushCache()
        dst_ds = None
        src_ds = None
    finally:
        if os.path.exists(temp_png):
            os.remove(temp_png)

    return output_path


def add_geotiff_to_project(geotiff_path: str, prompt: str = "") -> QgsRasterLayer:
    """Add GeoTIFF as a raster layer in the 'AI Edit' group."""
    if prompt:
        display_name = _slugify(prompt)[:60] or "ai_edit_result"
        if len(display_name) > 20 and len(prompt) > 60:
            parts = display_name.rsplit("_", 1)
            if len(parts) > 1 and len(parts[0]) > 20:
                display_name = parts[0]
    else:
        display_name = "ai_edit_result"

    existing_names = [lyr.name() for lyr in QgsProject.instance().mapLayers().values()]
    if display_name in existing_names:
        counter = 2
        while f"{display_name}_{counter}" in existing_names:
            counter += 1
        display_name = f"{display_name}_{counter}"

    layer = QgsRasterLayer(geotiff_path, display_name)
    if not layer.isValid():
        raise RuntimeError(f"Failed to create valid raster layer from {geotiff_path}")

    QgsProject.instance().addMapLayer(layer, False)

    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup("AI Edit")
    if group is None:
        group = root.insertGroup(0, "AI Edit")
    node = group.insertLayer(0, layer)

    if node is not None:
        node.setExpanded(False)

    return layer


def get_output_dir() -> str:
    """Get the output directory for generated images."""
    from qgis.core import QgsApplication, QgsProject

    project = QgsProject.instance()
    project_path = project.absoluteFilePath()
    if project_path:
        return os.path.join(os.path.dirname(project_path), "ai_edit_outputs")
    return os.path.join(QgsApplication.qgisSettingsDirPath(), "ai_edit", "generated")


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "_", text)
    return text.strip("_")
