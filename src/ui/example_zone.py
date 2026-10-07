







from __future__ import annotations

import urllib.parse

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsPointXY,
    QgsProject,
    QgsProviderRegistry,
    QgsRasterLayer,
    QgsRectangle,
    QgsUnitTypes,
)

from ..core.extent_transform import transform_extent
from ..core.logger import log_warning
from ..core.qt_compat import _resolve


EXAMPLE_EMPTY_PROJECT_CRS = "EPSG:3857"

_EXAMPLE_ZOOM_MARGIN = 1.15


def example_xyz_url(basemap) -> str:

    if not isinstance(basemap, dict):
        return ""
    url = basemap.get("xyz")
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url or '"' in url:
        return ""
    return url


def _layer_xyz_url(layer) -> str:
    try:
        if layer.providerType() != "wms":
            return ""
        parts = QgsProviderRegistry.instance().decodeUri("wms", layer.source())
        return str(parts.get("url") or "") if str(parts.get("type") or "") == "xyz" else ""
    except (AttributeError, RuntimeError):
        return ""


def find_or_add_example_basemap(basemap):




    url = example_xyz_url(basemap)
    if not url:
        return None
    project = QgsProject.instance()
    for layer in project.mapLayers().values():
        if _layer_xyz_url(layer) == url:
            _show_layer(project, layer)
            return layer
    zmax = basemap.get("zmax")

    uri = "type=xyz&url=" + urllib.parse.quote(url, safe=":/?{}") + "&zmin=0"
    if isinstance(zmax, int) and not isinstance(zmax, bool):
        uri += f"&zmax={zmax}"
    name = str(basemap.get("name") or "Basemap")
    layer = QgsRasterLayer(uri, name, "wms")
    if not layer.isValid():
        log_warning("example basemap did not load")
        return None
    project.addMapLayer(layer, False)
    project.layerTreeRoot().insertLayer(-1, layer)
    return layer


def _show_layer(project, layer) -> None:

    node = project.layerTreeRoot().findLayer(layer.id())
    while node is not None:
        try:
            node.setItemVisibilityChecked(True)
            node = node.parent()
        except (AttributeError, RuntimeError):
            return


def _ground_size_m(west, south, east, north) -> tuple[float, float]:


    da = QgsDistanceArea()
    da.setSourceCrs(QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance().transformContext())
    da.setEllipsoid("WGS84")
    mid_lat = (south + north) / 2.0
    mid_lon = (west + east) / 2.0
    width = da.measureLine(QgsPointXY(west, mid_lat), QgsPointXY(east, mid_lat))
    height = da.measureLine(QgsPointXY(mid_lon, south), QgsPointXY(mid_lon, north))
    return width, height


def _map_units_per_ground_m(xform, lon, lat, step_lon, step_lat) -> tuple[float, float]:






    da = QgsDistanceArea()
    da.setSourceCrs(QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance().transformContext())
    da.setEllipsoid("WGS84")
    scales = []
    for a, b in (((lon - step_lon, lat), (lon + step_lon, lat)),
                 ((lon, lat - step_lat), (lon, lat + step_lat))):
        pa, pb = QgsPointXY(*a), QgsPointXY(*b)
        ground = da.measureLine(pa, pb)
        ma, mb = xform.transform(pa), xform.transform(pb)
        mapped = ((mb.x() - ma.x()) ** 2 + (mb.y() - ma.y()) ** 2) ** 0.5
        scales.append(mapped / ground if ground > 0 else 0.0)
    return scales[0], scales[1]


def example_zone_rectangle(bbox_4326, dest_crs) -> QgsRectangle | None:










    try:
        west, south, east, north = (float(v) for v in bbox_4326)
    except (TypeError, ValueError):
        return None
    if not (west < east and south < north):
        return None
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    if dest_crs is None or not dest_crs.isValid() or dest_crs.authid() == EXAMPLE_EMPTY_PROJECT_CRS:
        return transform_extent(QgsRectangle(west, south, east, north), wgs84, dest_crs)
    try:
        xform = QgsCoordinateTransform(wgs84, dest_crs, QgsProject.instance())
        centre = xform.transform(QgsPointXY((west + east) / 2.0, (south + north) / 2.0))
    except Exception:  # noqa: BLE001
        return None
    if dest_crs.isGeographic():
        half_w, half_h = (east - west) / 2.0, (north - south) / 2.0
    else:
        width_m, height_m = _ground_size_m(west, south, east, north)
        try:
            per_m_x, per_m_y = _map_units_per_ground_m(
                xform, (west + east) / 2.0, (south + north) / 2.0, (east - west) / 2.0, (north - south) / 2.0)
        except Exception:  # noqa: BLE001
            per_m_x = per_m_y = 0.0
        if not (per_m_x > 0 and per_m_y > 0):
            meters = _resolve(Qgis, "DistanceUnit", "Meters") if hasattr(Qgis, "DistanceUnit") \
                else _resolve(QgsUnitTypes, None, "DistanceMeters")
            per_m_x = per_m_y = QgsUnitTypes.fromUnitToUnitFactor(meters, dest_crs.mapUnits())
        half_w, half_h = width_m * per_m_x / 2.0, height_m * per_m_y / 2.0
    if not (half_w > 0 and half_h > 0):
        return None
    return QgsRectangle(centre.x() - half_w, centre.y() - half_h,
                        centre.x() + half_w, centre.y() + half_h)


def prepare_example_map(example: dict, canvas):



    project = QgsProject.instance()
    if not project.mapLayers():
        crs = QgsCoordinateReferenceSystem(EXAMPLE_EMPTY_PROJECT_CRS)
        project.setCrs(crs)
        canvas.setDestinationCrs(crs)
    layer = find_or_add_example_basemap(example.get("basemap"))
    zone = example.get("zone") if isinstance(example.get("zone"), dict) else {}
    rect = example_zone_rectangle(zone.get("bbox_4326"), canvas.mapSettings().destinationCrs())
    if rect is None or rect.isEmpty():
        return layer, None
    view = QgsRectangle(rect)
    view.scale(_EXAMPLE_ZOOM_MARGIN)
    canvas.setExtent(view)
    canvas.refresh()
    return layer, rect
