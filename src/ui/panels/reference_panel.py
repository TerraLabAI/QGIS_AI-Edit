


































from __future__ import annotations

from qgis.PyQt.QtCore import QPoint, QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.reference_image_store import ReferenceImage, ReferenceImageStore
from ..dock import design_tokens as T
from ..dock.design_tokens import qcolor, repolish_widget
from ..dock.mime import _file_paths_from_mime, _layers_from_mime, _mime_has_droppable
from ..icons import icon_for, pixmap_for
from ..onboarding_hint import GUIDE_ANCHOR_REFERENCE, open_guide
from ..panel_helpers import build_panel_header, make_notice_card, panel_section_label
from ..reference_images_widget import (
    ReferenceImagesWidget,
    _ThumbWidget,
    open_reference_preview,
    reference_preview_title,
)



_SOURCE_GLYPHS = {"file": "file", "layer": "layers", "map": "camera"}
_SOURCE_GLYPH_PX = 16
_REMOVE_GLYPH_PX = 14
_TIP_GLYPH_PX = 14


_REFERENCE_HUE = "sky"

_HINT_LABEL_QSS = f"QLabel {{ {T.HINT_QSS} }}"
_NAME_LABEL_QSS = f"QLabel {{ {T.BODY_QSS} font-weight: 500; }}"




_SOURCE_TAG_QSS = (
    f"QLabel {{ background: {T.FIELD}; border: none; border-radius: {T.RADIUS_CHIP}px;"
    f" color: {T.INK_2}; font-size: {T.FONT_MICRO}px; font-weight: 600; padding: 1px 6px; }}"
)



_REMOVE_BTN_QSS = (
    "QToolButton { background: transparent; border: 1px solid transparent; padding: 0px;"
    f" border-radius: {T.RADIUS_PILL_SMALL}px; }}"
    f"QToolButton:hover {{ background: {T.RED_TINT}; }}"
    f"QToolButton:pressed {{ background: {T.RED_TINT}; border-color: {T.RED}; }}"
    f"QToolButton:focus {{ border-color: {T.ACCENT_BORDER}; }}"
)



_COUNT_QSS = f"QLabel {{ {T.HINT_QSS} padding: 4px 2px 0px 2px; }}"
_COUNT_FULL_QSS = _COUNT_QSS + f"QLabel {{ color: {T.ORANGE_TEXT}; }}"

_CARD_QSS = (
    f"QWidget#referenceCard {{ background: {T.SURFACE}; border: 1px solid {T.LINE};"
    f" border-radius: {T.RADIUS_CARD}px; }}"
)




_ADD_BLOCK_QSS = (
    f"QFrame#referenceAdd {{ background: {T.INSET}; border: 1px dashed {T.LINE_INPUT};"
    f" border-radius: {T.RADIUS_BOX}px; }}"
    f'QFrame#referenceAdd[receiving="true"] {{ background: {T.ACCENT_TINT};'
    f" border: 1px solid {T.ACCENT_BORDER}; }}"
    f'QFrame#referenceAdd[locked="true"] {{ border: 1px dashed {T.LINE}; }}'
    "QFrame#referenceAdd QLabel { background: transparent; border: none; }"
)



_SOURCE_BTN_QSS = T.BTN_GHOST_QSS + T.picked_qss("QPushButton")


def quiet_glyph_icon(widget: QWidget, glyph: str, size: int):


    return icon_for(widget, glyph, size, qcolor(T.INK_2), disabled_color=qcolor(T.INK_3))


def reference_source_label(kind: str) -> str:



    if kind == "layer":
        return get_export_copy("widgets.reference_panel.source_project_layer_label",
                               tr("Project layer"))
    if kind == "map":
        return get_export_copy("widgets.reference_panel.source_map_view_label",
                               tr("Map view"))
    return get_export_copy("widgets.reference_panel.source_computer_label",
                           tr("Your computer"))


