"""Prompt Library dialog.

Tab-style navigation: clicking a sidebar entry swaps the right pane.
Sidebar order: Favorites → Recent → (separator) → Top Picks → themed
categories. The user's own lists (Favorites, Recent) sit at the top; the
curated catalog follows. The themed categories are long (13 métiers), so only
the first few show by default and the rest collapse behind a "show more"
toggle. The dialog still opens on Top Picks regardless of sidebar order.

Recent and Favorites are the user's own past generations, fetched from the
server in the background: each renders as a before/after card carrying the
prompt, location, and signed input/output URLs. From a card the user can reuse
the prompt, add the output back to the map as a georeferenced layer, download
it, or star it. Top Picks and the themed categories stay curated prompt cards.

Generation favorites (the ★ on a Recent/Favorites card) are a separate concept
from prompt favorites (the ★ on a curated template) and sync to their own
endpoint.
"""
from __future__ import annotations

import os

try:  # SIP comes packaged with both PyQt5 and PyQt6 - used to detect dead C++ objects.
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover - defensive only
    _sip = None

from qgis.PyQt.QtCore import QPoint, QSize, QThread, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QGuiApplication, QIcon
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.date_format import format_smart_date
from ...core.i18n import tr
from ...core.logger import log_debug, log_warning
from ...core.prompts import prompt_history
from ...core.prompts.prompt_presets import (
    _CATEGORY_ORDER,
    get_all_categories,
    lookup_template_by_prompt,
)
from ..onboarding_hint import (
    HINT_LIBRARY_INTRO,
    DismissibleHint,
    is_hint_dismissed,
    search_icon,
)


def _is_alive(obj) -> bool:
    """True when the underlying Qt C++ object is still alive."""
    if obj is None:
        return False
    if _sip is None:
        return True
    try:
        return not _sip.isdeleted(obj)
    except (TypeError, RuntimeError):
        return False


# Themed categories show every reliable card up front; curation is enforced
# by `experimental: true` on fragile presets, which are gated behind their
# own amber disclosure button. There's no second "Show N more" reveal.


def _split_experimental(presets: list[dict]) -> tuple[list[dict], list[dict]]:
    """Partition a category's presets into (reliable, experimental).

    Experimental presets are server-flagged templates that Nano Banana 2
    hallucinates on often (NDVI maps, individual-instance counting, watershed
    delineation, etc). The dialog renders them behind a separate disclosure
    so the curated default view stays trustworthy."""
    reliable: list[dict] = []
    experimental: list[dict] = []
    for p in presets:
        if p.get("experimental"):
            experimental.append(p)
        else:
            reliable.append(p)
    return reliable, experimental


_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
_ICONS_DIR = os.path.join(_PLUGIN_DIR, "resources", "icons")
_HISTORY_SVG = os.path.join(_ICONS_DIR, "history.svg")
_STAR_OUTLINE_SVG = os.path.join(_ICONS_DIR, "star.svg")
_STAR_FILLED_SVG = os.path.join(_ICONS_DIR, "star-filled.svg")
_TROPHY_SVG = os.path.join(_ICONS_DIR, "trophy.svg")

# QIcon parses the SVG on construction; memoize so a 50-card gallery doesn't
# re-read the same few files 150 times. QIcon is implicitly shared, so handing
# the same instance to many buttons is safe.
_ICON_CACHE: dict = {}


def _icon(path: str) -> QIcon:
    ic = _ICON_CACHE.get(path)
    if ic is None:
        ic = QIcon(path)
        _ICON_CACHE[path] = ic
    return ic


# ---------------------------------------------------------------------------
# QSS
# ---------------------------------------------------------------------------
_SIDEBAR_ITEM = (
    "QPushButton { text-align: left; border: none; border-radius: 4px; "
    "padding: 10px 10px; font-size: 13px; color: palette(text); "
    "background: transparent; }"
    "QPushButton:hover { background: rgba(128,128,128,0.12); }"
)

_SIDEBAR_ITEM_ACTIVE = (
    "QPushButton { text-align: left; border: none; border-radius: 4px; "
    "padding: 10px 10px; font-size: 13px; font-weight: bold; "
    "color: palette(text); background: rgba(128,128,128,0.18); }"
)

# The category expander is deliberately NOT styled like a sidebar item: it sits
# at a smaller size in muted grey with the chevron inline next to the text (no
# icon-column glyph), so it reads as a "show more" control rather than another
# category in the list (#128).
_CATEGORY_TOGGLE_BTN = (
    "QPushButton { text-align: left; border: none; border-radius: 4px; "
    "padding: 8px 12px; font-size: 11px; color: rgba(128,128,128,0.9); "
    "background: transparent; }"
    "QPushButton:hover { background: rgba(128,128,128,0.10); "
    "color: palette(text); }"
)

_SEARCH_BOX = (
    "QLineEdit { border: 1px solid rgba(128,128,128,0.3); "
    "border-radius: 4px; padding: 6px 10px; font-size: 13px; "
    "color: palette(text); background: palette(base); }"
)

# Cards read as clickable tiles: a clearly visible frame at rest, then a
# leaf-green lift on hover (same green as the prompt-row chips). Paired with the
# footer "Use" hint below so the affordance is legible even before hovering.
_CARD_NORMAL = (
    "QFrame#card { border: 1px solid rgba(128,128,128,0.30); "
    "border-radius: 6px; background: rgba(128,128,128,0.05); }"
)

_CARD_HOVER = (
    "QFrame#card { border: 1px solid rgba(139,172,39,0.75); "
    "border-radius: 6px; background: rgba(139,172,39,0.09); }"
)

# Right-aligned click affordance on every card footer: a faint chevron at rest
# that becomes a green "Use ->" on hover. Swapped by each card's enter/leave.
_USE_HINT_REST = (
    "QLabel { color: rgba(128,128,128,0.60); font-size: 13px; font-weight: 700; "
    "background: transparent; border: none; }"
)
_USE_HINT_HOVER = (
    "QLabel { color: #4d7c0f; font-size: 12px; font-weight: 700; "
    "background: transparent; border: none; }"
)


def _build_use_hint(parent) -> QLabel:
    hint = QLabel("›", parent)  # › chevron
    hint.setStyleSheet(_USE_HINT_REST)
    hint.setAttribute(QtC.WA_TransparentForMouseEvents)
    return hint


def _set_use_hint(hint: QLabel, hovered: bool) -> None:
    if hovered:
        hint.setText(f"{tr('Use')} →")  # Use →
        hint.setStyleSheet(_USE_HINT_HOVER)
    else:
        hint.setText("›")  # ›
        hint.setStyleSheet(_USE_HINT_REST)


_STAR_BTN = (
    "QToolButton { background: transparent; border: none; padding: 4px; "
    "border-radius: 4px; }"
    "QToolButton:hover { background: rgba(128,128,128,0.18); }"
)

_EMPTY_MSG = (
    "QLabel { color: palette(text); font-size: 12px; "
    "background: transparent; border: none; }"
)

_LOAD_MORE_BTN = (
    "QPushButton { background: transparent; "
    "border: 1px solid rgba(128,128,128,0.3); border-radius: 4px; "
    "padding: 8px 14px; font-size: 12px; color: palette(text); }"
    "QPushButton:hover { background: rgba(128,128,128,0.12); "
    "border-color: rgba(128,128,128,0.5); }"
)

# Neutral disclosure button + header for the experimental section. It used to
# be amber/goldenrod, which read as an error or warning and undercut trust in
# the whole library (#128). The reveal is just an "advanced" affordance, so it
# now matches the Load-more button's calm grey; the word "experimental" carries
# the caution on its own.
_EXPERIMENTAL_BTN = _LOAD_MORE_BTN

_EXPERIMENTAL_HEADER = (
    "QLabel { color: rgba(128,128,128,0.9); font-size: 11px; font-weight: 600; "
    "background: transparent; border: none; padding: 4px 2px 0px 2px; "
    "letter-spacing: 0.5px; }"
)

