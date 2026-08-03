

















from __future__ import annotations

import datetime as _dt

from qgis.PyQt.QtCore import QLocale, QTimer
from qgis.PyQt.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core.config_store import get_export_copy, get_export_dial
from ....core.i18n import tr
from ....core.prompts.conversation_summary import conversation_entries, filter_entries
from .common import (
    _HALL_SECTION_TITLE,
    _is_alive,
    style_library_scroll,
)
from .generation_card import _GenerationCard
from .library_empty_state import build_library_empty_state






_SESSION_GROUP_TITLE_GAP = 10

_SESSION_GROUP_GAP = 24



_SESSION_GALLERY_PREFIX = "sessions:"



_SEARCH_DEBOUNCE_MS = 180


def _session_month_bucket(iso_ts: str) -> tuple[int, int] | None:


    raw = (iso_ts or "").strip()
    if not raw:
        return None
    try:

        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    local = parsed.astimezone()
    return (local.year, local.month)


def _session_month_label(bucket: tuple[int, int]) -> str:


    year, month = bucket
    return f"{QLocale().standaloneMonthName(month)} {year}"


def _session_given_name(entry: dict) -> str:


    for job in entry.get("members") or []:
        name = " ".join(str(job.get("session_title") or "").split())
        if name:
            return " ".join(str(entry.get("title") or name).split())
    return ""


def _session_with_readable_title(entry: dict) -> dict:





    title = " ".join(str(entry.get("title") or "").split())
    if not title or _session_given_name(entry):
        return entry
    prompts = [
        " ".join(str(job.get("prompt") or "").split())
        for job in (entry.get("members") or [])
    ]
    source = next((p for p in prompts if p.startswith(title) and len(p) > len(title)), "")
    if not source:
        return entry
    cut = title if source[len(title)] == " " else title.rsplit(" ", 1)[0]
    return dict(entry, title=(cut or title).rstrip(" ,.;:-") + "…")


