"""Sessions and Starred pages: the library home for the user's own work.

The rail's Sessions row opens the Sessions page (its internal target stays
"user_favorites": that key is what server copy overrides and anchors point
at, only the user-facing label moved). Top to bottom: a pinned search box,
then the sessions grouped by month (newest first, unparseable dates under a
final "Earlier" bucket). The rail's Starred row ("user_starred") opens the
Starred page: the user's pinned prompts + generations as one card gallery,
promoted out of this page's old secondary shelf.

The Sessions page is a pure view over the cached recent jobs. Each session renders as
one before/after card (the shared generation-card gallery, one gallery per
month); clicking a card opens the generation-detail popup in session mode,
whose Resume / Rename / Delete outcomes bubble to the plugin through the
dialog's session_* signals. The plugin hands fresh lists back through
set_session_jobs().
"""
from __future__ import annotations

import datetime as _dt

from qgis.gui import QgsFilterLineEdit
from qgis.PyQt.QtCore import QLocale, QTimer
from qgis.PyQt.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy
from ....core.i18n import tr
from ....core.prompts.conversation_summary import conversation_entries, filter_entries
from .common import (
    _EMPTY_MSG,
    _SEARCH_BOX,
    _is_alive,
)
from .generation_card import _GenerationCard

# Starred page gallery: smaller cards in 4 columns, like the old hall.
_STARRED_GALLERY_COLUMNS = 4
_STARRED_GALLERY_SLIDER_H = 130

# Month bucket header over a month's session cards. Normal case (the design
# system bans uppercase section titles); the month name itself comes from
# QLocale, so it localizes without tr().
_SESSION_GROUP_LABEL = (
    "QLabel { font-size: 11px; font-weight: 700; color: rgba(128,128,128,0.95);"
    " background: transparent; border: none; padding: 10px 4px 2px 4px; }"
)

# Gallery keys of the month sections, namespaced so a rebuild retires exactly
# its own galleries and never touches Recent / Favorites / starred.
_SESSION_GALLERY_PREFIX = "sessions:"


def _session_month_bucket(iso_ts: str) -> tuple[int, int] | None:
    """(year, month) of the LOCAL date behind an ISO timestamp, or None when
    it does not parse (those sessions group under the final Earlier bucket)."""
    raw = (iso_ts or "").strip()
    if not raw:
        return None
    try:
        # Python <3.11 chokes on the trailing "Z" UTC marker.
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    local = parsed.astimezone()
    return (local.year, local.month)


def _session_month_label(bucket: tuple[int, int]) -> str:
    """"January 2026" style header. QLocale localizes the month name and the
    year is a number, so no tr() is involved."""
    year, month = bucket
    return f"{QLocale().standaloneMonthName(month)} {year}"


