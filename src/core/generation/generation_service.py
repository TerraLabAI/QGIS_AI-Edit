from __future__ import annotations

import base64
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from ..errors import NETWORK_ERROR_CODES, ErrorCode
from ..i18n import tr
from ..logger import log_debug, log_warning

# Network-level error codes (from terralab_client._classify_network_error) that
# are transient: a flaky/slow link can produce one mid-poll while the server is
# still working. We must NOT abandon a paid generation on a single blip, so we
# tolerate a few consecutive ones before giving up. Real server/app errors carry
# other codes and still fail fast.
_RETRYABLE_POLL_CODES = frozenset(
    {"TIMEOUT", "NO_NETWORK", "DNS_ERROR", "CONNECTION_REFUSED", "PROXY_ERROR", "SSL_ERROR",
     # A transient 429 from the read limiter must not abandon a paid generation.
     "RATE_LIMITED"}
)
_MAX_CONSECUTIVE_POLL_ERRORS = 5

# The inline submit body (main image + guidance + reference images that could
# not be offloaded to presigned upload) is capped by the platform at ~4.5 MB,
# rejected as 413 before our code runs. Reference images have no presigned path,
# so when several large ones push the inline body over this safe ceiling we
# refuse client-side with an actionable message instead of letting the upload
# fail opaquely. Headroom left for JSON keys, the prompt and geo fields.
_MAX_INLINE_BODY_BYTES = 4_200_000


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
        """Stop the local polling loop only. We deliberately do NOT cancel the
        job server-side: a user interrupting (Stop/Exit, closing the dock,
        unloading the plugin) is not our fault, so we never refund. The
        generation keeps running on the server, is charged once, and the result
        lands in the user's Recent tab to pick up. Refunds happen only for
        genuine server-side failures (the reconcile cron) or our own delivery
        failures (the download path)."""
        self._cancelled = True

    def reset(self):
        self._cancelled = False

    def _sleep_or_cancelled(self, seconds: float) -> bool:
        """Sleep in small chunks so a Cancel is picked up quickly. Returns True
        if cancellation was requested during the wait (caller should bail)."""
        if seconds <= 0:
            return self._cancelled
        for _ in range(int(seconds * 5)):
            if self._cancelled:
                return True
            time.sleep(0.2)
        return self._cancelled

    def _wait_with_progress(
        self, seconds, on_progress, status, polls, max_polls, estimated_time, submit_time
    ) -> bool:
        """Sleep `seconds`, ticking the progress callback every ~2s so the
        loading messages and progress bar keep moving between slower polls.
        Returns True if cancellation was requested during the wait."""
        waited = 0.0
        while waited < seconds:
            chunk = min(2.0, seconds - waited)
            if self._sleep_or_cancelled(chunk):
                return True
            waited += chunk
            if on_progress and waited < seconds:
                on_progress(
                    status, polls, max_polls, estimated_time, time.time() - submit_time
                )
        return False

    # Below this base64 size we send the image inline in the submit body -
    # a single round-trip looks cleaner from outside and avoids an extra API
    # call for the common-case small generations (most zones encode well under
    # this once compressed). Above it, we'd risk the serverless body cap, so we
    # switch to the presigned-upload path.
    _INLINE_BASE64_THRESHOLD = 4 * 1024 * 1024  # 4 MB of base64 ≈ 3 MB raw

    def _try_upload_token_flow(
        self, image_b64: str, auth: dict, image_format: str | None = None,
        reserved_bytes: int = 0,
    ) -> str | None:
        """Attempt the presigned-upload path. Returns the upload token on
        success, or None to signal the caller to fall back to inline base64.

        Skipped entirely when the image is small enough to inline so we don't
        burn an extra round-trip on small generations. ``reserved_bytes`` is
        other inline payload that shares the submit body (reference images), so
        a near-threshold main image still moves to the presigned path when the
        combined body would overflow the serverless cap.

        ``image_format`` ('webp' | 'jpeg' | 'png') is the format the canvas was
        encoded as; the server signs the upload with a matching content-type.

        Failures here are silent (logged but not surfaced) - we'd rather pay
        the inline body cost than show a network error for a path we control
        entirely and can retry as inline.
        """
        if len(image_b64) + reserved_bytes <= self._INLINE_BASE64_THRESHOLD:
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
        # mislabeled object (the server still sniffs it, but the archive + image proxy
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

        # Retry a transient blip on the storage PUT before conceding. The
        # presigned path is taken precisely for large images, so a single
        # dropped packet here would otherwise cascade into an inline body that
        # overflows the cap and surfaces a misleading "too much image data".
        ok, err = False, None
        for _attempt in range(3):
            ok, err = self._client.upload_to_signed_url(upload_url, data, headers)
            if ok:
                break
            log_warning(f"Presigned upload attempt {_attempt + 1} failed: {err}")
            if _attempt < 2:
                time.sleep(0.5 * (_attempt + 1))
        if not ok:
            log_warning(f"Presigned upload failed after retries: {err}; falling back to inline")
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
        # Single shared inline budget. The submit body carries main + guidance +
        # context together, and the serverless body cap applies to the WHOLE
        # body, not to any one image. So every inline decision must reserve all
        # the other payload that will stay inline: the main image reserves
        # guidance + context up front. Without this, three individually
        # sub-threshold images (e.g. a 1K main + a markup overlay + one
        # reference) each look small alone yet together overflow the cap -> the
        # edge rejects the POST with HTTP 413 before it reaches our function.
        ctx_inline_bytes = sum(len(c) for c in (context_images or []))
        guidance_bytes = len(guidance_image) if guidance_image else 0
        upload_token = self._try_upload_token_flow(
            image_b64, auth, ctx.input_format if ctx is not None else None,
            reserved_bytes=ctx_inline_bytes + guidance_bytes,
        )
        # If the main image stayed inline it still occupies the body, so the
        # guidance decision below must reserve it too (alongside context).
        main_inline_bytes = len(image_b64) if upload_token is None else 0

        # The clean base image (the zone with the marks removed; the marks ride
        # on the main image) travels through the guidance channel. It rides the
        # same upload path as the main image: presigned when large (keeps the
        # submit body under the serverless cap), inline otherwise. Its own
        # format token is used so a rare PNG encode fallback on it alone stays
        # correctly labeled. It never counts against the reference-image quota.
        guidance_upload_token = None
        guidance_inline = None
        if guidance_image:
            guidance_upload_token = self._try_upload_token_flow(
                guidance_image, auth, guidance_format,
                reserved_bytes=ctx_inline_bytes + main_inline_bytes,
            )
            if guidance_upload_token is None:
                guidance_inline = guidance_image
                log_debug(
                    f"Guidance image: inline ({len(guidance_image)} b64 bytes)"
                )
            else:
                log_debug("Guidance image: presigned upload")

        # Everything that stayed inline shares the platform body cap. Main and
        # guidance offload to presigned when large, but reference images have no
        # presigned path, so this is where a stack of big references is caught.
        # Refuse early with a clear message rather than eat an opaque 413.
        guidance_inline_bytes = len(guidance_inline) if guidance_inline else 0
        total_inline_bytes = main_inline_bytes + guidance_inline_bytes + ctx_inline_bytes
        if total_inline_bytes > _MAX_INLINE_BODY_BYTES:
            log_warning(
                f"Inline submit body too large ({total_inline_bytes} bytes): "
                f"main={main_inline_bytes}, guidance={guidance_inline_bytes}, "
                f"context={ctx_inline_bytes}"
            )
            return GenerationResult(
                success=False,
                error=tr(
                    "Too much image data to send. Remove a reference image or "
                    "lower the resolution, then try again."
                ),
                error_code=ErrorCode.TOO_LARGE.value,
            )

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
            if ctx.session_id:
                geo_kwargs["session_id"] = ctx.session_id
            if ctx.template_id:
                geo_kwargs["template_id"] = ctx.template_id
            if ctx.template_name:
                geo_kwargs["template_name"] = ctx.template_name

        # One idempotency key for this whole generation attempt: a submit retried
        # after a dropped response reuses it so the server dedupes instead of
        # creating a second paid job. Old servers ignore the field.
        idempotency_key = secrets.token_urlsafe(16)
        image_kwargs = (
            {"upload_token": upload_token}
            if upload_token is not None
            else {"image_b64": image_b64}
        )
        resp: dict = {}
        for _attempt in range(2):
            if self._cancelled:
                return GenerationResult(
                    success=False,
                    error=tr("Generation cancelled"),
                    error_code=ErrorCode.GENERATION_CANCELLED.value,
                )
            resp = self._client.submit_generation(
                prompt=prompt,
                resolution=suggested_resolution,
                aspect_ratio=aspect_ratio,
                auth=auth,
                context_images=context_images,
                guidance_image=guidance_inline,
                guidance_upload_token=guidance_upload_token,
                idempotency_key=idempotency_key,
                **image_kwargs,
                **geo_kwargs,
            )
            if "error" not in resp:
                break
            code = resp.get("code", "")
            # Retry only a transient network blip, reusing the key so the retry
            # cannot double-charge. App errors (quota, bad request) fail fast.
            if code in NETWORK_ERROR_CODES and _attempt == 0:
                if self._sleep_or_cancelled(1.0):
                    return GenerationResult(
                        success=False,
                        error=tr("Generation cancelled"),
                        error_code=ErrorCode.GENERATION_CANCELLED.value,
                    )
                continue
            return GenerationResult(
                success=False, error=resp["error"], error_code=code
            )

        # A flaky link can drop the connection right as a 2xx arrives, leaving an
        # empty/truncated body (_request returns {} for an empty 2xx). A bare
        # resp["request_id"] would then raise KeyError out of run(), which emits
        # no failed signal and wedges the dock on "generating" forever. Treat a
        # missing id as a clean, reassuring failure instead.
        request_id = resp.get("request_id")
        if not request_id:
            log_warning(f"Submit returned no request_id; resp keys={list(resp.keys())}")
            return GenerationResult(
                success=False,
                error=tr(
                    "The server did not confirm your request. If a credit was "
                    "charged it will be refunded shortly. Check the Recent tab "
                    "before retrying."
                ),
                error_code=ErrorCode.SERVER_ERROR.value,
            )
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
        # Time-based budget: per-poll sleeps vary now that the server sends an
        # adaptive retry_after hint, so counting iterations would under- or
        # over-wait. An iteration hard cap still guards against a misconfigured
        # tiny interval producing a multi-hour loop.
        HARD_CAP = 1000
        if max_wait:
            budget_s = float(max_wait)
        elif estimated_time:
            budget_s = max(360.0, float(estimated_time) * 3)
        else:
            budget_s = 360.0
        # Poll-count estimate kept for the progress callback signature.
        max_polls = min(int(budget_s / poll_interval), HARD_CAP)

        if ctx is not None:
            ctx.submitted_resolution = resp.get("resolution", suggested_resolution)
            ctx.submitted_aspect_ratio = resp.get("aspect_ratio", aspect_ratio)
            ctx.submit_timestamp = time.time()
            ctx.request_id = request_id
            ctx.model_name = resp.get("model_name") or None
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

        # Poll. Pending responses from newer servers carry an adaptive
        # retry_after hint (slower early in the job, fast near completion);
        # without one we keep the fixed interval, so older servers work
        # unchanged.
        consecutive_poll_errors = 0
        polls = 0
        while polls < HARD_CAP and (time.time() - submit_time) < budget_s:
            if self._cancelled:
                return GenerationResult(
                    success=False,
                    error=tr("Generation cancelled"),
                    error_code=ErrorCode.GENERATION_CANCELLED.value,
                    request_id=request_id,
                )

            status_resp = self._client.poll_status(request_id, auth=auth)
            polls += 1

            if "error" in status_resp and "status" not in status_resp:
                code = status_resp.get("code", "")
                # A transient network blip during polling must not abandon a
                # paid generation: the job is already submitted and charged, and
                # the server keeps working. Tolerate a few consecutive blips
                # before giving up, backing off so a rate-limit spike or flaky
                # link isn't answered with a retry storm.
                if code in _RETRYABLE_POLL_CODES:
                    consecutive_poll_errors += 1
                    if consecutive_poll_errors <= _MAX_CONSECUTIVE_POLL_ERRORS:
                        backoff = min(
                            poll_interval * (2 ** (consecutive_poll_errors - 1)), 12.0
                        )
                        log_warning(
                            f"Transient poll error {code} "
                            f"({consecutive_poll_errors}/{_MAX_CONSECUTIVE_POLL_ERRORS}), "
                            f"retrying in {backoff:.0f}s"
                        )
                        if self._sleep_or_cancelled(backoff):
                            return GenerationResult(
                                success=False,
                                error=tr("Generation cancelled"),
                                error_code=ErrorCode.GENERATION_CANCELLED.value,
                                request_id=request_id,
                            )
                        continue
                # Non-retryable server/app error, or too many consecutive blips.
                if ctx is not None:
                    ctx.poll_count = polls
                    ctx.total_wait_seconds = round(time.time() - submit_time, 1)
                    ctx.final_status = "error"
                return GenerationResult(
                    success=False,
                    error=status_resp.get("error") or tr("Status check failed"),
                    error_code=code,
                    request_id=request_id,
                )

            # A good response clears the transient-error streak.
            consecutive_poll_errors = 0
            status = status_resp.get("status", "unknown")

            if on_progress:
                elapsed = time.time() - submit_time
                on_progress(status, polls, max_polls, estimated_time, elapsed)

            if status == "completed":
                if ctx is not None:
                    ctx.poll_count = polls
                    ctx.total_wait_seconds = round(time.time() - submit_time, 1)
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
                    ctx.poll_count = polls
                    ctx.total_wait_seconds = round(time.time() - submit_time, 1)
                    ctx.final_status = "failed"
                return GenerationResult(
                    success=False,
                    error=status_resp.get("error") or tr("Generation failed"),
                    request_id=request_id,
                )

            sleep_s = poll_interval
            hint = status_resp.get("retry_after")
            if hint is not None:
                try:
                    sleep_s = min(max(float(hint), 1.0), 15.0)
                except (TypeError, ValueError):
                    pass
            if self._wait_with_progress(
                sleep_s, on_progress, status, polls, max_polls, estimated_time, submit_time
            ):
                return GenerationResult(
                    success=False,
                    error=tr("Generation cancelled"),
                    error_code=ErrorCode.GENERATION_CANCELLED.value,
                    request_id=request_id,
                )

        # Last-ditch poll with force_fallback=true: the plugin exhausted its
        # poll budget but the server may have a terminal state cached, or can
        # close it via the provider queue now. Saves the user the round-trip to the
        # reconcile cron (which would otherwise take up to 2 min to resolve).
        # Retry once on a flaky link so a single blip doesn't discard a
        # generation that actually finished. Capped at 2 attempts: this asks the
        # server to hit the provider queue, so we must not hammer it.
        for _attempt in range(2):
            if self._cancelled:
                break
            try:
                final = self._client.poll_status(request_id, auth=auth, force_fallback=True)
                final_status = final.get("status", "unknown")
                if final_status == "completed":
                    if ctx is not None:
                        ctx.poll_count = polls
                        ctx.total_wait_seconds = round(time.time() - submit_time, 1)
                        ctx.final_status = "completed"
                    return GenerationResult(
                        success=True,
                        image_url=final.get("image_url"),
                        request_id=request_id,
                    )
                if final_status == "failed":
                    if ctx is not None:
                        ctx.poll_count = polls
                        ctx.total_wait_seconds = round(time.time() - submit_time, 1)
                        ctx.final_status = "failed"
                    return GenerationResult(
                        success=False,
                        error=final.get("error") or tr("Generation failed"),
                        request_id=request_id,
                    )
            except Exception:  # nosec B110
                pass
            if _attempt == 0 and self._sleep_or_cancelled(1.5):
                break

        if ctx is not None:
            ctx.poll_count = polls
            ctx.total_wait_seconds = round(time.time() - submit_time, 1)
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
