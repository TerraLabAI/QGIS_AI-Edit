"""Large JSON caches kept as files in the QGIS profile, not in QSettings.

The QSettings ini backend has no partial write: every setValue anywhere in the
profile rewrites the whole shared QGIS3.ini, and two QGIS instances fight over
it. The template catalog (about 270 KB) and the history job lists used to live
there. Each now sits in its own file under
``<profile>/TerraLab/ai_edit_cache/``, written atomically (temp file, then
replace), and only when its content changed.

Safe from a worker thread: no QSettings on the write path, and one lock guards
the in-memory copy of what each file holds.
"""
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
# name -> text this process last read from or wrote to the file.
_known_text: dict[str, str] = {}
_known_stat: dict[str, tuple] = {}
# Settings keys already checked for a migration this session.
_migrated: set[tuple[str, str]] = set()


def cache_file_path(name: str) -> str | None:
    """Absolute path of cache file ``name``, or None without a QGIS profile."""
    if (
        not isinstance(name, str) or not name or name in (".", "..")
        or any(char in name for char in ("/", "\\", "\x00"))
        or os.path.isabs(name)
    ):
        return None
    try:
        from qgis.core import QgsApplication

        base = QgsApplication.qgisSettingsDirPath() or ""
    except Exception:  # noqa: BLE001 - no QGIS, no cache file
        return None
    if not base:
        return None
    return os.path.join(base, "TerraLab", _CACHE_DIR_NAME, name)


def _signature(stat) -> tuple:
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def read_cache_text(name: str) -> str | None:
    """File content, or None when the file is missing or unreadable."""
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
    """Seconds since the file was last written or confirmed current."""
    path = cache_file_path(name)
    if path is None:
        return None
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


def write_cache_text(name: str, text: str, touch_if_unchanged: bool = False) -> bool:
    """Atomically replace a cache, skipping an unchanged on-disk value.

    The file signature invalidates the memo after another QGIS instance writes
    it. One lock covers the comparison and replacement within this process.
    """
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
    """Move a settings blob once per profile; failed moves remain retryable."""
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
                    pass  # nosec B110 - invalid timestamps do not prevent migration.
            if isinstance(raw, str) and raw and not os.path.exists(path):
                if not write_cache_text(name, raw):
                    return stamp
                if stamp is not None:
                    try:
                        os.utime(path, (stamp, stamp))
                    except (OSError, ValueError, OverflowError):
                        pass  # nosec B110 - the file just reads as fresh.
            settings.remove(key)
            for extra in extra_keys:
                if settings.contains(extra):
                    settings.remove(extra)
            _migrated.add(migration)
            log_debug(f"moved settings key {key} to cache file {name}")
            return stamp
        except Exception as err:  # noqa: BLE001 - a cache migration never blocks.
            log_warning(f"cache migration for {name} failed: {err}")
            return None


def reset_cache_file_state() -> None:
    """Forget the in-memory copies and migration flags (tests)."""
    with _lock:
        _known_text.clear()
        _known_stat.clear()
        _migrated.clear()
