"""Thumbnail strip widget for context reference images.

This widget renders only the header + thumbnail row. The bordered container,
drag-drop visual feedback, and the "attach" entry point all live on the
surrounding _PromptContainer (in dock_widget.py). The widget is invisible
whenever the store is empty.
"""
from __future__ import annotations

import os

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
from .layer_renderer import is_remote_layer, load_transient_layers, render_layers_to_qimage

THUMB_PX = 56
# Free-tier reference-image cap. This is a UX/entitlement gate, not a storage
# limit: the store keeps enforcing MAX_REFERENCES as the hard ceiling, and the
# backend rejects free-tier generations carrying more than this many context
# images. Adding past this on free tier surfaces an upsell, not an error.
FREE_TIER_MAX_REFERENCES = 1
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
# Companions a shapefile needs alongside the .shp to load. .prj is technically
# optional (no CRS = degraded render via the fallback CRS), so we don't gate on
# it. Keep the two mandatory ones only.
_SHP_REQUIRED_COMPANIONS = (".shx", ".dbf")


def _missing_shapefile_companions(shp_path: str) -> list[str]:
    """Return the list of required companion extensions missing next to a .shp
    file. The check is case-insensitive (Windows ships .SHX uppercase
    sometimes); we look up either casing on disk."""
    base, _ = os.path.splitext(shp_path)
    missing: list[str] = []
    for ext in _SHP_REQUIRED_COMPANIONS:
        if not (os.path.isfile(base + ext) or os.path.isfile(base + ext.upper())):
            missing.append(ext)
    return missing


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
        pos = QtC.event_pos(event)
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
    # Emitted when a free-tier user tries to add past FREE_TIER_MAX_REFERENCES.
    # The dock shows the subscribe upsell; the widget stays out of presentation.
    upsell_requested = pyqtSignal()

    def __init__(self, store: ReferenceImageStore, parent=None):
        super().__init__(parent)
        self._store = store
        self._readonly = False
        # Default restrictive (free) until the dock confirms the tier via
        # set_free_tier(), matching the dock's own _is_free_tier default. The
        # server enforces the real cap, so a brief restrictive window is safe.
        self._free_tier = True
        # Parented QTimer for the 4 s "error cleared" auto-dismiss. Holding a
        # ref so we can stop it when the widget is destroyed first.
        self._error_clear_timer: QTimer | None = None

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
        # Hard ceiling only. Drives attach-button visibility in the dock: the
        # free-tier gate deliberately does NOT hide the button, so clicking it
        # surfaces the upsell instead of silently doing nothing.
        return self._store.count() >= MAX_REFERENCES

    def set_free_tier(self, free_tier: bool) -> None:
        """Tell the widget whether the current user is on the free tier.

        Pushed by the dock on every set_credits() call. Non-destructive: never
        removes images already added if the tier flips to free, only blocks
        adding more.
        """
        self._free_tier = free_tier

    def _check_can_add(self) -> str:
        """Single source of truth for the add gate. Returns a reason:
        ``"ok"``, ``"free_limit"`` (free tier nudge), or ``"hard_cap"`` (the
        MAX_REFERENCES ceiling). Free-tier is checked first so a free user at
        the limit always sees the upsell, never the generic cap message."""
        count = self._store.count()
        if self._free_tier and count >= FREE_TIER_MAX_REFERENCES:
            return "free_limit"
        if count >= MAX_REFERENCES:
            return "hard_cap"
        return "ok"

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
        reason = self._check_can_add()
        if reason == "free_limit":
            self.upsell_requested.emit()
            return
        if reason == "hard_cap":
            self._show_temp_error(
                tr("Maximum {n} reference images reached").format(n=MAX_REFERENCES)
            )
            return
        # Parent on the top-level window (the dock), not self: this widget is
        # hidden while the store is empty, and a hidden parent causes the file
        # picker to silently fail to show up on Windows.
        paths, _ = QFileDialog.getOpenFileNames(
            self.window(),
            tr("Select reference images or layers"),
            "",
            tr(
                "Supported files (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff "
                "*.asc *.img *.vrt *.dem *.pdf *.shp *.gpkg *.geojson *.kml *.kmz)"
            )
            + ";;"
            + tr("All files (*)"),
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

    def add_layers(self, layers: list) -> None:
        """Entry point for layers dragged from the QGIS Layers panel."""
        if self._readonly:
            return
        added = 0
        error_shown = False
        for layer in layers:
            reason = self._check_can_add()
            if reason == "free_limit":
                self.upsell_requested.emit()
                break
            if reason == "hard_cap":
                if not error_shown:
                    self._show_temp_error(
                        tr("Maximum {n} reference images reached").format(n=MAX_REFERENCES)
                    )
                    error_shown = True
                break
            try:
                self._render_and_store([layer], layer.name())
                added += 1
            except ReferenceImageStoreError as err:
                self._show_temp_error(str(err))
                error_shown = True
        if added > 0:
            self._refresh()

    def _add_paths(self, paths: list[str]) -> None:
        added = 0
        error_shown = False
        for path in paths:
            reason = self._check_can_add()
            if reason == "free_limit":
                self.upsell_requested.emit()
                break
            if reason == "hard_cap":
                if not error_shown:
                    self._show_temp_error(
                        tr("Maximum {n} reference images reached").format(n=MAX_REFERENCES)
                    )
                    error_shown = True
                break
            ext = os.path.splitext(path)[1].lower()
            try:
                if ext in _IMAGE_EXTS:
                    self._store.add(path)
                else:
                    if ext == ".shp":
                        missing = _missing_shapefile_companions(path)
                        if missing:
                            raise ReferenceImageStoreError(
                                tr(
                                    "Shapefile {name} is missing required "
                                    "companion files ({missing}). Drop the "
                                    "whole set together."
                                ).format(
                                    name=os.path.basename(path),
                                    missing=", ".join(missing),
                                )
                            )
                    layers = load_transient_layers(path)
                    if not layers:
                        raise ReferenceImageStoreError(
                            tr("Could not load {name} as a layer").format(
                                name=os.path.basename(path)
                            )
                        )
                    self._render_and_store(layers, os.path.basename(path))
                added += 1
            except ReferenceImageStoreError as err:
                self._show_temp_error(str(err))
                error_shown = True
        if added > 0:
            self._refresh()

    def _render_and_store(self, layers: list, source_name: str) -> None:
        if any(is_remote_layer(lyr) for lyr in layers):
            raise ReferenceImageStoreError(
                tr(
                    "{name} is an online layer (WMS, WMTS, WFS, ArcGIS) and "
                    "cannot be used as a reference image. Export the area to a "
                    "file first."
                ).format(name=source_name)
            )
        extent, crs = self._current_view_extent()
        image = render_layers_to_qimage(
            layers, fallback_extent=extent, fallback_crs=crs
        )
        if image is None:
            raise ReferenceImageStoreError(
                tr("Could not render {name}").format(name=source_name)
            )
        self._store.add_from_qimage(image, source_name)

    def _current_view_extent(self):
        try:
            from qgis.utils import iface
            canvas = iface.mapCanvas() if iface is not None else None
            if canvas is not None:
                return canvas.extent(), canvas.mapSettings().destinationCrs()
        except Exception:  # nosec B110 - no canvas is a valid (no-fallback) state.
            pass
        return None, None

    def _show_temp_error(self, msg: str) -> None:
        """Emit an error that auto-clears after 4 seconds. Timer is parented
        to ``self`` so it dies with the widget instead of firing on a
        deleted C++ object."""
        self.error_occurred.emit(msg)
        if self._error_clear_timer is not None:
            self._error_clear_timer.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self.error_cleared.emit)
        timer.start(4000)
        self._error_clear_timer = timer

    def _on_remove(self, ref_id: str) -> None:
        self._store.remove(ref_id)
        self._refresh()

    def _open_preview(self, image_path: str) -> None:
        # Parent to QGIS main window, not to this widget. On macOS fullscreen,
        # a dialog parented to a widget inside a (possibly floating) dock can
        # open in a different Mission Control Space and yank the user out of
        # QGIS. See AIEditDockWidget._main_window_for_dialog for the rationale.
        parent_window = self
        try:
            from qgis.utils import iface
            mw = iface.mainWindow() if iface is not None else None
            if mw is not None:
                parent_window = mw
        except Exception:  # nosec B110 - fall back to self on any failure.
            pass
        dlg = _ImagePreviewDialog(image_path, parent_window)
        dlg.exec()
