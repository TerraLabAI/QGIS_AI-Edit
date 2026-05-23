





from __future__ import annotations

import json
import threading
import time

from ..auth.activation_manager import SETTINGS_PREFIX
from ..config_store import get_export_dial

_RECENT_KEY = f"{SETTINGS_PREFIX}prompt_history"
_FAVORITES_KEY = f"{SETTINGS_PREFIX}favorite_prompts"





_RECENT_CAP = 500
_history_lock = threading.RLock()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize(prompt: str) -> str:

    return prompt.strip() if isinstance(prompt, str) else ""


def _settings():


    try:
        from qgis.core import QgsSettings
    except ImportError:
        return None
    return QgsSettings()


def _load_entries(key: str) -> list[dict]:
    settings = _settings()
    raw = settings.value(key, "") if settings is not None else ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return []
    if not isinstance(data, list):
        return []
    cap = get_export_dial("history.recent_cap", _RECENT_CAP) if key == _RECENT_KEY else None
    entries = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        prompt = _normalize(item.get("prompt"))
        if not prompt or prompt in seen:
            continue
        seen.add(prompt)
        entry = dict(item)
        entry["prompt"] = prompt
        for field in ("label", "source_category", "ts"):
            if field in entry and not isinstance(entry[field], str):
                entry[field] = None if field != "ts" else ""
        entries.append(entry)
        if cap is not None and len(entries) >= cap:
            break
    return entries


def _save_entries(key: str, entries: list[dict]) -> None:
    settings = _settings()
    if settings is None:
        return
    text = json.dumps(entries, ensure_ascii=False)
    if settings.value(key, "") != text:
        settings.setValue(key, text)






def get_recent() -> list[dict]:

    return _load_entries(_RECENT_KEY)


def add_recent(prompt: str) -> None:

    with _history_lock:
        text = _normalize(prompt)
        if not text:
            return
        entries = [e for e in get_recent() if _normalize(e.get("prompt", "")) != text]
        entries.insert(0, {"prompt": text, "ts": _now_iso()})
        cap = get_export_dial("history.recent_cap", _RECENT_CAP)
        if len(entries) > cap:
            entries = entries[:cap]
        _save_entries(_RECENT_KEY, entries)






def get_favorites() -> list[dict]:

    return _load_entries(_FAVORITES_KEY)


def is_favorite(prompt: str) -> bool:
    text = _normalize(prompt)
    if not text:
        return False
    return any(_normalize(e.get("prompt", "")) == text for e in get_favorites())


def toggle_favorite(
    prompt: str,
    label: str | None = None,
    source_category: str | None = None,
) -> bool:

    with _history_lock:
        text = _normalize(prompt)
        if not text:
            return False
        entries = get_favorites()
        existing_idx = next(
            (i for i, e in enumerate(entries) if _normalize(e.get("prompt", "")) == text),
            None,
        )
        if existing_idx is not None:
            entries.pop(existing_idx)
            _save_entries(_FAVORITES_KEY, entries)
            return False
        entries.insert(0, {
            "prompt": text,
            "label": label or None,
            "source_category": source_category or None,
            "ts": _now_iso(),
        })
        _save_entries(_FAVORITES_KEY, entries)
        return True
