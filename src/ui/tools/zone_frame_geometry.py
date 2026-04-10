

from __future__ import annotations

from qgis.core import QgsGeometry, QgsRectangle

from ...core import qt_compat as QtC
from .selection_map_tool import supported_ratios


def _repair_polygon(geom: QgsGeometry) -> QgsGeometry | None:









    if geom is None or geom.isEmpty():
        return None
    fixed = geom if geom.isGeosValid() else geom.makeValid()
    if fixed is None or fixed.isEmpty():
        fixed = geom
    parts = fixed.asGeometryCollection() if fixed.isMultipart() else [fixed]
    polygons = [p for p in parts if not p.isEmpty() and p.type() == QtC.PolygonGeometry]
    if not polygons:
        return None
    combined = QgsGeometry.collectGeometry(polygons)
    if combined is None or combined.isEmpty() or combined.area() <= 0:
        return None
    return combined


def expand_bbox_to_ratio_and_min_size(
    bbox: QgsRectangle, mupp: float, min_size_px: float
) -> QgsRectangle:










    w0 = bbox.width()
    h0 = bbox.height()
    if w0 <= 0 or h0 <= 0:
        return QgsRectangle(bbox)
    cx = bbox.center().x()
    cy = bbox.center().y()
    current_ratio = w0 / h0
    best = min(supported_ratios(), key=lambda r: abs(r[0] / r[1] - current_ratio))
    target_ratio = best[0] / best[1]
    if current_ratio <= target_ratio:

        new_w = h0 * target_ratio
        new_h = h0
    else:

        new_w = w0
        new_h = w0 / target_ratio
    if mupp and mupp > 0:
        min_map = min_size_px * mupp
        scale = max(
            1.0,
            (min_map / new_w) if new_w > 0 else 1.0,
            (min_map / new_h) if new_h > 0 else 1.0,
        )
        new_w *= scale
        new_h *= scale
    half_w = new_w / 2.0
    half_h = new_h / 2.0
    return QgsRectangle(cx - half_w, cy - half_h, cx + half_w, cy + half_h)
