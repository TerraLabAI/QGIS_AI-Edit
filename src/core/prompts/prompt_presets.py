







from __future__ import annotations

import os
import re
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
from .prompt_detect import (
    detect_freeform_vector_intent,
    detect_prompt_guidance,
    detect_seg_context,
)
from .prompt_format import format_template_prompt


__all__ = [
    "_CATEGORY_ORDER",
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
    "detect_freeform_vector_intent",
    "detect_prompt_guidance",
    "detect_seg_context",
    "format_template_prompt",
    "get_all_categories",
    "get_need_groups",
    "get_need_page",
    "get_need_tiles",
    "get_preset_by_id",
    "get_top_picks",
    "get_vector_hints",
    "invalidate_catalog_memo",
    "lookup_template_by_prompt",
    "seed_catalog_memo",
]


def _normalize_for_match(s: str) -> str:

    return re.sub(r"\s+", " ", (s or "")).strip()


_CATEGORY_LABELS = {
    "cartography": "Cartography",
    "landcover": "Land cover",
    "segment": "Segment",
    "climate": "Climate scenarios",
    "urban": "Urban scenarios",
    "energy": "Energy & solar",
    "cleanup": "Cleanup & enhance",
    "presentation": "Presentation renders",
    "forestry": "Forestry & vegetation",
    "agriculture": "Agriculture",
    "archaeology": "Archaeology & heritage",
    "geology": "Geology & mining",
    "hydrology": "Water & hydrology",
}











_NEED_LABELS = {
    "project": "Show",
    "classify": "Extract",
    "render": "Repair",
}

_NEED_TAGLINES = {
    "project": "Show a project: renders, plans, simulations, before/after",
    "classify": "Extract data: detect, segment, count, map",
    "render": "Repair imagery: sharpen, upscale, fix gaps and seams",
}

_NEED_ORDER = ["project", "classify", "render"]

_CATEGORY_NEED = {
    "climate": "project",
    "urban": "project",
    "energy": "project",
    "cartography": "project",
    "presentation": "project",
    "archaeology": "project",
    "landcover": "classify",
    "segment": "classify",
    "forestry": "classify",
    "agriculture": "classify",
    "geology": "classify",
    "hydrology": "classify",
    "cleanup": "render",
}



