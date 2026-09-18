from __future__ import annotations

import time
import uuid
from urllib.parse import quote, urlencode

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QByteArray, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..core import qt_compat as QtC
from ..core.config_store import get_export_copy, get_export_dial
from ..core.i18n import tr
from ..core.logger import log_debug, log_warning
from ..core.request_context import request_context
from .image_download import _TIMEOUT_DOWNLOAD
from .network_error_classifier import (
    _MIN_IMAGE_BYTES,
    CANCELLED_CODE,
    DownloadError,
    _cancelled_result,
    _classify_network_error,
    _current_feedback,
    _is_feedback_cancelled,
    _looks_like_image,  # noqa: F401 - compatibility re-export
    _reply_failed,
    _safe_int,
    network_setup_hint,
    qgis_timeout_hint,
    request_feedback,
)
from .network_response import (
    http_failure,
    json_body,
    response_object,
    transfer_timeout,
    valid_headers,
    valid_http_url,
)

# Timeout defaults (milliseconds). Server override via export-config
# `timeouts_ms` {api, startup, download}; the constants stay the fallback
# (and the only value the startup GETs that fetch the config can see).
_TIMEOUT_API = 30_000
# Lightweight startup/interactive GETs (bootstrap, export config, account,
# credits): short so an unstable connection surfaces fast instead of hanging
# a visible "checking..." for 30s.
_TIMEOUT_STARTUP = 8_000
_SUBMIT_TIMEOUTS_MS = {
    "1K": 45_000,
    "2K": 60_000,
    "4K": 90_000,
}

# Standard authenticated write/action endpoints (favorite, delete, rename,
# refund, upload-url request): long enough for a slow link, short enough to
# fail visibly rather than hang a button.
_TIMEOUT_WRITE_MS = 10_000
# Fire-and-forget background posts (cancel a pairing code or a generation,
# a telemetry batch): the UI never waits on these, so a short budget just
# keeps a dead connection from lingering.
_TIMEOUT_QUICK_POST_MS = 5_000
# Account erasure: a sensitive, infrequent call given extra headroom over
# the standard write timeout.
_TIMEOUT_ACCOUNT_DELETE_MS = 15_000
# Presigned storage PUT default (used when the caller does not override it).
_TIMEOUT_UPLOAD_PUT_MS = 60_000
# Unauthenticated pairing status poll (one HTTP round-trip per poll tick).
_TIMEOUT_PAIR_POLL_MS = 10_000


def _api_timeout_ms() -> int:
    return get_export_dial("timeouts_ms.api", _TIMEOUT_API)


def _startup_timeout_ms() -> int:
    return get_export_dial("timeouts_ms.startup", _TIMEOUT_STARTUP)


def _with_context(path: str) -> str:
    """Append the request context to a path, keeping any query it already has.

    Only for the calls whose answer can legitimately differ per client (the
    startup config and bundle): it lets the server send copy in the user's
    language and reach one broken version. Every part is optional and the
    whole thing is best-effort, so a path always comes back usable.
    """
    try:
        params = request_context()
        if not params:
            return path
        query = urlencode(params)
        return f"{path}{'&' if '?' in path else '?'}{query}"
    except Exception:  # nosec B110
        return path


# Re-exported: callers that already name these through the client keep working.
__all__ = [
    "_looks_like_image",
    "_MIN_IMAGE_BYTES",
    "_TIMEOUT_DOWNLOAD",
    "CANCELLED_CODE",
    "DownloadError",
    "TerraLabClient",
    "network_setup_hint",
    "qgis_timeout_hint",
    "request_feedback",
]


# request_context() as a header value, worked out once per session: it reads
# metadata.txt and the OS name, and the answer cannot change until a restart.
_client_context: dict[str, bytes | None] = {"header": None}


def _client_context_value() -> bytes:
    if _client_context["header"] is None:
        try:
            params = request_context()
            value = urlencode(params)
            _client_context["header"] = value.encode("ascii", errors="ignore")
        except Exception:  # nosec B110
            _client_context["header"] = b""
    return _client_context["header"] or b""


