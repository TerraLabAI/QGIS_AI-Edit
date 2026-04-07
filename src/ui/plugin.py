import os

from qgis.PyQt.QtCore import QSettings, Qt, QUrl, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtGui import QIcon, QColor, QDesktopServices
from qgis.core import QgsRectangle, QgsWkbTypes, QgsPointXY
from qgis.gui import QgsRubberBand

from ..api.terralab_client import TerraLabClient
from ..core.auth_manager import AuthManager
from ..core.generation_service import GenerationService, calculate_closest_aspect_ratio
from ..core.activation_manager import (
    get_activation_key,
    save_activation,
    clear_activation,
    validate_key_with_server,
    has_consent,
    save_consent,
    tr,
)
from ..core.logger import log, log_warning
from ..core.pipeline_context import PipelineContext
from ..workers.generation_worker import GenerationWorker
from .dock_widget import AIEditDockWidget
from .selection_map_tool import RectangleSelectionTool
from .canvas_exporter import (
    export_canvas_zone,
    calculate_suggested_resolution,
    set_server_config,
    has_server_config,
)
from .raster_writer import add_geotiff_to_project, get_output_dir


from ..core.prompt_presets import fetch_remote_presets

SETTINGS_KEY_PREFIX = "AIEdit"

DASHBOARD_URL = "https://terra-lab.ai/dashboard"
SUBSCRIBE_URL = "https://terra-lab.ai/ai-edit"


class _PresetLoaderWorker(QThread):
    """Background worker to fetch remote presets without blocking QGIS."""

    loaded = pyqtSignal(list)

    def __init__(self, client):
        super().__init__()
        self._client = client

    def run(self):
        result = fetch_remote_presets(self._client)
        if result:
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
            log("Export config: fetching from server...")
            config = self._client.get_export_config()
            if "error" not in config:
                log("Export config: loaded from server")
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
    if code in ("INVALID_KEY", "SUBSCRIPTION_INACTIVE"):
        return f'{error} — <a href="{DASHBOARD_URL}">Check your dashboard</a>'
    if code == "TRIAL_EXHAUSTED":
        return (
            f"{error}. AI Edit runs on cloud AI infrastructure with real costs. "
            f"Your subscription helps keep the plugin open source. "
            f'<a href="{SUBSCRIBE_URL}">Subscribe at terra-lab.ai</a>'
        )
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
        return f'{error} — <a href="{DASHBOARD_URL}">Check your dashboard</a>'
    return error


