"""Is the generation the user just paid for actually on screen?

New outputs go to the top of the AI-Edit group, but the group itself may sit
anywhere: ``find_ai_edit_group`` deliberately looks through the whole tree so a
user can file it inside a folder of their own. Park it under an opaque basemap
and every generation lands BEHIND the imagery it edited - the GeoTIFF is
written, the credit is spent, the canvas does not change, and the plugin used
to say nothing. The reports read as "I launch it and nothing happens".

This module answers only the question "what is covering it", conservatively: a
false negative costs the user nothing beyond today's silence, while a false
positive nags about a result they can plainly see. So it flags a layer only
when it is a raster, drawn above, checked visible, fully opaque, and covering
the whole zone.
"""
from __future__ import annotations

from qgis.core import (
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
)

from ..core import qt_compat as QtC
from ..core.logger import log_warning

# Below this the layer lets enough through that the result stays legible under
# it, so it is not worth a warning. Rasters are commonly dimmed to ~0.5.
_OPAQUE_THRESHOLD = 0.95


def layers_hiding_layer(layer, extent: QgsRectangle) -> list:
    """Visible opaque rasters drawn above ``layer`` that cover ``extent``.

    Empty when the result is on top, when nothing above it is opaque, or on any
    error: this drives a warning, and a warning that cannot be computed is one
    that must not fire.
    """
    try:
        return _layers_hiding_layer(layer, extent)
    except Exception as err:  # noqa: BLE001 - a check must never break a result
        log_warning(f"occlusion check skipped: {err}")
        return []


def _layers_hiding_layer(layer, extent: QgsRectangle) -> list:
    project = QgsProject.instance()
    root = project.layerTreeRoot()
    order = root.layerOrder()  # index 0 is the topmost, i.e. drawn last
    target_id = layer.id()
    above: list = []
    for candidate in order:
        if candidate is None:
            continue
        if candidate.id() == target_id:
            return above
        if _hides_zone(candidate, extent, root, project):
            above.append(candidate)
    # The layer is not in the tree order at all: nothing to say about it.
    return []


def _hides_zone(candidate, extent: QgsRectangle, root, project) -> bool:
    """Whether ``candidate`` paints opaque pixels over the whole of ``extent``."""
    if candidate.type() != QtC.RasterLayerType:
        # A vector layer above the result almost always leaves it readable
        # (points, outlines, labels), so it is never worth a warning.
        return False
    node = root.findLayer(candidate.id())
    if node is None or not node.isVisible():
        return False
    if not _is_opaque(candidate):
        return False
    return _covers(candidate, extent, project)


def _is_opaque(candidate) -> bool:
    """Full opacity and a normal blend mode, so nothing shows through."""
    renderer = candidate.renderer()
    if renderer is not None and renderer.opacity() < _OPAQUE_THRESHOLD:
        return False
    if QtC.CompositionModeSourceOver is None:
        # The enum could not be resolved on this Qt build; treat the layer as
        # opaque rather than skip the check, since the opacity test above is
        # already the discriminating one.
        return True
    return candidate.blendMode() == QtC.CompositionModeSourceOver


def _covers(candidate, extent: QgsRectangle, project) -> bool:
    """Whether the layer's footprint contains the whole zone, in project CRS."""
    footprint = candidate.extent()
    if footprint is None or footprint.isEmpty():
        return False
    layer_crs = candidate.crs()
    project_crs = project.crs()
    if layer_crs.isValid() and project_crs.isValid() and layer_crs != project_crs:
        transform = QgsCoordinateTransform(layer_crs, project_crs, project)
        footprint = transform.transformBoundingBox(footprint)
    return footprint.contains(extent)
