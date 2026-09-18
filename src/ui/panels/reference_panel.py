"""References panel: the full-dock view for managing reference images.

The feature is called "References" everywhere the user reads it (terminology
pass, 2026-09-17): the panel title, the buttons that add one, and the tag
every added image carries. ``reference_source_label`` is the one place those
names are written, so a button and the cards it produces can never drift
apart.

Sibling-swap panel (same pattern as the Draw panel): swaps with the dock's
main widget. It is a second view over the SAME ReferenceImageStore as the
compact thumbnail strip above the prompt; every add and remove funnels
through the shared ReferenceImagesWidget so gating (free tier, hard cap,
server kill switch wired by the dock) and the strip refresh stay in one
place.

Shape (2026-09-18, the ChatGPT / AI Agent composer attachments), top to
bottom:

1. the title and ONE line that says what a reference does;
2. one add block: the three sources as buttons inside a drop area, so there
   is a single way in, and its one muted line doubles as the state line
   (drop here, drag on the map, locked, limit reached);
3. what is attached: "Added", the count against the limit, one card per
   image (numbered thumbnail, name, source tag, remove), then the one line
   that says how to use them: name them in the prompt;
4. Done, the panel's one way out.

What the AI should take from an image is said in the prompt, so the cards
carry no note field (owner call 2026-09-02); the store's notes and the
context_image_notes wire key stay for the agent API.

File imports never touch the project, layer picks are rendered to an
offscreen image by the strip's existing flow, and the map capture is owned
by the plugin's canvas side. Nothing here changes any layer's visibility.
"""
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

# The three sources take the shared glyphs: a file, the layer stack, a
# camera for a capture of the map.
_SOURCE_GLYPHS = {"file": "file", "layer": "layers", "map": "camera"}
_SOURCE_GLYPH_PX = 16
_REMOVE_GLYPH_PX = 14
_TIP_GLYPH_PX = 14
# The one hue this screen carries besides the green primary: the add block's
# glyphs and the "how to use them" line.
_REFERENCE_HUE = "sky"

_HINT_LABEL_QSS = f"QLabel {{ {T.HINT_QSS} }}"
_NAME_LABEL_QSS = f"QLabel {{ {T.BODY_QSS} font-weight: 500; }}"

# Small source tag under the reference name. It repeats, word for word, the
# button that added the image, so the panel names one thing one way
# (terminology pass, 2026-09-17). Neutral: it is metadata, not a message.
_SOURCE_TAG_QSS = (
    f"QLabel {{ background: {T.FIELD}; border: none; border-radius: {T.RADIUS_CHIP}px;"
    f" color: {T.INK_2}; font-size: {T.FONT_MICRO}px; font-weight: 600; padding: 1px 6px; }}"
)

# Card remove: a quiet X that takes the red wash under the pointer, the
# composer attachment's remove, never a fill.
_REMOVE_BTN_QSS = (
    "QToolButton { background: transparent; border: 1px solid transparent; padding: 0px;"
    f" border-radius: {T.RADIUS_PILL_SMALL}px; }}"
    f"QToolButton:hover {{ background: {T.RED_TINT}; }}"
    f"QToolButton:pressed {{ background: {T.RED_TINT}; border-color: {T.RED}; }}"
    f"QToolButton:focus {{ border-color: {T.ACCENT_BORDER}; }}"
)

# The "n of limit" count beside "Added". The section label's own top
# padding, so both sit on one baseline.
_COUNT_QSS = f"QLabel {{ {T.HINT_QSS} padding: 4px 2px 0px 2px; }}"
_COUNT_FULL_QSS = _COUNT_QSS + f"QLabel {{ color: {T.ORANGE_TEXT}; }}"

_CARD_QSS = (
    f"QWidget#referenceCard {{ background: {T.SURFACE}; border: 1px solid {T.LINE};"
    f" border-radius: {T.RADIUS_CARD}px; }}"
)

