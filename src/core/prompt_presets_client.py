"""Server-side template catalog fetcher.

Pulls `GET /api/ai-edit/presets` once per plugin session (cached 24h in
QSettings) and hands the parsed catalog to the prompt library dialog.
The server is the single source of truth for templates; when the cache
is missing and the network is down (first install offline), themed tabs
render empty until a fetch succeeds.

Response shape (v2):
    {
      "version": 2,
      "categories": [
        {
          "key": "<cat>",
          "label": { "en": "...", "fr": "...", "es": "...", "pt": "..." },
          "presets": [
            {
              "id": "...",
              "label": { "en": "...", ... },
              "prompt": "...",
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
"""
from __future__ import annotations

import json
import time
from typing import Any

from qgis.PyQt.QtCore import QSettings

from .logger import log_debug, log_warning

_CACHE_KEY = "terralab/ai_edit/server_catalog_v2"
_CACHE_TS_KEY = "terralab/ai_edit/server_catalog_v2_ts"
_CACHE_TTL_SECONDS = 24 * 60 * 60


def _validate_catalog(payload: Any) -> dict | None:
    """Return the catalog dict if shape is recognised, else None.

    Defensive: future server tweaks shouldn't crash the plugin - anything that
    doesn't match the v2 shape falls back to local presets.
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
            if not isinstance(p.get("id"), str) or not isinstance(p.get("prompt"), str):
                return None
    return payload


def _read_cache_raw() -> tuple[dict | None, float | None]:
    """Return (catalog, age_seconds) regardless of TTL - caller decides freshness."""
    settings = QSettings()
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
    if catalog is None or age is None or age > _CACHE_TTL_SECONDS:
        return None
    return catalog


def read_cached_catalog_stale_ok() -> dict | None:
    """Return cached catalog regardless of TTL - for instant first render
    while a background worker revalidates."""
    catalog, _ = _read_cache_raw()
    return catalog


def _write_cache(catalog: dict) -> None:
    """Persist the validated catalog. Best-effort, swallows write errors."""
    try:
        settings = QSettings()
        settings.setValue(_CACHE_KEY, json.dumps(catalog))
        settings.setValue(_CACHE_TS_KEY, str(time.time()))
    except Exception as err:  # noqa: BLE001 - QSettings IO errors aren't fatal.
        log_warning(f"Failed to persist preset cache: {err}")
    # Drop the in-memory memo so the next call sees the fresh catalog.
    try:
        from . import prompt_presets as _pp

        _pp.invalidate_catalog_memo()
    except Exception:  # pragma: no cover - circular-import guard  # nosec B110
        pass


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