class TerraLabClient:
    """HTTP client for TerraLab backend API.

    Uses QgsBlockingNetworkRequest so requests go through the QGIS
    network stack (proxy settings, Network Logger F12, SSL config).
    """

    def __init__(self, base_url: str = None, env_vars: dict | None = None):
        if base_url is None:
            # Prefer env_vars passed by AIEditPlugin (already read once on
            # the main thread). Fall back to reading .env.local if needed
            # (mostly for tests and standalone usage).
            if env_vars and env_vars.get("TERRALAB_BASE_URL"):
                base_url = env_vars["TERRALAB_BASE_URL"]
            else:
                base_url = self._read_base_url()
        if not valid_http_url(base_url):
            raise ValueError("Invalid API base URL")
        self.base_url = base_url.rstrip("/")
        # Dev-only raw-prompt mode: when RAW_PROMPT=true in .env.local, every
        # submit carries raw_prompt so the server (TerraLab team allowlist only)
        # sends the typed prompt alone, with no PREPROMPT or geo line. Lets the
        # team compare models on identical raw input. Off for every normal user.
        self._raw_prompt = str((env_vars or {}).get("RAW_PROMPT", "")).lower() == "true"

    @staticmethod
    def _read_base_url() -> str:
        """Read TERRALAB_BASE_URL from .env.local if available."""
        import os

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(plugin_dir, ".env.local")
        try:
            if os.path.isfile(env_path):
                with open(env_path, encoding="utf-8-sig", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("TERRALAB_BASE_URL="):
                            return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass  # nosec B110
        return "https://terra-lab.ai"

    # -- public API (same signatures as before) ----------------------------

    def submit_generation(
        self,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        auth: dict,
        image_b64: str | None = None,
        upload_token: str | None = None,
        context_images: list[str] | None = None,
        context_image_notes: list[str] | None = None,
        guidance_image: str | None = None,
        guidance_upload_token: str | None = None,
        centroid_lat: float | None = None,
        centroid_lon: float | None = None,
        ground_resolution_m: float | None = None,
        bbox_wgs84: dict | None = None,
        bbox: dict | None = None,
        crs_authid: str | None = None,
        crs_wkt: str | None = None,
        export_width: int | None = None,
        export_height: int | None = None,
        basemap: str | None = None,
        parent_request_id: str | None = None,
        session_id: str | None = None,
        template_id: str | None = None,
        template_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """Submit a prompt for generation. Exactly one of ``image_b64`` or
        ``upload_token`` must be provided.

        ``upload_token`` is the preferred path: the image bytes were already
        PUT to the server-provided signed URL, so the submit body is tiny.
        ``image_b64`` remains as a fallback when the upload path fails.

        ``context_images`` is an optional list of base64-encoded reference
        images. Sent only when non-empty so older backends ignore the field.
        ``context_image_notes`` is an optional list of per-image user
        instructions aligned index-for-index with ``context_images`` ("" for
        images without a note). Sent only when context images ride along AND
        at least one note is non-empty (the service normalizes this), so
        older backends never see the field otherwise.

        Geospatial capture context (``centroid``, ``bbox_wgs84``, native
        ``bbox`` + ``crs_authid``/``crs_wkt``, ``ground_resolution_m``,
        ``export_width``/``export_height``) and iteration tracking
        (``parent_request_id``) are sent only when present, so older backends
        silently ignore them.
        """
        if _is_feedback_cancelled(_current_feedback()):
            return _cancelled_result()
        if (image_b64 is None) == (upload_token is None):
            raise ValueError(
                "submit_generation requires exactly one of image_b64 or upload_token"
            )
        payload: dict = {
            "prompt": prompt,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        }
        # Stable per-attempt key so a retried submit dedupes server-side instead
        # of charging twice. Sent only when present; old servers ignore it.
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        if upload_token is not None:
            payload["upload_token"] = upload_token
        else:
            payload["image"] = image_b64
        if context_images:
            payload["context_images"] = context_images
            # NEW OPTIONAL field, additive: never sent without context_images,
            # never sent when every note is empty, and the shape of
            # context_images itself is untouched.
            if context_image_notes and any(n for n in context_image_notes):
                payload["context_image_notes"] = context_image_notes
        # Clean base image (separate from user reference images): the same zone
        # with the markup removed. The marks themselves are drawn onto the MAIN
        # image; this clean copy lets the server have the model restore the
        # pixels under each mark. ``marks_on_input`` signals that contract.
        # Sent only when present so older backends ignore the fields.
        if guidance_upload_token:
            payload["guidance_upload_token"] = guidance_upload_token
            payload["marks_on_input"] = True
        elif guidance_image:
            payload["guidance_image"] = guidance_image
            payload["marks_on_input"] = True
        if centroid_lat is not None and centroid_lon is not None:
            payload["centroid_lat"] = centroid_lat
            payload["centroid_lon"] = centroid_lon
        if ground_resolution_m is not None:
            payload["ground_resolution_m"] = ground_resolution_m
        if bbox_wgs84 is not None:
            payload["bbox_wgs84"] = bbox_wgs84
        if bbox is not None:
            payload["bbox"] = bbox
        if crs_authid:
            payload["crs_authid"] = crs_authid
        elif crs_wkt:
            payload["crs_wkt"] = crs_wkt
        if export_width and export_height:
            payload["export_width"] = export_width
            payload["export_height"] = export_height
        if basemap:
            payload["basemap"] = basemap
        if parent_request_id:
            payload["parent_request_id"] = parent_request_id
        if session_id:
            payload["session_id"] = session_id
        if template_id:
            payload["template_id"] = template_id
        if template_name:
            payload["template_name"] = template_name
        # Dev-only: bare-prompt comparison mode (team allowlist enforced server
        # side). Absent for normal users, so older/standard servers ignore it.
        if self._raw_prompt:
            payload["raw_prompt"] = True
        body = json_body(payload)
        return self._request(
            "POST",
            "/api/ai-edit/generate",
            auth=auth,
            body=body,
            timeout_ms=_get_submit_timeout_ms(resolution),
        )

    def request_upload_url(self, auth: dict, image_format: str = "png") -> dict:
        """Ask the server for a presigned PUT URL to upload the input image.

        ``image_format`` ('webp' | 'jpeg' | 'png') tells the server which
        content-type and extension to sign the upload with, so the stored
        object is labeled to match the bytes we PUT. The PUT then echoes the
        server's ``required_headers``, keeping the content-type authoritative
        server-side.

        Response shape on success:
            {"upload_token": str, "upload_url": str, "expires_at": int,
             "max_bytes": int, "required_headers": {"Content-Type": str, ...}}
        """
        body = json_body({"format": image_format})
        return self._request(
            "POST",
            "/api/ai-edit/upload-url",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def upload_to_signed_url(
        self,
        url: str,
        data: bytes,
        headers: dict,
        timeout_ms: int | None = None,
    ) -> tuple[bool, str | None]:
        """PUT raw bytes to a presigned upload URL. Returns (ok, error_message)."""
        if timeout_ms is None:
            timeout_ms = get_export_dial("pipeline.terralab_client.upload_put_timeout_ms", _TIMEOUT_UPLOAD_PUT_MS)
        if _is_feedback_cancelled(_current_feedback()):
            return (False, f"{CANCELLED_CODE}: cancelled")
        if not valid_http_url(url) or not valid_headers(headers):
            return (False, "CLIENT_ERROR: invalid upload request")
        if not isinstance(data, bytes) or not data:
            return (False, "CLIENT_ERROR: empty upload")
        timeout_ms = transfer_timeout(timeout_ms, _TIMEOUT_UPLOAD_PUT_MS)
        req = QNetworkRequest(QUrl(url))
        for k, v in headers.items():
            req.setRawHeader(k.encode("utf-8"), v.encode("utf-8"))
        QtC.set_transfer_timeout(req, timeout_ms)
        feedback = _current_feedback()
        if _is_feedback_cancelled(feedback):
            return (False, f"{CANCELLED_CODE}: cancelled")
        blocker = QgsBlockingNetworkRequest()
        payload = QByteArray(data)
        try:
            err = blocker.put(req, payload, feedback=feedback)
        except AttributeError:
            return (False, "QgsBlockingNetworkRequest.put not available")
        if _is_feedback_cancelled(feedback):
            return (False, f"{CANCELLED_CODE}: cancelled")
        if _reply_failed(err, blocker):
            code, msg = _classify_network_error(blocker, timeout_ms)
            return (False, f"{code}: {msg}")
        reply = blocker.reply()
        http_status = reply.attribute(QtC.HttpStatusCodeAttribute) if reply else None
        status_int = _safe_int(http_status) if http_status is not None else None
        if status_int is None or not 200 <= status_int < 300:
            log_warning(f"Upload PUT failed: HTTP {status_int}")
            return (False, f"HTTP {status_int}")
        return (True, None)

    def poll_status(self, request_id: str, auth: dict, force_fallback: bool = False) -> dict:
        """Poll generation status. force_fallback=True bypasses the server's
        grace window and asks it to hit the provider queue immediately. Used as a last
        attempt right before the plugin gives up polling."""
        path = f"/api/ai-edit/generate/status?request_id={quote(request_id, safe='')}"
        if force_fallback:
            path += "&force_fallback=true"
        return self._request("GET", path, auth=auth)

    def get_usage(self, auth: dict, timeout_ms: int | None = None) -> dict:
        """Get usage info. The pre-generation pre-flight passes a shorter timeout
        so an offline/stalled link fails fast instead of blocking the user."""
        return self._request("GET", "/api/plugin/usage", auth=auth, timeout_ms=timeout_ms)

    def get_history(self, auth: dict) -> dict:
        """Get the user's past prompts (deduped server-side, newest first)."""
        return self._request("GET", "/api/plugin/history", auth=auth)

    def get_favorites(self, auth: dict) -> dict:
        """Get the user's starred prompts."""
        return self._request("GET", "/api/plugin/favorites", auth=auth)

    def get_generation_history(
        self, auth: dict, limit: int = 24, favorites_only: bool = False,
        before: str | None = None,
    ) -> dict:
        """Get the user's past generations (before/after + prompt + location).
        Newest first. Each job carries short-lived signed input/output URLs.
        favorites_only filters to starred generations. `before` (ISO timestamp
        of the oldest job already shown) pages further back; the response's
        has_more flag says whether older rows remain."""
        path = f"/api/ai-edit/history?limit={limit}"
        if favorites_only:
            path += "&favorites_only=true"
        if before:
            path += f"&before={quote(before, safe='')}"
        return self._request("GET", path, auth=auth)

    def set_generation_favorite(
        self, auth: dict, request_id: str, is_favorite: bool
    ) -> dict:
        """Star or unstar a past generation. Idempotent."""
        body = json_body({"request_id": request_id, "is_favorite": is_favorite})
        return self._request(
            "POST",
            "/api/ai-edit/history/favorite",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def delete_generation_session(
        self, auth: dict, session_id: str | None = None, request_id: str | None = None
    ) -> dict:
        """Delete a past generation, either a whole conversation (session_id)
        or a single generation within one (request_id). Exactly one of the
        two must be given; the server accepts either but never both."""
        if bool(session_id) == bool(request_id):
            raise ValueError("Pass exactly one of session_id or request_id")
        body = json_body({"session_id": session_id} if session_id else {"request_id": request_id})
        return self._request(
            "POST",
            "/api/ai-edit/history/delete",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def delete_all_generations(self, auth: dict) -> dict:
        """Delete every past generation for this account. Requires explicit
        confirmation in the request body (the server refuses without it)."""
        body = json_body({"confirm": True})
        return self._request(
            "POST",
            "/api/ai-edit/history/delete-all",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def delete_account(self, auth: dict, confirm: str) -> dict:
        """Schedule the erasure of the whole account.

        ``confirm`` is the account email address exactly as the user retyped
        it; the server refuses the call when the two do not match. This does
        not delete on the spot: the account is locked out of every TerraLab
        plugin right away and the data is erased for good once the grace
        period named in the answer runs out.
        """
        body = json_body({"confirm": confirm})
        result = self._request(
            "POST",
            "/api/plugin/account/delete",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial(
                "pipeline.terralab_client.account_delete_timeout_ms", _TIMEOUT_ACCOUNT_DELETE_MS
            ),
        )
        # A refusal is a 4xx, which QgsBlockingNetworkRequest reports as a
        # network error, and _request then hands the parsed body straight back.
        # That body carries its reason code but not always a human line, and
        # without one every caller reads the refusal as a success. Give it one.
        if isinstance(result, dict) and "error" not in result and result.get("code"):
            result = dict(result)
            result["error"] = get_export_copy(
                "pipeline.terralab_client.account_delete_failed", tr("The account could not be deleted.")
            )
        return result

    def rename_generation_session(self, auth: dict, session_id: str, title: str) -> dict:
        """Rename a conversation. The server normalizes and clamps the title
        and returns the normalized value in the response."""
        body = json_body({"session_id": session_id, "title": title})
        return self._request(
            "POST",
            "/api/ai-edit/history/rename",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def add_favorite(
        self,
        auth: dict,
        prompt: str,
        label: str | None = None,
        source_category: str | None = None,
    ) -> dict:
        """Star a prompt server-side. Idempotent."""
        body = json_body({
            "prompt": prompt,
            "label": label,
            "source_category": source_category,
        })
        return self._request(
            "POST", "/api/plugin/favorites", auth=auth, body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def remove_favorite(self, auth: dict, prompt: str) -> dict:
        """Unstar a prompt server-side. Idempotent."""
        body = json_body({"prompt": prompt})
        return self._request(
            "POST", "/api/plugin/favorites/delete", auth=auth, body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def get_account(self, auth: dict, include_usage: bool = False) -> dict:
        """Get account info (email, subscriptions, usage).

        ``include_usage`` asks the server to bundle the /api/plugin/usage
        payload under a top-level ``usage`` key, so one request feeds both
        the account dialog and the credits display. Older servers ignore the
        param and the key is simply absent: callers must treat it as optional.
        """
        path = "/api/plugin/account"
        if include_usage:
            path += "?include=usage"
        return self._request("GET", path, auth=auth, timeout_ms=_startup_timeout_ms())

    def get_export_config(self) -> dict:
        """Fetch export config from the server (no auth required)."""
        return self._request(
            "GET",
            _with_context("/api/ai-edit/export-config"),
            timeout_ms=_startup_timeout_ms(),
        )

    def get_bootstrap(self, auth: dict | None = None) -> dict:
        """One-call startup bundle: export config + preset catalog + usage
        (when auth is sent). Newer servers only; callers fall back to the
        individual endpoints when this 404s."""
        path = _with_context("/api/plugin/bootstrap")
        if auth:
            return self._request(
                "GET", path, auth=auth, timeout_ms=_startup_timeout_ms()
            )
        return self._request("GET", path, timeout_ms=_startup_timeout_ms())

    def get_config(self, product: str) -> dict:
        """Fetch server-driven plugin config (no auth required)."""
        return self._request(
            "GET", _with_context(f"/api/plugin/config?product={quote(product, safe='')}"),
            timeout_ms=_startup_timeout_ms(),
        )

    def get_plugin_login_link(
        self, target: str, cta_source: str, auth: dict, locale: str | None = None
    ) -> dict:
        """A one-time link that opens ``target`` on the website signed in.

        Returns ``{"url": ...}``, ``{"url": None, "reason": ...}`` when the
        server declines, or ``{"error", "code"}``. The caller opens its plain
        URL on anything but an https ``url``.
        """
        from ..core.request_context import plugin_version

        payload: dict = {
            "target": target,
            "cta_source": cta_source,
            "plugin_version": plugin_version(),
        }
        if locale:
            payload["locale"] = locale
        return self._request(
            "POST",
            "/api/plugin/login-link",
            auth=auth,
            body=json_body(payload),
            timeout_ms=5_000,
        )

    def poll_pairing(self, code: str, timeout_ms: int | None = None) -> dict:
        """Poll whether a pairing code has been bound to an activation key.

        Unauthenticated GET (the code itself is the bearer of trust). Returns
        {"status": "pending" | "ready" | "not_found", ...} or {"error", "code"}
        on a network/server failure (the caller retries those within a deadline).
        """
        if timeout_ms is None:
            timeout_ms = get_export_dial("pipeline.terralab_client.pair_poll_timeout_ms", _TIMEOUT_PAIR_POLL_MS)
        return self._request(
            "GET",
            f"/api/plugin/pair/poll?code={quote(code, safe='')}",
            timeout_ms=timeout_ms,
        )

    def cancel_pairing(self, code: str) -> dict:
        """Retire an abandoned pairing code server-side, so a later Confirm in
        the browser shows expired instead of binding a key nobody polls for.

        Unauthenticated POST (the code itself is the bearer of trust).
        """
        body = json_body({"code": code, "product": "ai-edit"})
        return self._request(
            "POST",
            "/api/plugin/pair/cancel",
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.quick_post_timeout_ms", _TIMEOUT_QUICK_POST_MS),
        )

    def send_telemetry_batch(self, events: list, auth: dict) -> dict:
        """Send a batch of telemetry events to the track endpoint."""
        body = json_body({"events": events})
        return self._request(
            "POST",
            "/api/plugin/track",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.quick_post_timeout_ms", _TIMEOUT_QUICK_POST_MS),
        )

    def cancel_generation(self, request_id: str, auth: dict) -> dict:
        """Fire a server-side cancel for a pending generation.

        Used when the user closes the dock mid-generation so the row is
        marked 'cancelled' (and credits refunded) instead of being orphaned
        until the reconcile cron times it out.
        """
        body = json_body({"request_id": request_id})
        return self._request(
            "POST",
            "/api/ai-edit/generate/cancel",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.quick_post_timeout_ms", _TIMEOUT_QUICK_POST_MS),
        )

    def refund_generation(
        self, request_id: str, reason: str, auth: dict, error_code: str | None = None
    ) -> dict:
        """Ask the server to refund credits for a completed generation that
        the plugin failed to deliver to the user (download error, disk write
        error). The server returns 'already_refunded' if previously called.
        Reason must be one of: download_failed, write_error, disk_full, unknown.
        `error_code` is the optional fine-grained cause (TIMEOUT, SSL_ERROR,
        INCOMPLETE, ...) the server surfaces in the ops alert.
        """
        payload = {"request_id": request_id, "reason": reason}
        if error_code:
            payload["error_code"] = error_code
        body = json_body(payload)
        return self._request(
            "POST",
            "/api/ai-edit/generate/refund",
            auth=auth,
            body=body,
            timeout_ms=get_export_dial("pipeline.terralab_client.write_timeout_ms", _TIMEOUT_WRITE_MS),
        )

    def download_image(self, url: str) -> bytes:
        """Download validated image bytes, raising DownloadError on failure."""
        from .image_download import download_image

        return download_image(url, QgsBlockingNetworkRequest)

    # -- internal ----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        auth: dict | None = None,
        body: bytes | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        """Execute an HTTP request via QGIS network stack.

        Returns a dict - either the parsed JSON response or
        {"error": "...", "code": "..."} on failure.
        """
        if _is_feedback_cancelled(_current_feedback()):
            return _cancelled_result()
        if method not in ("GET", "POST"):
            return {"error": "Unsupported request method", "code": "CLIENT_ERROR"}
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            return {"error": "Invalid request path", "code": "CLIENT_ERROR"}
        if auth is not None and not valid_headers(auth):
            return {"error": "Invalid request headers", "code": "CLIENT_ERROR"}
        timeout_ms = transfer_timeout(_api_timeout_ms() if timeout_ms is None else timeout_ms, _TIMEOUT_API)
        url = f"{self.base_url}{path}"
        if not valid_http_url(url):
            return {"error": "Invalid request URL", "code": "CLIENT_ERROR"}
        req = QNetworkRequest(QUrl(url))
        req.setAttribute(QtC.RedirectPolicyAttribute, QtC.NoLessSafeRedirectPolicy)
        req.setRawHeader(b"Content-Type", b"application/json")
        req.setRawHeader(b"Accept", b"application/json")
        QtC.set_transfer_timeout(req, timeout_ms)
        if auth:
            for key, value in auth.items():
                req.setRawHeader(key.encode("utf-8"), value.encode("utf-8"))
        context_header = _client_context_value()
        if context_header:
            req.setRawHeader(b"X-Client-Context", context_header)
        trace_id = uuid.uuid4().hex
        req.setRawHeader(b"X-Client-Request-ID", trace_id.encode("ascii"))
        feedback = _current_feedback()
        blocker = QgsBlockingNetworkRequest()
        started = time.perf_counter()
        if method == "GET":
            err = blocker.get(req, forceRefresh=True, feedback=feedback)
        else:
            payload = QByteArray(body) if body else QByteArray()
            err = blocker.post(req, payload, feedback=feedback)
        if _is_feedback_cancelled(feedback):
            return _cancelled_result()
        reply = blocker.reply()
        status = _safe_int(reply.attribute(QtC.HttpStatusCodeAttribute)) if reply is not None else None
        raw = bytes(reply.content()) if reply is not None else b""
        log_debug(
            f"API request id={trace_id} method={method} status={status} "
            f"duration_ms={int((time.perf_counter() - started) * 1000)} bytes={len(raw)}"
        )
        parsed = response_object(raw) if raw else None
        if _reply_failed(err, blocker):
            if status is not None and status >= 400 and parsed is not None and "error" in parsed:
                return http_failure(parsed, status, reply.rawHeader(b"Retry-After"))
            code, msg = _classify_network_error(blocker, timeout_ms)
            result = {"error": msg, "code": code}
            if status is not None and status >= 400:
                return http_failure(result, status, reply.rawHeader(b"Retry-After"))
            return result
        if status is not None and status >= 400:
            log_warning(f"API request failed id={trace_id} status={status}")
            return http_failure(parsed, status, reply.rawHeader(b"Retry-After"))
        # A missing/redirect/partial status is not a completed JSON API call.
        if status is None or not 200 <= status < 300 or status == 206:
            return {"error": "Invalid server response", "code": "SERVER_ERROR"}
        if not raw and status == 204:
            return {}
        if parsed is None:
            log_warning(f"Invalid API response id={trace_id} bytes={len(raw)}")
            return {"error": "Invalid server response", "code": "SERVER_ERROR"}
        return parsed


def _get_submit_timeout_ms(resolution: str) -> int:
    """Client-side timeout for generation submission.

    Reads the server-supplied ``submit_timeouts_ms`` from the export config
    (loaded at plugin startup into ConfigStore). Falls back to the local
    hardcoded defaults if the server hasn't shipped the field yet, so older
    backends keep working.
    """
    try:
        from ..core.config_store import get_store
        store = get_store()
        cfg = store.get_server_export_config() if store is not None else None
        if cfg:
            server_map = cfg.get("submit_timeouts_ms")
            if isinstance(server_map, dict):
                val = server_map.get(resolution)
                return transfer_timeout(val, _SUBMIT_TIMEOUTS_MS.get(resolution, _TIMEOUT_API))
    except Exception:  # nosec B110
        pass
    return _SUBMIT_TIMEOUTS_MS.get(resolution, _TIMEOUT_API)
