"""Generation pipeline as a QgsTask: auth -> generate -> download -> write GeoTIFF."""
from __future__ import annotations

import base64
import copy
import math
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
# Only admit "taking a bit longer than usual" once elapsed is well past the
# server's (p75-ish) estimate, so it shows for the genuinely slow tail rather
# than on every run. Measured against the UNCAPPED elapsed/estimate ratio.
_LONGER_THAN_USUAL_RATIO = 1.5
# Waits before each download retry. Long enough in total (about 23 s) for a
# Wi-Fi resume or a VPN reconnect; the sleep checks for Cancel every 0.2 s.
_DOWNLOAD_RETRY_DELAYS_S = (2, 6, 15)

# Result-image download retries: some networks drop the redirect but keep the
# API host reachable, so a few retries (with the stream=1 fallback) recover
# most transient failures.
_DOWNLOAD_RETRY_ATTEMPTS = 4
# Upper bound on a served count: the last backoff repeats on every attempt.
_MAX_DOWNLOAD_RETRY_ATTEMPTS = 5


def _ctx_snapshot(ctx) -> dict:
    """Copy ctx fields the main thread reads on success/failure (no cross-thread reads)."""
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
        "output_rescued": bool(getattr(ctx, "output_rescued", False)),
    }


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
        # Paths only at dispatch; run() base64-encodes them off the UI thread.
        self._context_image_paths = list(context_image_paths or [])
        # Per-image notes aligned with the paths (snapshot_notes order).
        self._context_image_notes = list(context_image_notes or [])
        self._context_images: list[str] = []
        self._guidance_image = guidance_image
        self._guidance_format = guidance_format

        self._success_payload: dict | None = None
        self._failure_payload: tuple[str, str, dict] | None = None
        # Set when the run ended on a cancel the service saw first, so
        # finished() stays silent instead of reporting a failure.
        self._ended_on_cancel = False
        # Aborts the request in flight on cancel. Shared with the service so
        # its own cancel() (Stop, Exit, unload) reaches the request too.
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
            # A cancel is never a failure: the cancel path emits
            # generation_cancelled and recovers the dock itself. Covers the
            # race where the service was cancelled before task.cancel() landed.
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
        """Request a refund once. Returns True only when the server confirmed
        it, so callers never promise the user a refund that did not happen."""
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
                # Server accepted the call but rejected the refund. Most
                # common: WRONG_STATUS (job not 'completed') or RATE_LIMITED.
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
        # Lazy import + swallow: telemetry must never break the worker.
        try:
            from ..core import telemetry
            from ..core import telemetry_events as te
            allowed = {
                "generation_refund_attempted": te.GENERATION_REFUND_ATTEMPTED,
                "generation_refund_failed": te.GENERATION_REFUND_FAILED,
            }
            telemetry.track(allowed[event], properties)
            # No flush() from the worker thread: addTask() is main-thread-only.
            # The main thread flushes when the generation finishes
            # (_on_generation_error / _on_generation_finished).
        except Exception:  # nosec B110
            pass

    def _sleep_cancellable(self, seconds: float) -> bool:
        """Sleep in 0.2s slices so Cancel is honored during a retry backoff.
        Returns True if cancellation was requested during the wait."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.isCanceled():
                return True
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        return self.isCanceled()

    def run(self) -> bool:
        # Last-resort guard: any unhandled exception on a pipeline path
        # (reference encoding, the service call, a progress callback, the
        # GeoTIFF write) must still resolve the run. Route it through the
        # existing failure path so finished() always reports and the dock
        # never wedges on the generating view. Cancel keeps its own semantics:
        # a cancelled run returns False and finished() stays silent.
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

        # Reads up to 12 files; tolerates any that vanished since dispatch.
        # When notes ride along, the paired encoder drops a skipped file's
        # note with it so the two lists stay index-for-index aligned.
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
                # Raw exception text is technical jargon (SSL traces, socket
                # errors); log it, show the friendly line the code maps to.
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

        # One factual line while the server works (Yvann, 2026-09-18: no more
        # rotating jokes); the dock's dots, clock and percent carry the motion.
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
            # No client-side refund on timeout: a timeout is usually the user's
            # own slow/flaky link (the job often completed server-side and lands
            # in Recent), which is not our fault. The server reconcile cron is
            # the sole authority and refunds only genuine server-side failures.
            # We only refund when delivery fails on our side (download path).
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
                # Retries ask the server to send the bytes directly instead of
                # redirecting: some networks block the redirect target while
                # the API host stays reachable, so re-following the redirect
                # can never succeed. Older servers ignore the param.
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
                # The service's cancel aborts the download before task.cancel()
                # lands: stop here, never retry into a refund.
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
            # A cancel during the last attempt is the user's, never a refund.
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
            # Only promise a refund the server actually confirmed.
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

        # Flat-tint sniff on the downloaded bytes: lights the Vectorize CTA
        # for manual segmentation / land-cover prompts that carry no template
        # hints. Optional by design, a failure must never fail the run.
        if (
            self._ctx is not None
            and not self._ctx.vector_color
            and not self._ctx.vector_classes
        ):
            try:
                from ..core.vectorize_detect import detect_flat_colors

                self._ctx.flat_classes = detect_flat_colors(
                    image_data, seg_hint=bool(getattr(self._ctx, "seg_intent", False))
                )
            except Exception:  # nosec B110
                self._ctx.flat_classes = None

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
            # No refund: the generation is completed and archived server-side,
            # so it already sits in the prompt library's Recent tab with a
            # working GeoTIFF download. Refunding a locally failed save would
            # pay back credits for an image the user still has access to.
            # Ship the failing frames so write failures are diagnosable from
            # telemetry (the bare message has not been enough to pinpoint the
            # recurring numpy/PROJ poisoning on Windows). Usernames stripped.
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
                # Flush runs on the main thread in _on_generation_error.
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
            except Exception:  # nosec B110 - diagnostics cannot discard a saved result.
                pass

        # Run before emit so unload can't race a half-written .debug/ tree.
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

        # Keep the swipe's true "before": the exact input the generation ran
        # from, written as a sibling GeoTIFF. Best-effort - a failure here
        # must never fail a completed generation.
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
        except Exception as before_err:  # noqa: BLE001 - cosmetic sidecar
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
            # No slot filled and no cancel: answer anyway, or the dock stays on
            # the generating view for good.
            self.failed.emit(
                tr("Generation failed"),
                ErrorCode.GENERATION_FAILED.value,
                _ctx_snapshot(self._ctx),
            )


GenerationWorker = GenerationTask
