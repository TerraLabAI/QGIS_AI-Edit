from __future__ import annotations

from ...core import qt_compat as QtC
from ...core.prompts.prompt_presets import format_template_prompt


class DockLibraryMixin:
    """Prompt library dialog plumbing and Recent/Favorites history cache
    for AIEditDockWidget."""

    def set_library_dependencies(self, client, auth_manager):
        """Plugin hands us its TerraLabClient + AuthManager so the Prompt
        library dialog can sync Recent/Favorites with the server. Optional -
        if not set, the dialog falls back to local cache only."""
        self._library_client = client
        self._library_auth_manager = auth_manager

    def set_server_catalog(self, catalog: dict | None) -> None:
        """Hand the server-fetched preset catalog (v2 shape) to the dialog.
        When None, the prompt library falls back to the locally-cached
        catalog if present; with neither, themed tabs render empty."""
        self._server_catalog = catalog

    def _main_window_for_dialog(self):
        """Parent to use for popup dialogs.

        On macOS, parenting a dialog to a QDockWidget (especially when the
        dock is floating, or when QGIS itself is in a fullscreen Space) makes
        the dialog open in its own Mission Control Space, yanking the user
        out of the QGIS workspace. The QGIS main window is always anchored
        to the right Space, so we use it as the parent instead.

        Falls back to `self` if iface isn't reachable for any reason.
        """
        try:
            from qgis.utils import iface
            mw = iface.mainWindow() if iface is not None else None
            if mw is not None:
                return mw
        except Exception:  # nosec B110 - any failure falls back below.
            pass
        return self

    def _open_templates_dialog(self) -> dict | None:
        """Open the prompt library. Returns selected preset or None.
        template_selected fires only for curated picks (Top Picks / themed);
        Recent + Favorites have their own telemetry events that don't carry
        user prompt text."""
        # Reentrancy guard: a fast double-click can emit `templates_clicked`
        # twice before the first modal grabs input, stacking two nested exec()
        # loops over the same widgets. The second teardown then races the first
        # and crashes QGIS. One library at a time.
        if getattr(self, "_library_open", False):
            return None
        self._library_open = True
        # Kick off a background catalog refetch so the NEXT open is fresh.
        # This open uses whatever catalog the dock currently has.
        self.catalog_refresh_requested.emit()
        from ..dialogs.prompt_templates_dialog import PromptTemplatesDialog

        auth_provider = None
        if self._library_auth_manager is not None:
            auth_provider = self._library_auth_manager.get_auth_header
        # Parent the dialog to the QGIS main window, not to this dock widget.
        # On macOS in fullscreen, a dialog parented to a (possibly floating)
        # dock widget gets put into its own Mission Control Space and steals
        # the user out of QGIS. Anchoring to mainWindow() keeps the popup in
        # the same Space as QGIS itself.
        parent_window = self._main_window_for_dialog()
        browse_only = self._prompt_container.is_readonly() or self._result_prompt_container.is_readonly()
        # Build inside the try so a failure here still clears _library_open
        # (otherwise the guard above would wedge the library shut for good).
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
            # Add-to-map / download run in a background task while the modal
            # stays open, so the user can act on several past generations in
            # one visit.
            dlg.generation_action.connect(self._on_history_generation_action)
            dlg.history_synced.connect(self._on_library_history_synced)
            if dlg.exec():
                # Full-restore beats prompt selection: the user wants the whole
                # generation context (prompt + refs + zone) back, not just text.
                restore = dlg.get_restore_job()
                if restore:
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
            if dlg is not None:
                dlg.deleteLater()

    def _on_library_history_synced(self, recent: list, favorites: list) -> None:
        """Store the library's freshly fetched/edited Recent + Favorites so the
        next open is instant. Fresh until a new generation marks it dirty. Also
        persisted to disk so the next SESSION opens warm too."""
        self._library_recent_cache = list(recent or [])
        self._library_favorite_cache = list(favorites or [])
        self._library_history_loaded = True
        self._library_history_dirty = False
        from ...core.prompts import history_cache

        history_cache.save_recent_jobs(self._library_recent_cache)
        history_cache.save_favorite_jobs(self._library_favorite_cache)

    def mark_library_history_dirty(self) -> None:
        """Force the next library open to refetch Recent/Favorites (e.g. after a
        new generation completes so it shows up)."""
        self._library_history_dirty = True

    def _on_history_generation_action(self, action: str, job: dict):
        """Route a past-generation action from the prompt library up to the
        plugin, which owns the download + write + layer-add work."""
        if action == "add_to_map":
            self.history_add_to_map.emit(job)
        elif action in ("download", "download_output"):
            self.history_download.emit({**job, "download_side": "output"})
        elif action == "download_input":
            self.history_download.emit({**job, "download_side": "input"})

    def _on_browse_templates_clicked(self):
        """Open templates dialog. Fill whichever prompt input is active."""
        preset = self._open_templates_dialog()
        if not preset:
            return

        self.prime_prompt_from_preset(preset)
        # The dialog is a deliberate user action, so put the caret in the prompt
        # they just picked (the onboarding path skips this to avoid stealing
        # focus from the canvas the user is about to draw on).
        target = self._result_prompt_input if self._result_section.isVisible() else self._prompt_input
        target.setFocus()

    def prime_prompt_from_preset(self, preset: dict):
        """Fill the active prompt input from a preset dict (id/label/prompt) and
        arm the template so later edits keep the association. Used by the
        onboarding to pre-load a land-cover prompt; a no-op if the preset or its
        prompt text is missing (catalog unavailable offline)."""
        if not preset or not preset.get("prompt"):
            return
        self._active_template_id = str(preset.get("id") or "") or None
        self._active_template_name = str(preset.get("label") or "") or None
        if self._result_section.isVisible():
            target = self._result_prompt_input
            update_enabled = self._update_result_generate_enabled
            adjust_height = self._adjust_result_prompt_height
        else:
            target = self._prompt_input
            update_enabled = self._update_generate_enabled
            adjust_height = self._adjust_prompt_height
        target.blockSignals(True)
        target.setPlainText(format_template_prompt(preset["prompt"]))
        target.blockSignals(False)
        target.moveCursor(QtC.CursorEnd)
        update_enabled()
        adjust_height()
