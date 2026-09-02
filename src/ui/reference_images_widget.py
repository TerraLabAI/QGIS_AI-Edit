"""Thumbnail strip widget for context reference images.

This widget renders only the header + thumbnail row. The bordered container,
drag-drop visual feedback, and the "attach" entry point all live on the
surrounding _PromptContainer (in dock_widget.py). The widget is invisible
whenever the store is empty.
"""
from __future__ import annotations

import os

from qgis.PyQt.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
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
from ..core.config_store import get_export_dial
from ..core.i18n import tr
from ..core.reference_image_store import (
    ReferenceImage,
    ReferenceImageStore,
    ReferenceImageStoreError,
    max_references,
)
from .dock.style import FOCUS_RING
from .layer_renderer import (
    layer_misses_zone,
    load_transient_layers,
    render_layers_to_qimage,
)
from .panel_helpers import main_window_for_dialog

REF_THUMB_PX = 56
# Thumbnail frame = the image plus the 2px max border on each side, so the
# focus ring has its own pixels and never lands under the image (same inset the
# version strip's tiles use).
_THUMB_BOX_PX = REF_THUMB_PX + 4
# Remove button box, painted edge to edge: everything that removes the
# reference is visible, and the box clears the WCAG 2.2 minimum target.
_REMOVE_BTN_PX = 24
# Free-tier reference-image cap. This is a UX/entitlement gate, not a storage
# limit: the store keeps enforcing MAX_REFERENCES as the hard ceiling, and the
# backend rejects free-tier generations carrying more than this many context
# images. Adding past this on free tier surfaces an upsell, not an error.
#
# MUST equal the server gate (403 REFERENCE_LIMIT above 1 for a free account).
# It shipped as 3 while the backend refused above 1, so a free user could add
# three references and only learn on Generate that two were never allowed.
FREE_TIER_MAX_REFERENCES = 1
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def free_tier_max_references() -> int:
    """Free-tier reference cap, server-tunable. Read at add-click time so a
    config refresh applies in-session."""
    return get_export_dial(
        "entitlements.free_tier_max_references", FREE_TIER_MAX_REFERENCES
    )


def reference_add_reason(store: ReferenceImageStore, free_tier: bool) -> str:
    """Single source of truth for the add gate, shared by the strip and the
    Reference panel. Returns ``"ok"``, ``"free_limit"`` (free tier nudge), or
    ``"hard_cap"`` (the MAX_REFERENCES ceiling). Free-tier is checked first so
    a free user at the limit always sees the upsell, never the generic cap
    message."""
    count = store.count()
    if free_tier and count >= free_tier_max_references():
        return "free_limit"
    if count >= max_references():
        return "hard_cap"
    return "ok"


def reference_preview_title(record: ReferenceImage | None, is_markup: bool) -> str:
    """Window title for a reference preview: the source name, with a Mark up
    tag when the image is the Mark up composite."""
    if record is None:
        return tr("Reference image")
    if is_markup:
        return tr("Mark up reference")
    name = (record.source_filename or "").strip()
    if name:
        return tr("Reference image: {name}").format(name=name)
    return tr("Reference image")


def open_reference_preview(anchor: QWidget, image_path: str, title: str) -> None:
    """Open the modal reference preview, shared by the strip and the panel.

    A null pixmap would open an empty, content-less modal that still blocks
    the user (reject() in the dialog's __init__ doesn't stop a later exec()),
    so bail before building it. Parented to the QGIS main window, not the
    anchor: on macOS fullscreen a dialog parented to a widget inside a
    (possibly floating) dock can open in a different Mission Control Space
    and yank the user out of QGIS."""
    if QPixmap(image_path).isNull():
        return
    parent_window = main_window_for_dialog(anchor)
    dlg = _ImagePreviewDialog(image_path, parent_window, title=title)
    dlg.exec()
    # Parented to the long-lived main window, so free it explicitly instead
    # of leaking one dialog plus its large scaled pixmap per open.
    dlg.deleteLater()


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
    f"QFrame:focus {{ border: 2px solid {FOCUS_RING}; }}"
)

# No margin: a QSS margin trims what is painted and not what is clickable, and
# this button removes the reference. It used to leave 176 of its 576 px2
# unpainted but still destructive, so the circle now fills the whole box.
_REMOVE_BTN_STYLE = (
    "QToolButton { background: rgba(0, 0, 0, 0.55); color: white;"
    " border: none; border-radius: 12px; font-weight: bold; font-size: 13px;"
    " margin: 0px; }"
    "QToolButton:hover { background: rgba(211, 47, 47, 0.85); }"
)

