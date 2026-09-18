"""Network error classification for the TerraLab client.

Turns a failed QgsBlockingNetworkRequest into an AIEditError-shaped dict
(``error`` + ``code``) the UI can branch on, in words the user can act on,
checks that a downloaded result body is an image, and holds the cancel
handle that lets a task abort the request it is waiting on. Split out of
terralab_client.py so the request plumbing and the failure taxonomy can be
read on their own.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager

from qgis.core import QgsBlockingNetworkRequest

from ..core import qt_compat as QtC
from ..core.config_store import get_export_copy
from ..core.errors import ErrorCode
from ..core.i18n import tr
from ..core.log_scrub import scrub_urls
from ..core.logger import log_debug, log_warning

# Private names are listed too: other modules import them from here.
__all__ = [
    "_classify_network_error",
    "_current_feedback",
    "_cancelled_result",
    "_is_feedback_cancelled",
    "_looks_like_image",
    "_MIN_IMAGE_BYTES",
    "_reply_failed",
    "_safe_int",
    "_scrub_urls",
    "CANCELLED_CODE",
    "DownloadError",
    "network_setup_hint",
    "qgis_timeout_hint",
    "request_feedback",
]

# Single implementation in core/log_scrub.py, shared with the bug-report path.
_scrub_urls = scrub_urls


def _safe_int(val):
    """Convert Qt enum or attribute value to int (Qt5 returns int, Qt6 returns enum)."""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(val.value)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None


# Codes whose usual cause is a network that needs a proxy QGIS does not use.
_PROXY_HINT_CODES = frozenset({"DNS_ERROR", "TIMEOUT", "CONNECTION_REFUSED", "NO_NETWORK"})


def _qgis_proxy_enabled() -> bool:
    """The "Use proxy for web access" box in QGIS's network options. True
    when it cannot be read, so an unreadable profile never adds the hint."""
    try:
        from qgis.core import QgsSettings

        return bool(QgsSettings().value("proxy/proxyEnabled", False, type=bool))
    except Exception:  # nosec B110
        return True


def network_setup_hint(code: str) -> str:
    """Extra next step for a connectivity code when QGIS sends no proxy.

    Many company networks only let traffic out through a proxy. A browser
    finds it on its own, QGIS does not, so the same failure reads as a dead
    connection. Empty when the hint does not apply."""
    if (code or "").strip().upper() not in _PROXY_HINT_CODES:
        return ""
    if _qgis_proxy_enabled():
        return ""
    return tr(
        "If your browser works, turn on Settings > Options > Network > "
        "Use proxy for web access."
    )


_qgis_timeout_logged = False


def _qgis_network_timeout_ms() -> int:
    """QGIS's own network timeout (Settings > Options > Network), 0 if unknown."""
    global _qgis_timeout_logged
    try:
        from qgis.core import QgsNetworkAccessManager

        value = int(QgsNetworkAccessManager.timeout())
    except Exception:  # nosec B110
        return 0
    if not _qgis_timeout_logged:
        _qgis_timeout_logged = True
        log_debug(f"QGIS network timeout: {value} ms")
    return value


def qgis_timeout_hint(timeout_ms: int | None) -> str:
    """Next step when QGIS's global timeout, not ours, may have cut a request.

    The QGIS timeout aborts every request that runs longer, whatever the
    request asked for. Empty when it is at least ``timeout_ms``."""
    timeout_ms = _safe_int(timeout_ms)
    if not timeout_ms or timeout_ms < 0:
        return ""
    qgis_ms = _qgis_network_timeout_ms()
    if 0 < qgis_ms < int(timeout_ms):
        return tr("Raise the timeout in Settings > Options > Network.")
    return ""


def _join_hints(message: str, *hints: str) -> str:
    return " ".join([message] + [h for h in hints if h])


def _body_is_json(reply) -> bool:
    """True when the reply body parses as a JSON object (our server's shape)."""
    try:
        raw = bytes(reply.content()).decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        return (isinstance(parsed, dict) and isinstance(parsed.get("error"), str)
                and bool(parsed["error"]) and isinstance(parsed.get("code"), str)
                and bool(parsed["code"]))
    except Exception:
        return False


