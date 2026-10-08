







from __future__ import annotations

from typing import Any

from ..config_store import get_export_dial
from ..i18n import tr



from .preset_normalize import (
    _CLIENT_PRESET_FIELDS,
    _KNOWN_PRESET_FIELDS,
    _MAX_EXTRA_CHARS,
    _MAX_EXTRA_DEPTH,
    _MAX_EXTRA_ITEMS,
    _MAX_EXTRA_KEYS,
    _current_lang,
    _is_json_shaped,
    _normalize_preset,
    _pick_label,
    _preset_extras,
)
from .prompt_format import format_template_prompt


__all__ = [
    "_CLIENT_PRESET_FIELDS",
    "_current_lang",
    "_is_json_shaped",
    "_KNOWN_PRESET_FIELDS",
    "_MAX_EXTRA_CHARS",
    "_MAX_EXTRA_DEPTH",
    "_MAX_EXTRA_ITEMS",
    "_MAX_EXTRA_KEYS",
    "_normalize_preset",
    "_pick_label",
    "_preset_extras",
    "format_template_prompt",
    "get_all_categories",
    "get_need_groups",
    "get_need_page",
    "get_need_tiles",
    "get_preset_by_id",
    "get_top_picks",
    "invalidate_catalog_memo",
    "seed_catalog_memo",
    "template_label",
]








_catalog_memo: dict[str, Any] = {"catalog": None, "loaded": False}


def _cached_catalog() -> dict | None:

    if _catalog_memo["loaded"]:
        return _catalog_memo["catalog"]
    from .prompt_presets_client import read_cached_catalog_stale_ok

    read_cached_catalog_stale_ok()
    return _catalog_memo["catalog"]


def invalidate_catalog_memo() -> None:



    _catalog_memo["catalog"] = None
    _catalog_memo["loaded"] = False


def seed_catalog_memo(catalog: dict | None) -> None:







    _catalog_memo["catalog"] = catalog
    _catalog_memo["loaded"] = True


def _iter_server_presets(catalog: dict | None):



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


    if isinstance(prompt_field, str):
        if prompt_field:
            yield prompt_field
    elif isinstance(prompt_field, dict):
        for v in prompt_field.values():
            if isinstance(v, str) and v:
                yield v


def template_label(template_id: str) -> str | None:



    if not template_id:
        return None
    for _cat_key, p in _iter_server_presets(_cached_catalog()):
        if p.get("id") == template_id:
            return _pick_label(p.get("label"), template_id)
    return None


def _normalize_served(preset: dict, cat_key: str, catalog: dict | None) -> dict:


    placeholder = isinstance(catalog, dict) and catalog.get("prompts") == "placeholder"
    return _normalize_preset(preset, cat_key, placeholder=placeholder)


def _build_prompt_lookup(catalog: dict | None) -> dict[str, dict]:






    lookup: dict[str, dict] = {}
    for cat_key, p in _iter_server_presets(catalog):
        label = _pick_label(p.get("label"), p.get("id", ""))
        for variant in _iter_prompt_variants(p.get("prompt")):
            key = variant.strip()
            if not key:
                continue
            lookup[key] = {"label": label, "category": cat_key}
    return lookup


def _build_preset_lookup(catalog: dict | None) -> dict[str, dict]:



    lookup: dict[str, dict] = {}
    for cat_key, p in _iter_server_presets(catalog):
        norm = _normalize_served(p, cat_key, catalog)
        for variant in _iter_prompt_variants(p.get("prompt")):
            key = variant.strip()
            if key and key not in lookup:
                lookup[key] = norm
    return lookup


def get_preset_by_id(preset_id: str, server_catalog: dict | None = None) -> dict | None:








    if not preset_id:
        return None
    if server_catalog is None:
        server_catalog = _cached_catalog()
    for cat_key, p in _iter_server_presets(server_catalog):
        if p.get("id") == preset_id:
            return _normalize_served(p, cat_key, server_catalog)
    return None


def _build_recent_presets(catalog: dict | None) -> list[dict]:


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




    from . import prompt_history

    lookup = _build_preset_lookup(catalog)
    out: list[dict] = []
    for i, entry in enumerate(prompt_history.get_favorites()):
        prompt = (entry.get("prompt") or "").strip()
        if not prompt:
            continue
        full = lookup.get(prompt)
        if full is not None:
            preset = dict(full)
            preset["id"] = full.get("id") or f"fav_{i}"
            preset["prompt"] = prompt
            preset["from_favorites"] = True
            out.append(preset)
            continue



        stored_label = entry.get("label")
        out.append({
            "id": f"fav_{i}",
            "label": tr(stored_label) if stored_label else prompt,
            "prompt": prompt,
            "source_category": entry.get("source_category"),
            "from_favorites": True,
        })
    return out





_TOP_PICKS_FALLBACK_COUNT = 12


def _top_picks_fallback_size() -> int:


    return get_export_dial("library.top_picks_fallback_count", _TOP_PICKS_FALLBACK_COUNT)


def get_top_picks(server_catalog: dict | None = None) -> list[dict]:











    if server_catalog is None:
        server_catalog = _cached_catalog()
    live = [
        _normalize_served(p, cat_key, server_catalog)
        for cat_key, p in _iter_server_presets(server_catalog)
    ]
    picks = [p for p in live if p["top_pick"]]
    if not picks:
        return live[:_top_picks_fallback_size()]
    order = _served_top_pick_order(server_catalog)
    if not order:
        return picks
    rank = {pid: i for i, pid in enumerate(order)}
    unranked = len(rank)
    return sorted(picks, key=lambda p: rank.get(p["id"], unranked))


