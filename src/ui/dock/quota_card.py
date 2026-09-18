"""The account's credit balance, only at the moments it changes what the user can do.

AI Agent's ``quota_card.py`` on AI Edit's line (Yvann, 2026-09-17: the dock no
longer prints a balance; Settings holds it). Four shapes, one widget:

* ``show_low``: a free account near the end of its month. One bold row and a
  small primary "Get Pro". Ignorable.
* ``show_low_contact``: a subscriber running low. The same row with a ghost
  "Copy email" and the address under it.
* ``show_wall``: the free month is spent. The fact, the date it comes back,
  one line of what Pro adds, one wide primary, and the custom-needs line.
* ``show_contact``: a subscriber who spent the month. The fact, one line, a
  ghost "Copy email", the address, and a quiet "Manage plan".

The card only lays words out. The dock decides when each shape shows
(``DockAccountMixin.set_credits``, ``DockProCeilingMixin``) and what a click
does (the signed-in plans page, the clipboard), so the telemetry stays where
it was.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.i18n import tr
from ..icons import icon_for
from .design_tokens import (
    BODY_QSS,
    BTN_GHOST_QSS,
    BTN_ICON_QSS,
    BTN_PRIMARY_QSS,
    BTN_PRIMARY_WIDE_PX,
    BTN_PRIMARY_WIDE_QSS,
    BTN_QUIET_QSS,
    BTN_SMALL_PX,
    FONT_BASE,
    FONT_BODY,
    HINT_QSS,
    INK,
    INK_2,
    LINE,
    RADIUS_PANEL,
    RADIUS_PILL_SMALL,
    SPACE_CARD,
    SPACE_TIGHT,
    SURFACE,
    category_line,
    category_tint,
    gauge_category,
    qcolor,
    repolish_widget,
)

_DISMISS_GLYPH_PX = 14

_CARD_MARGINS = (16, 14, 16, 14)
_CARD_QSS_TEMPLATE = (
    "QWidget#quotaCard {{ background: {ground}; border: 1px solid {line};"
    f" border-radius: {RADIUS_PANEL}px; }}}}"
    "QLabel {{ background: transparent; border: none; }}"
)
_CARD_QSS = _CARD_QSS_TEMPLATE.format(ground=SURFACE, line=LINE)
# What is left, as a fraction, for each shape: the card takes the gauge hue
# (amber when low, coral once spent), a faint ground and a line in it.
_SHAPE_LEFT = {"low": 0.1, "low_contact": 0.1, "free_out": 0.0, "paid_out": 0.0}


def _card_qss(state: str) -> str:
    if state not in _SHAPE_LEFT:
        return _CARD_QSS
    category = gauge_category(_SHAPE_LEFT[state])
    return _CARD_QSS_TEMPLATE.format(ground=category_tint(category), line=category_line(category))


_TITLE_QSS = f"font-size: {FONT_BASE}px; font-weight: 600; color: {INK}; background: transparent; border: none;"
_COMPACT_QSS = f"font-size: {FONT_BODY}px; font-weight: 600; color: {INK}; background: transparent; border: none;"
# The card's small buttons: the 32 px pills cut to 28 so they sit on one row
# with the sentence. Later declarations win, so the base keeps its colours.
_SMALL_PILL = (
    f"QPushButton {{ min-height: {BTN_SMALL_PX}px; max-height: {BTN_SMALL_PX}px;"
    f" border-radius: {RADIUS_PILL_SMALL}px; padding: 0 12px; }}"
)
_SMALL_PRIMARY_QSS = BTN_PRIMARY_QSS + _SMALL_PILL
_SMALL_GHOST_QSS = BTN_GHOST_QSS + (
    f"QPushButton {{ min-height: {BTN_SMALL_PX - 2}px; max-height: {BTN_SMALL_PX - 2}px;"
    f" border-radius: {RADIUS_PILL_SMALL}px; padding: 0 12px; }}"
)


def _pill(parent: QWidget, qss: str, height: int) -> QPushButton:
    btn = QPushButton(parent)
    btn.setStyleSheet(qss)
    btn.setFixedHeight(height)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setAutoDefault(False)
    return btn


def _words(parent: QWidget, qss: str, selectable: bool = False) -> QLabel:
    label = QLabel(parent)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setStyleSheet(qss)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class QuotaCard(QWidget):
    """Low-balance row or end-of-month card; hidden otherwise.

    ``primary_clicked``: the compact primary or the wide one (the plans page).
    ``ghost_clicked``: "Copy email" (the dock copies and says "Copied!").
    ``manage_clicked``: "Manage plan" on a subscriber's card.
    """

    primary_clicked = pyqtSignal()
    # True on show, False on hide: the dock steps Generate down to an outline
    # while the wall's offer is on screen (one filled button per screen).
    shown_changed = pyqtSignal(bool)
    ghost_clicked = pyqtSignal()
    manage_clicked = pyqtSignal()
    # The row's quiet dismiss, with the shape it closed ("low", "low_contact").
    dismissed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None, object_name: str = ""):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.state = ""
        # Minimum: a short dock must never squeeze the lines of the wall.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        card = QWidget(self)
        card.setObjectName("quotaCard")
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card.setStyleSheet(_CARD_QSS)
        self._card = card
        col = QVBoxLayout(card)
        col.setContentsMargins(*_CARD_MARGINS)
        col.setSpacing(SPACE_TIGHT)

        # The compact row: one bold sentence, a small pill beside it.
        self._row = QWidget(card)
        row = QHBoxLayout(self._row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_CARD + 2)
        self._compact = _words(self._row, _COMPACT_QSS)
        row.addWidget(self._compact, 1)
        self.compact_button = _pill(self._row, _SMALL_PRIMARY_QSS, BTN_SMALL_PX)
        self.compact_button.clicked.connect(self._on_compact_clicked)
        row.addWidget(self.compact_button, 0, Qt.AlignmentFlag.AlignVCenter)
        # A quiet dismiss for the ignorable row only; a wall never has one.
        self.dismiss_button = QToolButton(self._row)
        self.dismiss_button.setIcon(icon_for(self.dismiss_button, "close", _DISMISS_GLYPH_PX, qcolor(INK_2)))
        self.dismiss_button.setIconSize(QSize(_DISMISS_GLYPH_PX, _DISMISS_GLYPH_PX))
        self.dismiss_button.setFixedSize(BTN_SMALL_PX, BTN_SMALL_PX)
        self.dismiss_button.setAutoRaise(True)
        self.dismiss_button.setStyleSheet(BTN_ICON_QSS)
        self.dismiss_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dismiss_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.dismiss_button.setToolTip(tr("Dismiss"))
        self.dismiss_button.setAccessibleName(tr("Dismiss"))
        self.dismiss_button.clicked.connect(self._on_dismiss_clicked)
        row.addWidget(self.dismiss_button, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(self._row)

        self._title = _words(card, _TITLE_QSS)
        self._note = _words(card, HINT_QSS)
        col.addWidget(self._title)
        col.addWidget(self._note)

        # The offer: what Pro adds, then the wide primary. No benefit list.
        self._plan = QWidget(card)
        plan = QVBoxLayout(self._plan)
        plan.setContentsMargins(0, SPACE_CARD + 2, 0, 0)
        plan.setSpacing(SPACE_CARD + 2)
        self._pitch = _words(self._plan, BODY_QSS)
        plan.addWidget(self._pitch)
        self.wide_button = _pill(self._plan, BTN_PRIMARY_WIDE_QSS, BTN_PRIMARY_WIDE_PX)
        self.wide_button.clicked.connect(self.primary_clicked.emit)
        plan.addWidget(self.wide_button)
        col.addWidget(self._plan)

        # The subscriber's card: one line, then Copy email and Manage plan.
        self._body = _words(card, BODY_QSS)
        col.addWidget(self._body)
        self._actions = QWidget(card)
        actions = QHBoxLayout(self._actions)
        actions.setContentsMargins(0, SPACE_TIGHT, 0, 0)
        actions.setSpacing(SPACE_CARD)
        self.ghost_button = _pill(self._actions, _SMALL_GHOST_QSS, BTN_SMALL_PX)
        self.ghost_button.clicked.connect(self.ghost_clicked.emit)
        actions.addWidget(self.ghost_button)
        self.manage_button = _pill(self._actions, BTN_QUIET_QSS, BTN_SMALL_PX)
        self.manage_button.clicked.connect(self.manage_clicked.emit)
        actions.addWidget(self.manage_button)
        actions.addStretch(1)
        col.addWidget(self._actions)

        self._escape = _words(card, HINT_QSS, selectable=True)
        col.addWidget(self._escape)
        outer.addWidget(card)
        self._parts = (self._row, self._title, self._note, self._plan,
                       self._body, self._actions, self._escape)
        self._compact_is_ghost = False
        self.hide()

    # -- shapes ---------------------------------------------------------------

    def show_low(self, text: str, button_text: str) -> None:
        """A free account near the end of its month: one row, ignorable."""
        self.state = "low"
        self._compact.setText(text)
        self._set_compact_kind(ghost=False, text=button_text)
        self.dismiss_button.setVisible(True)
        # Still the offer's click, drawn as an outline: Generate stays the
        # screen's one filled button.
        self.compact_button.setStyleSheet(_SMALL_GHOST_QSS)
        self._show_only(self._row)

    def show_low_contact(self, text: str, button_text: str, escape: str) -> None:
        """A subscriber running low: the row, a ghost Copy email, the address."""
        self.state = "low_contact"
        self._compact.setText(text)
        self._set_compact_kind(ghost=True, text=button_text)
        self.dismiss_button.setVisible(True)
        self._escape.setText(escape)
        self._show_only(self._row, self._escape if escape else None)

    def show_wall(self, title: str, note: str, pitch: str, button_text: str, escape: str) -> None:
        """The free month is spent: the fact, the date, the offer."""
        self.state = "free_out"
        self._title.setText(title)
        self._note.setText(note)
        self._pitch.setText(pitch)
        self._pitch.setVisible(bool(pitch))
        self.wide_button.setText(button_text)
        self._escape.setText(escape)
        self._show_only(self._title, self._note if note else None, self._plan,
                        self._escape if escape else None)

    def show_contact(self, title: str, body: str, ghost_text: str, escape: str,
                     manage_text: str = "") -> None:
        """A subscriber who spent the month: a human contact, not a wall."""
        self.state = "paid_out"
        self._title.setText(title)
        self._body.setText(body)
        self.ghost_button.setText(ghost_text)
        self.ghost_button.setVisible(bool(ghost_text))
        self.manage_button.setText(manage_text)
        self.manage_button.setVisible(bool(manage_text))
        self._escape.setText(escape)
        self._show_only(self._title, self._body if body else None, self._actions,
                        self._escape if escape else None)

    def clear(self) -> None:
        self.state = ""
        self.hide()

    # -- internals ------------------------------------------------------------

    def showEvent(self, event):  # noqa: N802 - Qt signature
        super().showEvent(event)
        self.shown_changed.emit(True)

    def hideEvent(self, event):  # noqa: N802 - Qt signature
        super().hideEvent(event)
        self.shown_changed.emit(False)

    def _show_only(self, *parts) -> None:
        self._card.setStyleSheet(_card_qss(self.state))
        keep = [p for p in parts if p is not None]
        for part in self._parts:
            part.setVisible(part in keep)
        self.show()
        # A sheet set before the first show is dropped: polish it again.
        repolish_widget(self._card)

    def _set_compact_kind(self, ghost: bool, text: str) -> None:
        self._compact_is_ghost = ghost
        self.compact_button.setStyleSheet(_SMALL_GHOST_QSS if ghost else _SMALL_PRIMARY_QSS)
        self.compact_button.setText(text)

    def _on_dismiss_clicked(self) -> None:
        self.dismissed.emit(self.state)
        self.clear()

    def _on_compact_clicked(self) -> None:
        if self._compact_is_ghost:
            self.ghost_clicked.emit()
        else:
            self.primary_clicked.emit()
