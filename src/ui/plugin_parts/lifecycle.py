from __future__ import annotations

import os

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QSettings, QTimer
from qgis.PyQt.QtGui import QAction, QIcon, QKeySequence, QShortcut

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import clear_config_cache, migrate_legacy_key
from ...core.config_store import set_store
from ...core.i18n import tr
from ...core.logger import log, log_warning
from ..dock_widget import AIEditDockWidget
from ..tools.selection_map_tool import RectangleSelectionTool

# Qt maps Ctrl -> Cmd on macOS automatically.
LAUNCH_SHORTCUT = "Ctrl+Alt+E"


class PluginLifecycleMixin:
    def initGui(self):
        # Idempotent; defers silently if auth DB is locked.
        try:
            migrate_legacy_key()
        except Exception as err:  # nosec B110
            log_warning(f"Auth migration raised: {err}")

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
        self._dock_widget.vectorize_suggestion_clicked.connect(
            self._on_vectorize_suggestion_clicked
        )
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
        self._map_tool = RectangleSelectionTool(self._canvas)
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
        """Called by QGIS when plugin is unloaded."""
        # Make any pending plugin-update-check timer a no-op (belt-and-suspenders
        # alongside parenting it to the dock).
        self._update_check_done = True
        # Tear down any in-flight onboarding tile-warm-up watcher (disconnects
        # the canvas signal so it can't fire against a torn-down dock).
        self._finish_imagery_gate()
        # Stop generation task. QgsTaskManager owns the task lifecycle, so we
        # request cancellation and drop our reference; the framework drains
        # the run() loop and emits taskTerminated on the main thread.
        if self._worker is not None and self._worker.is_active():
            self._generation_service.cancel()
            for sig in [
                self._worker.succeeded,
                self._worker.progress,
                self._worker.failed,
                self._worker.taskTerminated,
            ]:
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
            self._pairing_worker,
            self._catalog_loader,
            self._bootstrap_task,
            self._activation_config_loader,
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
        self._pairing_worker = None
        self._catalog_loader = None
        self._bootstrap_task = None

        # Drain in-flight history tasks (add-to-map, download, reference reload).
        # Their succeeded/failed slots touch self._canvas / self._iface, which
        # are stale after unload, so disconnect then cancel before teardown.
        for task in list(self._history_tasks):
            try:
                task.succeeded.disconnect()
                task.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
            try:
                task.cancel()
            except Exception:  # nosec B110
                pass
        self._history_tasks.clear()

        # Drop the layer-removal listener before the plugin objects vanish.
        try:
            QgsProject.instance().layersRemoved.disconnect(
                self._on_project_layers_changed
            )
        except (RuntimeError, TypeError):
            pass

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

        from ..dialogs.error_report_dialog import stop_log_collector

        stop_log_collector()

        if self._settings_action and self._terralab_menu:
            self._terralab_menu.removeAction(self._settings_action)
            self._settings_action = None

        if self._action:
            from ..terralab_menu import remove_from_plugins_menu, remove_plugin_from_menu

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

            from ..terralab_toolbar import remove_action_from_toolbar

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
            # Detach the selection tool from the canvas before we drop it, or
            # QgsMapCanvas keeps pointing at a torn-down tool and the next click
            # after a reload dispatches into freed state. Mirrors the markup
            # unset above.
            try:
                if self._canvas is not None and self._canvas.mapTool() is self._map_tool:
                    self._canvas.unsetMapTool(self._map_tool)
            except RuntimeError:  # nosec B110 - C++ canvas already gone
                pass
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
