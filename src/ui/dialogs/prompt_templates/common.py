






from __future__ import annotations

import os

try:
    from qgis.PyQt import sip as _sip
except ImportError:  # pragma: no cover
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



_ICONS_DIR = ICONS_DIR
_STAR_OUTLINE_SVG = STAR_OUTLINE_SVG
_STAR_FILLED_SVG = STAR_FILLED_SVG


def paint_search_clear_button(line_edit) -> None:





    size = 14
    for action in line_edit.findChildren(QtC.QAction):
        if action.objectName() == "_q_qlineeditclearaction":
            action.setIcon(icon_for(line_edit, "close", size, tk.qcolor(tk.INK_3)))


def _is_alive(obj) -> bool:

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




_ICON_CACHE: dict = {}


def _icon(path: str) -> QIcon:
    ic = _ICON_CACHE.get(path)
    if ic is None:
        ic = QIcon(path)
        _ICON_CACHE[path] = ic
    return ic


def tinted_svg_pixmap(path: str, px: int, color) -> QPixmap:





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








LIBRARY_DIALOG_QSS = (
    f"QDialog#promptLibrary {{ background: {tk.CANVAS}; }}"
    + tk.TOOLTIP_QSS
)


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


_NEED_HEADER_BTN = (
    "QPushButton { text-align: left; border: none;"
    f" border-radius: {tk.RADIUS_CONTROL}px; padding: 10px 12px 4px 12px;"
    f" font-size: {tk.FONT_BASE}px; font-weight: 600; color: {tk.INK};"
    " background: transparent; }"
    f"QPushButton:hover {{ background: {tk.HOVER}; }}"
)



_SEARCH_BOX = tk.INPUT_QSS + (
    f"QLineEdit {{ min-height: {tk.BTN_PRIMARY_WIDE_PX - 14}px; padding: 6px 12px;"
    f" border-radius: {tk.RADIUS_PILL_WIDE}px; }}"
)



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



_STAR_BTN = tk.BTN_ICON_QSS

_EMPTY_MSG = (
    f"QLabel {{ color: {tk.INK_2}; font-size: {tk.FONT_BODY}px;"
    " background: transparent; border: none; }"
)

_LOAD_MORE_BTN = tk.BTN_GHOST_QSS



_ORIGIN_PILL = (
    f"QLabel {{ background: {tk.FIELD}; border: none;"
    f" border-radius: {tk.CHIP_PX // 2 - 2}px; padding: 1px 8px;"
    f" font-size: {tk.FONT_MICRO}px; font-weight: 600; color: {tk.INK_2}; }}"
)


_NEED_TILE_TITLE = f"QLabel {{ {tk.HEADLINE_QSS} }}"
_NEED_TILE_SUB = (
    f"QLabel {{ color: {tk.INK_2}; font-size: {tk.FONT_BODY}px;"
    " background: transparent; border: none; }"
)


_BACK_BTN_SMALL = (
    "QPushButton { background: transparent; border: 1px solid transparent; padding: 0px;"
    f" border-radius: {tk.RADIUS_CONTROL}px; }}"
    f"QPushButton:hover {{ background: {tk.HOVER}; }}"
    f"QPushButton:pressed {{ background: {tk.HOVER_ON}; }}"
    f"QPushButton:focus {{ border-color: {tk.ACCENT_BORDER}; }}"
)


_LANDING_HEADING = _NEED_TILE_TITLE


_HALL_SECTION_TITLE = (
    f"QLabel {{ color: {tk.INK}; font-size: {tk.FONT_PROSE}px; font-weight: 600;"
    " background: transparent; border: none; }"
)
_HALL_SECTION_COUNT = (
    f"QLabel {{ color: {tk.INK_3}; font-size: {tk.FONT_BODY}px;"
    " background: transparent; border: none; }"
)
_FEED_SUBTITLE = _NEED_TILE_SUB








_RAIL_FILL = tk.FIELD if tk.DARK else tk.PAGE
_RAIL_PANEL = (
    f"QFrame#librail {{ border: none; border-right: 1px solid {tk.LINE};"
    f" background: {_RAIL_FILL}; }}"
    "QFrame#librail QLabel { background: transparent; border: none; }"
)

_RAIL_GROUP = (
    f"QLabel {{ color: {tk.INK_3}; font-size: {tk.FONT_HINT}px; font-weight: 600;"
    " background: transparent; border: none; }"
)

