
from __future__ import annotations

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest

from ..core import qt_compat as QtC
from ..core.config_store import get_export_copy, get_export_dial
from ..core.i18n import tr
from ..core.logger import log_debug
from .network_error_classifier import (
    _MIN_IMAGE_BYTES,
    CANCELLED_CODE,
    DownloadError,
    _classify_network_error,
    _current_feedback,
    _is_feedback_cancelled,
    _looks_like_image,
    _reply_failed,
    _safe_int,
)
from .network_response import transfer_timeout, valid_http_url

_TIMEOUT_DOWNLOAD = 180_000


def download_image(url: str, blocker_factory) -> bytes:

    if _is_feedback_cancelled(_current_feedback()):
        raise DownloadError(CANCELLED_CODE, tr("Request cancelled."))
    if not valid_http_url(url):
        raise DownloadError("CLIENT_ERROR", tr("Invalid server response"))
    req = QNetworkRequest(QUrl(url))


    req.setAttribute(QtC.RedirectPolicyAttribute, QtC.NoLessSafeRedirectPolicy)
    timeout_ms = transfer_timeout(get_export_dial("timeouts_ms.download", _TIMEOUT_DOWNLOAD), _TIMEOUT_DOWNLOAD)
    QtC.set_transfer_timeout(req, timeout_ms)

    feedback = _current_feedback()
    if _is_feedback_cancelled(feedback):
        raise DownloadError(CANCELLED_CODE, tr("Request cancelled."))
    blocker = blocker_factory()
    err = blocker.get(req, forceRefresh=True, feedback=feedback)

    if _is_feedback_cancelled(feedback):
        raise DownloadError(CANCELLED_CODE, tr("Request cancelled."))
    if _reply_failed(err, blocker):
        code, msg = _classify_network_error(blocker, timeout_ms)
        raise DownloadError(
            code, tr("Download failed ({code}): {msg}").format(code=code, msg=msg)
        )

    reply = blocker.reply()
    http_status = reply.attribute(QtC.HttpStatusCodeAttribute)
    if (_safe_int(http_status) or 0) >= 400:
        raise DownloadError(
            f"HTTP_{_safe_int(http_status)}",
            tr("Download failed: HTTP {status}").format(status=http_status),
        )

    if _safe_int(http_status) != 200:
        raise DownloadError("INCOMPLETE", tr("Invalid server response"))
    data = bytes(reply.content())
    content_type = reply.rawHeader(b"Content-Type")
    ct_str = bytes(content_type).decode("ascii", errors="replace") if content_type else "?"
    log_debug(
        f"Downloaded {len(data)} bytes, content-type={ct_str[:80]}"
    )




    if not data:
        raise DownloadError(
            "EMPTY_BODY",
            get_export_copy(
                "pipeline.terralab_client.empty_response", tr("Server returned an empty response (0 bytes)")
            ),
        )






    min_bytes = get_export_dial("download.min_image_bytes", _MIN_IMAGE_BYTES)
    if len(data) < min_bytes or not _looks_like_image(data):
        raise DownloadError(
            "NOT_IMAGE",
            get_export_copy(
                "pipeline.terralab_client.not_image",
                tr("Server returned a non-image response, retrying download"),
            ),
        )
    declared = reply.rawHeader(b"Content-Length")
    encoding = reply.rawHeader(b"Content-Encoding")
    if declared and (not encoding or bytes(encoding).lower() == b"identity"):
        expected = _safe_int(bytes(declared).decode("ascii", errors="replace"))
        if expected is not None and expected >= 0 and len(data) != expected:
            raise DownloadError(
                "INCOMPLETE",
                tr("Download incomplete: received {got} of {total} bytes").format(
                    got=len(data), total=expected
                ),
            )
    return data
