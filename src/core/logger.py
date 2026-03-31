from qgis.core import QgsMessageLog, Qgis

TAG = "AI Edit"


def log(message, level=Qgis.MessageLevel.Info):
    QgsMessageLog.logMessage(message, TAG, level=level)


def log_warning(message):
    log(message, level=Qgis.MessageLevel.Warning)
