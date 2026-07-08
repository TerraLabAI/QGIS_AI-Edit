"""Server sync of history, generation favorites, and prompt favorites."""
from __future__ import annotations

from ....core import qt_compat as QtC
from ....core.logger import log_debug
from .workers import _detach_worker, _FavoriteSyncWorker, _LibrarySyncWorker


class SyncMixin:
    """Background history/favorite sync for PromptTemplatesDialog."""

    def _on_star_toggled(self, prompt: str, now_fav: bool, label: str, source_cat: str):
        # Prompt favorites now live in the unified Favorites tab, so refresh the
        # favorites catalog from prompt_history and rebuild that page to reflect
        # the add/remove. Deferred so the card whose star fired isn't destroyed
        # mid-signal (Qt6 crash guard, same pattern as _on_generation_favorite).
        self._load_categories()
        self._refresh_sidebar_button("user_favorites")
        QtC.safe_single_shot(
            0, self, lambda: self._reload_dynamic_pages(keys=("user_favorites",))
        )
        self._sync_favorite(prompt, label, source_cat, now_fav)

    def _sync_favorite(self, prompt: str, label: str, source_cat: str, now_fav: bool):
        if self._client is None or self._auth_provider is None:
            return
        auth = self._auth_provider() or {}
        if not auth.get("Authorization"):
            return
        # Unparented + detached: if the dialog closes mid-fetch, the worker keeps
        # running to completion (POST/DELETE is read-write-safe to abandon) and
        # self-deletes via finished. Parenting to the dialog would destroy the
        # parent while the worker is still alive, which crashes on Qt6.
        worker = _FavoriteSyncWorker(
            self._client, auth, prompt, label, source_cat, now_fav, parent=None
        )
        _detach_worker(worker)
        worker.start()

    # -- Server sync -----------------------------------------------------

    def _start_sync(self):
        """Kick off a background fetch of /history + /favorites. If client/auth
        unavailable, silently skips - local cache is the source for this session."""
        # Session cache is still fresh (nothing generated since last fetch): the
        # seeded lists already populated the UI, so skip the network round-trip.
        if self._history_fresh and (self._recent_jobs or self._favorite_jobs):
            return
        if self._client is None or self._auth_provider is None:
            return
        auth = self._auth_provider() or {}
        if not auth.get("Authorization"):
            return
        # Unparented + detached: the dialog can be closed or deleted while this
        # blocking fetch is still in flight without destroying a running QThread
        # (which aborts QGIS on Qt6). The data slots below are bound methods, so
        # Qt drops them automatically when the dialog is destroyed and a late
        # result never lands on a dead object.
        worker = _LibrarySyncWorker(self._client, auth, parent=None)
        worker.recent_jobs_fetched.connect(self._on_recent_jobs_fetched)
        worker.favorite_jobs_fetched.connect(self._on_favorite_jobs_fetched)
        worker.failed.connect(self._on_sync_failed)
        _detach_worker(worker)
        self._sync_worker = worker
        worker.start()

    @staticmethod
    def _same_jobs(a: list, b: list) -> bool:
        """True when two job lists hold the same generations in the same order.
        Used to skip a needless page rebuild (and its flicker) when a background
        refresh returns exactly what the warm cache already showed."""
        return [j.get("request_id") for j in a] == [j.get("request_id") for j in b]

    def _on_recent_jobs_fetched(self, jobs: list, has_more: bool = False):
        jobs = jobs or []
        self._recent_has_more = bool(has_more)
        changed = not self._same_jobs(jobs, self._recent_jobs)
        self._recent_jobs = jobs
        if changed:
            self._reload_dynamic_pages(keys=("recent",))
        self.history_synced.emit(self._recent_jobs, self._favorite_jobs)

    def _on_favorite_jobs_fetched(self, jobs: list):
        jobs = jobs or []
        changed = not self._same_jobs(jobs, self._favorite_jobs)
        self._favorite_jobs = jobs
        if changed:
            self._reload_dynamic_pages(keys=("user_favorites",))
        self.history_synced.emit(self._recent_jobs, self._favorite_jobs)

    def _on_sync_failed(self, msg: str):
        log_debug(f"Prompt library sync failed (local cache stays): {msg}")
