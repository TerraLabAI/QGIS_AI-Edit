
















from __future__ import annotations

import json
import math
import time
from typing import Any

from .logger import log_debug, log_warning
from .prompts import cache_blob_file

CONFIG_CACHE_FILE = "server_config_v1.json"

_FILE_FORMAT = 1


_MAX_FILE_CHARS = 4_000_000

CONFIG_KINDS = ("export", "plugin")
CONFIG_SCOPES = ("public", "account")


def config_cache_path() -> str | None:

    return cache_blob_file.cache_file_path(CONFIG_CACHE_FILE)


def _slot_name(kind: str, scope: str) -> str:
    return f"{kind}_{scope}"


def load_saved_configs() -> dict[str, dict]:



    raw = cache_blob_file.read_cache_text(CONFIG_CACHE_FILE)
    if not raw or len(raw) > _MAX_FILE_CHARS:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, RecursionError):
        log_warning("Saved server config unreadable; ignoring it")
        return {}
    if not isinstance(parsed, dict):
        return {}
    slots: dict[str, dict] = {}
    for kind in CONFIG_KINDS:
        for scope in CONFIG_SCOPES:
            name = _slot_name(kind, scope)
            slot = parsed.get(name)
            if not isinstance(slot, dict):
                continue
            config = slot.get("config")
            saved_at = slot.get("saved_at")
            if not isinstance(config, dict) or not config:
                continue
            if (
                isinstance(saved_at, bool)
                or not isinstance(saved_at, (int, float))
                or not math.isfinite(saved_at)
            ):
                saved_at = None
            slots[name] = {"config": config, "saved_at": saved_at}
    return slots


def write_saved_configs(slots: dict[str, dict]) -> bool:


    payload: dict[str, Any] = {"format": _FILE_FORMAT, "written_at": time.time()}
    for kind in CONFIG_KINDS:
        for scope in CONFIG_SCOPES:
            name = _slot_name(kind, scope)
            slot = slots.get(name)
            if isinstance(slot, dict) and isinstance(slot.get("config"), dict):
                payload[name] = {"config": slot["config"], "saved_at": slot.get("saved_at")}
    try:
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as err:
        log_warning(f"Server config not saved: {err}")
        return False
    ok = cache_blob_file.write_cache_text(CONFIG_CACHE_FILE, text)
    if ok:
        log_debug("Server config saved to disk")
    return ok


def drop_saved_account_configs() -> None:


    slots = load_saved_configs()
    if not any(name.endswith("_account") for name in slots):
        return
    write_saved_configs({name: slot for name, slot in slots.items() if not name.endswith("_account")})
