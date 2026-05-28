"""Prompt Library dialog.

Tab-style navigation: clicking a sidebar entry swaps the right pane.
Sidebar order: Favorites → Recent → (separator) → Top Picks → themed
categories. The user's own lists (Favorites, Recent) sit at the top; the
curated catalog follows. The themed categories are long (13 métiers), so only
the first few show by default and the rest collapse behind a "show more"
toggle. The dialog still opens on Top Picks regardless of sidebar order.

Recent and Favorites sync with the server: on open the dialog renders local
cache instantly, then fetches /api/plugin/history + /api/plugin/favorites in
the background and re-renders. Star toggles are optimistic (instant local
write) and posted to the server in a fire-and-forget worker.

Recent renders the full deduped history as plain text cards in pages of
_RECENT_PAGE_SIZE, growing with a "Load more" button so a power user with
thousands of generations never blocks the dialog open. Only Top Picks use
the rich before/after slider; Recent + Favorites stay text-only.
"""
from __future__ import annotations

import os
import re

try:  # SIP comes packaged with both PyQt5 and PyQt6 - used to detect dead C++ objects.
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover - defensive only
    _sip = None

from qgis.PyQt.QtCore import QSize, QThread, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QIcon, QPixmap
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
from ...core.date_format import format_smart_date
from ...core.i18n import tr
from ...core.logger import log_debug, log_warning
from ...core.prompts import prompt_history
from ...core.prompts.prompt_presets import (
    _CATEGORY_ORDER,
    format_template_prompt,
    get_all_categories,
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


# Page size for Recent's "Load more" pagination.
_RECENT_PAGE_SIZE = 50

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

# Bundled demo assets. Each Top Picks card reads its before/after preview
# from src/ui/demo_assets/<preset_id>/{before,after}.jpg, falling back to the
# server-hosted URL (or a text-only card) when no local image is shipped.
# JPEG keeps the bundle under ~900 KB for the 12 files combined.
_DEMO_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "demo_assets")

# Curated Top Picks shown when local demo assets exist. Empty/no assets ->
# the server's top_picks list is used as-is.
_LOCAL_TOP_PICKS_ORDER = [
    "detect_buildings",
    "detect_field_parcels",
    "simulate_flood_extent",
    "enhance_aerial",
    "detect_landcover_simple",
    "add_solar_panels",
]


def _local_demo_pixmap_paths(preset_id: str) -> tuple[str | None, str | None]:
    """Return (before_path, after_path) under demo_assets/<id>/, or (None, None)."""
    if not preset_id:
        return None, None
    folder = os.path.join(_DEMO_ASSETS_DIR, preset_id)
    before = os.path.join(folder, "before.jpg")
    after = os.path.join(folder, "after.jpg")
    return (
        before if os.path.isfile(before) else None,
        after if os.path.isfile(after) else None,
    )


def _has_any_local_demo() -> bool:
    """True when at least one preset folder lives under demo_assets/.
    Triggers both the local Top Picks filter and the local-pixmap path on cards."""
    if not os.path.isdir(_DEMO_ASSETS_DIR):
        return False
    try:
        for name in os.listdir(_DEMO_ASSETS_DIR):
            if os.path.isdir(os.path.join(_DEMO_ASSETS_DIR, name)):
                return True
    except OSError:
        return False
    return False


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

_CARD_NORMAL = (
    "QFrame#card { border: 1px solid rgba(128,128,128,0.15); "
    "border-radius: 4px; background: rgba(128,128,128,0.03); }"
)

_CARD_HOVER = (
    "QFrame#card { border: 1px solid rgba(128,128,128,0.35); "
    "border-radius: 4px; background: rgba(128,128,128,0.08); }"
)

_STAR_BTN = (
    "QToolButton { background: transparent; border: none; padding: 4px; "
    "border-radius: 4px; }"
    "QToolButton:hover { background: rgba(128,128,128,0.18); }"
)

_DISCLOSURE_BTN = (
    "QToolButton { background: transparent; border: none; padding: 2px 6px; "
    "border-radius: 4px; color: palette(text); font-size: 16px; }"
    "QToolButton:hover { background: rgba(128,128,128,0.18); }"
)