def _reply_failed(err, blocker: QgsBlockingNetworkRequest) -> bool:
    """True when a blocking request did not complete.

    QgsBlockingNetworkRequest answers NoError when the per-request transfer
    timeout or the QGIS network timeout aborts the reply: only the reply's own
    error (OperationCanceledError, no status, empty body) shows it. Reading
    NoError alone turned those into empty successes."""
    if err != QtC.BlockingNoError:
        return True
    try:
        reply = blocker.reply()
    except Exception:
        return True
    if reply is None:
        return True
    try:
        return reply.error() != QtC.NetworkNoError
    except Exception:
        return False


def _classify_network_error(
    blocker: QgsBlockingNetworkRequest,
    timeout_ms: int | None = None,
) -> tuple[str, str]:
    """Map a QgsBlockingNetworkRequest failure to (error_code, user_message).

    Also logs full diagnostics for bug reports. ``timeout_ms`` is the
    transfer timeout the request asked for; it decides whether a TIMEOUT
    also points at the QGIS network timeout.
    """
    try:
        reply = blocker.reply()
    except Exception:
        reply = None
    qt_error = reply.error() if reply is not None else QtC.UnknownNetworkError
    error_string = blocker.errorMessage() or ""

    http_status = None
    if reply:
        attr = reply.attribute(QtC.HttpStatusCodeAttribute)
        if attr is not None:
            http_status = _safe_int(attr)

    log_warning(
        f"Network error: qt_error={_safe_int(qt_error)}, http_status={http_status}, "
        f"detail={scrub_urls(error_string)[:500]}"
    )

    if qt_error == QtC.HostNotFoundError:
        return (
            "DNS_ERROR",
            _join_hints(
                tr("Cannot reach the server. Check your internet connection."),
                network_setup_hint("DNS_ERROR"),
            ),
        )

    if qt_error == QtC.ConnectionRefusedError_:
        return (
            "CONNECTION_REFUSED",
            _join_hints(
                tr("Server refused the connection. The service may be temporarily down."),
                network_setup_hint("CONNECTION_REFUSED"),
            ),
        )

    # An aborted reply with no feedback cancel behind it is a timeout: ours
    # (setTransferTimeout) or the QGIS global one. Callers check the feedback
    # before they get here.
    if qt_error in (QtC.TimeoutError_, QtC.OperationCanceledError):
        return (
            "TIMEOUT",
            _join_hints(
                tr("Request timed out. Check your connection or try again."),
                qgis_timeout_hint(timeout_ms),
                network_setup_hint("TIMEOUT"),
            ),
        )

    if qt_error == QtC.SslHandshakeFailedError:
        return (
            "SSL_ERROR",
            tr(
                "Secure connection failed, often because of company SSL inspection. "
                "Ask your IT team to allow terra-lab.ai, or import your company root "
                "certificate in Settings > Options > Authentication."
            ),
        )

    if qt_error in QtC.PROXY_ERRORS:
        return (
            "PROXY_ERROR",
            tr(
                "Proxy connection failed. "
                "Check QGIS proxy settings (Settings > Options > Network)."
            ),
        )

    if qt_error in (QtC.ContentAccessDenied, QtC.AuthenticationRequiredError):
        # Our server always answers 401/403 with a JSON body. Anything else
        # (an HTML block page, a firewall challenge) came from a filter on the
        # user's network and says nothing about the key: calling it an auth
        # failure would sign a paying user out.
        if reply is not None and not _body_is_json(reply):
            return (
                "NETWORK_BLOCKED",
                tr(
                    "Your network blocked the connection (HTTP {status}). "
                    "Ask your IT team to allow terra-lab.ai."
                ).format(status=http_status if http_status is not None else "?"),
            )
        return (
            "AUTH_ERROR",
            tr("Authentication failed. Check your activation key."),
        )

    # An oversized request body is rejected by the platform (often before our
    # handler runs) as 413. Without this branch it falls through to the generic
    # "check your connection" message, which misleads the user into blaming
    # their network instead of removing a reference image.
    if http_status == 429:
        return "RATE_LIMITED", tr("The service is busy. Please try again shortly.")
    if http_status == 408:
        return "TIMEOUT", tr("Request timed out. Check your connection or try again.")

    if http_status == 413:
        return (
            "PAYLOAD_TOO_LARGE",
            tr(
                "Too much image data to send. Remove a reference image or lower "
                "the resolution, then try again."
            ),
        )

    # The server DID answer, with a failure status whose body was not
    # parseable JSON (typically a bare infrastructure incident page). The
    # user's connection worked, so "check your internet" points them at the
    # wrong side. SERVER_ERROR is already a known transient code (localizer,
    # report policy), and the activation flow keeps the session on it.
    if http_status is not None and http_status >= 500:
        return (
            "SERVER_ERROR",
            get_export_copy(
                "pipeline.network_error_classifier.server_error",
                tr(
                    "The service is temporarily unavailable (server error). "
                    "Your connection is fine - please try again in a few minutes."
                ),
            ),
        )

    # Fallback. Canonical code is NO_NETWORK (ErrorCode enum) so every consumer
    # (inline-only set, retry list, message localizer) treats it as a handled
    # network failure instead of opening the bug-report dialog.
    return (
        "NO_NETWORK",
        _join_hints(
            tr("Network error. Check your internet connection."),
            network_setup_hint("NO_NETWORK"),
        ),
    )


