from __future__ import annotations

from .log_scrub import scrub_secrets, scrub_urls, scrub_user_paths



try:
    from qgis.core import Qgis, QgsMessageLog
    _INFO = Qgis.MessageLevel.Info
    _WARNING = Qgis.MessageLevel.Warning
except ImportError:
    QgsMessageLog = None
    _INFO = None
    _WARNING = None

TAG = "AI Edit"


def log(message, level=None):

    if QgsMessageLog is None:
        return
    safe_message = scrub_user_paths(scrub_urls(scrub_secrets(str(message))))

    try:
        QgsMessageLog.logMessage(safe_message, TAG, level=_INFO if level is None else level)
    except RuntimeError:
        return


def log_warning(message):

    log(message, level=_WARNING)


def log_debug(message):

    log(message)
