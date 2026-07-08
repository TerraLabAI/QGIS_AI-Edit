"""Vectorize run lifecycle: task launch, success/failure handling, persistence."""
from __future__ import annotations

import os

from qgis.core import QgsFeature, QgsProject, QgsRasterLayer

from ....core import telemetry
from ....core import telemetry_events as te
from ....core.errors import AIEditError, ErrorCode
from ....core.i18n import tr
from ...layer_groups import (
    add_layer_to_ai_edit_top,
    find_generation_subgroup_for_layer,
    promote_layer_to_own_subgroup,
)


class RunLifecycleMixin:
    """Runs the vectorize task and turns its results into map layers."""

    def _on_run_clicked(self) -> None:
        if self._busy:
            return

        # After success, button reads "Done" and exits; refine spinboxes still re-run.
        if self._succeeded:
            self.done_clicked.emit()
            return

        raster = self._layer_combo.currentLayer()
        if not isinstance(raster, QgsRasterLayer):
            self._show_status(
                tr("Pick a raster from the source list first."), is_error=True
            )
            return
        if raster.bandCount() < 3:
            self._show_status(
                tr("This raster needs at least 3 bands (RGB)."), is_error=True
            )
            return

        target_rgb = (self._color.red(), self._color.green(), self._color.blue())
        self._run_vectorize(raster, target_rgb, is_initial=True)

    def _on_refine_apply(self) -> None:
        """Debounced re-run on the same raster + extracted color."""
        if self._last_raster_id is None or self._last_target_rgb is None:
            return
        raster = QgsProject.instance().mapLayer(self._last_raster_id)
        if not isinstance(raster, QgsRasterLayer):
            self._show_status(
                tr("Source raster is no longer available."), is_error=True
            )
            return
        # Use the CURRENT swatch color so re-picking the color (or the eyedropper)
        # re-runs detection with the new target, not the originally locked one.
        target_rgb = (self._color.red(), self._color.green(), self._color.blue())
        self._run_vectorize(raster, target_rgb, is_initial=False)

    def _run_vectorize(
        self,
        raster: QgsRasterLayer,
        target_rgb: tuple[int, int, int],
        is_initial: bool,
    ) -> None:
        # Friendly, dated tree name ("Buildings (3 Jul)" when a class is known,
        # else "<raster> (vector)"), deduped against existing layers. Only used
        # on the initial run; refine re-runs transplant into the existing layer
        # and keep its name.
        from ....core.generation.vectorization_service import (
            friendly_vector_layer_name,
        )
        layer_name = friendly_vector_layer_name(self._class_label, raster.name())

        # Supersede any in-flight run (e.g. a debounced refine tick) so the
        # latest parameters win and runs never overlap.
        self.cancel_pending_task()

        # Capture all QgsProject / main-thread context NOW. The heavy compute
        # runs on a worker thread and must not touch QgsProject or the layer.
        project = QgsProject.instance()
        compute_kwargs = {
            "raster_path": (raster.source() or "").split("|", 1)[0],
            "raster_crs": raster.crs(),
            "transform_context": project.transformContext(),
            "ellipsoid": project.ellipsoid() or "EPSG:7030",
            "target_rgb": target_rgb,
            "tolerance": int(self._tolerance_spin.value()),
            "sieve_threshold": int(self._sieve_spin.value()),
            "min_pixels": int(self._min_pixels_spin.value()),
            "simplify_factor": float(self._simplify_spin.value()),
            "round_corners": bool(self._round_corners_check.isChecked()),
            "expand_value": int(self._expand_spin.value()),
            "fill_holes": bool(self._fill_holes_check.isChecked()),
            "class_label": self._class_label,
            # Foreground extraction against a white background: assign each pixel
            # to the nearer of the picked color and white, so anti-aliased class
            # edges split at the true boundary and a hue-drifted output (e.g. a
            # "red" that renders as #D37523) is still captured. The 2-color
            # "class in color, everything else white" map is the canonical case.
            "match_mode": "nearest",
            "background_rgb": self._background,
        }
        params = {
            "raster_id": raster.id(),
            "raster_name": raster.name() or "",
            "raster_crs": raster.crs(),
            "target_rgb": target_rgb,
            "is_initial": is_initial,
            "layer_name": layer_name,
            "tolerance": compute_kwargs["tolerance"],
            "sieve_threshold": compute_kwargs["sieve_threshold"],
            "simplify_factor": compute_kwargs["simplify_factor"],
            "round_corners": compute_kwargs["round_corners"],
            "expand_value": compute_kwargs["expand_value"],
            "fill_holes": compute_kwargs["fill_holes"],
        }

        # Only flip the action row into a "running" state on the initial click.
        # Debounced refine re-runs keep the Done button + status line intact so
        # spinbox ticks don't flicker the panel.
        if is_initial:
            self._busy = True
            self._run_btn.setEnabled(False)
            self._color_btn.setEnabled(False)
            self._run_btn.setText(tr("Vectorizing..."))
            self._show_status(
                tr("Vectorizing “{name}”...").format(name=raster.name()),
                is_error=False,
            )

        from qgis.core import QgsApplication

        from ....workers.vectorize_task import VectorizeTask

        task = VectorizeTask(compute_kwargs, params)
        task.succeeded.connect(self._on_vectorize_succeeded)
        task.failed.connect(self._on_vectorize_failed)
        self._vectorize_task = task
        QgsApplication.taskManager().addTask(task)

    def cancel_pending_task(self) -> None:
        """Cancel any in-flight vectorize task (new run, panel exit, teardown)."""
        task = self._vectorize_task
        self._vectorize_task = None
        if task is not None:
            try:
                if task.is_active():
                    task.cancel()
            except RuntimeError:
                pass

    def _on_vectorize_succeeded(self, feats, params) -> None:
        """Main thread: build the layer from the computed features, then place
        it and update the panel. Layer/project work must stay on this thread."""
        self._vectorize_task = None
        is_initial = params["is_initial"]
        try:
            from ....core.generation.vectorization_service import _build_vector_layer

            new_layer = _build_vector_layer(
                feats, params["raster_crs"], params["layer_name"],
                params["target_rgb"], None, self._class_label,
                source_raster_name=params.get("raster_name", ""),
            )

            previous_id = self._last_layer_id
            existing = (
                QgsProject.instance().mapLayer(previous_id) if previous_id else None
            )
            if existing is None:
                # First run: swap the volatile memory layer for a GeoPackage
                # table so the result survives the QGIS session. Falls back to
                # the memory layer if the write fails.
                persisted = self._persist_layer(new_layer, params)
                if persisted is not None:
                    new_layer = persisted
            if existing is not None:
                # Re-run: transplant the new geometries into the existing layer
                # so the user's symbology, name and layer id all survive.
                provider = existing.dataProvider()
                old_ids = [f.id() for f in existing.getFeatures()]
                delete_ok = provider.deleteFeatures(old_ids) if old_ids else True
                fresh_feats = [QgsFeature(f) for f in new_layer.getFeatures()]
                add_ok = provider.addFeatures(fresh_feats)
                if existing.providerType() == "ogr":
                    # OGR edits write straight to the GeoPackage, so a False
                    # return means the disk write failed. Surface it instead of
                    # reporting success on a silently-emptied layer.
                    if not (delete_ok and add_ok):
                        raise AIEditError(
                            ErrorCode.WRITE_ERROR,
                            tr("Couldn't save the updated features to the file."),
                        )
                    # Provider edits went straight to the GeoPackage; re-read
                    # so feature count and ids reflect the file.
                    existing.reload()
                existing.updateExtents()
                # Re-pick of a different colour: restyle so the trace matches the
                # new selection. Same colour keeps the user's symbology untouched.
                if params["target_rgb"] != self._last_target_rgb:
                    from ....core.generation.vectorization_service import (
                        _apply_style,
                        _set_layer_provenance,
                    )
                    _apply_style(existing, params["target_rgb"])
                    _set_layer_provenance(
                        existing, params.get("raster_name", ""),
                        params["target_rgb"], self._class_label,
                    )
                existing.triggerRepaint()
                final_layer = existing
            else:
                QgsProject.instance().addMapLayer(new_layer, False)
                # Lazily promote the source raster into its own sub-group on the
                # first vectorization, then drop the vector layer alongside it.
                raster_id = params["raster_id"]
                subgroup = find_generation_subgroup_for_layer(raster_id)
                if subgroup is None:
                    subgroup = promote_layer_to_own_subgroup(raster_id)
                if subgroup is not None:
                    subgroup.insertLayer(0, new_layer)
                else:
                    add_layer_to_ai_edit_top(new_layer)
                final_layer = new_layer

            # Hide the source raster so the freshly traced polygons read clearly
            # on top. With the vector now in the picked colour, leaving the raster
            # visible underneath would make the trace hard to see against it.
            raster_id = params["raster_id"]
            if raster_id:
                node = QgsProject.instance().layerTreeRoot().findLayer(raster_id)
                if node is not None:
                    node.setItemVisibilityChecked(False)

            self._last_layer_id = final_layer.id()
            self._last_raster_id = params["raster_id"]
            self._last_target_rgb = params["target_rgb"]

            polygon_count = final_layer.featureCount()
            self._show_status(
                "✓ " + tr("{n} polygons added").format(n=polygon_count),
                is_error=False,
                is_success=True,
            )
            self._succeeded = True
            if is_initial:
                self._activate_layer_in_panel(final_layer)
                self._refine_group.setVisible(True)
                self._run_btn.setText(tr("Done"))
                self._exit_btn.setVisible(False)
                # Color section stays visible so the user can re-pick the color
                # / tolerance and refine without restarting. Only the source
                # layer is locked in (you refine one raster at a time).
                self._layer_group.setVisible(False)
            telemetry.track(
                te.VECTORIZE_COMPLETED,
                {
                    "polygon_count": polygon_count,
                    "tolerance": params["tolerance"],
                    "sieve": params["sieve_threshold"],
                    "simplify": int(params["simplify_factor"]),
                    "round_corners": params["round_corners"],
                    "expand": params["expand_value"],
                    "fill_holes": params["fill_holes"],
                    "is_initial": is_initial,
                },
            )
        except AIEditError as err:
            self._handle_run_error(err.message, err.code)
        except Exception as e:
            self._handle_run_error(str(e), None)
        finally:
            self._reset_button()

    def _persist_layer(self, mem_layer, params):
        """One GeoPackage next to the generated rasters, one table per run
        (lowercase ASCII names per the GeoPackage spec)."""
        import time

        from ....core.generation.vectorization_service import (
            AI_EDIT_GPKG_FILENAME,
            make_layer_permanent,
        )
        from ....core.slug import slugify
        from ...raster_writer import get_output_dir

        base = slugify(self._class_label or params.get("raster_name", ""))[:40] or "result"
        table_name = f"vectorize_{base}_{time.strftime('%Y%m%d_%H%M%S')}"
        return make_layer_permanent(
            mem_layer,
            os.path.join(get_output_dir(), AI_EDIT_GPKG_FILENAME),
            table_name,
            params["target_rgb"],
            self._class_label,
            params.get("raster_name", ""),
        )

    def _on_vectorize_failed(self, message: str, code: str) -> None:
        self._vectorize_task = None
        from ....core.errors import ErrorCode as _EC

        code_enum = None
        if code:
            try:
                code_enum = _EC(code)
            except ValueError:
                code_enum = None
        self._handle_run_error(message, code_enum)
        self._reset_button()

    def _handle_run_error(self, message: str, code=None) -> None:
        """Render a friendlier error and steer the user to the lever that
        usually fixes it. ``code`` lets us branch without parsing English
        substrings (replaced lower().contains check).
        """
        from ....core.errors import ErrorCode as _EC
        is_zero_match = code == _EC.NO_PIXELS_MATCHED
        if is_zero_match and self._succeeded:
            # Active refine: detection is fixed, so a re-run only zeroes out
            # when the outline/selection filters drop everything. Steer the
            # user to the lever that usually did it - min polygon size.
            self._show_status(
                tr(
                    "No shapes left after filtering. Lower 'Min polygon "
                    "size' below."
                ),
                is_error=True,
            )
            self._refine_group.setVisible(True)
            self._min_pixels_spin.setFocus()
        elif is_zero_match:
            # Cold 0-match: nothing was vectorized yet, so the refine knobs
            # would be editing polygons that don't exist. Keep them hidden
            # and steer the user back to the color picker (their recovery
            # path stays visible above). Showing 8 dead controls here just
            # confuses (issue #164).
            self._refine_group.setVisible(False)
            self._show_status(
                tr(
                    "0 matches. Pick a closer color above, or use "
                    "Pick on map to sample one from the raster."
                ),
                is_error=True,
            )
        else:
            self._show_status(message, is_error=True)

    def _activate_layer_in_panel(self, layer) -> None:
        """Highlight the freshly-produced layer in the QGIS Layers panel."""
        try:
            from qgis.utils import iface as _iface
            if _iface is not None:
                _iface.setActiveLayer(layer)
        except Exception:  # pragma: no cover  # nosec B110
            pass

    def _reset_button(self) -> None:
        self._busy = False
        self._run_btn.setEnabled(True)
        self._color_btn.setEnabled(True)
        # If we succeeded, the run text was already set to "Done" upstream
        # and we must NOT overwrite it here (otherwise refine re-runs would
        # silently flip the label back to "Vectorize").
        if not self._succeeded:
            self._run_btn.setText(tr("Vectorize"))
