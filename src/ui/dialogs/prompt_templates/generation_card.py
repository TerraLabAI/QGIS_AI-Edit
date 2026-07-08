
from __future__ import annotations

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy
from ....core.date_format import format_smart_date
from ....core.i18n import tr
from ....core.prompts.prompt_presets import lookup_template_by_prompt
from ...dock import design_tokens as tk
from .card_grid import add_row_height_filler, focus_neighbour_card, focus_out_of_grid
from .cards import _CARD_FOCUS, build_card_slider
from .common import (
    _CARD_HOVER,
    _CARD_NORMAL,
    CARD_HINT_QSS,
    CARD_PROMPT_QSS,
    CARD_TITLE_QSS,
    ClampedLabel,
    _build_origin_pill,
    _build_use_hint,
    _set_use_hint,
    _sip,
)




_VERSION_BADGE_QSS = (
    f"QLabel#versionBadge {{ background: {tk.SURFACE}; color: {tk.INK};"
    f" border: 1px solid {tk.LINE}; border-radius: {tk.CHIP_PX // 2}px;"
    f" font-size: {tk.FONT_MICRO}px; font-weight: 600; padding: 0px 9px;"
    f" min-height: {tk.CHIP_PX - 2}px; max-height: {tk.CHIP_PX - 2}px; }}"
)


