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


    from ...core.prompts.prompt_presets_client import fetch_server_catalog
    catalog = fetch_server_catalog(client, force_refresh=force_refresh)
    if catalog is None:
        return {"error": "Catalog unavailable", "code": "UNAVAILABLE"}
    return catalog


class StartupMixin:
    def _toggle_dock(self):
        if self._dock_widget.isVisible():




            self._toggling_dock = True
            try:
                self._dock_widget.hide()
            finally:
                self._toggling_dock = False
            log_debug("Dock hidden (toggle)")
        else:




            if self._startup_bootstrap_done:
                self._check_activation_state()
            self._dock_widget.show()
            self._dock_widget.raise_()
            self._ensure_dock_height()
            self._emit_plugin_opened("manual")
            log_debug("Dock shown")

    def _emit_plugin_opened(self, open_source: str) -> None:



        if self._plugin_opened_emitted:
            return
        if not has_accepted_privacy_notice():
            self._pending_open_source = open_source
            return
        self._plugin_opened_emitted = True
        telemetry.track(te.PLUGIN_OPENED, {"open_source": open_source})
        telemetry.flush()

    def _ensure_dock_widget(self):










        dock = self._dock_widget
        if dock is None:
            return None
        if not dock.isVisible():
            dock.setVisible(True)
            self._ensure_dock_height()
            dock.raise_()
        return dock

    def _ensure_dock_height(self):




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
            except Exception as err:  # nosec B110
                log_debug(f"Dock height adjust skipped: {err}")
        QTimer.singleShot(0, _apply)

    def _load_export_config(self):

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














        if self._startup_bootstrap_done:
            return
        self._startup_bootstrap_done = True
        self._bootstrap_startup()

    def _require_privacy_notice(self, on_accept) -> bool:






        if has_accepted_privacy_notice():
            return True
        self._privacy_notice_on_accept = on_accept
        self._show_privacy_notice()
        return False

    def _show_privacy_notice(self):





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




        loader = GenericRequestTask(
            "AI Edit config warm",
            lambda c=self._client: c.get_config("ai-edit"),
            silent=True,
        )
        loader.succeeded.connect(self._on_activation_config_warmed)
        self._activation_config_loader = loader
        QgsApplication.taskManager().addTask(loader)

    def _on_activation_config_warmed(self, result):


        if isinstance(result, dict) and "error" not in result:
            from ...core.config_store import get_store

            store = get_store()
            if store is not None:
                store.set_activation_config(result)



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


    def _refresh_tuned_config(self):





        if self._tuned_config_task is not None:
            return
        auth = self._auth_manager.get_auth_header()
        if not auth:
            return
        task = GenericRequestTask(
            "AI Edit tuned config",
            lambda c=self._client, a=auth: c.get_bootstrap(a),
            silent=True,
        )
        task.succeeded.connect(self._on_tuned_config_loaded)
        task.failed.connect(self._on_tuned_config_failed)
        self._tuned_config_task = task
        QgsApplication.taskManager().addTask(task)

    def _on_tuned_config_loaded(self, payload):
        self._tuned_config_task = None
        config = payload.get("export_config") if isinstance(payload, dict) else None
        if isinstance(config, dict):
            self._on_export_config_loaded(config)

    def _on_tuned_config_failed(self, message: str, code: str):
        self._tuned_config_task = None
        log_debug(f"Tuned config refresh failed ({code}): {message}")

    def _on_bootstrap_failed(self, message: str, code: str):




        if (code or "").strip().upper() in NETWORK_ERROR_CODES:
            log_debug(f"Bootstrap failed on the network ({code}); skipping the legacy loaders")
            if self._dock_widget is None:
                return
            if self._auth_manager.has_activation_key():

                self._on_key_invalid(message, code)
            else:
                self._show_connectivity_notice(code, message)
            return
        self._bootstrap_fallback()

    def _bootstrap_fallback(self):

        log_debug("Bootstrap unavailable; using individual startup requests")
        self._load_export_config()
        self._load_server_catalog()
        if self._auth_manager.has_activation_key():
            self._check_activation_state()

    def _load_server_catalog(self):













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


        self._connectivity_notice_shown = False




        if not isinstance(config, dict):
            log_warning("Export config: unexpected shape, keeping the previous one")
            return
        set_server_config(config)
        costs = config.get("resolution_credit_costs")
        if self._dock_widget:
            self._dock_widget.set_resolution_credit_costs(costs if isinstance(costs, dict) else {})

    def _on_export_config_failed(self, error_message: str, code: str = ""):



        log_warning(f"Export config failed to load: {error_message} (code={code})")
        self._show_connectivity_notice(code, error_message)

    def _show_connectivity_notice(self, code: str = "", message: str = "") -> None:





        if self._connectivity_notice_shown:
            return
        self._connectivity_notice_shown = True
        from qgis.core import Qgis


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

        for task in (self._worker, self._export_worker):
            try:
                if task is not None and task.is_active():
                    return True
            except RuntimeError:
                continue
        return False

    def _on_dock_visibility_changed(self, visible: bool):
        if visible:



            self._maybe_bootstrap_on_show()


            if self._selection_tool_was_active:
                self._selection_tool_was_active = False
                if self._map_tool is not None and self._dock_widget is not None:
                    self._activate_selection_tool()
            return




        if self._in_tool_panel is not None:
            self._exit_tool_panel()


        self._selection_tool_was_active = (
            self._map_tool is not None and self._canvas.mapTool() is self._map_tool
        )
        if self._selection_tool_was_active:
            self._deactivate_selection_tool()

        if self._swipe_controller is not None and self._swipe_controller.is_active():
            self._swipe_controller.stop()




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


        if self._toggling_dock:
            return
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._selected_polygon = None
        self._selection_tool_was_active = False
        if self._map_tool:
            self._map_tool.set_has_zone(False)

    def _check_for_plugin_update(self):




        if not self._dock_widget or self._update_check_done:
            return
        if self._dock_widget.check_for_updates():
            self._update_check_done = True
            return
        self._update_check_index += 1
        if self._update_check_index < len(self._update_check_delays):
            delay = self._update_check_delays[self._update_check_index]
            QtC.safe_single_shot(delay, self._dock_widget, self._check_for_plugin_update)
