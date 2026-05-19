









from __future__ import annotations

import hashlib
import tempfile
import time
from collections import deque
from pathlib import Path

from qgis.core import QgsNetworkAccessManager
from qgis.PyQt.QtCore import QByteArray, QObject, QStandardPaths, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from ..api.network_response import transfer_timeout, valid_http_url
from ..core.config_store import get_export_dial
from ..core.logger import log_debug, log_warning
from ..core.output_paths import replace_with_retry
from ..core.qt_compat import (
    CacheLocation,
    HttpStatusCodeAttribute,
    NetworkNoError,
    NoLessSafeRedirectPolicy,
    RedirectPolicyAttribute,
    safe_single_shot,
    set_transfer_timeout,
)




_KNOWN_MISSING: dict[str, float] = {}





_CACHE_DIR_PREFIX = "ai-edit-template-demos-v"
_CACHE_DIR_FALLBACK_VERSION = "4"


_FETCH_TIMEOUT_MS = 15_000

_MIN_IMAGE_BYTES = 256





_MAX_CONCURRENT_FETCHES = 3


def _cache_dir_name() -> str:





    from ..core.auth.activation_manager import get_server_config

    version = get_server_config().get("demo_cache_version")
    if isinstance(version, bool):
        version = None
    if isinstance(version, int):
        version = str(version)
    if (
        not isinstance(version, str)
        or not version.strip()
        or len(version) > 64
        or not all(c.isalnum() or c in "._-" for c in version.strip())
    ):
        version = _CACHE_DIR_FALLBACK_VERSION
    return f"{_CACHE_DIR_PREFIX}{version.strip()}"


def _cache_root() -> Path:









    name = _cache_dir_name()
    base = QStandardPaths.writableLocation(CacheLocation)
    if base:
        return Path(base) / name
    return Path.home() / ".cache" / name


def _cleanup_stale_cache_dirs(active: Path) -> None:

    import shutil

    try:
        for child in active.parent.iterdir():
            if (
                child.name.startswith(_CACHE_DIR_PREFIX)
                and child.name != active.name
                and not child.is_symlink()
                and child.is_dir()
            ):
                shutil.rmtree(child, ignore_errors=True)
    except OSError:
        pass  # nosec B110


def _cache_path(template_id: str, which: str) -> Path:
    def component(value):


        if (value and len(value) <= 100 and value.isascii()
                and all(c.isalnum() or c in "-_" for c in value)
                and value.upper() not in {"CON", "PRN", "AUX", "NUL"}
                and not (len(value) == 4 and value[:3].upper() in {"COM", "LPT"} and value[-1].isdigit())):
            return value
        return "key-" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    return _cache_root() / component(template_id) / f"{component(which)}.jpg"





_DEMO_CACHE_TTL_SECONDS = 7 * 24 * 3600


def _demo_cache_ttl() -> int:



    return get_export_dial("cache_ttl_s.demos", _DEMO_CACHE_TTL_SECONDS)


def read_cached_pixmap(template_id: str, which: str) -> QPixmap | None:

    path = _cache_path(template_id, which)
    try:
        if not path.is_file() or path.is_symlink():
            return None
        age = time.time() - path.stat().st_mtime
        if age < 0 or age > _demo_cache_ttl():
            return None
        pm = QPixmap(str(path))
        if pm.isNull() or pm.width() < 2 or pm.height() < 2:
            return None
        return pm
    except Exception as err:  # noqa: BLE001
        log_warning(f"Failed to read cached demo {path}: {err}")
        return None