class _GenerationCard(QFrame):















    CARD_WIDTH = 300
    SLIDER_WIDTH = 300
    SLIDER_HEIGHT = 175

    def __init__(self, job, demo_loader, on_open, parent=None, *,
                 show_origin_pill=False, version_count=1, title: str = ""):
        super().__init__(parent)
        self.setObjectName("card")
        self._job = job
        self._request_id = str(job.get("request_id") or "")
        self._on_open = on_open
        self._demo_loader = demo_loader
        self._version_badge = None
        self.setCursor(QtC.PointingHandCursor)
        self.setStyleSheet(_CARD_NORMAL + _CARD_FOCUS)



        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)




        self.setMinimumWidth(200)


        self.setSizePolicy(QtC.SizePolicyExpanding, QSizePolicy.Policy.Preferred)


        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)


        self._slider = build_card_slider(self)
        self._slider.setFixedHeight(self.SLIDER_HEIGHT)
        self._slider.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        self._slider.setCursor(QtC.PointingHandCursor)
        self._slider.clicked.connect(self._emit_open)
        outer.addWidget(self._slider)





        self.set_version_count(version_count)

        footer = QWidget(self)
        footer_v = QVBoxLayout(footer)
        footer_v.setContentsMargins(12, 8, 12, 10)
        footer_v.setSpacing(4)





        prompt_raw = job.get("prompt") or ""
        template_match = lookup_template_by_prompt(prompt_raw)
        template_label = template_match[1] if template_match else ""
        named = " ".join((title or "").split())
        self.setAccessibleName(
            named or template_label or prompt_raw
            or get_export_copy("dialogs.generation_card.your_prompt_accessible_name", tr("Your prompt"))
        )


        if show_origin_pill:
            pill_row = QHBoxLayout()
            pill_row.setContentsMargins(0, 0, 0, 0)
            pill_row.addWidget(_build_origin_pill(self, bool(template_match)))
            pill_row.addStretch()
            footer_v.addLayout(pill_row)





        title_lbl = ClampedLabel(named or template_label or prompt_raw, lines=2)
        title_lbl.setStyleSheet(
            CARD_TITLE_QSS if (named or template_label) else CARD_PROMPT_QSS)
        footer_v.addWidget(title_lbl)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)
        date_text = format_smart_date(job.get("created_at") or "")
        if date_text:
            date_lbl = QLabel(date_text)
            date_lbl.setStyleSheet(CARD_HINT_QSS)
            bottom_row.addWidget(date_lbl)
        bottom_row.addStretch()
        self._use_hint = _build_use_hint(self)
        bottom_row.addWidget(self._use_hint)
        footer_v.addLayout(bottom_row)

        outer.addWidget(footer)
        add_row_height_filler(outer, footer)






        self._before_url = job.get("input_thumb_url") or job.get("input_url")
        self._after_url = job.get("output_thumb_url") or job.get("output_url")
        self._pending_sides: set[str] = set()
        self._loader_connected = False
        self._thumbs_requested = False

    def load_thumbnails(self):


        if self._thumbs_requested or self._demo_loader is None or not self._request_id:
            return
        self._thumbs_requested = True
        if self._before_url:
            self._pending_sides.add("before")
        if self._after_url:
            self._pending_sides.add("after")
        if not self._pending_sides:
            return
        self._demo_loader.loaded.connect(self._on_demo_loaded)
        self._demo_loader.failed.connect(self._on_demo_failed)
        self._loader_connected = True
        if "before" in self._pending_sides:
            self._demo_loader.request(self._request_id, "before", self._before_url)
        if "after" in self._pending_sides:
            self._demo_loader.request(self._request_id, "after", self._after_url)

    def _on_demo_loaded(self, request_id, which, pixmap):
        if request_id != self._request_id:
            return
        if which == "before":
            self._slider.set_before(pixmap)
        elif which == "after":
            self._slider.set_after(pixmap)
        self._settle_side(which)

    def _on_demo_failed(self, request_id, which):
        if request_id != self._request_id:
            return
        self._settle_side(which)

    def _settle_side(self, which: str):


        self._pending_sides.discard(which)
        if not self._pending_sides:
            self._disconnect_loader()

    def _disconnect_loader(self):
        if not self._loader_connected or self._demo_loader is None:
            return
        for sig, slot in (
            (self._demo_loader.loaded, self._on_demo_loaded),
            (self._demo_loader.failed, self._on_demo_failed),
        ):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._loader_connected = False

    def _emit_open(self):





        QTimer.singleShot(0, self._do_open)

    def _do_open(self):
        if _sip is not None and _sip.isdeleted(self):
            return
        self._on_open(self._job)

    def deleteLater(self):  # noqa: N802
        self._disconnect_loader()
        super().deleteLater()

    def set_version_count(self, n: int) -> None:



        if n <= 1:
            if self._version_badge is not None:
                self._version_badge.hide()
            return
        if self._version_badge is None:
            self._version_badge = QLabel(self)
            self._version_badge.setObjectName("versionBadge")
            self._version_badge.setAlignment(QtC.AlignCenter)

            self._version_badge.setStyleSheet(_VERSION_BADGE_QSS)


            self._version_badge.setAttribute(QtC.WA_TransparentForMouseEvents)
        self._version_badge.setText(tr("{n} versions").format(n=n))
        self._version_badge.adjustSize()
        self._version_badge.raise_()
        self._version_badge.show()
        self._position_version_badge()

    def _position_version_badge(self):
        if self._version_badge is None:
            return
        b = self._version_badge
        b.move(max(10, self.width() - b.width() - 10), 10)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._position_version_badge()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._position_version_badge()

    def enterEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_HOVER + _CARD_FOCUS)
        _set_use_hint(self._use_hint, True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_NORMAL + _CARD_FOCUS)
        _set_use_hint(self._use_hint, self.hasFocus())
        super().leaveEvent(event)

    def focusInEvent(self, event):  # noqa: N802

        _set_use_hint(self._use_hint, True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802
        if not self.underMouse():
            _set_use_hint(self._use_hint, False)
        super().focusOutEvent(event)

    def mousePressEvent(self, event):  # noqa: N802



        super().mousePressEvent(event)

        if event.button() == QtC.LeftButton:
            y = QtC.event_pos(event).y()
            if y >= self._slider.height():
                self._emit_open()

    def keyPressEvent(self, event):  # noqa: N802

        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._emit_open()
            event.accept()
            return


        if focus_neighbour_card(self, event.key()) or focus_out_of_grid(self, event.key()):
            event.accept()
            return


        event.ignore()


class _SidebarButton(QPushButton):


    def __init__(self, icon_html: str, label_html: str, parent=None):
        super().__init__(parent)
        self.setText("")
        self._label = QLabel(f"{icon_html}&nbsp;&nbsp;{label_html}")
        self._label.setTextFormat(QtC.RichText)
        self._label.setAttribute(QtC.WA_TransparentForMouseEvents)
        self._label.setStyleSheet("background: transparent; border: none; padding: 0px;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self._label)

    def set_label_html(self, icon_html: str, label_html: str):
        self._label.setText(f"{icon_html}&nbsp;&nbsp;{label_html}")
