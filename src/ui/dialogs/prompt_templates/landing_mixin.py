"""Library landing page: one toggleable feed row (Popular / Recent / Favorites)
plus three visual need tiles (Analyser / Simuler / Habiller) that drill into a
per-need gallery. Replaces the old need/category sidebar as the entry surface.

Mixed into PromptTemplatesDialog. Reuses the existing card builders
(`_build_top_pick_card`, `_build_generation_card`) so the feed cards behave
exactly like the gallery cards, and `get_need_tiles` for the tile data.
"""
from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.i18n import tr
from ....core.prompts.prompt_presets import get_need_tiles
from .common import (
    _EMPTY_MSG,
    _FEED_SEG,
    _FEED_SEG_BTN,
    _FEED_SEG_BTN_ACTIVE,
    _FEED_SUBTITLE,
    _LANDING_HEADING,
    _NEED_ACCENT,
    _NEED_ICON,
    _NEED_TILE_COUNT,
    _NEED_TILE_EXPLORE,
    _NEED_TILE_SUB,
    _SEE_ALL_BTN,
    _TILE_ARROW_OVERLAY,
    _TILE_NAME_OVERLAY,
)

_FEEDS = ("popular", "recent", "favorites")
_FEED_TO_PAGE = {"popular": "favorites", "recent": "recent", "favorites": "user_favorites"}
# A landing feed shows a fixed row of this many cards (no horizontal scroll);
# the rest live behind "See all".
_FEED_STRIP_MAX = 4
# Uniform card height for the feed strip so the row does not jump between
# Popular (template slider cards) and Recent (generation cards).
_FEED_CARD_HEIGHT = 272
# Fallback accent when a hero preset has no known category glyph colour.
_ACCENT_FALLBACK = "rgba(139,172,39,0.9)"


class _NeedTile(QFrame):
    """One clickable family tile, bordered in its family colour. The whole
    frame opens the family; the hero image and overlay inside route their
    clicks to the same handler, so there is a single interaction model."""

    def __init__(self, on_click, accent, parent=None):
        super().__init__(parent)
        self.setObjectName("needtile")
        self._on_click = on_click
        self._accent = accent
        self.setCursor(QtC.PointingHandCursor)
        self.setStyleSheet(self._tile_style(False))
        self.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)

    def _tile_style(self, hover: bool) -> str:
        bg = "rgba(128,128,128,0.12)" if hover else "rgba(128,128,128,0.05)"
        return (
            f"QFrame#needtile {{ border: 1px solid {self._accent}; "
            f"border-radius: 8px; background: {bg}; }}"
        )

    def enterEvent(self, event):
        self.setStyleSheet(self._tile_style(True))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(self._tile_style(False))
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self._on_click()
        super().mousePressEvent(event)


class _TileHero(QFrame):
    """Before/after preview for a need tile: the slider only, with NO prompt
    name and NO 'Use' affordance. The family name and 'Explore' live on the
    tile itself, so the tile reads as a family, not a single prompt."""

    HERO_HEIGHT = 240

    def __init__(self, preset, on_click, demo_loader=None, absolute_url=None, parent=None):
        super().__init__(parent)
        self._tid = preset.get("id", "")
        self._pending: set[str] = set()
        self._loader = None

        from ...before_after_slider import BeforeAfterSlider

        self._slider = BeforeAfterSlider(self, auto_loop=False, show_badges=False)
        self._slider.setFixedHeight(self.HERO_HEIGHT)
        self._slider.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)
        self._slider.clicked.connect(lambda: on_click())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._slider)

        if demo_loader is not None and absolute_url is not None and self._tid:
            url_before = preset.get("demo_url_before")
            url_after = preset.get("demo_url_after")
            if url_before:
                self._pending.add("before")
            if url_after:
                self._pending.add("after")
            if self._pending:
                self._loader = demo_loader
                demo_loader.loaded.connect(self._on_loaded)
                demo_loader.failed.connect(self._on_failed)
                if url_before:
                    demo_loader.request(self._tid, "before", absolute_url(url_before))
                if url_after:
                    demo_loader.request(self._tid, "after", absolute_url(url_after))
        self._refresh()

    def _on_loaded(self, tid, which, pixmap):
        if tid != self._tid:
            return
        if which == "before":
            self._slider.set_before(pixmap)
        elif which == "after":
            self._slider.set_after(pixmap)
        self._pending.discard(which)
        self._refresh()

    def _on_failed(self, tid, which):
        if tid != self._tid:
            return
        self._pending.discard(which)
        self._refresh()

    def _refresh(self):
        if not self._pending:
            self._slider.set_placeholder_text(tr("No preview"))

    def deleteLater(self):  # noqa: N802 - Qt signature
        if self._loader is not None:
            for sig, slot in (
                (self._loader.loaded, self._on_loaded),
                (self._loader.failed, self._on_failed),
            ):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        super().deleteLater()