class AIEditPlugin:
    """Main QGIS plugin class. Orchestrates all tiers."""

    def __init__(self, iface):
        self._iface = iface
        self._canvas = iface.mapCanvas()
        self._dock_widget = None
        self._map_tool = None
        self._action = None
        self._selected_extent = None
        self._worker = None
        self._selection_rubber_band = None
        self._previous_map_tool = None
        self._terralab_toolbar = None
        self._preset_loader = None
        self._export_config_loader = None

        # Initialize tiers
        self._dev_mode = False
        self._skip_trial_check = False
        self._client = self._create_client()
        self._auth_manager = AuthManager(self._client)
        self._generation_service = GenerationService(self._client)

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
        with open(path, "r") as f:
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
            get_or_create_terralab_menu,
            add_plugin_to_menu,
            add_to_plugins_menu,
        )

        main_window = self._iface.mainWindow()
        self._terralab_menu = get_or_create_terralab_menu(main_window)

        self._action = QAction(
            QIcon(icon_path) if os.path.exists(icon_path) else QIcon(),
            tr("ai_edit"),
            main_window,
        )
        self._action.setToolTip(tr("ai_edit_tooltip"))
        self._action.triggered.connect(self._toggle_dock)
        add_plugin_to_menu(self._terralab_menu, self._action, "ai-edit")

        from .terralab_toolbar import (
            get_or_create_terralab_toolbar,
            add_action_to_toolbar,
        )

        self._terralab_toolbar = get_or_create_terralab_toolbar(self._iface)
        add_action_to_toolbar(self._terralab_toolbar, self._action, "ai-edit")

        add_to_plugins_menu(self._iface, self._action)

        # Create dock widget and register it with QGIS (hidden by default)
        self._dock_widget = AIEditDockWidget(self._iface.mainWindow())
        self._iface.addDockWidget(Qt.RightDockWidgetArea, self._dock_widget)
        self._dock_widget.hide()
        self._dock_widget.select_zone_clicked.connect(self._activate_selection_tool)
        self._dock_widget.stop_clicked.connect(self._on_stop)
        self._dock_widget.generate_clicked.connect(self._on_generate)
        self._dock_widget.activation_attempted.connect(self._on_activation_attempted)
        self._dock_widget.change_key_clicked.connect(self._on_change_key)

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
        else:
            # No key stored: force activation screen (clears stale dev state)
            clear_activation(settings)
            self._dock_widget.set_activated(False)

        from .error_report_dialog import start_log_collector

        start_log_collector()

        # Load remote presets in background (non-blocking)
        self._load_remote_presets()

        # Load export config in background (non-blocking)
        self._load_export_config()

        if self._dev_mode:
            log("AI Edit plugin loaded [DEV MODE]")
        else:
            log("AI Edit plugin loaded")

    def unload(self):
        """Called by QGIS when plugin is unloaded."""
        if self._worker and self._worker.isRunning():
            self._generation_service.cancel()
            self._worker.wait(5000)

        self._clear_selection_rectangle()

        if self._dock_widget:
            self._iface.removeDockWidget(self._dock_widget)
            self._dock_widget.deleteLater()
            self._dock_widget = None

        from .error_report_dialog import stop_log_collector

        stop_log_collector()

        if self._action:
            from .terralab_menu import remove_plugin_from_menu, remove_from_plugins_menu

            remove_from_plugins_menu(self._iface, self._action)
            remove_plugin_from_menu(
                self._terralab_menu, self._action, self._iface.mainWindow()
            )

            from .terralab_toolbar import remove_action_from_toolbar

            if self._terralab_toolbar:
                try:
                    remove_action_from_toolbar(
                        self._terralab_toolbar, self._action, self._iface.mainWindow()
                    )
                except (RuntimeError, AttributeError):
                    pass
                self._terralab_toolbar = None

            self._action = None
            self._terralab_menu = None

        self._map_tool = None
        log("AI Edit plugin unloaded")

    def _toggle_dock(self):
        if self._dock_widget.isVisible():
            self._dock_widget.hide()
        else:
            self._dock_widget.show()

    def _load_remote_presets(self):
        """Fetch presets from server in background thread."""
        self._preset_loader = _PresetLoaderWorker(self._client)
        self._preset_loader.loaded.connect(self._on_presets_loaded)
        self._preset_loader.start()

    def _on_presets_loaded(self, categories):
        """Update dock widget menu with remote presets."""
        if self._dock_widget:
            self._dock_widget.update_presets(categories)

    def _load_export_config(self):
        """Fetch export config from server in background thread."""
        self._export_config_loader = _ExportConfigLoaderWorker(self._client)
        self._export_config_loader.loaded.connect(self._on_export_config_loaded)
        self._export_config_loader.failed.connect(self._on_export_config_failed)
        self._export_config_loader.start()

    def _on_export_config_loaded(self, config):
        """Set global export config from server response."""
        set_server_config(config)

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
        self._canvas.unsetMapTool(self._map_tool)
        self._restore_previous_map_tool()
        self._clear_selection_rectangle()
        self._selected_extent = None

    def _on_zone_selected(self, extent: QgsRectangle):
        self._selected_extent = extent
        self._dock_widget.set_zone_selected()
        # Show persistent selection rectangle before deactivating the tool
        self._show_selection_rectangle(extent)
        self._canvas.unsetMapTool(self._map_tool)
        self._restore_previous_map_tool()
        log("Zone selected")

    def _on_zone_too_small(self):
        self._dock_widget.set_status(
            tr("zone_too_small"), is_error=True
        )

    def _on_change_key(self):
        """Reset activation state so user can enter a new key."""
        clear_activation()
        self._auth_manager.set_activation_key("")
        self._dock_widget.set_activated(False)
        self._dock_widget.set_activation_key("")
        self._dock_widget.set_activation_message("")
        log("Activation key cleared by user")

    def _show_consent_dialog(self) -> bool:
        """Show the terms/privacy consent dialog. Returns True if accepted."""
        msg = QMessageBox(self._iface.mainWindow())
        msg.setWindowTitle(tr("consent_title"))
        msg.setText(tr("consent_text"))
        msg.setInformativeText(
            f'<a href="https://terra-lab.ai/terms-of-sale">{tr("consent_terms_link")}</a> | '
            f'<a href="https://terra-lab.ai/privacy-policy">{tr("consent_privacy_link")}</a>'
        )
        accept_btn = msg.addButton(tr("consent_accept"), QMessageBox.AcceptRole)
        msg.addButton(tr("consent_decline"), QMessageBox.RejectRole)
        msg.setDefaultButton(accept_btn)
        msg.exec_()
        return msg.clickedButton() == accept_btn

    def _on_activation_attempted(self, key: str):
        # Show consent dialog on first activation attempt
        if not has_consent():
            if not self._show_consent_dialog():
                self._dock_widget.set_activation_message(
                    tr("consent_decline"), is_error=True
                )
                return
            save_consent()

        success, message = validate_key_with_server(self._client, key)
        if success:
            save_activation(key)
            self._auth_manager.set_activation_key(key)
            self._dock_widget.set_activated(True)
            self._dock_widget.set_activation_message(tr("key_verified"), is_error=False)
            log("Activation successful")
        else:
            self._dock_widget.set_activation_message(message, is_error=True)
            log_warning(f"Activation failed: {message}")

    def _on_generate(self, prompt: str):
        if self._worker and self._worker.isRunning():
            return
        if not self._selected_extent:
            self._dock_widget.set_status(tr("no_zone"), is_error=True)
            return

        # Ensure server config is loaded before generation
        if not has_server_config():
            self._dock_widget.set_status(
                "Cannot generate: export config not loaded from server. "
                "Check your internet connection and restart QGIS.",
                is_error=True
            )
            return

        ctx = PipelineContext()

        try:
            map_settings = self._canvas.mapSettings()
            image_b64, img_w, img_h, actual_extent = export_canvas_zone(
                map_settings, self._selected_extent, ctx=ctx
            )
        except Exception as e:
            self._dock_widget.set_status(
                tr("export_error").format(error=e), is_error=True
            )
            return

        suggested_res = calculate_suggested_resolution(
            map_settings, self._selected_extent
        )

        aspect_ratio = calculate_closest_aspect_ratio(img_w, img_h)
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

        self._dock_widget.set_generating(True)
        self._dock_widget.set_status("")
        self._generation_service.reset()
        log(f"Generation started: prompt='{prompt[:50]}', ratio={aspect_ratio}")

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

    def _on_generation_progress(self, status: str, current: int, total: int):
        self._dock_widget.set_progress_message(status)

    def _on_generation_error(self, message: str, code: str):
        self._dock_widget.set_idle()
        self._clear_selection_rectangle()
        self._restore_previous_map_tool()
        self._cleanup_worker()
        if code == "TRIAL_EXHAUSTED":
            self._dock_widget.show_trial_exhausted_info(message, SUBSCRIBE_URL)
        else:
            enriched = _enrich_error_message(message, code)
            self._dock_widget.set_status(enriched, is_error=True)
        log_warning(f"Generation failed: {message} (code={code})")

    def _on_generation_finished(self, result_info: dict):
        self._cleanup_worker()
        self._clear_selection_rectangle()
        self._restore_previous_map_tool()

        try:
            layer = add_geotiff_to_project(
                result_info["geotiff_path"], result_info.get("prompt", "")
            )
            self._dock_widget.set_generation_complete(
                tr("layer_added").format(name=layer.name())
            )
            log(f"Generation complete: {result_info['geotiff_path']}")
        except Exception as e:
            self._dock_widget.set_idle()
            self._dock_widget.set_status(
                tr("error_adding_layer").format(error=e), is_error=True
            )
            log_warning(f"Failed to add layer: {e}")

    def _cleanup_worker(self):
        """Safely clean up the worker thread without crashing QGIS."""
        if self._worker is None:
            return
        try:
            self._worker.wait(5000)
        except RuntimeError:
            pass
        self._worker = None

    # --- Selection rectangle management ---

    def _show_selection_rectangle(self, extent):
        self._clear_selection_rectangle()
        rb = QgsRubberBand(self._canvas, QgsWkbTypes.PolygonGeometry)
        rb.setColor(QColor(65, 105, 225, 80))
        rb.setStrokeColor(QColor(65, 105, 225, 200))
        rb.setWidth(2)
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
