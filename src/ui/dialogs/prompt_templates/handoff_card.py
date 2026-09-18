"""The AI Segmentation hand-off card in the Prompt Library.

The Segment family (two-colour masks of buildings, trees, roads) left the
library on 2026-09-02: the AI Segmentation plugin traces those as real
geometry. The people who used those cards will look for them at the top of
the Analyze page and in the search box, so the card stands exactly there, and
nowhere else.
"""
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

# Search words that used to reach a Segment card. Lowercase, matched as
# substrings of the lowercased query.
_SEGMENTATION_QUERY_WORDS = (
    "segment", "outline", "detect", "building", "tree", "road",
    "water", "vegetation", "footprint", "mask",
)

# An info card: the blue wash on its soft border, 10 px corners.
_CARD_QSS = (
    f"QFrame#segHandoffCard {{ background-color: {tk.ACCENT_TINT};"
    f" border: 1px solid {tk.ACCENT_BORDER_SOFT}; border-radius: {tk.RADIUS_CARD}px; }}"
    f"QFrame#segHandoffCard QLabel {{ background: transparent; border: none; color: {tk.INK}; }}"
)
_HANDOFF_GLYPH_PX = 20
# Narrower than this, the button goes under the sentence, px.
_STACK_BELOW_PX = 460


def query_asks_for_segmentation(query: str) -> bool:
    """Whether a library search reads as a request for object outlines."""
    q = (query or "").strip().lower()
    return bool(q) and any(word in q for word in _SEGMENTATION_QUERY_WORDS)


class _HandoffCard(QFrame):
    """The info card. Below ``_STACK_BELOW_PX`` wide its button moves under
    the sentence: side by side, a 640 px window squeezed the sentence to three
    words a line beside the button."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("segHandoffCard")
        self.setStyleSheet(_CARD_QSS)
        # Never taller than its content: a page's closing stretch used to
        # share its space with the card, leaving a blue slab with the button
        # at its foot.
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

        # Sentence and button share one box that turns from a row into a
        # column when the card gets narrow.
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

    def resizeEvent(self, event):  # noqa: N802 - Qt signature
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
            # Stacked, the glyph stays beside the sentence's first line.
            self._row.setAlignment(self._glyph, QtC.AlignTop if narrow else QtC.AlignVCenter)


def build_segmentation_handoff_card(parent=None) -> QFrame:
    """A full-width info card: one sentence and a ghost button that opens AI
    Segmentation, or the Plugin Manager on it when it is not installed."""
    return _HandoffCard(parent)
