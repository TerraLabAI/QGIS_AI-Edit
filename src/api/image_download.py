
from __future__ import annotations

import re

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

_MIN_RESUME_BYTES = 16_384
_CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


def download_image(url: str, blocker_factory, resume: dict | None = None) -> bytes:







    if _is_feedback_cancelled(_current_feedback()):
        raise DownloadError(CANCELLED_CODE, tr("Request cancelled."))
    if not valid_http_url(url):
        raise DownloadError("CLIENT_ERROR", tr("Invalid server response"))
    have = b""
    total = None
    if resume is not None:
        have, total = resume.get("data") or b"", resume.get("total")
        if not have or not isinstance(total, int) or len(have) >= total:
            resume.clear()
            have, total = b"", None
    req = QNetworkRequest(QUrl(url))
    if have:
        req.setRawHeader(b"Range", f"bytes={len(have)}-".encode("ascii"))


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
    reply = blocker.reply()
    http_status = reply.attribute(QtC.HttpStatusCodeAttribute) if reply is not None else None
    status_int = _safe_int(http_status)
    if have and status_int in (200, 416) and _reply_failed(err, blocker):




        resume.clear()
        log_debug(f"Range ask answered {status_int}: downloading the whole image")
        return download_image(url, blocker_factory, resume=resume)
    if _reply_failed(err, blocker):
        _keep_partial(resume, reply, status_int, have, total)
        code, msg = _classify_network_error(blocker, timeout_ms)



        if code != "NO_NETWORK" or (_safe_int(http_status) or 0) < 400:
            raise DownloadError(
                code, tr("Download failed ({code}): {msg}").format(code=code, msg=msg)
            )

    if (_safe_int(http_status) or 0) >= 400:
        raise DownloadError(
            f"HTTP_{_safe_int(http_status)}",
            tr("Download failed: HTTP {status}").format(status=http_status),
        )

    if status_int == 206 and have:
        data = _joined_tail(reply, have, total)
        if data is None:
            resume.clear()
            raise DownloadError("INCOMPLETE", tr("Invalid server response"))
        log_debug(f"Download resumed at byte {len(have)} of {total}")
    elif status_int != 200:
        raise DownloadError("INCOMPLETE", tr("Invalid server response"))
    else:
        data = bytes(reply.content())
    if resume is not None:


        resume.clear()
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
    expected = None
    if status_int == 206:
        expected = total
    elif declared and (not encoding or bytes(encoding).lower() == b"identity"):
        expected = _safe_int(bytes(declared).decode("ascii", errors="replace"))
        if expected is not None and expected >= 0 and len(data) != expected:
            _keep_partial(resume, reply, status_int, b"", None)
            raise DownloadError(
                "INCOMPLETE",
                tr("Download incomplete: received {got} of {total} bytes").format(
                    got=len(data), total=expected
                ),
            )






    if (expected is None or expected < 0 or status_int == 206) and not _image_ends_whole(data):
        raise DownloadError(
            "INCOMPLETE",
            tr("Download incomplete: the image ends early ({got} bytes)").format(got=len(data)),
        )
    return data


def _keep_partial(resume: dict | None, reply, status: int | None, have: bytes, total: int | None) -> None:




    if resume is None or reply is None:
        return
    try:
        body = bytes(reply.content())
        encoding = bytes(reply.rawHeader(b"Content-Encoding") or b"").lower()
        if not body or encoding not in (b"", b"identity"):
            return
        if status == 200:
            declared = _safe_int(bytes(reply.rawHeader(b"Content-Length") or b"").decode("ascii", "replace"))
            if declared and len(body) < declared and len(body) >= _MIN_RESUME_BYTES:
                resume["data"], resume["total"] = body, declared
        elif status == 206 and have and total:
            match = _CONTENT_RANGE_RE.match(bytes(reply.rawHeader(b"Content-Range") or b"").decode("ascii", "replace"))
            if match and int(match.group(1)) == len(have) and int(match.group(3)) == total:
                joined = have + body
                if len(joined) < total:
                    resume["data"], resume["total"] = joined, total
    except Exception:  # noqa: BLE001
        resume.clear()


def _joined_tail(reply, have: bytes, total: int | None) -> bytes | None:


    match = _CONTENT_RANGE_RE.match(bytes(reply.rawHeader(b"Content-Range") or b"").decode("ascii", "replace"))
    if not match or not total:
        return None
    start, end, size = (int(match.group(i)) for i in (1, 2, 3))
    body = bytes(reply.content())
    if start != len(have) or size != total or end != total - 1 or len(body) != end - start + 1:
        return None
    return have + body



_PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"


def _image_ends_whole(data: bytes) -> bool:




    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _PNG_END in data
    if data[:3] == b"\xff\xd8\xff":

        return data.rstrip(b"\x00\r\n\t ")[-2:] == b"\xff\xd9"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return len(data) >= int.from_bytes(data[4:8], "little") + 8
    return True
