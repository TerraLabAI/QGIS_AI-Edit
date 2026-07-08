
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



def _generic_label(key: str) -> str:
    return {
        "background": lambda: get_export_copy("pipeline.vectorize_palette.label_background", tr("background")),
        "paved": lambda: get_export_copy("pipeline.vectorize_palette.label_paved", tr("paved")),
        "water": lambda: get_export_copy("pipeline.vectorize_palette.label_water", tr("water")),
        "vegetation": lambda: get_export_copy("pipeline.vectorize_palette.label_vegetation", tr("vegetation")),
    }.get(key, lambda: "")()


def _palette_entries(classes) -> list[dict]:


    entries: list[dict] = []
    if not isinstance(classes, list):
        return entries
    for c in classes[:32]:
        if not isinstance(c, dict):
            continue
        rgb = c.get("rgb")
        if not (isinstance(rgb, list) and len(rgb) == 3
                and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in rgb)):
            continue
        fraction = c.get("fraction")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
            continue
        key = c.get("label_key") if isinstance(c.get("label_key"), str) else ""
        label = _generic_label(key) or (c.get("label") if isinstance(c.get("label"), str) else "")
        entries.append({
            "rgb": tuple(rgb),
            "fraction": float(fraction),
            "label": label[:60],
            "is_background": c.get("is_background") is True,
        })
    return entries


class ColorControlsMixin:


    def set_request_context(self, client, auth_manager) -> None:

        self._pixel_client = client
        self._pixel_auth_manager = auth_manager

    def _rebuild_class_list(self, raster: QgsRasterLayer) -> None:







        rid = raster.id()
        if rid == self._classes_raster_id:
            return
        self._classes_raster_id = rid
        self._pinned_class = None
        self._class_list.set_classes([])
        self._class_list.setVisible(False)
        self._photo_hint.setVisible(False)
        self._sync_run_enabled()

        client = getattr(self, "_pixel_client", None)
        auth_manager = getattr(self, "_pixel_auth_manager", None)
        auth = auth_manager.get_auth_header() if auth_manager is not None else {}
        if client is None or not auth:
            return
        path = (raster.source() or "").split("|", 1)[0]

        def _work(c=client, a=auth, p=path):
            from ....core.pixel_sample import raster_histogram

            hist = raster_histogram(p)
            if hist is None:
                return {"classes": []}
            total, bins = hist
            return c.analyze_palette(a, total, bins)

        from qgis.core import QgsApplication

        from ....workers.generic_request_task import GenericRequestTask

        task = GenericRequestTask("AI Edit colour analysis", _work, silent=True)
        task.succeeded.connect(lambda result, r=rid: self._on_classes_analyzed(r, result))
        self._classes_task = task
        QgsApplication.taskManager().addTask(task)

    def _on_classes_analyzed(self, rid: str, result) -> None:
        if rid != self._classes_raster_id:
            return
        entries = _palette_entries(result.get("classes") if isinstance(result, dict) else None)
        self._class_list.set_classes(entries)
        pinned = self._pinned_class
        if pinned is not None and not self._succeeded:
            self._class_list.ensure_class(pinned[0], pinned[1])
        self._show_class_rows(len(entries) >= 2 or pinned is not None)

    def _clear_class_list(self) -> None:


        self._classes_raster_id = None
        self._pinned_class = None
        self._class_list.set_classes([])
        self._show_class_rows(False)

    def _show_class_rows(self, has_rows: bool) -> None:

        self._class_list.setVisible(has_rows)
        self._photo_hint.setVisible(not has_rows)
        self._sync_run_enabled()

    def _on_eyedropper_clicked(self, checked: bool = True) -> None:


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
        except ImportError:  # pragma: no cover
            _iface = None
        if _iface is None:
            self._set_eyedropper_checked(False)
            return

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


        self._eyedropper_tool = tool



        tool.deactivated.connect(lambda t=tool: self._on_eyedropper_released(t))
        self._set_eyedropper_checked(True)
        canvas.setMapTool(tool)



        try:
            canvas.setFocus()
        except RuntimeError:  # nosec B110
            pass
        self._show_status(_eyedropper_armed_text(), is_error=False, is_hint=True)

    def _set_eyedropper_checked(self, checked: bool) -> None:
        btn = self._eyedropper_btn
        if btn.isChecked() != checked:
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.blockSignals(False)

    def _on_eyedropper_released(self, tool) -> None:


        if self._eyedropper_tool is tool:
            self._eyedropper_tool = None
        if self._eyedropper_tool is None:
            self._set_eyedropper_checked(False)
            if self._status_label.text() == _eyedropper_armed_text():
                self._show_status("", is_error=False)
        try:
            tool.deleteLater()
        except (RuntimeError, AttributeError):  # nosec B110
            pass

    def _on_eyedropper_color(self, color: QColor) -> None:
        rgb = (color.red(), color.green(), color.blue())


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






        tool = self._eyedropper_tool
        self._eyedropper_tool = None
        try:
            self._set_eyedropper_checked(False)
            if self._status_label.text() == _eyedropper_armed_text():
                self._show_status("", is_error=False)
        except RuntimeError:  # nosec B110
            pass
        if tool is None:
            return
        try:
            from qgis.utils import iface as _iface
        except ImportError:  # pragma: no cover
            return
        try:
            canvas = _iface.mapCanvas() if _iface is not None else None
            if canvas is not None and canvas.mapTool() is tool:
                previous = getattr(tool, "_previous_tool", None)
                if previous is not None and previous is not tool:
                    canvas.setMapTool(previous)
                else:
                    canvas.unsetMapTool(tool)
        except (RuntimeError, AttributeError):  # nosec B110
            pass


        try:
            tool.deleteLater()
        except (RuntimeError, AttributeError):  # nosec B110
            pass
