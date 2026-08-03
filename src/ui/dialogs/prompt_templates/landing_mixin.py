"""Library default page: the Top picks spotlight.

The persistent navigation rail (see `rail_mixin.py`) is now the entry surface,
so the old landing layout (Your work sidebar + family tiles + an "or" beat) is
gone. What remains here:

- `_build_landing_page`: the rail's default target, "Top picks" - the measured
  community picks, shown as equal before/after cards that carry the prompt
  text.
- `_pinned_entries`: the user's starred prompts + starred generations, feeding
  the Starred page (`sessions_mixin.py`) and its rail count.

Mixed into PromptTemplatesDialog; reuses the existing card builders and the
detail popup in `pages_mixin.py`.
"""
from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy, get_export_dial
from ....core.i18n import tr
from .common import (
    _EMPTY_MSG,
    _FEED_SUBTITLE,
    _LANDING_HEADING,
)

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
                    tr("Our hand-picked selection to get you started."),
                ),
            ),
            "work": (
                get_export_copy("library.work_title", tr("Sessions")),
                # Sessions only since the starred shelf moved to its own
                # Starred page; the fallback stopped mentioning stars.
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

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(6, 4, 6, 8)
        outer.setSpacing(8)
        outer.addWidget(self._build_spotlight_block())
        outer.addStretch()

        scroll.setWidget(content)
        self._landing_page = scroll
        self._refresh_shelves()
        return scroll

    @staticmethod
    def _make_section_heading(title: str, subtitle: str) -> QWidget:
        """Section heading with its explainer visible (not a tooltip)."""
        host = QWidget()
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(1)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(_LANDING_HEADING)
        box.addWidget(title_lbl)
        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet(_FEED_SUBTITLE)
        box.addWidget(sub_lbl)
        return host

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
        box.setSpacing(8)

        title, tagline = self._feed_meta()["popular"]
        box.addWidget(self._make_section_heading(title, tagline))

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
            empty = QLabel(get_export_copy("library.empty_picks", tr("Nothing here yet.")))
            empty.setStyleSheet(_EMPTY_MSG)
            self._spot_slot.addWidget(empty)
            return

        # Same grid + card builder as the category pages, so Top picks reads
        # identically (3 columns, default card size). Each card carries the
        # prompt name and its text: the picks teach what a good prompt is.
        grid_host = QWidget()
        grid = self._new_card_grid(grid_host, columns=_SPOT_CARD_COLUMNS)
        self._populate_grid_cards(grid, picks, "favorites", columns=_SPOT_CARD_COLUMNS)
        self._spot_slot.addWidget(grid_host)
