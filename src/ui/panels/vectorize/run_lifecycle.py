
from __future__ import annotations

import os

from qgis.core import QgsProject, QgsRasterLayer

from ....core import telemetry
from ....core import telemetry_events as te
from ....core.config_store import get_export_copy
from ....core.errors import AIEditError, ErrorCode
from ....core.i18n import tr
from ...layer_groups import (
    add_layer_to_ai_edit_top,
    find_generation_subgroup_for_layer,
    promote_layer_to_own_subgroup,
)


def _emptying_setting(run: dict | None, good: dict | None) -> str | None:









    if not run:
        return None
    if good:
        if run["expand"] < 0 and run["expand"] < good["expand"]:
            return "expand"
        if run["min_pixels"] > good["min_pixels"]:
            return "min_pixels"
        if run["tolerance"] < good["tolerance"]:
            return "tolerance"
    return "expand" if run["expand"] < 0 else None


def _result_line(layer_name: str, polygons: int, classes: int, is_initial: bool) -> str:



    count = (
        tr("1 polygon") if polygons == 1 else tr("{n} polygons").format(n=polygons)
    )
    if classes > 1:
        count = tr("{count} in {k} classes").format(count=count, k=classes)
    if is_initial:
        head = get_export_copy("widgets.run_lifecycle.added_to_map_as", tr("Added to your map as"))
        return tr("{head} “{name}”: {count}.").format(head=head, name=layer_name, count=count)
    return tr("“{name}” updated: {count}.").format(name=layer_name, count=count)


