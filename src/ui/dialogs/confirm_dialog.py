"""The plugin's one alert and question window, in the AI Agent's look.

AI Segmentation's ``confirm_dialog.py`` (4e52f288) on AI Edit's tokens, so
both plugins ask the same way.

Every popup that used to be a ``QMessageBox`` goes through here: a bold
title, a muted body, then the buttons on the right. One filled button per
window (green, or red when the action destroys something); the other ways out
are hairline pills; a destructive "don't save" sits alone on the left as a
quiet red word, the way ChatGPT and macOS lay it out.

The call is synchronous, like the box it replaces::

    choice = ask_choice(parent, title, body,
                        [ChoiceButton("drop", tr("Discard"), DISCARD),
                         ChoiceButton("cancel", tr("Cancel")),
                         ChoiceButton("save", tr("Save"), PRIMARY)],
                        default="save", escape="cancel")

``ask_choice`` returns the key of the button pressed. Escape and the window's
close button both answer ``escape``. Enter presses the primary only, and only
when ``default`` names it; every other button has ``autoDefault`` off, so a
focused secondary never catches Enter.

The window runs with ``exec()`` and the answer is read after it returns. It is
never delivered through a Python slot on ``finished``: that deadlocked QGIS
once in AI Segmentation, inside PyQt's sender lookup.
"""
from __future__ import annotations

from typing import NamedTuple

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ...core.i18n import tr
from ..dock import design_tokens as tokens

_BTN_PRIMARY = tokens.BTN_PRIMARY_QSS
_BTN_GHOST = tokens.BTN_GHOST_QSS
BTN_PILL_PX = tokens.BTN_PX
RADIUS_PILL = tokens.RADIUS_PILL
FONT_BASE = tokens.FONT_BASE
FONT_BODY = tokens.FONT_BODY
INK = tokens.INK
INK_2 = tokens.INK_2
INSET = tokens.INSET
LINE = tokens.LINE
SURFACE = tokens.SURFACE
RED_INK = tokens.RED_TEXT
RED_TINT = tokens.RED_TINT
RADIUS_CONTROL = tokens.RADIUS_CONTROL
ACCENT_BORDER = tokens.ACCENT_BORDER


def scale_px_length(px: int) -> int:
    return px


def scale_qss_font_px(qss: str) -> str:
    return qss


def msg_glyph_colour(tone: str) -> str:
    return {
        "warning": tokens.ORANGE,
        "error": tokens.RED,
        "success": tokens.GREEN,
    }.get(tone, tokens.LINK_INK)


# Button kinds.
PRIMARY = "primary"        # the one filled green action
DANGER = "danger"          # the one filled action, when it destroys something
SECONDARY = "secondary"    # a hairline pill (Cancel, Not now, No)
DISCARD = "discard"        # a quiet red word on the left (Don't save)

# Tones: the glyph left of the title. None paints no glyph.
INFO = "info"
WARNING = "warning"
SUCCESS = "success"
ERROR = "error"
_TONE_GLYPHS = {INFO: "sparkles", WARNING: "warning", SUCCESS: "check", ERROR: "warning"}
_GLYPH_PX = 20

_WIDTH_PX = 440
_WIDTH_MAX_PX = 600

# The native title bar carries the product name; the question is the bold line
# inside, so it is never printed twice.
_WINDOW_TITLE = "AI Edit"

_FOCUS_RING = f"QPushButton:focus {{ border: 2px solid {ACCENT_BORDER}; }}"
# The filled red's ring gives its width back through the padding and the
# height, like the token pills, so a focused button never grows by 4 px.
_FOCUS_RING_FILLED = (
    f"QPushButton:focus {{ border: 2px solid {ACCENT_BORDER}; padding: 0 14px;"
    f" min-height: {BTN_PILL_PX - 4}px; }}"
)

_DIALOG_QSS = f"QDialog#confirmDialog {{ background: {SURFACE}; }}"
_TITLE_QSS = (f"font-size: {FONT_BASE + 2}px; font-weight: 600; color: {INK};"
              " background: transparent;")
