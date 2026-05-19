"""Prompt catalog facade backed by the server-side catalog.

The plugin no longer ships a hardcoded preset list. All presets, prompts,
and Top Picks come from /api/ai-edit/presets via prompt_presets_client.
This module wraps the cached catalog for the dialog (and other callers)
in a stable shape, falling back to empty themed shells when no cache is
available (first install, offline).
"""
from __future__ import annotations

import re
from typing import Any

from qgis.PyQt.QtCore import QSettings

from .i18n import tr


def _normalize_for_match(s: str) -> str:
    """Collapse whitespace so reformatted prompts still match the source."""
    return re.sub(r"\s+", " ", (s or "")).strip()


def format_template_prompt(prompt: str) -> str:
    """Add paragraph breaks for readability: one sentence per paragraph,
    plus a break after the first list-introducing colon."""
    if not prompt:
        return prompt
    with_colon = re.sub(r":\s+(?=[a-zA-Z][^.:\n]*,)", ":\n\n", prompt, count=1)
    return re.sub(r"\.\s+([A-Z])", r".\n\n\1", with_colon)


# Category metadata. Display labels are translated via tr(); icons + colors
# are static and used by the dialog's sidebar.
_CATEGORY_META = {
    "recent": {"icon": "⟲", "color": "#6a8cc0"},
    "user_favorites": {"icon": "★", "color": "#e57373"},
    "favorites": {"icon": "★", "color": "#b89868"},
    "cartography": {"icon": "❖", "color": "#9880b0"},
    "landcover": {"icon": "◉", "color": "#68a868"},
    "segment": {"icon": "▣", "color": "#b07878"},
    "climate": {"icon": "⛅", "color": "#5ca0c0"},
    "urban": {"icon": "⌂", "color": "#b08858"},
    "energy": {"icon": "☀", "color": "#d4a548"},
    "cleanup": {"icon": "⌫", "color": "#a0a058"},
    "presentation": {"icon": "❀", "color": "#c08fa0"},
}

_CATEGORY_LABELS = {
    "cartography": "Cartography",
    "landcover": "Land cover",
    "segment": "Vectorize",
    "climate": "Climate scenarios",
    "urban": "Urban scenarios",
    "energy": "Energy & solar",
    "cleanup": "Cleanup & enhance",
    "presentation": "Presentation renders",
}

_CATEGORY_ORDER = [
    "cartography",
    "landcover",
    "segment",
    "climate",
    "urban",
    "energy",
    "cleanup",
    "presentation",
]


def _current_lang() -> str:
    """Return the 2-char language code matching the server label keys."""
    locale = QSettings().value("locale/userLocale", "en_US") or "en"
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


def _normalize_preset(preset: dict, source_category: str) -> dict:
    """Pull a server preset into the flat shape the dialog expects.

    `prompt` is a polyglot dict `{en, fr, es, pt}` on v3 server catalogs.
    Older string-only payloads still work via `_pick_label`'s str fallback.
    """
    return {
        "id": preset.get("id", ""),
        "label": _pick_label(preset.get("label"), preset.get("id", "")),
        "prompt": _pick_label(preset.get("prompt"), ""),
        "source_category": source_category,
        "top_pick": bool(preset.get("top_pick", False)),
        "vector_color": preset.get("vector_color"),
        "demo_url_before": preset.get("demo_url_before"),
        "demo_url_after": preset.get("demo_url_after"),
    }


# Session-lifetime memo. `_cached_catalog` is called many times per
# result-render and per template lookup, and each call would otherwise
# parse the same JSON blob from QSettings. The memo is invalidated by
# `prompt_presets_client._write_cache` whenever a fresh server catalog
# lands on disk, so stale-after-refresh isn't a worry.
_CATALOG_MEMO: dict | None = None
_CATALOG_MEMO_LOADED = False


def _cached_catalog() -> dict | None:
    """Lazy + memoized read of the locally-cached server catalog."""
    global _CATALOG_MEMO, _CATALOG_MEMO_LOADED
    if _CATALOG_MEMO_LOADED:
        return _CATALOG_MEMO
    from .prompt_presets_client import read_cached_catalog_stale_ok

    _CATALOG_MEMO = read_cached_catalog_stale_ok()
    _CATALOG_MEMO_LOADED = True
    return _CATALOG_MEMO


