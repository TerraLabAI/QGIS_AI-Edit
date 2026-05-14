











from __future__ import annotations

import math
import os
import tempfile
import threading
import time

from ..logger import log_debug, log_warning
from ..output_paths import remove_with_retry, replace_with_retry

_CACHE_DIR_NAME = "ai_edit_cache"

_lock = threading.RLock()

_known_text: dict[str, str] = {}
_known_stat: dict[str, tuple] = {}

_migrated: set[tuple[str, str]] = set()


def cache_file_path(name: str) -> str | None:

    if (
        not isinstance(name, str) or not name or name in (".", "..")
        or any(char in name for char in ("/", "\\", "\x00"))
        or os.path.isabs(name)
    ):
        return None
    try:
        from qgis.core import QgsApplication

        base = QgsApplication.qgisSettingsDirPath() or ""
    except Exception:  # noqa: BLE001
        return None
    if not base:
        return None
    return os.path.join(base, "TerraLab", _CACHE_DIR_NAME, name)


def _signature(stat) -> tuple:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def read_cache_text(name: str) -> str | None:

    path = cache_file_path(name)
    if path is None:
        return None
    with _lock:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
                signature = _signature(os.fstat(f.fileno()))
        except (OSError, ValueError) as err:
            _known_text.pop(path, None)
            _known_stat.pop(path, None)
            if not isinstance(err, FileNotFoundError):
                log_warning(f"cache file {name} unreadable: {err}")
            return None
        _known_text[path] = text
        _known_stat[path] = signature
        return text


def cache_file_age_s(name: str) -> float | None:

    path = cache_file_path(name)
    if path is None:
        return None
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


def write_cache_text(name: str, text: str, touch_if_unchanged: bool = False) -> bool:





    path = cache_file_path(name)
    if path is None or not isinstance(text, str):
        return False
    with _lock:
        try:
            signature = _signature(os.stat(path))
        except OSError:
            signature = None
        known = _known_text.get(path) if signature == _known_stat.get(path) else None
        if known is None and signature is not None:
            known = read_cache_text(name)
        if known == text and signature is not None:
            if touch_if_unchanged:
                try:
                    os.utime(path, None)
                    _known_stat[path] = _signature(os.stat(path))
                except OSError as err:
                    log_debug(f"cache file {name} touch failed: {err}")
                    return False
            return True
        tmp = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", prefix=".cache-", suffix=".tmp",
                dir=os.path.dirname(path), delete=False,
            ) as f:
                tmp = f.name
                f.write(text)
            replace_with_retry(tmp, path)
            _known_text[path] = text
            _known_stat[path] = _signature(os.stat(path))
        except (OSError, ValueError) as err:
            if tmp is not None:
                remove_with_retry(tmp)
            _known_text.pop(path, None)
            _known_stat.pop(path, None)
            log_warning(f"cache file {name} not written: {err}")
            return False
        return True


def remove_cache_file(name: str) -> None:
    path = cache_file_path(name)
    with _lock:
        _known_text.pop(path, None)
        _known_stat.pop(path, None)
        if path is not None:
            remove_with_retry(path)


def migrate_settings_key(name: str, key: str, extra_keys: tuple = ()) -> float | None:

    path = cache_file_path(name)
    if path is None:
        return None
    migration = (path, key)
    with _lock:
        if migration in _migrated:
            return None
        try:
            from qgis.core import QgsSettings

            settings = QgsSettings()
            if not settings.contains(key):
                _migrated.add(migration)
                return None
            raw = settings.value(key, "")
            stamp = None
            if extra_keys and settings.contains(extra_keys[0]):
                try:
                    stamp = float(settings.value(extra_keys[0], ""))
                    if not math.isfinite(stamp) or stamp < 0:
                        stamp = None
                except (TypeError, ValueError, OverflowError):
                    pass  # nosec B110
            if isinstance(raw, str) and raw and not os.path.exists(path):
                if not write_cache_text(name, raw):
                    return stamp
                if stamp is not None:
                    try:
                        os.utime(path, (stamp, stamp))
                    except (OSError, ValueError, OverflowError):
                        pass  # nosec B110
            settings.remove(key)
            for extra in extra_keys:
                if settings.contains(extra):
                    settings.remove(extra)
            _migrated.add(migration)
            log_debug(f"moved settings key {key} to cache file {name}")
            return stamp
        except Exception as err:  # noqa: BLE001
            log_warning(f"cache migration for {name} failed: {err}")
            return None
