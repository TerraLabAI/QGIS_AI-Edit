





from __future__ import annotations

from qgis.core import QgsBlockingNetworkRequest, QgsRaster, QgsTask
from qgis.PyQt.QtCore import QIODevice, QLocale, QObject, QStandardPaths, Qt, QTimer
from qgis.PyQt.QtGui import QImage, QPainter, QPalette, QTextCursor, QTextOption
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import QFrame, QSizePolicy, QTextEdit


def _resolve(parent, scope: str | None, name: str):
    if scope:
        scoped = getattr(getattr(parent, scope, None), name, None)
        if scoped is not None:
            return scoped
    return getattr(parent, name)



LeftDockWidgetArea = _resolve(Qt, "DockWidgetArea", "LeftDockWidgetArea")
RightDockWidgetArea = _resolve(Qt, "DockWidgetArea", "RightDockWidgetArea")


PointingHandCursor = _resolve(Qt, "CursorShape", "PointingHandCursor")
CrossCursor = _resolve(Qt, "CursorShape", "CrossCursor")
WaitCursor = _resolve(Qt, "CursorShape", "WaitCursor")
ArrowCursor = _resolve(Qt, "CursorShape", "ArrowCursor")


AlignCenter = _resolve(Qt, "AlignmentFlag", "AlignCenter")
AlignTop = _resolve(Qt, "AlignmentFlag", "AlignTop")
AlignBottom = _resolve(Qt, "AlignmentFlag", "AlignBottom")
AlignLeft = _resolve(Qt, "AlignmentFlag", "AlignLeft")
AlignRight = _resolve(Qt, "AlignmentFlag", "AlignRight")
AlignVCenter = _resolve(Qt, "AlignmentFlag", "AlignVCenter")


Key_Return = _resolve(Qt, "Key", "Key_Return")
Key_Enter = _resolve(Qt, "Key", "Key_Enter")
Key_Escape = _resolve(Qt, "Key", "Key_Escape")
Key_Backspace = _resolve(Qt, "Key", "Key_Backspace")
Key_Delete = _resolve(Qt, "Key", "Key_Delete")


WindowShortcut = _resolve(Qt, "ShortcutContext", "WindowShortcut")
WidgetWithChildrenShortcut = _resolve(
    Qt, "ShortcutContext", "WidgetWithChildrenShortcut"
)


def event_pos(event):






    if hasattr(event, "position"):
        try:
            return event.position().toPoint()
        except (AttributeError, TypeError):
            pass
    return event.pos()



ShiftModifier = _resolve(Qt, "KeyboardModifier", "ShiftModifier")


LeftButton = _resolve(Qt, "MouseButton", "LeftButton")
RightButton = _resolve(Qt, "MouseButton", "RightButton")


NoFocus = _resolve(Qt, "FocusPolicy", "NoFocus")


OtherFocusReason = _resolve(Qt, "FocusReason", "OtherFocusReason")


ToolButtonTextBesideIcon = _resolve(Qt, "ToolButtonStyle", "ToolButtonTextBesideIcon")


DownArrow = _resolve(Qt, "ArrowType", "DownArrow")
RightArrow = _resolve(Qt, "ArrowType", "RightArrow")


RichText = _resolve(Qt, "TextFormat", "RichText")
PlainText = _resolve(Qt, "TextFormat", "PlainText")


LinksAccessibleByMouse = _resolve(Qt, "TextInteractionFlag", "LinksAccessibleByMouse")


WA_TransparentForMouseEvents = _resolve(Qt, "WidgetAttribute", "WA_TransparentForMouseEvents")
WA_StyledBackground = _resolve(Qt, "WidgetAttribute", "WA_StyledBackground")


ScrollBarAlwaysOff = _resolve(Qt, "ScrollBarPolicy", "ScrollBarAlwaysOff")
ScrollBarAsNeeded = _resolve(Qt, "ScrollBarPolicy", "ScrollBarAsNeeded")



WrapAtWordBoundaryOrAnywhere = _resolve(
    QTextOption, "WrapMode", "WrapAtWordBoundaryOrAnywhere"
)



LineWrapWidgetWidth = _resolve(QTextEdit, "LineWrapMode", "WidgetWidth")


KeepAspectRatio = _resolve(Qt, "AspectRatioMode", "KeepAspectRatio")
KeepAspectRatioByExpanding = _resolve(Qt, "AspectRatioMode", "KeepAspectRatioByExpanding")
SmoothTransformation = _resolve(Qt, "TransformationMode", "SmoothTransformation")