class RunLifecycleMixin:


    def _on_run_clicked(self) -> None:


        if self._busy or self._succeeded:
            return

        raster = self._layer_combo.currentLayer()
        if not isinstance(raster, QgsRasterLayer):
            self._show_status(
                get_export_copy(
                    "widgets.run_lifecycle.pick_map_first",
                    tr("Pick a map under Layer first."),
                ),
                is_error=True,
            )
            return
        if raster.bandCount() < 3:
            self._show_status(
                get_export_copy(
                    "widgets.run_lifecycle.needs_rgb_bands",
                    tr("This raster needs at least 3 bands (RGB)."),
                ),
                is_error=True,
            )
            return
        if not self._class_list.selected_classes():
            self._show_status(
                get_export_copy(
                    "widgets.run_lifecycle.check_one_class",
                    tr("Check at least one class to vectorize."),
                ),
                is_error=True,
            )
            return

        self._run_vectorize(raster, is_initial=True)

    def _on_refine_apply(self) -> None:


        if self._last_raster_id is None:
            return
        raster = QgsProject.instance().mapLayer(self._last_raster_id)
        if not isinstance(raster, QgsRasterLayer):
            self._show_status(
                get_export_copy(
                    "widgets.run_lifecycle.map_unavailable",
                    tr("The map this layer came from was removed. Vectorize it again."),
                ),
                is_error=True,
            )
            return
        if not self._class_list.selected_classes():
            self._show_status(
                get_export_copy(
                    "widgets.run_lifecycle.check_one_class",
                    tr("Check at least one class to vectorize."),
                ),
                is_error=True,
            )
            return
        self._run_vectorize(raster, is_initial=False)

    def _run_vectorize(self, raster: QgsRasterLayer, is_initial: bool) -> None:







        from ....core.generation.vectorize_layer import friendly_vector_layer_name

        classes = self._class_list.selected_classes()
        competitors = self._class_list.competitor_colors()
        map_name = (raster.name() or "").strip()
        if len(classes) == 1:
            name_base = classes[0]["label"]
        elif map_name:
            name_base = tr("{map} polygons").format(map=map_name)
        else:
            name_base = ""
        layer_name = friendly_vector_layer_name(name_base, raster.name())



        self.cancel_pending_task()



        project = QgsProject.instance()
        compute_kwargs = {
            "raster_path": (raster.source() or "").split("|", 1)[0],
            "raster_crs": raster.crs(),
            "transform_context": project.transformContext(),
            "ellipsoid": project.ellipsoid() or "EPSG:7030",




            "classes": classes,
            "competitors": competitors,
            "tolerance": int(self._tolerance_spin.value()),
            "sieve_threshold": int(self._sieve_spin.value()),
            "min_pixels": int(self._min_pixels_spin.value()),
            "simplify_factor": float(self._simplify_spin.value()),
            "round_corners": bool(self._round_corners_check.isChecked()),
            "expand_value": int(self._expand_spin.value()),
            "fill_holes": bool(self._fill_holes_check.isChecked()),
        }



        self._run_settings = {
            "expand": compute_kwargs["expand_value"],
            "min_pixels": compute_kwargs["min_pixels"],
            "sieve": compute_kwargs["sieve_threshold"],
            "tolerance": compute_kwargs["tolerance"],
        }
        params = {
            "settings": dict(self._run_settings),
            "raster_id": raster.id(),
            "raster_name": raster.name() or "",
            "raster_crs": raster.crs(),
            "classes": classes,
            "signature": self._class_list.selection_signature(),
            "is_initial": is_initial,
            "layer_name": layer_name,
            "tolerance": compute_kwargs["tolerance"],
            "sieve_threshold": compute_kwargs["sieve_threshold"],
            "simplify_factor": compute_kwargs["simplify_factor"],
            "round_corners": compute_kwargs["round_corners"],
            "expand_value": compute_kwargs["expand_value"],
            "fill_holes": compute_kwargs["fill_holes"],
        }





        if is_initial:
            self._busy = True
            self._set_setup_locked(True)
            self._run_btn.setEnabled(False)
            self._run_btn.setText(
                get_export_copy("widgets.run_lifecycle.vectorizing_button_label", tr("Vectorizing..."))
            )
            self._show_status("", is_error=False)
        else:
            self._show_status(
                get_export_copy("widgets.run_lifecycle.updating_layer", tr("Updating the layer...")),
                is_error=False,
                is_hint=True,
            )

        from qgis.core import QgsApplication

        from ....workers.vectorize_task import VectorizeTask

        task = VectorizeTask(compute_kwargs, params)
        task.succeeded.connect(self._on_vectorize_succeeded)
        task.failed.connect(self._on_vectorize_failed)


        task.taskTerminated.connect(
            lambda t=task: self._on_vectorize_task_terminated(t)
        )
        self._vectorize_task = task
        QgsApplication.taskManager().addTask(task)

    def cancel_pending_task(self) -> None:

        task = self._vectorize_task
        self._vectorize_task = None
        if task is not None:
            from ...plugin_parts.lifecycle import disconnect_signals



            disconnect_signals(task, ("succeeded", "failed", "taskTerminated"))
            try:
                if task.is_active():
                    task.cancel()
            except RuntimeError:
                pass

    def _on_vectorize_task_terminated(self, task) -> None:





        if self._vectorize_task is not task:
            return
        self._vectorize_task = None
        if not self._busy:
            return
        try:
            self._show_status(
                get_export_copy("widgets.run_lifecycle.cancelled", tr("Vectorize cancelled.")),
                is_error=False,
            )
        finally:
            self._reset_button()

    def _on_vectorize_succeeded(self, feats, params) -> None:


        self._vectorize_task = None
        is_initial = params["is_initial"]
        classes = params["classes"]
        try:
            from ....core.generation.vectorize_layer import (
                build_vector_layer,
                refresh_class_setup,
                transplant_features,
            )

            new_layer = build_vector_layer(
                feats, params["raster_crs"], params["layer_name"],
                classes, source_raster_name=params.get("raster_name", ""),
            )

            previous_id = self._last_layer_id
            existing = (
                QgsProject.instance().mapLayer(previous_id) if previous_id else None
            )
            if existing is None:



                persisted = self._persist_layer(new_layer, params)
                if persisted is not None:
                    new_layer = persisted
                else:
                    self._notify_memory_only()
            if existing is not None:




                if existing.isEditable():



                    raise AIEditError(
                        ErrorCode.LAYER_IN_EDIT_MODE,
                        tr("Save or discard your edits on the vector layer, then run Vectorize again."),
                    )
                transplant_ok = transplant_features(existing, new_layer)
                if not transplant_ok:



                    raise AIEditError(
                        ErrorCode.WRITE_ERROR,
                        tr("Couldn't save the updated features to the file."),
                    )
                if existing.providerType() == "ogr":


                    existing.reload()
                existing.updateExtents()



                if params["signature"] != self._last_signature:
                    refresh_class_setup(existing, classes, params.get("raster_name", ""))
                existing.triggerRepaint()
                final_layer = existing
            else:
                QgsProject.instance().addMapLayer(new_layer, False)


                raster_id = params["raster_id"]
                subgroup = find_generation_subgroup_for_layer(raster_id)
                if subgroup is None:
                    subgroup = promote_layer_to_own_subgroup(raster_id)
                if subgroup is not None:
                    subgroup.insertLayer(0, new_layer)
                else:
                    add_layer_to_ai_edit_top(new_layer)
                final_layer = new_layer




            raster_id = params["raster_id"]
            if raster_id:
                node = QgsProject.instance().layerTreeRoot().findLayer(raster_id)
                if node is not None:
                    node.setItemVisibilityChecked(False)

            self._last_layer_id = final_layer.id()
            self._last_raster_id = params["raster_id"]
            self._last_signature = params["signature"]
            self._good_settings = params.get("settings")

            polygon_count = final_layer.featureCount()


            traced_count = len({f.attributes()[1] for f in feats})



            self._show_status(
                _result_line(
                    final_layer.name(), polygon_count, traced_count,
                    is_initial and existing is None,
                ),
                is_error=False,
                is_success=True,
            )
            was_refining = self._succeeded
            self._succeeded = True
            if is_initial:
                self._activate_layer_in_panel(final_layer)
            if not was_refining:



                self._apply_page()



                self.setFocus()
            telemetry.track(
                te.VECTORIZE_COMPLETED,
                {
                    "polygon_count": polygon_count,
                    "class_count": len(classes),
                    "tolerance": params["tolerance"],
                    "sieve": params["sieve_threshold"],
                    "simplify": float(params["simplify_factor"]),
                    "round_corners": params["round_corners"],
                    "expand": params["expand_value"],
                    "fill_holes": params["fill_holes"],
                    "is_initial": is_initial,
                },
            )
            telemetry.flush()
        except AIEditError as err:
            self._handle_run_error(err.message, err.code)
        except Exception as e:  # noqa: BLE001
            from ....core.logger import log_warning

            log_warning(f"Vectorize result could not be placed: {e}")
            self._handle_run_error(str(e), None, friendly=False)
        finally:
            self._reset_button()

    def _persist_layer(self, mem_layer, params):


        import time

        from ....core.generation.vectorize_layer import (
            AI_EDIT_GPKG_FILENAME,
            persist_layer_to_gpkg,
        )
        from ....core.slug import slugify
        from ...raster_writer import get_output_dir

        classes = params["classes"]
        single_label = classes[0]["label"] if len(classes) == 1 else ""
        base = slugify(single_label or params.get("raster_name", ""))[:40] or "result"
        table_name = f"vectorize_{base}_{time.strftime('%Y%m%d_%H%M%S')}"
        layer, reason = persist_layer_to_gpkg(
            mem_layer,
            os.path.join(get_output_dir(), AI_EDIT_GPKG_FILENAME),
            table_name,
            classes,
            params.get("raster_name", ""),
        )
        if layer is None:
            telemetry.track(te.PLUGIN_ERROR, {
                "stage": "vectorize",
                "error_code": "gpkg_persist_failed",
                "error_message": reason,
            })
        return layer

    def _notify_memory_only(self) -> None:


        try:
            from qgis.core import Qgis
            from qgis.utils import iface as _iface

            if _iface is not None:
                _iface.messageBar().pushMessage(
                    "AI Edit",
                    tr(
                        "Saved in memory only: the GeoPackage is in use. "
                        "Save the layer before closing QGIS."
                    ),
                    level=Qgis.MessageLevel.Warning,
                    duration=12,
                )
        except Exception:  # nosec B110
            pass

    def _on_vectorize_failed(self, message: str, code: str) -> None:
        self._vectorize_task = None
        from ....core.errors import ErrorCode as _EC

        code_enum = None
        if code:
            try:
                code_enum = _EC(code)
            except ValueError:
                code_enum = None




        try:
            self._handle_run_error(message, code_enum)
        finally:
            self._reset_button()

    def _handle_run_error(self, message: str, code=None, friendly: bool = True) -> None:




        from ....core.errors import ErrorCode as _EC
        is_zero_match = code == _EC.NO_PIXELS_MATCHED


        if is_zero_match and self._succeeded:
            error_code = "no_shapes_after_filter"
        elif is_zero_match:
            error_code = "zero_matches"
        elif code == _EC.WRITE_ERROR:
            error_code = "write_error"
        elif code == _EC.LAYER_IN_EDIT_MODE:
            error_code = "layer_in_edit_mode"
        else:
            error_code = "vectorize_failed"



        from ....core.log_scrub import scrub_file_paths, scrub_urls
        telemetry.track(te.PLUGIN_ERROR, {
            "stage": "vectorize",
            "error_code": error_code,
            "error_message": scrub_file_paths(scrub_urls(str(message or "")))[:200],
        })
        telemetry.flush()
        if is_zero_match and self._succeeded:




            run = self._run_settings or {}
            setting = _emptying_setting(self._run_settings, self._good_settings)
            if setting == "expand":
                text = tr(
                    "Contracting by {n} px erased every shape. Set "
                    "“Expand/Contract” closer to 0."
                ).format(n=abs(run["expand"]))
                focus = self._expand_spin
            elif setting == "min_pixels":
                text = tr(
                    "No shape reaches {n} px. Lower “Min polygon size”."
                ).format(n=run["min_pixels"])
                focus = self._min_pixels_spin
            elif setting == "tolerance":
                text = tr(
                    "“Color tolerance” at {n} matches no pixel. Raise it."
                ).format(n=run["tolerance"])
                focus = self._tolerance_spin
            else:
                text = get_export_copy(
                    "widgets.run_lifecycle.no_shapes_generic_v2",
                    tr(
                        "No shapes left with these settings. Set “Expand/Contract” "
                        "closer to 0, lower “Min polygon size”, or raise “Color tolerance”."
                    ),
                )
                focus = self._min_pixels_spin
            self._show_status(
                text
                + " "
                + get_export_copy(
                    "widgets.run_lifecycle.keeps_last_result",
                    tr("The layer keeps its last result."),
                ),
                is_error=True,
            )
            self._refine_group.setVisible(True)
            focus.setFocus()
        elif is_zero_match:





            self._refine_group.setVisible(False)
            self._show_status(
                get_export_copy(
                    "widgets.run_lifecycle.zero_matches_colors",
                    tr(
                        "No pixel matches the checked colors. Adjust a color, "
                        "or add one with “Add color from map”."
                    ),
                ),
                is_error=True,
            )
        elif friendly and message:
            self._show_status(message, is_error=True)
        else:


            self._show_status(
                get_export_copy(
                    "widgets.run_lifecycle.place_failed",
                    tr("The polygons could not be added to your map. Try again."),
                ),
                is_error=True,
            )

    def _activate_layer_in_panel(self, layer) -> None:

        try:
            from qgis.utils import iface as _iface
            if _iface is not None:
                _iface.setActiveLayer(layer)
        except Exception:  # pragma: no cover  # nosec B110
            pass

    def _reset_button(self) -> None:


        self._busy = False
        self._set_setup_locked(False)
        self._run_btn.setText(get_export_copy("widgets.run_lifecycle.run_button_label", tr("Vectorize")))
        self._sync_run_enabled()
