












from __future__ import annotations

from typing import Callable

from qgis.core import QgsLayerTreeGroup, QgsLayerTreeLayer, QgsMapLayer, QgsProject

AI_EDIT_GROUP_NAME = "AI-Edit"



_OWNERSHIP_PROPERTY = "terralab/ai_edit_group"

_GENERATION_SUBGROUP_PROPERTY = "terralab/ai_edit_generation"



MARKUP_LAYER_PROPERTY = "ai_edit_markup"


def _walk_groups(node):

    for child in node.children():
        if isinstance(child, QgsLayerTreeGroup):
            yield child
            yield from _walk_groups(child)


def find_ai_edit_group() -> QgsLayerTreeGroup | None:








    root = QgsProject.instance().layerTreeRoot()
    for child in _walk_groups(root):
        if child.customProperty(_OWNERSHIP_PROPERTY):
            return child
    for child in _walk_groups(root):
        if child.name() == AI_EDIT_GROUP_NAME:
            return child
    return None


def get_or_create_ai_edit_group() -> QgsLayerTreeGroup:















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


    pin_markup_to_top()
    return group


def collect_ai_edit_layer_ids() -> set[str]:









    group = find_ai_edit_group()
    if group is None:
        return set()

    ids: set[str] = set()

    def _collect(node) -> None:
        for child in node.children():
            if isinstance(child, QgsLayerTreeGroup):
                _collect(child)
            elif isinstance(child, QgsLayerTreeLayer):
                layer_id = child.layerId()
                if layer_id:
                    ids.add(layer_id)

    _collect(group)
    return ids


def set_ai_edit_layers_checked(checked: bool, except_ids: set[str] | None = None) -> None:









    group = find_ai_edit_group()
    if group is None:
        return
    skip = except_ids or set()

    def _apply(node) -> None:
        for child in node.children():
            if isinstance(child, QgsLayerTreeGroup):
                _apply(child)
            elif isinstance(child, QgsLayerTreeLayer):
                if child.layerId() and child.layerId() not in skip:
                    child.setItemVisibilityChecked(checked)

    _apply(group)


def find_generation_subgroup_for_layer(layer_id: str) -> QgsLayerTreeGroup | None:






    parent = get_or_create_ai_edit_group()
    for child in parent.children():
        if not isinstance(child, QgsLayerTreeGroup):
            continue
        for sub in child.children():
            if isinstance(sub, QgsLayerTreeLayer) and sub.layerId() == layer_id:
                return child
    return None


def promote_layer_to_own_subgroup(layer_id: str) -> QgsLayerTreeGroup | None:







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





    clone = source_node.clone()
    subgroup.addChildNode(clone)
    parent.removeChildNode(source_node)
    return subgroup


def add_layer_to_ai_edit_top(layer: QgsMapLayer) -> QgsLayerTreeLayer:





    group = get_or_create_ai_edit_group()
    return group.insertLayer(0, layer)


def bring_ai_edit_group_to_front() -> bool:











    root = QgsProject.instance().layerTreeRoot()
    group = find_ai_edit_group()
    if group is None:
        return False
    parent = group.parent()
    if parent is root and root.children() and root.children()[0] is group:
        return True



    clone = group.clone()
    root.insertChildNode(0, clone)
    if parent is not None:
        parent.removeChildNode(group)
    pin_markup_to_top()
    return True


def pin_markup_to_top() -> None:







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



    clone = markup_node.clone()
    root.insertChildNode(0, clone)
    root.removeChildNode(markup_node)


def most_recent_ai_edit_output(
    predicate: Callable[[QgsMapLayer], bool] | None = None,
) -> QgsMapLayer | None:






    group = find_ai_edit_group()
    if group is None:
        return None
    for sub in group.children():
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
    return None


def pick_default_layer(
    predicate: Callable[[QgsMapLayer], bool],
) -> QgsMapLayer | None:






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


def drop_from_snapping(layer: QgsMapLayer) -> None:








    try:
        project = QgsProject.instance()
        cfg = project.snappingConfig()
        cfg.removeLayers([layer])
        project.setSnappingConfig(cfg)
    except Exception:  # nosec B110
        pass
