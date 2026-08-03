"""Persistent navigation rail for the prompt library.

Left of the page stack, always visible. It NAVIGATES between the library's
views (Featured, one item per category, Sessions) and marks exactly one as
active ("you are here"), unlike a filter chip that only narrows a list.

Three headed groups: Featured (the measured Top picks), Categories (one row
per need), and My work (the user's own material: Sessions and Starred).
Rows are deliberately plain - label + muted count, no glyph
column, no per-family colour - so the rail reads as a quiet index, not a
rainbow. Selecting a row calls `_rail_navigate(target)` on the host dialog;
the host owns which stack page each target shows. Mixed into
PromptTemplatesDialog; reuses `get_need_tiles` for the category rows.
"""
from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.i18n import tr
from ....core.prompts.prompt_presets import get_need_tiles
from .common import (
    _RAIL_GROUP,
    _RAIL_ITEM_COUNT,
    _RAIL_PANEL,
    _is_alive,
    _rail_item_style,
    _rail_subitem_style,
)

_RAIL_WIDTH = 210
# Subfamily row text at rest: muted grey, one clear tier below the white
# family label, so family vs subfamily reads at a glance. The active state
# (full text colour, bold) is applied by _set_rail_active_subfamily.
_RAIL_SUB_TEXT_REST = (
    "color: rgba(128,128,128,0.95); font-size: 12px; "
    "background: transparent; border: none;"
)
# Active subfamily text: weight and full text colour mark it, never a hue.
_RAIL_SUB_TEXT_ACTIVE = (
    "color: palette(text); font-size: 12px; font-weight: 700; "
    "background: transparent; border: none;"
)


