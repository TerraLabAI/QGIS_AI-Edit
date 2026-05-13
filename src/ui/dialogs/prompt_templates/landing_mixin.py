














from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core.config_store import get_export_copy, get_export_dial
from ....core.i18n import tr
from .common import style_library_scroll
from .library_empty_state import build_library_empty_state




_SPOT_CARD_COUNT = 9
_SPOT_CARD_COLUMNS = 3


def _spotlight_card_count() -> int:

    return get_export_dial("library.spot_card_count", _SPOT_CARD_COUNT)


class LandingMixin:





    def _feed_meta(self) -> dict:






        return {
            "popular": (
                get_export_copy("library.picks_title", tr("Top picks")),



                get_export_copy(
                    "library.picks_subtitle",
                    tr("Proven prompts to start from. Open one to see it before and after."),
                ),
            ),
            "work": (
                get_export_copy("library.work_title", tr("Sessions")),


                get_export_copy(
                    "library.work_subtitle",
                    tr("Your work sessions, newest first."),
                ),
            ),
        }



    def _build_landing_page(self) -> QWidget:


        self._spot_slot: QVBoxLayout | None = None
        self._feed_all_pages: dict[str, QWidget] = {}




        page = QWidget()
        page_box = QVBoxLayout(page)
        page_box.setContentsMargins(0, 0, 0, 0)
        page_box.setSpacing(14)
        title, tagline = self._feed_meta()["popular"]
        page_box.addWidget(self._build_page_header(title, tagline))

        scroll = QScrollArea()
        style_library_scroll(scroll)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(0, 0, 12, 20)
        outer.setSpacing(14)
        outer.addWidget(self._build_spotlight_block())
        outer.addStretch()
        scroll.setWidget(content)
        page_box.addWidget(scroll, 1)

        self._landing_page = page
        self._refresh_shelves()
        return page



    def _pinned_entries(self) -> list[dict]:
        fav_cat = self._categories_by_key.get("user_favorites") or {}
        entries = [
            {"kind": "preset", "data": p} for p in (fav_cat.get("presets") or [])
        ]
        entries += [{"kind": "job", "data": j} for j in self._favorite_jobs]
        return entries

    @staticmethod
    def _clear_slot(slot: QVBoxLayout) -> None:
        while slot.count():
            item = slot.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _refresh_shelves(self) -> None:



        if getattr(self, "_spot_slot", None) is None:
            return
        self._refresh_spotlight()
        self._refresh_rail_counts()



    def _top_pick_presets(self) -> list[dict]:
        cat = self._categories_by_key.get("favorites") or {}
        return list(cat.get("presets") or [])

    def _build_spotlight_block(self) -> QWidget:
        block = QWidget()
        box = QVBoxLayout(block)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(14)



        holder = QWidget()
        self._spot_slot = QVBoxLayout(holder)
        self._spot_slot.setContentsMargins(0, 0, 0, 0)
        box.addWidget(holder)
        return block

    def _refresh_spotlight(self) -> None:
        picks = self._top_pick_presets()[:_spotlight_card_count()]
        self._clear_slot(self._spot_slot)


        self._card_widgets = [(c, k) for (c, k) in self._card_widgets if k != "favorites"]
        if not picks:


            self._spot_slot.addWidget(build_library_empty_state(
                get_export_copy(
                    "library.empty_picks_offline", tr("The prompts could not load")),
                get_export_copy(
                    "library.empty_picks_offline_hint",
                    tr("Check your connection, then reopen the library."),
                ),
                suggestions=[self._suggest_sessions()],
                glyph="sparkles",
            ))
            return




        grid_host = QWidget()
        grid = self._new_card_grid(grid_host, columns=_SPOT_CARD_COLUMNS)
        self._populate_grid_cards(grid, picks, "favorites", columns=_SPOT_CARD_COLUMNS)
        self._spot_slot.addWidget(grid_host)
