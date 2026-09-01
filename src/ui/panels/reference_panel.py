"""Reference panel: the full-dock view for managing reference images.

Sibling-swap panel (same pattern as MarkupPanel): swaps with the dock's
main widget. It is a second view over the SAME ReferenceImageStore as the
compact thumbnail strip above the prompt; every add and remove funnels
through the shared ReferenceImagesWidget so gating (free tier, hard cap,
server kill switch wired by the dock) and the strip refresh stay in one
place. On top of the strip it adds the two explicit import entry points
and a per-image note field ("what should the AI take from this image"),
persisted in the store and sent as context_image_notes.

References stay hidden layers-wise: file imports never touch the project,
and layer picks are rendered to an offscreen image by the strip's existing
flow. Nothing here changes any layer's visibility.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QPoint, QPointF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPolygonF
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_dial
from ...core.i18n import tr
from ...core.reference_image_store import ReferenceImage, ReferenceImageStore
from ..dock.style import _BTN_GHOST, BRAND_RED, FOCUS_RING
from ..onboarding_hint import HINT_REFERENCE, DismissibleHint, open_guide
from ..panel_helpers import build_panel_header, make_hidpi_pixmap
from ..reference_images_widget import (
    ReferenceImagesWidget,
    _ThumbWidget,
    open_reference_preview,
    reference_preview_title,
)

# Ceiling for one per-image note, server-tunable. Notes are short pointers
# ("match this roof color"), not prompts; the prompt box is for prose.
REFERENCE_NOTE_MAX_CHARS = 300


def reference_note_max_chars() -> int:
    """Per-note character cap, read at row build so a config refresh applies
    in-session."""
    return get_export_dial("reference_notes.max_chars", REFERENCE_NOTE_MAX_CHARS)


# 24px line icons for the two import chips, drawn in the Mark up tool-icon
# voice (same stroke weight, round caps, theme text colour) - emoji read as
# off-brand here (owner call 2026-08-03).
_IMPORT_ICON_PX = 24


def _make_import_icon(shape: str, color: QColor) -> QIcon:
    """"file": a folder outline. "layer": three stacked map sheets."""
    pm = make_hidpi_pixmap(_IMPORT_ICON_PX)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(1.9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if shape == "file":
        # Folder outline: left-edge tab, then the body.
        p.drawPolygon(QPolygonF([
            QPointF(4, 18.5), QPointF(4, 6.5), QPointF(9.5, 6.5),
            QPointF(11.5, 9), QPointF(20, 9), QPointF(20, 18.5),
        ]))
    else:
        # Classic layers glyph: one full diamond, two chevrons beneath.
        p.drawPolygon(QPolygonF([
            QPointF(12, 3.5), QPointF(20, 7.5),
            QPointF(12, 11.5), QPointF(4, 7.5),
        ]))
        for dy in (0.0, 3.5):
            p.drawPolyline(QPolygonF([
                QPointF(4.5, 11.5 + dy),
                QPointF(12, 15.5 + dy),
                QPointF(19.5, 11.5 + dy),
            ]))
    p.end()
    return QIcon(pm)


# Small source tag next to the reference name (File / Layer / Mark up).
# Neutral tint: it is metadata, not a message.
_SOURCE_TAG_QSS = (
    "QLabel { background: rgba(128, 128, 128, 0.14);"
    " border: 1px solid rgba(128, 128, 128, 0.30); border-radius: 3px;"
    " color: palette(text); font-size: 9px; font-weight: 600;"
    " padding: 0px 4px; }"
)

_NOTE_EDIT_QSS = (
    "QLineEdit { background: palette(base); color: palette(text);"
    " border: 1px solid rgba(128, 128, 128, 0.35); border-radius: 4px;"
    " padding: 3px 6px; font-size: 11px; }"
    f"QLineEdit:focus {{ border: 1px solid {FOCUS_RING}; }}"
    "QLineEdit:disabled { color: rgba(128, 128, 128, 0.55);"
    " background: rgba(128, 128, 128, 0.06); }"
)

# Row remove button: red-outline weight (destructive action sitting in a row,
# never a full red fill).
_ROW_REMOVE_BTN_QSS = (
    "QToolButton { background: transparent;"
    " border: 1px solid rgba(211, 47, 47, 0.45); border-radius: 4px;"
    f" color: {BRAND_RED}; font-size: 13px; font-weight: bold;"
    " padding: 0px; }"
    "QToolButton:hover { background: rgba(211, 47, 47, 0.18);"
    " border: 1px solid rgba(211, 47, 47, 0.75); }"
    "QToolButton:disabled { color: rgba(128, 128, 128, 0.5);"
    " border: 1px solid rgba(128, 128, 128, 0.25); }"
    f"QToolButton:focus {{ border: 2px solid {FOCUS_RING}; }}"
)

# Taxonomy error tint (persistent variant) for the panel's inline error line.
_PANEL_ERROR_QSS = (
    "QLabel { background: rgba(229, 72, 77, 0.14);"
    " border: 1px solid rgba(229, 72, 77, 0.45); border-radius: 4px;"
    " color: #ef5350; font-size: 11px; padding: 6px 8px; }"
)

_HINT_LABEL_QSS = (
    "QLabel { font-size: 11px; color: rgba(128, 128, 128, 0.95);"
    " background: transparent; border: none; }"
)

_IMPORT_CHIP_QSS = (
    "QPushButton {"
    " background: rgba(128, 128, 128, 0.06);"
    " border: 1px solid rgba(128, 128, 128, 0.20);"
    " border-radius: 8px;"
    " padding: 9px 8px;"
    " color: palette(text);"
    " font-size: 12px; font-weight: 600;"
    "}"
    "QPushButton:hover {"
    " background: rgba(128, 128, 128, 0.14);"
    " border: 1px solid rgba(128, 128, 128, 0.35);"
    "}"
    "QPushButton:disabled {"
    " background: rgba(128, 128, 128, 0.04);"
    " border: 1px solid rgba(128, 128, 128, 0.10);"
    " color: rgba(128, 128, 128, 0.55);"
    "}"
)

_EMPTY_STATE_QSS = (
    "QLabel { font-size: 11px; color: palette(text);"
    " background: rgba(128, 128, 128, 0.08);"
    " border: 1px solid rgba(128, 128, 128, 0.20); border-radius: 4px;"
    " padding: 8px; }"
)


class _ReferenceNoteRow(QWidget):
    """One reference in the panel list: thumbnail, name + source tag, the
    per-image note field, and a remove button."""

    remove_clicked = pyqtSignal(str)          # ref id
    note_edited = pyqtSignal(str, str)        # ref id, raw text
    preview_requested = pyqtSignal(str)       # image path

    def __init__(
        self,
        record: ReferenceImage,
        index: int,
        note: str,
        is_markup: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ref_id = record.id

        # Soft rounded card, the Mark up chip fill: rows carry their own
        # shape now that the titled group box around the list is gone.
        # WA_StyledBackground because a plain QWidget ignores stylesheet
        # backgrounds without it.
        self.setObjectName("referenceNoteRow")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#referenceNoteRow {"
            " background: rgba(128, 128, 128, 0.06);"
            " border: 1px solid rgba(128, 128, 128, 0.20);"
            " border-radius: 8px; }"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(8)

        # Thumbnail, reused from the strip. Read-only hides its hover-remove
        # overlay (this row has its own explicit remove button) while keeping
        # the click / Enter preview.
        self._thumb = _ThumbWidget(record, index, self)
        self._thumb.set_readonly(True)
        self._thumb.preview_requested.connect(self.preview_requested.emit)
        row.addWidget(self._thumb, 0, QtC.AlignTop)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)

        name = (record.source_filename or "").strip() or tr("Reference image")
        name_label = QLabel(name)
        name_label.setToolTip(name)
        name_label.setStyleSheet(
            "font-size: 11px; color: palette(text);"
            " background: transparent; border: none;"
        )
        # Ignored: long file names shrink (clip) instead of widening the dock.
        name_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        head.addWidget(name_label, 1)

        if is_markup:
            tag_text = tr("Mark up")
        elif record.source_kind == "layer":
            tag_text = tr("Layer")
        else:
            tag_text = tr("File")
        tag = QLabel(tag_text)
        tag.setStyleSheet(_SOURCE_TAG_QSS)
        head.addWidget(tag, 0)

        self._remove_btn = QToolButton(self)
        self._remove_btn.setText("×")
        self._remove_btn.setFixedSize(22, 22)
        self._remove_btn.setStyleSheet(_ROW_REMOVE_BTN_QSS)
        self._remove_btn.setCursor(QtC.PointingHandCursor)
        self._remove_btn.setToolTip(tr("Remove this reference image"))
        self._remove_btn.setAccessibleName(
            tr("Remove reference image {n}").format(n=index)
        )
        self._remove_btn.clicked.connect(
            lambda: self.remove_clicked.emit(self._ref_id)
        )
        head.addWidget(self._remove_btn, 0)
        right.addLayout(head)

        # The Mark up composite ships through the guidance channel, not as a
        # context image, so a note on it would never reach the model: no field.
        self._note_edit: QLineEdit | None = None
        if not is_markup:
            note_edit = QLineEdit(self)
            note_edit.setStyleSheet(_NOTE_EDIT_QSS)
            note_edit.setPlaceholderText(
                tr("What should the AI take from this image?")
            )
            note_edit.setMaxLength(reference_note_max_chars())
            note_edit.setAccessibleName(
                tr("Instructions for reference image {n}").format(n=index)
            )
            note_edit.blockSignals(True)
            note_edit.setText(note)
            note_edit.blockSignals(False)
            # textEdited fires on user typing only, never on setText, so the
            # store is written exactly when the user changes something.
            note_edit.textEdited.connect(
                lambda text: self.note_edited.emit(self._ref_id, text)
            )
            self._note_edit = note_edit
            right.addWidget(note_edit)
        else:
            guidance_hint = QLabel(
                tr("Your marks guide the edit. No note is needed here.")
            )
            guidance_hint.setStyleSheet(_HINT_LABEL_QSS)
            guidance_hint.setWordWrap(True)
            right.addWidget(guidance_hint)

        row.addLayout(right, 1)

    def set_readonly(self, readonly: bool) -> None:
        self._remove_btn.setEnabled(not readonly)
        if self._note_edit is not None:
            self._note_edit.setEnabled(not readonly)


class ReferencePanel(QWidget):
    """Full-dock Reference view: import entry points + per-image notes.

    Second view over the shared store; the compact strip stays the at-a-glance
    state above the prompt. The dock swaps this panel in via
    set_reference_state() (Mark up sibling pattern) and restores the main
    widget when done_clicked fires.
    """

    done_clicked = pyqtSignal()

    def __init__(
        self,
        store: ReferenceImageStore,
        strip: ReferenceImagesWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._strip = strip
        self._readonly = False
        self._rows: list[_ReferenceNoteRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(build_panel_header(tr("Reference images")))

        # Concise, closable, blue-tinted - the Mark up panel's exact hint
        # pattern. The long standing paragraph read as clutter (owner call
        # 2026-08-03); the import buttons' tooltips keep the detail.
        self._reference_hint = DismissibleHint(
            HINT_REFERENCE,
            "",
            tr("Each image is cropped to your zone and stays hidden from "
               "the map. Add a note to tell the AI what to take from it."),
            link_text=tr("See an example"),
        )
        self._reference_hint.link_activated.connect(
            lambda: open_guide("panel_reference")
        )
        layout.addWidget(self._reference_hint)

        # Import entry points - two chip buttons side by side, the Mark up
        # tool-chip look. Glyphs outside tr().
        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.setSpacing(6)

        ink = self.palette().color(QPalette.ColorRole.WindowText)
        self._file_btn = QPushButton(tr("From your computer"))
        self._file_btn.setIcon(_make_import_icon("file", ink))
        self._file_btn.setIconSize(QSize(_IMPORT_ICON_PX, _IMPORT_ICON_PX))
        self._file_btn.setStyleSheet(_IMPORT_CHIP_QSS)
        self._file_btn.setCursor(QtC.PointingHandCursor)
        self._file_btn.setToolTip(
            tr("Pick images or data files. Data files (GeoTIFF, shapefile, "
               "GeoJSON...) are rendered at your zone.")
        )
        self._file_btn.clicked.connect(self._on_file_import_clicked)
        add_row.addWidget(self._file_btn, 1)

        self._layer_btn = QPushButton(tr("From a QGIS layer"))
        self._layer_btn.setIcon(_make_import_icon("layer", ink))
        self._layer_btn.setIconSize(QSize(_IMPORT_ICON_PX, _IMPORT_ICON_PX))
        self._layer_btn.setStyleSheet(_IMPORT_CHIP_QSS)
        self._layer_btn.setCursor(QtC.PointingHandCursor)
        self._layer_btn.setToolTip(
            tr("Snapshot one of this project's layers at your zone. The "
               "layer itself is not changed and stays where it is.")
        )
        self._layer_btn.clicked.connect(self._show_layer_menu)
        add_row.addWidget(self._layer_btn, 1)
        layout.addLayout(add_row)

        # Inline error line (the dock's status box lives under the hidden main
        # widget while this panel is open, so errors surface here too).
        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(_PANEL_ERROR_QSS)
        self._error_label.setVisible(False)
        layout.addWidget(self._error_label)
        strip.error_occurred.connect(self._on_strip_error)
        strip.error_cleared.connect(self._on_strip_error_cleared)

        # Current references, one soft card per image - no titled group box,
        # the rows carry their own shape (owner call 2026-08-03: minimal).
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 2, 0, 0)
        self._list_layout.setSpacing(6)

        self._empty_label = QLabel(
            tr("No references yet. Add one to guide the AI.")
        )
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet(_EMPTY_STATE_QSS)
        self._list_layout.addWidget(self._empty_label)
        layout.addWidget(self._list_host)

        # Rebuild whenever the shared store changes through the strip (adds
        # from here, drops on the prompt box, paste, the result container...).
        strip.images_changed.connect(self._rebuild_rows)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 6, 0, 0)
        action_row.setSpacing(6)
        action_row.addStretch()
        self._done_btn = QPushButton(tr("Done"))
        self._done_btn.setToolTip(
            tr("Keep these references to guide the edit, and close the panel")
        )
        self._done_btn.setStyleSheet(_BTN_GHOST)
        self._done_btn.setCursor(QtC.PointingHandCursor)
        self._done_btn.setMinimumHeight(34)
        self._done_btn.setMinimumWidth(80)
        self._done_btn.clicked.connect(self.done_clicked.emit)
        action_row.addWidget(self._done_btn)
        layout.addLayout(action_row)
        layout.addStretch()

        self._rebuild_rows()

    # -- public API ------------------------------------------------------

    def activate(self) -> None:
        """Called by the dock each time the panel becomes visible."""
        self._on_strip_error_cleared()
        self._rebuild_rows()

    def set_readonly(self, readonly: bool) -> None:
        """Lock imports, notes and removal (used during generation)."""
        self._readonly = readonly
        self._file_btn.setEnabled(not readonly)
        self._layer_btn.setEnabled(not readonly)
        for row in self._rows:
            row.set_readonly(readonly)

    # -- imports ---------------------------------------------------------

    def _on_file_import_clicked(self) -> None:
        # The strip owns the picker: same formats, same free-tier / cap gate,
        # same error signals, and it refreshes both views on success.
        self._strip.open_file_picker()

    def _show_layer_menu(self) -> None:
        """Project-layer picker, reusing the Reference menu's layer listing
        (lazy import: the dock package may be mid-initialization when this
        panel module is first imported)."""
        from ..dock.prompt_container import _PromptContainer

        menu = QMenu(self)
        menu.setStyleSheet(_PromptContainer._ATTACH_MENU_STYLE)
        layers = _PromptContainer._project_layer_choices()
        if not layers:
            empty = menu.addAction(tr("No layers in the project"))
            empty.setEnabled(False)
        for layer in layers:
            # Doubled "&": Qt would otherwise eat it as a mnemonic marker.
            action = menu.addAction(
                _PromptContainer._layer_icon(layer),
                layer.name().replace("&", "&&"),
            )
            action.triggered.connect(
                lambda _checked=False, lyr=layer: self._strip.add_layers([lyr])
            )
        menu.setMinimumWidth(max(menu.sizeHint().width(), self._layer_btn.width()))
        menu.exec(self._layer_btn.mapToGlobal(QPoint(0, self._layer_btn.height())))
        menu.deleteLater()

    # -- list ------------------------------------------------------------

    def _rebuild_rows(self) -> None:
        for row in self._rows:
            self._list_layout.removeWidget(row)
            row.deleteLater()
        self._rows = []

        records = self._store.list()
        self._empty_label.setVisible(not records)
        for idx, record in enumerate(records, start=1):
            row = _ReferenceNoteRow(
                record,
                idx,
                self._store.get_note(record.id),
                self._store.is_markup(record.id),
                self._list_host,
            )
            row.set_readonly(self._readonly)
            row.remove_clicked.connect(self._strip.remove_reference)
            row.note_edited.connect(self._store.set_note)
            row.preview_requested.connect(self._open_preview)
            self._rows.append(row)
            self._list_layout.addWidget(row)

    def _open_preview(self, image_path: str) -> None:
        record = next(
            (r for r in self._store.list() if r.path == image_path), None
        )
        is_markup = record is not None and self._store.is_markup(record.id)
        open_reference_preview(
            self, image_path, reference_preview_title(record, is_markup)
        )

    # -- error line ------------------------------------------------------

    def _on_strip_error(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.setVisible(True)

    def _on_strip_error_cleared(self) -> None:
        self._error_label.setVisible(False)

    # -- keys ------------------------------------------------------------

    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
        # Esc closes the panel, like Mark up's Done. Handled here (not a
        # window-context QShortcut) so it cannot collide with the hidden
        # Mark up panel's own Esc shortcut; Esc bubbles up from any focused
        # child (QLineEdit ignores it), so this catches it panel-wide.
        if event.key() == Qt.Key.Key_Escape:
            self.done_clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)
