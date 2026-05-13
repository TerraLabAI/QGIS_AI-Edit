






from __future__ import annotations

import os

from qgis.PyQt.QtCore import QEvent, QSettings, QSize, Qt, QTimer, pyqtSignal
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
from ..core import telemetry
from ..core import telemetry_events as te
from ..core.config_store import get_export_copy, get_export_dial, get_export_dial_ratio
from ..core.i18n import tr
from ..core.reference_image_store import (
    ReferenceImage,
    ReferenceImageStore,
    ReferenceImageStoreError,
    max_references,
)
from .dock import design_tokens as tokens
from .icons import icon_for, pixmap_for
from .layer_renderer import (
    layer_misses_zone,
    load_transient_layers,
    render_layers_to_qimage,
)
from .panel_helpers import main_window_for_dialog

REF_THUMB_PX = 56



_THUMB_BOX_PX = REF_THUMB_PX + 4


_REMOVE_BTN_PX = 24








FREE_TIER_MAX_REFERENCES = 1
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

_PREVIEW_MAX_SCREEN_RATIO = 0.8

_ERROR_CLEAR_MS = 4000


def free_tier_max_references() -> int:


    return get_export_dial(
        "entitlements.free_tier_max_references", FREE_TIER_MAX_REFERENCES
    )


def reference_add_reason(store: ReferenceImageStore, free_tier: bool) -> str:





    count = store.count()
    if free_tier and count >= free_tier_max_references():
        return "free_limit"
    if count >= max_references():
        return "hard_cap"
    return "ok"


def reference_preview_title(record: ReferenceImage | None, is_markup: bool) -> str:


    if record is None:
        return get_export_copy("widgets.reference_images_widget.reference_image", tr("Reference image"))
    if is_markup:
        return get_export_copy("widgets.reference_images_widget.draw_reference", tr("Your drawing"))
    name = (record.source_filename or "").strip()
    if name:
        return tr("Reference image: {name}").format(name=name)
    return get_export_copy("widgets.reference_images_widget.reference_image", tr("Reference image"))


def open_reference_preview(anchor: QWidget, image_path: str, title: str) -> None:








    if QPixmap(image_path).isNull():
        return
    parent_window = main_window_for_dialog(anchor)
    dlg = _ImagePreviewDialog(image_path, parent_window, title=title)
    dlg.exec()


    dlg.deleteLater()





_SHP_REQUIRED_COMPANIONS = (".shx", ".dbf")


def _missing_shapefile_companions(shp_path: str) -> list[str]:



    base, _ = os.path.splitext(shp_path)
    missing: list[str] = []
    for ext in _SHP_REQUIRED_COMPANIONS:
        if not (os.path.isfile(base + ext) or os.path.isfile(base + ext.upper())):
            missing.append(ext)
    return missing


_LAST_DIR_KEY = "ai_edit/reference_last_dir"


def _hard_cap_message() -> str:
    return tr("Maximum {n} reference images reached").format(n=max_references())


def _partial_cap_message(added: int, total: int) -> str:


    if added <= 0 or total <= 1:
        return _hard_cap_message()
    return tr("Added {added} of {total}. The limit is {n} references.").format(
        added=added, total=total, n=max_references()
    )


def _failures_message(failures: list[tuple[str, str]]) -> str:


    if len(failures) == 1:
        name, reason = failures[0]
        return tr("Could not add {name}. {reason}").format(name=name, reason=reason)
    names = ", ".join(name for name, _reason in failures)
    return tr("{n} were not added: {names}. {reason}").format(
        n=len(failures), names=names, reason=failures[0][1]
    )


_THUMB_STYLE = (
    f"QFrame {{ background: {tokens.INSET};"
    f" border: 1px solid {tokens.LINE}; border-radius: {tokens.RADIUS_CONTROL}px; }}"
    f"QFrame:focus {{ border: 2px solid {tokens.ACCENT_BORDER}; }}"
)




_REMOVE_BTN_STYLE = (
    f"QToolButton {{ background: {tokens.SURFACE}; border: 1px solid {tokens.LINE_STRONG};"
    " border-radius: 12px; margin: 0px; padding: 0px; }"
    f"QToolButton:hover {{ background: {tokens.RED_TINT}; border-color: {tokens.RED}; }}"
)


_NUMBER_BADGE_PX = 16
_THUMB_BADGE_STYLE = (
    f"QLabel {{ background: {tokens.SURFACE}; color: {tokens.INK};"
    f" border: none; border-radius: {tokens.RADIUS_CHIP}px;"
    f" font-size: {tokens.FONT_HINT}px; font-weight: bold; padding: 0 2px; }}"
)


