"""Flattening a canvas render set past QGIS "render as a group" proxies.

A layer tree group with "Render as a group" ticked (QGIS >= 3.24) reaches the
map renderer as ONE ``QgsGroupLayer``: its member layers are absent from
``QgsMapSettings.layers()`` and no id in that list matches theirs. Every check
the plugin makes against the render set - "will the Mark up layer draw?", "what
is the finest native resolution over this zone?", "drop the AI results from the
export" - silently reads False for a layer nested in such a group unless the
proxies are expanded first. A grouped raster then looks, to the plugin, exactly
like a layer that was deleted.
"""
from __future__ import annotations

# Optional: QgsGroupLayer landed in QGIS 3.24 and the plugin still runs on 3.22,
# where no group can be a render proxy and expansion is a no-op by construction.
try:
    from qgis.core import QgsGroupLayer
except ImportError:
    QgsGroupLayer = None


def is_group_proxy(layer) -> bool:
    """Whether ``layer`` is a "render as a group" stand-in rather than real data."""
    return QgsGroupLayer is not None and isinstance(layer, QgsGroupLayer)


def expand_render_set(layers) -> list:
    """``layers`` with every group proxy replaced, in place, by its members.

    Depth-first, so a group nested in another group flattens too. Draw order is
    preserved - members take the proxy's slot - and the input list is never
    mutated. Use this for READING the render set (ids, resolutions); to filter
    it for an actual render, use ``drop_layers``, which keeps a group's own
    compositing wherever it can.
    """
    out: list = []
    for layer in layers or []:
        out.extend(_flatten_layer(layer, set()))
    return out


def drop_layers(layers, exclude_ids) -> list:
    """``layers`` minus ``exclude_ids``, reaching inside group proxies.

    A proxy holding nothing excluded is kept WHOLE, so the group's own opacity
    and blend mode survive the common case. One holding only excluded layers is
    dropped. Only a group holding some of each is replaced by its surviving
    members: that trades the group's compositing for the guarantee that an
    excluded layer never reaches the model, which is the point of excluding it.
    """
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
    """``layer.id()``, or "" for a layer whose C++ side is already gone."""
    try:
        return layer.id() or ""
    except (RuntimeError, AttributeError):
        return ""


def _flatten_layer(layer, seen: set[int]) -> list:
    """One layer, expanded. ``seen`` breaks a group that contains itself."""
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
