"""Shared plumbing behind the AI Edit public API.

``mcp_api`` and every sibling ``mcp_api_*`` module import from here, so the
lookup rules and the never-raises contract are written once. Nothing in this
module is part of the public surface: a caller uses ``mcp_api`` instead.
"""
from __future__ import annotations

import difflib
import functools
import os
from collections.abc import Sequence

from qgis.core import QgsProject

# QGIS registers a plugin under the name of the folder it is installed in. The
# released folder is "AI_Edit"; a checkout installed under its repository
# folder registers under that name instead, so this module's own folder is
# tried first and the key self-adapts to any install.
_PLUGIN_FOLDER = os.path.basename(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
AI_EDIT_KEYS = [_PLUGIN_FOLDER, "AI_Edit", "QGIS_AI-Edit"]


def _find_plugin():
    """Return the live AI Edit plugin instance, or None when it is not loaded."""
    import qgis.utils
    for key in AI_EDIT_KEYS:
        plugin = qgis.utils.plugins.get(key)
        if plugin is not None:
            return plugin
    return None


def _never_raises(func):
    """Turn any escaping exception into the dict the contract promises.

    It also holds the second half of that promise: a failure always says
    something. A method that comes back with a blank ``_error``, and an
    exception whose text is empty, both leave a caller with a failure and no
    reason for it, so each is given a plain sentence naming what failed.
    """
    @functools.wraps(func)
    def _wrapped(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
        except Exception as err:  # noqa: BLE001 - the contract is: never raise.
            detail = str(err).strip() or f"{type(err).__name__} (no message)"
            return {"_error": f"{func.__name__} failed: {detail}"}
        if isinstance(result, dict) and "_error" in result:
            reported = result["_error"]
            if not str("" if reported is None else reported).strip():
                result["_error"] = f"{func.__name__} failed and gave no reason."
        return result
    return _wrapped


def not_found_error(
    kind: str,
    given: object,
    available: Sequence[object] | None = None,
    means: str = "",
    valid_range: tuple[int, int] | None = None,
) -> dict:
    """The one answer every lookup that misses gives back.

    ``kind`` names what was looked for, in the singular: "project layer",
    "preset family", "session". ``given`` is what the caller passed.
    ``available`` is every value that would have worked, so the answer can
    point at the nearest one instead of only refusing. ``means`` is one short
    line saying what the argument is for, worth passing whenever a name could
    be read as the name of something to create rather than something to find.
    ``valid_range`` replaces the name list for an index, where the answers that
    would work are a span of whole numbers rather than a set of names.

    Returns ``{"_error": ...}``, plus ``_suggestions`` when the near matches
    are worth trying. Three states, because they need three different moves:
    a close name to retry, a short list to choose from, or nothing there at all.
    """
    shown = str(given)
    if valid_range is not None:
        low, high = valid_range
        message = (
            f"{kind} {shown} is out of range. Valid values run {low} to {high}."
            if high >= low
            else f"There is no {kind} yet."
        )
        return {"_error": f"{message} {means}".strip()}

    names = [str(name) for name in (available or []) if str(name)]
    suggestions = difflib.get_close_matches(shown, names, n=3, cutoff=0.5)
    message = f"No {kind} matches '{shown}'."
    if means:
        message += f" {means}"
    if suggestions:
        message += " Did you mean: " + ", ".join(f"'{name}'" for name in suggestions) + "?"
    elif names:
        message += " Available: " + ", ".join(f"'{name}'" for name in names[:8])
        if len(names) > 8:
            message += f" (+{len(names) - 8} more)"
    else:
        message += f" There is no {kind} to choose from yet."
    out: dict = {"_error": message}
    if suggestions:
        out["_suggestions"] = suggestions
    return out


def _usage_fields(raw) -> dict:
    """Keep the non-secret fields of a usage payload. Never a key or a token."""
    if not isinstance(raw, dict):
        return {}
    error = raw.get("error")
    code = raw.get("code")
    fields = {
        "used": raw.get("images_used"),
        "limit": raw.get("images_limit"),
        "is_free": raw.get("is_free_tier"),
        "error": error,
        "code": code,
    }
    # A refused read is a failure, and the module contract says a failure comes
    # back under "_error". Without it a caller reads a 401 as a success whose
    # counts happen to be None.
    if error:
        fields["_error"] = str(error) if not code else f"{error} (code: {code})"
    return fields


def _project_layer_names() -> list[str]:
    """Every layer name in the project, for the nearest-match answer above."""
    return [layer.name() for layer in QgsProject.instance().mapLayers().values()]


def _find_project_layer(name: str):
    """First project layer whose name matches, exact first then substring."""
    if not name:
        return None
    target = name.strip().lower()
    fallback = None
    for layer in QgsProject.instance().mapLayers().values():
        current = (layer.name() or "").strip().lower()
        if current == target:
            return layer
        if fallback is None and target in current:
            fallback = layer
    return fallback


def _jsonable(value, depth: int = 0):
    """Copy a served or cached structure down to types ``json.dumps`` accepts.

    The prompt catalogue and the history rows both come from a server that may
    add fields at any time, so they are passed through this rather than trusted
    to be plain data. Anything that is not a string, a number, a boolean, a
    list or a dict is rendered as its text.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if depth >= 8:
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, depth + 1) for item in value]
    return str(value)
