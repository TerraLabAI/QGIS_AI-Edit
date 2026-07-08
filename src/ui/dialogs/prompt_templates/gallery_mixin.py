
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
from ....core.config_store import get_export_copy, get_export_dial
from ....core.i18n import tr
from ....core.logger import log_debug
from ....core.prompts.session_grouping import group_recent_jobs
from .card_grid import card_grid_columns, settle_card_grid
from .common import _LOAD_MORE_BTN, _gallery_batch_size, _is_alive
from .generation_card import _GenerationCard
from .workers import _detach_worker, _GenerationFavoriteWorker, _HistoryPageWorker


_SCROLL_DEBOUNCE_MS = 50

_LAZY_LOAD_SETTLE_MS = 80


class GalleryMixin:


    def _build_card_gallery(
        self, key: str, entries: list, outer_v: QVBoxLayout, scroll: QScrollArea,
        columns: int = 3, slider_h: int | None = None, paginate: bool = True,
    ) -> None:






        grid_host = QWidget()
        grid = self._new_card_grid(grid_host, columns=columns)
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
            "columns": columns,
            "slider_h": slider_h,


            "page_size": _gallery_batch_size() if paginate else max(1, len(entries)),
        }
        self._append_gallery_cards(key)

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


        st = self._gallery_state.get(key)
        if st is None:
            return
        entries = st["entries"]
        grid = st["grid"]
        cards = st["cards"]
        start = st["visible"]
        end = min(start + st.get("page_size", _gallery_batch_size()), len(entries))



        show_origin = False
        columns = card_grid_columns(grid, st.get("columns", 3))
        slider_h = st.get("slider_h")
        for idx in range(start, end):
            row, col = divmod(idx, columns)
            entry = entries[idx]
            if entry.get("kind") == "preset":
                card = self._build_top_pick_card(entry["data"])
            elif entry.get("kind") == "session":


                card = self._build_session_card(entry)
            else:
                card = self._build_generation_card(
                    entry["data"], show_origin, entry.get("count", 1)
                )
            if slider_h is not None:
                slider = getattr(card, "_slider", None)
                if slider is not None:
                    slider.setFixedHeight(slider_h)
            grid.addWidget(card, row, col)
            cards.append(card)
        st["visible"] = end
        settle_card_grid(grid)

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

            btn.setVisible(True)
            btn.setEnabled(True)
            btn.setText(
                get_export_copy("dialogs.gallery_mixin.load_older_button", tr("Load older generations"))
            )
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
            st["btn"].setText(
                get_export_copy("dialogs.gallery_mixin.loading_button", tr("Loading..."))
            )
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






        self._retire_gallery_wiring(key)
        trigger = lambda: self._load_visible_cards(scroll, cards)  # noqa: E731
        self._gallery_loaders[key] = trigger




        debounce = QTimer(scroll)
        debounce.setSingleShot(True)
        debounce.setInterval(
            get_export_dial("dialogs.gallery_mixin.scroll_debounce_ms", _SCROLL_DEBOUNCE_MS)
        )
        debounce.timeout.connect(trigger)
        on_scroll = lambda _v: debounce.start()  # noqa: E731
        scroll.verticalScrollBar().valueChanged.connect(on_scroll)
        self._gallery_lazy_wiring[key] = (scroll, on_scroll, debounce)

        QTimer.singleShot(0, trigger)
        QTimer.singleShot(
            get_export_dial("dialogs.gallery_mixin.lazy_load_settle_ms", _LAZY_LOAD_SETTLE_MS), trigger
        )

    def _retire_gallery_wiring(self, key: str) -> None:


        wiring = self._gallery_lazy_wiring.pop(key, None)
        if wiring is None:
            return
        scroll, slot, timer = wiring
        if _is_alive(scroll):
            try:
                scroll.verticalScrollBar().valueChanged.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        if _is_alive(timer):
            timer.stop()
            timer.deleteLater()

    def _retire_gallery(self, key: str) -> None:



        self._gallery_state.pop(key, None)
        self._gallery_loaders.pop(key, None)
        self._retire_gallery_wiring(key)

    @staticmethod
    def _load_visible_cards(scroll: QScrollArea, cards: list) -> None:
        if not _is_alive(scroll):
            return


        if all(getattr(c, "_thumbs_requested", True) for c in cards if _is_alive(c)):
            return
        viewport = scroll.viewport()
        vp_h = viewport.height()
        if vp_h <= 0:
            return

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


        self.generation_action.emit(action, job)

    def _on_generation_favorite(self, request_id: str, now_favorited: bool):


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

        self.history_synced.emit(self._recent_jobs, self._favorite_jobs)




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
