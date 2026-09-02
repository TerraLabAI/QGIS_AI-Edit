"""Category pages, template-card grids, and the detail popup."""
from __future__ import annotations

from qgis.PyQt.QtCore import QEasingCurve, QPoint, QPropertyAnimation
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core import telemetry
from ....core import telemetry_events as te
from ....core.i18n import tr
from ....core.prompts.prompt_presets import get_need_page
from .cards import _BeforeAfterCard
from .common import (
    _BACK_BTN_SMALL,
    _EMPTY_MSG,
    _HALL_SECTION_COUNT,
    _HALL_SECTION_TITLE,
    _HISTORY_SVG,
    _NEED_TILE_SUB,
    _NEED_TILE_TITLE,
    _STAR_OUTLINE_SVG,
    _TABS_WITH_COUNT,
    _is_alive,
)
from .handoff_card import build_segmentation_handoff_card


class PagesMixin:
    """Page construction for the sidebar tabs of PromptTemplatesDialog."""

    # -- Pages -----------------------------------------------------------

    @staticmethod
    def _new_card_grid(host: QWidget, columns: int = 3) -> QGridLayout:
        """A card grid whose columns share the width equally, so cards stretch
        to fill the page and never clip at the right edge when the window
        resizes."""
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        for c in range(columns):
            grid.setColumnStretch(c, 1)
        return grid

    def _build_page(self, key: str) -> QWidget:
        """One scrollable page per sidebar tab - cards for that category only.

        Top Picks (``favorites``) uses a 3-column square-card grid with
        before/after slider previews. Recent paginates with a Load-more
        button so power users with thousands of prompts open instantly.
        Every other tab is a plain vertical list of text cards."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)

        content = QWidget()
        category = self._categories_by_key[key]

        if key == "favorites":
            # Minimal grid: 3-col x 2-row of compact slider cards (6 total).
            # Each slider sits idle at 50/50 (auto_loop disabled) so the page
            # reads as a calm launcher; the divider animates only on hover.
            # Whole page tops out around 400px tall and fits the default
            # 1100x720 dialog without any scrollbar.
            outer_v = QVBoxLayout(content)
            outer_v.setContentsMargins(6, 4, 6, 8)
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
            # The user's past generations as before/after cards they can reopen,
            # reuse, or add back to the map.
            outer_v = QVBoxLayout(content)
            outer_v.setContentsMargins(6, 4, 6, 8)
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
            # Unified Favorites: the curated templates the user starred AND the
            # generations they starred, each card carrying a Template / Your
            # prompt origin pill. Everything lives in ONE continuous grid -
            # starred templates/prompts first, then starred generations - so
            # removing any favorite reflows every later card into place with no
            # half-empty row at a section boundary.
            outer_v = QVBoxLayout(content)
            outer_v.setContentsMargins(6, 4, 6, 8)
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
            layout.setContentsMargins(6, 4, 6, 10)
            layout.setSpacing(6)
            presets = category["presets"]
            if not presets:
                empty = self._build_empty_state(key)
                if empty is not None:
                    layout.addWidget(empty)
            else:
                # Same visual language as Top Picks: a 3-column grid of
                # before/after preview cards. Each cell falls back to a text
                # card when its demo asset is missing, so every category reads
                # the same way whether or not its demos are seeded yet.
                # Experimental presets never reach this list (masked at the
                # resolver, R3), so every card here is reliable.
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
        """Top Picks layout: compact BeforeAfterCard cells in a 3x2 grid.

        Renders the slider card from the server-hosted demo URL, falling back
        to a text card when the demo is not seeded yet so the grid still
        renders even before any before/after asset exists."""
        for idx, preset in enumerate(presets):
            row, col = divmod(idx, columns)
            card = self._build_top_pick_card(preset)
            grid.addWidget(card, row, col)
            self._card_widgets.append((card, page_key))

    # -- Need drill-in pages (landing redesign) --------------------------

    def _switch_to_page(self, widget: QWidget) -> None:
        """Show an arbitrary stack page (landing or a need page) and keep the
        navigation rail's active row in sync with it. The landing/need flow does
        not use sidebar highlighting or tab keys."""
        self._stack.setCurrentWidget(widget)
        self._sync_rail_for_page(widget)

    def _build_back_header(self, title: str, tagline: str = "") -> QWidget:
        """Compact header: a small borderless back arrow + title (and an
        optional single-line tagline). Back returns to the landing page."""
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        back = QPushButton("←")  # left arrow, glyph outside tr()
        back.setStyleSheet(_BACK_BTN_SMALL)
        back.setCursor(QtC.PointingHandCursor)
        back.setFixedSize(26, 26)
        back.setToolTip(tr("Back to library"))
        back.clicked.connect(lambda _c=False: self._switch_to_page(self._landing_page))
        row.addWidget(back)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        lbl = QLabel(title)
        lbl.setStyleSheet(_NEED_TILE_TITLE)
        titles.addWidget(lbl)
        if tagline:
            sub = QLabel(tagline)
            sub.setStyleSheet(_NEED_TILE_SUB)
            sub.setWordWrap(False)  # one line, never wrap to two
            titles.addWidget(sub)
        row.addLayout(titles)
        row.addStretch()
        return host

    def _ensure_need_page(self, need_key: str) -> QWidget | None:
        """Lazily build and cache the drill-in page for one need."""
        page = self._need_pages.get(need_key)
        if page is None:
            page = self._build_need_page(need_key)
            self._need_pages[need_key] = page
            self._stack.addWidget(page)
        return page

    def _build_need_page(self, need_key: str) -> QWidget:
        """One continuous scrolling hall per family (R6). A fixed header
        (back affordance + family name/tagline) sits above a QScrollArea
        holding one section per subfamily: a header (label + live count)
        followed by its full card grid, in catalog order. The rail's
        subfamily rows glide the hall to a section, and a scroll-spy keeps
        the active row in sync with the scroll position."""
        data = get_need_page(need_key, self._server_catalog)
        categories = [cat for cat in data["categories"] if cat["presets"]]

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(10)

        outer.addWidget(self._build_back_header(data["label"], data["tagline"]))

        # No subfamily row up here: the navigation rail lists this family's
        # subfamilies indented under the active category row (RailMixin), so
        # the page keeps only the hall itself and its section headers.

        # The hall itself: one section per subfamily, stacked so the whole
        # family's structure is visible by scrolling, no click required.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        content = QWidget()
        hall = QVBoxLayout(content)
        hall.setContentsMargins(0, 2, 0, 8)
        hall.setSpacing(20)

        # Object outlines are AI Segmentation's job now: the card sits where
        # the Segment cards used to be, at the top of the Analyze family.
        if need_key == "classify":
            hall.addWidget(build_segmentation_handoff_card(content))

        sections: list[tuple[str, QWidget]] = []
        for cat in categories:
            section = self._build_hall_section(cat)
            hall.addWidget(section)
            sections.append((cat["key"], section))

        if not categories:
            empty = QLabel(tr("No prompts in this section yet."))
            empty.setStyleSheet(_EMPTY_MSG)
            hall.addWidget(empty)

        hall.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # Smooth glide on a rail subfamily click (same pattern as the version
        # strip's chevron jump); the scroll-spy guard below suppresses row
        # churn while this animation is in flight.
        anim = QPropertyAnimation(scroll.verticalScrollBar(), b"value", page)
        anim.setDuration(240)
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
        # The hall opens at the top, so the first section is the current one.
        if categories:
            self._set_hall_active_section(need_key, categories[0]["key"])
        scroll.verticalScrollBar().valueChanged.connect(
            lambda v, k=need_key: self._on_hall_scrolled(k, v)
        )
        return page

    def _build_hall_section(self, category: dict) -> QWidget:
        """One hall section: a strong subfamily header (17px title + muted
        count) followed by its full preset grid. Hierarchy comes from type
        size and weight alone - no coloured bar. No "see all" either: the
        section already shows every live prompt."""
        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        # Plain QLabel text (no buddy set) renders "&" literally - unlike
        # QPushButton, it needs no escaping here.
        title = QLabel(category["label"])
        title.setStyleSheet(_HALL_SECTION_TITLE)
        title_row.addWidget(title)
        count = QLabel(f"({len(category['presets'])})")
        count.setStyleSheet(_HALL_SECTION_COUNT)
        title_row.addWidget(count)
        title_row.addStretch()
        box.addLayout(title_row)

        grid_host = QWidget()
        grid = self._new_card_grid(grid_host, columns=3)
        self._populate_grid_cards(grid, category["presets"], category["key"], columns=3)
        box.addWidget(grid_host)
        return section

    @staticmethod
    def _hall_section_y(state: dict, section: QWidget) -> int:
        """Section's vertical offset inside the hall's scroll content - the
        scrollbar value that puts this section's header at the viewport top."""
        return section.mapTo(state["content"], QPoint(0, 0)).y()

    def _scroll_hall_to(self, need_key: str, cat_key: str) -> None:
        """Rail subfamily click: glide the hall to `cat_key`'s section ("All"
        glides to top). The rows are anchors, not filters - nothing is
        rebuilt."""
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
        anim.stop()  # may itself emit finished() synchronously - clear the
        # programmatic guard AFTER stop(), right before start(), so a second
        # row clicked mid-glide still suppresses the spy for its own glide.
        state["programmatic"] = True
        anim.setStartValue(bar.value())
        anim.setEndValue(target)
        anim.start()

    def _on_hall_scroll_anim_finished(self, need_key: str) -> None:
        state = self._need_state.get(need_key)
        if state is not None:
            state["programmatic"] = False

    def _on_hall_scrolled(self, need_key: str, _value: int) -> None:
        """Scroll-spy: set the active section from the current scroll
        position. Skipped while a rail click is driving a programmatic glide
        (see the `programmatic` guard in `_scroll_hall_to`) so the two never
        fight over which section is active."""
        state = self._need_state.get(need_key)
        if not state or state.get("programmatic"):
            return
        bar = state["scroll_area"].verticalScrollBar()
        value = bar.value()
        live = [(k, s) for k, s in state["sections"] if _is_alive(s)]
        # Bottom clamp: a section near the end of a short hall may never be
        # able to scroll its header all the way to the viewport top (not
        # enough content below it), so the plain top-anchored check below
        # would never select it. At the true scroll bottom the last live
        # section is what fills the viewport, so it wins outright.
        if bar.maximum() > 0 and value >= bar.maximum() and live:
            active = live[-1][0]
        else:
            # Above the first section header the first section is still the
            # one on screen (there is no "All" entry in the summary row).
            active = live[0][0] if live else None
            for cat_key, section in live:
                if value >= self._hall_section_y(state, section):
                    active = cat_key
        if active is None:
            return
        self._set_hall_active_section(need_key, active)

    def _set_hall_active_section(self, need_key: str, cat_key: str) -> None:
        """Track which subfamily section the hall shows and mirror it in the
        rail's subfamily rows (the hall itself carries no marker)."""
        state = self._need_state.get(need_key)
        if not state or state.get("active") == cat_key:
            return
        self._set_rail_active_subfamily(need_key, cat_key)
        state["active"] = cat_key

    def _build_top_pick_card(self, preset: dict) -> QFrame:
        """One library card: always a compact before/after slider so every cell
        in the grid keeps the same shape and stays aligned. When no demo asset
        exists (freeform favorite, or a template whose demo isn't seeded yet)
        the slider paints a 'No preview' placeholder instead of an image."""
        from ....core.prompts.prompt_presets_client import absolute_demo_url

        loader = self._demo_loader if self._client is not None else None

        def _abs(rel, _client=self._client):
            return absolute_demo_url(_client, rel) if _client is not None else rel

        card = _BeforeAfterCard(
            preset,
            self._on_card_clicked,
            demo_loader=loader,
            absolute_url=_abs if loader is not None else None,
        )
        star = card.star_button()
        if star is not None:
            star.toggled_state.connect(self._on_star_toggled)
        return card

    def _absolute_demo_url(self, rel: str) -> str:
        from ....core.prompts.prompt_presets_client import absolute_demo_url

        return absolute_demo_url(self._client, rel)

    def _open_detail(self, *, job: dict | None = None, preset: dict | None = None):
        """Open the detail popup for a generation or a curated template. The
        popup applies nothing itself: it records an outcome we read here so the
        nested modal loops stay sane."""
        # A fast double-click on a card can fire this twice before the first
        # popup grabs input; two stacked detail modals over the same loader
        # race on teardown and crash QGIS. One popup at a time.
        if getattr(self, "_detail_open", False):
            return
        self._detail_open = True
        from ..generation_detail_dialog import GenerationDetailDialog

        detail = GenerationDetailDialog(
            self,
            job=job,
            preset=preset,
            client=self._client,
            demo_loader=self._demo_loader,
            absolute_url=self._absolute_demo_url,
            on_action=self._on_generation_action,
            on_favorite=self._on_generation_favorite,
            browse_only=self._browse_only,
        )
        # Favoriting a template now lives in this popup; route its toggle
        # through the same handler the inline stars used (server sync + state).
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

    def _build_empty_state(self, key: str) -> QWidget | None:
        if key == "recent":
            icon_path = _HISTORY_SVG
            message = tr(
                "Nothing here yet. The generations you run will land here, ready to "
                "reopen, reuse, or add back to the map."
            )
        elif key == "user_favorites":
            icon_path = _STAR_OUTLINE_SVG
            message = tr(
                "No favorites yet. Open any template or generation and tap the ★ "
                "in its preview to keep it close."
            )
        else:
            return None

        # Outer container so we can center horizontally inside the scroll area.
        outer = QWidget()
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(20, 40, 20, 40)

        inner = QWidget()
        inner.setMaximumWidth(360)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setAlignment(QtC.AlignCenter)

        icon_label = QLabel()
        icon_label.setPixmap(QIcon(icon_path).pixmap(36, 36))
        icon_label.setAlignment(QtC.AlignCenter)
        icon_label.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(icon_label)

        msg = QLabel(message)
        msg.setStyleSheet(_EMPTY_MSG)
        msg.setAlignment(QtC.AlignCenter)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        outer_layout.addStretch()
        outer_layout.addWidget(inner)
        outer_layout.addStretch()
        return outer

    def _ensure_page(self, key: str) -> QWidget | None:
        """Build the page for `key` on first use and add it to the stack.
        Lazy so the dialog open + each tab switch stays cheap; a page (and its
        thumbnail downloads) only materializes when the user actually visits it.
        """
        if key in self._pages:
            return self._pages[key]
        if key not in self._categories_by_key:
            return None
        page = self._build_page(key)
        self._pages[key] = page
        self._stack.addWidget(page)
        return page

    # -- Interaction -----------------------------------------------------

    def _on_card_clicked(self, preset: dict):
        # A card click now opens the detail popup (full prompt + demo + actions)
        # instead of applying the prompt straight away. The popup's "Use this
        # prompt" button is what selects it. Browse-only opens read-only.
        self._open_detail(preset=preset)

    def _reload_dynamic_pages(self, keys=("recent", "user_favorites")):
        """Rebuild the named generation tabs from the current job lists. Only
        rebuilds pages that are already built (lazy): an unvisited tab will pick
        up the fresh data when the user first opens it. Sidebar counts always
        refresh. No _load_categories() here - these tabs render from the
        server-fetched jobs, not the prompt-preset catalog."""
        keyset = set(keys)
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if k not in keyset
        ]

        for key in keys:
            if key not in self._pages:
                continue
            old = self._pages[key]
            # removeWidget on the currently shown page makes the stack fall back
            # to index 0; re-select the rebuilt page so the visible tab does not
            # silently jump to Top Picks during a background sync or star toggle.
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

        # Landing shelves (Recent/Favorites in particular) must reflect the
        # same data: reappear once a shelf gets its first entry, disappear if
        # it goes back to empty (e.g. a favorite unstarred to zero).
        self._refresh_shelves()

        # The Sessions and Starred pages render from the same lists; keep the
        # one on screen live (a hidden one rebuilds on open). This is also the
        # path that keeps the rail's Starred count in step with star toggles
        # (via _refresh_shelves -> _refresh_rail_counts above).
        self._refresh_sessions_if_open()
        self._refresh_starred_if_open()

        # If a search is active, re-run it so the results pick up new presets
        # (e.g. a newly-fetched Recent entry, or a card whose star state changed).
        query = self._search_input.text().strip().lower()
        if query and self._active_tab == "__search__":
            self._rebuild_search_results(query)
