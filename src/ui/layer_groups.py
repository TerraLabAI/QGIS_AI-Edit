"""Shared layer-tree grouping helper.

All AI Edit outputs (generated rasters, Mark up annotations, Vectorize
polygons) live under a single group at the top of the layer tree so
users keep their existing layer organisation untouched.
"""
from __future__ import annotations

from qgis.core import QgsLayerTreeGroup, QgsProject

AI_EDIT_GROUP_NAME = "AI-Edit"
# Custom property stamped on the group we own. Lets us distinguish our
# group from a user-created or other-plugin "AI-Edit" group that happens
# to share the name, so we never absorb someone else's layers.
_OWNERSHIP_PROPERTY = "terralab/ai_edit_group"


def get_or_create_ai_edit_group() -> QgsLayerTreeGroup:
    """Return the AI-Edit group, creating it at the top of the tree if absent.

    Resolution order:
      1. The first root-level group that carries our ownership marker.
      2. The first root-level group named ``AI-Edit`` (custom properties
         on tree nodes do not always survive project save/reload in QGIS
         3.x, so we fall back to name matching and re-stamp the marker
         so future calls hit the fast path).
      3. Create a fresh group at the top of the tree.

    Without the name fallback, every reload of a saved project caused
    Vectorize and other writers to spawn a second AI-Edit group next to
    the first one.
    """
    root = QgsProject.instance().layerTreeRoot()
    # Pass 1: ownership marker (fast path on fresh-from-creation groups).
    for child in root.children():
        if not isinstance(child, QgsLayerTreeGroup):
            continue
        if child.customProperty(_OWNERSHIP_PROPERTY):
            return child
    # Pass 2: name fallback for groups that lost the marker across a
    # project save/reload. Re-stamp so the fast path kicks in next time.
    for child in root.children():
        if not isinstance(child, QgsLayerTreeGroup):
            continue
        if child.name() == AI_EDIT_GROUP_NAME:
            child.setCustomProperty(_OWNERSHIP_PROPERTY, True)
            return child
    # No matching group anywhere; create a fresh one at the top.
    group = root.insertGroup(0, AI_EDIT_GROUP_NAME)
    group.setCustomProperty(_OWNERSHIP_PROPERTY, True)
    return group
