
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
from .sessions_mixin import _SEARCH_DEBOUNCE_MS, SessionsMixin
from .sync_mixin import SyncMixin
from .workers import _RECENT_PAGE_SIZE, _HistoryPageWorker, _LibrarySyncWorker


_SEARCH_GLYPH_PX = 16



_OPEN_WIDTH_RATIO = 0.96
_OPEN_HEIGHT_RATIO = 0.92


class PromptTemplatesDialog(
    RailMixin, LandingMixin, PagesMixin, GalleryMixin, SearchMixin,
    SessionsMixin, SyncMixin, QDialog
):





    generation_action = pyqtSignal(str, dict)


    history_synced = pyqtSignal(list, list)





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













        super().__init__(parent)
        self.setObjectName("promptLibrary")
        self.setStyleSheet(LIBRARY_DIALOG_QSS)
        self._browse_only = browse_only



        self.setWindowTitle(
            get_export_copy("dialogs.dialog.window_title_library_view_only", tr("Library (view only)"))
            if browse_only
            else get_export_copy("dialogs.dialog.window_title_library", tr("Library"))
        )
        self.setMinimumSize(640, 480)



        self._apply_open_size()
        self.setSizeGripEnabled(True)

        self._client = client
        self._auth_provider = auth_provider
        self._server_catalog = server_catalog




        self._demo_loader = None
        if client is not None:
            from ...template_demo_loader import TemplateDemoLoader

            self._demo_loader = TemplateDemoLoader(self)




        self._recent_jobs: list[dict] = list(recent_jobs or [])
        self._favorite_jobs: list[dict] = list(favorite_jobs or [])
        self._history_fresh = bool(history_fresh)



        self._recent_has_more = len(self._recent_jobs) >= _RECENT_PAGE_SIZE
        self._recent_page_worker: _HistoryPageWorker | None = None

        self._selected_preset: dict | None = None


        self._restore_job: dict | None = None
        self._categories_by_key: dict[str, dict] = {}
        self._sidebar_buttons: dict[str, _SidebarButton] = {}
        self._pages: dict[str, QWidget] = {}




        self._need_pages: dict[str, QWidget] = {}
        self._need_state: dict[str, dict] = {}



        self._card_widgets: list[tuple[QWidget, str]] = []


        self._active_tab: str = "favorites"
        self._previous_tab: str = "favorites"
        self._sync_worker: _LibrarySyncWorker | None = None



        self._gallery_loaders: dict = {}

        self._gallery_state: dict = {}



        self._gallery_lazy_wiring: dict = {}

        self._load_categories()
        self._build_ui()
        self._start_sync()


        settle_dialog_default_button(self)

    def showEvent(self, event):  # noqa: N802

        settle_dialog_default_button(self)
        super().showEvent(event)



    def _load_categories(self):





        cats = get_all_categories(self._server_catalog)
        self._categories_by_key = {c["key"]: c for c in cats}



    def _apply_open_size(self):





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


        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        right = QVBoxLayout()
        right.setContentsMargins(24, 18, 12, 0)
        right.setSpacing(14)

        self._search_input = QLineEdit()
        self._search_input.setObjectName("librarySearch")



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


        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(
            get_export_dial("dialogs.dialog.search_debounce_ms", _SEARCH_DEBOUNCE_MS)
        )
        self._search_debounce.timeout.connect(self._run_search)
        self._search_input.textChanged.connect(self._on_search_changed)


        self._search_input.installEventFilter(self)

        find = QtC.QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        find.activated.connect(self._focus_search)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 12, 0)
        search_row.addWidget(self._search_input)
        right.addLayout(search_row)






        self._stack = QStackedWidget()



        rail = self._build_rail_nav()

        self._landing_page = self._build_landing_page()
        self._stack.addWidget(self._landing_page)

        self._search_page = self._build_search_page()
        self._stack.addWidget(self._search_page)

        right.addWidget(self._stack, 1)
        root.addWidget(rail)
        root.addLayout(right, 1)

        self._stack.setCurrentWidget(self._landing_page)
        self._stack.currentChanged.connect(self._sync_search_for_page)
        self._set_rail_active("popular")


        self._search_input.setFocus()



    @staticmethod
    def _prompt_search_placeholder() -> str:
        return get_export_copy("dialogs.dialog.search_prompts_placeholder", tr("Search prompts"))

    def _sync_search_for_page(self, _index: int = 0) -> None:





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


        card = self._first_card_on_page()
        if card is None:
            return False
        card.setFocus(Qt.FocusReason.TabFocusReason)
        return True

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._search_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape and self._search_input.text():
                self._search_input.clear()
                return True

            if key in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._focus_first_card():
                    return True
        return super().eventFilter(obj, event)



    def _rail_navigate(self, target: str) -> None:





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


            self._open_sessions_page()
        elif target == "user_starred":
            self._open_starred_page()

    def _sync_rail_for_page(self, widget: QWidget) -> None:



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


        if need_key is not None:
            state = self._need_state.get(need_key) or {}
            self._show_rail_subfamilies(need_key, state.get("entries", []))
            active = state.get("active")
            if active:

                self._rail_sub_active = None
                self._set_rail_active_subfamily(need_key, active)
        else:
            self._clear_rail_subfamilies()

    def _tab_count(self, key: str, category: dict | None = None) -> int:


        if key == "recent":
            return len(self._recent_jobs)
        if key == "user_favorites":

            cat = category or self._categories_by_key.get(key) or {}
            return len(self._favorite_jobs) + len(cat.get("presets", []))
        cat = category or self._categories_by_key.get(key) or {}
        return len(cat.get("presets", []))

    def _on_sidebar_click(self, key: str):

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



    def _switch_to_tab(self, key: str):



        if self._ensure_page(key) is None:
            return
        self._active_tab = key
        self._previous_tab = key
        self._stack.setCurrentWidget(self._pages[key])
        for k, btn in self._sidebar_buttons.items():
            btn.setStyleSheet(_SIDEBAR_ITEM_ACTIVE if k == key else _SIDEBAR_ITEM)


        trigger = self._gallery_loaders.get(key)
        if trigger is not None:
            QTimer.singleShot(0, trigger)

    def get_selected_preset(self) -> dict | None:
        return self._selected_preset

    def get_restore_job(self) -> dict | None:

        return self._restore_job



    def closeEvent(self, event):  # noqa: N802








        super().closeEvent(event)
