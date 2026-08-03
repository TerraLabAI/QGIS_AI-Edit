


















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


_COPY_RESET_DELAY_MS = 1400
_SHEET_WIDTH_PX = 340

_ANCHOR_GAP_PX = 6

_PROMPT_MAX_H_PX = 150
_SHEET_MARGIN_PX = 16
_WELL_PAD_PX = 10


_PROMPT_TEXT_W_PX = _SHEET_WIDTH_PX - 2 * _SHEET_MARGIN_PX - 2 * _WELL_PAD_PX - 8


_FACT_CAPTION_STYLE = (
    f"QLabel {{ color: {INK_2}; font-size: {FONT_HINT}px; font-weight: 500;"
    " background: transparent; border: none; }"
)
_FACT_VALUE_STYLE = (
    f"QLabel {{ color: {INK}; font-size: {FONT_BODY}px; font-weight: 600;"
    " background: transparent; border: none; }"
)

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

_CARD_BTN_QSS = BTN_GHOST_QSS + f"QPushButton:focus {{ border-color: {ACCENT_BORDER}; }}"


@dataclass
class VersionFacts:



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



    if facts.is_original:
        return []
    pairs = [
        (get_export_copy("widgets.version_details_popup.fact_made_from", tr("Made from")),
         facts.base_label),
        (get_export_copy("widgets.version_details_popup.fact_quality", tr("Quality")),
         facts.definition),

        (get_export_copy("widgets.version_details_popup.fact_output_size", tr("Output size")),
         facts.dimensions),
        (get_export_copy("widgets.version_details_popup.fact_template", tr("Template")),
         facts.template_name),
    ]
    return [(caption, value) for caption, value in pairs if value]


class VersionDetailsPopup(QDialog):



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



    def _build_head(self, sheet: QFrame, layout: QVBoxLayout) -> None:

        title = QLabel(self._facts.label, sheet)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        title.setStyleSheet(TITLE_QSS)
        layout.addWidget(title)

        summary = (


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


        if not self._prompt:



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


        needed = max(1, body.sizeHint().height())
        scroll.setFixedHeight(min(needed, get_export_dial(
            "widgets.version_details_popup.prompt_max_h_px", _PROMPT_MAX_H_PX)))
        box.addWidget(scroll)
        layout.addWidget(well)

    def _build_actions(self, sheet: QFrame, layout: QVBoxLayout) -> None:


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



    def _pick(self) -> None:


        self.accept()
        if self._on_pick is not None:
            self._on_pick()

    def _reveal_layer(self) -> None:
        self.accept()
        if self._on_reveal_layer is not None:
            self._on_reveal_layer()

    def _place_under(self, anchor) -> None:

        try:
            origin = anchor.mapToGlobal(QPoint(0, anchor.height() + _ANCHOR_GAP_PX))
            screen = anchor.screen() or QApplication.primaryScreen()
        except RuntimeError:
            return
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
