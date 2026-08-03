"""Prompt Library dialog shell: window, sidebar, and tab switching."""
from __future__ import annotations

from qgis.PyQt.QtCore import QSettings, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QGuiApplication
from qgis.PyQt.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ....core.i18n import tr
from ....core.prompts.prompt_presets import get_all_categories
from ...onboarding_hint import search_icon
from .common import (
    _NEED_COLLAPSED_SETTING,
    _SEARCH_BOX,
    _SIDEBAR_ITEM,
    _SIDEBAR_ITEM_ACTIVE,
    _TABS_WITH_COUNT,
    _is_alive,
    _sidebar_icon_html,
    _tab_label,
)
from .gallery_mixin import GalleryMixin
from .generation_card import _SidebarButton
from .landing_mixin import LandingMixin
from .pages_mixin import PagesMixin
from .rail_mixin import RailMixin
from .search_mixin import SearchMixin
from .sessions_mixin import SessionsMixin
from .sync_mixin import SyncMixin
from .workers import _HistoryPageWorker, _LibrarySyncWorker


class PromptTemplatesDialog(
    RailMixin, LandingMixin, PagesMixin, GalleryMixin, SearchMixin,
    SessionsMixin, SyncMixin, QDialog
):
    """Tab-style modal for browsing recent, favorites, top picks, and templates."""

    # Emitted when the user acts on a past generation without picking its
    # prompt: action is "add_to_map" or "download", job is the history row.
    # "Reuse prompt" stays on the existing accept()/get_selected_preset path.
    generation_action = pyqtSignal(str, dict)
    # Emitted whenever the Recent/Favorites lists change (fetched or edited) so
    # the dock can keep a session cache and reopen the library instantly.
    history_synced = pyqtSignal(list, list)
    # Sessions page (SessionsMixin): the page is a pure view over the cached
    # jobs, so every mutation bubbles to the plugin. Resume carries the
    # session's cover job (the dialog closes itself); delete/rename carry the
    # session entry (key, session_id, cover, members). The refresh signal
    # fires when the page opens; fresh lists come back via set_session_jobs().
    session_resume_requested = pyqtSignal(dict)
    session_delete_requested = pyqtSignal(dict)
    session_rename_requested = pyqtSignal(dict)
    sessions_refresh_requested = pyqtSignal()

    def __init__(
        self,
        parent=None,
        client=None,
        auth_provider=None,
        server_catalog: dict | None = None,
        browse_only: bool = False,
        recent_jobs: list | None = None,
        favorite_jobs: list | None = None,
        history_fresh: bool = False,
    ):
        """
        client: TerraLabClient instance (optional). If None, no server sync.
        auth_provider: callable returning current auth headers dict (optional).
            We take a callable instead of a header so we always send a fresh
            token, even if the user re-activates while the dialog is constructed.
        server_catalog: parsed result of GET /api/ai-edit/presets (optional).
            When provided, presets carry demo_url_before/demo_url_after and the
            dialog renders rich before/after cards. When None, falls back to
            the local prompt_presets catalog with text-only cards.
        browse_only: when True, card clicks do not select a preset (used
            while a generation is in flight). The user can still scroll, star
            favorites, and inspect prompts.
        """
        super().__init__(parent)
        self._browse_only = browse_only
        self.setWindowTitle(
            tr("Prompt library (view only)") if browse_only else tr("Prompt library")
        )
        self.setMinimumSize(640, 480)
        # Open large: size to hug the 3-column grid so there is little empty
        # space on the sides, and grow to most of the screen so the previews
        # read big. Capped to the available screen so it never spills offscreen.
        self._apply_open_size()
        self.setSizeGripEnabled(True)

        self._client = client
        self._auth_provider = auth_provider
        self._server_catalog = server_catalog

        # Async image fetcher, shared by Top Picks demo sliders and the
        # Recent/Favorites generation thumbnails. Instantiated whenever a
        # client exists (history thumbnails need it even without a catalog).
        self._demo_loader = None
        if client is not None:
            from ...template_demo_loader import TemplateDemoLoader

            self._demo_loader = TemplateDemoLoader(self)

        # Past generations for the Recent + Favorites galleries. Seeded from the
        # dock's session cache (instant, no blank-then-fill) and refreshed in the
        # background only when the cache is stale (history_fresh is False).
        self._recent_jobs: list[dict] = list(recent_jobs or [])
        self._favorite_jobs: list[dict] = list(favorite_jobs or [])
        self._history_fresh = bool(history_fresh)
        # Whether the server holds generations older than what we have; drives
        # the Recent tab's server-side Load more. A full warm-cache page means
        # "probably more" until a sync says otherwise.
        self._recent_has_more = len(self._recent_jobs) >= 50
        self._recent_page_worker: _HistoryPageWorker | None = None

        self._selected_preset: dict | None = None
        # A past generation the user chose to fully reproduce (prompt + refs +
        # zone). Read by the dock after exec() to drive the restore flow.
        self._restore_job: dict | None = None
        self._categories_by_key: dict[str, dict] = {}
        self._sidebar_buttons: dict[str, _SidebarButton] = {}
        # Need-group folding: header button + member category buttons + state,
        # keyed by need key. Populated in _build_ui.
        self._need_header_btns: dict[str, QPushButton] = {}
        self._need_members: dict[str, list[_SidebarButton]] = {}
        self._need_collapsed: dict[str, bool] = {}
        # cat_key -> need key, to auto-unfold when a folded tab is targeted.
        self._category_need: dict[str, str] = {}
        self._pages: dict[str, QWidget] = {}
        # Landing redesign: need drill-in pages (R6 hall: sections +
        # scroll-spy state), keyed by need key. The sidebar/tab state above is
        # retained only to keep the galleries, favorites, and search working;
        # it is no longer the entry surface.
        self._need_pages: dict[str, QWidget] = {}
        self._need_state: dict[str, dict] = {}
        # Grid cards (_BeforeAfterCard) stored as generic widgets keyed by the
        # page they live on. Star refresh uses `card.star_button()` and
        # `card.preset()`.
        self._card_widgets: list[tuple[QWidget, str]] = []
        # Default landing: Top Picks. First-time users see curated content,
        # not their empty Recent/Favorites.
        self._active_tab: str = "favorites"
        self._previous_tab: str = "favorites"
        self._sync_worker: _LibrarySyncWorker | None = None

        # key -> callable that loads the visible thumbnails for that gallery
        # page (recent/user_favorites). Re-triggered when the tab is shown.
        self._gallery_loaders: dict = {}
        # key -> {grid, jobs, cards, visible, btn} for the Show-more paging.
        self._gallery_state: dict = {}
        # key -> (scroll, scrollbar slot, debounce timer) so a gallery rebuilt
        # on a surviving scroll area (the Sessions page's month galleries) can
        # retire its previous lazy-load hook instead of stacking a new one.
        self._gallery_lazy_wiring: dict = {}

        self._load_categories()
        self._build_ui()
        self._start_sync()

    # -- Data ------------------------------------------------------------

    def _load_categories(self):
        """Build the category dict from the server catalog.

        `get_all_categories` reads the explicit `server_catalog` first, falls
        back to the locally-cached catalog (`prompt_presets_client`), and
        returns empty themed shells when neither is available."""
        cats = get_all_categories(self._server_catalog)
        self._categories_by_key = {c["key"]: c for c in cats}

    # -- Layout ----------------------------------------------------------

    def _apply_open_size(self):
        """Size to snugly fit the 220px sidebar + a 3-column 300px card grid,
        then grow toward the screen so the previews feel generous. Clamped to
        the screen the dialog will actually open on (Qt centres it on its
        parent), never the primary one: on a two-monitor desk, measuring the
        1920x1080 primary hands the laptop panel a window it cannot fit."""
        target_w, target_h = 1220, 880
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(target_w, int(avail.width() * 0.96))
            target_h = min(target_h, int(avail.height() * 0.92))
        self.resize(max(target_w, 640), max(target_h, 480))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            tr('Search prompts... e.g. "add trees", "segment buildings"')
        )
        self._search_input.addAction(
            search_icon(), QLineEdit.ActionPosition.LeadingPosition
        )
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setStyleSheet(_SEARCH_BOX)
        # Debounce: rebuilding the whole results grid on every keystroke was
        # visibly laggy. Coalesce keystrokes into one rebuild after a short pause.
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(180)
        self._search_debounce.timeout.connect(self._run_search)
        self._search_input.textChanged.connect(self._on_search_changed)
        root.addWidget(self._search_input)

        # Rail-first: a persistent navigation rail sits left of the page stack
        # and is always visible. It navigates (Top picks / one item per category
        # / Sessions); the stack shows the selected page. The old
        # sidebar/tab helpers below remain only to serve the galleries,
        # favorites, and search; they are no longer the entry.
        self._stack = QStackedWidget()

        # Rail built before the pages so `_rail_items` exists when a page switch
        # tries to sync the active row.
        rail = self._build_rail_nav()

        self._landing_page = self._build_landing_page()
        self._stack.addWidget(self._landing_page)
        # Search results page - shown when the search input is non-empty.
        self._search_page = self._build_search_page()
        self._stack.addWidget(self._search_page)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addWidget(rail)
        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)

        self._stack.setCurrentWidget(self._landing_page)
        self._set_rail_active("popular")
        # TODO(telemetry Task 6): track LIBRARY_LANDING_VIEWED once the event is
        # added to the website registry + analytics_events.json.

    # -- Rail navigation -------------------------------------------------

    def _rail_navigate(self, target: str) -> None:
        """Show the page a rail row stands for. `target` is "popular" (Top
        picks / the landing), "need:<key>" (a category drill-in),
        "user_favorites" (the Sessions page) or "user_starred" (the Starred
        page: the user's pinned prompts and generations). Clears any active
        search first so the rail is never fighting a filtered results page."""
        if self._search_input.text().strip():
            self._search_input.blockSignals(True)
            self._search_input.clear()
            self._search_input.blockSignals(False)
        if target == "popular":
            self._switch_to_page(self._landing_page)
        elif target.startswith("need:"):
            page = self._ensure_need_page(target.split(":", 1)[1])
            if page is not None:
                self._switch_to_page(page)
        elif target == "user_favorites":
            # Historical target key kept (anchors + server copy point at it);
            # the destination is the Sessions page.
            self._open_sessions_page()
        elif target == "user_starred":
            self._open_starred_page()

    def _sync_rail_for_page(self, widget: QWidget) -> None:
        """Light the rail row matching the page now shown, or clear the rail for
        pages with no row (search). Central so back buttons and search-clear keep
        the rail truthful without each caller repeating it."""
        if getattr(self, "_rail_items", None) is None:
            return
        target: str | None = None
        need_key: str | None = None
        if widget is self._landing_page:
            target = "popular"
        elif widget is self._feed_all_pages.get("work"):
            target = "user_favorites"
        elif widget is self._feed_all_pages.get("starred"):
            target = "user_starred"
        else:
            for key, page in self._need_pages.items():
                if page is widget:
                    target = f"need:{key}"
                    need_key = key
                    break
        self._set_rail_active(target)
        # The open family lists its subfamilies indented in the rail (a table
        # of contents); any other page folds them away.
        if need_key is not None:
            state = self._need_state.get(need_key) or {}
            self._show_rail_subfamilies(need_key, state.get("entries", []))
            active = state.get("active")
            if active:
                # Re-apply after the rebuild (the rows start unhighlighted).
                self._rail_sub_active = None
                self._set_rail_active_subfamily(need_key, active)
        else:
            self._clear_rail_subfamilies()

    def _tab_count(self, key: str, category: dict | None = None) -> int:
        """Sidebar badge count. Recent/Favorites count past generations;
        other counted tabs count their presets."""
        if key == "recent":
            return len(self._recent_jobs)
        if key == "user_favorites":
            # Both halves of the unified tab: starred templates + starred gens.
            cat = category or self._categories_by_key.get(key) or {}
            return len(self._favorite_jobs) + len(cat.get("presets", []))
        cat = category or self._categories_by_key.get(key) or {}
        return len(cat.get("presets", []))

    def _update_need_header_text(self, need_key: str):
        btn = self._need_header_btns.get(need_key)
        if btn is None or not _is_alive(btn):
            return
        chevron = "▸" if self._need_collapsed.get(need_key) else "▾"
        label = str(btn.property("_need_label") or "")
        btn.setText(f"{chevron}  {label}")

    def _on_toggle_need(self, need_key: str):
        """Fold or unfold one need group and remember the choice."""
        collapsed = not self._need_collapsed.get(need_key, False)
        self._need_collapsed[need_key] = collapsed
        QSettings().setValue(
            _NEED_COLLAPSED_SETTING.format(key=need_key), collapsed
        )
        for btn in self._need_members.get(need_key, []):
            if _is_alive(btn):
                btn.setVisible(not collapsed)
        self._update_need_header_text(need_key)

    def _ensure_need_visible(self, cat_key: str):
        """Unfold the need group holding `cat_key` (e.g. tab targeted from a
        search result) so its highlighted button is actually visible."""
        need_key = self._category_need.get(cat_key)
        if need_key and self._need_collapsed.get(need_key):
            self._on_toggle_need(need_key)

    def _on_sidebar_click(self, key: str):
        """Sidebar click is an explicit "leave search" - clear the box."""
        if self._search_input.text().strip():
            self._search_input.blockSignals(True)
            self._search_input.clear()
            self._search_input.blockSignals(False)
        self._switch_to_tab(key)

    def _refresh_sidebar_button(self, key: str):
        cat = self._categories_by_key.get(key)
        if cat is None or key not in self._sidebar_buttons:
            return
        count = None
        if key in _TABS_WITH_COUNT:
            count = self._tab_count(key, cat)
        self._sidebar_buttons[key].set_label_html(
            _sidebar_icon_html(key),
            _tab_label(key, cat["label"], count),
        )

    # -- Tab switching ---------------------------------------------------

    def _switch_to_tab(self, key: str):
        """Show the stack page for `key` and highlight its sidebar button.
        Does not touch the search input - clearing the search is the caller's
        responsibility (see _on_sidebar_click)."""
        if self._ensure_page(key) is None:
            return
        # If the target sits in a folded need group, unfold it first so its
        # highlighted button is actually visible (e.g. selected via search).
        self._ensure_need_visible(key)
        self._active_tab = key
        self._previous_tab = key
        self._stack.setCurrentWidget(self._pages[key])
        for k, btn in self._sidebar_buttons.items():
            btn.setStyleSheet(_SIDEBAR_ITEM_ACTIVE if k == key else _SIDEBAR_ITEM)
        # Now that the page has a real viewport size, load its visible
        # thumbnails (a gallery rebuilt while hidden couldn't measure them).
        trigger = self._gallery_loaders.get(key)
        if trigger is not None:
            QTimer.singleShot(0, trigger)

    def get_selected_preset(self) -> dict | None:
        return self._selected_preset

    def get_restore_job(self) -> dict | None:
        """A past generation the user chose to fully reproduce, or None."""
        return self._restore_job

    # -- Cleanup ---------------------------------------------------------

    def closeEvent(self, event):  # noqa: N802
        # Do NOT block here. Background workers are unparented and detached (see
        # _detach_worker): they finish on their own and self-delete, and their
        # data slots are bound methods Qt drops when this dialog is destroyed, so
        # nothing lands on a dead object. The old quit()+wait() froze the UI and
        # did nothing useful (a run()-override QThread has no event loop to quit).
        # Shutdown is the one place that must wait, and it does:
        # workers.drain_prompt_library_workers() runs from unload() and from
        # aboutToQuit, so Qt never destroys a thread still inside run().
        super().closeEvent(event)
