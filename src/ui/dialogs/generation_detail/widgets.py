"""Small helper widgets used by the generation detail dialog."""
from __future__ import annotations

from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.i18n import tr
from .styles import _ACTION_BTN, _DOWNLOAD_SVG, _REF_OVERLAY_BTN, _REF_THUMB


class _AspectBox(QWidget):
    """Keeps its single child at a fixed width:height ratio, centered. The
    before/after slider draws cover-fit, so matching the box ratio to the
    image ratio shows the whole image with no crop (portrait or landscape)."""

    def __init__(self, child: QWidget, ratio: float, parent=None):
        super().__init__(parent)
        self._child = child
        child.setParent(self)
        self._ratio = ratio if ratio and ratio > 0 else 1.0
        self._overlay: QWidget | None = None
        self._overlay_margin = 10

    def set_ratio(self, ratio: float) -> None:
        self._ratio = ratio if ratio and ratio > 0 else 1.0
        self._relayout()

    def set_overlay(self, widget: QWidget) -> None:
        """Float ``widget`` over the bottom-right corner of the image rect."""
        self._overlay = widget
        widget.setParent(self)
        widget.raise_()
        self._relayout()

    def resizeEvent(self, event):  # noqa: N802 - Qt signature
        self._relayout()
        super().resizeEvent(event)

    def _relayout(self) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if w / h > self._ratio:
            ch = h
            cw = int(round(h * self._ratio))
        else:
            cw = w
            ch = int(round(w / self._ratio))
        cx, cy = (w - cw) // 2, (h - ch) // 2
        self._child.setGeometry(cx, cy, cw, ch)
        if self._overlay is not None:
            ow = self._overlay.width()
            oh = self._overlay.height()
            m = self._overlay_margin
            self._overlay.move(cx + cw - ow - m, cy + ch - oh - m)
            self._overlay.raise_()


class _RefThumb(QWidget):
    """Reference-image thumbnail. Hover shows open / download controls; the
    whole tile opens the image large on click."""

    def __init__(self, index: int, on_open, on_download, parent=None):
        super().__init__(parent)
        self._index = index
        self._on_open = on_open
        self._full_pm: QPixmap | None = None
        self.setFixedSize(72, 72)
        self.setCursor(QtC.PointingHandCursor)
        self.setToolTip(tr("Click to open. Hover to download."))

        self._img = QLabel(self)
        self._img.setGeometry(0, 0, 72, 72)
        self._img.setAlignment(QtC.AlignCenter)
        self._img.setStyleSheet(_REF_THUMB)

        self._overlay = QWidget(self)
        self._overlay.setGeometry(0, 0, 72, 72)
        self._overlay.setStyleSheet("background: rgba(20,24,15,0.45); border-radius: 4px;")
        ov = QHBoxLayout(self._overlay)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(6)
        ov.addStretch(1)
        open_b = QToolButton(self._overlay)
        open_b.setText("⤢")
        open_b.setFixedSize(26, 26)
        open_b.setCursor(QtC.PointingHandCursor)
        open_b.setToolTip(tr("Open large"))
        open_b.setStyleSheet(_REF_OVERLAY_BTN)
        open_b.clicked.connect(lambda: self._on_open(self._index))
        ov.addWidget(open_b)
        dl_b = QToolButton(self._overlay)
        dl_b.setIcon(QIcon(_DOWNLOAD_SVG))
        dl_b.setFixedSize(26, 26)
        dl_b.setCursor(QtC.PointingHandCursor)
        dl_b.setToolTip(tr("Download original"))
        dl_b.setStyleSheet(_REF_OVERLAY_BTN)
        dl_b.clicked.connect(lambda: on_download(self._index))
        ov.addWidget(dl_b)
        ov.addStretch(1)
        self._overlay.hide()

    def set_pixmap(self, pm: QPixmap) -> None:
        self._full_pm = pm
        self._img.setPixmap(
            pm.scaled(70, 70, QtC.KeepAspectRatio, QtC.SmoothTransformation)
        )

    def full_pixmap(self) -> QPixmap | None:
        return self._full_pm

    def enterEvent(self, event):  # noqa: N802 - Qt signature
        if self._full_pm is not None:
            self._overlay.show()
            self._overlay.raise_()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt signature
        self._overlay.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802 - Qt signature
        if event.button() == QtC.LeftButton and self._full_pm is not None:
            self._on_open(self._index)
        super().mousePressEvent(event)


class _ImageLightbox(QDialog):
    """Simple large-image viewer with a Download button, used for references."""

    def __init__(self, pixmap: QPixmap, title: str, on_download=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title or tr("Reference image"))
        v = QVBoxLayout(self)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)

        img = QLabel(self)
        img.setAlignment(QtC.AlignCenter)
        shown = pixmap
        if pixmap.width() > 1100 or pixmap.height() > 800:
            shown = pixmap.scaled(
                1100, 800, QtC.KeepAspectRatio, QtC.SmoothTransformation
            )
        img.setPixmap(shown)
        v.addWidget(img, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        if on_download is not None:
            dl = QPushButton(tr("Download original"))
            dl.setIcon(QIcon(_DOWNLOAD_SVG))
            dl.setStyleSheet(_ACTION_BTN)
            dl.setCursor(QtC.PointingHandCursor)
            dl.clicked.connect(on_download)
            row.addWidget(dl)
        close = QPushButton(tr("Close"))
        close.setStyleSheet(_ACTION_BTN)
        close.setCursor(QtC.PointingHandCursor)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        v.addLayout(row)
