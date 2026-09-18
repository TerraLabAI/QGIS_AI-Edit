from __future__ import annotations

import time

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt, QTimer

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_export_copy
from ...core.errors import NETWORK_ERROR_CODES, TRANSIENT_SERVER_ERROR_CODES
from ...core.i18n import tr
from ...core.logger import log_debug, log_warning
from ...core.privacy_notice import (
    has_accepted_privacy_notice,
    save_privacy_notice_accepted,
)
from ...workers.generic_request_task import GenericRequestTask
from ..canvas_exporter import set_server_config
from .errors import _enrich_error_message, _localize_server_error


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
            # A toolbar toggle is a non-destructive hide: the zone, the prompt
            # and a running generation all survive it, so re-opening restores
            # the exact state. The guard tells the visibilityChanged handler
            # this is a toggle, not a close, so it keeps the zone.
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
            self._emit_plugin_opened("manual")
            log_debug("Dock shown")

    def _emit_plugin_opened(self, open_source: str) -> None:
        """Send plugin_opened once per session. Before the privacy notice is
        accepted the collector drops everything, so the source is parked and
        the event goes out from the notice's accept handler instead."""
        if self._plugin_opened_emitted:
            return
        if not has_accepted_privacy_notice():
            self._pending_open_source = open_source
            return
        self._plugin_opened_emitted = True
        telemetry.track(te.PLUGIN_OPENED, {"open_source": open_source})
        telemetry.flush()

    def _ensure_dock_widget(self):
        """Return the dock, opening it first when it is closed.

        The entry point for anything that drives the plugin without a user
        (see src/mcp_api.py): every generation path dereferences the dock, so
        it has to be on screen before they run. An already open dock is left
        untouched, so this never closes what _toggle_dock would have toggled.
        Showing it fires visibilityChanged, which runs the same first-open
        bootstrap a manual open does. Returns None only when the dock is gone,
        which happens before initGui and after unload.
        """
        dock = self._dock_widget
        if dock is None:
            return None
        if not dock.isVisible():
            dock.setVisible(True)
            self._ensure_dock_height()
            dock.raise_()
        return dock

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
            self._on_export_config_failed
        )
        QgsApplication.taskManager().addTask(self._export_config_loader)

    def _maybe_bootstrap_on_show(self):
        """Run the startup bundle the first time the dock is shown this session.

        Once-guarded so toggling the dock open/closed never refires a network
        storm. Deferring here (instead of initGui) means an idle install makes
        no network calls at all.

        The privacy notice is deliberately NOT shown here. QGIS restores an
        open dock at launch, so a notice on first show reads as a popup that
        greets the user before they have asked for anything. It is shown at
        the last moment instead, when the first generation is about to send a
        map extent off the machine (`_require_privacy_notice`). Until then the
        telemetry collector still drops every event, so nothing about this
        user's usage reaches anyone who has not read the notice.
        """
        if self._startup_bootstrap_done:
            return
        self._startup_bootstrap_done = True
        self._bootstrap_startup()

    def _require_privacy_notice(self, on_accept) -> bool:
        """Gate the first thing that leaves the machine behind the notice.

        Returns True when this profile has already accepted the current notice
        and the caller may go ahead now. Otherwise it opens the notice and
        runs `on_accept` once the user presses Continue, so the action they
        clicked still happens without them clicking twice."""
        if has_accepted_privacy_notice():
            return True
        self._privacy_notice_on_accept = on_accept
        self._show_privacy_notice()
        return False

    def _show_privacy_notice(self):
        """The notice, opened at the moment the user asks for something that
        leaves the machine. Nothing but the dialog happens until they answer:
        the queued action waits in the accept handler, and the collector drops
        every event meanwhile. Non-blocking (open, not exec) so the caller
        returns and an outside driver can reach the dialog."""
        from ..dialogs.privacy_notice_dialog import PrivacyNoticeDialog

        if self._privacy_notice_dialog is not None:
            self._privacy_notice_dialog.raise_()
            return
        dialog = PrivacyNoticeDialog(self._iface.mainWindow())
        dialog.accepted.connect(self._on_privacy_notice_accepted)
        dialog.rejected.connect(self._on_privacy_notice_declined)
        dialog.finished.connect(self._on_privacy_notice_closed)
        self._privacy_notice_dialog = dialog
        dialog.open()

    def _on_privacy_notice_accepted(self):
        save_privacy_notice_accepted()
        log_debug("Privacy notice accepted")
        if not self._startup_bootstrap_done:
            self._startup_bootstrap_done = True
            self._bootstrap_startup()
        pending = self._pending_open_source
        self._pending_open_source = None
        if pending is not None:
            self._emit_plugin_opened(pending)
        queued = self._privacy_notice_on_accept
        self._privacy_notice_on_accept = None
        if queued is not None:
            queued()

    def _on_privacy_notice_declined(self):
        """Not now: the action they clicked does not run, and the next one
        asks again. The dock stays where it is, because the user came here to
        generate, not to open a plugin."""
        log_debug("Privacy notice declined")
        self._privacy_notice_on_accept = None
        if self._dock_widget is not None:
            self._dock_widget.set_status(
                tr("Nothing was sent. Press Generate again to read the notice."),
                is_error=False,
            )

    def _on_privacy_notice_closed(self, _result: int):
        dialog = self._privacy_notice_dialog
        self._privacy_notice_dialog = None
        if dialog is not None:
            dialog.deleteLater()

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
        task.failed.connect(self._on_bootstrap_failed)
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
            # The kill switches were read when the dock was built, before this
            # answer existed. Re-read them now, or a feature switched off
            # server-side stays visible until the next activation refresh.
            if self._dock_widget is not None:
                self._dock_widget.refresh_feature_visibility()

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

    def _on_bootstrap_failed(self, message: str, code: str):
        """A network failure would hit the per-route requests too, each one
        waiting out its own timeout behind the same broken proxy: show the
        notice now. Anything else (an older server without /bootstrap) takes
        the legacy route."""
        if (code or "").strip().upper() in NETWORK_ERROR_CODES:
            log_debug(f"Bootstrap failed on the network ({code}); skipping the legacy loaders")
            if self._dock_widget is None:
                return
            if self._auth_manager.has_activation_key():
                # Same keep-session path as a failed key check on the network.
                self._on_key_invalid(message, code)
            else:
                self._show_connectivity_notice(code, message)
            return
        self._bootstrap_fallback()

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
        # The client returns whatever valid JSON arrived, so a list or a string
        # gets this far. This runs on the GUI thread, where an exception kills
        # the session, and a bad shape must leave the config already in place
        # rather than replace it with something no reader can use.
        if not isinstance(config, dict):
            log_warning("Export config: unexpected shape, keeping the previous one")
            return
        set_server_config(config)
        costs = config.get("resolution_credit_costs")
        if self._dock_widget:
            self._dock_widget.set_resolution_credit_costs(costs if isinstance(costs, dict) else {})

    def _on_export_config_failed(self, error_message: str, code: str = ""):
        """Handle export config loading failure (fallback path). The code is
        forwarded so a server-side 5xx shows the 'service unavailable' copy
        instead of blaming the user's connection."""
        log_warning(f"Export config failed to load: {error_message} (code={code})")
        self._show_connectivity_notice(code, error_message)

    def _show_connectivity_notice(self, code: str = "", message: str = "") -> None:
        """Show ONE transient, dismissible 'no connection' notice per startup
        episode. Non-blocking (message bar), deduped so the three fallback
        loaders (config + catalog + key validation) never stack notices. The
        flag resets on any successful startup fetch so a later real outage can
        notify again."""
        if self._connectivity_notice_shown:
            return
        self._connectivity_notice_shown = True
        from qgis.core import Qgis
        # A server-side failure (5xx, rate limit) is not the user's connection:
        # blaming their internet sends them debugging the wrong side.
        code_up = (code or "").strip().upper()
        if code_up in TRANSIENT_SERVER_ERROR_CODES:
            banner = get_export_copy(
                "connectivity.server_down",
                tr("Service temporarily unavailable, please retry shortly."),
                escape=True,
            )
        else:
            banner = get_export_copy(
                "connectivity.offline",
                tr("AI Edit could not reach the server. Some features need an internet connection."),
                escape=True,
            )
        # A proxy, SSL or filter failure has its own next step, which the
        # generic "no connection" banner hides: give it in the dock line.
        if code_up in NETWORK_ERROR_CODES:
            detail = _enrich_error_message(message, code_up)
        else:
            detail = _localize_server_error("", code)
        self._notify(
            banner,
            level=Qgis.MessageLevel.Warning,
            duration=8,
        )
        if self._dock_widget:
            self._dock_widget.set_status(detail or tr("No internet connection."), is_error=True)

    def _generation_in_flight(self) -> bool:
        """A canvas export or a generation is running for the current zone."""
        for task in (self._worker, self._export_worker):
            try:
                if task is not None and task.is_active():
                    return True
            except RuntimeError:  # the task manager already deleted it
                continue
        return False

    def _on_dock_visibility_changed(self, visible: bool):
        if visible:
            # First real show (toolbar, launch shortcut, Panels menu, or QGIS
            # restoring the dock open at launch) is what kicks off the
            # deferred startup network.
            self._maybe_bootstrap_on_show()
            # Whatever way the dock came back, the zone tool it stood down on
            # the hide returns with it.
            if self._selection_tool_was_active:
                self._selection_tool_was_active = False
                if self._map_tool is not None and self._dock_widget is not None:
                    self._activate_selection_tool()
            return
        # Every hide (toolbar button, title-bar X, Panels menu) leaves a tool
        # panel the way Done does: its map tool goes back to the one it
        # borrowed from, a running vectorize stops, the pointer is QGIS's
        # again. Without this a hidden panel kept its tool armed on the map.
        if self._in_tool_panel is not None:
            self._exit_tool_panel()
        # The zone tool stands down while nobody can see the dock, so a stray
        # map click can't draw a zone behind the user's back.
        self._selection_tool_was_active = (
            self._map_tool is not None and self._canvas.mapTool() is self._map_tool
        )
        if self._selection_tool_was_active:
            self._deactivate_selection_tool()
        # Disarm swipe: without the dock the toggle is unreachable.
        if self._swipe_controller is not None and self._swipe_controller.is_active():
            self._swipe_controller.stop()
        # A generation is never cancelled by a hide: its credits are already
        # booked, so a stop would charge the user for nothing. It keeps
        # running, the result lands on the map, and the dock shows it when it
        # opens again. The zone it runs on stays too.
        if self._generation_in_flight():
            self._notify(
                get_export_copy(
                    "flows.dock_hidden.generation_continues",
                    tr("Still generating. The result is added to your map when ready."),
                    escape=True,
                ),
                duration=6,
            )
            return
        # A toolbar toggle keeps the zone for the next open; a close (X)
        # clears it, the way it always has.
        if self._toggling_dock:
            return
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._selected_polygon = None
        self._selection_tool_was_active = False
        if self._map_tool:
            self._map_tool.set_has_zone(False)

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