# Small rounded pill marking a Favorites entry's origin (curated template vs
# the user's own saved prompt). Matches the design-system "Category Pill".
_ORIGIN_PILL = (
    "QLabel { background: rgba(128,128,128,0.10); border-radius: 9px; "
    "padding: 1px 8px; font-size: 10px; color: palette(text); }"
)

# Sidebar tab order. Themed tabs are sourced from `_CATEGORY_ORDER` so the
# data facade and the sidebar can't drift; the dialog only owns the synthetic
# wrapper (Favorites, Recent, separator, Top Picks). "__separator__" inserts
# a visual divider. The user's own lists lead; the curated catalog follows.
_TAB_ORDER = [
    "user_favorites",   # Favorites (personal)
    "recent",           # Recent (personal)
    "__separator__",
    "favorites",        # Top Picks (curated)
    *_CATEGORY_ORDER,   # 13 themed métiers - first few shown, rest collapsed
]

# Tabs whose count is shown as "(N)" next to the label.
_TABS_WITH_COUNT = {"recent", "user_favorites"}

# Themed category keys (the long "métiers" list). The sidebar shows only the
# first _SIDEBAR_VISIBLE_CATEGORIES of these up front and tucks the rest behind
# a "show more" toggle so the 13-deep list doesn't overwhelm the panel (#128).
_CATEGORY_KEYS = set(_CATEGORY_ORDER)
_SIDEBAR_VISIBLE_CATEGORIES = 5

# Recent/Favorites galleries show this many generations first; the rest reveal
# in batches behind a "Show more" button so the page stays light. 9 = a full
# 3x3 grid per batch.
_GALLERY_PAGE_SIZE = 9

# Sidebar glyph: Recent + Top Picks use an SVG image, others use Unicode with a tint.
# Mirror `prompt_presets._CATEGORY_META` so every category in _TAB_ORDER has a glyph.
_SIDEBAR_GLYPHS = {
    "user_favorites": ("☆", "#e57373"),
    "cartography": ("❖", "#9880b0"),
    "landcover": ("◉", "#68a868"),
    "segment": ("▣", "#b07878"),
    "climate": ("⛅", "#5ca0c0"),
    "urban": ("⌂", "#b08858"),
    "energy": ("☀", "#d4a548"),
    "cleanup": ("⌫", "#a0a058"),
    "presentation": ("❀", "#c08fa0"),
    "forestry": ("✺", "#4d8c3f"),
    "agriculture": ("✿", "#c4a548"),
    "archaeology": ("⛏", "#9b7a4f"),
    "geology": ("◈", "#8c6a4b"),
    "hydrology": ("≈", "#3b8fb0"),
}

_MAX_TITLE_CHARS = 80

# Fixed height of a Recent/Favorites card's title/prompt block (~2 text lines).
# Those grids mix 1-line template names with 2-line custom prompts, so reserving
# two lines on every card keeps a row from having a tall card next to short ones.
# Template grids (Top Picks, themed) stay compact 1-line - they never wrap.
_CARD_TITLE_H = 36

# Char budget for a wrapped prompt/title on a 2-line Recent/Favorites card. Sized
# to fill both lines of a ~320px card before the word-boundary ellipsis kicks in.
_CARD_PROMPT_CHARS = 92


