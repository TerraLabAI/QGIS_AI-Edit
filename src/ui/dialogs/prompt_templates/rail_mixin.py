














from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QObject, QSize, Qt
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy
from ....core.i18n import tr
from ....core.prompts.prompt_presets import get_need_tiles
from ...dock import design_tokens as tk
from ...icons import logo_pixmap, pixmap_for
from .common import (
    _RAIL_GROUP,
    _RAIL_ITEM_COUNT,
    _RAIL_PANEL,
    _STAR_OUTLINE_SVG,
    RAIL_GLYPH_PX,
    RAIL_LABEL_ACTIVE_QSS,
    RAIL_LABEL_QSS,
    RAIL_SUBTITLE_QSS,
    RAIL_TITLE_QSS,
    ElidedLabel,
    _is_alive,
    _rail_item_style,
    _rail_subitem_style,
    set_rail_count,
    tinted_svg_pixmap,
)

_RAIL_WIDTH = 224
_RAIL_LOGO_PX = 26



_RAIL_TARGET_GLYPHS = {
    "popular": "sparkles",
    "need:project": "image",
    "need:classify": "classify",
    "need:render": "pencil",
    "user_favorites": "clock",
}
_RAIL_FALLBACK_GLYPH = "circle"
_RAIL_STARRED_TARGET = "user_starred"



_RAIL_SUB_TEXT_REST = (
    f"color: {tk.INK_2}; font-size: {tk.FONT_BODY}px; "
    "background: transparent; border: none;"
)
_RAIL_SUB_TEXT_ACTIVE = (
    f"color: {tk.INK}; font-size: {tk.FONT_BODY}px; font-weight: 600; "
    "background: transparent; border: none;"
)


class _RailKeys(QObject):





    def __init__(self, rows: list, parent=None, into_page=None):
        super().__init__(parent)
        self._rows = rows
        self._into_page = into_page

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() != QEvent.Type.KeyPress:
            return False
        key = event.key()
        live = [b for b in self._rows if _is_alive(b) and b.isVisible()]
        if obj not in live:
            return False
        if key == Qt.Key.Key_Right and self._into_page is not None:
            return bool(self._into_page())
        if key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            live[0 if key == Qt.Key.Key_Home else -1].setFocus(Qt.FocusReason.TabFocusReason)
            return True
        step = {Qt.Key.Key_Up: -1, Qt.Key.Key_Down: 1}.get(key)
        if step is None:
            return False
        index = live.index(obj) + step
        if 0 <= index < len(live):
            live[index].setFocus(Qt.FocusReason.TabFocusReason)
        return True


