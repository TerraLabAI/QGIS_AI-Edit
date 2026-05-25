"""Shared layer-tree grouping helper.

All AI Edit outputs (generated rasters, Mark up annotations, Vectorize
polygons) live under a single group at the top of the layer tree so
users keep their existing layer organisation untouched.

Generations land as flat children of the AI-Edit group. A per-raster
sub-group is created lazily the first time a raster is vectorized: at
that point the raster is moved into a new sub-group named after it and
its vector layer joins it. Subsequent vectorizations of the same raster
land in the same sub-group. Generations that are never vectorized stay
flat, so the tree only grows nesting when bundling is actually useful.
"""
from __future__ import annotations

from typing import Callable

from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer, QgsMapLayer, QgsProject

AI_EDIT_GROUP_NAME = "AI-Edit"
# Custom property stamped on the group we own. Lets us distinguish our
# group from a user-created or other-plugin "AI-Edit" group that happens
# to share the name, so we never absorb someone else's layers.
_OWNERSHIP_PROPERTY = "terralab/ai_edit_group"
# Per-generation sub-group marker + slot for the source raster layer id
# so vector layers know which sub-group to join even after a project reload.
_GENERATION_SUBGROUP_PROPERTY = "terralab/ai_edit_generation"
_GENERATION_SOURCE_LAYER_PROPERTY = "terralab/ai_edit_source_layer_id"
# Custom property stamped on the shared Mark up annotation layer. The
# layer is unique per project and lives at the bottom of the AI-Edit
# group so new generations stack above it without pushing it around.
MARKUP_LAYER_PROPERTY = "ai_edit_markup"


def _walk_groups(node):
    """Yield every QgsLayerTreeGroup descendant of ``node`` (depth-first)."""
    for child in node.children():
        if isinstance(child, QgsLayerTreeGroup):
            yield child
            yield from _walk_groups(child)


def get_or_create_ai_edit_group() -> QgsLayerTreeGroup:
    """Return the AI-Edit group, creating it at the top of the tree if absent.

    Resolution order:
      1. Any group anywhere in the tree that carries our ownership marker
         (lets the user drag the AI-Edit group into a folder of their own
         layout without us spawning a duplicate next time).
      2. Any group anywhere in the tree named ``AI-Edit`` (custom
         properties on tree nodes do not always survive project save/reload
         in QGIS 3.x, so we fall back to name matching and re-stamp the
         marker so future calls hit the fast path).
      3. Create a fresh group at the top of the tree.

    The recursive walk is the fix for the audit finding "user drags AI-Edit
    into a sub-folder, next reload creates a second group at root".
    """
    root = QgsProject.instance().layerTreeRoot()
    for child in _walk_groups(root):
        if child.customProperty(_OWNERSHIP_PROPERTY):
            return child
    for child in _walk_groups(root):
        if child.name() == AI_EDIT_GROUP_NAME:
            child.setCustomProperty(_OWNERSHIP_PROPERTY, True)
            return child
    group = root.insertGroup(0, AI_EDIT_GROUP_NAME)
    group.setCustomProperty(_OWNERSHIP_PROPERTY, True)
    # Creating the group at index 0 pushes the Mark up layer down; bounce it
    # back to the very top so its annotations stay visible above the group.
    pin_markup_to_top()
    return group


def find_generation_subgroup_for_layer(layer_id: str) -> QgsLayerTreeGroup | None:
    """Return the per-raster sub-group that contains ``layer_id``.

    Walks the AI-Edit group's sub-groups and looks for a layer node whose
    id matches. Used by Vectorize to drop vector outputs into the same
    sub-group as their source raster instead of a flat group.
    """
    parent = get_or_create_ai_edit_group()
    for child in parent.children():
        if not isinstance(child, QgsLayerTreeGroup):
            continue
        for sub in child.children():
            if isinstance(sub, QgsLayerTreeLayer) and sub.layerId() == layer_id:
                return child
    return None