def invalidate_catalog_memo() -> None:
    """Clear the session memo. Called from prompt_presets_client when a
    fresh server catalog is written so the next read sees it.
    """
    global _CATALOG_MEMO, _CATALOG_MEMO_LOADED
    _CATALOG_MEMO = None
    _CATALOG_MEMO_LOADED = False


def _iter_server_presets(catalog: dict | None):
    """Yield (category_key, raw_preset) pairs for every preset in `catalog`."""
    if not isinstance(catalog, dict):
        return
    for cat in catalog.get("categories", []) or []:
        if not isinstance(cat, dict):
            continue
        key = cat.get("key")
        if not isinstance(key, str):
            continue
        for p in cat.get("presets", []) or []:
            if isinstance(p, dict):
                yield key, p


def _iter_prompt_variants(prompt_field: Any):
    """Yield every language variant of a preset prompt. Accepts the v3
    polyglot `{en, fr, es, pt}` shape and the legacy plain-string shape."""
    if isinstance(prompt_field, str):
        if prompt_field:
            yield prompt_field
    elif isinstance(prompt_field, dict):
        for v in prompt_field.values():
            if isinstance(v, str) and v:
                yield v


def lookup_template_by_prompt(prompt_text: str) -> tuple[str, str] | None:
    """Return (template_id, label) when prompt_text equals a server preset
    after whitespace normalization. Matches across ALL language variants of
    the preset, so a French user running the French version of a template
    still gets tagged with the same canonical template_id as an English user.
    This is what makes per-template usage analytics language-agnostic."""
    norm = _normalize_for_match(prompt_text)
    if not norm:
        return None
    catalog = _cached_catalog()
    for _cat_key, p in _iter_server_presets(catalog):
        for variant in _iter_prompt_variants(p.get("prompt")):
            if _normalize_for_match(variant) == norm:
                label = _pick_label(p.get("label"), p.get("id", ""))
                return p.get("id", ""), label
    return None


def get_vector_hints(template_id: str) -> tuple[str | None, list[dict] | None]:
    """Return (vector_color, vector_classes) for a known template_id, or
    (None, None) if the template doesn't have either field set or the
    catalog cache is unavailable.

    Used at Generate-time to stash vectorize hints onto PipelineContext so
    the result panel can suggest "Extract red regions" with the swatch
    pre-filled. vector_classes (multi-class list) wins when both are set
    on the same preset; back-compat templates populate vector_color too.
    """
    if not template_id:
        return None, None
    catalog = _cached_catalog()
    for _cat_key, p in _iter_server_presets(catalog):
        if p.get("id") == template_id:
            classes = p.get("vector_classes")
            if not isinstance(classes, list) or not classes:
                classes = None
            color = p.get("vector_color")
            if not isinstance(color, str) or not color:
                color = None
            return color, classes
    return None, None


def _build_prompt_lookup(catalog: dict | None) -> dict[str, dict]:
    """Map raw prompt text -> {label, category} for re-attaching template
    metadata to Recent/Favorites entries the user saved from a template.

    Indexes every language variant of every polyglot prompt, so a Recent
    entry saved in any language re-attaches to its template on next read."""
    lookup: dict[str, dict] = {}
    for cat_key, p in _iter_server_presets(catalog):
        label = _pick_label(p.get("label"), p.get("id", ""))
        for variant in _iter_prompt_variants(p.get("prompt")):
            key = variant.strip()
            if not key:
                continue
            lookup[key] = {"label": label, "category": cat_key}
    return lookup


