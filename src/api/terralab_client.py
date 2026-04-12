from __future__ import annotations

import json

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QByteArray, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from ..core.logger import log_warning

# Timeout defaults (milliseconds)
_TIMEOUT_API = 30_000
_TIMEOUT_DOWNLOAD = 60_000

# Map QNetworkReply error codes to (our_code, user_message)
_PROXY_ERRORS = {
    QNetworkReply.ProxyConnectionRefusedError,
    QNetworkReply.ProxyConnectionClosedError,
    QNetworkReply.ProxyNotFoundError,
    QNetworkReply.ProxyTimeoutError,
    QNetworkReply.ProxyAuthenticationRequiredError,
    QNetworkReply.UnknownProxyError,
}


def _classify_network_error(
    blocker: QgsBlockingNetworkRequest,
) -> tuple[str, str]:
    """Map a QgsBlockingNetworkRequest failure to (error_code, user_message).

    Also logs full diagnostics for bug reports.
    """
    reply = blocker.reply()
    qt_error = reply.error() if reply else QNetworkReply.UnknownNetworkError
    error_string = blocker.errorMessage()

    # Try to get HTTP status even on error
    http_status = None
    if reply:
        attr = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if attr is not None:
            http_status = int(attr)

    # Log full diagnostics
    log_warning(
        f"Network error: qt_error={int(qt_error)}, http_status={http_status}, detail={error_string[:500]}"
    )

    # Classify
    if qt_error == QNetworkReply.HostNotFoundError:
        return (
            "DNS_ERROR",
            "Cannot reach the server. Check your internet connection.",
        )

    if qt_error == QNetworkReply.ConnectionRefusedError:
        return (
            "CONNECTION_REFUSED",
            "Server refused the connection. The service may be temporarily down.",
        )

    if qt_error == QNetworkReply.TimeoutError:
        return (
            "TIMEOUT",
            "Request timed out. Check your connection or try again.",
        )

    if qt_error == QNetworkReply.SslHandshakeFailedError:
        return (
            "SSL_ERROR",
            "SSL certificate error. Your network may be blocking secure connections.",
        )

    if qt_error in _PROXY_ERRORS:
        return (
            "PROXY_ERROR",
            "Proxy connection failed. "
            "Check QGIS proxy settings (Settings > Options > Network).",
        )

    if qt_error in (
        QNetworkReply.ContentAccessDenied,
        QNetworkReply.AuthenticationRequiredError,
    ):
        return (
            "AUTH_ERROR",
            "Authentication failed. Check your activation key.",
        )

    # Fallback
    return (
        "NO_INTERNET",
        "Network error. Check your internet connection.",
    )


class TerraLabClient:
    """HTTP client for TerraLab backend API.

    Uses QgsBlockingNetworkRequest so requests go through the QGIS
    network stack (proxy settings, Network Logger F12, SSL config).
    """

    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = self._read_base_url()
        self.base_url = base_url.rstrip("/")

    @staticmethod
    def _read_base_url() -> str:
        """Read TERRALAB_BASE_URL from .env.local if available."""
        import os

        plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(plugin_dir, ".env.local")
        if os.path.isfile(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TERRALAB_BASE_URL="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        return "https://terra-lab.ai"

    # -- public API (same signatures as before) ----------------------------

    def submit_generation(
        self,
        image_b64: str,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        auth: dict,
    ) -> dict:
        """Submit an image + prompt for generation."""
        body = json.dumps(
            {
                "image": image_b64,
                "prompt": prompt,
                "resolution": resolution,
                "aspect_ratio": aspect_ratio,
            }
        ).encode("utf-8")
        return self._request("POST", "/api/ai-edit/generate", auth=auth, body=body)

    def poll_status(self, request_id: str, auth: dict) -> dict:
        """Poll generation status."""
        return self._request(
            "GET",
            f"/api/ai-edit/generate/status?request_id={request_id}",
            auth=auth,
        )

    def get_usage(self, auth: dict) -> dict:
        """Get usage info."""
        return self._request("GET", "/api/plugin/usage", auth=auth)

    def get_account(self, auth: dict) -> dict:
        """Get account info (email, subscriptions, usage)."""
        return self._request("GET", "/api/plugin/account", auth=auth)

    def get_presets(self) -> dict:
        """Fetch prompt presets from the server (no auth required)."""
        return self._request("GET", "/api/ai-edit/presets")

    def get_export_config(self) -> dict:
        """Fetch export config from the server (no auth required)."""
        return self._request("GET", "/api/ai-edit/export-config")

    def get_config(self, product: str) -> dict:
        """Fetch server-driven plugin config (no auth required)."""
        return self._request(
            "GET", f"/api/plugin/config?product={product}"
        )

    def send_magic_link(self, email: str, device_id: str, product: str) -> dict:
        """Send a magic link for free tier signup."""
        body = json.dumps({
            "email": email,
            "device_id": device_id,
            "product": product,
        }).encode("utf-8")
        return self._request("POST", "/api/auth/magic-link", body=body)

    def send_telemetry_batch(self, events: list, auth: dict) -> dict:
        """Send a batch of telemetry events to the track endpoint."""
        body = json.dumps({"events": events}).encode("utf-8")
        return self._request(
            "POST", "/api/plugin/track", auth=auth, body=body, timeout_ms=5_000
        )

    def download_image(self, url: str) -> bytes:
        """Download image bytes from a signed URL.

        Raises RuntimeError on failure (callers use try/except).
        """
        req = QNetworkRequest(QUrl(url))
        req.setTransferTimeout(_TIMEOUT_DOWNLOAD)

        blocker = QgsBlockingNetworkRequest()
        err = blocker.get(req, forceRefresh=True)

        if err != QgsBlockingNetworkRequest.NoError:
            code, msg = _classify_network_error(blocker)
            raise RuntimeError(f"Download failed ({code}): {msg}")

        reply = blocker.reply()
        http_status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if http_status and int(http_status) >= 400:
            raise RuntimeError(f"Download failed: HTTP {http_status}")

        return bytes(reply.content())

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

        Returns a dict — either the parsed JSON response or
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
        if err != QgsBlockingNetworkRequest.NoError:
            # Still try to parse the HTTP response body: QgsBlockingNetworkRequest
            # treats HTTP 4xx/5xx as errors, but we need the JSON reason codes.
            reply = blocker.reply()
            if reply:
                http_attr = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
                if http_attr and int(http_attr) >= 400:
                    raw = bytes(reply.content()).decode("utf-8")
                    if raw:
                        try:
                            return json.loads(raw)
                        except Exception:
                            pass
            code, msg = _classify_network_error(blocker)
            return {"error": msg, "code": code}

        # -- HTTP-level handling -------------------------------------------
        reply = blocker.reply()
        http_status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        raw_body = bytes(reply.content()).decode("utf-8")

        if http_status and int(http_status) >= 400:
            # Server returned an error — try to parse JSON body
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
