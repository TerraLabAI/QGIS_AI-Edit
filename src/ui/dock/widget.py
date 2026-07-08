from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeySequence
from qgis.PyQt.QtWidgets import QApplication, QDockWidget

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.qt_compat import QShortcut
from ...core.reference_image_store import ReferenceImageStore
from ...core.resolution_labels import DEFAULT_RESOLUTION_CREDIT_COSTS
from ..keyboard_focus import apply_keyboard_focus_policy
from ..panel_helpers import make_section_header
from .account import DockAccountMixin
from .blocked_reasons import DockBlockedReasonsMixin
from .build import build_ui
from .chrome import DockChromeMixin
from .dock_sizing import dock_minimum_height
from .generation_state import DockGenerationStateMixin
from .library import DockLibraryMixin
from .pro_ceiling import DockProCeilingMixin
from .pro_nudges import DockProNudgesMixin
from .prompts import DockPromptMixin
from .tools_footer import DockToolsFooterMixin
from .update_banner import DockUpdateBannerMixin
from .versions import DockVersionsMixin
from .zone_sources import DockZoneSourcesMixin


__all__ = [
    "_make_section_header",
    "AIEditDockWidget",
]

_make_section_header = make_section_header


class AIEditDockWidget(
    DockChromeMixin,
    DockUpdateBannerMixin,
    DockAccountMixin,
    DockBlockedReasonsMixin,
    DockLibraryMixin,
    DockGenerationStateMixin,
    DockProCeilingMixin,
    DockProNudgesMixin,
    DockVersionsMixin,
    DockPromptMixin,
    DockToolsFooterMixin,
    DockZoneSourcesMixin,
    QDockWidget,
):







    stop_clicked = pyqtSignal()
    generate_clicked = pyqtSignal(str)



    base_version_selected = pyqtSignal(int)
    retry_clicked = pyqtSignal(str)
    pairing_requested = pyqtSignal(str)
    pairing_cancel_requested = pyqtSignal(str)
    settings_clicked = pyqtSignal()
    launch_clicked = pyqtSignal()
    try_example_requested = pyqtSignal()
    exit_clicked = pyqtSignal()
    zone_clear_requested = pyqtSignal()


    zone_source_picked = pyqtSignal(str, str)
    markup_clicked = pyqtSignal()
    vectorize_clicked = pyqtSignal()


    reference_panel_requested = pyqtSignal()
    reference_done_clicked = pyqtSignal()
    reference_capture_requested = pyqtSignal()





    vectorize_suggestion_clicked = pyqtSignal(str, str, str, str)



    swipe_toggled = pyqtSignal(bool)
    markup_done_clicked = pyqtSignal()
    markup_clear_clicked = pyqtSignal()
    markup_undo_clicked = pyqtSignal()
    markup_tool_changed = pyqtSignal(str)
    markup_color_changed = pyqtSignal(QColor)
    vectorize_done_clicked = pyqtSignal()

    template_selected = pyqtSignal(str, str)




    catalog_refresh_requested = pyqtSignal()



    history_add_to_map = pyqtSignal(dict)
    history_download = pyqtSignal(dict)


    history_restore = pyqtSignal(dict)



    conversation_delete = pyqtSignal(dict)
    conversation_rename = pyqtSignal(dict)
    conversations_page_requested = pyqtSignal(str)


    conversations_refresh_requested = pyqtSignal()

    def __init__(self, parent=None, reference_store: ReferenceImageStore | None = None):
        super().__init__(get_export_copy("dock.widget.title", tr("AI Edit by TerraLab")), parent)


        self.setObjectName("AIEditDockWidget")
        self.setAllowedAreas(QtC.LeftDockWidgetArea | QtC.RightDockWidgetArea)

        try:
            char_w = self.fontMetrics().averageCharWidth()
            self.setMinimumWidth(max(300, int(char_w * 50)))
        except Exception:
            self.setMinimumWidth(300)


        self.setMinimumHeight(dock_minimum_height(self))
        self._reference_store = reference_store
        self._library_client = None
        self._library_auth_manager = None
        self._server_catalog: dict | None = None







        from ...core.prompts import history_cache as _history_cache

        self._library_recent_cache: list = _history_cache.get_recent_jobs()
        self._library_favorite_cache: list = _history_cache.get_favorite_jobs()
        self._library_history_loaded = bool(
            self._library_recent_cache or self._library_favorite_cache
        )
        self._library_history_dirty = True




        self._active_template_id: str | None = None
        self._active_template_name: str | None = None


        self._status_hide_timer: QTimer | None = None









        self._escape_shortcut = QShortcut(QKeySequence(QtC.Key_Escape), self)
        self._escape_shortcut.setContext(QtC.WindowShortcut)
        self._escape_shortcut.activated.connect(self._on_escape_pressed)




        self._generate_shortcut_return = QShortcut(QKeySequence(QtC.Key_Return), self)
        self._generate_shortcut_return.setContext(QtC.WindowShortcut)
        self._generate_shortcut_return.activated.connect(self._on_generate_shortcut)
        self._generate_shortcut_enter = QShortcut(QKeySequence(QtC.Key_Enter), self)
        self._generate_shortcut_enter.setContext(QtC.WindowShortcut)
        self._generate_shortcut_enter.activated.connect(self._on_generate_shortcut)




        self._layer_warning_timer = QTimer(self)
        self._layer_warning_timer.setSingleShot(True)
        self._layer_warning_timer.timeout.connect(self._run_layer_warning_update)
        self._layer_warning_dirty = False

        self._setup_title_bar()

        build_ui(self)

        apply_keyboard_focus_policy(self)



        self.markup_clicked.connect(self._mark_guide_ai_touched)


        self._zone_selected = False


        self._imagery_loading = False
        self._activated = False
        self._checking_credits = False
        self._swipe_eligible = False
        self._swipe_panel_lock = False
        self._is_free_tier = True
        self._cached_used: int | None = None
        self._cached_limit: int | None = None




        self._reset_date: str | None = None



        self._wall_telemetry_shown = False
        self._prewall_telemetry_shown = False




        self._selected_resolution = "1K"
        self._resolution_user_choice = False



        self._resolution_credit_costs: dict[str, int] = dict(DEFAULT_RESOLUTION_CREDIT_COSTS)











        QgsProject.instance().layersAdded.connect(self._schedule_layer_warning_update)
        QgsProject.instance().layersRemoved.connect(self._schedule_layer_warning_update)
        QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
            self._schedule_layer_warning_update
        )
        QgsProject.instance().readProject.connect(self._on_project_loaded)
        QgsProject.instance().cleared.connect(self._on_project_loaded)
        self._update_layer_warning()



        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._refresh_dock_key_shortcuts)
        canvas = self._map_canvas_or_none()
        if canvas is not None:
            canvas.mapToolSet.connect(self._refresh_dock_key_shortcuts)
        self.visibilityChanged.connect(self._refresh_dock_key_shortcuts)
        self._refresh_dock_key_shortcuts()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)

        apply_keyboard_focus_policy(self)


        if self._layer_warning_dirty:
            self._schedule_layer_warning_update()


        prep_ticker = getattr(self, "_prep_ticker", None)
        if getattr(self, "_resume_prep_ticker_on_show", False) and prep_ticker is not None:
            prep_ticker.start()
        self._resume_prep_ticker_on_show = False
        progress_timer = getattr(self, "_progress_timer", None)
        if (
            progress_timer is not None
            and self._progress_widget.isVisibleTo(self)
            and self._progress_bar.value() < getattr(
                self, "_progress_target", self._progress_bar.value()
            )
        ):
            progress_timer.start()

    def hideEvent(self, event):  # noqa: N802
        prep_ticker = getattr(self, "_prep_ticker", None)
        self._resume_prep_ticker_on_show = bool(
            prep_ticker is not None and prep_ticker.isActive()
        )
        self._stop_prep_ticker()
        self._stop_progress_animation()
        super().hideEvent(event)

    @staticmethod
    def _map_canvas_or_none():
        try:
            from qgis.utils import iface as _iface
            return _iface.mapCanvas() if _iface is not None else None
        except Exception:
            return None

    def _refresh_dock_key_shortcuts(self, *_args) -> None:





        try:
            ours = self.isVisible() and self._is_escape_for_us()
            vectorize_open = self._vectorize_panel.isVisible()
            self._escape_shortcut.setEnabled(ours)

            for shortcut in (self._generate_shortcut_return, self._generate_shortcut_enter):
                shortcut.setEnabled(ours and not vectorize_open)
        except (RuntimeError, AttributeError):
            pass

    def _on_escape_pressed(self):

















        if not self.isVisible():
            return
        if self._progress_widget.isVisible():
            return





        if not self._is_escape_for_us():
            return








        if self._swipe_btn.isChecked():
            self._swipe_btn.click()
            return



        line_tool = self._active_markup_line_tool()
        if line_tool is not None:
            line_tool.escape_step()
            return
        if not self._main_widget.isVisible():


            for name in ("_markup_panel", "_vectorize_panel", "_reference_panel"):
                panel = getattr(self, name, None)
                if panel is not None and panel.isVisible():
                    panel.done_clicked.emit()
                    return
            return
        draw_tool = self._active_polygon_draw_tool()
        if draw_tool is not None:
            draw_tool.clear_in_progress_drawing()
            return
        if self._zone_selected and self._prompt_section.isVisible():
            self.zone_clear_requested.emit()
            return
        self.exit_clicked.emit()

    def _is_escape_for_us(self) -> bool:







        def inside(widget, ancestor) -> bool:
            while widget is not None:
                if widget is ancestor:
                    return True
                widget = widget.parent()
            return False

        focus = QApplication.focusWidget()
        if focus is not None and inside(focus, self):
            return True
        canvas = self._map_canvas_or_none()
        if canvas is None:
            return False
        try:
            tool = canvas.mapTool()
        except Exception:
            return False
        if tool is None:
            return False
        from ..panels.swipe_panel import _SwipeMapTool
        from ..tools.markup_tools import _MarkupBaseMapTool
        from ..tools.polygon_selection_tool import PolygonSelectionTool
        if not isinstance(
            tool, (PolygonSelectionTool, _MarkupBaseMapTool, _SwipeMapTool)
        ):
            return False
        return focus is None or inside(focus, canvas)

    def _active_polygon_draw_tool(self):









        from ..tools.polygon_selection_tool import PolygonSelectionTool

        try:
            from qgis.utils import iface as _iface
            if _iface is None:
                return None
            tool = _iface.mapCanvas().mapTool()
        except Exception:
            return None
        if isinstance(tool, PolygonSelectionTool) and tool.has_points():
            return tool
        return None

    def _active_markup_line_tool(self):







        from ..tools.markup_line_tool import LineMapTool

        try:
            from qgis.utils import iface as _iface
            if _iface is None:
                return None
            tool = _iface.mapCanvas().mapTool()
        except Exception:
            return None
        if isinstance(tool, LineMapTool):
            return tool
        return None

    def closeEvent(self, event):







        self._stop_progress_animation()
        self._vectorize_panel.deactivate()
        super().closeEvent(event)

    def cleanup(self):

        self.cleanup_account_check()
        self.disconnect_update_refresh()




        try:
            self._vectorize_panel.deactivate()
        except Exception:  # nosec B110
            pass
        try:
            self._vectorize_panel.cancel_eyedropper()
        except Exception:  # nosec B110
            pass
        try:
            QgsProject.instance().layersAdded.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layersRemoved.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._schedule_layer_warning_update
            )
        except (TypeError, RuntimeError):
            pass
        self._layer_warning_timer.stop()


        try:
            app = QApplication.instance()
            if app is not None:
                app.focusChanged.disconnect(self._refresh_dock_key_shortcuts)
        except (TypeError, RuntimeError):
            pass
        try:
            canvas = self._map_canvas_or_none()
            if canvas is not None:
                canvas.mapToolSet.disconnect(self._refresh_dock_key_shortcuts)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().readProject.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().cleared.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass

        for combo in (
            getattr(self._vectorize_panel, "_layer_combo", None),
            getattr(self, "_layer_combo", None),
        ):
            try:
                if combo is not None and hasattr(combo, "cleanup"):
                    combo.cleanup()
            except Exception:  # nosec B110
                pass
