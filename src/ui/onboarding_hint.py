













from __future__ import annotations

import html
import os
import weakref

from qgis.PyQt.QtCore import QSettings, QSize, pyqtSignal
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
from ..core.config_store import get_export_copy, get_export_dial_list
from ..core.i18n import tr




ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "resources",
    "icons",
)

_SETTINGS_PREFIX = "AIEdit/hints/"


HINT_LIBRARY_INTRO = "library_intro"
HINT_MARKUP = "flow_markup"
HINT_VECTORIZE = "flow_vectorize"


HINT_REFERENCE = "flow_reference"

HINT_FIRST_STEPS = "first_steps"




HINT_GUIDE_AI = "guide_ai"


HINT_SEG_CROSS = "seg_cross_promo"






HINT_MARKUP_PROMPT = "markup_prompt_tip"
ALL_HINTS = [
    HINT_LIBRARY_INTRO, HINT_MARKUP, HINT_VECTORIZE, HINT_FIRST_STEPS,
    HINT_GUIDE_AI, HINT_SEG_CROSS, HINT_MARKUP_PROMPT, HINT_REFERENCE,
]











SESSION_ONLY_HINTS = frozenset({
    HINT_GUIDE_AI, HINT_MARKUP_PROMPT, HINT_MARKUP, HINT_REFERENCE,
})
_SESSION_DISMISSED: set[str] = set()









GREEN_TINT = (139, 172, 39)
BLUE_TINT = (25, 118, 210)


NEUTRAL_TINT = (128, 132, 138)





_LIVE_HINTS: list[weakref.ref[QWidget]] = []




GUIDE_LINK_HREF = "terralab:guide"


def build_guide_link_html(text: str) -> str:












    from .dock.design_tokens import LINK_INK

    return (
        f'<a href="{GUIDE_LINK_HREF}" style="color: {LINK_INK};'
        f' text-decoration: none;">{html.escape(text)} ↗</a>'
    )







GUIDE_ANCHOR_MARKUP = "mark-up"




GUIDE_ANCHOR_REFERENCE = "reference-images-and-layers"


def guide_url(content: str, anchor: str = "") -> str:






    from ..core.auth.activation_manager import get_guide_url

    base = get_guide_url()
    sep = "&" if "?" in base else "?"
    url = (
        f"{base}{sep}utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit"
        f"&utm_content={content}"
    )
    return f"{url}#{anchor}" if anchor else url


def open_guide(content: str, anchor: str = "") -> None:







    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl(guide_url(content, anchor)))
    try:
        from ..core import telemetry
        from ..core import telemetry_events as te
        telemetry.track(te.TUTORIAL_OPENED, {"tutorial_source": content})
    except Exception:  # nosec B110
        pass


def is_hint_dismissed(hint_id: str) -> bool:










    if hint_id in SESSION_ONLY_HINTS:
        if hint_id in _SESSION_DISMISSED:
            return True
    elif bool(QSettings().value(_SETTINGS_PREFIX + hint_id, False, type=bool)):
        return True
    return hint_id in get_export_dial_list("hints.suppressed", ())


def dismiss_hint(hint_id: str) -> None:
    if hint_id in SESSION_ONLY_HINTS:
        _SESSION_DISMISSED.add(hint_id)
        return
    QSettings().setValue(_SETTINGS_PREFIX + hint_id, True)


def reset_hints() -> None:





    s = QSettings()
    for hint_id in ALL_HINTS:
        s.remove(_SETTINGS_PREFIX + hint_id)
    _SESSION_DISMISSED.clear()
    for ref in list(_LIVE_HINTS):
        widget = ref()
        if widget is None:
            _LIVE_HINTS.remove(ref)
            continue
        reshow = getattr(widget, "reshow", None)
        if callable(reshow):
            reshow()
            continue
        hint_id = getattr(widget, "_guidance_hint_id", "")
        widget.setVisible(bool(hint_id) and not is_hint_dismissed(hint_id))


def register_hint_widget(widget: QWidget, hint_id: str) -> None:







    widget._guidance_hint_id = hint_id
    widget.setVisible(not is_hint_dismissed(hint_id))
    _LIVE_HINTS.append(weakref.ref(widget))


