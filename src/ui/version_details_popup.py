"""Version details card, opened from a version-strip tile.

Split out of ``version_strip.py`` when that file passed the size ceiling. The
seam is the concern: the strip is a film-strip widget, this is the card that
reads ONE version out loud. No image: the canvas already shows the version
full-size.

The card is a sheet in the AI Agent line (surface on the strong hairline,
14 px corners, no shadow), laid out as one designed block instead of a debug
dump (Yvann, 2026-09-17): a title that says what the version IS, a quiet line
under it, the facts as label/value rows, the prompt in a well with room to
read, then the actions grouped at the bottom behind a hairline. It opens under
the tile it describes, and Close is its one way out (Escape and a click
outside still work).

Since the result screen's prompt box now starts empty, this card is where the
prompt that produced a version lives, so Copy prompt sits in the action row as
a button, not as a link glued to a section label.
"""
from __future__ import annotations

from dataclasses import dataclass

from qgis.PyQt.QtCore import QPoint, QSize, Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core import qt_compat as QtC
from ..core.config_store import get_export_copy, get_export_dial
from ..core.i18n import tr
from .dock.design_tokens import (
    ACCENT_BORDER,
    BODY_QSS,
    BTN_GHOST_QSS,
    BTN_LINK_QSS,
    BTN_PRIMARY_WIDE_QSS,
    CARD_QSS,
    FONT_BODY,
    FONT_HINT,
    HINT_QSS,
    INK,
    INK_2,
    LINE,
    SCROLL_AREA_QSS,
    TITLE_QSS,
    qcolor,
)
from .icons import icon_for

# Delay before the "Copied" confirmation reverts to "Copy prompt".
_COPY_RESET_DELAY_MS = 1400
_SHEET_WIDTH_PX = 340
# The card's gap under the tile it describes.
_ANCHOR_GAP_PX = 6
# The prompt well never grows past this; a longer prompt scrolls inside it.
_PROMPT_MAX_H_PX = 150
_SHEET_MARGIN_PX = 16
_WELL_PAD_PX = 10
# Room the prompt text has inside the well (sheet minus both margin sets and
# the slim scrollbar), used to size the well to the prompt it holds.
_PROMPT_TEXT_W_PX = _SHEET_WIDTH_PX - 2 * _SHEET_MARGIN_PX - 2 * _WELL_PAD_PX - 8

# One fact of the version: a quiet caption on the left, the value on the right.
_FACT_CAPTION_STYLE = (
    f"QLabel {{ color: {INK_2}; font-size: {FONT_HINT}px; font-weight: 500;"
    " background: transparent; border: none; }"
)
_FACT_VALUE_STYLE = (
    f"QLabel {{ color: {INK}; font-size: {FONT_BODY}px; font-weight: 600;"
    " background: transparent; border: none; }"
)
# The section label above the prompt: a small 600 header, not shouting caps.
_STRIP_SECTION_STYLE = (
    f"color: {INK_2}; font-size: {FONT_HINT}px; font-weight: 600;"
    " background: transparent; border: none;"
)
_PROMPT_TEXT_STYLE = (
    f"QLabel {{ color: {INK}; font-size: {FONT_BODY}px; background: transparent;"
    " border: none; }"
)
_NOTE_STYLE = HINT_QSS
_ACTION_DIVIDER_STYLE = f"QFrame {{ background: {LINE}; border: none; }}"
# The card's buttons take focus, so a keyboard user can see where they are.
_CARD_BTN_QSS = BTN_GHOST_QSS + f"QPushButton:focus {{ border-color: {ACCENT_BORDER}; }}"


@dataclass
class VersionFacts:
    """Everything the card reads about one version. Built by the strip, which
    owns the tile's label, lineage and metadata."""

    label: str = ""
    is_original: bool = False
    is_picked: bool = False
    prompt: str = ""
    definition: str = ""
    dimensions: str = ""
    template_name: str = ""
    base_label: str = ""
    layer_name: str = ""


