"""Capture a rectangle of the map as a reference image ("Map" in the
Reference panel).

The panel is pure dock UI and owns no map tool, so the canvas side lives
here, next to the zone tool: arm the native extent tool, remember the tool
it replaced, render the visible canvas at the dragged rectangle, hand the
image to the shared reference strip (which applies the free-tier and cap
gates), and restore the previous tool on capture, Esc, a second click on the
chip, or Done. A run in progress, or any other tool panel, never arms it.
"""
from __future__ import annotations

from qgis.core import QgsRectangle

from ...core.canvas_export.render_set import expand_render_set
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
        """The "Map" chip: arm the extent tool, or disarm it on a second click.

        Gated on the panel being on screen, not on how it got there: the
        panel opens from the footer chip and from the prompt box alike, and
        only the footer path marks ``_in_tool_panel``. A silent return here
        left the zone tool active under the user's drag, which then read as a
        zone being redrawn and stored nothing.
        """
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
        # Swapping the canvas tool deactivates the zone tool, and its default
        # on a deactivate is to DROP the zone (the right thing when the user
        # picks pan or another plugin's tool). Ask it to keep the zone alive
        # across this one switch, the way Mark up and Compare do, or the zone
        # vanishes under the capture and the flow lands back on "draw".
        if self._map_tool is not None:
            try:
                self._map_tool.preserve_state_on_next_deactivate()
                self._map_tool.hide_action_badges()
            except (RuntimeError, AttributeError):  # nosec B110
                pass
        self._canvas.setMapTool(tool)
        self._dock_widget.set_reference_capture_armed(True)
        self._iface.messageBar().pushInfo(
            "AI Edit",
            tr("Drag a rectangle on the map to capture it as a reference. Esc cancels."),
        )

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
        except RuntimeError:  # nosec B110 - canvas or tool already gone
            pass
        if self._dock_widget is not None:
            self._dock_widget.set_reference_capture_armed(False)

    def _cancel_reference_capture(self) -> None:
        """Disarm without capturing: Esc, the chip again, Done, teardown."""
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
        """Render what the user sees at the dragged rectangle and store it.

        The visible canvas render set (group proxies expanded) at the canvas
        CRS: a "screenshot of the map", not the chosen input layer, because an
        exemplar is often another layer, or several. Online tiles settle
        inside the renderer, the rectangle sits inside the view so they are
        already warm on screen.
        """
        self._restore_tool_after_capture()
        widget = getattr(self._dock_widget, "_reference_widget", None)
        if widget is None:
            return
        try:
            settings = self._canvas.mapSettings()
            layers = expand_render_set(settings.layers())
            # Exactly the rectangle: the layer-reference renderer would fall
            # back to the local layers' own extent when they miss it, and on
            # a tile basemap those are the plugin's past outputs.
            image = render_layers_at_extent(
                layers, QgsRectangle(rect), settings.destinationCrs()
            )
        except Exception as err:  # noqa: BLE001 - a capture must never break the flow
            log_warning(f"map capture render failed: {err}")
            image = None
        widget.add_captured_image(image, tr("Map capture"))

    def _teardown_reference_capture(self) -> None:
        self._cancel_reference_capture()
        tool = getattr(self, "_reference_capture_tool", None)
        self._reference_capture_tool = None
        if tool is not None:
            try:
                tool.deleteLater()
            except RuntimeError:  # nosec B110
                pass