def _truncate(text: str, n: int = _MAX_TITLE_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


# Cap for the muted prompt snippet that sits permanently under each Top Picks
# card title. Big enough for the first sentence's intent, small enough that
# the snippet wraps to 2-3 lines max inside a 250-px card.
def _preset_matches(preset: dict, query: str) -> bool:
    """Case-insensitive substring match across label, prompt, and category."""
    haystack = (
        f'{preset.get("label", "")} {preset.get("prompt", "")} '
        f'{preset.get("source_category", "") or ""}'
    ).lower()
    return query in haystack


def _svg_url(path: str) -> str:
    return QUrl.fromLocalFile(path).toString()


def _sidebar_icon_html(cat_key: str) -> str:
    if cat_key == "recent":
        return (
            f'<img src="{_svg_url(_HISTORY_SVG)}" width="14" height="14" '
            'style="vertical-align: middle;" />'
        )
    if cat_key == "favorites":
        # Trophy SVG sets Top Picks apart from the ☆ Favorites tab.
        return (
            f'<img src="{_svg_url(_TROPHY_SVG)}" width="15" height="15" '
            'style="vertical-align: middle;" />'
        )
    glyph, color = _SIDEBAR_GLYPHS.get(cat_key, ("", "palette(text)"))
    return f'<span style="color:{color}; font-size:15px;">{glyph}</span>'


def _tab_label(cat_key: str, label: str, count: int | None = None) -> str:
    """Sidebar label HTML - name with optional muted count badge."""
    if count is not None and count > 0:
        count_html = (
            f' <span style="color:rgba(128,128,128,0.8); font-size:11px;">'
            f'({count})</span>'
        )
    else:
        count_html = ""
    return (
        f'<span style="font-size:13px; color:palette(text);">{label}</span>'
        f'{count_html}'
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


def _build_origin_pill(parent, has_template: bool) -> QLabel:
    """Plain text pill marking a Favorites entry's origin so curated TerraLab
    templates and the user's own saved prompts are told apart at a glance
    (#128). Text only, no color coding - the box + word carry the meaning.
    """
    pill = QLabel(tr("Template") if has_template else tr("Your prompt"), parent)
    pill.setStyleSheet(_ORIGIN_PILL)
    # Let clicks fall through to the card so the pill never blocks selection.
    pill.setAttribute(QtC.WA_TransparentForMouseEvents)
    return pill


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
        from ..before_after_slider import BeforeAfterSlider

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


def _card_prompt(prompt: str, n: int = 66) -> str:
    """Flatten whitespace and truncate at a word boundary so the prompt fits
    ~2 lines on a card without an ugly mid-word cut."""
    flat = " ".join((prompt or "").split())
    if len(flat) <= n:
        return flat
    cut = flat[:n].rsplit(" ", 1)[0] or flat[:n]
    return cut.rstrip(" ,.;:-") + "…"


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

    def __init__(self, job, demo_loader, on_open, parent=None, *, show_origin_pill=False):
        super().__init__(parent)
        self.setObjectName("card")
        self._job = job
        self._request_id = str(job.get("request_id") or "")
        self._on_open = on_open
        self._demo_loader = demo_loader
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
        from ..before_after_slider import BeforeAfterSlider

        self._slider = BeforeAfterSlider(self, auto_loop=False, show_badges=False)
        self._slider.setFixedHeight(self.SLIDER_HEIGHT)
        self._slider.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        self._slider.setCursor(QtC.PointingHandCursor)
        self._slider.clicked.connect(self._emit_open)
        outer.addWidget(self._slider)

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


# ---------------------------------------------------------------------------
# Sync workers
# ---------------------------------------------------------------------------

class _LibrarySyncWorker(QThread):
    """Background fetch of the user's past generations for Recent + Favorites.

    Pulls the rich before/after history endpoint (prompt + signed input/output
    URLs + location) so Recent and Favorites render as generation cards the
    user can re-add to the map, reuse, or download. Emits one signal per
    section so the dialog refreshes progressively."""

    recent_jobs_fetched = pyqtSignal(list, bool)
    favorite_jobs_fetched = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, client, auth: dict, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth

    def run(self):
        try:
            hist = self._client.get_generation_history(self._auth, limit=50)
        except Exception as e:
            self.failed.emit(f"history: {e}")
            return
        if isinstance(hist, dict) and "error" not in hist:
            jobs = hist.get("jobs", []) or []
            # Older servers don't send has_more; a full page implies more.
            self.recent_jobs_fetched.emit(jobs, bool(hist.get("has_more", len(jobs) >= 50)))
        else:
            self.failed.emit(
                f"history: {hist.get('error', 'unknown') if isinstance(hist, dict) else 'parse_error'}"
            )

        try:
            favs = self._client.get_generation_history(
                self._auth, limit=50, favorites_only=True
            )
        except Exception as e:
            self.failed.emit(f"favorites: {e}")
            return
        if isinstance(favs, dict) and "error" not in favs:
            self.favorite_jobs_fetched.emit(favs.get("jobs", []) or [])
        else:
            self.failed.emit(
                f"favorites: {favs.get('error', 'unknown') if isinstance(favs, dict) else 'parse_error'}"
            )


class _HistoryPageWorker(QThread):
    """Background fetch of one OLDER page of the generation history - the
    Recent tab's server-side Load more, used once the locally held jobs are
    all visible but the server reported has_more."""

    page_fetched = pyqtSignal(list, bool)
    failed = pyqtSignal(str)

    def __init__(self, client, auth: dict, before: str, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._before = before

    def run(self):
        try:
            resp = self._client.get_generation_history(
                self._auth, limit=50, before=self._before
            )
        except Exception as e:
            self.failed.emit(f"history page: {e}")
            return
        if isinstance(resp, dict) and "error" not in resp:
            jobs = resp.get("jobs", []) or []
            self.page_fetched.emit(jobs, bool(resp.get("has_more", False)))
        else:
            self.failed.emit("history page: server error")


class _FavoriteSyncWorker(QThread):
    """Fire-and-forget POST/DELETE for a single prompt favorite toggle (the ★
    on a curated template, distinct from a generation favorite)."""

    def __init__(
        self,
        client,
        auth: dict,
        prompt: str,
        label: str,
        source_category: str,
        now_favorited: bool,
        parent=None,
    ):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._prompt = prompt
        self._label = label or None
        self._source_category = source_category or None
        self._now_favorited = now_favorited

    def run(self):
        try:
            if self._now_favorited:
                self._client.add_favorite(
                    self._auth, self._prompt, self._label, self._source_category
                )
            else:
                self._client.remove_favorite(self._auth, self._prompt)
        except Exception as e:
            log_warning(f"Favorite sync failed (silent): {e}")


class _GenerationFavoriteWorker(QThread):
    """Fire-and-forget star/unstar of a single past generation."""

    def __init__(self, client, auth: dict, request_id: str, now_favorited: bool, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._request_id = request_id
        self._now_favorited = now_favorited

    def run(self):
        try:
            self._client.set_generation_favorite(
                self._auth, self._request_id, self._now_favorited
            )
        except Exception as e:
            log_warning(f"Generation favorite sync failed (silent): {e}")


# In-flight background workers, held independently of any dialog. A running
# QThread that loses its last Python reference can be garbage-collected and
# destroyed mid-run, which aborts the QGIS process. Keeping the worker here
# until it emits finished lets the dialog be closed or deleted at any time
# while the blocking fetch is still going, without crashing.
_INFLIGHT_WORKERS: set = set()


def _detach_worker(worker: QThread) -> None:
    _INFLIGHT_WORKERS.add(worker)
    worker.finished.connect(lambda: _INFLIGHT_WORKERS.discard(worker))
    worker.finished.connect(worker.deleteLater)


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class PromptTemplatesDialog(QDialog):
    """Tab-style modal for browsing recent, favorites, top picks, and templates."""

    # Emitted when the user acts on a past generation without picking its
    # prompt: action is "add_to_map" or "download", job is the history row.
    # "Reuse prompt" stays on the existing accept()/get_selected_preset path.
    generation_action = pyqtSignal(str, dict)
    # Emitted whenever the Recent/Favorites lists change (fetched or edited) so
    # the dock can keep a session cache and reopen the library instantly.
    history_synced = pyqtSignal(list, list)

    def __init__(
        self,
        parent=None,
        client=None,
        auth_provider=None,
        server_catalog: dict | None = None,
        browse_only: bool = False,
        recent_jobs: list | None = None,
        favorite_jobs: list | None = None,
        history_fresh: bool = False,
    ):
        """
        client: TerraLabClient instance (optional). If None, no server sync.
        auth_provider: callable returning current auth headers dict (optional).
            We take a callable instead of a header so we always send a fresh
            token, even if the user re-activates while the dialog is constructed.
        server_catalog: parsed result of GET /api/ai-edit/presets (optional).
            When provided, presets carry demo_url_before/demo_url_after and the
            dialog renders rich before/after cards. When None, falls back to
            the local prompt_presets catalog with text-only cards.
        browse_only: when True, card clicks do not select a preset (used
            while a generation is in flight). The user can still scroll, star
            favorites, and inspect prompts.
        """
        super().__init__(parent)
        self._browse_only = browse_only
        self.setWindowTitle(
            tr("Prompt library (view only)") if browse_only else tr("Prompt library")
        )
        self.setMinimumSize(640, 480)
        # Open large: size to hug the 3-column grid so there is little empty
        # space on the sides, and grow to most of the screen so the previews
        # read big. Capped to the available screen so it never spills offscreen.
        self._apply_open_size()
        self.setSizeGripEnabled(True)

        self._client = client
        self._auth_provider = auth_provider
        self._server_catalog = server_catalog

        # Async image fetcher, shared by Top Picks demo sliders and the
        # Recent/Favorites generation thumbnails. Instantiated whenever a
        # client exists (history thumbnails need it even without a catalog).
        self._demo_loader = None
        if client is not None:
            from ..template_demo_loader import TemplateDemoLoader

            self._demo_loader = TemplateDemoLoader(self)

        # Past generations for the Recent + Favorites galleries. Seeded from the
        # dock's session cache (instant, no blank-then-fill) and refreshed in the
        # background only when the cache is stale (history_fresh is False).
        self._recent_jobs: list[dict] = list(recent_jobs or [])
        self._favorite_jobs: list[dict] = list(favorite_jobs or [])
        self._history_fresh = bool(history_fresh)
        # Whether the server holds generations older than what we have; drives
        # the Recent tab's server-side Load more. A full warm-cache page means
        # "probably more" until a sync says otherwise.
        self._recent_has_more = len(self._recent_jobs) >= 50
        self._recent_page_worker: _HistoryPageWorker | None = None

        self._selected_preset: dict | None = None
        # A past generation the user chose to fully reproduce (prompt + refs +
        # zone). Read by the dock after exec() to drive the restore flow.
        self._restore_job: dict | None = None
        self._categories_by_key: dict[str, dict] = {}
        self._sidebar_buttons: dict[str, _SidebarButton] = {}
        # Collapsed themed-category sidebar buttons (beyond the visible few) and
        # the toggle that reveals them. Populated in _build_ui.
        self._collapsed_category_btns: list[_SidebarButton] = []
        self._category_toggle_btn: QPushButton | None = None
        self._categories_expanded: bool = False
        self._pages: dict[str, QWidget] = {}
        # Grid cards (_BeforeAfterCard) stored as generic widgets keyed by the
        # page they live on. Star refresh uses `card.star_button()` and
        # `card.preset()`.
        self._card_widgets: list[tuple[QWidget, str]] = []
        # Default landing: Top Picks. First-time users see curated content,
        # not their empty Recent/Favorites.
        self._active_tab: str = "favorites"
        self._sync_worker: _LibrarySyncWorker | None = None

        # Themed-category pagination state, keyed by category key. Each entry
        # carries the layout, load-more button, and how many cards are visible.
        # Rebuilt fresh every time _build_page runs for a themed tab.
        self._themed_state: dict[str, dict] = {}
        # key -> callable that loads the visible thumbnails for that gallery
        # page (recent/user_favorites). Re-triggered when the tab is shown.
        self._gallery_loaders: dict = {}
        # key -> {grid, jobs, cards, visible, btn} for the Show-more paging.
        self._gallery_state: dict = {}

        self._load_categories()
        self._build_ui()
        self._start_sync()

    # -- Data ------------------------------------------------------------

    def _load_categories(self):
        """Build the category dict from the server catalog.

        `get_all_categories` reads the explicit `server_catalog` first, falls
        back to the locally-cached catalog (`prompt_presets_client`), and
        returns empty themed shells when neither is available."""
        cats = get_all_categories(self._server_catalog)
        self._categories_by_key = {c["key"]: c for c in cats}

    # -- Layout ----------------------------------------------------------

    def _apply_open_size(self):
        """Size to snugly fit the 220px sidebar + a 3-column 300px card grid,
        then grow toward the screen so the previews feel generous. Clamped to
        the available screen on small displays."""
        target_w, target_h = 1220, 880
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(target_w, int(avail.width() * 0.96))
            target_h = min(target_h, int(avail.height() * 0.92))
        self.resize(max(target_w, 640), max(target_h, 480))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # First-run guidance: a dismissible callout explaining, in plain words,
        # what the library is and the pick -> preview -> use flow. Hidden for
        # good once closed; re-enabled from Account Settings.
        if not is_hint_dismissed(HINT_LIBRARY_INTRO):
            root.addWidget(DismissibleHint(
                HINT_LIBRARY_INTRO,
                tr("Transform your selected area with AI"),
                tr(
                    'These are ready-made instructions ("prompts"). Each one '
                    "tells the AI how to redraw the zone you selected on the map, "
                    "for example segment buildings, classify land cover, or change "
                    "the season."
                ),
                steps=[
                    ("", tr("Pick an example"), tr("browse a category or search")),
                    ("", tr("Preview the result"),
                     tr("click a card to see before and after")),
                    ("", tr("Use it"), tr("it runs on the zone you selected")),
                ],
            ))

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            tr('Search prompts...  e.g. "add trees", "segment buildings"')
        )
        self._search_input.addAction(
            search_icon(), QLineEdit.ActionPosition.LeadingPosition
        )
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setStyleSheet(_SEARCH_BOX)
        self._search_input.textChanged.connect(self._on_search_changed)
        root.addWidget(self._search_input)

        body = QHBoxLayout()
        body.setSpacing(8)

        # Sidebar - wide enough for the longest label ("Presentation renders"
        # @ 13px font ≈ 165px) + icon + padding without ellipsis on any tab.
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(2)
        # Kept so the category toggle can relocate to the bottom when expanded.
        self._sidebar_layout = sidebar_layout

        # Group the sidebar so the user's own entries read apart from curated
        # templates ("weird that Recent/Favorites are in here" feedback).
        sidebar_layout.addWidget(self._sidebar_section_header(tr("Your prompts")))

        # Themed categories are long (13 deep); show only the first few and
        # collapse the rest behind a toggle so the sidebar stays scannable.
        themed_seen = 0
        for key in _TAB_ORDER:
            if key == "__separator__":
                sep_wrap = QWidget()
                sep_wrap.setFixedHeight(13)
                sep_inner = QVBoxLayout(sep_wrap)
                sep_inner.setContentsMargins(12, 6, 12, 6)
                line = QFrame()
                line.setFixedHeight(1)
                line.setStyleSheet(
                    "background: rgba(128,128,128,0.3); border: none;"
                )
                sep_inner.addWidget(line)
                sidebar_layout.addWidget(sep_wrap)
                sidebar_layout.addWidget(self._sidebar_section_header(tr("Templates")))
                continue
            cat = self._categories_by_key.get(key)
            if cat is None:
                continue
            btn = self._build_sidebar_button(key, cat)
            sidebar_layout.addWidget(btn)
            self._sidebar_buttons[key] = btn

            if key in _CATEGORY_KEYS:
                themed_seen += 1
                if themed_seen == _SIDEBAR_VISIBLE_CATEGORIES:
                    # Insert the toggle right after the last always-visible
                    # category; the categories added after it start hidden.
                    self._category_toggle_btn = self._build_category_toggle()
                    sidebar_layout.addWidget(self._category_toggle_btn)
                elif themed_seen > _SIDEBAR_VISIBLE_CATEGORIES:
                    btn.setVisible(False)
                    self._collapsed_category_btns.append(btn)

        # No toggle needed when there are fewer categories than the cap.
        if self._category_toggle_btn is not None and not self._collapsed_category_btns:
            self._category_toggle_btn.setVisible(False)
        self._update_category_toggle_text()

        sidebar_layout.addStretch()
        body.addWidget(sidebar)

        vsep = QFrame()
        vsep.setFrameShape(QtC.FrameVLine)
        vsep.setFrameShadow(QtC.FrameSunken)
        body.addWidget(vsep)

        # Pages are built lazily (only when a tab is first shown) so opening the
        # dialog never pays for the 13 category pages + the 24-card Recent grid
        # and its thumbnail downloads up front. Only the default tab + the
        # search page exist at construction; the rest materialize on demand via
        # _ensure_page.
        self._stack = QStackedWidget()
        self._ensure_page(self._active_tab)
        # Search results page - shown when the search input is non-empty.
        self._search_page = self._build_search_page()
        self._stack.addWidget(self._search_page)

        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)

        # Remember which tab to restore when the search box is cleared.
        self._previous_tab: str = self._active_tab
        self._switch_to_tab(self._active_tab)

    def _tab_count(self, key: str, category: dict | None = None) -> int:
        """Sidebar badge count. Recent/Favorites count past generations;
        other counted tabs count their presets."""
        if key == "recent":
            return len(self._recent_jobs)
        if key == "user_favorites":
            # Both halves of the unified tab: starred templates + starred gens.
            cat = category or self._categories_by_key.get(key) or {}
            return len(self._favorite_jobs) + len(cat.get("presets", []))
        cat = category or self._categories_by_key.get(key) or {}
        return len(cat.get("presets", []))

    @staticmethod
    def _sidebar_section_header(text: str) -> QLabel:
        """Muted uppercase group label for the sidebar (Your prompts / Templates)."""
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            "color: rgba(128,128,128,0.95); font-size: 10px; font-weight: 700;"
            " letter-spacing: 0.8px; background: transparent; border: none;"
            " padding: 6px 12px 2px 12px;"
        )
        return lbl

    def _build_sidebar_button(self, key: str, category: dict) -> _SidebarButton:
        count = None
        if key in _TABS_WITH_COUNT:
            count = self._tab_count(key, category)
        btn = _SidebarButton(
            _sidebar_icon_html(key),
            _tab_label(key, category["label"], count),
        )
        btn.setCursor(QtC.PointingHandCursor)
        btn.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        btn.clicked.connect(lambda checked, k=key: self._on_sidebar_click(k))
        return btn

    def _build_category_toggle(self) -> QPushButton:
        """Expander that reveals/hides the extra themed categories. Styled as a
        muted 'show more' control (not a _SidebarButton) so it doesn't read as
        another category. Text is set by _update_category_toggle_text."""
        btn = QPushButton()
        btn.setStyleSheet(_CATEGORY_TOGGLE_BTN)
        btn.setCursor(QtC.PointingHandCursor)
        btn.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        btn.clicked.connect(self._on_toggle_categories)
        return btn

    def _update_category_toggle_text(self):
        """Refresh the toggle chevron + label to match the expanded state. The
        chevron sits inline with the text (not in an icon column) so the row
        stays distinct from the category entries above it."""
        btn = self._category_toggle_btn
        if btn is None:
            return
        hidden = len(self._collapsed_category_btns)
        if self._categories_expanded:
            btn.setText("▴  " + tr("Show fewer categories"))
        else:
            btn.setText("▾  " + tr("Show {n} more categories").format(n=hidden))

    def _on_toggle_categories(self):
        """Reveal or hide the collapsed themed categories."""
        self._categories_expanded = not self._categories_expanded
        for btn in self._collapsed_category_btns:
            if _is_alive(btn):
                btn.setVisible(self._categories_expanded)
        self._reposition_category_toggle()
        self._update_category_toggle_text()

    def _reposition_category_toggle(self):
        """Keep the toggle next to the boundary it controls: tucked under the
        last always-visible category when collapsed, and pushed to the very
        bottom (under the last revealed category) once expanded."""
        layout = getattr(self, "_sidebar_layout", None)
        toggle = self._category_toggle_btn
        if layout is None or toggle is None or not self._collapsed_category_btns:
            return
        layout.removeWidget(toggle)
        if self._categories_expanded:
            # Just before the trailing stretch (last layout item).
            layout.insertWidget(layout.count() - 1, toggle)
        else:
            # Back above the first collapsed category.
            idx = layout.indexOf(self._collapsed_category_btns[0])
            layout.insertWidget(idx if idx >= 0 else layout.count() - 1, toggle)

    def _on_sidebar_click(self, key: str):
        """Sidebar click is an explicit "leave search" - clear the box."""
        if self._search_input.text().strip():
            self._search_input.blockSignals(True)
            self._search_input.clear()
            self._search_input.blockSignals(False)
        self._switch_to_tab(key)

    def _refresh_sidebar_button(self, key: str):
        cat = self._categories_by_key.get(key)
        if cat is None or key not in self._sidebar_buttons:
            return
        count = None
        if key in _TABS_WITH_COUNT:
            count = self._tab_count(key, cat)
        self._sidebar_buttons[key].set_label_html(
            _sidebar_icon_html(key),
            _tab_label(key, cat["label"], count),
        )

    # -- Pages -----------------------------------------------------------

    @staticmethod
    def _new_card_grid(host: QWidget, columns: int = 3) -> QGridLayout:
        """A card grid whose columns share the width equally, so cards stretch
        to fill the page and never clip at the right edge when the window
        resizes."""
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for c in range(columns):
            grid.setColumnStretch(c, 1)
        return grid

    def _build_page(self, key: str) -> QWidget:
        """One scrollable page per sidebar tab - cards for that category only.

        Top Picks (``favorites``) uses a 3-column square-card grid with
        before/after slider previews. Recent paginates with a Load-more
        button so power users with thousands of prompts open instantly.
        Every other tab is a plain vertical list of text cards."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)

        content = QWidget()
        category = self._categories_by_key[key]

        if key == "favorites":
            # Minimal grid: 3-col x 2-row of compact slider cards (6 total).
            # Each slider sits idle at 50/50 (auto_loop disabled) so the page
            # reads as a calm launcher; the divider animates only on hover.
            # Whole page tops out around 400px tall and fits the default
            # 1100x720 dialog without any scrollbar.
            outer_v = QVBoxLayout(content)
            outer_v.setContentsMargins(6, 4, 6, 8)
            outer_v.setSpacing(8)
            presets = category["presets"]
            if not presets:
                empty = self._build_empty_state(key)
                if empty is not None:
                    outer_v.addWidget(empty)
            else:
                grid_host = QWidget()
                grid = self._new_card_grid(grid_host, columns=3)
                self._populate_grid_cards(grid, presets, key, columns=3)
                outer_v.addWidget(grid_host)
            outer_v.addStretch()
        elif key == "recent":
            # The user's past generations as before/after cards they can reopen,
            # reuse, or add back to the map.
            outer_v = QVBoxLayout(content)
            outer_v.setContentsMargins(6, 4, 6, 8)
            outer_v.setSpacing(8)
            if not self._recent_jobs:
                self._gallery_state.pop(key, None)
                empty = self._build_empty_state(key)
                if empty is not None:
                    outer_v.addWidget(empty)
            else:
                entries = [{"kind": "job", "data": j} for j in self._recent_jobs]
                self._build_card_gallery(key, entries, outer_v, scroll)
            outer_v.addStretch()
        elif key == "user_favorites":
            # Unified Favorites: the curated templates the user starred AND the
            # generations they starred, each card carrying a Template / Your
            # prompt origin pill. Everything lives in ONE continuous grid -
            # starred templates/prompts first, then starred generations - so
            # removing any favorite reflows every later card into place with no
            # half-empty row at a section boundary.
            outer_v = QVBoxLayout(content)
            outer_v.setContentsMargins(6, 4, 6, 8)
            outer_v.setSpacing(8)
            fav_presets = category.get("presets", []) or []
            fav_jobs = self._favorite_jobs
            if not fav_presets and not fav_jobs:
                self._gallery_state.pop(key, None)
                empty = self._build_empty_state(key)
                if empty is not None:
                    outer_v.addWidget(empty)
            else:
                entries = [{"kind": "preset", "data": p} for p in fav_presets]
                entries += [{"kind": "job", "data": j} for j in fav_jobs]
                self._build_card_gallery(key, entries, outer_v, scroll)
            outer_v.addStretch()
        else:
            layout = QVBoxLayout(content)
            layout.setContentsMargins(6, 4, 6, 10)
            layout.setSpacing(6)
            presets = category["presets"]
            if not presets:
                empty = self._build_empty_state(key)
                if empty is not None:
                    layout.addWidget(empty)
                self._themed_state.pop(key, None)
            else:
                # Every reliable preset is shown up front; experimentals
                # (server-flagged fragile prompts) hide behind a single
                # amber disclosure so the curated default view stays clean.
                reliable, experimental = _split_experimental(presets)
                # Same visual language as Top Picks: a 3-column grid of
                # before/after preview cards. Each cell falls back to a text
                # card when its demo asset is missing, so every category reads
                # the same way whether or not its demos are seeded yet.
                grid_host = QWidget()
                grid = self._new_card_grid(grid_host, columns=3)
                self._populate_grid_cards(grid, reliable, key, columns=3)
                layout.addWidget(grid_host)

                exp_btn = None
                if experimental:
                    exp_btn = QPushButton(
                        self._show_experimental_label(len(experimental))
                    )
                    exp_btn.setStyleSheet(_EXPERIMENTAL_BTN)
                    exp_btn.setCursor(QtC.PointingHandCursor)
                    exp_btn.clicked.connect(
                        lambda _=False, k=key: self._on_show_experimental(k)
                    )
                    row = QHBoxLayout()
                    row.setContentsMargins(0, 6, 0, 0)
                    row.addStretch()
                    row.addWidget(exp_btn)
                    row.addStretch()
                    layout.addLayout(row)
                    self._themed_state[key] = {
                        "layout": layout,
                        "grid": grid,
                        "reliable_count": len(reliable),
                        "experimental": experimental,
                        "exp_btn": exp_btn,
                    }
                else:
                    self._themed_state.pop(key, None)
            layout.addStretch()

        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _show_experimental_label(count: int) -> str:
        return tr("Show {n} experimental templates").format(n=count)

    def _on_show_experimental(self, key: str):
        """Reveal the experimental templates for this category, prefixed
        with a small amber header that warns the prompts may misfire."""
        state = self._themed_state.get(key)
        if state is None:
            return
        exp_btn = state.get("exp_btn")
        experimental = state.get("experimental") or []
        grid = state.get("grid")
        if exp_btn is None or not _is_alive(exp_btn) or not experimental or grid is None:
            return
        # Continue the same 3-column grid: an amber header spanning the row,
        # then the experimental templates as preview cards below it.
        start = int(state.get("reliable_count", 0))
        header_row = (start + 2) // 3
        header = QLabel(tr("EXPERIMENTAL (may produce unexpected results)"))
        header.setStyleSheet(_EXPERIMENTAL_HEADER)
        header.setWordWrap(True)
        grid.addWidget(header, header_row, 0, 1, 3)
        base = (header_row + 1) * 3
        for i, preset in enumerate(experimental):
            row, col = divmod(base + i, 3)
            card = self._build_top_pick_card(preset)
            grid.addWidget(card, row, col)
            self._card_widgets.append((card, key))
        exp_btn.setVisible(False)
        state["exp_btn"] = None
        self._themed_state.pop(key, None)

    @staticmethod
    def _row_index_of(layout: QVBoxLayout, btn: QPushButton) -> int:
        """Return the layout index of the row that hosts `btn`, so callers
        can insertWidget before it. Falls back to layout.count() - 1 (right
        before the trailing stretch) if the row can't be found."""
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            sub = item.layout()
            if sub is not None and sub.indexOf(btn) >= 0:
                return i
        return max(0, layout.count() - 1)

    def _populate_grid_cards(
        self, grid: QGridLayout, presets: list[dict], page_key: str, columns: int = 3
    ):
        """Top Picks layout: compact BeforeAfterCard cells in a 3x2 grid.

        Renders the slider card from the server-hosted demo URL, falling back
        to a text card when the demo is not seeded yet so the grid still
        renders even before any before/after asset exists."""
        for idx, preset in enumerate(presets):
            row, col = divmod(idx, columns)
            card = self._build_top_pick_card(preset)
            grid.addWidget(card, row, col)
            self._card_widgets.append((card, page_key))

    def _build_top_pick_card(self, preset: dict) -> QFrame:
        """One library card: always a compact before/after slider so every cell
        in the grid keeps the same shape and stays aligned. When no demo asset
        exists (freeform favorite, or a template whose demo isn't seeded yet)
        the slider paints a 'No preview' placeholder instead of an image."""
        from ...core.prompts.prompt_presets_client import absolute_demo_url

        loader = self._demo_loader if self._client is not None else None

        def _abs(rel, _client=self._client):
            return absolute_demo_url(_client, rel) if _client is not None else rel

        card = _BeforeAfterCard(
            preset,
            self._on_card_clicked,
            demo_loader=loader,
            absolute_url=_abs if loader is not None else None,
        )
        star = card.star_button()
        if star is not None:
            star.toggled_state.connect(self._on_star_toggled)
        return card

    def _build_card_gallery(
        self, key: str, entries: list, outer_v: QVBoxLayout, scroll: QScrollArea,
    ) -> None:
        """Paged grid of cards into ONE continuous 3-column grid. Each entry is
        a tagged dict ({"kind": "preset"|"job", "data": ...}); presets render as
        template slider cards, jobs as generation cards. Shared by Recent (jobs
        only) and the unified Favorites tab (presets + jobs in one flow), so a
        removed favorite reflows every later card with no gap."""
        grid_host = QWidget()
        grid = self._new_card_grid(grid_host, columns=3)
        cards: list = []
        more_btn = QPushButton()
        more_btn.setStyleSheet(_LOAD_MORE_BTN)
        more_btn.setCursor(QtC.PointingHandCursor)
        more_btn.clicked.connect(lambda _=False, k=key: self._on_gallery_show_more(k))
        self._gallery_state[key] = {
            "grid": grid,
            "entries": entries,
            "cards": cards,
            "visible": 0,
            "btn": more_btn,
        }
        self._append_gallery_cards(key)  # first _GALLERY_PAGE_SIZE

        outer_v.addWidget(grid_host)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 6, 0, 0)
        btn_row.addStretch()
        btn_row.addWidget(more_btn)
        btn_row.addStretch()
        outer_v.addLayout(btn_row)
        self._update_gallery_more_btn(key)

        self._wire_gallery_lazy_load(key, scroll, cards)

    def _append_gallery_cards(self, key: str) -> None:
        """Build the next batch of cards into the gallery grid, dispatching on
        each entry's kind so presets and generations share one index space."""
        st = self._gallery_state.get(key)
        if st is None:
            return
        entries = st["entries"]
        grid = st["grid"]
        cards = st["cards"]
        start = st["visible"]
        end = min(start + _GALLERY_PAGE_SIZE, len(entries))
        # Favorites tag generation cards with a Template / Your prompt origin
        # pill so the two kinds read apart; Recent does not (all generations).
        show_origin = key == "user_favorites"
        for idx in range(start, end):
            row, col = divmod(idx, 3)
            entry = entries[idx]
            if entry.get("kind") == "preset":
                card = self._build_top_pick_card(entry["data"])
            else:
                card = self._build_generation_card(entry["data"], show_origin)
            grid.addWidget(card, row, col)
            cards.append(card)
        st["visible"] = end

    def _update_gallery_more_btn(self, key: str) -> None:
        st = self._gallery_state.get(key)
        if st is None:
            return
        btn = st.get("btn")
        if btn is None or not _is_alive(btn):
            return
        remaining = len(st["entries"]) - st["visible"]
        if remaining > 0:
            btn.setVisible(True)
            btn.setEnabled(True)
            btn.setText(tr("Show {n} more").format(n=remaining))
        elif key == "recent" and self._recent_has_more:
            # Local jobs all visible but the server holds older ones.
            btn.setVisible(True)
            btn.setEnabled(True)
            btn.setText(tr("Load older generations"))
        else:
            btn.setVisible(False)

    def _on_gallery_show_more(self, key: str) -> None:
        st = self._gallery_state.get(key)
        if (
            key == "recent"
            and st is not None
            and st["visible"] >= len(st["entries"])
            and self._recent_has_more
        ):
            self._fetch_older_recent()
            return
        self._append_gallery_cards(key)
        self._update_gallery_more_btn(key)
        trigger = self._gallery_loaders.get(key)
        if trigger is not None:
            QTimer.singleShot(0, trigger)

    def _fetch_older_recent(self) -> None:
        """Server-side Load more for Recent: fetch the page older than the
        oldest job currently held and append it to the gallery."""
        if self._client is None or self._auth_provider is None:
            return
        if self._recent_page_worker is not None and self._recent_page_worker.isRunning():
            return
        oldest = self._recent_jobs[-1].get("created_at") if self._recent_jobs else None
        if not oldest:
            return
        auth = self._auth_provider() or {}
        if not auth.get("Authorization"):
            return
        st = self._gallery_state.get("recent")
        if st is not None and _is_alive(st.get("btn")):
            st["btn"].setEnabled(False)
            st["btn"].setText(tr("Loading..."))
        worker = _HistoryPageWorker(self._client, auth, oldest, parent=None)
        worker.page_fetched.connect(self._on_older_recent_fetched)
        worker.failed.connect(self._on_older_recent_failed)
        _detach_worker(worker)
        self._recent_page_worker = worker
        worker.start()

    def _on_older_recent_fetched(self, jobs: list, has_more: bool) -> None:
        self._recent_has_more = bool(has_more) and bool(jobs)
        known = {j.get("request_id") for j in self._recent_jobs}
        fresh = [j for j in (jobs or []) if j.get("request_id") not in known]
        if fresh:
            self._recent_jobs.extend(fresh)
            st = self._gallery_state.get("recent")
            if st is not None:
                st["entries"].extend({"kind": "job", "data": j} for j in fresh)
                self._append_gallery_cards("recent")
            self._refresh_sidebar_button("recent")
        self._update_gallery_more_btn("recent")
        trigger = self._gallery_loaders.get("recent")
        if trigger is not None:
            QTimer.singleShot(0, trigger)
        self.history_synced.emit(self._recent_jobs, self._favorite_jobs)

    def _on_older_recent_failed(self, msg: str) -> None:
        log_debug(f"Recent older-page fetch failed: {msg}")
        self._update_gallery_more_btn("recent")

    def _build_generation_card(self, job: dict, show_origin_pill: bool = False) -> _GenerationCard:
        return _GenerationCard(
            job,
            self._demo_loader,
            on_open=lambda j: self._open_detail(job=j),
            show_origin_pill=show_origin_pill,
        )

    def _absolute_demo_url(self, rel: str) -> str:
        from ...core.prompts.prompt_presets_client import absolute_demo_url

        return absolute_demo_url(self._client, rel)

    def _open_detail(self, *, job: dict | None = None, preset: dict | None = None):
        """Open the detail popup for a generation or a curated template. The
        popup applies nothing itself: it records an outcome we read here so the
        nested modal loops stay sane."""
        # A fast double-click on a card can fire this twice before the first
        # popup grabs input; two stacked detail modals over the same loader
        # race on teardown and crash QGIS. One popup at a time.
        if getattr(self, "_detail_open", False):
            return
        self._detail_open = True
        from .generation_detail_dialog import GenerationDetailDialog

        detail = GenerationDetailDialog(
            self,
            job=job,
            preset=preset,
            client=self._client,
            demo_loader=self._demo_loader,
            absolute_url=self._absolute_demo_url,
            on_action=self._on_generation_action,
            on_favorite=self._on_generation_favorite,
            browse_only=self._browse_only,
        )
        # Favoriting a template now lives in this popup; route its toggle
        # through the same handler the inline stars used (server sync + state).
        detail.prompt_favorite_toggled.connect(self._on_star_toggled)
        try:
            detail.exec()
            outcome = detail.outcome()
            if outcome == "use" and not self._browse_only:
                if job is not None:
                    self._restore_job = job
                    telemetry.track(te.RECENT_SELECTED, {
                        "request_id": str(job.get("request_id") or ""),
                        "had_template": bool(job.get("template_id")),
                    })
                    telemetry.flush()
                elif preset is not None:
                    self._selected_preset = preset
                self.accept()
            elif outcome == "close":
                self.accept()
        finally:
            self._detail_open = False
            detail.deleteLater()

    def _wire_gallery_lazy_load(self, key: str, scroll: QScrollArea, cards: list) -> None:
        """Download thumbnails only for cards in (or near) the viewport, and
        load more as the user scrolls. Keeps opening Recent fast - no upfront
        burst of dozens of full-size image downloads."""
        trigger = lambda: self._load_visible_cards(scroll, cards)  # noqa: E731
        self._gallery_loaders[key] = trigger
        scroll.verticalScrollBar().valueChanged.connect(lambda _v: trigger())
        # Two ticks: one once the event loop drains, one after layout settles.
        QTimer.singleShot(0, trigger)
        QTimer.singleShot(80, trigger)

    @staticmethod
    def _load_visible_cards(scroll: QScrollArea, cards: list) -> None:
        if not _is_alive(scroll):
            return
        viewport = scroll.viewport()
        vp_h = viewport.height()
        if vp_h <= 0:
            return
        # Prefetch one screen ahead so scrolling stays smooth.
        margin = vp_h
        for card in cards:
            if not _is_alive(card) or getattr(card, "_thumbs_requested", True):
                continue
            try:
                top = card.mapTo(viewport, QPoint(0, 0)).y()
            except (RuntimeError, TypeError):
                continue
            if top + card.height() >= -margin and top <= vp_h + margin:
                card.load_thumbnails()

    def _on_generation_action(self, action: str, job: dict):
        """Bubble add-to-map / download up to the dock (work runs in a task
        while this modal stays open)."""
        self.generation_action.emit(action, job)

    def _on_generation_favorite(self, request_id: str, now_favorited: bool):
        """Star/unstar a past generation. Optimistic UI already updated by the
        card; sync to the server and keep the in-memory lists consistent."""
        for j in self._recent_jobs:
            if j.get("request_id") == request_id:
                j["is_favorite"] = now_favorited
        if now_favorited:
            if not any(j.get("request_id") == request_id for j in self._favorite_jobs):
                match = next(
                    (j for j in self._recent_jobs if j.get("request_id") == request_id), None
                )
                if match is not None:
                    self._favorite_jobs.insert(0, dict(match))
        else:
            self._favorite_jobs = [
                j for j in self._favorite_jobs if j.get("request_id") != request_id
            ]
        self._refresh_sidebar_button("user_favorites")
        # Keep the dock's session cache in step with this star change.
        self.history_synced.emit(self._recent_jobs, self._favorite_jobs)
        # Rebuild the Favorites page so a freshly-starred generation shows up
        # there immediately. Deferred: destroying the card whose star was just
        # clicked mid-signal crashes on Qt6. Parent the timer to the dialog so
        # closing the library before it fires can't land on a freed widget.
        QtC.safe_single_shot(
            0, self, lambda: self._reload_dynamic_pages(keys=("user_favorites",))
        )
        if self._client is None or self._auth_provider is None:
            return
        auth = self._auth_provider() or {}
        if not auth.get("Authorization"):
            return
        worker = _GenerationFavoriteWorker(
            self._client, auth, request_id, now_favorited, parent=None
        )
        _detach_worker(worker)
        worker.start()

    def _build_search_page(self) -> QWidget:
        """Empty container for cross-tab search results. Filled by
        _rebuild_search_results whenever the search box is non-empty."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)

        content = QWidget()
        self._search_layout = QVBoxLayout(content)
        self._search_layout.setContentsMargins(6, 4, 6, 10)
        self._search_layout.setSpacing(10)
        self._search_layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _rebuild_search_results(self, query: str):
        """Wipe + repopulate the search page with cards matching `query`
        across every category."""
        while self._search_layout.count() > 0:
            item = self._search_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # Drop old search-page card refs so star refresh doesn't touch ghosts.
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if k != "__search__"
        ]

        total = 0
        for key in _TAB_ORDER:
            if key == "__separator__":
                continue
            # Recent + Favorites are generation galleries (server-fetched jobs),
            # not prompt-text presets - search covers the curated catalog only.
            if key in ("recent", "user_favorites"):
                continue
            category = self._categories_by_key.get(key)
            if category is None:
                continue
            matches = [
                p for p in category.get("presets", []) if _preset_matches(p, query)
            ]
            if not matches:
                continue
            self._search_layout.addWidget(self._search_section_header(key, len(matches)))
            # Same 3-column before/after preview-card grid as the category tabs,
            # so search results read identically to the rest of the library
            # (preview cards everywhere, never a text-row list).
            grid_host = QWidget()
            grid = self._new_card_grid(grid_host, columns=3)
            self._populate_grid_cards(grid, matches, "__search__", columns=3)
            self._search_layout.addWidget(grid_host)
            total += len(matches)

        if total == 0:
            empty = QLabel(tr("No matches found."))
            empty.setStyleSheet(_EMPTY_MSG)
            empty.setAlignment(QtC.AlignCenter)
            empty.setWordWrap(True)
            self._search_layout.addWidget(empty)

        self._search_layout.addStretch()

    def _search_section_header(self, key: str, match_count: int) -> QLabel:
        category = self._categories_by_key[key]
        label_html = (
            f'{_sidebar_icon_html(key)}&nbsp;&nbsp;'
            f'<span style="font-size:13px; font-weight:bold; color:palette(text);">'
            f'{category["label"]}</span>'
            f'&nbsp;<span style="color:rgba(128,128,128,0.7); font-size:11px;">'
            f'({match_count})</span>'
        )
        header = QLabel(label_html)
        header.setTextFormat(QtC.RichText)
        header.setStyleSheet(
            "QLabel { padding: 4px 0px; background: transparent; border: none; }"
        )
        return header

    def _build_empty_state(self, key: str) -> QWidget | None:
        if key == "recent":
            icon_path = _HISTORY_SVG
            message = tr(
                "Nothing here yet. The generations you run will land here, ready to "
                "reopen, reuse, or add back to the map."
            )
        elif key == "user_favorites":
            icon_path = _STAR_OUTLINE_SVG
            message = tr(
                "No favorites yet. Open any template or generation and tap the ★ "
                "in its preview to keep it close."
            )
        else:
            return None

        # Outer container so we can center horizontally inside the scroll area.
        outer = QWidget()
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(20, 40, 20, 40)

        inner = QWidget()
        inner.setMaximumWidth(360)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setAlignment(QtC.AlignCenter)

        icon_label = QLabel()
        icon_label.setPixmap(QIcon(icon_path).pixmap(36, 36))
        icon_label.setAlignment(QtC.AlignCenter)
        icon_label.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(icon_label)

        msg = QLabel(message)
        msg.setStyleSheet(_EMPTY_MSG)
        msg.setAlignment(QtC.AlignCenter)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        outer_layout.addStretch()
        outer_layout.addWidget(inner)
        outer_layout.addStretch()
        return outer

    # -- Tab switching ---------------------------------------------------

    def _ensure_page(self, key: str) -> QWidget | None:
        """Build the page for `key` on first use and add it to the stack.
        Lazy so the dialog open + each tab switch stays cheap; a page (and its
        thumbnail downloads) only materializes when the user actually visits it.
        """
        if key in self._pages:
            return self._pages[key]
        if key not in self._categories_by_key:
            return None
        page = self._build_page(key)
        self._pages[key] = page
        self._stack.addWidget(page)
        return page

    def _switch_to_tab(self, key: str):
        """Show the stack page for `key` and highlight its sidebar button.
        Does not touch the search input - clearing the search is the caller's
        responsibility (see _on_sidebar_click)."""
        if self._ensure_page(key) is None:
            return
        # If the target is a collapsed category, expand the section first so its
        # highlighted button is actually visible (e.g. selected via search).
        if (
            not self._categories_expanded
            and key in self._sidebar_buttons  # noqa: W503
            and self._sidebar_buttons[key] in self._collapsed_category_btns  # noqa: W503
        ):
            self._on_toggle_categories()
        self._active_tab = key
        self._previous_tab = key
        self._stack.setCurrentWidget(self._pages[key])
        for k, btn in self._sidebar_buttons.items():
            btn.setStyleSheet(_SIDEBAR_ITEM_ACTIVE if k == key else _SIDEBAR_ITEM)
        # Now that the page has a real viewport size, load its visible
        # thumbnails (a gallery rebuilt while hidden couldn't measure them).
        trigger = self._gallery_loaders.get(key)
        if trigger is not None:
            QTimer.singleShot(0, trigger)

    # -- Interaction -----------------------------------------------------

    def _on_card_clicked(self, preset: dict):
        # A card click now opens the detail popup (full prompt + demo + actions)
        # instead of applying the prompt straight away. The popup's "Use this
        # prompt" button is what selects it. Browse-only opens read-only.
        self._open_detail(preset=preset)

    def _prune_dead_cards(self):
        """Drop card refs whose underlying Qt widget has been destroyed."""
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if _is_alive(c)
        ]

    def _on_star_toggled(self, prompt: str, now_fav: bool, label: str, source_cat: str):
        # Prompt favorites now live in the unified Favorites tab, so refresh the
        # favorites catalog from prompt_history and rebuild that page to reflect
        # the add/remove. Deferred so the card whose star fired isn't destroyed
        # mid-signal (Qt6 crash guard, same pattern as _on_generation_favorite).
        self._load_categories()
        self._refresh_sidebar_button("user_favorites")
        QtC.safe_single_shot(
            0, self, lambda: self._reload_dynamic_pages(keys=("user_favorites",))
        )
        self._sync_favorite(prompt, label, source_cat, now_fav)

    def _sync_favorite(self, prompt: str, label: str, source_cat: str, now_fav: bool):
        if self._client is None or self._auth_provider is None:
            return
        auth = self._auth_provider() or {}
        if not auth.get("Authorization"):
            return
        # Unparented + detached: if the dialog closes mid-fetch, the worker keeps
        # running to completion (POST/DELETE is read-write-safe to abandon) and
        # self-deletes via finished. Parenting to the dialog would destroy the
        # parent while the worker is still alive, which crashes on Qt6.
        worker = _FavoriteSyncWorker(
            self._client, auth, prompt, label, source_cat, now_fav, parent=None
        )
        _detach_worker(worker)
        worker.start()

    def _reload_dynamic_pages(self, keys=("recent", "user_favorites")):
        """Rebuild the named generation tabs from the current job lists. Only
        rebuilds pages that are already built (lazy): an unvisited tab will pick
        up the fresh data when the user first opens it. Sidebar counts always
        refresh. No _load_categories() here - these tabs render from the
        server-fetched jobs, not the prompt-preset catalog."""
        keyset = set(keys)
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if k not in keyset
        ]

        for key in keys:
            if key not in self._pages:
                continue
            old = self._pages[key]
            # removeWidget on the currently shown page makes the stack fall back
            # to index 0; re-select the rebuilt page so the visible tab does not
            # silently jump to Top Picks during a background sync or star toggle.
            was_current = self._stack.currentWidget() is old
            idx = self._stack.indexOf(old)
            new = self._build_page(key)
            self._stack.insertWidget(idx, new)
            self._stack.removeWidget(old)
            old.deleteLater()
            self._pages[key] = new
            if was_current:
                self._stack.setCurrentWidget(new)

        for key in _TABS_WITH_COUNT:
            if key in keyset:
                self._refresh_sidebar_button(key)

        # If a search is active, re-run it so the results pick up new presets
        # (e.g. a newly-fetched Recent entry, or a card whose star state changed).
        query = self._search_input.text().strip().lower()
        if query and self._active_tab == "__search__":
            self._rebuild_search_results(query)

    def get_selected_preset(self) -> dict | None:
        return self._selected_preset

    def get_restore_job(self) -> dict | None:
        """A past generation the user chose to fully reproduce, or None."""
        return self._restore_job

    # -- Server sync -----------------------------------------------------

    def _start_sync(self):
        """Kick off a background fetch of /history + /favorites. If client/auth
        unavailable, silently skips - local cache is the source for this session."""
        # Session cache is still fresh (nothing generated since last fetch): the
        # seeded lists already populated the UI, so skip the network round-trip.
        if self._history_fresh and (self._recent_jobs or self._favorite_jobs):
            return
        if self._client is None or self._auth_provider is None:
            return
        auth = self._auth_provider() or {}
        if not auth.get("Authorization"):
            return
        # Unparented + detached: the dialog can be closed or deleted while this
        # blocking fetch is still in flight without destroying a running QThread
        # (which aborts QGIS on Qt6). The data slots below are bound methods, so
        # Qt drops them automatically when the dialog is destroyed and a late
        # result never lands on a dead object.
        worker = _LibrarySyncWorker(self._client, auth, parent=None)
        worker.recent_jobs_fetched.connect(self._on_recent_jobs_fetched)
        worker.favorite_jobs_fetched.connect(self._on_favorite_jobs_fetched)
        worker.failed.connect(self._on_sync_failed)
        _detach_worker(worker)
        self._sync_worker = worker
        worker.start()

    @staticmethod
    def _same_jobs(a: list, b: list) -> bool:
        """True when two job lists hold the same generations in the same order.
        Used to skip a needless page rebuild (and its flicker) when a background
        refresh returns exactly what the warm cache already showed."""
        return [j.get("request_id") for j in a] == [j.get("request_id") for j in b]

    def _on_recent_jobs_fetched(self, jobs: list, has_more: bool = False):
        jobs = jobs or []
        self._recent_has_more = bool(has_more)
        changed = not self._same_jobs(jobs, self._recent_jobs)
        self._recent_jobs = jobs
        if changed:
            self._reload_dynamic_pages(keys=("recent",))
        self.history_synced.emit(self._recent_jobs, self._favorite_jobs)

    def _on_favorite_jobs_fetched(self, jobs: list):
        jobs = jobs or []
        changed = not self._same_jobs(jobs, self._favorite_jobs)
        self._favorite_jobs = jobs
        if changed:
            self._reload_dynamic_pages(keys=("user_favorites",))
        self.history_synced.emit(self._recent_jobs, self._favorite_jobs)

    def _on_sync_failed(self, msg: str):
        log_debug(f"Prompt library sync failed (local cache stays): {msg}")

    # -- Search ----------------------------------------------------------

    def _on_search_changed(self, text: str):
        """Search across all categories. Non-empty query → search-results page;
        empty query → restore the last sidebar tab."""
        query = text.strip().lower()
        if not query:
            if self._active_tab == "__search__":
                # Restore the tab the user was on before they started searching
                # (lazily building it if needed).
                target = (
                    self._previous_tab
                    if self._previous_tab in self._categories_by_key
                    else "favorites"
                )
                self._switch_to_tab(target)
            return

        # Entering search: remember current tab so we can restore it on clear.
        if self._active_tab != "__search__":
            self._previous_tab = self._active_tab
        self._rebuild_search_results(query)
        self._stack.setCurrentWidget(self._search_page)
        self._active_tab = "__search__"
        # Drop sidebar highlight while searching - no tab is "active".
        for btn in self._sidebar_buttons.values():
            btn.setStyleSheet(_SIDEBAR_ITEM)

    # -- Cleanup ---------------------------------------------------------

    def closeEvent(self, event):  # noqa: N802
        # Do NOT block here. Background workers are unparented and detached (see
        # _detach_worker): they finish on their own and self-delete, and their
        # data slots are bound methods Qt drops when this dialog is destroyed, so
        # nothing lands on a dead object. The old quit()+wait() froze the UI and
        # did nothing useful (a run()-override QThread has no event loop to quit).
        super().closeEvent(event)
