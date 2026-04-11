from __future__ import annotations

import os
import re
import time

from osgeo import gdal, osr
from qgis.core import QgsProject, QgsRasterLayer

from ..core.logger import log


def write_geotiff(
    image_data: bytes,
    extent_dict: dict,
    crs_wkt: str,
    output_dir: str,
    prompt: str = "",
    ctx=None,
) -> str:
    """Write raw image bytes as a georeferenced GeoTIFF.

    Uses only GDAL (no QgsRectangle or QgsCoordinateReferenceSystem)
    so it can safely run on a worker thread.

    Args:
        image_data: Raw PNG/image bytes (already downloaded)
        extent_dict: Dict with xmin, ymin, xmax, ymax in map coordinates
        crs_wkt: CRS as WKT string
        output_dir: Directory to save the GeoTIFF
        prompt: Original prompt (used in filename)

    Returns:
        Path to the created GeoTIFF file
    """
    os.makedirs(output_dir, exist_ok=True)

    # Generate filename
    timestamp = int(time.time())
    slug = _slugify(prompt)[:40] if prompt else "generated"
    filename = f"{timestamp}_{slug}.tif"
    output_path = os.path.join(output_dir, filename)

    xmin = extent_dict["xmin"]
    ymin = extent_dict["ymin"]
    xmax = extent_dict["xmax"]
    ymax = extent_dict["ymax"]

    # Write to temp PNG first, then convert to GeoTIFF with GDAL
    temp_png = os.path.join(output_dir, f"_temp_{timestamp}.png")
    try:
        with open(temp_png, "wb") as f:
            f.write(image_data)

        # Open with GDAL
        src_ds = gdal.Open(temp_png)
        if src_ds is None:
            raise RuntimeError("Failed to open downloaded image with GDAL")

        width = src_ds.RasterXSize
        height = src_ds.RasterYSize
        src_bands = src_ds.RasterCount
        # Always write RGB (3 bands) — drop alpha to avoid transparency gaps
        bands = min(src_bands, 3)
        log(f"GeoTIFF: {width}x{height}px, {bands} bands")

        # Map the full image to the original geographic extent.
        # The user's selection is the spatial truth — any tiny ratio mismatch
        # from the server results in slightly non-square pixels, which is
        # invisible and standard in GIS rasters.
        ext_width = xmax - xmin
        ext_height = ymax - ymin
        log(f"GeoTIFF extent: {ext_width:.2f}x{ext_height:.2f} map units")

        if ctx is not None:
            ctx.crop_offsets = (0, 0, width, height)

        # Create GeoTIFF with full image dimensions
        driver = gdal.GetDriverByName("GTiff")
        dst_ds = driver.Create(output_path, width, height, bands, gdal.GDT_Byte)
        if dst_ds is None:
            raise RuntimeError(f"Failed to create GeoTIFF at {output_path}")

        # Set geotransform: (x_origin, x_pixel_size, 0, y_origin, 0, -y_pixel_size)
        x_res = ext_width / width
        y_res = ext_height / height
        geotransform = (
            xmin,
            x_res,
            0,
            ymax,
            0,
            -y_res,
        )
        dst_ds.SetGeoTransform(geotransform)

        if ctx is not None:
            ctx.output_path = output_path
            ctx.geotransform = geotransform
            ctx.output_bands = bands
            ctx.output_dimensions = (width, height)
            ctx.received_image_width = width
            ctx.received_image_height = height
            ctx.received_size_bytes = len(image_data)

        # Set CRS directly from WKT (no QGIS objects needed)
        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs_wkt)
        dst_ds.SetProjection(srs.ExportToWkt())

        # Write custom metadata
        dst_ds.SetMetadataItem("AI_EDIT_PROMPT", prompt)
        dst_ds.SetMetadataItem(
            "AI_EDIT_TIMESTAMP", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )
        dst_ds.SetMetadataItem("AI_EDIT_CRS", crs_wkt[:200])
        dst_ds.SetMetadataItem(
            "AI_EDIT_EXTENT", f"{xmin:.6f},{ymin:.6f},{xmax:.6f},{ymax:.6f}"
        )
        dst_ds.SetMetadataItem(
            "AI_EDIT_RESOLUTION", ctx.submitted_resolution if ctx else "unknown"
        )
        dst_ds.SetMetadataItem("AI_EDIT_MODEL", "AI Edit")

        # Copy all bands (full image, no crop)
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
        # Try to break at underscore boundary
        if len(display_name) > 20 and len(prompt) > 60:
            parts = display_name.rsplit("_", 1)
            if len(parts) > 1 and len(parts[0]) > 20:
                display_name = parts[0]
    else:
        display_name = "ai_edit_result"

    # Handle duplicates
    existing_names = [lyr.name() for lyr in QgsProject.instance().mapLayers().values()]
    if display_name in existing_names:
        counter = 2
        while f"{display_name}_{counter}" in existing_names:
            counter += 1
        display_name = f"{display_name}_{counter}"

    layer = QgsRasterLayer(geotiff_path, display_name)
    if not layer.isValid():
        raise RuntimeError(f"Failed to create valid raster layer from {geotiff_path}")

    # Add to project without adding to layer tree root
    QgsProject.instance().addMapLayer(layer, False)

    # Add to AI Edit group (create if needed)
    root = QgsProject.instance().layerTreeRoot()
    group = root.findGroup("AI Edit")
    if group is None:
        group = root.insertGroup(0, "AI Edit")
    node = group.insertLayer(0, layer)

    # Collapse the layer node to hide band details
    if node is not None:
        node.setExpanded(False)

    return layer


def get_output_dir() -> str:
    """Get the output directory for generated images.

    Uses project-relative folder if a project is open, falls back to QGIS profile dir.
    """
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
