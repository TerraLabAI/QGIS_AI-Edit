"""Cross-category search page and its debounced rebuild."""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ....core import qt_compat as QtC
from ....core.i18n import tr
from .common import (
    _EMPTY_MSG,
    _SIDEBAR_ITEM,
    _TAB_ORDER,
    _preset_matches,
    _sidebar_icon_html,
)


class SearchMixin:
    """Search-results page for PromptTemplatesDialog."""

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

    # -- Search ----------------------------------------------------------

    def _on_search_changed(self, text: str):
        """Search across all categories. Non-empty query → search-results page;
        empty query → restore the last sidebar tab."""
        query = text.strip().lower()
        if not query:
            self._search_debounce.stop()
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
        # Switch into search mode immediately; debounce only the heavy rebuild
        # so typing stays smooth.
        self._stack.setCurrentWidget(self._search_page)
        self._active_tab = "__search__"
        # Drop sidebar highlight while searching - no tab is "active".
        for btn in self._sidebar_buttons.values():
            btn.setStyleSheet(_SIDEBAR_ITEM)
        self._search_debounce.start()

    def _run_search(self):
        """Debounced: rebuild the search grid for the current query."""
        query = self._search_input.text().strip().lower()
        if query:
            self._rebuild_search_results(query)
