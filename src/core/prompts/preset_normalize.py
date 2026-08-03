"""Turning one served preset into the flat dict the plugin reads.

Split out of prompt_presets.py (which re-exports every name below, so callers
and tests keep reaching them through the facade) when that file passed the
repo's 800-line cap. Three concerns, in dependency order: resolving a polyglot
`{en, fr, es, pt}` field to the user's language, deciding which served fields
are safe to carry through untouched, and writing the validated fields last so
a served key can never shadow one.

Nothing here reads the catalog or the network; it takes a preset dict and
returns a preset dict.
"""
from __future__ import annotations

from typing import Any

from ..i18n import _read_user_locale


def _current_lang() -> str:
    """Return the 2-char language code matching the server label keys."""
    locale = _read_user_locale() or "en"
    short = locale[:2].lower()
    return short if short in ("en", "fr", "es", "pt") else "en"


def _pick_label(label_field: Any, fallback: str = "") -> str:
    """Return a string label from the server's polyglot `{en, fr, es, pt}`
    dict, the current locale first, else "en", else the fallback."""
    if isinstance(label_field, str):
        return label_field
    if isinstance(label_field, dict):
        lang = _current_lang()
        return label_field.get(lang) or label_field.get("en") or fallback
    return fallback


# Fields this module validates and owns in a normalized preset. A served key
# can never take one of these over: they are written last (see
# `_normalize_preset`), after the pass-through.
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

# Markers the plugin stamps on a preset after normalization (Recent /
# Favorites origin, and the timestamp shown on a Recent card). Reserved the
# same way, so a served field cannot fake where a card came from.
_CLIENT_PRESET_FIELDS = frozenset({"from_recent", "from_favorites", "ts"})

# Bounds on a passed-through value. A catalog comes from parsed JSON, so a few
# levels and a few dozen entries are plenty; the depth bound stops a
# pathological payload from recursing away during validation, and the size
# bounds stop one preset from carrying a megabyte the plugin has no code for
# into every copy of every card. Same order as the other served reads
# (_MAX_POOL_ENTRIES, _MAX_COPY_CHARS). Over the bound, the key is dropped
# rather than trimmed: the plugin does not know what the value means, and a
# truncated id or URL would be worse than an absent one.
_MAX_EXTRA_DEPTH = 6
_MAX_EXTRA_KEYS = 40
_MAX_EXTRA_ITEMS = 40
_MAX_EXTRA_CHARS = 400


def _is_json_shaped(value: Any, depth: int = 0) -> bool:
    """True for a value that could have come out of a JSON document: a scalar,
    or a list/dict of such values, within the bounds above. Everything else
    (callables, any other exotic object, anything oversized) is refused."""
    if isinstance(value, str):
        return len(value) <= _MAX_EXTRA_CHARS
    if value is None or isinstance(value, (bool, int, float)):
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
    """Every served field this plugin version has no code for, kept as-is.

    This is what lets the catalog carry new per-preset settings (a different
    model, a resolution, a credit cost, a negative prompt, a vectorize
    setting) before any plugin release knows about them: an old plugin passes
    them along and ignores them, a newer one reads them. A key the plugin owns
    is never taken from the server, and a value that is not JSON-shaped, or is
    over the bounds above, is dropped rather than allowed to break the catalog.
    """
    extras: dict = {}
    try:
        items = list(preset.items())
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
                extras[key] = value
        except Exception:  # nosec B112 - continue, so B112 not B110
            continue
    return extras


def _normalize_preset(preset: dict, source_category: str) -> dict:
    """Pull a server preset into the flat shape the dialog expects.

    The fields below are validated and coerced here. Everything else the
    server sent is passed through untouched (see `_preset_extras`), and the
    validated fields are written last so a served key can never shadow one.
    Callers that only read the fields below are unaffected by the extras.

    `prompt` is a polyglot dict `{en, fr, es, pt}` on v3 server catalogs.
    Older string-only payloads still work via `_pick_label`'s str fallback.
    """
    normalized = _preset_extras(preset)
    normalized.update({
        "id": preset.get("id", ""),
        "label": _pick_label(preset.get("label"), preset.get("id", "")),
        "prompt": _pick_label(preset.get("prompt"), ""),
        "source_category": source_category,
        "top_pick": bool(preset.get("top_pick", False)),
        # Templates the server flags as fragile (model hallucinates often).
        # Plugin renders these under a separate "Experimental" disclosure
        # in each category page so the curated list stays trustworthy.
        "experimental": bool(preset.get("experimental", False)),
        "vector_color": preset.get("vector_color"),
        # Optional per-preset family override: lets the server move a single
        # prompt to a different family (need) without moving its whole category.
        # Absent/unknown -> the preset inherits its category's need.
        "need": preset.get("need"),
        "demo_url_before": preset.get("demo_url_before"),
        "demo_url_after": preset.get("demo_url_after"),
    })
    return normalized
