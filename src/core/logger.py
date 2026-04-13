from __future__ import annotations

from qgis.core import Qgis, QgsMessageLog

TAG = "AI Edit"


def log(message, level=Qgis.MessageLevel.Info):
    """Log to QGIS Log Messages panel (visible to user)."""
    QgsMessageLog.logMessage(message, TAG, level=level)


def log_warning(message):
    """Log a warning to QGIS Log Messages panel."""
    log(message, level=Qgis.MessageLevel.Warning)


def log_debug(message):
    """Log to QGIS Log Messages panel (same as log, kept for call-site compat)."""
    log(message)
