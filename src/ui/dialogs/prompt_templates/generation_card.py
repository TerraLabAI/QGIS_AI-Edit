"""Past-generation card and the sidebar tab button."""
from __future__ import annotations

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.date_format import format_smart_date
from ....core.i18n import tr
from ....core.prompts.prompt_presets import lookup_template_by_prompt
from .common import (
    _CARD_HOVER,
    _CARD_NORMAL,
    _CARD_PROMPT_CHARS,
    _CARD_TITLE_H,
    _build_origin_pill,
    _build_use_hint,
    _card_prompt,
    _set_use_hint,
    _sip,
)


class _GenerationCard(QFrame):
    """One past generation: clean before/after slider preview + title.

    A click anywhere opens the detail popup (full before/after, reference
    images, metadata, and the Use / Add to map / Download actions). The card
    itself stays a calm preview - no inline action buttons.

    Favoriting lives in the detail popup the card opens (consistent with
    curated templates), so the card carries no inline star and does NOT join
    the dialog's _card_widgets list.

    Callback: on_open(job). ``show_origin_pill`` adds a Template / Your prompt
    pill (used in the unified Favorites tab to tell card origins apart).
    """

    CARD_WIDTH = 300
    SLIDER_WIDTH = 300
    SLIDER_HEIGHT = 175

    def __init__(self, job, demo_loader, on_open, parent=None, *,
                 show_origin_pill=False, version_count=1):
        super().__init__(parent)
        self.setObjectName("card")
        self._job = job
        self._request_id = str(job.get("request_id") or "")
        self._on_open = on_open
        self._demo_loader = demo_loader
        self._version_badge = None
        self.setCursor(QtC.PointingHandCursor)
        self.setStyleSheet(_CARD_NORMAL)
        # Size to content (like the template cards) so the footer never leaves a
        # dead gap below the title/date. The 175px image caps every card, so the
        # grid stays aligned at the image even when footers differ (1-line
        # template name vs 2-line custom prompt, optional origin pill).
        self.setMinimumWidth(200)
        self.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Before/after wipe slider (badges hidden - keeps just the divider).
        from ...before_after_slider import BeforeAfterSlider

        self._slider = BeforeAfterSlider(self, auto_loop=False, show_badges=False)
        self._slider.setFixedHeight(self.SLIDER_HEIGHT)
        self._slider.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        self._slider.setCursor(QtC.PointingHandCursor)
        self._slider.clicked.connect(self._emit_open)
        outer.addWidget(self._slider)

        # Iteration session collapsed into one card: a corner badge shows how
        # many versions share this session. Opening the card restores the whole
        # session, so the user picks the exact version in the strip. Built here,
        # refreshable later: older siblings can land on a later history page.
        self.set_version_count(version_count)

        footer = QWidget(self)
        footer_v = QVBoxLayout(footer)
        footer_v.setContentsMargins(10, 6, 10, 8)
        footer_v.setSpacing(3)

        # A generation counts as a template only when its prompt still matches a
        # curated template verbatim (whitespace + language normalized). If the
        # user edited the prompt the match fails, so an edited template reads as
        # a custom prompt - it shows the prompt itself, not the template name.
        prompt_raw = job.get("prompt") or ""
        template_match = lookup_template_by_prompt(prompt_raw)
        template_label = template_match[1] if template_match else ""

        # Optional origin pill (unified Favorites tab): Template vs Your prompt.
        if show_origin_pill:
            pill_row = QHBoxLayout()
            pill_row.setContentsMargins(0, 0, 0, 0)
            pill_row.addWidget(_build_origin_pill(self, bool(template_match)))
            pill_row.addStretch()
            footer_v.addLayout(pill_row)

        if template_label:
            # Template: the name says it all, bold. Wraps to a second line if
            # long; the fixed-height block below keeps it aligned with prompts.
            title_lbl = QLabel(_card_prompt(template_label, _CARD_PROMPT_CHARS))
            title_lbl.setWordWrap(True)
            title_lbl.setStyleSheet(
                "color: palette(text); font-size: 12px; font-weight: 600; "
                "background: transparent; border: none;"
            )
        else:
            # Custom or edited prompt: the prompt is the body, plain weight,
            # cleanly truncated (never a bold clipped heading).
            title_lbl = QLabel(_card_prompt(prompt_raw, _CARD_PROMPT_CHARS))
            title_lbl.setWordWrap(True)
            title_lbl.setStyleSheet(
                "color: palette(text); font-size: 12px; font-weight: 400; "
                "background: transparent; border: none;"
            )
        # Reserve two lines on every card so 1-line names and 2-line prompts are
        # the same height (top-aligned so a short name sits at the top).
        title_lbl.setFixedHeight(_CARD_TITLE_H)
        title_lbl.setAlignment(QtC.AlignLeft | QtC.AlignTop)
        title_lbl.setTextFormat(QtC.PlainText)
        footer_v.addWidget(title_lbl)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)
        date_text = format_smart_date(job.get("created_at") or "")
        if date_text:
            date_lbl = QLabel(date_text)
            date_lbl.setStyleSheet(
                "color: rgba(128,128,128,0.85); font-size: 10px; "
                "background: transparent; border: none;"
            )
            bottom_row.addWidget(date_lbl)
        bottom_row.addStretch()
        self._use_hint = _build_use_hint(self)
        bottom_row.addWidget(self._use_hint)
        footer_v.addLayout(bottom_row)

        outer.addWidget(footer)

        # Preview loads lazily (only when the card scrolls into view) so
        # opening Recent never downloads dozens of images at once. Prefer the
        # small server thumbnails (~30 KB) over the full-res images so a grid of
        # 4K generations stays light and cheap to stream; fall back to the full
        # image for older generations that predate thumbnails.
        self._before_url = job.get("input_thumb_url") or job.get("input_url")
        self._after_url = job.get("output_thumb_url") or job.get("output_url")
        self._pending_sides: set[str] = set()
        self._loader_connected = False
        self._thumbs_requested = False

    def load_thumbnails(self):
        """Fetch the before/after images. Idempotent; safe to call from the
        scroll handler on every tick. Cached on disk by request_id."""
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
        """Drop a resolved side; once both settle, unhook from the shared
        loader so it stops notifying this card."""
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
        # Defer so the originating event (this card's mousePressEvent or the
        # slider's mouseReleaseEvent) fully unwinds before the modal detail
        # popup opens. Removing a favorite inside that popup rebuilds the grid
        # and destroys this card mid-event; opening from a clean stack means the
        # later teardown never lands on a C++ object still on the call stack.
        QTimer.singleShot(0, self._do_open)

    def _do_open(self):
        if _sip is not None and _sip.isdeleted(self):
            return
        self._on_open(self._job)

    def deleteLater(self):  # noqa: N802 - Qt signature
        self._disconnect_loader()
        super().deleteLater()

    def set_version_count(self, n: int) -> None:
        """Show/update the 'N versions' badge. Created lazily so a card that first
        rendered as a singleton (n==1, no badge) can still grow a badge when older
        siblings of its session arrive on a later history page; hidden when n<=1."""
        if n <= 1:
            if self._version_badge is not None:
                self._version_badge.hide()
            return
        if self._version_badge is None:
            self._version_badge = QLabel(self)
            self._version_badge.setStyleSheet(
                "QLabel { background: rgba(0,0,0,0.66); color: white; "
                "font-size: 10px; font-weight: 600; border-radius: 9px; "
                "padding: 2px 8px; }"
            )
            # Let clicks through to the card so the badge corner isn't a dead
            # zone (the card opens on click anywhere).
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
        b.move(max(8, self.width() - b.width() - 8), 8)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._position_version_badge()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._position_version_badge()

    def enterEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_HOVER)
        _set_use_hint(self._use_hint, True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_NORMAL)
        _set_use_hint(self._use_hint, False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        # Run the base handler first, while this card is still alive; the open
        # is deferred (see _emit_open) so order no longer matters, but keeping
        # super() first matches _BeforeAfterCard and is robust by construction.
        super().mousePressEvent(event)
        # Footer (below the slider) is also clickable - opens the detail popup.
        if event.button() == QtC.LeftButton:
            y = QtC.event_pos(event).y()
            if y >= self._slider.height():
                self._emit_open()


class _SidebarButton(QPushButton):
    """Sidebar tab entry: colored HTML icon + label (+ optional count badge)."""

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
