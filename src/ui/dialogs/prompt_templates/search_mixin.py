"""Cross-category search page and its debounced rebuild."""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy
from ....core.i18n import tr
from .common import (
    _HALL_SECTION_COUNT,
    _HALL_SECTION_TITLE,
    _is_alive,
    _preset_matches,
    style_library_scroll,
)
from .handoff_card import build_segmentation_handoff_card, query_asks_for_segmentation
from .library_empty_state import build_library_empty_state

# Category keys that are not a catalog family: the user's own lists (their
# generations are not prompt text) and Top picks, whose cards all live in a
# family too and are searched last so a pick never shows twice.
_PERSONAL_KEYS = ("recent", "user_favorites")
_TOP_PICKS_KEY = "favorites"
# Widest the AI Segmentation card gets under the no-result state, px.
_HANDOFF_MAX_W = 560


class SearchMixin:
    """Search-results page for PromptTemplatesDialog."""

    def _build_search_page(self) -> QWidget:
        """Empty container for cross-tab search results. Filled by
        _rebuild_search_results whenever the search box is non-empty."""
        scroll = QScrollArea()
        style_library_scroll(scroll)

        content = QWidget()
        self._search_layout = QVBoxLayout(content)
        self._search_layout.setContentsMargins(0, 0, 12, 20)
        self._search_layout.setSpacing(12)
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
                # Hide and unparent now: deleteLater alone left the last
                # query's cards painted under the new ones until the event
                # loop ran.
                w.hide()
                w.setParent(None)
                w.deleteLater()
        # Drop old search-page card refs so star refresh doesn't touch ghosts.
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if k != "__search__"
        ]

        # The page header goes in first, filled with the count once the
        # sections are built: the same title block as every other page, so
        # the results never start higher than a family page's title.
        header_at = self._search_layout.count()
        total = 0
        # Every family the catalog serves, in its order (a category the
        # website adds is searchable at once, which the local order table
        # missed), then Top picks for any pick no family holds. A preset
        # already shown is never shown again: "solar" listed "Add solar farm"
        # under Top picks and again under Energy & solar.
        keys = [
            k for k in self._categories_by_key
            if k not in _PERSONAL_KEYS and k != _TOP_PICKS_KEY
        ] + [_TOP_PICKS_KEY]
        seen: set[str] = set()
        for key in keys:
            category = self._categories_by_key.get(key)
            if category is None:
                continue
            matches = []
            for preset in category.get("presets", []):
                ident = str(preset.get("id") or preset.get("prompt") or "")
                # Name and prompt only, not the family's name: "solar" found
                # "Add wind farm" just because it sits in Energy & solar.
                if ident in seen or not _preset_matches(preset, query):
                    continue
                seen.add(ident)
                matches.append(preset)
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

        if total:
            typed = " ".join(self._search_input.text().split())
            count_line = (
                get_export_copy("dialogs.search_mixin.one_result", tr("1 prompt"))
                if total == 1 else
                get_export_copy(
                    "dialogs.search_mixin.result_count", tr("{n} prompts")).format(n=total)
            )
            self._search_layout.insertWidget(header_at, self._build_page_header(
                get_export_copy(
                    "dialogs.search_mixin.results_title", tr('Results for "{query}"'),
                ).format(query=typed),
                count_line,
            ))
        if total == 0:
            self._search_layout.addWidget(build_library_empty_state(
                get_export_copy("dialogs.search_mixin.no_matches", tr("No matches found")),
                get_export_copy(
                    "dialogs.search_mixin.no_matches_hint",
                    tr('Try one word, like "trees"'),
                ),
                [
                    (
                        "close",
                        get_export_copy("dialogs.search_mixin.clear_search", tr("Clear the search")),
                        self._search_input.clear,
                    ),
                    self._suggest_top_picks(),
                ],
                top_margin=40,
                glyph="search",
            ))
            # "buildings", "segment roads": the cards that answered this left
            # for AI Segmentation, so the empty result says where they went.
            if query_asks_for_segmentation(query):
                self._search_layout.addWidget(self._centred_handoff_card())

        self._search_layout.addStretch()

    @staticmethod
    def _centred_handoff_card() -> QWidget:
        """The AI Segmentation card under a no-result state, held to the
        empty state's column instead of spanning the whole page."""
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(16, 0, 16, 0)
        row.addStretch(1)
        card = build_segmentation_handoff_card(host)
        card.setMaximumWidth(_HANDOFF_MAX_W)
        row.addWidget(card, 4)
        row.addStretch(1)
        return host

    def _search_section_header(self, key: str, match_count: int) -> QWidget:
        """A results group title and its muted count, like a hall section."""
        category = self._categories_by_key[key]
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(2, 4, 0, 0)
        row.setSpacing(8)
        title = QLabel(str(category["label"]))
        title.setTextFormat(QtC.PlainText)
        title.setStyleSheet(_HALL_SECTION_TITLE)
        # Wraps: a long family name in a longer language never widens the page.
        title.setWordWrap(True)
        row.addWidget(title)
        count = QLabel(str(match_count))
        count.setStyleSheet(_HALL_SECTION_COUNT)
        row.addWidget(count, 0, QtC.AlignBottom)
        row.addStretch()
        return header

    # -- Search ----------------------------------------------------------

    def _on_search_changed(self, text: str):
        """Search across all categories. Non-empty query → search-results page;
        empty query → back to the landing page. On the Sessions page the same
        field filters the sessions instead (one search field, one place)."""
        if getattr(self, "_search_scope", "prompts") == "sessions":
            debounce = getattr(self, "_sessions_debounce", None)
            if debounce is not None and _is_alive(debounce):
                debounce.start()
            return
        query = text.strip().lower()
        if not query:
            self._search_debounce.stop()
            self._active_tab = ""
            if self._stack.currentWidget() is self._search_page:
                self._switch_to_page(self._landing_page)
            return

        # Switch into search mode immediately; debounce only the heavy rebuild
        # so typing stays smooth. Search has no rail row, so clear the rail.
        entering = self._stack.currentWidget() is not self._search_page
        self._stack.setCurrentWidget(self._search_page)
        self._sync_rail_for_page(self._search_page)
        self._active_tab = "__search__"
        if entering:
            # The first letter builds at once: the page still held the last
            # search's cards, which flashed for the debounce otherwise.
            self._search_debounce.stop()
            self._rebuild_search_results(query)
            return
        self._search_debounce.start()

    def _run_search(self):
        """Debounced: rebuild the search grid for the current query."""
        query = self._search_input.text().strip().lower()
        if query:
            self._rebuild_search_results(query)
