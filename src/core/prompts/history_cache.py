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

_RECENT_JOBS_KEY = f"{SETTINGS_PREFIX}library_recent_jobs"
_FAVORITE_JOBS_KEY = f"{SETTINGS_PREFIX}library_favorite_jobs"

# Matches the server fetch limit: caching more than we ever fetch is wasted I/O.
_JOBS_CAP = 50


def _load(key: str) -> list[dict]:
    raw = QgsSettings().value(key, "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return [j for j in data if isinstance(j, dict)] if isinstance(data, list) else []


def _save(key: str, jobs: list[dict]) -> None:
    capped = [j for j in (jobs or []) if isinstance(j, dict)][:_JOBS_CAP]
    QgsSettings().setValue(key, json.dumps(capped, ensure_ascii=False))


def get_recent_jobs() -> list[dict]:
    return _load(_RECENT_JOBS_KEY)


def get_favorite_jobs() -> list[dict]:
    return _load(_FAVORITE_JOBS_KEY)


def save_recent_jobs(jobs: list[dict]) -> None:
    _save(_RECENT_JOBS_KEY, jobs)


def save_favorite_jobs(jobs: list[dict]) -> None:
    _save(_FAVORITE_JOBS_KEY, jobs)


def clear() -> None:
    s = QgsSettings()
    s.remove(_RECENT_JOBS_KEY)
    s.remove(_FAVORITE_JOBS_KEY)