class _SourceButton(QPushButton):




    def __init__(self, shape: str, label: str, tip: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self._glyph = _SOURCE_GLYPHS.get(shape, "file")
        self.setStyleSheet(_SOURCE_BTN_QSS)
        self.setCursor(QtC.PointingHandCursor)
        self.setIconSize(QSize(_SOURCE_GLYPH_PX, _SOURCE_GLYPH_PX))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)



        self.setMinimumWidth(1)
        self.setToolTip(tip)
        self.setAccessibleName(label)
        self.setAccessibleDescription(tip)
        self._sync_icon()
        self.toggled.connect(lambda _checked: self._sync_icon())

    def _sync_icon(self) -> None:


        if self.isCheckable() and self.isChecked():
            self.setIcon(icon_for(self, "check", _SOURCE_GLYPH_PX,
                                  qcolor(T.category_ink(T.PICKED_HUE))))
        else:
            self.setIcon(icon_for(self, self._glyph, _SOURCE_GLYPH_PX,
                                  qcolor(T.category_ink(_REFERENCE_HUE)),
                                  disabled_color=qcolor(T.INK_3)))

    def setCheckable(self, checkable: bool) -> None:  # noqa: N802
        super().setCheckable(checkable)
        self._sync_icon()


class _AddBlock(QFrame):






    def __init__(self, buttons: list[QPushButton], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("referenceAdd")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setStyleSheet(_ADD_BLOCK_QSS)
        self.setProperty("receiving", False)
        self.setProperty("locked", False)
        self._buttons = buttons
        self._stacked: bool | None = None

        col = QVBoxLayout(self)
        col.setContentsMargins(10, 10, 10, 10)
        col.setSpacing(T.SPACE_OUTER)
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(T.SPACE_CARD)
        self._grid.setVerticalSpacing(T.SPACE_CARD)
        col.addLayout(self._grid)
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        self.hint.setAlignment(QtC.AlignCenter)
        self.hint.setStyleSheet(_HINT_LABEL_QSS)
        col.addWidget(self.hint)
        self._place(stacked=False)

    def _row_width_needed(self) -> int:





        margins = self.contentsMargins()
        layout_margins = self.layout().contentsMargins()
        widths = max(b.sizeHint().width() for b in self._buttons) * len(self._buttons)
        gaps = self._grid.horizontalSpacing() * (len(self._buttons) - 1)
        return (widths + gaps + margins.left() + margins.right()
                + layout_margins.left() + layout_margins.right())

    def _place(self, stacked: bool) -> None:
        if stacked == self._stacked:
            return
        self._stacked = stacked
        for button in self._buttons:
            self._grid.removeWidget(button)
        for index, button in enumerate(self._buttons):
            if stacked:
                self._grid.addWidget(button, index, 0)
            else:
                self._grid.addWidget(button, 0, index)
        for column in range(len(self._buttons)):
            self._grid.setColumnStretch(column, 0 if stacked else 1)
        self._grid.setColumnStretch(0, 1)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)


        self._place(stacked=self.width() < self._row_width_needed())

    def set_hint(self, text: str) -> None:
        self.hint.setText(text)

    def set_receiving(self, receiving: bool) -> None:

        if bool(self.property("receiving")) == bool(receiving):
            return
        self.setProperty("receiving", bool(receiving))
        repolish_widget(self)

    def set_locked(self, locked: bool) -> None:
        if bool(self.property("locked")) == bool(locked):
            return
        self.setProperty("locked", bool(locked))
        repolish_widget(self)


class _ElidedNameLabel(QLabel):





    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setStyleSheet(_NAME_LABEL_QSS)



        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, max(0, self.width())
        )
        self.setText(shown)


        self.setToolTip(self._full_text if shown != self._full_text else "")


def reference_display_name(record: ReferenceImage) -> str:
    return (record.source_filename or "").strip() or get_export_copy(
        "widgets.reference_panel.reference_fallback_name", tr("Reference")
    )


