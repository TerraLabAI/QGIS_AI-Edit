from __future__ import annotations

import os
import time

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QSettings

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import has_consent, save_consent
from ...core.generation.pipeline_context import PipelineContext
from ...core.i18n import tr
from ...core.logger import log, log_debug
from ...core.prompts.prompt_presets import (
    detect_freeform_vector_intent,
    detect_seg_context,
    get_vector_hints,
    lookup_template_by_prompt,
)
from ...workers.export_worker import ExportWorker
from ...workers.generation_worker import GenerationWorker
from ..canvas_exporter import apply_export_context, has_server_config, prepare_export
from ..raster_writer import get_output_dir
from .errors import _scrub_paths


class GenerationMixin:
    @staticmethod
    def _days_since_activation() -> int | None:
        """Cohort prop. None for legacy users without a stored timestamp."""
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
        """QgsTask.taskTerminated fires on cancel OR normal failure. Normal
        failures already emit `failed` (handled by _on_generation_error), so
        act only on a real cancellation, and only when the plugin itself did
        not start it (Stop/Exit already recover the UI and log the event).
        This catches the native QGIS task-manager Cancel button, which would
        otherwise leave the dock stuck on 'generating'."""
        worker = self._worker
        if worker is None or not worker.isCanceled():
            return
        if self._generation_cancel_handled:
            self._generation_cancel_handled = False
            return
        # User cancelled from the task widget: not our fault, so no refund (the
        # job keeps running server-side and lands in Recent). Just log it and
        # return the dock to a usable state.
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
        self._cleanup_worker()
        self._clear_markup_layer()

    def _maybe_emit_first_generation_milestone(self):
        """One-shot event when the user completes their first successful generation.

        Persisted via QSettings so it never re-fires across QGIS sessions.
        """
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
        # Force-flush so a crash doesn't lose the flag and re-fire the milestone.
        try:
            settings.sync()
        except Exception:  # nosec B110
            pass
        self._first_generation_milestone_emitted = True

    def _maybe_show_tutorial_nudge(self) -> None:
        """First real commitment (first Launch/Generate click, or first zone
        drawn): a one-time ~10s message-bar nudge pointing new users at the
        video tutorial. Persisted once-ever in QSettings so it never nags again.
        The link opens even when telemetry is off (QGIS handles the click)."""
        try:
            settings = QSettings()
            if settings.value("AIEdit/tutorial_simple_shown", False, type=bool):
                return
            settings.setValue("AIEdit/tutorial_simple_shown", True)
        except Exception:  # nosec B110 - never break the flow on a settings error
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
        except Exception:  # nosec B110 - a nudge must never break the flow
            pass

    def _on_retry(self, prompt: str):
        """Retry on same zone: re-export the current canvas view (includes generated layers)."""
        if not self._selected_extent:
            self._dock_widget.set_status(
                tr("Cannot retry: no zone selected."), is_error=True
            )
            return
        # Carry the retry flag through the export hand-off so
        # generation_started/completed/failed are all tagged is_retry=True.
        self._on_generate(prompt, is_retry=True)

    def _on_generate(self, prompt: str, is_retry: bool = False):
        if self._worker is not None and self._worker.is_active():
            self._dock_widget.set_status(tr("Generation already in progress"), is_error=True)
            return
        if self._export_worker is not None and self._export_worker.is_active():
            # Click landed while a previous render is still in flight - swallow.
            return
        if not self._selected_extent:
            self._dock_widget.set_status(tr("No zone selected"), is_error=True)
            return
        # Launching a new generation is a clear "I am done comparing"
        # signal: drop the swipe overlay so the canvas renders fresh.
        self._disarm_swipe()

        # Save consent on first generation and hide checkbox
        if not has_consent():
            save_consent()
            self._dock_widget.hide_consent()

        # Ensure server config is loaded before generation
        if not has_server_config():
            self._dock_widget.set_status(
                tr(
                    "Cannot generate: export config not loaded from server. "
                    "Check your internet connection and restart QGIS."
                ),
                is_error=True
            )
            return

        ctx = PipelineContext()
        # Base picked in the version strip. Index 0 (Original) rebuilds from the
        # clean map (every AI result dropped from the export below) and is not an
        # iteration; any generated version keeps only that version in the export
        # and anchors the next edit on its request id.
        base_version = (
            self._versions[self._selected_version_index]
            if 0 <= self._selected_version_index < len(self._versions)
            else None
        )
        base_layer_id = base_version["layer_id"] if base_version else None
        ctx.parent_request_id = base_version["request_id"] if base_version else None
        # Same continuous flow on this zone = same session.
        ctx.session_id = self._session_id

        # Tag the job with template_id. The armed template (set when the
        # user picked a preset) wins so prompt edits keep the association;
        # fall back to exact text match for prompts loaded any other way.
        armed = self._dock_widget.get_active_template()
        match = armed or lookup_template_by_prompt(prompt)
        if match:
            ctx.template_id, ctx.template_name = match
            ctx.vector_color, ctx.vector_classes = get_vector_hints(ctx.template_id)
        else:
            # No preset matched. Still light up the Vectorize CTA when the
            # free-form prompt asks to segment, detect, or vectorize one
            # feature type without naming colors (server paints #FF0000).
            ctx.vector_color = detect_freeform_vector_intent(prompt)
        # Broader signal for the worker's flat-output sniff (manual land
        # cover / color-classification prompts get a relaxed threshold).
        ctx.seg_intent = detect_seg_context(prompt)

        if self._dock_widget._is_free_tier:
            suggested_res = "1K"
        else:
            suggested_res = self._dock_widget.get_selected_resolution()

        # Lock UI; prep ticker animates while export+upload run off-thread.
        self._dock_widget.set_generating(True)
        self._dock_widget.set_status("")

        # When the user drew markup, the marks are rendered directly onto the
        # MAIN image (co-located guidance), and the same zone WITHOUT the marks
        # is sent as a clean base so the model can restore the pixels under each
        # mark. With no markup, behave exactly as before (single clean render).
        markup_layer = None
        if self._markup_manager is not None and self._markup_manager.annotation_count() > 0:
            try:
                markup_layer = self._markup_manager.layer()
            except RuntimeError:
                markup_layer = None

        # Pick the base by excluding every AI-Edit result from the EXPORT except
        # the selected version, so the model sees exactly that base. Original
        # (base_layer_id None) drops them all for the clean map. The active Mark
        # up layer is kept (user guidance, not an AI edit). prepare_export
        # filters a clone, so on-screen layers and the just-generated image are
        # never hidden.
        from ..layer_groups import collect_ai_edit_layer_ids

        exclude_layer_ids = collect_ai_edit_layer_ids()
        if base_layer_id:
            exclude_layer_ids.discard(base_layer_id)
        if markup_layer is not None:
            exclude_layer_ids.discard(markup_layer.id())

        try:
            map_settings = self._canvas.mapSettings()
            # Always export the input at the chosen resolution (1K/2K/4K). The
            # model only ever works at those sizes, so sending the full native
            # zone is pointless: a big Google Satellite selection would balloon
            # into tens of MB, stall the upload, and gain nothing.
            prep = prepare_export(
                map_settings,
                self._selected_extent,
                target_resolution=suggested_res,
                markup_layer=markup_layer,
                exclude_layer_ids=exclude_layer_ids,
            )
        except Exception as e:
            self._dock_widget.set_generating(False)
            msg = tr("Export error: {error}").format(error=e)
            self._dock_widget.set_status(msg, is_error=True)
            telemetry.track(te.EXPORT_FAILED, {
                "stage": "export",
                "error_code": "canvas_export_failed",
                "error_message": _scrub_paths(str(e))[:200],
            })
            telemetry.flush()
            self._show_error_report(msg)
            return

        # Hand off everything the export-completed callback needs.
        self._pending_generation = {
            "prompt": prompt,
            "ctx": ctx,
            "prep": prep,
            "suggested_res": suggested_res,
            "crs_wkt": map_settings.destinationCrs().toWkt(),
            "is_retry": is_retry,
        }

        worker = ExportWorker(prep)
        worker.completed.connect(self._on_export_completed)
        worker.failed.connect(self._on_export_failed)
        # Drop our reference once the task ends (runs after the handlers above;
        # the identity guard keeps a late signal from nulling a newer worker).
        # Otherwise the finished task and its ExportPrep render payload stay
        # alive until the next generation.
        worker.completed.connect(lambda *_a, w=worker: self._cleanup_export_worker(w))
        worker.failed.connect(lambda *_a, w=worker: self._cleanup_export_worker(w))
        self._export_worker = worker
        QgsApplication.taskManager().addTask(worker)

    def _cleanup_export_worker(self, worker):
        if self._export_worker is worker:
            self._export_worker = None

    def _on_export_failed(self, error_msg: str):
        if self._pending_generation is None:
            # User cancelled / dock was torn down before the render finished; a
            # late export-failure signal must not paint an error over LAUNCH.
            return
        self._pending_generation = None
        self._dock_widget.set_generating(False)
        msg = tr("Export error: {error}").format(error=error_msg)
        self._dock_widget.set_status(msg, is_error=True)
        telemetry.track(te.EXPORT_FAILED, {
            "stage": "export",
            "error_code": "canvas_export_failed",
            "error_message": _scrub_paths(error_msg)[:200],
        })
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
            # User cancelled / dock was torn down before the render finished.
            return

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

        apply_export_context(ctx, prep, actual_extent, size_bytes, input_format)

        # Canvas captured: advance the prep ticker to the upload phase so the
        # message pool reflects what's actually happening next (sending bytes).
        self._dock_widget.prep_advance_phase("upload")

        # Use "auto" so the model preserves the input image dimensions.
        # Explicit ratios (e.g. "21:9") cause the model to reshape the output,
        # which creates alignment issues with the source imagery.
        aspect_ratio = "auto"
        ctx.aspect_ratio = aspect_ratio

        # Use the actual rendered extent (QGIS may adjust it to match the
        # output pixel aspect ratio). This prevents image stretching.
        extent_dict = {
            "xmin": actual_extent.xMinimum(),
            "ymin": actual_extent.yMinimum(),
            "xmax": actual_extent.xMaximum(),
            "ymax": actual_extent.yMaximum(),
        }
        output_dir = get_output_dir()

        # Update rubber band to match the actual rendered extent
        self._selected_extent = actual_extent
        self._show_selection_rectangle(actual_extent)

        # ``guidance_b64`` here is the clean base (the zone with the marks
        # removed); the marks ride on ``image_b64``. The server is told via the
        # marks_on_input flag to restore the pixels under each mark from it, so
        # no stroke appears in the result.

        # Preserve original zone for retry (never chain from AI result)
        self._last_image_b64 = image_b64
        self._last_guidance_b64 = guidance_b64 or None
        self._last_guidance_format = guidance_format or None
        self._last_input_format = input_format
        self._last_input_bytes = size_bytes
        self._last_extent_dict = extent_dict
        self._last_crs_wkt = crs_wkt
        self._last_aspect_ratio = aspect_ratio
        self._last_suggested_res = suggested_res

        # Seed the version strip's Original tile from the very first export of
        # this lineage - that render is the clean zone before any AI edit. Later
        # exports (iterations) skip this; the Original is captured once. When
        # markup was drawn, prefer the clean base so the Original tile shows the
        # unmarked zone rather than the strokes.
        if not self._versions:
            self._versions.append({"layer_id": None, "request_id": None, "prompt": ""})
            self._selected_version_index = 0
            pixmap = self._pixmap_from_b64(guidance_b64 or image_b64)
            try:
                self._dock_widget.seed_version_strip(pixmap)
            except AttributeError:
                pass

        # Keep the markup layer alive through the generation so the marks stay
        # visible while the model works. It is dropped when the generation ends
        # (_on_generation_finished / _on_generation_error / cancel) so the
        # result shows clean and the temporary layer never piles up.

        if self._map_tool:
            self._map_tool.set_locked(True)
        # set_generating(True) already called at click time. Don't call again
        # here or we'd reset the prep ticker phase + bar back to 1%.
        self._generation_service.reset()
        self._generation_start_time = time.time()
        self._last_generation_is_retry = is_retry
        used_markup = bool(guidance_b64)
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
        }))
        self._last_generation_used_markup = used_markup
        log(f"Generation started: prompt_len={len(prompt)}, resolution={suggested_res}, zone={img_w}x{img_h}px")

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

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
            guidance_image=guidance_b64 or None,
            guidance_format=guidance_format or None,
        )
        self._worker.succeeded.connect(self._on_generation_finished)
        self._worker.progress.connect(self._on_generation_progress)
        self._worker.failed.connect(self._on_generation_error)
        # Recover the dock if the job is cancelled from the native QGIS
        # task-manager widget (which never routes through Stop/Exit).
        self._worker.taskTerminated.connect(self._on_generation_task_terminated)
        # Fresh run: clear the guard so that cancel is recovered (Stop/Exit set
        # it to suppress the duplicate recovery they already perform).
        self._generation_cancel_handled = False
        QgsApplication.taskManager().addTask(self._worker)
