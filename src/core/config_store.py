from __future__ import annotations

import html
import math
import re
import time
from copy import deepcopy
from typing import Any




_NOT_ACCOUNT_AUTH_STATES = frozenset({"degraded", "public"})






_PUBLIC_OVERLAY_KEYS = (

    "features",

    "min_recommended_version",
    "latest_version",
    "release_notes_line",
    "update_message",
    "min_supported_version",
    "marketplace_url",
    "update_policy",

    "upgrade_url",
    "tutorial_url",
    "guide_url",
    "cross_promo_url",
    "sibling_links",
    "support_email",
    "dashboard_url",
    "subscribe_url",
    "dashboard_error_url",
    "subscribe_error_url",
    "wall_url",
    "prewall_url",
    "content_policy_url",
    "terms_url",
    "privacy_url",
    "generate_privacy_url",
    "blog_url",
    "branding_url",
    "contact_page_url",
    "contact_call_url",
)


def _auth_state_of(config: dict) -> str:
    value = config.get("auth_state") if isinstance(config, dict) else None
    return value.strip().lower() if isinstance(value, str) else ""


def _has_tuned_marker(config: dict) -> bool:
    marker = config.get("tuned_config") if isinstance(config, dict) else None
    return isinstance(marker, int) and not isinstance(marker, bool) and marker >= 1


def is_account_export_config(config: dict) -> bool:


    return _has_tuned_marker(config) and _auth_state_of(config) not in _NOT_ACCOUNT_AUTH_STATES


class ConfigStore:











    def __init__(self):

        self._slots: dict[str, dict] = {}
        self._disk_loaded = False
        self._telemetry_collector: Any = None



    @staticmethod
    def _saved_at(slot: dict) -> float:
        value = slot.get("saved_at")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
        return 0.0

    def _public_is_newer(self, kind: str) -> bool:
        account = self._slots.get(f"{kind}_account")
        public = self._slots.get(f"{kind}_public")
        return bool(account and public and self._saved_at(public) > self._saved_at(account))

    def _effective(self, kind: str) -> dict | None:








        account = self._slots.get(f"{kind}_account")
        public = self._slots.get(f"{kind}_public")
        if account and public and self._public_is_newer(kind):
            merged = dict(account["config"])
            newer = public["config"]
            for key in _PUBLIC_OVERLAY_KEYS:

                if key in newer:
                    merged[key] = newer[key]
            return merged
        slot = account or public
        return slot["config"] if slot else None

    def _effective_slot(self, kind: str) -> dict | None:


        if self._public_is_newer(kind):
            return self._slots.get(f"{kind}_public")
        return self._slots.get(f"{kind}_account") or self._slots.get(f"{kind}_public")

    def _put(self, kind: str, scope: str, config: dict, source: str) -> None:
        self._slots[f"{kind}_{scope}"] = {
            "config": deepcopy(config),
            "saved_at": time.time(),
            "source": source,
        }

    def _persist(self) -> None:
        from . import config_disk_cache

        config_disk_cache.write_saved_configs(
            {name: slot for name, slot in self._slots.items() if slot.get("config")}
        )

    def load_saved_config(self) -> bool:



        if self._disk_loaded:
            return False
        self._disk_loaded = True
        try:
            from . import config_disk_cache

            saved = config_disk_cache.load_saved_configs()
        except Exception:  # nosec B110
            return False
        loaded = False
        for name, slot in saved.items():
            if name in self._slots:
                continue
            self._slots[name] = {
                "config": slot["config"],
                "saved_at": slot.get("saved_at"),
                "source": "disk",
            }
            loaded = True
        return loaded

    def accept_fetched_export_config(self, config: dict) -> None:






        if not isinstance(config, dict) or not config:
            return
        scope = "account" if is_account_export_config(config) else "public"
        self._put("export", scope, config, "network")
        self._persist()

    def accept_fetched_plugin_config(self, config: dict, keyed: bool) -> None:


        if not isinstance(config, dict) or not config:
            return
        account = keyed and _auth_state_of(config) not in _NOT_ACCOUNT_AUTH_STATES
        self._put("plugin", "account" if account else "public", config, "network")
        self._persist()

    def clear_account_scope(self) -> None:

        had = any(name.endswith("_account") for name in self._slots)
        for name in [n for n in self._slots if n.endswith("_account")]:
            del self._slots[name]
        if had:
            self._persist()
        else:
            from . import config_disk_cache

            config_disk_cache.drop_saved_account_configs()

    def export_config_origin(self) -> tuple[str, int | None]:



        slot = self._effective_slot("export")
        if not slot:
            return "none", None
        saved_at = slot.get("saved_at")
        age = None
        if isinstance(saved_at, (int, float)) and not isinstance(saved_at, bool) and math.isfinite(saved_at):
            age = max(0, int(time.time() - saved_at))
        return str(slot.get("source") or "network"), age



    def set_server_export_config(self, config: dict) -> None:


        if isinstance(config, dict):
            self._slots.pop("export_public", None)
            self._slots.pop("export_account", None)
            scope = "account" if is_account_export_config(config) else "public"
            self._put("export", scope, config, "network")

    def get_server_export_config(self) -> dict | None:
        return self._effective("export")

    def has_server_export_config(self) -> bool:
        return self._effective("export") is not None

    def set_activation_config(self, config: dict) -> None:


        if not isinstance(config, dict) or not config:
            return
        self._slots.pop("plugin_public", None)
        self._slots.pop("plugin_account", None)
        self._put("plugin", "public", config, "network")

    def get_activation_config(self) -> dict | None:






        return self._effective("plugin")

    def clear_activation_config(self) -> None:
        self._slots.pop("plugin_public", None)
        self._slots.pop("plugin_account", None)

    def set_telemetry_collector(self, collector: Any) -> None:
        self._telemetry_collector = collector

    def clear(self) -> None:

        self._slots = {}
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