def _served_top_pick_order(catalog: dict | None) -> list[str]:

    if not isinstance(catalog, dict):
        return []
    raw = catalog.get("top_picks")
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(pid for pid in raw if isinstance(pid, str) and pid))


def _find_server_category(catalog: dict | None, cat_key: str) -> dict | None:

    if not isinstance(catalog, dict):
        return None
    for cat in catalog.get("categories", []) or []:
        if isinstance(cat, dict) and cat.get("key") == cat_key:
            return cat
    return None


def _served_needs(catalog: dict | None) -> list[dict]:

    if not isinstance(catalog, dict):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for entry in catalog.get("needs", []) or []:
        key = entry.get("key") if isinstance(entry, dict) else None
        if isinstance(key, str) and key and key not in seen:
            out.append(entry)
            seen.add(key)
    return out


def _need_keys(catalog: dict | None) -> list[str]:
    return [entry["key"] for entry in _served_needs(catalog)]


def _themed_category_label(cat_key: str, catalog: dict | None) -> str:

    cat = _find_server_category(catalog, cat_key)
    if cat is not None:
        resolved = _pick_label(cat.get("label"), "")
        if resolved:
            return resolved
    return cat_key


def _category_need(cat_key: str, catalog: dict | None) -> str | None:


    cat = _find_server_category(catalog, cat_key)
    if cat is not None:
        need = cat.get("need")
        if isinstance(need, str) and need in _need_keys(catalog):
            return need
    return None


def _preset_need(preset: dict, catalog: dict | None) -> str | None:




    need = preset.get("need")
    if isinstance(need, str) and need in _need_keys(catalog):
        return need
    return _category_need(preset.get("source_category", ""), catalog)


def _all_category_keys(catalog: dict | None) -> list[str]:

    keys: list[str] = []
    if isinstance(catalog, dict):
        for cat in catalog.get("categories", []) or []:
            key = cat.get("key") if isinstance(cat, dict) else None
            if isinstance(key, str) and key not in keys:
                keys.append(key)
    return keys


def get_need_groups(server_catalog: dict | None = None) -> list[dict]:



    if server_catalog is None:
        server_catalog = _cached_catalog()
    keys = _all_category_keys(server_catalog)
    return [{
        "key": entry["key"],
        "label": _pick_label(entry.get("label"), entry["key"]),
        "tagline": _pick_label(entry.get("tagline"), ""),
        "categories": [c for c in keys if _category_need(c, server_catalog) == entry["key"]],
    } for entry in _served_needs(server_catalog)]


def _presets_by_need(server_catalog: dict | None) -> dict[str, list[dict]]:


    if server_catalog is None:
        server_catalog = _cached_catalog()
    buckets: dict[str, list[dict]] = {k: [] for k in _need_keys(server_catalog)}
    for cat in _themed_categories(server_catalog):
        for preset in cat["presets"]:
            need = _preset_need(preset, server_catalog)
            if need is not None:
                buckets.setdefault(need, []).append(preset)
    return buckets


def get_need_tiles(server_catalog: dict | None = None) -> list[dict]:






    buckets = _presets_by_need(server_catalog)
    tiles: list[dict] = []
    for group in get_need_groups(server_catalog):
        presets = buckets.get(group["key"], [])
        cats_present = {p.get("source_category") for p in presets}
        hero = (
            next((p for p in presets if p.get("top_pick")), None)
            or next((p for p in presets if p.get("demo_url_after")), None)
            or (presets[0] if presets else None)
        )
        tiles.append({
            "key": group["key"],
            "label": group["label"],
            "tagline": group["tagline"],
            "preset_count": len(presets),
            "category_count": len(cats_present),
            "hero": hero,
        })
    return tiles


def get_need_page(need_key: str, server_catalog: dict | None = None) -> dict:




    if server_catalog is None:
        server_catalog = _cached_catalog()
    group = next(
        (g for g in get_need_groups(server_catalog) if g["key"] == need_key), None
    )
    if group is None:
        return {"key": need_key, "label": "", "tagline": "", "categories": []}
    categories: list[dict] = []
    for cat in _themed_categories(server_catalog):
        matching = [
            p for p in cat["presets"] if _preset_need(p, server_catalog) == need_key
        ]
        if matching:
            categories.append(
                {"key": cat["key"], "label": cat["label"], "presets": matching}
            )
    return {
        "key": need_key,
        "label": group["label"],
        "tagline": group["tagline"],
        "categories": categories,
    }


def get_all_categories(server_catalog: dict | None = None) -> list[dict]:





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
        "label": tr("Top picks"),
        "presets": get_top_picks(server_catalog),
    })

    result.extend(_themed_categories(server_catalog))
    return result


def _themed_categories(catalog: dict | None) -> list[dict]:

    by_category: dict[str, list[dict]] = {}
    for category, preset in _iter_server_presets(catalog):
        by_category.setdefault(category, []).append(_normalize_served(preset, category, catalog))
    return [{
        "key": key,
        "label": _themed_category_label(key, catalog),
        "presets": by_category.get(key, []),
    } for key in _all_category_keys(catalog)]
