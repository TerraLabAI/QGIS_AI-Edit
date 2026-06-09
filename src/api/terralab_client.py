from __future__ import annotations

import json

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QByteArray, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..core import qt_compat as QtC
from ..core.i18n import tr
from ..core.logger import log_debug, log_warning

# Timeout defaults (milliseconds)
_TIMEOUT_API = 30_000
_TIMEOUT_DOWNLOAD = 180_000
_SUBMIT_TIMEOUTS_MS = {
    "1K": 45_000,
    "2K": 60_000,
    "4K": 90_000,
}


def _safe_int(val):
    """Convert Qt enum or attribute value to int (Qt5 returns int, Qt6 returns enum)."""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return getattr(val, "value", val)


def _classify_network_error(
    blocker: QgsBlockingNetworkRequest,
) -> tuple[str, str]:
    """Map a QgsBlockingNetworkRequest failure to (error_code, user_message).

    Also logs full diagnostics for bug reports.
    """
    reply = blocker.reply()
    qt_error = reply.error() if reply else QtC.UnknownNetworkError
    error_string = blocker.errorMessage()

    http_status = None
    if reply:
        attr = reply.attribute(QtC.HttpStatusCodeAttribute)
        if attr is not None:
            http_status = _safe_int(attr)

    log_warning(
        f"Network error: qt_error={_safe_int(qt_error)}, http_status={http_status}, detail={error_string[:500]}"
    )

    if qt_error == QtC.HostNotFoundError:
        return (
            "DNS_ERROR",
            "Cannot reach the server. Check your internet connection.",
        )

    if qt_error == QtC.ConnectionRefusedError_:
        return (
            "CONNECTION_REFUSED",
            "Server refused the connection. The service may be temporarily down.",
        )

    if qt_error == QtC.TimeoutError_:
        return (
            "TIMEOUT",
            "Request timed out. Check your connection or try again.",
        )

    if qt_error == QtC.SslHandshakeFailedError:
        return (
            "SSL_ERROR",
            "SSL certificate error. Your network may be blocking secure connections.",
        )

    if qt_error in QtC.PROXY_ERRORS:
        return (
            "PROXY_ERROR",
            "Proxy connection failed. "
            "Check QGIS proxy settings (Settings > Options > Network).",
        )

    if qt_error in (QtC.ContentAccessDenied, QtC.AuthenticationRequiredError):
        return (
            "AUTH_ERROR",
            "Authentication failed. Check your activation key.",
        )

    # An oversized request body is rejected by the platform (often before our
    # handler runs) as 413. Without this branch it falls through to the generic
    # "check your connection" message, which misleads the user into blaming
    # their network instead of removing a reference image.
    if http_status == 413:
        return (
            "PAYLOAD_TOO_LARGE",
            "Too much image data to send. Remove a reference image or lower the resolution, then try again.",
        )

    # Fallback. Canonical code is NO_NETWORK (ErrorCode enum) so every consumer
    # (inline-only set, retry list, message localizer) treats it as a handled
    # network failure instead of opening the bug-report dialog.
    return (
        "NO_NETWORK",
        "Network error. Check your internet connection.",
    )


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
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _read_base_url() -> str:
        """Read TERRALAB_BASE_URL from .env.local if available."""
        import os

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(plugin_dir, ".env.local")
        try:
            if os.path.isfile(env_path):
                with open(env_path, encoding="utf-8") as f:
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
        template_id: str | None = None,
        template_name: str | None = None,
    ) -> dict:
        """Submit a prompt for generation. Exactly one of ``image_b64`` or
        ``upload_token`` must be provided.

        ``upload_token`` is the preferred path: the image bytes were already
        PUT to the server-provided signed URL, so the submit body is tiny.
        ``image_b64`` remains as a fallback when the upload path fails.

        ``context_images`` is an optional list of base64-encoded reference
        images. Sent only when non-empty so older backends ignore the field.

        Geospatial capture context (``centroid``, ``bbox_wgs84``, native
        ``bbox`` + ``crs_authid``/``crs_wkt``, ``ground_resolution_m``,
        ``export_width``/``export_height``) and iteration tracking
        (``parent_request_id``) are sent only when present, so older backends
        silently ignore them.
        """
        if (image_b64 is None) == (upload_token is None):
            raise ValueError(
                "submit_generation requires exactly one of image_b64 or upload_token"
            )
        payload: dict = {
            "prompt": prompt,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        }
        if upload_token is not None:
            payload["upload_token"] = upload_token
        else:
            payload["image"] = image_b64
        if context_images:
            payload["context_images"] = context_images
        # Markup-overlay guidance image (separate from user reference images).
        # Sent only when present so older backends ignore the field.
        if guidance_upload_token:
            payload["guidance_upload_token"] = guidance_upload_token
        elif guidance_image:
            payload["guidance_image"] = guidance_image
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
        if template_id:
            payload["template_id"] = template_id
        if template_name:
            payload["template_name"] = template_name
        body = json.dumps(payload).encode("utf-8")
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
        body = json.dumps({"format": image_format}).encode("utf-8")
        return self._request(
            "POST",
            "/api/ai-edit/upload-url",
            auth=auth,
            body=body,
            timeout_ms=10_000,
        )

    def upload_to_signed_url(
        self,
        url: str,
        data: bytes,
        headers: dict,
        timeout_ms: int = 60_000,
    ) -> tuple[bool, str | None]:
        """PUT raw bytes to a presigned upload URL. Returns (ok, error_message)."""
        req = QNetworkRequest(QUrl(url))
        for k, v in headers.items():
            req.setRawHeader(k.encode("utf-8"), v.encode("utf-8"))
        req.setTransferTimeout(timeout_ms)
        blocker = QgsBlockingNetworkRequest()
        payload = QByteArray(data)
        try:
            err = blocker.put(req, payload)
        except AttributeError:
            return (False, "QgsBlockingNetworkRequest.put not available")
        if err != QtC.BlockingNoError:
            code, msg = _classify_network_error(blocker)
            return (False, f"{code}: {msg}")
        reply = blocker.reply()
        http_status = reply.attribute(QtC.HttpStatusCodeAttribute) if reply else None
        status_int = _safe_int(http_status) if http_status is not None else None
        if status_int is not None and status_int >= 400:
            raw = bytes(reply.content()).decode("utf-8", errors="replace") if reply else ""
            log_warning(f"Upload PUT failed: HTTP {status_int} {raw[:200]}")
            return (False, f"HTTP {status_int}")
        return (True, None)

    def poll_status(self, request_id: str, auth: dict, force_fallback: bool = False) -> dict:
        """Poll generation status. force_fallback=True bypasses the server's
        grace window and asks it to hit the provider queue immediately. Used as a last
        attempt right before the plugin gives up polling."""
        path = f"/api/ai-edit/generate/status?request_id={request_id}"
        if force_fallback:
            path += "&force_fallback=true"
        return self._request("GET", path, auth=auth)

    def get_usage(self, auth: dict, timeout_ms: int = _TIMEOUT_API) -> dict:
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
        self, auth: dict, limit: int = 24, favorites_only: bool = False
    ) -> dict:
        """Get the user's past generations (before/after + prompt + location).
        Newest first. Each job carries short-lived signed input/output URLs.
        favorites_only filters to starred generations."""
        path = f"/api/ai-edit/history?limit={limit}"
        if favorites_only:
            path += "&favorites_only=true"
        return self._request("GET", path, auth=auth)

    def set_generation_favorite(
        self, auth: dict, request_id: str, is_favorite: bool
    ) -> dict:
        """Star or unstar a past generation. Idempotent."""
        body = json.dumps(
            {"request_id": request_id, "is_favorite": is_favorite}
        ).encode("utf-8")
        return self._request(
            "POST", "/api/ai-edit/history/favorite", auth=auth, body=body, timeout_ms=10_000
        )

    def add_favorite(
        self,
        auth: dict,
        prompt: str,
        label: str | None = None,
        source_category: str | None = None,
    ) -> dict:
        """Star a prompt server-side. Idempotent."""
        body = json.dumps({
            "prompt": prompt,
            "label": label,
            "source_category": source_category,
        }).encode("utf-8")
        return self._request("POST", "/api/plugin/favorites", auth=auth, body=body)

    def remove_favorite(self, auth: dict, prompt: str) -> dict:
        """Unstar a prompt server-side. Idempotent."""
        body = json.dumps({"prompt": prompt}).encode("utf-8")
        return self._request(
            "POST", "/api/plugin/favorites/delete", auth=auth, body=body
        )

    def get_account(self, auth: dict) -> dict:
        """Get account info (email, subscriptions, usage)."""
        return self._request("GET", "/api/plugin/account", auth=auth)

    def get_export_config(self) -> dict:
        """Fetch export config from the server (no auth required)."""
        return self._request("GET", "/api/ai-edit/export-config")

    def get_config(self, product: str) -> dict:
        """Fetch server-driven plugin config (no auth required)."""
        return self._request(
            "GET", f"/api/plugin/config?product={product}"
        )

    def poll_pairing(self, code: str, timeout_ms: int = 10_000) -> dict:
        """Poll whether a pairing code has been bound to an activation key.

        Unauthenticated GET (the code itself is the bearer of trust). Returns
        {"status": "pending" | "ready" | "not_found", ...} or {"error", "code"}
        on a network/server failure (the caller retries those within a deadline).
        """
        from urllib.parse import quote
        return self._request(
            "GET",
            f"/api/plugin/pair/poll?code={quote(code, safe='')}",
            timeout_ms=timeout_ms,
        )

    def send_telemetry_batch(self, events: list, auth: dict) -> dict:
        """Send a batch of telemetry events to the track endpoint."""
        body = json.dumps({"events": events}).encode("utf-8")
        return self._request(
            "POST", "/api/plugin/track", auth=auth, body=body, timeout_ms=5_000
        )

    def cancel_generation(self, request_id: str, auth: dict) -> dict:
        """Fire a server-side cancel for a pending generation.

        Used when the user closes the dock mid-generation so the row is
        marked 'cancelled' (and credits refunded) instead of being orphaned
        until the reconcile cron times it out.
        """
        body = json.dumps({"request_id": request_id}).encode("utf-8")
        return self._request(
            "POST", "/api/ai-edit/generate/cancel", auth=auth, body=body, timeout_ms=5_000
        )

    def refund_generation(self, request_id: str, reason: str, auth: dict) -> dict:
        """Ask the server to refund credits for a completed generation that
        the plugin failed to deliver to the user (download error, disk write
        error). The server returns 'already_refunded' if previously called.
        Reason must be one of: download_failed, write_error, disk_full, unknown.
        """
        body = json.dumps({"request_id": request_id, "reason": reason}).encode("utf-8")
        return self._request(
            "POST", "/api/ai-edit/generate/refund", auth=auth, body=body, timeout_ms=10_000
        )

    def download_image(self, url: str) -> bytes:
        """Download image bytes from a signed URL.

        Raises RuntimeError on failure (callers use try/except).
        """
        req = QNetworkRequest(QUrl(url))
        req.setTransferTimeout(_TIMEOUT_DOWNLOAD)

        blocker = QgsBlockingNetworkRequest()
        err = blocker.get(req, forceRefresh=True)

        if err != QtC.BlockingNoError:
            code, msg = _classify_network_error(blocker)
            raise RuntimeError(
                tr("Download failed ({code}): {msg}").format(code=code, msg=msg)
            )

        reply = blocker.reply()
        http_status = reply.attribute(QtC.HttpStatusCodeAttribute)
        if http_status and _safe_int(http_status) >= 400:
            raise RuntimeError(
                tr("Download failed: HTTP {status}").format(status=http_status)
            )

        data = bytes(reply.content())
        content_type = reply.rawHeader(b"Content-Type")
        ct_str = bytes(content_type).decode("ascii", errors="replace") if content_type else "?"
        head_hex = data[:16].hex() if data else ""
        log_debug(
            f"Downloaded {len(data)} bytes, content-type={ct_str}, head={head_hex}"
        )
        # A flaky/slow link can drop the connection after the 200 headers arrive,
        # leaving a truncated or empty body that QgsBlockingNetworkRequest still
        # reports as success. Raise so the caller's retry loop re-downloads
        # instead of writing a corrupt GeoTIFF.
        if not data:
            raise RuntimeError(tr("Server returned an empty response (0 bytes)"))
        declared = reply.rawHeader(b"Content-Length")
        if declared:
            expected = _safe_int(bytes(declared).decode("ascii", errors="replace"))
            if expected and len(data) < expected:
                raise RuntimeError(
                    tr("Download incomplete: received {got} of {total} bytes").format(
                        got=len(data), total=expected
                    )
                )
        return data

    # -- internal ----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        auth: dict | None = None,
        body: bytes | None = None,
        timeout_ms: int = _TIMEOUT_API,
    ) -> dict:
        """Execute an HTTP request via QGIS network stack.

        Returns a dict - either the parsed JSON response or
        {"error": "...", "code": "..."} on failure.
        """
        url = f"{self.base_url}{path}"
        req = QNetworkRequest(QUrl(url))
        req.setRawHeader(b"Content-Type", b"application/json")
        req.setTransferTimeout(timeout_ms)

        if auth:
            for key, value in auth.items():
                req.setRawHeader(key.encode("utf-8"), value.encode("utf-8"))

        blocker = QgsBlockingNetworkRequest()

        if method == "GET":
            err = blocker.get(req, forceRefresh=True)
        elif method == "POST":
            payload = QByteArray(body) if body else QByteArray()
            err = blocker.post(req, payload)
        else:
            return {
                "error": f"Unsupported method: {method}",
                "code": "CLIENT_ERROR",
            }

        # -- Network-level failure -----------------------------------------
        if err != QtC.BlockingNoError:
            # Still try to parse the HTTP response body: QgsBlockingNetworkRequest
            # treats HTTP 4xx/5xx as errors, but we need the JSON reason codes.
            reply = blocker.reply()
            if reply:
                http_attr = reply.attribute(QtC.HttpStatusCodeAttribute)
                if http_attr and _safe_int(http_attr) >= 400:
                    # `errors='replace'` so a non-UTF8 proxy/CDN error page
                    # never crashes the client with UnicodeDecodeError.
                    raw = bytes(reply.content()).decode("utf-8", errors="replace")
                    if raw:
                        try:
                            return json.loads(raw)
                        except Exception:
                            pass  # nosec B110
            code, msg = _classify_network_error(blocker)
            return {"error": msg, "code": code}

        # -- HTTP-level handling -------------------------------------------
        reply = blocker.reply()
        http_status = reply.attribute(QtC.HttpStatusCodeAttribute)
        raw_body = bytes(reply.content()).decode("utf-8", errors="replace")

        if http_status and _safe_int(http_status) >= 400:
            # Server returned an error - try to parse JSON body
            log_warning(f"HTTP {http_status}: {raw_body[:500]}")
            try:
                error_body = json.loads(raw_body)
                if "error" in error_body:
                    return error_body
                return {
                    "error": error_body.get("detail", raw_body[:200]),
                    "code": "SERVER_ERROR",
                }
            except Exception:
                return {
                    "error": f"Server error (HTTP {http_status})",
                    "code": "SERVER_ERROR",
                }

        # -- Success -------------------------------------------------------
        if not raw_body:
            return {}
        try:
            return json.loads(raw_body)
        except json.JSONDecodeError:
            log_warning(f"Invalid JSON response: {raw_body[:500]}")
            return {"error": "Invalid server response", "code": "SERVER_ERROR"}


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
                if isinstance(val, (int, float)) and val > 0:
                    return int(val)
    except Exception:  # nosec B110
        pass
    return _SUBMIT_TIMEOUTS_MS.get(resolution, _TIMEOUT_API)
