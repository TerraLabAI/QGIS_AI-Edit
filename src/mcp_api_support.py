





from __future__ import annotations

import difflib
import functools
import math
import os
from collections.abc import Sequence

from qgis.core import QgsProject





_PLUGIN_FOLDER = os.path.basename(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
AI_EDIT_KEYS = [_PLUGIN_FOLDER, "AI_Edit", "QGIS_AI-Edit"]


def _find_plugin():

    import qgis.utils
    for key in AI_EDIT_KEYS:
        plugin = qgis.utils.plugins.get(key)
        if plugin is not None:
            return plugin
    return None


def _never_raises(func):







    @functools.wraps(func)
    def _wrapped(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
        except Exception as err:  # noqa: BLE001
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



    if error:
        fields["_error"] = str(error) if not code else f"{error} (code: {code})"
    return fields


def _project_layer_names() -> list[str]:

    return [layer.name() for layer in QgsProject.instance().mapLayers().values()]


def _find_project_layer(name: str):

    if not isinstance(name, str) or not name.strip():
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







    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if depth >= 8:
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item, depth + 1) for item in value]
    return str(value)


def _whole_number(value) -> int:

    if isinstance(value, bool):
        raise ValueError("Boolean is not a whole-number parameter")
    number = int(value)
    if not isinstance(value, (str, int)) and number != value:
        raise ValueError("A fractional value is not a whole number")
    return number


def _response_error(payload, action: str) -> dict | None:

    if not isinstance(payload, dict):
        return {"_error": f"{action} returned an invalid response."}
    if "error" in payload or "_error" in payload:
        detail = str(payload.get("_error") or payload.get("error") or f"{action} failed.")
        return {"_error": detail, "code": str(payload.get("code") or "")}
    return None
