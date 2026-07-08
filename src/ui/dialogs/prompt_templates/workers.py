
from __future__ import annotations

import time

try:
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover
    _sip = None

from qgis.PyQt.QtCore import QCoreApplication, QThread, pyqtSignal

from ....core.config_store import get_export_dial
from ....core.logger import log_warning









_RECENT_PAGE_SIZE = 50


class _LibrarySyncWorker(QThread):







    recent_jobs_fetched = pyqtSignal(list, bool)
    favorite_jobs_fetched = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, client, auth: dict, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth

    def run(self):



        if self.isInterruptionRequested():
            return
        page_size = _RECENT_PAGE_SIZE
        try:
            hist = self._client.get_generation_history(self._auth, limit=page_size)
        except Exception as e:
            self.failed.emit(f"history: {e}")
            return
        if isinstance(hist, dict) and "error" not in hist:
            jobs = hist.get("jobs", []) or []

            self.recent_jobs_fetched.emit(jobs, bool(hist.get("has_more", len(jobs) >= page_size)))
        else:
            self.failed.emit(
                f"history: {hist.get('error', 'unknown') if isinstance(hist, dict) else 'parse_error'}"
            )

        if self.isInterruptionRequested():
            return
        try:
            favs = self._client.get_generation_history(
                self._auth, limit=page_size, favorites_only=True
            )
        except Exception as e:
            self.failed.emit(f"favorites: {e}")
            return
        if isinstance(favs, dict) and "error" not in favs:
            self.favorite_jobs_fetched.emit(favs.get("jobs", []) or [])
        else:
            self.failed.emit(
                f"favorites: {favs.get('error', 'unknown') if isinstance(favs, dict) else 'parse_error'}"
            )


class _HistoryPageWorker(QThread):




    page_fetched = pyqtSignal(list, bool)
    failed = pyqtSignal(str)

    def __init__(self, client, auth: dict, before: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._before = before

    def run(self):
        if self.isInterruptionRequested():
            return
        try:
            resp = self._client.get_generation_history(
                self._auth,
                limit=_RECENT_PAGE_SIZE,
                before=self._before,
            )
        except Exception as e:
            self.failed.emit(f"history page: {e}")
            return
        if isinstance(resp, dict) and "error" not in resp:
            jobs = resp.get("jobs", []) or []
            self.page_fetched.emit(jobs, bool(resp.get("has_more", False)))
        else:
            self.failed.emit("history page: server error")


class _FavoriteSyncWorker(QThread):



    def __init__(
        self,
        client,
        auth: dict,
        prompt: str,
        label: str,
        source_category: str,
        now_favorited: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._prompt = prompt
        self._label = label or None
        self._source_category = source_category or None
        self._now_favorited = now_favorited

    def run(self):
        if self.isInterruptionRequested():
            return
        try:
            if self._now_favorited:
                self._client.add_favorite(
                    self._auth, self._prompt, self._label, self._source_category
                )
            else:
                self._client.remove_favorite(self._auth, self._prompt)
        except Exception as e:
            log_warning(f"Favorite sync failed (silent): {e}")


class _GenerationFavoriteWorker(QThread):


    def __init__(self, client, auth: dict, request_id: str, now_favorited: bool, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._request_id = request_id
        self._now_favorited = now_favorited

    def run(self):
        if self.isInterruptionRequested():
            return
        try:
            self._client.set_generation_favorite(
                self._auth, self._request_id, self._now_favorited
            )
        except Exception as e:
            log_warning(f"Generation favorite sync failed (silent): {e}")







_INFLIGHT_WORKERS: set = set()


def _detach_worker(worker: QThread) -> None:
    _INFLIGHT_WORKERS.add(worker)
    worker.finished.connect(lambda: _INFLIGHT_WORKERS.discard(worker))
    worker.finished.connect(worker.deleteLater)








DRAIN_WAIT_MS = 1500






_DIALOG_SIGNALS = (
    "recent_jobs_fetched",
    "favorite_jobs_fetched",
    "page_fetched",
    "failed",
)



_STRANDED_WORKERS: list = []


def _mute_worker(worker: QThread) -> None:

    for name in _DIALOG_SIGNALS:
        try:
            sig = getattr(worker, name, None)
            if sig is None:
                continue
            sig.disconnect()
        except (RuntimeError, TypeError):  # nosec B110
            pass


def _strand_worker(worker: QThread) -> None:

















    _STRANDED_WORKERS.append(worker)
    if _sip is not None:
        try:
            worker.setParent(None)
            _sip.transferto(worker, None)
            return
        except (RuntimeError, TypeError, ValueError):  # nosec B110
            pass
    try:
        worker.setParent(QCoreApplication.instance())
    except (RuntimeError, TypeError):  # nosec B110
        pass
    log_warning("Prompt Library worker outlived the shutdown drain and could not be detached")


def drain_prompt_library_workers(wait_ms: int | None = None) -> None:



















    if wait_ms is None:

        wait_ms = min(get_export_dial("dialogs.workers.drain_wait_ms", DRAIN_WAIT_MS), DRAIN_WAIT_MS * 2)
    deadline = time.monotonic() + max(0, wait_ms) / 1000.0
    for worker in list(_INFLIGHT_WORKERS):
        _mute_worker(worker)
        try:
            worker.requestInterruption()
        except (RuntimeError, AttributeError):  # nosec B110
            pass
        try:
            if worker.isRunning():
                remaining_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
                worker.wait(remaining_ms)
            still_running = worker.isRunning()
        except RuntimeError:
            still_running = False
        if not still_running:
            _INFLIGHT_WORKERS.discard(worker)
            continue
        _strand_worker(worker)
