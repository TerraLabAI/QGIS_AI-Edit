
from __future__ import annotations

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices

from ..core.logger import log_warning

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def open_external(url: str) -> bool:






    qurl = QUrl((url or "").strip())
    scheme = qurl.scheme().lower()
    if scheme not in _ALLOWED_SCHEMES:

        log_warning(f"Blocked external URL with disallowed scheme: {scheme or '(none)'}")
        return False
    return QDesktopServices.openUrl(qurl)


def open_mailto(url: str) -> bool:





    qurl = QUrl((url or "").strip())
    if qurl.scheme().lower() != "mailto":
        log_warning(f"Blocked mail link with disallowed scheme: {qurl.scheme() or '(none)'}")
        return False
    try:
        return bool(QDesktopServices.openUrl(qurl))
    except Exception:  # noqa: BLE001
        return False
