"""Library default page: the Top picks spotlight.

The persistent navigation rail (see `rail_mixin.py`) is now the entry surface,
so the old landing layout (Your work sidebar + family tiles + an "or" beat) is
gone. What remains here:

- `_build_landing_page`: the rail's default target, "Top picks" - the measured
  community picks, shown as equal before/after cards that carry the prompt
  text.
- `_pinned_entries`: the user's starred prompts + starred generations, feeding
  the Favorites page (`sessions_mixin.py`) and its rail count.

Mixed into PromptTemplatesDialog; reuses the existing card builders and the
detail popup in `pages_mixin.py`.
"""
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

# Top picks: nine picks in a 3-column grid (3x3), built with the same card
# grid as the category pages so every page reads identically (same card size,
# no tinted island).
_SPOT_CARD_COUNT = 9
_SPOT_CARD_COLUMNS = 3


def _spotlight_card_count() -> int:
    """Picks the Top picks page shows, read at use time."""
    return get_export_dial("library.spot_card_count", _SPOT_CARD_COUNT)


class LandingMixin:
    """Top picks default page. Requires the host to provide
    `_build_top_pick_card`, `_open_detail`, `_switch_to_page`,
    `_categories_by_key`, `_favorite_jobs`, and the rail helpers in
    `RailMixin`."""

    def _feed_meta(self) -> dict:
        # The Top picks tagline and the Sessions page title, used by the
        # spotlight header and the Sessions back header. Served
        # (copy.library.*) because the shelf a user lands on is the one
        # sentence that decides whether they browse or close the dialog.
        # "work" keeps its historical wire key; only the fallback moved to
        # the sessions vocabulary.
        return {
            "popular": (
                get_export_copy("library.picks_title", tr("Top picks")),
                # Editorial, not measured: this is a hand-picked list, so the
                # subtitle says so. Swap back to a "most run" wording only once
                # the order is actually driven by usage.
                get_export_copy(
                    "library.picks_subtitle",
                    tr("Proven prompts to start from. Open one to see it before and after."),
                ),
            ),
            "work": (
                get_export_copy("library.work_title", tr("Sessions")),
                # Sessions only since the starred shelf moved to its own
                # Favorites page; the fallback stopped mentioning stars.
                get_export_copy(
                    "library.work_subtitle",
                    tr("Your work sessions, newest first."),
                ),
            ),
        }

    # -- default page: Top picks ----------------------------------------

    def _build_landing_page(self) -> QWidget:
        # Content slot rebuilt by _refresh_shelves (initial build + every
        # history sync / star toggle).
        self._spot_slot: QVBoxLayout | None = None
        self._feed_all_pages: dict[str, QWidget] = {}

        # Header above the scroll area, cards inside it: the same build as the
        # family, Sessions and Favorites pages, so the title stays put while
        # the grid scrolls, and sits at the same place on every page.
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

    # -- personal entries (also feed the rail counts) -------------------

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
                widget.setParent(None)  # remove from view now (deleteLater is async)
                widget.deleteLater()

    def _refresh_shelves(self) -> None:
        """Rebuild the spotlight and refresh the rail counts from current data.
        Called on initial build and whenever the host reloads the
        recent/favorites data (`_reload_dynamic_pages`)."""
        if getattr(self, "_spot_slot", None) is None:
            return
        self._refresh_spotlight()
        self._refresh_rail_counts()

    # -- Top picks spotlight --------------------------------------------

    def _top_pick_presets(self) -> list[dict]:
        cat = self._categories_by_key.get("favorites") or {}  # Top picks source
        return list(cat.get("presets") or [])

    def _build_spotlight_block(self) -> QWidget:
        block = QWidget()
        box = QVBoxLayout(block)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(14)

        # The cards sit directly on the page (no tinted band), so Top picks
        # reads like every other page. The slot is a plain container.
        holder = QWidget()
        self._spot_slot = QVBoxLayout(holder)
        self._spot_slot.setContentsMargins(0, 0, 0, 0)
        box.addWidget(holder)
        return block

    def _refresh_spotlight(self) -> None:
        picks = self._top_pick_presets()[:_spotlight_card_count()]
        self._clear_slot(self._spot_slot)
        # Drop the previous spotlight cards from the star-refresh registry so it
        # never accumulates dead widgets across rebuilds.
        self._card_widgets = [(c, k) for (c, k) in self._card_widgets if k != "favorites"]
        if not picks:
            # Top picks come from the server catalog, so an empty page means
            # it has not arrived: say that, not "Nothing here yet".
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

        # Same grid + card builder as the category pages, so Top picks reads
        # identically (3 columns, default card size). Each card carries the
        # prompt name and its text: the picks teach what a good prompt is.
        grid_host = QWidget()
        grid = self._new_card_grid(grid_host, columns=_SPOT_CARD_COLUMNS)
        self._populate_grid_cards(grid, picks, "favorites", columns=_SPOT_CARD_COLUMNS)
        self._spot_slot.addWidget(grid_host)
