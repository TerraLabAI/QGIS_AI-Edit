"""Prompt catalog facade backed by the server-side catalog.

The plugin no longer ships a hardcoded preset list. All presets, prompts,
and Top Picks come from /api/ai-edit/presets via prompt_presets_client.
This module wraps the cached catalog for the dialog (and other callers)
in a stable shape, falling back to empty themed shells when no cache is
available (first install, offline).
"""
from __future__ import annotations

import os
import re
from typing import Any

from ..config_store import get_export_dial
from ..i18n import tr

# Re-exports: the format/detect/normalize logic moved to sibling modules;
# callers keep importing everything through this facade.
from .preset_normalize import (  # noqa: F401
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
from .prompt_detect import (  # noqa: F401
    detect_freeform_vector_intent,
    detect_prompt_guidance,
    detect_seg_context,
)
from .prompt_format import format_template_prompt  # noqa: F401


def _normalize_for_match(s: str) -> str:
    """Collapse whitespace so reformatted prompts still match the source."""
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

# High-level needs grouping the themed categories in the library sidebar.
# Mirrored with the website catalog (`needs` array + per-category `need`
# field); these local tables are the offline fallback, resolved through
# `get_need_groups` the same way category labels are.
# The three families follow the measured usage split (ai-edit-product.md):
# Show 57%, Extract 28%, Repair 15%, in that priority order. Keys stay
# classify/project/render (QSettings fold state, accents, and old per-preset
# `need` overrides all key on them); only labels, order, and the category
# mapping moved. The live labels come from the server catalog's needs[] and
# MUST mirror these (website `needs.ts`).
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

# Grouped by need (project -> classify -> render) so the sidebar and search
# results walk the catalog in the same order the need groups display it.
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
    """Clear the session memo. Called from prompt_presets_client when the
    persisted catalog is wiped so the next read goes back to disk.
    """
    global _CATALOG_MEMO, _CATALOG_MEMO_LOADED
    _CATALOG_MEMO = None
    _CATALOG_MEMO_LOADED = False
    _clear_match_indexes()


def seed_catalog_memo(catalog: dict | None) -> None:
    """Adopt a catalog the caller has already parsed and validated.

    `prompt_presets_client._write_cache` calls this with the very dict it just
    wrote to QSettings, so a fresh catalog costs zero extra parses: without it
    the session read the 274 KB blob, json.loads'd it and re-validated it three
    times per start (stale read, first memo fill, post-fetch invalidation).
    """
    global _CATALOG_MEMO, _CATALOG_MEMO_LOADED
    _CATALOG_MEMO = catalog
    _CATALOG_MEMO_LOADED = True
    _clear_match_indexes()


# Dev escape hatch for R3 (experimental masking): SHOW_EXPERIMENTAL in
# .env.local. This module has no plugin instance to read env_vars from (it
# is called from core, not ui), so it parses the file itself, following the
# exact KEY=VALUE convention AIEditPlugin._load_env_file uses for DEBUG /
# SKIP_TRIAL_CHECK / RAW_PROMPT. Memoized like `_cached_catalog` above since
# this is checked on every catalog iteration.
_SHOW_EXPERIMENTAL_MEMO: bool | None = None


def _read_show_experimental_flag() -> bool:
    """Read SHOW_EXPERIMENTAL from .env.local at the plugin root. Missing
    file or read error -> False (masking stays on by default)."""
    plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    env_path = os.path.join(plugin_dir, ".env.local")
    if not os.path.isfile(env_path):
        return False
    try:
        with open(env_path, encoding="utf-8") as f:
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
    """Yield (category_key, raw_preset) pairs for every preset in `catalog`,
    experimental included. Used only by the generation-time / telemetry
    matchers (`lookup_template_by_prompt`, `get_vector_hints`) that must keep
    resolving experimental templates even after R3 masking hides them from
    browsing, so usage data on the few that perform well keeps flowing (the
    R4 promotion signal). Display code must go through `_iter_live_presets`
    instead."""
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
    """Yield (category_key, raw_preset) pairs for every LIVE preset - the R3
    masking choke point. Every display accessor built on this (themed
    categories, need groups/tiles/pages, top picks, search, Recent/Favorites
    template re-attachment) inherits the filter for free. SHOW_EXPERIMENTAL
    in .env.local disables the filter for QA."""
    show_experimental = _show_experimental()
    for key, p in _iter_server_presets(catalog):
        if p.get("experimental") and not show_experimental:
            continue
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


# The two generation-time matchers below used to walk the whole catalog on
# every call: ~488 `re.sub` whitespace collapses and 6.0 ms per
# `lookup_template_by_prompt`, once per Recent card (9 per page), twice on the
# Generate click, once per detail popup. Both are now dict lookups into indexes
# built in ONE walk, keyed by normalized prompt and by preset id.
#
# Invalidation rule: the indexes belong to one catalog OBJECT. They are rebuilt
# whenever the catalog handed in is not the one they were built from (identity,
# not equality), and dropped outright by `invalidate_catalog_memo` /
# `seed_catalog_memo`. `_MATCH_SOURCE` holds a strong reference to that catalog,
# so its id can never be recycled under a stale index while the memo lives.
_MATCH_SOURCE: dict | None = None
_MATCH_BY_PROMPT: dict[str, tuple[str, str]] | None = None
_MATCH_BY_ID: dict[str, tuple[str | None, list[dict] | None]] | None = None


def _clear_match_indexes() -> None:
    global _MATCH_SOURCE, _MATCH_BY_PROMPT, _MATCH_BY_ID
    _MATCH_SOURCE = None
    _MATCH_BY_PROMPT = None
    _MATCH_BY_ID = None


def _match_indexes(catalog: dict | None) -> tuple[dict, dict]:
    """The (by normalized prompt, by preset id) pair for `catalog`, built once.

    Experimental presets are indexed on purpose: these two matchers are the R3
    masking exception (see `_iter_server_presets`). First preset in catalog
    order wins a key, which is the order the linear walks resolved in."""
    global _MATCH_SOURCE, _MATCH_BY_PROMPT, _MATCH_BY_ID
    if _MATCH_BY_PROMPT is None or catalog is not _MATCH_SOURCE:
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
        _MATCH_BY_PROMPT = by_prompt
        _MATCH_BY_ID = by_id
        _MATCH_SOURCE = catalog
    return _MATCH_BY_PROMPT, _MATCH_BY_ID


def lookup_template_by_prompt(prompt_text: str) -> tuple[str, str] | None:
    """Return (template_id, label) when prompt_text equals a server preset
    after whitespace normalization. Matches across ALL language variants of
    the preset, so a French user running the French version of a template
    still gets tagged with the same canonical template_id as an English user.
    This is what makes per-template usage analytics language-agnostic."""
    norm = _normalize_for_match(prompt_text)
    if not norm:
        return None
    by_prompt, _by_id = _match_indexes(_cached_catalog())
    return by_prompt.get(norm)


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
    _by_prompt, by_id = _match_indexes(_cached_catalog())
    return by_id.get(template_id, (None, None))


def _build_prompt_lookup(catalog: dict | None) -> dict[str, dict]:
    """Map raw prompt text -> {label, category} for re-attaching template
    metadata to Recent/Favorites entries the user saved from a template.

    Indexes every language variant of every polyglot prompt, so a Recent
    entry saved in any language re-attaches to its template on next read.
    Live presets only (R3): an experimental template re-attaches as a plain
    text card, same as any other display surface."""
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
    """Map raw prompt text -> the full normalized preset (id + label + demo
    image URLs). Lets a saved favorite re-render as the template's before/after
    preview card instead of a bare text card. Indexes every language variant.
    Live presets only (R3), same reasoning as `_build_prompt_lookup`."""
    lookup: dict[str, dict] = {}
    for cat_key, p in _iter_live_presets(catalog):
        norm = _normalize_preset(p, cat_key)
        for variant in _iter_prompt_variants(p.get("prompt")):
            key = variant.strip()
            if key and key not in lookup:
                lookup[key] = norm
    return lookup


def get_preset_by_id(preset_id: str, server_catalog: dict | None = None) -> dict | None:
    """Return the normalized preset (id + label + prompt) for `preset_id`.

    Used to prime a prompt programmatically (e.g. reopening a template from
    the library). When `server_catalog` is None, falls back to the
    locally-cached catalog. Returns None when the catalog is unavailable
    (first install offline), the id is absent, or the preset is experimental
    and masked (R3) - reopening from the library is a display action, so
    callers can skip the prompt fill and degrade gracefully."""
    if not preset_id:
        return None
    if server_catalog is None:
        server_catalog = _cached_catalog()
    for cat_key, p in _iter_live_presets(server_catalog):
        if p.get("id") == preset_id:
            return _normalize_preset(p, cat_key)
    return None


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
    """User-managed Favorites from prompt_history. A favorite that matches a
    curated template re-renders as that template's before/after preview card
    (full preset: id + demo images); a freeform saved prompt with no template
    match stays a text card. Both carry from_favorites for the origin pill."""
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
        # No matching template: the user's own saved prompt -> text card. Keep
        # any stored label/category so the pill still reads right; otherwise
        # show the prompt itself as the card title.
        stored_label = entry.get("label")
        out.append({
            "id": f"fav_{i}",
            "label": tr(stored_label) if stored_label else prompt,
            "prompt": prompt,
            "source_category": entry.get("source_category"),
            "from_favorites": True,
        })
    return out


# Fallback size when the catalog carries no per-preset `top_pick` flags at
# all (spec R2 open question 2): enough for the landing shelf strip plus a
# non-trivial "See all", without silently becoming a full catalog dump.
_TOP_PICKS_FALLBACK_COUNT = 12


def _top_picks_fallback_size() -> int:
    """How many live presets stand in for an unflagged catalog, read at use
    time so the shelf length can be retuned from a deploy."""
    return get_export_dial("library.top_picks_fallback_count", _TOP_PICKS_FALLBACK_COUNT)


def get_top_picks(server_catalog: dict | None = None) -> list[dict]:
    """Top Picks: every LIVE preset flagged `top_pick` in the served catalog,
    in catalog order (R2 - editorial, not usage-driven). Falls back to the
    first `_TOP_PICKS_FALLBACK_COUNT` live presets in catalog order when the
    catalog ships no top_pick flags, so the shelf is never empty just
    because curation hasn't caught up. `server_catalog` falls back to the
    locally-cached catalog when None, same as the other get_* accessors."""
    if server_catalog is None:
        server_catalog = _cached_catalog()
    live = [_normalize_preset(p, cat_key) for cat_key, p in _iter_live_presets(server_catalog)]
    picks = [p for p in live if p["top_pick"]]
    return picks or live[:_top_picks_fallback_size()]


def _find_server_category(catalog: dict | None, cat_key: str) -> dict | None:
    """Return the raw server category dict for `cat_key`, or None."""
    if not isinstance(catalog, dict):
        return None
    for cat in catalog.get("categories", []) or []:
        if isinstance(cat, dict) and cat.get("key") == cat_key:
            return cat
    return None


def _themed_category_label(cat_key: str, catalog: dict | None) -> str:
    """Resolve the user-facing category label.

    Prefers the server's polyglot label when available (single source of truth,
    so new categories don't need a plugin .ts update), and falls back to the
    plugin's local tr() table when offline or for the very first session."""
    cat = _find_server_category(catalog, cat_key)
    if cat is not None:
        resolved = _pick_label(cat.get("label"), "")
        if resolved:
            return resolved
    local = _CATEGORY_LABELS.get(cat_key)
    return tr(local) if local else cat_key


def _build_themed_category(cat_key: str, catalog: dict | None) -> list[dict]:
    """All LIVE presets in `cat_key` from the server catalog (empty if the
    category is unavailable, or if every one of its presets is experimental
    and masked - R3). Filters through `_iter_live_presets` rather than the
    category's raw preset list so masking reaches every category page,
    need group and count derived from it."""
    if _find_server_category(catalog, cat_key) is None:
        return []
    return [
        _normalize_preset(p, cat_key)
        for k, p in _iter_live_presets(catalog)
        if k == cat_key
    ]


def _category_need(cat_key: str, catalog: dict | None) -> str:
    """Need key for a category: the server's assignment first (so future
    categories land in the right group without a plugin update), else the
    local fallback table."""
    cat = _find_server_category(catalog, cat_key)
    if cat is not None:
        need = cat.get("need")
        if isinstance(need, str) and need in _NEED_LABELS:
            return need
    return _CATEGORY_NEED.get(cat_key, _NEED_ORDER[0])


# Pseudo-categories from get_all_categories that are NOT part of the themed
# catalog and must be excluded from need grouping.
_PERSONAL_CATEGORY_KEYS = ("recent", "user_favorites", "favorites")


def _preset_need(preset: dict, catalog: dict | None) -> str:
    """Effective need for a (normalized) preset: its own `need` override when
    valid, else its category's need. Mirrors the website's optional per-preset
    `need` field so a single prompt can move families without moving its
    category."""
    need = preset.get("need")
    if isinstance(need, str) and need in _NEED_LABELS:
        return need
    return _category_need(preset.get("source_category", ""), catalog)


def _all_category_keys(catalog: dict | None) -> list[str]:
    """Category keys to walk: the local `_CATEGORY_ORDER` first (stable
    order), then any server catalog categories not in the local table
    appended, so a category the server adds still renders (the local table
    is only the offline fallback, not a whitelist)."""
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
    """Ordered need groups for the library sidebar.

    Each group is ``{key, label, tagline, categories: [cat_key, ...]}``.
    Labels and taglines prefer the server catalog's polyglot ``needs``
    entries and fall back to the local tables, mirroring how category
    labels resolve."""
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
    """Every themed preset bucketed by its EFFECTIVE need (per-preset `need`
    override wins over the category's need)."""
    buckets: dict[str, list[dict]] = {k: [] for k in _NEED_ORDER}
    for cat in get_all_categories(server_catalog):
        if cat["key"] in _PERSONAL_CATEGORY_KEYS:
            continue
        for preset in cat["presets"]:
            buckets.setdefault(_preset_need(preset, server_catalog), []).append(preset)
    return buckets


def get_need_tiles(server_catalog: dict | None = None) -> list[dict]:
    """One tile per need, in `_NEED_ORDER`, for the library landing page.

    Presets are grouped by their EFFECTIVE need (a preset's own `need` override
    wins over its category's need). `hero` is the tile's before/after thumbnail
    preset: first top pick in the need, else first preset with an "after" demo,
    else first preset, else None (empty need)."""
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
    """The need's drill-in payload: label, tagline, and its categories each as
    ``{key, label, presets}`` - only presets whose EFFECTIVE need matches, so a
    prompt moved via a per-preset `need` override shows under the right family.
    Empty shell if `need_key` is unknown."""
    group = next(
        (g for g in get_need_groups(server_catalog) if g["key"] == need_key), None
    )
    if group is None:
        return {"key": need_key, "label": "", "tagline": "", "categories": []}
    categories: list[dict] = []
    for cat in get_all_categories(server_catalog):
        if cat["key"] in _PERSONAL_CATEGORY_KEYS:
            continue
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
        "label": tr("Top picks"),
        "presets": get_top_picks(server_catalog),
    })

    for cat_key in _all_category_keys(server_catalog):
        result.append({
            "key": cat_key,
            "label": _themed_category_label(cat_key, server_catalog),
            "presets": _build_themed_category(cat_key, server_catalog),
        })

    return result
