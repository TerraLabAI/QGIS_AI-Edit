from __future__ import annotations

import time
from typing import Any


class _TTLValue:
    __slots__ = ("value", "set_at", "ttl_s")

    def __init__(self, value: Any, ttl_s: float):
        self.value = value
        self.set_at = time.monotonic()
        self.ttl_s = ttl_s

    def is_fresh(self) -> bool:
        return (time.monotonic() - self.set_at) < self.ttl_s


_DEFAULT_TTL_S = 30 * 60


class ConfigStore:
    """One per plugin instance. Cleared on unload to avoid Plugin Reloader leaks."""

    def __init__(self):
        self._server_export_config: dict | None = None
        self._activation_config: _TTLValue | None = None
        self._telemetry_collector: Any = None

    def set_server_export_config(self, config: dict) -> None:
        self._server_export_config = config

    def get_server_export_config(self) -> dict | None:
        return self._server_export_config

    def has_server_export_config(self) -> bool:
        return self._server_export_config is not None

    def clear_server_export_config(self) -> None:
        self._server_export_config = None

    def set_activation_config(self, config: dict, ttl_s: float = _DEFAULT_TTL_S) -> None:
        self._activation_config = _TTLValue(config, ttl_s)

    def get_activation_config(self) -> dict | None:
        if self._activation_config is None:
            return None
        if not self._activation_config.is_fresh():
            self._activation_config = None
            return None
        return self._activation_config.value

    def clear_activation_config(self) -> None:
        self._activation_config = None

    def set_telemetry_collector(self, collector: Any) -> None:
        self._telemetry_collector = collector

    def get_telemetry_collector(self) -> Any:
        return self._telemetry_collector

    def clear(self) -> None:
        self._server_export_config = None
        self._activation_config = None
        if self._telemetry_collector is not None:
            shutdown = getattr(self._telemetry_collector, "shutdown", None)
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
