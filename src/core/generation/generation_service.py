from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Callable

from ..errors import ErrorCode
from ..i18n import tr
from ..logger import log_debug, log_warning


@dataclass
class GenerationResult:
    success: bool
    image_url: str | None = None
    error: str | None = None
    error_code: str | None = None
    request_id: str | None = None


class GenerationService:
    """Orchestrates the image generation flow. Pure Python."""

    def __init__(self, client, poll_interval: float = 2.0, max_polls: int = 60):
        self._client = client
        self._poll_interval = poll_interval
        self._max_polls = max_polls
        self._cancelled = False
        self._active_request_id: str | None = None
        self._active_auth: dict | None = None

    def cancel(self):
        """Mark the polling loop as cancelled and fire a best-effort
        server-side cancel so the row transitions to status='cancelled' and
        the credits are refunded immediately (without waiting for the
        reconciliation cron to time the row out)."""
        self._cancelled = True
        request_id = self._active_request_id
        auth = self._active_auth
        if not request_id or not auth:
            return
        # Use QgsTask instead of a raw threading.Thread daemon. Python daemon
        # threads running QgsBlockingNetworkRequest can corrupt Qt's network
        # state on shutdown because Qt sockets aren't safe outside Qt threads.
        try:
            from qgis.core import QgsApplication

            from ...workers.generic_request_task import GenericRequestTask

            client = self._client

            def _do_cancel():
                try:
                    client.cancel_generation(request_id, auth)
                except Exception:  # nosec B110
                    pass
                return {}

            task = GenericRequestTask("AI Edit generation cancel", _do_cancel)
            QgsApplication.taskManager().addTask(task)
        except Exception as err:  # nosec B110
            log_warning(f"Cancel task could not be scheduled: {err}")

    def reset(self):
        self._cancelled = False
        self._active_request_id = None
        self._active_auth = None

    # Below this base64 size we send the image inline in the submit body -
    # a single round-trip looks cleaner from outside and avoids an extra API
    # call for the common-case small generations (most zones encode well under
    # this once compressed). Above it, we'd risk the serverless body cap, so we
    # switch to the presigned-upload path.
    _INLINE_BASE64_THRESHOLD = 4 * 1024 * 1024  # 4 MB of base64 ≈ 3 MB raw

    def _try_upload_token_flow(
        self, image_b64: str, auth: dict, image_format: str | None = None
    ) -> str | None:
        """Attempt the presigned-upload path. Returns the upload token on
        success, or None to signal the caller to fall back to inline base64.

        Skipped entirely when the image is small enough to inline so we don't
        burn an extra round-trip on small generations.

        ``image_format`` ('webp' | 'jpeg' | 'png') is the format the canvas was
        encoded as; the server signs the upload with a matching content-type.

        Failures here are silent (logged but not surfaced) - we'd rather pay
        the inline body cost than show a network error for a path we control
        entirely and can retry as inline.
        """
        if len(image_b64) <= self._INLINE_BASE64_THRESHOLD:
            return None

        try:
            resp = self._client.request_upload_url(auth, image_format or "png")
        except Exception as e:
            log_warning(f"Upload URL request raised: {e}")
            return None
        if not isinstance(resp, dict) or "error" in resp:
            log_warning(f"Upload URL request returned error: {resp}")
            return None
        upload_url = resp.get("upload_url")
        token = resp.get("upload_token")
        headers = resp.get("required_headers") or {}
        max_bytes = resp.get("max_bytes")
        if not upload_url or not token:
            return None

        # Guard against an older server that ignores the 'format' field and
        # signs the upload as PNG. We PUT echoing the server's Content-Type, so
        # uploading non-PNG bytes under a PNG-signed URL would store a
        # mislabeled object (fal still sniffs it, but the archive + image proxy
        # would serve it with the wrong type). If the signed content-type
        # doesn't match what we encoded, skip the upload path and fall back to
        # inline, where the server detects the format from the bytes.
        expected_ct = {
            "webp": "image/webp", "jpeg": "image/jpeg", "png": "image/png",
        }.get((image_format or "png").lower(), "image/png")
        signed_ct = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
        if signed_ct and signed_ct != expected_ct:
            log_warning(
                f"Upload URL signed for {signed_ct}, expected {expected_ct}; "
                "falling back to inline so the bytes aren't mislabeled"
            )
            return None

        try:
            data = base64.b64decode(image_b64)
        except Exception as e:
            log_warning(f"Could not decode image_b64 for upload: {e}")
            return None
        if max_bytes is not None and len(data) > max_bytes:
            log_warning(
                f"Image too large for upload ({len(data)} > {max_bytes}), falling back to inline"
            )
            return None

        ok, err = self._client.upload_to_signed_url(upload_url, data, headers)
        if not ok:
            log_warning(f"Presigned upload failed: {err}; falling back to inline")
            return None
        return token

    def generate(
        self,
        image_b64: str,
        prompt: str,
        auth: dict,
        suggested_resolution: str,
        aspect_ratio: str = "1:1",
        on_progress: Callable = None,
        ctx=None,
        context_images: list[str] | None = None,
        guidance_image: str | None = None,
        guidance_format: str | None = None,
    ) -> GenerationResult:
        """Submit image for generation and poll until complete."""
        if self._cancelled:
            return GenerationResult(
                success=False,
                error=tr("Generation cancelled"),
                error_code=ErrorCode.GENERATION_CANCELLED.value,
            )

        ctx_count = len(context_images) if context_images else 0
        log_debug(
            f"Submitting: resolution={suggested_resolution}, "
            f"aspect={aspect_ratio}, prompt_len={len(prompt)}, "
            f"image_b64_len={len(image_b64)}, context_images={ctx_count}, "
            f"guidance={'yes' if guidance_image else 'no'}"
        )

        # Preferred path: upload the image straight to remote storage via a
        # short-lived presigned URL, then submit only a tiny token. Skips the
        # serverless body-size cap entirely so multi-MB inputs go through
        # without truncation. Falls back to inline base64 if any step fails so
        # an outage on the storage path doesn't break edits.
        upload_token = self._try_upload_token_flow(
            image_b64, auth, ctx.input_format if ctx is not None else None
        )

        # The markup-overlay guidance image rides the same upload path as the
        # main image: presigned when large (keeps the submit body under the
        # serverless cap), inline otherwise. Its own format token is used so a
        # rare PNG encode fallback on the guidance alone stays correctly
        # labeled. It never counts against the user's reference-image quota.
        guidance_upload_token = None
        guidance_inline = None
        if guidance_image:
            guidance_upload_token = self._try_upload_token_flow(
                guidance_image, auth, guidance_format
            )
            if guidance_upload_token is None:
                guidance_inline = guidance_image
                log_debug(
                    f"Guidance image: inline ({len(guidance_image)} b64 bytes)"
                )
            else:
                log_debug("Guidance image: presigned upload")

        # Pull geospatial + iteration context off the pipeline ctx so the
        # backend can use it. All fields optional - old backends ignore
        # them, no plugin re-release needed for backwards compat.
        geo_kwargs: dict = {}
        if ctx is not None:
            if ctx.centroid_lat is not None and ctx.centroid_lon is not None:
                geo_kwargs["centroid_lat"] = ctx.centroid_lat
                geo_kwargs["centroid_lon"] = ctx.centroid_lon
            if ctx.ground_resolution_m is not None:
                geo_kwargs["ground_resolution_m"] = ctx.ground_resolution_m
            # Full capture context: exact footprint in WGS84 + native CRS so the
            # backend can georeference each generation precisely later. All
            # optional - old backends ignore unknown fields.
            if ctx.bbox_wgs84 is not None:
                geo_kwargs["bbox_wgs84"] = ctx.bbox_wgs84
            if ctx.extent is not None:
                geo_kwargs["bbox"] = ctx.extent
            if ctx.crs_authid:
                geo_kwargs["crs_authid"] = ctx.crs_authid
            elif ctx.crs_wkt:
                # Only when there is no EPSG authid (custom/project CRS): keeps
                # payload and storage lean for the common 3857/4326 case.
                geo_kwargs["crs_wkt"] = ctx.crs_wkt
            if ctx.export_width and ctx.export_height:
                geo_kwargs["export_width"] = ctx.export_width
                geo_kwargs["export_height"] = ctx.export_height
            if ctx.basemap:
                geo_kwargs["basemap"] = ctx.basemap
            if ctx.parent_request_id:
                geo_kwargs["parent_request_id"] = ctx.parent_request_id
            if ctx.template_id:
                geo_kwargs["template_id"] = ctx.template_id
            if ctx.template_name:
                geo_kwargs["template_name"] = ctx.template_name

        if upload_token is not None:
            resp = self._client.submit_generation(
                upload_token=upload_token,
                prompt=prompt,
                resolution=suggested_resolution,
                aspect_ratio=aspect_ratio,
                auth=auth,
                context_images=context_images,
                guidance_image=guidance_inline,
                guidance_upload_token=guidance_upload_token,
                **geo_kwargs,
            )
        else:
            resp = self._client.submit_generation(
                image_b64=image_b64,
                prompt=prompt,
                resolution=suggested_resolution,
                aspect_ratio=aspect_ratio,
                auth=auth,
                context_images=context_images,
                guidance_image=guidance_inline,
                guidance_upload_token=guidance_upload_token,
                **geo_kwargs,
            )

        if "error" in resp:
            return GenerationResult(
                success=False, error=resp["error"], error_code=resp.get("code", "")
            )

        request_id = resp["request_id"]
        submit_time = time.time()
        # Track active job so cancel() can fire a server-side cancel + refund.
        self._active_request_id = request_id
        self._active_auth = auth
        log_debug(
            f"Submitted: request_id={request_id}, "
            f"resolution={resp.get('resolution', suggested_resolution)}, "
            f"aspect={resp.get('aspect_ratio', aspect_ratio)}, "
            f"est={resp.get('estimated_time', '?')}s, "
            f"max_wait={resp.get('max_wait', '?')}s, "
            f"credits={resp.get('credit_cost', '?')}"
        )

        # Use server-suggested polling config if available
        poll_interval = resp.get("poll_interval", self._poll_interval)
        estimated_time = resp.get("estimated_time")
        max_wait = resp.get("max_wait")  # Server-driven hard ceiling (seconds)
        # Cap the polling loop at 1000 iterations to guard against a misconfigured
        # tiny poll_interval producing a multi-hour wait.
        HARD_CAP = 1000
        absolute_max_polls = min(int(360 / poll_interval), HARD_CAP)
        if max_wait:
            max_polls = min(int(max_wait / poll_interval), HARD_CAP)
        elif estimated_time:
            max_polls = min(max(absolute_max_polls, int(estimated_time * 3 / poll_interval)), HARD_CAP)
        else:
            max_polls = absolute_max_polls

        if ctx is not None:
            ctx.submitted_resolution = resp.get("resolution", suggested_resolution)
            ctx.submitted_aspect_ratio = resp.get("aspect_ratio", aspect_ratio)
            ctx.submit_timestamp = time.time()
            ctx.request_id = request_id
            ctx.credit_cost = resp.get("credit_cost")
            ctx.estimated_time_seconds = estimated_time
            ctx.max_wait_seconds = max_wait

        # If submit already returned the image (sync mode), skip polling
        if resp.get("status") == "completed" and resp.get("image_url"):
            if ctx is not None:
                ctx.poll_count = 0
                ctx.total_wait_seconds = 0.0
                ctx.final_status = "completed"
            return GenerationResult(
                success=True,
                image_url=resp["image_url"],
                request_id=request_id,
            )

        # Poll
        for i in range(max_polls):
            if self._cancelled:
                return GenerationResult(
                    success=False,
                    error=tr("Generation cancelled"),
                    error_code=ErrorCode.GENERATION_CANCELLED.value,
                    request_id=request_id,
                )

            status_resp = self._client.poll_status(request_id, auth=auth)

            # Fail fast on server errors instead of silently retrying
            if "error" in status_resp and "status" not in status_resp:
                if ctx is not None:
                    ctx.poll_count = i + 1
                    ctx.total_wait_seconds = (i + 1) * poll_interval
                    ctx.final_status = "error"
                return GenerationResult(
                    success=False,
                    error=status_resp.get("error") or tr("Status check failed"),
                    error_code=status_resp.get("code", ""),
                    request_id=request_id,
                )

            status = status_resp.get("status", "unknown")

            if on_progress:
                elapsed = time.time() - submit_time
                on_progress(status, i + 1, max_polls, estimated_time, elapsed)

            if status == "completed":
                if ctx is not None:
                    ctx.poll_count = i + 1
                    ctx.total_wait_seconds = (i + 1) * poll_interval
                    ctx.final_status = "completed"
                    ctx.received_image_width = status_resp.get("output_width")
                    ctx.received_image_height = status_resp.get("output_height")
                return GenerationResult(
                    success=True,
                    image_url=status_resp.get("image_url"),
                    request_id=request_id,
                )

            if status == "failed":
                if ctx is not None:
                    ctx.poll_count = i + 1
                    ctx.total_wait_seconds = (i + 1) * poll_interval
                    ctx.final_status = "failed"
                return GenerationResult(
                    success=False,
                    error=status_resp.get("error") or tr("Generation failed"),
                    request_id=request_id,
                )

            if poll_interval > 0:
                # Sleep in small chunks so cancellation is responsive
                for _ in range(int(poll_interval * 5)):
                    if self._cancelled:
                        return GenerationResult(
                            success=False,
                            error=tr("Generation cancelled"),
                            error_code=ErrorCode.GENERATION_CANCELLED.value,
                            request_id=request_id,
                        )
                    time.sleep(0.2)

        # Last-ditch poll with force_fallback=true: the plugin exhausted its
        # poll budget but the server may have a terminal state cached, or can
        # close it via fal queue now. Saves the user the round-trip to the
        # reconcile cron (which would otherwise take up to 2 min to resolve).
        try:
            final = self._client.poll_status(request_id, auth=auth, force_fallback=True)
            final_status = final.get("status", "unknown")
            if final_status == "completed":
                if ctx is not None:
                    ctx.poll_count = max_polls
                    ctx.total_wait_seconds = max_polls * poll_interval
                    ctx.final_status = "completed"
                return GenerationResult(
                    success=True,
                    image_url=final.get("image_url"),
                    request_id=request_id,
                )
            if final_status == "failed":
                if ctx is not None:
                    ctx.poll_count = max_polls
                    ctx.total_wait_seconds = max_polls * poll_interval
                    ctx.final_status = "failed"
                return GenerationResult(
                    success=False,
                    error=final.get("error") or tr("Generation failed"),
                    request_id=request_id,
                )
        except Exception:  # nosec B110
            pass

        if ctx is not None:
            ctx.poll_count = max_polls
            ctx.total_wait_seconds = max_polls * poll_interval
            ctx.final_status = "timeout"

        return GenerationResult(
            success=False,
            error=tr(
                "Generation timed out, please try again. "
                "If a credit was charged, the server will refund it shortly."
            ),
            error_code=ErrorCode.GENERATION_TIMED_OUT.value,
            request_id=request_id,
        )