# The add block is the drop target, so it has a resting look (a dashed
# hairline, the drop-zone convention) and a receiving one (the interaction
# blue). Keyed on a dynamic property, repolished on drag enter / leave.
_ADD_BLOCK_QSS = (
    f"QFrame#referenceAdd {{ background: {T.INSET}; border: 1px dashed {T.LINE_INPUT};"
    f" border-radius: {T.RADIUS_BOX}px; }}"
    f'QFrame#referenceAdd[receiving="true"] {{ background: {T.ACCENT_TINT};'
    f" border: 1px solid {T.ACCENT_BORDER}; }}"
    f'QFrame#referenceAdd[locked="true"] {{ border: 1px dashed {T.LINE}; }}'
    "QFrame#referenceAdd QLabel { background: transparent; border: none; }"
)

# A source button: the ghost pill, with the picked look (green tint, green
# border) while the map capture is armed.
_SOURCE_BTN_QSS = T.BTN_GHOST_QSS + T.picked_qss("QPushButton")


def quiet_glyph_icon(widget: QWidget, glyph: str, size: int):
    """The glyph a card's own control takes: the secondary ink, greyed when
    the control is disabled."""
    return icon_for(widget, glyph, size, qcolor(T.INK_2), disabled_color=qcolor(T.INK_3))


def reference_source_label(kind: str) -> str:
    """The one name a source has: on the button that adds a reference and on
    the tag of every reference that button produced. One word list, so the
    panel can never call the same thing two names ("Computer" then "File")."""
    if kind == "layer":
        return get_export_copy("widgets.reference_panel.source_project_layer_label",
                               tr("Project layer"))
    if kind == "map":
        return get_export_copy("widgets.reference_panel.source_map_view_label",
                               tr("Map view"))
    return get_export_copy("widgets.reference_panel.source_computer_label",
                           tr("Your computer"))


