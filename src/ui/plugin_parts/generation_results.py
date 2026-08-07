from __future__ import annotations

import time

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import get_wall_url
from ...core.config_store import get_export_dial_list
from ...core.errors import build_failure_props
from ...core.i18n import tr
from ...core.log_scrub import scrub_user_paths as _scrub_paths
from ...core.logger import log, log_warning
from ...core.prompts import history_cache, prompt_history
from ..dialogs.error_report_dialog import REPORT_PROBLEM_HREF
from ..raster_writer import add_geotiff_to_project, get_output_dir
from .errors import (
    _CREDIT_REASSURE_CODES,
    _enrich_error_message,
    _is_model_failure,
    _is_prompt_blocked,
    _is_safety_block,
    _is_service_busy,
    _prompt_blocked_message,
    _report_policy,
    _resolve_class_label,
    subscribe_error_url,
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
        if normalized_code == "GENERATION_CANCELLED":
            # A cancel is never a failure: generation_cancelled was already
            # emitted by the cancel path (Stop/Exit or the task-manager
            # handler), which also recovers the dock. No failure event, no
            # red error status for a stale cancelled result.
            log("Generation cancelled; failure signal ignored")
            return
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
        # Always ships stage + error_code + scrubbed error_message (max 200).
        failure_props = build_failure_props(None, effective_code, message) or {}
        extra_props: dict = {
            **failure_props,
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
            # show_trial_exhausted_info fires TRIAL_EXHAUSTED_VIEWED itself
            # (once per continuous wall state), so a proactive credits
            # refresh showing the same wall later in the session can never
            # double-count it here.
            self._dock_widget.show_trial_exhausted_info(message, get_wall_url())
            # The user typically heads to the browser to subscribe next; ship
            # now so the batch is not lost with the session.
            telemetry.flush()
        elif is_quota_error:
            self._dock_widget.show_usage_limit_info(message, subscribe_error_url())
            telemetry.track(te.TRIAL_EXHAUSTED_VIEWED, {"is_free_tier": False})
            # The user typically heads to the browser to subscribe next; ship
            # now so the batch is not lost with the session.
            telemetry.flush()
        elif _is_prompt_blocked(normalized_code):
            # The server read the prompt and refused to run it. Ahead of the
            # model-failure branch on purpose: the refusal message carries the
            # very words that branch matches on ("blocked", "content policy"),
            # and "try rephrasing" is the wrong advice for a rule. Nothing was
            # charged (the refusal answers the submit call, before the charge).
            self._dock_widget.set_status(
                _prompt_blocked_message(message, normalized_code), is_error=True
            )
        elif _is_model_failure(message, normalized_code):
            # The model couldn't produce an image (no-output / safety block). The
            # server already marked the job failed and refunded the credit, so we
            # never show the raw provider error or open the bug-report dialog:
            # reassure the user (not charged) and tell them what to try instead.
            # Which of the two wordings applies is decided by the same
            # server-extendable list as the failure itself, so a provider that
            # rephrases its refusal does not silently fall to the generic
            # message.
            if _is_safety_block(message):
                enriched = tr(
                    "Generation failed: the request was blocked by a safety filter. "
                    "You have not been charged. Try rephrasing your prompt."
                )
            else:
                # Two thirds of these failures over the 30 days to 2026-07-28
                # were a question or a role prompt typed into the edit box, so
                # the message names what the tool does instead of asking for a
                # blind rephrase.
                enriched = tr(
                    "Generation failed: the AI returned no image. You have not been "
                    "charged. AI Edit draws on the map and cannot answer questions, "
                    "so describe the change you want to see, then try again."
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
            # Union-only server extras: error_policy.credit_reassure_extra.
            if normalized_code in get_export_dial_list(
                "error_policy.credit_reassure_extra",
                _CREDIT_REASSURE_CODES,
                normalize=str.upper,
            ):
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
        mask the original error, so it is swallowed (and logged).

        Auto-opens at most once per generation attempt. The gate is reset in
        ``_on_generate``, when the user commits to an attempt, so every later
        step of that attempt (export failure, hand-off failure, generation
        failure) can still report exactly once; a stray second failure signal
        for the same attempt never stacks a second modal on the first.
        """
        if getattr(self, "_error_report_dialog_shown", False):
            return
        self._error_report_dialog_shown = True
        try:
            from ..dialogs.error_report_dialog import show_error_report
            show_error_report(self._iface.mainWindow(), error_message, request_id)
        except Exception as err:  # nosec B110
            log_warning(f"Could not open error report dialog: {err}")

    def _remember_zone_polygon_for_history(self, request_id: str | None) -> None:
        """Locally-only companion record so history restore can rebuild the
        zone shape later (spec section 7). No-op with no polygon (a plain
        rectangle zone) or no request id (the run never reached the server).
        The polygon itself never leaves this machine (D2): this writes to a
        local cache keyed by request_id, never to a server-synced field."""
        if not request_id or self._selected_polygon is None or self._selected_polygon.isEmpty():
            return
        try:
            canvas_crs = self._canvas.mapSettings().destinationCrs()
            history_cache.save_zone_polygon(
                request_id, self._selected_polygon.asWkt(), canvas_crs.authid()
            )
        except Exception as err:  # nosec B110 - local history enrichment is best-effort.
            log_warning(f"zone polygon history save failed: {err}")

    def _on_generation_finished(self, result_info: dict):
        if self._map_tool:
            self._map_tool.set_locked(False)
        # result_info already holds the ctx snapshot copied off the worker.
        self._last_completed_request_id = result_info.get("request_id")
        self._remember_zone_polygon_for_history(self._last_completed_request_id)
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
                before_path=result_info.get("before_geotiff_path", ""),
            )
            try:
                self._iface.setActiveLayer(layer)
            except Exception as err:  # nosec B110
                log_warning(f"setActiveLayer failed: {err}")
            prompt_history.add_recent(result_info.get("prompt", ""))
            # New result exists server-side now (status flips to completed
            # before the poll returns, and the history route filters on that
            # status, not on archival). Refetch so the Resume rows and the
            # Conversations panel show this generation right away; the dirty
            # mark keeps the Library honest if that refetch fails.
            if self._dock_widget is not None:
                self._dock_widget.mark_library_history_dirty()
            self._refresh_conversations_cache()
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
            # The result is on the map now, and the zone frame stays on it:
            # the user must keep seeing where the edit applies, whatever they
            # browse next. Re-shown rather than left alone because the layer
            # just added sits over it (see _redraw_zone_outline, which draws
            # the polygon when there is one and the rectangle otherwise).
            self._redraw_zone_outline()
            thumb = self._render_layer_thumb(layer)
            # Persist a small local copy for the conversation rows: server
            # thumb URLs are signed and expire, this one never leaves disk.
            from ...core.prompts import conversation_thumbs

            conversation_thumbs.save_thumb(
                self._last_completed_request_id or "", thumb
            )
            # Local GeoTIFF index: browsing back to this version later (even
            # offline, even after a preview drop) re-adds it from disk.
            history_cache.save_output_paths(
                self._last_completed_request_id or "",
                result_info.get("geotiff_path") or "",
                result_info.get("before_geotiff_path") or "",
            )
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
                telemetry.track(
                    te.PLUGIN_ERROR,
                    build_failure_props("write", "post_complete_ui", str(e)),
                )
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
            telemetry.track(
                te.PLUGIN_ERROR,
                build_failure_props("write", "layer_add_failed", str(e)),
            )
            telemetry.flush()
            self._dock_widget.set_generating(False)
            msg = tr("Error adding layer: {error}").format(error=e)
            self._dock_widget.set_status(msg, is_error=True)
            self._show_error_report(msg, result_info.get("request_id") or "")
            log_warning(f"Failed to add layer: {e}")

    # Plugin-declared signals only: an argument-less worker.disconnect() would
    # also sever the QgsTaskManager hookups made by addTask().
    _WORKER_SIGNALS = ("succeeded", "progress", "failed", "taskTerminated")

    def _cleanup_worker(self):
        """Drop our reference to the QgsTask; TaskManager owns its lifetime.

        The signal lookup sits INSIDE the guard: the task manager can have
        deleted the C++ half already, and on such a dead sip wrapper even
        reading `worker.succeeded` raises RuntimeError. Building the list first
        put that raise outside every try, so it escaped into the Qt slot that
        called us and left the dock locked on the generating view.
        """
        worker = self._worker
        # Nulled first, so even an unexpected raise below still drops the
        # reference; a task we keep pointing at blocks the next Generate click.
        self._worker = None
        if worker is None:
            return
        for name in self._WORKER_SIGNALS:
            try:
                sig = getattr(worker, name, None)
                if sig is None:
                    continue
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