class RailMixin:
    """Builds and drives the navigation rail. Requires the host to provide
    `_server_catalog`, `_top_pick_presets`, `_session_entries`,
    `_pinned_entries`, `_recent_jobs` and a `_rail_navigate(target)` method
    (wired in the dialog)."""

    def _build_rail_nav(self) -> QWidget:
        # target key -> its QPushButton and count label, so _set_rail_active
        # can restyle every row and _refresh_rail_counts can update the
        # personal/Top-picks counts after a history sync.
        self._rail_items: dict[str, QPushButton] = {}
        self._rail_counts: dict[str, QLabel] = {}
        self._rail_active: str | None = None
        # Per-category container for the subfamily rows shown while that
        # family's page is open (a table of contents inside the rail). Only
        # one is ever populated at a time.
        self._rail_sub_hosts: dict[str, QWidget] = {}
        self._rail_sub_items: dict[str, QPushButton] = {}
        self._rail_sub_need: str | None = None
        self._rail_sub_active: str | None = None

        panel = QFrame()
        panel.setObjectName("librail")
        panel.setStyleSheet(_RAIL_PANEL)
        panel.setFixedWidth(_RAIL_WIDTH)
        box = QVBoxLayout(panel)
        box.setContentsMargins(4, 6, 10, 8)
        box.setSpacing(2)

        self._add_rail_group(box, tr("Featured"), first=True)
        box.addWidget(self._make_rail_item(
            "popular", tr("Top picks"), len(self._top_pick_presets()),
        ))

        cat_rows = self._category_rail_rows()
        if cat_rows:
            self._add_rail_group(box, tr("Categories"))
            for target, label, count in cat_rows:
                box.addWidget(self._make_rail_item(target, label, count))
                # Empty, collapsed host right under the row; filled with this
                # family's subfamilies while its page is open.
                host = QWidget()
                sub_box = QVBoxLayout(host)
                sub_box.setContentsMargins(0, 0, 0, 2)
                sub_box.setSpacing(0)
                host.setVisible(False)
                box.addWidget(host)
                self._rail_sub_hosts[target] = host

        # Third group, the user's own material: Sessions (work sessions) and
        # Starred (the pinned prompts + generations). Same header voice and
        # spacing as Categories so the three rail zones read as peers. The
        # Sessions target keeps its historical "user_favorites" key (anchors
        # and the server copy override on library.work_title point at it);
        # Starred is a new destination, so it gets a new stable key.
        self._add_rail_group(box, tr("My work"))
        box.addWidget(self._make_rail_item(
            "user_favorites", tr("Sessions"), len(self._session_entries()),
        ))
        box.addWidget(self._make_rail_item(
            "user_starred", tr("Starred"), len(self._pinned_entries()),
        ))

        box.addStretch()
        return panel

    def _category_rail_rows(self) -> list[tuple[str, str, int]]:
        """One (target, label, count) per non-empty need, in catalog order.
        Empty needs are skipped so the rail never shows a dead row (same rule
        the old tiles used)."""
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
        lbl.setContentsMargins(8, 2 if first else 12, 8, 4)
        box.addWidget(lbl)

    def _make_rail_item(self, target: str, label: str, count: int) -> QPushButton:
        btn = QPushButton()
        btn.setObjectName("railitem")
        btn.setCursor(QtC.PointingHandCursor)
        btn.setStyleSheet(_rail_item_style(False))
        btn.clicked.connect(lambda _c=False, t=target: self._rail_navigate(t))

        # Identical margins in both states so the label never shifts when a
        # row becomes active.
        row = QHBoxLayout(btn)
        row.setContentsMargins(12, 0, 4, 0)
        row.setSpacing(10)
        text = QLabel(label)
        text.setStyleSheet(
            "color: palette(text); font-size: 13px; background: transparent; border: none;"
        )
        text.setAttribute(QtC.WA_TransparentForMouseEvents)
        row.addWidget(text, 1)
        count_lbl = QLabel(str(count))
        count_lbl.setStyleSheet(_RAIL_ITEM_COUNT)
        count_lbl.setAttribute(QtC.WA_TransparentForMouseEvents)
        row.addWidget(count_lbl)

        self._rail_items[target] = btn
        self._rail_counts[target] = count_lbl
        return btn

    def _refresh_rail_counts(self) -> None:
        """Update the counts that move with the user's history: Top picks,
        Sessions and Starred. Category counts are catalog-fixed and never
        change here. Called from `_refresh_shelves` after a favorites/recent
        reload (star toggles land here through `_reload_dynamic_pages`)."""
        if getattr(self, "_rail_counts", None) is None:
            return
        live = {
            "popular": len(self._top_pick_presets()),
            "user_favorites": len(self._session_entries()),
            "user_starred": len(self._pinned_entries()),
        }
        for target, value in live.items():
            lbl = self._rail_counts.get(target)
            if lbl is not None and _is_alive(lbl):
                lbl.setText(str(value))

    def _restyle_rail_item(self, key: str, active: bool) -> None:
        """Write one row's sheet. Both states are neutral: the active marker
        is the fill + bold label, never a colour."""
        btn = self._rail_items.get(key)
        if btn is None or not _is_alive(btn):
            return
        btn.setStyleSheet(_rail_item_style(active))

    def _set_rail_active(self, target: str | None) -> None:
        """Mark one rail row active and clear the rest. No-op targets (a page
        with no rail row, e.g. search) simply clear every row.

        Two guards, because every search keystroke calls this through
        `_sync_rail_for_page` and `setStyleSheet` on a card-sized widget costs
        ~279 us to restyle and ~691 us with the repaint. An unchanged target
        does nothing at all, and a changed one touches the two rows that
        actually move instead of the whole rail. Invariant this rests on: every
        row except `_rail_active` carries the inactive sheet, which
        `_make_rail_item` establishes at build time and only this method
        changes."""
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
        """Fill the container under `need:<need_key>` with one row per
        subfamily (label + muted count) and show it; every other family's
        container empties and hides. A row click glides the open hall to its
        section (`_scroll_hall_to` on the host dialog)."""
        self._clear_rail_subfamilies()
        host = self._rail_sub_hosts.get(f"need:{need_key}")
        if host is None or not _is_alive(host):
            return
        box = host.layout()
        for cat_key, label, count in entries:
            btn = QPushButton()
            btn.setObjectName("railsubitem")
            btn.setCursor(QtC.PointingHandCursor)
            btn.setStyleSheet(_rail_subitem_style(False))
            btn.clicked.connect(
                lambda _c=False, k=need_key, ck=cat_key: self._scroll_hall_to(k, ck)
            )
            row = QHBoxLayout(btn)
            row.setContentsMargins(27, 0, 4, 0)
            row.setSpacing(8)
            # The text QLabel is restyled by _set_rail_active_subfamily (QSS
            # does not cascade a parent button's color into child labels).
            text = QLabel(label)
            text.setStyleSheet(_RAIL_SUB_TEXT_REST)
            text.setAttribute(QtC.WA_TransparentForMouseEvents)
            row.addWidget(text, 1)
            count_lbl = QLabel(str(count))
            count_lbl.setStyleSheet(_RAIL_ITEM_COUNT)
            count_lbl.setAttribute(QtC.WA_TransparentForMouseEvents)
            row.addWidget(count_lbl)
            box.addWidget(btn)
            self._rail_sub_items[cat_key] = (btn, text)
        self._rail_sub_need = need_key
        host.setVisible(True)

    def _clear_rail_subfamilies(self) -> None:
        """Empty and hide every family's subfamily container.

        Same keystroke path as `_set_rail_active`: `_sync_rail_for_page` calls
        this for every page with no family open, so it early-outs when there is
        already nothing expanded."""
        if self._rail_sub_need is None and not self._rail_sub_items:
            return
        self._rail_sub_need = None
        self._rail_sub_active = None
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
        """Highlight the subfamily row the hall is currently scrolled to.
        Driven by the scroll-spy; ignores halls whose family is not the one
        the rail currently expands."""
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
