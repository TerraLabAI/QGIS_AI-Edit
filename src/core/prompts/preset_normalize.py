











from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from ..i18n import _read_user_locale


def _current_lang() -> str:

    locale = _read_user_locale() or "en"
    short = locale[:2].lower()
    return short if short in ("en", "fr", "es", "pt") else "en"


def _pick_label(label_field: Any, fallback: str = "") -> str:


    if isinstance(label_field, str):
        return label_field
    if isinstance(label_field, dict):
        lang = _current_lang()
        for code in (lang, "en"):
            value = label_field.get(code)
            if isinstance(value, str) and value.strip():
                return value
        return fallback if isinstance(fallback, str) else ""
    return fallback





_KNOWN_PRESET_FIELDS = frozenset({
    "id",
    "label",
    "prompt",
    "source_category",
    "top_pick",
    "experimental",
    "vector_color",
    "need",
    "demo_url_before",
    "demo_url_after",
    "demo_url_vector",
    "example",
})




_CLIENT_PRESET_FIELDS = frozenset({"from_recent", "from_favorites", "ts", "placeholder"})









_MAX_EXTRA_DEPTH = 6
_MAX_EXTRA_KEYS = 40
_MAX_EXTRA_ITEMS = 40
_MAX_EXTRA_CHARS = 400


def _is_json_shaped(value: Any, depth: int = 0) -> bool:



    if isinstance(value, str):
        return len(value) <= _MAX_EXTRA_CHARS
    if isinstance(value, float):
        return math.isfinite(value)
    if value is None or isinstance(value, (bool, int)):
        return True
    if depth >= _MAX_EXTRA_DEPTH:
        return False
    if isinstance(value, list):
        return len(value) <= _MAX_EXTRA_ITEMS and all(
            _is_json_shaped(v, depth + 1) for v in value
        )
    if isinstance(value, dict):
        return len(value) <= _MAX_EXTRA_ITEMS and all(
            isinstance(k, str)
            and len(k) <= _MAX_EXTRA_CHARS
            and _is_json_shaped(v, depth + 1)
            for k, v in value.items()
        )
    return False


def _preset_extras(preset: dict) -> dict:









    extras: dict = {}
    try:
        items = preset.items()
    except Exception:  # nosec B110
        return extras
    for key, value in items:
        if len(extras) >= _MAX_EXTRA_KEYS:
            break
        if not isinstance(key, str) or not key or len(key) > _MAX_EXTRA_CHARS:
            continue
        if key in _KNOWN_PRESET_FIELDS or key in _CLIENT_PRESET_FIELDS:
            continue
        try:
            if _is_json_shaped(value):
                extras[key] = deepcopy(value)
        except Exception:  # nosec B112
            continue
    return extras


_HEX_COLOR_CHARS = frozenset("0123456789abcdefABCDEF")
_VECTORIZE_MODES = frozenset({"classes", "single", "none"})
_VECTORIZE_NUMBERS = ("tolerance", "simplify", "sieve", "min_pixels", "feature_count")


def _safe_example_url(value: Any) -> str | None:



    if not isinstance(value, str) or not value or len(value) > _MAX_EXTRA_CHARS:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme:
        if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username:
            return None
        return value
    if parsed.netloc or not value.startswith("/"):
        return None
    return value


def _clean_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and len(value) <= _MAX_EXTRA_CHARS:
        return value.strip()
    return None


