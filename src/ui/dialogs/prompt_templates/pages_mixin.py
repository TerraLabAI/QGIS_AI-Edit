
from __future__ import annotations

from qgis.PyQt.QtCore import QEasingCurve, QPoint, QPropertyAnimation
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core import telemetry
from ....core import telemetry_events as te
from ....core.config_store import get_export_copy, get_export_dial
from ....core.i18n import tr
from ....core.prompts.prompt_presets import get_need_page
from .card_grid import CardGridReflow, card_grid_columns, settle_card_grid
from .cards import _BeforeAfterCard
from .common import (
    _HALL_SECTION_TITLE,
    _NEED_TILE_SUB,
    _NEED_TILE_TITLE,
    _STAR_OUTLINE_SVG,
    _TABS_WITH_COUNT,
    _is_alive,
    card_description,
    style_library_scroll,
)
from .handoff_card import build_segmentation_handoff_card
from .library_empty_state import build_library_empty_state


_CARD_GRID_GAP = 12


_PAGE_MARGINS = (0, 0, 12, 20)


_HALL_SCROLL_ANIM_MS = 240


class PagesMixin:




    @staticmethod
    def _new_card_grid(host: QWidget, columns: int = 3) -> QGridLayout:




        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(_CARD_GRID_GAP)
        grid.setVerticalSpacing(_CARD_GRID_GAP)
        for c in range(columns):
            grid.setColumnStretch(c, 1)
        CardGridReflow(host, grid, columns)
        return grid

    def _build_page(self, key: str) -> QWidget:






        scroll = QScrollArea()
        style_library_scroll(scroll)

        content = QWidget()
        category = self._categories_by_key[key]

        if key == "favorites":





            outer_v = QVBoxLayout(content)
            outer_v.setContentsMargins(*_PAGE_MARGINS)
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


            outer_v = QVBoxLayout(content)
            outer_v.setContentsMargins(*_PAGE_MARGINS)
            outer_v.setSpacing(8)
            if not self._recent_jobs:
                self._gallery_state.pop(key, None)
                empty = self._build_empty_state(key)
                if empty is not None:
                    outer_v.addWidget(empty)
            else:
                entries = self._grouped_recent_entries(self._recent_jobs)
                self._build_card_gallery(key, entries, outer_v, scroll)
            outer_v.addStretch()
        elif key == "user_favorites":






            outer_v = QVBoxLayout(content)
            outer_v.setContentsMargins(*_PAGE_MARGINS)
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
            layout.setContentsMargins(*_PAGE_MARGINS)
            layout.setSpacing(6)
            presets = category["presets"]
            if not presets:
                empty = self._build_empty_state(key)
                if empty is not None:
                    layout.addWidget(empty)
            else:






                grid_host = QWidget()
                grid = self._new_card_grid(grid_host, columns=3)
                self._populate_grid_cards(grid, presets, key, columns=3)
                layout.addWidget(grid_host)
            layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _populate_grid_cards(
        self, grid: QGridLayout, presets: list[dict], page_key: str, columns: int = 3
    ):





        columns = card_grid_columns(grid, columns)
        for idx, preset in enumerate(presets):
            row, col = divmod(idx, columns)
            card = self._build_top_pick_card(preset)
            grid.addWidget(card, row, col)
            self._card_widgets.append((card, page_key))
        settle_card_grid(grid)



    def _switch_to_page(self, widget: QWidget) -> None:



        self._stack.setCurrentWidget(widget)
        self._sync_rail_for_page(widget)

    @staticmethod
    def _build_page_header(title: str, tagline: str = "") -> QWidget:





        host = QWidget()
        titles = QVBoxLayout(host)
        titles.setContentsMargins(2, 0, 0, 0)
        titles.setSpacing(2)
        lbl = QLabel(title)
        lbl.setStyleSheet(_NEED_TILE_TITLE)
        lbl.setTextFormat(QtC.PlainText)


        lbl.setWordWrap(True)
        titles.addWidget(lbl)
        if tagline:
            sub = QLabel(tagline)
            sub.setStyleSheet(_NEED_TILE_SUB)
            sub.setTextFormat(QtC.PlainText)
            sub.setWordWrap(True)
            titles.addWidget(sub)
        return host

    def _ensure_need_page(self, need_key: str) -> QWidget | None:

        page = self._need_pages.get(need_key)
        if page is None:
            page = self._build_need_page(need_key)
            self._need_pages[need_key] = page
            self._stack.addWidget(page)
        return page

    def _build_need_page(self, need_key: str) -> QWidget:






        data = get_need_page(need_key, self._server_catalog)
        categories = [cat for cat in data["categories"] if cat["presets"]]

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        outer.addWidget(self._build_page_header(data["label"], data["tagline"]))







        scroll = QScrollArea()
        style_library_scroll(scroll)
        content = QWidget()
        hall = QVBoxLayout(content)
        hall.setContentsMargins(*_PAGE_MARGINS)
        hall.setSpacing(24)



        if need_key == "classify":
            hall.addWidget(build_segmentation_handoff_card(content))

        sections: list[tuple[str, QWidget]] = []
        for cat in categories:
            section = self._build_hall_section(cat)
            hall.addWidget(section)
            sections.append((cat["key"], section))

        if not categories:
            hall.addWidget(build_library_empty_state(
                get_export_copy(
                    "dialogs.pages_mixin.empty_section", tr("No prompts in this section yet")),
                suggestions=[self._suggest_top_picks()],
            ))

        hall.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)




        anim = QPropertyAnimation(scroll.verticalScrollBar(), b"value", page)
        anim.setDuration(get_export_dial(
            "dialogs.pages_mixin.hall_scroll_anim_ms", _HALL_SCROLL_ANIM_MS))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda k=need_key: self._on_hall_scroll_anim_finished(k))

        self._need_state[need_key] = {
            "scroll_area": scroll,
            "content": content,
            "sections": sections,
            "entries": [
                (cat["key"], cat["label"], len(cat["presets"]))
                for cat in categories
            ],
            "active": None,
            "programmatic": False,
            "anim": anim,
        }

        if categories:
            self._set_hall_active_section(need_key, categories[0]["key"])
        scroll.verticalScrollBar().valueChanged.connect(
            lambda v, k=need_key: self._on_hall_scrolled(k, v)
        )
        return page

    def _build_hall_section(self, category: dict) -> QWidget:





        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(2, 0, 0, 0)
        title_row.setSpacing(8)


        title = QLabel(category["label"])
        title.setStyleSheet(_HALL_SECTION_TITLE)
        title.setWordWrap(True)
        title_row.addWidget(title, 1)
        box.addLayout(title_row)

        grid_host = QWidget()
        grid = self._new_card_grid(grid_host, columns=3)
        self._populate_grid_cards(grid, category["presets"], category["key"], columns=3)
        box.addWidget(grid_host)
        return section

    @staticmethod
    def _hall_section_y(state: dict, section: QWidget) -> int:


        return section.mapTo(state["content"], QPoint(0, 0)).y()

    def _scroll_hall_to(self, need_key: str, cat_key: str) -> None:



        state = self._need_state.get(need_key)
        if not state:
            return
        bar = state["scroll_area"].verticalScrollBar()
        target = 0
        if cat_key != "__all__":
            for ck, section in state["sections"]:
                if ck == cat_key and _is_alive(section):
                    target = self._hall_section_y(state, section)
                    break
        target = max(0, min(target, bar.maximum()))
        self._set_hall_active_section(need_key, cat_key)
        anim = state["anim"]
        anim.stop()


        state["programmatic"] = True
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.start()

    def _on_hall_scroll_anim_finished(self, need_key: str) -> None:
        state = self._need_state.get(need_key)
        if state is not None:
            state["programmatic"] = False

    def _on_hall_scrolled(self, need_key: str, _value: int) -> None:




        state = self._need_state.get(need_key)
        if not state or state.get("programmatic"):
            return
        bar = state["scroll_area"].verticalScrollBar()
        value = bar.value()
        live = [(k, s) for k, s in state["sections"] if _is_alive(s)]





        if bar.maximum() > 0 and value >= bar.maximum() and live:
            active = live[-1][0]
        else:


            active = live[0][0] if live else None
            for cat_key, section in live:
                if value >= self._hall_section_y(state, section):
                    active = cat_key
        if active is None:
            return
        self._set_hall_active_section(need_key, active)

    def _set_hall_active_section(self, need_key: str, cat_key: str) -> None:


        state = self._need_state.get(need_key)
        if not state or state.get("active") == cat_key:
            return
        self._set_rail_active_subfamily(need_key, cat_key)
        state["active"] = cat_key

    def _build_top_pick_card(self, preset: dict) -> QFrame:




        from ....core.prompts.prompt_presets_client import absolute_demo_url

        loader = self._demo_loader if self._client is not None else None

        def _abs(rel, _client=self._client):
            return absolute_demo_url(_client, rel) if _client is not None else rel

        card = _BeforeAfterCard(
            preset,
            self._on_card_clicked,
            demo_loader=loader,
            absolute_url=_abs if loader is not None else None,
            hint=card_description(preset),
        )
        star = card.star_button()
        if star is not None:
            star.toggled_state.connect(self._on_star_toggled)
        return card

    def _absolute_demo_url(self, rel: str) -> str:
        from ....core.prompts.prompt_presets_client import absolute_demo_url

        return absolute_demo_url(self._client, rel)

    def _open_detail(self, *, job: dict | None = None, preset: dict | None = None):






        if getattr(self, "_detail_open", False):
            return
        self._detail_open = True
        from ..generation_detail_dialog import GenerationDetailDialog

        detail = GenerationDetailDialog(
            self,
            job=job,
            preset=self._preset_for_preview(preset),
            client=self._client,
            demo_loader=self._demo_loader,
            absolute_url=self._absolute_demo_url,
            on_action=self._on_generation_action,
            on_favorite=self._on_generation_favorite,
            browse_only=self._browse_only,
        )


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

    def _preset_for_preview(self, preset: dict | None) -> dict | None:



        if preset is None or preset.get("category_label"):
            return preset
        family = self._categories_by_key.get(str(preset.get("source_category") or ""))
        label = str((family or {}).get("label") or "").strip()
        return dict(preset, category_label=label) if label else preset

    def _suggest_top_picks(self):

        return (
            "sparkles",
            get_export_copy("dialogs.pages_mixin.suggest_top_picks", tr("Browse the top picks")),
            lambda: self._rail_navigate("popular"),
        )

    def _suggest_sessions(self):

        return (
            "clock",
            get_export_copy("dialogs.pages_mixin.suggest_sessions", tr("Open your sessions")),
            lambda: self._rail_navigate("user_favorites"),
        )

    def _build_empty_state(self, key: str) -> QWidget | None:
        if key == "recent":
            question = get_export_copy(
                "dialogs.pages_mixin.empty_recent_title", tr("No edits yet"))
            message = get_export_copy(
                "dialogs.pages_mixin.empty_recent",
                tr(
                    "Nothing here yet. The generations you run will land here, ready to "
                    "reopen, reuse, or add back to the map."
                ),
            )
            suggestions = [self._suggest_top_picks()]
            return build_library_empty_state(question, message, suggestions, glyph="clock")
        if key == "user_favorites":
            question = get_export_copy(
                "dialogs.pages_mixin.no_favorites_title", tr("No favorites yet"))
            message = get_export_copy(
                "dialogs.pages_mixin.empty_favorites",
                tr(
                    "Open a prompt or a past edit and press its star: "
                    "it will wait for you here."
                ),
            )
            suggestions = [self._suggest_top_picks(), self._suggest_sessions()]
            return build_library_empty_state(
                question, message, suggestions, icon=_STAR_OUTLINE_SVG)
        return None

    def _ensure_page(self, key: str) -> QWidget | None:




        if key in self._pages:
            return self._pages[key]
        if key not in self._categories_by_key:
            return None
        page = self._build_page(key)
        self._pages[key] = page
        self._stack.addWidget(page)
        return page



    def _on_card_clicked(self, preset: dict):



        self._open_detail(preset=preset)

    def _reload_dynamic_pages(self, keys=("recent", "user_favorites")):





        keyset = set(keys)
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if k not in keyset
        ]

        for key in keys:
            if key not in self._pages:
                continue
            old = self._pages[key]



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




        self._refresh_shelves()





        self._refresh_sessions_if_open()
        self._refresh_starred_if_open()



        query = self._search_input.text().strip().lower()
        if query and self._active_tab == "__search__":
            self._rebuild_search_results(query)
