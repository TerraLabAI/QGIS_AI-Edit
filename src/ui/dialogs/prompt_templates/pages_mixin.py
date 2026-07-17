"""Category pages, template-card grids, and the detail popup."""
from __future__ import annotations

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
    _EXPERIMENTAL_BTN,
    _EXPERIMENTAL_HEADER,
    _HISTORY_SVG,
    _NEED_ACCENT,
    _NEED_TAB,
    _NEED_TILE_SUB,
    _NEED_TILE_TITLE,
    _STAR_OUTLINE_SVG,
    _TABS_WITH_COUNT,
    _is_alive,
    _split_experimental,
)


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
                self._themed_state.pop(key, None)
            else:
                # Every reliable preset is shown up front; experimentals
                # (server-flagged fragile prompts) hide behind a single
                # amber disclosure so the curated default view stays clean.
                reliable, experimental = _split_experimental(presets)
                # Same visual language as Top Picks: a 3-column grid of
                # before/after preview cards. Each cell falls back to a text
                # card when its demo asset is missing, so every category reads
                # the same way whether or not its demos are seeded yet.
                grid_host = QWidget()
                grid = self._new_card_grid(grid_host, columns=3)
                self._populate_grid_cards(grid, reliable, key, columns=3)
                layout.addWidget(grid_host)

                exp_btn = None
                if experimental:
                    exp_btn = QPushButton(
                        self._show_experimental_label(len(experimental))
                    )
                    exp_btn.setStyleSheet(_EXPERIMENTAL_BTN)
                    exp_btn.setCursor(QtC.PointingHandCursor)
                    exp_btn.clicked.connect(
                        lambda _=False, k=key: self._on_show_experimental(k)
                    )
                    row = QHBoxLayout()
                    row.setContentsMargins(0, 6, 0, 0)
                    row.addStretch()
                    row.addWidget(exp_btn)
                    row.addStretch()
                    layout.addLayout(row)
                    self._themed_state[key] = {
                        "layout": layout,
                        "grid": grid,
                        "reliable_count": len(reliable),
                        "experimental": experimental,
                        "exp_btn": exp_btn,
                    }
                else:
                    self._themed_state.pop(key, None)
            layout.addStretch()

        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _show_experimental_label(count: int) -> str:
        return tr("Show {n} experimental templates").format(n=count)

    def _on_show_experimental(self, key: str):
        """Reveal the experimental templates for this category, prefixed
        with a small amber header that warns the prompts may misfire."""
        state = self._themed_state.get(key)
        if state is None:
            return
        exp_btn = state.get("exp_btn")
        experimental = state.get("experimental") or []
        grid = state.get("grid")
        if exp_btn is None or not _is_alive(exp_btn) or not experimental or grid is None:
            return
        # Continue the same 3-column grid: an amber header spanning the row,
        # then the experimental templates as preview cards below it.
        start = int(state.get("reliable_count", 0))
        header_row = (start + 2) // 3
        header = QLabel(tr("EXPERIMENTAL (may produce unexpected results)"))
        header.setStyleSheet(_EXPERIMENTAL_HEADER)
        header.setWordWrap(True)
        grid.addWidget(header, header_row, 0, 1, 3)
        base = (header_row + 1) * 3
        for i, preset in enumerate(experimental):
            row, col = divmod(base + i, 3)
            card = self._build_top_pick_card(preset)
            grid.addWidget(card, row, col)
            self._card_widgets.append((card, key))
        exp_btn.setVisible(False)
        state["exp_btn"] = None
        self._themed_state.pop(key, None)

    @staticmethod
    def _row_index_of(layout: QVBoxLayout, btn: QPushButton) -> int:
        """Return the layout index of the row that hosts `btn`, so callers
        can insertWidget before it. Falls back to layout.count() - 1 (right
        before the trailing stretch) if the row can't be found."""
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item is None:
                continue
            sub = item.layout()
            if sub is not None and sub.indexOf(btn) >= 0:
                return i
        return max(0, layout.count() - 1)

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
        """Show an arbitrary stack page (landing or a need page). The
        landing/need flow does not use sidebar highlighting or tab keys."""
        self._stack.setCurrentWidget(widget)

    @staticmethod
    def _tab_active_qss(accent: str) -> str:
        """Active category tab: underlined in the family's accent colour."""
        return (
            "QPushButton { border: none; border-bottom: 2px solid " + accent + "; "
            "background: transparent; padding: 6px 2px 3px 2px; font-size: 13px; "
            "font-weight: 700; color: palette(text); }"
        )

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
        """Aggregate every category of one need into a single filtered gallery:
        a back header, category filter chips, and a card grid rebuilt on filter
        so it always reflows without gaps."""
        data = get_need_page(need_key, self._server_catalog)
        accent = _NEED_ACCENT.get(need_key, "#8bac27")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(6, 4, 6, 8)
        outer.setSpacing(10)

        # Thin family-colour bar at the top echoes the family's tile accent.
        top_bar = QFrame()
        top_bar.setFixedHeight(3)
        top_bar.setStyleSheet(f"background: {accent}; border: none;")
        outer.addWidget(top_bar)

        outer.addWidget(self._build_back_header(data["label"], data["tagline"]))

        presets = [p for cat in data["categories"] for p in cat["presets"]]

        self._need_chips[need_key] = {}
        if len(data["categories"]) > 1:
            tab_row = QHBoxLayout()
            tab_row.setContentsMargins(0, 0, 0, 0)
            tab_row.setSpacing(16)
            all_tab = QPushButton(tr("All"))
            all_tab.setStyleSheet(self._tab_active_qss(accent))
            all_tab.setCursor(QtC.PointingHandCursor)
            all_tab.clicked.connect(
                lambda _c=False, k=need_key: self._render_need_grid(k, "__all__")
            )
            tab_row.addWidget(all_tab)
            self._need_chips[need_key]["__all__"] = all_tab
            for cat in data["categories"]:
                # && renders a literal & (Qt would otherwise treat "&" as a
                # mnemonic and show "Forestry & vegetation" as "Forestry _vegetation").
                tab = QPushButton(cat["label"].replace("&", "&&"))
                tab.setStyleSheet(_NEED_TAB)
                tab.setCursor(QtC.PointingHandCursor)
                tab.clicked.connect(
                    lambda _c=False, k=need_key, ck=cat["key"]: self._render_need_grid(k, ck)
                )
                tab_row.addWidget(tab)
                self._need_chips[need_key][cat["key"]] = tab
            tab_row.addStretch()
            tab_wrap = QWidget()
            tab_wrap.setLayout(tab_row)
            outer.addWidget(tab_wrap)

        grid_host = QWidget()
        grid = self._new_card_grid(grid_host, columns=3)
        outer.addWidget(grid_host)

        if not presets:
            empty = QLabel(tr("No prompts in this section yet."))
            empty.setStyleSheet(_EMPTY_MSG)
            outer.addWidget(empty)

        outer.addStretch()
        scroll.setWidget(content)

        self._need_state[need_key] = {
            "grid": grid, "all_presets": presets, "active": "__all__", "accent": accent,
        }
        self._render_need_grid(need_key, "__all__")
        return scroll

    def _render_need_grid(self, need_key: str, cat_key: str) -> None:
        """Rebuild a need page's card grid for the active category tab. All
        prompts render together (no experimental section) so the page stays
        simple; the rebuild reflows cleanly with no gaps."""
        state = self._need_state.get(need_key)
        if not state:
            return
        grid = state["grid"]
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) removes it from view now; deleteLater alone is
                # async, so a same-frame rebuild would briefly overlap old cells.
                widget.setParent(None)
                widget.deleteLater()

        presets = state["all_presets"]
        if cat_key != "__all__":
            presets = [p for p in presets if p.get("source_category") == cat_key]
        for idx, preset in enumerate(presets):
            row, col = divmod(idx, 3)
            grid.addWidget(self._build_top_pick_card(preset), row, col)

        state["active"] = cat_key
        active_qss = self._tab_active_qss(state.get("accent", "#8bac27"))
        for key, tab in self._need_chips.get(need_key, {}).items():
            tab.setStyleSheet(active_qss if key == cat_key else _NEED_TAB)

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

    def _prune_dead_cards(self):
        """Drop card refs whose underlying Qt widget has been destroyed."""
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if _is_alive(c)
        ]

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

        # If a search is active, re-run it so the results pick up new presets
        # (e.g. a newly-fetched Recent entry, or a card whose star state changed).
        query = self._search_input.text().strip().lower()
        if query and self._active_tab == "__search__":
            self._rebuild_search_results(query)
