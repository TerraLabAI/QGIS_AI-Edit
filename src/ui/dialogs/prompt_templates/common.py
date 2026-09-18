"""Shared constants, styles, and small helpers for the Prompt Library.

Every colour, radius and size here comes from ``src/ui/dock/design_tokens.py``
(the AI Agent line in AI Edit's colours): cards are a surface on a hairline
with 10 px corners, the rail is AI Agent's settings rail (plain rows, 16 px
glyphs, the selected row a soft blue wash), text uses the three inks.
"""
from __future__ import annotations

import os

try:  # SIP comes packaged with both PyQt5 and PyQt6 - used to detect dead C++ objects.
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover - defensive only
    _sip = None

from qgis.PyQt.QtCore import QEvent, QSize, Qt
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPixmap
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QWidget

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy, get_export_dial
from ....core.i18n import tr
from ....core.prompts.prompt_presets import _CATEGORY_ORDER
from ...dock import design_tokens as tk
from ...dock.style import (
    ICONS_DIR,
    STAR_FILLED_SVG,
    STAR_OUTLINE_SVG,
    svg_url,
)
from ...icons import icon_for, pixmap_for

# Private names are listed too: other modules import them from here.
__all__ = [
    "_BACK_BTN_SMALL",
    "paint_search_clear_button",
    "_build_origin_pill",
    "_build_use_hint",
    "_CARD_HOVER",
    "_CARD_NORMAL",
    "_card_prompt",
    "_CARD_PROMPT_CHARS",
    "_CARD_TITLE_H",
    "_EMPTY_MSG",
    "_FEED_SUBTITLE",
    "_gallery_batch_size",
    "_GALLERY_PAGE_SIZE",
    "_HALL_SECTION_COUNT",
    "_HALL_SECTION_TITLE",
    "_HISTORY_SVG",
    "_icon",
    "_ICON_CACHE",
    "_ICONS_DIR",
    "_is_alive",
    "_LANDING_HEADING",
    "_LOAD_MORE_BTN",
    "_MAX_TITLE_CHARS",
    "_NEED_COLLAPSED_SETTING",
    "_NEED_HEADER_BTN",
    "_NEED_TILE_SUB",
    "_NEED_TILE_TITLE",
    "_ORIGIN_PILL",
    "_preset_matches",
    "_RAIL_GROUP",
    "_RAIL_ITEM_COUNT",
    "_rail_item_style",
    "_RAIL_PANEL",
    "_rail_subitem_style",
    "_SEARCH_BOX",
    "_set_use_hint",
    "_SIDEBAR_GLYPHS",
    "_sidebar_icon_html",
    "_SIDEBAR_ITEM",
    "_SIDEBAR_ITEM_ACTIVE",
    "_sip",
    "_STAR_BTN",
    "_STAR_FILLED_SVG",
    "_STAR_OUTLINE_SVG",
    "_svg_url",
    "_tab_label",
    "_TAB_ORDER",
    "_TABS_WITH_COUNT",
    "_TROPHY_SVG",
    "_truncate",
    "_USE_HINT_HOVER",
    "_USE_HINT_REST",
    "CARD_HINT_QSS",
    "card_description",
    "ClampedLabel",
    "ElidedLabel",
    "RAIL_LABEL_ACTIVE_QSS",
    "set_rail_count",
    "CARD_PROMPT_QSS",
    "CARD_TITLE_QSS",
    "LIBRARY_DIALOG_QSS",
    "RAIL_GLYPH_PX",
    "RAIL_LABEL_QSS",
    "RAIL_SUBTITLE_QSS",
    "RAIL_TITLE_QSS",
    "LibraryUseHint",
    "style_library_scroll",
    "tinted_svg_pixmap",
]

# The dock's style module owns the icon paths. Re-exported under the local names
# this package and its facade already import.
_ICONS_DIR = ICONS_DIR
_STAR_OUTLINE_SVG = STAR_OUTLINE_SVG
_STAR_FILLED_SVG = STAR_FILLED_SVG