_BODY_QSS = (f"font-size: {FONT_BODY + 1}px; color: {INK_2};"
             " background: transparent;")
_DETAIL_QSS = (f"font-size: {FONT_BODY}px; color: {INK}; background: {INSET};"
               f" border: 1px solid {LINE}; border-radius: {RADIUS_CONTROL}px;"
               " padding: 8px 10px;")

# The filled red of a destructive primary. styles.py has the red outline and
# the soft red pill, but no filled one: the dock never needs it.
_BTN_DANGER_FILLED = (
    f"QPushButton {{ background-color: {tokens.DANGER_FILL}; color: {tokens.ON_DANGER};"
    f" padding: 0 16px; min-height: {BTN_PILL_PX}px; border: none;"
    f" border-radius: {RADIUS_PILL}px; font-size: {FONT_BODY}px;"
    " font-weight: 600; }"
    f"QPushButton:hover {{ background-color: {tokens.DANGER_FILL_HOVER}; }}"
    f"QPushButton:pressed {{ background-color: {tokens.DANGER_FILL_HOVER}; }}"
    # A primary that cannot run is grey, never a washed-out red (ui.md).
    f"QPushButton:disabled {{ background-color: {tokens.PRIMARY_DISABLED_FILL};"
    f" color: {tokens.PRIMARY_DISABLED_INK}; }}"
)
# The quiet destructive word: no fill, no border, red ink, a red wash under
# the pointer. Same height as the pills so the row stays level.
_BTN_DISCARD = (
    f"QPushButton {{ background: transparent; color: {RED_INK};"
    f" padding: 0 10px; min-height: {BTN_PILL_PX}px;"
    f" border: 2px solid transparent; border-radius: {RADIUS_PILL}px;"
    f" font-size: {FONT_BODY}px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {RED_TINT}; }}"
    f"QPushButton:pressed {{ background: {RED_TINT}; }}"
)

_KIND_QSS = {
    PRIMARY: _BTN_PRIMARY,
    DANGER: _BTN_DANGER_FILLED + _FOCUS_RING_FILLED,
    SECONDARY: _BTN_GHOST,
    DISCARD: _BTN_DISCARD + _FOCUS_RING,
}


# The destructive primary of a confirm window, for the windows that build
# their own layout (the typed account deletion).
DANGER_FILLED_QSS = _BTN_DANGER_FILLED + _FOCUS_RING_FILLED
# The native title bar of every AI Edit window that asks something.
CONFIRM_WINDOW_TITLE = _WINDOW_TITLE
DIALOG_SURFACE_QSS = _DIALOG_QSS


class ChoiceButton(NamedTuple):
    """One button: the key ``ask_choice`` returns, its label, its kind."""

    key: str
    label: str
    kind: str = SECONDARY


