"""Which layers the model sees: the picked layer as the input, what sits above
it as references.

An edit starts from ONE layer the user picks in the dock's "Image to edit"
field, like AI Segmentation's picker (Yvann, 2026-09-18): the visible canvas
was the input for a day, and a stack of basemap, polygons and labels sent as
one flat image left the model editing the labels along with the ground.

- The input render set is that layer alone, or the version picked in the strip
  (v1, v2), with the Mark up layer on top as user guidance (``prepare_export``
  lifts it into an overlay and keeps it out of the clean base).
- Every visible layer drawn ABOVE the picked one (polygons, labels, another
  raster) goes to the model as a reference instead, so what the user drew
  still guides the edit without being painted into it.

The module reads layer ids and nothing else, so it loads and tests headless.
"""
from __future__ import annotations

# _layer_id is private to the package, not to this module: render_set.py is the
# sibling that owns stale-layer tolerance, and duplicating it here would be a
# second answer to one question.
from .render_set import _layer_id, expand_render_set


def build_input_render_set(input_layer, markup_layer=None) -> list:
    """The layers to render for the input image, top first.

    ``input_layer`` is the layer picked in the dock, or the version picked in
    the strip. ``markup_layer`` rides on top of it. Nothing else on the canvas
    reaches the input.
    """
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
    """The visible layers drawn above ``input_layer``, top first.

    ``canvas_layers`` is ``QgsMapSettings.layers()``, what the canvas actually
    draws, top first. Group proxies are expanded so a layer inside a "render as
    a group" group counts on its own. The AI Edit outputs
    (``ui/layer_groups.collect_ai_edit_layer_ids``) and the Mark up layer are
    left out: a previous result is not a reference, and the drawing already
    travels as the overlay.

    A picked layer the canvas does not draw has nothing above it: the user
    unticked it, so no stacking order says what covers it.

    The list is fresh; ``canvas_layers`` is never mutated.
    """
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
