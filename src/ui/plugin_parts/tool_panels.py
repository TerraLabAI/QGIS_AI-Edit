from __future__ import annotations

from qgis.core import Qgis, QgsRectangle
from qgis.PyQt.QtCore import QEvent, QObject, QTimer
from qgis.PyQt.QtGui import QColor, QKeySequence

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.logger import log_warning
from ...core.qt_compat import QAction
from ..tools.markup_line_tool import LineMapTool
from ..tools.markup_tools import (
    ArrowMapTool,
    CircleMapTool,
    MarkupLayerManager,
    PencilMapTool,
)




_MARKUP_OUTSIDE_NOTICE_MS = 3000


class _MarkupUndoFilter(QObject):






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


class ToolPanelsMixin:


    def _leave_other_tool_panel(self, target: str) -> None:





        current = getattr(self, "_in_tool_panel", None)
        if current is not None and current != target:
            self._exit_tool_panel()

    def _tool_feature_blocked(self, name: str) -> bool:

        dock = getattr(self, "_dock_widget", None)
        if dock is None:
            return False
        return dock._feature_blocked(name)

    def _on_markup_clicked(self):





        self._disarm_swipe()
        if self._in_tool_panel == "markup":

            self._on_markup_done_clicked()
            return
        if self._tool_feature_blocked("markup"):
            return


        self._leave_other_tool_panel("markup")
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()
        if self._markup_manager is None:
            self._markup_manager = MarkupLayerManager(self._canvas, self._dock_widget)
            self._markup_manager.annotation_count_changed.connect(
                self._dock_widget.set_markup_annotation_count
            )
            self._markup_manager.outside_zone_attempted.connect(
                self._on_markup_outside_zone
            )

        current = self._canvas.mapTool()
        if current is not None and current not in self._markup_tool_objs.values():
            self._pre_markup_map_tool = current
        self._in_tool_panel = "markup"
        self._dock_widget.set_markup_state()
        self._dock_widget.set_markup_zone_present(self._selected_extent is not None)
        self._markup_manager.set_clip_zone(self._selected_extent, self._selected_polygon)
        self._dock_widget.set_markup_annotation_count(
            self._markup_manager.annotation_count()
        )


        if self._markup_event_filter is None:
            self._markup_event_filter = _MarkupUndoFilter(self._on_markup_undo)
        self._iface.mainWindow().installEventFilter(self._markup_event_filter)
        self._suppress_qgis_undo()




        if not self._markup_maptool_set_connected:
            try:
                self._canvas.mapToolSet.connect(self._on_markup_maptool_set)
                self._markup_maptool_set_connected = True
            except (TypeError, RuntimeError):
                pass
        telemetry.track(te.MARKUP_OPENED)

    def _on_markup_tool_changed(self, tool_key: str):

        if self._markup_manager is None:
            return
        existing = self._markup_tool_objs.get(tool_key)
        if existing is None:
            if tool_key == "pencil":
                existing = PencilMapTool(self._canvas, self._markup_manager)
            elif tool_key == "line":
                existing = LineMapTool(self._canvas, self._markup_manager)
            elif tool_key == "arrow":
                existing = ArrowMapTool(self._canvas, self._markup_manager)
            elif tool_key == "circle":
                existing = CircleMapTool(self._canvas, self._markup_manager)
            else:
                return
            self._markup_tool_objs[tool_key] = existing
        existing.set_color(self._dock_widget.get_markup_color())



        if self._map_tool is not None and self._canvas.mapTool() is self._map_tool:
            self._map_tool.preserve_state_on_next_deactivate()
        self._canvas.setMapTool(existing)

    def _on_markup_maptool_set(self, new_tool, old_tool=None):








        if self._in_tool_panel != "markup":
            return
        if new_tool is not None and new_tool in self._markup_tool_objs.values():
            return
        for key, tool in self._markup_tool_objs.items():
            if tool is old_tool:
                self._dock_widget.set_markup_tool_unchecked(key)
                return

    def _on_markup_color_changed(self, color: QColor):

        for tool in self._markup_tool_objs.values():
            tool.set_color(color)

    def _on_markup_clear_clicked(self):
        if self._markup_manager is not None:
            self._markup_manager.clear_all()


        self._dock_widget.clear_markup_reference()

    def _on_markup_undo(self):
        if self._in_tool_panel == "markup" and self._markup_manager is not None:
            self._markup_manager.undo_last()

    def _on_markup_outside_zone(self):


        if self._markup_outside_notice_active:
            return
        self._markup_outside_notice_active = True
        notice_ms = get_export_dial(
            "flows.tool_panels.markup_outside_notice_ms", _MARKUP_OUTSIDE_NOTICE_MS
        )
        try:
            self._iface.messageBar().pushMessage(
                "AI Edit",
                get_export_copy(
                    "flows.tool_panels.markup_outside_zone",
                    tr("You can only draw inside the selected zone."),
                ),
                level=Qgis.MessageLevel.Warning,
                duration=max(1, notice_ms // 1000),
            )
        except Exception:  # nosec B110
            pass
        QtC.safe_single_shot(
            notice_ms, self._dock_widget, self._reset_markup_outside_notice
        )

    def _reset_markup_outside_notice(self):
        self._markup_outside_notice_active = False

    def _on_markup_done_clicked(self):





        if getattr(self, "_markup_done_in_progress", False):
            return
        self._markup_done_in_progress = True
        try:
            self._exit_tool_panel()
        finally:
            self._markup_done_in_progress = False

    def _on_reference_clicked(self):





        self._disarm_swipe()
        if self._in_tool_panel == "reference":
            self._exit_tool_panel()
            return
        self._leave_other_tool_panel("reference")
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()
        self._in_tool_panel = "reference"
        self._dock_widget.set_reference_state()

    def _on_reference_done_clicked(self):

        if self._in_tool_panel == "reference":
            self._exit_tool_panel()

    def _on_vectorize_clicked(self):





        self._disarm_swipe()
        if self._in_tool_panel == "vectorize":
            self._exit_tool_panel()
            return
        if self._tool_feature_blocked("vectorize"):
            return
        self._leave_other_tool_panel("vectorize")
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()
        current = self._canvas.mapTool()
        if current is not None and current not in self._markup_tool_objs.values():
            self._pre_markup_map_tool = current
        self._in_tool_panel = "vectorize"
        self._dock_widget.set_vectorize_state()
        telemetry.track(te.VECTORIZE_PANEL_OPENED, {"source": "footer"})

    def _on_vectorize_suggestion_clicked(
        self, layer_id: str, color_hex: str, class_label: str, trigger: str = ""
    ):






        if self._tool_feature_blocked("vectorize"):
            return




        self._disarm_swipe()
        self._leave_other_tool_panel("vectorize")


        self._promote_version_for_layer(layer_id)
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()
        if self._in_tool_panel != "vectorize":
            current = self._canvas.mapTool()
            if current is not None and current not in self._markup_tool_objs.values():
                self._pre_markup_map_tool = current
            self._in_tool_panel = "vectorize"
            self._dock_widget.set_vectorize_state()
            telemetry.track(te.VECTORIZE_PANEL_OPENED, {"source": "canvas_pill"})


        self._dock_widget._vectorize_panel.preconfigure(
            layer_id=layer_id, color_hex=color_hex, class_label=class_label
        )
        telemetry.track(
            te.VECTORIZE_SUGGESTION_CLICKED,
            {
                "color": color_hex,
                "has_class_label": bool(class_label),
                "trigger": trigger or None,
            },
        )

    def _on_vectorize_done_clicked(self):
        self._exit_tool_panel()

    def _on_canvas_compare(self) -> None:












        if self._swipe_controller is None:
            return
        if self._swipe_controller.is_active():
            self._swipe_controller.stop()
            return
        if self._tool_feature_blocked("swipe"):
            return
        if self._map_tool is not None and self._canvas.mapTool() is self._map_tool:
            self._map_tool.preserve_state_on_next_deactivate()
        self._swipe_controller.start(self._forward_canvas_overlay_click)

    def _forward_canvas_overlay_click(self, canvas_pt) -> bool:






        if self._map_tool is None:
            return False
        which = self._map_tool.overlay_hit(canvas_pt)
        if which is None:
            return False
        QTimer.singleShot(0, lambda: self._dispatch_overlay_action(which))
        return True

    def _dispatch_overlay_action(self, which: str) -> None:



        if self._map_tool is None:
            return
        if which == "compare":
            self._on_canvas_compare()
        elif which == "vectorize":
            self._on_canvas_vectorize()
        elif which == "delete":




            self._on_zone_delete_requested()

    def _show_action_pills(self) -> None:










        if self._map_tool is None:
            return
        if getattr(self._map_tool, "_zone_rect", None) is None and self._selected_extent is not None:
            try:
                self._map_tool.set_zone(QgsRectangle(self._selected_extent))
            except Exception as err:  # nosec B110
                log_warning(f"zone rect restore for pills failed: {err}")
        can_compare = self._swipe_controller is not None and self._swipe_controller.can_swipe_now()
        layer = None
        if not can_compare and self._swipe_controller is not None:
            layer = self._selected_version_layer()
            if layer is not None:
                try:
                    self._iface.setActiveLayer(layer)
                    can_compare = self._swipe_controller.can_swipe_now()
                except Exception as err:  # nosec B110
                    log_warning(f"re-activate result for Compare failed: {err}")


        from ...core.auth.activation_manager import is_feature_enabled





        self._map_tool.show_action_badges(compare=False, vectorize=False)
        if self._dock_widget is not None:
            self._dock_widget.set_swipe_button_enabled(
                can_compare and is_feature_enabled("swipe")
            )

    def _on_canvas_vectorize(self) -> None:


        if self._vectorize_suggestion is None:
            return
        self._disarm_swipe()
        layer_id, color_hex, class_label, trigger = self._vectorize_suggestion
        self._on_vectorize_suggestion_clicked(
            layer_id, color_hex or "", class_label or "", trigger or ""
        )

    def _disarm_swipe(self) -> None:








        if self._swipe_controller is not None and self._swipe_controller.is_active():
            self._swipe_controller.stop()

    def _on_swipe_toggled(self, checked: bool) -> None:







        if checked:
            if self._tool_feature_blocked("swipe"):

                self._dock_widget.set_swipe_button_checked(False)
                return
            self._swipe_controller.start()
        else:
            self._swipe_controller.stop()

    def _on_swipe_armed(self) -> None:
        self._dock_widget.set_swipe_button_checked(True)
        if self._map_tool is not None:



            self._map_tool.set_compare_active(True)


        telemetry.track(te.SWIPE_ARMED, {
            "has_true_before": bool(self._swipe_controller.has_true_before()),
        })

    def _on_swipe_disarmed(self) -> None:
        self._dock_widget.set_swipe_button_checked(False)
        if self._map_tool is not None:



            self._map_tool.set_compare_active(False)



        self._dock_widget.set_swipe_button_enabled(
            self._swipe_controller.can_swipe_now()
        )
        telemetry.track(te.SWIPE_DISARMED)

    def _exit_tool_panel(self):




        self._cancel_reference_capture()

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
        if self._markup_maptool_set_connected:
            try:
                self._canvas.mapToolSet.disconnect(self._on_markup_maptool_set)
            except (TypeError, RuntimeError):
                pass
            self._markup_maptool_set_connected = False
        self._restore_qgis_undo()
        self._in_tool_panel = None
        self._dock_widget.exit_tool_panel()




        if self._pills_armed:
            if self._vectorize_suggestion is not None:
                from qgis.core import QgsProject

                layer = QgsProject.instance().mapLayer(self._vectorize_suggestion[0])
                if layer is not None:
                    try:
                        self._iface.setActiveLayer(layer)
                    except Exception as err:  # nosec B110
                        log_warning(f"re-activate result layer failed: {err}")
            self._show_action_pills()

    def _suppress_qgis_undo(self) -> None:





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

        if self._markup_manager is not None:
            self._markup_manager.remove_layer()
