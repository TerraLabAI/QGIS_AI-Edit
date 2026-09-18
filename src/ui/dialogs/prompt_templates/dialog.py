"""Prompt Library dialog shell: window, sidebar, and tab switching."""
from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy, get_export_dial, get_export_dial_ratio
from ....core.i18n import tr
from ....core.prompts.prompt_presets import get_all_categories
from ...icons import icon_for
from ...keyboard_focus import settle_dialog_default_button
from ...panel_helpers import screen_for_dialog
from .common import (
    _SEARCH_BOX,
    _SIDEBAR_ITEM,
    _SIDEBAR_ITEM_ACTIVE,
    _TABS_WITH_COUNT,
    LIBRARY_DIALOG_QSS,
    _sidebar_icon_html,
    _tab_label,
    paint_search_clear_button,
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

# The magnifier at the left of the search field, px.
_SEARCH_GLYPH_PX = 16

# Debounce before a keystroke in the search box rebuilds the results grid.
_SEARCH_DEBOUNCE_MS = 180

# How much of the available screen width/height the dialog may claim when
# opening large, so it never asks for more than the screen it opens on.
_OPEN_WIDTH_RATIO = 0.96
_OPEN_HEIGHT_RATIO = 0.92
# History pages come back this many rows at a time; a full page from the
# session cache is treated as "there is probably more" until a sync says
# otherwise. Matches the fetch size in workers.py's history page worker.
_RECENT_PAGE_SIZE = 50


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
        self.setObjectName("promptLibrary")
        self.setStyleSheet(LIBRARY_DIALOG_QSS)
        self._browse_only = browse_only
        # "Library", the word of the dock button that opens it and of the
        # "Open the Library" notice. Fresh keys, so an older served value
        # cannot bring "Prompt library" back.
        self.setWindowTitle(
            get_export_copy("dialogs.dialog.window_title_library_view_only", tr("Library (view only)"))
            if browse_only
            else get_export_copy("dialogs.dialog.window_title_library", tr("Library"))
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
        self._recent_has_more = len(self._recent_jobs) >= _RECENT_PAGE_SIZE
        self._recent_page_worker: _HistoryPageWorker | None = None

        self._selected_preset: dict | None = None
        # A past generation the user chose to fully reproduce (prompt + refs +
        # zone). Read by the dock after exec() to drive the restore flow.
        self._restore_job: dict | None = None
        self._categories_by_key: dict[str, dict] = {}
        self._sidebar_buttons: dict[str, _SidebarButton] = {}
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
        # No filled action on the library: Enter in the search field never
        # presses a card button or Show more.
        settle_dialog_default_button(self)

    def showEvent(self, event):  # noqa: N802 - Qt signature
        # Before QDialog picks a default among buttons built since __init__.
        settle_dialog_default_button(self)
        super().showEvent(event)

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
        screen = screen_for_dialog(self.parentWidget())
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(target_w, int(avail.width() * get_export_dial_ratio(
                "dialogs.dialog.open_width_ratio", _OPEN_WIDTH_RATIO)))
            target_h = min(target_h, int(avail.height() * get_export_dial_ratio(
                "dialogs.dialog.open_height_ratio", _OPEN_HEIGHT_RATIO)))
        self.resize(max(target_w, 640), max(target_h, 480))

    def _build_ui(self):
        # AI Agent's settings window: the rail runs the full height at the
        # left; the right column holds the search field over the page stack.
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        right = QVBoxLayout()
        right.setContentsMargins(24, 18, 12, 0)
        right.setSpacing(14)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("librarySearch")
        # Short, like ChatGPT's "Search GPTs": the long example sentence was
        # cut to "flood th..." at the window's minimum width. The example
        # lives in the no-result hint.
        self._search_scope = "prompts"
        self._search_input.setPlaceholderText(self._prompt_search_placeholder())
        self._search_input.setAccessibleName(self._prompt_search_placeholder())
        self._search_input.addAction(
            icon_for(self._search_input, "search", _SEARCH_GLYPH_PX),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self._search_input.setClearButtonEnabled(True)
        paint_search_clear_button(self._search_input)
        self._search_input.setStyleSheet(_SEARCH_BOX)
        # Debounce: rebuilding the whole results grid on every keystroke was
        # visibly laggy. Coalesce keystrokes into one rebuild after a short pause.
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(
            get_export_dial("dialogs.dialog.search_debounce_ms", _SEARCH_DEBOUNCE_MS)
        )
        self._search_debounce.timeout.connect(self._run_search)
        self._search_input.textChanged.connect(self._on_search_changed)
        # Down from the field steps into the first card of the page; Escape
        # clears a query before it closes the window.
        self._search_input.installEventFilter(self)
        # Ctrl+F (Cmd+F) jumps back to the field from anywhere in the window.
        find = QtC.QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        find.activated.connect(self._focus_search)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 12, 0)
        search_row.addWidget(self._search_input)
        right.addLayout(search_row)

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

        right.addWidget(self._stack, 1)
        root.addWidget(rail)
        root.addLayout(right, 1)

        self._stack.setCurrentWidget(self._landing_page)
        self._stack.currentChanged.connect(self._sync_search_for_page)
        self._set_rail_active("popular")
        # Typing is the fastest way in, as in AI Agent's library: the search
        # field holds the focus when the window opens.
        self._search_input.setFocus()
        # TODO(telemetry Task 6): track LIBRARY_LANDING_VIEWED once the event is
        # added to the website registry + analytics_events.json.

    @staticmethod
    def _prompt_search_placeholder() -> str:
        return get_export_copy("dialogs.dialog.search_prompts_placeholder", tr("Search prompts"))

    def _sync_search_for_page(self, _index: int = 0) -> None:
        """One search field for the whole window, always in the same place.
        On the Sessions page it filters the sessions (its placeholder says
        so); everywhere else it searches the prompts. The old second field
        on the Sessions page pushed that page's title 50 px higher than every
        other page's."""
        sessions = getattr(self, "_feed_all_pages", {}).get("work")
        on_sessions = sessions is not None and self._stack.currentWidget() is sessions
        scope = "sessions" if on_sessions else "prompts"
        if scope == self._search_scope:
            return
        self._search_scope = scope
        text = (
            get_export_copy("dialogs.sessions_mixin.search_placeholder", tr("Search your sessions"))
            if on_sessions else self._prompt_search_placeholder()
        )
        self._search_input.setPlaceholderText(text)
        self._search_input.setAccessibleName(text)

    def _focus_search(self) -> None:
        self._search_input.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search_input.selectAll()

    def _first_card_on_page(self) -> QWidget | None:
        """The first card of the page on screen, in reading order, skipping
        cards scrolled above the view."""
        page = self._stack.currentWidget()
        if page is None:
            return None
        placed = []
        for card in page.findChildren(QFrame, "card"):
            if not card.isVisibleTo(page) or card.focusPolicy() == Qt.FocusPolicy.NoFocus:
                continue
            top_left = card.mapTo(page, card.rect().topLeft())
            if top_left.y() + card.height() <= 0:
                continue
            placed.append((top_left.y(), top_left.x(), card))
        if not placed:
            return None
        return min(placed, key=lambda entry: (entry[0], entry[1]))[2]

    def _focus_first_card(self) -> bool:
        """Keyboard focus to the page's first card on screen. False when the
        page shows none (an empty state)."""
        card = self._first_card_on_page()
        if card is None:
            return False
        card.setFocus(Qt.FocusReason.TabFocusReason)
        return True

    def eventFilter(self, obj, event):  # noqa: N802 - Qt signature
        if obj is self._search_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape and self._search_input.text():
                self._search_input.clear()
                return True
            # Down, or Enter once the results are in, steps into the grid.
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._focus_first_card():
                    return True
        return super().eventFilter(obj, event)

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
