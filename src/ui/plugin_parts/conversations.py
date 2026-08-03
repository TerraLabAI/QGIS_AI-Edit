"""Network flows behind the sessions UI: cache refresh, paging, delete,
rename, and the one-time thumbnail backfill.

The Prompt Library's Sessions page renders from the dock's cache and emits
intents; everything here runs the matching client call in a
GenericRequestTask and writes the results back into the dock cache + disk
cache, so the page stays honest after every mutation.
"""
from __future__ import annotations

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.i18n import tr
from ...core.logger import log_debug, log_warning
from ...core.prompts import conversation_thumbs, history_cache
from ...core.prompts.conversation_summary import conversation_entries
from ...workers.generic_request_task import GenericRequestTask


class ConversationsMixin:
    # Cache refresh + paging ---------------------------------------------

    def _refresh_conversations_cache(self) -> None:
        """One silent startup fetch so the Library's Sessions page is fresh on
        its first open. Falls back to the disk cache on any failure."""
        dock = self._dock_widget
        if dock is None:
            return
        if self._client is None or not self._auth_manager.has_activation_key():
            log_debug("history refresh skipped: no client or key yet")
            return
        # Several UI paths can ask at once (startup bootstrap + home screen,
        # generation end + Exit): one in-flight fetch serves them all.
        running = getattr(self, "_conversations_refresh_task", None)
        if running is not None and running.is_active():
            log_debug("history refresh skipped: already in flight")
            return
        auth = self._auth_manager.get_auth_header()
        client = self._client

        def _work():
            return client.get_generation_history(auth, limit=50)

        task = GenericRequestTask(tr("Refreshing history"), _work, silent=True)
        task.succeeded.connect(self._on_conversations_refreshed)
        task.failed.connect(self._on_conversations_refresh_failed)
        self._conversations_refresh_task = task
        self._hold_history_task(task)
        log_debug("history refresh started")

    def _on_conversations_refresh_failed(self, message: str, code: str) -> None:
        """A stale list beats an empty one: keep rendering the cache. The old
        lambda swallowed the reason, which made this path undiagnosable."""
        log_warning(f"history refresh failed: {message} ({code})")

    def _on_conversations_refreshed(self, payload) -> None:
        dock = self._dock_widget
        if dock is None:
            return
        payload = payload or {}
        jobs = payload.get("jobs") or []
        log_debug(
            f"history refresh: {len(jobs)} jobs"
            f" (has_more={bool(payload.get('has_more'))})"
        )
        # Read by the Sessions page's "load older" affordance when it asks
        # for another history page.
        self._sessions_has_more = bool(payload.get("has_more"))
        dock._on_library_history_synced(jobs, dock._library_favorite_cache)
        # The refresh can be answering the Sessions page's own open (its
        # sessions_refresh_requested), so push the result into the live page.
        self._refresh_sessions_page()
        self._backfill_conversation_thumbs(jobs)

    # One-time catch-up for generations older than the local thumb store:
    # the refresh that just returned carries FRESH signed URLs (valid ~1 h),
    # so this is the only reliable moment to fetch what the store misses.
    # Idempotent: whatever fails stays missing and retries next startup.

    def _backfill_conversation_thumbs(self, jobs: list) -> None:
        if getattr(self, "_thumb_backfill_started", False):
            return
        self._thumb_backfill_started = True
        if self._client is None:
            return
        wanted: list[tuple[str, str]] = []
        for entry in conversation_entries(jobs):
            members = entry.get("members") or []
            for member in members:
                rid = member.get("request_id") or ""
                url = member.get("output_thumb_url") or member.get("output_url")
                if rid and url and conversation_thumbs.load_thumb(rid) is None:
                    wanted.append((rid, url))
            session_id = entry.get("session_id") or ""
            oldest = members[-1] if members else {}
            in_url = oldest.get("input_thumb_url") or oldest.get("input_url")
            if (
                session_id
                and in_url
                and conversation_thumbs.load_thumb(f"in-{session_id}") is None
            ):
                wanted.append((f"in-{session_id}", in_url))
        if not wanted:
            return
        client = self._client

        def _work(items=tuple(wanted)):
            blobs = {}
            for key, url in items:
                try:
                    blobs[key] = client.download_image(url)
                except Exception as err:  # noqa: BLE001
                    log_warning(f"thumb backfill download failed: {err}")
            return {"blobs": blobs}

        task = GenericRequestTask(tr("Loading thumbnails"), _work, silent=True)
        task.succeeded.connect(self._on_thumb_backfill_done)
        task.failed.connect(lambda *_: None)
        self._hold_history_task(task)

    def _on_thumb_backfill_done(self, payload) -> None:
        # Pixmap work happens here on the main thread (QPixmap is not
        # thread-safe), the task only carried bytes.
        from qgis.PyQt.QtGui import QPixmap

        saved = 0
        for key, blob in ((payload or {}).get("blobs") or {}).items():
            if not blob:
                continue
            pixmap = QPixmap()
            # No except-continue here: the plugin repository's bandit pass
            # flags that shape (B112) and would block the public release.
            try:
                loaded = pixmap.loadFromData(blob) and not pixmap.isNull()
            except Exception:  # one bad blob costs one thumb, not the batch
                loaded = False
            if loaded:
                conversation_thumbs.save_thumb(key, pixmap)
                saved += 1
        if saved and self._dock_widget is not None:
            self._refresh_sessions_page()

    def _on_conversations_page_requested(self, before: str) -> None:
        dock = self._dock_widget
        if dock is None or self._client is None:
            return
        auth = self._auth_manager.get_auth_header()
        client = self._client

        def _work(cursor=before):
            return client.get_generation_history(auth, limit=50, before=cursor)

        task = GenericRequestTask(tr("Loading older sessions"), _work, silent=True)
        task.succeeded.connect(self._on_conversations_page_loaded)
        task.failed.connect(
            lambda msg, _code: self._notify(msg, self._warning_level(), duration=5)
        )
        self._hold_history_task(task)

    def _on_conversations_page_loaded(self, payload) -> None:
        dock = self._dock_widget
        if dock is None:
            return
        payload = payload or {}
        known = {
            j.get("request_id") for j in dock._library_recent_cache if isinstance(j, dict)
        }
        fresh = [
            j
            for j in (payload.get("jobs") or [])
            if isinstance(j, dict) and j.get("request_id") not in known
        ]
        # The in-memory cache grows past the disk cap on purpose: paging is a
        # per-session deep dive, the disk cache stays the warm-start window.
        dock._library_recent_cache.extend(fresh)
        history_cache.save_recent_jobs(dock._library_recent_cache)
        self._sessions_has_more = bool(payload.get("has_more"))
        self._refresh_sessions_page()

    def _refresh_sessions_page(self) -> None:
        """Repaint the Prompt Library's Sessions page if it is open. The page
        renders from the dock's _library_recent_cache on every open, so a
        closed dialog needs nothing; an open one gets the updated list pushed
        (set_session_jobs repaints unconditionally: a rename changes titles
        without changing request_ids, which a same-jobs shortcut would skip)."""
        dock = self._dock_widget
        dialog = getattr(dock, "_templates_dialog", None) if dock else None
        if dialog is not None:
            dialog.set_session_jobs(dock._library_recent_cache)

    # Delete --------------------------------------------------------------

    def _on_conversation_delete(self, entry: dict) -> None:
        from qgis.PyQt.QtWidgets import QMessageBox

        box = QMessageBox(self._iface.mainWindow())
        box.setWindowTitle(tr("Delete this session?"))
        box.setText(
            tr(
                "This deletes its generations and their images from TerraLab "
                "servers. Layers already in your project stay. This cannot "
                "be undone."
            )
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes
        )
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        session_id = entry.get("session_id")
        request_id = (entry.get("cover") or {}).get("request_id")
        if self._client is None or not (session_id or request_id):
            return
        auth = self._auth_manager.get_auth_header()
        client = self._client

        def _work(sid=session_id, rid=request_id):
            if sid:
                return client.delete_generation_session(auth, session_id=sid)
            return client.delete_generation_session(auth, request_id=rid)

        task = GenericRequestTask(tr("Deleting session"), _work)
        task.succeeded.connect(lambda _p, e=entry: self._apply_conversation_delete(e))
        task.failed.connect(
            lambda msg, code, e=entry: self._on_conversation_delete_failed(msg, code, e)
        )
        self._hold_history_task(task)

    def _on_conversation_delete_failed(self, msg: str, code: str, entry: dict) -> None:
        # Already gone server-side (a retry, another machine): treat as done
        # locally instead of stranding a ghost row.
        if code == "WRONG_REQUEST":
            self._apply_conversation_delete(entry)
            return
        self._notify(msg, self._warning_level(), duration=6)

    def _apply_conversation_delete(self, entry: dict) -> None:
        dock = self._dock_widget
        if dock is None:
            return
        gone = {
            m.get("request_id")
            for m in (entry.get("members") or [])
            if isinstance(m, dict)
        }
        gone.add((entry.get("cover") or {}).get("request_id"))
        gone.discard(None)
        dock._library_recent_cache = [
            j for j in dock._library_recent_cache if j.get("request_id") not in gone
        ]
        dock._library_favorite_cache = [
            j for j in dock._library_favorite_cache if j.get("request_id") not in gone
        ]
        history_cache.save_recent_jobs(dock._library_recent_cache)
        history_cache.save_favorite_jobs(dock._library_favorite_cache)
        # "Deleted everywhere" includes this machine: drop the local thumbs too.
        for rid in gone:
            conversation_thumbs.delete_thumb(rid)
        if entry.get("session_id"):
            conversation_thumbs.delete_thumb(f"in-{entry['session_id']}")
        self._refresh_sessions_page()
        self._notify(tr("Session deleted."), duration=4)
        telemetry.track(te.CONVERSATION_DELETED, {"scope": "one"})

    def _clear_local_conversations(self) -> None:
        """Drop the locally cached conversation rows, in memory and on disk.

        Thumbnails are deliberately left alone: they are named by request_id,
        so another account can never read them, and the common sign-out is a
        user reconnecting to the SAME account, who then keeps the whole store
        instead of re-downloading it. Callers that mean "delete everything"
        (the Account Settings wipe) clear the thumbs themselves."""
        dock = self._dock_widget
        history_cache.clear()
        if dock is None:
            return
        dock._library_recent_cache = []
        dock._library_favorite_cache = []
        dock._conversations_has_more = False
        # The cached-fresh flag would otherwise let the next Library open trust
        # the now-empty cache instead of refetching.
        dock.mark_library_history_dirty()
        # Re-arm the one-shot thumb backfill so a newly signed-in account gets
        # its own thumbs in this session rather than after a QGIS restart.
        self._thumb_backfill_started = False
        self._refresh_sessions_page()

    # Rename --------------------------------------------------------------

    def _on_conversation_rename(self, entry: dict) -> None:
        from qgis.PyQt.QtWidgets import QInputDialog, QLineEdit

        session_id = entry.get("session_id")
        if not session_id or self._client is None:
            return
        title, ok = QInputDialog.getText(
            self._iface.mainWindow(),
            tr("Rename session"),
            tr("Title"),
            QLineEdit.EchoMode.Normal,
            entry.get("title") or "",
        )
        title = (title or "").strip()
        if not ok or not title:
            return
        auth = self._auth_manager.get_auth_header()
        client = self._client

        def _work(sid=session_id, text=title):
            return client.rename_generation_session(auth, sid, text)

        task = GenericRequestTask(tr("Renaming session"), _work)
        task.succeeded.connect(
            lambda payload, sid=session_id: self._apply_conversation_rename(sid, payload)
        )
        task.failed.connect(
            lambda msg, _code: self._notify(msg, self._warning_level(), duration=6)
        )
        self._hold_history_task(task)

    def _apply_conversation_rename(self, session_id: str, payload) -> None:
        dock = self._dock_widget
        if dock is None:
            return
        # The server returns the normalized title; store that, not the raw input.
        title = str((payload or {}).get("title") or "").strip()
        if not title:
            return
        for job in dock._library_recent_cache:
            if isinstance(job, dict) and job.get("session_id") == session_id:
                job["session_title"] = title
        for job in dock._library_favorite_cache:
            if isinstance(job, dict) and job.get("session_id") == session_id:
                job["session_title"] = title
        history_cache.save_recent_jobs(dock._library_recent_cache)
        history_cache.save_favorite_jobs(dock._library_favorite_cache)
        self._refresh_sessions_page()
        telemetry.track(te.CONVERSATION_RENAMED, {})

    # Thumbnails live in the local disk store (core.prompts.conversation_thumbs,
    # written at generation time); the dock reads them directly, so no network
    # loader exists here.

    # Message-bar level helpers (Qgis enum import stays out of module scope
    # so the module keeps importing headless).

    @staticmethod
    def _warning_level():
        from qgis.core import Qgis

        return Qgis.MessageLevel.Warning

    @staticmethod
    def _success_level():
        from qgis.core import Qgis

        return Qgis.MessageLevel.Success
