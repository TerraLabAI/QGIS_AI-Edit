
from __future__ import annotations

import base64
import copy
import math
import re
import time

from qgis.core import QgsFeedback, QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..api.network_error_classifier import request_feedback
from ..core.config_store import get_export_copy, get_export_dial
from ..core.errors import ErrorCode
from ..core.generation.pipeline_context import save_debug_artifacts
from ..core.i18n import tr
from ..core.log_scrub import scrub_user_paths
from ..core.logger import log_debug
from ..core.raster_writer import write_geotiff
from ..core.reference_image_store import (
    encode_references_b64,
    encode_references_with_notes,
)

DEFAULT_ESTIMATED_TIME = 25



_LONGER_THAN_USUAL_RATIO = 1.5


_DOWNLOAD_RETRY_DELAYS_S = (2, 6, 15)




_DOWNLOAD_RETRY_ATTEMPTS = 4

_MAX_DOWNLOAD_RETRY_ATTEMPTS = 5


def _ctx_snapshot(ctx) -> dict:

    if ctx is None:
        return {}
    return {
        "request_id": getattr(ctx, "request_id", None),
        "crs_authid": getattr(ctx, "crs_authid", None),
        "template_id": getattr(ctx, "template_id", None),
        "template_name": getattr(ctx, "template_name", None),
        "vector_color": copy.deepcopy(getattr(ctx, "vector_color", None)),
        "vector_classes": copy.deepcopy(getattr(ctx, "vector_classes", None)),
        "flat_classes": copy.deepcopy(getattr(ctx, "flat_classes", None)),
        "flat_foreground": getattr(ctx, "flat_foreground", None),
        "output_rescued": bool(getattr(ctx, "output_rescued", False)),
    }


