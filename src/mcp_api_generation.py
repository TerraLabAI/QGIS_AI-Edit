






from __future__ import annotations

from typing import Any

from .mcp_api_support import _never_raises, _whole_number, not_found_error


class GenerationMixin:




    def _apply_resolution(self, label: str | None) -> dict:

        from .core.entitlements import is_tier_allowed
        from .core.resolution_labels import resolution_tiers

        tiers = list(resolution_tiers())
        label = (label or "").strip()
        if not label:
            return {"resolution": self._selected_resolution(), "applied": False}
        if label not in tiers:
            return not_found_error(
                "resolution", label, tiers,
                means="resolution picks how much detail comes back, from get_resolutions().",
            )
        if not is_tier_allowed(label, bool(self._is_free_tier())):
            return {
                "_error": f"Resolution '{label}' needs a paid plan.",
                "plan_restricted": True,
            }
        dock = self._dock()
        setter = getattr(dock, "_on_resolution_selected", None) if dock is not None else None
        if not callable(setter):
            return {"resolution": label, "applied": False}
        setter(label)
        return {"resolution": self._selected_resolution() or label, "applied": True}

    def _arm_template(self, template_id: str) -> dict:

        dock = self._dock()
        if dock is None or not hasattr(dock, "get_active_template"):
            return {"template_id": template_id, "armed": False}
        name = ""
        try:
            for category in (self.get_presets().get("categories") or []):
                for preset in (category.get("presets") or []):
                    if str(preset.get("id")) == template_id:
                        name = str(preset.get("label") or preset.get("name") or "")
                        break
                if name:
                    break
        except Exception:  # nosec B110
            pass
        dock._active_template_id = template_id
        dock._active_template_name = name or None
        return {
            "template_id": template_id,
            "template_name": name,
            "armed": bool(dock.get_active_template()),
        }

    def _version_strip(self):
        dock = self._dock()
        return getattr(dock, "_version_strip", None) if dock is not None else None

    def _versions(self) -> list[dict]:

        strip = self._version_strip()
        if strip is None:
            return []
        try:
            count = int(strip.count())
            selected = int(strip.selected_index())
        except Exception:
            return []
        tiles = getattr(strip, "_tiles", []) or []
        records = getattr(self._plugin, "_versions", []) or []
        out = []
        for index in range(count):
            try:
                label = strip.label_for(index)
            except Exception:
                label = "Original" if index == 0 else f"V{index}"
            prompt = getattr(tiles[index], "_prompt", "") if index < len(tiles) else ""
            entry: dict[str, Any] = {
                "index": index,
                "label": label,
                "prompt": prompt or "",
                "selected": index == selected,
            }


            record = records[index - 1] if 0 < index <= len(records) else None
            if isinstance(record, dict):
                entry["layer_id"] = record.get("layer_id")
                entry["request_id"] = record.get("request_id")
            out.append(entry)
        return out

    def _result_layers(self) -> list[tuple[str, str]]:

        from qgis.core import QgsProject

        from .ui.layer_groups import collect_ai_edit_layer_ids

        project = QgsProject.instance()
        try:
            ids = collect_ai_edit_layer_ids()
        except Exception:
            return []
        if not ids:
            return []
        found = []
        for layer_id in project.layerTreeRoot().findLayerIds():
            if layer_id not in ids:
                continue
            layer = project.mapLayer(layer_id)
            if layer is not None:
                found.append((layer_id, layer.name()))
        return found

    def _result_layer_names(self) -> list[str]:

        return [name for _id, name in self._result_layers()]

    def _result_layer_ids(self) -> list[str]:

        return [layer_id for layer_id, _name in self._result_layers()]



    @_never_raises
    def set_resolution(self, resolution: str) -> dict:









        self._open_dock()
        return self._apply_resolution(resolution)

    @_never_raises
    def generate(
        self,
        prompt: str,
        bbox=None,
        resolution: str | None = None,
        template_id: str | None = None,
        reference_layers=None,
        polygon_wkt: str | None = None,
    ) -> dict:
































        plugin = self._plugin
        prompt = (prompt or "").strip()
        if not prompt:
            return {"submitted": False, "_error": "prompt is required"}
        if bbox is not None and polygon_wkt:
            return {
                "submitted": False,
                "_error": "Pass bbox or polygon_wkt, not both.",
            }
        if self._busy():
            return {
                "submitted": False,
                "busy": True,
                "_error": "A generation is already running",
            }
        dock = self._open_dock()
        if dock is None:
            return {"submitted": False, "_error": "The AI Edit panel is not available."}
        if not self._server_config_ok():
            return {
                "submitted": False,
                "server_config_ok": False,
                "_error": (
                    "AI Edit settings have not arrived from the server yet. "
                    "Check the connection and call generate again in a moment."
                ),
            }



        current_zone = getattr(plugin, "_selected_extent", None)
        keep_zone = bbox is None and not polygon_wkt and bool(current_zone)
        shape = None
        if polygon_wkt:
            resolved = self._resolve_polygon(polygon_wkt)
            if isinstance(resolved, dict):
                resolved["submitted"] = False
                return resolved
            extent, shape = resolved
        elif bbox is None:
            extent = current_zone if keep_zone else self._canvas_extent()
        else:
            extent = self._resolve_extent(bbox)
            if isinstance(extent, dict):
                extent["submitted"] = False
                return extent

        refused = self._validate_extent(extent)
        if refused is not None:
            refused["submitted"] = False
            return refused

        applied = self._apply_resolution(resolution)
        if "_error" in applied:
            applied["submitted"] = False
            return applied



        if not keep_zone:
            self._install_zone(extent, shape)


        template = self._arm_template(str(template_id)) if template_id else None




        references = None
        if reference_layers:
            names = [reference_layers] if isinstance(reference_layers, str) else list(reference_layers)
            references = self._attach_references(names, extent)
            if "_error" in references:
                references["submitted"] = False
                return references

        run = getattr(plugin, "_on_generate", None)
        if not callable(run):
            return {"submitted": False, "_error": "The generation entry point is not available."}



        plugin._api_run_requested = True
        try:
            run(prompt)
        finally:
            plugin._api_run_requested = False




        started = self._busy()
        if not started:
            return {
                "submitted": False,
                "_error": "AI Edit did not take the run.",
                "dock_status": self._dock_status(),
            }

        result: dict[str, Any] = {
            "submitted": True,
            "request_id": None,
            "running": True,
            "resolution": applied.get("resolution"),
            "zone_reused": keep_zone,
            "free_shape": shape is not None or (keep_zone and getattr(plugin, "_selected_polygon", None) is not None),
            "note": "The run is asynchronous. Poll generation_status().",
            "hint": (
                "Poll generation_status() every 5 seconds until running is False, "
                "which takes from under a minute to a few minutes."
            ),
        }
        if references is not None:
            result["references_attached"] = references.get("attached")
            result["references_missing"] = references.get("missing")
            result["references_added_layers"] = references.get("added_layers")
            result["references_replaced"] = references.get("replaced")
        if template is not None:
            result["template"] = template
        return result

    @_never_raises
    def generation_status(self) -> dict:














        plugin = self._plugin
        running = self._busy()
        last_request_id = getattr(plugin, "_last_completed_request_id", None)
        last_error = str(getattr(plugin, "_last_generation_error", "") or "")
        last_error_code = str(getattr(plugin, "_last_generation_error_code", "") or "")
        if running:
            hint = (
                "Poll generation_status() again in about 5 seconds; it is finished "
                "when running is False and state is 'done'."
            )
        elif last_error:
            hint = (
                "The last run failed: read error and error_code. Fix what they name "
                "before calling generate() again, because a retry costs a credit."
            )
        elif last_request_id:
            hint = (
                "Call list_versions() and select_version(index) to pick a result, then "
                "vectorize(target_rgb) to trace one colour of it into polygons."
            )
        else:
            hint = "Call set_zone(bbox) then generate(prompt) to start an edit."
        result_layers = self._result_layers()
        return {
            "running": running,

            "in_flight": running,
            "state": "generating" if running else ("done" if last_request_id else "idle"),
            "result_layers": [name for _id, name in result_layers],
            "result_layer_ids": [layer_id for layer_id, _name in result_layers],
            "last_completed_request_id": last_request_id,
            "dock_status": self._dock_status(),
            "error": last_error,
            "error_code": last_error_code,
            "current_resolution": self._selected_resolution(),
            "versions": self._versions(),
            "selected_version_index": getattr(plugin, "_selected_version_index", 0),
            "hint": hint,
        }

    @_never_raises
    def cancel(self, exit_session: bool = False) -> dict:







        plugin = self._plugin
        was_generating = self._busy()
        action = "exit" if exit_session else "stop"
        handler = getattr(plugin, "_on_exit_clicked" if exit_session else "_on_stop", None)
        if not callable(handler):
            return {"_error": f"The {action} handler is not available."}
        handler()
        return {
            "ok": True,
            "action": action,
            "was_generating": was_generating,
            "running": self._busy(),
            "hint": "The zone is gone. Call set_zone(bbox) before the next generate().",
        }

    @_never_raises
    def list_versions(self) -> dict:








        versions = self._versions()
        selected = next((v["index"] for v in versions if v.get("selected")), 0)
        return {
            "count": len(versions),
            "selected_index": selected,
            "versions": versions,
        }

    @_never_raises
    def select_version(self, index: int) -> dict:






        if self._busy():
            return {"_error": "Wait for the current generation before selecting a version.", "busy": True}
        dock = self._dock()
        strip = self._version_strip()
        if dock is None or strip is None:
            return {"_error": "No versions yet. Run a generation first."}
        count = int(strip.count())
        if count <= 0:
            return {"_error": "No versions yet. Run a generation first."}
        try:
            index = _whole_number(index)
        except (TypeError, ValueError, OverflowError):
            return {"_error": "index must be a whole number."}
        if index < 0 or index >= count:
            out = not_found_error(
                "version index", index, valid_range=(0, count - 1),
                means="Index 0 is the original image and each result adds one.",
            )
            out["count"] = count
            return out
        if hasattr(dock, "select_version"):
            dock.select_version(index)
        handler = getattr(dock, "_on_version_selected", None)
        if callable(handler):
            handler(index)
        try:
            label = strip.label_for(index)
        except Exception:
            label = None
        return {
            "ok": True,
            "selected_index": index,
            "label": label,
            "versions": self._versions(),
            "hint": "Call generate(prompt) to build the next edit on this version.",
        }

    @_never_raises
    def finish_session(self, index: int | None = None) -> dict:










        selected = None
        if index is not None:
            selected = self.select_version(index)
            if "_error" in selected:
                return selected
        versions = self._versions()
        chosen = next((v for v in versions if v.get("selected")), None)
        handler = getattr(self._plugin, "_on_exit_clicked", None)
        if not callable(handler):
            return {"_error": "The finish handler is not available."}
        handler()
        return {
            "ok": True,
            "kept_index": chosen.get("index") if chosen else None,
            "kept_label": chosen.get("label") if chosen else None,
            "layers": self._result_layer_names(),
            "note": "Results were already layers in the project. Nothing was written now.",
            "hint": (
                "Call set_zone(bbox) to start the next piece of work, or list_sessions() "
                "to reopen this one later."
            ),
        }

    @_never_raises
    def compare(self, on: bool = True) -> dict:









        dock = self._dock()
        if dock is None:
            return {"_error": "The AI Edit panel is not available."}
        button = getattr(dock, "_swipe_btn", None)
        handler = getattr(self._plugin, "_on_swipe_toggled", None)
        if not callable(handler):
            return {"_error": "Before and after is not available in this build."}
        wanted = bool(on)
        driven = False
        if button is not None:
            try:
                if not button.isEnabled() and wanted:
                    return {
                        "_error": "Nothing to compare yet. Run a generation first.",
                        "comparing": False,
                    }
                if button.isChecked() != wanted:


                    button.setChecked(wanted)
                    driven = True
            except Exception:  # nosec B110
                driven = False
        if not driven:
            handler(wanted)
        comparing = self._comparing()
        result: dict[str, Any] = {"ok": comparing == wanted, "comparing": comparing}
        if comparing != wanted:
            result["_error"] = (
                "The before and after slider is off. It needs a result to compare against, "
                "and a plan that carries it."
            ) if wanted else "The before and after slider is still on."
        return result

    def _comparing(self) -> bool:

        controller = getattr(self._plugin, "_swipe_controller", None)
        try:
            return bool(controller is not None and controller.is_active())
        except Exception:
            return False
