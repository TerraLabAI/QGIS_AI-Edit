"""Persistent disk cache of the user's generation jobs (Recent + Favorites).

The prompt library renders from this cache the instant it opens, then refreshes
in the background. Without it the first open of a session has nothing to show
until the network round-trip returns, which reads as a "3 then 6" pop-in as the
local prompt favorites render first and the server jobs arrive seconds later.

Server data stays the source of truth; this is a warm-start cache only. Stored
as a JSON blob in QgsSettings, same mechanism as prompt_history.
"""
from __future__ import annotations

import json

from qgis.core import QgsSettings

from ..auth.activation_manager import SETTINGS_PREFIX
from ..config_store import get_export_dial

_RECENT_JOBS_KEY = f"{SETTINGS_PREFIX}library_recent_jobs"
_FAVORITE_JOBS_KEY = f"{SETTINGS_PREFIX}library_favorite_jobs"
_ZONE_POLYGON_KEY = f"{SETTINGS_PREFIX}zone_polygons"
_OUTPUT_PATHS_KEY = f"{SETTINGS_PREFIX}output_paths"

# Matches the server fetch limit: caching more than we ever fetch is wasted I/O.
_JOBS_CAP = 50

# Same order of magnitude as _JOBS_CAP: this is a companion table for the
# same window of recent generations, not a long-term archive.
_ZONE_POLYGON_CAP = 50

# Companion table for the same recent-generation window as the polygons.
_OUTPUT_PATHS_CAP = 50


def _load_jobs(key: str) -> list[dict]:
    raw = QgsSettings().value(key, "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [j for j in data if isinstance(j, dict)] if isinstance(data, list) else []


def _save_jobs(key: str, jobs: list[dict]) -> None:
    cap = get_export_dial("history.jobs_cap", _JOBS_CAP)
    capped = [j for j in (jobs or []) if isinstance(j, dict)][:cap]
    QgsSettings().setValue(key, json.dumps(capped, ensure_ascii=False))


def _load_dict(key: str, settings=None) -> dict:
    # `settings` lets a read-then-write pair share one QgsSettings; building
    # one costs ~89 us against a real profile, reading off it ~1 us.
    raw = (settings or QgsSettings()).value(key, "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
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


def save_zone_polygon(request_id: str, wkt: str, crs_authid: str) -> None:
    """Remember the canvas-CRS polygon (spec section 7) that produced
    ``request_id``, purely on this machine.

    This is deliberately NOT part of the server-synced recent/favorite job
    dicts: the polygon never leaves the machine (D2), so the server can never
    echo it back in a history fetch. History restore instead looks it up here
    by request_id. A generation from another device, or one that aged out of
    the cap, simply has no entry here and restores as a plain bbox, which is
    the same additive fallback old history entries already get."""
    if not request_id or not wkt:
        return
    settings = QgsSettings()
    data = _load_dict(_ZONE_POLYGON_KEY, settings)
    data[request_id] = {"wkt": wkt, "crs_authid": crs_authid or ""}
    cap = get_export_dial("history.zone_polygon_cap", _ZONE_POLYGON_CAP)
    if len(data) > cap:
        for key in list(data.keys())[: len(data) - cap]:
            data.pop(key, None)
    settings.setValue(_ZONE_POLYGON_KEY, json.dumps(data, ensure_ascii=False))


def get_zone_polygon(request_id: str) -> dict | None:
    """Return {"wkt": ..., "crs_authid": ...} for ``request_id``, or None when
    this machine never stored one (bbox-only history, spec section 7)."""
    if not request_id:
        return None
    entry = _load_dict(_ZONE_POLYGON_KEY).get(request_id)
    if isinstance(entry, dict) and entry.get("wkt"):
        return entry
    return None


def save_output_paths(request_id: str, path: str, before_path: str = "") -> None:
    """Remember where ``request_id``'s GeoTIFF (and its before sidecar) landed
    on THIS machine's disk. Version browsing re-adds the layer from here with
    zero network; a deleted file just falls back to the download path. Local
    only on purpose: paths never ride a server payload (telemetry rules ban
    them too), so another machine simply has no entry and downloads."""
    if not request_id or not path:
        return
    settings = QgsSettings()
    data = _load_dict(_OUTPUT_PATHS_KEY, settings)
    data[request_id] = {"path": path, "before_path": before_path or ""}
    cap = get_export_dial("history.output_path_cap", _OUTPUT_PATHS_CAP)
    if len(data) > cap:
        for key in list(data.keys())[: len(data) - cap]:
            data.pop(key, None)
    settings.setValue(_OUTPUT_PATHS_KEY, json.dumps(data, ensure_ascii=False))


def get_output_paths(request_id: str) -> dict | None:
    """Return {"path": ..., "before_path": ...} recorded for ``request_id``,
    or None when this machine never wrote its GeoTIFF."""
    if not request_id:
        return None
    entry = _load_dict(_OUTPUT_PATHS_KEY).get(request_id)
    if isinstance(entry, dict) and entry.get("path"):
        return entry
    return None


def save_before_path(request_id: str, before_path: str) -> None:
    """Attach ``before_path`` (an archived-input sidecar downloaded on its
    own, e.g. the session-base snapshot) to ``request_id``'s existing entry.
    No-op when the output GeoTIFF itself was never written on this machine:
    entries are keyed on the output path, and inventing one would corrupt the
    version-browsing fast path."""
    if not before_path:
        return
    entry = get_output_paths(request_id)
    if not entry:
        return
    save_output_paths(request_id, entry.get("path") or "", before_path)


def clear() -> None:
    s = QgsSettings()
    s.remove(_RECENT_JOBS_KEY)
    s.remove(_FAVORITE_JOBS_KEY)
    s.remove(_ZONE_POLYGON_KEY)
    s.remove(_OUTPUT_PATHS_KEY)
