
































from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.config_store import get_export_copy
from ..core.i18n import tr
from .cross_plugin_discovery import (
    SIBLINGS,
    STATE_ENABLE,
    STATE_INSTALL,
    STATE_OPEN,
    STATE_RESTART,
    STATE_UPDATE,
    open_sibling_tutorial,
    run_sibling_action,
    sibling_link,
    sibling_state,
)
from .dock.design_tokens import (
    BTN_GHOST_QSS,
    BTN_LINK_QSS,
    BTN_PRIMARY_QSS,
    BTN_PX,
    BTN_QUIET_QSS,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    HINT_QSS,
    INK,
    INK_2,
    LINE,
    ORANGE_TEXT,
    PAGE,
    RADIUS_CARD,
    SURFACE,
)
from .icons import widget_pixel_ratio
from .sibling_thumbnails import (
    GuideStill,
    ThumbnailLoader,
    cached_thumbnail,
    is_thumbnail_url_usable,
    logo_tile_pixmap,
    sibling_logo_path,
)

_LOGO_PX = 28



_CARD_MAX_W = 520
_CARD_MIN_W = 280
_CARD_PADDING = 14
_GRID_SPACING = 12
_DIALOG_MARGIN = 20



_CARD_QSS = (
    f"QFrame#siblingCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QLabel#siblingName {{ font-size: {FONT_BASE + 1}px; font-weight: 600; color: {INK};"
    " background: transparent; border: none; }"
    f"QLabel#siblingTagline {{ font-size: {FONT_BODY}px; color: {INK_2};"
    " background: transparent; border: none; }"
    f"QLabel#siblingState {{ font-size: {FONT_HINT}px; color: {INK_2};"
    " background: transparent; border: none; }"
    f'QLabel#siblingState[warn="true"] {{ color: {ORANGE_TEXT}; }}'
    f"QLabel#siblingProblem {{ font-size: {FONT_HINT}px; color: {ORANGE_TEXT};"
    " background: transparent; border: none; }"
)


def _card_copy():






    return (
        ("ai-agent", get_export_copy(
            "widgets.siblings_dialog.ai_agent_tagline",
            tr("Run QGIS from a single sentence."))),
        ("ai-segmentation", get_export_copy(
            "widgets.siblings_dialog.ai_segmentation_tagline",
            tr("Turn buildings, trees or water into polygons."))),
    )


def _action_copy(state: str):

    if state == STATE_OPEN:
        return (get_export_copy("widgets.siblings_dialog.open_in_qgis", tr("Open in QGIS")),
                get_export_copy("widgets.siblings_dialog.open_action_tooltip",
                                tr("Show the plugin's panel.")),
                BTN_GHOST_QSS, True)
    if state == STATE_UPDATE:
        return (get_export_copy("widgets.siblings_dialog.update_in_qgis", tr("Update")),
                get_export_copy("widgets.siblings_dialog.update_action_tooltip",
                                tr("A newer version is ready. Opens the QGIS plugin manager on it.")),
                BTN_PRIMARY_QSS, True)
    if state == STATE_ENABLE:
        return (get_export_copy("widgets.siblings_dialog.enable_in_qgis", tr("Turn on")),
                get_export_copy("widgets.siblings_dialog.enable_action_tooltip",
                                tr("It is installed but switched off. Turns it on and opens it.")),
                BTN_PRIMARY_QSS, True)
    if state == STATE_RESTART:
        return (get_export_copy("widgets.siblings_dialog.restart_qgis", tr("Restart QGIS")),
                get_export_copy("widgets.siblings_dialog.restart_action_tooltip",
                                tr("It is installed but did not start. Restart QGIS to use it.")),
                BTN_GHOST_QSS, False)
    return (get_export_copy("widgets.siblings_dialog.install_in_qgis", tr("Install in QGIS")),
            get_export_copy("widgets.siblings_dialog.install_action_tooltip",
                            tr("Opens the QGIS plugin manager on this plugin.")),
            BTN_PRIMARY_QSS, True)


def _state_copy(state: str) -> str:
    if state == STATE_OPEN:
        return get_export_copy("widgets.siblings_dialog.installed_state", tr("Installed"))
    if state == STATE_UPDATE:
        return get_export_copy("widgets.siblings_dialog.update_state", tr("Update available"))
    if state == STATE_ENABLE:
        return get_export_copy("widgets.siblings_dialog.disabled_state", tr("Turned off"))
    if state == STATE_RESTART:
        return get_export_copy("widgets.siblings_dialog.restart_state", tr("Needs a restart"))
    return ""


