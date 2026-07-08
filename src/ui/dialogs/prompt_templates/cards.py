
from __future__ import annotations

from qgis.PyQt.QtCore import QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core import telemetry
from ....core import telemetry_events as te
from ....core.config_store import get_export_copy
from ....core.i18n import tr
from ....core.prompts import prompt_history
from ...dock.style import FOCUS_RING
from .card_grid import add_row_height_filler, focus_neighbour_card, focus_out_of_grid
from .common import (
    _CARD_HOVER,
    _CARD_NORMAL,
    _STAR_BTN,
    _STAR_FILLED_SVG,
    _STAR_OUTLINE_SVG,
    CARD_HINT_QSS,
    CARD_TITLE_QSS,
    ElidedLabel,
    _build_use_hint,
    _icon,
    _set_use_hint,
    _sip,
    _truncate,
)










_CARD_FOCUS = f"QFrame#card:focus {{ border: 2px solid {FOCUS_RING}; }}"


class ElidedCardTitle(QLabel):





    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setTextFormat(QtC.PlainText)
        self.setText(text)

    def minimumSizeHint(self):  # noqa: N802

        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.width()
        )
        if shown != self.text():
            self.setText(shown)
            self.setToolTip(self._full_text if shown != self._full_text else "")




_SQUARE_BOTTOM_KW = "square_bottom"


def build_card_slider(parent):


    from ...before_after_slider import BeforeAfterSlider

    options = {"auto_loop": False, "show_badges": False, "handle_grab_only": True}
    try:
        return BeforeAfterSlider(parent, **options, **{_SQUARE_BOTTOM_KW: True})
    except TypeError:
        return BeforeAfterSlider(parent, **options)


class _StarButton(QToolButton):


    toggled_state = pyqtSignal(str, bool, str, str)


    def __init__(
        self,
        prompt: str,
        label: str | None,
        source_category: str | None,
        parent=None,
    ):
        super().__init__(parent)
        self._prompt = prompt
        self._label = label
        self._source_category = source_category
        self.setCursor(QtC.PointingHandCursor)
        self.setIconSize(QSize(16, 16))
        self.setFixedSize(28, 28)
        self.setStyleSheet(_STAR_BTN)
        self.setAutoRaise(True)
        self.clicked.connect(self._on_clicked)
        self.refresh()

    def prompt(self) -> str:
        return self._prompt

    def refresh(self):
        is_fav = prompt_history.is_favorite(self._prompt)
        if is_fav:
            self.setIcon(_icon(_STAR_FILLED_SVG))
            remove_text = get_export_copy("dialogs.cards.remove_from_favorites", tr("Remove from favorites"))
            self.setAccessibleName(remove_text)
            self.setToolTip(remove_text)
        else:
            self.setIcon(_icon(_STAR_OUTLINE_SVG))
            add_text = get_export_copy("dialogs.cards.add_to_favorites", tr("Add to favorites"))
            self.setAccessibleName(add_text)
            self.setToolTip(add_text)

    def _on_clicked(self):
        now_fav = prompt_history.toggle_favorite(
            self._prompt, self._label, self._source_category
        )
        telemetry.track(te.FAVORITE_TOGGLED, {"now_favorited": now_fav, "source": "library"})
        telemetry.flush()
        self.refresh()
        self.toggled_state.emit(
            self._prompt,
            now_fav,
            self._label or "",
            self._source_category or "",
        )


