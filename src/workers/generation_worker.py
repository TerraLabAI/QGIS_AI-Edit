"""Generation pipeline as a QgsTask: auth -> generate -> download -> write GeoTIFF."""
from __future__ import annotations

import base64
import random
import time

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.errors import ErrorCode
from ..core.generation.pipeline_context import save_debug_artifacts
from ..core.i18n import tr
from ..core.logger import log_debug
from ..core.prompts.loading_messages import get_phase_messages
from ..ui.raster_writer import write_geotiff

DEFAULT_ESTIMATED_TIME = 25


def _ctx_snapshot(ctx) -> dict:
    """Copy ctx fields the main thread reads on success/failure (no cross-thread reads)."""
    if ctx is None:
        return {}
    return {
        "request_id": getattr(ctx, "request_id", None),
        "template_id": getattr(ctx, "template_id", None),
        "template_name": getattr(ctx, "template_name", None),
        "vector_color": getattr(ctx, "vector_color", None),
        "vector_classes": getattr(ctx, "vector_classes", None),
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
        context_images=None,
    ):
        super().__init__("AI Edit generation", QgsTask.Flag.CanCancel)
        self._client = client
        self._auth_manager = auth_manager
        self._service = service
        self._image_b64 = image_b64
        self._prompt = prompt
        self._aspect_ratio = aspect_ratio
        self._extent_dict = extent_dict
        self._crs_wkt = crs_wkt
        self._output_dir = output_dir
        self._ctx = ctx
        self._debug_mode = debug_mode
        self._plugin_dir = plugin_dir
        self._skip_trial_check = skip_trial_check
        self._suggested_resolution = suggested_resolution
        self._context_images = context_images or []

        self._success_payload: dict | None = None
        self._failure_payload: tuple[str, str, dict] | None = None

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
        self._failure_payload = (message, code_str, _ctx_snapshot(self._ctx))
        return False

    def _refund_if_needed(self, request_id: str | None, reason: str) -> None:
        if not request_id:
            self._track_refund_event(
                "generation_refund_attempted",
                {"reason": reason, "outcome": "no_request_id"},
            )
            return
        if self._ctx is not None and getattr(self._ctx, "refund_emitted", False):
            return
        self._track_refund_event(
            "generation_refund_attempted",
            {"reason": reason, "request_id": request_id},
        )
        try:
            response = self._client.refund_generation(
                request_id, reason, self._auth_manager.get_auth_header()
            )
            log_debug(f"Refund requested for {request_id} ({reason}): {response}")
            if self._ctx is not None:
                self._ctx.refund_emitted = True
            if isinstance(response, dict) and "error" in response:
                # Server accepted the call but rejected the refund. Most
                # common: WRONG_STATUS (job not 'completed') or RATE_LIMITED.
                self._track_refund_event(
                    "generation_refund_failed",
                    {
                        "reason": reason,
                        "request_id": request_id,
                        "error_code": str(response.get("code", "")),
                        "error_message": str(response.get("error", ""))[:200],
                    },
                )
        except Exception as refund_err:
            log_debug(f"Refund request failed (server may retry via cron): {refund_err}")
            self._track_refund_event(
                "generation_refund_failed",
                {
                    "reason": reason,
                    "request_id": request_id,
                    "error_code": "EXCEPTION",
                    "error_message": str(refund_err)[:200],
                },
            )

    @staticmethod
    def _track_refund_event(event: str, properties: dict) -> None:
        # Lazy import + swallow: telemetry must never break the worker.
        try:
            from ..core import telemetry
            telemetry.track(event, properties)
            telemetry.flush()
        except Exception:  # nosec B110
            pass

    def run(self) -> bool:
        if self.isCanceled():
            return False

        self.progress.emit(tr("Preparing..."), 0)

        if not self._skip_trial_check:
            try:
                allowed, reason, code = self._auth_manager.check_can_generate()
            except Exception as e:
                return self._mark_failed(str(e), ErrorCode.NO_NETWORK)
            if not allowed:
                return self._mark_failed(reason, code or ErrorCode.GENERATION_FAILED.value)

        if self.isCanceled():
            return False

        self.progress.emit(tr("Sending your image to the AI..."), 5)

        early = get_phase_messages("early")
        mid = get_phase_messages("mid")
        late = get_phase_messages("late")
        random.shuffle(early)
        random.shuffle(mid)
        random.shuffle(late)
        self._phase_messages = (early, mid, late)
        self._phase_indices = [0, 0, 0]
        self._poll_count = 0
        self._start_time = time.time()
        self._last_pct = 5

        def _on_progress(status, current, total, estimated_time=None, elapsed=None):
            if self.isCanceled():
                return
            self._poll_count += 1
            if self._poll_count % 2 == 1:
                est = estimated_time or DEFAULT_ESTIMATED_TIME
                t_elapsed = elapsed if elapsed is not None else (time.time() - self._start_time)
                t = min(t_elapsed / est, 1.0) if est > 0 else 0

                phase = 0 if t < 0.3 else (1 if t < 0.75 else 2)
                msgs = self._phase_messages[phase]
                idx = self._phase_indices[phase]
                msg = msgs[idx % len(msgs)]
                self._phase_indices[phase] = idx + 1

                target_pct = min(92, int(95 * (1 - (1 - t) ** 2)))
                pct = min(target_pct, self._last_pct + 8)
                pct = max(pct, self._last_pct + 1)
                pct = min(pct, 92)
                self._last_pct = pct

                if t >= 1.0:
                    msg = tr("Taking a bit longer than usual...")

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
        )

        if self.isCanceled():
            return False

        if not result.success:
            return self._mark_failed(
                result.error or tr("Generation failed"),
                result.error_code or ErrorCode.GENERATION_FAILED.value,
            )

        self.progress.emit(tr("Grabbing your masterpiece..."), 93)

        image_data = None
        last_download_err: Exception | None = None
        for attempt in range(1, 4):
            if self.isCanceled():
                return False
            try:
                image_data = self._client.download_image(result.image_url)
                log_debug(f"Downloaded image (attempt {attempt}): {len(image_data)} bytes")
                break
            except Exception as e:
                last_download_err = e
                if attempt < 3:
                    backoff = 2 ** (attempt - 1)
                    log_debug(f"Download attempt {attempt} failed: {e}; retry in {backoff}s")
                    time.sleep(backoff)

        if image_data is None:
            request_id = getattr(result, "request_id", None) or (
                self._ctx.request_id if self._ctx is not None else None
            )
            self._refund_if_needed(request_id, "download_failed")
            return self._mark_failed(
                tr(
                    "Failed to download result image after 3 attempts: {err}. "
                    "Credit refunded."
                ).format(err=last_download_err),
                ErrorCode.DOWNLOAD_FAILED.value,
            )

        if self.isCanceled():
            return False

        self.progress.emit(tr("Dropping it on the map..."), 97)

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
            request_id = (self._ctx.request_id if self._ctx is not None else None)
            self._refund_if_needed(request_id, "write_error")
            return self._mark_failed(
                tr("Failed to write GeoTIFF: {err}. Credit refunded.").format(err=e),
                ErrorCode.WRITE_ERROR.value,
            )

        if self._ctx is not None:
            for w in self._ctx.validate():
                log_debug(f"Pipeline: {w}")
            log_debug(f"Pipeline: {self._ctx.safe_log_summary()}")

        # Run before emit so unload can't race a half-written .debug/ tree.
        if self._debug_mode and self._ctx is not None:
            try:
                sent_img = base64.b64decode(self._image_b64)
                ctx_bytes = [base64.b64decode(b) for b in self._context_images]
                save_debug_artifacts(
                    self._ctx,
                    sent_img,
                    image_data,
                    self._plugin_dir,
                    context_images=ctx_bytes,
                )
            except Exception:  # nosec B110
                pass

        self._success_payload = {
            "geotiff_path": geotiff_path,
            "prompt": self._prompt,
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


GenerationWorker = GenerationTask