class LandingMixin:
    """Landing page construction and the feed toggle. Requires the host to
    provide `_build_top_pick_card`, `_build_generation_card`,
    `_grouped_recent_entries`, `_ensure_need_page`, `_switch_to_page`,
    `_categories_by_key`, `_recent_jobs`, `_favorite_jobs`, `_server_catalog`."""

    def _feed_meta(self) -> dict:
        return {
            "popular": (tr("Popular"), tr("What the community runs most often")),
            "recent": (tr("Recent"), tr("Your latest prompts")),
            "favorites": (tr("Favorites"), tr("Your starred prompts")),
        }

    # -- landing page ----------------------------------------------------

    def _build_landing_page(self) -> QWidget:
        self._feed_buttons: dict[str, QPushButton] = {}
        self._feed_all_pages: dict[str, QWidget] = {}
        self._active_feed = "popular"
        meta = self._feed_meta()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtC.FrameNoFrame)
        scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(6, 4, 6, 8)
        outer.setSpacing(10)

        # 1) Families first - the entry point. An explicit instruction makes it
        # clear this is a choice: pick a family to see its prompts.
        heading = QLabel(tr("Choose a family to explore"))
        heading.setStyleSheet(_LANDING_HEADING)
        outer.addWidget(heading)

        tiles_host = QWidget()
        grid = QGridLayout(tiles_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        self._build_need_tiles(grid)
        outer.addWidget(tiles_host)

        # The large family tiles carry the vertical weight; keep only a modest
        # gap before the feed (a big flexible gap here read as an awkward hole).
        outer.addSpacing(18)

        # 2) Then the feed (Popular / Recent / Favorites), below the families.
        seg_row = QHBoxLayout()
        seg_row.setContentsMargins(0, 0, 0, 0)
        seg_row.setSpacing(10)
        seg = QWidget()
        seg.setObjectName("feedseg")
        seg.setStyleSheet(_FEED_SEG)
        seg_inner = QHBoxLayout(seg)
        seg_inner.setContentsMargins(3, 3, 3, 3)
        seg_inner.setSpacing(2)
        group = QButtonGroup(content)
        group.setExclusive(True)
        for key in _FEEDS:
            btn = QPushButton(meta[key][0])
            btn.setCheckable(True)
            btn.setCursor(QtC.PointingHandCursor)
            btn.setStyleSheet(_FEED_SEG_BTN)
            btn.clicked.connect(lambda _checked=False, k=key: self._switch_feed(k))
            group.addButton(btn)
            seg_inner.addWidget(btn)
            self._feed_buttons[key] = btn
        seg_row.addWidget(seg)
        self._feed_subtitle = QLabel("")
        self._feed_subtitle.setStyleSheet(_FEED_SUBTITLE)
        seg_row.addWidget(self._feed_subtitle)
        seg_row.addStretch()
        outer.addLayout(seg_row)

        # Feed slot: one strip visible at a time, swapped by _switch_feed.
        self._feed_slot = QVBoxLayout()
        self._feed_slot.setContentsMargins(0, 0, 0, 0)
        feed_host = QWidget()
        feed_host.setLayout(self._feed_slot)
        outer.addWidget(feed_host)

        # "See all" sits at the bottom-right of the feed, labelled with the total
        # so the user knows how much more there is.
        see_all_row = QHBoxLayout()
        see_all_row.setContentsMargins(0, 2, 0, 0)
        see_all_row.addStretch()
        self._see_all_btn = QPushButton()
        self._see_all_btn.setStyleSheet(_SEE_ALL_BTN)
        self._see_all_btn.setCursor(QtC.PointingHandCursor)
        self._see_all_btn.clicked.connect(lambda _c=False: self._open_feed_all(self._active_feed))
        see_all_row.addWidget(self._see_all_btn)
        outer.addLayout(see_all_row)

        outer.addStretch(1)

        scroll.setWidget(content)
        self._landing_page = scroll
        # Populate the default feed now that the slot exists.
        self._switch_feed("popular")
        return scroll

    # -- feed toggle -----------------------------------------------------

    def _feed_entries(self, feed_key: str) -> list:
        """Tagged entries ({"kind": "preset"|"job", "data": ...}) for a feed,
        drawn from the same sources the sidebar tabs used."""
        if feed_key == "recent":
            return self._grouped_recent_entries(self._recent_jobs) if self._recent_jobs else []
        if feed_key == "favorites":
            cat = self._categories_by_key.get("user_favorites") or {}
            entries = [{"kind": "preset", "data": p} for p in (cat.get("presets") or [])]
            entries += [{"kind": "job", "data": j} for j in self._favorite_jobs]
            return entries
        cat = self._categories_by_key.get("favorites") or {}  # Top Picks
        return [{"kind": "preset", "data": p} for p in (cat.get("presets") or [])]

    def _build_feed_row(self, entries: list) -> QWidget:
        """A fixed row of up to 4 before/after cards for the active feed - no
        horizontal scroll. Fewer entries pad with spacers so card widths stay
        even; the rest live behind 'See all'."""
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(12)
        if not entries:
            empty = QLabel(tr("Nothing here yet."))
            empty.setStyleSheet(_EMPTY_MSG)
            row.addWidget(empty)
            row.addStretch()
            return host
        shown = entries[:_FEED_STRIP_MAX]
        for entry in shown:
            if entry.get("kind") == "preset":
                card = self._build_top_pick_card(entry["data"])
            else:
                card = self._build_generation_card(entry["data"], True, entry.get("count", 1))
                # A short row: load thumbnails eagerly instead of the gallery's
                # viewport-based lazy loader.
                if hasattr(card, "load_thumbnails"):
                    card.load_thumbnails()
            card.setFixedHeight(_FEED_CARD_HEIGHT)  # uniform row height across feeds
            row.addWidget(card, 1)
        # Keep card widths at ~1/4 even when fewer than 4 are shown.
        for _ in range(_FEED_STRIP_MAX - len(shown)):
            row.addWidget(QWidget(), 1)
        return host

    def _open_feed_all(self, feed_key: str) -> None:
        """'See all' for the active feed: the full gallery under a back header."""
        if feed_key not in _FEEDS:
            feed_key = "popular"
        page = self._feed_all_pages.get(feed_key)
        if page is None:
            page = self._build_feed_all_page(feed_key)
            self._feed_all_pages[feed_key] = page
            self._stack.addWidget(page)
        self._switch_to_page(page)

    def _build_feed_all_page(self, feed_key: str) -> QWidget:
        """Full gallery for a feed, reusing the existing page builder under a
        compact back header."""
        container = QWidget()
        box = QVBoxLayout(container)
        box.setContentsMargins(6, 4, 6, 4)
        box.setSpacing(8)
        box.addWidget(self._build_back_header(self._feed_meta()[feed_key][0]))
        box.addWidget(self._build_page(_FEED_TO_PAGE[feed_key]), 1)
        return container

    def _switch_feed(self, feed_key: str) -> None:
        if feed_key not in _FEEDS:
            feed_key = "popular"
        self._active_feed = feed_key
        while self._feed_slot.count():
            item = self._feed_slot.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)  # remove from view now (deleteLater is async)
                widget.deleteLater()
        entries = self._feed_entries(feed_key)
        self._feed_slot.addWidget(self._build_feed_row(entries))
        self._feed_subtitle.setText(self._feed_meta()[feed_key][1])
        total = len(entries)
        if total > _FEED_STRIP_MAX:
            self._see_all_btn.setText(tr("See all") + f" ({total})  ›")  # chevron outside tr()
            self._see_all_btn.setVisible(True)
        else:
            self._see_all_btn.setVisible(False)
        for key, btn in self._feed_buttons.items():
            active = key == feed_key
            btn.setChecked(active)
            btn.setStyleSheet(_FEED_SEG_BTN_ACTIVE if active else _FEED_SEG_BTN)
        # TODO(telemetry Task 6): track LIBRARY_FEED_SWITCHED {"feed": feed_key}
        # once the event is added to the website registry + analytics_events.json.

    # -- need tiles ------------------------------------------------------

    def _build_need_tiles(self, grid: QGridLayout) -> None:
        for idx, tile in enumerate(get_need_tiles(self._server_catalog)):
            if tile["preset_count"] == 0:
                continue  # do not show an empty need as a dead tile
            row, col = divmod(idx, 3)
            grid.addWidget(self._make_need_tile(tile), row, col)

    def _make_need_tile(self, tile: dict) -> QWidget:
        need_key = tile["key"]
        accent = _NEED_ACCENT.get(need_key, _ACCENT_FALLBACK)
        frame = _NeedTile(lambda k=need_key: self._open_need(k), accent)
        box = QVBoxLayout(frame)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)

        # Image with a branded overlay stacked on top (same grid cell): a
        # family-colour veil, an emblem badge, the huge family name and a big
        # arrow. This is what makes the tile read as a portal, not a prompt.
        hero = tile["hero"]
        stack = QWidget()
        grid = QGridLayout(stack)
        grid.setContentsMargins(0, 0, 0, 0)
        if hero:
            grid.addWidget(self._build_tile_hero(hero, need_key), 0, 0)
        overlay = QFrame()
        overlay.setStyleSheet(self._tile_scrim(accent))
        overlay.setAttribute(QtC.WA_TransparentForMouseEvents)  # clicks fall to the tile
        ov = QVBoxLayout(overlay)
        ov.setContentsMargins(12, 10, 12, 10)
        ov.setSpacing(0)
        top = QHBoxLayout()
        badge = QLabel(_NEED_ICON.get(need_key, "◉"))
        badge.setFixedSize(30, 30)
        badge.setAlignment(QtC.AlignCenter)
        badge.setStyleSheet(
            f"QLabel {{ background: {accent}; color: #ffffff; "
            "border-radius: 15px; font-size: 15px; }"
        )
        top.addWidget(badge)
        top.addStretch()
        ov.addLayout(top)
        ov.addStretch()
        bottom = QHBoxLayout()
        name = QLabel(tile["label"])
        name.setStyleSheet(_TILE_NAME_OVERLAY)
        bottom.addWidget(name)
        bottom.addStretch()
        arrow = QLabel("→")  # glyph outside tr()
        arrow.setStyleSheet(_TILE_ARROW_OVERLAY)
        bottom.addWidget(arrow)
        ov.addLayout(bottom)
        grid.addWidget(overlay, 0, 0)
        box.addWidget(stack)

        # Below the image: tagline, count and the Explore cue.
        text = QWidget()
        tv = QVBoxLayout(text)
        tv.setContentsMargins(13, 10, 13, 12)
        tv.setSpacing(4)
        sub = QLabel(tile["tagline"])
        sub.setStyleSheet(_NEED_TILE_SUB)
        sub.setWordWrap(True)
        tv.addWidget(sub)
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 3, 0, 0)
        foot.setSpacing(0)
        count = QLabel(tr("{n} prompts").format(n=tile["preset_count"]))
        count.setStyleSheet(_NEED_TILE_COUNT)
        foot.addWidget(count)
        foot.addStretch()
        explore = QLabel(tr("Explore") + "  ›")  # chevron outside tr()
        explore.setStyleSheet(_NEED_TILE_EXPLORE)
        explore.setAttribute(QtC.WA_TransparentForMouseEvents)  # click falls to the tile
        foot.addWidget(explore)
        tv.addLayout(foot)
        box.addWidget(text)
        return frame

    @staticmethod
    def _tile_scrim(accent: str) -> str:
        """Overlay gradient: a family-colour veil at the top fading to a dark
        base at the bottom so the white name stays legible on any imagery."""
        hexc = accent.lstrip("#")
        return (
            "QFrame { border: none; background: qlineargradient("
            "x1:0, y1:0, x2:0, y2:1, "
            "stop:0 #22" + hexc + ", stop:0.55 #33" + hexc
            + ", stop:1 rgba(0,0,0,180)); }"
        )

    def _build_tile_hero(self, hero: dict, need_key: str) -> QWidget:
        """The tile's before/after preview - image only (no prompt name / Use),
        clicking it opens the family."""
        from ....core.prompts.prompt_presets_client import absolute_demo_url

        loader = self._demo_loader if self._client is not None else None

        def _abs(rel, _client=self._client):
            return absolute_demo_url(_client, rel) if _client is not None else rel

        return _TileHero(
            hero,
            (lambda k=need_key: self._open_need(k)),
            demo_loader=loader,
            absolute_url=_abs if loader is not None else None,
        )

    def _open_need(self, need_key: str) -> None:
        page = self._ensure_need_page(need_key)
        if page is not None:
            self._switch_to_page(page)
        # TODO(telemetry Task 6): track LIBRARY_NEED_OPENED {"need": need_key}
        # once the event is added to the website registry + analytics_events.json.