def _hint_styles() -> dict:



    from .dock import design_tokens as t

    return {
        "card": (
            f"QFrame#hintCard {{ background: {t.SURFACE}; border: 1px solid {t.LINE_STRONG};"
            f" border-radius: {t.RADIUS_BOX}px; }}"
        ),
        "title": (
            f"color: {t.INK}; font-size: {t.FONT_BASE}px; font-weight: 600;"
            " background: transparent; border: none;"
        ),
        "body": (
            f"color: {t.INK_2}; font-size: {t.FONT_BODY}px;"
            " background: transparent; border: none;"
        ),
        "close": (
            "QToolButton { background: transparent; border: 1px solid transparent; padding: 0;"
            f" border-radius: {t.RADIUS_CHIP}px; }}"
            f"QToolButton:hover {{ background: {t.HOVER}; }}"
            f"QToolButton:pressed {{ background: {t.HOVER_ON}; }}"
            f"QToolButton:focus {{ border-color: {t.ACCENT_BORDER}; }}"
        ),


        "action": (
            f"QToolButton {{ background: {t.SURFACE}; color: {t.INK};"
            f" border: 1px solid {t.LINE_STRONG}; border-radius: {t.RADIUS_PILL_SMALL}px;"
            f" padding: 0 12px; min-height: {t.BTN_SMALL_PX - 2}px;"
            f" font-size: {t.FONT_BODY}px; font-weight: 500; }}"
            f"QToolButton:hover {{ background: {t.HOVER}; border-color: {t.INK_3}; }}"
            f"QToolButton:pressed {{ background: {t.HOVER_ON}; }}"
            f"QToolButton:focus {{ border-color: {t.ACCENT_BORDER}; }}"
        ),
        "step": (
            f"QFrame {{ background: {t.INSET}; border: 1px solid {t.LINE};"
            f" border-radius: {t.RADIUS_CARD}px; }}"
        ),
        "step_num": (
            f"color: {t.ON_ACCENT}; background: {t.ACCENT}; border-radius: 11px;"
            f" font-size: {t.FONT_BODY}px; font-weight: 700;"
        ),
        "step_title": (
            f"color: {t.INK}; font-size: {t.FONT_BODY}px; font-weight: 600;"
            " background: transparent; border: none;"
        ),
        "step_sub": (
            f"color: {t.INK_2}; font-size: {t.FONT_HINT}px;"
            " background: transparent; border: none;"
        ),
    }





_TINT_CATEGORIES = {GREEN_TINT: "leaf", BLUE_TINT: "sky"}
_HINT_TILE_PX = 22
_HINT_TILE_GLYPH_PX = 14


def _hint_category(tint: tuple[int, int, int]) -> tuple[str, bool]:

    category = _TINT_CATEGORIES.get(tuple(tint))
    return (category, True) if category else ("leaf", False)


def _hint_card_qss(category: str, tinted: bool) -> str:
    from .dock import design_tokens as t

    base = _hint_styles()["card"]
    if not tinted:
        return base
    return base + (
        f"QFrame#hintCard {{ background: {t.category_tint(category)};"
        f" border-color: {t.category_line(category)}; }}"
    )


def _hint_glyph_tile(parent: QWidget, category: str) -> QLabel:

    from .dock import design_tokens as t
    from .icons import pixmap_for

    tile = QLabel(parent)
    tile.setObjectName("hintTile")
    tile.setFixedSize(_HINT_TILE_PX, _HINT_TILE_PX)
    tile.setAlignment(QtC.AlignCenter)
    tile.setStyleSheet(
        f"QLabel#hintTile {{ background: {t.category_tint(category, strong=True)};"
        f" border: none; border-radius: {_HINT_TILE_PX // 2}px; }}"
    )
    tile.setPixmap(pixmap_for(tile, "sparkles", _HINT_TILE_GLYPH_PX, t.qcolor(t.category_ink(category))))
    return tile


