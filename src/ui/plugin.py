from __future__ import annotations

import os
import time

from qgis.core import QgsApplication, QgsPointXY, QgsRectangle
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import QEvent, QObject, QSettings, QTimer
from qgis.PyQt.QtGui import QColor, QIcon, QKeySequence
from qgis.PyQt.QtWidgets import QAction, QShortcut

from ..api.terralab_client import TerraLabClient
from ..core import qt_compat as QtC
from ..core import telemetry
from ..core.auth.activation_manager import (
    clear_activation,
    clear_config_cache,
    get_activation_key,
    get_dashboard_url,
    get_server_config,
    has_consent,
    migrate_legacy_key,
    save_activation,
    save_consent,
    validate_key_with_server,
)
from ..core.auth.auth_manager import AuthManager
from ..core.config_store import ConfigStore, set_store
from ..core.generation.generation_service import GenerationService
from ..core.generation.pipeline_context import PipelineContext
from ..core.i18n import tr
from ..core.logger import log, log_debug, log_warning
from ..core.prompts import prompt_history
from ..core.prompts.prompt_presets import (
    detect_freeform_vector_intent,
    get_vector_hints,
    lookup_template_by_prompt,
)
from ..core.reference_image_store import ReferenceImageStore
from ..workers.export_worker import ExportWorker
from ..workers.generation_worker import GenerationWorker
from ..workers.generic_request_task import GenericRequestTask
from .canvas_exporter import (
    apply_export_context,
    has_server_config,
    prepare_export,
    set_server_config,
)
from .dock_widget import AIEditDockWidget
from .raster_writer import (
    add_geotiff_to_project,
    get_output_dir,
)
from .tools.markup_tools import (
    ArrowMapTool,
    CircleMapTool,
    MarkupLayerManager,
    PencilMapTool,
)
from .tools.selection_map_tool import RectangleSelectionTool


class _MarkupUndoFilter(QObject):
    """Main-window event filter that intercepts Cmd/Ctrl+Z during Mark up.

    Installed via mainWindow.installEventFilter() so the keypress fires
    before QGIS's own Undo action regardless of which widget has focus.
    """

    def __init__(self, on_undo) -> None:
        super().__init__()
        self._on_undo = on_undo

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() != QEvent.Type.KeyPress:
            return False
        if event.matches(QKeySequence.StandardKey.Undo):
            self._on_undo()
            return True
        return False


# Qt maps Ctrl -> Cmd on macOS automatically.
LAUNCH_SHORTCUT = "Ctrl+Alt+E"


DASHBOARD_ERROR_URL = (
    "https://terra-lab.ai/dashboard"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dashboard_error"
)
SUBSCRIBE_ERROR_URL = (
    "https://terra-lab.ai/dashboard/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=subscribe"
)


def _key_validation_request(client, key):
    """Run validate_key_with_server and reshape its (ok, msg, code) tuple into
    the dict shape GenericRequestTask expects ({} on success, {"error": msg,
    "code": code} on failure)."""
    success, message, code = validate_key_with_server(client, key)
    if success:
        return {}
    return {"error": message, "code": code}


def _server_catalog_request(client, force_refresh: bool):
    """Fetch server preset catalog. Returns the dict on success or a sentinel
    error dict so GenericRequestTask routes via ``failed``."""
    from ..core.prompts.prompt_presets_client import fetch_server_catalog
    catalog = fetch_server_catalog(client, force_refresh=force_refresh)
    if catalog is None:
        return {"error": "Catalog unavailable", "code": "UNAVAILABLE"}
    return catalog


def _localize_server_error(error: str, code: str) -> str:
    """Replace an English server error string with its localized equivalent
    when the server's `code` is one we know about. Keeps the original English
    text for unknown codes so users still see something actionable.

    The server stays i18n-agnostic: it sends stable codes; the plugin owns the
    user-facing copy. Adding a new server error code = add one branch here and
    one tr() string in the .ts files.
    """
    if not code:
        return error
    mapping = {
        "RATE_LIMITED": tr("Too many requests, please wait a moment."),
        "RATE_LIMITER_DOWN": tr("Service temporarily unavailable, please retry shortly."),
        "STORAGE_UNAVAILABLE": tr("Storage temporarily unavailable, please retry shortly."),
        "SIGN_FAILED": tr("Could not prepare upload, please retry shortly."),
        "UPLOAD_TOKEN_INVALID": tr("Upload session expired, please retry."),
        "UPLOAD_TOKEN_MISMATCH": tr("Upload session does not match your account."),
        "WRONG_PRODUCT": tr("This activation key is for a different product."),
        "WRONG_REQUEST": tr("Unknown or unauthorized request."),
        "AUTH_MIGRATION_REQUIRED": tr("Account migration required, please re-login from the website."),
        "NOT_READY": tr("Result not ready yet."),
        "NOT_AVAILABLE": tr("Result not available."),
        "UPSTREAM_UNAVAILABLE": tr("Result temporarily unavailable, please retry shortly."),
        "UPSTREAM_EMPTY": tr("Result temporarily unavailable, please retry shortly."),
        "FAL_BAD_RESPONSE": tr("The generation service returned an unexpected response, please retry."),
        "FAL_ERROR": tr("Generation failed, please try again."),
        "MISCONFIGURED": tr("Service not configured. Please contact support."),
        "SERVER_ERROR": tr("Service temporarily unavailable, please retry shortly."),
        "DB_ERROR": tr("Database error, please retry shortly."),
        "BAD_REQUEST": tr("Invalid request. Check your prompt and the selected area, then try again."),
        "BAD_INPUT": tr("Invalid input. Check your prompt and the selected area."),
        "INVALID_INPUT": tr("Invalid input. Try a different image or selection."),
        "PAYLOAD_TOO_LARGE": tr("Image too large. Try selecting a smaller area or lowering the resolution."),
        "RESOLUTION_NOT_ALLOWED": tr(
            "This resolution is not available on your plan."
            " Upgrade to unlock higher resolutions."
        ),
        "NOT_FOUND": tr("Resource not found."),
        "NOT_SEEDED": tr("Catalog not yet available, please retry shortly."),
        "DEMO_FETCH_FAILED": tr("Could not load the demo preview."),
        "UNKNOWN_TEMPLATE": tr("Unknown template."),
    }
    return mapping.get(code, error)


def _enrich_error_message(error: str, code: str = "") -> str:
    """Translate the server-supplied error (via code), then append actionable
    guidance (deep links, network hints) based on the same code."""
    localized = _localize_server_error(error, code)
    if code in ("INVALID_KEY", "SUBSCRIPTION_INACTIVE", "FREE_TIER_EXPIRED"):
        return f'{localized}. <a href="{DASHBOARD_ERROR_URL}">{tr("Check your dashboard")}</a>'
    if code == "TRIAL_EXHAUSTED":
        config = get_server_config()
        dashboard = config.get("upgrade_url", get_dashboard_url())
        return f'{localized}. <a href="{dashboard}">{tr("Subscribe")}</a>'
    if code == "PROXY_ERROR":
        return f"{localized}. {tr('Check QGIS proxy settings: Settings > Options > Network')}"
    if code == "SSL_ERROR":
        return f"{localized}. {tr('If you are on a corporate network, ask your IT team about SSL inspection settings')}"
    if code in ("DNS_ERROR", "NO_INTERNET"):
        return f"{localized}. {tr('Check your internet connection')}"
    if code == "TIMEOUT":
        return f"{localized}. {tr('Try again, or check your internet speed')}"
    if code == "CONNECTION_REFUSED":
        return f"{localized}. {tr('The service may be temporarily unavailable')}"
    if code == "AUTH_ERROR":
        return f'{localized}. <a href="{DASHBOARD_ERROR_URL}">{tr("Check your dashboard")}</a>'
    return localized


