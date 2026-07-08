from __future__ import annotations

import time

from qgis.core import Qgis
from qgis.PyQt.QtWidgets import QPushButton

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import get_wall_url, mark_privacy_notice_seen
from ...core.config_store import get_export_copy, get_export_dial_list
from ...core.errors import build_failure_props
from ...core.i18n import tr
from ...core.log_scrub import scrub_user_paths as _scrub_paths
from ...core.logger import log, log_warning
from ...core.number_format import format_count
from ...core.prompts import history_cache, prompt_history
from ..dialogs.error_report_dialog import REPORT_PROBLEM_HREF
from ..layer_groups import bring_ai_edit_group_to_front
from ..layer_occlusion import layers_hiding_layer
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
    def _restore_failed_iteration(self) -> None:

        started_from_result = bool(
            getattr(self, "_generation_started_from_result", False)
        )
        self._generation_started_from_result = False
        if started_from_result and self._versions:
            self._dock_widget._enter_iteration_state()

    def _on_generation_progress(self, status: str, percentage: int):
        self._dock_widget.set_progress_message(status, percentage)

    def _on_generation_error(self, message: str, code: str, ctx_snapshot: dict | None = None):
        if self._map_tool:
            self._map_tool.set_locked(False)
        self._dock_widget.set_generating(False)
        self._restore_failed_iteration()



        snap = ctx_snapshot or {}
        template_id = snap.get("template_id")
        template_name = snap.get("template_name")
        self._cleanup_worker()


        self._clear_markup_layer()
        normalized_code = (code or "").strip().upper()
        if normalized_code == "GENERATION_CANCELLED":




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


        effective_code = (code or "").strip() or "model_failure"

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



        if normalized_code != "TRIAL_EXHAUSTED" and not is_quota_error:
            telemetry.track(te.GENERATION_FAILED, self._enrich_generation_props(extra_props))
            telemetry.flush()
        if normalized_code == "TRIAL_EXHAUSTED":




            self._dock_widget.show_trial_exhausted_info(message, get_wall_url())


            telemetry.flush()
        elif is_quota_error:
            self._dock_widget.show_usage_limit_info(message, subscribe_error_url())
            telemetry.track(te.TRIAL_EXHAUSTED_VIEWED, {"is_free_tier": False})


            telemetry.flush()
        elif _is_prompt_blocked(normalized_code):





            self._dock_widget.set_status(
                _prompt_blocked_message(message, normalized_code), is_error=True
            )
        elif _is_model_failure(message, normalized_code):








            if _is_safety_block(message):
                enriched = get_export_copy(
                    "flows.generation_results.safety_block",
                    tr(
                        "Generation failed: the request was blocked by a safety filter. "
                        "You have not been charged. Try rephrasing your prompt."
                    ), escape=True,
                )
            else:




                enriched = get_export_copy(
                    "flows.generation_results.no_image_returned",
                    tr(
                        "Generation failed: the AI returned no image. You have not been "
                        "charged. AI Edit draws on the map and cannot answer questions, "
                        "so describe the change you want to see, then try again."
                    ), escape=True,
                )
            self._dock_widget.set_status(enriched, is_error=True)
            self._offer_model_failure_action(_is_safety_block(message))
        elif _is_service_busy(message, normalized_code):


            enriched = get_export_copy(
                "flows.generation_results.servers_busy",
                tr(
                    "Our image servers are busy right now. You have not been charged. "
                    "Please wait a moment and try again."
                ), escape=True,
            )
            self._dock_widget.set_status(enriched, is_error=True)
            self._dock_widget.set_status_action(
                get_export_copy("flows.generation_results.try_again", tr("Try again")),
                self._retry_last_prompt,
            )
        else:
            enriched = _enrich_error_message(message, code)



            if normalized_code in get_export_dial_list(
                "error_policy.credit_reassure_extra",
                _CREDIT_REASSURE_CODES,
                normalize=str.upper,
            ):
                credit_note = get_export_copy(
                    "flows.generation_results.no_credit_used",
                    tr("No credit was used."),
                    escape=True,
                )
                enriched = f"{enriched} {credit_note}"
            request_id = snap.get("request_id") or ""
            policy = _report_policy(normalized_code)
            if policy == "link":


                report_link = (
                    f'<a href="{REPORT_PROBLEM_HREF}">{tr("Report a problem")}</a>'
                )
                self._dock_widget.arm_report_context(request_id)
                self._dock_widget.set_status(f"{enriched} {report_link}", is_error=True)
            elif policy == "dialog":


                self._dock_widget.set_status(enriched, is_error=True)
                self._show_error_report(enriched, request_id)
            else:

                self._dock_widget.set_status(enriched, is_error=True)
        log_warning(f"Generation failed: {message} (code={code})")



        self._last_generation_error = message or ""
        self._last_generation_error_code = effective_code

    def _offer_model_failure_action(self, is_safety_block: bool) -> None:






        dock = self._dock_widget
        if is_safety_block:
            dock.set_status_action(
                get_export_copy("flows.generation_results.edit_your_prompt", tr("Edit your prompt")),
                dock.focus_prompt_input,
            )
        else:
            dock.set_status_action(
                get_export_copy(
                    "flows.generation_results.open_library", tr("Open the Library")
                ),
                dock._on_browse_templates_clicked,
            )

    def _retry_last_prompt(self) -> None:





        dock = self._dock_widget
        if dock._result_prompt_widget.isVisible():
            dock._on_retry_clicked()
            return
        prompt = dock.get_prompt()
        if prompt:
            dock.generate_clicked.emit(prompt)

    def _show_error_report(self, error_message: str, request_id: str = "") -> None:









        if getattr(self, "_error_report_dialog_shown", False):
            return


        if getattr(self, "_headless_run", False):
            log_warning("Error report dialog suppressed: this run came from the API.")
            return
        self._error_report_dialog_shown = True
        try:
            from ..dialogs.error_report_dialog import show_error_report
            show_error_report(self._iface.mainWindow(), error_message, request_id)
        except Exception as err:  # nosec B110
            log_warning(f"Could not open error report dialog: {err}")

    def _remember_zone_polygon_for_history(
        self, request_id: str | None, crs_authid: str | None = None
    ) -> None:





        if not request_id or self._selected_polygon is None or self._selected_polygon.isEmpty():
            return
        try:



            if not crs_authid:
                crs_authid = self._canvas.mapSettings().destinationCrs().authid()
            history_cache.save_zone_polygon(
                request_id, self._selected_polygon.asWkt(), crs_authid
            )
        except Exception as err:  # nosec B110
            log_warning(f"zone polygon history save failed: {err}")

    def _on_generation_finished(self, result_info: dict):
        if self._map_tool:
            self._map_tool.set_locked(False)

        self._last_completed_request_id = result_info.get("request_id")
        self._remember_zone_polygon_for_history(
            self._last_completed_request_id, result_info.get("crs_authid")
        )
        vector_color: str | None = result_info.get("vector_color")
        vector_classes: list[dict] | None = result_info.get("vector_classes")
        template_id: str | None = result_info.get("template_id")
        template_name: str | None = result_info.get("template_name")
        self._cleanup_worker()



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


            mark_privacy_notice_seen()
            if self._dock_widget is not None:
                self._dock_widget.hide_privacy_notice()
            self._dock_widget.set_generation_complete(layer.name(), layer.id())
            self._warn_if_result_hidden(layer)



            result_prompt = result_info.get("prompt", "")


            base_index = self._selected_version_index
            base_label = (
                tr("Original")
                if base_index <= 0
                else f"V{base_index}"
            )
            self._versions.append({
                "layer_id": layer.id(),
                "request_id": self._last_completed_request_id,
                "prompt": result_prompt,
            })
            self._selected_version_index = len(self._versions) - 1





            self._redraw_zone_outline()
            thumb = self._render_layer_thumb(layer)


            from ...core.prompts import conversation_thumbs

            conversation_thumbs.save_thumb(
                self._last_completed_request_id or "", thumb
            )


            history_cache.save_output_paths(
                self._last_completed_request_id or "",
                result_info.get("geotiff_path") or "",
                result_info.get("before_geotiff_path") or "",
            )


            try:
                dims = f"{format_count(layer.width())} × {format_count(layer.height())} px"
            except Exception:  # nosec B110
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




                vector_color = result_info.get("flat_foreground") or flat_classes[0][0]
                cta_trigger = "flat_output"
            class_label = _resolve_class_label(vector_color, vector_classes)
            detected_colors = (
                [c for c, _share in flat_classes] if flat_classes else None
            )
            if detected_colors is None and isinstance(vector_classes, list):

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




            self._vectorize_suggestion = (
                layer.id(), vector_color, class_label, cta_trigger
            )
            self._pills_armed = True
            self._show_action_pills()
            self._refresh_credits()
            log(f"Generation complete ({round(duration, 1)}s): {result_info['geotiff_path']}")
        except Exception as e:
            if completed_emitted:







                telemetry.track(
                    te.PLUGIN_ERROR,
                    build_failure_props("write", "post_complete_ui", str(e)),
                )
                telemetry.flush()
                log_warning(f"Post-completion UI step failed: {e}")
                return





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

    def _warn_if_result_hidden(self, layer) -> None:








        try:
            if not self._selected_extent:
                return
            covering = layers_hiding_layer(layer, self._selected_extent)
            if not covering:
                return
            telemetry.track(te.RESULT_HIDDEN_WARNED, {"covering_count": len(covering)})
            bar = self._iface.messageBar()
            widget = bar.createMessage(
                get_export_copy(
                    "flows.generation_results.result_hidden_title",
                    tr("Your result is behind other layers"),
                ),
                get_export_copy(
                    "flows.generation_results.result_hidden_body",
                    tr("It was created, but something opaque is drawn on top of it."),
                ),
            )
            front_button = QPushButton(
                get_export_copy(
                    "flows.generation_results.bring_to_front", tr("Bring it to the front")
                )
            )
            widget.layout().addWidget(front_button)

            def _resolve():
                telemetry.track(te.RESULT_HIDDEN_RESOLVED, {"choice": "bring_to_front"})
                bar.popWidget(widget)
                bring_ai_edit_group_to_front()
                try:
                    self._iface.mapCanvas().refresh()
                except Exception as err:  # noqa: BLE001
                    log_warning(f"canvas refresh after reorder failed: {err}")

            front_button.clicked.connect(_resolve)
            bar.pushWidget(widget, Qgis.MessageLevel.Warning)
        except Exception as err:  # noqa: BLE001
            log_warning(f"hidden-result notice skipped: {err}")



    _WORKER_SIGNALS = ("succeeded", "progress", "failed", "taskTerminated")

    def _cleanup_worker(self):








        worker = self._worker


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
