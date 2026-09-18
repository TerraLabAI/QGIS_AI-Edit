"""Post-generation version strip (film-strip of edit results).

A horizontal, Krea/Freepik-style strip that lives under the prompt once the
user has run at least one generation. The clean source capture is pinned on the
left as the "Original" tile and never scrolls away; a thin separator divides it
from the AI results, which live in a horizontal scroll area and append to the
right as they are produced. When the results overflow the dock width, small
chevrons appear to jump to either end. The most recent result is auto-selected.
Clicking a tile picks which version the next edit starts from.

Each tile is a small card: the picture, then its name under it (Original / V1 /
V2...) with the whole tile width, so no name is ever cut. The picked tile
carries the shared picked look (a soft tint of the hue, a thin border in the
hue ink and a check), never a solid fill. An eye button in the picture's top
right corner opens that version's details card (its prompt, what it was made
from, what it was saved as). The picked tile drives the next edit; the dock's
prompt placeholder and Generate button name it.

Tiles take keyboard focus from Tab (never from a click, which would pull the
caret out of the prompt box): Space or Return picks one, and the context-menu
key (or a right-click) opens the same details popover the button does without
touching the selection.

This widget is a pure view: it owns the thumbnails and the selection state,
and emits ``version_selected(index)``. The plugin owns the authoritative
mapping from strip index to the underlying layer / request id. Index 0 is the
Original.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QPainter, QPainterPath, QPixmap
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import qt_compat as QtC
from ..core.config_store import get_export_copy, get_export_dial
from ..core.i18n import tr
from ..core.resolution_labels import resolution_display_label
from .dock.design_tokens import (
    ACCENT_BORDER,
    FIELD,
    FONT_HINT,
    FONT_MICRO,
    HOVER,
    INK,
    INK_2,
    LINE,
    LINE_INPUT,
    LINE_STRONG,
    PICKED_HUE,
    RADIUS_CHIP,
    RADIUS_CONTROL,
    SPACE_TIGHT,
    SURFACE,
    category_ink,
    category_tint,
    qcolor,
    repolish_widget,
)
from .icons import icon_for, pixmap_for, widget_pixel_ratio
from .panel_helpers import main_window_for_dialog
from .version_details_popup import VersionDetailsPopup, VersionFacts

# The picture inside a tile. 76 px made the row the biggest thing on the
# result screen, above the prompt it serves (Yvann, 2026-09-18: "too big,
# more minimalist"). 48 px keeps the zone recognisable in a compact row.
VERSION_THUMB_PX = 48
# The card's padding around the picture, and the caption line under it. The
# caption sits OUTSIDE the picture, with the full tile width to itself, so
# "Original" reads as a word instead of "Orig...".
_TILE_PAD_PX = 3
_CAPTION_H_PX = 14
_TILE_W_PX = VERSION_THUMB_PX + 2 * _TILE_PAD_PX
_TILE_H_PX = 2 * _TILE_PAD_PX + VERSION_THUMB_PX + 2 + _CAPTION_H_PX
# The caption spans the tile inside its 1 px border, wider than the picture,
# so "Original" still fits at 11 px semibold on a 48 px tile.
_CAPTION_W_PX = _TILE_W_PX - 2
# Smallest pointer/touch target we ship (WCAG 2.2 target size, minimum).
_MIN_TARGET_PX = 24
# The picked check: a round field chip on the picture, and the glyph in it.
_MARK_PX = 16
_CHECK_PX = 10
# Chevron-jump glide duration.
_SCROLL_ANIM_MS = 240

# The picked look, shared with AI Segmentation: a soft tint of the hue, a thin
# border in the hue ink and a check. Never a solid fill. Keyboard focus stays
# the interaction blue and dashed, so the two never read as one thing.
_PICKED_INK = category_ink(PICKED_HUE)
_PICKED_TINT = category_tint(PICKED_HUE)
_PICKED_TINT_ON = category_tint(PICKED_HUE, strong=True)
_FOCUS_RING_QSS = f"QFrame#versionTile:focus {{ border: 1px dashed {ACCENT_BORDER}; }}"

# A tile is a small card: 8 px corners on the strong hairline at rest, the
# picked tint and hue border when it is what the next edit starts from. One
# stylesheet for both states (a `picked` property switches them), so no rule
# can go missing when the state changes. The picture is clipped to the inner
# curve (see rounded_cover_pixmap), so no square corner shows inside.
_TILE_STYLE = (
    f"QFrame#versionTile {{ background: {SURFACE};"
    f" border: 1px solid {LINE_STRONG}; border-radius: {RADIUS_CONTROL}px; }}"
    f"QFrame#versionTile:hover {{ border-color: {LINE_INPUT}; }}"
    f'QFrame#versionTile[picked="true"] {{ background: {_PICKED_TINT};'
    f" border: 1px solid {_PICKED_INK}; }}"
    f'QFrame#versionTile[picked="true"]:hover {{ background: {_PICKED_TINT_ON}; }}'
    f"QLabel#versionCaption {{ color: {INK}; font-size: {FONT_MICRO}px;"
    " font-weight: 600; background: transparent; border: none; }"
    f'QFrame#versionTile[picked="true"] QLabel#versionCaption {{ color: {_PICKED_INK}; }}'
    + _FOCUS_RING_QSS
)

# The picked mark's chip: opaque, so the check reads over any photograph.
_BASE_BADGE_STYLE = (
    f"QLabel {{ background: {FIELD}; border: none; border-radius: {_MARK_PX // 2}px; }}"
)

# Details button, top right of the picture: a round field chip, always there so
# "open this version" is a visible affordance and not a hover secret. The box
# meets the minimum target size; the chip painted inside it is 18 px, so it
# covers little of a 48 px picture.
_INFO_STYLE = (
    f"QToolButton {{ background: {FIELD}; border: none; border-radius: 9px;"
    " margin: 3px; padding: 0; }"
    f"QToolButton:hover {{ background: {SURFACE}; }}"
    f"QToolButton:focus {{ border: 1px solid {ACCENT_BORDER}; }}"
)

# The heading above the row: a quiet hint-weight label that says what the
# tiles are for, lighter than the prompt above it.
_HEADER_STYLE = (
    f"QLabel {{ color: {INK_2}; font-size: {FONT_HINT}px; font-weight: 400;"
    " background: transparent; border: none; }"
)

# Overflow chevron: a round surface button on the strong hairline, floating
# over the scroll edge.
_NAV_STYLE = (
    f"QToolButton {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {_MIN_TARGET_PX // 2}px; padding: 0; }}"
    f"QToolButton:hover {{ background: {HOVER}; }}"
    f"QToolButton:focus {{ border: 2px solid {ACCENT_BORDER}; }}"
)


def _pick_tooltip() -> str:
    """What a click on a tile does."""
    return get_export_copy(
        "widgets.version_strip.pick_tooltip",
        tr("Start the next edit from this version"),
    )


def _strip_heading(readonly: bool) -> str:
    """The row's heading: what a pick does, or, while a generation runs and
    picking is locked, just what the row holds."""
    if readonly:
        return get_export_copy("widgets.version_strip.versions_heading", tr("Versions"))
    return get_export_copy(
        "widgets.version_strip.start_from_heading", tr("Start the next edit from")
    )


def rounded_cover_pixmap(pixmap: QPixmap, size: int, radius: float, ratio: float) -> QPixmap:
    """``pixmap`` cropped to fill a ``size`` square, its corners on ``radius``.

    Drawn at the screen's pixel ratio so a Retina thumbnail stays sharp."""
    physical = max(1, int(round(size * ratio)))
    scaled = pixmap.scaled(
        QSize(physical, physical),
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        QtC.SmoothTransformation,
    )
    out = QPixmap(physical, physical)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, physical, physical), radius * ratio, radius * ratio)
        painter.setClipPath(path)
        painter.drawPixmap(
            -(scaled.width() - physical) // 2, -(scaled.height() - physical) // 2, scaled
        )
    finally:
        painter.end()
    out.setDevicePixelRatio(ratio)
    return out


