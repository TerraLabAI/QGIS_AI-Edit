from __future__ import annotations

import os
from contextlib import contextmanager

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtCore import QSettings, QTimer
from qgis.PyQt.QtGui import QIcon, QKeySequence

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import clear_config_cache, migrate_legacy_key
from ...core.config_store import set_store
from ...core.errors import build_failure_props
from ...core.i18n import tr
from ...core.logger import log, log_warning
from ...core.qt_compat import QAction, QShortcut
from ..dock_widget import AIEditDockWidget
from ..tools.polygon_selection_tool import PolygonSelectionTool

# Qt maps Ctrl -> Cmd on macOS automatically.
LAUNCH_SHORTCUT = "Ctrl+Alt+E"

# Plugin-declared signals per task class. Only these are disconnected at
# teardown; an argument-less task.disconnect() would also sever the
# QgsTaskManager hookups made by addTask() and orphan the task.
GENERATION_TASK_SIGNALS = ("succeeded", "progress", "failed", "taskTerminated")
EXPORT_TASK_SIGNALS = ("completed", "failed")
REQUEST_TASK_SIGNALS = ("succeeded", "failed")  # GenericRequestTask
PAIRING_TASK_SIGNALS = (  # PairingPollTask
    "pairing_succeeded",
    "pairing_failed",
    "pairing_timeout",
    "pairing_browser_seen",
    "pairing_stalled",
)
# Single source for the background-loader teardown: drives both the drain
# and the null-out so the two can never drift.
LOADER_SIGNALS = {
    "_export_config_loader": REQUEST_TASK_SIGNALS,
    "_credits_loader": REQUEST_TASK_SIGNALS,
    "_key_validation_worker": REQUEST_TASK_SIGNALS,
    "_pairing_worker": PAIRING_TASK_SIGNALS,
    "_catalog_loader": REQUEST_TASK_SIGNALS,
    "_bootstrap_task": REQUEST_TASK_SIGNALS,
    "_activation_config_loader": REQUEST_TASK_SIGNALS,
}
# Selection map tool signals connected in initGui.
MAP_TOOL_SIGNALS = (
    "selection_made",
    "zone_too_small",
    "zone_invalid",
    "zone_delete_requested",
    "compare_requested",
    "vectorize_requested",
)


def disconnect_signals(obj, signal_names):
    """Disconnect the named signals if present. Per-signal only.

    A QgsTask can have its C++ half deleted by the task manager before the
    plugin unloads; on such a dead sip wrapper even getattr raises
    RuntimeError, so the guard wraps the whole per-signal access, not just
    the disconnect call."""
    if obj is None:
        return
    for name in signal_names:
        try:
            sig = getattr(obj, name, None)
            if sig is None:
                continue
            sig.disconnect()
        except (RuntimeError, TypeError):
            pass


def drain_task(task, signal_names):
    """Disconnect the named plugin signals, then request a cancel.

    Per-signal only: never call task.disconnect() with no arguments.
    """
    if task is None:
        return
    disconnect_signals(task, signal_names)
    try:
        task.cancel()
    except Exception:  # nosec B110
        pass


def safe_error_text(err: BaseException) -> str:
    """str(err) that cannot itself raise.

    A sip wrapper around a deleted object, and any exception whose message is
    built lazily, can raise from __str__. Inside a teardown handler that raise
    kills the rest of unload(): the exact failure the handler exists to remove.
    """
    try:
        return str(err)
    except Exception:  # noqa: BLE001 - the fallback must always answer
        try:
            return type(err).__name__
        except Exception:  # noqa: BLE001
            return "unprintable error"


def step_error_code(label: str, stage: str) -> str:
    """Stable snake_case error_code for one guarded step, e.g. unload_dock_removal.

    Labels are literals written in this file, so this only normalises their
    spelling; no user data can reach it.
    """
    try:
        slug = "".join(c if c.isalnum() else "_" for c in str(label).lower())
    except Exception:  # noqa: BLE001 - a label that will not render is not worth a raise
        slug = ""
    return f"{stage}_{slug.strip('_') or 'step'}"[:60]


