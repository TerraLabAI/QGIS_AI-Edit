"""Persistent disk cache of the user's generation jobs (Recent + Favorites).

The prompt library renders from this cache the instant it opens, then refreshes
in the background. Without it the first open of a session has nothing to show
until the network round-trip returns, which reads as a "3 then 6" pop-in as the
local prompt favorites render first and the server jobs arrive seconds later.

Server data stays the source of truth; this is a warm-start cache only. The
two job lists are JSON files in the QGIS profile (see cache_blob_file), written
only when they change; the small per-generation tables stay in QgsSettings.
"""
from __future__ import annotations

import json
import threading
from itertools import islice

from qgis.core import QgsSettings

from ..auth.activation_manager import SETTINGS_PREFIX
from ..config_store import get_export_dial
from ..logger import log_warning
from . import cache_blob_file

_RECENT_JOBS_KEY = f"{SETTINGS_PREFIX}library_recent_jobs"
_FAVORITE_JOBS_KEY = f"{SETTINGS_PREFIX}library_favorite_jobs"
_ZONE_POLYGON_KEY = f"{SETTINGS_PREFIX}zone_polygons"
_OUTPUT_PATHS_KEY = f"{SETTINGS_PREFIX}output_paths"

# Cache files that replaced the two job-list settings keys above.
_JOBS_FILES = {
    _RECENT_JOBS_KEY: "library_recent_jobs.json",
    _FAVORITE_JOBS_KEY: "library_favorite_jobs.json",
}

# Matches the server fetch limit: caching more than we ever fetch is wasted I/O.
_JOBS_CAP = 50

# Same order of magnitude as _JOBS_CAP: this is a companion table for the
# same window of recent generations, not a long-term archive.
_ZONE_POLYGON_CAP = 50

# Companion table for the same recent-generation window as the polygons.
_OUTPUT_PATHS_CAP = 50

_companion_lock = threading.RLock()


def _load_jobs(key: str) -> list[dict]:
    name = _JOBS_FILES[key]
    cache_blob_file.migrate_settings_key(name, key)
    raw = cache_blob_file.read_cache_text(name)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return []
    if not isinstance(data, list):
        return []
    cap = get_export_dial("history.jobs_cap", _JOBS_CAP)
    return list(islice((j for j in data if isinstance(j, dict)), cap))