class _ReferenceCard(QWidget):




    remove_clicked = pyqtSignal(str)
    note_edited = pyqtSignal(str, str)
    preview_requested = pyqtSignal(str)

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



        self.setObjectName("referenceCard")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setStyleSheet(_CARD_QSS)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(T.SPACE_OUTER + 2)




        self._thumb = _ThumbWidget(record, index, self,
                                   remove_overlay=False, whole_badge=False)
        self._thumb.preview_requested.connect(self.preview_requested.emit)
        self._thumb.remove_clicked.connect(self.remove_clicked.emit)
        row.addWidget(self._thumb, 0, QtC.AlignVCenter)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(T.SPACE_TIGHT)
        right.addStretch(1)

        name = reference_display_name(record)
        right.addWidget(_ElidedNameLabel(name, self))






        tag_text, note_text = _card_source_words(record, is_markup)
        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(T.SPACE_CARD)
        tag = QLabel(tag_text)
        tag.setStyleSheet(_SOURCE_TAG_QSS)
        meta.addWidget(tag, 0, QtC.AlignTop)
        meta.addStretch(1)
        right.addLayout(meta)


        if note_text:
            note_label = QLabel(note_text)
            note_label.setStyleSheet(_HINT_LABEL_QSS)
            note_label.setWordWrap(True)
            right.addWidget(note_label)
        right.addStretch(1)
        row.addLayout(right, 1)

        self._remove_btn = QToolButton(self)
        self._remove_btn.setIcon(quiet_glyph_icon(self, "close", _REMOVE_GLYPH_PX))
        self._remove_btn.setIconSize(QSize(_REMOVE_GLYPH_PX, _REMOVE_GLYPH_PX))
        self._remove_btn.setFixedSize(T.BTN_SMALL_PX, T.BTN_SMALL_PX)
        self._remove_btn.setStyleSheet(_REMOVE_BTN_QSS)
        self._remove_btn.setCursor(QtC.PointingHandCursor)
        self._remove_btn.setToolTip(
            get_export_copy("widgets.reference_panel.remove_tooltip_v2", tr("Remove"))
        )
        self._remove_btn.setAccessibleName(
            tr("Remove {name}").format(name=name)
        )
        self._remove_btn.clicked.connect(
            lambda: self.remove_clicked.emit(self._ref_id)
        )
        row.addWidget(self._remove_btn, 0, QtC.AlignVCenter)

    def set_readonly(self, readonly: bool) -> None:
        self._remove_btn.setEnabled(not readonly)
        self._thumb.set_readonly(readonly)

    def focus_thumb(self) -> None:
        self._thumb.setFocus(Qt.FocusReason.TabFocusReason)


def _card_source_words(record: ReferenceImage, is_markup: bool) -> tuple[str, str]:







    if is_markup:
        return (
            get_export_copy("widgets.reference_panel.tag_draw", tr("Draw")),
            get_export_copy("widgets.reference_panel.markup_guidance_hint",
                            tr("Your marks guide the edit.")),
        )
    kind = record.source_kind if record.source_kind in ("file", "layer", "map") else "file"
    if getattr(record, "whole_layer", False):
        return (
            reference_source_label(kind),
            get_export_copy("widgets.reference_panel.card_note_outside_zone",
                            tr("Outside your zone, sent whole")),
        )
    return (reference_source_label(kind), "")


