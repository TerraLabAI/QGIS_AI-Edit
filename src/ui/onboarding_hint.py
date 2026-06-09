"""Dismissible in-UI guidance hints.

A new GIS user who is not AI-savvy needs to be told, in plain words, what each
step of the flow does. These hints are non-blocking inline callouts: shown once,
closed for good with the x, and re-enabled from Account Settings ("Show guidance
tips again"). One hint per screen at most, so guidance never becomes clutter.

Pattern (how mature apps do onboarding without burdening the UI):
  - inline callout, never a modal that blocks work,
  - dismissible and remembered (QSettings), not nagging,
  - re-showable on demand from settings,
  - short, action-oriented copy with an optional 1-2-3 step row.
"""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import QSettings, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import qt_compat as QtC
from ..core.i18n import tr

_SETTINGS_PREFIX = "AIEdit/hints/"

# Hint ids. Listed here so settings can reset them all at once.
HINT_LIBRARY_INTRO = "library_intro"
HINT_ZONE = "flow_zone"
HINT_PROMPT = "flow_prompt"
HINT_TOOLS = "flow_tools"
HINT_MARKUP = "flow_markup"
HINT_VECTORIZE = "flow_vectorize"
ALL_HINTS = [
    HINT_LIBRARY_INTRO, HINT_ZONE, HINT_PROMPT, HINT_TOOLS,
    HINT_MARKUP, HINT_VECTORIZE,
]

_ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "resources", "icons"
)


def is_hint_dismissed(hint_id: str) -> bool:
    return bool(QSettings().value(_SETTINGS_PREFIX + hint_id, False, type=bool))


def dismiss_hint(hint_id: str) -> None:
    QSettings().setValue(_SETTINGS_PREFIX + hint_id, True)


def reset_hints() -> None:
    """Re-enable every hint so the user sees the guidance again."""
    s = QSettings()
    for hint_id in ALL_HINTS:
        s.remove(_SETTINGS_PREFIX + hint_id)


_CARD_STYLE = (
    "QFrame#hintCard { background: rgba(139,172,39,0.12); "
    "border: 1px solid rgba(139,172,39,0.38); border-radius: 11px; }"
)
_TITLE_STYLE = (
    "color: palette(text); font-size: 14px; font-weight: 800; "
    "background: transparent; border: none;"
)
_BODY_STYLE = (
    "color: palette(text); font-size: 12px; background: transparent; border: none;"
)
_CLOSE_STYLE = (
    "QToolButton { background: transparent; color: rgba(120,124,130,0.95); "
    "border: none; font-size: 16px; font-weight: 700; }"
    "QToolButton:hover { color: palette(text); }"
)
_STEP_STYLE = (
    "QFrame { background: palette(base); border: 1px solid rgba(128,128,128,0.20); "
    "border-radius: 9px; }"
)
_STEP_NUM_STYLE = (
    "color: #14210A; background: #8BAC27; border-radius: 11px; "
    "font-size: 12px; font-weight: 800;"
)
_STEP_TITLE_STYLE = (
    "color: palette(text); font-size: 12px; font-weight: 700; "
    "background: transparent; border: none;"
)
_STEP_SUB_STYLE = (
    "color: rgba(120,124,130,0.95); font-size: 11px; "
    "background: transparent; border: none;"
)


class DismissibleHint(QWidget):
    """A green-tinted inline guidance callout with an optional step row.

    ``steps`` is a list of ``(glyph, title, subtitle)`` tuples rendered as a
    1-2-3 row. Closing the card stores the dismissal so it stays hidden until
    the user resets guidance from settings.
    """

    dismissed = pyqtSignal()

    def __init__(
        self,
        hint_id: str,
        title: str,
        body: str,
        steps: list[tuple[str, str, str]] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._hint_id = hint_id

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setObjectName("hintCard")
        card.setStyleSheet(_CARD_STYLE)
        outer.addWidget(card)

        col = QVBoxLayout(card)
        col.setContentsMargins(16, 13, 12, 14)
        col.setSpacing(7)

        close_btn = QToolButton(card)
        close_btn.setText("✕")  # x glyph
        close_btn.setToolTip(tr("Got it - hide this tip"))
        close_btn.setCursor(QtC.PointingHandCursor)
        close_btn.setStyleSheet(_CLOSE_STYLE)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self._on_close)

        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet(_BODY_STYLE)

        if title:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(_TITLE_STYLE)
            title_lbl.setWordWrap(True)
            head.addWidget(title_lbl, 1)
            head.addWidget(close_btn, 0, QtC.AlignTop)
            col.addLayout(head)
            col.addWidget(body_lbl)
        else:
            # No title: the close button floats at the body's top-right so the
            # text starts at the very top - no empty title band above it.
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            head.addWidget(body_lbl, 1, QtC.AlignTop)
            head.addWidget(close_btn, 0, QtC.AlignTop)
            col.addLayout(head)

        if steps:
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 0)
            row.setSpacing(8)
            for i, (glyph, step_title, step_sub) in enumerate(steps, start=1):
                row.addWidget(self._step(i, glyph, step_title, step_sub), 1)
            col.addLayout(row)

    def _step(self, n: int, glyph: str, title: str, sub: str) -> QFrame:
        box = QFrame(self)
        box.setStyleSheet(_STEP_STYLE)
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(9)
        num = QLabel(str(n))
        num.setFixedSize(22, 22)
        num.setAlignment(QtC.AlignCenter)
        num.setStyleSheet(_STEP_NUM_STYLE)
        h.addWidget(num, 0)
        txt = QVBoxLayout()
        txt.setContentsMargins(0, 0, 0, 0)
        txt.setSpacing(1)
        t = QLabel(f"{glyph}  {title}" if glyph else title)
        t.setStyleSheet(_STEP_TITLE_STYLE)
        txt.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setWordWrap(True)
            s.setStyleSheet(_STEP_SUB_STYLE)
            txt.addWidget(s)
        h.addLayout(txt, 1)
        return box

    def _on_close(self) -> None:
        dismiss_hint(self._hint_id)
        self.hide()
        self.dismissed.emit()


def search_icon() -> QIcon:
    """Magnifier icon for the prompt-library search field."""
    return QIcon(os.path.join(_ICONS_DIR, "search.svg"))