def _fact_rows(facts: VersionFacts) -> list[tuple[str, str]]:
    """The version's facts as caption/value pairs, in reading order. Empty
    values are dropped, so a restored session with no lineage shows fewer
    rows rather than blank ones."""
    if facts.is_original:
        return []
    pairs = [
        (get_export_copy("widgets.version_details_popup.fact_made_from", tr("Made from")),
         facts.base_label),
        (get_export_copy("widgets.version_details_popup.fact_quality", tr("Quality")),
         facts.definition),
        # Same word as the session details window, for the same fact.
        (get_export_copy("widgets.version_details_popup.fact_output_size", tr("Output size")),
         facts.dimensions),
        (get_export_copy("widgets.version_details_popup.fact_template", tr("Template")),
         facts.template_name),
    ]
    return [(caption, value) for caption, value in pairs if value]


class VersionDetailsPopup(QDialog):
    """The card a tile opens: what the version is, its facts, its prompt, and
    the actions that belong to it."""

    def __init__(
        self,
        facts: VersionFacts,
        parent=None,
        anchor=None,
        on_pick=None,
        on_reveal_layer=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(get_export_copy(
            "widgets.version_details_popup.window_title", tr("Version details")))
        self.setModal(True)
        self._facts = facts
        self._prompt = facts.prompt or ""
        self._on_pick = on_pick
        self._on_reveal_layer = on_reveal_layer
        self._copy_btn = None
        # A frameless card: the sheet below is the whole window, its corners on
        # a transparent ground. Popup closes on an outside click.
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(CARD_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sheet = QFrame(self)
        sheet.setObjectName("sheet")
        sheet.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sheet.setFixedWidth(_SHEET_WIDTH_PX)
        outer.addWidget(sheet)

        layout = QVBoxLayout(sheet)
        layout.setContentsMargins(
            _SHEET_MARGIN_PX, _SHEET_MARGIN_PX - 2, _SHEET_MARGIN_PX, _SHEET_MARGIN_PX - 2
        )
        layout.setSpacing(10)

        self._build_head(sheet, layout)
        self._build_facts(sheet, layout)
        self._build_prompt(sheet, layout)
        self._build_actions(sheet, layout)

        self.adjustSize()
        if anchor is not None:
            self._place_under(anchor)

    # -- build -------------------------------------------------------------

    def _build_head(self, sheet: QFrame, layout: QVBoxLayout) -> None:
        """The title says what this version is, not what its prompt said."""
        title = QLabel(self._facts.label, sheet)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        summary = (
            # The title already says "Original": the line under it says what
            # that is instead of repeating the word.
            get_export_copy(
                "widgets.version_details_popup.original_summary_v2",
                tr("Your map, before any AI edit"),
            )
            if self._facts.is_original
            else get_export_copy(
                "widgets.version_details_popup.version_summary",
                tr("An AI edit of your map zone"),
            )
        )
        if self._facts.is_picked:
            # Says where this version stands without colour: the picked tile's
            # tint and check say the same thing on the strip.
            summary = "{}. {}".format(
                summary,
                get_export_copy(
                    "widgets.version_details_popup.picked_note",
                    tr("The next edit starts from it."),
                ),
            )
        line = QLabel(summary, sheet)
        line.setTextFormat(Qt.TextFormat.PlainText)
        line.setWordWrap(True)
        line.setStyleSheet(_NOTE_STYLE)
        layout.addWidget(line)

    def _build_facts(self, sheet: QFrame, layout: QVBoxLayout) -> None:
        """The facts as quiet rows in a well: caption left, value right."""
        rows = _fact_rows(self._facts)
        layer_name = self._facts.layer_name
        if not rows and not layer_name:
            return
        well = QFrame(sheet)
        well.setObjectName("inset")
        well.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box = QVBoxLayout(well)
        box.setContentsMargins(_WELL_PAD_PX, 8, _WELL_PAD_PX, 8)
        box.setSpacing(6)
        for caption, value in rows:
            box.addWidget(self._fact_row(well, caption, value))
        if layer_name:
            box.addWidget(self._saved_layer_row(well, layer_name))
        layout.addWidget(well)

    def _fact_row(self, parent: QWidget, caption: str, value: str) -> QWidget:
        row = QWidget(parent)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        cap = QLabel(caption, row)
        cap.setTextFormat(Qt.TextFormat.PlainText)
        cap.setStyleSheet(_FACT_CAPTION_STYLE)
        line.addWidget(cap, 0)
        line.addStretch(1)
        val = QLabel(value, row)
        val.setTextFormat(Qt.TextFormat.PlainText)
        val.setWordWrap(True)
        val.setAlignment(QtC.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        val.setStyleSheet(_FACT_VALUE_STYLE)
        line.addWidget(val, 0)
        return row

    def _saved_layer_row(self, parent: QWidget, layer_name: str) -> QWidget:
        """The layer this version was written to. It used to eat a whole row of
        the result screen ("Saved as ..."); here it is one fact among the
        others, and its name still selects the layer in the Layers panel."""
        row = QWidget(parent)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        cap = QLabel(
            get_export_copy(
                "widgets.version_details_popup.fact_added_to_map_as",
                tr("Added to your map as"),
            ),
            row,
        )
        cap.setTextFormat(Qt.TextFormat.PlainText)
        cap.setStyleSheet(_FACT_CAPTION_STYLE)
        line.addWidget(cap, 0)
        line.addStretch(1)
        # A layer name is often a whole prompt: elide it to the room the row
        # has, and keep the full name in the tooltip.
        link = QPushButton(row)
        link.setStyleSheet(BTN_LINK_QSS)
        cap.ensurePolished()
        link.ensurePolished()
        room = _SHEET_WIDTH_PX - 2 * _SHEET_MARGIN_PX - 2 * _WELL_PAD_PX - 10
        room -= cap.fontMetrics().horizontalAdvance(cap.text()) + 12
        link.setText(
            link.fontMetrics().elidedText(
                layer_name, Qt.TextElideMode.ElideMiddle, max(60, room)
            )
        )
        link.setCursor(QtC.PointingHandCursor)
        link.setFlat(True)
        tip = get_export_copy(
            "widgets.version_details_popup.saved_layer_tooltip",
            tr("Show this layer in the Layers panel"),
        )
        link.setToolTip(f"{layer_name}\n{tip}")
        link.setAccessibleName(f"{layer_name} - {tip}")
        link.clicked.connect(self._reveal_layer)
        link.setAutoDefault(False)
        line.addWidget(link, 0)
        return row

    def _build_prompt(self, sheet: QFrame, layout: QVBoxLayout) -> None:
        """The prompt in a well with room to read, or a one-line note when the
        version has none."""
        if not self._prompt:
            # The Original has no prompt and never had one: the line under the
            # title already says what it is, so a second sentence saying the
            # same thing is dropped.
            if self._facts.is_original:
                return
            note = QLabel(
                get_export_copy(
                    "widgets.version_details_popup.no_prompt_note",
                    tr("No prompt was saved for this version."),
                ),
                sheet,
            )
            note.setWordWrap(True)
            note.setTextFormat(Qt.TextFormat.PlainText)
            note.setStyleSheet(BODY_QSS)
            layout.addWidget(note)
            return

        section = QLabel(
            get_export_copy("widgets.version_details_popup.prompt_label", tr("Prompt")), sheet
        )
        section.setStyleSheet(_STRIP_SECTION_STYLE)
        layout.addWidget(section)

        well = QFrame(sheet)
        well.setObjectName("inset")
        well.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        box = QVBoxLayout(well)
        box.setContentsMargins(_WELL_PAD_PX, 8, _WELL_PAD_PX, 8)
        box.setSpacing(0)

        body = QLabel(self._prompt)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(_PROMPT_TEXT_STYLE)
        body.setAlignment(QtC.AlignLeft | Qt.AlignmentFlag.AlignTop)
        body.setFixedWidth(_PROMPT_TEXT_W_PX)

        scroll = QScrollArea(well)
        scroll.setWidget(body)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        scroll.setStyleSheet(SCROLL_AREA_QSS)
        scroll.viewport().setAutoFillBackground(False)
        # A short prompt gets exactly its height; a long one stops at the
        # ceiling and scrolls, so the card never grows past the screen.
        needed = max(1, body.sizeHint().height())
        scroll.setFixedHeight(min(needed, get_export_dial(
            "widgets.version_details_popup.prompt_max_h_px", _PROMPT_MAX_H_PX)))
        box.addWidget(scroll)
        layout.addWidget(well)

    def _build_actions(self, sheet: QFrame, layout: QVBoxLayout) -> None:
        """The actions, grouped at the bottom behind a hairline: the one thing
        this version can do next, then Copy prompt and the way out."""
        divider = QFrame(sheet)
        divider.setFixedHeight(1)
        divider.setStyleSheet(_ACTION_DIVIDER_STYLE)
        layout.addWidget(divider)

        if self._on_pick is not None and not self._facts.is_picked:
            pick = QPushButton(
                get_export_copy(
                    "widgets.version_details_popup.start_from_button",
                    tr("Start from {label}"),
                ).replace("{label}", self._facts.label),
                sheet,
            )
            pick.setStyleSheet(BTN_PRIMARY_WIDE_QSS)
            pick.setCursor(QtC.PointingHandCursor)
            pick.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            pick.clicked.connect(self._pick)
            # Enter answers the card's one filled action, and nothing else.
            pick.setDefault(True)
            layout.addWidget(pick)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        if self._prompt:
            self._copy_btn = QPushButton(
                get_export_copy(
                    "widgets.version_details_popup.copy_prompt_button", tr("Copy prompt")
                ),
                sheet,
            )
            self._copy_btn.setIcon(icon_for(sheet, "copy", 14, qcolor(INK_2)))
            self._copy_btn.setIconSize(QSize(14, 14))
            self._copy_btn.setStyleSheet(_CARD_BTN_QSS)
            self._copy_btn.setCursor(QtC.PointingHandCursor)
            self._copy_btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._copy_btn.clicked.connect(self._copy_prompt)
            # A QDialog makes every button auto-default: Enter on a picked
            # version's card (no Start from) used to press Copy prompt.
            self._copy_btn.setAutoDefault(False)
            row.addWidget(self._copy_btn, 0)
        row.addStretch(1)
        close = QPushButton(
            get_export_copy("widgets.version_details_popup.close_button", tr("Close")), sheet
        )
        close.setStyleSheet(_CARD_BTN_QSS)
        close.setCursor(QtC.PointingHandCursor)
        close.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        close.clicked.connect(self.reject)
        close.setAutoDefault(False)
        row.addWidget(close, 0)
        layout.addLayout(row)

    # -- actions -----------------------------------------------------------

    def _pick(self) -> None:
        """Make this version the one the next edit starts from, then close:
        the same thing a click on its tile does, said in words."""
        self.accept()
        if self._on_pick is not None:
            self._on_pick()

    def _reveal_layer(self) -> None:
        self.accept()
        if self._on_reveal_layer is not None:
            self._on_reveal_layer()

    def _place_under(self, anchor) -> None:
        """Open under ``anchor``, kept inside the screen it is on."""
        try:
            origin = anchor.mapToGlobal(QPoint(0, anchor.height() + _ANCHOR_GAP_PX))
            screen = anchor.screen() or QApplication.primaryScreen()
        except RuntimeError:
            return  # the tile was deleted before the card opened
        x, y = origin.x(), origin.y()
        if screen is not None:
            area = screen.availableGeometry()
            x = max(area.left(), min(x, area.right() - self.width()))
            if y + self.height() > area.bottom():
                y = max(area.top(), origin.y() - anchor.height() - 2 * _ANCHOR_GAP_PX - self.height())
        self.move(x, y)

    def _copy_prompt(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._prompt)
        if self._copy_btn is not None:
            self._copy_btn.setText(get_export_copy(
                "widgets.version_details_popup.copied_button", tr("Copied")))
            QtC.safe_single_shot(
                get_export_dial(
                    "widgets.version_details_popup.copy_reset_delay_ms", _COPY_RESET_DELAY_MS
                ),
                self._copy_btn,
                self._reset_copy_btn,
            )

    def _reset_copy_btn(self) -> None:
        if self._copy_btn is not None:
            self._copy_btn.setText(get_export_copy(
                "widgets.version_details_popup.copy_prompt_button", tr("Copy prompt")))