class _ThumbWidget(QFrame):







    remove_clicked = pyqtSignal(str)
    preview_requested = pyqtSignal(str)

    def __init__(self, record: ReferenceImage, index: int, parent=None,
                 remove_overlay: bool = True, whole_badge: bool = True):
        super().__init__(parent)
        self._ref_id = record.id
        self._image_path = record.path
        self._readonly = False
        self._hovered = False
        self._remove_overlay = bool(remove_overlay)
        self.setFixedSize(_THUMB_BOX_PX, _THUMB_BOX_PX)
        self.setStyleSheet(_THUMB_STYLE)
        self.setCursor(QtC.PointingHandCursor)





        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(tr("Reference image {n}").format(n=index))
        self.setToolTip(get_export_copy(
            "widgets.reference_images_widget.thumb_preview_tooltip", tr("Preview")
        ))
        self.setAccessibleDescription(
            get_export_copy(
                "widgets.reference_images_widget.thumb_a11y_hint",
                tr("Press Enter to preview it, Delete to remove it."),
            )
        )

        self._pixmap_label = QLabel(self)
        self._pixmap_label.setGeometry(2, 2, REF_THUMB_PX, REF_THUMB_PX)
        self._pixmap_label.setAlignment(QtC.AlignCenter)
        pixmap = QPixmap(record.path)
        if not pixmap.isNull():
            self._pixmap_label.setPixmap(
                pixmap.scaled(
                    QSize(REF_THUMB_PX, REF_THUMB_PX),
                    QtC.KeepAspectRatio,
                    QtC.SmoothTransformation,
                )
            )


        self._badge = QLabel(str(index), self)
        self._badge.setFixedSize(_NUMBER_BADGE_PX, _NUMBER_BADGE_PX)
        self._badge.setAlignment(QtC.AlignCenter)
        self._badge.setStyleSheet(_THUMB_BADGE_STYLE)
        font = QFont()
        font.setPixelSize(tokens.FONT_HINT)
        font.setBold(True)
        self._badge.setFont(font)
        self._badge.move(2, 2)


        self._badge.setVisible(index > 0)






        self._whole_badge = None
        if whole_badge and getattr(record, "whole_layer", False):
            self._whole_badge = QLabel(self)
            self._whole_badge.setFixedSize(_NUMBER_BADGE_PX, _NUMBER_BADGE_PX)
            self._whole_badge.setAlignment(QtC.AlignCenter)
            self._whole_badge.setStyleSheet(_THUMB_BADGE_STYLE)
            self._whole_badge.setPixmap(
                pixmap_for(self._whole_badge, "expand", 10, tokens.qcolor(tokens.INK))
            )
            whole_tip = get_export_copy(
                "widgets.reference_images_widget.whole_layer_tooltip",
                tr(
                    "This layer does not cover your zone, so it is sent whole, "
                    "not aligned to it."
                ),
            )
            self._whole_badge.setToolTip(whole_tip)
            self.setToolTip(whole_tip)
            self._whole_badge.move(2, _THUMB_BOX_PX - _NUMBER_BADGE_PX - 2)






        self._remove_btn = QToolButton(self)
        self._remove_btn.setIcon(icon_for(self._remove_btn, "close", 12, tokens.qcolor(tokens.INK)))
        self._remove_btn.setFixedSize(_REMOVE_BTN_PX, _REMOVE_BTN_PX)
        self._remove_btn.setStyleSheet(_REMOVE_BTN_STYLE)
        self._remove_btn.setCursor(QtC.PointingHandCursor)
        self._remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._remove_btn.setAccessibleName(
            tr("Remove reference image {n}").format(n=index)
        )
        self._remove_btn.setToolTip(
            get_export_copy("widgets.reference_images_widget.remove_tooltip", tr("Remove this reference image"))
        )
        self._remove_btn.move(_THUMB_BOX_PX - _REMOVE_BTN_PX, 0)
        self._remove_btn.setVisible(False)
        self._remove_btn.clicked.connect(
            lambda: self.remove_clicked.emit(self._ref_id)
        )

    def set_readonly(self, readonly: bool) -> None:


        self._readonly = readonly
        self._update_remove_visible()

    def _update_remove_visible(self) -> None:
        self._remove_btn.setVisible(
            self._remove_overlay
            and not self._readonly
            and (self._hovered or self.hasFocus())
        )

    def enterEvent(self, event):  # noqa: N802
        self._hovered = True
        self._update_remove_visible()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hovered = False
        self._update_remove_visible()
        super().leaveEvent(event)

    def focusInEvent(self, event):  # noqa: N802
        self._update_remove_visible()
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802
        self._update_remove_visible()
        super().focusOutEvent(event)





    _PREVIEW_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
    _REMOVE_KEYS = (Qt.Key.Key_Delete,)

    def event(self, event):






        if event.type() == QEvent.Type.ShortcutOverride:
            if event.key() in self._PREVIEW_KEYS + self._REMOVE_KEYS:
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        if key in self._PREVIEW_KEYS:
            self.preview_requested.emit(self._image_path)
            event.accept()
            return
        if key in self._REMOVE_KEYS:
            if not self._readonly:
                self.remove_clicked.emit(self._ref_id)
            event.accept()
            return


        event.ignore()

    def mousePressEvent(self, event):  # noqa: N802



        pos = QtC.event_pos(event)
        if self._remove_btn.isVisible() and self._remove_btn.geometry().contains(pos):
            super().mousePressEvent(event)
            return
        self.preview_requested.emit(self._image_path)
        super().mousePressEvent(event)


