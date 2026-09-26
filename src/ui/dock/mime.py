from __future__ import annotations

import os

from qgis.core import QgsMimeDataUtils, QgsProject, QgsRasterLayer, QgsVectorLayer
from qgis.PyQt.QtCore import QFileInfo

from ...core.logger import log_warning

_IMAGE_DROP_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_GEODATA_DROP_EXTS = {
    ".tif", ".tiff", ".asc", ".img", ".vrt", ".dem", ".pdf",
    ".shp", ".gpkg", ".geojson", ".kml", ".kmz",
}
_URI_MIME = "application/x-vnd.qgis.qgis.uri"
_LAYERTREE_MIME = "application/qgis.layertreemodeldata"


def _file_paths_from_mime(mime) -> list[str]:


    if not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        if path.lower().endswith(".lnk"):


            path = QFileInfo(path).symLinkTarget() or path
        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_DROP_EXTS or ext in _GEODATA_DROP_EXTS:
            out.append(path)
    return out


def _mime_has_droppable(mime) -> bool:

    if _file_paths_from_mime(mime):
        return True
    return mime.hasFormat(_URI_MIME) or mime.hasFormat(_LAYERTREE_MIME)


def _layers_from_mime(mime) -> list:


    layers: list = []
    seen: set = set()




    if mime.hasFormat(_URI_MIME):
        try:
            for uri in QgsMimeDataUtils.decodeUriList(mime):
                lid = getattr(uri, "layerId", "") or ""
                if lid:
                    lyr = QgsProject.instance().mapLayer(lid)
                    if lyr is not None:
                        if id(lyr) not in seen:
                            layers.append(lyr)
                            seen.add(id(lyr))
                        continue
                provider = getattr(uri, "providerKey", "") or "ogr"
                name = getattr(uri, "name", "") or "ref"
                if getattr(uri, "layerType", "") == "raster":
                    lyr = QgsRasterLayer(uri.uri, name, provider)
                else:
                    lyr = QgsVectorLayer(uri.uri, name, provider)
                if lyr.isValid() and id(lyr) not in seen:
                    layers.append(lyr)
                    seen.add(id(lyr))
        except Exception as err:  # nosec B110
            log_warning(f"URI-list layer decode failed: {err}")

    if layers:
        return layers


    if mime.hasFormat(_LAYERTREE_MIME):
        try:
            from qgis.PyQt.QtXml import QDomDocument
            doc = QDomDocument()
            doc.setContent(bytes(mime.data(_LAYERTREE_MIME)))
            nodes = doc.elementsByTagName("layer-tree-layer")
            for i in range(nodes.count()):
                lid = nodes.at(i).toElement().attribute("id", "")
                if not lid:
                    continue
                lyr = QgsProject.instance().mapLayer(lid)
                if lyr is not None and id(lyr) not in seen:
                    layers.append(lyr)
                    seen.add(id(lyr))
        except Exception as err:  # nosec B110
            log_warning(f"Layer-tree MIME decode failed: {err}")

    return layers