class _SourceButton(QPushButton):
    """One way to add a reference: a glyph and the source's name on a ghost
    pill. What the source gives the AI is its tooltip and accessible
    description; the block's own line says the rest."""

    def __init__(self, shape: str, label: str, tip: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self._glyph = _SOURCE_GLYPHS.get(shape, "file")
        self.setStyleSheet(_SOURCE_BTN_QSS)
        self.setCursor(QtC.PointingHandCursor)
        self.setIconSize(QSize(_SOURCE_GLYPH_PX, _SOURCE_GLYPH_PX))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # An explicit minimum, so three buttons side by side never hold the
        # dock wider than it is: the block stacks them first (_AddBlock
        # measures their full size hints to decide).
        self.setMinimumWidth(1)
        self.setToolTip(tip)
        self.setAccessibleName(label)
        self.setAccessibleDescription(tip)
        self._sync_icon()
        self.toggled.connect(lambda _checked: self._sync_icon())

    def _sync_icon(self) -> None:
        """The source glyph at rest; the picked check while armed, in the
        picked hue, so the armed state never rests on the tint alone."""
        if self.isCheckable() and self.isChecked():
            self.setIcon(icon_for(self, "check", _SOURCE_GLYPH_PX,
                                  qcolor(T.category_ink(T.PICKED_HUE))))
        else:
            self.setIcon(icon_for(self, self._glyph, _SOURCE_GLYPH_PX,
                                  qcolor(T.category_ink(_REFERENCE_HUE)),
                                  disabled_color=qcolor(T.INK_3)))

    def setCheckable(self, checkable: bool) -> None:  # noqa: N802 - Qt naming
        super().setCheckable(checkable)
        self._sync_icon()


class _AddBlock(QFrame):
    """The one way in: the three source buttons inside the drop area, and one
    muted line under them that says what else works or why nothing does.

    The buttons sit side by side when their words fit and stack into full
    width rows when they do not (measured, so a long language stacks too)."""

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
        # The row gives each button the same share (one stretch per column),
        # so it fits only when the widest label fits in that share. Summing
        # the hints let a row through at 382 px on Windows, where Segoe UI
        # makes "Your computer" 129 px against 116 and 101 for the other two:
        # each got 116 px and the label lost its last letter.
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

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        # The width needed never depends on the placement, so a switch
        # cannot bounce back on the resize it causes.
        self._place(stacked=self.width() < self._row_width_needed())

    def set_hint(self, text: str) -> None:
        self.hint.setText(text)

    def set_receiving(self, receiving: bool) -> None:
        """Light the block up while a droppable drag hovers the panel."""
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
    """A reference's name, shortened in the middle with an ellipsis when the
    card is narrower than the name. A plain QLabel with an Ignored policy
    clips the last letters off with nothing to say it did, which reads as a
    broken card; a file name keeps its extension this way."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setStyleSheet(_NAME_LABEL_QSS)
        # Ignored: a long name shrinks instead of widening the dock. The
        # label's own size never depends on the text, so setting the text
        # from resizeEvent cannot loop.
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def resizeEvent(self, event):  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, max(0, self.width())
        )
        self.setText(shown)
        # The whole name on hover only when the card cut it: a tooltip that
        # repeats the visible words is noise.
        self.setToolTip(self._full_text if shown != self._full_text else "")


def reference_display_name(record: ReferenceImage) -> str:
    return (record.source_filename or "").strip() or get_export_copy(
        "widgets.reference_panel.reference_fallback_name", tr("Reference")
    )


class _ReferenceCard(QWidget):
    """One attached reference: the numbered thumbnail (the number the prompt
    can use), the name, the source tag with the one surprise worth saying,
    and a remove X that is always visible."""

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

        # Soft rounded card. WA_StyledBackground because a plain QWidget
        # ignores stylesheet backgrounds without it.
        self.setObjectName("referenceCard")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setStyleSheet(_CARD_QSS)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(T.SPACE_OUTER + 2)

        # Thumbnail, reused from the strip, without its hover X (this card
        # has its own) and without the whole-layer badge (the card says it in
        # words). Click or Enter previews, Delete removes.
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

        # No per-image note field: what the AI should take from an image is
        # said in the prompt, like everything else (owner call 2026-09-02).
        # The wire key context_image_notes stays for the agent API, which
        # still sets notes through the store. Under the name, not beside it:
        # at the dock's narrowest the tag would leave the name three letters.
        tag_text, note_text = _card_source_words(record, is_markup)
        meta = QHBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(T.SPACE_CARD)
        tag = QLabel(tag_text)
        tag.setStyleSheet(_SOURCE_TAG_QSS)
        meta.addWidget(tag, 0, QtC.AlignTop)
        meta.addStretch(1)
        right.addLayout(meta)
        # The surprise, on its own line under the tag: beside it, a narrow
        # dock squeezed it into a three-line column.
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
    """(tag, the one thing the tag does not already say) for one card.

    The tag repeats the button that added the image, and that button already
    says what the source does, so most cards carry no second line. The
    exceptions are the two surprises: a layer or data file that missed the
    zone and went whole, and the Draw composite, which is a drawing rather
    than an import."""
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
    """Full-dock References view: what a reference does, the one way in,
    what is attached, and how to use it.

    Second view over the shared store; the compact strip stays the at-a-glance
    state above the prompt. The dock swaps this panel in via
    set_reference_state() (Draw sibling pattern) and restores the main
    widget when done_clicked fires.
    """

    done_clicked = pyqtSignal()
    # "Map view": the plugin arms (or disarms) the canvas capture.
    map_capture_requested = pyqtSignal()
    # The free-plan notice's "Upgrade to Pro".
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

        # The panel takes a file or a layer dropped anywhere on it, through
        # the same MIME readers the prompt box uses.
        self.setAcceptDrops(True)
        # Focus lands on the panel itself when it opens (no ring on any one
        # button), so Escape and Tab work straight away: the prompt box that
        # held the caret is hidden under this panel.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_STAGE)

        # No close glyph here: it sat right under the dock header's own
        # close X, a duplicate of the Done button below (owner call
        # 2026-09-17). Done is this panel's one way out; the dock owns Escape.
        # The one line under the title says what a reference is FOR: the
        # panel never said it, so "Add a reference" read as a chore.
        header = build_panel_header(
            get_export_copy("widgets.reference_panel.header_title_plural", tr("References")),
            subtitle=get_export_copy(
                "widgets.reference_panel.header_subtitle_purpose",
                tr("Show the AI a style, a legend or an object to match."),
            ),
            show_close=False,
        )
        layout.addWidget(header)

        # -- the way in ----------------------------------------------------
        # File first, because it is the one every user has (the ChatGPT
        # attach order), then the project layer, then the capture tool.
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

        # Messages about the last add, right under the block that caused
        # them (the dock's status box lives under the hidden main widget
        # while this panel is open). An error stays until the next action,
        # not 4 s: a long file name and its reason take longer to read.
        self._error_card = make_notice_card("", "error")
        self._error_label = self._error_card.text_label
        self._error_label.setTextFormat(Qt.TextFormat.PlainText)
        self._error_card.setVisible(False)
        layout.addWidget(self._error_card)
        strip.error_occurred.connect(self._on_strip_error)

        # The free-plan limit, said here: the dock's own upsell banner sits
        # under the hidden main widget, so a free user clicking a source at
        # the limit used to see nothing happen at all.
        self._upsell_card = make_notice_card("", "warning")
        self._upsell_label = self._upsell_card.text_label
        self._upsell_label.setTextFormat(Qt.TextFormat.PlainText)
        self._upgrade_btn = QPushButton(
            get_export_copy("upsell.upgrade_button", tr("Upgrade to Pro")), self._upsell_card
        )
        self._upgrade_btn.setStyleSheet(T.BTN_LINK_QSS)
        self._upgrade_btn.setCursor(QtC.PointingHandCursor)
        self._upgrade_btn.clicked.connect(self.upgrade_requested.emit)
        # Under the sentence, not beside it: beside it squeezed the sentence
        # into a three-line column at the dock's narrowest.
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
        # The dock owns the Pro door; reach it when this panel lives in one.
        opener = getattr(parent, "_open_pro_from", None)
        if callable(opener):
            self.upgrade_requested.connect(lambda: opener("reference_limit"))

        # -- what is attached ---------------------------------------------
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
        # Named, so the transparency rule cannot cascade onto the cards.
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
        # Cards go in right after the empty line, the prompt line after them.
        self._cards_start = self._list_layout.count()

        self._prompt_tip = self._build_prompt_tip()
        self._list_layout.addWidget(self._prompt_tip)

        # No scroll area of its own: the dock already scrolls, and a nested
        # one only got the height of its size hint there, so a live dock
        # showed one card and a half behind a second scrollbar.
        layout.addWidget(self._list_host)

        # Rebuild whenever the shared store changes through the strip (adds
        # from here, drops on the prompt box, paste, the result container...).
        strip.images_changed.connect(self._on_images_changed)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(T.SPACE_CARD)
        action_row.addStretch()
        self._done_btn = QPushButton(get_export_copy("widgets.reference_panel.done_button", tr("Done")))
        # The panel's one primary: the references are kept and the panel closes.
        self._done_btn.setStyleSheet(T.BTN_PRIMARY_QSS)
        self._done_btn.setCursor(QtC.PointingHandCursor)
        self._done_btn.setMinimumWidth(96)
        self._done_btn.clicked.connect(self.done_clicked.emit)
        action_row.addWidget(self._done_btn)
        layout.addLayout(action_row)
        # Everything packs at the top; the free height goes under Done.
        layout.addStretch(1)

        self._rebuild_rows()

    def _build_prompt_tip(self) -> QWidget:
        """The one line that says how to use what is attached: name it in the
        prompt, by its number. Shown only once something is attached, where
        it has a subject (the old tip card said it over an empty panel)."""
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

    # -- public API ------------------------------------------------------

    def activate(self) -> None:
        """Called by the dock each time the panel becomes visible."""
        self._clear_messages()
        self._rebuild_rows()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_readonly(self, readonly: bool) -> None:
        """Lock imports and removal (used during generation)."""
        self._readonly = readonly
        for card in self._rows:
            card.set_readonly(readonly)
        if readonly:
            self._set_receiving(False)
        self._refresh_add_state()

    def set_capture_armed(self, armed: bool) -> None:
        """The plugin owns the capture tool; the button only mirrors its state."""
        self._capture_armed = bool(armed)
        self._map_btn.blockSignals(True)
        self._map_btn.setChecked(self._capture_armed)
        self._map_btn.blockSignals(False)
        self._map_btn._sync_icon()
        self._refresh_add_state()

    # -- imports ---------------------------------------------------------

    def _on_map_capture_clicked(self) -> None:
        # The button's checked state is set back by set_capture_armed once
        # the plugin has actually armed (or disarmed) the tool.
        self._map_btn.blockSignals(True)
        self._map_btn.setChecked(self._capture_armed)
        self._map_btn.blockSignals(False)
        self._map_btn._sync_icon()
        if self._readonly:
            return
        self._clear_messages()
        self.map_capture_requested.emit()

    def _on_file_import_clicked(self) -> None:
        # The strip owns the picker: same formats, same free-tier / cap gate,
        # same error signals, and it refreshes both views on success.
        self._clear_messages()
        self._strip.open_file_picker()

    def _show_layer_menu(self) -> None:
        """Project-layer picker, reusing the prompt box's layer listing
        (lazy import: the dock package may be mid-initialization when this
        panel module is first imported)."""
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

    # -- state -------------------------------------------------------------

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
        """The source buttons' enabled state and the block's one line, from
        the most pressing state down: locked, armed, receiving, full, idle."""
        at_cap = self._at_hard_cap()
        # The free plan's own ceiling, under the hard one: the sources stay
        # clickable there (a click opens the upgrade card), but the line
        # says the limit is reached instead of inviting another drop.
        at_limit = self._at_add_limit()
        enabled = not self._readonly and not at_cap
        for button in (self._file_btn, self._layer_btn):
            button.setEnabled(enabled)
        # The armed capture stays clickable at the cap so it can be disarmed.
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
        # The count takes the warning ink at the ceiling, next to words that
        # already say it (never colour alone).
        self._count_label.setStyleSheet(_COUNT_FULL_QSS if at_limit else _COUNT_QSS)

    def _on_images_changed(self) -> None:
        # A change in the store is the next action: the last message is
        # answered (an add went through, or one was removed to make room).
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
        # The prompt line names "reference 1": with only the Draw composite
        # attached (it carries no number) there is no reference 1 to name.
        self._prompt_tip.setVisible(
            any(not self._store.is_markup(record.id) for record in records)
        )
        limit = self._limit()
        # No "0 of 12" over an empty list: the count says something once
        # there is something to count.
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
        # Done's tooltip talks about references only when there are some.
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
        """Remove through the strip, then hand keyboard focus to the card
        that took its place (or the panel), never to nowhere."""
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

    # -- messages --------------------------------------------------------

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

    # -- drag and drop ---------------------------------------------------

    def _at_add_limit(self) -> bool:
        limit = self._limit()
        return self._at_hard_cap() or (limit > 0 and self._store.count() >= limit)

    def _set_receiving(self, receiving: bool) -> None:
        # At the limit the block does not light up or say "Release to add":
        # the drop would only bring the limit message. It is still taken, so
        # that message (or the upgrade card) answers it.
        receiving = bool(receiving) and not self._readonly and not self._at_add_limit()
        self._add_block.set_receiving(receiving)
        if receiving != self._receiving:
            self._receiving = receiving
            self._refresh_add_state()

    def dragEnterEvent(self, event):  # noqa: N802 - Qt naming
        if self._readonly or not _mime_has_droppable(event.mimeData()):
            event.ignore()
            return
        # Force Copy: a Layers-panel drag proposes MoveAction, which would
        # make QGIS remove the layer from the tree once we accept.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self._set_receiving(True)

    def dragMoveEvent(self, event):  # noqa: N802 - Qt naming
        if self._readonly or not _mime_has_droppable(event.mimeData()):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def dragLeaveEvent(self, event):  # noqa: N802 - Qt naming
        self._set_receiving(False)
        event.accept()

    def dropEvent(self, event):  # noqa: N802 - Qt naming
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
            # Copy, not move - never let the source remove the user's layer.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        event.ignore()


def _html_escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
