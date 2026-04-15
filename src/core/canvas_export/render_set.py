










from __future__ import annotations



try:
    from qgis.core import QgsGroupLayer
except ImportError:
    QgsGroupLayer = None


def is_group_proxy(layer) -> bool:

    return QgsGroupLayer is not None and isinstance(layer, QgsGroupLayer)


def expand_render_set(layers) -> list:








    out: list = []
    for layer in layers or []:
        out.extend(_flatten_layer(layer, set()))
    return out


def drop_layers(layers, exclude_ids) -> list:








    skip = set(exclude_ids or ())
    if not skip:
        return list(layers or [])
    out: list = []
    for layer in layers or []:
        if layer is None:
            continue
        if not is_group_proxy(layer):
            if _layer_id(layer) not in skip:
                out.append(layer)
            continue
        members = _flatten_layer(layer, set())
        kept = [m for m in members if _layer_id(m) not in skip]
        if len(kept) == len(members):
            out.append(layer)
        else:
            out.extend(kept)
    return out


def _layer_id(layer) -> str:

    try:
        return layer.id() or ""
    except (RuntimeError, AttributeError):
        return ""


def _flatten_layer(layer, seen: set[int]) -> list:

    if layer is None:
        return []
    if not is_group_proxy(layer):
        return [layer]
    key = id(layer)
    if key in seen:
        return []
    seen.add(key)
    out: list = []
    try:
        children = layer.childLayers() or []
    except (RuntimeError, AttributeError):
        return []
    for child in children:
        out.extend(_flatten_layer(child, seen))
    return out
