




from __future__ import annotations

from qgis.core import QgsProject, QgsRasterLayer

from .mcp_api_support import _never_raises


class InteractiveMixin:


    @_never_raises
    def get_input_layer(self) -> dict:






        dock = self._dock()
        layer = dock.selected_input_layer() if dock is not None else None
        has_layer = isinstance(layer, QgsRasterLayer) and layer.isValid()
        return {
            "has_layer": has_layer,
            "layer_id": layer.id() if has_layer else None,
            "layer_name": layer.name() if has_layer else None,
            "crs": (layer.crs().authid() or layer.crs().toWkt()) if has_layer else None,
            "locked": self._busy() or bool(getattr(self._plugin, "_selected_extent", None)),
        }

    @_never_raises
    def set_input_layer(self, layer_id: str) -> dict:







        if self._busy():
            return {"_error": "Wait for the current generation before changing its image.", "busy": True}
        layer = QgsProject.instance().mapLayer(str(layer_id or ""))
        if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
            return {"_error": "layer_id must identify a valid raster in this project."}
        current = self.get_input_layer()
        if current.get("layer_id") == layer.id():
            return {**current, "ok": True, "changed": False}
        if current.get("locked"):
            return {
                **current,
                "_error": "This zone belongs to another image. Finish or clear the zone before choosing a new image.",
            }
        dock = self._open_dock()
        combo = getattr(dock, "_layer_combo", None)
        if combo is None:
            return {"_error": "The AI Edit image picker is not available."}


        combo.set_include_hidden(True)
        combo.setLayer(layer)
        selected = self.get_input_layer()
        if selected.get("layer_id") != layer.id():
            return {**selected, "_error": "The image picker could not select this raster."}
        return {**selected, "ok": True, "changed": True}

    def _interactive_prompt_widget(self):
        dock = self._dock()
        if dock is None:
            return None
        result = getattr(dock, "_result_prompt_widget", None)


        main = getattr(dock, "_main_widget", dock)
        if result is not None and result.isVisibleTo(main):
            return dock._result_prompt_input
        return dock._prompt_input

    def _interactive_drawing(self):

        plugin = self._plugin
        canvas = getattr(plugin, "_canvas", None)
        current = canvas.mapTool() if canvas is not None else None
        if current is None:
            return None
        if current is getattr(plugin, "_map_tool", None):
            return "zone" if current.has_points() else None
        if current in (getattr(plugin, "_markup_tool_objs", {}) or {}).values():
            has_points = getattr(current, "has_points", None)
            pending = has_points() if callable(has_points) else getattr(current, "_active", False)
            return "markup" if pending else None
        return None

    @_never_raises
    def get_interactive_state(self) -> dict:









        plugin = self._plugin
        canvas = getattr(plugin, "_canvas", None)
        tool = getattr(plugin, "_map_tool", None)
        prompt_widget = self._interactive_prompt_widget()
        return {
            "input_layer": self.get_input_layer(),
            "zone": self._zone_summary(),
            "prompt": prompt_widget.toPlainText().strip() if prompt_widget is not None else "",
            "markup": self.markup_status(),
            "drawing_active": bool(canvas is not None and tool is not None and canvas.mapTool() is tool),
            "drawing": self._interactive_drawing(),
            "busy": self._busy(),
            "dock_open": self._dock_open(),
        }

    @_never_raises
    def prepare_interactive(
        self,
        layer_name: str | None = None,
        prompt: str | None = None,
        zone_wkt: str | None = None,
        interaction: str = "review",
    ) -> dict:














        if self._busy():
            return {"_error": "Wait for the current generation before preparing another edit.", "busy": True,
                    "submitted": False}
        if interaction not in ("review", "draw_zone", "markup"):
            return {"_error": "interaction must be review, draw_zone or markup.", "submitted": False}
        if interaction == "markup" and not zone_wkt and not self._zone_summary().get("has_zone"):
            return {"_error": "Choose or draw a zone before adding marks.", "submitted": False}
        if zone_wkt is not None and not str(zone_wkt).strip():
            return {"_error": "zone_wkt must contain a polygon; omit it to preserve the current zone.",
                    "submitted": False}
        if layer_name is not None:
            project = QgsProject.instance()
            layer = project.mapLayer(str(layer_name))
            if layer is None:
                matches = [item for item in project.mapLayersByName(str(layer_name))
                           if isinstance(item, QgsRasterLayer) and item.isValid()]
                if len(matches) != 1:
                    return {"_error": "Choose one raster by its exact layer id; this name is missing or ambiguous.",
                            "submitted": False}
                layer = matches[0]
            selected = self.set_input_layer(layer.id())
            if "_error" in selected:
                return {**selected, "submitted": False}
        dock = self._open_dock()
        if dock is None:
            return {"_error": "The AI Edit panel is not available.", "submitted": False}
        if zone_wkt is not None:
            zone = self.set_zone(polygon_wkt=zone_wkt)
            if "_error" in zone:
                return {**zone, "submitted": False}
        if interaction == "draw_zone":
            plugin = self._plugin
            if getattr(plugin, "_in_tool_panel", None) == "markup":
                plugin._on_markup_done_clicked()
            plugin._activate_selection_tool()
            if self._zone_summary().get("has_zone"):
                plugin._map_tool.set_zone(plugin._selected_extent, plugin._selected_polygon)
            else:
                dock.set_selecting_zone_state()
        elif interaction == "markup":
            failure = self._enter_markup()
            if failure is not None:
                return {**failure, "submitted": False}
            if not self.markup_status().get("active"):
                return {"_error": "Mark up is unavailable in the current panel state.", "submitted": False}
        if prompt is not None:
            widget = self._interactive_prompt_widget()
            if widget is not None:
                widget.setPlainText(str(prompt))
        return {
            **self.get_interactive_state(),
            "prepared": True,
            "submitted": False,
            "interaction": interaction,
            "hint": "Let the user finish in the panel, then read get_interactive_state() before generating.",
        }
