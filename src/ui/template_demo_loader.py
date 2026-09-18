"""Async loader for the prompt-library before/after demo images.

Caches PNG/JPEG bytes on disk under the platform's per-user cache dir
(via ``QStandardPaths.CacheLocation``) so the second open of the library
is instant. Fetches go through QGIS's ``QgsNetworkAccessManager`` (so they
inherit its SSL/proxy/auth config) and emit a signal per finished download
so cards can swap in the real
pixmap when ready. 404s (templates not yet seeded server-side) are
remembered so we don't refetch them.
"""
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

# Demos the server returned 404 for (not yet seeded). Module-level so the
# knowledge survives reopening the library dialog within a QGIS session and we
# don't re-issue doomed requests (each burns a concurrency slot + 15s timeout).
_KNOWN_MISSING: dict[str, float] = {}

# Cache version: server-bumpable via demo_cache_version in /api/plugin/config
# (a re-seed that must invalidate every client's cache is now a website deploy,
# not a plugin release; v3 and v4 each cost one). The fallback is the shipped
# version used when no server config is cached.
_CACHE_DIR_PREFIX = "ai-edit-template-demos-v"
_CACHE_DIR_FALLBACK_VERSION = "4"

# Per-request network timeout for a demo image fetch (no-op before Qt 5.15).
_FETCH_TIMEOUT_MS = 15_000
# A decoded reply shorter than this cannot be a real image; treated as unusable.
_MIN_IMAGE_BYTES = 256
# Cap simultaneous fetches so opening the library (or a popup with bigger
# preview images) doesn't fire dozens of requests at once and choke a slow
# link. Excess requests queue and start as in-flight ones finish. Kept low
# so a thin pipe isn't split too many ways (each split is likelier to hit
# the per-request transfer timeout).
_MAX_CONCURRENT_FETCHES = 3


def _cache_dir_name() -> str:
    """Cache dir name with a server-bumpable version suffix.

    Reads demo_cache_version from the cached server config (cache-only, no
    network); absent or garbage falls back to the shipped version, so past
    cache-poisoning incidents need a website deploy, not a plugin release."""
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
    """Per-platform cache dir for demo image bytes.

    Returns ``CacheLocation/<cache dir name>`` which is:
        - Windows: ``%LOCALAPPDATA%/<org>/<app>/cache/<name>``
        - macOS:   ``~/Library/Caches/<org>/<app>/<name>``
        - Linux:   ``~/.cache/<name>``
    Falls back to the historical Linux-style path when QStandardPaths
    returns nothing (rare, mostly headless test envs).
    """
    name = _cache_dir_name()
    base = QStandardPaths.writableLocation(CacheLocation)
    if base:
        return Path(base) / name
    return Path.home() / ".cache" / name


def _cleanup_stale_cache_dirs(active: Path) -> None:
    """Best-effort removal of sibling demo caches from other versions."""
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
        pass  # nosec B110  Cleanup must never block the loader.


def _cache_path(template_id: str, which: str) -> Path:
    def component(value):
        # Preserve existing safe cache names; hash unsafe/long names so distinct
        # ids cannot collide after sanitization or escape the cache directory.
        if (value and len(value) <= 100 and value.isascii()
                and all(c.isalnum() or c in "-_" for c in value)
                and value.upper() not in {"CON", "PRN", "AUX", "NUL"}
                and not (len(value) == 4 and value[:3].upper() in {"COM", "LPT"} and value[-1].isdigit())):
            return value
        return "key-" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    return _cache_root() / component(template_id) / f"{component(which)}.jpg"


# Demos rarely change, but a curated demo can be re-seeded server-side. Without
# expiry the on-disk cache would pin the old image forever; a 7-day TTL lets
# updates propagate while still keeping repeat opens instant.
_DEMO_CACHE_TTL_SECONDS = 7 * 24 * 3600


def _demo_cache_ttl() -> int:
    """Seconds a cached demo image stays usable, read at use time. Distinct
    from demo_cache_version above, which comes from the activation config and
    renames the whole cache dir; this one only ages the files inside it."""
    return get_export_dial("cache_ttl_s.demos", _DEMO_CACHE_TTL_SECONDS)