def paint_search_clear_button(line_edit) -> None:
    """Draw a search field's clear button with the close glyph in the quiet ink.

    Qt's own button is a platform disc (a white blob on the dark theme). Qt
    names its clear action ``_q_qlineeditclearaction``; when a Qt build names
    it otherwise, the platform button simply stays."""
    size = 14
    for action in line_edit.findChildren(QtC.QAction):
        if action.objectName() == "_q_qlineeditclearaction":
            action.setIcon(icon_for(line_edit, "close", size, tk.qcolor(tk.INK_3)))


def _is_alive(obj) -> bool:
    """True when the underlying Qt C++ object is still alive."""
    if obj is None:
        return False
    if _sip is None:
        return True
    try:
        return not _sip.isdeleted(obj)
    except (TypeError, RuntimeError):
        return False


_HISTORY_SVG = os.path.join(_ICONS_DIR, "history.svg")
_TROPHY_SVG = os.path.join(_ICONS_DIR, "trophy.svg")

# QIcon parses the SVG on construction; memoize so a 50-card gallery doesn't
# re-read the same few files 150 times. QIcon is implicitly shared, so handing
# the same instance to many buttons is safe.
_ICON_CACHE: dict = {}


def _icon(path: str) -> QIcon:
    ic = _ICON_CACHE.get(path)
    if ic is None:
        ic = QIcon(path)
        _ICON_CACHE[path] = ic
    return ic


def tinted_svg_pixmap(path: str, px: int, color) -> QPixmap:
    """An SVG icon file drawn in one ink, like the painted glyphs of
    src/ui/icons.py. The star exists only as a file, and its own grey sat
    apart from the rail's other glyphs and the empty-state tiles."""
    # QIcon hands back a pixmap at the screen's pixel ratio; the copy keeps
    # that ratio so it paints at ``px`` logical pixels, centred in its label.
    pixmap = _icon(path).pixmap(QSize(px, px))
    tinted = QPixmap(pixmap.size())
    tinted.setDevicePixelRatio(pixmap.devicePixelRatio())
    tinted.fill(QColor(0, 0, 0, 0))
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(0, 0, tinted.width(), tinted.height(), color)
    painter.end()
    return tinted


# ---------------------------------------------------------------------------
# QSS
# ---------------------------------------------------------------------------

# The dialog frame: the content sits on the canvas step so the surface cards
# read above it, like AI Agent's settings and library windows.
LIBRARY_DIALOG_QSS = (
    f"QDialog#promptLibrary {{ background: {tk.CANVAS}; }}"
    + tk.TOOLTIP_QSS
)

# Legacy tab buttons (the pre-rail sidebar); still restyled by _switch_to_tab.
_SIDEBAR_ITEM = (
    "QPushButton { text-align: left; border: none;"
    f" border-radius: {tk.RADIUS_CONTROL}px; padding: 8px 10px;"
    f" font-size: {tk.FONT_BASE}px; color: {tk.INK}; background: transparent; }}"
    f"QPushButton:hover {{ background: {tk.HOVER}; }}"
)

_SIDEBAR_ITEM_ACTIVE = (
    "QPushButton { text-align: left; border: none;"
    f" border-radius: {tk.RADIUS_CONTROL}px; padding: 8px 10px;"
    f" font-size: {tk.FONT_BASE}px; color: {tk.INK}; background: {tk.ACCENT_TINT_ON}; }}"
)

# Need-group headers of the legacy sidebar (fold rows).
_NEED_HEADER_BTN = (
    "QPushButton { text-align: left; border: none;"
    f" border-radius: {tk.RADIUS_CONTROL}px; padding: 10px 12px 4px 12px;"
    f" font-size: {tk.FONT_BASE}px; font-weight: 600; color: {tk.INK};"
    " background: transparent; }"
    f"QPushButton:hover {{ background: {tk.HOVER}; }}"
)

# The library's one search field: AI Agent's input as a 36 px pill (ChatGPT's
# search field), a magnifier glyph added as a leading action by the caller.
_SEARCH_BOX = tk.INPUT_QSS + (
    f"QLineEdit {{ min-height: {tk.BTN_PRIMARY_WIDE_PX - 14}px; padding: 6px 12px;"
    f" border-radius: {tk.RADIUS_PILL_WIDE}px; }}"
)

