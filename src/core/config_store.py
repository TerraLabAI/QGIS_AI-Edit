from __future__ import annotations

import html
import math
import re
from copy import deepcopy
from typing import Any


class ConfigStore:


    def __init__(self):
        self._server_export_config: dict | None = None
        self._activation_config: dict | None = None
        self._telemetry_collector: Any = None

    def set_server_export_config(self, config: dict) -> None:
        if isinstance(config, dict):
            self._server_export_config = deepcopy(config)

    def get_server_export_config(self) -> dict | None:
        return self._server_export_config

    def has_server_export_config(self) -> bool:
        return self._server_export_config is not None

    def set_activation_config(self, config: dict) -> None:


        if not isinstance(config, dict) or not config:
            return
        self._activation_config = deepcopy(config)

    def get_activation_config(self) -> dict | None:












        return self._activation_config

    def clear_activation_config(self) -> None:
        self._activation_config = None

    def set_telemetry_collector(self, collector: Any) -> None:
        self._telemetry_collector = collector

    def clear(self) -> None:
        self._server_export_config = None
        self._activation_config = None
        collector = self._telemetry_collector
        self._telemetry_collector = None
        if collector is not None:
            shutdown = getattr(collector, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:  # nosec B110
                    pass
        self._telemetry_collector = None


_store: ConfigStore | None = None


def set_store(store: ConfigStore | None) -> None:
    global _store
    _store = store


def get_store() -> ConfigStore | None:
    return _store










def _read_export_value(path: str) -> Any:

    store = _store
    cfg = store.get_server_export_config() if store is not None else None
    if not cfg:
        return None
    val: Any = cfg
    for part in path.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val






_MAX_DIAL_ENTRIES = 200
_MAX_DIAL_ENTRY_CHARS = 200


def get_export_dial(path: str, fallback):


    try:
        val = _read_export_value(path)
        if (
            isinstance(val, bool)
            or not isinstance(val, (int, float))
            or not math.isfinite(val)
            or val <= 0
        ):
            return fallback
        coerced = type(fallback)(val)


        if coerced <= 0:
            return fallback
        return coerced
    except Exception:  # nosec B110
        return fallback


def get_export_dial_pair(path: str, fallback: tuple[float, float]) -> tuple[float, float]:


    try:
        val = _read_export_value(path)
        if isinstance(val, (list, tuple)) and len(val) == 2:
            lo, hi = val
            if (
                not isinstance(lo, bool)
                and not isinstance(hi, bool)
                and isinstance(lo, (int, float))
                and isinstance(hi, (int, float))
                and math.isfinite(lo)
                and math.isfinite(hi)
                and 0 < lo <= hi
            ):
                return (float(lo), float(hi))
    except Exception:  # nosec B110
        pass
    return fallback


def get_export_dial_list(path: str, base=(), extra_only: bool = True, normalize=None) -> frozenset:








    merged = set(base)
    if not extra_only:
        return frozenset(merged)
    try:
        val = _read_export_value(path)
        if isinstance(val, (list, tuple)):
            for item in val[:_MAX_DIAL_ENTRIES]:
                if not isinstance(item, str):
                    continue
                entry = item[:_MAX_DIAL_ENTRY_CHARS].strip()
                if entry:
                    merged.add(normalize(entry) if normalize else entry)
    except Exception:  # nosec B110
        pass
    return frozenset(merged)


def get_export_dial_seq(
    path: str,
    base=(),
    max_len: int = 24,
    require_base_overlap: bool = False,
) -> tuple[str, ...]:











    merged = list(base)
    seen = set(merged)
    try:
        val = _read_export_value(path)
        if not isinstance(val, (list, tuple)):
            return tuple(merged)
        cleaned = []
        for item in val[:_MAX_DIAL_ENTRIES]:
            if not isinstance(item, str):
                continue
            entry = item[:_MAX_DIAL_ENTRY_CHARS].strip()
            if entry:
                cleaned.append(entry)
        if require_base_overlap and not any(entry in seen for entry in cleaned):
            return tuple(merged)
        for entry in cleaned:
            if len(merged) >= max_len:
                break
            if entry not in seen:
                seen.add(entry)
                merged.append(entry)
    except Exception:  # nosec B110
        return tuple(base)
    return tuple(merged)


def get_export_dial_objs(path: str, max_len: int = 40) -> tuple[dict, ...]:








    try:
        val = _read_export_value(path)
        if not isinstance(val, (list, tuple)):
            return ()
        return tuple(item for item in val[:max_len] if isinstance(item, dict))
    except Exception:  # nosec B110
        return ()


def get_export_dial_ratio(path: str, fallback: float) -> float:





    value = get_export_dial(path, fallback)
    return value if 0 < value <= 1 else fallback


def get_export_dial_str(path: str, fallback: str, allowed=None) -> str:


    try:
        val = _read_export_value(path)
        if isinstance(val, str):
            val = val.strip()
            if val and (allowed is None or val in allowed):
                return val
    except Exception:  # nosec B110
        pass
    return fallback


















_MAX_COPY_CHARS = 400


_COPY_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f<>]")