_PROMPT_BODY = (
    "QLabel { color: palette(text); font-size: 12px; font-weight: 400; "
    "background: rgba(128,128,128,0.05); border: 1px solid rgba(128,128,128,0.15); "
    "border-radius: 4px; padding: 8px 10px; }"
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

_SENTENCE_END = re.compile(r"\.(?=\s|$)|\n")


def _truncate(text: str, n: int = _MAX_TITLE_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def _first_sentence(text: str) -> str:
    """First sentence (cut at the first '. ' or newline), used as Recent title."""
    text = (text or "").strip()
    m = _SENTENCE_END.search(text)
    if not m:
        return text
    end = m.start() + 1 if text[m.start()] == "." else m.start()
    return text[:end].rstrip()


# Cap for the muted prompt snippet that sits permanently under each Top Picks
# card title. Big enough for the first sentence's intent, small enough that
# the snippet wraps to 2-3 lines max inside a 250-px card.
_CARD_SNIPPET_MAX_CHARS = 100


def _prompt_snippet(prompt: str) -> str:
    """Flatten whitespace, truncate to ``_CARD_SNIPPET_MAX_CHARS`` and end
    in an ellipsis. Used as the always-visible subtitle line under the
    Top Picks card title - hints at what the prompt actually outputs."""
    flat = " ".join((prompt or "").split())
    if len(flat) <= _CARD_SNIPPET_MAX_CHARS:
        return flat
    return flat[:_CARD_SNIPPET_MAX_CHARS].rstrip() + "..."


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
        telemetry.track("favorite_toggled", {"now_favorited": now_fav})
        telemetry.flush()
        self.refresh()
        self.toggled_state.emit(
            self._prompt,
            now_fav,
            self._label or "",
            self._source_category or "",
        )


def _build_prompt_disclosure(parent, prompt_text: str) -> tuple[QToolButton, QLabel]:
    """Small chevron button + hidden wrapped QLabel revealing the full prompt.
    Returned widgets are added to the card by the caller; the button toggles
    the label's visibility (collapsed by default).
    """
    btn = QToolButton(parent)
    btn.setText("▸")
    btn.setCheckable(True)
    btn.setFixedSize(26, 26)
    btn.setCursor(QtC.PointingHandCursor)
    btn.setStyleSheet(_DISCLOSURE_BTN)
    btn.setToolTip(tr("Show prompt"))
    btn.setAccessibleName(tr("Show full prompt"))

    # Same paragraph-break formatter the main prompt textarea uses - keeps the
    # disclosed text readable instead of one wall of run-on sentences.
    body = QLabel(format_template_prompt(prompt_text or ""))
    body.setWordWrap(True)
    body.setTextFormat(QtC.PlainText)
    body.setStyleSheet(_PROMPT_BODY)
    body.setVisible(False)

    def _on_toggled(checked: bool):
        body.setVisible(checked)
        btn.setText("▾" if checked else "▸")
        btn.setToolTip(tr("Hide prompt") if checked else tr("Show prompt"))

    btn.toggled.connect(_on_toggled)
    return btn, body


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


class _ClickableCard(QFrame):
    """One preset card: title + star + optional prompt disclosure + optional date."""

    def __init__(self, preset: dict, on_click, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._preset = preset
        self._on_click = on_click
        self.setCursor(QtC.PointingHandCursor)
        self.setStyleSheet(_CARD_NORMAL)

        from_favorites = bool(preset.get("from_favorites"))
        has_template = bool(preset.get("source_category"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 8, 10)
        outer.setSpacing(6)

        # Favorites mix curated templates and the user's own prompts; a small
        # origin pill at the top-left tells them apart (#128). Other tabs don't
        # need it: Top Picks and themed pages are all templates, Recent shows a
        # date.
        if from_favorites:
            pill_row = QHBoxLayout()
            pill_row.setContentsMargins(0, 0, 0, 0)
            pill_row.addWidget(_build_origin_pill(self, has_template))
            pill_row.addStretch()
            outer.addLayout(pill_row)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        # Two rendering modes:
        # - Title card (curated/themed templates, Top Picks, and every Recent
        #   or Favorites entry): a short title + chevron revealing the full
        #   formatted prompt. A named template shows its label; a user-typed
        #   prompt shows its first sentence so the list stays scannable instead
        #   of stacking walls of run-on text. Before #128, favorited user
        #   prompts skipped this and rendered the entire prompt with no title.
        # - Plain body: a bare user-typed prompt with no template and no
        #   Recent/Favorites context (fallback only).
        title_card = bool(preset.get("from_recent")) or from_favorites or has_template
        disclosure_body: QLabel | None = None
        if title_card:
            disclosure_btn, disclosure_body = _build_prompt_disclosure(self, preset["prompt"])
            # Chevron at the left edge so the revealed prompt sits directly
            # under the affordance that opened it.
            row.addWidget(disclosure_btn, 0, QtC.AlignTop)
            title_text = (
                _truncate(preset["label"]) if has_template
                else _truncate(_first_sentence(preset["prompt"]))
            )
            text = QLabel(title_text)
            text.setWordWrap(True)
            text.setTextFormat(QtC.PlainText)
            text.setStyleSheet(
                "color: palette(text); font-size: 13px; font-weight: 600; "
                "background: transparent; border: none;"
            )
            row.addWidget(text, 1)
        else:
            text = QLabel(preset["prompt"])
            text.setWordWrap(True)
            text.setTextFormat(QtC.PlainText)
            text.setStyleSheet(
                "color: palette(text); font-size: 12px; font-weight: 400; "
                "background: transparent; border: none;"
            )
            row.addWidget(text, 1)

        self._star = _StarButton(
            preset["prompt"],
            preset.get("label") if preset.get("source_category") else None,
            preset.get("source_category"),
            self,
        )
        # Anchor the star top-right when the card grows tall (multi-line text).
        row.addWidget(self._star, 0, QtC.AlignTop)
        outer.addLayout(row)
        if disclosure_body is not None:
            outer.addWidget(disclosure_body)

        # Recent cards carry a generation timestamp - surface it as a small
        # muted line so the user can tell entries apart at a glance.
        if preset.get("from_recent"):
            date_text = format_smart_date(preset.get("ts") or "")
            if date_text:
                date_label = QLabel(date_text)
                # Muted via a softer gray so it adapts to both light and dark themes.
                date_label.setStyleSheet(
                    "color: rgba(128,128,128,0.85); font-size: 11px; "
                    "background: transparent; border: none;"
                )
                outer.addWidget(date_label)

    def star_button(self) -> _StarButton:
        return self._star

    def preset(self) -> dict:
        return self._preset

    def enterEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_HOVER)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_NORMAL)
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == QtC.LeftButton:
            self._on_click(self._preset)
        super().mousePressEvent(event)


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
    CARD_WIDTH = 250
    SLIDER_WIDTH = 250
    SLIDER_HEIGHT = 140  # ~16:9 cinematic crop

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
        self.setFixedWidth(self.CARD_WIDTH)

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
        self._slider = BeforeAfterSlider(self, auto_loop=False)
        self._slider.setFixedSize(self.SLIDER_WIDTH, self.SLIDER_HEIGHT)
        self._slider.clicked.connect(self._emit_click)
        outer.addWidget(self._slider)

        # --- footer block: title + star, with a permanent prompt snippet ---
        # No chevron, no popup: the prompt snippet is shown muted under the
        # title at all times. Card height is fixed (slider + footer + snippet)
        # so every cell in the grid is the same size; nothing ever pushes a
        # sibling around when the user interacts with one card.
        footer_wrap = QWidget(self)
        footer_outer = QVBoxLayout(footer_wrap)
        footer_outer.setContentsMargins(10, 6, 8, 8)
        footer_outer.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        label = QLabel(_truncate(preset["label"]))
        label.setStyleSheet(
            "color: palette(text); font-size: 12px; font-weight: 600; "
            "background: transparent; border: none;"
        )
        title_row.addWidget(label, 1, QtC.AlignVCenter)

        self._star = _StarButton(
            preset["prompt"],
            preset.get("label"),
            preset.get("source_category"),
            self,
        )
        title_row.addWidget(self._star, 0, QtC.AlignVCenter)
        footer_outer.addLayout(title_row)

        # Permanent muted snippet. Word-wraps inside the 250-px card; the
        # 100-char cap keeps it under 3 lines regardless of font metrics.
        snippet_text = _prompt_snippet(preset.get("prompt") or "")
        if snippet_text:
            snippet = QLabel(snippet_text)
            snippet.setWordWrap(True)
            snippet.setTextFormat(QtC.PlainText)
            snippet.setStyleSheet(
                "color: rgba(170,170,170,0.75); font-size: 11px; "
                "background: transparent; border: none;"
            )
            footer_outer.addWidget(snippet)

        outer.addWidget(footer_wrap)

        # --- demo image loading ---
        # Two parallel sources, tried in order so the slider ends up populated
        # either way:
        #   1. Local on-disk pixmaps under .demo_assets/<preset_id>/ - dev path,
        #      lets us iterate before the server has the demo URLs seeded.
        #   2. Server-hosted demos via `demo_loader` + `absolute_url` - prod
        #      path; the loader caches bytes so the second open is instant.
        self._demo_loader = None
        tid = preset.get("id", "")
        local_before, local_after = _local_demo_pixmap_paths(tid)
        if local_before:
            pm = QPixmap(local_before)
            if not pm.isNull():
                self._slider.set_before(pm)
        if local_after:
            pm = QPixmap(local_after)
            if not pm.isNull():
                self._slider.set_after(pm)

        if demo_loader is not None and absolute_url is not None:
            url_before = preset.get("demo_url_before")
            url_after = preset.get("demo_url_after")
            # Only wire the loader for sides that are NOT already covered by a
            # local pixmap - avoids an unnecessary network fetch when devs have
            # the asset on disk.
            self._demo_loader = demo_loader
            demo_loader.loaded.connect(self._on_demo_loaded)
            if tid and url_before and not local_before:
                demo_loader.request(tid, "before", absolute_url(url_before))
            if tid and url_after and not local_after:
                demo_loader.request(tid, "after", absolute_url(url_after))

    def _on_demo_loaded(self, template_id: str, which: str, pixmap) -> None:
        if template_id != self._preset.get("id"):
            return
        if which == "before":
            self._slider.set_before(pixmap)
        elif which == "after":
            self._slider.set_after(pixmap)

    def deleteLater(self):  # noqa: N802 - Qt signature
        # Drop the demo_loader signal connection so an inflight image load
        # never tries to paint into a destroyed card.
        if self._demo_loader is not None:
            try:
                self._demo_loader.loaded.disconnect(self._on_demo_loaded)
            except (RuntimeError, TypeError):
                pass
        super().deleteLater()

    def _emit_click(self):
        self._on_click(self._preset)

    def star_button(self) -> _StarButton:
        return self._star

    def preset(self) -> dict:
        return self._preset

    def enterEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_HOVER)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setStyleSheet(_CARD_NORMAL)
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        # Slider has its own click semantic; only fire if click hit the
        # footer area below the slider.
        if event.button() == QtC.LeftButton:
            y = QtC.event_pos(event).y()
            if y >= self._slider.height():
                self._on_click(self._preset)
        super().mousePressEvent(event)


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
    """Background fetch of /api/plugin/history + /api/plugin/favorites.
    Emits one signal per successful section so the dialog can refresh
    progressively (history can return before favorites or vice-versa).

    Recent + Favorites render as text-only cards; we no longer pull the rich
    before/after history endpoint here (Top Picks owns the slider UI)."""

    history_fetched = pyqtSignal(list)
    favorites_fetched = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, client, auth: dict, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth

    def run(self):
        try:
            hist = self._client.get_history(self._auth)
        except Exception as e:
            self.failed.emit(f"history: {e}")
            return
        if isinstance(hist, dict) and "error" not in hist:
            self.history_fetched.emit(hist.get("prompts", []) or [])
        else:
            self.failed.emit(
                f"history: {hist.get('error', 'unknown') if isinstance(hist, dict) else 'parse_error'}"
            )

        try:
            favs = self._client.get_favorites(self._auth)
        except Exception as e:
            self.failed.emit(f"favorites: {e}")
            return
        if isinstance(favs, dict) and "error" not in favs:
            self.favorites_fetched.emit(favs.get("favorites", []) or [])
        else:
            self.failed.emit(
                f"favorites: {favs.get('error', 'unknown') if isinstance(favs, dict) else 'parse_error'}"
            )


class _FavoriteSyncWorker(QThread):
    """Fire-and-forget POST/DELETE for a single favorite toggle."""

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

    def __init__(
        self,
        parent=None,
        client=None,
        auth_provider=None,
        server_catalog: dict | None = None,
        browse_only: bool = False,
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
        # Wider default so the 4-card grid + slider previews have room to breathe.
        self.resize(1100, 720)
        self.setSizeGripEnabled(True)

        self._client = client
        self._auth_provider = auth_provider
        self._server_catalog = server_catalog

        # Lazy-instantiated demo image fetcher (only when server catalog is
        # available - otherwise there's nothing to fetch).
        self._demo_loader = None
        if server_catalog is not None and client is not None:
            from ..template_demo_loader import TemplateDemoLoader

            self._demo_loader = TemplateDemoLoader(self)

        self._selected_preset: dict | None = None
        self._categories_by_key: dict[str, dict] = {}
        self._sidebar_buttons: dict[str, _SidebarButton] = {}
        # Collapsed themed-category sidebar buttons (beyond the visible few) and
        # the toggle that reveals them. Populated in _build_ui.
        self._collapsed_category_btns: list[_SidebarButton] = []
        self._category_toggle_btn: QPushButton | None = None
        self._categories_expanded: bool = False
        self._pages: dict[str, QWidget] = {}
        # Cards mix _ClickableCard and _BeforeAfterCard - store as generic
        # widgets keyed by the page they live on. Star refresh uses
        # `card.star_button()` and `card.preset()`, both implemented on both.
        self._card_widgets: list[tuple[QWidget, str]] = []
        # Default landing: Top Picks. First-time users see curated content,
        # not their empty Recent/Favorites.
        self._active_tab: str = "favorites"
        self._sync_worker: _LibrarySyncWorker | None = None

        # Recent pagination - rebuilt fresh every time the Recent page renders.
        self._recent_layout: QVBoxLayout | None = None
        self._recent_load_more_btn: QPushButton | None = None
        self._recent_visible_count: int = 0
        # Themed-category pagination state, keyed by category key. Each entry
        # carries the layout, load-more button, and how many cards are visible.
        # Rebuilt fresh every time _build_page runs for a themed tab.
        self._themed_state: dict[str, dict] = {}

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
        self._apply_local_top_picks_filter()

    def _apply_local_top_picks_filter(self):
        """Override Top Picks with a curated local list when .demo_assets/
        is present. Dev-only - lets us preview the trimmed grid layout
        before the server's top_picks list is curated. Prod (no folder) keeps
        the server's order untouched.

        Looks up presets across EVERY themed category, not just the server's
        Top Picks list - so we can promote a preset (e.g. detect_landcover_simple)
        that the server has not yet flagged as top_pick."""
        if not _has_any_local_demo():
            return
        fav = self._categories_by_key.get("favorites")
        if fav is None:
            return
        # Index every preset in every themed category by id so any local
        # override target can be found regardless of where it lives in the
        # server catalog.
        by_id: dict[str, dict] = {}
        for cat_key, cat in self._categories_by_key.items():
            if cat_key in ("favorites", "user_favorites", "recent"):
                continue
            for p in cat.get("presets", []) or []:
                pid = p.get("id")
                if isinstance(pid, str) and pid and pid not in by_id:
                    by_id[pid] = p
        # Fallback: include the server's own Top Picks entries too, since
        # those normalized presets aren't necessarily mirrored in their
        # themed category (e.g. recent server-only experimental flagging).
        for p in fav.get("presets", []) or []:
            pid = p.get("id")
            if isinstance(pid, str) and pid and pid not in by_id:
                by_id[pid] = p

        ordered = [by_id[pid] for pid in _LOCAL_TOP_PICKS_ORDER if pid in by_id]
        if ordered:
            fav["presets"] = ordered

    # -- Layout ----------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(tr("Search prompts..."))
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

        # Stack of pages, one per tab + a hidden search-results page.
        self._stack = QStackedWidget()
        for key in _TAB_ORDER:
            if key == "__separator__":
                continue
            if key not in self._categories_by_key:
                continue
            page = self._build_page(key)
            self._pages[key] = page
            self._stack.addWidget(page)
        # Search results page - shown when the search input is non-empty.
        self._search_page = self._build_search_page()
        self._stack.addWidget(self._search_page)

        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)

        # Remember which tab to restore when the search box is cleared.
        self._previous_tab: str = self._active_tab
        self._switch_to_tab(self._active_tab)

    def _build_sidebar_button(self, key: str, category: dict) -> _SidebarButton:
        count = None
        if key in _TABS_WITH_COUNT:
            count = len(category.get("presets", []))
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
            count = len(cat.get("presets", []))
        self._sidebar_buttons[key].set_label_html(
            _sidebar_icon_html(key),
            _tab_label(key, cat["label"], count),
        )

    # -- Pages -----------------------------------------------------------

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
                grid = QGridLayout(grid_host)
                grid.setContentsMargins(0, 0, 0, 0)
                grid.setHorizontalSpacing(10)
                grid.setVerticalSpacing(10)
                self._populate_grid_cards(grid, presets, key, columns=3)
                # Center the grid horizontally so it sits like a poster on the
                # page rather than hugging the left edge of the scroll area.
                grid_row = QHBoxLayout()
                grid_row.setContentsMargins(0, 0, 0, 0)
                grid_row.addStretch()
                grid_row.addWidget(grid_host)
                grid_row.addStretch()
                outer_v.addLayout(grid_row)
            outer_v.addStretch()
        elif key == "recent":
            layout = QVBoxLayout(content)
            layout.setContentsMargins(6, 4, 6, 10)
            layout.setSpacing(6)
            presets = category["presets"]
            if not presets:
                empty = self._build_empty_state(key)
                if empty is not None:
                    layout.addWidget(empty)
                self._recent_layout = None
                self._recent_load_more_btn = None
                self._recent_visible_count = 0
            else:
                visible = min(_RECENT_PAGE_SIZE, len(presets))
                self._populate_list_cards(layout, presets[:visible], key)
                btn = QPushButton(self._load_more_label(len(presets) - visible))
                btn.setStyleSheet(_LOAD_MORE_BTN)
                btn.setCursor(QtC.PointingHandCursor)
                btn.clicked.connect(self._on_load_more_recent)
                btn.setVisible(visible < len(presets))
                row = QHBoxLayout()
                row.setContentsMargins(0, 6, 0, 0)
                row.addStretch()
                row.addWidget(btn)
                row.addStretch()
                layout.addLayout(row)
                self._recent_layout = layout
                self._recent_load_more_btn = btn
                self._recent_visible_count = visible
            layout.addStretch()
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
                self._populate_list_cards(layout, reliable, key)

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
        layout = state["layout"]
        if exp_btn is None or not _is_alive(exp_btn) or not experimental:
            return
        insert_at = self._row_index_of(layout, exp_btn)
        header = QLabel(tr("EXPERIMENTAL (may produce unexpected results)"))
        header.setStyleSheet(_EXPERIMENTAL_HEADER)
        header.setWordWrap(True)
        layout.insertWidget(insert_at, header)
        insert_at += 1
        for preset in experimental:
            card = _ClickableCard(preset, self._on_card_clicked)
            card.star_button().toggled_state.connect(self._on_star_toggled)
            layout.insertWidget(insert_at, card)
            self._card_widgets.append((card, key))
            insert_at += 1
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

    @staticmethod
    def _load_more_label(remaining: int) -> str:
        return tr("Load more ({n} remaining)").format(n=remaining)

    def _on_load_more_recent(self):
        """Append the next batch of Recent cards above the load-more button."""
        cat = self._categories_by_key.get("recent")
        if cat is None or self._recent_layout is None or self._recent_load_more_btn is None:
            return
        if not _is_alive(self._recent_load_more_btn):
            return
        presets = cat["presets"]
        cur = self._recent_visible_count
        if cur >= len(presets):
            self._recent_load_more_btn.setVisible(False)
            return
        next_batch = presets[cur: cur + _RECENT_PAGE_SIZE]

        # Insert cards before the load-more button so the button stays anchored
        # at the bottom of the list as more entries scroll in.
        btn_parent_row = None
        for i in range(self._recent_layout.count()):
            item = self._recent_layout.itemAt(i)
            if item is None:
                continue
            if item.layout() is not None and item.layout().indexOf(self._recent_load_more_btn) >= 0:
                btn_parent_row = i
                break
        insert_at = btn_parent_row if btn_parent_row is not None else self._recent_layout.count() - 1
        for preset in next_batch:
            card = _ClickableCard(preset, self._on_card_clicked)
            card.star_button().toggled_state.connect(self._on_star_toggled)
            self._recent_layout.insertWidget(insert_at, card)
            self._card_widgets.append((card, "recent"))
            insert_at += 1

        self._recent_visible_count += len(next_batch)
        remaining = len(presets) - self._recent_visible_count
        if remaining <= 0:
            self._recent_load_more_btn.setVisible(False)
        else:
            self._recent_load_more_btn.setText(self._load_more_label(remaining))

    def _populate_grid_cards(
        self, grid: QGridLayout, presets: list[dict], page_key: str, columns: int = 3
    ):
        """Top Picks layout: compact BeforeAfterCard cells in a 3x2 grid.

        Renders the slider card when ANY demo source is available - either a
        local pixmap under .demo_assets/<id>/ OR a server-hosted demo URL.
        Falls back to a text card when neither is present so the grid still
        renders even before any before/after asset exists."""
        for idx, preset in enumerate(presets):
            row, col = divmod(idx, columns)
            card = self._build_top_pick_card(preset)
            grid.addWidget(card, row, col)
            self._card_widgets.append((card, page_key))

    def _build_top_pick_card(self, preset: dict) -> QFrame:
        """One Top Picks card: compact slider when a demo is available, plain
        text card otherwise. Same fallback rules for every cell so the grid
        stays uniform even when some prompts lack assets."""
        tid = preset.get("id", "")
        local_before, local_after = _local_demo_pixmap_paths(tid)
        has_local = bool(local_before or local_after)
        has_url = bool(preset.get("demo_url_before") or preset.get("demo_url_after"))
        # Slider needs SOMETHING to show; without either source the rich card
        # would render an empty placeholder, so we drop back to the text card.
        if has_local or (has_url and self._demo_loader is not None and self._client is not None):
            from ...core.prompts.prompt_presets_client import absolute_demo_url

            def _abs(rel, _client=self._client):
                return absolute_demo_url(_client, rel)

            card = _BeforeAfterCard(
                preset,
                self._on_card_clicked,
                demo_loader=self._demo_loader,
                absolute_url=_abs,
            )
        else:
            card = _ClickableCard(preset, self._on_card_clicked)
        card.star_button().toggled_state.connect(self._on_star_toggled)
        return card

    def _populate_list_cards(self, layout: QVBoxLayout, presets: list[dict], page_key: str):
        """All non-Top-Picks tabs: vertical list of plain text cards."""
        for preset in presets:
            card = _ClickableCard(preset, self._on_card_clicked)
            card.star_button().toggled_state.connect(self._on_star_toggled)
            layout.addWidget(card)
            self._card_widgets.append((card, page_key))

    # Back-compat shim - search results layout still calls _populate_cards;
    # keep it for non-grid contexts and route to the list variant.
    def _populate_cards(self, layout: QVBoxLayout, presets: list[dict], page_key: str):
        self._populate_list_cards(layout, presets, page_key)

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
            category = self._categories_by_key.get(key)
            if category is None:
                continue
            matches = [
                p for p in category.get("presets", []) if _preset_matches(p, query)
            ]
            if not matches:
                continue
            self._search_layout.addWidget(self._search_section_header(key, len(matches)))
            self._populate_cards(self._search_layout, matches, "__search__")
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
                "Nothing here yet. The prompts you run will land here, ready to replay."
            )
        elif key == "user_favorites":
            icon_path = _STAR_OUTLINE_SVG
            message = tr(
                "No favorites yet. Tap the ★ on any prompt to keep it close."
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

    def _switch_to_tab(self, key: str):
        """Show the stack page for `key` and highlight its sidebar button.
        Does not touch the search input - clearing the search is the caller's
        responsibility (see _on_sidebar_click)."""
        if key not in self._pages:
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

    # -- Interaction -----------------------------------------------------

    def _on_card_clicked(self, preset: dict):
        if self._browse_only:
            return
        self._selected_preset = preset
        if preset.get("from_recent"):
            telemetry.track("recent_selected")
            telemetry.flush()
        self.accept()

    def _prune_dead_cards(self):
        """Drop card refs whose underlying Qt widget has been destroyed."""
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets
            if _is_alive(c) and _is_alive(c.star_button())
        ]

    def _on_star_toggled(self, prompt: str, now_fav: bool, label: str, source_cat: str):
        # Refresh every visible star button (other tabs may show the same prompt).
        # Defensive: cards can be destroyed mid-iteration when a page rebuild
        # races with the star-toggled signal - skip ghost widgets cleanly.
        self._prune_dead_cards()
        for card, _ in list(self._card_widgets):
            try:
                card.star_button().refresh()
            except RuntimeError:
                continue
        # Defer the page rebuild so the star button finishes its click handler
        # before its parent page gets deleteLater'd. Destroying a widget mid
        # signal-emission crashes QGIS on Qt6.
        QTimer.singleShot(0, lambda: self._reload_dynamic_pages(keys=("user_favorites",)))
        # Fire server sync (fire-and-forget).
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
        """Rebuild the named dynamic tabs from current cache. Callers pass
        only the subset that actually changed so untouched tabs keep their
        scroll position (Load-more state in particular)."""
        self._load_categories()

        keyset = set(keys)
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if k not in keyset
        ]

        for key in keys:
            if key not in self._pages:
                continue
            old = self._pages[key]
            idx = self._stack.indexOf(old)
            new = self._build_page(key)
            self._stack.insertWidget(idx, new)
            self._stack.removeWidget(old)
            old.deleteLater()
            self._pages[key] = new

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

    # -- Server sync -----------------------------------------------------

    def _start_sync(self):
        """Kick off a background fetch of /history + /favorites. If client/auth
        unavailable, silently skips - local cache is the source for this session."""
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
        worker.history_fetched.connect(self._on_history_fetched)
        worker.favorites_fetched.connect(self._on_favorites_fetched)
        worker.failed.connect(self._on_sync_failed)
        _detach_worker(worker)
        self._sync_worker = worker
        worker.start()

    def _on_history_fetched(self, prompts: list):
        prompt_history.replace_recent(prompts)
        self._reload_dynamic_pages(keys=("recent",))

    def _on_favorites_fetched(self, favorites: list):
        prompt_history.replace_favorites(favorites)
        self._reload_dynamic_pages(keys=("user_favorites",))

    def _on_sync_failed(self, msg: str):
        log_debug(f"Prompt library sync failed (local cache stays): {msg}")

    # -- Search ----------------------------------------------------------

    def _on_search_changed(self, text: str):
        """Search across all categories. Non-empty query → search-results page;
        empty query → restore the last sidebar tab."""
        query = text.strip().lower()
        if not query:
            if self._active_tab == "__search__":
                # Restore the tab the user was on before they started searching.
                target = self._previous_tab if self._previous_tab in self._pages else "favorites"
                self._active_tab = target
                self._stack.setCurrentWidget(self._pages[target])
                for k, btn in self._sidebar_buttons.items():
                    btn.setStyleSheet(_SIDEBAR_ITEM_ACTIVE if k == target else _SIDEBAR_ITEM)
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
