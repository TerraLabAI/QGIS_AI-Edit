"""Layer predicates used to pick vectorizable AI Edit outputs."""
from __future__ import annotations

from qgis.core import QgsProject, QgsRasterLayer

from ...layer_groups import AI_EDIT_GROUP_NAME


def _is_ai_edit_output(layer) -> bool:
    """Return True when ``layer`` lives under the AI-Edit layer-tree group.

    The AI-Edit group is the canonical home for every plugin-generated
    raster, so group membership is a reliable marker for "produced by AI
    Edit" without stamping per-layer properties.
    """
    if not isinstance(layer, QgsRasterLayer):
        return False
    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(layer.id())
    if node is None:
        return False
    parent = node.parent()
    while parent is not None and parent is not root:
        if parent.name() == AI_EDIT_GROUP_NAME:
            return True
        parent = parent.parent()
    return False


def _is_visible_ai_edit_output(layer) -> bool:
    """``_is_ai_edit_output`` plus tree visibility.

    The combo only lists visible layers, so the default pick must also be
    visible — otherwise ``setLayer()`` silently fails and the combo lands
    on whatever fallback it auto-picks.
    """
    if not _is_ai_edit_output(layer):
        return False
    node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    return node is not None and node.isVisible()