_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class GenerationTask(QgsTask):
    progress = pyqtSignal(str, int)
    failed = pyqtSignal(str, str, dict)
    succeeded = pyqtSignal(dict)

    def __init__(
        self,
        client,
        auth_manager,
        service,
        image_b64,
        prompt,
        aspect_ratio,
        extent_dict,
        crs_wkt,
        output_dir,
        suggested_resolution,
        ctx=None,
        debug_mode=False,
        plugin_dir="",
        skip_trial_check=False,
        context_image_paths=None,
        context_image_notes=None,
        guidance_image=None,
        guidance_format=None,
    ):
        super().__init__("AI Edit generation", QgsTask.Flag.CanCancel)
        self._client = client
        self._auth_manager = auth_manager
        self._service = service
        self._image_b64 = image_b64
        self._prompt = prompt
        self._aspect_ratio = aspect_ratio
        self._extent_dict = dict(extent_dict)
        self._crs_wkt = crs_wkt
        self._output_dir = output_dir
        self._ctx = ctx
        self._debug_mode = debug_mode
        self._plugin_dir = plugin_dir
        self._skip_trial_check = skip_trial_check
        self._suggested_resolution = suggested_resolution

        self._context_image_paths = list(context_image_paths or [])

        self._context_image_notes = list(context_image_notes or [])
        self._context_images: list[str] = []
        self._guidance_image = guidance_image
        self._guidance_format = guidance_format

        self._success_payload: dict | None = None
        self._failure_payload: tuple[str, str, dict] | None = None


        self._ended_on_cancel = False


        self._feedback = QgsFeedback()
        try:
            service.set_feedback(self._feedback)
        except AttributeError:
            pass

    def cancel(self) -> None:
        try:
            self._feedback.cancel()
        except Exception:  # nosec B110
            pass
        super().cancel()

    @property
    def ctx(self):
        return self._ctx

    def is_active(self) -> bool:
        try:
            return self.status() in (
                QgsTask.TaskStatus.Running,
                QgsTask.TaskStatus.Queued,
                QgsTask.TaskStatus.OnHold,
            )
        except Exception:
            return False

    def _mark_failed(self, message: str, code: str | ErrorCode) -> bool:
        code_str = code.value if isinstance(code, ErrorCode) else str(code)
        if code_str.strip().upper() == ErrorCode.GENERATION_CANCELLED.value:



            self._ended_on_cancel = True
            return False
        self._failure_payload = (message, code_str, _ctx_snapshot(self._ctx))
        return False

    def _refund_if_needed(
        self,
        request_id: str | None,
        reason: str,
        error_code: str | None = None,
        error_message: str | None = None,
        stream_fallback_used: bool | None = None,
    ) -> bool:


        if not request_id:
            self._track_refund_event(
                "generation_refund_attempted",
                {"reason": reason, "outcome": "no_request_id"},
            )
            return False
        if self._ctx is not None and getattr(self._ctx, "refund_emitted", False):
            return False
        attempt_props = {"reason": reason, "request_id": request_id}
        if error_code:
            attempt_props["error_code"] = error_code
        if error_message:
            attempt_props["error_message"] = scrub_user_paths(error_message)[:200]
        if stream_fallback_used is not None:
            attempt_props["stream_fallback_used"] = stream_fallback_used
        self._track_refund_event("generation_refund_attempted", attempt_props)
        try:
            response = self._client.refund_generation(
                request_id, reason, self._auth_manager.get_auth_header(), error_code=error_code
            )
            log_debug(f"Refund requested for {request_id} ({reason}): {response}")
            if self._ctx is not None:
                self._ctx.refund_emitted = True
            if not isinstance(response, dict) or "error" in response or response.get("refunded") is not True:


                refused = response if isinstance(response, dict) else {}
                self._track_refund_event(
                    "generation_refund_failed",
                    {
                        "reason": reason,
                        "request_id": request_id,
                        "error_code": str(refused.get("code", "")),
                        "error_message": scrub_user_paths(str(refused.get("error") or "Refund not confirmed"))[:200],
                    },
                )
                return False
            return True
        except Exception as refund_err:
            log_debug(f"Refund request failed (server may retry via cron): {refund_err}")
            self._track_refund_event(
                "generation_refund_failed",
                {
                    "reason": reason,
                    "request_id": request_id,
                    "error_code": "EXCEPTION",
                    "error_message": scrub_user_paths(str(refund_err))[:200],
                },
            )
            return False

    @staticmethod
    def _track_refund_event(event: str, properties: dict) -> None:

        try:
            from ..core import telemetry
            from ..core import telemetry_events as te
            allowed = {
                "generation_refund_attempted": te.GENERATION_REFUND_ATTEMPTED,
                "generation_refund_failed": te.GENERATION_REFUND_FAILED,
            }
            telemetry.track(allowed[event], properties)



        except Exception:  # nosec B110
            pass

    def _analyze_flat_output(self, image_data: bytes) -> tuple[list | None, str | None]:


        try:
            from ..core.pixel_sample import pack, result_sample

            sample = result_sample(image_data)
            if sample is None:
                return None, None
            width, height, raw = sample
            resp = self._client.analyze_pixels(
                self._auth_manager.get_auth_header(), "flat_output", width, height, pack(raw),
                seg_hint=bool(getattr(self._ctx, "seg_intent", False)),
            )
            classes = resp.get("flat_classes") if isinstance(resp, dict) else None
            if not isinstance(classes, list) or len(classes) < 2:
                return None, None
            cleaned = [
                (str(c[0]), float(c[1])) for c in classes[:16]
                if isinstance(c, (list, tuple)) and len(c) == 2
                and isinstance(c[0], str) and _HEX_RE.match(c[0])
                and isinstance(c[1], (int, float)) and not isinstance(c[1], bool)
            ]
            if len(cleaned) < 2:
                return None, None
            fg = resp.get("foreground")
            fg = fg if isinstance(fg, str) and _HEX_RE.match(fg) else cleaned[0][0]
            return cleaned, fg
        except Exception:  # nosec B110
            return None, None

    def _sleep_cancellable(self, seconds: float) -> bool:


        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.isCanceled():
                return True
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        return self.isCanceled()

    def run(self) -> bool:






        try:
            with request_feedback(self._feedback):
                return self._run_pipeline()
        except Exception as unexpected:
            if self.isCanceled():
                return False
            log_debug(f"Generation worker crashed unexpectedly: {unexpected}")
            return self._mark_failed(
                get_export_copy("pipeline.generation_worker.generation_failed", tr("Generation failed")),
                ErrorCode.GENERATION_FAILED.value,
            )

    def _run_pipeline(self) -> bool:
        if self.isCanceled():
            return False

        self.progress.emit(get_export_copy("pipeline.generation_worker.preparing", tr("Preparing...")), 0)




        notes = self._context_image_notes
        if notes and len(notes) == len(self._context_image_paths):
            self._context_images, notes = encode_references_with_notes(
                self._context_image_paths, notes
            )
            self._context_image_notes = notes
        else:
            if notes:
                log_debug(
                    "context_image_notes dropped: "
                    f"{len(notes)} notes for {len(self._context_image_paths)} paths"
                )
                self._context_image_notes = []
            self._context_images = encode_references_b64(self._context_image_paths)

        if self.isCanceled():
            return False

        if not self._skip_trial_check:
            try:
                allowed, reason, code = self._auth_manager.check_can_generate()
            except Exception as e:


                log_debug(f"Pre-generation check raised: {e}")
                return self._mark_failed(
                    get_export_copy(
                        "pipeline.generation_worker.no_network",
                        tr("No internet connection. Check your network and try again."),
                    ),
                    ErrorCode.NO_NETWORK,
                )
            if not allowed:
                return self._mark_failed(reason, code or ErrorCode.GENERATION_FAILED.value)

        if self.isCanceled():
            return False

        self.progress.emit(
            get_export_copy("pipeline.generation_worker.sending_image", tr("Sending your image to the AI...")), 5
        )



        generating_msg = get_export_copy(
            "pipeline.generation_worker.generating_image", tr("Generating your image...")
        )
        self._poll_count = 0
        self._start_time = time.monotonic()
        self._last_pct = 5

        def _on_progress(status, current, total, estimated_time=None, elapsed=None):
            if self.isCanceled():
                return
            self._poll_count += 1
            if self._poll_count % 2 == 1:
                est = estimated_time or get_export_dial(
                    "loading.default_estimated_time_s", DEFAULT_ESTIMATED_TIME
                )
                t_elapsed = elapsed if elapsed is not None else (time.monotonic() - self._start_time)
                try:
                    est = float(est)
                    t_elapsed = float(t_elapsed)
                    if not math.isfinite(est) or est <= 0:
                        est = DEFAULT_ESTIMATED_TIME
                    if not math.isfinite(t_elapsed) or t_elapsed < 0:
                        t_elapsed = max(0.0, time.monotonic() - self._start_time)
                except (TypeError, ValueError, OverflowError):
                    est = DEFAULT_ESTIMATED_TIME
                    t_elapsed = max(0.0, time.monotonic() - self._start_time)
                raw_ratio = t_elapsed / est
                t = min(raw_ratio, 1.0)
                msg = generating_msg

                target_pct = min(92, int(95 * (1 - (1 - t) ** 2)))
                pct = min(target_pct, self._last_pct + 8)
                pct = max(pct, self._last_pct + 1)
                pct = min(pct, 92)
                self._last_pct = pct

                if raw_ratio >= get_export_dial(
                    "loading.longer_than_usual_ratio", _LONGER_THAN_USUAL_RATIO
                ):
                    msg = get_export_copy(
                        "pipeline.generation_worker.taking_longer",
                        tr("Taking a bit longer than usual..."),
                    )

                self.progress.emit(msg, pct)
                try:
                    self.setProgress(float(pct))
                except Exception:  # nosec B110
                    pass

        result = self._service.generate(
            image_b64=self._image_b64,
            prompt=self._prompt,
            auth=self._auth_manager.get_auth_header(),
            aspect_ratio=self._aspect_ratio,
            on_progress=_on_progress,
            ctx=self._ctx,
            suggested_resolution=self._suggested_resolution,
            context_images=self._context_images,
            context_image_notes=self._context_image_notes or None,
            guidance_image=self._guidance_image,
            guidance_format=self._guidance_format,
            is_cancelled=self.isCanceled,
        )

        if self.isCanceled():
            return False

        if not result.success:





            return self._mark_failed(
                result.error
                or get_export_copy("pipeline.generation_worker.generation_failed", tr("Generation failed")),
                result.error_code or ErrorCode.GENERATION_FAILED.value,
            )

        self.progress.emit(
            get_export_copy("pipeline.generation_worker.grabbing_masterpiece", tr("Grabbing your masterpiece...")),
            93,
        )

        image_data = None
        last_download_err: Exception | None = None
        stream_fallback_used = False
        download_attempts = min(
            get_export_dial("pipeline.generation_worker.download_retry_attempts", _DOWNLOAD_RETRY_ATTEMPTS),
            _MAX_DOWNLOAD_RETRY_ATTEMPTS,
        )
        for attempt in range(1, download_attempts + 1):
            if self.isCanceled():
                return False
            url = result.image_url
            if attempt > 1 and url:




                url = f"{url}{'&' if '?' in url else '?'}stream=1"
                stream_fallback_used = True
            try:
                image_data = self._client.download_image(url)
                if not isinstance(image_data, (bytes, bytearray)) or not image_data:
                    image_data = None
                    raise ValueError("Empty or invalid image response")
                log_debug(f"Downloaded image (attempt {attempt}): {len(image_data)} bytes")
                break
            except Exception as e:
                last_download_err = e


                if self.isCanceled() or getattr(e, "code", "") == ErrorCode.GENERATION_CANCELLED.value:
                    self._ended_on_cancel = True
                    return False
                if attempt < download_attempts:
                    backoff = _DOWNLOAD_RETRY_DELAYS_S[
                        min(attempt - 1, len(_DOWNLOAD_RETRY_DELAYS_S) - 1)
                    ]
                    log_debug(f"Download attempt {attempt} failed: {e}; retry in {backoff}s")
                    if self._sleep_cancellable(backoff):
                        return False

        if image_data is None:

            if self.isCanceled():
                return False
            request_id = getattr(result, "request_id", None) or (
                self._ctx.request_id if self._ctx is not None else None
            )
            refunded = self._refund_if_needed(
                request_id,
                "download_failed",
                error_code=getattr(last_download_err, "code", None),
                error_message=str(last_download_err) if last_download_err else None,
                stream_fallback_used=stream_fallback_used,
            )

            credit_note = (
                get_export_copy("pipeline.generation_worker.credit_refunded", tr("Credit refunded."))
                if refunded
                else get_export_copy(
                    "pipeline.generation_worker.credit_refund_pending",
                    tr("If a credit was charged, it will be refunded."),
                )
            )
            return self._mark_failed(
                tr(
                    "Failed to download result image after {attempts} attempts: {err}."
                ).format(attempts=download_attempts, err=last_download_err) + " " + credit_note,
                ErrorCode.DOWNLOAD_FAILED.value,
            )

        if self.isCanceled():
            return False





        if (
            self._ctx is not None
            and not self._ctx.vector_color
            and not self._ctx.vector_classes
        ):
            self._ctx.flat_classes, self._ctx.flat_foreground = self._analyze_flat_output(image_data)

        self.progress.emit(
            get_export_copy("pipeline.generation_worker.dropping_on_map", tr("Dropping it on the map...")), 97
        )

        try:
            geotiff_path = write_geotiff(
                image_data=image_data,
                extent_dict=self._extent_dict,
                crs_wkt=self._crs_wkt,
                output_dir=self._output_dir,
                prompt=self._prompt,
                ctx=self._ctx,
            )
        except Exception as e:







            try:
                import traceback as _tb

                tail = " | ".join(_tb.format_exc().strip().splitlines()[-4:])
                tail = scrub_user_paths(tail)
                from ..core import telemetry
                from ..core import telemetry_events as te

                telemetry.track(te.PLUGIN_ERROR, {
                    "stage": "write",
                    "error_code": "write_geotiff_failed",
                    "error_message": tail[:200],
                })

            except Exception:  # nosec B110
                pass
            return self._mark_failed(
                tr(
                    "The image was generated but could not be saved to your "
                    "output folder ({err}). It is kept in your prompt library: "
                    "open the Recent tab and download the AI result, or change "
                    "the output folder and try again."
                ).format(err=e),
                ErrorCode.WRITE_ERROR.value,
            )

        if self.isCanceled():
            return False
        if self._ctx is not None:
            try:
                for w in self._ctx.validate():
                    log_debug(f"Pipeline: {w}")
                log_debug(f"Pipeline: {self._ctx.safe_log_summary()}")
            except Exception:  # nosec B110
                pass


        if self._debug_mode and self._ctx is not None:
            try:
                sent_img = base64.b64decode(self._image_b64)
                ctx_bytes = [base64.b64decode(b) for b in self._context_images]
                guidance_img = (
                    base64.b64decode(self._guidance_image)
                    if self._guidance_image
                    else None
                )
                save_debug_artifacts(
                    self._ctx,
                    sent_img,
                    image_data,
                    self._plugin_dir,
                    context_images=ctx_bytes,
                    guidance_png=guidance_img,
                    guidance_format=self._guidance_format,
                )
            except Exception:  # nosec B110
                pass




        before_path = ""
        try:
            from ..core.raster_writer import before_file_base

            before_path = write_geotiff(
                image_data=base64.b64decode(self._image_b64),
                extent_dict=self._extent_dict,
                crs_wkt=self._crs_wkt,
                output_dir=self._output_dir,
                prompt=self._prompt,
                file_base=before_file_base(geotiff_path),
            )
        except Exception as before_err:  # noqa: BLE001
            log_debug(f"before GeoTIFF write skipped: {before_err}")
            before_path = ""

        self._success_payload = {
            "geotiff_path": geotiff_path,
            "before_geotiff_path": before_path,
            "prompt": self._prompt,
            "crs_wkt": self._crs_wkt,
            **_ctx_snapshot(self._ctx),
        }
        return True

    def finished(self, result: bool) -> None:
        if self.isCanceled():
            return
        if result and self._success_payload is not None:
            self.succeeded.emit(self._success_payload)
        elif self._failure_payload is not None:
            self.failed.emit(*self._failure_payload)
        elif not self._ended_on_cancel:


            self.failed.emit(
                tr("Generation failed"),
                ErrorCode.GENERATION_FAILED.value,
                _ctx_snapshot(self._ctx),
            )


GenerationWorker = GenerationTask
