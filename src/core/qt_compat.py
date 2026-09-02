"""Qt5/Qt6 compatibility shim for scoped enums.

Qt6 (QGIS 4) moved flat enums like ``Qt.LeftDockWidgetArea`` into nested
scopes: ``Qt.DockWidgetArea.LeftDockWidgetArea``.  This module resolves
them once at import time so the rest of the codebase stays clean.
"""
from __future__ import annotations

from qgis.core import QgsBlockingNetworkRequest, QgsRaster, QgsTask
from qgis.PyQt.QtCore import QIODevice, QObject, QStandardPaths, Qt, QTimer
from qgis.PyQt.QtGui import QImage, QPainter, QPalette, QTextCursor, QTextOption
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
AlignBottom = _resolve(Qt, "AlignmentFlag", "AlignBottom")
AlignLeft = _resolve(Qt, "AlignmentFlag", "AlignLeft")
AlignVCenter = _resolve(Qt, "AlignmentFlag", "AlignVCenter")

# Qt.Key
Key_Return = _resolve(Qt, "Key", "Key_Return")
Key_Enter = _resolve(Qt, "Key", "Key_Enter")
Key_Escape = _resolve(Qt, "Key", "Key_Escape")
Key_Backspace = _resolve(Qt, "Key", "Key_Backspace")
Key_Delete = _resolve(Qt, "Key", "Key_Delete")

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

# Qt.TextInteractionFlag
LinksAccessibleByMouse = _resolve(Qt, "TextInteractionFlag", "LinksAccessibleByMouse")

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

# QStandardPaths.StandardLocation
CacheLocation = _resolve(QStandardPaths, "StandardLocation", "CacheLocation")

# QImage.Format
FormatARGB32 = _resolve(QImage, "Format", "Format_ARGB32")

# QPainter.CompositionMode - what QgsMapLayer.blendMode() returns. SourceOver is
# the default, i.e. "paints over whatever is underneath".
CompositionModeSourceOver = _resolve(
    QPainter, "CompositionMode", "CompositionMode_SourceOver"
)

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
    # Qgis.LayerType is the only spelling left on QGIS 4; QgsMapLayer.LayerType
    # is the QGIS 3 one. Same scoped-then-flat treatment as the geometry types.
    RasterLayerType = getattr(getattr(Qgis, "LayerType", None), "Raster", None)
except Exception:
    PolygonGeometry = None
    LineGeometry = None
    RasterLayerType = None
if PolygonGeometry is None:
    from qgis.core import QgsWkbTypes
    PolygonGeometry = _resolve(QgsWkbTypes, "GeometryType", "PolygonGeometry")
if LineGeometry is None:
    from qgis.core import QgsWkbTypes
    LineGeometry = _resolve(QgsWkbTypes, "GeometryType", "LineGeometry")
if RasterLayerType is None:
    from qgis.core import QgsMapLayer
    RasterLayerType = _resolve(QgsMapLayer, "LayerType", "RasterLayer")

# QgsRaster.IdentifyFormat (scoped on QGIS 4, flat attribute on QGIS 3)
IdentifyFormatValue = _resolve(QgsRaster, "IdentifyFormat", "IdentifyFormatValue")

# QgsVertexMarker.IconType.ICON_CIRCLE - scoped on QGIS 4, flat also on QGIS 3.
try:
    from qgis.gui import QgsVertexMarker
    VertexIconCircle = _resolve(QgsVertexMarker, "IconType", "ICON_CIRCLE")
except Exception:
    VertexIconCircle = None


# QNetworkReply.NetworkError
def _net_enum(name: str):
    return _resolve(QNetworkReply, "NetworkError", name)


NetworkNoError = _net_enum("NoError")
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


def set_transfer_timeout(request: QNetworkRequest, msec: int) -> bool:
    """Apply a per-request transfer timeout when the running Qt has one.

    ``QNetworkRequest.setTransferTimeout`` landed in Qt 5.15, which every build
    in the declared range ships (the 3.22 floor already requires it), so the
    guard is now belt and braces rather than load-bearing. It stays because a
    missing method here raises AttributeError on every single request, and that
    would kill the plugin outright on any build we guessed wrong about. Returns True when the
    timeout was applied; on an older Qt the request just falls back to Qt's own
    (much longer) socket timeouts, which is slow but still works.
    """
    setter = getattr(request, "setTransferTimeout", None)
    if setter is None:
        return False
    try:
        setter(int(msec))
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def silent_task_flags(can_cancel: bool = True):
    """CanCancel plus Hidden/Silent when the running QGIS exposes them.

    Hidden / Silent landed in QGIS 3.26; the plugin floor (metadata.txt
    qgisMinimumVersion) is older, so resolve each flag defensively. Naming
    QgsTask.Flag.Hidden directly would AttributeError at import on older builds;
    there the task degrades to a plain (visible) cancellable task, which is
    harmless. Keeping startup/background requests hidden stops the task-manager
    widget from filling with alarming "AI Edit ..." rows on every launch.
    """
    flags = QgsTask.Flag.CanCancel if can_cancel else QgsTask.Flag(0)
    for name in ("Hidden", "Silent"):
        flag = getattr(QgsTask.Flag, name, None)
        if flag is not None:
            flags = flags | flag
    return flags


def _gui_or_widget_class(name: str):
    """Resolve a class that Qt6 moved from QtWidgets into QtGui.

    Measured by importing each name on both builds: on QGIS 3.22.0 (Qt5)
    ``QAction``, ``QActionGroup``, ``QShortcut``, ``QUndoCommand``,
    ``QUndoStack``, ``QUndoGroup`` and ``QFileSystemModel`` all sit in
    QtWidgets and the QtGui spelling raises ImportError; on QGIS 4.0.0 all
    seven answer from QtGui and the last four have left QtWidgets. Neither
    module alone covers both, so naming one directly is a load-time failure on
    the other across the whole range metadata.txt advertises (3.22 to 4.99).
    Try the Qt6 home first, fall back to the Qt5 one.
    """
    from qgis.PyQt import QtGui, QtWidgets

    found = getattr(QtGui, name, None)
    return found if found is not None else getattr(QtWidgets, name)


QAction = _gui_or_widget_class("QAction")
QActionGroup = _gui_or_widget_class("QActionGroup")
QShortcut = _gui_or_widget_class("QShortcut")


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


def safe_disconnect(owner, signal_name: str, slot=None) -> bool:
    """Disconnect one signal on ``owner``, swallowing the three teardown faults.

    Each of the three is normal during teardown, none is worth a raise:
    TypeError means this slot was never connected (a second cleanup() call, or
    a widget built while the project swapped its layer tree), RuntimeError
    means the C++ half of ``owner`` is already deleted, and AttributeError
    means ``owner`` is None. Reading ``owner.signal_name`` can itself raise the
    last two, so the lookup sits inside the guard. One call per signal, never
    one try block around a batch: the first failure would skip the rest and
    leave live connections firing into a destroyed widget. True when the
    disconnect happened.
    """
    try:
        signal = getattr(owner, signal_name)
        if slot is None:
            signal.disconnect()
        else:
            signal.disconnect(slot)
        return True
    except (TypeError, RuntimeError, AttributeError):
        return False
