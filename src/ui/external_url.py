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