_RAIL_ITEM_COUNT = (
    f"QLabel {{ color: {tk.INK_3}; font-size: {tk.FONT_BODY}px;"
    " background: transparent; border: none; }"
)
RAIL_LABEL_QSS = (
    f"color: {tk.INK}; font-size: {tk.FONT_BASE}px; background: transparent; border: none;"
)


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



    del active
    return (
        "QPushButton#railsubitem { text-align: left; border: 1px solid transparent;"
        f" border-radius: {tk.RADIUS_CONTROL}px; padding: 0px; min-height: {tk.ROW_PX}px;"
        f" font-size: {tk.FONT_BODY}px; color: {tk.INK_2}; background: transparent; }}"
        f"QPushButton#railsubitem:hover {{ background: {tk.HOVER}; }}"
        f"QPushButton#railsubitem:focus {{ border-color: {tk.ACCENT_BORDER}; }}"
    )


def _rail_item_style(active: bool) -> str:



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


    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtC.FrameNoFrame)
    scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
    scroll.setStyleSheet(tk.SCROLL_AREA_QSS)
    viewport = scroll.viewport()
    if viewport is not None:
        viewport.setAutoFillBackground(False)
        viewport.setStyleSheet("background: transparent;")






_TAB_ORDER = [
    "user_favorites",
    "recent",
    "__separator__",
    "favorites",
    *_CATEGORY_ORDER,
]


_TABS_WITH_COUNT = {"recent", "user_favorites"}



_NEED_COLLAPSED_SETTING = "AIEdit/library_need_collapsed_{key}"




_GALLERY_PAGE_SIZE = 9


def _gallery_batch_size() -> int:

    return get_export_dial("library.gallery_page_size", _GALLERY_PAGE_SIZE)




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





_CARD_TITLE_H = 36



_CARD_PROMPT_CHARS = 92


def _card_prompt_chars() -> int:

    return get_export_dial("dialogs.common.card_prompt_chars", _CARD_PROMPT_CHARS)


def _truncate(text: str, n: int | None = None) -> str:
    limit = n if n is not None else get_export_dial("dialogs.common.max_title_chars", _MAX_TITLE_CHARS)
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _preset_matches(preset: dict, query: str) -> bool:



    haystack = (
        f'{preset.get("label", "")} {preset.get("prompt", "")} '
        f'{preset.get("source_category", "") or ""}'
    ).lower()
    words = query.lower().split()
    return bool(words) and all(word in haystack for word in words)


_svg_url = svg_url


def _sidebar_icon_html(cat_key: str) -> str:


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




    pill = QLabel(
        get_export_copy("dialogs.common.template_pill", tr("Template")) if has_template
        else get_export_copy("dialogs.common.your_prompt_pill", tr("Your prompt")),
        parent,
    )
    pill.setStyleSheet(_ORIGIN_PILL)

    pill.setAttribute(QtC.WA_TransparentForMouseEvents)
    return pill


def _card_prompt(prompt: str, n: int = 66) -> str:


    flat = " ".join((prompt or "").split())
    if len(flat) <= n:
        return flat
    cut = flat[:n].rsplit(" ", 1)[0] or flat[:n]
    return cut.rstrip(" ,.;:-") + "…"


def set_rail_count(label: QLabel, count: int) -> None:


    if label is None or not _is_alive(label):
        return
    label.setText(str(count))
    label.setVisible(count > 0)


class ElidedLabel(QLabel):




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

    def minimumSizeHint(self):  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        width = self.fontMetrics().horizontalAdvance(self._full_text) + 2
        return QSize(width, hint.height())

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):  # noqa: N802


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






    def __init__(self, text: str = "", lines: int = 2, parent=None):
        super().__init__(parent)
        self._full_text = " ".join((text or "").split())
        self._lines = max(1, int(lines))
        self.setTextFormat(QtC.PlainText)
        self.setWordWrap(True)
        self.setAlignment(QtC.AlignLeft | QtC.AlignTop)
        self._apply_height()
        self.setText(self._full_text)

    def minimumSizeHint(self):  # noqa: N802
        return QSize(0, self._block_height())

    def sizeHint(self):  # noqa: N802
        return QSize(super().sizeHint().width(), self._block_height())

    def _block_height(self) -> int:
        return self.fontMetrics().lineSpacing() * self._lines + 2

    def _apply_height(self) -> None:
        self.setFixedHeight(self._block_height())

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._apply_height()
            self._clamp()

    def resizeEvent(self, event):  # noqa: N802
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




    served = str(preset.get("description") or "").strip()
    if served:
        return " ".join(served.split())
    prompt = " ".join(str(preset.get("prompt") or "").split())
    for mark in (". ", ": ", "; "):
        cut = prompt.find(mark)
        if cut > 0:
            prompt = prompt[:cut + (1 if mark == ". " else 0)]
            break


    label = " ".join(str(preset.get("label") or "").split()).rstrip(".…").lower()
    own = not preset.get("source_category")
    if own and label and prompt.lower().startswith(label[:40]):
        return ""
    return prompt