NoPen = _resolve(Qt, "PenStyle", "NoPen")


TextSelectableByMouse = _resolve(Qt, "TextInteractionFlag", "TextSelectableByMouse")
TextBrowserInteraction = _resolve(Qt, "TextInteractionFlag", "TextBrowserInteraction")


WriteOnly = _resolve(QIODevice, "OpenModeFlag", "WriteOnly")


LocaleDefaultNumberOptions = _resolve(QLocale, "NumberOption", "DefaultNumberOptions")


CacheLocation = _resolve(QStandardPaths, "StandardLocation", "CacheLocation")


FormatARGB32 = _resolve(QImage, "Format", "Format_ARGB32")



CompositionModeSourceOver = _resolve(
    QPainter, "CompositionMode", "CompositionMode_SourceOver"
)


CursorEnd = _resolve(QTextCursor, "MoveOperation", "End")


SizePolicyExpanding = _resolve(QSizePolicy, "Policy", "Expanding")
SizePolicyFixed = _resolve(QSizePolicy, "Policy", "Fixed")


PaletteBase = _resolve(QPalette, "ColorRole", "Base")


FrameNoFrame = _resolve(QFrame, "Shape", "NoFrame")
FrameHLine = _resolve(QFrame, "Shape", "HLine")
FrameVLine = _resolve(QFrame, "Shape", "VLine")
FrameSunken = _resolve(QFrame, "Shadow", "Sunken")


BlockingNoError = _resolve(QgsBlockingNetworkRequest, "ErrorCode", "NoError")
BlockingNetworkError = _resolve(QgsBlockingNetworkRequest, "ErrorCode", "NetworkError")


try:
    from qgis.core import Qgis
    _gt = getattr(Qgis, "GeometryType", None)
    PolygonGeometry = getattr(_gt, "Polygon", None)
    LineGeometry = getattr(_gt, "Line", None)


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


IdentifyFormatValue = _resolve(QgsRaster, "IdentifyFormat", "IdentifyFormatValue")


try:
    from qgis.gui import QgsVertexMarker
    VertexIconCircle = _resolve(QgsVertexMarker, "IconType", "ICON_CIRCLE")
except Exception:
    VertexIconCircle = None



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



OperationCanceledError = _net_enum("OperationCanceledError")

PROXY_ERRORS = {
    _net_enum("ProxyConnectionRefusedError"),
    _net_enum("ProxyConnectionClosedError"),
    _net_enum("ProxyNotFoundError"),
    _net_enum("ProxyTimeoutError"),
    _net_enum("ProxyAuthenticationRequiredError"),
    _net_enum("UnknownProxyError"),
}


HttpStatusCodeAttribute = _resolve(
    QNetworkRequest, "Attribute", "HttpStatusCodeAttribute"
)




RedirectPolicyAttribute = _resolve(
    QNetworkRequest, "Attribute", "RedirectPolicyAttribute"
)
NoLessSafeRedirectPolicy = _resolve(
    QNetworkRequest, "RedirectPolicy", "NoLessSafeRedirectPolicy"
)


def set_transfer_timeout(request: QNetworkRequest, msec: int) -> bool:










    setter = getattr(request, "setTransferTimeout", None)
    if setter is None:
        return False
    try:
        setter(int(msec))
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def silent_task_flags(can_cancel: bool = True):









    flags = QgsTask.Flag.CanCancel if can_cancel else QgsTask.Flag(0)
    for name in ("Hidden", "Silent"):
        flag = getattr(QgsTask.Flag, name, None)
        if flag is not None:
            flags = flags | flag
    return flags


def _gui_or_widget_class(name: str):











    from qgis.PyQt import QtGui, QtWidgets

    found = getattr(QtGui, name, None)
    return found if found is not None else getattr(QtWidgets, name)


QAction = _gui_or_widget_class("QAction")
QActionGroup = _gui_or_widget_class("QActionGroup")
QShortcut = _gui_or_widget_class("QShortcut")


def safe_single_shot(msec: int, owner: QObject, callback) -> QTimer:











    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.timeout.connect(callback)



    timer.timeout.connect(timer.deleteLater)
    timer.start(max(0, int(msec)))
    return timer


def safe_disconnect(owner, signal_name: str, slot=None) -> bool:












    try:
        signal = getattr(owner, signal_name)
        if slot is None:
            signal.disconnect()
        else:
            signal.disconnect(slot)
        return True
    except (TypeError, RuntimeError, AttributeError):
        return False