class RailMixin:





    def _build_rail_nav(self) -> QWidget:



        self._rail_items: dict[str, QPushButton] = {}
        self._rail_counts: dict[str, QLabel] = {}
        self._rail_labels: dict[str, QLabel] = {}


        self._rail_rows: list[QPushButton] = []
        self._rail_keys = _RailKeys(self._rail_rows, self, self._focus_first_card)
        self._rail_active: str | None = None



        self._rail_sub_hosts: dict[str, QWidget] = {}
        self._rail_sub_items: dict[str, QPushButton] = {}
        self._rail_sub_need: str | None = None
        self._rail_sub_active: str | None = None

        panel = QFrame()
        panel.setObjectName("librail")
        panel.setStyleSheet(_RAIL_PANEL)
        panel.setFixedWidth(_RAIL_WIDTH)
        box = QVBoxLayout(panel)
        box.setContentsMargins(10, 16, 10, 12)
        box.setSpacing(2)

        box.addWidget(self._build_rail_heading(panel))




        box.addWidget(self._make_rail_item(
            "popular",
            get_export_copy("dialogs.rail_mixin.top_picks_label", tr("Top picks")),
            0,
        ))

        cat_rows = self._category_rail_rows()
        if cat_rows:
            self._add_rail_group(box, get_export_copy(
                "dialogs.rail_mixin.group_categories", tr("Categories")))
            for target, label, count in cat_rows:
                box.addWidget(self._make_rail_item(target, label, count))


                host = QWidget()
                sub_box = QVBoxLayout(host)
                sub_box.setContentsMargins(0, 2, 0, 4)
                sub_box.setSpacing(1)
                host.setVisible(False)
                box.addWidget(host)
                self._rail_sub_hosts[target] = host




        self._add_rail_group(box, get_export_copy(
            "dialogs.rail_mixin.group_my_work", tr("My work")))
        box.addWidget(self._make_rail_item(
            "user_favorites",


            get_export_copy("library.work_title", tr("Sessions")),
            len(self._session_entries()),
        ))
        box.addWidget(self._make_rail_item(
            _RAIL_STARRED_TARGET,
            get_export_copy("dialogs.rail_mixin.favorites_label", tr("Favorites")),
            len(self._pinned_entries()),
        ))

        box.addStretch()
        return panel

    @staticmethod
    def _build_rail_heading(parent: QWidget) -> QWidget:


        host = QWidget(parent)
        row = QHBoxLayout(host)
        row.setContentsMargins(6, 0, 4, 10)
        row.setSpacing(10)
        mark = QLabel(host)
        mark.setPixmap(logo_pixmap(mark, _RAIL_LOGO_PX))
        mark.setStyleSheet("background: transparent; border: none;")
        row.addWidget(mark, 0, QtC.AlignVCenter)
        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        title = QLabel("AI Edit", host)
        title.setStyleSheet(RAIL_TITLE_QSS)
        names.addWidget(title)
        subtitle = QLabel(get_export_copy(
            "dialogs.dialog.window_title_library", tr("Library")), host)
        subtitle.setStyleSheet(RAIL_SUBTITLE_QSS)
        names.addWidget(subtitle)
        row.addLayout(names, 1)
        return host

    def _category_rail_rows(self) -> list[tuple[str, str, int]]:



        rows: list[tuple[str, str, int]] = []
        for tile in get_need_tiles(self._server_catalog):
            if tile["preset_count"] == 0:
                continue
            rows.append((
                f'need:{tile["key"]}',
                tile["label"],
                tile["preset_count"],
            ))
        return rows

    @staticmethod
    def _add_rail_group(box: QVBoxLayout, label: str, first: bool = False) -> None:
        lbl = QLabel(label)
        lbl.setStyleSheet(_RAIL_GROUP)
        lbl.setContentsMargins(10, 4 if first else 14, 8, 4)
        box.addWidget(lbl)

    @staticmethod
    def _rail_glyph_label(parent: QWidget, target: str) -> QLabel:
        glyph = QLabel(parent)
        glyph.setFixedSize(QSize(RAIL_GLYPH_PX, RAIL_GLYPH_PX))
        glyph.setStyleSheet("background: transparent; border: none;")
        glyph.setAttribute(QtC.WA_TransparentForMouseEvents)
        if target == _RAIL_STARRED_TARGET:
            glyph.setPixmap(tinted_svg_pixmap(
                _STAR_OUTLINE_SVG, RAIL_GLYPH_PX, tk.qcolor(tk.INK_2)))
        else:
            name = _RAIL_TARGET_GLYPHS.get(target, _RAIL_FALLBACK_GLYPH)
            glyph.setPixmap(pixmap_for(glyph, name, RAIL_GLYPH_PX, tk.qcolor(tk.INK_2)))
        return glyph

    def _make_rail_item(self, target: str, label: str, count: int) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("railitem")
        btn.setCursor(QtC.PointingHandCursor)
        btn.setStyleSheet(_rail_item_style(False))
        btn.setAccessibleName(label)
        btn.clicked.connect(lambda _c=False, t=target: self._rail_navigate(t))



        row = QHBoxLayout(btn)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(10)
        row.addWidget(self._rail_glyph_label(btn, target), 0, QtC.AlignVCenter)


        text = ElidedLabel(label)
        text.setStyleSheet(RAIL_LABEL_QSS)
        text.setAttribute(QtC.WA_TransparentForMouseEvents)
        row.addWidget(text, 1)
        count_lbl = QLabel()
        count_lbl.setStyleSheet(_RAIL_ITEM_COUNT)
        count_lbl.setAttribute(QtC.WA_TransparentForMouseEvents)
        row.addWidget(count_lbl)
        set_rail_count(count_lbl, count)

        self._rail_items[target] = btn
        self._rail_counts[target] = count_lbl
        self._rail_labels[target] = text
        btn.installEventFilter(self._rail_keys)
        self._rail_rows.append(btn)
        return btn

    def _focus_rail(self) -> None:


        row = self._rail_items.get(self._rail_active or "")
        if row is None or not _is_alive(row):
            live = [b for b in self._rail_rows if _is_alive(b) and b.isVisible()]
            row = live[0] if live else None
        if row is not None:
            row.setFocus(Qt.FocusReason.TabFocusReason)

    def _refresh_rail_counts(self) -> None:




        if getattr(self, "_rail_counts", None) is None:
            return
        live = {
            "user_favorites": len(self._session_entries()),
            _RAIL_STARRED_TARGET: len(self._pinned_entries()),
        }
        for target, value in live.items():
            set_rail_count(self._rail_counts.get(target), value)

    def _restyle_rail_item(self, key: str, active: bool) -> None:


        btn = self._rail_items.get(key)
        if btn is None or not _is_alive(btn):
            return
        btn.setStyleSheet(_rail_item_style(active))
        label = self._rail_labels.get(key)
        if label is not None and _is_alive(label):
            label.setStyleSheet(RAIL_LABEL_ACTIVE_QSS if active else RAIL_LABEL_QSS)

    def _set_rail_active(self, target: str | None) -> None:











        if target == self._rail_active:
            return
        previous = self._rail_active
        self._rail_active = target
        if previous is not None:
            self._restyle_rail_item(previous, False)
        if target is not None:
            self._restyle_rail_item(target, True)

    def _show_rail_subfamilies(
        self, need_key: str, entries: list[tuple[str, str, int]]
    ) -> None:




        self._clear_rail_subfamilies()
        host = self._rail_sub_hosts.get(f"need:{need_key}")
        if host is None or not _is_alive(host):
            return
        box = host.layout()
        parent_row = self._rail_items.get(f"need:{need_key}")
        insert_at = (
            self._rail_rows.index(parent_row) + 1
            if parent_row in self._rail_rows else len(self._rail_rows)
        )
        for cat_key, label, count in entries:
            btn = QPushButton()
            btn.setObjectName("railsubitem")
            btn.setCursor(QtC.PointingHandCursor)
            btn.setStyleSheet(_rail_subitem_style(False))
            btn.setAccessibleName(label)
            btn.clicked.connect(
                lambda _c=False, k=need_key, ck=cat_key: self._scroll_hall_to(k, ck)
            )
            row = QHBoxLayout(btn)
            row.setContentsMargins(36, 5, 10, 5)
            row.setSpacing(8)


            text = ElidedLabel(label)
            text.setStyleSheet(_RAIL_SUB_TEXT_REST)
            text.setAttribute(QtC.WA_TransparentForMouseEvents)
            row.addWidget(text, 1)
            count_lbl = QLabel()
            count_lbl.setStyleSheet(_RAIL_ITEM_COUNT)
            count_lbl.setAttribute(QtC.WA_TransparentForMouseEvents)
            row.addWidget(count_lbl)
            set_rail_count(count_lbl, count)
            box.addWidget(btn)
            btn.installEventFilter(self._rail_keys)
            self._rail_rows.insert(insert_at, btn)
            insert_at += 1
            self._rail_sub_items[cat_key] = (btn, text)
        self._rail_sub_need = need_key
        host.setVisible(True)

    def _clear_rail_subfamilies(self) -> None:





        if self._rail_sub_need is None and not self._rail_sub_items:
            return
        self._rail_sub_need = None
        self._rail_sub_active = None
        subrows = {btn for btn, _text in self._rail_sub_items.values()}
        self._rail_rows[:] = [b for b in self._rail_rows if b not in subrows]
        self._rail_sub_items = {}
        for host in self._rail_sub_hosts.values():
            if not _is_alive(host):
                continue
            box = host.layout()
            while box.count():
                item = box.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            host.setVisible(False)

    def _set_rail_active_subfamily(self, need_key: str, cat_key: str) -> None:



        if need_key != self._rail_sub_need or cat_key == self._rail_sub_active:
            return
        self._rail_sub_active = cat_key
        for key, (btn, text) in self._rail_sub_items.items():
            if not _is_alive(btn):
                continue
            active = key == cat_key
            btn.setStyleSheet(_rail_subitem_style(active))
            text.setStyleSheet(
                _RAIL_SUB_TEXT_ACTIVE if active else _RAIL_SUB_TEXT_REST
            )
