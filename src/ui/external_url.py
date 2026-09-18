"""Scheme allow-list gate for URLs opened in the user's browser."""
from __future__ import annotations

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices

from ..core.logger import log_warning

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def open_external(url: str) -> bool:
    """Open ``url`` in the default browser only when its scheme is http(s).

    Server/config-derived URLs must never reach QDesktopServices with an
    arbitrary scheme (file:, javascript:, custom OS handlers). Returns
    False (and logs) instead of opening anything else.
    """
    qurl = QUrl((url or "").strip())
    scheme = qurl.scheme().lower()
    if scheme not in _ALLOWED_SCHEMES:
        # Log the scheme only, never the full URL.
        log_warning(f"Blocked external URL with disallowed scheme: {scheme or '(none)'}")
        return False
    return QDesktopServices.openUrl(qurl)


def open_mailto(url: str) -> bool:
    """Hand a ``mailto:`` link to the OS mail client. Kept apart from
    ``open_external`` on purpose: a served http link must never turn into a
    mail scheme, and a mail link must never reach the browser gate. Returns
    False when the scheme is anything else or the OS has no handler, so the
    caller can fall back to showing the address."""
    qurl = QUrl((url or "").strip())
    if qurl.scheme().lower() != "mailto":
        log_warning(f"Blocked mail link with disallowed scheme: {qurl.scheme() or '(none)'}")
        return False
    try:
        return bool(QDesktopServices.openUrl(qurl))
    except Exception:  # noqa: BLE001 - no mail handler; the caller shows the address.
        return False
