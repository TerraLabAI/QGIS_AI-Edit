








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


_scrub_urls = scrub_urls


def _safe_int(val):

    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(val.value)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None



_PROXY_HINT_CODES = frozenset({"DNS_ERROR", "TIMEOUT", "CONNECTION_REFUSED", "NO_NETWORK"})


def _qgis_proxy_enabled() -> bool:


    try:
        from qgis.core import QgsSettings

        return bool(QgsSettings().value("proxy/proxyEnabled", False, type=bool))
    except Exception:  # nosec B110
        return True


def network_setup_hint(code: str) -> str:





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

    try:
        raw = bytes(reply.content()).decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        return (isinstance(parsed, dict) and isinstance(parsed.get("error"), str)
                and bool(parsed["error"]) and isinstance(parsed.get("code"), str)
                and bool(parsed["code"]))
    except Exception:
        return False


def _reply_failed(err, blocker: QgsBlockingNetworkRequest) -> bool:






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




    return (
        "NO_NETWORK",
        _join_hints(
            tr("Network error. Check your internet connection."),
            network_setup_hint("NO_NETWORK"),
        ),
    )






_request_scope = threading.local()



CANCELLED_CODE = ErrorCode.GENERATION_CANCELLED.value


@contextmanager
def request_feedback(feedback):



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





    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code






_MIN_IMAGE_BYTES = 64


def _looks_like_image(data: bytes) -> bool:









    if len(data) < 12:
        return False
    signatures = (
        data[:8] == b"\x89PNG\r\n\x1a\n",
        data[:3] == b"\xff\xd8\xff",
        data[:4] == b"RIFF" and data[8:12] == b"WEBP",
        data[:6] in (b"GIF87a", b"GIF89a"),
        data[:4] in (b"II\x2a\x00", b"MM\x00\x2a", b"II\x2b\x00", b"MM\x00\x2b"),
        data[:2] == b"BM",
        data[4:8] == b"ftyp" and data[8:12] in (
            b"avif", b"avis", b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1",
        ),
    )
    return any(signatures)
