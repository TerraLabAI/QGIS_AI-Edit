from __future__ import annotations

import os
import sys
from contextlib import contextmanager

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtGui import QIcon, QKeySequence

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import clear_config_cache, migrate_legacy_key
from ...core.config_store import get_export_copy, set_store
from ...core.errors import build_failure_props
from ...core.i18n import tr
from ...core.logger import log, log_warning
from ...core.qt_compat import QAction, QShortcut
from ..dock_widget import AIEditDockWidget
from ..tools.polygon_selection_tool import PolygonSelectionTool


LAUNCH_SHORTCUT = "Ctrl+Alt+E"




GENERATION_TASK_SIGNALS = ("succeeded", "progress", "failed", "taskTerminated")
EXPORT_TASK_SIGNALS = ("completed", "failed", "taskTerminated")
REQUEST_TASK_SIGNALS = ("succeeded", "failed")
PAIRING_TASK_SIGNALS = (
    "pairing_succeeded",
    "pairing_failed",
    "pairing_timeout",
    "pairing_browser_seen",
    "pairing_stalled",
    "pairing_network_problem",
    "taskTerminated",
)


LOADER_SIGNALS = {
    "_export_config_loader": REQUEST_TASK_SIGNALS,
    "_credits_loader": REQUEST_TASK_SIGNALS,
    "_key_validation_worker": REQUEST_TASK_SIGNALS,
    "_pairing_worker": PAIRING_TASK_SIGNALS,
    "_catalog_loader": REQUEST_TASK_SIGNALS,
    "_bootstrap_task": REQUEST_TASK_SIGNALS,
    "_tuned_config_task": REQUEST_TASK_SIGNALS,
    "_export_size_task": REQUEST_TASK_SIGNALS,
    "_zone_size_task": REQUEST_TASK_SIGNALS,
    "_activation_config_loader": REQUEST_TASK_SIGNALS,

    "_basemap_probe_task": REQUEST_TASK_SIGNALS,
}

_PROMPT_LIBRARY_WORKERS_MODULE = (
    __name__.rsplit(".plugin_parts", 1)[0] + ".dialogs.prompt_templates.workers"
)


def drain_prompt_library_if_loaded() -> None:


    module = sys.modules.get(_PROMPT_LIBRARY_WORKERS_MODULE)
    if module is not None:
        module.drain_prompt_library_workers()



MAP_TOOL_SIGNALS = (
    "selection_made",
    "zone_too_small",
    "zone_invalid",
    "zone_delete_requested",
    "compare_requested",
    "vectorize_requested",
)


def disconnect_signals(obj, signal_names):






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




    if task is None:
        return
    disconnect_signals(task, signal_names)
    try:
        task.cancel()
    except Exception:  # nosec B110
        pass


def safe_error_text(err: BaseException) -> str:






    try:
        return str(err)
    except Exception:  # noqa: BLE001
        try:
            return type(err).__name__
        except Exception:  # noqa: BLE001
            return "unprintable error"


def step_error_code(label: str, stage: str) -> str:





    try:
        slug = "".join(c if c.isalnum() else "_" for c in str(label).lower())
    except Exception:  # noqa: BLE001
        slug = ""
    return f"{stage}_{slug.strip('_') or 'step'}"[:60]


def is_deleted_qt_object_error(err: BaseException) -> bool:








    if not isinstance(err, RuntimeError):
        return False
    return "has been deleted" in safe_error_text(err)


def report_teardown_failure(label: str, err: BaseException, stage: str = "unload") -> None:












    detail = safe_error_text(err)
    try:
        log_warning(f"Guarded step failed ({stage}/{label}): {detail}")
    except Exception:  # nosec B110
        pass
    try:
        if is_deleted_qt_object_error(err):
            return
        telemetry.track(
            te.PLUGIN_ERROR,
            build_failure_props(stage, step_error_code(label, stage), detail),
        )
    except Exception:  # nosec B110
        pass


def release_dock_widget(iface, dock) -> None:









    if dock is None:
        return
    try:
        iface.removeDockWidget(dock)
    finally:
        try:
            dock.hide()
            dock.setParent(None)
        finally:
            dock.deleteLater()