def _clean_number(value: Any, minimum: float = 0.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < minimum:
        return None
    return value


def _clean_hex_color(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and len(value) == 7
        and value.startswith("#")
        and all(c in _HEX_COLOR_CHARS for c in value[1:])
    ):
        return value
    return None


def _clean_bbox_4326(value: Any) -> list[float] | None:

    if not isinstance(value, list) or len(value) != 4:
        return None
    nums = [_clean_number(v, minimum=-180.0) for v in value]
    if any(n is None for n in nums):
        return None
    west, south, east, north = (float(n) for n in nums)
    if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
        return None
    return [west, south, east, north]


def _clean_basemap(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    xyz = value.get("xyz")
    if not isinstance(xyz, str) or not xyz.lower().startswith("https://") or len(xyz) > _MAX_EXTRA_CHARS:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in xyz) or "\\" in xyz:
        return None
    out: dict = {"xyz": xyz}
    name = _clean_text(value.get("name"))
    if name:
        out["name"] = name
    how = _clean_text(value.get("how"))
    if how:
        out["how"] = how
    zmax = value.get("zmax")
    if isinstance(zmax, int) and not isinstance(zmax, bool) and 0 <= zmax <= 24:
        out["zmax"] = zmax
    return out


def _clean_vectorize(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    mode = value.get("mode")
    if mode not in _VECTORIZE_MODES:
        return None
    out: dict = {"mode": mode}
    color = _clean_hex_color(value.get("color"))
    if color:
        out["color"] = color
    classes = value.get("classes")
    if isinstance(classes, list):
        kept = []
        for item in classes[:_MAX_EXTRA_ITEMS]:
            if not isinstance(item, dict):
                continue
            label = _clean_text(item.get("label"))
            if not label:
                continue
            entry = {"label": label}
            item_color = _clean_hex_color(item.get("color"))
            if item_color:
                entry["color"] = item_color
            kept.append(entry)
        if kept:
            out["classes"] = kept
    for key in _VECTORIZE_NUMBERS:
        number = _clean_number(value.get(key))
        if number is not None:
            out[key] = number
    note = _clean_text(value.get("note"))
    if note:
        out["note"] = note
    return out


def _sanitize_example(value: Any) -> dict | None:



    try:
        if not isinstance(value, dict) or not _is_json_shaped(value):
            return None
        out: dict = {}
        place = _clean_text(value.get("place"))
        if place:
            out["place"] = place
        gsd = _clean_number(value.get("gsd_m"))
        if gsd:
            out["gsd_m"] = gsd
        resolution = _clean_text(value.get("resolution"))
        if resolution and len(resolution) <= 8:
            out["resolution"] = resolution
        credits = value.get("credits")
        if isinstance(credits, int) and not isinstance(credits, bool) and credits >= 0:
            out["credits"] = credits
        zone = value.get("zone")
        if isinstance(zone, dict):
            bbox = _clean_bbox_4326(zone.get("bbox_4326"))
            if bbox:
                out["zone"] = {"bbox_4326": bbox}
        basemap = _clean_basemap(value.get("basemap"))
        if basemap:
            out["basemap"] = basemap
        vectorize = _clean_vectorize(value.get("vectorize"))
        if vectorize:
            out["vectorize"] = vectorize
        chain = value.get("chain")
        if isinstance(chain, dict):
            chain_prompt = _clean_text(chain.get("prompt"))
            if chain_prompt:
                out["chain"] = {"prompt": chain_prompt}
        limits = _clean_text(value.get("limits"))
        if limits:
            out["limits"] = limits
        assets = value.get("assets")
        if isinstance(assets, dict):
            kept_assets = {
                key: url
                for key in ("result_url", "polygons_url", "recipe_url")
                if (url := _safe_example_url(assets.get(key)))
            }
            if kept_assets:
                out["assets"] = kept_assets
        return out or None
    except Exception:  # nosec B110
        return None


def _normalize_preset(preset: dict, source_category: str, placeholder: bool = False) -> dict:













    normalized = _preset_extras(preset)
    preset_id = preset.get("id")
    preset_id = preset_id if isinstance(preset_id, str) else ""
    normalized.update({
        "id": preset_id,
        "label": _pick_label(preset.get("label"), preset_id),
        "prompt": _pick_label(preset.get("prompt"), ""),
        "source_category": source_category,
        "top_pick": bool(preset.get("top_pick", False)),



        "experimental": bool(preset.get("experimental", False)),
        "vector_color": preset.get("vector_color"),



        "need": preset.get("need"),
        "demo_url_before": preset.get("demo_url_before"),
        "demo_url_after": preset.get("demo_url_after"),


        "demo_url_vector": _safe_example_url(preset.get("demo_url_vector")),
        "example": _sanitize_example(preset.get("example")),
    })
    if placeholder or is_placeholder_preset(normalized):


        normalized["placeholder"] = True
        normalized["prompt"] = ""
    return normalized


def _same_preset_text(a: Any, b: Any) -> bool:
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


def is_placeholder_preset(preset: Any) -> bool:






    if not isinstance(preset, dict):
        return False
    if preset.get("from_recent") or preset.get("from_favorites"):
        return False
    if preset.get("placeholder") is True:
        return True
    prompt = preset.get("prompt")
    label = preset.get("label")
    if isinstance(prompt, dict) and isinstance(label, dict):
        variants = [(k, v) for k, v in prompt.items() if isinstance(v, str) and v.strip()]
        if not variants:
            return True
        return all(_same_preset_text(v, label.get(k)) for k, v in variants)
    text = _pick_label(prompt, "")
    if not isinstance(text, str) or not text.strip():
        return True
    return _same_preset_text(text, _pick_label(label, ""))


def catalog_has_placeholders(catalog: Any) -> bool:

    if not isinstance(catalog, dict):
        return False
    if catalog.get("prompts") == "placeholder":
        return True
    for cat in catalog.get("categories") or []:
        if not isinstance(cat, dict):
            continue
        for preset in cat.get("presets") or []:
            if is_placeholder_preset(preset):
                return True
    return False
