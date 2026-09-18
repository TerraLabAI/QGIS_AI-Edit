"""The dock's title bar: the banana mark, the name, and the panel's own buttons.

``[mark] AI Edit   [Get Pro] [history] [gear] [float] [x]``

AI Agent's header (``QGIS_AI-Agent-Team/src/ui/header.py``) in AI Edit's
colours. The same set in every TerraLab plugin (2026-09-17): Settings holds the
tutorial, shortcuts, contact and report pages, so the header carries no book
or help menu; the tutorial link sits on the home page instead.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from qgis.PyQt.QtCore import QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ..icons import icon_for, logo_pixmap, logo_size
from .design_tokens import (
    BTN_ICON_QSS,
    INK,
    INK_2,
    INK_3,
    LINE,
    SPACE_TIGHT,
    qcolor,
)
from .style import DOCK_BRANDING_URL
from .widgets import _FooterIconButton

if TYPE_CHECKING:
    from .widget import AIEditDockWidget

HEADER_HEIGHT_PX = 36
# The mark beside the name: 20 tall in a box cut to its portrait shape.
_MARK_PX = 20
_MARK_GAP_PX = 6
_GLYPH_PX = 18
_HEADER_BTN_PX = 28

_TITLE_PX = 12
_BYLINE_PX = 10

_HEADER_QSS = (
    "QWidget#aiEditHeader { background: transparent;"
    f" border-bottom: 1px solid {LINE}; }}"
    f"QLabel#aiEditHeaderTitle {{ font-size: {_TITLE_PX}px; font-weight: 600;"
    f" color: {INK}; background: transparent; border: none; }}"
    f"QLabel#aiEditHeaderByline {{ font-size: {_BYLINE_PX}px; font-weight: 400;"
    f" color: {INK_2}; background: transparent; border: none; }}"
    "QLabel { background: transparent; border: none; }"
)


class _BrandTile(QWidget):
    """The mark and the name as one click target: opens the AI Edit page."""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class _DockHeader(QWidget):
    """The title bar, able to go narrower than its full content: under the
    full width the name hides first, then the Get Pro pill drops its words
    (the glyph and the tooltip stay), so any translation fits a 300 px dock."""

    def __init__(self) -> None:
        super().__init__()
        self.title_label: QWidget | None = None
        self.pro_pill = None
        self._pill_text = ""
        self._pill_full_w = 0
        self._fitting = False

    def minimumSizeHint(self):  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        spare = 0
        if self.title_label is not None and not self.title_label.isHidden():
            spare += self.title_label.sizeHint().width()
        if self.pro_pill is not None and not self.pro_pill.isHidden() and self.pro_pill.text():
            spare += max(0, self.pro_pill.sizeHint().width() - self.pro_pill.height())
        return QSize(max(0, hint.width() - spare), hint.height())

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self.fit_to_width()

    def fit_to_width(self) -> None:
        """Pick the header's form from the width alone. Each piece changes
        only when its state flips, so no resize ping-pongs."""
        pill = self.pro_pill
        title = self.title_label
        if pill is None or title is None or self._fitting:
            return
        self._fitting = True
        try:
            if pill.text():
                self._pill_text = pill.text()
                self._pill_full_w = pill.sizeHint().width()
            full = self.layout().sizeHint().width()
            if title.isHidden():
                full += title.sizeHint().width() + _MARK_GAP_PX
            pill_on = not pill.isHidden()
            if pill_on and not pill.text():
                full += max(0, self._pill_full_w - pill.width())
            room = self.width()
            hide_title = full > room
            if hide_title:
                full -= title.sizeHint().width() + _MARK_GAP_PX
            compact_pill = pill_on and full > room
            if title.isHidden() != hide_title:
                title.setVisible(not hide_title)
            if compact_pill and pill.text():
                pill.setText("")
                pill.setFixedWidth(pill.height() + 8)
            elif not compact_pill and not pill.text() and self._pill_text:
                pill.setMinimumWidth(0)
                pill.setMaximumWidth(16777215)
                pill.setText(self._pill_text)
        finally:
            self._fitting = False


class HeaderIconButton(_FooterIconButton):
    """A flat header glyph in the panel ink (AI Agent's header), its tint
    the hover step and the blue selected wash while its menu or dialog is open.

    Keeps ``_FooterIconButton``'s ``set_active`` / ``set_hovered`` so the
    Settings gear can show its dialog is open.
    """

    def __init__(self, parent: QWidget, glyph: str, tooltip: str, accessible: str = ""):
        super().__init__(parent)
        self._glyph = glyph
        self.setStyleSheet(BTN_ICON_QSS)
        self.setAutoRaise(True)
        self.setFixedSize(_HEADER_BTN_PX, _HEADER_BTN_PX)
        self.setIconSize(QSize(_GLYPH_PX, _GLYPH_PX))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setToolTip(tooltip)
        self.setAccessibleName(accessible or tooltip)
        self._paint_glyph()

    def _paint_glyph(self) -> None:
        self.setIcon(icon_for(self, self._glyph, _GLYPH_PX, qcolor(INK), disabled_color=qcolor(INK_3)))


def _title_bar_button(parent: QWidget, glyph: str, tooltip: str) -> QToolButton:
    """The float and close buttons a dock title bar owes: thin glyphs in the
    panel ink, the same button as the header's other icons."""
    return HeaderIconButton(parent, glyph, tooltip)


def build_dock_header(dock: AIEditDockWidget) -> QWidget:
    """Build the title bar and hang its buttons on the dock.

    Sets ``dock._pro_pill``, ``dock._history_btn`` and ``dock._settings_btn``
    (the names the account and plugin code already use).
    """
    header = _DockHeader()
    header.setObjectName("aiEditHeader")
    header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    header.setStyleSheet(_HEADER_QSS)
    header.setFixedHeight(HEADER_HEIGHT_PX)
    row = QHBoxLayout(header)
    row.setContentsMargins(12, 4, 6, 4)
    row.setSpacing(SPACE_TIGHT)

    brand = _BrandTile(header)
    brand.setCursor(Qt.CursorShape.PointingHandCursor)
    brand.setToolTip(get_export_copy("dock.header.brand_tooltip", tr("Open the AI Edit page")))
    brand.setAccessibleName(brand.toolTip())
    brand_row = QHBoxLayout(brand)
    brand_row.setContentsMargins(0, 0, 0, 0)
    brand_row.setSpacing(_MARK_GAP_PX)
    mark = QLabel(brand)
    mark.setFixedSize(logo_size(_MARK_PX))
    mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
    mark.setPixmap(logo_pixmap(mark, _MARK_PX))
    brand_row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
    # The wordmark: the name over a small "by TerraLab" line, stacked with no
    # gap so the pair sits at the row's fixed height beside the mark.
    wordmark = QWidget(brand)
    wordmark_col = QVBoxLayout(wordmark)
    wordmark_col.setContentsMargins(0, 0, 0, 0)
    wordmark_col.setSpacing(0)
    title = QLabel(tr("AI Edit"), wordmark)
    title.setObjectName("aiEditHeaderTitle")
    wordmark_col.addWidget(title)
    byline = QLabel(tr("by TerraLab"), wordmark)
    byline.setObjectName("aiEditHeaderByline")
    wordmark_col.addWidget(byline)
    header.title_label = wordmark
    brand_row.addWidget(wordmark, 0, Qt.AlignmentFlag.AlignVCenter)

    def open_product_page() -> None:
        from ..external_url import open_external

        open_external(DOCK_BRANDING_URL)

    brand.clicked.connect(open_product_page)
    row.addWidget(brand, 0)
    row.addStretch(1)

    # Get Pro: a signed-in Free account only (DockProNudgesMixin._sync_pro_pill).
    from .pro_nudges import build_pro_pill

    dock._pro_pill = build_pro_pill(header)
    dock._pro_pill.clicked.connect(dock._on_pro_pill_clicked)
    header.pro_pill = dock._pro_pill
    row.addWidget(dock._pro_pill, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addSpacing(SPACE_TIGHT)

    # History: the past sessions, AI Agent's clock. Shown once signed in
    # (set_activated), like Settings. The home page no longer repeats it.
    history_label = get_export_copy("dock.dock_header.sessions_link", tr("Sessions"))
    dock._history_btn = HeaderIconButton(header, "clock", history_label)
    dock._history_btn.clicked.connect(dock._on_open_sessions_page)
    dock._history_btn.setVisible(False)
    row.addWidget(dock._history_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    # Settings: shown once signed in (set_activated). It holds the tutorial,
    # shortcuts, contact and report pages, so no help menu sits beside it.
    settings_label = get_export_copy("dock.build_result.settings", tr("Settings"))
    dock._settings_btn = HeaderIconButton(header, "gear", settings_label)
    dock._settings_btn.clicked.connect(dock._on_settings_btn_clicked)
    dock._settings_btn.setVisible(False)
    row.addWidget(dock._settings_btn, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addSpacing(SPACE_TIGHT)

    float_btn = _title_bar_button(
        header,
        "float_window",
        get_export_copy("dock.chrome.float_btn_tooltip", tr("Dock or undock this panel")),
    )
    float_btn.clicked.connect(lambda: dock.setFloating(not dock.isFloating()))
    row.addWidget(float_btn, 0, Qt.AlignmentFlag.AlignVCenter)
    close_btn = _title_bar_button(
        header,
        "close",
        get_export_copy("dock.chrome.close_btn_tooltip", tr("Close this panel")),
    )
    close_btn.clicked.connect(dock.close)
    row.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignVCenter)
    return header