class _ImagePreviewDialog(QDialog):


    def __init__(self, image_path: str, parent=None, title: str | None = None):
        super().__init__(parent)
        default_title = get_export_copy("widgets.reference_images_widget.reference_image", tr("Reference image"))
        self.setWindowTitle(title or default_title)
        self.setModal(True)

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.reject()
            return




        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else None
        screen_ratio = get_export_dial_ratio(
            "widgets.reference_images_widget.preview_max_screen_ratio", _PREVIEW_MAX_SCREEN_RATIO
        )
        max_w = int(avail.width() * screen_ratio) if avail is not None else 1280
        max_h = int(avail.height() * screen_ratio) if avail is not None else 800
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

        self.accept()
        super().mousePressEvent(event)


class _HorizontalWheelScrollArea(QScrollArea):









    def wheelEvent(self, event):  # noqa: N802
        delta = event.angleDelta()
        if delta.x() == 0 and delta.y() != 0:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta.y())
            event.accept()
            return
        super().wheelEvent(event)


def _image_is_empty(image) -> bool:




















    if image is None or image.isNull():
        return True
    try:
        argb = image.convertToFormat(QtC.FormatARGB32)
        bits = argb.constBits()
        size = argb.sizeInBytes() if hasattr(argb, "sizeInBytes") else argb.byteCount()
        try:
            bits.setsize(size)
        except AttributeError:
            pass
        raw = bytes(bits)
        if len(raw) < 8:
            return True


        return raw == raw[:4] * (len(raw) // 4)
    except Exception:  # noqa: BLE001
        return False


class ReferenceImagesWidget(QWidget):


    images_changed = pyqtSignal()
    error_occurred = pyqtSignal(str)
    error_cleared = pyqtSignal()


    upsell_requested = pyqtSignal()

    def __init__(self, store: ReferenceImageStore, parent=None):
        super().__init__(parent)
        self._store = store
        self._readonly = False



        self._target_extent = None
        self._target_crs = None



        self._free_tier = True


        self._error_clear_timer: QTimer | None = None



        self._above_ref_ids: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)






        self._thumbs_host = QWidget(self)
        self._thumbs_host.setStyleSheet("background: transparent;")
        self._thumbs_row = QHBoxLayout(self._thumbs_host)
        self._thumbs_row.setContentsMargins(0, 0, 0, 0)
        self._thumbs_row.setSpacing(6)

        self._thumbs_scroll = _HorizontalWheelScrollArea(self)
        self._thumbs_scroll.setWidget(self._thumbs_host)
        self._thumbs_scroll.setWidgetResizable(True)
        self._thumbs_scroll.setFrameShape(QtC.FrameNoFrame)



        self._thumbs_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self._thumbs_scroll.viewport().setAutoFillBackground(False)



        self._thumbs_scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        self._thumbs_scroll.setVerticalScrollBarPolicy(QtC.ScrollBarAlwaysOff)

        self._thumbs_scroll.setFixedHeight(_THUMB_BOX_PX)

        self._thumbs_scroll.setSizePolicy(
            QtC.SizePolicyExpanding, QtC.SizePolicyFixed
        )
        outer.addWidget(self._thumbs_scroll)

        self._refresh()



    def clear(self) -> None:
        self._store.clear()
        self._above_ref_ids = []
        self._refresh()

    def set_layers_above(self, layers: list) -> int:












        if self._readonly:
            return 0
        for ref_id in self._above_ref_ids:
            self._store.remove(ref_id)
        self._above_ref_ids = []
        for layer in layers or []:
            if self._store.count() >= self.add_limit():
                break
            try:



                record = self._render_and_store([layer], layer.name(), drop_if_empty=True)
            except (ReferenceImageStoreError, RuntimeError):
                continue
            self._above_ref_ids.append(record.id)
        self._refresh()
        return len(self._above_ref_ids)

    def clear_markup_image(self) -> None:



        markup = next(
            (r for r in self._store.list() if self._store.is_markup(r.id)), None
        )
        if markup is not None:
            self._store.remove(markup.id)
            self._refresh()

    def get_all_b64(self) -> list[str]:
        return self._store.get_all_b64()

    def count(self) -> int:
        return self._store.count()

    def at_capacity(self) -> bool:



        return self._store.count() >= max_references()

    def set_free_tier(self, free_tier: bool) -> None:






        self._free_tier = free_tier

    def is_free_tier(self) -> bool:
        return self._free_tier

    def add_limit(self) -> int:



        if self._free_tier:
            return min(free_tier_max_references(), max_references())
        return max_references()

    def _check_can_add(self) -> str:

        return reference_add_reason(self._store, self._free_tier)

    def add_paths(self, paths: list[str]) -> None:

        if self._readonly:
            return
        self._add_paths(paths)

    def add_qimages(self, items: list) -> None:




        if self._readonly or not items:
            return
        added = 0
        for image, name in items:
            if self._store.count() >= max_references():
                break
            if image is None or image.isNull():
                continue
            try:
                self._store.add_from_qimage(image, name or "reference")
                added += 1
            except ReferenceImageStoreError as err:
                self._show_temp_error(str(err))
        if added > 0:
            self._refresh()

    def set_readonly(self, readonly: bool) -> None:



        self._readonly = readonly
        for i in range(self._thumbs_row.count()):
            item = self._thumbs_row.itemAt(i)
            widget = item.widget() if item is not None else None
            if isinstance(widget, _ThumbWidget):
                widget.set_readonly(readonly)

    def open_file_picker(self) -> None:

        if self._readonly:
            return
        reason = self._check_can_add()
        if reason == "free_limit":
            self.upsell_requested.emit()
            return
        if reason == "hard_cap":
            self._show_temp_error(_hard_cap_message())
            return



        supported = tr(
                "Supported files (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff "
                "*.asc *.img *.vrt *.dem *.pdf *.shp *.gpkg *.geojson *.kml *.kmz)"
            )
        all_files = tr("All files (*)")
        file_filter = f"{supported};;{all_files}"
        picker_title = get_export_copy(
            "widgets.reference_images_widget.file_picker_title", tr("Select reference images or layers")
        )



        settings = QSettings()
        start_dir = str(settings.value(_LAST_DIR_KEY, "") or "")
        if start_dir and not os.path.isdir(start_dir):
            start_dir = ""
        paths, _ = QFileDialog.getOpenFileNames(
            self.window(),
            picker_title,
            start_dir,
            file_filter,
        )
        if paths:
            settings.setValue(_LAST_DIR_KEY, os.path.dirname(paths[0]))
            self._add_paths(paths)



    def _refresh(self) -> None:


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

    def add_layers(self, layers: list, drop_if_empty: bool = False) -> None:








        if self._readonly:
            return
        added = 0
        failures: list[tuple[str, str]] = []
        stop = ""
        for layer in layers:
            stop = self._check_can_add()
            if stop != "ok":
                break
            try:
                self._render_and_store([layer], layer.name(),
                                       drop_if_empty=drop_if_empty)
                added += 1
            except ReferenceImageStoreError as err:
                failures.append((layer.name(), str(err)))
        self._finish_batch(added, len(layers), failures, stop)

    def _finish_batch(self, added: int, total: int,
                      failures: list[tuple[str, str]], stop: str) -> None:




        if added > 0:
            self._refresh()
        if stop == "free_limit":
            self.upsell_requested.emit()
        elif stop == "hard_cap":
            self._show_temp_error(_partial_cap_message(added, total))
        elif failures:
            self._show_temp_error(_failures_message(failures))

    def _add_paths(self, paths: list[str]) -> None:
        added = 0
        failures: list[tuple[str, str]] = []
        stop = ""
        for path in paths:
            stop = self._check_can_add()
            if stop != "ok":
                break
            ext = os.path.splitext(path)[1].lower()
            try:
                if ext in _IMAGE_EXTS:
                    self._store.add(path)
                    telemetry.track(
                        te.REFERENCE_ADDED,
                        {"source_kind": "file", "whole_layer": False},
                    )
                else:
                    if ext == ".shp":
                        missing = _missing_shapefile_companions(path)
                        if missing:


                            raise ReferenceImageStoreError(
                                tr(
                                    "Its companion files are missing ({missing}). "
                                    "Drop the whole set together."
                                ).format(missing=", ".join(missing))
                            )
                    layers = load_transient_layers(path)
                    if not layers:
                        raise ReferenceImageStoreError(get_export_copy(
                            "widgets.reference_images_widget.not_openable",
                            tr("Not an image or a map file QGIS can open."),
                        ))



                    self._render_and_store(
                        layers, os.path.basename(path), source_kind="file"
                    )
                added += 1
            except ReferenceImageStoreError as err:
                failures.append((os.path.basename(path), str(err)))
        self._finish_batch(added, len(paths), failures, stop)

    def set_target_extent(self, extent, crs) -> None:


        self._target_extent = extent
        self._target_crs = crs

    def _render_and_store(self, layers: list, source_name: str,
                          source_kind: str = "layer",
                          drop_if_empty: bool = False) -> ReferenceImage:
        extent, crs = self._current_view_extent()


        whole = layer_misses_zone(layers, self._target_extent, self._target_crs)
        image = render_layers_to_qimage(
            layers,
            fallback_extent=extent,
            fallback_crs=crs,
            force_extent=self._target_extent,
            force_crs=self._target_crs,
        )
        if image is None:
            raise ReferenceImageStoreError(
                tr("Could not render {name}").format(name=source_name)
            )
        if drop_if_empty and _image_is_empty(image):
            raise ReferenceImageStoreError(
                tr("{name} draws nothing inside your zone").format(name=source_name)
            )
        record = self._store.add_from_qimage(
            image, source_name, source_kind=source_kind, whole_layer=whole
        )
        telemetry.track(
            te.REFERENCE_ADDED, {"source_kind": source_kind, "whole_layer": whole}
        )
        if whole:
            self._notify_whole_layer(source_name)
        return record

    def _notify_whole_layer(self, source_name: str) -> None:

        try:
            from qgis.utils import iface

            iface.messageBar().pushInfo(
                "AI Edit",
                tr('"{layer}" does not cover your zone. It is sent as a whole image.')
                .format(layer=source_name),
            )
        except Exception:  # nosec B110
            pass

    def add_captured_image(self, image, source_name: str) -> None:






        if self._readonly:
            return
        reason = self._check_can_add()
        if reason == "free_limit":
            self.upsell_requested.emit()
            return
        if reason == "hard_cap":
            self._show_temp_error(_hard_cap_message())
            return
        if image is None or image.isNull():
            self._show_temp_error(get_export_copy(
                "widgets.reference_images_widget.map_capture_failed",
                tr("Could not capture the map. Zoom in and try again."),
            ))
            return
        try:
            self._store.add_from_qimage(image, source_name, source_kind="map")
        except ReferenceImageStoreError as err:
            self._show_temp_error(str(err))
            return
        telemetry.track(
            te.REFERENCE_ADDED, {"source_kind": "map", "whole_layer": False}
        )
        self._refresh()

    def _current_view_extent(self):
        try:
            from qgis.utils import iface
            canvas = iface.mapCanvas() if iface is not None else None
            if canvas is not None:
                return canvas.extent(), canvas.mapSettings().destinationCrs()
        except Exception:  # nosec B110
            pass
        return None, None

    def _show_temp_error(self, msg: str) -> None:



        self.error_occurred.emit(msg)
        if self._error_clear_timer is not None:
            self._error_clear_timer.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self.error_cleared.emit)
        timer.start(get_export_dial("widgets.reference_images_widget.error_clear_ms", _ERROR_CLEAR_MS))
        self._error_clear_timer = timer

    def remove_reference(self, ref_id: str) -> None:


        self._on_remove(ref_id)

    def _on_remove(self, ref_id: str) -> None:
        self._store.remove(ref_id)
        if ref_id in self._above_ref_ids:
            self._above_ref_ids.remove(ref_id)
        self._refresh()

    def _build_preview_title(self, image_path: str) -> str:


        record = next(
            (r for r in self._store.list() if r.path == image_path), None
        )
        is_markup = record is not None and self._store.is_markup(record.id)
        return reference_preview_title(record, is_markup)

    def _open_preview(self, image_path: str) -> None:
        open_reference_preview(self, image_path, self._build_preview_title(image_path))