class SessionsMixin:
    """Builds and drives the Sessions and Starred pages. Requires the host to
    provide `_recent_jobs`, `_pinned_entries`, `_feed_all_pages`, `_stack`,
    `_build_back_header`, `_build_card_gallery`, `_retire_gallery`,
    `_build_empty_state`,
    `_feed_meta`, the detail-popup collaborators (`_client`, `_demo_loader`,
    `_absolute_demo_url`, `_on_generation_action`, `_on_generation_favorite`)
    and the session_* / sessions_refresh_requested signals (declared on the
    dialog).
    """

    # -- Data ------------------------------------------------------------

    def _session_entries(self) -> list[dict]:
        """The resumable sessions behind the cached recent jobs (newest
        first). Also feeds the rail's Sessions count."""
        return conversation_entries(self._recent_jobs)

    @staticmethod
    def _session_cover_job(entry: dict) -> dict:
        """Job whose images represent the session: the entry's server cover
        (its newest member)."""
        return dict(entry.get("cover") or {})

    # -- Entry points ----------------------------------------------------

    def open_at_sessions(self) -> None:
        """Land the library directly on the Sessions page (dock hint entry).
        Safe to call right after construction, before exec()."""
        self._open_sessions_page()

    def set_session_jobs(self, jobs: list) -> None:
        """Plugin-facing: replace the cached recent jobs (after a delete, a
        rename, or a sessions_refresh_requested fetch) and repaint every
        surface rendering from them. Unconditional list refresh: a rename
        changes titles without changing request ids, which the sync path's
        same-jobs shortcut would otherwise skip."""
        self._on_recent_jobs_fetched(list(jobs or []), self._recent_has_more)
        self._refresh_sessions_if_open()

    def _open_sessions_page(self) -> None:
        """Rebuild + show the Sessions page. Rebuilt on every open so it
        always reflects the current jobs; stored under the historical "work"
        key so the rail sync keeps mapping it to the "user_favorites" row."""
        old = self._feed_all_pages.pop("work", None)
        if old is not None:
            self._stack.removeWidget(old)
            old.deleteLater()
        page = self._build_sessions_page()
        self._feed_all_pages["work"] = page
        self._stack.addWidget(page)
        self._switch_to_page(page)
        # Let the plugin refetch in the background; results come back through
        # set_session_jobs, the list on screen keeps rendering the cache.
        self.sessions_refresh_requested.emit()

    # -- Page ------------------------------------------------------------

    def _build_sessions_page(self) -> QWidget:
        title, tagline = self._feed_meta()["work"]
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(8)
        outer.addWidget(self._build_back_header(title, tagline))

        # Pinned above the scroll area, so it stays visible however long the
        # list grows. QgsFilterLineEdit ships the clear button. Debounced:
        # every keystroke rebuilds card galleries now (heavier than the old
        # text rows), so coalesce typing into one rebuild.
        search = QgsFilterLineEdit()
        search.setPlaceholderText(tr("Search your sessions"))
        search.setStyleSheet(_SEARCH_BOX)
        debounce = QTimer(page)
        debounce.setSingleShot(True)
        debounce.setInterval(180)
        debounce.timeout.connect(self._rebuild_session_list)
        search.valueChanged.connect(lambda _v: debounce.start())
        outer.addWidget(search)
        self._sessions_search = search

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        # The month galleries wire their lazy thumbnail loading to this
        # scroll area on every rebuild.
        self._sessions_scroll = scroll
        content = QWidget()
        content_box = QVBoxLayout(content)
        content_box.setContentsMargins(0, 2, 6, 8)
        content_box.setSpacing(10)

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

    # -- Starred page -----------------------------------------------------

    def _open_starred_page(self) -> None:
        """Rebuild + show the Starred page (the rail's Starred row,
        "user_starred"). Rebuilt on every open, like the Sessions page, so it
        always reflects the current pinned entries."""
        old = self._feed_all_pages.pop("starred", None)
        if old is not None:
            self._stack.removeWidget(old)
            old.deleteLater()
        self._card_widgets = [
            (c, k) for (c, k) in self._card_widgets if k != "work_favorites"
        ]
        # Retire the old gallery outright: a rebuild can come back empty (last
        # star removed), with no _build_card_gallery call to overwrite it.
        self._retire_gallery("work_favorites")
        page = self._build_starred_page()
        self._feed_all_pages["starred"] = page
        self._stack.addWidget(page)
        self._switch_to_page(page)

    def _build_starred_page(self) -> QWidget:
        """The user's starred prompts + starred generations as one card
        gallery (origin pills tell the two kinds apart), exactly what the old
        Sessions-page shelf held, now a page of its own. The gallery keeps its
        "work_favorites" key so the origin-pill rule and the star-refresh
        registry carry over unchanged."""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(8)
        title = get_export_copy("library.starred_title", tr("Your starred prompts"))
        outer.addWidget(self._build_back_header(title))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        content = QWidget()
        content_box = QVBoxLayout(content)
        content_box.setContentsMargins(0, 2, 6, 8)
        content_box.setSpacing(8)

        pinned = self._pinned_entries()
        if pinned:
            self._build_card_gallery(
                "work_favorites", pinned, content_box, scroll,
                columns=_STARRED_GALLERY_COLUMNS,
                slider_h=_STARRED_GALLERY_SLIDER_H,
                paginate=False,
            )
        else:
            # Same empty state as the legacy Favorites tab: star icon + the
            # one line telling the user where stars come from.
            empty = self._build_empty_state("user_favorites")
            if empty is not None:
                content_box.addWidget(empty)

        content_box.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        return page

    def _refresh_starred_if_open(self) -> None:
        """Rebuild the Starred page when it is on screen, so a star toggle
        reflows it immediately. A hidden one rebuilds on open anyway. Called
        from `_reload_dynamic_pages` (already deferred past the click that
        destroyed the toggled card)."""
        page = getattr(self, "_feed_all_pages", {}).get("starred")
        if page is None or self._stack.currentWidget() is not page:
            return
        self._open_starred_page()

    # -- List ------------------------------------------------------------

    def _refresh_sessions_if_open(self) -> None:
        """Re-render the session list when the Sessions page is on screen.
        A hidden page needs nothing: it is rebuilt on every open."""
        page = getattr(self, "_feed_all_pages", {}).get("work")
        if page is None or self._stack.currentWidget() is not page:
            return
        if _is_alive(getattr(self, "_sessions_search", None)):
            self._rebuild_session_list()

    def _rebuild_session_list(self, *_args) -> None:
        """Wipe + repopulate the month-grouped session card galleries from
        the cached jobs, filtered by the search text. A month whose sessions
        are all filtered out simply never renders."""
        box = getattr(self, "_sessions_list_box", None)
        if box is None:
            return
        # Retire the previous month galleries first: their paging state and
        # lazy-load wiring outlive the widgets otherwise.
        for key in [
            k for k in self._gallery_state
            if str(k).startswith(_SESSION_GALLERY_PREFIX)
        ]:
            self._retire_gallery(key)
        # Hide + unparent NOW: deleteLater alone leaves ghost cards painted
        # over the fresh ones until the event loop runs.
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
            query = self._sessions_search.value() or ""
        shown = filter_entries(entries, text=query)

        if not shown:
            if entries:
                message = tr("No session matches.")
            else:
                message = get_export_copy(
                    "library.empty_work",
                    tr("Your generations will live here. Run a Top pick to get started."),
                )
            empty = QLabel(message)
            empty.setStyleSheet(_EMPTY_MSG)
            empty.setWordWrap(True)
            box.addWidget(empty)
            return

        # Bucket by month, insertion-ordered (entries arrive newest first);
        # unparseable dates land in a final Earlier bucket.
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
                tr("Earlier"), undated
            )

        scroll = getattr(self, "_sessions_scroll", None)
        for key, (label, group) in month_groups.items():
            header = QLabel(label)
            header.setStyleSheet(_SESSION_GROUP_LABEL)
            box.addWidget(header)
            section = QWidget()
            section_v = QVBoxLayout(section)
            section_v.setContentsMargins(0, 0, 0, 0)
            section_v.setSpacing(8)
            self._build_card_gallery(
                key, self._session_card_entries(group), section_v, scroll,
                paginate=False,
            )
            box.addWidget(section)

    def _session_card_entries(self, group: list[dict]) -> list[dict]:
        """Gallery entries for one month of sessions: one card per session,
        imaged by its cover, badged with its generation count."""
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
        """One session as a before/after card (dispatched by the shared
        gallery builder). Click opens the session popup, not the plain
        generation detail; the corner badge carries the generation count."""
        session = entry["entry"]
        return _GenerationCard(
            entry["data"],
            self._demo_loader,
            on_open=lambda _j, s=session: self._open_session_detail(s),
            version_count=int(entry.get("count") or 1),
        )

    # -- Actions ---------------------------------------------------------

    def _open_session_detail(self, entry: dict) -> None:
        """Session card click: the generation-detail popup in session mode
        (cover before/after, title/prompt, generation count + date, and
        the Resume / Rename / Delete actions). The popup only records an
        outcome; this maps it onto the dialog's session_* signals. Resume
        also closes the library, landing the user on the dock and canvas."""
        # Same double-open guard as _open_detail: two stacked modals over one
        # loader race on teardown and crash QGIS. One popup at a time.
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
            session_entry=entry,
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
        """Hand the cover job to the plugin and close: resuming lands on the
        dock and the canvas, not in this modal. Entries without location data
        never reach this page (conversation_entries drops them)."""
        if self._browse_only:
            return
        cover = dict((entry or {}).get("cover") or {})
        if not cover:
            return
        self.session_resume_requested.emit(cover)
        self.accept()