class ConfirmDialog(QDialog):
    """Title, body, optional detail, buttons. ``chosen`` holds the answer."""

    def __init__(self, parent, title: str, body: str, buttons,
                 default: str | None = None, escape: str | None = None,
                 tone: str | None = None, detail: str = "",
                 selectable: bool = False):
        super().__init__(parent)
        buttons = list(buttons)
        keys = [b.key for b in buttons]
        if escape is None:
            escape = keys[-1] if len(keys) == 1 else next(
                (b.key for b in buttons if b.kind == SECONDARY), keys[0])
        self._escape = escape
        self.chosen = escape
        self.buttons: dict[str, QPushButton] = {}

        self.setObjectName("confirmDialog")
        self.setWindowTitle(_WINDOW_TITLE)
        self.setStyleSheet(_DIALOG_QSS)
        self.setModal(True)
        self.setAccessibleName(title)
        self.setAccessibleDescription(body)
        # The dialog holds the focus when it opens, so no button shows the
        # keyboard ring before the user presses Tab.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(10)
        glyph_name = _TONE_GLYPHS.get(tone or "")
        indent = 0
        if glyph_name:
            size = scale_px_length(_GLYPH_PX)
            glyph = QLabel()
            glyph.setObjectName("confirmDialogGlyph")
            glyph.setFixedSize(size, size)
            glyph.setStyleSheet("background: transparent;")
            try:
                from ..icons import pixmap_for
                glyph.setPixmap(pixmap_for(
                    glyph, glyph_name, size, tokens.qcolor(msg_glyph_colour(tone))))
            except (ImportError, RuntimeError, AttributeError):
                glyph.hide()
            head.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
            indent = size + head.spacing()
        self.title_label = QLabel(title)
        self.title_label.setObjectName("confirmDialogTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.title_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.title_label.setStyleSheet(scale_qss_font_px(_TITLE_QSS))
        head.addWidget(self.title_label, 1)
        layout.addLayout(head)

        self.body_label = None
        if body:
            self.body_label = QLabel(body)
            self.body_label.setObjectName("confirmDialogBody")
            self.body_label.setWordWrap(True)
            self.body_label.setTextFormat(Qt.TextFormat.PlainText)
            self.body_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.body_label.setStyleSheet(scale_qss_font_px(_BODY_QSS))
            if selectable:
                self.body_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(self.body_label)
            if indent:
                layout.itemAt(layout.count() - 1).widget().setContentsMargins(
                    indent, 0, 0, 0)

        self.detail_label = None
        if detail:
            self.detail_label = QLabel(detail)
            self.detail_label.setObjectName("confirmDialogDetail")
            self.detail_label.setWordWrap(True)
            self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
            self.detail_label.setStyleSheet(scale_qss_font_px(_DETAIL_QSS))
            self.detail_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard)
            row = QHBoxLayout()
            row.setContentsMargins(indent, 4, 0, 0)
            row.addWidget(self.detail_label)
            layout.addLayout(row)

        layout.addSpacing(12)
        left = [b for b in buttons if b.kind == DISCARD]
        right = [b for b in buttons if b.kind != DISCARD]
        # The filled one goes last, at the right edge, where the eye ends.
        right.sort(key=lambda b: b.kind in (PRIMARY, DANGER))
        made = {spec.key: self._make_button(spec, default)
                for spec in left + right}

        # Buttons size to their labels. When a translation makes the row wider
        # than the window may grow, the buttons stack instead: the filled one
        # on top, the quiet discard last.
        spacing = 8
        margins = layout.contentsMargins()
        row_need = (sum(b.sizeHint().width() for b in made.values())  # win-ok: no equal stretch, sizeHint each
                    + spacing * (len(made) + (1 if left else 0))
                    + margins.left() + margins.right())
        widest = scale_px_length(_WIDTH_MAX_PX)
        self.stacked = row_need > widest
        if self.stacked:
            column = QVBoxLayout()
            column.setSpacing(spacing)
            for spec in list(reversed(right)) + left:
                column.addWidget(made[spec.key])
            layout.addLayout(column)
            width = widest
        else:
            row = QHBoxLayout()
            row.setSpacing(spacing)
            for spec in left:
                row.addWidget(made[spec.key])
            row.addStretch(1)
            for spec in right:
                row.addWidget(made[spec.key])
            layout.addLayout(row)
            width = max(scale_px_length(_WIDTH_PX), row_need)
        # A fixed width lets the title and body wrap; the height is then asked
        # of the layout for that width, since a wrapped label only knows its
        # height once it knows its width.
        self.setFixedWidth(width)
        layout.activate()
        if layout.hasHeightForWidth():
            self.setFixedHeight(layout.totalHeightForWidth(width))

    def _make_button(self, spec: ChoiceButton, default: str | None) -> QPushButton:
        btn = QPushButton(spec.label)
        btn.setObjectName(f"confirmDialogBtn_{spec.key}")
        btn.setStyleSheet(scale_qss_font_px(_KIND_QSS.get(spec.kind, _BTN_GHOST)))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Accessible name = what the button says, without the mnemonic marker.
        btn.setAccessibleName(spec.label.replace("&&", "\0").replace(
            "&", "").replace("\0", "&"))
        btn.setMinimumHeight(scale_px_length(BTN_PILL_PX))
        btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        filled = spec.kind in (PRIMARY, DANGER)
        is_default = filled and default is not None and spec.key == default
        btn.setAutoDefault(False)
        btn.setDefault(is_default)
        btn.clicked.connect(lambda _checked=False, k=spec.key: self._answer(k))
        self.buttons[spec.key] = btn
        return btn

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def reject(self) -> None:
        # Escape and the close button end here; a button press accepts.
        self.chosen = self._escape
        super().reject()

    def _answer(self, key: str) -> None:
        self.chosen = key
        self.accept()