class _BeforeAfterCard(QFrame):











    CARD_WIDTH = 300
    SLIDER_WIDTH = 300
    SLIDER_HEIGHT = 175

    def __init__(
        self,
        preset: dict,
        on_click,
        demo_loader=None,
        absolute_url=None,
        parent=None,
        hint: str = "",
    ):
        super().__init__(parent)
        self.setObjectName("card")
        self._preset = preset
        self._on_click = on_click
        self.setCursor(QtC.PointingHandCursor)
        self.setStyleSheet(_CARD_NORMAL + _CARD_FOCUS)



        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(
            preset.get("label") or get_export_copy("dialogs.cards.default_accessible_name", tr("Template"))
        )


        self.setMinimumWidth(200)


        self.setSizePolicy(QtC.SizePolicyExpanding, QSizePolicy.Policy.Preferred)



        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)








        self._slider = build_card_slider(self)
        self._slider.setFixedHeight(self.SLIDER_HEIGHT)
        self._slider.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        self._slider.clicked.connect(self._emit_click)
        outer.addWidget(self._slider)





        footer_wrap = QWidget(self)
        footer_outer = QVBoxLayout(footer_wrap)
        self._star = None






        footer_outer.setContentsMargins(12, 9, 12, 11)
        footer_outer.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        label = ElidedCardTitle(_truncate(preset["label"]))
        label.setStyleSheet(CARD_TITLE_QSS)
        title_row.addWidget(label, 1)
        self._use_hint = _build_use_hint(self)
        title_row.addWidget(self._use_hint, 0, QtC.AlignVCenter)
        footer_outer.addLayout(title_row)


        hint_lbl = ElidedLabel(hint)
        hint_lbl.setStyleSheet(CARD_HINT_QSS)
        footer_outer.addWidget(hint_lbl)

        outer.addWidget(footer_wrap)
        add_row_height_filler(outer, footer_wrap)




        self._demo_loader = None
        self._tid = preset.get("id", "")
        self._pending_sides: set[str] = set()
        tid = self._tid

        if demo_loader is not None and absolute_url is not None:
            url_before = preset.get("demo_url_before")
            url_after = preset.get("demo_url_after")
            if tid and url_before:
                self._pending_sides.add("before")
            if tid and url_after:
                self._pending_sides.add("after")
            if self._pending_sides:
                self._demo_loader = demo_loader
                demo_loader.loaded.connect(self._on_demo_loaded)
                demo_loader.failed.connect(self._on_demo_failed)
            if tid and url_before:
                demo_loader.request(tid, "before", absolute_url(url_before))
            if tid and url_after:
                demo_loader.request(tid, "after", absolute_url(url_after))




        self._refresh_placeholder()

    def _on_demo_loaded(self, template_id: str, which: str, pixmap) -> None:
        if template_id != self._tid:
            return
        if which == "before":
            self._slider.set_before(pixmap)
        elif which == "after":
            self._slider.set_after(pixmap)
        self._settle_side(which)

    def _on_demo_failed(self, template_id: str, which: str) -> None:
        if template_id != self._tid:
            return
        self._settle_side(which)

    def _settle_side(self, which: str) -> None:
        self._pending_sides.discard(which)
        if not self._pending_sides:
            self._refresh_placeholder()

    def _refresh_placeholder(self) -> None:


        if not self._pending_sides:
            self._slider.set_placeholder_text(
                get_export_copy("dialogs.cards.no_preview_placeholder", tr("No preview"))
            )

    def deleteLater(self):  # noqa: N802


        if self._demo_loader is not None:
            for sig, slot in (
                (self._demo_loader.loaded, self._on_demo_loaded),
                (self._demo_loader.failed, self._on_demo_failed),
            ):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        super().deleteLater()

    def _emit_click(self):





        QTimer.singleShot(0, self._do_click)

    def _do_click(self):
        if _sip is not None and _sip.isdeleted(self):
            return
        self._on_click(self._preset)

    def star_button(self) -> _StarButton | None:
        return self._star

    def preset(self) -> dict:
        return self._preset

    def enterEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_HOVER + _CARD_FOCUS)
        _set_use_hint(self._use_hint, True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_NORMAL + _CARD_FOCUS)
        _set_use_hint(self._use_hint, self.hasFocus())
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802




        super().mousePressEvent(event)


        if event.button() == QtC.LeftButton:
            y = QtC.event_pos(event).y()
            if y >= self._slider.height():
                self._emit_click()

    def focusInEvent(self, event):  # noqa: N802

        _set_use_hint(self._use_hint, True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802
        if not self.underMouse():
            _set_use_hint(self._use_hint, False)
        super().focusOutEvent(event)

    def keyPressEvent(self, event):  # noqa: N802

        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._emit_click()
            event.accept()
            return


        if focus_neighbour_card(self, event.key()) or focus_out_of_grid(self, event.key()):
            event.accept()
            return


        event.ignore()
