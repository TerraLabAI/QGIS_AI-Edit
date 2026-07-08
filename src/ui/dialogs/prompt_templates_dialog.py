"""Prompt Library dialog.

Tab-style navigation: clicking a sidebar entry swaps the right pane.
Sidebar order: Favorites → Recent → Top Picks → (separator) → themed
categories. The user's own lists (Favorites, Recent) and the Top Picks
shortcut sit at the top; the themed catalog follows below the divider. The
themed categories are long (13 métiers), so they are grouped under three
high-level needs (Classify / Project / Render); each group folds via its
header and remembers its state across sessions. The dialog still opens on Top
Picks regardless of sidebar order.

Recent and Favorites are the user's own past generations, fetched from the
server in the background: each renders as a before/after card carrying the
prompt, location, and signed input/output URLs. From a card the user can reuse
the prompt, add the output back to the map as a georeferenced layer, download
it, or star it. Top Picks and the themed categories stay curated prompt cards.

Generation favorites (the ★ on a Recent/Favorites card) are a separate concept
from prompt favorites (the ★ on a curated template) and sync to their own
endpoint.
"""

from .prompt_templates.cards import _BeforeAfterCard, _StarButton
from .prompt_templates.common import (
    _CARD_HOVER,
    _CARD_NORMAL,
    _CARD_PROMPT_CHARS,
    _CARD_TITLE_H,
    _EMPTY_MSG,
    _EXPERIMENTAL_BTN,
    _EXPERIMENTAL_HEADER,
    _GALLERY_PAGE_SIZE,
    _HISTORY_SVG,
    _ICON_CACHE,
    _ICONS_DIR,
    _LOAD_MORE_BTN,
    _MAX_TITLE_CHARS,
    _NEED_COLLAPSED_SETTING,
    _NEED_HEADER_BTN,
    _ORIGIN_PILL,
    _PLUGIN_DIR,
    _SEARCH_BOX,
    _SIDEBAR_GLYPHS,
    _SIDEBAR_ITEM,
    _SIDEBAR_ITEM_ACTIVE,
    _STAR_BTN,
    _STAR_FILLED_SVG,
    _STAR_OUTLINE_SVG,
    _TAB_ORDER,
    _TABS_WITH_COUNT,
    _TROPHY_SVG,
    _USE_HINT_HOVER,
    _USE_HINT_REST,
    _build_origin_pill,
    _build_use_hint,
    _card_prompt,
    _icon,
    _is_alive,
    _preset_matches,
    _set_use_hint,
    _sidebar_icon_html,
    _sip,
    _split_experimental,
    _svg_url,
    _tab_label,
    _truncate,
)
from .prompt_templates.dialog import PromptTemplatesDialog
from .prompt_templates.generation_card import _GenerationCard, _SidebarButton
from .prompt_templates.workers import (
    _INFLIGHT_WORKERS,
    _detach_worker,
    _FavoriteSyncWorker,
    _GenerationFavoriteWorker,
    _HistoryPageWorker,
    _LibrarySyncWorker,
)

__all__ = [
    "PromptTemplatesDialog",
    "_BeforeAfterCard",
    "_CARD_HOVER",
    "_CARD_NORMAL",
    "_CARD_PROMPT_CHARS",
    "_CARD_TITLE_H",
    "_EMPTY_MSG",
    "_EXPERIMENTAL_BTN",
    "_EXPERIMENTAL_HEADER",
    "_FavoriteSyncWorker",
    "_GALLERY_PAGE_SIZE",
    "_GenerationCard",
    "_GenerationFavoriteWorker",
    "_HISTORY_SVG",
    "_HistoryPageWorker",
    "_ICON_CACHE",
    "_ICONS_DIR",
    "_INFLIGHT_WORKERS",
    "_LOAD_MORE_BTN",
    "_LibrarySyncWorker",
    "_MAX_TITLE_CHARS",
    "_NEED_COLLAPSED_SETTING",
    "_NEED_HEADER_BTN",
    "_ORIGIN_PILL",
    "_PLUGIN_DIR",
    "_SEARCH_BOX",
    "_SIDEBAR_GLYPHS",
    "_SIDEBAR_ITEM",
    "_SIDEBAR_ITEM_ACTIVE",
    "_STAR_BTN",
    "_STAR_FILLED_SVG",
    "_STAR_OUTLINE_SVG",
    "_SidebarButton",
    "_StarButton",
    "_TAB_ORDER",
    "_TABS_WITH_COUNT",
    "_TROPHY_SVG",
    "_USE_HINT_HOVER",
    "_USE_HINT_REST",
    "_build_origin_pill",
    "_build_use_hint",
    "_card_prompt",
    "_detach_worker",
    "_icon",
    "_is_alive",
    "_preset_matches",
    "_set_use_hint",
    "_sidebar_icon_html",
    "_sip",
    "_split_experimental",
    "_svg_url",
    "_tab_label",
    "_truncate",
]
