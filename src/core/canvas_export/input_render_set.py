
















from __future__ import annotations




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
        if layer_id and layer_id not in skip:
            above.append(layer)
    return []