def is_deleted_qt_object_error(err: BaseException) -> bool:
    """True when PyQt raised because the C++ half of a widget is already gone.

    QGIS deletes its own widgets before it calls unload() on shutdown, so a
    teardown step touching the toolbar or the canvas raises RuntimeError
    ("wrapped C/C++ object of type QToolBar has been deleted"). Expected, not
    actionable, and nothing is left dangling: the object the step wanted to
    clean up no longer exists.
    """
    if not isinstance(err, RuntimeError):
        return False
    return "has been deleted" in safe_error_text(err)


def report_teardown_failure(label: str, err: BaseException, stage: str = "unload") -> None:
    """Log a failed step and ship it as plugin_error. Never raises, never blocks.

    A Warning line in the QGIS log is invisible to us, so a teardown that fails
    on every machine of one QGIS build would go unnoticed. The message rides
    through build_failure_props, which scrubs user paths and caps at 200 chars.
    track() only queues; the batch leaves on the telemetry step's own flush, so
    a step failing AFTER telemetry shut down is logged but no longer sent.

    One exception never reaches telemetry: a widget whose C++ half QGIS already
    deleted. It fires on normal shutdown, on every machine, and in 1.7.2 it made
    up 16 of the 18 errors that tripped the release-regression alert.
    """
    detail = safe_error_text(err)
    try:
        log_warning(f"Guarded step failed ({stage}/{label}): {detail}")
    except Exception:  # nosec B110 - logging must never break teardown
        pass
    try:
        if is_deleted_qt_object_error(err):
            return
        telemetry.track(
            te.PLUGIN_ERROR,
            build_failure_props(stage, step_error_code(label, stage), detail),
        )
    except Exception:  # nosec B110 - telemetry must never break teardown
        pass


@contextmanager
def teardown_step(label: str, stage: str = "unload"):
    """Isolate one section of unload(): report what raised, then carry on.

    Unguarded, a single raise (a canvas whose C++ half is already gone during
    QGIS shutdown) skipped every later section, leaving the plugin half torn
    down: event filter installed, dock never removed, telemetry still up. The
    next load then stacked a second set of connections on the first.

    Also used by the post-failure recovery paths, which run inside Qt slots
    where a raise has nowhere to go; those pass their own ``stage``.
    """
    try:
        yield
    except Exception as err:  # noqa: BLE001 - teardown never re-raises
        report_teardown_failure(label, err, stage)


