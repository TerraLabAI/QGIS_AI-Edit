







from __future__ import annotations

from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
)

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy
from ....core.i18n import tr
from ...cross_plugin_discovery import open_ai_segmentation
from ...dock import design_tokens as tk
from ...icons import pixmap_for



_SEGMENTATION_QUERY_WORDS = (
    "segment", "outline", "detect", "building", "tree", "road",
    "water", "vegetation", "footprint", "mask",
)


_CARD_QSS = (
    f"QFrame#segHandoffCard {{ background-color: {tk.ACCENT_TINT};"
    f" border: 1px solid {tk.ACCENT_BORDER_SOFT}; border-radius: {tk.RADIUS_CARD}px; }}"
    f"QFrame#segHandoffCard QLabel {{ background: transparent; border: none; color: {tk.INK}; }}"
)
_HANDOFF_GLYPH_PX = 20

_STACK_BELOW_PX = 460


def query_asks_for_segmentation(query: str) -> bool:

    q = (query or "").strip().lower()
    return bool(q) and any(word in q for word in _SEGMENTATION_QUERY_WORDS)


class _HandoffCard(QFrame):




    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("segHandoffCard")
        self.setStyleSheet(_CARD_QSS)



        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        row = QHBoxLayout(self)
        self._row = row
        row.setContentsMargins(14, 12, 12, 12)
        row.setSpacing(12)

        glyph = QLabel(self)
        glyph.setFixedSize(QSize(_HANDOFF_GLYPH_PX, _HANDOFF_GLYPH_PX))
        glyph.setPixmap(pixmap_for(glyph, "polygon", _HANDOFF_GLYPH_PX, tk.qcolor(tk.LINK_INK)))
        row.addWidget(glyph, 0, QtC.AlignVCenter)
        self._glyph = glyph



        self._body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(12)
        text = QLabel(get_export_copy(
            "dialogs.handoff_card.segmentation_pitch",
            tr(
                "Looking for building, tree or road outlines? AI Segmentation traces "
                "them as real geometry."
            ),
        ))
        text.setWordWrap(True)
        text.setStyleSheet(f"font-size: {tk.FONT_BODY}px;")
        self._body.addWidget(text, 1, QtC.AlignVCenter)

        self._button = QPushButton(get_export_copy(
            "dialogs.handoff_card.open_segmentation_button", tr("Open AI Segmentation")))
        self._button.setCursor(QtC.PointingHandCursor)
        self._button.setStyleSheet(tk.BTN_GHOST_QSS)
        self._button.setAutoDefault(False)
        self._button.clicked.connect(open_ai_segmentation)
        self._body.addWidget(self._button, 0, QtC.AlignVCenter)
        row.addLayout(self._body, 1)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        narrow = event.size().width() < _STACK_BELOW_PX
        direction = (
            QBoxLayout.Direction.TopToBottom if narrow
            else QBoxLayout.Direction.LeftToRight
        )
        if self._body.direction() != direction:
            self._body.setDirection(direction)
            align = QtC.AlignLeft if narrow else QtC.AlignVCenter
            self._body.setAlignment(self._button, align)

            self._row.setAlignment(self._glyph, QtC.AlignTop if narrow else QtC.AlignVCenter)


def build_segmentation_handoff_card(parent=None) -> QFrame:


    return _HandoffCard(parent)
