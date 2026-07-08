"""Curated-template card widgets: star toggle + before/after preview card."""
from __future__ import annotations

from qgis.PyQt.QtCore import QSize, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core import telemetry
from ....core import telemetry_events as te
from ....core.i18n import tr
from ....core.prompts import prompt_history
from .common import (
    _CARD_HOVER,
    _CARD_NORMAL,
    _CARD_PROMPT_CHARS,
    _CARD_TITLE_H,
    _STAR_BTN,
    _STAR_FILLED_SVG,
    _STAR_OUTLINE_SVG,
    _build_origin_pill,
    _build_use_hint,
    _card_prompt,
    _set_use_hint,
    _sip,
    _truncate,
)

# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class _StarButton(QToolButton):
    """Favorite toggle button. Owns its prompt + meta."""

    toggled_state = pyqtSignal(str, bool, str, str)
    # prompt, now_favorited, label_or_empty, source_category_or_empty

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
            self.setIcon(QIcon(_STAR_FILLED_SVG))
            self.setAccessibleName(tr("Remove from favorites"))
            self.setToolTip(tr("Remove from favorites"))
        else:
            self.setIcon(QIcon(_STAR_OUTLINE_SVG))
            self.setAccessibleName(tr("Add to favorites"))
            self.setToolTip(tr("Add to favorites"))

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
    """Richer card: BeforeAfterSlider preview on top, label + star below.

    Used for curated templates that have ``demo_url_before`` / ``demo_url_after``.
    Clicking anywhere on the card (slider OR label) selects the preset.
    The slider's drag interaction sets the divider position but does NOT
    trigger selection - the user must click + release without dragging.
    """

    # Compact grid cell dimensions used by every Top Picks card. Sized so a
    # 3-col grid fits the default dialog content width (~880px) with breathing
    # room, and a 2-row grid never triggers a scrollbar.
    CARD_WIDTH = 300
    SLIDER_WIDTH = 300
    SLIDER_HEIGHT = 175  # ~16:9 cinematic crop

    def __init__(
        self,
        preset: dict,
        on_click,
        demo_loader=None,
        absolute_url=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("card")
        self._preset = preset
        self._on_click = on_click
        self.setCursor(QtC.PointingHandCursor)
        self.setStyleSheet(_CARD_NORMAL)
        # Flexible width so the grid columns stretch to fill the window (no
        # clipped right edge); a minimum keeps the preview readable when small.
        self.setMinimumWidth(200)
        self.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # --- slider preview ---
        # Late import to avoid Qt initialisation order issues at module load.
        from ...before_after_slider import BeforeAfterSlider

        # auto_loop=False keeps the divider parked at 50/50 by default - vital
        # when 6 cards share the page so the eye doesn't get pulled in 6
        # different directions. Each card animates only while the cursor is
        # over it (the slider already pauses on hover and respects drags).
        # No badges: match the clean Recent-card preview (the full before/after
        # detail lives in the popup the card opens).
        self._slider = BeforeAfterSlider(self, auto_loop=False, show_badges=False)
        self._slider.setFixedHeight(self.SLIDER_HEIGHT)
        self._slider.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        self._slider.clicked.connect(self._emit_click)
        outer.addWidget(self._slider)

        # --- footer block: title only ---
        # A template's name says enough at a glance; the full prompt lives in
        # the detail popup the card opens. No inline star either: favoriting
        # happens from that popup so the grid stays a clean launcher.
        footer_wrap = QWidget(self)
        footer_outer = QVBoxLayout(footer_wrap)
        self._star = None
        from_favorites = bool(preset.get("from_favorites"))

        if from_favorites:
            # Favorites: match the generation cards sharing this grid - origin
            # pill, a 2-line title block, then the use hint on its own row - so
            # every cell is the same height (1-line name vs 2-line prompt alike).
            footer_outer.setContentsMargins(10, 6, 10, 8)
            footer_outer.setSpacing(3)
            pill_row = QHBoxLayout()
            pill_row.setContentsMargins(0, 0, 0, 0)
            pill_row.addWidget(
                _build_origin_pill(self, bool(preset.get("source_category")))
            )
            pill_row.addStretch()
            footer_outer.addLayout(pill_row)

            label = QLabel(_card_prompt(preset["label"], _CARD_PROMPT_CHARS))
            label.setWordWrap(True)
            label.setFixedHeight(_CARD_TITLE_H)
            label.setAlignment(QtC.AlignLeft | QtC.AlignTop)
            label.setTextFormat(QtC.PlainText)
            # 12px (not the template grid's 13px) so a starred template and a
            # starred generation read at the same size side by side.
            label.setStyleSheet(
                "color: palette(text); font-size: 12px; font-weight: 600; "
                "background: transparent; border: none;"
            )
            footer_outer.addWidget(label)

            bottom_row = QHBoxLayout()
            bottom_row.setContentsMargins(0, 0, 0, 0)
            bottom_row.setSpacing(6)
            bottom_row.addStretch()
            self._use_hint = _build_use_hint(self)
            bottom_row.addWidget(self._use_hint)
            footer_outer.addLayout(bottom_row)
        else:
            # Templates (Top Picks, themed): one bold line says it all - compact,
            # no reserved second line. Title and use hint share a single row.
            footer_outer.setContentsMargins(10, 8, 10, 10)
            footer_outer.setSpacing(4)
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(6)
            label = QLabel(_truncate(preset["label"]))
            label.setStyleSheet(
                "color: palette(text); font-size: 13px; font-weight: 600; "
                "background: transparent; border: none;"
            )
            title_row.addWidget(label)
            title_row.addStretch()
            self._use_hint = _build_use_hint(self)
            title_row.addWidget(self._use_hint)
            footer_outer.addLayout(title_row)

        outer.addWidget(footer_wrap)

        # --- demo image loading ---
        # Server-hosted demos via `demo_loader` + `absolute_url`. The loader
        # caches bytes on disk so the second open of the library is instant.
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

        # Nothing will ever populate the slider: label it "No preview" instead
        # of leaving the default "Loading…" spinning forever, so the card still
        # reads as a normal grid cell (same shape, just no image).
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
        # Once nothing is pending, an empty slider means the demo is genuinely
        # absent (no asset seeded, or every fetch failed) - say so plainly.
        if not self._pending_sides:
            self._slider.set_placeholder_text(tr("No preview"))

    def deleteLater(self):  # noqa: N802 - Qt signature
        # Drop the demo_loader signal connections so an inflight image load
        # never tries to paint into a destroyed card.
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
        # Defer so the originating event (card mousePressEvent or the slider's
        # mouseReleaseEvent) fully unwinds before the click opens the detail
        # popup. Un-favoriting in that popup rebuilds the grid and destroys this
        # card/slider; running super() afterwards on a deleted C++ object is the
        # RuntimeError we are guarding against.
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
        self.setStyleSheet(_CARD_HOVER)
        _set_use_hint(self._use_hint, True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_NORMAL)
        _set_use_hint(self._use_hint, False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        # Run the base handler first, while this card's C++ object is still
        # alive. The click opens the detail popup, and un-favoriting there
        # rebuilds the grid and destroys this card; deferring it lets the event
        # fully unwind so nothing touches a deleted object.
        super().mousePressEvent(event)
        # Slider has its own click semantic; only fire if click hit the
        # footer area below the slider.
        if event.button() == QtC.LeftButton:
            y = QtC.event_pos(event).y()
            if y >= self._slider.height():
                self._emit_click()