# Request-scoped cancel handle. A task sets it for its own worker thread, so
# every client call made inside run() can be aborted by task.cancel() without
# threading a parameter through each public method. Thread-local because one
# client instance serves every task at once.
_request_scope = threading.local()

# The code a request aborted by its feedback answers with. Every consumer
# already treats it as a cancel: no toast, no report, no failure event.
CANCELLED_CODE = ErrorCode.GENERATION_CANCELLED.value


@contextmanager
def request_feedback(feedback):
    """Route every client call made on this thread inside the block through
    ``feedback``, so ``feedback.cancel()`` (safe from any thread) aborts the
    request in flight instead of waiting for its timeout."""
    previous = getattr(_request_scope, "feedback", None)
    _request_scope.feedback = feedback
    try:
        yield feedback
    finally:
        _request_scope.feedback = previous


def _current_feedback():
    return getattr(_request_scope, "feedback", None)


def _is_feedback_cancelled(feedback) -> bool:
    if feedback is None:
        return False
    try:
        return bool(feedback.isCanceled())
    except Exception:
        return False


def _cancelled_result() -> dict:
    return {"error": tr("Request cancelled."), "code": CANCELLED_CODE}


class DownloadError(RuntimeError):
    """A failed image download, carrying a structured `code` so the refund
    event / ops alert can say WHY (TIMEOUT, SSL_ERROR, INCOMPLETE, ...)
    instead of a bare "download_failed".
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# Smallest plausible real image. A transient proxy/CDN error body
# ({"code":"UPSTREAM_UNAVAILABLE"} or an HTML error page) is well under this,
# so the floor also catches non-image payloads that slip past the magic-byte
# check below.
_MIN_IMAGE_BYTES = 64


def _looks_like_image(data: bytes) -> bool:
    """True if the bytes start with a known raster image signature.

    The image proxy can answer a slow/transient backend with a 200 and a tiny
    JSON/HTML error body (a storage hiccup, an upstream 503 surfaced as a branded
    page). Those bytes are not an image: writing them produces a corrupt
    GeoTIFF that fails later as a write error. Detecting them here instead lets
    the caller's retry loop re-download (escalating to the stream=1 bypass),
    so a recoverable blip never becomes a lost generation.
    """
    if len(data) < 12:
        return False
    signatures = (
        data[:8] == b"\x89PNG\r\n\x1a\n",  # PNG
        data[:3] == b"\xff\xd8\xff",  # JPEG
        data[:4] == b"RIFF" and data[8:12] == b"WEBP",  # WebP
        data[:6] in (b"GIF87a", b"GIF89a"),  # GIF
        data[:4] in (b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b"),  # TIFF
        data[:2] == b"BM",  # BMP
        data[4:8] == b"ftyp" and data[8:12] in (
            b"avif", b"avis", b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1",
        ),  # Image brands only: MP4 uses the same container signature.
    )
    return any(signatures)