def _save_jobs(key: str, jobs: list[dict]) -> None:
    cap = get_export_dial("history.jobs_cap", _JOBS_CAP)
    capped = list(islice((j for j in (jobs or []) if isinstance(j, dict)), cap))
    name = _JOBS_FILES[key]
    # A save before any read still has to move the old key out of the ini.
    cache_blob_file.migrate_settings_key(name, key)
    # Unchanged lists (most refreshes) skip the disk write.
    try:
        text = json.dumps(capped, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as err:
        log_warning(f"History cache serialization failed: {type(err).__name__}")
        return
    cache_blob_file.write_cache_text(name, text)


def _load_dict(key: str, settings=None) -> dict:
    # `settings` lets a read-then-write pair share one QgsSettings; building
    # one costs ~89 us against a real profile, reading off it ~1 us.
    try:
        source = settings if settings is not None else QgsSettings()
        raw = source.value(key, "")
        data = json.loads(raw) if raw else {}
    except (ValueError, TypeError, RecursionError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def get_recent_jobs() -> list[dict]:
    return _load_jobs(_RECENT_JOBS_KEY)


def get_favorite_jobs() -> list[dict]:
    return _load_jobs(_FAVORITE_JOBS_KEY)


def save_recent_jobs(jobs: list[dict]) -> None:
    _save_jobs(_RECENT_JOBS_KEY, jobs)


def save_favorite_jobs(jobs: list[dict]) -> None:
    _save_jobs(_FAVORITE_JOBS_KEY, jobs)


def _save_companion(key: str, request_id: str, entry: dict, cap: int) -> None:
    with _companion_lock:
        settings = QgsSettings()
        data = _load_dict(key, settings)
        before = dict(data)
        # Re-saving an older generation makes it recent for eviction too.
        data.pop(request_id, None)
        data[request_id] = entry
        for old_key in list(data)[:max(0, len(data) - cap)]:
            data.pop(old_key, None)
        if list(before.items()) != list(data.items()):
            settings.setValue(key, json.dumps(data, ensure_ascii=False))


def save_zone_polygon(request_id: str, wkt: str, crs_authid: str) -> None:
    """Remember a generation's native polygon locally, newest saves last."""
    if not isinstance(request_id, str) or not request_id or not isinstance(wkt, str) or not wkt:
        return
    entry = {"wkt": wkt, "crs_authid": crs_authid if isinstance(crs_authid, str) else ""}
    _save_companion(
        _ZONE_POLYGON_KEY, request_id, entry,
        get_export_dial("history.zone_polygon_cap", _ZONE_POLYGON_CAP),
    )


def get_zone_polygon(request_id: str) -> dict | None:
    """Return {"wkt": ..., "crs_authid": ...} for ``request_id``, or None when
    this machine never stored one (bbox-only history, spec section 7)."""
    if not isinstance(request_id, str) or not request_id:
        return None
    entry = _load_dict(_ZONE_POLYGON_KEY).get(request_id)
    if isinstance(entry, dict) and isinstance(entry.get("wkt"), str) and entry["wkt"]:
        crs_authid = entry.get("crs_authid")
        return {
            "wkt": entry["wkt"],
            "crs_authid": crs_authid if isinstance(crs_authid, str) else "",
        }
    return None


def save_output_paths(request_id: str, path: str, before_path: str = "") -> None:
    """Remember where ``request_id``'s GeoTIFF (and its before sidecar) landed
    on THIS machine's disk. Version browsing re-adds the layer from here with
    zero network; a deleted file just falls back to the download path. Local
    only on purpose: paths never ride a server payload (telemetry rules ban
    them too), so another machine simply has no entry and downloads."""
    if not isinstance(request_id, str) or not request_id or not isinstance(path, str) or not path:
        return
    entry = {"path": path, "before_path": before_path if isinstance(before_path, str) else ""}
    _save_companion(
        _OUTPUT_PATHS_KEY, request_id, entry,
        get_export_dial("history.output_path_cap", _OUTPUT_PATHS_CAP),
    )


def get_output_paths(request_id: str) -> dict | None:
    """Return {"path": ..., "before_path": ...} recorded for ``request_id``,
    or None when this machine never wrote its GeoTIFF."""
    if not isinstance(request_id, str) or not request_id:
        return None
    entry = _load_dict(_OUTPUT_PATHS_KEY).get(request_id)
    if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"]:
        before = entry.get("before_path")
        return {"path": entry["path"], "before_path": before if isinstance(before, str) else ""}
    return None


def save_before_path(request_id: str, before_path: str) -> None:
    """Attach ``before_path`` (an archived-input sidecar downloaded on its
    own, e.g. the session-base snapshot) to ``request_id``'s existing entry.
    No-op when the output GeoTIFF itself was never written on this machine:
    entries are keyed on the output path, and inventing one would corrupt the
    version-browsing fast path."""
    if not isinstance(before_path, str) or not before_path:
        return
    with _companion_lock:
        entry = get_output_paths(request_id)
        if entry:
            save_output_paths(request_id, entry["path"], before_path)


def clear() -> None:
    for name in _JOBS_FILES.values():
        cache_blob_file.remove_cache_file(name)
    s = QgsSettings()
    s.remove(_RECENT_JOBS_KEY)
    s.remove(_FAVORITE_JOBS_KEY)
    s.remove(_ZONE_POLYGON_KEY)
    s.remove(_OUTPUT_PATHS_KEY)
