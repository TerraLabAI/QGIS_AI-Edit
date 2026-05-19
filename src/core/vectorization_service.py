"""Color-based raster vectorization using direct GDAL API.

Avoids QGIS Processing chaining (TEMPORARY_OUTPUT references can vanish
between gdal:* steps on macOS). Everything runs in-memory via GDAL MEM /
OGR Memory drivers; filtering + simplification happen in Python on
QgsGeometry objects.
"""
from __future__ import annotations

import os

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import (
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsRasterLayer,
    QgsVectorLayer,
)

from .logger import log_debug


def vectorize_by_color(
    raster_layer: QgsRasterLayer,
    target_rgb: tuple[int, int, int],
    tolerance: int = 40,
    sieve_threshold: int = 10,
    min_pixels: int = 50,
    simplify_factor: float = 1.5,
    layer_name: str | None = None,
    output_rgb: tuple[int, int, int] | None = None,
) -> QgsVectorLayer:
    """Extract pixels matching ``target_rgb`` (±tolerance per channel) as polygons.

    ``output_rgb`` controls the outline colour of the resulting layer.
    Defaults to a vivid green that contrasts with any AI-Edit class color.

    Returns a styled in-memory ``QgsVectorLayer`` not yet added to the project.
    """
    raster_path = raster_layer.source()
    if not raster_path or not os.path.exists(raster_path):
        raise RuntimeError("Raster layer has no on-disk source file")

    log_debug(f"Vectorize: src={raster_path} rgb={target_rgb} tol={tolerance}")

    src = gdal.Open(raster_path)
    if src is None:
        raise RuntimeError("Could not open raster")
    if src.RasterCount < 3:
        raise RuntimeError("Raster must have at least 3 bands (RGB)")

    width, height = src.RasterXSize, src.RasterYSize
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    if not proj:
        raise RuntimeError("Raster has no CRS")

    r = src.GetRasterBand(1).ReadAsArray()
    g = src.GetRasterBand(2).ReadAsArray()
    b = src.GetRasterBand(3).ReadAsArray()
    src = None

    tr, tg, tb = target_rgb
    mask = (
        (np.abs(r.astype(np.int16) - tr) <= tolerance)
        & (np.abs(g.astype(np.int16) - tg) <= tolerance)  # noqa: W503
        & (np.abs(b.astype(np.int16) - tb) <= tolerance)  # noqa: W503
    ).astype(np.uint8)
    if int(mask.sum()) == 0:
        raise RuntimeError("No pixels matched the selected color")

    mem_raster_driver = gdal.GetDriverByName("MEM")
    mask_ds = mem_raster_driver.Create("", width, height, 1, gdal.GDT_Byte)
    mask_ds.SetGeoTransform(gt)
    mask_ds.SetProjection(proj)
    mask_band = mask_ds.GetRasterBand(1)
    mask_band.WriteArray(mask)
    mask_band.FlushCache()

    if sieve_threshold > 0:
        # In-place noise removal - drops connected components smaller than threshold.
        gdal.SieveFilter(
            srcBand=mask_band,
            maskBand=None,
            dstBand=mask_band,
            threshold=int(sieve_threshold),
            connectedness=8,
        )

    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(proj)

    ogr_driver = ogr.GetDriverByName("Memory")
    ogr_ds = ogr_driver.CreateDataSource("vec")
    ogr_layer = ogr_ds.CreateLayer("polys", spatial_ref, ogr.wkbPolygon)
    ogr_layer.CreateField(ogr.FieldDefn("value", ogr.OFTInteger))

    gdal.Polygonize(mask_band, None, ogr_layer, 0, ["8CONNECTED=8"])

    pixel_area = abs(gt[1] * gt[5])
    min_area = pixel_area * float(min_pixels)
    simplify_tol = (pixel_area ** 0.5) * simplify_factor

    name = layer_name or "Vector"
    # Build the memory layer with a CRS-agnostic URI, then set the real CRS
    # via setCrs(): a stale EPSG:4326 fallback when authid() is empty
    # (custom / user-defined CRS) would silently corrupt spatial alignment.
    mem_layer = QgsVectorLayer(
        "Polygon?field=value:integer",
        name,
        "memory",
    )
    raster_crs = raster_layer.crs()
    if raster_crs.isValid():
        mem_layer.setCrs(raster_crs)
    mem_provider = mem_layer.dataProvider()

    feats: list[QgsFeature] = []
    ogr_layer.ResetReading()
    for ogr_feat in ogr_layer:
        if ogr_feat.GetField("value") != 1:
            continue
        geom_ref = ogr_feat.GetGeometryRef()
        if geom_ref is None:
            continue
        geom = QgsGeometry.fromWkt(geom_ref.ExportToWkt())
        if geom.isEmpty() or geom.area() < min_area:
            continue
        if simplify_tol > 0:
            simplified = geom.simplify(simplify_tol)
            if not simplified.isEmpty():
                geom = simplified
        feat = QgsFeature()
        feat.setGeometry(geom)
        feat.setAttributes([1])
        feats.append(feat)

    mask_ds = None
    ogr_ds = None

    if not feats:
        raise RuntimeError(
            "No polygons remained after filtering "
            "(try a wider tolerance or smaller min size)"
        )

    mem_provider.addFeatures(feats)
    mem_layer.updateExtents()
    style_rgb = output_rgb if output_rgb is not None else DEFAULT_OUTPUT_RGB
    _apply_style(mem_layer, style_rgb)
    log_debug(f"Vectorize done: {len(feats)} polygons (min_area={min_area:.2f} map_units²)")
    return mem_layer


# Vivid green that reads clearly over any AI-Edit class color (the source
# raster will most often be red/orange/blue). Matches QGIS's "Standard"
# style-library green tone with a thick outline so the polygon perimeter
# survives at any zoom level.
DEFAULT_OUTPUT_RGB: tuple[int, int, int] = (0, 200, 83)


def _apply_style(layer: QgsVectorLayer, output_rgb: tuple[int, int, int]) -> None:
    """Outline-only style: 2 px green stroke, no fill so the raster shows through."""
    r, g, b = output_rgb
    symbol = QgsFillSymbol.createSimple(
        {
            "color": "0,0,0,0",  # transparent fill
            "outline_color": f"{r},{g},{b},255",
            "outline_width": "2",
            "outline_width_unit": "Pixel",
            "outline_style": "solid",
        }
    )
    layer.renderer().setSymbol(symbol)
    layer.triggerRepaint()
