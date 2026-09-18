
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





_INLINE_BASE64_THRESHOLD = 4 * 1024 * 1024



_UPLOAD_RETRY_ATTEMPTS = 3
_UPLOAD_RETRY_BACKOFF_S = 0.5


class GenerationUploadMixin:
    def _try_upload_token_flow(
        self, image_b64: str, auth: dict, image_format: str | None = None,
        reserved_bytes: int = 0,
    ) -> str | None:


















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




















        ctx_inline_bytes = sum(len(c) for c in (context_images or [])) + extra_inline_bytes
        guidance_bytes = len(guidance_image) if guidance_image else 0
        upload_token = self._try_upload_token_flow(
            image_b64, auth, getattr(ctx, "input_format", None),
            reserved_bytes=ctx_inline_bytes + guidance_bytes,
        )


        main_inline_bytes = len(image_b64) if upload_token is None else 0







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
