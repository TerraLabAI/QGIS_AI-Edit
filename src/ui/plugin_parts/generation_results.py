from __future__ import annotations

import time

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import get_dashboard_url, get_server_config
from ...core.i18n import tr
from ...core.logger import log, log_warning
from ...core.prompts import prompt_history
from ..dialogs.error_report_dialog import REPORT_PROBLEM_HREF
from ..raster_writer import add_geotiff_to_project, get_output_dir
from .errors import (
    _CREDIT_REASSURE_CODES,
    SUBSCRIBE_ERROR_URL,
    _enrich_error_message,
    _failure_stage,
    _is_model_failure,
    _is_service_busy,
    _report_policy,
    _resolve_class_label,
    _scrub_paths,
)


class GenerationResultsMixin:
    def _on_generation_progress(self, status: str, percentage: int):
        self._dock_widget.set_progress_message(status, percentage)

    def _on_generation_error(self, message: str, code: str, ctx_snapshot: dict | None = None):
        if self._map_tool:
            self._map_tool.set_locked(False)
        self._dock_widget.set_generating(False)
        # Template metadata arrives in the ctx_snapshot dict copied off the
        # worker thread (C2). Read it before cleanup so generation_failed is
        # segmentable by template in telemetry.
        snap = ctx_snapshot or {}
        template_id = snap.get("template_id")
        template_name = snap.get("template_name")
        self._cleanup_worker()
        # Generation ended (with an error): drop the markup layer so it does not
        # linger or accumulate. A retry re-sends the cached marked image.
        self._clear_markup_layer()
        normalized_code = (code or "").strip().upper()
        message_lower = (message or "").lower()
        quota_codes = {
            "QUOTA_EXCEEDED",
            "LIMIT_REACHED",
            "USAGE_LIMIT_REACHED",
            "MONTHLY_LIMIT_REACHED",
        }
        is_quota_error = normalized_code in quota_codes or "monthly limit reached" in message_lower
        duration = time.time() - getattr(self, "_generation_start_time", time.time())
        # error_code must never be empty: the polling path returns a bare
        # status=failed (model could not produce an image) with no code.
        effective_code = (code or "").strip() or "model_failure"
        extra_props: dict = {
            "error_code": effective_code,
            "stage": _failure_stage(normalized_code),
            "is_retry": self._last_generation_is_retry,
            "duration_ms": int(duration * 1000),
            "resolution": getattr(self, "_last_suggested_res", ""),
            "template_id": template_id,
            "template_name": template_name,
            "used_template": bool(template_id),
        }
        # WRITE_ERROR is ~90% Windows-path; surface enough to triage the sub-class.
        if normalized_code == "WRITE_ERROR":
            try:
                import sys as _sys
                output_dir = get_output_dir() or ""
                extra_props.update({
                    "os": _sys.platform,
                    "output_dir_len": len(output_dir),
                    "output_dir_has_unicode": not output_dir.isascii(),
                    "output_dir_has_spaces": " " in output_dir,
                    "exception_msg": _scrub_paths((message or "")[:200]),
                })
            except Exception:  # nosec B110
                pass
        # Running out of credits is a monetization outcome, not a product
        # failure: it ships as TRIAL_EXHAUSTED_VIEWED below. Emitting it as
        # generation_failed too made healthy releases read as regressions.
        if normalized_code != "TRIAL_EXHAUSTED" and not is_quota_error:
            telemetry.track(te.GENERATION_FAILED, self._enrich_generation_props(extra_props))
            telemetry.flush()
        if normalized_code == "TRIAL_EXHAUSTED":
            # Cache-only (no client): pre-warmed off-thread at startup, so this
            # never blocks the UI thread. Falls back to the default upgrade URL.
            config = get_server_config()
            dashboard = config.get("upgrade_url", get_dashboard_url())
            self._dock_widget.show_trial_exhausted_info(message, dashboard)
            telemetry.track(te.TRIAL_EXHAUSTED_VIEWED, {"is_free_tier": True})
            # The user typically heads to the browser to subscribe next; ship
            # now so the batch is not lost with the session.
            telemetry.flush()
        elif is_quota_error:
            self._dock_widget.show_usage_limit_info(message, SUBSCRIBE_ERROR_URL)
            telemetry.track(te.TRIAL_EXHAUSTED_VIEWED, {"is_free_tier": False})
            # The user typically heads to the browser to subscribe next; ship
            # now so the batch is not lost with the session.
            telemetry.flush()
        elif _is_model_failure(message, normalized_code):
            # The model couldn't produce an image (no-output / safety block). The
            # server already marked the job failed and refunded the credit, so we
            # never show the raw provider error or open the bug-report dialog:
            # reassure the user (not charged) and tell them what to try instead.
            if "block" in (message or "").lower() or "safety" in (message or "").lower():
                enriched = tr(
                    "Generation failed: the request was blocked by a safety filter. "
                    "You have not been charged. Try rephrasing your prompt."
                )
            else:
                enriched = tr(
                    "Generation failed: the AI couldn't create an image for this "
                    "request. You have not been charged. Try rephrasing your prompt, "
                    "or pick a different zone."
                )
            self._dock_widget.set_status(enriched, is_error=True)
        elif _is_service_busy(message, normalized_code):
            # Servers momentarily overloaded; user not charged. Calm inline retry,
            # never the bug-report dialog (nothing for the user to report).
            enriched = tr(
                "Our image servers are busy right now. You have not been charged. "
                "Please wait a moment and try again."
            )
            self._dock_widget.set_status(enriched, is_error=True)
        else:
            enriched = _enrich_error_message(message, code)
            # Reassure on EVERY credit-safe failure that no credit was kept (the
            # server refunds failed jobs; pre-charge errors never charged).
            if normalized_code in _CREDIT_REASSURE_CODES:
                enriched = f"{enriched} {tr('No credit was used.')}"
            request_id = snap.get("request_id") or ""
            policy = _report_policy(normalized_code)
            if policy == "link":
                # Transient/our-side: clean inline message + an OPTIONAL log link.
                # We never force a modal for something the user just retries.
                report_link = (
                    f'<a href="{REPORT_PROBLEM_HREF}">{tr("Report a problem")}</a>'
                )
                self._dock_widget.arm_report_context(request_id)
                self._dock_widget.set_status(f"{enriched} {report_link}", is_error=True)
            elif policy == "dialog":
                # Likely a genuine bug: surface it and proactively offer to send
                # the log so we hear about it.
                self._dock_widget.set_status(enriched, is_error=True)
                self._show_error_report(enriched, request_id)
            else:
                # User-fixable (network, key, zone, plan): plain inline message.
                self._dock_widget.set_status(enriched, is_error=True)
        log_warning(f"Generation failed: {message} (code={code})")

    def _show_error_report(self, error_message: str, request_id: str = "") -> None:
        """Open the copy-logs/email report dialog. A failure here must never
        mask the original error, so it is swallowed (and logged)."""
        try:
            from ..dialogs.error_report_dialog import show_error_report
            show_error_report(self._iface.mainWindow(), error_message, request_id)
        except Exception as err:  # nosec B110
            log_warning(f"Could not open error report dialog: {err}")

    def _on_generation_finished(self, result_info: dict):
        if self._map_tool:
            self._map_tool.set_locked(False)
        # result_info already holds the ctx snapshot copied off the worker.
        self._last_completed_request_id = result_info.get("request_id")
        vector_color: str | None = result_info.get("vector_color")
        vector_classes: list[dict] | None = result_info.get("vector_classes")
        template_id: str | None = result_info.get("template_id")
        template_name: str | None = result_info.get("template_name")
        self._cleanup_worker()
        # Generation is over: drop the markup layer so the result shows clean
        # and the temporary layer does not accumulate. The marks stayed visible
        # for the whole run.
        self._clear_markup_layer()
        duration = time.time() - getattr(self, "_generation_start_time", time.time())

        completed_emitted = False
        try:
            layer = add_geotiff_to_project(
                result_info["geotiff_path"],
                result_info.get("prompt", ""),
                crs_wkt=result_info.get("crs_wkt", ""),
            )
            try:
                self._iface.setActiveLayer(layer)
            except Exception as err:  # nosec B110
                log_warning(f"setActiveLayer failed: {err}")
            prompt_history.add_recent(result_info.get("prompt", ""))
            # New result exists server-side now, so the library's session cache
            # of Recent/Favorites must refetch the next time it opens.
            if self._dock_widget is not None:
                self._dock_widget.mark_library_history_dirty()
            telemetry.track(te.GENERATION_COMPLETED, self._enrich_generation_props({
                "duration_ms": int(duration * 1000),
                "resolution": getattr(self, "_last_suggested_res", ""),
                "is_retry": self._last_generation_is_retry,
                "used_markup": self._last_generation_used_markup,
                "output_rescued": bool(result_info.get("output_rescued")),
                "template_id": template_id,
                "template_name": template_name,
                "used_template": bool(template_id),
            }))
            telemetry.flush()
            completed_emitted = True
            self._maybe_emit_first_generation_milestone()
            self._dock_widget.set_generation_complete(layer.name(), layer.id())
            # Append this result to the lineage and let the strip show + select
            # it. The Original tile was seeded at export time, so by now the
            # strip already holds at least the Original.
            result_prompt = result_info.get("prompt", "")
            # The base this result was generated from is the version that was
            # selected when generation started (still current until we append).
            base_index = self._selected_version_index
            base_label = tr("Original") if base_index <= 0 else f"V{base_index}"
            self._versions.append({
                "layer_id": layer.id(),
                "request_id": self._last_completed_request_id,
                "prompt": result_prompt,
            })
            self._selected_version_index = len(self._versions) - 1
            thumb = self._render_layer_thumb(layer)
            # Metadata surfaced in the version-details dialog: the definition the
            # user picked and whether a prompt template shaped this run.
            try:
                dims = f"{layer.width()} × {layer.height()}"
            except Exception:  # nosec B110 - dimensions are cosmetic only.
                dims = None
            version_meta = {
                "definition": getattr(self, "_last_suggested_res", "") or "",
                "dimensions": dims,
                "template_name": template_name,
                "base_label": base_label,
            }
            try:
                self._dock_widget.add_version_thumb(thumb, result_prompt, version_meta)
            except AttributeError:
                pass
            flat_classes = result_info.get("flat_classes") or None
            cta_trigger = ""
            if vector_color or vector_classes:
                cta_trigger = "template" if template_id else "freeform_verb"
            elif flat_classes:
                # No template or prompt hint, but the output itself is a small
                # set of flat color zones (land-cover / segmentation look,
                # detected worker-side): suggest vectorizing anyway with the
                # main foreground class pre-filled.
                from ...core.vectorize_detect import pick_foreground_color

                vector_color = pick_foreground_color(flat_classes)
                cta_trigger = "flat_output"
            class_label = _resolve_class_label(vector_color, vector_classes)
            detected_colors = (
                [c for c, _share in flat_classes] if flat_classes else None
            )
            if detected_colors is None and isinstance(vector_classes, list):
                # Multi-class template: show its palette on the CTA card.
                detected_colors = [
                    e.get("color")
                    for e in vector_classes
                    if isinstance(e, dict) and e.get("color")
                ] or None
            self._dock_widget.set_vectorize_suggestion(
                layer.id(),
                vector_color,
                class_label,
                detected_colors=detected_colors,
                trigger=cta_trigger,
            )
            if vector_color:
                telemetry.track(te.VECTORIZE_HINT_SHOWN, {
                    "trigger": cta_trigger,
                    "n_colors": len(flat_classes or []),
                })
            # Surface the next action on the canvas, beside the × badge:
            # Compare whenever a before/after is possible, Vectorize when the
            # run produced a vectorizable result (same signal that drives the
            # dock CTA above).
            self._vectorize_suggestion = (
                layer.id(), vector_color, class_label, cta_trigger
            )
            self._pills_armed = True
            self._show_action_pills()
            self._refresh_credits()
            log(f"Generation complete ({round(duration, 1)}s): {result_info['geotiff_path']}")
        except Exception as e:
            if completed_emitted:
                # The run was already counted complete and the completed view is
                # already showing (set_generation_complete ran first): a cosmetic
                # post-complete UI step failed. Never re-emit generation_completed,
                # never blame the layer-add (it succeeded), and do NOT call
                # set_generating(False) here - it would hide the finished result
                # and "Saved as" line for a layer already added and billed. Just
                # record the exception and leave the completed view intact.
                telemetry.track(te.PLUGIN_ERROR, {
                    "stage": "write",
                    "error_code": "post_complete_ui",
                    "error_message": _scrub_paths(str(e))[:200],
                })
                telemetry.flush()
                log_warning(f"Post-completion UI step failed: {e}")
                return
            # The generation itself succeeded and was billed; only the local
            # layer-add failed. Still emit generation_completed (with the same
            # props as the success path plus layer_add_failed) so a billed run
            # is never miscounted as a failure, and keep the plugin_error for
            # the write-side diagnosis.
            telemetry.track(te.GENERATION_COMPLETED, self._enrich_generation_props({
                "duration_ms": int(duration * 1000),
                "resolution": getattr(self, "_last_suggested_res", ""),
                "is_retry": self._last_generation_is_retry,
                "used_markup": self._last_generation_used_markup,
                "output_rescued": bool(result_info.get("output_rescued")),
                "template_id": template_id,
                "template_name": template_name,
                "used_template": bool(template_id),
                "layer_add_failed": True,
            }))
            telemetry.track(te.PLUGIN_ERROR, {
                "stage": "write",
                "error_code": "layer_add_failed",
                "error_message": _scrub_paths(str(e))[:200],
            })
            telemetry.flush()
            self._dock_widget.set_generating(False)
            msg = tr("Error adding layer: {error}").format(error=e)
            self._dock_widget.set_status(msg, is_error=True)
            self._show_error_report(msg, result_info.get("request_id") or "")
            log_warning(f"Failed to add layer: {e}")

    def _cleanup_worker(self):
        """Drop our reference to the QgsTask; TaskManager owns its lifetime."""
        if self._worker is None:
            return
        for sig in [
            self._worker.succeeded,
            self._worker.progress,
            self._worker.failed,
            self._worker.taskTerminated,
        ]:
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._worker = None
