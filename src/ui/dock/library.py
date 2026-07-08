from __future__ import annotations

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.i18n import tr
from ...core.prompts.prompt_presets import format_template_prompt
from ..panel_helpers import main_window_for_dialog


class DockLibraryMixin:



    def _feature_blocked(self, name: str) -> bool:







        import html

        from ...core.auth.activation_manager import (
            feature_disabled_message,
            is_feature_enabled,
        )

        if is_feature_enabled(name):
            return False
        message = feature_disabled_message(
            name,
            tr("This feature is temporarily unavailable. Please try again later."),
        )


        self._show_status_box(html.escape(message, quote=False), "warning")
        return True

    def _gated_by_feature(self, name: str, handler):



        def _forward(*args):
            if not self._feature_blocked(name):
                handler(*args)

        return _forward

    def set_library_dependencies(self, client, auth_manager):



        self._library_client = client
        self._library_auth_manager = auth_manager
        panel = getattr(self, "_vectorize_panel", None)
        if panel is not None:
            panel.set_request_context(client, auth_manager)

    def set_server_catalog(self, catalog: dict | None) -> None:



        self._server_catalog = catalog

    def _main_window_for_dialog(self):

        return main_window_for_dialog(self)

    def _open_templates_dialog(self, open_sessions: bool = False) -> dict | None:









        if getattr(self, "_library_open", False):
            return None
        if self._feature_blocked("library"):
            return None
        self._library_open = True


        self.catalog_refresh_requested.emit()
        from ..dialogs.prompt_templates_dialog import PromptTemplatesDialog

        auth_provider = None
        if self._library_auth_manager is not None:
            auth_provider = self._library_auth_manager.get_auth_header





        parent_window = self._main_window_for_dialog()
        browse_only = self._prompt_container.is_readonly() or self._result_prompt_container.is_readonly()


        dlg = None
        try:
            history_fresh = (
                self._library_history_loaded and not self._library_history_dirty
            )
            dlg = PromptTemplatesDialog(
                parent_window,
                client=self._library_client,
                auth_provider=auth_provider,
                server_catalog=self._server_catalog,
                browse_only=browse_only,
                recent_jobs=self._library_recent_cache,
                favorite_jobs=self._library_favorite_cache,
                history_fresh=history_fresh,
            )



            dlg.generation_action.connect(self._on_history_generation_action)
            dlg.history_synced.connect(self._on_library_history_synced)




            dlg.session_resume_requested.connect(self._on_session_resume)
            dlg.session_delete_requested.connect(self.conversation_delete.emit)
            dlg.session_rename_requested.connect(self.conversation_rename.emit)
            dlg.sessions_refresh_requested.connect(self._on_sessions_page_opened)
            self._templates_dialog = dlg
            if open_sessions:
                dlg.open_at_sessions()
            if dlg.exec():


                restore = dlg.get_restore_job()
                if restore:
                    if not self._feature_blocked("history"):
                        self.history_restore.emit(restore)
                    return None
                preset = dlg.get_selected_preset()
                if preset and not preset.get("from_recent") and not preset.get("from_favorites"):
                    self.template_selected.emit(
                        str(preset.get("id") or ""),
                        str(preset.get("label") or ""),
                    )
                return preset
            return None
        finally:
            self._library_open = False
            self._templates_dialog = None
            if dlg is not None:
                dlg.deleteLater()

    def _on_sessions_page_opened(self) -> None:



        telemetry.track(te.CONVERSATIONS_OPENED)
        self.conversations_refresh_requested.emit()

    def _on_session_resume(self, cover: dict) -> None:


        if not cover or self._feature_blocked("history"):
            return
        self.history_restore.emit(cover)

    def _on_open_sessions_page(self):



        preset = self._open_templates_dialog(open_sessions=True)
        if preset:
            self.prime_prompt_from_preset(preset)

    def _on_library_history_synced(self, recent: list, favorites: list) -> None:



        self._library_recent_cache = list(recent or [])
        self._library_favorite_cache = list(favorites or [])
        self._library_history_loaded = True
        self._library_history_dirty = False
        from ...core.prompts import history_cache

        history_cache.save_recent_jobs(self._library_recent_cache)
        history_cache.save_favorite_jobs(self._library_favorite_cache)



    def mark_library_history_dirty(self) -> None:


        self._library_history_dirty = True

    def _on_history_generation_action(self, action: str, job: dict):


        if self._feature_blocked("history"):
            return
        if action == "add_to_map":
            self.history_add_to_map.emit(job)
        elif action in ("download", "download_output"):
            self.history_download.emit({**job, "download_side": "output"})
        elif action == "download_input":
            self.history_download.emit({**job, "download_side": "input"})

    def _on_browse_templates_clicked(self):

        preset = self._open_templates_dialog()
        if not preset:
            return

        self.prime_prompt_from_preset(preset)



        target = self._result_prompt_input if self._result_section.isVisible() else self._prompt_input
        target.setFocus()

    def prime_prompt_from_preset(self, preset: dict):




        if not preset or not preset.get("prompt"):
            return
        self._active_template_id = str(preset.get("id") or "") or None
        self._active_template_name = str(preset.get("label") or "") or None
        if self._result_section.isVisible():
            target = self._result_prompt_input
            container = self._result_prompt_container
            update_enabled = self._update_result_generate_enabled
            adjust_height = self._adjust_result_prompt_height
        else:
            target = self._prompt_input
            container = self._prompt_container
            update_enabled = self._update_generate_enabled
            adjust_height = self._adjust_prompt_height
        target.blockSignals(True)
        target.setPlainText(format_template_prompt(preset["prompt"]))
        target.blockSignals(False)
        target.moveCursor(QtC.CursorEnd)
        update_enabled()
        adjust_height()


        container.refresh_favorite_star()
