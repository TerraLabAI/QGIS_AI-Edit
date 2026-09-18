from __future__ import annotations

import base64
import copy
import math
import os
import threading
import time
import weakref
from typing import Callable

from ..config_store import get_export_copy, get_export_dial, get_export_dial_list, get_export_dial_pair
from ..errors import NETWORK_ERROR_CODES, ErrorCode
from ..i18n import tr
from ..logger import log_debug, log_warning
from .generation_result import GenerationResult
from .generation_upload import GenerationUploadMixin

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
# Ceiling for the exponential backoff between transient-poll retries.
_POLL_BACKOFF_CAP_S = 12.0
# Clamp applied to the server's adaptive retry_after hint.
_RETRY_AFTER_CLAMP_S = (1.0, 15.0)
# Poll budget when submit carries no max_wait: at least the floor, or the
# server's estimate times the factor.
_POLL_BUDGET_FLOOR_S = 360.0
_POLL_BUDGET_ESTIMATE_FACTOR = 3.0

# The inline submit body (main image + guidance + reference images that could
# not be offloaded to presigned upload) is capped by the platform at ~4.5 MB,
# rejected as 413 before our code runs. Reference images have no presigned path,
# so when several large ones push the inline body over this safe ceiling we
# refuse client-side with an actionable message instead of letting the upload
# fail opaquely. Headroom left for JSON keys, the prompt and geo fields.
_MAX_INLINE_BODY_BYTES = 4_200_000

# Time-based budget: per-poll sleeps vary now that the server sends an
# adaptive retry_after hint, so counting iterations would under- or
# over-wait. An iteration hard cap still guards against a misconfigured
# tiny interval producing a multi-hour loop.
_POLL_HARD_CAP = 1000

# Submit retry after a network blip on the initial POST. Safe to repeat: every
# attempt carries the same idempotency key. The second wait (5x the backoff)
# gives a resuming Wi-Fi or a reconnecting VPN time to come back.
_SUBMIT_RETRY_ATTEMPTS = 3
_SUBMIT_RETRY_STEPS = (1, 5)
# Upper bound on any served retry count above: each retry backs off, so an
# unbounded count would stall a generation for minutes.
_MAX_RETRY_ATTEMPTS = 5
_SUBMIT_RETRY_BACKOFF_S = 1.0
# Last-ditch force_fallback poll after the budget runs out.
_RESCUE_RETRY_ATTEMPTS = 2
_RESCUE_RETRY_BACKOFF_S = 1.5


def _positive_number(value, fallback):
    """Accept only finite positive server timings; malformed hints use defaults."""
    if isinstance(value, bool):
        return fallback
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def _retry_delay(value, fallback, minimum, maximum):
    hint = _positive_number(value, None)
    return min(max(hint, minimum), maximum) if hint is not None else fallback


def _image_url(response):
    value = response.get("image_url")
    return isinstance(value, str) and bool(value.strip())


def normalize_context_image_notes(
    context_images: list[str] | None, notes: list[str] | None
) -> list[str] | None:
    """Per-image notes ready for submit, or None when there is nothing to say.

    Aligned index-for-index with ``context_images`` (trimmed or padded with
    ""), each entry whitespace-stripped. Returns None unless at least one note
    is non-empty, so older backends never see the field for note-less
    generations."""
    if not context_images or not notes:
        return None
    aligned = [note.strip() if isinstance(note, str) else "" for note in notes[: len(context_images)]]
    aligned += [""] * (len(context_images) - len(aligned))
    return aligned if any(aligned) else None


def _cancelled_message() -> str:
    return get_export_copy("pipeline.generation_service.generation_cancelled", tr("Generation cancelled"))


def _generation_failed_message() -> str:
    return get_export_copy("pipeline.generation_service.generation_failed", tr("Generation failed"))


def _status_check_failed_message() -> str:
    return get_export_copy("pipeline.generation_service.status_check_failed", tr("Status check failed"))