def ensure_saved_config_loaded() -> bool:


    store = _store
    if store is None:
        return False
    try:
        return store.load_saved_config()
    except Exception:  # nosec B110
        return False


def clear_saved_account_config() -> None:


    try:
        store = _store
        if store is not None:
            store.clear_account_scope()
        else:
            from . import config_disk_cache

            config_disk_cache.drop_saved_account_configs()
    except Exception:  # nosec B110
        pass


def get_config_origin() -> tuple[str, int | None]:

    store = _store
    if store is None:
        return "none", None
    return store.export_config_origin()


def get_config_schema() -> int | None:


    val = _read_export_value("config_schema")
    if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
        return val
    return None










class ConfigMissing(KeyError):


    def __init__(self, key: str, reason: str = "missing"):
        super().__init__(key)
        self.key = key
        self.reason = reason

    def __str__(self) -> str:
        return f"server config {self.reason}: {self.key}"


def require_dial(key: str, lo: float | None = None, hi: float | None = None):


    val = _read_export_value(key)
    if val is None:
        raise ConfigMissing(key)
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
        raise ConfigMissing(key, "invalid")
    if (lo is not None and val < lo) or (hi is not None and val > hi):
        raise ConfigMissing(key, "out_of_bounds")
    return val


def require_table(key: str):

    val = _read_export_value(key)
    if val is None:
        raise ConfigMissing(key)
    if not isinstance(val, (dict, list)) or not val:
        raise ConfigMissing(key, "invalid")
    return val


def require_str(key: str) -> str:

    val = _read_export_value(key)
    if val is None:
        raise ConfigMissing(key)
    if not isinstance(val, str) or not val.strip():
        raise ConfigMissing(key, "invalid")
    return val.strip()


def require_bool(key: str) -> bool:

    val = _read_export_value(key)
    if val is None:
        raise ConfigMissing(key)
    if not isinstance(val, bool):
        raise ConfigMissing(key, "invalid")
    return val


def has_keys(*keys: str) -> bool:


    try:
        return all(_read_export_value(key) is not None for key in keys)
    except Exception:  # nosec B110
        return False










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


def _served_copy_fits_locale() -> bool:



    try:
        from .request_context import request_lang

        return bool(request_lang())
    except Exception:  # nosec B110
        return True


def get_export_copy(
    string_id: str,
    fallback: str,
    max_chars: int = _MAX_COPY_CHARS,
    escape: bool = False,
) -> str:








    served = get_export_text("copy", string_id, max_chars) if _served_copy_fits_locale() else None
    if served is None:
        return fallback
    return html.escape(served, quote=False) if escape else served


_MAX_COPY_LIST_ITEMS = 20


def get_export_copy_list(string_id: str, max_chars: int = _MAX_COPY_CHARS) -> list[str] | None:



    if not _served_copy_fits_locale():
        return None
    try:
        container = _read_export_value("copy")
        value = container.get(string_id) if isinstance(container, dict) else None
    except Exception:  # nosec B110
        return None
    if not isinstance(value, list):
        return None
    lines = [_clean_served_text(item, max_chars) for item in value[:_MAX_COPY_LIST_ITEMS]]
    lines = [line for line in lines if line]
    return lines or None


def get_activation_copy(
    string_id: str,
    fallback: str,
    max_chars: int = _MAX_COPY_CHARS,
    escape: bool = False,
) -> str:




    if not _served_copy_fits_locale():
        return fallback
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
