"""Curated-template card widgets: star toggle + before/after preview card."""
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

# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

# Keyboard focus ring for a card, shared with _GenerationCard. Appended to
# _CARD_NORMAL / _CARD_HOVER at every swap, because setStyleSheet replaces the
# whole sheet and a rule left out of one of them disappears on hover. The hover
# state is the strong hairline on the hover step, so the 2 px FOCUS_RING (the
# plugin's single ring hue) reads as a different state under the cursor.
_CARD_FOCUS = f"QFrame#card:focus {{ border: 2px solid {FOCUS_RING}; }}"


class ElidedCardTitle(QLabel):
    """A one-line card title that ends in an ellipsis when the card is narrow.

    A plain QLabel was clipped mid-letter at a 900 px wide library ("Sharpen &
    upscale imag"). The full name moves to the tooltip whenever it is cut."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setTextFormat(QtC.PlainText)
        self.setText(text)

    def minimumSizeHint(self):  # noqa: N802 - Qt signature
        # Let the row shrink the title; the ellipsis takes over from there.
        return QSize(0, super().minimumSizeHint().height())

    def resizeEvent(self, event):  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.width()
        )
        if shown != self.text():
            self.setText(shown)
            self.setToolTip(self._full_text if shown != self._full_text else "")


# Keyword the slider takes to round only its top corners (the picture is the
# top of the card). Older slider builds lack it; the card then keeps four.
_SQUARE_BOTTOM_KW = "square_bottom"


def build_card_slider(parent):
    """The before/after preview of a library card: idle at 50/50, no badges,
    dragged only by its handle, its bottom edge square against the footer."""
    from ...before_after_slider import BeforeAfterSlider

    options = {"auto_loop": False, "show_badges": False, "handle_grab_only": True}
    try:
        return BeforeAfterSlider(parent, **options, **{_SQUARE_BOTTOM_KW: True})
    except TypeError:
        return BeforeAfterSlider(parent, **options)


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
        hint: str = "",
    ):
        super().__init__(parent)
        self.setObjectName("card")
        self._preset = preset
        self._on_click = on_click
        self.setCursor(QtC.PointingHandCursor)
        self.setStyleSheet(_CARD_NORMAL + _CARD_FOCUS)
        # The card is the only way to pick a template, so it has to be tabbable
        # and activatable. Tab focus only: a mouse click opens the preview and
        # used to leave a focus ring on the card once the preview closed.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(
            preset.get("label") or get_export_copy("dialogs.cards.default_accessible_name", tr("Template"))
        )
        # Flexible width so the grid columns stretch to fill the window (no
        # clipped right edge); a minimum keeps the preview readable when small.
        self.setMinimumWidth(200)
        # Preferred height: the cards of one grid row share the tallest one's
        # height, so a row never shows a short card next to a tall one.
        self.setSizePolicy(QtC.SizePolicyExpanding, QSizePolicy.Policy.Preferred)

        # 1 px inset: the picture sits inside the card's hairline and takes the
        # card's own top corners.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(1, 1, 1, 1)
        outer.setSpacing(0)

        # --- slider preview ---
        # auto_loop=False keeps the divider parked at 50/50 by default - vital
        # when 6 cards share the page so the eye doesn't get pulled in 6
        # different directions. Each card animates only while the cursor is
        # over it (the slider already pauses on hover and respects drags).
        # No badges: match the clean Recent-card preview (the full before/after
        # detail lives in the popup the card opens).
        self._slider = build_card_slider(self)
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
        # One footer on every page, Favorites included (ChatGPT's GPT cards):
        # the name with the chevron, then one muted line saying what it does.
        # Never the category (the page or section above names it) and no
        # Template pill: the description already tells a template from a past
        # edit, whose card shows its date instead. The full prompt lives in
        # the preview window the card opens.
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
        # Always present, even empty, so every card of a row keeps the same
        # height.
        hint_lbl = ElidedLabel(hint)
        hint_lbl.setStyleSheet(CARD_HINT_QSS)
        footer_outer.addWidget(hint_lbl)

        outer.addWidget(footer_wrap)
        add_row_height_filler(outer, footer_wrap)

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
            self._slider.set_placeholder_text(
                get_export_copy("dialogs.cards.no_preview_placeholder", tr("No preview"))
            )

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
                except (RuntimeError, TypeError):  # slot never connected or loader deleted
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
        self.setStyleSheet(_CARD_HOVER + _CARD_FOCUS)
        _set_use_hint(self._use_hint, True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_NORMAL + _CARD_FOCUS)
        _set_use_hint(self._use_hint, self.hasFocus())
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

    def focusInEvent(self, event):  # noqa: N802
        # A keyboard user sees the same "Open" cue a pointer does.
        _set_use_hint(self._use_hint, True)
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802
        if not self.underMouse():
            _set_use_hint(self._use_hint, False)
        super().focusOutEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        # Space and Return open the same detail popup a click does.
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._emit_click()
            event.accept()
            return
        # Arrow keys walk the grid, like the GPT store, and leave it at its
        # top and left edges.
        if focus_neighbour_card(self, event.key()) or focus_out_of_grid(self, event.key()):
            event.accept()
            return
        # Keys we don't handle: ignore, so Tab, Escape and the dialog's own
        # shortcuts keep working.
        event.ignore()