class GenerationService(GenerationUploadMixin):
    """Orchestrates the image generation flow. Pure Python."""

    def __init__(self, client, poll_interval: float = 2.0, max_polls: int = 60):
        self._client = client
        self._poll_interval = _positive_number(poll_interval, 2.0)
        self._max_polls = max_polls
        # One service serves every run, and a cancelled run keeps draining in
        # its thread after the dock has moved on. Cancellation is therefore
        # per thread: cancel() bumps an epoch, generate() snapshots it in the
        # worker thread, and only that run reads the mismatch. A later run's
        # start can no longer un-cancel the old one.
        self._cancel_epoch = 0
        self._run = threading.local()
        # QgsFeedback of each task running generate(), registered by that
        # task. cancel() aborts their requests in flight; QgsFeedback.cancel()
        # is safe from any thread. Weak, so a finished task is not kept alive.
        self._feedbacks = weakref.WeakSet()
        self._feedback_lock = threading.Lock()

    def set_feedback(self, feedback) -> None:
        with self._feedback_lock:
            self._feedbacks.add(feedback)

    def cancel(self):
        """Stop the local polling loop only. We deliberately do NOT cancel the
        job server-side: a user interrupting (Stop/Exit, closing the dock,
        unloading the plugin) is not our fault, so we never refund. The
        generation keeps running on the server, is charged once, and the result
        lands in the user's Recent tab to pick up. Refunds happen only for
        genuine server-side failures (the reconcile cron) or our own delivery
        failures (the download path)."""
        self._cancel_epoch += 1
        with self._feedback_lock:
            feedbacks = list(self._feedbacks)
        for feedback in feedbacks:
            try:
                feedback.cancel()
            except Exception:  # nosec B110
                pass

    def reset(self):
        """Kept for callers; a new run snapshots the epoch in generate()."""

    def _is_cancelled(self) -> bool:
        """True if the plugin cancelled this run OR its QgsTask was cancelled
        (e.g. the native task-manager Cancel button)."""
        run_epoch = getattr(self._run, "epoch", self._cancel_epoch)
        cb = getattr(self._run, "is_cancelled", None)
        return run_epoch != self._cancel_epoch or bool(cb and cb())

    def _sleep_or_cancelled(self, seconds: float) -> bool:
        """Sleep in small chunks so a Cancel is picked up quickly. Returns True
        if cancellation was requested during the wait (caller should bail)."""
        if seconds <= 0:
            return self._is_cancelled()
        deadline = time.monotonic() + seconds
        while True:
            if self._is_cancelled():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._is_cancelled()
            time.sleep(min(0.2, remaining))

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
                    status, polls, max_polls, estimated_time, time.monotonic() - submit_time
                )
        return False

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
        context_image_notes: list[str] | None = None,
        guidance_image: str | None = None,
        guidance_format: str | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> GenerationResult:
        """Submit image for generation and poll until complete."""
        self._run.epoch = self._cancel_epoch
        self._run.is_cancelled = is_cancelled
        if self._is_cancelled():
            return GenerationResult(
                success=False,
                error=_cancelled_message(),
                error_code=ErrorCode.GENERATION_CANCELLED.value,
            )

        # Snapshot caller-owned values before the first blocking upload.
        auth = dict(auth)
        context_images = list(context_images) if context_images else None
        notes = normalize_context_image_notes(context_images, context_image_notes)

        ctx_count = len(context_images) if context_images else 0
        note_count = sum(1 for n in (notes or []) if n)
        log_debug(
            f"Submitting: resolution={suggested_resolution}, "
            f"aspect={aspect_ratio}, prompt_len={len(prompt)}, "
            f"image_b64_len={len(image_b64)}, context_images={ctx_count}, "
            f"context_notes={note_count}, "
            f"guidance={'yes' if guidance_image else 'no'}"
        )

        prepared = self._prepare_uploads(
            image_b64, auth, ctx, context_images, guidance_image, guidance_format,
            extra_inline_bytes=sum(len(n.encode("utf-8")) for n in (notes or []))
            + len(prompt.encode("utf-8")),
        )
        if isinstance(prepared, GenerationResult):
            return prepared
        upload_token, guidance_upload_token, guidance_inline = prepared

        submitted = self._submit(
            prompt=prompt,
            suggested_resolution=suggested_resolution,
            aspect_ratio=aspect_ratio,
            auth=auth,
            image_b64=image_b64,
            upload_token=upload_token,
            context_images=context_images,
            context_image_notes=notes,
            guidance_inline=guidance_inline,
            guidance_upload_token=guidance_upload_token,
            geo_kwargs=self._build_geo_kwargs(ctx),
        )
        if isinstance(submitted, GenerationResult):
            return submitted
        resp, request_id, submit_time = submitted

        plan = self._resolve_poll_plan(resp, request_id, suggested_resolution, aspect_ratio, ctx)
        if isinstance(plan, GenerationResult):
            return plan
        poll_interval, estimated_time, budget_s, max_polls, hard_cap = plan

        result, polls = self._poll_until_done(
            request_id, auth, ctx, on_progress, poll_interval,
            estimated_time, budget_s, max_polls, submit_time, hard_cap,
        )
        if result is not None:
            return result

        return self._rescue_or_timeout(request_id, auth, ctx, polls, submit_time)

    def _build_geo_kwargs(self, ctx) -> dict:
        """Optional submit fields sourced from the pipeline context."""
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
        return copy.deepcopy(geo_kwargs)

    def _submit(
        self,
        prompt: str,
        suggested_resolution: str,
        aspect_ratio: str,
        auth: dict,
        image_b64: str,
        upload_token: str | None,
        context_images: list[str] | None,
        context_image_notes: list[str] | None,
        guidance_inline: str | None,
        guidance_upload_token: str | None,
        geo_kwargs: dict,
    ) -> tuple[dict, str, float] | GenerationResult:
        """Submit the job, retrying one network blip. Returns
        (resp, request_id, submit_time) or a failed GenerationResult.
        """
        # One idempotency key for this whole generation attempt: a submit retried
        # after a dropped response reuses it so the server dedupes instead of
        # creating a second paid job. Old servers ignore the field.
        # Same shape as secrets.token_urlsafe(16) (22 url-safe characters),
        # without the secrets import cost at plugin load.
        idempotency_key = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode("ascii")
        image_kwargs = (
            {"upload_token": upload_token}
            if upload_token is not None
            else {"image_b64": image_b64}
        )
        submit_attempts = min(
            get_export_dial("pipeline.generation_service.submit_retry_attempts", _SUBMIT_RETRY_ATTEMPTS),
            _MAX_RETRY_ATTEMPTS,
        )
        submit_backoff_s = get_export_dial(
            "pipeline.generation_service.submit_retry_backoff_s", _SUBMIT_RETRY_BACKOFF_S
        )
        resp: dict = {}
        for _attempt in range(submit_attempts):
            if self._is_cancelled():
                return GenerationResult(
                    success=False,
                    error=_cancelled_message(),
                    error_code=ErrorCode.GENERATION_CANCELLED.value,
                )
            resp = self._client.submit_generation(
                prompt=prompt,
                resolution=suggested_resolution,
                aspect_ratio=aspect_ratio,
                auth=auth,
                context_images=context_images,
                context_image_notes=context_image_notes,
                guidance_image=guidance_inline,
                guidance_upload_token=guidance_upload_token,
                idempotency_key=idempotency_key,
                **image_kwargs,
                **geo_kwargs,
            )
            if not isinstance(resp, dict):
                resp = {"error": _generation_failed_message(), "code": ErrorCode.SERVER_ERROR.value}
            if "error" not in resp:
                break
            code = resp.get("code", "")
            # Retry only a transient network blip, reusing the key so the retry
            # cannot double-charge. App errors (quota, bad request) fail fast.
            if code in NETWORK_ERROR_CODES and _attempt < submit_attempts - 1:
                log_warning(f"Submit attempt {_attempt + 1} failed ({code}); retrying")
                step = _SUBMIT_RETRY_STEPS[min(_attempt, len(_SUBMIT_RETRY_STEPS) - 1)]
                if self._sleep_or_cancelled(submit_backoff_s * step):
                    return GenerationResult(
                        success=False,
                        error=_cancelled_message(),
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
        if not isinstance(request_id, str) or not request_id.strip():
            log_warning(f"Submit returned no request_id; resp keys={list(resp.keys())}")
            return GenerationResult(
                success=False,
                error=get_export_copy(
                    "pipeline.generation_service.no_request_id",
                    tr(
                        "The server did not confirm your request. If a credit was "
                        "charged it will be refunded shortly. Check the Recent tab "
                        "before retrying."
                    ),
                ),
                error_code=ErrorCode.SERVER_ERROR.value,
            )
        submit_time = time.monotonic()
        log_debug(
            f"Submitted: request_id={request_id}, "
            f"resolution={resp.get('resolution', suggested_resolution)}, "
            f"aspect={resp.get('aspect_ratio', aspect_ratio)}, "
            f"est={resp.get('estimated_time', '?')}s, "
            f"max_wait={resp.get('max_wait', '?')}s, "
            f"credits={resp.get('credit_cost', '?')}"
        )
        return resp, request_id, submit_time

    def _resolve_poll_plan(
        self,
        resp: dict,
        request_id: str,
        suggested_resolution: str,
        aspect_ratio: str,
        ctx,
    ) -> tuple[float, float | None, float, int, int] | GenerationResult:
        """Derive the poll budget from the submit response and stamp ctx.

        Returns (poll_interval, estimated_time, budget_s, max_polls, hard_cap),
        or a completed GenerationResult when submit already carried the image.
        """
        # Use server-suggested polling config if available
        poll_interval = _positive_number(resp.get("poll_interval"), self._poll_interval)
        estimated_time = _positive_number(resp.get("estimated_time"), None)
        max_wait = _positive_number(resp.get("max_wait"), None)  # Server-driven hard ceiling (seconds)
        if max_wait:
            budget_s = float(max_wait)
        else:
            floor_s = get_export_dial("poll.budget_floor_s", _POLL_BUDGET_FLOOR_S)
            if estimated_time:
                factor = get_export_dial(
                    "poll.budget_estimate_factor", _POLL_BUDGET_ESTIMATE_FACTOR
                )
                budget_s = max(floor_s, float(estimated_time) * factor)
            else:
                budget_s = floor_s
        # Read once and carried to the poll loop: a config refresh mid-run must
        # not move the ceiling out from under a generation already counting.
        hard_cap = get_export_dial("poll.hard_cap", _POLL_HARD_CAP)
        # Poll-count estimate kept for the progress callback signature.
        max_polls = max(1, min(math.ceil(budget_s / poll_interval), hard_cap))

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
        if resp.get("status") == "completed" and _image_url(resp):
            if ctx is not None:
                ctx.poll_count = 0
                ctx.total_wait_seconds = 0.0
                ctx.final_status = "completed"
            return GenerationResult(
                success=True,
                image_url=resp["image_url"],
                request_id=request_id,
            )
        return poll_interval, estimated_time, budget_s, max_polls, hard_cap

    def _poll_until_done(
        self,
        request_id: str,
        auth: dict,
        ctx,
        on_progress: Callable,
        poll_interval: float,
        estimated_time: float | None,
        budget_s: float,
        max_polls: int,
        submit_time: float,
        hard_cap: int | None = None,
    ) -> tuple[GenerationResult | None, int]:
        """Poll until a terminal state, cancellation, or budget exhaustion.

        Returns (result, polls); result is None when the budget ran out.
        ``hard_cap`` comes from the plan so both uses see one value; None means
        read it here.
        """
        # Poll. Pending responses from newer servers carry an adaptive
        # retry_after hint (slower early in the job, fast near completion);
        # without one we keep the fixed interval, so older servers work
        # unchanged.
        max_poll_errors = get_export_dial(
            "poll.max_consecutive_errors", _MAX_CONSECUTIVE_POLL_ERRORS
        )
        backoff_cap_s = get_export_dial("poll.backoff_cap_s", _POLL_BACKOFF_CAP_S)
        retry_lo, retry_hi = get_export_dial_pair(
            "poll.retry_after_clamp_s", _RETRY_AFTER_CLAMP_S
        )
        # Union-only: the server can ADD retryable codes (poll.retryable_extra,
        # uppercased) but never remove a shipped one. Resolved once per call,
        # not at import, so a config refresh applies to the next generation.
        retryable_codes = get_export_dial_list(
            "poll.retryable_extra", _RETRYABLE_POLL_CODES, normalize=str.upper
        )
        if hard_cap is None:
            hard_cap = get_export_dial("poll.hard_cap", _POLL_HARD_CAP)
        consecutive_poll_errors = 0
        polls = 0
        while polls < hard_cap and (time.monotonic() - submit_time) < budget_s:
            if self._is_cancelled():
                return GenerationResult(
                    success=False,
                    error=_cancelled_message(),
                    error_code=ErrorCode.GENERATION_CANCELLED.value,
                    request_id=request_id,
                ), polls

            status_resp = self._client.poll_status(request_id, auth=auth)
            polls += 1
            if not isinstance(status_resp, dict):
                status_resp = {"error": _status_check_failed_message(), "code": ErrorCode.SERVER_ERROR.value}
            if self._is_cancelled():
                return GenerationResult(False, error=_cancelled_message(),
                                        error_code=ErrorCode.GENERATION_CANCELLED.value,
                                        request_id=request_id), polls
            if status_resp.get("status") == "completed" and not _image_url(status_resp):
                status_resp = {"error": _status_check_failed_message(), "code": ErrorCode.EMPTY_RESPONSE.value}

            if "error" in status_resp and "status" not in status_resp:
                code = status_resp.get("code", "")
                # A transient network blip during polling must not abandon a
                # paid generation: the job is already submitted and charged, and
                # the server keeps working. Tolerate a few consecutive blips
                # before giving up, backing off so a rate-limit spike or flaky
                # link isn't answered with a retry storm.
                if code in retryable_codes:
                    consecutive_poll_errors += 1
                    if consecutive_poll_errors <= max_poll_errors:
                        backoff = min(
                            poll_interval * (2 ** (consecutive_poll_errors - 1)),
                            backoff_cap_s,
                        )
                        backoff = _retry_delay(status_resp.get("retry_after"), backoff, retry_lo, retry_hi)
                        backoff = min(backoff, max(0.0, budget_s - (time.monotonic() - submit_time)))
                        log_warning(
                            f"Transient poll error {code} "
                            f"({consecutive_poll_errors}/{max_poll_errors}), "
                            f"retrying in {backoff:.0f}s"
                        )
                        if self._sleep_or_cancelled(backoff):
                            return GenerationResult(
                                success=False,
                                error=_cancelled_message(),
                                error_code=ErrorCode.GENERATION_CANCELLED.value,
                                request_id=request_id,
                            ), polls
                        continue
                # Non-retryable server/app error, or too many consecutive blips.
                if ctx is not None:
                    ctx.poll_count = polls
                    ctx.total_wait_seconds = round(time.monotonic() - submit_time, 1)
                    ctx.final_status = "error"
                return GenerationResult(
                    success=False,
                    error=status_resp.get("error") or _status_check_failed_message(),
                    error_code=code,
                    request_id=request_id,
                ), polls

            # A good response clears the transient-error streak.
            consecutive_poll_errors = 0
            status = status_resp.get("status", "unknown")

            if on_progress:
                elapsed = time.monotonic() - submit_time
                on_progress(status, polls, max_polls, estimated_time, elapsed)

            if status == "completed":
                if ctx is not None:
                    ctx.poll_count = polls
                    ctx.total_wait_seconds = round(time.monotonic() - submit_time, 1)
                    ctx.final_status = "completed"
                    ctx.received_image_width = status_resp.get("output_width")
                    ctx.received_image_height = status_resp.get("output_height")
                return GenerationResult(
                    success=True,
                    image_url=status_resp.get("image_url"),
                    request_id=request_id,
                ), polls

            if status == "failed":
                if ctx is not None:
                    ctx.poll_count = polls
                    ctx.total_wait_seconds = round(time.monotonic() - submit_time, 1)
                    ctx.final_status = "failed"
                return GenerationResult(
                    success=False,
                    error=status_resp.get("error") or _generation_failed_message(),
                    error_code=status_resp.get("code") or ErrorCode.GENERATION_FAILED.value,
                    request_id=request_id,
                ), polls

            sleep_s = _retry_delay(status_resp.get("retry_after"), poll_interval, retry_lo, retry_hi)
            sleep_s = min(sleep_s, max(0.0, budget_s - (time.monotonic() - submit_time)))
            if self._wait_with_progress(
                sleep_s, on_progress, status, polls, max_polls, estimated_time, submit_time
            ):
                return GenerationResult(
                    success=False,
                    error=_cancelled_message(),
                    error_code=ErrorCode.GENERATION_CANCELLED.value,
                    request_id=request_id,
                ), polls
        return None, polls

    def _rescue_or_timeout(
        self,
        request_id: str,
        auth: dict,
        ctx,
        polls: int,
        submit_time: float,
    ) -> GenerationResult:
        """Final force_fallback polls after budget exhaustion, else time out."""
        # Last-ditch poll with force_fallback=true: the plugin exhausted its
        # poll budget but the server may have a terminal state cached, or can
        # close it via the provider queue now. Saves the user the round-trip to the
        # reconcile cron (which would otherwise take up to 2 min to resolve).
        # Retry once on a flaky link so a single blip doesn't discard a
        # generation that actually finished. Capped at 2 attempts: this asks the
        # server to hit the provider queue, so we must not hammer it.
        rescue_attempts = min(
            get_export_dial("pipeline.generation_service.rescue_retry_attempts", _RESCUE_RETRY_ATTEMPTS),
            _MAX_RETRY_ATTEMPTS,
        )
        rescue_backoff_s = get_export_dial(
            "pipeline.generation_service.rescue_retry_backoff_s", _RESCUE_RETRY_BACKOFF_S
        )
        for _attempt in range(rescue_attempts):
            if self._is_cancelled():
                break
            try:
                final = self._client.poll_status(request_id, auth=auth, force_fallback=True)
                final_status = final.get("status", "unknown")
                if final_status == "completed" and _image_url(final):
                    if ctx is not None:
                        ctx.poll_count = polls
                        ctx.total_wait_seconds = round(time.monotonic() - submit_time, 1)
                        ctx.final_status = "completed"
                    return GenerationResult(
                        success=True,
                        image_url=final.get("image_url"),
                        request_id=request_id,
                    )
                if final_status == "failed":
                    if ctx is not None:
                        ctx.poll_count = polls
                        ctx.total_wait_seconds = round(time.monotonic() - submit_time, 1)
                        ctx.final_status = "failed"
                    return GenerationResult(
                        success=False,
                        error=final.get("error") or _generation_failed_message(),
                        error_code=final.get("code") or ErrorCode.GENERATION_FAILED.value,
                        request_id=request_id,
                    )
            except Exception:  # nosec B110
                pass
            if _attempt < rescue_attempts - 1 and self._sleep_or_cancelled(rescue_backoff_s):
                break

        if self._is_cancelled():
            return GenerationResult(False, error=_cancelled_message(),
                                    error_code=ErrorCode.GENERATION_CANCELLED.value,
                                    request_id=request_id)
        if ctx is not None:
            ctx.poll_count = polls
            ctx.total_wait_seconds = round(time.monotonic() - submit_time, 1)
            ctx.final_status = "timeout"

        return GenerationResult(
            success=False,
            error=get_export_copy(
                "pipeline.generation_service.generation_timed_out",
                tr(
                    "Generation timed out, please try again. "
                    "If a credit was charged, the server will refund it shortly."
                ),
            ),
            error_code=ErrorCode.GENERATION_TIMED_OUT.value,
            request_id=request_id,
        )