class ReferencePanel(QWidget):









    done_clicked = pyqtSignal()

    map_capture_requested = pyqtSignal()

    upgrade_requested = pyqtSignal()

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
        self._capture_armed = False
        self._receiving = False
        self._rows: list[_ReferenceCard] = []



        self.setAcceptDrops(True)



        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_STAGE)






        header = build_panel_header(
            get_export_copy("widgets.reference_panel.header_title_plural", tr("References")),
            subtitle=get_export_copy(
                "widgets.reference_panel.header_subtitle_purpose",
                tr("Show the AI a style, a legend or an object to match."),
            ),
            show_close=False,
        )
        layout.addWidget(header)




        self._file_btn = _SourceButton(
            "file",
            reference_source_label("file"),
            get_export_copy(
                "widgets.reference_panel.file_chip_tooltip",
                tr("Pick images or data files. Data files (GeoTIFF, shapefile, "
                   "GeoJSON...) are rendered at your zone."),
            ),
            self,
        )
        self._file_btn.clicked.connect(self._on_file_import_clicked)

        self._layer_btn = _SourceButton(
            "layer",
            reference_source_label("layer"),
            get_export_copy(
                "widgets.reference_panel.layer_chip_tooltip",
                tr("Snapshot one of this project's layers at your zone. The "
                   "layer itself is not changed and stays where it is."),
            ),
            self,
        )
        self._layer_btn.clicked.connect(self._show_layer_menu)

        self._map_btn = _SourceButton(
            "map",
            reference_source_label("map"),
            get_export_copy(
                "widgets.reference_panel.map_chip_tooltip",
                tr("Drag a rectangle anywhere on the map to capture what you see "
                   "as a reference. Esc cancels."),
            ),
            self,
        )
        self._map_btn.setCheckable(True)
        self._map_btn.clicked.connect(self._on_map_capture_clicked)

        self._add_block = _AddBlock([self._file_btn, self._layer_btn, self._map_btn], self)
        layout.addWidget(self._add_block)





        self._error_card = make_notice_card("", "error")
        self._error_label = self._error_card.text_label
        self._error_label.setTextFormat(Qt.TextFormat.PlainText)
        self._error_card.setVisible(False)
        layout.addWidget(self._error_card)
        strip.error_occurred.connect(self._on_strip_error)




        self._upsell_card = make_notice_card("", "warning")
        self._upsell_label = self._upsell_card.text_label
        self._upsell_label.setTextFormat(Qt.TextFormat.PlainText)
        self._upgrade_btn = QPushButton(
            get_export_copy("upsell.upgrade_button", tr("Upgrade to Pro")), self._upsell_card
        )
        self._upgrade_btn.setStyleSheet(T.BTN_LINK_QSS)
        self._upgrade_btn.setCursor(QtC.PointingHandCursor)
        self._upgrade_btn.clicked.connect(self.upgrade_requested.emit)


        upsell_row = self._upsell_card.layout()
        upsell_row.removeWidget(self._upsell_label)
        upsell_text = QVBoxLayout()
        upsell_text.setContentsMargins(0, 0, 0, 0)
        upsell_text.setSpacing(T.SPACE_TIGHT)
        upsell_text.addWidget(self._upsell_label)
        upsell_text.addWidget(self._upgrade_btn, 0, QtC.AlignLeft)
        upsell_row.addLayout(upsell_text, 1)
        self._upsell_card.setVisible(False)
        layout.addWidget(self._upsell_card)
        strip.upsell_requested.connect(self._on_upsell)

        opener = getattr(parent, "_open_pro_from", None)
        if callable(opener):
            self.upgrade_requested.connect(lambda: opener("reference_limit"))


        list_head = QHBoxLayout()
        list_head.setContentsMargins(0, 0, 0, 0)
        list_head.setSpacing(T.SPACE_CARD)
        self._list_title = panel_section_label(
            get_export_copy("widgets.reference_panel.list_title", tr("Added"))
        )
        list_head.addWidget(self._list_title, 1)
        self._count_label = QLabel()
        self._count_label.setStyleSheet(_COUNT_QSS)
        list_head.addWidget(self._count_label, 0, QtC.AlignBottom)
        layout.addLayout(list_head)

        self._list_host = QWidget()

        self._list_host.setObjectName("referenceStage")
        self._list_host.setStyleSheet(
            "QWidget#referenceStage { background: transparent; }"
        )
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(T.SPACE_CARD)

        self._empty_label = QLabel(
            get_export_copy(
                "widgets.reference_panel.list_empty",
                tr("Nothing yet. The AI works from your zone and prompt only."),
            )
        )
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet(_HINT_LABEL_QSS + "QLabel { padding: 0px 2px; }")
        self._list_layout.addWidget(self._empty_label)

        self._cards_start = self._list_layout.count()

        self._prompt_tip = self._build_prompt_tip()
        self._list_layout.addWidget(self._prompt_tip)




        layout.addWidget(self._list_host)



        strip.images_changed.connect(self._on_images_changed)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(T.SPACE_CARD)
        action_row.addStretch()
        self._done_btn = QPushButton(get_export_copy("widgets.reference_panel.done_button", tr("Done")))

        self._done_btn.setStyleSheet(T.BTN_PRIMARY_QSS)
        self._done_btn.setCursor(QtC.PointingHandCursor)
        self._done_btn.setMinimumWidth(96)
        self._done_btn.clicked.connect(self.done_clicked.emit)
        action_row.addWidget(self._done_btn)
        layout.addLayout(action_row)

        layout.addStretch(1)

        self._rebuild_rows()

    def _build_prompt_tip(self) -> QWidget:



        tip = QWidget()
        tip.setObjectName("referencePromptTip")
        row = QHBoxLayout(tip)
        row.setContentsMargins(2, 2, 2, 0)
        row.setSpacing(T.SPACE_CARD)
        glyph = QLabel(tip)
        glyph.setPixmap(pixmap_for(glyph, "spark", _TIP_GLYPH_PX,
                                   qcolor(T.category_ink(_REFERENCE_HUE))))
        glyph.setFixedWidth(_TIP_GLYPH_PX)
        glyph.setStyleSheet("background: transparent;")
        row.addWidget(glyph, 0, QtC.AlignTop)
        text = get_export_copy(
            "widgets.reference_panel.prompt_tip_numbered",
            tr('In your prompt, say what to take from each: "roof colours from reference 1".'),
        )
        link = get_export_copy("widgets.reference_panel.reference_hint_link", tr("See an example"))
        label = QLabel(tip)
        label.setWordWrap(True)
        label.setTextFormat(QtC.RichText)
        label.setText(
            f"{_html_escape(text)} <a href='guide' style='color: {T.LINK_INK};"
            f" text-decoration: none;'>{_html_escape(link)}</a>"
        )
        label.setStyleSheet(_HINT_LABEL_QSS)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                      | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        label.linkActivated.connect(
            lambda _href: open_guide("panel_reference", anchor=GUIDE_ANCHOR_REFERENCE)
        )
        row.addWidget(label, 1)
        self._prompt_tip_label = label
        return tip



    def activate(self) -> None:

        self._clear_messages()
        self._rebuild_rows()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_readonly(self, readonly: bool) -> None:

        self._readonly = readonly
        for card in self._rows:
            card.set_readonly(readonly)
        if readonly:
            self._set_receiving(False)
        self._refresh_add_state()

    def set_capture_armed(self, armed: bool) -> None:

        self._capture_armed = bool(armed)
        self._map_btn.blockSignals(True)
        self._map_btn.setChecked(self._capture_armed)
        self._map_btn.blockSignals(False)
        self._map_btn._sync_icon()
        self._refresh_add_state()



    def _on_map_capture_clicked(self) -> None:


        self._map_btn.blockSignals(True)
        self._map_btn.setChecked(self._capture_armed)
        self._map_btn.blockSignals(False)
        self._map_btn._sync_icon()
        if self._readonly:
            return
        self._clear_messages()
        self.map_capture_requested.emit()

    def _on_file_import_clicked(self) -> None:


        self._clear_messages()
        self._strip.open_file_picker()

    def _show_layer_menu(self) -> None:



        from ..dock.prompt_container import _PromptContainer

        self._clear_messages()
        menu = QMenu(self)
        menu.setStyleSheet(T.MENU_QSS)
        layers = _PromptContainer._project_layer_choices()
        if not layers:
            empty = menu.addAction(
                get_export_copy("widgets.reference_panel.no_layers_in_project", tr("No layers in the project"))
            )
            empty.setEnabled(False)
        for layer in layers:

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



    def _limit(self) -> int:
        try:
            return int(self._strip.add_limit())
        except (AttributeError, TypeError, ValueError):
            return 0

    def _at_hard_cap(self) -> bool:
        try:
            return bool(self._strip.at_capacity())
        except AttributeError:
            return False

    def _refresh_add_state(self) -> None:


        at_cap = self._at_hard_cap()



        at_limit = self._at_add_limit()
        enabled = not self._readonly and not at_cap
        for button in (self._file_btn, self._layer_btn):
            button.setEnabled(enabled)

        self._map_btn.setEnabled(not self._readonly and (not at_cap or self._capture_armed))
        self._add_block.set_locked(not enabled)
        if self._readonly:
            text = get_export_copy("widgets.reference_panel.add_locked",
                                   tr("Locked while the AI generates"))
        elif self._capture_armed:
            text = get_export_copy("widgets.reference_panel.add_capture_armed",
                                   tr("Drag a box on the map. Esc cancels."))
        elif self._receiving:
            text = get_export_copy("widgets.reference_panel.add_release_to_add",
                                   tr("Release to add"))
        elif at_cap:
            text = get_export_copy("widgets.reference_panel.add_limit_reached",
                                   tr("Limit reached. Remove one to add another."))
        elif at_limit:
            text = get_export_copy("widgets.reference_panel.add_free_limit_reached",
                                   tr("Free plan limit reached."))
        else:
            text = get_export_copy("widgets.reference_panel.add_drop_hint",
                                   tr("Or drop images and layers here"))
        self._add_block.set_hint(text)


        self._count_label.setStyleSheet(_COUNT_FULL_QSS if at_limit else _COUNT_QSS)

    def _on_images_changed(self) -> None:


        self._clear_messages()
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        for card in self._rows:
            self._list_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self._rows = []

        records = self._store.list()
        self._empty_label.setVisible(not records)


        self._prompt_tip.setVisible(
            any(not self._store.is_markup(record.id) for record in records)
        )
        limit = self._limit()


        self._count_label.setText(
            tr("{n} of {limit}").format(n=len(records), limit=limit)
            if limit and records else ""
        )
        self._count_label.setVisible(bool(limit and records))
        number = 0
        for position, record in enumerate(records):
            is_markup = self._store.is_markup(record.id)
            if not is_markup:
                number += 1
            card = _ReferenceCard(
                record,
                0 if is_markup else number,
                self._store.get_note(record.id),
                is_markup,
                self._list_host,
            )
            card.set_readonly(self._readonly)
            card.remove_clicked.connect(self._on_remove_clicked)
            card.note_edited.connect(self._store.set_note)
            card.preview_requested.connect(self._open_preview)
            self._rows.append(card)
            self._list_layout.insertWidget(self._cards_start + position, card)
        self._refresh_add_state()

        if records:
            done_tip = get_export_copy(
                "widgets.reference_panel.done_tooltip",
                tr("Keep these references to guide the edit, and close the panel"),
            )
        else:
            done_tip = get_export_copy("widgets.reference_panel.done_tooltip_empty",
                                       tr("Close the panel"))
        self._done_btn.setToolTip(done_tip)

    def _on_remove_clicked(self, ref_id: str) -> None:


        if self._readonly:
            return
        ids = [card._ref_id for card in self._rows]
        position = ids.index(ref_id) if ref_id in ids else -1
        focus = self.focusWidget()
        had_focus = focus is not None and self.isAncestorOf(focus)
        self._strip.remove_reference(ref_id)
        if not had_focus:
            return
        if self._rows:
            self._rows[max(0, min(position, len(self._rows) - 1))].focus_thumb()
        else:
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _open_preview(self, image_path: str) -> None:
        record = next(
            (r for r in self._store.list() if r.path == image_path), None
        )
        is_markup = record is not None and self._store.is_markup(record.id)
        open_reference_preview(
            self, image_path, reference_preview_title(record, is_markup)
        )



    def _on_strip_error(self, message: str) -> None:
        self._upsell_card.setVisible(False)
        self._error_label.setText(message)
        self._error_card.setVisible(True)

    def _on_upsell(self) -> None:
        cap = self._limit() or 1
        if cap == 1:
            text = tr("The free plan includes {n} reference. Remove it to add another.")
        else:
            text = tr("The free plan includes {n} references. Remove one to add another.")
        self._error_card.setVisible(False)
        self._upsell_label.setText(text.format(n=cap))
        self._upsell_card.setVisible(True)

    def _clear_messages(self) -> None:
        self._error_card.setVisible(False)
        self._upsell_card.setVisible(False)



    def _at_add_limit(self) -> bool:
        limit = self._limit()
        return self._at_hard_cap() or (limit > 0 and self._store.count() >= limit)

    def _set_receiving(self, receiving: bool) -> None:



        receiving = bool(receiving) and not self._readonly and not self._at_add_limit()
        self._add_block.set_receiving(receiving)
        if receiving != self._receiving:
            self._receiving = receiving
            self._refresh_add_state()

    def dragEnterEvent(self, event):  # noqa: N802
        if self._readonly or not _mime_has_droppable(event.mimeData()):
            event.ignore()
            return


        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._set_receiving(True)

    def dragMoveEvent(self, event):  # noqa: N802
        if self._readonly or not _mime_has_droppable(event.mimeData()):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event):  # noqa: N802
        self._set_receiving(False)
        event.accept()

    def dropEvent(self, event):  # noqa: N802
        self._set_receiving(False)
        if self._readonly:
            event.ignore()
            return
        mime = event.mimeData()
        paths = _file_paths_from_mime(mime)
        layers = _layers_from_mime(mime)
        if paths or layers:
            self._clear_messages()
        if paths:
            self._strip.add_paths(paths)
        if layers:
            self._strip.add_layers(layers)
        if paths or layers:

            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        event.ignore()


def _html_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