_CATEGORY_ORDER = [
    "climate",
    "urban",
    "energy",
    "cartography",
    "presentation",
    "archaeology",
    "landcover",
    "segment",
    "forestry",
    "agriculture",
    "geology",
    "hydrology",
    "cleanup",
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
    _clear_match_indexes()


def seed_catalog_memo(catalog: dict | None) -> None:







    _catalog_memo["catalog"] = catalog
    _catalog_memo["loaded"] = True
    _clear_match_indexes()








_SHOW_EXPERIMENTAL_MEMO: bool | None = None


def _read_show_experimental_flag() -> bool:


    plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    env_path = os.path.join(plugin_dir, ".env.local")
    if not os.path.isfile(env_path):
        return False
    try:
        with open(env_path, encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == "SHOW_EXPERIMENTAL":
                    return value.strip().strip('"').strip("'").lower() == "true"
    except OSError:
        return False
    return False


def _show_experimental() -> bool:
    global _SHOW_EXPERIMENTAL_MEMO
    if _SHOW_EXPERIMENTAL_MEMO is None:
        _SHOW_EXPERIMENTAL_MEMO = _read_show_experimental_flag()
    return _SHOW_EXPERIMENTAL_MEMO


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


def _iter_live_presets(catalog: dict | None):





    show_experimental = _show_experimental()
    for key, p in _iter_server_presets(catalog):
        if p.get("experimental") and not show_experimental:
            continue
        yield key, p


def _iter_prompt_variants(prompt_field: Any):


    if isinstance(prompt_field, str):
        if prompt_field:
            yield prompt_field
    elif isinstance(prompt_field, dict):
        for v in prompt_field.values():
            if isinstance(v, str) and v:
                yield v















_match_index: dict[str, Any] = {"source": None, "by_prompt": None, "by_id": None}


def _clear_match_indexes() -> None:
    _match_index.update(source=None, by_prompt=None, by_id=None, language=None)


def _match_indexes(catalog: dict | None) -> tuple[dict, dict]:





    language = _current_lang()
    current = dict(_match_index)
    if (
        current["by_prompt"] is None or catalog is not current["source"]
        or current.get("language") != language
    ):
        by_prompt: dict[str, tuple[str, str]] = {}
        by_id: dict[str, tuple[str | None, list[dict] | None]] = {}
        for _cat_key, p in _iter_server_presets(catalog):
            preset_id = p.get("id", "")
            label = _pick_label(p.get("label"), preset_id)
            for variant in _iter_prompt_variants(p.get("prompt")):
                key = _normalize_for_match(variant)
                if key and key not in by_prompt:
                    by_prompt[key] = (preset_id, label)
            if isinstance(preset_id, str) and preset_id and preset_id not in by_id:
                classes = p.get("vector_classes")
                if not isinstance(classes, list) or not classes:
                    classes = None
                color = p.get("vector_color")
                if not isinstance(color, str) or not color:
                    color = None
                by_id[preset_id] = (color, classes)
        _match_index.update(by_prompt=by_prompt, by_id=by_id, source=catalog, language=language)
        return by_prompt, by_id
    return current["by_prompt"], current["by_id"]


def lookup_template_by_prompt(prompt_text: str) -> tuple[str, str] | None:





    norm = _normalize_for_match(prompt_text)
    if not norm:
        return None
    by_prompt, _by_id = _match_indexes(_cached_catalog())
    return by_prompt.get(norm)


def get_vector_hints(template_id: str) -> tuple[str | None, list[dict] | None]:









    if not template_id:
        return None, None
    _by_prompt, by_id = _match_indexes(_cached_catalog())
    return by_id.get(template_id, (None, None))


def _build_prompt_lookup(catalog: dict | None) -> dict[str, dict]:







    lookup: dict[str, dict] = {}
    for cat_key, p in _iter_live_presets(catalog):
        label = _pick_label(p.get("label"), p.get("id", ""))
        for variant in _iter_prompt_variants(p.get("prompt")):
            key = variant.strip()
            if not key:
                continue
            lookup[key] = {"label": label, "category": cat_key}
    return lookup


def _build_preset_lookup(catalog: dict | None) -> dict[str, dict]:




    lookup: dict[str, dict] = {}
    for cat_key, p in _iter_live_presets(catalog):
        norm = _normalize_preset(p, cat_key)
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
    for cat_key, p in _iter_live_presets(server_catalog):
        if p.get("id") == preset_id:
            return _normalize_preset(p, cat_key)
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
    live = [_normalize_preset(p, cat_key) for cat_key, p in _iter_live_presets(server_catalog)]
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


def _themed_category_label(cat_key: str, catalog: dict | None) -> str:





    cat = _find_server_category(catalog, cat_key)
    if cat is not None:
        resolved = _pick_label(cat.get("label"), "")
        if resolved:
            return resolved
    local = _CATEGORY_LABELS.get(cat_key)
    return tr(local) if local else cat_key


def _build_themed_category(cat_key: str, catalog: dict | None) -> list[dict]:





    if _find_server_category(catalog, cat_key) is None:
        return []
    return [
        _normalize_preset(p, cat_key)
        for k, p in _iter_live_presets(catalog)
        if k == cat_key
    ]


def _category_need(cat_key: str, catalog: dict | None) -> str:



    cat = _find_server_category(catalog, cat_key)
    if cat is not None:
        need = cat.get("need")
        if isinstance(need, str) and need in _NEED_LABELS:
            return need
    return _CATEGORY_NEED.get(cat_key, _NEED_ORDER[0])




_PERSONAL_CATEGORY_KEYS = ("recent", "user_favorites", "favorites")


def _preset_need(preset: dict, catalog: dict | None) -> str:




    need = preset.get("need")
    if isinstance(need, str) and need in _NEED_LABELS:
        return need
    return _category_need(preset.get("source_category", ""), catalog)


def _all_category_keys(catalog: dict | None) -> list[str]:




    keys = list(_CATEGORY_ORDER)
    seen = set(keys)
    if isinstance(catalog, dict):
        for cat in catalog.get("categories", []) or []:
            key = cat.get("key") if isinstance(cat, dict) else None
            if isinstance(key, str) and key not in seen:
                keys.append(key)
                seen.add(key)
    return keys


def get_need_groups(server_catalog: dict | None = None) -> list[dict]:






    if server_catalog is None:
        server_catalog = _cached_catalog()

    server_needs: dict[str, dict] = {}
    if isinstance(server_catalog, dict):
        for entry in server_catalog.get("needs", []) or []:
            if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                server_needs[entry["key"]] = entry

    groups: list[dict] = []
    for need_key in _NEED_ORDER:
        srv = server_needs.get(need_key) or {}
        groups.append({
            "key": need_key,
            "label": _pick_label(srv.get("label"), "") or tr(_NEED_LABELS[need_key]),
            "tagline": (
                _pick_label(srv.get("tagline"), "") or tr(_NEED_TAGLINES[need_key])
            ),
            "categories": [
                c for c in _all_category_keys(server_catalog)
                if _category_need(c, server_catalog) == need_key
            ],
        })
    return groups


def _presets_by_need(server_catalog: dict | None) -> dict[str, list[dict]]:


    buckets: dict[str, list[dict]] = {k: [] for k in _NEED_ORDER}
    if server_catalog is None:
        server_catalog = _cached_catalog()
    for cat in _themed_categories(server_catalog):
        for preset in cat["presets"]:
            buckets.setdefault(_preset_need(preset, server_catalog), []).append(preset)
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
    for category, preset in _iter_live_presets(catalog):
        by_category.setdefault(category, []).append(_normalize_preset(preset, category))
    return [{
        "key": key,
        "label": _themed_category_label(key, catalog),
        "presets": by_category.get(key, []),
    } for key in _all_category_keys(catalog)]