class TemplateDemoLoader(QObject):










    loaded = pyqtSignal(str, str, QPixmap)
    failed = pyqtSignal(str, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._queue: deque[tuple[str, str, str]] = deque()
        self._pending: dict[tuple[str, str], str] = {}
        self._waiting: dict[str, list[tuple[str, str]]] = {}
        self._in_flight = 0


        try:
            root = _cache_root()
            root.mkdir(parents=True, exist_ok=True)
            _cleanup_stale_cache_dirs(root)
        except OSError as err:
            log_warning(f"Demo cache dir unavailable: {err}")

    def request(self, template_id: str, which: str, url: str) -> None:





        if not isinstance(template_id, str) or not isinstance(which, str) or not template_id or not which:
            return
        key = (template_id, which)
        if not valid_http_url(url):
            safe_single_shot(0, self, lambda: self.failed.emit(template_id, which))
            return
        if self._pending.get(key) == url:
            return
        now = time.monotonic()
        for missing, recorded in list(_KNOWN_MISSING.items()):
            if now - recorded >= _demo_cache_ttl():
                _KNOWN_MISSING.pop(missing, None)



        if url in _KNOWN_MISSING:
            safe_single_shot(0, self, lambda: self.failed.emit(template_id, which))
            return
        self._pending[key] = url



        safe_single_shot(
            0, self,
            lambda t=template_id, w=which, u=url: self._load_cached_or_fetch(t, w, u),
        )

    def _load_cached_or_fetch(self, template_id: str, which: str, url: str) -> None:
        key = (template_id, which)
        if self._pending.get(key) != url:
            return
        pm = read_cached_pixmap(template_id, which)
        if pm is not None:
            self._pending.pop(key, None)
            self.loaded.emit(template_id, which, pm)
            return
        if url in self._waiting:
            self._waiting[url].append(key)
            return
        self._waiting[url] = [key]
        self._queue.append((template_id, which, url))
        self._pump()

    def _pump(self) -> None:

        max_concurrent = get_export_dial(
            "widgets.template_demo_loader.max_concurrent_fetches", _MAX_CONCURRENT_FETCHES)
        max_concurrent = max(1, max_concurrent)
        while self._in_flight < max_concurrent and self._queue:
            template_id, which, url = self._queue.popleft()
            keys = self._waiting.get(url, [])
            if not any(self._pending.get(key) == url for key in keys):
                self._waiting.pop(url, None)
                continue
            self._in_flight += 1
            try:
                self._start(template_id, which, url)
            except (RuntimeError, ValueError, TypeError):
                self._in_flight = max(0, self._in_flight - 1)
                for key in self._waiting.pop(url, []):
                    if self._pending.get(key) == url:
                        self._pending.pop(key, None)
                        self.failed.emit(*key)

    def _start(self, template_id: str, which: str, url: str) -> None:
        req = QNetworkRequest(QUrl(url))


        req.setAttribute(RedirectPolicyAttribute, NoLessSafeRedirectPolicy)
        req.setRawHeader(b"Accept", b"image/jpeg, image/png, image/webp, image/*")
        set_transfer_timeout(
            req, transfer_timeout(
                get_export_dial("widgets.template_demo_loader.fetch_timeout_ms", _FETCH_TIMEOUT_MS),
                _FETCH_TIMEOUT_MS))




        reply = QgsNetworkAccessManager.instance().get(req)
        reply.setParent(self)
        reply.finished.connect(
            lambda r=reply, t=template_id, w=which, u=url: self._on_finished(r, t, w, u)
        )

    def _on_finished(self, reply: QNetworkReply, template_id: str, which: str, url: str) -> None:













        keys = [key for key in self._waiting.pop(url, []) if self._pending.get(key) == url]
        pixmap = None
        try:
            pixmap = self._pixmap_from_reply(reply, template_id, which, url, keys)
        except Exception as err:  # noqa: BLE001
            log_warning(f"Demo fetch handling failed for {template_id}/{which}: {err}")
        try:
            reply.deleteLater()
        except (RuntimeError, AttributeError):  # nosec B110
            pass
        self._in_flight = max(0, self._in_flight - 1)


        try:
            self._pump()
        except Exception as err:  # noqa: BLE001
            log_warning(f"Demo queue pump failed after {template_id}/{which}: {err}")
        for key in keys:
            if self._pending.get(key) != url:
                continue
            self._pending.pop(key, None)
            if pixmap is None:
                self.failed.emit(*key)
            else:
                self.loaded.emit(*key, pixmap)

    def _pixmap_from_reply(
        self, reply: QNetworkReply, template_id: str, which: str, url: str, keys: list
    ) -> QPixmap | None:

        err_code = reply.error()
        no_err = NetworkNoError
        http_status = reply.attribute(HttpStatusCodeAttribute)
        try:
            http_int = int(http_status) if http_status is not None else 0
        except (TypeError, ValueError):
            http_int = 0
        if err_code != no_err or http_int != 200:
            if http_int == 404:
                _KNOWN_MISSING[url] = time.monotonic()
            else:
                log_debug(
                    f"Demo fetch failed for {template_id}/{which}: "
                    f"err={err_code} http={http_int}"
                )
            return None
        data: QByteArray = reply.readAll()
        buf = bytes(data)
        if len(buf) < get_export_dial("widgets.template_demo_loader.min_image_bytes", _MIN_IMAGE_BYTES):
            return None
        pm = QPixmap()
        if not pm.loadFromData(buf) or pm.width() < 2 or pm.height() < 2:
            log_debug(f"Demo bytes did not decode for {template_id}/{which}")
            return None
        for key in keys:
            self._write_cache(*key, buf)
        return pm

    @staticmethod
    def _write_cache(template_id: str, which: str, buf: bytes) -> None:
        path = _cache_path(template_id, which)
        tmp = None
        try:



            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".demo-", suffix=".tmp", delete=False) as f:
                tmp = Path(f.name)
                f.write(buf)
            replace_with_retry(str(tmp), str(path))
        except OSError as err:
            log_warning(f"Failed to write demo cache {path}: {err}")
            try:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
            except OSError:
                pass  # nosec B110
