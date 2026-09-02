










from __future__ import annotations

from qgis.core import QgsRectangle

from ...core.canvas_export.render_set import expand_render_set
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.logger import log_warning
from ..layer_renderer import render_layers_at_extent
from ..tools.reference_capture_tool import ReferenceCaptureTool


class ReferenceCaptureMixin:
    def _ensure_reference_capture_tool(self) -> ReferenceCaptureTool:
        tool = getattr(self, "_reference_capture_tool", None)
        if tool is None:
            tool = ReferenceCaptureTool(self._canvas)
            tool.captured.connect(self._on_reference_captured)
            tool.cancelled.connect(self._on_reference_capture_cancelled)
            self._reference_capture_tool = tool
            self._reference_capture_previous_tool = None
        return tool

    def _reference_capture_armed(self) -> bool:
        tool = getattr(self, "_reference_capture_tool", None)
        try:
            return tool is not None and self._canvas.mapTool() is tool
        except RuntimeError:
            return False

    def _on_reference_capture_requested(self) -> None:








        panel = getattr(self._dock_widget, "_reference_panel", None)
        if panel is None or not panel.isVisible():
            return
        if self._worker is not None and self._worker.is_active():
            return
        if self._reference_capture_armed():
            self._cancel_reference_capture()
            return
        tool = self._ensure_reference_capture_tool()
        self._reference_capture_previous_tool = self._canvas.mapTool()





        if self._map_tool is not None:
            try:
                self._map_tool.preserve_state_on_next_deactivate()
                self._map_tool.hide_action_badges()
            except (RuntimeError, AttributeError):  # nosec B110
                pass
        self._canvas.setMapTool(tool)
        self._dock_widget.set_reference_capture_armed(True)





        try:
            self._canvas.setFocus()
        except RuntimeError:  # nosec B110
            pass

    def _restore_tool_after_capture(self) -> None:
        tool = getattr(self, "_reference_capture_tool", None)
        previous = getattr(self, "_reference_capture_previous_tool", None)
        self._reference_capture_previous_tool = None
        try:
            if tool is not None and self._canvas.mapTool() is tool:
                if previous is not None:
                    self._canvas.setMapTool(previous)
                else:
                    self._canvas.unsetMapTool(tool)
        except RuntimeError:  # nosec B110
            pass
        if self._dock_widget is not None:
            self._dock_widget.set_reference_capture_armed(False)

    def _cancel_reference_capture(self) -> None:

        if not self._reference_capture_armed():
            return
        try:
            self._reference_capture_tool.clearRubberBand()
        except RuntimeError:  # nosec B110
            pass
        self._restore_tool_after_capture()

    def _on_reference_capture_cancelled(self) -> None:
        self._restore_tool_after_capture()

    def _on_reference_captured(self, rect: QgsRectangle) -> None:








        self._restore_tool_after_capture()
        widget = getattr(self._dock_widget, "_reference_widget", None)
        if widget is None:
            return
        try:
            settings = self._canvas.mapSettings()
            layers = expand_render_set(settings.layers())



            image = render_layers_at_extent(
                layers, QgsRectangle(rect), settings.destinationCrs()
            )
        except Exception as err:  # noqa: BLE001
            log_warning(f"map capture render failed: {err}")
            image = None
        widget.add_captured_image(image, self._next_map_capture_name())

    def _next_map_capture_name(self) -> str:


        base = get_export_copy("flows.reference_capture.map_capture_label", tr("Map capture"))
        store = getattr(self._dock_widget, "_reference_store", None)
        try:
            taken = {
                r.source_filename for r in store.list() if r.source_kind == "map"
            } if store is not None else set()
        except (AttributeError, RuntimeError):
            taken = set()
        if base not in taken:
            return base
        number = 2
        while f"{base} {number}" in taken:
            number += 1
        return f"{base} {number}"

    def _teardown_reference_capture(self) -> None:
        self._cancel_reference_capture()
        tool = getattr(self, "_reference_capture_tool", None)
        self._reference_capture_tool = None
        if tool is not None:
            try:
                tool.deleteLater()
            except RuntimeError:  # nosec B110
                pass
