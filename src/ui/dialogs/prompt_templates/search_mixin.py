
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




_PERSONAL_KEYS = ("recent", "user_favorites")
_TOP_PICKS_KEY = "favorites"

_HANDOFF_MAX_W = 560


class SearchMixin:


    def _build_search_page(self) -> QWidget:


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


        while self._search_layout.count() > 0:
            item = self._search_layout.takeAt(0)
            w = item.widget()
            if w is not None:



                w.hide()
                w.setParent(None)
                w.deleteLater()

        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if k != "__search__"
        ]




        header_at = self._search_layout.count()
        total = 0





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


                if ident in seen or not _preset_matches(preset, query):
                    continue
                seen.add(ident)
                matches.append(preset)
            if not matches:
                continue
            self._search_layout.addWidget(self._search_section_header(key, len(matches)))



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


            if query_asks_for_segmentation(query):
                self._search_layout.addWidget(self._centred_handoff_card())

        self._search_layout.addStretch()

    @staticmethod
    def _centred_handoff_card() -> QWidget:


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

        category = self._categories_by_key[key]
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(2, 4, 0, 0)
        row.setSpacing(8)
        title = QLabel(str(category["label"]))
        title.setTextFormat(QtC.PlainText)
        title.setStyleSheet(_HALL_SECTION_TITLE)

        title.setWordWrap(True)
        row.addWidget(title)
        count = QLabel(str(match_count))
        count.setStyleSheet(_HALL_SECTION_COUNT)
        row.addWidget(count, 0, QtC.AlignBottom)
        row.addStretch()
        return header



    def _on_search_changed(self, text: str):



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



        entering = self._stack.currentWidget() is not self._search_page
        self._stack.setCurrentWidget(self._search_page)
        self._sync_rail_for_page(self._search_page)
        self._active_tab = "__search__"
        if entering:


            self._search_debounce.stop()
            self._rebuild_search_results(query)
            return
        self._search_debounce.start()

    def _run_search(self):

        query = self._search_input.text().strip().lower()
        if query:
            self._rebuild_search_results(query)
