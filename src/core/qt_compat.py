"""Qt5/Qt6 compatibility shim for scoped enums.

Qt6 (QGIS 4) moved flat enums like ``Qt.LeftDockWidgetArea`` into nested
scopes: ``Qt.DockWidgetArea.LeftDockWidgetArea``.  This module resolves
them once at import time so the rest of the codebase stays clean.
"""
from __future__ import annotations

from qgis.core import QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QIODevice, QObject, Qt, QTimer
from qgis.PyQt.QtGui import QImage, QPalette, QTextCursor, QTextOption
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import QFrame, QSizePolicy, QTextEdit


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
WaitCursor = _resolve(Qt, "CursorShape", "WaitCursor")
ArrowCursor = _resolve(Qt, "CursorShape", "ArrowCursor")

# Qt.AlignmentFlag
AlignCenter = _resolve(Qt, "AlignmentFlag", "AlignCenter")
AlignTop = _resolve(Qt, "AlignmentFlag", "AlignTop")
AlignLeft = _resolve(Qt, "AlignmentFlag", "AlignLeft")
AlignVCenter = _resolve(Qt, "AlignmentFlag", "AlignVCenter")

# Qt.Key
Key_Return = _resolve(Qt, "Key", "Key_Return")
Key_Enter = _resolve(Qt, "Key", "Key_Enter")
Key_Escape = _resolve(Qt, "Key", "Key_Escape")

# Qt.ShortcutContext
WindowShortcut = _resolve(Qt, "ShortcutContext", "WindowShortcut")
WidgetWithChildrenShortcut = _resolve(
    Qt, "ShortcutContext", "WidgetWithChildrenShortcut"
)


def event_pos(event):
    """Return a Qt5/Qt6-safe QPoint for a QMouseEvent or QgsMapMouseEvent.

    Qt6 deprecates ``QMouseEvent.pos()`` in favour of
    ``position().toPoint()``; use this wrapper everywhere a mouse event's
    widget-local position is needed so the same source runs on QGIS 3 and 4.
    """
    if hasattr(event, "position"):
        try:
            return event.position().toPoint()
        except (AttributeError, TypeError):
            pass
    return event.pos()


# Qt.KeyboardModifier
ShiftModifier = _resolve(Qt, "KeyboardModifier", "ShiftModifier")

# Qt.MouseButton
LeftButton = _resolve(Qt, "MouseButton", "LeftButton")
RightButton = _resolve(Qt, "MouseButton", "RightButton")

# Qt.FocusPolicy
NoFocus = _resolve(Qt, "FocusPolicy", "NoFocus")

# Qt.FocusReason
OtherFocusReason = _resolve(Qt, "FocusReason", "OtherFocusReason")

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
WA_StyledBackground = _resolve(Qt, "WidgetAttribute", "WA_StyledBackground")

# Qt.ScrollBarPolicy
ScrollBarAlwaysOff = _resolve(Qt, "ScrollBarPolicy", "ScrollBarAlwaysOff")
ScrollBarAsNeeded = _resolve(Qt, "ScrollBarPolicy", "ScrollBarAsNeeded")

# QTextOption.WrapMode - wrap mid-token so a long URL or unbreakable string
# still flows to the next line instead of triggering horizontal scroll.
WrapAtWordBoundaryOrAnywhere = _resolve(
    QTextOption, "WrapMode", "WrapAtWordBoundaryOrAnywhere"
)

# QTextEdit.LineWrapMode - pinned to widget width so wrapping always engages
# even when QSS or a rich-text paste would otherwise leave it implicit.
LineWrapWidgetWidth = _resolve(QTextEdit, "LineWrapMode", "WidgetWidth")

# Qt.AspectRatioMode / Qt.TransformationMode
KeepAspectRatio = _resolve(Qt, "AspectRatioMode", "KeepAspectRatio")
KeepAspectRatioByExpanding = _resolve(Qt, "AspectRatioMode", "KeepAspectRatioByExpanding")
SmoothTransformation = _resolve(Qt, "TransformationMode", "SmoothTransformation")

# Qt.PenStyle
NoPen = _resolve(Qt, "PenStyle", "NoPen")

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

# QPalette.ColorRole
PaletteBase = _resolve(QPalette, "ColorRole", "Base")

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
    _gt = getattr(Qgis, "GeometryType", None)
    PolygonGeometry = getattr(_gt, "Polygon", None)
    LineGeometry = getattr(_gt, "Line", None)
except Exception:
    PolygonGeometry = None
    LineGeometry = None
if PolygonGeometry is None:
    from qgis.core import QgsWkbTypes
    PolygonGeometry = QgsWkbTypes.PolygonGeometry
if LineGeometry is None:
    from qgis.core import QgsWkbTypes
    LineGeometry = QgsWkbTypes.LineGeometry


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
# Redirect policy. PyQt5 on some QGIS 3 builds exposes these flat, not scoped,
# so resolve through the same scoped-then-flat helper rather than hardcoding
# QNetworkRequest.Attribute.* / QNetworkRequest.RedirectPolicy.* (which would
# AttributeError on those builds, on every API request and download).
RedirectPolicyAttribute = _resolve(
    QNetworkRequest, "Attribute", "RedirectPolicyAttribute"
)
NoLessSafeRedirectPolicy = _resolve(
    QNetworkRequest, "RedirectPolicy", "NoLessSafeRedirectPolicy"
)


def safe_single_shot(msec: int, owner: QObject, callback) -> QTimer:
    """A single-shot timer bound to ``owner``'s lifetime.

    ``QTimer.singleShot(msec, lambda: widget.setText(...))`` keeps the lambda
    (and the widget it captures) alive in the global event loop. If the widget
    is destroyed before the timer fires, the deferred call lands on a freed C++
    object and segfaults QGIS, the classic "closed the dialog too fast" crash.

    Parenting the timer to ``owner`` makes Qt destroy the timer together with
    ``owner``, so it can never fire into a dead widget. Returns the timer so the
    caller can stop it early if needed.
    """
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(callback)
    timer.start(max(0, int(msec)))
    return timer