class DismissibleHint(QWidget):














    dismissed = pyqtSignal()
    action = pyqtSignal()
    link_activated = pyqtSignal()

    def __init__(
        self,
        hint_id: str,
        title: str,
        body: str,
        steps: list[tuple[str, str, str]] | None = None,
        action_text: str | None = None,
        rich_body: bool = False,
        visibility_gate=None,
        tint: tuple[int, int, int] | None = None,
        action_color: tuple[int, int, int] | None = None,
        link_text: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._hint_id = hint_id




        self._visibility_gate = visibility_gate
        tint = tint or GREEN_TINT
        category, tinted = _hint_category(tint)
        styles = _hint_styles()
        from .dock.design_tokens import BTN_SMALL_PX, INK_2, qcolor
        from .icons import icon_for

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setObjectName("hintCard")
        card.setAttribute(QtC.WA_StyledBackground, True)
        card.setStyleSheet(_hint_card_qss(category, tinted))
        outer.addWidget(card)

        col = QVBoxLayout(card)
        col.setContentsMargins(12, 10, 8, 12)
        col.setSpacing(4)

        close_btn = QToolButton(card)
        close_btn.setIcon(icon_for(close_btn, "close", 14, qcolor(INK_2)))
        close_btn.setIconSize(QSize(14, 14))
        close_btn.setAccessibleName(
            get_export_copy("widgets.onboarding_hint.hide_tip_tooltip", tr("Hide this tip")))
        close_btn.setToolTip(
            get_export_copy("widgets.onboarding_hint.hide_tip_tooltip", tr("Hide this tip")))
        close_btn.setCursor(QtC.PointingHandCursor)
        close_btn.setStyleSheet(styles["close"])
        close_btn.setFixedSize(BTN_SMALL_PX, BTN_SMALL_PX)
        close_btn.clicked.connect(self._on_close)

        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setStyleSheet(styles["body"])



        as_html = rich_body or bool(link_text)
        body_lbl.setTextFormat(QtC.RichText if as_html else QtC.PlainText)
        if link_text:


            head_html = body if rich_body else html.escape(body)
            body_lbl.setText(f"{head_html}<br>{build_guide_link_html(link_text)}")


            body_lbl.setOpenExternalLinks(False)
            body_lbl.setTextInteractionFlags(QtC.LinksAccessibleByMouse)
            body_lbl.linkActivated.connect(self._on_link_activated)

        act_btn = None
        if action_text:
            act_btn = QToolButton(card)
            act_btn.setText(action_text)
            act_btn.setCursor(QtC.PointingHandCursor)



            act_btn.setStyleSheet(styles["action"])
            act_btn.clicked.connect(self.action.emit)

        if title:
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            head.addWidget(_hint_glyph_tile(card, category), 0, QtC.AlignVCenter)
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet(styles["title"])
            title_lbl.setWordWrap(True)
            head.addWidget(title_lbl, 1, QtC.AlignVCenter)
            head.addWidget(close_btn, 0, QtC.AlignTop)
            col.addLayout(head)
            body_lbl.setContentsMargins(_HINT_TILE_PX + 8, 0, 16, 0)
            col.addWidget(body_lbl)
            if act_btn is not None:
                col.addSpacing(4)
                act_row = QHBoxLayout()
                act_row.setContentsMargins(_HINT_TILE_PX + 8, 0, 0, 0)
                act_row.addWidget(act_btn, 0, QtC.AlignLeft)
                act_row.addStretch(1)
                col.addLayout(act_row)
        else:





            col.setContentsMargins(12, 6, 6, 6)
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(8)
            head.addWidget(_hint_glyph_tile(card, category), 0, QtC.AlignVCenter)
            head.addWidget(body_lbl, 1, QtC.AlignVCenter)
            if act_btn is not None:
                head.addWidget(act_btn, 0, QtC.AlignVCenter)
            head.addWidget(close_btn, 0, QtC.AlignVCenter)
            col.addLayout(head)

        if steps:
            row = QHBoxLayout()
            row.setContentsMargins(0, 4, 0, 0)
            row.setSpacing(8)
            for i, (glyph, step_title, step_sub) in enumerate(steps, start=1):
                row.addWidget(self._step(i, glyph, step_title, step_sub), 1)
            col.addLayout(row)

        self.setVisible(not is_hint_dismissed(hint_id))
        _LIVE_HINTS.append(weakref.ref(self))

    def _step(self, n: int, glyph: str, title: str, sub: str) -> QFrame:
        box = QFrame(self)
        styles = _hint_styles()
        box.setStyleSheet(styles["step"])
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(9)
        num = QLabel(str(n))
        num.setFixedSize(22, 22)
        num.setAlignment(QtC.AlignCenter)
        num.setStyleSheet(styles["step_num"])
        h.addWidget(num, 0)
        txt = QVBoxLayout()
        txt.setContentsMargins(0, 0, 0, 0)
        txt.setSpacing(1)
        t = QLabel(f"{glyph}  {title}" if glyph else title)
        t.setStyleSheet(styles["step_title"])
        txt.addWidget(t)
        if sub:
            s = QLabel(sub)
            s.setWordWrap(True)
            s.setStyleSheet(styles["step_sub"])
            txt.addWidget(s)
        h.addLayout(txt, 1)
        return box

    def reshow(self) -> None:










        if is_hint_dismissed(self._hint_id):
            return
        gate = self._visibility_gate
        if gate is not None:
            try:
                if not gate():
                    return
            except Exception:  # nosec B110
                pass
        self.show()

    def _on_close(self) -> None:
        dismiss_hint(self._hint_id)
        self.hide()
        self.dismissed.emit()

    def _on_link_activated(self, href: str) -> None:





        if href == GUIDE_LINK_HREF:
            self.link_activated.emit()


def search_icon() -> QIcon:

    return QIcon(os.path.join(ICONS_DIR, "search.svg"))
