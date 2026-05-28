






from __future__ import annotations

import hashlib
import threading
import uuid

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import QSysInfo


_SETTINGS_KEY = "AIEdit/device_seed"


_HASH_LEN = 16

_cached: str | None = None
_cache_lock = threading.Lock()


def _machine_seed(settings) -> bytes:





    try:
        raw = bytes(QSysInfo.machineUniqueId())
        if raw:
            return raw
    except Exception:  # nosec B110
        pass

    seed = settings.value(_SETTINGS_KEY, "", type=str)
    if not seed:
        seed = uuid.uuid4().hex
        settings.setValue(_SETTINGS_KEY, seed)
    return seed.encode("utf-8")


def get_device_hash(settings=None) -> str:




    global _cached
    with _cache_lock:
        if _cached is not None:
            return _cached
        s = settings if settings is not None else QgsSettings()
        digest = hashlib.sha256(_machine_seed(s)).hexdigest()
        _cached = digest[:_HASH_LEN]
        return _cached



_PLATFORM_MAX_LEN = 48


def get_device_platform() -> str:





    try:
        name = QSysInfo.prettyProductName() or ""
    except Exception:  # nosec B110
        name = ""
    if not isinstance(name, str):
        return ""
    return " ".join(name.split())[:_PLATFORM_MAX_LEN]
