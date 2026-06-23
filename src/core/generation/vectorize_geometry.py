
from __future__ import annotations

from qgis.core import Qgis, QgsDistanceArea, QgsFeature, QgsGeometry

from .. import qt_compat as QtC
from ..logger import log_debug
from ..raster_writer import read_crop_polygon_wkt


def repair_polygon_geometry(geom: QgsGeometry) -> QgsGeometry:







    method = getattr(getattr(Qgis, "MakeValidMethod", None), "Structure", None)
    if method is not None:
        try:
            fixed = geom.makeValid(method)
            if fixed is not None and not fixed.isEmpty():
                return fixed
        except Exception as err:  # noqa: BLE001
            log_debug(f"Vectorize: structure repair unavailable ({err}), using linework")
    return geom.makeValid()


def _make_measurer(raster_crs, transform_context, ellipsoid: str) -> QgsDistanceArea:


    measurer = QgsDistanceArea()
    if raster_crs is not None and raster_crs.isValid():
        measurer.setSourceCrs(raster_crs, transform_context)


    if not ellipsoid or ellipsoid.upper() == "NONE":
        ellipsoid = "EPSG:7030"
    measurer.setEllipsoid(ellipsoid)
    return measurer


def _clip_feats_to_crop(
    feats: list,
    raster_path: str,
    raster_crs,
    transform_context,
    ellipsoid: str,
) -> list:










    crop_wkt = read_crop_polygon_wkt(raster_path)
    if not crop_wkt:
        return feats
    crop_geom = QgsGeometry.fromWkt(crop_wkt)
    if crop_geom is None or crop_geom.isEmpty():
        return feats
    if not crop_geom.isGeosValid():
        fixed = crop_geom.makeValid()
        if fixed is not None and not fixed.isEmpty():
            crop_geom = fixed

    measurer = _make_measurer(raster_crs, transform_context, ellipsoid)
    clipped: list[QgsFeature] = []
    next_fid = 1
    for feat in feats:
        geom = feat.geometry().intersection(crop_geom)
        if geom is None or geom.isEmpty():
            continue
        if not geom.isGeosValid():
            fixed = repair_polygon_geometry(geom)
            if fixed is not None and not fixed.isEmpty():
                geom = fixed
        parts = geom.asGeometryCollection() if geom.isMultipart() else [geom]
        attrs = feat.attributes()
        for part in parts:
            if part.isEmpty() or part.type() != QtC.PolygonGeometry:
                continue
            if part.area() <= 0:
                continue
            new_feat = QgsFeature()
            new_feat.setGeometry(part)
            new_attrs = list(attrs)
            new_attrs[0] = next_fid
            new_attrs[3] = float(measurer.measureArea(part))
            new_feat.setAttributes(new_attrs)
            clipped.append(new_feat)
            next_fid += 1
    return clipped
