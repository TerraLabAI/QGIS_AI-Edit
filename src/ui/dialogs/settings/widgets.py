







from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QRectF, Qt
from qgis.PyQt.QtGui import QPainter
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...dock.design_tokens import (
    ACCENT_TINT,
    ACCENT_TINT_ON,
    FIELD,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    INK_3,
    LINE,
    RADIUS_CARD,
    RADIUS_CONTROL,
    SCROLL_AREA_QSS,
    SURFACE,
    category_fill,
    category_ink,
    category_line,
    category_tint,
    gauge_category,
    qcolor,
)

PAGE_TITLE_QSS = f"font-size: 16px; font-weight: 600; color: {INK}; background: transparent;"
PAGE_SUBTITLE_QSS = f"font-size: {FONT_BODY}px; color: {INK_2}; background: transparent;"
ROW_TITLE_QSS = f"font-size: {FONT_BASE}px; color: {INK}; background: transparent;"
ROW_NOTE_QSS = f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent;"
GROUP_TITLE_QSS = f"font-size: {FONT_BODY}px; font-weight: 600; color: {INK}; background: transparent;"
SAVED_HINT_QSS = f"font-size: {FONT_HINT}px; color: {INK_2}; background: transparent;"


_GROUP_QSS = (
    f"QFrame#settingsGroup {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    "QFrame#settingsGroup QFrame#settingsRow { border: none; background: transparent; }"
    "QFrame#settingsGroup QLabel { background: transparent; border: none; }"
)
_ROW_DIVIDER_QSS = f"QFrame {{ background: {LINE}; border: none; max-height: 1px; min-height: 1px; }}"


def nav_qss(name: str, category: str | None = None) -> str:



    selected = category_tint(category, strong=True) if category else ACCENT_TINT_ON
    return (
        f"QListWidget#{name} {{ background: transparent; border: none; outline: none;"
        f" padding: 4px 6px; font-size: {FONT_BASE}px; }}"
        f"QListWidget#{name}::item {{ padding: 7px 8px; border-radius: {RADIUS_CONTROL}px;"
        f" color: {INK}; margin: 1px 0; border: none; }}"
        f"QListWidget#{name}::item:hover {{ background: {ACCENT_TINT}; }}"
        f"QListWidget#{name}::item:selected {{ background: {selected}; color: {INK}; }}"
    )


def sidebar_qss(name: str) -> str:
    return (
        f"QFrame#{name} {{ background: {FIELD}; border: none; border-right: 1px solid {LINE}; }}"
        f"QFrame#{name} QLabel {{ background: transparent; border: none; }}"
    )


class Switch(QAbstractButton):


    def __init__(self, parent=None, checked: bool = False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked()
        track = qcolor(INK)
        track.setAlphaF(0.85 if on else 0.28)
        if not self.isEnabled():
            track.setAlphaF(0.18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        knob_d = rect.height() - 6
        x = rect.right() - 3 - knob_d if on else rect.left() + 3
        knob = qcolor(SURFACE) if on else qcolor(INK)
        if not on:
            knob.setAlphaF(0.9)
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(x, rect.top() + 3, knob_d, knob_d))
        painter.end()


class SettingRow(QFrame):


    def __init__(self, title: str, note: str = "", control: QWidget | None = None,
                 parent=None, control_below: bool = False):
        super().__init__(parent)
        self.setObjectName("settingsRow")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(self) if control_below else QHBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(8 if control_below else 16)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(2)
        self.title_label = QLabel(title, self)
        self.title_label.setStyleSheet(ROW_TITLE_QSS)
        self.title_label.setWordWrap(True)
        words.addWidget(self.title_label)
        self.note_label = QLabel(note, self)
        self.note_label.setStyleSheet(ROW_NOTE_QSS)
        self.note_label.setWordWrap(True)
        self.note_label.setVisible(bool(note))
        words.addWidget(self.note_label)
        if control_below:
            outer.addLayout(words)
            if control is not None:
                outer.addWidget(control)
        else:
            outer.addLayout(words, 1)
            if control is not None:
                outer.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)

        if control is not None:
            try:
                if not control.accessibleName():
                    control.setAccessibleName(title)
            except (RuntimeError, AttributeError):
                pass

    def set_note(self, note: str) -> None:
        self.note_label.setText(note)
        self.note_label.setVisible(bool(note))