_GENERIC_MODEL_FAILURE_HINTS = (
    "couldn't complete",
    "could not complete",
    "rephrasing your prompt",
    "no credit was charged",
)


def _is_generic_model_failure(message: str, normalized_code: str) -> bool:
    """True when the failure looks like the server's catch-all 'model failed'
    response (no specific quota / auth / network code). Used to swap the
    raw message for a softer 'the target may not be in this area' hint
    when the run took unusually long before failing."""
    if normalized_code in {
        "QUOTA_EXCEEDED",
        "LIMIT_REACHED",
        "USAGE_LIMIT_REACHED",
        "MONTHLY_LIMIT_REACHED",
        "TRIAL_EXHAUSTED",
        "INVALID_KEY",
        "SUBSCRIPTION_INACTIVE",
        "FREE_TIER_EXPIRED",
        "AUTH_ERROR",
        "TIMEOUT",
        "DNS_ERROR",
        "NO_INTERNET",
        "PROXY_ERROR",
        "SSL_ERROR",
        "CONNECTION_REFUSED",
    }:
        return False
    text = (message or "").lower()
    return any(needle in text for needle in _GENERIC_MODEL_FAILURE_HINTS)


def _resolve_class_label(
    vector_color: str | None, vector_classes: list[dict] | None
) -> str:
    """Look up the semantic class name for ``vector_color`` in the multi-class
    catalog. Empty string when the template only declares a color, leaving
    the field blank in the table for the user to fill in (autocomplete kicks
    in after the first value is typed).
    """
    if not vector_color or not isinstance(vector_classes, list):
        return ""
    target = vector_color.upper().lstrip("#")
    for entry in vector_classes:
        if not isinstance(entry, dict):
            continue
        color = (entry.get("color") or "").upper().lstrip("#")
        if color and color == target:
            label = entry.get("label")
            if isinstance(label, str):
                return label.strip()
    return ""


