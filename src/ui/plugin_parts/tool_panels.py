from __future__ import annotations

from qgis.core import Qgis, QgsRectangle
from qgis.PyQt.QtCore import QEvent, QObject, QTimer
from qgis.PyQt.QtGui import QAction, QColor, QKeySequence

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.i18n import tr
from ...core.logger import log_warning
from ..tools.markup_tools import (
    ArrowMapTool,
    CircleMapTool,
    MarkupLayerManager,
    PencilMapTool,
)


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


class ToolPanelsMixin:
    # --- Mark up / Vectorize tool panels -------------------------------

    def _on_markup_clicked(self):
        """User picked Tools → Mark up. Swap the dock view and arm the canvas.

        Toggles: a second click on the footer Mark up icon while the panel is
        already open closes it (same as the in-panel Finish button).
        """
        self._disarm_swipe()
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()

        if self._in_tool_panel == "markup":
            # Closing via the footer toggle must match the in-panel Finish
            # button: capture the marks as a reference and drop the layer.
            self._on_markup_done_clicked()
            return
        if self._markup_manager is None:
            self._markup_manager = MarkupLayerManager(self._canvas, self._dock_widget)
            self._markup_manager.annotation_count_changed.connect(
                self._dock_widget.set_markup_annotation_count
            )
            self._markup_manager.outside_zone_attempted.connect(
                self._on_markup_outside_zone
            )
        # Capture the current map tool so Done can restore it.
        current = self._canvas.mapTool()
        if current is not None and current not in self._markup_tool_objs.values():
            self._pre_markup_map_tool = current
        self._in_tool_panel = "markup"
        self._dock_widget.set_markup_state()
        self._dock_widget.set_markup_zone_present(self._selected_extent is not None)
        self._markup_manager.set_clip_zone(self._selected_extent)
        self._dock_widget.set_markup_annotation_count(
            self._markup_manager.annotation_count()
        )
        # Cmd/Ctrl+Z undo across all focus paths: suppress QGIS undo action,
        # map tool key handler (canvas focus), main-window event filter (dock focus).
        if self._markup_event_filter is None:
            self._markup_event_filter = _MarkupUndoFilter(self._on_markup_undo)
        self._iface.mainWindow().installEventFilter(self._markup_event_filter)
        self._suppress_qgis_undo()
        telemetry.track(te.MARKUP_OPENED)

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
        # "Clear all" wipes the markup entirely: current strokes and the saved
        # reference thumbnail, so nothing markup-related is left behind.
        self._dock_widget.clear_markup_reference()

    def _on_markup_undo(self):
        if self._in_tool_panel == "markup" and self._markup_manager is not None:
            self._markup_manager.undo_last()

    def _on_markup_outside_zone(self):
        """Yellow notice when the user draws outside the selected zone. Marks
        only count inside the zone, so an out-of-zone stroke is dropped."""
        if self._markup_outside_notice_active:
            return
        self._markup_outside_notice_active = True
        try:
            self._iface.messageBar().pushMessage(
                "AI Edit",
                tr("You can only draw inside the selected zone."),
                level=Qgis.MessageLevel.Warning,
                duration=3,
            )
        except Exception:  # nosec B110 - a missing message bar never blocks drawing.
            pass
        QtC.safe_single_shot(
            3000, self._dock_widget, self._reset_markup_outside_notice
        )

    def _reset_markup_outside_notice(self):
        self._markup_outside_notice_active = False

    def _on_markup_done_clicked(self):
        """Leave the Mark up panel. The marks stay on the map so they render
        directly onto the image sent to the model; the same zone WITHOUT the
        marks is sent alongside so the model restores the pixels under each
        mark and no stroke appears in the result. The markup layer is dropped
        once the image has been captured at generation time."""
        if getattr(self, "_markup_done_in_progress", False):
            return
        self._markup_done_in_progress = True
        try:
            self._exit_tool_panel()
        finally:
            self._markup_done_in_progress = False

    def _on_vectorize_clicked(self):
        """User picked Tools → Vectorize.

        Toggles: a second click on the footer Vectorize icon while the panel
        is open closes it (same as the in-panel Done button).
        """
        self._disarm_swipe()
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()
        if self._in_tool_panel == "vectorize":
            self._exit_tool_panel()
            return
        current = self._canvas.mapTool()
        if current is not None and current not in self._markup_tool_objs.values():
            self._pre_markup_map_tool = current
        self._in_tool_panel = "vectorize"
        self._dock_widget.set_vectorize_state()
        telemetry.track(te.VECTORIZE_PANEL_OPENED, {"source": "footer"})

    def _on_vectorize_suggestion_clicked(
        self, layer_id: str, color_hex: str, class_label: str
    ):
        """User clicked the post-generation \"Vectorize this result\" CTA.
        Open the panel with the source raster + color + class label pre-filled.

        Bypasses the toggle in `_on_vectorize_clicked` so a second click on
        the CTA never closes an already-open panel.
        """
        if self._map_tool is not None:
            self._map_tool.hide_action_badges()
        if self._in_tool_panel != "vectorize":
            current = self._canvas.mapTool()
            if current is not None and current not in self._markup_tool_objs.values():
                self._pre_markup_map_tool = current
            self._in_tool_panel = "vectorize"
            self._dock_widget.set_vectorize_state()
            telemetry.track(te.VECTORIZE_PANEL_OPENED, {"source": "canvas_pill"})
        # activate() runs first via set_vectorize_state; preconfigure overrides
        # the just-reset state with the template's values.
        self._dock_widget._vectorize_panel.preconfigure(
            layer_id=layer_id, color_hex=color_hex, class_label=class_label
        )
        telemetry.track(
            te.VECTORIZE_SUGGESTION_CLICKED,
            {"color": color_hex, "has_class_label": bool(class_label)},
        )

    def _on_vectorize_done_clicked(self):
        self._exit_tool_panel()

    def _on_canvas_compare(self) -> None:
        """Compare pill (canvas) clicked: toggle the before/after swipe.

        The pill stays on the canvas during the comparison (see the overlay
        click-forwarding below), so this is a real toggle: arm if off, disarm
        if already comparing. Arming preserves the zone + pills so they survive
        the swipe taking the canvas, and passes the click-forwarding callback
        so the pills stay live underneath it.
        """
        if self._swipe_controller is None:
            return
        if self._swipe_controller.is_active():
            self._swipe_controller.stop()
            return
        if self._map_tool is not None and self._canvas.mapTool() is self._map_tool:
            self._map_tool.preserve_state_on_next_deactivate()
        self._swipe_controller.start(self._forward_canvas_overlay_click)

    def _forward_canvas_overlay_click(self, canvas_pt) -> bool:
        """Let the action pills claim a click while the swipe owns the canvas.

        Pure hit-test here; the actual action is deferred a tick because it may
        swap the map tool, which must not happen inside the swipe tool's own
        press event.
        """
        if self._map_tool is None:
            return False
        which = self._map_tool.overlay_hit(canvas_pt)
        if which is None:
            return False
        QTimer.singleShot(0, lambda: self._dispatch_overlay_action(which))
        return True

    def _dispatch_overlay_action(self, which: str) -> None:
        # Deferred a tick from the canvas click; if the plugin was unloaded in
        # between (unload() clears _map_tool), the captured callback would touch
        # torn-down state. Bail before doing anything.
        if self._map_tool is None:
            return
        if which == "compare":
            self._on_canvas_compare()
        elif which == "vectorize":
            self._on_canvas_vectorize()
        elif which == "delete":
            self._on_zone_delete_requested()

    def _show_action_pills(self) -> None:
        """Show the canvas action pills with the right options for the current
        result: Compare when a before/after is possible, Vectorize when the run
        was a detection / segmentation template.

        Self-healing: tool detours (Mark up, Vectorize, eyedropper) often leave
        a vector layer active, which made Compare silently vanish while
        Vectorize stayed. If Compare is ineligible but the lineage has a result
        raster on the map, re-activate it and re-check. Same for the zone rect:
        programmatic flows may not have armed the map tool, so restore it from
        the selected extent before showing badges (they anchor to it)."""
        if self._map_tool is None:
            return
        if getattr(self._map_tool, "_zone_rect", None) is None and self._selected_extent is not None:
            try:
                self._map_tool.set_zone(QgsRectangle(self._selected_extent))
            except Exception as err:  # nosec B110
                log_warning(f"zone rect restore for pills failed: {err}")
        can_compare = self._swipe_controller is not None and self._swipe_controller.can_swipe_now()
        if not can_compare and self._swipe_controller is not None:
            layer = self._selected_version_layer()
            if layer is not None:
                try:
                    self._iface.setActiveLayer(layer)
                    can_compare = self._swipe_controller.can_swipe_now()
                except Exception as err:  # nosec B110
                    log_warning(f"re-activate result for Compare failed: {err}")
        color = self._vectorize_suggestion[1] if self._vectorize_suggestion else None
        self._map_tool.show_action_badges(compare=can_compare, vectorize=bool(color))

    def _on_canvas_vectorize(self) -> None:
        """Vectorize pill (canvas) clicked: open the Vectorize panel pre-filled
        with the just-generated result, mirroring the dock CTA."""
        if self._vectorize_suggestion is None:
            return
        self._disarm_swipe()
        layer_id, color_hex, class_label = self._vectorize_suggestion
        self._on_vectorize_suggestion_clicked(
            layer_id, color_hex or "", class_label or ""
        )

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
        if self._map_tool is not None:
            self._map_tool.set_compare_active(True)
        telemetry.track(te.SWIPE_ARMED)

    def _on_swipe_disarmed(self) -> None:
        self._dock_widget.set_swipe_button_checked(False)
        if self._map_tool is not None:
            self._map_tool.set_compare_active(False)
        # After disarm, the active layer might have become non-eligible
        # while the swipe was on; re-evaluate the enable state so the
        # button greys out cleanly.
        self._dock_widget.set_swipe_button_enabled(
            self._swipe_controller.can_swipe_now()
        )
        telemetry.track(te.SWIPE_DISARMED)

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
        # Tool panels only hid the canvas pills; if they still belong to the
        # current result (Done from Vectorize / Mark up), bring them back.
        # Vectorize leaves a vector layer active, which would make Compare
        # ineligible, so re-activate the result raster first.
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