# A card: the surface on a hairline, 10 px corners. Hover takes the strong
# hairline and the hover step (the footer is the part that shows it).
_CARD_NORMAL = (
    f"QFrame#card {{ border: 1px solid {tk.LINE};"
    f" border-radius: {tk.RADIUS_CARD}px; background: {tk.SURFACE}; }}"
    "QFrame#card QLabel { background: transparent; border: none; }"
)

_CARD_HOVER = (
    f"QFrame#card {{ border: 1px solid {tk.LINE_STRONG};"
    f" border-radius: {tk.RADIUS_CARD}px; background: {tk.HOVER}; }}"
    "QFrame#card QLabel { background: transparent; border: none; }"
)

# Card text roles: a 13 px semibold title and an 11 px secondary line.
CARD_TITLE_QSS = (
    f"color: {tk.INK}; font-size: {tk.FONT_BASE}px; font-weight: 600;"
    " background: transparent; border: none;"
)
CARD_PROMPT_QSS = (
    f"color: {tk.INK}; font-size: {tk.FONT_BODY}px; font-weight: 400;"
    " background: transparent; border: none;"
)
CARD_HINT_QSS = (
    f"color: {tk.INK_2}; font-size: {tk.FONT_HINT}px;"
    " background: transparent; border: none;"
)

# The click affordance at the right of a card footer: a quiet chevron at rest,
# "Open" plus the chevron in the link blue under the pointer or the keyboard
# focus (a click opens the preview window, whose own button uses the prompt).
_USE_HINT_REST = (
    f"QLabel {{ color: {tk.INK_3}; font-size: {tk.FONT_BODY}px; font-weight: 600;"
    " background: transparent; border: none; }"
)
_USE_HINT_HOVER = (
    f"QLabel {{ color: {tk.LINK_INK}; font-size: {tk.FONT_BODY}px; font-weight: 600;"
    " background: transparent; border: none; }"
)
_USE_HINT_GLYPH_PX = 14


