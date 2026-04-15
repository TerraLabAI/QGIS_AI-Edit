from __future__ import annotations

import os
import re
import time

from osgeo import gdal, osr
from qgis.core import QgsProject, QgsRasterLayer

from ..core.logger import log_debug


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

        # Geotransform: map the received image pixels to the geographic extent.
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


def add_geotiff_to_project(
    geotiff_path: str, prompt: str = "", generation_number: int | None = None,
) -> QgsRasterLayer:
    """Add GeoTIFF as a raster layer in the 'AI Edit' group."""
    if prompt:
        slug = _slugify(prompt)[:55] or "ai_edit_result"
        if len(slug) > 20 and len(prompt) > 55:
            parts = slug.rsplit("_", 1)
            if len(parts) > 1 and len(parts[0]) > 20:
                slug = parts[0]
    else:
        slug = "ai_edit_result"

    if generation_number is not None:
        display_name = f"#{generation_number} {slug}"
    else:
        display_name = slug

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
