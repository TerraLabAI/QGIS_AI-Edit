"""The generation group of the AI Edit public API.

One edit at a time: AI Edit holds one zone and one worker, so a second
``generate()`` while one runs is refused rather than queued. Everything here
returns at once and the work continues in the background, so a caller polls
``generation_status()`` instead of waiting.
"""
from __future__ import annotations

from typing import Any

from .mcp_api_support import _never_raises, not_found_error


class GenerationMixin:
    """Starting an edit, following it, and choosing among its results."""

    # --- internals --------------------------------------------------------

    def _apply_resolution(self, label: str | None) -> dict:
        """Set the output resolution before a run. Returns the applied value."""
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
        """Tag the next run with a prompt template, without touching the prompt."""
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
        except Exception:  # nosec B110 - the name is a nicety, the id is the tag.
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
        """The version lineage: index 0 is the original, then one per result."""
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
            # Index 0 is the untouched original, so the records list, which holds
            # one entry per RESULT, is one step behind the strip.
            record = records[index - 1] if 0 < index <= len(records) else None
            if isinstance(record, dict):
                entry["layer_id"] = record.get("layer_id")
                entry["request_id"] = record.get("request_id")
            out.append(entry)
        return out

    def _result_layers(self) -> list[tuple[str, str]]:
        """The layers AI Edit produced as (id, name), newest first in tree order."""
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
        """Names of the layers AI Edit produced, newest first in tree order."""
        return [name for _id, name in self._result_layers()]

    def _result_layer_ids(self) -> list[str]:
        """Ids of the same layers. Two runs can share a name, never an id."""
        return [layer_id for layer_id, _name in self._result_layers()]

    # --- public -----------------------------------------------------------

    @_never_raises
    def set_resolution(self, resolution: str) -> dict:
        """Choose how much detail the next edit comes back with. Costs nothing.

        ``resolution`` is one of the labels ``get_resolutions()`` lists. A
        bigger one costs more credits per edit and takes longer to come back.
        Free accounts are limited to the smallest one, and asking for a bigger
        one returns ``plan_restricted`` True rather than changing anything.

        Returns ``resolution`` as it now stands and ``applied``.
        """
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
        """Start one AI edit and return at once. The work runs in the background.

        This is the call that spends credits. One edit runs at a time.

        ``prompt`` is the instruction, in plain words. ``bbox`` is the zone to
        edit, either ``[xmin, ymin, xmax, ymax]`` or a dict of those four keys,
        read in the map canvas CRS. ``polygon_wkt`` selects a free shape
        instead, the same thing a person draws in the panel: the rectangle
        around the shape is edited and the result is then cut back to the
        shape. Pass one of the two, never both. Leave both out to keep the zone
        already selected in the panel, which is how you iterate on a result;
        with no zone selected at all, the current canvas view is used.
        ``resolution`` is one of the labels ``get_resolutions()`` lists.
        ``template_id`` tags the run with a preset from ``get_presets()``.
        ``reference_layers`` names project layers to send as extra context,
        each cropped to the zone. It takes the same well-known basemap names
        ``attach_reference()`` does ("Satellite", "Streets", "Terrain",
        "Hillshade", "OpenStreetMap"): one that is not in the project is added
        to it, hidden, and ``references_added_layers`` names it. It REPLACES
        every picture already attached, notes and all, and
        ``references_replaced`` names what it dropped, so take one road or the
        other. For a file on disk, or a note per picture, call
        ``attach_reference()`` and then call this WITHOUT
        ``reference_layers``.

        Returns ``submitted``, ``request_id`` (always None here: the server
        mints the id once it accepts the job, and ``generation_status()``
        reports it when the run ends), plus the ``resolution`` used and what was
        attached. A run already in flight comes back as
        ``{"busy": True, "_error": ...}``: poll ``generation_status()`` and
        retry when it reports ``running`` False.
        """
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

        # The same truth test _on_generate applies to the zone, so the facade
        # and the plugin can never disagree on whether one is selected.
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

        applied = self._apply_resolution(resolution)
        if "_error" in applied:
            applied["submitted"] = False
            return applied

        # A fresh zone breaks the iteration chain on purpose (new lineage, no
        # parent run). Reusing the selected zone must not, so it is left alone.
        if not keep_zone:
            self._install_zone(extent, shape)

        # After the zone, never before: selecting one clears the armed template.
        template = self._arm_template(str(template_id)) if template_id else None

        # References are rendered here, between the zone and the run, and the
        # run starts immediately after: the panel can rebuild itself at any
        # time and that would drop them.
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
        # Hand the run over as headless. _on_generate reads this once and the
        # failure path then reports instead of opening a modal, which nothing
        # on this side of the call could ever dismiss.
        plugin._api_run_requested = True
        try:
            run(prompt)
        finally:
            plugin._api_run_requested = False

        # The plugin refuses a run in place (no zone, marks that would not
        # render) by returning without starting anything, so the answer is
        # whether it took the job, not whether the call returned.
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
            "free_shape": shape is not None,
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
        """Report what the current or last run is doing. Makes no network call.

        Returns ``running``, ``state`` ("generating", "done" or "idle"),
        ``result_layers`` (the layers AI Edit has produced in this project),
        ``result_layer_ids`` (the same layers by id, which never collide),
        ``last_completed_request_id``, the panel's own ``dock_status`` line,
        ``current_resolution``, and the ``versions`` lineage with
        ``selected_version_index``. Poll this after ``generate()``: the run is
        finished when ``running`` is False.

        ``error`` and ``error_code`` carry the last failure, both empty when
        the last run did not fail. A run that failed still ends with ``state``
        "done" or "idle", so these two are what tells success from failure.
        """
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
        return {
            "running": running,
            # Kept for callers written against the older reflection layer.
            "in_flight": running,
            "state": "generating" if running else ("done" if last_request_id else "idle"),
            "result_layers": self._result_layer_names(),
            "result_layer_ids": self._result_layer_ids(),
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
        """Stop the run in flight and clear the zone.

        With ``exit_session`` True it also leaves the edit session and returns
        the panel to its start screen, the same as the Exit button. Returns
        ``ok``, the ``action`` taken, ``was_generating``, and ``running`` as it
        stands afterwards.
        """
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
        """List the results of the current piece of work. Costs nothing.

        Index 0 is the original image, and every edit adds one. Each entry
        carries ``index``, ``label``, the ``prompt`` that produced it,
        ``selected``, and where known its ``layer_id`` in the project and its
        ``request_id``. Returns ``count``, ``selected_index`` and ``versions``.
        Empty until the first edit finishes.
        """
        versions = self._versions()
        selected = next((v["index"] for v in versions if v.get("selected")), 0)
        return {
            "count": len(versions),
            "selected_index": selected,
            "versions": versions,
        }

    @_never_raises
    def select_version(self, index: int) -> dict:
        """Pick the version the next edit builds on, as clicking a tile does.

        ``index`` 0 is the original image and each result adds one. Returns
        ``ok``, ``selected_index``, its ``label``, and the full ``versions``
        list. Run a generation first: with no lineage yet this returns an error.
        """
        dock = self._dock()
        strip = self._version_strip()
        if dock is None or strip is None:
            return {"_error": "No versions yet. Run a generation first."}
        count = int(strip.count())
        if count <= 0:
            return {"_error": "No versions yet. Run a generation first."}
        try:
            index = int(index)
        except (TypeError, ValueError):
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
        """Close this piece of work and return the panel to its start screen.

        Nothing has to be saved first. Every result is already a layer in the
        QGIS project from the moment it arrives, and the whole session stays in
        the history, so finishing only ends the session and drops the zone.
        Pass ``index`` to select that version before finishing, which is what
        leaves it as the one showing on the map.

        Costs nothing. Returns ``ok``, ``kept_index`` and ``kept_label``.
        """
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
        """Turn the before and after slider on the map on or off. Costs nothing.

        The same thing as the Before / after button: a divider you drag across
        the zone to see the original under the result. This only changes what
        is drawn, never the data. Returns ``ok`` and ``comparing``, which is
        what the map is actually doing rather than what was asked for: the
        slider is refused on a plan that does not carry it, and on a project
        with no result to compare against.
        """
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
                    # Checking the button emits toggled, which runs the handler.
                    # Calling the handler too ran the whole thing twice.
                    button.setChecked(wanted)
                    driven = True
            except Exception:  # nosec B110 - the handler below is what matters.
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
        """Whether the before and after slider is on the map right now."""
        controller = getattr(self._plugin, "_swipe_controller", None)
        try:
            return bool(controller is not None and controller.is_active())
        except Exception:
            return False