class _ResultsScroll(QScrollArea):
    """Horizontal scroll area that keeps two chevron buttons floating over its
    left/right edges. Overlaying (rather than placing them inline) means showing
    a chevron never changes the scroll geometry, so a single click always lands
    at the true end."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._left = None
        self._right = None

    def attach_chevrons(self, left: QToolButton, right: QToolButton) -> None:
        self._left = left
        self._right = right
        left.setParent(self)
        right.setParent(self)
        self.reposition_chevrons()

    def reposition_chevrons(self) -> None:
        if self._left is None or self._right is None:
            return
        bw = self._left.width()
        bh = self._left.height()
        y = max(0, (self.height() - bh) // 2)
        self._left.move(0, y)
        self._right.move(max(0, self.width() - bw), y)
        self._left.raise_()
        self._right.raise_()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.reposition_chevrons()


class _VersionTile(QFrame):
    """A single strip thumbnail with a permanent caption and a details popover that
    shows on hover or focus."""

    clicked = pyqtSignal(int)

    def __init__(
        self,
        index: int,
        pixmap: QPixmap,
        is_original: bool,
        prompt: str,
        meta: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._index = index
        self._is_original = is_original
        self._selected = False
        self._readonly = False
        self._prompt = prompt
        self._meta = meta or {}
        # Set by the strip on the tile whose layer the last generation wrote,
        # so the details card can name it and select it in the Layers panel.
        self._layer_probe = None
        self.setObjectName("versionTile")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedSize(_TILE_W_PX, _TILE_H_PX)
        self.setStyleSheet(_TILE_STYLE)
        self.setCursor(QtC.PointingHandCursor)
        # The tile IS the picker for what the next edit builds on, so it has to
        # be reachable and activatable without a mouse. TabFocus, never
        # StrongFocus: the golden path is "pick V2, then type the next edit",
        # and a click-focusable frame pulls the caret out of the prompt box, so
        # the characters typed straight after the pick go nowhere.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        label = (
            tr("Original")
            if is_original
            else tr("V{n}").format(n=index)
        )
        self._label = label
        self.setAccessibleName(label)
        # Says what a click does, so the row never reads as a gallery the user
        # is merely looking at (Yvann, 2026-09-17).
        self.setToolTip(_pick_tooltip())
        # The details button is chrome that never takes focus, so this is a
        # screen reader's route to the version's facts and prompt.
        self.setAccessibleDescription(self._details_text())

        # The picture, inset by the card's padding and rounded to a chip curve
        # so it sits inside the card instead of filling it edge to edge.
        self._pixmap_label = QLabel(self)
        self._pixmap_label.setGeometry(
            _TILE_PAD_PX, _TILE_PAD_PX, VERSION_THUMB_PX, VERSION_THUMB_PX
        )
        self._pixmap_label.setAlignment(QtC.AlignCenter)
        self._pixmap_label.setStyleSheet("background: transparent; border: none;")
        if pixmap is not None and not pixmap.isNull():
            self._pixmap_label.setPixmap(
                rounded_cover_pixmap(
                    pixmap, VERSION_THUMB_PX, RADIUS_CHIP, widget_pixel_ratio(self)
                )
            )

        # Caption under the picture, with the whole tile width to itself: the
        # word is never cut. A language that still overflows elides and keeps
        # the whole word in the tooltip and the accessible name.
        cap = QLabel(label, self)
        cap.setObjectName("versionCaption")
        cap.setAlignment(QtC.AlignCenter)
        # Measure with the caption's own 11 px semibold, not the default font
        # it carries before the tile's stylesheet reaches it.
        cap.ensurePolished()
        room = _CAPTION_W_PX
        if cap.fontMetrics().horizontalAdvance(label) > room:
            cap.setText(cap.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, room))
            cap.setToolTip(label)
        cap.setGeometry(
            1,
            _TILE_PAD_PX + VERSION_THUMB_PX + 2,
            room,
            _CAPTION_H_PX,
        )
        self._caption = cap

        # Picked mark, top left of the picture: a round field chip with the
        # check in the picked hue. The tint and the border say the same thing
        # in colour; this says it without colour.
        self._base_badge = QLabel(self)
        self._base_badge.setFixedSize(_MARK_PX, _MARK_PX)
        self._base_badge.setAlignment(QtC.AlignCenter)
        self._base_badge.setStyleSheet(_BASE_BADGE_STYLE)
        self._base_badge.setPixmap(pixmap_for(self, "check", _CHECK_PX, qcolor(_PICKED_INK)))
        self._base_badge.move(_TILE_PAD_PX + 2, _TILE_PAD_PX + 2)
        self._base_badge.setVisible(False)

        # Details button, top right of the picture, always visible: opening a
        # version is a thing the user does (Yvann, 2026-09-17), so it is not
        # hidden behind hover. Its own click opens the card and, being a child
        # on top, never changes which version is picked. It stays out of the
        # tab chain: the same card opens from the focused tile with the
        # context-menu key (contextMenuEvent).
        self._info_btn = QToolButton(self)
        self._info_btn.setIcon(icon_for(self, "eye", 12, qcolor(INK)))
        self._info_btn.setIconSize(QSize(12, 12))
        self._info_btn.setStyleSheet(_INFO_STYLE)
        self._info_btn.setCursor(QtC.PointingHandCursor)
        self._info_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        details_label = get_export_copy("widgets.version_strip.version_details", tr("Version details"))
        self._info_btn.setAccessibleName(details_label)
        self._info_btn.setToolTip(details_label)
        self._info_btn.setFixedSize(_MIN_TARGET_PX, _MIN_TARGET_PX)
        # The box's 3 px margin lines the painted chip up with the picture's
        # top right corner.
        self._info_btn.move(
            _TILE_PAD_PX + VERSION_THUMB_PX - _MIN_TARGET_PX + 2, _TILE_PAD_PX - 2
        )
        self._info_btn.clicked.connect(self._open_info)

    def set_layer_probe(self, probe) -> None:
        """Hand the tile a callable returning ``(layer name, reveal callback)``
        for the layer this version was saved to, or None. Only the tile the
        last generation wrote gets one."""
        self._layer_probe = probe

    def _facts(self) -> VersionFacts:
        """Everything the details card reads about this version."""
        layer_name = ""
        if self._layer_probe is not None:
            found = self._layer_probe()
            layer_name = (found[0] if found else "") or ""
        return VersionFacts(
            label=self._label,
            is_original=self._is_original,
            is_picked=self._selected,
            prompt=self._prompt,
            definition=resolution_display_label(self._meta.get("definition")) or "",
            dimensions=str(self._meta.get("dimensions") or ""),
            template_name=str(self._meta.get("template_name") or ""),
            base_label=str(self._meta.get("base_label") or ""),
            layer_name=layer_name,
        )

    def _details_text(self) -> str:
        """The details card's content on one line, for a screen reader that
        reads the tile itself."""
        facts = self._facts()
        parts = [facts.label]
        if facts.is_original:
            parts.append(get_export_copy("widgets.version_strip.clean_source", tr("no AI edit")))
        else:
            if facts.definition:
                parts.append(facts.definition)
            if facts.base_label:
                parts.append(tr("from {base}").format(base=facts.base_label))
            if facts.prompt:
                parts.append(facts.prompt)
        return ", ".join(parts)

    def _reveal_layer(self) -> None:
        found = self._layer_probe() if self._layer_probe is not None else None
        if found and callable(found[1]):
            found[1]()

    def _open_info(self) -> None:
        # Parent on the top-level window, not self: on macOS fullscreen a dialog
        # parented to a widget inside a (floating) dock can open in another Space.
        parent_window = main_window_for_dialog(self)
        dlg = VersionDetailsPopup(
            self._facts(),
            parent_window,
            anchor=self,
            on_pick=None if self._readonly else self._pick_from_card,
            on_reveal_layer=self._reveal_layer,
        )
        dlg.exec()

    def _pick_from_card(self) -> None:
        """The card's 'Start from ...' button: the same thing a click on the
        tile does, said in words."""
        if not self._readonly:
            self.clicked.emit(self._index)

    def _refresh_accessible_name(self) -> None:
        """Name the tile with its selection state."""
        selected_word = get_export_copy("widgets.version_strip.selected_suffix", tr("selected"))
        suffix = " - " + selected_word if self._selected else ""
        self.setAccessibleName(self._label + suffix)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        # A property switch, not a second stylesheet: one sheet holds both
        # states, so no rule can be lost when the tile is picked.
        self.setProperty("picked", "true" if selected else "false")
        repolish_widget(self)
        # The caption's colour comes from a descendant rule of the same sheet,
        # and Qt does not re-evaluate a child when its parent is repolished:
        # without this the word kept the hue of the state it was last in.
        repolish_widget(self._caption)
        self._base_badge.setVisible(selected)
        self._refresh_accessible_name()

    def set_readonly(self, readonly: bool) -> None:
        """A locked tile (a generation is running) still opens its card, but
        no longer promises what a click would do."""
        self._readonly = readonly
        self.setCursor(QtC.ArrowCursor if readonly else QtC.PointingHandCursor)
        self.setToolTip("" if readonly else _pick_tooltip())

    def mousePressEvent(self, event):  # noqa: N802
        # Left button only. A right-click is the "show me the details" gesture
        # (contextMenuEvent below); re-pointing the base of the next edit as a
        # side effect of reading a tile is not something the user asked for.
        if not self._readonly and event.button() == QtC.LeftButton:
            self.clicked.emit(self._index)
        super().mousePressEvent(event)

    # Keys this tile answers for itself. They are claimed from the window's
    # shortcuts in event() below, not just handled here.
    _OWN_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)

    def event(self, event):
        # The dock owns QShortcut(Return/Enter, WindowShortcut) for Generate,
        # and a window shortcut wins the keyboard race before any focused
        # child's keyPressEvent runs. Accepting the ShortcutOverride is Qt's
        # way to say "this key is mine while I hold focus", so Return on a
        # focused tile picks that version instead of spending credits.
        if event.type() == QEvent.Type.ShortcutOverride:
            if event.key() in self._OWN_KEYS:
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):  # noqa: N802
        # Space and Return are the keyboard twin of a click on the tile.
        if event.key() in self._OWN_KEYS:
            if not self._readonly:
                self.clicked.emit(self._index)
            event.accept()
            return
        # Keys we don't handle: ignore, so Tab, Escape and the QGIS shortcuts
        # keep working.
        event.ignore()

    def contextMenuEvent(self, event):  # noqa: N802
        # Fired by a right-click AND by the keyboard menu key (Shift+F10), the
        # keyboard route to the details card. Reading the details never changes
        # which version is picked (see mousePressEvent).
        self._open_info()
        event.accept()


class VersionStrip(QWidget):
    """Version picker: a 'Start the next edit from' heading + a thumbnail row
    (Original pinned left, results scrolling right)."""

    version_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tiles: list[_VersionTile] = []
        self._selected_index = 0
        self._readonly = False
        self._layer_probe = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_TIGHT)

        # Quiet heading so the row's purpose reads at a glance: these pictures
        # are the choice of what the next edit builds on.
        self._header = QLabel(_strip_heading(False), self)
        self._header.setStyleSheet(_HEADER_STYLE)
        outer.addWidget(self._header)

        self._row_host = QWidget(self)
        self._row_host.setStyleSheet("background: transparent;")
        self._row = QHBoxLayout(self._row_host)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(SPACE_TIGHT)

        # Separator between the pinned Original and the scrolling results.
        self._separator = QFrame(self._row_host)
        self._separator.setFrameShape(QtC.FrameVLine)
        self._separator.setFixedWidth(1)
        self._separator.setStyleSheet(f"QFrame {{ color: {LINE}; background: {LINE}; border: none; }}")
        self._separator.setVisible(False)

        # Scrolling results (generated versions only; Original stays pinned).
        self._gen_host = QWidget()
        self._gen_host.setStyleSheet("background: transparent;")
        self._gen_row = QHBoxLayout(self._gen_host)
        self._gen_row.setContentsMargins(0, 0, 0, 0)
        self._gen_row.setSpacing(SPACE_TIGHT)
        self._gen_row.addStretch()

        self._scroll = _ResultsScroll(self._row_host)
        self._scroll.setWidget(self._gen_host)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtC.FrameNoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        self._scroll.setFixedHeight(_TILE_H_PX)
        self._scroll.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)

        # Overflow chevrons overlaid on the scroll edges (jump to far end).
        self._left_btn = self._make_nav_btn(
            "chevron_left", get_export_copy("widgets.version_strip.jump_first", tr("Jump to the first version"))
        )
        self._left_btn.clicked.connect(self._scroll_to_start)
        self._right_btn = self._make_nav_btn(
            "chevron_right", get_export_copy("widgets.version_strip.jump_latest", tr("Jump to the latest version"))
        )
        self._right_btn.clicked.connect(self._scroll_to_end)
        self._scroll.attach_chevrons(self._left_btn, self._right_btn)

        bar = self._scroll.horizontalScrollBar()
        bar.valueChanged.connect(lambda _v: self._update_nav())
        bar.rangeChanged.connect(lambda _a, _b: self._update_nav())

        # Smooth glide when a chevron jumps to an end (no instant teleport).
        self._scroll_anim = QPropertyAnimation(bar, b"value", self)
        self._scroll_anim.setDuration(get_export_dial("widgets.version_strip.scroll_anim_ms", _SCROLL_ANIM_MS))
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Assemble: [Original inserted at 0 in reset] | sep | scroll
        self._row.addWidget(self._separator)
        self._row.addWidget(self._scroll, 1)
        outer.addWidget(self._row_host)

        self.setVisible(False)

    # -- public API --------------------------------------------------------

    def reset(
        self, original_pixmap: QPixmap | None, prompt: str = "", meta: dict | None = None
    ) -> None:
        """Clear the strip and seed it with the pinned Original tile (selected)."""
        self._clear_generated()
        self._remove_original()
        tile = self._make_tile(0, original_pixmap, is_original=True, prompt=prompt, meta=meta)
        self._row.insertWidget(0, tile)
        self._tiles.append(tile)
        self._separator.setVisible(False)
        self._selected_index = 0
        tile.set_selected(True)
        self.setVisible(True)
        self._update_nav()

    def add_version(
        self, pixmap: QPixmap | None, prompt: str = "", meta: dict | None = None
    ) -> int:
        """Append a generated version to the scroll and auto-select it.

        Returns the new tile's strip index. Falls back to 0 if the strip was
        never seeded (defensive; the plugin always seeds first).
        """
        if not self._tiles:
            return 0
        self._take_gen_stretch()
        index = len(self._tiles)
        tile = self._make_tile(index, pixmap, is_original=False, prompt=prompt, meta=meta)
        self._gen_row.addWidget(tile)
        self._gen_row.addStretch()
        self._tiles.append(tile)
        self._separator.setVisible(True)
        self._refresh_layer_probes()
        self.set_selected(index)
        self._update_nav()
        return index

    def set_saved_layer_probe(self, probe) -> None:
        """Hand the strip a callable returning ``(layer name, reveal callback)``
        for the layer the last generation wrote, or None.

        Only the newest generated tile gets it: that is the version the dock
        just saved. The old "Saved as <layer>" row of the result screen is
        gone, and this is where that fact and its click now live."""
        self._layer_probe = probe
        self._refresh_layer_probes()

    def clear(self) -> None:
        """Empty the strip and hide it, heading included (a new lineage starts
        blank). Everything the strip holds is dropped here: the tiles, the
        selection, the separator and the overflow chevrons. Nothing may
        outlive the tiles. The saved-layer probe survives: it is the wiring to
        the dock, set once at build time, and it answers None on its own once
        there is no saved layer to name."""
        self._clear_generated()
        self._remove_original()
        self._separator.setVisible(False)
        self._selected_index = 0
        self._scroll_anim.stop()
        self._left_btn.setVisible(False)
        self._right_btn.setVisible(False)
        self.setVisible(False)
        self._update_nav()

    def set_selected(self, index: int) -> None:
        """Move the selection ring. Does not emit ``version_selected``."""
        if index < 0 or index >= len(self._tiles):
            return
        self._selected_index = index
        for i, tile in enumerate(self._tiles):
            tile.set_selected(i == index)
        self._ensure_visible(index)

    def selected_index(self) -> int:
        return self._selected_index

    def label_for(self, index: int) -> str:
        """Short label for a strip index: 'Original' (0) or 'V{n}'."""
        return (
            tr("Original")
            if index <= 0
            else tr("V{n}").format(n=index)
        )

    def count(self) -> int:
        return len(self._tiles)

    def set_readonly(self, readonly: bool) -> None:
        """Lock selection while a generation runs (scrolling stays allowed)."""
        self._readonly = readonly
        self._header.setText(_strip_heading(readonly))
        for tile in self._tiles:
            tile.set_readonly(readonly)

    # -- internal ----------------------------------------------------------

    def _make_tile(
        self, index: int, pixmap, is_original: bool, prompt: str, meta: dict | None = None
    ) -> _VersionTile:
        parent = self._row_host if is_original else self._gen_host
        tile = _VersionTile(index, pixmap, is_original, prompt, meta, parent)
        tile.set_readonly(self._readonly)
        tile.clicked.connect(self._on_tile_clicked)
        return tile

    def _refresh_layer_probes(self) -> None:
        """Point the saved-layer probe at the newest generated tile only: the
        Original was never written to a layer, and an older version's layer is
        not what the last generation saved."""
        newest = len(self._tiles) - 1
        for i, tile in enumerate(self._tiles):
            tile.set_layer_probe(
                self._layer_probe if (i == newest and i > 0) else None
            )

    def _make_nav_btn(self, glyph: str, name: str) -> QToolButton:
        btn = QToolButton(self._scroll)
        btn.setIcon(icon_for(self, glyph, 16, qcolor(INK)))
        btn.setIconSize(QSize(16, 16))
        btn.setCursor(QtC.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setAccessibleName(name)
        btn.setToolTip(name)
        btn.setFixedSize(_MIN_TARGET_PX, _MIN_TARGET_PX)
        btn.setStyleSheet(_NAV_STYLE)
        btn.setVisible(False)
        return btn

    def _on_tile_clicked(self, index: int) -> None:
        if self._readonly or index == self._selected_index:
            return
        self.set_selected(index)
        self.version_selected.emit(index)

    def _ensure_visible(self, index: int) -> None:
        # Index 0 is the pinned Original (outside the scroll); only results scroll.
        if 1 <= index < len(self._tiles):
            self._scroll.ensureWidgetVisible(self._tiles[index])
            # A tile added this instant has no geometry yet, so the call above
            # measured nothing: with six or more versions the picked newest
            # one sat past the right edge. Again once the row is laid out.
            QtC.safe_single_shot(0, self, self._ensure_selected_visible)

    def _ensure_selected_visible(self) -> None:
        index = self._selected_index
        if 1 <= index < len(self._tiles):
            self._scroll.ensureWidgetVisible(self._tiles[index])
        self._update_nav()

    def showEvent(self, event):  # noqa: N802
        # The strip is re-homed between screens hidden; its picked tile comes
        # back into view with it.
        super().showEvent(event)
        QtC.safe_single_shot(0, self, self._ensure_selected_visible)

    def _animate_scroll_to(self, target: int) -> None:
        bar = self._scroll.horizontalScrollBar()
        self._scroll_anim.stop()
        self._scroll_anim.setStartValue(bar.value())
        self._scroll_anim.setEndValue(target)
        self._scroll_anim.start()

    def _scroll_to_start(self) -> None:
        self._animate_scroll_to(self._scroll.horizontalScrollBar().minimum())

    def _scroll_to_end(self) -> None:
        self._animate_scroll_to(self._scroll.horizontalScrollBar().maximum())

    def _update_nav(self) -> None:
        """Show a chevron only when results overflow in that direction. The
        chevrons overlay the scroll, so toggling them never reflows the row."""
        bar = self._scroll.horizontalScrollBar()
        overflow = bar.maximum() > bar.minimum()
        self._left_btn.setVisible(overflow and bar.value() > bar.minimum())
        self._right_btn.setVisible(overflow and bar.value() < bar.maximum())
        self._scroll.reposition_chevrons()

    def _take_gen_stretch(self) -> None:
        count = self._gen_row.count()
        if count > 0:
            item = self._gen_row.itemAt(count - 1)
            if item is not None and item.widget() is None:
                self._gen_row.takeAt(count - 1)

    def _clear_generated(self) -> None:
        # Keep index 0 (Original) in _tiles; drop the generated tail + widgets.
        self._tiles = self._tiles[:1] if self._tiles else []
        while self._gen_row.count():
            item = self._gen_row.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._gen_row.addStretch()

    def _remove_original(self) -> None:
        if self._tiles:
            original = self._tiles[0]
            self._row.removeWidget(original)
            original.setParent(None)
            original.deleteLater()
        self._tiles = []

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_nav()
