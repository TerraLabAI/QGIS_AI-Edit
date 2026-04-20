from __future__ import annotations

import os
import time

from qgis.core import QgsPointXY, QgsRectangle
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import QSettings, QThread, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import QAction, QApplication

from ..api.terralab_client import TerraLabClient
from ..core import telemetry
from ..core.activation_manager import (
    clear_activation,
    clear_config_cache,
    get_activation_key,
    get_dashboard_url,
    get_server_config,
    has_consent,
    save_activation,
    save_consent,
    validate_key_with_server,
)
from ..core.auth_manager import AuthManager
from ..core.generation_service import GenerationService
from ..core.i18n import tr
from ..core.logger import log, log_debug, log_warning
from ..core.pipeline_context import PipelineContext
from ..workers.generation_worker import GenerationWorker
from .canvas_exporter import (
    export_canvas_zone,
    has_server_config,
    set_server_config,
)
from .dock_widget import AIEditDockWidget
from .raster_writer import add_geotiff_to_project, get_output_dir
from .selection_map_tool import RectangleSelectionTool

DASHBOARD_ERROR_URL = (
    "https://terra-lab.ai/dashboard"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dashboard_error"
)
SUBSCRIBE_ERROR_URL = (
    "https://terra-lab.ai/dashboard/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=subscribe"
)


class _CreditsLoaderWorker(QThread):
    """Background worker to fetch usage/credits without blocking QGIS."""

    loaded = pyqtSignal(dict)

    def __init__(self, auth_manager):
        super().__init__()
        self._auth_manager = auth_manager

    def run(self):
        result = self._auth_manager.get_usage_info()
        if "error" not in result:
            self.loaded.emit(result)


class _ExportConfigLoaderWorker(QThread):
    """Background worker to fetch export config from server without blocking QGIS."""

    loaded = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, client):
        super().__init__()
        self._client = client

    def run(self):
        try:
            log_debug("Export config: fetching from server...")
            config = self._client.get_export_config()
            if "error" not in config:
                log_debug("Export config: loaded from server")
                self.loaded.emit(config)
            else:
                error_msg = config.get("error", "Unknown error")
                log_warning(f"Export config: server returned error - {error_msg}")
                self.failed.emit(f"Server error: {error_msg}")
        except Exception as e:
            log_warning(f"Export config: fetch failed - {e}")
            self.failed.emit(f"Connection error: {e}")


def _enrich_error_message(error: str, code: str = "") -> str:
    """Add actionable guidance to error messages based on error code."""
    if code in ("INVALID_KEY", "SUBSCRIPTION_INACTIVE", "FREE_TIER_EXPIRED"):
        return f'{error} — <a href="{DASHBOARD_ERROR_URL}">Check your dashboard</a>'
    if code == "TRIAL_EXHAUSTED":
        config = get_server_config()
        dashboard = config.get("upgrade_url", get_dashboard_url())
        promo_text = config.get("promo_text", "") if config.get("promo_active") else ""
        parts = [error]
        if promo_text:
            parts.append(promo_text)
        parts.append(f'<a href="{dashboard}">{tr("Subscribe now")}</a>')
        return ". ".join(parts)
    if code == "PROXY_ERROR":
        return f"{error} — Check QGIS proxy settings: Settings > Options > Network"
    if code == "SSL_ERROR":
        return (
            f"{error} — If you are on a corporate network, "
            f"ask your IT team about SSL inspection settings"
        )
    if code in ("DNS_ERROR", "NO_INTERNET"):
        return f"{error} — Check your internet connection"
    if code == "TIMEOUT":
        return f"{error} — Try again, or check your internet speed"
    if code == "CONNECTION_REFUSED":
        return f"{error} — The service may be temporarily unavailable"
    if code == "AUTH_ERROR":
        return f'{error} — <a href="{DASHBOARD_ERROR_URL}">Check your dashboard</a>'
    return error