class LibraryUseHint(QWidget):
    """The card's click affordance: a word that shows on hover (or on keyboard
    focus) and a chevron."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(QtC.WA_TransparentForMouseEvents)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self._word = QLabel(get_export_copy("dialogs.common.open_hint", tr("Open")), self)
        self._word.setStyleSheet(_USE_HINT_HOVER)
        self._word.setVisible(False)
        row.addWidget(self._word)
        self._glyph = QLabel(self)
        self._glyph.setFixedSize(QSize(_USE_HINT_GLYPH_PX, _USE_HINT_GLYPH_PX))
        self._glyph.setStyleSheet("background: transparent; border: none;")
        row.addWidget(self._glyph)
        self.set_hovered(False)

    def set_hovered(self, hovered: bool) -> None:
        self._word.setVisible(hovered)
        ink = tk.qcolor(tk.LINK_INK if hovered else tk.INK_3)
        self._glyph.setPixmap(pixmap_for(self, "chevron_right", _USE_HINT_GLYPH_PX, ink))


def _build_use_hint(parent) -> LibraryUseHint:
    return LibraryUseHint(parent)


def _set_use_hint(hint, hovered: bool) -> None:
    if hint is None or not _is_alive(hint):
        return
    setter = getattr(hint, "set_hovered", None)
    if setter is not None:
        setter(hovered)


# A small icon button (a star toggle): AI Agent's flat icon button.
_STAR_BTN = tk.BTN_ICON_QSS

_EMPTY_MSG = (
    f"QLabel {{ color: {tk.INK_2}; font-size: {tk.FONT_BODY}px;"
    " background: transparent; border: none; }"
)

_LOAD_MORE_BTN = tk.BTN_GHOST_QSS

# Small pill marking a starred entry's origin (template vs the user's own
# prompt): the field step, a chip, the secondary ink.
_ORIGIN_PILL = (
    f"QLabel {{ background: {tk.FIELD}; border: none;"
    f" border-radius: {tk.CHIP_PX // 2 - 2}px; padding: 1px 8px;"
    f" font-size: {tk.FONT_MICRO}px; font-weight: 600; color: {tk.INK_2}; }}"
)

# Page header: a headline title and one line of secondary text.
_NEED_TILE_TITLE = f"QLabel {{ {tk.HEADLINE_QSS} }}"
_NEED_TILE_SUB = (
    f"QLabel {{ color: {tk.INK_2}; font-size: {tk.FONT_BODY}px;"
    " background: transparent; border: none; }"
)

# The back button beside a page title: AI Agent's flat icon button.
_BACK_BTN_SMALL = (
    "QPushButton { background: transparent; border: 1px solid transparent; padding: 0px;"
    f" border-radius: {tk.RADIUS_CONTROL}px; }}"
    f"QPushButton:hover {{ background: {tk.HOVER}; }}"
    f"QPushButton:pressed {{ background: {tk.HOVER_ON}; }}"
    f"QPushButton:focus {{ border-color: {tk.ACCENT_BORDER}; }}"
)

# Landing heading and its one-line explainer.
_LANDING_HEADING = _NEED_TILE_TITLE
# Section header in a family page: title and a muted count, AI Agent's
# "Popular 24" row.
_HALL_SECTION_TITLE = (
    f"QLabel {{ color: {tk.INK}; font-size: {tk.FONT_PROSE}px; font-weight: 600;"
    " background: transparent; border: none; }"
)
_HALL_SECTION_COUNT = (
    f"QLabel {{ color: {tk.INK_3}; font-size: {tk.FONT_BODY}px;"
    " background: transparent; border: none; }"
)
_FEED_SUBTITLE = _NEED_TILE_SUB

# ---------------------------------------------------------------------------
# Persistent navigation rail (left of the page stack): AI Agent's settings
# rail. The field step behind it, a hairline on its right, plain rows with a
# 16 px glyph; the selected row is a blue tinted block, no coloured bar.
# ---------------------------------------------------------------------------
# On the light theme the field step equals the canvas, so the rail takes the
# page step there to stay a distinct column.
_RAIL_FILL = tk.FIELD if tk.DARK else tk.PAGE
_RAIL_PANEL = (
    f"QFrame#librail {{ border: none; border-right: 1px solid {tk.LINE};"
    f" background: {_RAIL_FILL}; }}"
    "QFrame#librail QLabel { background: transparent; border: none; }"
)
# Group label above a cluster of rail items: small, semibold, the third ink.
_RAIL_GROUP = (
    f"QLabel {{ color: {tk.INK_3}; font-size: {tk.FONT_HINT}px; font-weight: 600;"
    " background: transparent; border: none; }"
)
# Muted count on the right of a rail item.
_RAIL_ITEM_COUNT = (
    f"QLabel {{ color: {tk.INK_3}; font-size: {tk.FONT_BODY}px;"
    " background: transparent; border: none; }"
)
RAIL_LABEL_QSS = (
    f"color: {tk.INK}; font-size: {tk.FONT_BASE}px; background: transparent; border: none;"
)
# The selected row's label: the same ink, semibold (ChatGPT's sidebar marks the
# open page by weight on a soft wash, never by a filled slab).
RAIL_LABEL_ACTIVE_QSS = RAIL_LABEL_QSS + " font-weight: 600;"
RAIL_TITLE_QSS = (
    f"color: {tk.INK}; font-size: {tk.FONT_BASE}px; font-weight: 600;"
    " background: transparent; border: none;"
)
RAIL_SUBTITLE_QSS = (
    f"color: {tk.INK_2}; font-size: {tk.FONT_HINT}px; background: transparent; border: none;"
)
RAIL_GLYPH_PX = 16


def _rail_subitem_style(active: bool) -> str:
    """QSS for a subfamily row nested under the open category: indented, a
    step smaller, always flat. The current one is marked by its label alone
    (ink, semibold), so the rail never shows two lit rows at once."""
    del active  # the label carries the state; the row stays flat
    return (
        "QPushButton#railsubitem { text-align: left; border: 1px solid transparent;"
        f" border-radius: {tk.RADIUS_CONTROL}px; padding: 0px; min-height: {tk.ROW_PX}px;"
        f" font-size: {tk.FONT_BODY}px; color: {tk.INK_2}; background: transparent; }}"
        f"QPushButton#railsubitem:hover {{ background: {tk.HOVER}; }}"
        f"QPushButton#railsubitem:focus {{ border-color: {tk.ACCENT_BORDER}; }}"
    )


def _rail_item_style(active: bool) -> str:
    """QSS for one rail row. At rest: flat with a hover step. Selected: the
    soft blue wash (ACCENT_TINT) under a semibold label, a step deeper under
    the pointer; never the old filled slab."""
    if active:
        return (
            "QPushButton#railitem { text-align: left; border: 1px solid transparent;"
            f" border-radius: {tk.RADIUS_CONTROL}px; min-height: {tk.BTN_PX}px;"
            f" padding: 0px; background: {tk.ACCENT_TINT}; }}"
            f"QPushButton#railitem:hover {{ background: {tk.ACCENT_TINT_ON}; }}"
            f"QPushButton#railitem:focus {{ border-color: {tk.ACCENT_BORDER}; }}"
        )
    return (
        "QPushButton#railitem { text-align: left; border: 1px solid transparent;"
        f" border-radius: {tk.RADIUS_CONTROL}px; min-height: {tk.BTN_PX}px;"
        " padding: 0px; background: transparent; }"
        f"QPushButton#railitem:hover {{ background: {tk.HOVER}; }}"
        f"QPushButton#railitem:pressed {{ background: {tk.HOVER_ON}; }}"
        f"QPushButton#railitem:focus {{ border-color: {tk.ACCENT_BORDER}; }}"
    )


def style_library_scroll(scroll) -> None:
    """A library scroll area: no frame, no horizontal bar, AI Agent's slim
    vertical bar, and a transparent page so the canvas step shows through."""
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtC.FrameNoFrame)
    scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
    scroll.setStyleSheet(tk.SCROLL_AREA_QSS)
    viewport = scroll.viewport()
    if viewport is not None:
        viewport.setAutoFillBackground(False)
        viewport.setStyleSheet("background: transparent;")


# Sidebar tab order. Themed tabs are sourced from `_CATEGORY_ORDER` so the
# data facade and the sidebar can't drift; the dialog only owns the synthetic
# wrapper (Favorites, Recent, separator, Top Picks). "__separator__" inserts
# a visual divider. The user's own lists lead; the curated catalog follows.
_TAB_ORDER = [
    "user_favorites",   # Favorites (personal)
    "recent",           # Recent (personal)
    "__separator__",
    "favorites",        # Top Picks (curated)
    *_CATEGORY_ORDER,   # 13 themed métiers - first few shown, rest collapsed
]

# Tabs whose count is shown as "(N)" next to the label.
_TABS_WITH_COUNT = {"recent", "user_favorites"}

# QSettings key remembering a need group's folded state across sessions.
# Groups start expanded; only an explicit user fold is persisted.
_NEED_COLLAPSED_SETTING = "AIEdit/library_need_collapsed_{key}"

# Recent/Favorites galleries show this many generations first; the rest reveal
# in batches behind a "Show more" button so the page stays light. 9 = a full
# 3x3 grid per batch.
_GALLERY_PAGE_SIZE = 9


def _gallery_batch_size() -> int:
    """Cards a Recent/Favorites gallery reveals per batch, read at use time."""
    return get_export_dial("library.gallery_page_size", _GALLERY_PAGE_SIZE)


# Category glyphs from src/ui/icons.py, in the secondary ink. Mirrors
# `prompt_presets._CATEGORY_META` so every category in _TAB_ORDER has one.
_SIDEBAR_GLYPHS = {
    "user_favorites": ("clock", tk.INK_2),
    "cartography": ("layout", tk.INK_2),
    "landcover": ("classify", tk.INK_2),
    "segment": ("polygon", tk.INK_2),
    "climate": ("globe", tk.INK_2),
    "urban": ("home", tk.INK_2),
    "energy": ("bolt", tk.INK_2),
    "cleanup": ("sparkles", tk.INK_2),
    "presentation": ("image", tk.INK_2),
    "forestry": ("terrain", tk.INK_2),
    "agriculture": ("hexgrid", tk.INK_2),
    "archaeology": ("pin", tk.INK_2),
    "geology": ("contour", tk.INK_2),
    "hydrology": ("route", tk.INK_2),
}

_MAX_TITLE_CHARS = 80

# Fixed height of a Recent/Favorites card's title/prompt block (~2 text lines).
# Those grids mix 1-line template names with 2-line custom prompts, so reserving
# two lines on every card keeps a row from having a tall card next to short ones.
# Template grids (Top Picks, themed) stay compact 1-line - they never wrap.
_CARD_TITLE_H = 36

# Char budget for a wrapped prompt/title on a 2-line Recent/Favorites card. Sized
# to fill both lines of a ~320px card before the word-boundary ellipsis kicks in.
_CARD_PROMPT_CHARS = 92


def _card_prompt_chars() -> int:
    """Char budget for a card's wrapped prompt/title, read at use time."""
    return get_export_dial("dialogs.common.card_prompt_chars", _CARD_PROMPT_CHARS)


