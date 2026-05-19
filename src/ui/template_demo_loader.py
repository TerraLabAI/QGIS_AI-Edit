"""Async loader for the prompt-library before/after demo images.

Caches PNG/JPEG bytes on disk under the platform's per-user cache dir
(via ``QStandardPaths.CacheLocation``) so the second open of the library
is instant. A single ``QNetworkAccessManager`` queues HTTP fetches and
emits a signal per finished download so cards can swap in the real
pixmap when ready. 404s (templates not yet seeded server-side) are
remembered so we don't refetch them.
"""
from __future__ import annotations

import os
from pathlib import Path

from qgis.PyQt.QtCore import QByteArray, QObject, QStandardPaths, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ..core.logger import log_debug, log_warning


def _cache_root() -> Path:
    """Per-platform cache dir for demo image bytes.

    Returns ``CacheLocation/ai-edit-template-demos`` which is:
        - Windows: ``%LOCALAPPDATA%/<org>/<app>/cache/ai-edit-template-demos``
        - macOS:   ``~/Library/Caches/<org>/<app>/ai-edit-template-demos``
        - Linux:   ``~/.cache/<app>/ai-edit-template-demos``
    Falls back to the historical Linux-style path when QStandardPaths
    returns nothing (rare, mostly headless test envs).
    """
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    if base:
        return Path(base) / "ai-edit-template-demos"
    return Path.home() / ".cache" / "ai-edit-template-demos"


def _cache_path(template_id: str, which: str) -> Path:
    safe_id = "".join(c for c in template_id if c.isalnum() or c in "-_")
    return _cache_root() / safe_id / f"{which}.jpg"


def read_cached_pixmap(template_id: str, which: str) -> QPixmap | None:
    """Return a QPixmap loaded from the on-disk cache, or None if absent."""
    path = _cache_path(template_id, which)
    if not path.is_file():
        return None
    try:
        pm = QPixmap(str(path))
        if pm.isNull() or pm.width() < 2:
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
        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_finished)
        self._inflight: dict[str, tuple[str, str]] = {}
        self._known_missing: set[tuple[str, str]] = set()
        _cache_root().mkdir(parents=True, exist_ok=True)

    def request(self, template_id: str, which: str, url: str) -> None:
        """Try cache first; if miss, kick off an async network fetch."""
        if not template_id or which not in ("before", "after") or not url:
            return
        key = (template_id, which)
        if key in self._known_missing:
            self.failed.emit(template_id, which)
            return
        pm = read_cached_pixmap(template_id, which)
        if pm is not None:
            self.loaded.emit(template_id, which, pm)
            return
        req = QNetworkRequest(QUrl(url))
        req.setAttribute(QNetworkRequest.Attribute.FollowRedirectsAttribute, True)
        req.setRawHeader(b"Accept", b"image/jpeg, image/png, image/webp, image/*")
        req.setTransferTimeout(15_000)
        reply = self._nam.get(req)
        self._inflight[str(id(reply))] = (template_id, which)

    def _on_finished(self, reply: QNetworkReply) -> None:
        key = self._inflight.pop(str(id(reply)), None)
        try:
            if key is None:
                return
            template_id, which = key
            err_code = reply.error()
            no_err = QNetworkReply.NetworkError.NoError
            http_status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            try:
                http_int = int(http_status) if http_status is not None else 0
            except (TypeError, ValueError):
                http_int = 0
            if err_code != no_err or http_int >= 400:
                if http_int == 404:
                    self._known_missing.add((template_id, which))
                else:
                    log_debug(
                        f"Demo fetch failed for {template_id}/{which}: "
                        f"err={err_code} http={http_int}"
                    )
                self.failed.emit(template_id, which)
                return
            data: QByteArray = reply.readAll()
            buf = bytes(data)
            if len(buf) < 256:
                self.failed.emit(template_id, which)
                return
            pm = QPixmap()
            if not pm.loadFromData(buf):
                log_debug(f"Demo bytes did not decode for {template_id}/{which}")
                self.failed.emit(template_id, which)
                return
            self._write_cache(template_id, which, buf)
            self.loaded.emit(template_id, which, pm)
        finally:
            reply.deleteLater()

    @staticmethod
    def _write_cache(template_id: str, which: str, buf: bytes) -> None:
        path = _cache_path(template_id, which)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".jpg.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(buf)
            os.replace(tmp, path)
        except OSError as err:
            log_warning(f"Failed to write demo cache {path}: {err}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass  # nosec B110
