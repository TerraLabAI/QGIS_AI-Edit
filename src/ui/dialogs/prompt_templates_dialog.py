"""Prompt Library dialog: the facade other modules import from.

The window itself lives in ``prompt_templates/``: a persistent rail on the
left (Top picks, one row per category family, then Sessions and Favorites),
one search field over the page stack, and card grids of before/after
previews. A card opens the preview window, whose button uses the prompt.

Sessions are the user's own past generations, fetched from the server in the
background and grouped by zone; Favorites holds the starred templates, the
user's own starred prompts and the starred generations in one grid.
Generation favorites (a star on a past edit) and prompt favorites (a star on
a template) sync to separate endpoints.
"""

from .prompt_templates.cards import _BeforeAfterCard, _StarButton
from .prompt_templates.common import (
    _CARD_HOVER,
    _CARD_NORMAL,
    _CARD_PROMPT_CHARS,
    _CARD_TITLE_H,
    _EMPTY_MSG,
    _GALLERY_PAGE_SIZE,
    _HISTORY_SVG,
    _ICON_CACHE,
    _ICONS_DIR,
    _LOAD_MORE_BTN,
    _MAX_TITLE_CHARS,
    _NEED_COLLAPSED_SETTING,
    _NEED_HEADER_BTN,
    _ORIGIN_PILL,
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
    "_svg_url",
    "_tab_label",
    "_truncate",
]
