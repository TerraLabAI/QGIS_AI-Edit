"""Qt5/Qt6 compatibility shim for scoped enums.

Qt6 (QGIS 4) moved flat enums like ``Qt.LeftDockWidgetArea`` into nested
scopes: ``Qt.DockWidgetArea.LeftDockWidgetArea``.  This module resolves
them once at import time so the rest of the codebase stays clean.
"""
from __future__ import annotations

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QIODevice, Qt
from qgis.PyQt.QtGui import QImage, QTextCursor
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import QFrame, QSizePolicy


def _resolve(parent, scope: str | None, name: str):
    if scope:
        scoped = getattr(getattr(parent, scope, None), name, None)
        if scoped is not None:
            return scoped
    return getattr(parent, name)


# Qt.DockWidgetArea
LeftDockWidgetArea = _resolve(Qt, "DockWidgetArea", "LeftDockWidgetArea")
RightDockWidgetArea = _resolve(Qt, "DockWidgetArea", "RightDockWidgetArea")

# Qt.CursorShape
PointingHandCursor = _resolve(Qt, "CursorShape", "PointingHandCursor")
CrossCursor = _resolve(Qt, "CursorShape", "CrossCursor")

# Qt.AlignmentFlag
AlignCenter = _resolve(Qt, "AlignmentFlag", "AlignCenter")
AlignTop = _resolve(Qt, "AlignmentFlag", "AlignTop")

# Qt.Key
Key_Return = _resolve(Qt, "Key", "Key_Return")
Key_Enter = _resolve(Qt, "Key", "Key_Enter")
Key_Escape = _resolve(Qt, "Key", "Key_Escape")

# Qt.KeyboardModifier
ShiftModifier = _resolve(Qt, "KeyboardModifier", "ShiftModifier")

# Qt.MouseButton
LeftButton = _resolve(Qt, "MouseButton", "LeftButton")

# Qt.ToolButtonStyle
ToolButtonTextBesideIcon = _resolve(Qt, "ToolButtonStyle", "ToolButtonTextBesideIcon")

# Qt.ArrowType
DownArrow = _resolve(Qt, "ArrowType", "DownArrow")
RightArrow = _resolve(Qt, "ArrowType", "RightArrow")

# Qt.TextFormat
RichText = _resolve(Qt, "TextFormat", "RichText")
PlainText = _resolve(Qt, "TextFormat", "PlainText")

# Qt.WidgetAttribute
WA_TransparentForMouseEvents = _resolve(Qt, "WidgetAttribute", "WA_TransparentForMouseEvents")

# Qt.ScrollBarPolicy
ScrollBarAlwaysOff = _resolve(Qt, "ScrollBarPolicy", "ScrollBarAlwaysOff")

# Qt.TextInteractionFlag
TextSelectableByMouse = _resolve(Qt, "TextInteractionFlag", "TextSelectableByMouse")
TextBrowserInteraction = _resolve(Qt, "TextInteractionFlag", "TextBrowserInteraction")

# QIODevice.OpenModeFlag
WriteOnly = _resolve(QIODevice, "OpenModeFlag", "WriteOnly")

# QImage.Format
FormatARGB32 = _resolve(QImage, "Format", "Format_ARGB32")

# QTextCursor.MoveOperation
CursorEnd = _resolve(QTextCursor, "MoveOperation", "End")

# QSizePolicy.Policy
SizePolicyExpanding = _resolve(QSizePolicy, "Policy", "Expanding")
SizePolicyFixed = _resolve(QSizePolicy, "Policy", "Fixed")

# QFrame.Shape / QFrame.Shadow
FrameNoFrame = _resolve(QFrame, "Shape", "NoFrame")
FrameHLine = _resolve(QFrame, "Shape", "HLine")
FrameVLine = _resolve(QFrame, "Shape", "VLine")
FrameSunken = _resolve(QFrame, "Shadow", "Sunken")

# QgsBlockingNetworkRequest.ErrorCode
BlockingNoError = _resolve(QgsBlockingNetworkRequest, "ErrorCode", "NoError")

# Qgis.GeometryType (QGIS 4) vs QgsWkbTypes (QGIS 3)
try:
    from qgis.core import Qgis
    PolygonGeometry = getattr(
        getattr(Qgis, "GeometryType", None), "Polygon",
        None,
    )
except Exception:
    PolygonGeometry = None
if PolygonGeometry is None:
    from qgis.core import QgsWkbTypes
    PolygonGeometry = QgsWkbTypes.PolygonGeometry


# QNetworkReply.NetworkError
def _net_enum(name: str):
    return _resolve(QNetworkReply, "NetworkError", name)


HostNotFoundError = _net_enum("HostNotFoundError")
ConnectionRefusedError_ = _net_enum("ConnectionRefusedError")
TimeoutError_ = _net_enum("TimeoutError")
SslHandshakeFailedError = _net_enum("SslHandshakeFailedError")
ContentAccessDenied = _net_enum("ContentAccessDenied")
AuthenticationRequiredError = _net_enum("AuthenticationRequiredError")
UnknownNetworkError = _net_enum("UnknownNetworkError")

PROXY_ERRORS = {
    _net_enum("ProxyConnectionRefusedError"),
    _net_enum("ProxyConnectionClosedError"),
    _net_enum("ProxyNotFoundError"),
    _net_enum("ProxyTimeoutError"),
    _net_enum("ProxyAuthenticationRequiredError"),
    _net_enum("UnknownProxyError"),
}

# QNetworkRequest.Attribute
HttpStatusCodeAttribute = _resolve(
    QNetworkRequest, "Attribute", "HttpStatusCodeAttribute"
)
