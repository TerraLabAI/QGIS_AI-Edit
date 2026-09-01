














from __future__ import annotations

from qgis.core import (
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
)

from ..core import qt_compat as QtC
from ..core.logger import log_warning



_OPAQUE_THRESHOLD = 0.95


def layers_hiding_layer(layer, extent: QgsRectangle) -> list:






    try:
        return _layers_hiding_layer(layer, extent)
    except Exception as err:  # noqa: BLE001
        log_warning(f"occlusion check skipped: {err}")
        return []


def _layers_hiding_layer(layer, extent: QgsRectangle) -> list:
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    order = root.layerOrder()
    target_id = layer.id()
    above: list = []
    for candidate in order:
        if candidate is None:
            continue
        if candidate.id() == target_id:
            return above
        if _hides_zone(candidate, extent, root, project):
            above.append(candidate)

    return []


def _hides_zone(candidate, extent: QgsRectangle, root, project) -> bool:

    if candidate.type() != QtC.RasterLayerType:


        return False
    node = root.findLayer(candidate.id())
    if node is None or not node.isVisible():
        return False
    if not _is_opaque(candidate):
        return False
    return _covers(candidate, extent, project)


def _is_opaque(candidate) -> bool:

    renderer = candidate.renderer()
    if renderer is not None and renderer.opacity() < _OPAQUE_THRESHOLD:
        return False
    if QtC.CompositionModeSourceOver is None:



        return True
    return candidate.blendMode() == QtC.CompositionModeSourceOver


def _covers(candidate, extent: QgsRectangle, project) -> bool:

    footprint = candidate.extent()
    if footprint is None or footprint.isEmpty():
        return False
    layer_crs = candidate.crs()
    project_crs = project.crs()
    if layer_crs.isValid() and project_crs.isValid() and layer_crs != project_crs:
        transform = QgsCoordinateTransform(layer_crs, project_crs, project)
        footprint = transform.transformBoundingBox(footprint)
    return footprint.contains(extent)