class SettingGroup(QFrame):





    def __init__(self, parent=None, category: str | None = None):
        super().__init__(parent)
        self.setObjectName("settingsGroup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        qss = _GROUP_QSS
        if category:
            qss += (f"QFrame#settingsGroup {{ background: {category_tint(category)};"
                    f" border-color: {category_line(category)}; }}")
        self._divider_qss = (
            f"QFrame {{ background: {category_line(category)}; border: none;"
            " max-height: 1px; min-height: 1px; }"
            if category else _ROW_DIVIDER_QSS)
        self.setStyleSheet(qss)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)

    def add_row(self, row: QWidget) -> QWidget:
        if self._col.count():
            line = QFrame(self)
            line.setStyleSheet(self._divider_qss)
            line.setFixedHeight(1)
            self._col.addWidget(line)
        self._col.addWidget(row)
        return row


class Page(QWidget):





    def __init__(self, title: str, subtitle: str, parent=None,
                 glyph: str = "", category: str | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QVBoxLayout()
        head.setContentsMargins(24, 22, 24, 12)
        head.setSpacing(3)
        self.title_label = QLabel(title, self)
        self.title_label.setStyleSheet(PAGE_TITLE_QSS)
        self.title_label.setWordWrap(True)
        if glyph:
            from .category_tile import category_icon_tile, tile_beside

            self.title_tile = category_icon_tile(glyph, category, self)
            head.addLayout(tile_beside(self.title_tile, self.title_label))
            head.addSpacing(4)
        else:
            head.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setStyleSheet(PAGE_SUBTITLE_QSS)
        if glyph:

            from .category_tile import TILE_PX

            self.subtitle_label.setContentsMargins(TILE_PX + 12, 0, 0, 0)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        head.addWidget(self.subtitle_label)
        outer.addLayout(head)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            SCROLL_AREA_QSS + "QScrollArea > QWidget > QWidget { background: transparent; }")
        self._body = QWidget(self._scroll)
        self.body = QVBoxLayout(self._body)
        self.body.setContentsMargins(24, 4, 24, 20)
        self.body.setSpacing(14)
        self.body.addStretch(1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)

    @property
    def body_widget(self) -> QWidget:
        return self._body

    def add(self, widget: QWidget) -> QWidget:

        self.body.insertWidget(self.body.count() - 1, widget)
        return widget

    def add_group_title(self, text: str) -> QLabel:
        label = QLabel(text, self._body)
        label.setStyleSheet(GROUP_TITLE_QSS)
        label.setContentsMargins(2, 4, 0, 0)
        return self.add(label)


def muted_label(text: str, parent=None) -> QLabel:
    label = QLabel(text, parent)
    label.setStyleSheet(ROW_NOTE_QSS)
    label.setWordWrap(True)
    return label


def make_button(text: str, qss: str, parent=None) -> QPushButton:

    button = QPushButton(text, parent)
    button.setStyleSheet(qss)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAutoDefault(False)
    button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return button


def gauge_qss(fraction_left: float) -> str:

    chunk = category_fill(gauge_category(fraction_left))
    return (
        f"QProgressBar {{ background: {FIELD}; border: none; border-radius: 3px;"
        " max-height: 6px; min-height: 6px; }"
        f"QProgressBar::chunk {{ background: {chunk}; border-radius: 3px; }}"
    )


def make_usage_bar(value: int, maximum: int, parent=None) -> QProgressBar:


    bar = QProgressBar(parent)
    total = max(int(maximum), 1)
    left = max(0, min(int(value), total))
    bar.setRange(0, total)
    bar.setValue(left)
    bar.setTextVisible(False)
    bar.setFixedHeight(6)
    bar.setStyleSheet(gauge_qss(left / total))
    return bar




_BILLING_CARD_QSS = (
    f"QFrame#billingCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    "QFrame#billingCard QLabel { background: transparent; border: none; }"
)
_BILLING_NAME_QSS = (
    f"font-size: {FONT_BODY}px; font-weight: 600;"
    " color: %s; background: transparent;"
)
_BILLING_STAT_QSS = "font-size: 26px; font-weight: 600; color: %s; background: transparent;"
_BILLING_HEADLINE_QSS = f"font-size: 15px; font-weight: 600; color: {INK}; background: transparent;"
_BILLING_STATUS_QSS = f"font-size: {FONT_BODY}px; color: {INK_2}; background: transparent;"
_BILLING_POINT_QSS = f"font-size: {FONT_BODY}px; color: {INK}; background: transparent;"
_BILLING_FOOT_QSS = f"font-size: {FONT_HINT}px; color: {INK_3}; background: transparent;"