class SiblingCard(QFrame):


    install_requested = pyqtSignal(str)
    tutorial_requested = pyqtSignal(str)

    def __init__(self, product_id: str, note: str, parent=None):
        super().__init__(parent)
        self.product_id = str(product_id)
        self.state = STATE_INSTALL
        sibling = SIBLINGS.get(self.product_id, {})
        self.setObjectName("siblingCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_CARD_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMaximumWidth(_CARD_MAX_W)

        column = QVBoxLayout(self)
        column.setContentsMargins(_CARD_PADDING, _CARD_PADDING, _CARD_PADDING, _CARD_PADDING)
        column.setSpacing(10)

        logo_path = sibling_logo_path(str(sibling.get("icon") or ""))
        self._shot = GuideStill(logo_path, self)
        column.addWidget(self._shot)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(10)


        chip = logo_tile_pixmap(logo_path, _LOGO_PX, widget_pixel_ratio(self)) if logo_path else None
        if chip is not None:
            mark = QLabel(self)
            mark.setPixmap(chip)
            mark.setFixedSize(_LOGO_PX, _LOGO_PX)
            head.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        name = QLabel(str(sibling.get("label") or ""), self)
        name.setObjectName("siblingName")
        head.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
        self._state = QLabel("", self)
        self._state.setObjectName("siblingState")
        head.addWidget(self._state, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        column.addLayout(head)

        body = QLabel(note, self)
        body.setObjectName("siblingTagline")
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        column.addWidget(body, 1)


        self._problem = QLabel(get_export_copy(
            "widgets.siblings_dialog.enable_failed",
            tr("QGIS could not turn it on. Tick it in Plugins > Manage and Install Plugins.")), self)
        self._problem.setObjectName("siblingProblem")
        self._problem.setWordWrap(True)
        self._problem.hide()
        column.addWidget(self._problem)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self._action = QPushButton(self)
        self._action.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action.setAutoDefault(False)
        self._action.setFixedHeight(BTN_PX)
        self._action.clicked.connect(lambda: self.install_requested.emit(self.product_id))
        actions.addWidget(self._action, 0)


        guide = QPushButton(get_export_copy("widgets.siblings_dialog.read_guide", tr("Read the guide")), self)
        guide.setStyleSheet(BTN_QUIET_QSS)
        guide.setFixedHeight(BTN_PX)
        guide.setCursor(Qt.CursorShape.PointingHandCursor)
        guide.setAutoDefault(False)
        guide.setToolTip(get_export_copy(
            "widgets.siblings_dialog.read_guide_tooltip", tr("The written tutorial, on the TerraLab blog.")))
        guide.clicked.connect(lambda: self.tutorial_requested.emit(self.product_id))
        actions.addWidget(guide, 0)
        actions.addStretch(1)
        column.addLayout(actions)

        self._loader = None
        self._load_shot(sibling_link(self.product_id, "thumbnail_url"))
        self.refresh()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)



        self._shot.set_width(max(1, event.size().width() - 2 * _CARD_PADDING))

    def _load_shot(self, url: str) -> None:

        if not is_thumbnail_url_usable(url):
            return
        cached = cached_thumbnail(url)
        if cached is not None:
            self._shot.set_image(cached)
            return
        self._loader = ThumbnailLoader(self)
        self._loader.loaded.connect(self._shot.set_image)
        self._loader.fetch(url)

    def refresh(self) -> None:

        self.state = sibling_state(self.product_id)
        label, tooltip, style, enabled = _action_copy(self.state)
        self._action.setText(label)
        self._action.setToolTip(tooltip)
        self._action.setStyleSheet(style)
        self._action.setEnabled(enabled)
        self._state.setText(_state_copy(self.state))
        self._state.setProperty("warn", self.state in (STATE_UPDATE, STATE_RESTART))
        self._state.style().unpolish(self._state)
        self._state.style().polish(self._state)
        if self.state != STATE_ENABLE:
            self._problem.hide()

    def show_problem(self) -> None:
        self._problem.show()


class SiblingsPage(QWidget):







    opened = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._host = QWidget(self)
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(_GRID_SPACING)
        self._cards = [SiblingCard(product_id, note, self._host) for product_id, note in _card_copy()]
        for card in self._cards:
            card.install_requested.connect(self._on_install)
            card.tutorial_requested.connect(self._on_tutorial)
        self._columns = 0
        self._reflow(2)


        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._host, 1, Qt.AlignmentFlag.AlignTop)
        row.addStretch(0)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)

        room = event.size().width()
        self._reflow(2 if room >= 2 * self._card_min_width() + _GRID_SPACING else 1)

    def _card_min_width(self) -> int:



        widest = max((card.minimumSizeHint().width() for card in self._cards), default=0)
        return max(_CARD_MIN_W, widest)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def _reflow(self, columns: int) -> None:





        if columns == self._columns:
            return
        self._columns = columns
        for index, card in enumerate(self._cards):


            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self._host.setMaximumWidth(columns * _CARD_MAX_W + (columns - 1) * _GRID_SPACING)



        self.setMinimumWidth(self._card_min_width())

    def refresh(self) -> None:
        for card in self._cards:
            card.refresh()

    def _on_install(self, product_id: str) -> None:
        outcome = run_sibling_action(product_id)


        self.refresh()
        if outcome == "enable_failed":
            for card in self._cards:
                if card.product_id == product_id:
                    card.show_problem()
        self.opened.emit(product_id, outcome)

    def _on_tutorial(self, product_id: str) -> None:
        open_sibling_tutorial(product_id)
        self.opened.emit(product_id, "guide")