def _truncate(text: str, n: int | None = None) -> str:
    limit = n if n is not None else get_export_dial("dialogs.common.max_title_chars", _MAX_TITLE_CHARS)
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _preset_matches(preset: dict, query: str) -> bool:
    """Case-insensitive match across label, prompt and category key. Every
    word of the query must appear, in any order: "solar roof" finds "Add
    rooftop solar panels", which a plain substring test missed."""
    haystack = (
        f'{preset.get("label", "")} {preset.get("prompt", "")} '
        f'{preset.get("source_category", "") or ""}'
    ).lower()
    words = query.lower().split()
    return bool(words) and all(word in haystack for word in words)


_svg_url = svg_url  # the dock's style module owns this helper


def _sidebar_icon_html(cat_key: str) -> str:
    """Legacy sidebar icon markup: the two SVG tabs keep their image, the
    themed ones draw no text glyph any more (glyphs are painted pixmaps)."""
    if cat_key == "recent":
        return (
            f'<img src="{_svg_url(_HISTORY_SVG)}" width="14" height="14" '
            'style="vertical-align: middle;" />'
        )
    if cat_key == "favorites":
        return (
            f'<img src="{_svg_url(_TROPHY_SVG)}" width="15" height="15" '
            'style="vertical-align: middle;" />'
        )
    return ""