def read_cached_pixmap(template_id: str, which: str) -> QPixmap | None:
    """Return a QPixmap from the on-disk cache, or None if absent or stale."""
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
    """Async fetcher for template demo images. One instance per dialog.

    Signals:
        loaded(template_id, which, QPixmap) - fires when a download (or cache
            hit) yields a usable pixmap. The card matching template_id + which
            installs it into the slider.
        failed(template_id, which) - fires once we've concluded the demo will
            never be available (404 server-side or persistent network error).
    """

    loaded = pyqtSignal(str, str, QPixmap)
    failed = pyqtSignal(str, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._queue: deque[tuple[str, str, str]] = deque()
        self._pending: dict[tuple[str, str], str] = {}
        self._waiting: dict[str, list[tuple[str, str]]] = {}
        self._in_flight = 0
        # The cache only saves a refetch, so a locked or over-long cache path
        # must not raise inside the Prompt Library dialog's constructor.
        try:
            root = _cache_root()
            root.mkdir(parents=True, exist_ok=True)
            _cleanup_stale_cache_dirs(root)
        except OSError as err:
            log_warning(f"Demo cache dir unavailable: {err}")

    def request(self, template_id: str, which: str, url: str) -> None:
        """Try cache first; if miss, queue an async network fetch.

        ``which`` is normally "before"/"after" for card sliders; the detail
        popup also passes "ref0", "ref1", ... for reference thumbnails. Any
        non-empty token works as the on-disk cache filename."""
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
        # A 404 belongs to the resource URL, not to one card's cache key.
        # Several cards can legitimately alias the same server image, and a
        # failed seed should not make each alias spend another timeout.
        if url in _KNOWN_MISSING:
            safe_single_shot(0, self, lambda: self.failed.emit(template_id, which))
            return
        self._pending[key] = url
        # Defer the disk read + decode to the next event-loop turn so a burst of
        # cached cards built in one synchronous loop doesn't block the dialog's
        # first paint. Parented to self, so it can't fire after the loader dies.
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
        """Start queued fetches up to the concurrency cap."""
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
        # Follow redirects. Resolved via qt_compat (scoped-then-flat) because
        # PyQt5 on some QGIS 3 builds exposes these enums flat, not scoped.
        req.setAttribute(RedirectPolicyAttribute, NoLessSafeRedirectPolicy)
        req.setRawHeader(b"Accept", b"image/jpeg, image/png, image/webp, image/*")
        set_transfer_timeout(
            req, transfer_timeout(
                get_export_dial("widgets.template_demo_loader.fetch_timeout_ms", _FETCH_TIMEOUT_MS),
                _FETCH_TIMEOUT_MS))
        # Route through QGIS's network manager so the fetch inherits its SSL CA
        # bundle, proxy, and auth config. A bare QNetworkAccessManager fails
        # silently on some CDN hosts. Parent the reply to this loader so it dies
        # with the dialog (no callback on a dead object).
        reply = QgsNetworkAccessManager.instance().get(req)
        reply.setParent(self)
        reply.finished.connect(
            lambda r=reply, t=template_id, w=which, u=url: self._on_finished(r, t, w, u)
        )

    def _on_finished(self, reply: QNetworkReply, template_id: str, which: str, url: str) -> None:
        """Resolve exactly one card, whatever happened.

        This runs inside a ``QNetworkReply.finished`` slot, where a raise has
        nowhere to go and would leave the card reading "Loading..." for the
        life of the dialog. Every path therefore ends in one of the two
        signals, and the decode happens in a helper so the emit itself is
        outside the guard (a crashing card slot can never turn into a second,
        contradictory signal for the same card).

        The housekeeping between the two is guarded per step for the same
        reason: it used to sit in a ``finally``, which runs BEFORE the emits,
        so a ``deleteLater`` on a dead wrapper or a raise inside ``_pump``
        skipped the card's answer entirely."""
        keys = [key for key in self._waiting.pop(url, []) if self._pending.get(key) == url]
        pixmap = None
        try:
            pixmap = self._pixmap_from_reply(reply, template_id, which, url, keys)
        except Exception as err:  # noqa: BLE001
            log_warning(f"Demo fetch handling failed for {template_id}/{which}: {err}")
        try:
            reply.deleteLater()
        except (RuntimeError, AttributeError):  # nosec B110 - wrapper already dead
            pass
        self._in_flight = max(0, self._in_flight - 1)
        # Pumped before the emit so a card slot that raises cannot stall the
        # queue; pumping cannot stop the emit either way.
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
        """Decode the finished reply and cache its bytes. None means unusable."""
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
            # mkdir belongs INSIDE the guard: on Windows a long path or a
            # locked cache dir raises OSError, and letting that escape would
            # skip the caller's loaded.emit and hang the card on "Loading...".
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
