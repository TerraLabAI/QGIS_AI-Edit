







from __future__ import annotations

import base64
import math
import os
import zlib

from .config_store import get_export_dial



_FLAT_SAMPLE_SIDE = 64
_PALETTE_SAMPLE_PX = 512 * 512
_PALETTE_QUANT = 32


def pack(raw: bytes) -> str:
    return base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def result_sample(image_bytes: bytes) -> tuple[int, int, bytes] | None:


    if not image_bytes:
        return None
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QImage

    img = QImage.fromData(image_bytes)
    if img.isNull():
        return None
    side = get_export_dial("vectorize.detect.sample_side", _FLAT_SAMPLE_SIDE)
    img = img.scaled(
        side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
    ).convertToFormat(QImage.Format.Format_RGB32)
    width, height = img.width(), img.height()
    if width * height == 0:
        return None
    raw = bytearray()
    for y in range(height):
        for x in range(width):
            rgb = img.pixel(x, y)
            raw += bytes(((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF))
    return width, height, bytes(raw)


def _read_dims(width: int, height: int, budget: int) -> tuple[int, int]:


    if width <= 0 or height <= 0 or budget <= 0:
        return (0, 0)
    if width * height <= budget:
        return (width, height)
    bw = max(1, min(width, int(math.sqrt(budget * width / float(height)))))
    bh = max(1, min(height, budget // bw))
    if bw * bh > budget:
        bh = max(1, budget // bw)
    return (bw, bh)


def raster_histogram(raster_path: str) -> tuple[int, list[list[int]]] | None:




    try:
        import numpy as np
        from osgeo import gdal
    except ImportError:
        return None
    if not raster_path or not os.path.exists(raster_path):
        return None
    try:
        ds = gdal.Open(raster_path)
    except RuntimeError:
        return None
    if ds is None or ds.RasterCount < 3:
        return None
    budget = get_export_dial("vectorize.palette.sample_px", _PALETTE_SAMPLE_PX)
    bw, bh = _read_dims(ds.RasterXSize, ds.RasterYSize, budget)
    if not bw or not bh:
        return None
    bands = [ds.GetRasterBand(i).ReadAsArray(buf_xsize=bw, buf_ysize=bh) for i in (1, 2, 3)]
    valid = None
    first = ds.GetRasterBand(1)
    if first.GetMaskFlags() != gdal.GMF_ALL_VALID:
        valid = first.GetMaskBand().ReadAsArray(buf_xsize=bw, buf_ysize=bh)
        if valid is None:
            return None
    del ds
    if any(b is None for b in bands):
        return None
    rgb = np.stack(bands, axis=-1).reshape(-1, 3).astype(np.int32)
    if valid is not None:
        rgb = rgb[valid.reshape(-1) > 0]
    if not rgb.size:
        return None
    step = get_export_dial("vectorize.palette.quant", _PALETTE_QUANT)
    q = np.clip((rgb // step) * step + step // 2, 0, 255)
    keys = (q[:, 0] << 16) | (q[:, 1] << 8) | q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    return int(keys.size), [[int(k), int(c)] for k, c in zip(vals, counts)]