class AIEditPlugin:
    """Main QGIS plugin class. Orchestrates all tiers."""

    def __init__(self, iface):
        self._iface = iface
        self._canvas = iface.mapCanvas()
        self._dock_widget = None
        self._map_tool = None
        self._action = None
        self._settings_action = None
        self._selected_extent = None
        self._worker = None
        self._selection_rubber_band = None
        self._previous_map_tool = None
        self._terralab_toolbar = None
        self._export_config_loader = None
        self._credits_loader = None
        self._generation_counter = 0
        # Preserved for retry (re-generate from original, not from AI result)
        self._last_image_b64 = None
        self._last_extent_dict = None
        self._last_crs_wkt = None
        self._last_aspect_ratio = None
        self._last_suggested_res = None

        # Initialize tiers
        self._dev_mode = False
        self._skip_trial_check = False
        self._client = self._create_client()
        self._auth_manager = AuthManager(self._client)
        self._generation_service = GenerationService(self._client)

        # MCP programmatic API
        from ..mcp_api import EditMCPAPI
        self.mcp_api = EditMCPAPI(self)

    @property
    def auth_manager(self):
        return self._auth_manager

    @property
    def generation_service(self):
        return self._generation_service

    @property
    def client(self):
        return self._client

    def _create_client(self):
        """Create TerraLabClient. Reads TERRALAB_BASE_URL from .env.local."""
        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(plugin_dir, ".env.local")
        if os.path.isfile(env_path):
            env_vars = self._load_env_file(env_path)
            self._dev_mode = env_vars.get("DEBUG", "").lower() == "true"
            self._skip_trial_check = (
                env_vars.get("SKIP_TRIAL_CHECK", "").lower() == "true"
            )
        return TerraLabClient()

    @staticmethod
    def _load_env_file(path: str) -> dict:
        """Parse a simple KEY=VALUE env file (ignores comments and blank lines)."""
        env = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip().strip('"').strip("'")
        return env

    def initGui(self):
        """Called by QGIS when plugin is loaded."""
        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        icon_path = os.path.join(plugin_dir, "resources", "icons", "icon.png")

        from .terralab_menu import (
            _UTILITY_SEPARATOR,
            add_plugin_to_menu,
            add_to_plugins_menu,
            get_or_create_terralab_menu,
        )

        main_window = self._iface.mainWindow()
        self._terralab_menu = get_or_create_terralab_menu(main_window)

        self._action = QAction(
            QIcon(icon_path) if os.path.exists(icon_path) else QIcon(),
            tr("AI Edit"),
            main_window,
        )
        self._action.setToolTip(tr("AI Edit by TerraLab\nAI-powered image editing for geospatial data"))
        self._action.triggered.connect(self._toggle_dock)
        add_plugin_to_menu(self._terralab_menu, self._action, "ai-edit")

        from .terralab_toolbar import (
            add_action_to_toolbar,
            get_or_create_terralab_toolbar,
        )

        self._terralab_toolbar = get_or_create_terralab_toolbar(self._iface)
        add_action_to_toolbar(self._terralab_toolbar, self._action, "ai-edit")

        add_to_plugins_menu(self._iface, self._action)

        # Cross-plugin discovery: show AI Segmentation entry (#47).
        from .cross_plugin_discovery import make_ai_seg_action
        ai_seg_icon_path = os.path.join(plugin_dir, "resources", "icons", "ai_segmentation_icon.png")
        ai_seg_icon = QIcon(ai_seg_icon_path) if os.path.exists(ai_seg_icon_path) else None
        self._ai_seg_action = make_ai_seg_action(
            main_window,
            self._iface,
            tr("AI Segmentation"),
            tr("Segment elements on raster images using AI (opens AI Segmentation plugin)"),
            icon=ai_seg_icon,
        )
        add_action_to_toolbar(self._terralab_toolbar, self._ai_seg_action, "ai-segmentation", is_cross_promo=True)
        add_plugin_to_menu(self._terralab_menu, self._ai_seg_action, "ai-segmentation")
        add_to_plugins_menu(self._iface, self._ai_seg_action)

        # Add "Settings" to the TerraLab menu utility section
        settings_icon = QIcon(":/images/themes/default/mActionOptions.svg")
        self._settings_action = QAction(settings_icon, tr("Settings"), main_window)
        self._settings_action.setObjectName("_terralab_settings_action")
        # Prevent macOS from moving this to the app menu (Cocoa treats "Settings" as Preferences)
        self._settings_action.setMenuRole(QAction.MenuRole.NoRole)
        self._settings_action.triggered.connect(self._on_settings_clicked)
        # Insert before "Check for Updates" (first action after the separator)
        insert_before = None
        found_sep = False
        for a in self._terralab_menu.actions():
            if a.objectName() == _UTILITY_SEPARATOR:
                found_sep = True
                continue
            if found_sep:
                insert_before = a
                break
        if insert_before:
            self._terralab_menu.insertAction(insert_before, self._settings_action)
        else:
            self._terralab_menu.addAction(self._settings_action)

        # Create dock widget and register it with QGIS (hidden by default)
        self._dock_widget = AIEditDockWidget(self._iface.mainWindow())
        from ..core import qt_compat as QtC
        self._iface.addDockWidget(QtC.RightDockWidgetArea, self._dock_widget)
        self._dock_widget.hide()
        self._dock_widget.select_zone_clicked.connect(self._activate_selection_tool)
        self._dock_widget.stop_clicked.connect(self._on_stop)
        self._dock_widget.generate_clicked.connect(self._on_generate)
        self._dock_widget.retry_clicked.connect(self._on_retry)
        self._dock_widget.new_zone_clicked.connect(self._on_new_zone)
        self._dock_widget.template_selected.connect(self._on_template_selected)
        self._dock_widget.activation_attempted.connect(self._on_activation_attempted)
        self._dock_widget.change_key_clicked.connect(self._on_change_key)
        self._dock_widget.settings_clicked.connect(self._on_settings_clicked)

        # Create map tool
        self._map_tool = RectangleSelectionTool(self._canvas)
        self._map_tool.selection_made.connect(self._on_zone_selected)
        self._map_tool.zone_too_small.connect(self._on_zone_too_small)

        # Restore saved activation key
        settings = QSettings()
        saved_key = get_activation_key(settings)
        if saved_key:
            self._auth_manager.set_activation_key(saved_key)
            self._dock_widget.set_activation_key(saved_key)
            self._dock_widget.set_activated(True)
            self._settings_action.setEnabled(True)
            self._refresh_credits()
        else:
            # No key stored: force activation screen (clears stale dev state)
            clear_activation(settings)
            self._dock_widget.set_activated(False)
            self._settings_action.setEnabled(False)

        from .error_report_dialog import start_log_collector

        start_log_collector()

        # Initialize telemetry (respects consent + auth, non-blocking)
        telemetry.init_telemetry(self._client, self._auth_manager, "0.1.4")

        # Load export config in background (non-blocking)
        self._load_export_config()

        if self._dev_mode:
            log("AI Edit plugin loaded [DEV MODE]")
        else:
            log("AI Edit plugin loaded")
        if self._skip_trial_check:
            log_warning("DEV MODE: SKIP_TRIAL_CHECK is active — auth checks bypassed")

    def unload(self):
        """Called by QGIS when plugin is unloaded."""
        # Stop generation worker with fallback to terminate
        if self._worker and self._worker.isRunning():
            self._generation_service.cancel()
            for sig in [self._worker.finished, self._worker.progress, self._worker.error]:
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            self._worker.quit()
            if not self._worker.wait(3000):
                self._worker.terminate()
                self._worker.wait(1000)
        self._worker = None

        # Stop background loader workers and disconnect signals
        for loader in [self._export_config_loader, self._credits_loader]:
            if loader:
                try:
                    loader.disconnect()
                except (RuntimeError, TypeError):
                    pass
                if loader.isRunning():
                    loader.quit()
                    loader.wait(1000)
                loader.deleteLater()
        self._export_config_loader = None
        self._credits_loader = None

        self._clear_selection_rectangle()

        if self._dock_widget:
            self._iface.removeDockWidget(self._dock_widget)
            self._dock_widget.deleteLater()
            self._dock_widget = None

        from .error_report_dialog import stop_log_collector

        stop_log_collector()

        if self._settings_action and self._terralab_menu:
            self._terralab_menu.removeAction(self._settings_action)
            self._settings_action = None

        if self._action:
            from .terralab_menu import remove_from_plugins_menu, remove_plugin_from_menu

            remove_from_plugins_menu(self._iface, self._action)
            remove_plugin_from_menu(
                self._terralab_menu, self._action, self._iface.mainWindow()
            )

            ai_seg_action = getattr(self, "_ai_seg_action", None)
            if ai_seg_action is not None:
                try:
                    remove_from_plugins_menu(self._iface, ai_seg_action)
                except (RuntimeError, AttributeError):
                    pass
                try:
                    remove_plugin_from_menu(
                        self._terralab_menu, ai_seg_action, self._iface.mainWindow())
                except (RuntimeError, AttributeError):
                    pass

            from .terralab_toolbar import remove_action_from_toolbar

            if self._terralab_toolbar:
                try:
                    remove_action_from_toolbar(
                        self._terralab_toolbar, self._action, self._iface.mainWindow()
                    )
                except (RuntimeError, AttributeError):
                    pass
                if ai_seg_action is not None:
                    try:
                        remove_action_from_toolbar(
                            self._terralab_toolbar, ai_seg_action, self._iface.mainWindow()
                        )
                    except (RuntimeError, AttributeError):
                        pass
                self._terralab_toolbar = None

            self._action = None
            self._ai_seg_action = None
            self._terralab_menu = None

        self._map_tool = None
        clear_config_cache()
        log("AI Edit plugin unloaded")

    def _toggle_dock(self):
        if self._dock_widget.isVisible():
            self._dock_widget.hide()
            log_debug("Dock hidden")
        else:
            self._dock_widget.show()
            self._dock_widget.raise_()
            log_debug("Dock shown")

    def _on_settings_clicked(self):
        """Open the Account Settings dialog."""
        if not self._auth_manager.has_activation_key():
            return
        from .account_settings_dialog import AccountSettingsDialog

        dlg = AccountSettingsDialog(
            client=self._client,
            auth=self._auth_manager.get_auth_header(),
            activation_key=self._auth_manager.get_activation_key(),
            parent=self._iface.mainWindow(),
        )
        dlg.change_key_requested.connect(self._on_change_key)
        dlg.exec()

    def _load_export_config(self):
        """Fetch export config from server in background thread."""
        self._export_config_loader = _ExportConfigLoaderWorker(self._client)
        self._export_config_loader.loaded.connect(self._on_export_config_loaded)
        self._export_config_loader.failed.connect(self._on_export_config_failed)
        self._export_config_loader.start()

    def _on_export_config_loaded(self, config):
        """Set global export config from server response."""
        set_server_config(config)
        costs = config.get("resolution_credit_costs", {})
        if self._dock_widget:
            self._dock_widget.set_resolution_credit_costs(costs)

    def _on_export_config_failed(self, error_message: str):
        """Handle export config loading failure."""
        log_warning(f"Export config failed to load: {error_message}")
        if self._dock_widget:
            self._dock_widget.set_status(
                f"Warning: Cannot connect to server ({error_message}). "
                "Plugin requires internet connection to function.",
                is_error=True
            )

    def _activate_selection_tool(self):
        current_tool = self._canvas.mapTool()
        if current_tool and current_tool != self._map_tool:
            self._previous_map_tool = current_tool
        self._canvas.setMapTool(self._map_tool)
        self._canvas.setFocus()
        self._clear_selection_rectangle()
        self._dock_widget.set_status("")
        self._selected_extent = None

    def _restore_previous_map_tool(self):
        """Restore the map tool that was active before selection started."""
        if self._previous_map_tool:
            try:
                self._canvas.setMapTool(self._previous_map_tool)
            except RuntimeError:
                pass
        self._previous_map_tool = None

    def _on_stop(self):
        """Stop button: reset everything."""
        if self._worker and self._worker.isRunning():
            duration = time.time() - getattr(self, "_generation_start_time", time.time())
            telemetry.track("generation_cancelled", {
                "duration_seconds": round(duration, 1),
                "resolution": getattr(self, "_last_suggested_res", ""),
            })
            telemetry.flush()
        self._canvas.unsetMapTool(self._map_tool)
        self._restore_previous_map_tool()
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._last_image_b64 = None

    def _on_retry(self, prompt: str):
        """Retry on same zone: re-generate from the ORIGINAL canvas export."""
        if not self._last_image_b64 or not self._last_extent_dict:
            self._dock_widget.set_status(
                tr("Cannot retry: original zone data not available."), is_error=True
            )
            return
        self._run_generation_from_stored(prompt)

    def _on_new_zone(self, prompt: str):
        """Keep prompt, start new zone selection."""
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._last_image_b64 = None  # Will be re-captured from new zone
        self._dock_widget.set_active_mode()
        self._activate_selection_tool()

    def _on_template_selected(self, template_id: str):
        """Track template selection for analytics."""
        telemetry.track("template_selected", {"template_id": template_id})

    def _run_generation_from_stored(self, prompt: str):
        """Run generation using previously stored zone data (for retry)."""
        if self._worker and self._worker.isRunning():
            self._dock_widget.set_status("Generation already in progress", is_error=True)
            return

        if not has_consent():
            save_consent()
            self._dock_widget.hide_consent()

        if not has_server_config():
            self._dock_widget.set_status(
                "Cannot generate: export config not loaded from server. "
                "Check your internet connection and restart QGIS.",
                is_error=True,
            )
            return

        # Update resolution from selector (user may have changed it on retry)
        if not self._dock_widget._is_free_tier:
            self._last_suggested_res = self._dock_widget.get_selected_resolution()

        ctx = PipelineContext()
        ctx.aspect_ratio = self._last_aspect_ratio

        output_dir = get_output_dir()

        # Show the zone rectangle during retry
        if self._selected_extent:
            self._show_selection_rectangle(self._selected_extent)

        self._dock_widget.set_generating(True)
        self._dock_widget.set_status("")
        self._generation_service.reset()
        self._generation_start_time = time.time()
        telemetry.track("generation_started", {
            "prompt_length": len(prompt),
            "aspect_ratio": self._last_aspect_ratio or "",
            "resolution": self._last_suggested_res or "",
            "is_retry": True,
        })
        log_debug(f"Retry generation: prompt_len={len(prompt)}")

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

        self._worker = GenerationWorker(
            client=self._client,
            auth_manager=self._auth_manager,
            service=self._generation_service,
            image_b64=self._last_image_b64,
            prompt=prompt,
            aspect_ratio=self._last_aspect_ratio or "",
            extent_dict=self._last_extent_dict,
            crs_wkt=self._last_crs_wkt or "",
            output_dir=output_dir,
            ctx=ctx,
            debug_mode=self._dev_mode,
            plugin_dir=plugin_dir,
            skip_trial_check=self._skip_trial_check,
            suggested_resolution=self._last_suggested_res or "1K",
        )
        self._worker.finished.connect(self._on_generation_finished)
        self._worker.progress.connect(self._on_generation_progress)
        self._worker.error.connect(self._on_generation_error)
        self._worker.start()

    def _on_zone_selected(self, extent: QgsRectangle):
        self._selected_extent = extent
        # Show rectangle and deactivate tool BEFORE setting focus on prompt
        self._show_selection_rectangle(extent)
        self._canvas.unsetMapTool(self._map_tool)
        self._restore_previous_map_tool()
        # Focus set LAST — after map tool ops that steal focus
        self._dock_widget.set_zone_selected()
        log_debug("Zone selected")

    def _on_zone_too_small(self):
        self._canvas.unsetMapTool(self._map_tool)
        self._restore_previous_map_tool()
        self._dock_widget.set_idle()
        self._dock_widget.set_status(tr("Selected zone too small (min 50x50px)"), is_error=True)

    def _on_change_key(self):
        """Reset activation state so user can enter a new key."""
        clear_activation()
        self._auth_manager.set_activation_key("")
        self._dock_widget.show_change_key_mode()
        self._settings_action.setEnabled(False)
        log_debug("Activation key cleared by user")

    def _on_activation_attempted(self, key: str):
        success, message, code = validate_key_with_server(self._client, key)
        if success:
            save_activation(key)
            self._auth_manager.set_activation_key(key)
            self._dock_widget.set_activated(True)
            self._dock_widget.set_activation_message(tr("Activation key verified!"), is_error=False)
            self._dock_widget.hide_activation_limit_cta()
            self._settings_action.setEnabled(True)
            self._refresh_credits()
            telemetry.track("plugin_activated")
            telemetry.flush()
            log("Activation successful")
        else:
            self._dock_widget.set_activation_message(message, is_error=True)
            normalized_code = (code or "").strip().upper()
            message_lower = (message or "").lower()
            if normalized_code == "TRIAL_EXHAUSTED":
                config = get_server_config(self._client)
                dashboard = config.get("upgrade_url", SUBSCRIBE_ERROR_URL)
                self._dock_widget.show_activation_limit_cta(dashboard)
            is_quota_error = (
                normalized_code in {
                    "QUOTA_EXCEEDED",
                    "LIMIT_REACHED",
                    "USAGE_LIMIT_REACHED",
                    "MONTHLY_LIMIT_REACHED",
                }
                or "monthly limit reached" in message_lower  # noqa: W503
            )
            if is_quota_error:
                self._dock_widget.show_activation_limit_cta(SUBSCRIBE_ERROR_URL)
            log_warning(f"Activation failed: {message}")

    def _on_generate(self, prompt: str):
        if self._worker and self._worker.isRunning():
            self._dock_widget.set_status("Generation already in progress", is_error=True)
            return
        if not self._selected_extent:
            self._dock_widget.set_status(tr("No zone selected"), is_error=True)
            return

        # Save consent on first generation and hide checkbox
        if not has_consent():
            save_consent()
            self._dock_widget.hide_consent()

        # Ensure server config is loaded before generation
        if not has_server_config():
            self._dock_widget.set_status(
                "Cannot generate: export config not loaded from server. "
                "Check your internet connection and restart QGIS.",
                is_error=True
            )
            return

        ctx = PipelineContext()

        if self._dock_widget._is_free_tier:
            suggested_res = "1K"
        else:
            suggested_res = self._dock_widget.get_selected_resolution()

        # Show loading state on Generate button before blocking export
        self._dock_widget.set_generate_loading(True)
        QApplication.processEvents()

        try:
            map_settings = self._canvas.mapSettings()
            target_res = suggested_res if not self._dock_widget._is_free_tier else None
            image_b64, img_w, img_h, actual_extent = export_canvas_zone(
                map_settings, self._selected_extent, ctx=ctx,
                target_resolution=target_res,
            )
        except Exception as e:
            self._dock_widget.set_generate_loading(False)
            self._dock_widget.set_status(
                tr("Export error: {error}").format(error=e), is_error=True
            )
            return

        # Use "auto" so the model preserves the input image dimensions.
        # Explicit ratios (e.g. "21:9") cause the model to reshape the output,
        # which creates alignment issues with the source imagery.
        aspect_ratio = "auto"
        ctx.aspect_ratio = aspect_ratio

        # Use the actual rendered extent (QGIS may adjust it to match the
        # output pixel aspect ratio). This prevents image stretching.
        extent_dict = {
            "xmin": actual_extent.xMinimum(),
            "ymin": actual_extent.yMinimum(),
            "xmax": actual_extent.xMaximum(),
            "ymax": actual_extent.yMaximum(),
        }
        crs_wkt = map_settings.destinationCrs().toWkt()
        output_dir = get_output_dir()

        # Update rubber band to match the actual rendered extent
        self._selected_extent = actual_extent
        self._show_selection_rectangle(actual_extent)

        # Preserve original zone for retry (never chain from AI result)
        self._last_image_b64 = image_b64
        self._last_extent_dict = extent_dict
        self._last_crs_wkt = crs_wkt
        self._last_aspect_ratio = aspect_ratio
        self._last_suggested_res = suggested_res

        self._dock_widget.set_generating(True)
        self._dock_widget.set_status("")
        self._generation_service.reset()
        self._generation_start_time = time.time()
        telemetry.track("generation_started", {
            "prompt_length": len(prompt),
            "aspect_ratio": aspect_ratio,
            "resolution": suggested_res,
            "zone_width_px": img_w,
            "zone_height_px": img_h,
        })
        log(f"Generation started: prompt_len={len(prompt)}, resolution={suggested_res}, zone={img_w}x{img_h}px")

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

        self._worker = GenerationWorker(
            client=self._client,
            auth_manager=self._auth_manager,
            service=self._generation_service,
            image_b64=image_b64,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            extent_dict=extent_dict,
            crs_wkt=crs_wkt,
            output_dir=output_dir,
            ctx=ctx,
            debug_mode=self._dev_mode,
            plugin_dir=plugin_dir,
            skip_trial_check=self._skip_trial_check,
            suggested_resolution=suggested_res,
        )
        self._worker.finished.connect(self._on_generation_finished)
        self._worker.progress.connect(self._on_generation_progress)
        self._worker.error.connect(self._on_generation_error)
        self._worker.start()

    def _on_generation_progress(self, status: str, percentage: int):
        self._dock_widget.set_progress_message(status, percentage)

    def _on_generation_error(self, message: str, code: str):
        self._selected_extent = None
        self._dock_widget.set_idle()
        self._clear_selection_rectangle()
        self._restore_previous_map_tool()
        self._cleanup_worker()
        normalized_code = (code or "").strip().upper()
        message_lower = (message or "").lower()
        is_quota_error = (
            normalized_code in {
                "QUOTA_EXCEEDED",
                "LIMIT_REACHED",
                "USAGE_LIMIT_REACHED",
                "MONTHLY_LIMIT_REACHED",
            }
            or "monthly limit reached" in message_lower  # noqa: W503
        )
        duration = time.time() - getattr(self, "_generation_start_time", time.time())
        telemetry.track("generation_failed", {
            "error_code": code,
            "duration_seconds": round(duration, 1),
            "resolution": getattr(self, "_last_suggested_res", ""),
        })
        telemetry.flush()
        if normalized_code == "TRIAL_EXHAUSTED":
            config = get_server_config(self._client)
            dashboard = config.get("upgrade_url", get_dashboard_url())
            promo_text = config.get("promo_text", "") if config.get("promo_active") else ""
            self._dock_widget.show_trial_exhausted_info(message, dashboard, promo_text)
        elif is_quota_error:
            self._dock_widget.show_usage_limit_info(message, SUBSCRIBE_ERROR_URL)
        else:
            enriched = _enrich_error_message(message, code)
            self._dock_widget.set_status(enriched, is_error=True)
        log_warning(f"Generation failed: {message} (code={code})")

    def _on_generation_finished(self, result_info: dict):
        self._cleanup_worker()
        # Keep selection rectangle visible for retry/new zone flow
        self._restore_previous_map_tool()
        duration = time.time() - getattr(self, "_generation_start_time", time.time())

        try:
            self._generation_counter += 1
            layer = add_geotiff_to_project(
                result_info["geotiff_path"],
                result_info.get("prompt", ""),
                generation_number=self._generation_counter,
            )
            telemetry.track("generation_completed", {
                "duration_seconds": round(duration, 1),
                "resolution": getattr(self, "_last_suggested_res", ""),
            })
            telemetry.flush()
            self._dock_widget.set_generation_complete(layer.name())
            self._refresh_credits()
            log(f"Generation complete ({round(duration, 1)}s): {result_info['geotiff_path']}")
        except Exception as e:
            telemetry.track("plugin_error", {
                "error_type": "layer_add_failed",
                "error_message": str(e)[:200],
            })
            telemetry.flush()
            self._dock_widget.set_idle()
            self._clear_selection_rectangle()
            self._dock_widget.set_status(
                tr("Error adding layer: {error}").format(error=e), is_error=True
            )
            log_warning(f"Failed to add layer: {e}")

    def _refresh_credits(self):
        """Fetch and display current credits in background (non-blocking)."""
        self._credits_loader = _CreditsLoaderWorker(self._auth_manager)
        self._credits_loader.loaded.connect(self._on_credits_loaded)
        self._credits_loader.start()

    def _on_credits_loaded(self, usage: dict):
        """Update dock widget with credits from background fetch."""
        if self._dock_widget:
            used = usage.get("images_used")
            limit = usage.get("images_limit")
            self._dock_widget.set_credits(
                used=used,
                limit=limit,
                is_free_tier=usage.get("is_free_tier", False),
            )
            if (
                isinstance(used, int)
                and isinstance(limit, int)  # noqa: W503
                and limit > 0  # noqa: W503
                and used < limit  # noqa: W503
            ):
                self._dock_widget.hide_trial_info()
            if (
                isinstance(used, int)
                and isinstance(limit, int)  # noqa: W503
                and limit > 0  # noqa: W503
                and used >= limit  # noqa: W503
            ):
                is_free = usage.get("is_free_tier", False)
                if is_free:
                    config = get_server_config(self._client)
                    dashboard = config.get("upgrade_url", get_dashboard_url())
                    promo_text = config.get("promo_text", "") if config.get("promo_active") else ""
                    self._dock_widget.show_trial_exhausted_info(
                        f"All {limit} free credits used. Subscribe to continue.",
                        dashboard,
                        promo_text,
                    )
                else:
                    self._dock_widget.show_usage_limit_info(
                        f"Monthly limit reached ({used}/{limit}).",
                        SUBSCRIBE_ERROR_URL,
                    )

    def _cleanup_worker(self):
        """Safely clean up the worker thread without crashing QGIS."""
        if self._worker is None:
            return
        # Disconnect signals first to prevent callbacks on deleted UI
        for sig in [self._worker.finished, self._worker.progress, self._worker.error]:
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        try:
            self._worker.wait(5000)
        except RuntimeError:
            pass
        self._worker.deleteLater()
        self._worker = None

    # --- Selection rectangle management ---

    def _show_selection_rectangle(self, extent):
        self._clear_selection_rectangle()
        from ..core import qt_compat as QtC
        rb = QgsRubberBand(self._canvas, QtC.PolygonGeometry)
        rb.setColor(QColor(65, 105, 225, 15))
        rb.setStrokeColor(QColor(65, 105, 225, 180))
        rb.setWidth(3)
        rb.addPoint(QgsPointXY(extent.xMinimum(), extent.yMinimum()), False)
        rb.addPoint(QgsPointXY(extent.xMaximum(), extent.yMinimum()), False)
        rb.addPoint(QgsPointXY(extent.xMaximum(), extent.yMaximum()), False)
        rb.addPoint(QgsPointXY(extent.xMinimum(), extent.yMaximum()), True)
        self._selection_rubber_band = rb

    def _clear_selection_rectangle(self):
        if self._selection_rubber_band:
            try:
                scene = self._selection_rubber_band.scene()
                if scene is not None:
                    scene.removeItem(self._selection_rubber_band)
            except (RuntimeError, AttributeError):
                pass
            self._selection_rubber_band = None
