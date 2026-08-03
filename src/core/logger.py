from __future__ import annotations

# Import-safe without QGIS so pure-logic modules stay testable headless
# (scripts/check.sh runs the headless pytest layer under plain python3).
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
    """Log to QGIS Log Messages panel (visible to user)."""
    if QgsMessageLog is None:
        return
    QgsMessageLog.logMessage(message, TAG, level=_INFO if level is None else level)


def log_warning(message):
    """Log a warning to QGIS Log Messages panel."""
    log(message, level=_WARNING)


def log_debug(message):
    """Log to QGIS Log Messages panel (same as log, kept for call-site compat)."""
    log(message)