class PluginLifecycleMixin:
    def initGui(self):
        # Idempotent; defers silently if auth DB is locked.
        try:
            migrate_legacy_key()
        except Exception as err:  # nosec B110
            log_warning(f"Auth migration raised: {err}")

        # The stable public API other tools drive the plugin with. Built first
        # so it answers even if a later step of initGui fails. Never fatal: the
        # facade is a bonus surface and the panel is the product, so a broken
        # import here must not stop AI Edit from loading. Everything that reads
        # it (the Processing algorithms through edit_facade(), the agent bridge)
        # treats a missing one as "not available" rather than an error.
        self.mcp_api = None
        try:
            from ...mcp_api import EditMCPAPI
            self.mcp_api = EditMCPAPI(self)
        except Exception as err:  # nosec B110
            log_warning(f"Public API not built: {err}")

        # Publish a "terralab" module an outside agent can import, so a QGIS MCP
        # server that only offers code execution can still find this plugin.
        # Never fatal: the panel loads with or without it.
        if self.mcp_api is not None:
            try:
                from ...agent_bridge import register_product
                register_product("edit", self.mcp_api)
            except Exception as err:  # nosec B110
                log_warning(f"Agent bridge not published: {err}")

        # Publish the algorithms to the Processing registry, which is the one
        # place a generic QGIS MCP server can discover a capability it does not
        # already know about. Never fatal: the panel loads with or without it.
        self._processing_provider = None
        try:
            self._register_processing_provider()
        except Exception as err:  # nosec B110
            log_warning(f"Processing provider not registered: {err}")

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        icon_path = os.path.join(plugin_dir, "resources", "icons", "icon.png")

        from ..terralab_menu import (
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

        from ..terralab_toolbar import (
            add_action_to_toolbar,
            get_or_create_terralab_toolbar,
        )

        self._terralab_toolbar = get_or_create_terralab_toolbar(self._iface)
        add_action_to_toolbar(self._terralab_toolbar, self._action, "ai-edit")

        add_to_plugins_menu(self._iface, self._action)

        # Cross-plugin discovery: show AI Segmentation entry (#47).
        from ..cross_plugin_discovery import make_ai_seg_action
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
        add_plugin_to_menu(
            self._terralab_menu, self._ai_seg_action, "ai-segmentation", is_cross_promo=True
        )
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
                from ...core.prompts.prompt_presets_client import read_cached_catalog_stale_ok

                if self._dock_widget is not None:
                    self._dock_widget.set_server_catalog(read_cached_catalog_stale_ok())
            except Exception as err:  # noqa: BLE001
                log_warning(f"Server catalog stale read failed: {err}")
                if self._dock_widget is not None:
                    self._dock_widget.set_server_catalog(None)
        QTimer.singleShot(0, _load_stale_catalog)
        # Fresh catalog arrives via the startup bootstrap bundle (initGui);
        # library opens trigger _load_server_catalog refetches afterwards.
        self._iface.addDockWidget(QtC.RightDockWidgetArea, self._dock_widget)
        # Auto-open the panel on first install and after every upgrade (new version),
        # but never on a routine launch. Same-version launches let QGIS restore the dock
        # to the state the user left it in (open/closed + position), via its objectName.
        settings = QSettings()
        current_version = self._read_plugin_version()
        last_shown_version = settings.value("AIEdit/dock_shown_version", "", type=str)
        auto_open_source = None
        if last_shown_version != current_version:
            settings.setValue("AIEdit/dock_shown_version", current_version)
            auto_open_source = "auto_install" if not last_shown_version else "auto_upgrade"
            self._dock_widget.show()
            self._dock_widget.raise_()
            self._ensure_dock_height()
        # Check for a newer plugin version once QGIS has fetched repo metadata.
        # That cache is often empty just after startup, so retry on a backoff.
        self._update_check_done = False
        self._update_check_delays = [5000, 30000, 60000, 120000]
        self._update_check_index = 0
        # Parent the timer to the dock so it can't fire into a torn-down plugin
        # after unload (and doesn't retain the plugin in the global event loop).
        QtC.safe_single_shot(
            self._update_check_delays[0], self._dock_widget, self._check_for_plugin_update
        )
        self._dock_widget.stop_clicked.connect(self._on_stop)
        self._dock_widget.generate_clicked.connect(self._on_generate)
        self._dock_widget.retry_clicked.connect(self._on_retry)
        self._dock_widget.base_version_selected.connect(self._on_base_version_selected)
        self._dock_widget.template_selected.connect(self._on_template_selected)
        self._dock_widget.catalog_refresh_requested.connect(self._load_server_catalog)
        self._dock_widget.history_add_to_map.connect(self._on_history_add_to_map)
        self._dock_widget.history_download.connect(self._on_history_download)
        self._dock_widget.history_restore.connect(self._on_history_restore)
        self._dock_widget.pairing_requested.connect(self._on_pairing_requested)
        self._dock_widget.pairing_cancel_requested.connect(self._on_cancel_pairing)
        self._dock_widget.settings_clicked.connect(self._on_settings_clicked)
        self._dock_widget.launch_clicked.connect(self._on_launch_clicked)
        self._dock_widget.try_example_requested.connect(self._on_try_example)
        self._dock_widget.exit_clicked.connect(self._on_exit_clicked)
        self._dock_widget.zone_clear_requested.connect(self._on_zone_delete_requested)
        self._dock_widget.markup_clicked.connect(self._on_markup_clicked)
        self._dock_widget.markup_done_clicked.connect(self._on_markup_done_clicked)
        self._dock_widget.markup_clear_clicked.connect(self._on_markup_clear_clicked)
        self._dock_widget.markup_tool_changed.connect(self._on_markup_tool_changed)
        self._dock_widget.markup_color_changed.connect(self._on_markup_color_changed)
        self._dock_widget.vectorize_clicked.connect(self._on_vectorize_clicked)
        self._dock_widget.vectorize_done_clicked.connect(self._on_vectorize_done_clicked)
        self._dock_widget.reference_panel_requested.connect(self._on_reference_clicked)
        self._dock_widget.reference_done_clicked.connect(self._on_reference_done_clicked)
        self._dock_widget.vectorize_suggestion_clicked.connect(
            self._on_vectorize_suggestion_clicked
        )
        # Conversations: resume rows + panel intents land on the plugin's
        # ConversationsMixin, which owns the network side.
        self._dock_widget.conversation_delete.connect(self._on_conversation_delete)
        self._dock_widget.conversation_rename.connect(self._on_conversation_rename)
        self._dock_widget.conversations_page_requested.connect(
            self._on_conversations_page_requested
        )
        self._dock_widget.conversations_refresh_requested.connect(
            self._refresh_conversations_cache
        )
        # One silent fetch so the Resume rows are fresh at startup without
        # opening the Library first (falls back to the disk cache offline).
        self._refresh_conversations_cache()
        # Before/After swipe: toggle-based, no dock panel. The footer
        # button toggle drives the SwipeController; the controller signals
        # back so the button visual + enable state stays in sync.
        from ..panels.swipe_panel import SwipeController
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

        # Deleting the last visible raster must return the canvas to the same
        # empty baseline as a fresh start. The dock resets its own view, but the
        # selection map tool + zone rubber band are plugin-owned, so listen for
        # layer removal and tear them down when nothing visible remains.
        QgsProject.instance().layersRemoved.connect(self._on_project_layers_changed)

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
        self._map_tool = PolygonSelectionTool(self._canvas)
        self._map_tool.selection_made.connect(self._on_zone_selected)
        self._map_tool.zone_too_small.connect(self._on_zone_too_small)
        self._map_tool.zone_invalid.connect(self._on_zone_invalid)
        self._map_tool.zone_delete_requested.connect(self._on_zone_delete_requested)
        self._map_tool.compare_requested.connect(self._on_canvas_compare)
        self._map_tool.vectorize_requested.connect(self._on_canvas_vectorize)

        # Restore saved activation key. UI only here (optimistic activated or
        # sign-up screen); the network half rides the single bootstrap call
        # below instead of three separate startup requests.
        self._check_activation_state(validate=False)
        if not self._auth_manager.has_activation_key():
            telemetry.track(te.ACTIVATION_SCREEN_VIEWED)

        from ..dialogs.error_report_dialog import start_log_collector

        start_log_collector()

        # unload() does not run on every QGIS exit path, and Qt aborts the
        # process when it destroys a QThread still inside run(). Join the
        # detached Prompt Library threads on the way out too.
        from ..dialogs.prompt_templates.workers import drain_prompt_library_workers

        self._prompt_library_drain = drain_prompt_library_workers
        try:
            QgsApplication.instance().aboutToQuit.connect(drain_prompt_library_workers)
        except (AttributeError, TypeError, RuntimeError) as err:
            self._prompt_library_drain = None
            log_warning(f"Prompt Library drain not hooked to aboutToQuit: {err}")

        # Initialize telemetry (respects consent + auth, non-blocking)
        telemetry.init_telemetry(
            self._client, self._auth_manager, self._read_plugin_version()
        )
        # Dock auto-opened on install/upgrade never went through _toggle_dock,
        # so emit the open here to stop undercounting sessions.
        if auto_open_source is not None and not self._plugin_opened_emitted:
            self._plugin_opened_emitted = True
            telemetry.track(te.PLUGIN_OPENED, {"open_source": auto_open_source})
            telemetry.flush()

        # Startup network is deferred until the dock is actually shown (see
        # _maybe_bootstrap_on_show), so a user who never opens AI Edit makes no
        # network calls. The auto-open branch above shows the dock BEFORE
        # visibilityChanged is connected, so fire it explicitly here when the
        # dock is already visible; the once-guard makes the later signal a no-op.
        # A QGIS-restored-open dock becomes visible after initGui, which the
        # visibilityChanged handler then catches.
        if self._dock_widget.isVisible():
            self._maybe_bootstrap_on_show()

        if self._dev_mode:
            log("AI Edit plugin loaded [DEV MODE]")
        else:
            log("AI Edit plugin loaded")
        if self._skip_trial_check:
            log_warning("DEV MODE: SKIP_TRIAL_CHECK is active - auth checks bypassed")

    def unload(self):
        """Called by QGIS when plugin is unloaded.

        Every section runs inside teardown_step: one raise must never skip the
        sections that follow it.
        """
        # Make any pending plugin-update-check timer a no-op (belt-and-suspenders
        # alongside parenting it to the dock).
        self._update_check_done = True
        # First, so a reload can never leave two providers in the registry.
        with teardown_step("processing provider"):
            self._unregister_processing_provider()
        # Stop advertising this plugin to outside agents once it is disabled.
        with teardown_step("agent bridge"):
            from ...agent_bridge import unregister_product
            unregister_product("edit")
        with teardown_step("imagery gate"):
            self._finish_imagery_gate()
        # Stop generation task. QgsTaskManager owns the task lifecycle, so we
        # request cancellation and drop our reference; the framework drains
        # the run() loop and emits taskTerminated on the main thread.
        with teardown_step("generation worker"):
            if self._worker is not None and self._worker.is_active():
                self._generation_service.cancel()
                drain_task(self._worker, GENERATION_TASK_SIGNALS)
        self._worker = None

        # Same drain for the canvas-export worker. Drop the pending hand-off
        # so a late completed-signal doesn't try to kick a GenerationWorker
        # against a torn-down dock.
        self._pending_generation = None
        with teardown_step("export worker"):
            if self._export_worker is not None and self._export_worker.is_active():
                drain_task(self._export_worker, EXPORT_TASK_SIGNALS)
        self._export_worker = None

        # Stop background loader QgsTasks. We disconnect signals (slots
        # would land on a dying dock) then cancel; the task manager drains
        # the run() loop and disposes of the task itself.
        for attr, signal_names in LOADER_SIGNALS.items():
            with teardown_step(f"loader {attr}"):
                drain_task(getattr(self, attr, None), signal_names)
            setattr(self, attr, None)

        # Drain in-flight history tasks (add-to-map, download, reference reload).
        # Their succeeded/failed slots touch self._canvas / self._iface, which
        # are stale after unload, so disconnect then cancel before teardown.
        with teardown_step("history tasks"):
            for task in list(self._history_tasks):
                drain_task(task, REQUEST_TASK_SIGNALS)
            self._history_tasks.clear()

        # Drop the layer-removal listener before the plugin objects vanish.
        # Broad on purpose: every narrow handler in unload is one AttributeError
        # away from skipping the whole rest of the teardown.
        try:
            QgsProject.instance().layersRemoved.disconnect(
                self._on_project_layers_changed
            )
        except Exception:  # nosec B110 - already disconnected, or project gone
            pass

        # The user's pre-AI-Edit tool is a foreign object we only borrowed a
        # reference to, and only _deactivate_selection_tool ever cleared it -
        # a method unload does not call. Drop it here so the dead plugin graph
        # never keeps a stranded map tool alive. No restore: setMapTool at this
        # point re-fires mapToolSet into handlers that are mid-teardown, and the
        # map-tool section below already returns the canvas to QGIS's default.
        self._previous_map_tool = None

        with teardown_step("selection rectangle"):
            self._clear_selection_rectangle()

        # Drop the mapToolSet listener before the markup tools it references
        # are torn down below (see tool_panels.py._on_markup_maptool_set).
        if self._markup_maptool_set_connected:
            if self._canvas is not None:
                try:
                    self._canvas.mapToolSet.disconnect(self._on_markup_maptool_set)
                except Exception:  # nosec B110 - already disconnected, or canvas gone
                    pass
            self._markup_maptool_set_connected = False

        # Detach any Mark up tool we set on the canvas before our objects vanish.
        if self._markup_tool_objs:
            with teardown_step("markup tool unset"):
                # The canvas C++ half can already be gone (QGIS shutting down,
                # project torn down first). Unguarded, that raise skipped every
                # later section of unload.
                current = self._canvas.mapTool() if self._canvas else None
                if current in self._markup_tool_objs.values():
                    self._canvas.unsetMapTool(current)
            # deleteLater, or the canvas-parented C++ tools pin the dead
            # plugin graph for the rest of the session.
            for tool in self._markup_tool_objs.values():
                try:
                    tool.deleteLater()
                except Exception:  # nosec B110 - C++ tool already gone
                    pass
            self._markup_tool_objs.clear()
        with teardown_step("markup layer"):
            self._clear_markup_layer()
        with teardown_step("markup manager"):
            if self._markup_manager is not None:
                self._markup_manager.disconnect_signals()
        self._markup_manager = None
        self._pre_markup_map_tool = None
        if self._markup_event_filter is not None:
            try:
                self._iface.mainWindow().removeEventFilter(self._markup_event_filter)
            except Exception:  # nosec B110 - main window already torn down
                pass
            self._markup_event_filter = None
        with teardown_step("qgis undo stack"):
            self._restore_qgis_undo()
        if self._launch_shortcut is not None:
            try:
                self._launch_shortcut.setEnabled(False)
                self._launch_shortcut.deleteLater()
            except Exception:  # nosec B110 - C++ shortcut already gone
                pass
            self._launch_shortcut = None

        if self._swipe_controller is not None:
            with teardown_step("swipe controller"):
                self._swipe_controller.cleanup()
            self._swipe_controller = None

        if self._dock_widget:
            # Disconnect QgsProject signals before the dock is destroyed.
            # closeEvent used to do this but firing on every hide also broke
            # the dock when the user re-opened it from the Panels menu.
            with teardown_step("dock cleanup"):
                self._dock_widget.cleanup()
            with teardown_step("dock removal"):
                self._iface.removeDockWidget(self._dock_widget)
            with teardown_step("dock delete"):
                self._dock_widget.deleteLater()
            self._dock_widget = None

        # Wipe session-scoped reference images from disk.
        with teardown_step("reference store"):
            self._reference_store.cleanup()

        with teardown_step("log collector"):
            from ..dialogs.error_report_dialog import stop_log_collector

            stop_log_collector()

        with teardown_step("settings menu entry"):
            if self._settings_action and self._terralab_menu:
                self._terralab_menu.removeAction(self._settings_action)

        ai_seg_action = getattr(self, "_ai_seg_action", None)
        if self._action:
            # The import itself is a step: Plugin Reloader can drop the package
            # mid-unload, and a bare import here used to take the QAction
            # teardown, the map tool, the thread drain and the telemetry
            # shutdown down with it. A failed import leaves the names unbound,
            # so each use below reports itself and the rest still runs.
            with teardown_step("menu helper import"):
                from ..terralab_menu import remove_from_plugins_menu, remove_plugin_from_menu

            with teardown_step("plugins menu entry"):
                remove_from_plugins_menu(self._iface, self._action)
            with teardown_step("terralab menu entry"):
                remove_plugin_from_menu(
                    self._terralab_menu, self._action, self._iface.mainWindow()
                )

            if ai_seg_action is not None:
                with teardown_step("cross-promo plugins menu entry"):
                    remove_from_plugins_menu(self._iface, ai_seg_action)
                with teardown_step("cross-promo terralab menu entry"):
                    remove_plugin_from_menu(
                        self._terralab_menu, ai_seg_action, self._iface.mainWindow())

            with teardown_step("toolbar helper import"):
                from ..terralab_toolbar import remove_action_from_toolbar

            if self._terralab_toolbar:
                with teardown_step("toolbar entry"):
                    remove_action_from_toolbar(
                        self._terralab_toolbar, self._action, self._iface.mainWindow()
                    )
                if ai_seg_action is not None:
                    with teardown_step("cross-promo toolbar entry"):
                        remove_action_from_toolbar(
                            self._terralab_toolbar, ai_seg_action, self._iface.mainWindow()
                        )

        # The three QActions are parented to the QGIS main window, so pulling
        # them out of the menus is not enough: the C++ objects outlive unload
        # still connected to this plugin's bound methods, and every reload
        # stacks another dead plugin graph on the main window.
        for action in (self._settings_action, self._action, ai_seg_action):
            if action is None:
                continue
            with teardown_step("plugin action"):
                try:
                    action.triggered.disconnect()
                except (RuntimeError, TypeError):
                    pass
                action.deleteLater()
        self._settings_action = None
        self._action = None
        self._ai_seg_action = None
        self._terralab_toolbar = None
        self._terralab_menu = None

        if self._map_tool is not None:
            # Detach the selection tool from the canvas before we drop it, or
            # QgsMapCanvas keeps pointing at a torn-down tool and the next click
            # after a reload dispatches into freed state. Mirrors the markup
            # unset above.
            try:
                if self._canvas is not None and self._canvas.mapTool() is self._map_tool:
                    self._canvas.unsetMapTool(self._map_tool)
            except Exception:  # nosec B110 - C++ canvas already gone
                pass
            # Disconnect the plugin-facing signals, then deleteLater: the
            # canvas-parented C++ tool otherwise pins the dead plugin graph.
            disconnect_signals(self._map_tool, MAP_TOOL_SIGNALS)
            try:
                self._map_tool.cleanup()
            except Exception as err:  # nosec B110
                log_warning(f"Map tool cleanup failed: {err}")
            try:
                self._map_tool.deleteLater()
            except Exception:  # nosec B110 - C++ tool already gone
                pass
        self._map_tool = None

        # Join the detached Prompt Library threads last, once the visible UI is
        # gone: Qt aborts the process when it destroys a QThread still in run().
        with teardown_step("prompt library workers"):
            from ..dialogs.prompt_templates.workers import drain_prompt_library_workers

            drain_prompt_library_workers()
        drain_hook = getattr(self, "_prompt_library_drain", None)
        if drain_hook is not None:
            with teardown_step("about-to-quit hook"):
                QgsApplication.instance().aboutToQuit.disconnect(drain_hook)
            self._prompt_library_drain = None

        with teardown_step("config cache"):
            clear_config_cache()
        # Cancel any in-flight telemetry flush tasks before tearing down the
        # store so QgsTaskManager doesn't outlive the collector.
        with teardown_step("telemetry"):
            telemetry.shutdown_telemetry()
        with teardown_step("config store"):
            if self._config_store is not None:
                self._config_store.clear()
            set_store(None)
        log("AI Edit plugin unloaded")

    def _register_processing_provider(self):
        """Add the AI Edit provider to the Processing registry.

        Imported here rather than at module level so plugin load stays light,
        and so a QGIS build without the Processing plugin enabled fails on this
        one call instead of on the import of the whole controller.
        """
        from ...processing.edit_provider import TerraEditProcessingProvider

        provider = TerraEditProcessingProvider()
        provider_id = provider.id()
        # addProvider returns False AND deletes the provider it was given when
        # the id is already taken, which leaves a Python wrapper around a dead
        # C++ object. Holding that would make the matching removeProvider raise
        # on unload, so drop the reference instead of keeping a corpse.
        registry = QgsApplication.processingRegistry()
        if not registry.addProvider(provider):
            # Reloading the plugin can leave the previous provider behind with
            # its Python half collected: it answers to no id and lists no
            # algorithm, and it holds the name against us. Whoever reloaded
            # would have no algorithms until they restart QGIS, so take the id
            # back rather than stopping here. By id, never by object: the
            # object overload calls provider->id(), the pure virtual whose
            # Python override is exactly what a half-collected provider has
            # lost. A fresh instance is needed because the one above is
            # already deleted.
            registry.removeProvider(provider_id)
            provider = TerraEditProcessingProvider()
            if not registry.addProvider(provider):
                self._processing_provider = None
                log_warning(
                    f"Processing provider '{provider_id}' was not registered: "
                    "the id is already taken."
                )
                return
        self._processing_provider = provider

    def _unregister_processing_provider(self):
        """Remove the provider, so a reload does not leave two of them registered."""
        provider = getattr(self, "_processing_provider", None)
        # Dropped before the call, never after: a raise on an already-deleted
        # provider would otherwise leave the attribute set and the next unload
        # would retry the same dead object.
        self._processing_provider = None
        if provider is None:
            return
        from ...processing.edit_provider import TERRAEDIT_PROVIDER_ID

        # By id, never by object: the object overload calls provider->id() in
        # C++, and that override is gone the moment the Python half is
        # collected. The id is a constant, so it survives.
        QgsApplication.processingRegistry().removeProvider(TERRAEDIT_PROVIDER_ID)
