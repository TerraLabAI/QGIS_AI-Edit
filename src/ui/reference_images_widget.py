"""Thumbnail strip widget for context reference images.

This widget renders only the header + thumbnail row. The bordered container,
drag-drop visual feedback, and the "attach" entry point all live on the
surrounding _PromptContainer (in dock_widget.py). The widget is invisible
whenever the store is empty.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QSize, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QFont, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import qt_compat as QtC
from ..core.i18n import tr
from ..core.reference_image_store import (
    MAX_REFERENCES,
    ReferenceImage,
    ReferenceImageStore,
    ReferenceImageStoreError,
)

THUMB_PX = 56

_THUMB_STYLE = (
    "QFrame { background: rgba(0, 0, 0, 0.0);"
    " border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 4px; }"
)

_REMOVE_BTN_STYLE = (
    "QToolButton { background: rgba(0, 0, 0, 0.55); color: white;"
    " border: none; border-radius: 8px; font-weight: bold; font-size: 11px; }"
    "QToolButton:hover { background: rgba(211, 47, 47, 0.85); }"
)

_BADGE_STYLE = (
    "QLabel { background: rgba(0, 0, 0, 0.55); color: rgba(255, 255, 255, 0.9);"
    " border: none; border-top-left-radius: 3px; border-bottom-right-radius: 3px;"
    " font-size: 9px; font-weight: bold; padding: 0 2px; }"
)


class _ThumbWidget(QFrame):
    """Single reference thumbnail with numbered badge and remove button."""

    remove_clicked = pyqtSignal(str)
    preview_requested = pyqtSignal(str)  # emits the image path

    def __init__(self, record: ReferenceImage, index: int, parent=None):
        super().__init__(parent)
        self._ref_id = record.id
        self._image_path = record.path
        self._readonly = False
        self.setFixedSize(THUMB_PX + 2, THUMB_PX + 2)
        self.setStyleSheet(_THUMB_STYLE)
        self.setToolTip(tr("Click to preview: {name}").format(name=record.source_filename))
        self.setCursor(QtC.PointingHandCursor)

        self._pixmap_label = QLabel(self)
        self._pixmap_label.setGeometry(1, 1, THUMB_PX, THUMB_PX)
        self._pixmap_label.setAlignment(QtC.AlignCenter)
        pixmap = QPixmap(record.path)
        if not pixmap.isNull():
            self._pixmap_label.setPixmap(
                pixmap.scaled(
                    QSize(THUMB_PX, THUMB_PX),
                    QtC.KeepAspectRatio,
                    QtC.SmoothTransformation,
                )
            )

        # Number badge (top-left corner tag)
        self._badge = QLabel(str(index), self)
        self._badge.setFixedSize(14, 14)
        self._badge.setAlignment(QtC.AlignCenter)
        self._badge.setStyleSheet(_BADGE_STYLE)
        font = QFont()
        font.setPixelSize(9)
        font.setBold(True)
        self._badge.setFont(font)
        self._badge.move(1, 1)

        # Remove button (top-right) - hidden by default, revealed on hover so
        # the thumbnail looks clean while still letting the user delete it.
        self._remove_btn = QToolButton(self)
        self._remove_btn.setText("×")
        self._remove_btn.setFixedSize(16, 16)
        self._remove_btn.setStyleSheet(_REMOVE_BTN_STYLE)
        self._remove_btn.setCursor(QtC.PointingHandCursor)
        self._remove_btn.move(THUMB_PX + 2 - 16, 0)
        self._remove_btn.setVisible(False)
        self._remove_btn.clicked.connect(
            lambda: self.remove_clicked.emit(self._ref_id)
        )

    def set_readonly(self, readonly: bool) -> None:
        """Hide the remove button so the thumbnail stays clickable for
        preview but cannot be deleted (used during generation)."""
        self._readonly = readonly
        if readonly:
            self._remove_btn.setVisible(False)

    def enterEvent(self, event):  # noqa: N802
        if not self._readonly:
            self._remove_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._remove_btn.setVisible(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        # Only swallow the click for the remove button when it is actually
        # visible - otherwise the top-right corner would silently eat preview
        # clicks even though there is no button there.
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if (
            self._remove_btn.isVisible()
            and self._remove_btn.geometry().contains(pos)  # noqa: W503
        ):
            super().mousePressEvent(event)
            return
        self.preview_requested.emit(self._image_path)
        super().mousePressEvent(event)


class _ImagePreviewDialog(QDialog):
    """Modal preview of a reference image, scaled to fit screen."""

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Reference image preview"))
        self.setModal(True)

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.reject()
            return

        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        max_w = int(avail.width() * 0.8) if avail is not None else 1280
        max_h = int(avail.height() * 0.8) if avail is not None else 800
        scaled = pixmap.scaled(
            QSize(max_w, max_h),
            QtC.KeepAspectRatio,
            QtC.SmoothTransformation,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        label = QLabel(self)
        label.setPixmap(scaled)
        label.setAlignment(QtC.AlignCenter)
        layout.addWidget(label)
        self.resize(scaled.size())

    def mousePressEvent(self, event):  # noqa: N802
        # Click anywhere inside the dialog closes it.
        self.accept()
        super().mousePressEvent(event)


class ReferenceImagesWidget(QWidget):
    """Thumbnail strip; invisible while the store is empty."""

    images_changed = pyqtSignal()
    error_occurred = pyqtSignal(str)
    error_cleared = pyqtSignal()

    def __init__(self, store: ReferenceImageStore, parent=None):
        super().__init__(parent)
        self._store = store
        self._readonly = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Thumbnails row lives inside a horizontal-only QScrollArea so the
        # strip can hold up to MAX_REFERENCES tiles without forcing the dock
        # to grow wider than the QGIS window. Without this, Qt resolves the
        # overflow by widening the dock and pushing a horizontal scrollbar
        # onto the whole QGIS layout.
        self._thumbs_host = QWidget(self)
        self._thumbs_host.setStyleSheet("background: transparent;")
        self._thumbs_row = QHBoxLayout(self._thumbs_host)
        self._thumbs_row.setContentsMargins(0, 0, 0, 0)
        self._thumbs_row.setSpacing(6)

        self._thumbs_scroll = QScrollArea(self)
        self._thumbs_scroll.setWidget(self._thumbs_host)
        self._thumbs_scroll.setWidgetResizable(True)
        self._thumbs_scroll.setFrameShape(QtC.FrameNoFrame)
        # Let the surrounding _PromptContainer background show through -
        # without this the QScrollArea viewport paints palette(base), which
        # reads as a darker grey band sitting on top of the container fill.
        self._thumbs_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self._thumbs_scroll.viewport().setAutoFillBackground(False)
        # Hide both scrollbars - trackpad / mouse wheel still scroll the
        # viewport horizontally. Cleaner than ScrollBarAsNeeded which
        # paints a visible bar under the thumbnails.
        self._thumbs_scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        self._thumbs_scroll.setVerticalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        # One row of thumbnails, no extra space for a scrollbar.
        self._thumbs_scroll.setFixedHeight(THUMB_PX + 4)
        # Width follows the parent; never push the dock outward.
        self._thumbs_scroll.setSizePolicy(
            QtC.SizePolicyExpanding, QtC.SizePolicyFixed
        )
        outer.addWidget(self._thumbs_scroll)

        self._refresh()

    # -- public API --------------------------------------------------------

    def clear(self) -> None:
        self._store.clear()
        self._refresh()

    def get_all_b64(self) -> list[str]:
        return self._store.get_all_b64()

    def count(self) -> int:
        return self._store.count()

    def at_capacity(self) -> bool:
        return self._store.count() >= MAX_REFERENCES

    def add_paths(self, paths: list[str]) -> None:
        """Public entry point for the container's drop zone and attach button."""
        if self._readonly:
            return
        self._add_paths(paths)

    def set_readonly(self, readonly: bool) -> None:
        """Lock the widget during generation: thumbnails stay clickable
        for preview but the remove buttons and the add-paths flow are
        disabled."""
        self._readonly = readonly
        for i in range(self._thumbs_row.count()):
            item = self._thumbs_row.itemAt(i)
            widget = item.widget() if item is not None else None
            if isinstance(widget, _ThumbWidget):
                widget.set_readonly(readonly)

    def open_file_picker(self) -> None:
        """Public entry point for the paperclip button on the prompt container."""
        if self._readonly:
            return
        if self.at_capacity():
            self._show_temp_error(
                tr("Maximum {n} reference images reached").format(n=MAX_REFERENCES)
            )
            return
        # Parent on the top-level window (the dock), not self: this widget is
        # hidden while the store is empty, and a hidden parent causes the file
        # picker to silently fail to show up on Windows.
        paths, _ = QFileDialog.getOpenFileNames(
            self.window(),
            tr("Select reference images"),
            "",
            tr("Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff *.bmp)"),
        )
        if paths:
            self._add_paths(paths)

    # -- internal ----------------------------------------------------------

    def _refresh(self) -> None:
        """Rebuild the thumbnails row from store contents."""
        # Hide the entire widget when there is nothing to show.
        has_images = self._store.count() > 0
        self.setVisible(has_images)

        while self._thumbs_row.count():
            item = self._thumbs_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

        for idx, record in enumerate(self._store.list(), start=1):
            thumb = _ThumbWidget(record, idx, self)
            thumb.set_readonly(self._readonly)
            thumb.remove_clicked.connect(self._on_remove)
            thumb.preview_requested.connect(self._open_preview)
            self._thumbs_row.addWidget(thumb)
        self._thumbs_row.addStretch()

        self.images_changed.emit()

    def _add_paths(self, paths: list[str]) -> None:
        added = 0
        error_shown = False
        for path in paths:
            if self._store.count() >= MAX_REFERENCES:
                if not error_shown:
                    self._show_temp_error(
                        tr("Maximum {n} reference images reached").format(n=MAX_REFERENCES)
                    )
                    error_shown = True
                break
            try:
                self._store.add(path)
                added += 1
            except ReferenceImageStoreError as err:
                self._show_temp_error(str(err))
                error_shown = True
        if added > 0:
            self._refresh()

    def _show_temp_error(self, msg: str) -> None:
        """Emit an error that auto-clears after 4 seconds."""
        self.error_occurred.emit(msg)
        QTimer.singleShot(4000, self.error_cleared.emit)

    def _on_remove(self, ref_id: str) -> None:
        self._store.remove(ref_id)
        self._refresh()

    def _open_preview(self, image_path: str) -> None:
        dlg = _ImagePreviewDialog(image_path, self)
        dlg.exec()