BILLING_STACK_W = 720
_BILLING_CARD_MIN_W = 240
_BILLING_CHECK_PX = 14


class BillingCard(QFrame):




    def __init__(self, title: str, parent=None, category: str | None = None):
        super().__init__(parent)
        self._ink = category_ink(category) if category else ""
        self.setObjectName("billingCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_BILLING_CARD_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(_BILLING_CARD_MIN_W)
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(16, 14, 16, 14)
        self.column.setSpacing(6)
        self.title_label = QLabel(title, self)
        self.title_label.setStyleSheet(_BILLING_NAME_QSS % (self._ink or INK_3))
        self.title_label.setWordWrap(True)
        self.column.addWidget(self.title_label)

        self.column.addStretch(1)

    def add(self, widget: QWidget) -> QWidget:
        self.column.insertWidget(self.column.count() - 1, widget)
        return widget

    def add_layout(self, layout) -> None:
        self.column.insertLayout(self.column.count() - 1, layout)

    def _label(self, text: str, qss: str) -> QLabel:
        label = QLabel(text, self)
        label.setStyleSheet(qss)
        label.setWordWrap(True)
        return self.add(label)

    def add_stat(self, value: str, caption: str = "") -> QLabel:
        label = self._label(value, _BILLING_STAT_QSS % (self._ink or INK))
        if caption:
            self.add_status(caption)
        return label

    def add_headline(self, text: str) -> QLabel:
        return self._label(text, _BILLING_HEADLINE_QSS)

    def add_status(self, text: str) -> QLabel:
        return self._label(text, _BILLING_STATUS_QSS)

    def add_note(self, text: str) -> QLabel:
        return self._label(text, _BILLING_FOOT_QSS)

    def add_point(self, text: str, check_color: str) -> QWidget:

        from ...icons import pixmap_for

        row = QWidget(self)
        row.setMinimumHeight(26)
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(8)
        tick = QLabel(row)
        tick.setPixmap(pixmap_for(row, "check", _BILLING_CHECK_PX, qcolor(check_color)))
        tick.setFixedWidth(_BILLING_CHECK_PX + 2)
        line.addWidget(tick, 0, Qt.AlignmentFlag.AlignVCenter)
        label = QLabel(text, row)
        label.setStyleSheet(_BILLING_POINT_QSS)
        label.setWordWrap(True)
        line.addWidget(label, 1)
        return self.add(row)

    def add_action(self, button: QPushButton, qss: str) -> QPushButton:

        button.setStyleSheet(qss)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        row.addWidget(button, 1)
        self.add_layout(row)
        return button


class BillingCardRow(QWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(12)
        self._cards: list = []
        self._stacked = False
        self._viewport = None

    def add_card(self, card: QWidget) -> QWidget:
        self._cards.append(card)
        card.setParent(self)
        self._place()
        return card

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._watch_viewport()
        self._refit()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._viewport and event.type() == QEvent.Type.Resize:
            self._refit()
        return False

    def _watch_viewport(self) -> None:


        if getattr(self, "_viewport", None) is not None:
            return
        parent = self.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        self._viewport = parent.viewport() if parent is not None else None
        if self._viewport is not None:
            self._viewport.installEventFilter(self)

    def _room(self) -> int:

        viewport = getattr(self, "_viewport", None)
        if viewport is None:
            return self.width()
        left = self.mapTo(viewport.parentWidget(), self.rect().topLeft()).x() - viewport.x()

        return viewport.width() - 2 * max(0, left)

    def _refit(self) -> None:
        window = self.window()
        width = window.width() if window is not None else self.width()
        needed = sum(
            max(card.minimumWidth(), card.minimumSizeHint().width()) for card in self._cards
        )
        needed += self._grid.horizontalSpacing() * max(0, len(self._cards) - 1)
        stacked = int(width) < BILLING_STACK_W or needed > self._room()
        if stacked != self._stacked:
            self._stacked = stacked
            self._place()

    def _place(self) -> None:
        for index, card in enumerate(self._cards):
            self._grid.removeWidget(card)
            if self._stacked:
                self._grid.addWidget(card, index, 0)
            else:
                self._grid.addWidget(card, 0, index)
        columns = 1 if self._stacked else max(len(self._cards), 1)
        for column in range(max(len(self._cards), 1)):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
