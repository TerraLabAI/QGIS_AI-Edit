












from __future__ import annotations

import hashlib
import os
from urllib.parse import urlsplit

from qgis.PyQt.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    QRectF,
    QSize,
    QStandardPaths,
    Qt,
    QUrl,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath, QPixmap
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.PyQt.QtWidgets import QLabel, QSizePolicy

from ..core.config_store import get_export_dial
from ..core.qt_compat import set_transfer_timeout
from .dock import design_tokens as tokens
from .dock.design_tokens import ACCENT_TINT, LINE, RADIUS_CARD, qcolor
from .icons import widget_pixel_ratio

ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "resources", "icons")



_MAX_THUMBNAIL_BYTES = 900 * 1024




_FETCH_TIMEOUT_MS = 10_000


SHOT_RATIO = 630 / 1200


_MAX_DECODE_PIXELS = 16 * 1024 * 1024


_TILE_GROUND = tokens.LOGO_TILE_GROUND
_TILE_INSET = 0.14


def _enum(owner, scope, name):





    holder = getattr(owner, scope, None)
    if holder is not None and hasattr(holder, name):
        return getattr(holder, name)
    return getattr(owner, name)


_ALIGN_CENTER = _enum(Qt, "AlignmentFlag", "AlignCenter")
_NO_PEN = _enum(Qt, "PenStyle", "NoPen")
_KEEP_RATIO = _enum(Qt, "AspectRatioMode", "KeepAspectRatio")
_EXPAND_RATIO = _enum(Qt, "AspectRatioMode", "KeepAspectRatioByExpanding")
_SMOOTH = _enum(Qt, "TransformationMode", "SmoothTransformation")
_TRANSPARENT = _enum(Qt, "GlobalColor", "transparent")
_SIZE_EXPANDING = _enum(QSizePolicy, "Policy", "Expanding")
_SIZE_FIXED = _enum(QSizePolicy, "Policy", "Fixed")
_READ_ONLY = _enum(QIODevice, "OpenModeFlag", "ReadOnly")
_CACHE_LOCATION = _enum(QStandardPaths, "StandardLocation", "CacheLocation")
_NETWORK_NO_ERROR = _enum(QNetworkReply, "NetworkError", "NoError")
_HTTP_STATUS = _enum(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute")
_REDIRECT_POLICY = _enum(QNetworkRequest, "Attribute", "RedirectPolicyAttribute")
_NO_LESS_SAFE = _enum(QNetworkRequest, "RedirectPolicy", "NoLessSafeRedirectPolicy")


def sibling_logo_path(file_name: str) -> str:

    path = os.path.join(ICON_DIR, file_name) if file_name else ""
    return path if path and os.path.isfile(path) else ""


def logo_tile_pixmap(path: str, side: int, ratio: float = 1.0):

    source = QPixmap(str(path or ""))
    if source.isNull():
        return None
    ratio = max(1.0, float(ratio or 1.0))
    chip = QPixmap(int(side * ratio), int(side * ratio))
    chip.setDevicePixelRatio(ratio)
    chip.fill(QColor(_TRANSPARENT))
    radius = side / 3.0 + 2
    rounded = QPainterPath()
    rounded.addRoundedRect(QRectF(0.5, 0.5, side - 1, side - 1), radius, radius)
    painter = QPainter(chip)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.fillPath(rounded, qcolor(_TILE_GROUND))
    painter.setClipPath(rounded)
    inner = max(1, int(side * (1 - _TILE_INSET * 2)))
    art = source.scaled(int(inner * ratio), int(inner * ratio), _KEEP_RATIO, _SMOOTH)
    art.setDevicePixelRatio(ratio)
    painter.drawPixmap(int((side - art.width() / ratio) / 2),
                       int((side - art.height() / ratio) / 2), art)
    painter.setClipping(False)
    painter.setPen(qcolor(LINE))
    painter.drawPath(rounded)
    painter.end()
    return chip


def _max_thumbnail_bytes() -> int:
    return get_export_dial("widgets.siblings_dialog.max_thumbnail_bytes", _MAX_THUMBNAIL_BYTES)


def _thumbnail_cache_dir() -> str:


    root = QStandardPaths.writableLocation(_CACHE_LOCATION) or os.path.expanduser("~/.cache")
    return os.path.join(root, "terralab", "plugin-thumbnails")


def _cache_path(url: str) -> str:

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return os.path.join(_thumbnail_cache_dir(), f"{digest}.img")


def is_thumbnail_url_usable(url) -> bool:

    if not isinstance(url, str) or not url.lower().startswith("https://"):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return bool(parsed.hostname) and not parsed.username and not parsed.password


def _read_bounded_image(data: bytes):

    try:
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(_READ_ONLY)
        reader = QImageReader(buffer)
        size = reader.size()
        if size.isValid() and size.width() * size.height() > _MAX_DECODE_PIXELS:
            return None
        image = reader.read()
        return None if image.isNull() else image
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def cached_thumbnail(url: str):
    try:
        path = _cache_path(url)
        max_bytes = _max_thumbnail_bytes()
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, "rb") as handle:
            data = handle.read(max_bytes)
    except OSError:
        return None
    return _read_bounded_image(data)