_THUMB_BADGE_STYLE = (
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
        self._hovered = False
        self.setFixedSize(_THUMB_BOX_PX, _THUMB_BOX_PX)
        self.setStyleSheet(_THUMB_STYLE)
        self.setCursor(QtC.PointingHandCursor)
        # Preview and removal both hang off this frame, so it has to take focus
        # or neither is reachable without a mouse. TabFocus, never StrongFocus:
        # a click-focusable frame pulls the caret out of the prompt box the user
        # is writing in, and it parks focus on a Delete target they never asked
        # for.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(tr("Reference image {n}").format(n=index))
        self.setAccessibleDescription(
            tr("Press Enter to preview it, Delete to remove it.")
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

        # Number badge (top-left corner tag)
        self._badge = QLabel(str(index), self)
        self._badge.setFixedSize(14, 14)
        self._badge.setAlignment(QtC.AlignCenter)
        self._badge.setStyleSheet(_THUMB_BADGE_STYLE)
        font = QFont()
        font.setPixelSize(9)
        font.setBold(True)
        self._badge.setFont(font)
        self._badge.move(2, 2)

        # "Whole layer" (bottom-left): the layer missed the zone, so it was
        # rendered at its own extent and travels unaligned. Text, not a hue.
        self._whole_badge = None
        if getattr(record, "whole_layer", False):
            self._whole_badge = QLabel(tr("Whole layer"), self)
            self._whole_badge.setStyleSheet(_THUMB_BADGE_STYLE)
            self._whole_badge.setFont(font)
            self._whole_badge.setToolTip(tr(
                "This layer does not cover your zone, so it is sent whole, "
                "not aligned to it."
            ))
            self._whole_badge.adjustSize()
            self._whole_badge.move(
                2, _THUMB_BOX_PX - self._whole_badge.height() - 2
            )

        # Remove button (top-right) - hidden by default, revealed on hover or
        # while the thumbnail has focus, so the strip looks clean and a keyboard
        # user still sees that Delete does something. It stays out of the tab
        # chain: a hidden widget cannot hold focus, and Delete on the focused
        # thumbnail runs the same removal.
        self._remove_btn = QToolButton(self)
        self._remove_btn.setText("×")
        self._remove_btn.setFixedSize(_REMOVE_BTN_PX, _REMOVE_BTN_PX)
        self._remove_btn.setStyleSheet(_REMOVE_BTN_STYLE)
        self._remove_btn.setCursor(QtC.PointingHandCursor)
        self._remove_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._remove_btn.setAccessibleName(
            tr("Remove reference image {n}").format(n=index)
        )
        self._remove_btn.setToolTip(tr("Remove this reference image"))
        self._remove_btn.move(_THUMB_BOX_PX - _REMOVE_BTN_PX, 0)
        self._remove_btn.setVisible(False)
        self._remove_btn.clicked.connect(
            lambda: self.remove_clicked.emit(self._ref_id)
        )

    def set_readonly(self, readonly: bool) -> None:
        """Hide the remove button so the thumbnail stays clickable for
        preview but cannot be deleted (used during generation)."""
        self._readonly = readonly
        self._update_remove_visible()

    def _update_remove_visible(self) -> None:
        self._remove_btn.setVisible(
            not self._readonly and (self._hovered or self.hasFocus())
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

    # Keys this thumbnail answers for itself, exactly the two its accessible
    # description promises (plus Space, the standard activation twin). NOT
    # Backspace: it is the most reflexive key there is while writing a prompt,
    # and here it used to delete the reference with no dialog and no undo.
    _PREVIEW_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
    _REMOVE_KEYS = (Qt.Key.Key_Delete,)

    def event(self, event):
        # The dock owns QShortcut(Return/Enter, WindowShortcut) for Generate,
        # and a window shortcut wins the keyboard race before any focused
        # child's keyPressEvent runs. Accepting the ShortcutOverride is Qt's
        # way to say "this key is mine while I hold focus", so Enter on a
        # focused thumbnail previews the image the description promises instead
        # of spending credits.
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
        # Keys we don't handle: ignore, so Tab, Escape and the QGIS shortcuts
        # keep working.
        event.ignore()

    def mousePressEvent(self, event):  # noqa: N802
        # Only swallow the click for the remove button when it is actually
        # visible - otherwise the top-right corner would silently eat preview
        # clicks even though there is no button there.
        pos = QtC.event_pos(event)
        if self._remove_btn.isVisible() and self._remove_btn.geometry().contains(pos):
            super().mousePressEvent(event)
            return
        self.preview_requested.emit(self._image_path)
        super().mousePressEvent(event)


class _ImagePreviewDialog(QDialog):
    """Modal preview of a reference image, scaled to fit screen."""

    def __init__(self, image_path: str, parent=None, title: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title or tr("Reference image"))
        self.setModal(True)

        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self.reject()
            return

        # The screen this dialog will open on, not the primary one: a laptop
        # panel next to a bigger main monitor gets a preview sized for the
        # main monitor and opens with its edges off-screen.
        screen = self.screen() or QApplication.primaryScreen()
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


class _HorizontalWheelScrollArea(QScrollArea):
    """Scroll area that turns a plain vertical wheel into horizontal movement.

    The thumbnail strip is one row high, so its vertical range is always zero.
    A Mac trackpad emits a horizontal delta on a two-finger swipe and scrolls
    it fine, but a Windows mouse wheel emits a vertical one, which Qt routes to
    the empty vertical bar. Without this the references past the visible few
    could not be reached at all with a wheel.
    """

    def wheelEvent(self, event):  # noqa: N802 - Qt naming
        delta = event.angleDelta()
        if delta.x() == 0 and delta.y() != 0:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - delta.y())
            event.accept()
            return
        super().wheelEvent(event)


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
        # Generation zone extent (+ its CRS), pushed by the dock. When set, every
        # reference is rendered at this exact extent so it aligns pixel-for-pixel
        # with the input image instead of using the looser canvas view.
        self._target_extent = None
        self._target_crs = None
        # Default restrictive (free) until the dock confirms the tier via
        # set_free_tier(), matching the dock's own _is_free_tier default. The
        # server enforces the real cap, so a brief restrictive window is safe.
        self._free_tier = True
        # Parented QTimer for the 4 s "error cleared" auto-dismiss. Holding a
        # ref so we can stop it when the widget is destroyed first.
        self._error_clear_timer: QTimer | None = None
        # Store id of the current Mark up composite, if any. Tracked so a new
        # drawing replaces the previous one instead of stacking.
        self._markup_ref_id: str | None = None

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

        self._thumbs_scroll = _HorizontalWheelScrollArea(self)
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
        # Hide both scrollbars - the wheel still scrolls the viewport
        # horizontally (see _HorizontalWheelScrollArea). Cleaner than
        # ScrollBarAsNeeded which paints a visible bar under the thumbnails.
        self._thumbs_scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        self._thumbs_scroll.setVerticalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        # One row of thumbnails, no extra space for a scrollbar.
        self._thumbs_scroll.setFixedHeight(_THUMB_BOX_PX)
        # Width follows the parent; never push the dock outward.
        self._thumbs_scroll.setSizePolicy(
            QtC.SizePolicyExpanding, QtC.SizePolicyFixed
        )
        outer.addWidget(self._thumbs_scroll)

        self._refresh()

    # -- public API --------------------------------------------------------

    def clear(self) -> None:
        self._store.clear()
        self._markup_ref_id = None
        self._refresh()

    def set_markup_image(self, image, name: str = "Mark up") -> None:
        """Add (or replace) the single Mark up composite reference - the user's
        zone with their marks on top. Re-drawing replaces the previous one so
        only one Mark up reference exists at a time."""
        if image is None or image.isNull():
            return
        if self._markup_ref_id is not None:
            self._store.remove(self._markup_ref_id)
            self._markup_ref_id = None
        cap = max_references()
        if self._store.count() >= cap:
            self._show_temp_error(
                tr("Maximum {n} reference images reached").format(n=cap)
            )
            self._refresh()
            return
        try:
            record = self._store.add_from_qimage(image, name)
            self._markup_ref_id = record.id
            # Flag it so the store ships it via the guidance channel, not as a
            # context image.
            self._store.mark_as_markup(record.id)
        except ReferenceImageStoreError as err:
            self._show_temp_error(str(err))
        self._refresh()

    def clear_markup_image(self) -> None:
        """Drop the Mark up composite reference, if present."""
        if self._markup_ref_id is not None:
            self._store.remove(self._markup_ref_id)
            self._markup_ref_id = None
            self._refresh()

    def get_all_b64(self) -> list[str]:
        return self._store.get_all_b64()

    def count(self) -> int:
        return self._store.count()

    def at_capacity(self) -> bool:
        # Hard ceiling only. Drives attach-button visibility in the dock: the
        # free-tier gate deliberately does NOT hide the button, so clicking it
        # surfaces the upsell instead of silently doing nothing.
        return self._store.count() >= max_references()

    def set_free_tier(self, free_tier: bool) -> None:
        """Tell the widget whether the current user is on the free tier.

        Pushed by the dock on every set_credits() call. Non-destructive: never
        removes images already added if the tier flips to free, only blocks
        adding more.
        """
        self._free_tier = free_tier

    def _check_can_add(self) -> str:
        """Add gate for this strip's tier state; see reference_add_reason."""
        return reference_add_reason(self._store, self._free_tier)

    def add_paths(self, paths: list[str]) -> None:
        """Public entry point for the container's drop zone and attach button."""
        if self._readonly:
            return
        self._add_paths(paths)

    def add_qimages(self, items: list) -> None:
        """Inject already-decoded reference images (e.g. reloaded from a past
        generation). Items are (QImage, name) tuples. Respects the hard
        MAX_REFERENCES ceiling but not the free-tier nudge, since these are the
        user's own prior references being reproduced."""
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
                tr("Maximum {n} reference images reached").format(n=max_references())
            )
            return
        # Parent on the top-level window (the dock), not self: this widget is
        # hidden while the store is empty, and a hidden parent causes the file
        # picker to silently fail to show up on Windows.
        supported = tr(
            "Supported files (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff "
            "*.asc *.img *.vrt *.dem *.pdf *.shp *.gpkg *.geojson *.kml *.kmz)"
        )
        file_filter = f"{supported};;{tr('All files (*)')}"
        paths, _ = QFileDialog.getOpenFileNames(
            self.window(),
            tr("Select reference images or layers"),
            "",
            file_filter,
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
                        tr("Maximum {n} reference images reached").format(n=max_references())
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
                        tr("Maximum {n} reference images reached").format(n=max_references())
                    )
                    error_shown = True
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

    def set_target_extent(self, extent, crs) -> None:
        """Set the generation-zone extent that references should align to. Pass
        (None, None) to fall back to the canvas view."""
        self._target_extent = extent
        self._target_crs = crs

    def _render_and_store(self, layers: list, source_name: str) -> None:
        extent, crs = self._current_view_extent()
        # Decided before the render, from the same rule the renderer applies
        # when it falls back to the layer's own extent.
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
        self._store.add_from_qimage(image, source_name, whole_layer=whole)
        telemetry.track(
            te.REFERENCE_ADDED, {"source_kind": "layer", "whole_layer": whole}
        )
        if whole:
            self._notify_whole_layer(source_name)

    def _notify_whole_layer(self, source_name: str) -> None:
        """Say once, in the message bar, that a layer went whole and unaligned."""
        try:
            from qgis.utils import iface

            iface.messageBar().pushInfo(
                "AI Edit",
                tr('"{layer}" does not cover your zone. It is sent as a whole image.')
                .format(layer=source_name),
            )
        except Exception:  # nosec B110 - no message bar headless; the badge still shows.
            pass

    def add_captured_image(self, image, source_name: str) -> None:
        """Store a rectangle captured on the canvas ("Map" in the panel).

        Same gates as every other add: free-tier upsell, hard cap, read-only
        during a run. A failed render lands as the temp error, never as an
        empty thumbnail.
        """
        if self._readonly:
            return
        reason = self._check_can_add()
        if reason == "free_limit":
            self.upsell_requested.emit()
            return
        if reason == "hard_cap":
            self._show_temp_error(
                tr("Maximum {n} reference images reached").format(n=max_references())
            )
            return
        if image is None or image.isNull():
            self._show_temp_error(tr("Could not capture the map. Zoom in and try again."))
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

    def remove_reference(self, ref_id: str) -> None:
        """Public removal entry point (used by the Reference panel), so every
        remove funnels through this strip and refreshes both views."""
        self._on_remove(ref_id)

    def _on_remove(self, ref_id: str) -> None:
        if ref_id == self._markup_ref_id:
            self._markup_ref_id = None
        self._store.remove(ref_id)
        self._refresh()

    def _build_preview_title(self, image_path: str) -> str:
        """Window title for the preview: the source name, with a Mark up tag when
        the image is the Mark up composite, so the user knows what they opened."""
        record = next(
            (r for r in self._store.list() if r.path == image_path), None
        )
        is_markup = record is not None and record.id == self._markup_ref_id
        return reference_preview_title(record, is_markup)

    def _open_preview(self, image_path: str) -> None:
        open_reference_preview(self, image_path, self._build_preview_title(image_path))
