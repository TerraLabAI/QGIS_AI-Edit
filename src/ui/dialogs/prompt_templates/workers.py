"""Background QThread workers for history fetches and favorite sync."""
from __future__ import annotations

from qgis.PyQt.QtCore import QThread, pyqtSignal

from ....core.logger import log_warning

# ---------------------------------------------------------------------------
# Sync workers
# ---------------------------------------------------------------------------


class _LibrarySyncWorker(QThread):
    """Background fetch of the user's past generations for Recent + Favorites.

    Pulls the rich before/after history endpoint (prompt + signed input/output
    URLs + location) so Recent and Favorites render as generation cards the
    user can re-add to the map, reuse, or download. Emits one signal per
    section so the dialog refreshes progressively."""

    recent_jobs_fetched = pyqtSignal(list, bool)
    favorite_jobs_fetched = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, client, auth: dict, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth

    def run(self):
        try:
            hist = self._client.get_generation_history(self._auth, limit=50)
        except Exception as e:
            self.failed.emit(f"history: {e}")
            return
        if isinstance(hist, dict) and "error" not in hist:
            jobs = hist.get("jobs", []) or []
            # Older servers don't send has_more; a full page implies more.
            self.recent_jobs_fetched.emit(jobs, bool(hist.get("has_more", len(jobs) >= 50)))
        else:
            self.failed.emit(
                f"history: {hist.get('error', 'unknown') if isinstance(hist, dict) else 'parse_error'}"
            )

        try:
            favs = self._client.get_generation_history(
                self._auth, limit=50, favorites_only=True
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
    """Background fetch of one OLDER page of the generation history - the
    Recent tab's server-side Load more, used once the locally held jobs are
    all visible but the server reported has_more."""

    page_fetched = pyqtSignal(list, bool)
    failed = pyqtSignal(str)

    def __init__(self, client, auth: dict, before: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._before = before

    def run(self):
        try:
            resp = self._client.get_generation_history(
                self._auth, limit=50, before=self._before
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
    """Fire-and-forget POST/DELETE for a single prompt favorite toggle (the ★
    on a curated template, distinct from a generation favorite)."""

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
    """Fire-and-forget star/unstar of a single past generation."""

    def __init__(self, client, auth: dict, request_id: str, now_favorited: bool, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._request_id = request_id
        self._now_favorited = now_favorited

    def run(self):
        try:
            self._client.set_generation_favorite(
                self._auth, self._request_id, self._now_favorited
            )
        except Exception as e:
            log_warning(f"Generation favorite sync failed (silent): {e}")


# In-flight background workers, held independently of any dialog. A running
# QThread that loses its last Python reference can be garbage-collected and
# destroyed mid-run, which aborts the QGIS process. Keeping the worker here
# until it emits finished lets the dialog be closed or deleted at any time
# while the blocking fetch is still going, without crashing.
_INFLIGHT_WORKERS: set = set()


def _detach_worker(worker: QThread) -> None:
    _INFLIGHT_WORKERS.add(worker)
    worker.finished.connect(lambda: _INFLIGHT_WORKERS.discard(worker))
    worker.finished.connect(worker.deleteLater)
