"""Class detection and eyedropper handling for the Vectorize panel."""
from __future__ import annotations

from qgis.core import QgsRasterLayer
from qgis.PyQt.QtGui import QColor

from ....core.config_store import get_export_copy
from ....core.i18n import tr
from ...tools.eyedropper_tool import EyedropperMapTool


def _eyedropper_armed_text() -> str:
    return get_export_copy(
        "widgets.color_controls.eyedropper_hint_esc",
        tr("Click a color on the map. Esc cancels."),
    )


class ColorControlsMixin:
    """Populates the detected-class list and runs the canvas eyedropper."""

    def _rebuild_class_list(self, raster: QgsRasterLayer) -> None:
        """Detect the raster's flat colors and fill the class list.

        Recomputes only when the raster changes (cheap, but no need to redo it
        on every project signal). When detection finds nothing usable the map is
        probably photo-realistic; say so instead of showing a dead list."""
        rid = raster.id()
        if rid == self._classes_raster_id:
            return
        self._classes_raster_id = rid

        path = (raster.source() or "").split("|", 1)[0]
        try:
            from ....core.generation.vectorize_palette import detect_classes
            entries = detect_classes(path)
        except Exception:  # noqa: BLE001 - detection is a convenience, never block
            entries = []

        self._class_list.set_classes(entries)
        self._show_class_rows(len(entries) >= 2)

    def _clear_class_list(self) -> None:
        """No usable map picked: forget the previous map's classes, so a
        list detected on another raster never sits under this one."""
        self._classes_raster_id = None
        self._class_list.set_classes([])
        self._show_class_rows(False)

    def _show_class_rows(self, has_rows: bool) -> None:
        """The rows, or the photo notice when there is nothing to list."""
        self._class_list.setVisible(has_rows)
        self._photo_hint.setVisible(not has_rows)
        self._sync_run_enabled()

    def _on_eyedropper_clicked(self, checked: bool = True) -> None:
        """Arm the canvas eyedropper on the picked raster, or disarm it when
        the button is pressed again while it waits for a click."""
        if not checked:
            self.cancel_eyedropper()
            return
        raster = self._layer_combo.currentLayer()
        if not isinstance(raster, QgsRasterLayer):
            self._set_eyedropper_checked(False)
            self._show_status(
                get_export_copy(
                    "widgets.color_controls.pick_map_first",
                    tr("Pick a map under Layer first."),
                ),
                is_error=True,
            )
            return
        try:
            from qgis.utils import iface as _iface
        except ImportError:  # pragma: no cover - non-QGIS env
            _iface = None
        if _iface is None:
            self._set_eyedropper_checked(False)
            return
        # A second arm replaces the first cleanly.
        self.cancel_eyedropper()
        canvas = _iface.mapCanvas()
        previous_tool = canvas.mapTool()
        tool = EyedropperMapTool(
            canvas=canvas,
            raster=raster,
            on_color=self._on_eyedropper_color,
            on_off_raster=self._on_eyedropper_miss,
            previous_tool=previous_tool,
        )
        # Keep a reference on self so the tool isn't GC'd between click
        # and release.
        self._eyedropper_tool = tool
        # Every way the tool leaves the canvas (a pick, a miss, Esc on the
        # canvas, the user picking another QGIS tool) ends here, so the
        # button never stays lit on a tool that is no longer armed.
        tool.deactivated.connect(lambda t=tool: self._on_eyedropper_released(t))
        self._set_eyedropper_checked(True)
        canvas.setMapTool(tool)
        # The next move is a click on the map, and Esc must reach the tool:
        # with the focus left on this button, the dock's Escape closed the
        # whole panel instead of cancelling the pick.
        try:
            canvas.setFocus()
        except RuntimeError:  # nosec B110 - canvas gone, the pick still works by mouse
            pass
        self._show_status(_eyedropper_armed_text(), is_error=False, is_hint=True)

    def _set_eyedropper_checked(self, checked: bool) -> None:
        btn = self._eyedropper_btn
        if btn.isChecked() != checked:
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.blockSignals(False)

    def _on_eyedropper_released(self, tool) -> None:
        """The eyedropper left the canvas: unlight the button, drop the
        armed hint if nothing replaced it, and free the tool."""
        if self._eyedropper_tool is tool:
            self._eyedropper_tool = None
        if self._eyedropper_tool is None:
            self._set_eyedropper_checked(False)
            if self._status_label.text() == _eyedropper_armed_text():
                self._show_status("", is_error=False)
        try:
            tool.deleteLater()
        except (RuntimeError, AttributeError):  # nosec B110 - already gone
            pass

    def _on_eyedropper_color(self, color: QColor) -> None:
        rgb = (color.red(), color.green(), color.blue())
        # A sampled color joins the class list (or checks its near-twin); it
        # takes effect on the next Vectorize click.
        name = self._class_list.add_class(rgb)
        self._show_class_rows(True)
        hex_text = color.name().upper()
        if name:
            text = tr("{hex} is already listed as “{name}”. It is checked.").format(
                hex=hex_text, name=name
            )
        else:
            text = tr("Added {hex} to the classes.").format(hex=hex_text)
        self._show_status(text, is_error=False)

    def _on_eyedropper_miss(self) -> None:
        self._show_status(
            get_export_copy(
                "widgets.color_controls.eyedropper_miss_map",
                tr("That click missed the map. Try again on the map itself."),
            ),
            is_error=True,
        )

    def cancel_eyedropper(self) -> None:
        """Disarm the eyedropper and hand the canvas back. Teardown entry point.

        Called from deactivate() (every way out of the panel), the dock's
        cleanup() on unload, a new pick on the layer combo and a run start,
        so QGIS's canvas never keeps a map tool owned by a panel that is gone.
        """
        tool = self._eyedropper_tool
        self._eyedropper_tool = None
        try:
            self._set_eyedropper_checked(False)
            if self._status_label.text() == _eyedropper_armed_text():
                self._show_status("", is_error=False)
        except RuntimeError:  # nosec B110 - panel already torn down
            pass
        if tool is None:
            return
        try:
            from qgis.utils import iface as _iface
        except ImportError:  # pragma: no cover - non-QGIS env
            return
        try:
            canvas = _iface.mapCanvas() if _iface is not None else None
            if canvas is not None and canvas.mapTool() is tool:
                previous = getattr(tool, "_previous_tool", None)
                if previous is not None and previous is not tool:
                    canvas.setMapTool(previous)
                else:
                    canvas.unsetMapTool(tool)
        except (RuntimeError, AttributeError):  # nosec B110 - canvas already gone
            pass
        # QgsMapTool parents itself to the canvas, so dropping the Python
        # reference alone leaves the C++ tool alive on the map canvas.
        try:
            tool.deleteLater()
        except (RuntimeError, AttributeError):  # nosec B110
            pass