class SessionsMixin:













    def _session_entries(self) -> list[dict]:


        return conversation_entries(self._recent_jobs)

    @staticmethod
    def _session_cover_job(entry: dict) -> dict:


        return dict(entry.get("cover") or {})



    def open_at_sessions(self) -> None:


        self._open_sessions_page()

    def set_session_jobs(self, jobs: list) -> None:





        self._on_recent_jobs_fetched(list(jobs or []), self._recent_has_more)
        self._refresh_sessions_if_open()

    def _open_sessions_page(self) -> None:



        old = self._feed_all_pages.pop("work", None)
        if old is not None:
            self._stack.removeWidget(old)
            old.deleteLater()
        page = self._build_sessions_page()
        self._feed_all_pages["work"] = page
        self._stack.addWidget(page)
        self._switch_to_page(page)


        self.sessions_refresh_requested.emit()



    def _build_sessions_page(self) -> QWidget:
        title, tagline = self._feed_meta()["work"]
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        outer.addWidget(self._build_page_header(title, tagline))





        debounce = QTimer(page)
        debounce.setSingleShot(True)
        debounce.setInterval(
            get_export_dial("dialogs.sessions_mixin.search_debounce_ms", _SEARCH_DEBOUNCE_MS))
        debounce.timeout.connect(self._rebuild_session_list)
        self._sessions_debounce = debounce
        self._sessions_search = self._search_input

        scroll = QScrollArea()
        style_library_scroll(scroll)


        self._sessions_scroll = scroll
        content = QWidget()
        content_box = QVBoxLayout(content)
        content_box.setContentsMargins(0, 0, 12, 20)
        content_box.setSpacing(0)

        list_host = QWidget()
        self._sessions_list_box = QVBoxLayout(list_host)
        self._sessions_list_box.setContentsMargins(0, 0, 0, 0)
        self._sessions_list_box.setSpacing(0)
        content_box.addWidget(list_host)

        content_box.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self._rebuild_session_list()
        return page



    def _open_starred_page(self) -> None:



        old = self._feed_all_pages.pop("starred", None)
        if old is not None:
            self._stack.removeWidget(old)
            old.deleteLater()
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if k != "work_favorites"
        ]


        self._retire_gallery("work_favorites")
        page = self._build_starred_page()
        self._feed_all_pages["starred"] = page
        self._stack.addWidget(page)
        self._switch_to_page(page)

    def _build_starred_page(self) -> QWidget:





        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        title = get_export_copy("library.favorites_title", tr("Favorites"))

        outer.addWidget(self._build_page_header(title, get_export_copy(
            "library.favorites_subtitle",
            tr("Prompts and edits you starred."),
        )))

        scroll = QScrollArea()
        style_library_scroll(scroll)
        content = QWidget()
        content_box = QVBoxLayout(content)
        content_box.setContentsMargins(0, 0, 12, 20)
        content_box.setSpacing(8)

        pinned = self._pinned_entries()
        if pinned:
            self._build_card_gallery(
                "work_favorites", pinned, content_box, scroll, paginate=False,
            )
        else:


            empty = self._build_empty_state("user_favorites")
            if empty is not None:
                content_box.addWidget(empty)

        content_box.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        return page

    def _refresh_starred_if_open(self) -> None:




        page = getattr(self, "_feed_all_pages", {}).get("starred")
        if page is None or self._stack.currentWidget() is not page:
            return
        self._open_starred_page()



    def _refresh_sessions_if_open(self) -> None:


        page = getattr(self, "_feed_all_pages", {}).get("work")
        if page is None or self._stack.currentWidget() is not page:
            return
        if _is_alive(getattr(self, "_sessions_search", None)):
            self._rebuild_session_list()

    def _rebuild_session_list(self, *_args) -> None:



        box = getattr(self, "_sessions_list_box", None)
        if box is None:
            return


        for key in [
            k for k in self._gallery_state
            if str(k).startswith(_SESSION_GALLERY_PREFIX)
        ]:
            self._retire_gallery(key)


        while box.count():
            item = box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        entries = self._session_entries()
        query = ""
        if _is_alive(getattr(self, "_sessions_search", None)):
            query = self._sessions_search.text() or ""
        shown = filter_entries(entries, text=query)

        if not shown:
            if entries:
                empty = build_library_empty_state(
                    get_export_copy(
                        "dialogs.sessions_mixin.no_matches_message", tr("No sessions match")),
                    suggestions=[(
                        "close",
                        get_export_copy(
                            "dialogs.search_mixin.clear_search", tr("Clear the search")),
                        self._clear_sessions_search,
                    )],
                    top_margin=40,
                    glyph="search",
                )
            else:
                empty = build_library_empty_state(
                    get_export_copy(
                        "library.empty_work_title", tr("What will you edit first?")),
                    get_export_copy(
                        "library.empty_work",
                        tr("Every edit you run lands here, grouped by place, ready to pick up again."),
                    ),
                    [self._suggest_top_picks()],
                    glyph="clock",
                )
            box.addWidget(empty)
            return



        month_groups: dict[str, tuple[str, list[dict]]] = {}
        undated: list[dict] = []
        for entry in shown:
            bucket = _session_month_bucket(entry.get("created_at") or "")
            if bucket is None:
                undated.append(entry)
                continue
            key = f"{_SESSION_GALLERY_PREFIX}{bucket[0]:04d}-{bucket[1]:02d}"
            if key not in month_groups:
                month_groups[key] = (_session_month_label(bucket), [])
            month_groups[key][1].append(entry)
        if undated:
            month_groups[_SESSION_GALLERY_PREFIX + "earlier"] = (
                get_export_copy("dialogs.sessions_mixin.earlier_bucket_label", tr("Earlier")),
                undated,
            )

        scroll = getattr(self, "_sessions_scroll", None)
        for index, (key, (label, group)) in enumerate(month_groups.items()):
            if index:
                box.addSpacing(_SESSION_GROUP_GAP)
            header = QLabel(label)
            header.setStyleSheet(_HALL_SECTION_TITLE)


            header.setContentsMargins(2, 0, 0, 0)
            box.addWidget(header)
            box.addSpacing(_SESSION_GROUP_TITLE_GAP)
            section = QWidget()
            section_v = QVBoxLayout(section)
            section_v.setContentsMargins(0, 0, 0, 0)
            section_v.setSpacing(8)
            self._build_card_gallery(
                key, self._session_card_entries(group), section_v, scroll,
                paginate=False,
            )
            box.addWidget(section)

    def _clear_sessions_search(self) -> None:
        search = getattr(self, "_sessions_search", None)
        if _is_alive(search):
            search.clear()

    def _session_card_entries(self, group: list[dict]) -> list[dict]:


        return [
            {
                "kind": "session",
                "data": self._session_cover_job(entry),
                "count": int(entry.get("count") or 1),
                "entry": entry,
            }
            for entry in group
        ]

    def _build_session_card(self, entry: dict) -> _GenerationCard:



        session = entry["entry"]
        return _GenerationCard(
            entry["data"],
            self._demo_loader,
            on_open=lambda _j, s=session: self._open_session_detail(s),
            version_count=int(entry.get("count") or 1),


            title=_session_given_name(session),
        )



    def _open_session_detail(self, entry: dict) -> None:







        if getattr(self, "_detail_open", False):
            return
        self._detail_open = True
        from ..generation_detail_dialog import GenerationDetailDialog

        detail = GenerationDetailDialog(
            self,
            job=self._session_cover_job(entry),
            client=self._client,
            demo_loader=self._demo_loader,
            absolute_url=self._absolute_demo_url,
            on_action=self._on_generation_action,
            on_favorite=self._on_generation_favorite,
            browse_only=self._browse_only,
            session_entry=_session_with_readable_title(entry),
        )
        try:
            detail.exec()
            outcome = detail.outcome()
        finally:
            self._detail_open = False
            detail.deleteLater()
        if outcome == "resume":
            self._on_session_resume(entry)
        elif outcome == "rename":
            self.session_rename_requested.emit(entry)
        elif outcome == "delete":
            self.session_delete_requested.emit(entry)

    def _on_session_resume(self, entry: dict) -> None:



        if self._browse_only:
            return
        cover = dict((entry or {}).get("cover") or {})
        if not cover:
            return
        self.session_resume_requested.emit(cover)
        self.accept()
