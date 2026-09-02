"""The AI Segmentation hand-off card in the Prompt Library.

The Segment family (two-colour masks of buildings, trees, roads) left the
library on 2026-09-02: the AI Segmentation plugin traces those as real
geometry. The people who used those cards will look for them at the top of
the Analyze page and in the search box, so the card stands exactly there, and
nowhere else.
"""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ....core import qt_compat as QtC
from ....core.i18n import tr
from ...cross_plugin_discovery import open_ai_segmentation
from ...dock.style import _BTN_GHOST

# Search words that used to reach a Segment card. Lowercase, matched as
# substrings of the lowercased query.
_SEGMENTATION_QUERY_WORDS = (
    "segment", "outline", "detect", "building", "tree", "road",
    "water", "vegetation", "footprint", "mask",
)

_CARD_QSS = (
    "QFrame#segHandoffCard { background-color: rgba(30, 136, 229, 0.08);"
    " border: 1px solid rgba(30, 136, 229, 0.28); border-radius: 6px; }"
    "QLabel { background: transparent; border: none; color: palette(text); }"
)


def query_asks_for_segmentation(query: str) -> bool:
    """Whether a library search reads as a request for object outlines."""
    q = (query or "").strip().lower()
    return bool(q) and any(word in q for word in _SEGMENTATION_QUERY_WORDS)


def build_segmentation_handoff_card(parent=None) -> QFrame:
    """A full-width info card: one sentence and a ghost button that opens AI
    Segmentation, or the Plugin Manager on it when it is not installed."""
    card = QFrame(parent)
    card.setObjectName("segHandoffCard")
    card.setStyleSheet(_CARD_QSS)
    row = QHBoxLayout(card)
    row.setContentsMargins(14, 10, 14, 10)
    row.setSpacing(12)

    text_col = QVBoxLayout()
    text_col.setContentsMargins(0, 0, 0, 0)
    text_col.setSpacing(2)
    text = QLabel(tr(
        "Looking for building, tree or road outlines? AI Segmentation traces "
        "them as real geometry."
    ))
    text.setWordWrap(True)
    text.setStyleSheet("font-size: 12px;")
    text_col.addWidget(text)
    row.addLayout(text_col, 1)

    button = QPushButton(tr("Open AI Segmentation"))
    button.setCursor(QtC.PointingHandCursor)
    button.setMinimumHeight(30)
    button.setStyleSheet(_BTN_GHOST)
    button.clicked.connect(lambda: open_ai_segmentation())
    row.addWidget(button, 0, QtC.AlignVCenter)
    return card
