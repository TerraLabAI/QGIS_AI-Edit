"""The pictures on the More plugins cards: the guide still and the logo tile.

The still is the picture of that plugin's written guide, fetched once and
cached under the shared TerraLab cache dir, so one download serves whichever of
our plugins the user opens next. A picture that will not load paints a tinted
panel with the plugin's logo in it rather than leaving a gap.

The logo tile is AI Agent's ``logo_tile``: the plugin's own mark on a light
rounded chip, so a dark mark stays legible on a dark QGIS and both cards show
the same shape.

Split out of ``siblings_dialog.py``, which keeps the cards and the page.
"""
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
from .dock import design_tokens as tokens
from .dock.design_tokens import ACCENT_TINT, LINE, RADIUS_CARD, qcolor
from .icons import widget_pixel_ratio

ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "resources", "icons")

# A thumbnail is a small JPEG. Past this the reply is aborted rather than
# buffered, so a wrong URL cannot pull a large file into memory.
_MAX_THUMBNAIL_BYTES = 900 * 1024
# 1200 by 630 is the shape of every guide's picture, so the still keeps that
# ratio at whatever width the column ended up.
SHOT_RATIO = 630 / 1200
# Decompression-bomb guard: a header claiming more than this many pixels is
# never decoded.
_MAX_DECODE_PIXELS = 16 * 1024 * 1024
# The chip behind a plugin logo: the light theme's inset step in both themes,
# because a dark-ink mark on a transparent canvas disappears on a dark QGIS.
_TILE_GROUND = tokens.LOGO_TILE_GROUND
_TILE_INSET = 0.14


def _enum(owner, scope, name):
    """A Qt enum member, from the Qt6 scoped class or the Qt5 flat namespace.

    Not "or": several members are 0 (NoPen, NetworkError.NoError), and a falsy
    hit would fall through to the flat Qt5 spelling, absent under Qt6.
    """
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
    """The bundled logo file for a sibling, or "" when it is not shipped."""
    path = os.path.join(ICON_DIR, file_name) if file_name else ""
    return path if path and os.path.isfile(path) else ""


def logo_tile_pixmap(path: str, side: int, ratio: float = 1.0):
    """The logo at ``path`` on a light rounded chip ``side`` wide, or None."""
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
    """Where the stills live. Shared by the TerraLab plugins on purpose: the
    three of them show the same three pictures, so one download serves all."""
    root = QStandardPaths.writableLocation(_CACHE_LOCATION) or os.path.expanduser("~/.cache")
    return os.path.join(root, "terralab", "plugin-thumbnails")


def _cache_path(url: str) -> str:
    """The URL is the identity of the picture: a new still comes with a new URL."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return os.path.join(_thumbnail_cache_dir(), f"{digest}.img")


def is_thumbnail_url_usable(url) -> bool:
    """https only. A file: or data: URL would read something local instead."""
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return bool(parsed.hostname) and not parsed.username and not parsed.password


def _read_bounded_image(data: bytes):
    """Decode only after reading the header: a small file can claim a huge canvas."""
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
            pass  # nosec B110 - the picture is a nicety, never a failure path


class ThumbnailLoader(QObject):
    """One picture read, off the UI thread, reporting back on ``loaded``.

    Parent it to the card: one destroyed before the reply lands takes the
    loader and its connections with it, so nothing reaches a deleted widget.
    """

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
            reply = QgsNetworkAccessManager.instance().get(request)
        except Exception:  # noqa: BLE001
            return  # nosec B110 - no picture: the painted panel stands
        try:
            reply.setReadBufferSize(_max_thumbnail_bytes() + 1)
        except (AttributeError, RuntimeError):
            pass  # nosec B110
        self._reply = reply
        reply.downloadProgress.connect(self._on_progress)
        reply.finished.connect(self._on_finished)
        # Second, after ours so the bytes are read first: this slot has the
        # reply itself as receiver, so it still frees it when the card went
        # away mid-request and our own handler never runs.
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
    """The guide's picture, or the tinted panel with the logo that stands in.

    Never an empty box and never a broken-image icon. Its height follows its
    width through ``set_width`` (the card calls it on resize), so the picture
    keeps the guide's shape and is never cropped through its own title.
    """

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
        """Keep the picture's shape at ``width``; a no-op on the same width,
        so the relayout this causes does not chase itself."""
        if width == self._width or width <= 0:
            return
        self._width = width
        self.setFixedHeight(int(round(width * SHOT_RATIO)))

    def sizeHint(self):  # noqa: N802 - Qt override
        # A nominal width, never self.width(): a hint that reads the current
        # width changes every time the layout answers it.
        return QSize(240, int(round(240 * SHOT_RATIO)))

    def minimumSizeHint(self):  # noqa: N802 - Qt override
        return QSize(1, 1)

    def set_image(self, image: QImage) -> None:
        self._image = image if image is not None and not image.isNull() else None
        self.update()

    def paintEvent(self, _event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self.rect()
        frame = QRectF(rect.adjusted(0, 0, -1, -1))
        clip = QPainterPath()
        # One step inside the card's own corner, so the two curves nest.
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