def _tab_label(cat_key: str, label: str, count: int | None = None) -> str:
    """Sidebar label HTML - name with optional muted count."""
    if count is not None and count > 0:
        count_html = (
            f' <span style="color:{tk.INK_3}; font-size:{tk.FONT_HINT}px;">'
            f'{count}</span>'
        )
    else:
        count_html = ""
    return (
        f'<span style="font-size:{tk.FONT_BASE}px; color:{tk.INK};">{label}</span>'
        f'{count_html}'
    )


def _build_origin_pill(parent, has_template: bool) -> QLabel:
    """Plain text pill marking a starred entry's origin so curated TerraLab
    templates and the user's own saved prompts are told apart at a glance
    (#128). Text only, no color coding - the box + word carry the meaning.
    """
    pill = QLabel(
        get_export_copy("dialogs.common.template_pill", tr("Template")) if has_template
        else get_export_copy("dialogs.common.your_prompt_pill", tr("Your prompt")),
        parent,
    )
    pill.setStyleSheet(_ORIGIN_PILL)
    # Let clicks fall through to the card so the pill never blocks selection.
    pill.setAttribute(QtC.WA_TransparentForMouseEvents)
    return pill


def _card_prompt(prompt: str, n: int = 66) -> str:
    """Flatten whitespace and truncate at a word boundary so the prompt fits
    ~2 lines on a card without an ugly mid-word cut."""
    flat = " ".join((prompt or "").split())
    if len(flat) <= n:
        return flat
    cut = flat[:n].rsplit(" ", 1)[0] or flat[:n]
    return cut.rstrip(" ,.;:-") + "…"


def set_rail_count(label: QLabel, count: int) -> None:
    """A rail row's muted count, hidden at zero: an empty page says so itself,
    a "0" beside the row was noise (ChatGPT's sidebar shows none)."""
    if label is None or not _is_alive(label):
        return
    label.setText(str(count))
    label.setVisible(count > 0)


