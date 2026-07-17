"""Shared constants, styles, and small helpers for the Prompt Library."""
from __future__ import annotations

import os

try:  # SIP comes packaged with both PyQt5 and PyQt6 - used to detect dead C++ objects.
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover - defensive only
    _sip = None

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QLabel

from ....core import qt_compat as QtC
from ....core.i18n import tr
from ....core.prompts.prompt_presets import _CATEGORY_ORDER


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


_PLUGIN_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
)
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

# Need-group headers (Classify / Project / Render): clickable rows that fold
# their categories. These ARE the primary structure of the template list (the
# redundant "Templates" divider is gone), so they follow the design system's
# section-header spec - bold, palette(text), normal case, no letter-spacing -
# rather than the faint grey small-caps used for the "Your prompts" divider.
# That keeps them a clearly heavier tier than the divider above them instead
# of a second stack of grey uppercase labels. Chevron sits inline with the
# text (no icon column) so the fold affordance reads without a glyph column.
_NEED_HEADER_BTN = (
    "QPushButton { text-align: left; border: none; border-radius: 4px; "
    "padding: 10px 12px 4px 12px; font-size: 13px; font-weight: 700; "
    "color: palette(text); background: transparent; }"
    "QPushButton:hover { background: rgba(128,128,128,0.10); }"
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

# ---------------------------------------------------------------------------
# Landing redesign: need tiles, feed segmented control, category chips, back.
# Same visual language as the gallery cards (neutral frame at rest, brand-green
# lift on hover) so the landing reads as one system. No new hues: the per-need
# tile accent is applied at build time from the need's representative category
# glyph colour (reused from `_SIDEBAR_GLYPHS`), never invented here.
# ---------------------------------------------------------------------------
_NEED_TILE = (
    "QFrame#needtile { border: 1px solid rgba(128,128,128,0.30); "
    "border-radius: 8px; background: rgba(128,128,128,0.05); }"
)
_NEED_TILE_HOVER = (
    "QFrame#needtile { border: 1px solid rgba(139,172,39,0.75); "
    "border-radius: 8px; background: rgba(139,172,39,0.09); }"
)
_NEED_TILE_TITLE = (
    "QLabel { color: palette(text); font-size: 16px; font-weight: 700; "
    "background: transparent; border: none; }"
)
_NEED_TILE_SUB = (
    "QLabel { color: palette(text); font-size: 11px; "
    "background: transparent; border: none; }"
)
_NEED_TILE_COUNT = (
    "QLabel { color: rgba(128,128,128,0.9); font-size: 11px; "
    "background: transparent; border: none; }"
)

# Feed segmented control (Popular / Recent / Favorites): reuses the sidebar
# item's rest/active language, laid out horizontally inside a subtle track.
_FEED_SEG = "QWidget#feedseg { background: rgba(128,128,128,0.08); border-radius: 6px; }"
_FEED_SEG_BTN = (
    "QPushButton { border: none; border-radius: 4px; padding: 6px 14px; "
    "font-size: 13px; color: palette(text); background: transparent; }"
    "QPushButton:hover { background: rgba(128,128,128,0.12); }"
)
_FEED_SEG_BTN_ACTIVE = (
    "QPushButton { border: none; border-radius: 4px; padding: 6px 14px; "
    "font-size: 13px; font-weight: bold; color: palette(text); "
    "background: rgba(128,128,128,0.20); }"
)

# Category filter chips on a need page (All / Land cover / ...): neutral pill at
# rest (matches _ORIGIN_PILL), brand-green tint when active.
_NEED_CHIP = (
    "QPushButton { background: rgba(128,128,128,0.10); border: none; "
    "border-radius: 11px; padding: 4px 12px; font-size: 12px; color: palette(text); }"
    "QPushButton:hover { background: rgba(128,128,128,0.18); }"
)
_NEED_CHIP_ACTIVE = (
    "QPushButton { background: rgba(139,172,39,0.20); border: none; "
    "border-radius: 11px; padding: 4px 12px; font-size: 12px; "
    "font-weight: 700; color: palette(text); }"
)

# Flat back button on a need-page header.
_BACK_BTN = (
    "QPushButton { background: transparent; border: 1px solid rgba(128,128,128,0.3); "
    "border-radius: 4px; padding: 4px 10px; font-size: 14px; color: palette(text); }"
    "QPushButton:hover { background: rgba(128,128,128,0.12); }"
)

# Compact borderless back arrow (need / feed-all headers): small footprint,
# green on hover, no box.
_BACK_BTN_SMALL = (
    "QPushButton { background: transparent; border: none; font-size: 17px; "
    "color: palette(text); padding: 0px; }"
    "QPushButton:hover { color: #8bac27; }"
)

# Sub-group selector as underlined tabs: exactly one active (a brand-green
# underline), the rest plain. Reads as single-select, unlike the pill chips
# that looked like an accumulating multi-select.
_NEED_TAB = (
    "QPushButton { border: none; background: transparent; padding: 6px 2px 5px 2px; "
    "font-size: 13px; color: palette(text); }"
    "QPushButton:hover { color: #8bac27; }"
)
_NEED_TAB_ACTIVE = (
    "QPushButton { border: none; border-bottom: 2px solid #8bac27; background: transparent; "
    "padding: 6px 2px 3px 2px; font-size: 13px; font-weight: 700; color: palette(text); }"
)

# "See all" link on a landing feed row.
_FEED_LINK = (
    "QPushButton { background: transparent; border: none; font-size: 12px; "
    "color: #8bac27; padding: 2px 4px; }"
    "QPushButton:hover { color: #4d7c0f; }"
)

# "See all" as a visible bordered button, sat at the bottom-right of a feed.
_SEE_ALL_BTN = (
    "QPushButton { background: rgba(139,172,39,0.12); border: 1px solid rgba(139,172,39,0.5); "
    "border-radius: 6px; padding: 6px 14px; font-size: 12px; font-weight: 600; color: #8bac27; }"
    "QPushButton:hover { background: rgba(139,172,39,0.20); }"
)

# Need-tile family name (dominant) and its inline "Explore" affordance.
_NEED_TILE_NAME = (
    "QLabel { color: palette(text); font-size: 18px; font-weight: 800; "
    "background: transparent; border: none; }"
)
_NEED_TILE_EXPLORE = (
    "QLabel { color: #8bac27; font-size: 12px; font-weight: 700; "
    "background: transparent; border: none; }"
)

# Per-family accent colours, taken from the category glyph palette (not
# invented): green = Classify/Analyser, blue = Project/Simuler,
# violet = Render/Habiller. Keyed by need key so it survives a label rename.
_NEED_ACCENT = {"classify": "#68a868", "project": "#5ca0c0", "render": "#9880b0"}
# Family emblem glyphs (reused from _SIDEBAR_GLYPHS so they are known to render).
_NEED_ICON = {"classify": "◉", "project": "⛅", "render": "❖"}

# Family name + arrow overlaid on the tile image (white on a coloured scrim),
# so a family tile reads as a branded portal, not a prompt card.
_TILE_NAME_OVERLAY = (
    "QLabel { color: #ffffff; font-size: 22px; font-weight: 800; "
    "background: transparent; border: none; }"
)
_TILE_ARROW_OVERLAY = (
    "QLabel { color: #ffffff; font-size: 20px; font-weight: 700; "
    "background: transparent; border: none; }"
)

# Landing section heading and feed subtitle.
_LANDING_HEADING = (
    "QLabel { color: palette(text); font-size: 14px; font-weight: 700; "
    "background: transparent; border: none; }"
)
_FEED_SUBTITLE = (
    "QLabel { color: palette(text); font-size: 11px; "
    "background: transparent; border: none; }"
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

# QSettings key remembering a need group's folded state across sessions.
# Groups start expanded; only an explicit user fold is persisted.
_NEED_COLLAPSED_SETTING = "AIEdit/library_need_collapsed_{key}"

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


def _card_prompt(prompt: str, n: int = 66) -> str:
    """Flatten whitespace and truncate at a word boundary so the prompt fits
    ~2 lines on a card without an ugly mid-word cut."""
    flat = " ".join((prompt or "").split())
    if len(flat) <= n:
        return flat
    cut = flat[:n].rsplit(" ", 1)[0] or flat[:n]
    return cut.rstrip(" ,.;:-") + "…"