def _store_image(url: str, data: bytes) -> None:
    path = _cache_path(url)
    part = path + ".part"
    try:
        os.makedirs(_thumbnail_cache_dir(), exist_ok=True)
        with open(part, "wb") as handle:
            handle.write(data)
        os.replace(part, path)
    except OSError:
        try:
            os.remove(part)
        except OSError:
            pass  # nosec B110


class ThumbnailLoader(QObject):






    loaded = pyqtSignal(QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reply = None
        self._url = ""

    def fetch(self, url: str) -> None:
        from qgis.core import QgsNetworkAccessManager

        if self._reply is not None or not is_thumbnail_url_usable(url):
            return
        self._url = url
        request = QNetworkRequest(QUrl(url))
        try:
            request.setAttribute(_REDIRECT_POLICY, _NO_LESS_SAFE)
            set_transfer_timeout(
                request, get_export_dial("widgets.siblings_dialog.thumbnail_fetch_timeout_ms", _FETCH_TIMEOUT_MS)
            )
            reply = QgsNetworkAccessManager.instance().get(request)
        except Exception:  # noqa: BLE001
            return  # nosec B110
        try:
            reply.setReadBufferSize(_max_thumbnail_bytes() + 1)
        except (AttributeError, RuntimeError):
            pass  # nosec B110
        self._reply = reply
        reply.downloadProgress.connect(self._on_progress)
        reply.finished.connect(self._on_finished)



        reply.finished.connect(reply.deleteLater)

    def abort(self) -> None:
        reply, self._reply = self._reply, None
        if reply is None:
            return
        for signal in ("finished", "downloadProgress"):
            try:
                getattr(reply, signal).disconnect()
            except (AttributeError, RuntimeError, TypeError):
                pass  # nosec B110
        try:
            reply.abort()
            reply.deleteLater()
        except RuntimeError:
            pass  # nosec B110

    def _on_progress(self, received: int, total: int) -> None:
        max_bytes = _max_thumbnail_bytes()
        if self._reply is not None and (received > max_bytes or total > max_bytes):
            self.abort()

    def _on_finished(self) -> None:
        reply, self._reply = self._reply, None
        if reply is None:
            return
        data = b""
        try:
            status = reply.attribute(_HTTP_STATUS)
            served = int(status) if status is not None else 200
            if reply.error() == _NETWORK_NO_ERROR and served == 200:
                data = bytes(reply.readAll())
        except (RuntimeError, TypeError, ValueError):
            data = b""
        try:
            reply.deleteLater()
        except RuntimeError:
            pass  # nosec B110
        if not data or len(data) > _max_thumbnail_bytes():
            return
        image = _read_bounded_image(data)
        if image is None:
            return
        _store_image(self._url, data)
        self.loaded.emit(image)


class GuideStill(QLabel):







    def __init__(self, logo_path: str, parent=None):
        super().__init__(parent)
        self._image = None
        self._logo = None
        self._width = 0
        if logo_path:
            self._logo = logo_tile_pixmap(logo_path, 48, widget_pixel_ratio(self))
        self.setSizePolicy(_SIZE_EXPANDING, _SIZE_FIXED)
        self.setAlignment(_ALIGN_CENTER)
        self.setMinimumWidth(1)

    def set_width(self, width: int) -> None:


        if width == self._width or width <= 0:
            return
        self._width = width
        self.setFixedHeight(int(round(width * SHOT_RATIO)))

    def sizeHint(self):  # noqa: N802


        return QSize(240, int(round(240 * SHOT_RATIO)))

    def minimumSizeHint(self):  # noqa: N802
        return QSize(1, 1)

    def set_image(self, image: QImage) -> None:
        self._image = image if image is not None and not image.isNull() else None
        self.update()

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect()
        frame = QRectF(rect.adjusted(0, 0, -1, -1))
        clip = QPainterPath()

        clip.addRoundedRect(frame, RADIUS_CARD - 2, RADIUS_CARD - 2)
        if self._image is not None:
            ratio = float(self.devicePixelRatioF())
            target = QSize(max(1, int(rect.width() * ratio)), max(1, int(rect.height() * ratio)))
            scaled = self._image.scaled(target, _EXPAND_RATIO, _SMOOTH)
            pixmap = QPixmap.fromImage(scaled)
            pixmap.setDevicePixelRatio(ratio)
            painter.setClipPath(clip)
            painter.drawPixmap(
                rect.x() - max(0, (int(scaled.width() / ratio) - rect.width())) // 2,
                rect.y() - max(0, (int(scaled.height() / ratio) - rect.height())) // 2,
                pixmap)
            painter.end()
            return
        painter.fillPath(clip, qcolor(ACCENT_TINT))
        if self._logo is not None:
            ratio = self._logo.devicePixelRatio() or 1.0
            painter.drawPixmap(int((rect.width() - self._logo.width() / ratio) / 2),
                               int((rect.height() - self._logo.height() / ratio) / 2),
                               self._logo)
        painter.end()
