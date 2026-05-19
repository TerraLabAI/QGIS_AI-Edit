"""Local prompt history + favorites, persisted via QSettings.

Stores the user's successfully generated prompts (Recent) and any prompts
they've starred (Favorites). Both lists are local-only - nothing leaves the
machine. Recent is uncapped (per design D4); Qt handles thousands of entries
fine in a scroll area.
"""
from __future__ import annotations

import json
import time

from qgis.core import QgsSettings

from .activation_manager import SETTINGS_PREFIX

_RECENT_KEY = f"{SETTINGS_PREFIX}prompt_history"
_FAVORITES_KEY = f"{SETTINGS_PREFIX}favorite_prompts"

# Hard cap on locally-stored Recent entries. The list is serialised as a
# single JSON blob in QSettings and rewritten on every Generate, so an
# uncapped list balloons settings I/O and slows the library open. 500 is
# well past anyone's "recently used" memory and still loads instantly.
_RECENT_CAP = 500


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _normalize(prompt: str) -> str:
    """Dedupe key. Whitespace-trim only; case is preserved."""
    return (prompt or "").strip()


def _load(key: str) -> list[dict]:
    raw = QgsSettings().value(key, "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return data if isinstance(data, list) else []


def _save(key: str, entries: list[dict]) -> None:
    QgsSettings().setValue(key, json.dumps(entries, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Recent
# ---------------------------------------------------------------------------

def get_recent() -> list[dict]:
    """Return Recent entries, newest first. Each: {prompt, ts}."""
    return _load(_RECENT_KEY)


def add_recent(prompt: str) -> None:
    """Append a prompt to Recent, deduped + newest-first, capped to _RECENT_CAP."""
    text = _normalize(prompt)
    if not text:
        return
    entries = [e for e in get_recent() if _normalize(e.get("prompt", "")) != text]
    entries.insert(0, {"prompt": text, "ts": _now_iso()})
    if len(entries) > _RECENT_CAP:
        entries = entries[:_RECENT_CAP]
    _save(_RECENT_KEY, entries)


def clear_recent() -> None:
    _save(_RECENT_KEY, [])


def replace_recent(entries: list[dict]) -> None:
    """Overwrite local Recent cache with server data. Newest first, deduped, capped."""
    seen: set[str] = set()
    normalized: list[dict] = []
    for e in entries:
        prompt = _normalize(e.get("prompt") or "")
        if not prompt or prompt in seen:
            continue
        seen.add(prompt)
        normalized.append({
            "prompt": prompt,
            "ts": e.get("ts") or _now_iso(),
        })
        if len(normalized) >= _RECENT_CAP:
            break
    _save(_RECENT_KEY, normalized)


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------

def get_favorites() -> list[dict]:
    """Return Favorites, newest-starred first. Each: {prompt, label, source_category, ts}."""
    return _load(_FAVORITES_KEY)


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
    """Star/unstar a prompt. Returns the new favorite state (True = now favorited)."""
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
        _save(_FAVORITES_KEY, entries)
        return False
    entries.insert(0, {
        "prompt": text,
        "label": label or None,
        "source_category": source_category or None,
        "ts": _now_iso(),
    })
    _save(_FAVORITES_KEY, entries)
    return True


def replace_favorites(entries: list[dict]) -> None:
    """Overwrite local Favorites cache with server data."""
    seen: set[str] = set()
    normalized: list[dict] = []
    for e in entries:
        prompt = _normalize(e.get("prompt") or "")
        if not prompt or prompt in seen:
            continue
        seen.add(prompt)
        normalized.append({
            "prompt": prompt,
            "label": e.get("label") or None,
            "source_category": e.get("source_category") or None,
            "ts": e.get("ts") or _now_iso(),
        })
    _save(_FAVORITES_KEY, normalized)