def promote_layer_to_own_subgroup(layer_id: str) -> QgsLayerTreeGroup | None:
    """Move a flat AI-Edit child raster into a fresh sub-group named after it.

    Called the first time a raster is vectorized so the raster + its
    vector(s) get bundled together. If the raster is already inside a
    sub-group, returns that existing sub-group. Returns None if the
    layer can't be located under the AI-Edit group.
    """
    parent = get_or_create_ai_edit_group()
    project = QgsProject.instance()

    layer = project.mapLayer(layer_id)
    if layer is None:
        return None

    source_node = None
    for child in parent.children():
        if isinstance(child, QgsLayerTreeLayer) and child.layerId() == layer_id:
            source_node = child
            break
    if source_node is None:
        return find_generation_subgroup_for_layer(layer_id)

    base_name = layer.name()
    existing = {
        child.name()
        for child in parent.children()
        if isinstance(child, QgsLayerTreeGroup)
    }
    final_name = base_name
    counter = 2
    while final_name in existing:
        final_name = f"{base_name} ({counter})"
        counter += 1

    subgroup = parent.insertGroup(0, final_name)
    subgroup.setCustomProperty(_GENERATION_SUBGROUP_PROPERTY, True)
    subgroup.setExpanded(True)

    # Attach the clone BEFORE removing the original. QgsLayerTreeRegistryBridge
    # auto-removes a layer from the project when its last tree node disappears,
    # so the layer must keep at least one tree reference at all times during
    # the move. Detaching then reattaching deletes the raster.
    clone = source_node.clone()
    subgroup.addChildNode(clone)
    parent.removeChildNode(source_node)
    return subgroup


def add_layer_to_ai_edit_top(layer: QgsMapLayer) -> QgsLayerTreeLayer:
    """Insert ``layer`` at the top of the AI-Edit group. The Mark up
    annotation layer lives at the tree root above the group (not inside it),
    so new outputs go straight to index 0 without disturbing it.
    """
    group = get_or_create_ai_edit_group()
    return group.insertLayer(0, layer)


def add_subgroup_to_ai_edit_top(name: str) -> QgsLayerTreeGroup:
    """Create a sub-group at the top of the AI-Edit group."""
    group = get_or_create_ai_edit_group()
    return group.insertGroup(0, name)


def pin_markup_to_top() -> None:
    """Keep the Mark up layer at the very top of the tree, above the AI-Edit
    group, so its annotations always render over everything else.

    Idempotent. The Mark up layer lives as a direct child of the tree root
    (not inside the AI-Edit group) and is found by ``MARKUP_LAYER_PROPERTY``
    so it survives rename + reload.
    """
    root = QgsProject.instance().layerTreeRoot()
    children = list(root.children())
    markup_node = None
    for child in children:
        if isinstance(child, QgsLayerTreeLayer):
            layer = child.layer()
            if layer is not None and layer.customProperty(MARKUP_LAYER_PROPERTY):
                markup_node = child
                break
    if markup_node is None or children.index(markup_node) == 0:
        return
    # Clone-then-remove keeps at least one tree reference alive at all
    # times, otherwise QgsLayerTreeRegistryBridge would wipe the layer
    # from the project (same gotcha as promote_layer_to_own_subgroup).
    clone = markup_node.clone()
    root.insertChildNode(0, clone)
    root.removeChildNode(markup_node)


def most_recent_ai_edit_output(
    predicate: Callable[[QgsMapLayer], bool] | None = None,
) -> QgsMapLayer | None:
    """Return the topmost layer under the AI-Edit group passing ``predicate``.

    New generations are inserted at index 0 so the topmost match is the
    most recent. Walks per-generation sub-groups one level deep so vector
    outputs nested under their source raster are reachable too.
    """
    root = QgsProject.instance().layerTreeRoot()
    for child in root.children():
        if not isinstance(child, QgsLayerTreeGroup):
            continue
        is_ai_edit_group = (
            child.customProperty(_OWNERSHIP_PROPERTY)
            or child.name() == AI_EDIT_GROUP_NAME  # noqa: W503
        )
        if not is_ai_edit_group:
            continue
        for sub in child.children():
            if isinstance(sub, QgsLayerTreeLayer):
                layer = sub.layer()
                if layer is not None and (predicate is None or predicate(layer)):
                    return layer
            elif isinstance(sub, QgsLayerTreeGroup):
                for leaf in sub.children():
                    if isinstance(leaf, QgsLayerTreeLayer):
                        layer = leaf.layer()
                        if layer is not None and (predicate is None or predicate(layer)):
                            return layer
        break
    return None


def pick_default_layer(
    predicate: Callable[[QgsMapLayer], bool],
) -> QgsMapLayer | None:
    """Cascade picker shared by tool panels for their "From" combo.

    Priority: 1) the most recent AI-Edit output that passes ``predicate``;
    2) the user's currently active layer in the QGIS Layers panel if it
    passes; 3) None (caller falls back to its own logic).
    """
    most_recent = most_recent_ai_edit_output(predicate)
    if most_recent is not None:
        return most_recent
    try:
        from qgis.utils import iface as _iface
        active = _iface.activeLayer() if _iface is not None else None
    except Exception:
        active = None
    if active is not None:
        try:
            if predicate(active):
                return active
        except Exception:  # nosec B110
            pass
    return None