def build_siblings_page(parent=None) -> SiblingsPage:






    return SiblingsPage(parent)


def _open_more_url() -> None:


    from .external_url import open_external
    from .terralab_menu import TERRALAB_URL

    open_external(TERRALAB_URL)


class SiblingsDialog(QDialog):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("siblingsDialog")
        self.setWindowTitle(get_export_copy("widgets.siblings_dialog.window_title", tr("More from TerraLab")))
        self.setModal(False)


        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(f"QDialog#siblingsDialog {{ background: {PAGE}; }}")


        self.setMinimumWidth(_CARD_MIN_W + 2 * _DIALOG_MARGIN)

        column = QVBoxLayout(self)
        column.setContentsMargins(_DIALOG_MARGIN, _DIALOG_MARGIN, _DIALOG_MARGIN, 16)
        column.setSpacing(14)

        intro = QLabel(get_export_copy(
            "widgets.siblings_dialog.intro", tr("Other TerraLab plugins for QGIS")),
            self)
        intro.setWordWrap(True)
        intro.setStyleSheet(HINT_QSS)
        column.addWidget(intro)

        self._page = SiblingsPage(self)
        column.addWidget(self._page)
        column.addStretch(1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(6)
        more = QPushButton(get_export_copy("widgets.siblings_dialog.everything_we_make", tr("Everything we make")),
                           self)
        more.setStyleSheet(BTN_LINK_QSS)
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.setAutoDefault(False)
        more.clicked.connect(_open_more_url)
        footer.addWidget(more)
        footer.addStretch(1)
        close = QPushButton(get_export_copy("widgets.siblings_dialog.close", tr("Close")), self)
        close.setStyleSheet(BTN_GHOST_QSS)
        close.setFixedHeight(BTN_PX)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setAutoDefault(False)
        close.clicked.connect(self.close)
        footer.addWidget(close)
        column.addLayout(footer)



        column.activate()
        self.resize(2 * 320 + _GRID_SPACING + 2 * _DIALOG_MARGIN, self.sizeHint().height())

    @property
    def page(self) -> SiblingsPage:
        return self._page

    def refresh(self) -> None:
        self._page.refresh()


_OPEN_DIALOG = None


def show_siblings_dialog(parent=None) -> SiblingsDialog:





    global _OPEN_DIALOG  # noqa: PLW0603
    try:
        if _OPEN_DIALOG is not None and _OPEN_DIALOG.isVisible():
            _OPEN_DIALOG.refresh()
            _OPEN_DIALOG.raise_()
            _OPEN_DIALOG.activateWindow()
            return _OPEN_DIALOG
    except RuntimeError:
        _OPEN_DIALOG = None
    _OPEN_DIALOG = SiblingsDialog(parent)
    _OPEN_DIALOG.show()
    _OPEN_DIALOG.raise_()
    _OPEN_DIALOG.activateWindow()
    _OPEN_DIALOG.setFocus()
    return _OPEN_DIALOG