@contextmanager
def teardown_step(label: str, stage: str = "unload"):










    try:
        yield
    except Exception as err:  # noqa: BLE001
        report_teardown_failure(label, err, stage)


class PluginLifecycleMixin:
    def initGui(self):

        try:
            migrate_legacy_key()
        except Exception as err:  # nosec B110
            log_warning(f"Auth migration raised: {err}")







        self.mcp_api = None
        try:
            from ...mcp_api import EditMCPAPI
            self.mcp_api = EditMCPAPI(self)
        except Exception as err:  # nosec B110
            log_warning(f"Public API not built: {err}")






        self._agent_bridge_registered = False
        self._agent_bridge_unregister = None
        if self.mcp_api is not None:
            try:
                from ...agent_bridge import register_product, unregister_product
                register_product("edit", self.mcp_api)
                self._agent_bridge_registered = True



                self._agent_bridge_unregister = unregister_product
            except Exception as err:  # nosec B110
                log_warning(f"Agent bridge not published: {err}")




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


        settings_icon = QIcon(":/images/themes/default/mActionOptions.svg")


        self._settings_action = QAction(settings_icon, get_export_copy(
            "widgets.terralab_menu.ai_edit_settings", tr("AI Edit Settings...")), main_window)
        self._settings_action.setObjectName("_terralab_settings_action")

        self._settings_action.setMenuRole(QAction.MenuRole.NoRole)
        self._settings_action.triggered.connect(self._on_settings_clicked)

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


        self._dock_widget = AIEditDockWidget(
            self._iface.mainWindow(), reference_store=self._reference_store
        )
        self._dock_widget.set_library_dependencies(self._client, self._auth_manager)


        def _load_stale_catalog():
            try:
                from ...core.prompts.prompt_presets_client import read_cached_catalog_stale_ok

                if self._dock_widget is not None:
                    self._dock_widget.set_server_catalog(read_cached_catalog_stale_ok())
            except Exception as err:  # noqa: BLE001
                log_warning(f"Server catalog stale read failed: {err}")
                try:
                    if self._dock_widget is not None:
                        self._dock_widget.set_server_catalog(None)
                except RuntimeError:  # nosec B110
                    pass
        QtC.safe_single_shot(0, self._dock_widget, _load_stale_catalog)


        self._iface.addDockWidget(QtC.RightDockWidgetArea, self._dock_widget)



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


        self._update_check_done = False
        self._update_check_delays = [5000, 30000, 60000, 120000]
        self._update_check_index = 0


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
        self._dock_widget.zone_source_picked.connect(self._on_zone_source_picked)
        self._dock_widget.markup_clicked.connect(self._on_markup_clicked)
        self._dock_widget.markup_done_clicked.connect(self._on_markup_done_clicked)
        self._dock_widget.markup_clear_clicked.connect(self._on_markup_clear_clicked)
        self._dock_widget.markup_undo_clicked.connect(self._on_markup_undo)
        self._dock_widget.markup_tool_changed.connect(self._on_markup_tool_changed)
        self._dock_widget.markup_color_changed.connect(self._on_markup_color_changed)
        self._dock_widget.vectorize_clicked.connect(self._on_vectorize_clicked)
        self._dock_widget.vectorize_done_clicked.connect(self._on_vectorize_done_clicked)
        self._dock_widget.reference_panel_requested.connect(self._on_reference_clicked)
        self._dock_widget.reference_done_clicked.connect(self._on_reference_done_clicked)
        self._dock_widget.reference_capture_requested.connect(
            self._on_reference_capture_requested
        )
        self._dock_widget.vectorize_suggestion_clicked.connect(
            self._on_vectorize_suggestion_clicked
        )


        self._dock_widget.conversation_delete.connect(self._on_conversation_delete)
        self._dock_widget.conversation_rename.connect(self._on_conversation_rename)
        self._dock_widget.conversations_page_requested.connect(
            self._on_conversations_page_requested
        )
        self._dock_widget.conversations_refresh_requested.connect(
            self._refresh_conversations_cache
        )


        self._refresh_conversations_cache()



        from ..panels.swipe_panel import SwipeController



        self._swipe_controller = SwipeController(None)
        self._dock_widget.swipe_toggled.connect(self._on_swipe_toggled)
        self._swipe_controller.activated.connect(self._on_swipe_armed)
        self._swipe_controller.deactivated.connect(self._on_swipe_disarmed)
        self._swipe_controller.eligibility_changed.connect(
            self._dock_widget.set_swipe_button_enabled
        )


        self._dock_widget.set_swipe_button_enabled(
            self._swipe_controller.can_swipe_now()
        )


        self._dock_widget.visibilityChanged.connect(self._on_dock_visibility_changed)





        QgsProject.instance().layersRemoved.connect(self._on_project_layers_changed)




        self._launch_shortcut = QShortcut(
            QKeySequence(LAUNCH_SHORTCUT),
            self._iface.mainWindow(),
        )
        self._launch_shortcut.setContext(QtC.WindowShortcut)
        self._launch_shortcut.activated.connect(self._on_launch_shortcut_key)


        self._map_tool = PolygonSelectionTool(self._canvas)
        self._map_tool.selection_made.connect(self._on_zone_selected)
        self._map_tool.zone_too_small.connect(self._on_zone_too_small)
        self._map_tool.zone_invalid.connect(self._on_zone_invalid)
        self._map_tool.zone_delete_requested.connect(self._on_zone_delete_requested)
        self._map_tool.compare_requested.connect(self._on_canvas_compare)
        self._map_tool.vectorize_requested.connect(self._on_canvas_vectorize)




        self._check_activation_state(validate=False)
        if not self._auth_manager.has_activation_key():
            telemetry.track(te.ACTIVATION_SCREEN_VIEWED)

        from ..dialogs.error_report_dialog import start_log_collector

        start_log_collector()




        self._prompt_library_drain = drain_prompt_library_if_loaded
        try:
            QgsApplication.instance().aboutToQuit.connect(drain_prompt_library_if_loaded)
        except (AttributeError, TypeError, RuntimeError) as err:
            self._prompt_library_drain = None
            log_warning(f"Prompt Library drain not hooked to aboutToQuit: {err}")


        telemetry.init_telemetry(
            self._client, self._auth_manager, self._read_plugin_version()
        )



        try:
            QgsApplication.instance().aboutToQuit.connect(
                telemetry.shutdown_telemetry
            )
        except (AttributeError, TypeError, RuntimeError) as err:
            log_warning(f"Telemetry drain not hooked to aboutToQuit: {err}")


        if auto_open_source is not None:
            self._emit_plugin_opened(auto_open_source)








        if self._dock_widget.isVisible():
            self._maybe_bootstrap_on_show()

        if self._dev_mode:
            log("AI Edit plugin loaded [DEV MODE]")
        else:
            log("AI Edit plugin loaded")
        if self._skip_trial_check:
            log_warning("DEV MODE: SKIP_TRIAL_CHECK is active - auth checks bypassed")

    def unload(self):







        self._update_check_done = True

        with teardown_step("processing provider"):
            self._unregister_processing_provider()

        with teardown_step("agent bridge"):
            unregister = getattr(self, "_agent_bridge_unregister", None)
            if getattr(self, "_agent_bridge_registered", False) and unregister is not None:
                self._agent_bridge_registered = False
                self._agent_bridge_unregister = None
                unregister("edit")
        with teardown_step("imagery gate"):
            self._finish_imagery_gate()

        with teardown_step("sibling sign-in"):
            from ...core import sibling_sign_in
            sibling_sign_in.cancel("ai-edit")



        with teardown_step("generation worker"):
            if self._worker is not None and self._worker.is_active():
                self._generation_service.cancel()
                drain_task(self._worker, GENERATION_TASK_SIGNALS)
        self._worker = None




        self._pending_generation = None
        with teardown_step("export worker"):
            if self._export_worker is not None and self._export_worker.is_active():
                drain_task(self._export_worker, EXPORT_TASK_SIGNALS)
        self._export_worker = None




        for attr, signal_names in LOADER_SIGNALS.items():
            with teardown_step(f"loader {attr}"):
                drain_task(getattr(self, attr, None), signal_names)
            setattr(self, attr, None)




        with teardown_step("history tasks"):
            for task in list(self._history_tasks):
                drain_task(task, REQUEST_TASK_SIGNALS)
            self._history_tasks.clear()




        try:
            QgsProject.instance().layersRemoved.disconnect(
                self._on_project_layers_changed
            )
        except Exception:  # nosec B110
            pass







        self._previous_map_tool = None

        with teardown_step("reference capture tool"):
            self._teardown_reference_capture()

        with teardown_step("selection rectangle"):
            self._clear_selection_rectangle()



        if self._markup_maptool_set_connected:
            if self._canvas is not None:
                try:
                    self._canvas.mapToolSet.disconnect(self._on_markup_maptool_set)
                except Exception:  # nosec B110
                    pass
            self._markup_maptool_set_connected = False


        if self._markup_tool_objs:
            with teardown_step("markup tool unset"):



                current = self._canvas.mapTool() if self._canvas else None
                if current in self._markup_tool_objs.values():
                    self._canvas.unsetMapTool(current)


            for tool in self._markup_tool_objs.values():
                try:
                    tool.deleteLater()
                except Exception:  # nosec B110
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
            except Exception:  # nosec B110
                pass
            self._markup_event_filter = None
        with teardown_step("qgis undo stack"):
            self._restore_qgis_undo()
        if self._launch_shortcut is not None:
            try:
                self._launch_shortcut.setEnabled(False)
                self._launch_shortcut.deleteLater()
            except Exception:  # nosec B110
                pass
            self._launch_shortcut = None

        if self._swipe_controller is not None:
            with teardown_step("swipe controller"):
                self._swipe_controller.cleanup()
            self._swipe_controller = None


        if self._privacy_notice_dialog is not None:
            with teardown_step("privacy notice"):
                self._privacy_notice_dialog.reject()
            self._privacy_notice_dialog = None

        if self._dock_widget:



            with teardown_step("dock cleanup"):
                self._dock_widget.cleanup()



            try:
                self._dock_widget.visibilityChanged.disconnect(self._on_dock_visibility_changed)
            except Exception:  # nosec B110
                pass
            with teardown_step("dock removal"):
                release_dock_widget(self._iface, self._dock_widget)
            self._dock_widget = None


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
                from ..terralab_toolbar import (
                    is_terralab_toolbar_alive,
                    remove_action_from_toolbar,
                )



            toolbar_alive = False
            with teardown_step("toolbar liveness"):
                toolbar_alive = is_terralab_toolbar_alive(self._terralab_toolbar)
            if toolbar_alive and ai_seg_action is not None:
                with teardown_step("cross-promo toolbar entry"):
                    toolbar_alive = remove_action_from_toolbar(
                        self._terralab_toolbar, ai_seg_action, self._iface.mainWindow()
                    )
            if toolbar_alive:
                with teardown_step("toolbar entry"):
                    remove_action_from_toolbar(
                        self._terralab_toolbar, self._action, self._iface.mainWindow()
                    )





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




            try:
                if self._canvas is not None and self._canvas.mapTool() is self._map_tool:
                    self._canvas.unsetMapTool(self._map_tool)
            except Exception:  # nosec B110
                pass


            disconnect_signals(self._map_tool, MAP_TOOL_SIGNALS)
            try:
                self._map_tool.cleanup()
            except Exception as err:  # nosec B110
                log_warning(f"Map tool cleanup failed: {err}")
            try:
                self._map_tool.deleteLater()
            except Exception:  # nosec B110
                pass
        self._map_tool = None



        with teardown_step("prompt library workers"):
            drain_prompt_library_if_loaded()
        drain_hook = getattr(self, "_prompt_library_drain", None)
        if drain_hook is not None:
            with teardown_step("about-to-quit hook"):
                try:
                    QgsApplication.instance().aboutToQuit.disconnect(drain_hook)
                except (RuntimeError, TypeError):
                    pass
            self._prompt_library_drain = None

        with teardown_step("config cache"):
            clear_config_cache()
        with teardown_step("telemetry about-to-quit hook"):
            QgsApplication.instance().aboutToQuit.disconnect(
                telemetry.shutdown_telemetry
            )


        with teardown_step("telemetry"):
            telemetry.shutdown_telemetry()
        with teardown_step("config store"):
            if self._config_store is not None:
                self._config_store.clear()
            set_store(None)
        log("AI Edit plugin unloaded")
