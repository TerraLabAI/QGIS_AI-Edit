"""Prompt Library dialog shell: window, sidebar, and tab switching."""
from __future__ import annotations

from qgis.PyQt.QtCore import QSettings, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QGuiApplication
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.i18n import tr
from ....core.prompts.prompt_presets import get_all_categories, get_need_groups
from ...onboarding_hint import (
    HINT_LIBRARY_INTRO,
    DismissibleHint,
    is_hint_dismissed,
    search_icon,
)
from .common import (
    _NEED_COLLAPSED_SETTING,
    _NEED_HEADER_BTN,
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
from .pages_mixin import PagesMixin
from .search_mixin import SearchMixin
from .sync_mixin import SyncMixin
from .workers import _HistoryPageWorker, _LibrarySyncWorker


class PromptTemplatesDialog(PagesMixin, GalleryMixin, SearchMixin, SyncMixin, QDialog):
    """Tab-style modal for browsing recent, favorites, top picks, and templates."""

    # Emitted when the user acts on a past generation without picking its
    # prompt: action is "add_to_map" or "download", job is the history row.
    # "Reuse prompt" stays on the existing accept()/get_selected_preset path.
    generation_action = pyqtSignal(str, dict)
    # Emitted whenever the Recent/Favorites lists change (fetched or edited) so
    # the dock can keep a session cache and reopen the library instantly.
    history_synced = pyqtSignal(list, list)

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
        # Grid cards (_BeforeAfterCard) stored as generic widgets keyed by the
        # page they live on. Star refresh uses `card.star_button()` and
        # `card.preset()`.
        self._card_widgets: list[tuple[QWidget, str]] = []
        # Default landing: Top Picks. First-time users see curated content,
        # not their empty Recent/Favorites.
        self._active_tab: str = "favorites"
        self._sync_worker: _LibrarySyncWorker | None = None

        # Themed-category pagination state, keyed by category key. Each entry
        # carries the layout, load-more button, and how many cards are visible.
        # Rebuilt fresh every time _build_page runs for a themed tab.
        self._themed_state: dict[str, dict] = {}
        # key -> callable that loads the visible thumbnails for that gallery
        # page (recent/user_favorites). Re-triggered when the tab is shown.
        self._gallery_loaders: dict = {}
        # key -> {grid, jobs, cards, visible, btn} for the Show-more paging.
        self._gallery_state: dict = {}

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
        the available screen on small displays."""
        target_w, target_h = 1220, 880
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(target_w, int(avail.width() * 0.96))
            target_h = min(target_h, int(avail.height() * 0.92))
        self.resize(max(target_w, 640), max(target_h, 480))

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # First-run guidance: a dismissible callout explaining, in plain words,
        # what the library is and the pick -> preview -> use flow. Hidden for
        # good once closed; re-enabled from Account Settings.
        if not is_hint_dismissed(HINT_LIBRARY_INTRO):
            root.addWidget(DismissibleHint(
                HINT_LIBRARY_INTRO,
                tr("Transform your selected area with AI"),
                tr(
                    'These are ready-made instructions ("prompts"). Each one '
                    "tells the AI how to redraw the zone you selected on the map, "
                    "for example segment buildings, classify land cover, or change "
                    "the season."
                ),
                steps=[
                    ("", tr("Pick an example"), tr("browse a category or search")),
                    ("", tr("Preview the result"),
                     tr("click a card to see before and after")),
                    ("", tr("Use it"), tr("it runs on the zone you selected")),
                ],
            ))

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(
            tr('Search prompts...  e.g. "add trees", "segment buildings"')
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

        body = QHBoxLayout()
        body.setSpacing(8)

        # Sidebar - wide enough for the longest label ("Presentation renders"
        # @ 13px font ≈ 165px) + icon + padding without ellipsis on any tab.
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(2)

        # Group the sidebar so the user's own entries read apart from curated
        # templates ("weird that Recent/Favorites are in here" feedback).
        sidebar_layout.addWidget(self._sidebar_section_header(tr("Your prompts")))

        # The user's own lists lead, and curated Top Picks sits right under them
        # (no promoting divider above it - it's a personal shortcut, not the
        # catalog headline). Then a divider, then the themed categories grouped
        # under three foldable needs (Classify / Project / Render), which are
        # the real structure of the template catalog.
        for key in ("user_favorites", "recent", "favorites"):
            cat = self._categories_by_key.get(key)
            if cat is None:
                continue
            btn = self._build_sidebar_button(key, cat)
            sidebar_layout.addWidget(btn)
            self._sidebar_buttons[key] = btn

        sep_wrap = QWidget()
        sep_wrap.setFixedHeight(13)
        sep_inner = QVBoxLayout(sep_wrap)
        sep_inner.setContentsMargins(12, 6, 12, 6)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(128,128,128,0.3); border: none;")
        sep_inner.addWidget(line)
        sidebar_layout.addWidget(sep_wrap)
        # No "Templates" divider here: the three need headers below are the
        # template list's structure, so a grey small-caps divider on top of
        # them just stacks two near-identical header tiers.

        for group in get_need_groups(self._server_catalog):
            need_key = group["key"]
            members: list[_SidebarButton] = []
            header = self._build_need_header(group)
            sidebar_layout.addWidget(header)
            self._need_header_btns[need_key] = header
            for key in group["categories"]:
                cat = self._categories_by_key.get(key)
                if cat is None:
                    continue
                btn = self._build_sidebar_button(key, cat)
                # Indent under the group header so the hierarchy reads at a
                # glance (the header has no icon column of its own).
                btn.layout().setContentsMargins(22, 6, 10, 6)
                sidebar_layout.addWidget(btn)
                self._sidebar_buttons[key] = btn
                members.append(btn)
                self._category_need[key] = need_key
            self._need_members[need_key] = members
            # Empty groups (offline first run without a cached catalog still
            # lists local categories, so this is belt-and-braces) hide whole.
            header.setVisible(bool(members))
            if self._need_collapsed.get(need_key):
                for btn in members:
                    btn.setVisible(False)
            self._update_need_header_text(need_key)

        sidebar_layout.addStretch()
        body.addWidget(sidebar)

        vsep = QFrame()
        vsep.setFrameShape(QtC.FrameVLine)
        vsep.setFrameShadow(QtC.FrameSunken)
        body.addWidget(vsep)

        # Pages are built lazily (only when a tab is first shown) so opening the
        # dialog never pays for the 13 category pages + the 24-card Recent grid
        # and its thumbnail downloads up front. Only the default tab + the
        # search page exist at construction; the rest materialize on demand via
        # _ensure_page.
        self._stack = QStackedWidget()
        self._ensure_page(self._active_tab)
        # Search results page - shown when the search input is non-empty.
        self._search_page = self._build_search_page()
        self._stack.addWidget(self._search_page)

        body.addWidget(self._stack, 1)
        root.addLayout(body, 1)

        # Remember which tab to restore when the search box is cleared.
        self._previous_tab: str = self._active_tab
        self._switch_to_tab(self._active_tab)

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

    @staticmethod
    def _sidebar_section_header(text: str) -> QLabel:
        """Muted uppercase group label for the sidebar (Your prompts / Templates)."""
        lbl = QLabel(text.upper())
        lbl.setStyleSheet(
            "color: rgba(128,128,128,0.95); font-size: 10px; font-weight: 700;"
            " letter-spacing: 0.8px; background: transparent; border: none;"
            " padding: 6px 12px 2px 12px;"
        )
        return lbl

    def _build_sidebar_button(self, key: str, category: dict) -> _SidebarButton:
        count = None
        if key in _TABS_WITH_COUNT:
            count = self._tab_count(key, category)
        btn = _SidebarButton(
            _sidebar_icon_html(key),
            _tab_label(key, category["label"], count),
        )
        btn.setCursor(QtC.PointingHandCursor)
        btn.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        btn.clicked.connect(lambda checked, k=key: self._on_sidebar_click(k))
        return btn

    def _build_need_header(self, group: dict) -> QPushButton:
        """Foldable header for one need group. The tagline lives in the
        tooltip so the 220px sidebar stays clean; the chevron sits inline
        with the text (no icon column), keeping the row visually distinct
        from the category entries below it."""
        need_key = group["key"]
        self._need_collapsed[need_key] = (
            QSettings().value(
                _NEED_COLLAPSED_SETTING.format(key=need_key), False, type=bool
            )
        )
        btn = QPushButton()
        btn.setStyleSheet(_NEED_HEADER_BTN)
        btn.setCursor(QtC.PointingHandCursor)
        btn.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        btn.setToolTip(group["tagline"])
        btn.setProperty("_need_label", group["label"])
        btn.clicked.connect(lambda checked, k=need_key: self._on_toggle_need(k))
        return btn

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
        super().closeEvent(event)