def _clean_served_text(value: Any, max_chars: int) -> str | None:


    if not isinstance(value, str):
        return None
    text = _COPY_CTRL_RE.sub("", value[:max_chars]).strip()
    return text or None


def get_export_text(container_path: str, key: str, max_chars: int = _MAX_COPY_CHARS) -> str | None:



    try:
        container = _read_export_value(container_path)
        if isinstance(container, dict):
            return _clean_served_text(container.get(key), max_chars)
    except Exception:  # nosec B110
        pass
    return None


def get_export_copy(
    string_id: str,
    fallback: str,
    max_chars: int = _MAX_COPY_CHARS,
    escape: bool = False,
) -> str:








    served = get_export_text("copy", string_id, max_chars)
    if served is None:
        return fallback
    return html.escape(served, quote=False) if escape else served


def get_activation_copy(
    string_id: str,
    fallback: str,
    max_chars: int = _MAX_COPY_CHARS,
    escape: bool = False,
) -> str:




    try:
        store = get_store()
        config = store.get_activation_config() if store is not None else None
        container = config.get("copy") if isinstance(config, dict) else None
        served = _clean_served_text(container.get(string_id), max_chars) if isinstance(container, dict) else None
    except Exception:  # nosec B110
        served = None
    if served is None:
        return fallback
    return html.escape(served, quote=False) if escape else served


def get_export_block(path: str) -> dict | None:




    try:
        val = _read_export_value(path)
        return val if isinstance(val, dict) else None
    except Exception:  # nosec B110
        return None


class ServerDialSet(frozenset):





    def __new__(cls, cfg_key: str, base, normalize=None):
        obj = super().__new__(cls, base)
        obj._cfg_key = cfg_key
        obj._normalize = normalize
        return obj

    def __contains__(self, item) -> bool:
        if frozenset.__contains__(self, item):
            return True
        return item in get_export_dial_list(self._cfg_key, (), normalize=self._normalize)

    def __eq__(self, other):






        if isinstance(other, ServerDialSet):
            return (
                self._cfg_key == other._cfg_key
                and self._normalize == other._normalize
                and frozenset.__eq__(self, other)
            )
        return NotImplemented

    def __ne__(self, other):

        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = frozenset.__hash__


class ServerDialMap(dict):




    def __init__(self, cfg_key: str, defaults: dict):
        super().__init__(defaults)
        self._cfg_key = cfg_key

    def __eq__(self, other):






        if isinstance(other, ServerDialMap):
            return self._cfg_key == other._cfg_key and dict.__eq__(self, other)
        return NotImplemented

    def __ne__(self, other):

        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __getitem__(self, key):
        return get_export_dial(f"{self._cfg_key}.{key}", dict.__getitem__(self, key))

    def get(self, key, default=None):
        if key in self:
            return self[key]
        return default
