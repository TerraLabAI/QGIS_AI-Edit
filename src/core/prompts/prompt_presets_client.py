




































from __future__ import annotations

import json
import threading
from typing import Any
from urllib.parse import urlsplit

from ..config_store import get_export_dial
from ..logger import log_debug, log_warning
from . import cache_blob_file




_CACHE_KEY = "AIEdit/server_catalog_v2"
_CACHE_TS_KEY = "AIEdit/server_catalog_v2_ts"

_CACHE_FILE = "server_catalog_v2.json"
















_LEGACY_CACHE_KEY = "TerraLab/ai_edit/server_catalog_v2"
_LEGACY_CACHE_TS_KEY = "TerraLab/ai_edit/server_catalog_v2_ts"
_legacy_cache = {"checked": False}





_PRESETS_CACHE_TTL_SECONDS = 60 * 60




_CATALOG_FETCH_TIMEOUT_MS = 5_000


def _presets_cache_ttl() -> int:


    return get_export_dial("cache_ttl_s.presets", _PRESETS_CACHE_TTL_SECONDS)


def _is_polyglot_or_string(value: Any) -> bool:




    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(isinstance(v, str) and v.strip() for v in value.values())
    return False


def _validate_catalog(payload: Any) -> dict | None:






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
        if not isinstance(cat.get("key"), str) or not cat["key"].strip():
            return None
        presets = cat.get("presets")
        if not isinstance(presets, list):
            return None
        for p in presets:
            if not isinstance(p, dict):
                return None
            if not isinstance(p.get("id"), str) or not p["id"].strip():
                return None
            if not _is_polyglot_or_string(p.get("prompt")):
                return None
    return payload


def _drop_legacy_cache_key(settings) -> None:







    if _legacy_cache["checked"]:
        return
    _legacy_cache["checked"] = True
    try:
        if settings.contains(_LEGACY_CACHE_KEY):
            settings.remove(_LEGACY_CACHE_KEY)
            settings.remove(_LEGACY_CACHE_TS_KEY)
            log_debug("prompt_presets_client: dropped the pre-rename catalog key")
    except Exception as err:  # noqa: BLE001
        log_warning(f"Failed to drop the legacy preset cache key: {err}")







_memo_lock = threading.Lock()
_memo_generation = 0


def _seed_prompt_presets_memo(catalog: dict | None, expected_generation: int | None = None) -> None:





    global _memo_generation
    with _memo_lock:
        if expected_generation is not None and expected_generation != _memo_generation:
            log_debug("prompt_presets_client: fresher catalog already in the memo")
            return
        _memo_generation += 1
        try:
            from . import prompt_presets as _pp

            _pp.seed_catalog_memo(catalog)
        except Exception:  # pragma: no cover  # nosec B110
            pass


def _clear_prompt_presets_memo() -> None:


    global _memo_generation
    with _memo_lock:
        _memo_generation += 1
        try:
            from . import prompt_presets as _pp

            _pp.invalidate_catalog_memo()
        except Exception:  # pragma: no cover  # nosec B110
            pass


def _read_cache_raw() -> tuple[dict | None, float | None]:



    if not _legacy_cache["checked"]:
        try:
            from qgis.PyQt.QtCore import QSettings

            _drop_legacy_cache_key(QSettings())
        except Exception as err:  # noqa: BLE001
            log_warning(f"Legacy preset cache check failed: {err}")
    cache_blob_file.migrate_settings_key(_CACHE_FILE, _CACHE_KEY, (_CACHE_TS_KEY,))
    raw = cache_blob_file.read_cache_text(_CACHE_FILE)
    if not raw:
        return None, None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        return None, None
    catalog = _validate_catalog(parsed)
    if catalog is None:
        return None, None
    return catalog, cache_blob_file.cache_file_age_s(_CACHE_FILE)


def _read_cache() -> dict | None:

    catalog, age = _read_cache_raw()
    if catalog is None or age is None or age > _presets_cache_ttl():
        return None
    return catalog


def read_cached_catalog_stale_ok() -> dict | None:












    generation = _memo_generation
    catalog, _ = _read_cache_raw()
    _seed_prompt_presets_memo(catalog, expected_generation=generation)
    return catalog


def _write_cache(catalog: dict) -> None:





    try:
        cache_blob_file.migrate_settings_key(_CACHE_FILE, _CACHE_KEY, (_CACHE_TS_KEY,))
        cache_blob_file.write_cache_text(
            _CACHE_FILE, json.dumps(catalog), touch_if_unchanged=True
        )
    except Exception as err:  # noqa: BLE001
        log_warning(f"Failed to persist preset cache: {err}")




    _seed_prompt_presets_memo(catalog)


def invalidate_cache() -> None:






    try:
        cache_blob_file.remove_cache_file(_CACHE_FILE)
        from qgis.PyQt.QtCore import QSettings
        settings = QSettings()
        if settings.contains(_CACHE_KEY):
            settings.remove(_CACHE_KEY)
            settings.remove(_CACHE_TS_KEY)
    except Exception as err:  # noqa: BLE001
        log_warning(f"Failed to clear preset cache: {err}")
    _clear_prompt_presets_memo()


def fetch_server_catalog(client, force_refresh: bool = False) -> dict | None:







    if not force_refresh:
        cached = _read_cache()
        if cached is not None:
            log_debug("prompt_presets_client: returning cached catalog")
            return cached

    try:
        resp = client._request(
            "GET",
            "/api/ai-edit/presets",
            timeout_ms=get_export_dial(
                "pipeline.prompt_presets_client.catalog_fetch_timeout_ms", _CATALOG_FETCH_TIMEOUT_MS
            ),
        )
    except Exception as err:  # noqa: BLE001
        log_warning(f"Failed to fetch server catalog: {type(err).__name__}")
        return None

    if not isinstance(resp, dict) or "error" in resp:
        log_warning("Server catalog fetch returned an unsuccessful response")
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



    catalog = _validate_catalog(payload)
    if catalog is None:
        return None
    _write_cache(catalog)
    return catalog


def absolute_demo_url(client, relative: str) -> str:






    if not isinstance(relative, str) or not relative:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in relative):
        return ""
    try:
        parsed = urlsplit(relative)
        if parsed.scheme:
            if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or parsed.username:
                return ""
            return relative
        if parsed.netloc or relative.startswith("\\"):
            return ""
    except ValueError:
        return ""
    base = client.base_url.rstrip("/")
    return base + (relative if relative.startswith("/") else "/" + relative)
