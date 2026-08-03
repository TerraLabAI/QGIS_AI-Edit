"""Server-side template catalog fetcher.

Pulls `GET /api/ai-edit/presets` once per plugin session (cached 24h in
QSettings) and hands the parsed catalog to the prompt library dialog.
The server is the single source of truth for templates; when the cache
is missing and the network is down (first install offline), themed tabs
render empty until a fetch succeeds.

Response shape (v2, with polyglot prompts):
    {
      "version": 2,
      "categories": [
        {
          "key": "<cat>",
          "label": { "en": "...", "fr": "...", "es": "...", "pt": "..." },
          "presets": [
            {
              "id": "...",
              "label":  { "en": "...", "fr": "...", "es": "...", "pt": "..." },
              "prompt": { "en": "...", "fr": "...", "es": "...", "pt": "..." },
              "top_pick"?: true,
              "vector_color"?: "#FF0000",
              "demo_url_before": "/api/ai-edit/template-demos/<id>/before",
              "demo_url_after":  "/api/ai-edit/template-demos/<id>/after"
            },
            ...
          ]
        },
        ...
      ],
      "top_picks": ["<preset_id>", ...]
    }

    Legacy string-only `prompt` values are still accepted for back-compat;
    `_pick_label` resolves either shape to the current locale's string.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from ..config_store import get_export_dial
from ..logger import log_debug, log_warning

# Namespaced under AIEdit/ like every other plugin QSettings key (was under the
# shared TerraLab/ root).
_CACHE_KEY = "AIEdit/server_catalog_v2"
_CACHE_TS_KEY = "AIEdit/server_catalog_v2_ts"

# Where the catalog lived before the rename. The old copy was NOT harmless: the
# QSettings ini backend has no partial write, so a 274 KB value nothing reads
# still had to be rewritten on every setValue anywhere in the profile (recent
# jobs, prompt history, the live catalog itself). Measured on a real profile:
# QGIS3.ini 855 KB, of which this dead key was 280 KB. `_drop_legacy_cache_key`
# below deletes it once per session. Never rename the LIVE keys the same way -
# a QSettings key is on the user's disk.
#
# What the removal costs, so nobody rediscovers it: an ini key is compared
# case-insensitively on Windows (measured on Qt 5.15.2 and Qt 6.8.1, same
# result), and the v1.0.0 copy of this plugin that some profiles still carry
# under plugins/AI_Edit/ uses `terralab/ai_edit/server_catalog_v2` as its LIVE
# key. On a profile holding both, this removal wipes that install's cache once
# per session and it refetches. The key is only dead for profiles that do not
# carry the older copy.
_LEGACY_CACHE_KEY = "TerraLab/ai_edit/server_catalog_v2"
_LEGACY_CACHE_TS_KEY = "TerraLab/ai_edit/server_catalog_v2_ts"
_legacy_cache_checked = False
# 1h. The cache is now primarily an instant-render + offline fallback under a
# stale-while-revalidate strategy: every plugin start fires force_refresh=True
# on a background thread, so the catalog stays fresh without blocking the UI.
# Older 24h TTL meant pushed server changes took up to a day to surface even
# after a QGIS restart - actively painful during catalog iteration.
_PRESETS_CACHE_TTL_SECONDS = 60 * 60


def _presets_cache_ttl() -> int:
    """Seconds a cached catalog stays fresh, read at use time: a deploy can cut
    the wait before a pushed catalog change reaches a client."""
    return get_export_dial("cache_ttl_s.presets", _PRESETS_CACHE_TTL_SECONDS)


def _is_polyglot_or_string(value: Any) -> bool:
    """Accept either a plain string (legacy v2) or a polyglot dict with at
    least one non-empty language value (current `{en, fr, es, pt}` shape).
    `_pick_label` falls back to 'en' so we don't force its presence here,
    but the dict must contain at least one usable variant."""
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, dict):
        return any(isinstance(v, str) and v for v in value.values())
    return False


def _validate_catalog(payload: Any) -> dict | None:
    """Return the catalog dict if shape is recognised, else None.

    Defensive: future server tweaks shouldn't crash the plugin. Accepts both
    legacy v2 (string `prompt`) and current polyglot (`{en, fr, es, pt}` dict
    `prompt`) shapes so freshly translated catalogs are not rejected.
    """
    if not isinstance(payload, dict):
        return None
    categories = payload.get("categories")
    top_picks = payload.get("top_picks")
    if not isinstance(categories, list) or not isinstance(top_picks, list):
        return None
    if not categories:
        return None
    for cat in categories:
        if not isinstance(cat, dict):
            return None
        if not isinstance(cat.get("key"), str):
            return None
        presets = cat.get("presets")
        if not isinstance(presets, list):
            return None
        for p in presets:
            if not isinstance(p, dict):
                return None
            if not isinstance(p.get("id"), str):
                return None
            if not _is_polyglot_or_string(p.get("prompt")):
                return None
    return payload


def _drop_legacy_cache_key(settings) -> None:
    """Delete the pre-rename catalog copy, once per session and only when it is
    actually there.

    `contains` first, because `remove` on an absent key still dirties the
    settings and makes the next sync rewrite the whole ini for nothing. On an
    install that still carries it, this one removal hands 274 KB back and every
    later write in the profile gets that much cheaper."""
    global _legacy_cache_checked
    if _legacy_cache_checked:
        return
    _legacy_cache_checked = True
    try:
        if settings.contains(_LEGACY_CACHE_KEY):
            settings.remove(_LEGACY_CACHE_KEY)
            settings.remove(_LEGACY_CACHE_TS_KEY)
            log_debug("prompt_presets_client: dropped the pre-rename catalog key")
    except Exception as err:  # noqa: BLE001 - QSettings IO errors aren't fatal.
        log_warning(f"Failed to drop the legacy preset cache key: {err}")


# The catalog memo in `prompt_presets` is written from two threads: the stale
# read below runs on the main thread, `_write_cache` on a GenericRequestTask
# worker. The counter says which write is the latest, so a stale read that
# started before a fresh catalog landed cannot pin itself over it for the rest
# of the session.
_memo_lock = threading.Lock()
_memo_generation = 0


def _seed_prompt_presets_memo(catalog: dict | None, expected_generation: int | None = None) -> None:
    """Hand a parsed catalog to `prompt_presets`' session memo.

    With `expected_generation` the seed is conditional: it stands down when
    another thread has seeded or cleared the memo since that generation was
    read. Without it the seed always wins, which is what a fresh fetch wants."""
    global _memo_generation
    with _memo_lock:
        if expected_generation is not None and expected_generation != _memo_generation:
            log_debug("prompt_presets_client: fresher catalog already in the memo")
            return
        _memo_generation += 1
        try:
            from . import prompt_presets as _pp

            _pp.seed_catalog_memo(catalog)
        except Exception:  # pragma: no cover - circular-import guard  # nosec B110
            pass


def _clear_prompt_presets_memo() -> None:
    """Drop the session memo and count it, so a stale read in flight stands
    down instead of seeding what was just wiped."""
    global _memo_generation
    with _memo_lock:
        _memo_generation += 1
        try:
            from . import prompt_presets as _pp

            _pp.invalidate_catalog_memo()
        except Exception:  # pragma: no cover - circular-import guard  # nosec B110
            pass


def _read_cache_raw() -> tuple[dict | None, float | None]:
    """Return (catalog, age_seconds) regardless of TTL - caller decides freshness."""
    from qgis.PyQt.QtCore import QSettings
    settings = QSettings()
    # First catalog read of the session is the plugin's own startup read, so it
    # is where the one-off cleanup rides along.
    _drop_legacy_cache_key(settings)
    raw = settings.value(_CACHE_KEY, None)
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, None
    catalog = _validate_catalog(parsed)
    if catalog is None:
        return None, None
    ts_raw = settings.value(_CACHE_TS_KEY, None)
    age: float | None = None
    if ts_raw is not None:
        try:
            age = time.time() - float(ts_raw)
        except (TypeError, ValueError):
            age = None
    return catalog, age


def _read_cache() -> dict | None:
    """Return cached catalog if fresh (<TTL), else None."""
    catalog, age = _read_cache_raw()
    if catalog is None or age is None or age > _presets_cache_ttl():
        return None
    return catalog


def read_cached_catalog_stale_ok() -> dict | None:
    """Return cached catalog regardless of TTL - for instant first render
    while a background worker revalidates.

    Seeds `prompt_presets`' session memo with what it parsed. The startup path
    calls this first (a deferred read off initGui) and the memo used to stay
    empty, so the first accessor pulled the 274 KB blob out of QSettings and
    json.loads'd it a second time for the same content.

    The seed is conditional on the generation this call started from: a
    background fetch writing a fresh catalog between the read and the seed
    keeps it, and the stale dict is still returned to this caller for its own
    first render."""
    generation = _memo_generation
    catalog, _ = _read_cache_raw()
    _seed_prompt_presets_memo(catalog, expected_generation=generation)
    return catalog


def _write_cache(catalog: dict) -> None:
    """Persist the validated catalog. Best-effort, swallows write errors."""
    try:
        from qgis.PyQt.QtCore import QSettings
        settings = QSettings()
        settings.setValue(_CACHE_KEY, json.dumps(catalog))
        settings.setValue(_CACHE_TS_KEY, str(time.time()))
    except Exception as err:  # noqa: BLE001 - QSettings IO errors aren't fatal.
        log_warning(f"Failed to persist preset cache: {err}")
    # Hand the memo the dict we just wrote instead of clearing it. Clearing made
    # the next reader load the 274 KB blob back off QSettings and json.loads it
    # again for a catalog already in hand. Unconditional: this is the freshest
    # catalog there is, so it outranks any read already in flight.
    _seed_prompt_presets_memo(catalog)


def invalidate_cache() -> None:
    """Wipe the persisted catalog so the next fetch is forced to hit the server.

    Useful from the QGIS Python Console when the server catalog changed but the
    local cache is still serving the old version (push then `from
    QGIS_AI_Edit_Team.src.core.prompts.prompt_presets_client import invalidate_cache;
    invalidate_cache()`)."""
    try:
        from qgis.PyQt.QtCore import QSettings
        settings = QSettings()
        settings.remove(_CACHE_KEY)
        settings.remove(_CACHE_TS_KEY)
    except Exception as err:  # noqa: BLE001 - QSettings IO errors aren't fatal.
        log_warning(f"Failed to clear preset cache: {err}")
    _clear_prompt_presets_memo()


def fetch_server_catalog(client, force_refresh: bool = False) -> dict | None:
    """Return the latest server catalog dict, or None if unavailable.

    `client` is a `TerraLabClient` instance (we reuse its base_url + auth
    headers if needed; the /presets endpoint is currently public). When
    `force_refresh` is False (default) a fresh-enough cache short-circuits
    the network call so the dialog opens instantly on session 2+.
    """
    if not force_refresh:
        cached = _read_cache()
        if cached is not None:
            log_debug("prompt_presets_client: returning cached catalog")
            return cached

    try:
        resp = client._request("GET", "/api/ai-edit/presets", timeout_ms=5_000)
    except Exception as err:  # noqa: BLE001 - fall back to local on any client error.
        log_warning(f"Failed to fetch server catalog: {err}")
        return None

    if not isinstance(resp, dict) or "error" in resp:
        log_warning(
            f"Server catalog fetch returned error: "
            f"{resp.get('error') if isinstance(resp, dict) else resp!r}"
        )
        return None

    catalog = _validate_catalog(resp)
    if catalog is None:
        log_warning("Server catalog payload did not match expected v2 shape")
        return None

    _write_cache(catalog)
    log_debug(
        f"prompt_presets_client: fetched {len(catalog.get('categories', []))} categories"
    )
    return catalog


def store_catalog(payload) -> dict | None:
    """Validate + cache a catalog delivered out-of-band (the /bootstrap
    bundle) so the normal cached-read path serves it. Returns the parsed
    catalog, or None when the payload doesn't match the v2 shape."""
    catalog = _validate_catalog(payload)
    if catalog is None:
        return None
    _write_cache(catalog)
    return catalog


def absolute_demo_url(client, relative: str) -> str:
    """Resolve a demo URL (e.g. `/api/ai-edit/template-demos/<id>/before`) to
    the absolute terra-lab.ai URL via the client's base_url. Lets us serve
    different envs (prod / staging / dev) from the same plugin build.

    Idempotent on absolute URLs - short-circuits when ``relative`` already
    has a scheme so the same callable works for signed-URL history payloads."""
    if not relative:
        return ""
    if relative.startswith("http://") or relative.startswith("https://"):
        return relative
    base = client.base_url.rstrip("/")
    if not relative.startswith("/"):
        relative = "/" + relative
    return base + relative
