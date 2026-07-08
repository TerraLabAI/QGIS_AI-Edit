from __future__ import annotations

import time

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt, QTimer

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.i18n import tr
from ...core.logger import log_debug, log_warning
from ...workers.generic_request_task import GenericRequestTask
from ..canvas_exporter import set_server_config
from .errors import _localize_server_error


def _server_catalog_request(client, force_refresh: bool):
    """Fetch server preset catalog. Returns the dict on success or a sentinel
    error dict so GenericRequestTask routes via ``failed``."""
    from ...core.prompts.prompt_presets_client import fetch_server_catalog
    catalog = fetch_server_catalog(client, force_refresh=force_refresh)
    if catalog is None:
        return {"error": "Catalog unavailable", "code": "UNAVAILABLE"}
    return catalog


class StartupMixin:
    def _toggle_dock(self):
        if self._dock_widget.isVisible():
            # A toolbar toggle is a pure, non-destructive hide: it must preserve
            # everything (in-progress generation, selected zone, tool panel) so
            # re-opening restores the exact state. The guard tells the
            # visibilityChanged handler this is a toggle, not a real close, so it
            # skips the teardown that cancels the generation. The selection tool
            # is the one thing we stand down while hidden, so a stray map click
            # can't draw a zone behind the user's back; it returns on re-open.
            self._selection_tool_was_active = self._canvas.mapTool() is self._map_tool
            if self._selection_tool_was_active:
                self._deactivate_selection_tool()
            self._toggling_dock = True
            try:
                self._dock_widget.hide()
            finally:
                self._toggling_dock = False
            log_debug("Dock hidden (toggle)")
        else:
            # On the FIRST open the deferred bootstrap (fired by show() below via
            # visibilityChanged) already validates the key + fetches credits, so
            # skip this call to avoid a duplicate /usage. Later opens use it for
            # the cheap 900s-guarded revalidation.
            if self._startup_bootstrap_done:
                self._check_activation_state()
            self._dock_widget.show()
            self._dock_widget.raise_()
            self._ensure_dock_height()
            # Restore the selection tool if it was active when we toggled away.
            if self._selection_tool_was_active:
                self._activate_selection_tool()
                self._selection_tool_was_active = False
            if not self._plugin_opened_emitted:
                self._plugin_opened_emitted = True
                telemetry.track(te.PLUGIN_OPENED, {"open_source": "manual"})
                telemetry.flush()
            log_debug("Dock shown")

    def _ensure_dock_height(self):
        """Open AI Edit tall enough to actually work in. QGIS can dock it as a
        short box; grow it to most of the window height. Never shrinks a dock
        the user has already made taller. Deferred one tick so the resize runs
        after QGIS finishes laying the dock out."""
        def _apply():
            try:
                dock = self._dock_widget
                mw = self._iface.mainWindow()
                if dock is None or mw is None or not dock.isVisible():
                    return
                target = int(mw.height() * 0.85)
                if dock.height() >= target:
                    return
                mw.resizeDocks([dock], [target], Qt.Orientation.Vertical)
            except Exception as err:  # nosec B110 - sizing is best-effort.
                log_debug(f"Dock height adjust skipped: {err}")
        QTimer.singleShot(0, _apply)

    def _load_export_config(self):
        """Fetch export config from server in background thread."""
        self._export_config_loader = GenericRequestTask(
            "AI Edit export config",
            self._client.get_export_config,
            silent=True,
        )
        self._export_config_loader.succeeded.connect(self._on_export_config_loaded)
        self._export_config_loader.failed.connect(
            lambda msg, code: self._on_export_config_failed(f"Server error: {msg}")
        )
        QgsApplication.taskManager().addTask(self._export_config_loader)

    def _maybe_bootstrap_on_show(self):
        """Run the startup bundle the first time the dock is shown this session.

        Once-guarded so toggling the dock open/closed never refires a network
        storm. Deferring here (instead of initGui) means an idle install makes
        no network calls at all.
        """
        if self._startup_bootstrap_done:
            return
        self._startup_bootstrap_done = True
        self._bootstrap_startup()

    def _bootstrap_startup(self):
        """One background call for export config + preset catalog + key
        validation/credits. Replaces three separate startup requests; falls
        back to the legacy loaders when the server predates /bootstrap."""
        auth = self._auth_manager.get_auth_header()
        task = GenericRequestTask(
            "AI Edit bootstrap",
            lambda c=self._client, a=auth: c.get_bootstrap(a),
            silent=True,
        )
        task.succeeded.connect(self._on_bootstrap_loaded)
        task.failed.connect(lambda _msg, _code: self._bootstrap_fallback())
        self._bootstrap_task = task
        QgsApplication.taskManager().addTask(task)
        self._warm_activation_config()

    def _warm_activation_config(self):
        """Fetch the activation/upsell config off-thread so the first free-tier
        credits load reads it from cache instead of doing a blocking network GET
        on the UI thread. Silent: on failure the cache-only call sites fall back
        to the default upgrade URL."""
        loader = GenericRequestTask(
            "AI Edit config warm",
            lambda c=self._client: c.get_config("ai-edit"),
            silent=True,
        )
        loader.succeeded.connect(self._on_activation_config_warmed)
        self._activation_config_loader = loader
        QgsApplication.taskManager().addTask(loader)

    def _on_activation_config_warmed(self, result):
        """Store the warmed config on the main thread (mirrors the export-config
        path). A dict with an error key is ignored; the fallback stays in play."""
        if isinstance(result, dict) and "error" not in result:
            from ...core.config_store import get_store

            store = get_store()
            if store is not None:
                store.set_activation_config(result)

    def _on_bootstrap_loaded(self, payload):
        if not isinstance(payload, dict) or "export_config" not in payload:
            self._bootstrap_fallback()
            return
        config = payload.get("export_config")
        if isinstance(config, dict):
            self._on_export_config_loaded(config)
        catalog_payload = payload.get("catalog")
        if isinstance(catalog_payload, dict):
            try:
                from ...core.prompts.prompt_presets_client import store_catalog

                catalog = store_catalog(catalog_payload)
                if catalog is not None:
                    self._last_catalog_fetch_unix = time.time()
                    self._on_server_catalog_loaded(catalog)
            except Exception as err:  # nosec B110
                log_warning(f"Bootstrap catalog handling failed: {err}")
        usage = payload.get("usage")
        if isinstance(usage, dict) and "error" in usage:
            self._on_key_invalid(
                str(usage.get("error", "")), str(usage.get("code", ""))
            )
        elif isinstance(usage, dict):
            self._on_key_valid(usage)
        # usage None = signed-out startup; the sign-up screen is already shown.

    def _bootstrap_fallback(self):
        """Older server without /bootstrap: run the three legacy loaders."""
        log_debug("Bootstrap unavailable; using individual startup requests")
        self._load_export_config()
        self._load_server_catalog()
        if self._auth_manager.has_activation_key():
            self._check_activation_state()

    def _load_server_catalog(self):
        """Fetch the AI Edit preset catalog in the background and hand it to
        the dock when ready. Failures are silent - the stale cache or the
        local fallback covers the dialog in the meantime.

        `force_refresh=True` is the stale-while-revalidate move: the dock
        already shows the stale cache synchronously for instant UX, and this
        background worker always re-hits the server so the user gets the
        latest catalog within seconds. Without force_refresh, fetch would
        short-circuit on cache hit and the user could stay on a stale catalog
        until the TTL expired (painful right after a server-side push)."""
        # Every library open lands here; a catalog fetched less than a minute
        # ago is plenty fresh, so skip the refetch instead of re-hitting the
        # server on each open.
        now = time.time()
        if now - getattr(self, "_last_catalog_fetch_unix", 0.0) < 60.0:
            return
        self._last_catalog_fetch_unix = now
        self._catalog_loader = GenericRequestTask(
            "AI Edit preset catalog",
            lambda c=self._client: _server_catalog_request(c, force_refresh=True),
            silent=True,
        )
        self._catalog_loader.succeeded.connect(self._on_server_catalog_loaded)
        self._catalog_loader.failed.connect(lambda _msg, _code: self._on_server_catalog_failed())
        QgsApplication.taskManager().addTask(self._catalog_loader)

    def _on_server_catalog_loaded(self, catalog: dict):
        if self._dock_widget is not None:
            self._dock_widget.set_server_catalog(catalog)

    def _on_server_catalog_failed(self):
        log_debug("Server catalog: background fetch failed (stale or local fallback in effect)")

    def _on_export_config_loaded(self, config):
        """Set global export config from server response."""
        # Connection works again: re-arm the one-shot connectivity notice.
        self._connectivity_notice_shown = False
        set_server_config(config)
        costs = config.get("resolution_credit_costs", {})
        if self._dock_widget:
            self._dock_widget.set_resolution_credit_costs(costs)

    def _on_export_config_failed(self, error_message: str):
        """Handle export config loading failure (fallback path)."""
        log_warning(f"Export config failed to load: {error_message}")
        self._show_connectivity_notice()

    def _show_connectivity_notice(self, code: str = "") -> None:
        """Show ONE transient, dismissible 'no connection' notice per startup
        episode. Non-blocking (message bar), deduped so the three fallback
        loaders (config + catalog + key validation) never stack notices. The
        flag resets on any successful startup fetch so a later real outage can
        notify again."""
        if self._connectivity_notice_shown:
            return
        self._connectivity_notice_shown = True
        from qgis.core import Qgis
        self._notify(
            tr("AI Edit could not reach the server. Some features need an internet connection."),
            level=Qgis.MessageLevel.Warning,
            duration=8,
        )
        if self._dock_widget:
            detail = _localize_server_error("", code) or tr("No internet connection.")
            self._dock_widget.set_status(detail, is_error=True)

    def _on_dock_visibility_changed(self, visible: bool):
        if visible:
            # First real show (toolbar, launch shortcut, or QGIS restoring the
            # dock open at launch) is what kicks off the deferred startup network.
            self._maybe_bootstrap_on_show()
            return
        # A toolbar toggle hide is non-destructive (see _toggle_dock): preserve
        # the in-progress generation and all dock state so re-opening restores
        # it. Only a real close (title-bar X) runs the teardown below.
        if self._toggling_dock:
            return
        # Closing the dock from inside a tool panel (Mark up / Vectorize)
        # must reset to the base view; otherwise the next open stacks the
        # main widget on top of the still-visible tool panel (issue #164).
        # Done before the mid-generation early-return so it always runs.
        if self._in_tool_panel is not None:
            self._exit_tool_panel()
        # Mid-generation: cancel through _on_stop so refund + state reset run together.
        if self._worker is not None and self._worker.is_active():
            self._on_stop()
            return
        self._deactivate_selection_tool()
        self._clear_selection_rectangle()
        self._selected_extent = None
        if self._map_tool:
            self._map_tool.set_has_zone(False)
        # Disarm swipe: without the dock the toggle is unreachable.
        if self._swipe_controller is not None and self._swipe_controller.is_active():
            self._swipe_controller.stop()

    def _check_for_plugin_update(self):
        """Poll QGIS's plugin metadata for a newer version, retrying on a backoff.

        Early-returns if the dock was unloaded so a stale QTimer fire can't crash.
        """
        if not self._dock_widget or self._update_check_done:
            return
        if self._dock_widget.check_for_updates():
            self._update_check_done = True
            return
        self._update_check_index += 1
        if self._update_check_index < len(self._update_check_delays):
            delay = self._update_check_delays[self._update_check_index]
            QtC.safe_single_shot(delay, self._dock_widget, self._check_for_plugin_update)
