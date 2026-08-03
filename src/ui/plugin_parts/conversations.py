







from __future__ import annotations

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.i18n import tr
from ...core.logger import log_debug, log_warning
from ...core.privacy_notice import has_accepted_privacy_notice
from ...core.prompts import conversation_thumbs, history_cache
from ...core.prompts.conversation_summary import conversation_entries
from ...workers.generic_request_task import GenericRequestTask


class ConversationsMixin:


    def _refresh_conversations_cache(self) -> None:


        dock = self._dock_widget
        if dock is None:
            return
        if self._client is None or not self._auth_manager.has_activation_key():
            log_debug("history refresh skipped: no client or key yet")
            return



        if not has_accepted_privacy_notice():
            log_debug("history refresh skipped: privacy notice pending")
            return


        running = getattr(self, "_conversations_refresh_task", None)
        if running is not None and running.is_active():
            log_debug("history refresh skipped: already in flight")
            return
        auth = self._auth_manager.get_auth_header()
        client = self._client
        account_revision = self._history_account_revision()

        def _work():
            return client.get_generation_history(auth, limit=50)

        task = GenericRequestTask(tr("Refreshing sessions"), _work, silent=True)
        task.succeeded.connect(
            lambda payload, rev=account_revision:
            self._on_conversations_refreshed(payload, rev)
        )
        task.failed.connect(
            lambda message, code, rev=account_revision:
            self._on_conversations_refresh_failed(message, code, rev)
        )
        self._conversations_refresh_task = task
        self._hold_history_task(task)
        log_debug("history refresh started")

    def _on_conversations_refresh_failed(
        self, message: str, code: str, account_revision: int | None = None
    ) -> None:

        if not self._history_revision_is_current(account_revision):
            return
        log_warning(f"history refresh failed: {message} ({code})")

    def _on_conversations_refreshed(
        self, payload, account_revision: int | None = None
    ) -> None:
        if not self._history_revision_is_current(account_revision):
            return
        dock = self._dock_widget
        if dock is None:
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            log_warning("history refresh rejected malformed response")
            return
        jobs = payload["jobs"]
        if any(not isinstance(job, dict) for job in jobs):
            log_warning("history refresh rejected malformed jobs")
            return
        log_debug(
            f"history refresh: {len(jobs)} jobs"
            f" (has_more={bool(payload.get('has_more'))})"
        )


        self._sessions_has_more = bool(payload.get("has_more"))
        dock._on_library_history_synced(jobs, dock._library_favorite_cache)


        self._refresh_sessions_page()
        self._backfill_conversation_thumbs(jobs, account_revision)






    def _backfill_conversation_thumbs(
        self, jobs: list, account_revision: int | None = None
    ) -> None:
        if account_revision is None:
            account_revision = self._history_account_revision()
        if not self._history_revision_is_current(account_revision):
            return
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
                if rid and url and not conversation_thumbs.has_thumb(rid):
                    wanted.append((rid, url))
            session_id = entry.get("session_id") or ""
            oldest = members[-1] if members else {}
            in_url = oldest.get("input_thumb_url") or oldest.get("input_url")
            if (
                session_id
                and in_url
                and not conversation_thumbs.has_thumb(f"in-{session_id}")
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
        task.succeeded.connect(
            lambda payload, rev=account_revision:
            self._on_thumb_backfill_done(payload, rev)
        )
        task.failed.connect(lambda *_: None)
        self._hold_history_task(task)

    def _on_thumb_backfill_done(self, payload, account_revision: int | None = None) -> None:
        if not self._history_revision_is_current(account_revision):
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("blobs"), dict):
            log_warning("thumbnail backfill rejected malformed response")
            return


        from qgis.PyQt.QtGui import QPixmap

        saved = 0
        for key, blob in ((payload or {}).get("blobs") or {}).items():
            if not blob:
                continue
            pixmap = QPixmap()


            try:
                loaded = pixmap.loadFromData(blob) and not pixmap.isNull()
            except Exception:
                loaded = False
            if loaded:
                conversation_thumbs.save_thumb(key, pixmap, prune=False)
                saved += 1
        if saved:
            conversation_thumbs.prune_thumbs()
        if saved and self._dock_widget is not None:
            self._refresh_sessions_page()

    def _on_conversations_page_requested(self, before: str) -> None:
        dock = self._dock_widget
        if dock is None or self._client is None:
            return
        auth = self._auth_manager.get_auth_header()
        client = self._client
        account_revision = self._history_account_revision()

        def _work(cursor=before):
            return client.get_generation_history(auth, limit=50, before=cursor)

        task = GenericRequestTask(tr("Loading older sessions"), _work, silent=True)
        task.succeeded.connect(
            lambda payload, rev=account_revision:
            self._on_conversations_page_loaded(payload, rev)
        )
        task.failed.connect(
            lambda msg, _code, rev=account_revision:
            self._on_conversations_page_failed(msg, rev)
        )
        self._hold_history_task(task)

    def _on_conversations_page_failed(
        self, message: str, account_revision: int | None = None
    ) -> None:
        if self._history_revision_is_current(account_revision):
            self._notify(message, self._warning_level(), duration=5)

    def _on_conversations_page_loaded(
        self, payload, account_revision: int | None = None
    ) -> None:
        if not self._history_revision_is_current(account_revision):
            return
        dock = self._dock_widget
        if dock is None:
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            log_warning("history page rejected malformed response")
            return
        jobs = payload["jobs"]
        if any(not isinstance(job, dict) for job in jobs):
            log_warning("history page rejected malformed jobs")
            return
        known = {
            j.get("request_id") for j in dock._library_recent_cache if isinstance(j, dict)
        }
        fresh = [
            j
            for j in jobs
            if isinstance(j, dict) and j.get("request_id") not in known
        ]


        dock._library_recent_cache.extend(fresh)
        history_cache.save_recent_jobs(dock._library_recent_cache)
        self._sessions_has_more = bool(payload.get("has_more"))
        self._refresh_sessions_page()

    def _refresh_sessions_page(self) -> None:





        dock = self._dock_widget
        dialog = getattr(dock, "_templates_dialog", None) if dock else None
        if dialog is not None:
            dialog.set_session_jobs(dock._library_recent_cache)



    def _on_conversation_delete(self, entry: dict) -> None:
        from ..dialogs.confirm_dialog import question



        if not question(
            self._iface.mainWindow(),
            tr("Delete this session?"),
            tr(
                "This deletes its generations and their images from TerraLab "
                "servers. Layers already in your project stay. This cannot "
                "be undone."
            ),
            default_yes=False,
            destructive=True,
            yes_label=tr("Delete"),
        ):
            return

        session_id = entry.get("session_id")
        request_id = (entry.get("cover") or {}).get("request_id")
        if self._client is None or not (session_id or request_id):
            return
        auth = self._auth_manager.get_auth_header()
        client = self._client
        account_revision = self._history_account_revision()

        def _work(sid=session_id, rid=request_id):
            if sid:
                return client.delete_generation_session(auth, session_id=sid)
            return client.delete_generation_session(auth, request_id=rid)

        task = GenericRequestTask(tr("Deleting session"), _work)
        task.succeeded.connect(
            lambda _p, e=entry, rev=account_revision:
            self._apply_conversation_delete(e, rev)
        )
        task.failed.connect(
            lambda msg, code, e=entry, rev=account_revision:
            self._on_conversation_delete_failed(msg, code, e, rev)
        )
        self._hold_history_task(task)

    def _on_conversation_delete_failed(
        self, msg: str, code: str, entry: dict, account_revision: int | None = None
    ) -> None:
        if not self._history_revision_is_current(account_revision):
            return


        if code == "WRONG_REQUEST":
            self._apply_conversation_delete(entry, account_revision)
            return
        self._notify(msg, self._warning_level(), duration=6)

    def _apply_conversation_delete(
        self, entry: dict, account_revision: int | None = None
    ) -> None:
        if not self._history_revision_is_current(account_revision):
            return
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

        for rid in gone:
            conversation_thumbs.delete_thumb(rid)
        if entry.get("session_id"):
            conversation_thumbs.delete_thumb(f"in-{entry['session_id']}")
        self._refresh_sessions_page()
        self._notify(tr("Session deleted."), duration=4)
        telemetry.track(te.CONVERSATION_DELETED, {"scope": "one"})

    def _clear_local_conversations(self) -> None:








        dock = self._dock_widget
        history_cache.clear()
        if dock is None:
            return
        dock._library_recent_cache = []
        dock._library_favorite_cache = []
        dock._conversations_has_more = False


        dock.mark_library_history_dirty()


        self._thumb_backfill_started = False
        self._refresh_sessions_page()



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
        account_revision = self._history_account_revision()

        def _work(sid=session_id, text=title):
            return client.rename_generation_session(auth, sid, text)

        task = GenericRequestTask(tr("Renaming session"), _work)
        task.succeeded.connect(
            lambda payload, sid=session_id, rev=account_revision:
            self._apply_conversation_rename(sid, payload, rev)
        )
        task.failed.connect(
            lambda msg, _code, rev=account_revision:
            self._notify_history_failure(msg, rev)
        )
        self._hold_history_task(task)

    def _notify_history_failure(self, message: str, account_revision: int | None = None) -> None:
        if self._history_revision_is_current(account_revision):
            self._notify(message, self._warning_level(), duration=6)

    def _apply_conversation_rename(
        self, session_id: str, payload, account_revision: int | None = None
    ) -> None:
        if not self._history_revision_is_current(account_revision):
            return
        dock = self._dock_widget
        if dock is None:
            return

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








    @staticmethod
    def _warning_level():
        from qgis.core import Qgis

        return Qgis.MessageLevel.Warning
