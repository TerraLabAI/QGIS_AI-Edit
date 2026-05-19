from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Callable

from .logger import log_debug, log_warning


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

    def cancel(self):
        self._cancelled = True

    def reset(self):
        self._cancelled = False

    # Below this base64 size we send the image inline in the submit body -
    # a single round-trip looks cleaner from outside and avoids an extra API
    # call for the common-case small generations (1 K resolution typically
    # encodes to <2 MB of PNG). Above it, we'd risk the serverless body cap,
    # so we switch to the presigned-upload path.
    _INLINE_BASE64_THRESHOLD = 4 * 1024 * 1024  # 4 MB of base64 ≈ 3 MB raw

    def _try_upload_token_flow(self, image_b64: str, auth: dict) -> str | None:
        """Attempt the presigned-upload path. Returns the upload token on
        success, or None to signal the caller to fall back to inline base64.

        Skipped entirely when the image is small enough to inline so we don't
        burn an extra round-trip on small generations.

        Failures here are silent (logged but not surfaced) - we'd rather pay
        the inline body cost than show a network error for a path we control
        entirely and can retry as inline.
        """
        if len(image_b64) <= self._INLINE_BASE64_THRESHOLD:
            return None

        try:
            resp = self._client.request_upload_url(auth)
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
    ) -> GenerationResult:
        """Submit image for generation and poll until complete."""
        if self._cancelled:
            return GenerationResult(success=False, error="Generation cancelled")

        ctx_count = len(context_images) if context_images else 0
        log_debug(
            f"Submitting: resolution={suggested_resolution}, "
            f"aspect={aspect_ratio}, prompt_len={len(prompt)}, "
            f"image_b64_len={len(image_b64)}, context_images={ctx_count}"
        )

        # Preferred path: upload the image straight to remote storage via a
        # short-lived presigned URL, then submit only a tiny token. Skips the
        # serverless body-size cap entirely so multi-MB lossless PNG inputs
        # go through without truncation. Falls back to inline base64 if any
        # step fails so an outage on the storage path doesn't break edits.
        upload_token = self._try_upload_token_flow(image_b64, auth)

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
                **geo_kwargs,
            )

        if "error" in resp:
            return GenerationResult(
                success=False, error=resp["error"], error_code=resp.get("code", "")
            )

        request_id = resp["request_id"]
        submit_time = time.time()
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
        absolute_max_polls = int(360 / poll_interval)
        if max_wait:
            max_polls = int(max_wait / poll_interval)
        elif estimated_time:
            max_polls = max(absolute_max_polls, int(estimated_time * 3 / poll_interval))
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
                    success=False, error="Generation cancelled", request_id=request_id
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
                    error=status_resp.get("error", "Status check failed"),
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
                    error=status_resp.get("error", "Generation failed"),
                    request_id=request_id,
                )

            if poll_interval > 0:
                # Sleep in small chunks so cancellation is responsive
                for _ in range(int(poll_interval * 5)):
                    if self._cancelled:
                        return GenerationResult(
                            success=False, error="Generation cancelled",
                            request_id=request_id,
                        )
                    time.sleep(0.2)

        if ctx is not None:
            ctx.poll_count = max_polls
            ctx.total_wait_seconds = max_polls * poll_interval
            ctx.final_status = "timeout"

        return GenerationResult(
            success=False,
            error="Generation timed out, please try again",
            request_id=request_id,
        )
