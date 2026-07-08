from __future__ import annotations

import os
import time

from qgis.core import Qgis, QgsApplication
from qgis.PyQt.QtCore import QSettings
from qgis.PyQt.QtWidgets import QPushButton

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import has_consent, save_consent
from ...core.entitlements import coerce_tier
from ...core.errors import build_failure_props
from ...core.i18n import tr
from ...core.logger import log, log_debug, log_warning
from ...core.prompts.prompt_presets import (
    detect_freeform_vector_intent,
    detect_seg_context,
    get_vector_hints,
    lookup_template_by_prompt,
)
from ...workers.export_worker import ExportWorker
from ...workers.generic_request_task import GenericRequestTask
from ..canvas_exporter import (
    apply_export_context,
    build_input_render_set,
    has_server_config,
    has_tuned_config,
    native_size_inputs,
    prepare_export,
)
from ..raster_writer import get_output_dir
from .lifecycle import teardown_step


def _served_size(result) -> tuple[int, int] | None:


    if not isinstance(result, dict):
        return None
    w, h = result.get("width"), result.get("height")
    if isinstance(w, int) and isinstance(h, int) and not isinstance(w, bool) and not isinstance(h, bool):
        return (w, h)
    return None


class GenerationMixin:
    @staticmethod
    def _days_since_activation() -> int | None:

        raw = QSettings().value("AIEdit/activation_timestamp_unix", "", type=str)
        if not raw:
            return None
        try:
            ts = int(raw)
        except (TypeError, ValueError):
            return None
        delta = int((time.time() - ts) // 86400)
        return max(delta, 0)

    def _enrich_generation_props(self, base: dict) -> dict:
        enriched = {
            **base,
            "context_image_count": self._reference_store.count(),
            "context_total_size_bytes": self._reference_store.total_size_bytes(),
        }
        days = self._days_since_activation()
        if days is not None:
            enriched["days_since_activation"] = days
        return enriched

    def _on_generation_task_terminated(self):






        worker = self._worker
        if worker is None or not worker.isCanceled():
            return
        if self._generation_cancel_handled:
            self._generation_cancel_handled = False
            return



        duration = time.time() - getattr(self, "_generation_start_time", time.time())
        telemetry.track(te.GENERATION_CANCELLED, self._enrich_generation_props({
            "duration_ms": int(duration * 1000),
            "resolution": getattr(self, "_last_suggested_res", ""),
        }))
        telemetry.flush()
        self._generation_service.cancel()
        if self._map_tool:
            self._map_tool.set_locked(False)
        self._dock_widget.set_generating(False)
        self._restore_failed_iteration()
        self._cleanup_worker()
        self._clear_markup_layer()

    def _maybe_emit_first_generation_milestone(self):




        if self._first_generation_milestone_emitted:
            return
        settings = QSettings()
        already = settings.value("AIEdit/first_generation_milestone_fired", False, type=bool)
        if already:
            self._first_generation_milestone_emitted = True
            return
        days = self._days_since_activation()
        props = {}
        if days is not None:
            props["days_since_activation"] = days
        telemetry.track(te.FIRST_GENERATION_MILESTONE, props)
        telemetry.flush()
        settings.setValue("AIEdit/first_generation_milestone_fired", True)

        try:
            settings.sync()
        except Exception:  # nosec B110
            pass
        self._first_generation_milestone_emitted = True

    def _maybe_show_tutorial_nudge(self) -> None:




        try:
            settings = QSettings()
            if settings.value("AIEdit/tutorial_simple_shown", False, type=bool):
                return
            settings.setValue("AIEdit/tutorial_simple_shown", True)
        except Exception:  # nosec B110
            return
        try:
            from qgis.core import Qgis

            from ...core.auth.activation_manager import get_tutorial_url
            message = '{} <a href="{}">{}</a>'.format(
                tr("New here?"),
                get_tutorial_url(),
                tr("Watch the tutorial"),
            )
            self._iface.messageBar().pushMessage(
                "AI Edit", message, level=Qgis.MessageLevel.Info, duration=10
            )
        except Exception:  # nosec B110
            pass

    def _on_retry(self, prompt: str):


        if not self._selected_extent:
            self._dock_widget.set_status(
                tr("No zone selected"), is_error=True
            )
            return


        self._on_generate(prompt, is_retry=True)

    def _show_markup_hidden_bar(self, prompt: str, is_retry: bool) -> None:



        telemetry.track(te.MARKUP_HIDDEN_WARNED, {})
        bar = self._iface.messageBar()
        widget = bar.createMessage(
            tr("Your drawing won't be used"), tr("The AI Edit drawing layer is hidden.")
        )
        show_button = QPushButton(tr("Show it and generate"))
        without_button = QPushButton(tr("Generate without it"))
        widget.layout().addWidget(show_button)
        widget.layout().addWidget(without_button)

        def _resolve(choice: str):
            telemetry.track(te.MARKUP_HIDDEN_RESOLVED, {"choice": choice})
            bar.popWidget(widget)
            manager = self._markup_manager
            if choice == "show_and_generate":
                shown = manager.show_layer() if manager is not None else False
                if not shown and manager is not None:



                    manager.clear_all()
            else:
                if manager is not None:
                    manager.clear_all()
            self._on_generate(prompt, is_retry=is_retry)

        show_button.clicked.connect(lambda: _resolve("show_and_generate"))
        without_button.clicked.connect(lambda: _resolve("generate_without"))
        bar.pushWidget(widget, Qgis.MessageLevel.Warning)

    def _on_generate(self, prompt: str, is_retry: bool = False):
        if self._worker is not None and self._worker.is_active():
            self._dock_widget.set_status(tr("Generation already in progress"), is_error=True)
            return
        if self._export_worker is not None and self._export_worker.is_active():

            return
        if self._size_request_token is not None:

            return
        if not self._selected_extent:
            self._dock_widget.set_status(tr("No zone selected"), is_error=True)
            return




        if not self._require_privacy_notice(
            lambda: self._on_generate(prompt, is_retry=is_retry)
        ):
            return


        self._disarm_swipe()


        self._promote_selected_version()




        if not has_consent():
            save_consent()


        if not has_server_config():
            self._dock_widget.set_status(
                tr(
                    "Cannot generate: export config not loaded from server. "
                    "Check your internet connection and restart QGIS."
                ),
                is_error=True
            )
            return


        if not has_tuned_config():
            self._refresh_tuned_config()
            self._dock_widget.set_status(
                tr("Getting your settings from the server. Press Generate again in a few seconds."),
                is_error=False,
            )
            return



        from ...core.generation.pipeline_context import PipelineContext

        ctx = PipelineContext()




        base_version = (
            self._versions[self._selected_version_index]
            if 0 <= self._selected_version_index < len(self._versions)
            else None
        )
        base_layer_id = base_version["layer_id"] if base_version else None
        ctx.parent_request_id = base_version["request_id"] if base_version else None

        ctx.session_id = self._session_id




        armed = self._dock_widget.get_active_template()
        match = armed or lookup_template_by_prompt(prompt)
        if match:
            ctx.template_id, ctx.template_name = match
            ctx.vector_color, ctx.vector_classes = get_vector_hints(ctx.template_id)
        else:



            ctx.vector_color = detect_freeform_vector_intent(prompt)


        ctx.seg_intent = detect_seg_context(prompt)



        suggested_res = coerce_tier(
            self._dock_widget.get_selected_resolution(),
            self._dock_widget._is_free_tier,
        )





        markup_layer = None
        if self._markup_manager is not None and self._markup_manager.annotation_count() > 0:
            try:
                markup_layer = self._markup_manager.layer()
            except RuntimeError:
                markup_layer = None






        if markup_layer is not None and not self._markup_manager.markup_will_render():
            self._show_markup_hidden_bar(prompt, is_retry)
            return






        from qgis.core import QgsProject

        base_layer = None
        if base_layer_id:
            base_layer = QgsProject.instance().mapLayer(base_layer_id)
        if base_layer is None:
            base_layer = self._dock_widget.selected_input_layer()
        if base_layer is None:
            self._dock_widget.set_status(
                tr("Pick the layer to edit first."), is_error=True
            )
            return


        self._generation_started_from_result = bool(is_retry and self._versions)
        self._dock_widget.set_generating(True)
        self._dock_widget.set_status("")





        self._error_report_dialog_shown = False



        self._headless_run = bool(getattr(self, "_api_run_requested", False))


        self._last_generation_error = ""
        self._last_generation_error_code = ""

        self._request_export_size(prompt, ctx, suggested_res, markup_layer, base_layer, is_retry)

    def _request_export_size(self, prompt, ctx, suggested_res, markup_layer, base_layer, is_retry):







        extent = self._selected_extent
        token = object()
        self._size_request_token = token

        def _continue(size, t=token):
            self._continue_generation(t, size, prompt, ctx, suggested_res, markup_layer, base_layer, is_retry)

        auth = self._auth_manager.get_auth_header()
        if suggested_res:
            inputs = {"ratio": extent.width() / extent.height(), "tier": suggested_res}
        else:
            inputs = native_size_inputs(self._canvas.mapSettings(), extent)
        if not auth or not inputs:
            _continue(None)
            return
        task = GenericRequestTask(
            "AI Edit export size",
            lambda c=self._client, a=auth, i=inputs: c.get_export_size(a, i),
            silent=True,
        )
        task.succeeded.connect(lambda result: _continue(_served_size(result)))
        task.failed.connect(lambda _msg, _code: _continue(None))
        self._export_size_task = task
        QgsApplication.taskManager().addTask(task)

    def _continue_generation(self, token, size, prompt, ctx, suggested_res, markup_layer, base_layer, is_retry):


        if token is not self._size_request_token or self._dock_widget is None:
            return
        self._size_request_token = None
        self._export_size_task = None
        if not self._selected_extent:
            self._dock_widget.set_generating(False)
            return





        try:
            map_settings = self._canvas.mapSettings()
            render_layers = build_input_render_set(base_layer, markup_layer)




            prep = prepare_export(
                map_settings,
                self._selected_extent,
                target_resolution=suggested_res,
                markup_layer=markup_layer,
                layers=render_layers,
                size=size,
            )
        except Exception as e:
            self._dock_widget.set_generating(False)
            self._restore_failed_iteration()
            msg = tr("Could not capture your zone: {error}").format(error=e)
            self._dock_widget.set_status(msg, is_error=True)
            telemetry.track(
                te.EXPORT_FAILED,
                build_failure_props("export", "canvas_export_failed", str(e)),
            )
            telemetry.flush()
            self._show_error_report(msg)
            return


        from ..layer_renderer import input_layer_kind

        self._pending_generation = {
            "prompt": prompt,
            "ctx": ctx,
            "prep": prep,
            "suggested_res": suggested_res,
            "crs_wkt": map_settings.destinationCrs().toWkt(),
            "is_retry": is_retry,
            "input_layer_kind": input_layer_kind(base_layer),
        }

        worker = ExportWorker(prep)
        worker.completed.connect(self._on_export_completed)
        worker.failed.connect(self._on_export_failed)




        worker.completed.connect(lambda *_a, w=worker: self._cleanup_export_worker(w))
        worker.failed.connect(lambda *_a, w=worker: self._cleanup_export_worker(w))


        worker.taskTerminated.connect(
            lambda w=worker: self._on_export_task_terminated(w)
        )
        self._export_worker = worker
        QgsApplication.taskManager().addTask(worker)

    def _cleanup_export_worker(self, worker):
        if self._export_worker is worker:
            self._export_worker = None

    def _on_export_task_terminated(self, worker):





        if self._export_worker is not worker or self._pending_generation is None:
            return
        try:
            cancelled = worker.isCanceled()
        except RuntimeError:
            cancelled = True
        if not cancelled:
            return
        pending = self._pending_generation
        self._pending_generation = None
        self._export_worker = None
        with teardown_step("export cancel dock unlock", stage="generation"):
            self._dock_widget.set_generating(False)
            self._restore_failed_iteration()
            self._dock_widget.set_status(tr("Generation cancelled"))
        with teardown_step("export cancel telemetry", stage="generation"):
            telemetry.track(te.GENERATION_CANCELLED, self._enrich_generation_props({
                "duration_ms": 0,
                "resolution": pending.get("suggested_res", ""),
                "phase": "export",
            }))
            telemetry.flush()

    def _on_export_failed(self, error_msg: str):
        if self._pending_generation is None:


            return
        self._pending_generation = None
        self._dock_widget.set_generating(False)
        self._restore_failed_iteration()
        msg = tr("Could not capture your zone: {error}").format(error=error_msg)
        self._dock_widget.set_status(msg, is_error=True)
        telemetry.track(
            te.EXPORT_FAILED,
            build_failure_props("export", "canvas_export_failed", error_msg),
        )
        telemetry.flush()
        self._show_error_report(msg)

    def _on_export_completed(
        self,
        image_b64: str,
        img_w: int,
        img_h: int,
        actual_extent,
        size_bytes: int,
        input_format: str,
        guidance_b64: str = "",
        guidance_format: str = "",
    ):
        pending = self._pending_generation
        self._pending_generation = None
        if pending is None:

            return




        try:
            self._start_generation_from_export(
                pending, image_b64, img_w, img_h, actual_extent,
                size_bytes, input_format, guidance_b64, guidance_format,
            )
        except Exception as err:  # noqa: BLE001
            self._recover_from_handoff_failure(err)

    def _recover_from_handoff_failure(self, err: Exception) -> None:











        try:
            detail = str(err)
        except Exception:  # noqa: BLE001
            detail = type(err).__name__
        log_warning(f"Generation hand-off failed after export: {detail}")
        with teardown_step("hand-off worker cleanup", stage="generation"):
            self._cleanup_worker()
        with teardown_step("hand-off map tool unlock", stage="generation"):
            if self._map_tool:
                self._map_tool.set_locked(False)
        with teardown_step("hand-off dock unlock", stage="generation"):
            self._dock_widget.set_generating(False)
            self._restore_failed_iteration()
        msg = tr("Could not start the generation: {error}").format(error=detail)
        with teardown_step("hand-off status", stage="generation"):
            self._dock_widget.set_status(msg, is_error=True)
        with teardown_step("hand-off telemetry", stage="generation"):
            telemetry.track(te.PLUGIN_ERROR, {
                "stage": "generation",
                "error_code": "export_handoff_failed",
            })
            telemetry.flush()
        self._show_error_report(msg)

    def _start_generation_from_export(
        self,
        pending: dict,
        image_b64: str,
        img_w: int,
        img_h: int,
        actual_extent,
        size_bytes: int,
        input_format: str,
        guidance_b64: str = "",
        guidance_format: str = "",
    ):
        ctx = pending["ctx"]
        prep = pending["prep"]
        prompt = pending["prompt"]
        suggested_res = pending["suggested_res"]
        crs_wkt = pending["crs_wkt"]
        is_retry = pending.get("is_retry", False)

        log_debug(
            f"Export completed: main_b64={len(image_b64)}, "
            f"guidance_b64={len(guidance_b64)}, "
            f"guidance_format={guidance_format or '-'}, "
            f"used_markup={bool(guidance_b64)}"
        )







        apply_export_context(
            ctx, prep, actual_extent, size_bytes, input_format,
            zone_polygon=self._selected_polygon,
        )



        self._dock_widget.prep_advance_phase("upload")




        aspect_ratio = "auto"
        ctx.aspect_ratio = aspect_ratio



        extent_dict = {
            "xmin": actual_extent.xMinimum(),
            "ymin": actual_extent.yMinimum(),
            "xmax": actual_extent.xMaximum(),
            "ymax": actual_extent.yMaximum(),
        }
        output_dir = get_output_dir()





        self._selected_extent = actual_extent
        self._show_selection_rectangle(actual_extent, self._selected_polygon)







        self._last_image_b64 = image_b64
        self._last_guidance_b64 = guidance_b64 or None
        self._last_guidance_format = guidance_format or None
        self._last_suggested_res = suggested_res






        if not self._versions:
            self._versions.append({"layer_id": None, "request_id": None, "prompt": ""})
            self._selected_version_index = 0
            pixmap = self._pixmap_from_b64(guidance_b64 or image_b64)



            from ...core.prompts import conversation_thumbs

            if self._session_id:
                conversation_thumbs.save_thumb(f"in-{self._session_id}", pixmap)
            try:
                self._dock_widget.seed_version_strip(pixmap)
            except AttributeError:
                pass






        if self._map_tool:
            self._map_tool.set_locked(True)


        self._generation_service.reset()
        self._generation_start_time = time.time()
        self._last_generation_is_retry = is_retry
        used_markup = bool(guidance_b64)
        self._last_generation_used_markup = used_markup
        log(f"Generation started: prompt_len={len(prompt)}, resolution={suggested_res}, zone={img_w}x{img_h}px")

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        from ...workers.generation_worker import GenerationWorker

        self._worker = GenerationWorker(
            client=self._client,
            auth_manager=self._auth_manager,
            service=self._generation_service,
            image_b64=image_b64,
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            extent_dict=extent_dict,
            crs_wkt=crs_wkt,
            output_dir=output_dir,
            ctx=ctx,
            debug_mode=self._dev_mode,
            plugin_dir=plugin_dir,
            skip_trial_check=self._skip_trial_check,
            suggested_resolution=suggested_res,
            context_image_paths=self._reference_store.snapshot_paths(),
            context_image_notes=self._reference_store.snapshot_notes(),
            guidance_image=guidance_b64 or None,
            guidance_format=guidance_format or None,
        )
        self._worker.succeeded.connect(self._on_generation_finished)
        self._worker.progress.connect(self._on_generation_progress)
        self._worker.failed.connect(self._on_generation_error)


        self._worker.taskTerminated.connect(self._on_generation_task_terminated)


        self._generation_cancel_handled = False
        QgsApplication.taskManager().addTask(self._worker)






        try:
            telemetry.track(te.GENERATION_STARTED, self._enrich_generation_props({
                "prompt_length": len(prompt),
                "aspect_ratio": aspect_ratio,
                "resolution": suggested_res,
                "zone_width_px": img_w,
                "zone_height_px": img_h,
                "input_image_bytes": size_bytes,
                "input_image_format": input_format,
                "is_retry": is_retry,
                "has_geo_context": self._reference_store.count() > 0,
                "template_id": ctx.template_id,
                "template_name": ctx.template_name,
                "used_template": bool(ctx.template_id),
                "used_markup": used_markup,
                "input_layer_kind": pending.get("input_layer_kind", "other"),
            }))
        except Exception as err:  # noqa: BLE001
            log_warning(f"generation_started telemetry failed: {err}")
