
















from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, unquote




from .render_set import _layer_id, expand_render_set


def build_input_render_set(input_layer, markup_layer=None) -> list:






    render = [] if input_layer is None else [input_layer]
    if markup_layer is not None and _layer_id(markup_layer) != _layer_id(input_layer):
        render.insert(0, markup_layer)
    return render


def layers_above_input(
    canvas_layers,
    input_layer,
    ai_edit_layer_ids=None,
    markup_layer=None,
) -> list:














    input_id = _layer_id(input_layer) if input_layer is not None else ""
    if not input_id:
        return []
    input_source = layer_source_key(input_layer)
    skip = {layer_id for layer_id in (ai_edit_layer_ids or ()) if layer_id}
    if markup_layer is not None:
        skip.add(_layer_id(markup_layer))
    above: list = []
    for layer in expand_render_set(canvas_layers):
        if layer is None:
            continue
        layer_id = _layer_id(layer)
        if layer_id == input_id:
            return above
        if not layer_id or layer_id in skip:
            continue



        if input_source and _is_raster(layer) and layer_source_key(layer) == input_source:
            continue
        above.append(layer)
    return []




_TILE_HOST_RE = re.compile(r"://(?:mt|khm|[abcd])\d?\.")


def _is_raster(layer) -> bool:
    try:
        from qgis.core import QgsRasterLayer
    except ImportError:
        return False
    return isinstance(layer, QgsRasterLayer)


def layer_source_key(layer) -> str:





    try:
        provider = layer.dataProvider()
        name = ((provider.name() if provider is not None else "") or "").lower()
        source = layer.source() or ""
    except (AttributeError, RuntimeError):
        return ""
    if not source:
        return ""
    if "url=" in source:
        params = parse_qs(source, keep_blank_values=True)
        url = unquote((params.get("url") or [""])[0]).strip().lower()
        if url:
            return f"{name}:" + _TILE_HOST_RE.sub("://", url)
    path = source.split("|", 1)[0].strip()
    if path and os.path.exists(path):
        return f"{name}:" + os.path.normcase(os.path.abspath(path))
    return f"{name}:{source.strip()}"