class ElidedLabel(QLabel):
    """A one-line label that ends in an ellipsis when it runs out of room, the
    full text moving to its tooltip. Its minimum width is zero so a longer
    language shrinks it instead of widening the row."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setTextFormat(QtC.PlainText)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full_text = " ".join((text or "").split())
        self._elide()

    def full_text(self) -> str:
        return self._full_text

    def minimumSizeHint(self):  # noqa: N802 - Qt signature
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self):  # noqa: N802 - Qt signature
        hint = super().sizeHint()
        width = self.fontMetrics().horizontalAdvance(self._full_text) + 2
        return QSize(width, hint.height())

    def resizeEvent(self, event):  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):  # noqa: N802 - Qt signature
        # A stylesheet swap (the selected rail row turns semibold) changes the
        # font, so the cut has to be measured again.
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._elide()

    def _elide(self) -> None:
        width = self.width()
        full = self._full_text
        shown = full if width <= 0 else self.fontMetrics().elidedText(
            full, Qt.TextElideMode.ElideRight, width)
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(full if shown != full else "")


class ClampedLabel(QLabel):
    """Wrapped text held to ``lines`` lines: the last one ends in an ellipsis
    and the full text moves to the tooltip. Measured with the label's own font,
    so a narrow card never shows a clipped third line the way the old fixed
    character budget did. The height is always ``lines`` lines, so cards in one
    row stay the same height whether their text takes one line or two."""

    def __init__(self, text: str = "", lines: int = 2, parent=None):
        super().__init__(parent)
        self._full_text = " ".join((text or "").split())
        self._lines = max(1, int(lines))
        self.setTextFormat(QtC.PlainText)
        self.setWordWrap(True)
        self.setAlignment(QtC.AlignLeft | QtC.AlignTop)
        self._apply_height()
        self.setText(self._full_text)

    def minimumSizeHint(self):  # noqa: N802 - Qt signature
        return QSize(0, self._block_height())

    def sizeHint(self):  # noqa: N802 - Qt signature
        return QSize(super().sizeHint().width(), self._block_height())

    def _block_height(self) -> int:
        return self.fontMetrics().lineSpacing() * self._lines + 2

    def _apply_height(self) -> None:
        self.setFixedHeight(self._block_height())

    def changeEvent(self, event):  # noqa: N802 - Qt signature
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._apply_height()
            self._clamp()

    def resizeEvent(self, event):  # noqa: N802 - Qt signature
        super().resizeEvent(event)
        self._clamp()

    def _clamp(self) -> None:
        width = self.width()
        full = self._full_text
        if width <= 0 or not full:
            return
        metrics = self.fontMetrics()
        words = full.split(" ")
        lines: list[str] = []
        current = ""
        index = 0
        while index < len(words) and len(lines) < self._lines - 1:
            candidate = f"{current} {words[index]}".strip()
            if metrics.horizontalAdvance(candidate) <= width or not current:
                current = candidate
                index += 1
                continue
            lines.append(current)
            current = ""
        rest = " ".join(([current] if current else []) + words[index:]).strip()
        if rest:
            lines.append(metrics.elidedText(rest, Qt.TextElideMode.ElideRight, width))
        shown = "\n".join(lines)
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(full if shown.replace("\n", " ") != full else "")


def card_description(preset: dict) -> str:
    """The one-line description under a template card's title. A served
    ``description`` wins; otherwise the prompt's first sentence, which says
    what the template does ("Output a flat land use map..."). Never the
    category: the page or section already names it."""
    served = str(preset.get("description") or "").strip()
    if served:
        return " ".join(served.split())
    prompt = " ".join(str(preset.get("prompt") or "").split())
    for mark in (". ", ": ", "; "):
        cut = prompt.find(mark)
        if cut > 0:
            prompt = prompt[:cut + (1 if mark == ". " else 0)]
            break
    # A starred prompt of the user's own is titled by its opening words, so
    # the first sentence would only repeat the title under it.
    label = " ".join(str(preset.get("label") or "").split()).rstrip(".…").lower()
    own = not preset.get("source_category")
    if own and label and prompt.lower().startswith(label[:40]):
        return ""
    return prompt
