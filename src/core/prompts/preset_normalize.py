











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
})




_CLIENT_PRESET_FIELDS = frozenset({"from_recent", "from_favorites", "ts"})









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


def _normalize_preset(preset: dict, source_category: str) -> dict:










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
    })
    return normalized