def _parent_ok(parent):
    try:
        if parent is not None:
            parent.isVisible()  # raises RuntimeError on a deleted wrapper
        return parent
    except (RuntimeError, AttributeError):
        return None


def ask_choice(parent, title: str, body: str, buttons, default: str | None = None,
               escape: str | None = None, tone: str | None = None,
               detail: str = "", selectable: bool = False) -> str:
    """Show the window, wait, and return the key of the button pressed.

    ``escape`` is the answer for Escape and the close button; it defaults to
    the first SECONDARY button, or the only button. ``default`` is the key
    Enter presses, and only a PRIMARY or DANGER button can take it.
    """
    dlg = ConfirmDialog(_parent_ok(parent), title, body, buttons,
                        default=default, escape=escape, tone=tone,
                        detail=detail, selectable=selectable)
    try:
        dlg.exec()
        return dlg.chosen
    finally:
        dlg.deleteLater()


def info_box(parent, title: str, body: str = "", detail: str = "",
             selectable: bool = False, tone: str = INFO) -> None:
    """A message with one OK, the styled ``QMessageBox.information``."""
    ask_choice(parent, title, body, [ChoiceButton("ok", tr("OK"), PRIMARY)],
               default="ok", escape="ok", tone=tone, detail=detail,
               selectable=selectable)


def warning_box(parent, title: str, body: str = "", detail: str = "",
                selectable: bool = False) -> None:
    """A caution with one OK, the styled ``QMessageBox.warning``."""
    info_box(parent, title, body, detail=detail, selectable=selectable,
             tone=WARNING)


def error_box(parent, title: str, body: str = "", detail: str = "",
              selectable: bool = False) -> None:
    """A failure with one OK, the styled ``QMessageBox.critical``."""
    info_box(parent, title, body, detail=detail, selectable=selectable,
             tone=ERROR)


def success_box(parent, title: str, body: str = "", detail: str = "") -> None:
    """A done message with one OK."""
    info_box(parent, title, body, detail=detail, tone=SUCCESS)


def question(parent, title: str, body: str, default_yes: bool = True,
             destructive: bool = False, yes_label: str | None = None,
             no_label: str | None = None, tone: str | None = None) -> bool:
    """A yes/no question; True when the user said yes.

    Escape and the close button answer no. With ``default_yes`` False, Enter
    answers nothing, so an extra press never says yes by accident.
    """
    kind = DANGER if destructive else PRIMARY
    choice = ask_choice(
        parent, title, body,
        [ChoiceButton("no", no_label or tr("Cancel"), SECONDARY),
         ChoiceButton("yes", yes_label or tr("OK"), kind)],
        default="yes" if default_yes else None, escape="no", tone=tone)
    return choice == "yes"


__all__ = [
    "DANGER",
    "DISCARD",
    "ERROR",
    "INFO",
    "PRIMARY",
    "SECONDARY",
    "SUCCESS",
    "WARNING",
    "ChoiceButton",
    "ConfirmDialog",
    "ask_choice",
    "error_box",
    "info_box",
    "question",
    "success_box",
    "warning_box",
]
