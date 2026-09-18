"""Upload preparation shared by asynchronous generation runs."""
from __future__ import annotations

import base64
import math

from ..config_store import get_export_copy, get_export_dial
from ..errors import ErrorCode
from ..i18n import tr
from ..logger import log_debug, log_warning
from .generation_result import GenerationResult

_MAX_INLINE_BODY_BYTES = 4_200_000
_MAX_RETRY_ATTEMPTS = 5
# Below this base64 size we send the image inline in the submit body -
# a single round-trip looks cleaner from outside and avoids an extra API
# call for the common-case small generations (most zones encode well under
# this once compressed). Above it, we'd risk the serverless body cap, so we
# switch to the presigned-upload path.
_INLINE_BASE64_THRESHOLD = 4 * 1024 * 1024  # 4 MB of base64 ≈ 3 MB raw

# Presigned storage PUT retry: a transient blip here would otherwise cascade
# into an inline body that overflows the cap.
_UPLOAD_RETRY_ATTEMPTS = 3
_UPLOAD_RETRY_BACKOFF_S = 0.5


class GenerationUploadMixin:
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
        # Never above the inline body cap: a larger threshold would send
        # mid-size images inline, where the server rejects them as too large.
        threshold = min(
            get_export_dial("pipeline.generation_service.inline_base64_threshold_bytes", _INLINE_BASE64_THRESHOLD),
            get_export_dial("max_inline_body_bytes", _MAX_INLINE_BODY_BYTES),
        )
        if len(image_b64) + reserved_bytes <= threshold:
            return None

        try:
            resp = self._client.request_upload_url(auth, image_format or "png")
        except Exception as e:
            log_warning(f"Upload URL request raised: {e}")
            return None
        if not isinstance(resp, dict) or "error" in resp:
            # Log code + error only, never the whole dict: a malformed success
            # body would dump the signed upload URL into the QGIS log.
            code = resp.get("code") if isinstance(resp, dict) else None
            error = resp.get("error") if isinstance(resp, dict) else resp
            log_warning(f"Upload URL request returned error: code={code} error={error}")
            return None
        upload_url = resp.get("upload_url")
        token = resp.get("upload_token")
        headers = resp.get("required_headers") or {}
        if not isinstance(headers, dict):
            return None
        max_bytes = resp.get("max_bytes")
        if not isinstance(upload_url, str) or not upload_url or not isinstance(token, str) or not token:
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
        signed_ct = str(headers.get("Content-Type") or headers.get("content-type") or "").lower()
        if signed_ct and signed_ct != expected_ct:
            log_warning(
                f"Upload URL signed for {signed_ct}, expected {expected_ct}; "
                "falling back to inline so the bytes aren't mislabeled"
            )
            return None

        try:
            data = base64.b64decode(image_b64, validate=True)
        except Exception as e:
            log_warning(f"Could not decode image_b64 for upload: {e}")
            return None
        if max_bytes is not None and (
            isinstance(max_bytes, bool) or not isinstance(max_bytes, (int, float))
            or not math.isfinite(max_bytes) or max_bytes <= 0
        ):
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
        upload_attempts = min(
            get_export_dial("pipeline.generation_service.upload_retry_attempts", _UPLOAD_RETRY_ATTEMPTS),
            _MAX_RETRY_ATTEMPTS,
        )
        upload_backoff_s = get_export_dial(
            "pipeline.generation_service.upload_retry_backoff_s", _UPLOAD_RETRY_BACKOFF_S
        )
        ok, err = False, None
        for _attempt in range(upload_attempts):
            if self._is_cancelled():
                return None
            ok, err = self._client.upload_to_signed_url(upload_url, data, headers)
            if ok:
                break
            log_warning(f"Presigned upload attempt {_attempt + 1} failed: {err}")
            if _attempt < upload_attempts - 1 and self._sleep_or_cancelled(
                upload_backoff_s * (_attempt + 1)
            ):
                return None
        if not ok:
            log_warning(f"Presigned upload failed after retries: {err}; falling back to inline")
            return None
        return token

    def _prepare_uploads(
        self,
        image_b64: str,
        auth: dict,
        ctx,
        context_images: list[str] | None,
        guidance_image: str | None,
        guidance_format: str | None,
        extra_inline_bytes: int = 0,
    ) -> tuple[str | None, str | None, str | None] | GenerationResult:
        """Offload the main and guidance images to presigned upload when large.

        Returns (upload_token, guidance_upload_token, guidance_inline), or a
        failed GenerationResult when the inline body would overflow the cap.
        ``extra_inline_bytes`` is other inline payload that always rides the
        submit body (per-image notes), counted in every budget decision.
        """
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
        ctx_inline_bytes = sum(len(c) for c in (context_images or [])) + extra_inline_bytes
        guidance_bytes = len(guidance_image) if guidance_image else 0
        upload_token = self._try_upload_token_flow(
            image_b64, auth, getattr(ctx, "input_format", None),
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
        if total_inline_bytes > get_export_dial("max_inline_body_bytes", _MAX_INLINE_BODY_BYTES):
            log_warning(
                f"Inline submit body too large ({total_inline_bytes} bytes): "
                f"main={main_inline_bytes}, guidance={guidance_inline_bytes}, "
                f"context={ctx_inline_bytes}"
            )
            return GenerationResult(
                success=False,
                error=get_export_copy(
                    "pipeline.generation_service.too_much_image_data",
                    tr(
                        "Too much image data to send. Remove a reference image or "
                        "lower the resolution, then try again."
                    ),
                ),
                error_code=ErrorCode.TOO_LARGE.value,
            )
        return upload_token, guidance_upload_token, guidance_inline
