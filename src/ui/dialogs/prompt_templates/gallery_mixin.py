"""Recent/Favorites generation galleries: paging, lazy thumbnails, stars."""
from __future__ import annotations

from qgis.PyQt.QtCore import QPoint, QTimer
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.i18n import tr
from ....core.logger import log_debug
from ....core.prompts.session_grouping import group_recent_jobs
from .common import _GALLERY_PAGE_SIZE, _LOAD_MORE_BTN, _is_alive
from .generation_card import _GenerationCard
from .workers import _detach_worker, _GenerationFavoriteWorker, _HistoryPageWorker


class GalleryMixin:
    """Generation-card galleries shared by the Recent and Favorites tabs."""

    def _build_card_gallery(
        self, key: str, entries: list, outer_v: QVBoxLayout, scroll: QScrollArea,
    ) -> None:
        """Paged grid of cards into ONE continuous 3-column grid. Each entry is
        a tagged dict ({"kind": "preset"|"job", "data": ...}); presets render as
        template slider cards, jobs as generation cards. Shared by Recent (jobs
        only) and the unified Favorites tab (presets + jobs in one flow), so a
        removed favorite reflows every later card with no gap."""
        grid_host = QWidget()
        grid = self._new_card_grid(grid_host, columns=3)
        cards: list = []
        more_btn = QPushButton()
        more_btn.setStyleSheet(_LOAD_MORE_BTN)
        more_btn.setCursor(QtC.PointingHandCursor)
        more_btn.clicked.connect(lambda _=False, k=key: self._on_gallery_show_more(k))
        self._gallery_state[key] = {
            "grid": grid,
            "entries": entries,
            "cards": cards,
            "visible": 0,
            "btn": more_btn,
        }
        self._append_gallery_cards(key)  # first _GALLERY_PAGE_SIZE

        outer_v.addWidget(grid_host)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 6, 0, 0)
        btn_row.addStretch()
        btn_row.addWidget(more_btn)
        btn_row.addStretch()
        outer_v.addLayout(btn_row)
        self._update_gallery_more_btn(key)

        self._wire_gallery_lazy_load(key, scroll, cards)

    def _append_gallery_cards(self, key: str) -> None:
        """Build the next batch of cards into the gallery grid, dispatching on
        each entry's kind so presets and generations share one index space."""
        st = self._gallery_state.get(key)
        if st is None:
            return
        entries = st["entries"]
        grid = st["grid"]
        cards = st["cards"]
        start = st["visible"]
        end = min(start + _GALLERY_PAGE_SIZE, len(entries))
        # Favorites tag generation cards with a Template / Your prompt origin
        # pill so the two kinds read apart; Recent does not (all generations).
        show_origin = key == "user_favorites"
        for idx in range(start, end):
            row, col = divmod(idx, 3)
            entry = entries[idx]
            if entry.get("kind") == "preset":
                card = self._build_top_pick_card(entry["data"])
            else:
                card = self._build_generation_card(
                    entry["data"], show_origin, entry.get("count", 1)
                )
            grid.addWidget(card, row, col)
            cards.append(card)
        st["visible"] = end

    def _update_gallery_more_btn(self, key: str) -> None:
        st = self._gallery_state.get(key)
        if st is None:
            return
        btn = st.get("btn")
        if btn is None or not _is_alive(btn):
            return
        remaining = len(st["entries"]) - st["visible"]
        if remaining > 0:
            btn.setVisible(True)
            btn.setEnabled(True)
            btn.setText(tr("Show {n} more").format(n=remaining))
        elif key == "recent" and self._recent_has_more:
            # Local jobs all visible but the server holds older ones.
            btn.setVisible(True)
            btn.setEnabled(True)
            btn.setText(tr("Load older generations"))
        else:
            btn.setVisible(False)

    def _on_gallery_show_more(self, key: str) -> None:
        st = self._gallery_state.get(key)
        recent_exhausted = st is not None and st["visible"] >= len(st["entries"])
        if key == "recent" and recent_exhausted and self._recent_has_more:
            self._fetch_older_recent()
            return
        self._append_gallery_cards(key)
        self._update_gallery_more_btn(key)
        trigger = self._gallery_loaders.get(key)
        if trigger is not None:
            QTimer.singleShot(0, trigger)

    def _fetch_older_recent(self) -> None:
        """Server-side Load more for Recent: fetch the page older than the
        oldest job currently held and append it to the gallery."""
        if self._client is None or self._auth_provider is None:
            return
        if _is_alive(self._recent_page_worker) and self._recent_page_worker.isRunning():
            return
        oldest = self._recent_jobs[-1].get("created_at") if self._recent_jobs else None
        if not oldest:
            return
        auth = self._auth_provider() or {}
        if not auth.get("Authorization"):
            return
        st = self._gallery_state.get("recent")
        if st is not None and _is_alive(st.get("btn")):
            st["btn"].setEnabled(False)
            st["btn"].setText(tr("Loading..."))
        worker = _HistoryPageWorker(self._client, auth, oldest, parent=None)
        worker.page_fetched.connect(self._on_older_recent_fetched)
        worker.failed.connect(self._on_older_recent_failed)
        _detach_worker(worker)
        self._recent_page_worker = worker
        worker.start()

    def _on_older_recent_fetched(self, jobs: list, has_more: bool) -> None:
        self._recent_has_more = bool(has_more) and bool(jobs)
        known = {j.get("request_id") for j in self._recent_jobs}
        fresh = [j for j in (jobs or []) if j.get("request_id") not in known]
        if fresh:
            self._recent_jobs.extend(fresh)
            st = self._gallery_state.get("recent")
            if st is not None:
                # Re-group the full list. An older sibling on a later page can
                # grow a session whose cover card is already on screen, so refresh
                # the shown cards' counts (and badge) by session key, THEN append
                # only the sessions not yet shown.
                grouped = self._grouped_recent_entries(self._recent_jobs)
                counts = {e.get("key"): e.get("count", 1) for e in grouped}
                shown = {e.get("key") for e in st["entries"]}
                for i, entry in enumerate(st["entries"]):
                    new_count = counts.get(entry.get("key"), entry.get("count", 1))
                    if new_count != entry.get("count", 1):
                        entry["count"] = new_count
                        if i < len(st["cards"]):
                            card = st["cards"][i]
                            if isinstance(card, _GenerationCard):
                                card.set_version_count(new_count)
                new_entries = [e for e in grouped if e.get("key") not in shown]
                if new_entries:
                    st["entries"].extend(new_entries)
                    self._append_gallery_cards("recent")
            self._refresh_sidebar_button("recent")
        self._update_gallery_more_btn("recent")
        trigger = self._gallery_loaders.get("recent")
        if trigger is not None:
            QTimer.singleShot(0, trigger)
        self.history_synced.emit(self._recent_jobs, self._favorite_jobs)

    def _on_older_recent_failed(self, msg: str) -> None:
        log_debug(f"Recent older-page fetch failed: {msg}")
        self._update_gallery_more_btn("recent")

    def _grouped_recent_entries(self, jobs: list) -> list:
        """Collapse the Recent list so all versions made on one zone share a
        single card. The cover is the newest version; its badge shows the count.
        Opening it restores the whole session (zone-grouped) for version pick."""
        entries = []
        for grp in group_recent_jobs(jobs):
            entries.append({
                "kind": "job",
                "data": grp["cover"],
                "key": grp["key"],
                "count": grp["count"],
            })
        return entries

    def _build_generation_card(
        self, job: dict, show_origin_pill: bool = False, version_count: int = 1
    ) -> _GenerationCard:
        return _GenerationCard(
            job,
            self._demo_loader,
            on_open=lambda j: self._open_detail(job=j),
            show_origin_pill=show_origin_pill,
            version_count=version_count,
        )

    def _wire_gallery_lazy_load(self, key: str, scroll: QScrollArea, cards: list) -> None:
        """Download thumbnails only for cards in (or near) the viewport, and
        load more as the user scrolls. Keeps opening Recent fast - no upfront
        burst of dozens of full-size image downloads."""
        trigger = lambda: self._load_visible_cards(scroll, cards)  # noqa: E731
        self._gallery_loaders[key] = trigger
        # Coalesce scroll ticks: a fast scroll fires valueChanged dozens of
        # times/sec, and each call is an O(cards) mapTo sweep. Debounce ~50ms so
        # the sweep runs once the scroll settles. Timer parented to scroll so it
        # dies with the gallery.
        debounce = QTimer(scroll)
        debounce.setSingleShot(True)
        debounce.setInterval(50)
        debounce.timeout.connect(trigger)
        scroll.verticalScrollBar().valueChanged.connect(lambda _v: debounce.start())
        # Two ticks: one once the event loop drains, one after layout settles.
        QTimer.singleShot(0, trigger)
        QTimer.singleShot(80, trigger)

    @staticmethod
    def _load_visible_cards(scroll: QScrollArea, cards: list) -> None:
        if not _is_alive(scroll):
            return
        # Once every (alive) card has its thumbnails, the gallery is warm and the
        # mapTo sweep is pure waste on every further scroll tick. Skip it.
        if all(getattr(c, "_thumbs_requested", True) for c in cards if _is_alive(c)):
            return
        viewport = scroll.viewport()
        vp_h = viewport.height()
        if vp_h <= 0:
            return
        # Prefetch one screen ahead so scrolling stays smooth.
        margin = vp_h
        for card in cards:
            if not _is_alive(card) or getattr(card, "_thumbs_requested", True):
                continue
            try:
                top = card.mapTo(viewport, QPoint(0, 0)).y()
            except (RuntimeError, TypeError):
                continue
            if top + card.height() >= -margin and top <= vp_h + margin:
                card.load_thumbnails()

    def _on_generation_action(self, action: str, job: dict):
        """Bubble add-to-map / download up to the dock (work runs in a task
        while this modal stays open)."""
        self.generation_action.emit(action, job)

    def _on_generation_favorite(self, request_id: str, now_favorited: bool):
        """Star/unstar a past generation. Optimistic UI already updated by the
        card; sync to the server and keep the in-memory lists consistent."""
        for j in self._recent_jobs:
            if j.get("request_id") == request_id:
                j["is_favorite"] = now_favorited
        if now_favorited:
            if not any(j.get("request_id") == request_id for j in self._favorite_jobs):
                match = next(
                    (j for j in self._recent_jobs if j.get("request_id") == request_id), None
                )
                if match is not None:
                    self._favorite_jobs.insert(0, dict(match))
        else:
            self._favorite_jobs = [
                j for j in self._favorite_jobs if j.get("request_id") != request_id
            ]
        self._refresh_sidebar_button("user_favorites")
        # Keep the dock's session cache in step with this star change.
        self.history_synced.emit(self._recent_jobs, self._favorite_jobs)
        # Rebuild the Favorites page so a freshly-starred generation shows up
        # there immediately. Deferred: destroying the card whose star was just
        # clicked mid-signal crashes on Qt6. Parent the timer to the dialog so
        # closing the library before it fires can't land on a freed widget.
        QtC.safe_single_shot(
            0, self, lambda: self._reload_dynamic_pages(keys=("user_favorites",))
        )
        if self._client is None or self._auth_provider is None:
            return
        auth = self._auth_provider() or {}
        if not auth.get("Authorization"):
            return
        worker = _GenerationFavoriteWorker(
            self._client, auth, request_id, now_favorited, parent=None
        )
        _detach_worker(worker)
        worker.start()