def _build_recent_presets(catalog: dict | None) -> list[dict]:
    """Recent prompts from prompt_history, with template metadata re-attached
    when the prompt matches a known server preset."""
    from . import prompt_history

    lookup = _build_prompt_lookup(catalog)
    out: list[dict] = []
    for i, entry in enumerate(prompt_history.get_recent()):
        prompt = (entry.get("prompt") or "").strip()
        if not prompt:
            continue
        ts = entry.get("ts") or ""
        meta = lookup.get(prompt)
        if meta:
            out.append({
                "id": f"recent_{i}",
                "label": meta["label"],
                "prompt": prompt,
                "source_category": meta["category"],
                "from_recent": True,
                "ts": ts,
            })
        else:
            out.append({
                "id": f"recent_{i}",
                "label": prompt,
                "prompt": prompt,
                "source_category": None,
                "from_recent": True,
                "ts": ts,
            })
    return out


def _build_user_favorites_presets(catalog: dict | None) -> list[dict]:
    """User-managed Favorites from prompt_history. Saved entries carry their
    own label/source_category; if missing we look them up against the server
    catalog so the pill still shows the right category."""
    from . import prompt_history

    lookup = _build_prompt_lookup(catalog)
    out: list[dict] = []
    for i, entry in enumerate(prompt_history.get_favorites()):
        prompt = (entry.get("prompt") or "").strip()
        if not prompt:
            continue
        stored_label = entry.get("label")
        stored_cat = entry.get("source_category")
        if stored_label and stored_cat:
            out.append({
                "id": f"fav_{i}",
                "label": tr(stored_label),
                "prompt": prompt,
                "source_category": stored_cat,
                "from_favorites": True,
            })
            continue
        meta = lookup.get(prompt)
        if meta:
            out.append({
                "id": f"fav_{i}",
                "label": meta["label"],
                "prompt": prompt,
                "source_category": meta["category"],
                "from_favorites": True,
            })
        else:
            out.append({
                "id": f"fav_{i}",
                "label": prompt,
                "prompt": prompt,
                "source_category": None,
                "from_favorites": True,
            })
    return out


def _build_top_picks(catalog: dict | None) -> list[dict]:
    """Top Picks in server order. Each entry references a preset by id; we
    resolve those ids back to full presets so the dialog can render them."""
    if not isinstance(catalog, dict):
        return []
    tp_ids = catalog.get("top_picks")
    if not isinstance(tp_ids, list):
        return []
    by_id: dict[str, dict] = {}
    for cat_key, p in _iter_server_presets(catalog):
        pid = p.get("id")
        if isinstance(pid, str) and pid:
            by_id[pid] = _normalize_preset(p, cat_key)
    out: list[dict] = []
    for tid in tp_ids:
        if isinstance(tid, str) and tid in by_id:
            out.append(by_id[tid])
    return out


def _build_themed_category(cat_key: str, catalog: dict | None) -> list[dict]:
    """All presets in `cat_key` from the server catalog (empty if unavailable)."""
    if not isinstance(catalog, dict):
        return []
    for cat in catalog.get("categories", []) or []:
        if not isinstance(cat, dict):
            continue
        if cat.get("key") != cat_key:
            continue
        return [
            _normalize_preset(p, cat_key)
            for p in (cat.get("presets") or [])
            if isinstance(p, dict)
        ]
    return []


def get_all_categories(server_catalog: dict | None = None) -> list[dict]:
    """Return all categories with translated labels.

    `server_catalog`: optional v2 catalog dict. When None, falls back to the
    locally-cached server catalog. With neither, themed categories render
    as empty shells (first install offline / pre-activation)."""
    if server_catalog is None:
        server_catalog = _cached_catalog()

    result: list[dict] = []

    result.append({
        "key": "recent",
        "label": tr("Recent"),
        "presets": _build_recent_presets(server_catalog),
    })

    result.append({
        "key": "user_favorites",
        "label": tr("Favorites"),
        "presets": _build_user_favorites_presets(server_catalog),
    })

    result.append({
        "key": "favorites",
        "label": tr("Top Picks"),
        "presets": _build_top_picks(server_catalog),
    })

    for cat_key in _CATEGORY_ORDER:
        result.append({
            "key": cat_key,
            "label": tr(_CATEGORY_LABELS[cat_key]),
            "presets": _build_themed_category(cat_key, server_catalog),
        })

    return result