class AIEditPlugin:
    """Main QGIS plugin class. Orchestrates all tiers."""

    def __init__(self, iface):
        self._iface = iface
        self._canvas = iface.mapCanvas()
        self._dock_widget = None
        self._map_tool = None
        self._swipe_controller = None
        self._action = None
        self._settings_action = None
        self._selected_extent = None
        self._worker = None
        # Off-thread canvas exporter. Built fresh per click in _on_generate so
        # the heavy render+PNG-encode doesn't freeze the UI.
        self._export_worker: ExportWorker | None = None
        # Stash data captured on the main thread that the export-completed
        # callback needs to chain into the GenerationWorker. Cleared after use.
        self._pending_generation: dict | None = None
        self._selection_rubber_band = None
        self._selection_rubber_band_halo = None
        self._previous_map_tool = None
        self._terralab_toolbar = None
        self._export_config_loader = None
        self._credits_loader = None
        self._catalog_loader = None
        # Preserved for retry (re-generate from original, not from AI result)
        self._last_image_b64 = None
        self._last_extent_dict = None
        self._last_crs_wkt = None
        self._last_aspect_ratio = None
        self._last_suggested_res = None
        # Iteration anchor: sent as parent_request_id on the next submit.
        self._last_completed_request_id: str | None = None
        self._key_validation_worker = None
        # Skip /usage round-trips while rapidly toggling the dock (10/60s rate limit).
        self._last_key_validation_unix: float = 0.0
        # Mark up state. Lazy: manager is created on first entry.
        self._markup_manager: MarkupLayerManager | None = None
        self._markup_tool_objs: dict[str, object] = {}
        self._pre_markup_map_tool = None
        # Markup undo runs via a main-window event filter (more reliable
        # than QShortcut, which loses key races against QGIS's own Ctrl+Z).
        self._markup_event_filter: _MarkupUndoFilter | None = None
        # While Markup is open we disable any main-window QAction bound to
        # Ctrl/Cmd+Z so QGIS's project undo doesn't swallow the keystroke
        # before the map tool's keyPressEvent sees it. Restored on exit.
        self._suppressed_undo_actions: list[tuple[QAction, bool]] = []
        # Global "Launch AI Edit" shortcut. Cross-platform via QKeySequence
        # (Cmd+Alt+E on macOS, Ctrl+Alt+E on Windows/Linux).
        self._launch_shortcut: QShortcut | None = None
        self._in_tool_panel: str | None = None
        # Fires once per QGIS session, on the first dock-open. Lifecycle event,
        # ships without explicit consent (no PII).
        self._plugin_opened_emitted = False
        # Cached cohort props enriched onto every generation event.
        self._first_generation_milestone_emitted = False

        # Initialize tiers
        self._dev_mode = False
        self._skip_trial_check = False
        # ConfigStore owns server config, cached activation config, telemetry
        # collector. Wired so unload + Plugin Reloader leave no globals behind.
        self._config_store = ConfigStore()
        set_store(self._config_store)
        self._client = self._create_client()
        self._auth_manager = AuthManager(self._client)
        self._generation_service = GenerationService(self._client)
        self._reference_store = ReferenceImageStore()

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
        env_vars: dict = {}
        try:
            if os.path.isfile(env_path):
                env_vars = self._load_env_file(env_path)
        except OSError as err:
            # Network shares or USB drives can vanish mid-read; defaults are
            # safe (dev_mode and skip_trial_check stay False).
            log_warning(f".env.local read failed, using defaults: {err}")
        self._dev_mode = env_vars.get("DEBUG", "").lower() == "true"
        self._skip_trial_check = env_vars.get("SKIP_TRIAL_CHECK", "").lower() == "true"
        return TerraLabClient(env_vars=env_vars)

    @staticmethod
    def _load_env_file(path: str) -> dict:
        """Parse a simple KEY=VALUE env file (ignores comments and blank lines)."""
        env: dict = {}
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        env[key.strip()] = value.strip().strip('"').strip("'")
        except OSError as err:
            log_warning(f".env.local read failed: {err}")
        return env

    @staticmethod
    def _read_plugin_version() -> str:
        """Read plugin version from metadata.txt."""
        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        metadata_path = os.path.join(plugin_dir, "metadata.txt")
        try:
            with open(metadata_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("version="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            pass
        return "unknown"

    def initGui(self):
        # Idempotent; defers silently if auth DB is locked.
        try:
            migrate_legacy_key()
        except Exception as err:  # nosec B110
            log_warning(f"Auth migration raised: {err}")

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
        self._dock_widget = AIEditDockWidget(
            self._iface.mainWindow(), reference_store=self._reference_store
        )
        self._dock_widget.set_library_dependencies(self._client, self._auth_manager)
        # Defer stale catalog read off initGui so startup never blocks on disk.

        def _load_stale_catalog():
            try:
                from ..core.prompts.prompt_presets_client import read_cached_catalog_stale_ok

                if self._dock_widget is not None:
                    self._dock_widget.set_server_catalog(read_cached_catalog_stale_ok())
            except Exception as err:  # noqa: BLE001
                log_warning(f"Server catalog stale read failed: {err}")
                if self._dock_widget is not None:
                    self._dock_widget.set_server_catalog(None)
        QTimer.singleShot(0, _load_stale_catalog)
        self._load_server_catalog()
        self._iface.addDockWidget(QtC.RightDockWidgetArea, self._dock_widget)
        _first_install_settings = QSettings()
        if not _first_install_settings.value("AIEdit/dock_shown_once", False, type=bool):
            _first_install_settings.setValue("AIEdit/dock_shown_once", True)
            self._dock_widget.show()
            self._dock_widget.raise_()
        else:
            self._dock_widget.hide()
        self._dock_widget.stop_clicked.connect(self._on_stop)
        self._dock_widget.generate_clicked.connect(self._on_generate)
        self._dock_widget.retry_clicked.connect(self._on_retry)
        self._dock_widget.template_selected.connect(self._on_template_selected)
        self._dock_widget.catalog_refresh_requested.connect(self._load_server_catalog)
        self._dock_widget.activation_attempted.connect(self._on_activation_attempted)
        self._dock_widget.change_key_clicked.connect(self._on_change_key)
        self._dock_widget.settings_clicked.connect(self._on_settings_clicked)
        self._dock_widget.launch_clicked.connect(self._on_launch_clicked)
        self._dock_widget.exit_clicked.connect(self._on_exit_clicked)
        self._dock_widget.zone_clear_requested.connect(self._on_zone_delete_requested)
        self._dock_widget.markup_clicked.connect(self._on_markup_clicked)
        self._dock_widget.markup_done_clicked.connect(self._on_markup_done_clicked)
        self._dock_widget.markup_clear_clicked.connect(self._on_markup_clear_clicked)
        self._dock_widget.markup_tool_changed.connect(self._on_markup_tool_changed)
        self._dock_widget.markup_color_changed.connect(self._on_markup_color_changed)
        self._dock_widget.vectorize_clicked.connect(self._on_vectorize_clicked)
        self._dock_widget.vectorize_done_clicked.connect(self._on_vectorize_done_clicked)
        self._dock_widget.vectorize_suggestion_clicked.connect(
            self._on_vectorize_suggestion_clicked
        )
        # Before/After swipe: toggle-based, no dock panel. The footer
        # button toggle drives the SwipeController; the controller signals
        # back so the button visual + enable state stays in sync.
        from .panels.swipe_panel import SwipeController
        # parent must be a QObject; AIEditPlugin is a plain Python class.
        # The controller has no natural Qt parent, so we own its lifecycle
        # explicitly via cleanup() in unload().
        self._swipe_controller = SwipeController(None)
        self._dock_widget.swipe_toggled.connect(self._on_swipe_toggled)
        self._swipe_controller.activated.connect(self._on_swipe_armed)
        self._swipe_controller.deactivated.connect(self._on_swipe_disarmed)
        self._swipe_controller.eligibility_changed.connect(
            self._dock_widget.set_swipe_button_enabled
        )
        # Opening the Help menu disarms any active swipe, same rule as
        # the other AI Edit actions: only one tool owns the canvas at a
        # time so the user is never left guessing which mode they are in.
        self._dock_widget.help_menu_open_changed.connect(self._on_help_menu_open_changed)
        # Seed the initial enable state from the layer that's active right
        # now (currentLayerChanged only fires on subsequent changes).
        self._dock_widget.set_swipe_button_enabled(
            self._swipe_controller.can_swipe_now()
        )
        # Title-bar X close doesn't go through _toggle_dock, so listen for the
        # underlying visibility change to keep the map tool / cursor in sync.
        self._dock_widget.visibilityChanged.connect(self._on_dock_visibility_changed)

        # Global launch shortcut: Ctrl+Alt+E on Win/Linux, Cmd+Alt+E (⌥⌘E)
        # on macOS. WindowShortcut scope fires from anywhere inside QGIS
        # without us having to focus a particular widget first.
        self._launch_shortcut = QShortcut(
            QKeySequence(LAUNCH_SHORTCUT),
            self._iface.mainWindow(),
        )
        self._launch_shortcut.setContext(QtC.WindowShortcut)
        self._launch_shortcut.activated.connect(self._on_launch_shortcut)

        # Create map tool
        self._map_tool = RectangleSelectionTool(self._canvas)
        self._map_tool.selection_made.connect(self._on_zone_selected)
        self._map_tool.zone_too_small.connect(self._on_zone_too_small)
        self._map_tool.zone_invalid.connect(self._on_zone_invalid)
        self._map_tool.zone_delete_requested.connect(self._on_zone_delete_requested)

        # Restore saved activation key
        self._check_activation_state()
        if not self._auth_manager.has_activation_key():
            telemetry.track("activation_screen_viewed")

        from .dialogs.error_report_dialog import start_log_collector

        start_log_collector()

        # Initialize telemetry (respects consent + auth, non-blocking)
        telemetry.init_telemetry(
            self._client, self._auth_manager, self._read_plugin_version()
        )

        # Load export config in background (non-blocking)
        self._load_export_config()

        if self._dev_mode:
            log("AI Edit plugin loaded [DEV MODE]")
        else:
            log("AI Edit plugin loaded")
        if self._skip_trial_check:
            log_warning("DEV MODE: SKIP_TRIAL_CHECK is active - auth checks bypassed")

    def unload(self):
        """Called by QGIS when plugin is unloaded."""
        # Stop generation task. QgsTaskManager owns the task lifecycle, so we
        # request cancellation and drop our reference; the framework drains
        # the run() loop and emits taskTerminated on the main thread.
        if self._worker is not None and self._worker.is_active():
            self._generation_service.cancel()
            for sig in [self._worker.succeeded, self._worker.progress, self._worker.failed]:
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            try:
                self._worker.cancel()
            except Exception:  # nosec B110
                pass
        self._worker = None

        # Same drain for the canvas-export worker. Drop the pending hand-off
        # so a late completed-signal doesn't try to kick a GenerationWorker
        # against a torn-down dock.
        self._pending_generation = None
        if self._export_worker is not None and self._export_worker.is_active():
            for sig in [self._export_worker.completed, self._export_worker.failed]:
                try:
                    sig.disconnect()
                except (RuntimeError, TypeError):
                    pass
            try:
                self._export_worker.cancel()
            except Exception:  # nosec B110
                pass
        self._export_worker = None

        # Stop background loader QgsTasks. We disconnect signals (slots
        # would land on a dying dock) then cancel; the task manager drains
        # the run() loop and disposes of the task itself.
        for loader in [
            self._export_config_loader,
            self._credits_loader,
            self._key_validation_worker,
            self._catalog_loader,
        ]:
            if loader is None:
                continue
            try:
                loader.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                loader.cancel()
            except Exception:  # nosec B110
                pass
        self._export_config_loader = None
        self._credits_loader = None
        self._key_validation_worker = None
        self._catalog_loader = None

        self._clear_selection_rectangle()

        # Detach any Mark up tool we set on the canvas before our objects vanish.
        if self._markup_tool_objs:
            current = self._canvas.mapTool() if self._canvas else None
            if current in self._markup_tool_objs.values():
                self._canvas.unsetMapTool(current)
            self._markup_tool_objs.clear()
        self._clear_markup_layer()
        if self._markup_manager is not None:
            self._markup_manager.disconnect_signals()
        self._markup_manager = None
        self._pre_markup_map_tool = None
        if self._markup_event_filter is not None:
            try:
                self._iface.mainWindow().removeEventFilter(self._markup_event_filter)
            except RuntimeError:
                pass
            self._markup_event_filter = None
        self._restore_qgis_undo()
        if self._launch_shortcut is not None:
            try:
                self._launch_shortcut.setEnabled(False)
                self._launch_shortcut.deleteLater()
            except RuntimeError:
                pass
            self._launch_shortcut = None

        if self._swipe_controller is not None:
            try:
                self._swipe_controller.cleanup()
            except RuntimeError:
                pass
            self._swipe_controller = None

        if self._dock_widget:
            # Disconnect QgsProject signals before the dock is destroyed.
            # closeEvent used to do this but firing on every hide also broke
            # the dock when the user re-opened it from the Panels menu.
            try:
                self._dock_widget.cleanup()
            except Exception as err:  # nosec B110
                log_warning(f"Dock cleanup failed: {err}")
            self._iface.removeDockWidget(self._dock_widget)
            self._dock_widget.deleteLater()
            self._dock_widget = None

        # Wipe session-scoped reference images from disk.
        try:
            self._reference_store.cleanup()
        except Exception as err:  # nosec B110
            log_warning(f"Reference store cleanup failed: {err}")

        from .dialogs.error_report_dialog import stop_log_collector

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

        if self._map_tool is not None:
            try:
                self._map_tool.cleanup()
            except Exception as err:  # nosec B110
                log_warning(f"Map tool cleanup failed: {err}")
        self._map_tool = None
        clear_config_cache()
        # Cancel any in-flight telemetry flush tasks before tearing down the
        # store so QgsTaskManager doesn't outlive the collector.
        telemetry.shutdown_telemetry()
        if self._config_store is not None:
            self._config_store.clear()
        set_store(None)
        log("AI Edit plugin unloaded")

    @staticmethod
    def _days_since_activation() -> int | None:
        """Cohort prop. None for legacy users without a stored timestamp."""
        raw = QSettings().value("AIEdit/activation_timestamp_unix", "", type=str)
        if not raw:
            return None
        try:
            ts = int(raw)
        except (TypeError, ValueError):
            return None
        delta = int((time.time() - ts) // 86400)
        return max(delta, 0)

    def _enrich_generation_props(self, base: dict) -> dict:
        enriched = {
            **base,
            "context_image_count": self._reference_store.count(),
            "context_total_size_bytes": self._reference_store.total_size_bytes(),
        }
        days = self._days_since_activation()
        if days is not None:
            enriched["days_since_activation"] = days
        return enriched

    def _maybe_emit_first_generation_milestone(self):
        """One-shot event when the user completes their first successful generation.

        Persisted via QSettings so it never re-fires across QGIS sessions.
        """
        if self._first_generation_milestone_emitted:
            return
        settings = QSettings()
        already = settings.value("AIEdit/first_generation_milestone_fired", False, type=bool)
        if already:
            self._first_generation_milestone_emitted = True
            return
        days = self._days_since_activation()
        props = {}
        if days is not None:
            props["days_since_activation"] = days
        telemetry.track("first_generation_milestone", props)
        telemetry.flush()
        settings.setValue("AIEdit/first_generation_milestone_fired", True)
        # Force-flush so a crash doesn't lose the flag and re-fire the milestone.
        try:
            settings.sync()
        except Exception:  # nosec B110
            pass
        self._first_generation_milestone_emitted = True

    def _toggle_dock(self):
        if self._dock_widget.isVisible():
            self._dock_widget.hide()
            self._deactivate_selection_tool()
            self._clear_selection_rectangle()
            self._selected_extent = None
            if self._map_tool:
                self._map_tool.set_has_zone(False)
            log_debug("Dock hidden")
        else:
            # Clean up any orphan rubber band left by a previous session
            # (e.g. dock closed via the title-bar X instead of toggle).
            self._clear_selection_rectangle()
            self._selected_extent = None
            if self._map_tool:
                self._map_tool.set_has_zone(False)
            self._check_activation_state()
            self._dock_widget.show()
            self._dock_widget.raise_()
            # Selection tool stays off until the user clicks "Launch AI Edit".
            if not self._plugin_opened_emitted:
                self._plugin_opened_emitted = True
                telemetry.track("plugin_opened")
                telemetry.flush()
            log_debug("Dock shown")

    def _check_activation_state(self):
        """Check activation key presence and validate with server."""
        settings = QSettings()
        saved_key = get_activation_key(settings)
        if not saved_key:
            clear_activation(settings)
            self._auth_manager.set_activation_key("")
            self._dock_widget.set_activated(False)
            self._settings_action.setEnabled(False)
            self._last_key_validation_unix = 0.0
            return

        self._auth_manager.set_activation_key(saved_key)
        self._dock_widget.set_activation_key(saved_key)

        if (time.time() - self._last_key_validation_unix) < 30:
            self._dock_widget.set_activated(True)
            self._settings_action.setEnabled(True)
            # Stay on LAUNCH state; tool is activated on user click.
            return

        # Optimistic activation: a saved key implies the user is already
        # signed up. Show the activated UI immediately and disable
        # Launch until the async credit check confirms; otherwise the
        # sign-up screen flashes for half a second on every reload.
        self._dock_widget.set_activated(True)
        self._settings_action.setEnabled(True)
        self._dock_widget.set_launch_enabled(False)

        self._key_validation_worker = GenericRequestTask(
            "AI Edit key validation",
            lambda c=self._client, k=saved_key: _key_validation_request(c, k),
        )
        self._key_validation_worker.succeeded.connect(lambda _payload: self._on_key_valid())
        self._key_validation_worker.failed.connect(self._on_key_invalid)
        QgsApplication.taskManager().addTask(self._key_validation_worker)

    def _on_key_valid(self):
        """Server confirmed the key is valid."""
        self._last_key_validation_unix = time.time()
        self._dock_widget.set_activated(True)
        self._settings_action.setEnabled(True)
        # Stay on LAUNCH state; tool is activated on user click.
        self._dock_widget.set_checking_credits(True)
        self._refresh_credits()

    def _on_key_invalid(self, message: str, code: str):
        """Server rejected the key - show activation screen."""
        self._last_key_validation_unix = 0.0
        clear_activation()
        self._auth_manager.set_activation_key("")
        self._dock_widget.set_activated(False)
        self._dock_widget.set_activation_message(message, is_error=True)
        self._settings_action.setEnabled(False)

    def _on_settings_clicked(self):
        """Open the Account Settings dialog."""
        if not self._auth_manager.has_activation_key():
            return
        self._disarm_swipe()
        from .dialogs.account_settings_dialog import AccountSettingsDialog

        dlg = AccountSettingsDialog(
            client=self._client,
            auth=self._auth_manager.get_auth_header(),
            activation_key=self._auth_manager.get_activation_key(),
            parent=self._iface.mainWindow(),
        )
        dlg.change_key_requested.connect(self._on_change_key)
        self._dock_widget.set_settings_button_active(True)
        try:
            dlg.exec()
        finally:
            self._dock_widget.set_settings_button_active(False)

    def _load_export_config(self):
        """Fetch export config from server in background thread."""
        self._export_config_loader = GenericRequestTask(
            "AI Edit export config",
            self._client.get_export_config,
        )
        self._export_config_loader.succeeded.connect(self._on_export_config_loaded)
        self._export_config_loader.failed.connect(
            lambda msg, code: self._on_export_config_failed(f"Server error: {msg}")
        )
        QgsApplication.taskManager().addTask(self._export_config_loader)

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
        self._catalog_loader = GenericRequestTask(
            "AI Edit preset catalog",
            lambda c=self._client: _server_catalog_request(c, force_refresh=True),
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
        set_server_config(config)
        costs = config.get("resolution_credit_costs", {})
        if self._dock_widget:
            self._dock_widget.set_resolution_credit_costs(costs)

    def _on_export_config_failed(self, error_message: str):
        """Handle export config loading failure."""
        log_warning(f"Export config failed to load: {error_message}")
        if self._dock_widget:
            self._dock_widget.set_status(
                tr(
                    "Warning: Cannot connect to server ({error}). "
                    "Plugin requires internet connection to function."
                ).format(error=error_message),
                is_error=True
            )

    def _activate_selection_tool(self):
        """Activate selection tool. Preserves any existing zone."""
        if self._canvas.mapTool() != self._map_tool:
            current_tool = self._canvas.mapTool()
            if current_tool:
                self._previous_map_tool = current_tool
            self._canvas.setMapTool(self._map_tool)
        self._dock_widget.set_status("")

    def _deactivate_selection_tool(self):
        """Restore the map tool that was active before selection started."""
        if self._previous_map_tool:
            try:
                self._canvas.setMapTool(self._previous_map_tool)
            except RuntimeError:
                pass
        self._previous_map_tool = None

    def _on_stop(self):
        """Dock closing mid-generation: cancel work and clear zone state.

        Triggered by the dock's closeEvent (title-bar X). The Exit button has
        its own handler that also returns the dock to LAUNCH - see
        _on_exit_clicked.
        """
        if self._worker is not None and self._worker.is_active():
            duration = time.time() - getattr(self, "_generation_start_time", time.time())
            telemetry.track("generation_cancelled", self._enrich_generation_props({
                "duration_seconds": round(duration, 1),
                "resolution": getattr(self, "_last_suggested_res", ""),
            }))
            telemetry.flush()
            self._generation_service.cancel()
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._last_image_b64 = None
        if self._map_tool:
            self._map_tool.set_has_zone(False)
        self._deactivate_selection_tool()

    def _on_launch_shortcut(self):
        """Global shortcut: open the dock if hidden, then start a new edit."""
        if self._dock_widget is None:
            return
        if not self._dock_widget.isVisible():
            self._dock_widget.setVisible(True)
        self._dock_widget.raise_()
        self._on_launch_clicked()

    def _on_launch_clicked(self):
        """User clicked 'Launch AI Edit' on the entry screen."""
        telemetry.track("launch_clicked")
        self._disarm_swipe()
        self._activate_selection_tool()
        self._dock_widget.set_selecting_zone_state()

    def _on_dock_visibility_changed(self, visible: bool):
        if visible:
            return
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

    def _on_exit_clicked(self):
        """User clicked Exit / Done: cancel work and return to LAUNCH."""
        self._disarm_swipe()
        if self._worker is not None and self._worker.is_active():
            duration = time.time() - getattr(self, "_generation_start_time", time.time())
            telemetry.track("generation_cancelled", self._enrich_generation_props({
                "duration_seconds": round(duration, 1),
                "resolution": getattr(self, "_last_suggested_res", ""),
            }))
            telemetry.flush()
            self._generation_service.cancel()
        self._clear_selection_rectangle()
        self._selected_extent = None
        self._last_image_b64 = None
        if self._map_tool:
            self._map_tool.set_has_zone(False)
        self._deactivate_selection_tool()
        # Mark up annotations persist across sessions on a single shared layer.
        # User wipes them explicitly via the Clear all button.
        self._dock_widget.set_launch_state()

    def _on_retry(self, prompt: str):
        """Retry on same zone: re-export the current canvas view (includes generated layers)."""
        if not self._selected_extent:
            self._dock_widget.set_status(
                tr("Cannot retry: no zone selected."), is_error=True
            )
            return
        self._on_generate(prompt)

    def _on_template_selected(self, template_id: str, template_name: str = ""):
        """Track template selection for analytics."""
        props = {"template_id": template_id}
        if template_name:
            props["template_name"] = template_name
        telemetry.track("template_selected", props)

    def _run_generation_from_stored(self, prompt: str):
        """Run generation using previously stored zone data (for retry)."""
        if self._worker is not None and self._worker.is_active():
            self._dock_widget.set_status(tr("Generation already in progress"), is_error=True)
            return

        if not has_consent():
            save_consent()
            self._dock_widget.hide_consent()

        if not has_server_config():
            self._dock_widget.set_status(
                tr(
                    "Cannot generate: export config not loaded from server. "
                    "Check your internet connection and restart QGIS."
                ),
                is_error=True,
            )
            return

        # Update resolution from selector (user may have changed it on retry)
        if not self._dock_widget._is_free_tier:
            self._last_suggested_res = self._dock_widget.get_selected_resolution()

        ctx = PipelineContext()
        ctx.aspect_ratio = self._last_aspect_ratio
        # Retry on the same zone = iteration. Anchor on the previous result's
        # original input so the model keeps style coherence.
        ctx.parent_request_id = self._last_completed_request_id

        # Armed template wins over text match so user edits keep vector hints.
        armed = self._dock_widget.get_active_template()
        match = armed or lookup_template_by_prompt(prompt)
        if match:
            ctx.template_id, ctx.template_name = match
            ctx.vector_color, ctx.vector_classes = get_vector_hints(ctx.template_id)
        else:
            # No preset matched. Still light up the Vectorize CTA when the
            # free-form prompt asks to segment, detect, or vectorize one
            # feature type without naming colors (server paints #FF0000).
            ctx.vector_color = detect_freeform_vector_intent(prompt)

        output_dir = get_output_dir()

        # Show the zone rectangle during retry
        if self._selected_extent:
            self._show_selection_rectangle(self._selected_extent)

        if self._map_tool:
            self._map_tool.set_locked(True)
        self._dock_widget.set_generating(True)
        self._dock_widget.set_status("")
        self._generation_service.reset()
        self._generation_start_time = time.time()
        telemetry.track("generation_started", self._enrich_generation_props({
            "prompt_length": len(prompt),
            "aspect_ratio": self._last_aspect_ratio or "",
            "resolution": self._last_suggested_res or "",
            "is_retry": True,
            "template_id": ctx.template_id,
            "template_name": ctx.template_name,
            "used_template": bool(ctx.template_id),
        }))
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
            context_images=self._reference_store.get_all_b64(),
        )
        self._worker.succeeded.connect(self._on_generation_finished)
        self._worker.progress.connect(self._on_generation_progress)
        self._worker.failed.connect(self._on_generation_error)
        QgsApplication.taskManager().addTask(self._worker)

    def _on_zone_selected(self, extent: QgsRectangle):
        self._selected_extent = extent
        # Fresh zone breaks the iteration chain (parent_request_id + armed template).
        self._last_completed_request_id = None
        if self._dock_widget is not None:
            try:
                self._dock_widget.clear_active_template()
            except AttributeError:
                pass
        self._show_selection_rectangle(extent)
        self._dock_widget.set_zone_selected()
        log_debug("Zone selected")

    def _on_zone_too_small(self):
        try:
            canvas = self._canvas
            canvas_w = canvas.width() if canvas else 0
            min_pct = int(round(50 * 100 / canvas_w)) if canvas_w > 0 else 5
        except Exception:
            min_pct = 5
        self._dock_widget.set_status(
            tr(
                "Selected zone too small. Draw a rectangle at least "
                "{pct}% of the canvas size."
            ).format(pct=max(1, min_pct)),
            is_error=True,
        )

    def _on_zone_invalid(self, code: str, message: str):
        """Edge-zone refusal at draw time (antimeridian, polar, oversized,
        rotated map, invalid CRS). Surfaces a clear localized banner so the
        user can adjust before clicking Generate."""
        self._dock_widget.set_status(message, is_error=True)
        log_warning(f"Zone refused: {code} - {message}")

    def _on_zone_delete_requested(self):
        """Clear the current zone and return to the SELECTING_ZONE step.

        Triggered by right-click 'Clear zone', the badge button, or Escape
        from the prompt step. We keep the typed prompt and edit group so the
        user can redraw and continue iterating.
        """
        self._clear_selection_rectangle()
        self._selected_extent = None
        # Clearing the zone breaks the iteration chain.
        self._last_completed_request_id = None
        if self._map_tool is not None:
            self._map_tool.set_has_zone(False)
        self._dock_widget.set_zone_cleared()
        log_debug("Zone cleared")

    # --- Mark up / Vectorize tool panels -------------------------------

    def _on_markup_clicked(self):
        """User picked Tools → Mark up. Swap the dock view and arm the canvas.

        Toggles: a second click on the footer Mark up icon while the panel is
        already open closes it (same as the in-panel Finish button).
        """
        self._disarm_swipe()

        if self._in_tool_panel == "markup":
            self._exit_tool_panel()
            return
        if self._markup_manager is None:
            self._markup_manager = MarkupLayerManager(self._canvas, self._dock_widget)
            self._markup_manager.annotation_count_changed.connect(
                self._dock_widget.set_markup_annotation_count
            )
        # Capture the current map tool so Done can restore it.
        current = self._canvas.mapTool()
        if current is not None and current not in self._markup_tool_objs.values():
            self._pre_markup_map_tool = current
        self._in_tool_panel = "markup"
        self._dock_widget.set_markup_state()
        self._dock_widget.set_markup_zone_present(self._selected_extent is not None)
        self._dock_widget.set_markup_annotation_count(
            self._markup_manager.annotation_count()
        )
        # Cmd/Ctrl+Z undo across all focus paths: suppress QGIS undo action,
        # map tool key handler (canvas focus), main-window event filter (dock focus).
        if self._markup_event_filter is None:
            self._markup_event_filter = _MarkupUndoFilter(self._on_markup_undo)
        self._iface.mainWindow().installEventFilter(self._markup_event_filter)
        self._suppress_qgis_undo()
        telemetry.track("markup_opened")

    def _on_markup_tool_changed(self, tool_key: str):
        """User picked Pencil / Arrow / Circle in the Mark up panel."""
        if self._markup_manager is None:
            return
        existing = self._markup_tool_objs.get(tool_key)
        if existing is None:
            if tool_key == "pencil":
                existing = PencilMapTool(self._canvas, self._markup_manager)
            elif tool_key == "arrow":
                existing = ArrowMapTool(self._canvas, self._markup_manager)
            elif tool_key == "circle":
                existing = CircleMapTool(self._canvas, self._markup_manager)
            else:
                return
            self._markup_tool_objs[tool_key] = existing
        existing.set_color(self._dock_widget.get_markup_color())
        # Tell the selection tool to keep its zone state across this switch
        # so the rectangle outline + delete badge stay visible while the
        # user annotates. Without this hint deactivate() wipes them.
        if self._map_tool is not None and self._canvas.mapTool() is self._map_tool:
            self._map_tool.preserve_state_on_next_deactivate()
        self._canvas.setMapTool(existing)

    def _on_markup_color_changed(self, color: QColor):
        """User changed the annotation color - propagate to the active tool."""
        for tool in self._markup_tool_objs.values():
            tool.set_color(color)

    def _on_markup_clear_clicked(self):
        if self._markup_manager is not None:
            self._markup_manager.clear_all()

    def _on_markup_undo(self):
        if self._in_tool_panel == "markup" and self._markup_manager is not None:
            self._markup_manager.undo_last()

    def _on_markup_done_clicked(self):
        """Leave the Mark up panel - restore the previous map tool, keep annotations."""
        self._exit_tool_panel()

    def _on_vectorize_clicked(self):
        """User picked Tools → Vectorize.

        Toggles: a second click on the footer Vectorize icon while the panel
        is open closes it (same as the in-panel Done button).
        """
        self._disarm_swipe()
        if self._in_tool_panel == "vectorize":
            self._exit_tool_panel()
            return
        current = self._canvas.mapTool()
        if current is not None and current not in self._markup_tool_objs.values():
            self._pre_markup_map_tool = current
        self._in_tool_panel = "vectorize"
        self._dock_widget.set_vectorize_state()
        telemetry.track("vectorize_panel_opened")

    def _on_vectorize_suggestion_clicked(
        self, layer_id: str, color_hex: str, class_label: str
    ):
        """User clicked the post-generation \"Vectorize this result\" CTA.
        Open the panel with the source raster + color + class label pre-filled.

        Bypasses the toggle in `_on_vectorize_clicked` so a second click on
        the CTA never closes an already-open panel.
        """
        if self._in_tool_panel != "vectorize":
            current = self._canvas.mapTool()
            if current is not None and current not in self._markup_tool_objs.values():
                self._pre_markup_map_tool = current
            self._in_tool_panel = "vectorize"
            self._dock_widget.set_vectorize_state()
            telemetry.track("vectorize_panel_opened")
        # activate() runs first via set_vectorize_state; preconfigure overrides
        # the just-reset state with the template's values.
        self._dock_widget._vectorize_panel.preconfigure(
            layer_id=layer_id, color_hex=color_hex, class_label=class_label
        )
        telemetry.track(
            "vectorize_suggestion_clicked",
            {"color": color_hex, "has_class_label": bool(class_label)},
        )

    def _on_vectorize_done_clicked(self):
        self._exit_tool_panel()

    def _disarm_swipe(self) -> None:
        """Stop the swipe map tool if it is currently armed.

        Called from every other AI Edit action (vectorize, markup,
        settings, help, exit, launch) so the canvas only ever runs one
        AI Edit tool at a time. The user explicitly asked for this: as
        soon as they pick another action, the swipe must release the
        canvas so they are not left in a stale compare mode.
        """
        if self._swipe_controller is not None and self._swipe_controller.is_active():
            self._swipe_controller.stop()

    def _on_help_menu_open_changed(self, opened: bool) -> None:
        if opened:
            self._disarm_swipe()

    def _on_swipe_toggled(self, checked: bool) -> None:
        """Footer Before/After toggled by the user.

        ``checked=True`` arms the swipe map tool on the currently active
        AI-Edit raster; ``checked=False`` disarms it and restores the
        previous map tool. No dock panel is shown either way.
        """
        if checked:
            self._swipe_controller.start()
        else:
            self._swipe_controller.stop()

    def _on_swipe_armed(self) -> None:
        self._dock_widget.set_swipe_button_checked(True)
        telemetry.track("swipe_armed")

    def _on_swipe_disarmed(self) -> None:
        self._dock_widget.set_swipe_button_checked(False)
        # After disarm, the active layer might have become non-eligible
        # while the swipe was on; re-evaluate the enable state so the
        # button greys out cleanly.
        self._dock_widget.set_swipe_button_enabled(
            self._swipe_controller.can_swipe_now()
        )
        telemetry.track("swipe_disarmed")

    def _exit_tool_panel(self):
        """Common path for Done from either tool panel."""
        # Restore the canvas tool that was active before opening the panel.
        if self._pre_markup_map_tool is not None:
            try:
                self._canvas.setMapTool(self._pre_markup_map_tool)
            except RuntimeError:
                pass
        self._pre_markup_map_tool = None
        if self._markup_event_filter is not None:
            try:
                self._iface.mainWindow().removeEventFilter(self._markup_event_filter)
            except RuntimeError:
                pass
        self._restore_qgis_undo()
        self._in_tool_panel = None
        self._dock_widget.exit_tool_panel()

    def _suppress_qgis_undo(self) -> None:
        """Disable every main-window QAction bound to Cmd/Ctrl+Z while in
        Markup so QGIS's project-undo shortcut never intercepts the
        keystroke before our handlers can fire. Restored via the matching
        ``_restore_qgis_undo()`` call on panel exit / unload.
        """
        if self._suppressed_undo_actions:
            return
        target_seq = QKeySequence(QKeySequence.StandardKey.Undo)
        mainwin = self._iface.mainWindow()
        for action in mainwin.findChildren(QAction):
            try:
                shortcuts = action.shortcuts() or [action.shortcut()]
            except RuntimeError:
                continue
            if any(sc == target_seq for sc in shortcuts if not sc.isEmpty()):
                self._suppressed_undo_actions.append((action, action.isEnabled()))
                action.setEnabled(False)

    def _restore_qgis_undo(self) -> None:
        for action, was_enabled in self._suppressed_undo_actions:
            try:
                action.setEnabled(was_enabled)
            except RuntimeError:
                pass
        self._suppressed_undo_actions = []

    def _clear_markup_layer(self):
        """Drop the in-memory annotation layer (no-op if absent)."""
        if self._markup_manager is not None:
            self._markup_manager.remove_layer()

    def _on_change_key(self):
        """Show change-key mode without clearing the current key yet.

        The old key stays in QSettings so Cancel can restore it.
        Actual clearing happens when the new key is successfully validated.
        """
        self._dock_widget.show_change_key_mode()
        self._settings_action.setEnabled(False)
        log_debug("Change key mode entered")

    def _on_activation_attempted(self, key: str):
        success, message, code = validate_key_with_server(self._client, key)
        normalized_code = (code or "").strip().upper()
        if success:
            save_activation(key)
            self._auth_manager.set_activation_key(key)
            self._dock_widget.set_activated(True)
            self._dock_widget.set_activation_message(tr("Activation key verified!"), is_error=False)
            self._dock_widget.hide_activation_limit_cta()
            self._settings_action.setEnabled(True)
            # Stay on LAUNCH state; tool is activated on user click.
            self._dock_widget.set_checking_credits(True)
            self._refresh_credits()
            # Persist activation timestamp once for cohort analysis.
            settings = QSettings()
            if not settings.value("AIEdit/activation_timestamp_unix", "", type=str):
                settings.setValue("AIEdit/activation_timestamp_unix", str(int(time.time())))
            telemetry.track("activation_attempted", {"success": True})
            telemetry.track("plugin_activated")
            telemetry.flush()
            log("Activation successful")
        else:
            self._dock_widget.set_activation_message(message, is_error=True)
            telemetry.track("activation_attempted", {
                "success": False,
                "error_code": normalized_code or "UNKNOWN",
            })
            telemetry.flush()
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
        if self._worker is not None and self._worker.is_active():
            self._dock_widget.set_status(tr("Generation already in progress"), is_error=True)
            return
        if self._export_worker is not None and self._export_worker.is_active():
            # Click landed while a previous render is still in flight - swallow.
            return
        if not self._selected_extent:
            self._dock_widget.set_status(tr("No zone selected"), is_error=True)
            return
        # Launching a new generation is a clear "I am done comparing"
        # signal: drop the swipe overlay so the canvas renders fresh.
        self._disarm_swipe()

        # Save consent on first generation and hide checkbox
        if not has_consent():
            save_consent()
            self._dock_widget.hide_consent()

        # Ensure server config is loaded before generation
        if not has_server_config():
            self._dock_widget.set_status(
                tr(
                    "Cannot generate: export config not loaded from server. "
                    "Check your internet connection and restart QGIS."
                ),
                is_error=True
            )
            return

        ctx = PipelineContext()
        # If the user generates twice in a row without reselecting a zone,
        # treat the second submission as an iteration so the server attaches
        # the original input as a silent reference.
        ctx.parent_request_id = self._last_completed_request_id

        # Tag the job with template_id. The armed template (set when the
        # user picked a preset) wins so prompt edits keep the association;
        # fall back to exact text match for prompts loaded any other way.
        armed = self._dock_widget.get_active_template()
        match = armed or lookup_template_by_prompt(prompt)
        if match:
            ctx.template_id, ctx.template_name = match
            ctx.vector_color, ctx.vector_classes = get_vector_hints(ctx.template_id)
        else:
            # No preset matched. Still light up the Vectorize CTA when the
            # free-form prompt asks to segment, detect, or vectorize one
            # feature type without naming colors (server paints #FF0000).
            ctx.vector_color = detect_freeform_vector_intent(prompt)

        if self._dock_widget._is_free_tier:
            suggested_res = "1K"
        else:
            suggested_res = self._dock_widget.get_selected_resolution()

        # Lock UI; prep ticker animates while export+upload run off-thread.
        self._dock_widget.set_generating(True)
        self._dock_widget.set_status("")

        try:
            map_settings = self._canvas.mapSettings()
            # Always export the input at the chosen resolution (1K/2K/4K). The
            # model only ever works at those sizes, so sending the full native
            # zone is pointless: a big Google Satellite selection would balloon
            # into tens of MB, stall the upload, and gain nothing.
            prep = prepare_export(
                map_settings, self._selected_extent, target_resolution=suggested_res,
            )
        except Exception as e:
            self._dock_widget.set_generating(False)
            self._dock_widget.set_status(
                tr("Export error: {error}").format(error=e), is_error=True
            )
            return

        # Hand off everything the export-completed callback needs.
        self._pending_generation = {
            "prompt": prompt,
            "ctx": ctx,
            "prep": prep,
            "suggested_res": suggested_res,
            "crs_wkt": map_settings.destinationCrs().toWkt(),
        }

        worker = ExportWorker(prep)
        worker.completed.connect(self._on_export_completed)
        worker.failed.connect(self._on_export_failed)
        self._export_worker = worker
        QgsApplication.taskManager().addTask(worker)

    def _cleanup_export_worker(self, worker):
        if self._export_worker is worker:
            self._export_worker = None

    def _on_export_failed(self, error_msg: str):
        self._pending_generation = None
        self._dock_widget.set_generating(False)
        self._dock_widget.set_status(
            tr("Export error: {error}").format(error=error_msg), is_error=True
        )

    def _on_export_completed(
        self,
        image_b64: str,
        img_w: int,
        img_h: int,
        actual_extent,
        size_bytes: int,
    ):
        pending = self._pending_generation
        self._pending_generation = None
        if pending is None:
            # User cancelled / dock was torn down before the render finished.
            return

        ctx = pending["ctx"]
        prep = pending["prep"]
        prompt = pending["prompt"]
        suggested_res = pending["suggested_res"]
        crs_wkt = pending["crs_wkt"]

        apply_export_context(ctx, prep, actual_extent, size_bytes)

        # Canvas captured: advance the prep ticker to the upload phase so the
        # message pool reflects what's actually happening next (sending bytes).
        self._dock_widget.prep_advance_phase("upload")

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

        if self._map_tool:
            self._map_tool.set_locked(True)
        # set_generating(True) already called at click time. Don't call again
        # here or we'd reset the prep ticker phase + bar back to 1%.
        self._generation_service.reset()
        self._generation_start_time = time.time()
        telemetry.track("generation_started", self._enrich_generation_props({
            "prompt_length": len(prompt),
            "aspect_ratio": aspect_ratio,
            "resolution": suggested_res,
            "zone_width_px": img_w,
            "zone_height_px": img_h,
            "is_retry": False,
            "template_id": ctx.template_id,
            "template_name": ctx.template_name,
            "used_template": bool(ctx.template_id),
        }))
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
            context_images=self._reference_store.get_all_b64(),
        )
        self._worker.succeeded.connect(self._on_generation_finished)
        self._worker.progress.connect(self._on_generation_progress)
        self._worker.failed.connect(self._on_generation_error)
        QgsApplication.taskManager().addTask(self._worker)

    def _on_generation_progress(self, status: str, percentage: int):
        self._dock_widget.set_progress_message(status, percentage)

    def _on_generation_error(self, message: str, code: str, ctx_snapshot: dict | None = None):
        if self._map_tool:
            self._map_tool.set_locked(False)
        self._dock_widget.set_generating(False)
        # Template metadata arrives in the ctx_snapshot dict copied off the
        # worker thread (C2). Read it before cleanup so generation_failed is
        # segmentable by template in PostHog.
        snap = ctx_snapshot or {}
        template_id = snap.get("template_id")
        template_name = snap.get("template_name")
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
        extra_props: dict = {
            "error_code": code,
            "duration_seconds": round(duration, 1),
            "resolution": getattr(self, "_last_suggested_res", ""),
            "template_id": template_id,
            "template_name": template_name,
            "used_template": bool(template_id),
        }
        # WRITE_ERROR is ~90% Windows-path; surface enough to triage the sub-class.
        if normalized_code == "WRITE_ERROR":
            try:
                import sys as _sys
                output_dir = get_output_dir() or ""
                extra_props.update({
                    "os": _sys.platform,
                    "output_dir_len": len(output_dir),
                    "output_dir_has_unicode": not output_dir.isascii(),
                    "output_dir_has_spaces": " " in output_dir,
                    "exception_msg": (message or "")[:500],
                })
            except Exception:  # nosec B110
                pass
        telemetry.track("generation_failed", self._enrich_generation_props(extra_props))
        telemetry.flush()
        if normalized_code == "TRIAL_EXHAUSTED":
            config = get_server_config(self._client)
            dashboard = config.get("upgrade_url", get_dashboard_url())
            self._dock_widget.show_trial_exhausted_info(message, dashboard)
            telemetry.track("trial_exhausted_viewed", {"error_type": "TRIAL_EXHAUSTED"})
        elif is_quota_error:
            self._dock_widget.show_usage_limit_info(message, SUBSCRIBE_ERROR_URL)
            telemetry.track("trial_exhausted_viewed", {"error_type": code or "QUOTA_EXCEEDED"})
        else:
            enriched = _enrich_error_message(message, code)
            # When a generic model failure happens after a long run, almost
            # every time it's because the target the prompt described is not
            # visible in the selected zone (e.g. asking for mangroves on a
            # German aerial). Soften the wall-of-text and hint at the cause.
            if duration >= 40 and _is_generic_model_failure(message, normalized_code):
                enriched = tr(
                    "The model couldn't finish this generation. "
                    "It often means what your prompt describes is not visible "
                    "in the selected area. Try a different zone or rephrase."
                )
            self._dock_widget.set_status(enriched, is_error=True)
        log_warning(f"Generation failed: {message} (code={code})")

    def _on_generation_finished(self, result_info: dict):
        if self._map_tool:
            self._map_tool.set_locked(False)
        # result_info already holds the ctx snapshot copied off the worker.
        self._last_completed_request_id = result_info.get("request_id")
        vector_color: str | None = result_info.get("vector_color")
        vector_classes: list[dict] | None = result_info.get("vector_classes")
        template_id: str | None = result_info.get("template_id")
        template_name: str | None = result_info.get("template_name")
        self._cleanup_worker()
        duration = time.time() - getattr(self, "_generation_start_time", time.time())

        try:
            layer = add_geotiff_to_project(
                result_info["geotiff_path"],
                result_info.get("prompt", ""),
            )
            try:
                self._iface.setActiveLayer(layer)
            except Exception as err:  # nosec B110
                log_warning(f"setActiveLayer failed: {err}")
            prompt_history.add_recent(result_info.get("prompt", ""))
            telemetry.track("generation_completed", self._enrich_generation_props({
                "duration_seconds": round(duration, 1),
                "resolution": getattr(self, "_last_suggested_res", ""),
                "template_id": template_id,
                "template_name": template_name,
                "used_template": bool(template_id),
            }))
            telemetry.flush()
            self._maybe_emit_first_generation_milestone()
            self._dock_widget.set_generation_complete(layer.name(), layer.id())
            class_label = _resolve_class_label(vector_color, vector_classes)
            self._dock_widget.set_vectorize_suggestion(
                layer.id(), vector_color, class_label
            )
            self._refresh_credits()
            log(f"Generation complete ({round(duration, 1)}s): {result_info['geotiff_path']}")
        except Exception as e:
            telemetry.track("plugin_error", {
                "error_type": "layer_add_failed",
                "error_message": str(e)[:200],
            })
            telemetry.flush()
            self._dock_widget.set_generating(False)
            self._dock_widget.set_status(
                tr("Error adding layer: {error}").format(error=e), is_error=True
            )
            log_warning(f"Failed to add layer: {e}")

    def _refresh_credits(self):
        """Fetch and display current credits in background (non-blocking)."""
        self._credits_loader = GenericRequestTask(
            "AI Edit credits",
            self._auth_manager.get_usage_info,
        )
        self._credits_loader.succeeded.connect(self._on_credits_loaded)
        self._credits_loader.failed.connect(lambda _msg, _code: self._on_credits_failed())
        QgsApplication.taskManager().addTask(self._credits_loader)

    def _on_credits_failed(self):
        """Credits fetch failed - clear loading state so the UI is usable."""
        if self._dock_widget:
            self._dock_widget.set_checking_credits(False)
            self._dock_widget.set_launch_enabled(True)

    def _on_credits_loaded(self, usage: dict):
        """Update dock widget with credits from background fetch."""
        if self._dock_widget:
            self._dock_widget.set_checking_credits(False)
            self._dock_widget.set_launch_enabled(True)
            used = usage.get("images_used")
            limit = usage.get("images_limit")
            is_free = usage.get("is_free_tier", False)
            # Prime the subscribe URL BEFORE set_credits so the dock's
            # auto-surface logic can show the upsell banner inline.
            if is_free:
                config = get_server_config(self._client)
                dashboard = config.get("upgrade_url", get_dashboard_url())
                self._dock_widget.set_subscribe_url(dashboard)
            self._dock_widget.set_credits(
                used=used,
                limit=limit,
                is_free_tier=is_free,
            )
            # Paid-tier monthly limit still needs the dedicated CTA (different
            # message + different URL than the free-tier upsell).
            if (
                isinstance(used, int)
                and isinstance(limit, int)  # noqa: W503
                and limit > 0  # noqa: W503
                and used >= limit  # noqa: W503
                and not is_free  # noqa: W503
            ):
                self._dock_widget.show_usage_limit_info(
                    f"Monthly limit reached ({used}/{limit}).",
                    SUBSCRIBE_ERROR_URL,
                )

    def _cleanup_worker(self):
        """Drop our reference to the QgsTask; TaskManager owns its lifetime."""
        if self._worker is None:
            return
        for sig in [self._worker.succeeded, self._worker.progress, self._worker.failed]:
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._worker = None

    # --- Selection rectangle management ---

    def _show_selection_rectangle(self, extent):
        self._clear_selection_rectangle()
        rb = QgsRubberBand(self._canvas, QtC.PolygonGeometry)
        rb.setColor(QColor(0, 0, 0, 0))
        rb.setStrokeColor(QColor(65, 105, 225, 180))
        rb.setWidth(2)
        for x, y, last in (
            (extent.xMinimum(), extent.yMinimum(), False),
            (extent.xMaximum(), extent.yMinimum(), False),
            (extent.xMaximum(), extent.yMaximum(), False),
            (extent.xMinimum(), extent.yMaximum(), True),
        ):
            rb.addPoint(QgsPointXY(x, y), last)
        self._selection_rubber_band = rb

    def _clear_selection_rectangle(self):
        for attr in ("_selection_rubber_band_halo", "_selection_rubber_band"):
            band = getattr(self, attr, None)
            if not band:
                continue
            try:
                scene = band.scene()
                if scene is not None:
                    scene.removeItem(band)
            except (RuntimeError, AttributeError):
                pass
            setattr(self, attr, None)
