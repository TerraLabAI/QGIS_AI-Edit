"""Post-generation version strip (film-strip of edit results).

A horizontal, Krea/Freepik-style strip that lives under the prompt once the
user has run at least one generation. The clean source capture is pinned on the
left as the "Original" tile and never scrolls away; a thin separator divides it
from the AI results, which live in a horizontal scroll area and append to the
right as they are produced. When the results overflow the dock width, small
chevrons appear to jump to either end. The most recent result is auto-selected.
Clicking a tile picks which version the next edit starts from.

Each tile shows a permanent label (Original / V1 / V2...). Hovering reveals a
small ⓘ button; clicking it opens a light popup with that version's prompt and
basic info (resolution, which version it came from). The selected tile drives
the next edit; the dock's prompt placeholder + Generate button echo the base.

Tiles take keyboard focus from Tab (never from a click, which would pull the
caret out of the prompt box): Space or Return picks one, and the context-menu
key (or a right-click) opens the same details popup the ⓘ does without
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
    QSize,
    Qt,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QPixmap
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
from ..core.i18n import tr
from ..core.resolution_labels import resolution_display_label
from .dock.style import BRAND_BLUE, FOCUS_RING
from .panel_helpers import main_window_for_dialog
from .version_details_popup import VersionDetailsPopup

VERSION_THUMB_PX = 56
_TILE_PX = VERSION_THUMB_PX + 4  # tile box incl. the 2px max selection border
# Smallest pointer/touch target we ship (WCAG 2.2 target size, minimum).
_MIN_TARGET_PX = 24

# Keyboard focus ring. Same hue as the selection ring (the one brand token that
# clears 3:1 on both themes) and told apart from it by line STYLE, not colour:
# focus is dashed, selected is solid, and a selected tile also carries the →
# badge. Carried by every stylesheet the tile swaps at runtime: setStyleSheet
# replaces the whole sheet, so a rule left out of one of them vanishes the
# moment the tile is selected.
_FOCUS_RING_QSS = f"QFrame:focus {{ border: 2px dashed {FOCUS_RING}; }}"

# Resting tile: subtle 1px border, transparent fill so the prompt container
# shows through. Selected tile: 2px brand-blue ring (mirrors the reference
# capture). Kept as two full stylesheets so toggling is a single setStyleSheet.
_TILE_STYLE = (
    "QFrame { background: rgba(0, 0, 0, 0.0);"
    " border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 4px; }"
    + _FOCUS_RING_QSS
)
_TILE_STYLE_SELECTED = (
    "QFrame { background: rgba(0, 0, 0, 0.0);"
    f" border: 2px solid {BRAND_BLUE}; border-radius: 4px; }}"
    + _FOCUS_RING_QSS
)

# Permanent caption ("Original" / "V1" / "V2" ...) on each tile's bottom edge.
_CAPTION_STYLE = (
    "QLabel { background: rgba(0, 0, 0, 0.55); color: rgba(255, 255, 255, 0.95);"
    " border: none; border-bottom-left-radius: 3px; border-bottom-right-radius: 3px;"
    " font-size: 9px; font-weight: bold; padding: 0 2px; }"
)

# Selected-state badge (top-right). A non-color cue so the selection is not
# conveyed by the blue ring alone (colour-blind safety, per the design system).
# An arrow says what selection does - the next edit goes FROM here, which is
# what the strip's "Start from" header promises.
_BASE_BADGE_STYLE = (
    f"QLabel {{ background: {BRAND_BLUE}; color: white; border: none;"
    " border-top-right-radius: 3px; border-bottom-left-radius: 3px;"
    " font-size: 9px; font-weight: bold; padding: 0 2px; }"
)

# Small hover-only info button (top-left), opens the details popup. The button
# box is _MIN_TARGET_PX square; the margin shrinks only what is painted, so a
# 20x20 chip sits in a target that meets the minimum. Kept at 4px because the
# unpainted band still opens the popup instead of selecting the tile.
_INFO_STYLE = (
    "QToolButton { background: rgba(0, 0, 0, 0.6); color: white; border: none;"
    " border-top-left-radius: 3px; border-bottom-right-radius: 3px;"
    " font-size: 11px; font-weight: bold; margin: 0px 4px 4px 0px; }"
    "QToolButton:hover { background: rgba(0, 0, 0, 0.85); }"
    f"QToolButton:focus {{ background: rgba(0, 0, 0, 0.85);"
    f" border: 2px solid {FOCUS_RING}; }}"
)

# "Start from" header above the row: a quiet hint that the strip is the picker
# for what the next edit builds on.
_HEADER_STYLE = (
    "QLabel { color: palette(text); font-size: 11px; background: transparent; }"
)

# Overflow chevron: a small floating button overlaid on the scroll edge. Dark
# semi-opaque so the glyph stays readable over a thumbnail.
_NAV_STYLE = (
    "QToolButton { background: rgba(0, 0, 0, 0.55); color: white;"
    " border: none; border-radius: 4px; font-size: 14px; font-weight: bold; }"
    "QToolButton:hover { background: rgba(0, 0, 0, 0.78); }"
    f"QToolButton:focus {{ background: rgba(0, 0, 0, 0.78);"
    f" border: 2px solid {FOCUS_RING}; }}"
)


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
    """A single strip thumbnail with a permanent caption and an ⓘ popup that
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
        self._hovered = False
        self.setFixedSize(_TILE_PX, _TILE_PX)
        self.setStyleSheet(_TILE_STYLE)
        self.setCursor(QtC.PointingHandCursor)
        # The tile IS the picker for what the next edit builds on, so it has to
        # be reachable and activatable without a mouse. TabFocus, never
        # StrongFocus: the golden path is "pick V2, then type the next edit",
        # and a click-focusable frame pulls the caret out of the prompt box, so
        # the characters typed straight after the pick go nowhere.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        label = tr("Original") if is_original else tr("V{n}").format(n=index)
        self._label = label
        self.setAccessibleName(label)
        self.setAccessibleDescription(self._details_text())

        # Thumbnail, inset by the 2px max border so the selected ring never
        # clips the image.
        self._pixmap_label = QLabel(self)
        self._pixmap_label.setGeometry(2, 2, VERSION_THUMB_PX, VERSION_THUMB_PX)
        self._pixmap_label.setAlignment(QtC.AlignCenter)
        if pixmap is not None and not pixmap.isNull():
            self._pixmap_label.setPixmap(
                pixmap.scaled(
                    QSize(VERSION_THUMB_PX, VERSION_THUMB_PX),
                    QtC.KeepAspectRatio,
                    QtC.SmoothTransformation,
                )
            )

        # Permanent caption: "Original" / "V1" / "V2"...
        cap = QLabel(label, self)
        cap.setStyleSheet(_CAPTION_STYLE)
        cap.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        cap.adjustSize()
        cap.move(2, VERSION_THUMB_PX + 2 - cap.height())
        self._caption = cap

        # Base badge (top-right), shown only when this tile is selected.
        self._base_badge = QLabel("→", self)  # →
        self._base_badge.setStyleSheet(_BASE_BADGE_STYLE)
        self._base_badge.adjustSize()
        self._base_badge.move(_TILE_PX - self._base_badge.width(), 0)
        self._base_badge.setVisible(False)

        # Hover-only ⓘ (top-left). Its own click opens the popup and, being a
        # child on top, never triggers tile selection. It stays out of the tab
        # chain on purpose: a hidden widget cannot hold focus, and the same
        # popup opens from the tile itself (contextMenuEvent).
        self._info_btn = QToolButton(self)
        self._info_btn.setText("ⓘ")  # ⓘ
        self._info_btn.setStyleSheet(_INFO_STYLE)
        self._info_btn.setCursor(QtC.PointingHandCursor)
        self._info_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._info_btn.setAccessibleName(tr("Version details"))
        self._info_btn.setToolTip(tr("Version details"))
        self._info_btn.setFixedSize(_MIN_TARGET_PX, _MIN_TARGET_PX)
        self._info_btn.move(0, 0)
        self._info_btn.setVisible(False)
        self._info_btn.clicked.connect(self._open_info)

    def _details_text(self) -> str:
        """The ⓘ popup's content on one line. The button is chrome that never
        takes focus, so this is the route a screen reader has to the version's
        prompt and lineage."""
        parts = [self._label]
        definition = resolution_display_label(self._meta.get("definition"))
        if self._is_original:
            parts.append(tr("clean source"))
        else:
            if definition:
                parts.append(definition)
            base_label = self._meta.get("base_label")
            if base_label:
                parts.append(tr("from {base}").format(base=base_label))
            if self._prompt:
                parts.append(self._prompt)
        return ", ".join(parts)

    def _update_overlays(self) -> None:
        """Reveal the corner affordances on hover or keyboard focus."""
        self._info_btn.setVisible(self._hovered or self.hasFocus())

    def enterEvent(self, event):  # noqa: N802
        self._hovered = True
        self._update_overlays()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hovered = False
        self._update_overlays()
        super().leaveEvent(event)

    def focusInEvent(self, event):  # noqa: N802
        self._update_overlays()
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802
        self._update_overlays()
        super().focusOutEvent(event)

    def _open_info(self) -> None:
        # Parent on the top-level window, not self: on macOS fullscreen a dialog
        # parented to a widget inside a (floating) dock can open in another Space.
        parent_window = main_window_for_dialog(self)
        dlg = VersionDetailsPopup(
            self._label,
            resolution_display_label(self._meta.get("definition")),
            self._meta.get("base_label"),
            self._prompt,
            self._is_original,
            parent_window,
        )
        dlg.exec()

    def _refresh_accessible_name(self) -> None:
        """Name the tile with its selection state."""
        suffix = " - " + tr("selected") if self._selected else ""
        self.setAccessibleName(self._label + suffix)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.setStyleSheet(_TILE_STYLE_SELECTED if selected else _TILE_STYLE)
        self._base_badge.setVisible(selected)
        self._refresh_accessible_name()

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly

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
        # Fired by a right-click AND by the keyboard menu key (Shift+F10), which
        # is how the details popup opens without the hover-only ⓘ. Reading the
        # details never changes the selection (see mousePressEvent).
        self._open_info()
        event.accept()


class VersionStrip(QWidget):
    """Version picker: a 'Start from' header + a thumbnail row (Original pinned)."""

    version_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tiles: list[_VersionTile] = []
        self._selected_index = 0
        self._readonly = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)

        # Quiet header so the strip's purpose reads at a glance.
        self._header = QLabel(tr("Start from"), self)
        self._header.setStyleSheet(_HEADER_STYLE)
        outer.addWidget(self._header)

        self._row_host = QWidget(self)
        self._row_host.setStyleSheet("background: transparent;")
        self._row = QHBoxLayout(self._row_host)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(6)

        # Separator between the pinned Original and the scrolling results.
        self._separator = QFrame(self._row_host)
        self._separator.setFrameShape(QtC.FrameVLine)
        self._separator.setFixedWidth(1)
        self._separator.setStyleSheet("QFrame { color: rgba(128, 128, 128, 0.4); }")
        self._separator.setVisible(False)

        # Scrolling results (generated versions only; Original stays pinned).
        self._gen_host = QWidget()
        self._gen_host.setStyleSheet("background: transparent;")
        self._gen_row = QHBoxLayout(self._gen_host)
        self._gen_row.setContentsMargins(0, 0, 0, 0)
        self._gen_row.setSpacing(6)
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
        self._scroll.setFixedHeight(_TILE_PX)
        self._scroll.setSizePolicy(QtC.SizePolicyExpanding, QtC.SizePolicyFixed)

        # Overflow chevrons overlaid on the scroll edges (jump to far end).
        self._left_btn = self._make_nav_btn("‹", tr("Jump to the first version"))  # ‹
        self._left_btn.clicked.connect(self._scroll_to_start)
        self._right_btn = self._make_nav_btn("›", tr("Jump to the latest version"))  # ›
        self._right_btn.clicked.connect(self._scroll_to_end)
        self._scroll.attach_chevrons(self._left_btn, self._right_btn)

        bar = self._scroll.horizontalScrollBar()
        bar.valueChanged.connect(lambda _v: self._update_nav())
        bar.rangeChanged.connect(lambda _a, _b: self._update_nav())

        # Smooth glide when a chevron jumps to an end (no instant teleport).
        self._scroll_anim = QPropertyAnimation(bar, b"value", self)
        self._scroll_anim.setDuration(240)
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
        self.set_selected(index)
        self._update_nav()
        return index

    def clear(self) -> None:
        """Empty the strip and hide it (a new lineage starts blank)."""
        self._clear_generated()
        self._remove_original()
        self._separator.setVisible(False)
        self._selected_index = 0
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
        return tr("Original") if index <= 0 else tr("V{n}").format(n=index)

    def count(self) -> int:
        return len(self._tiles)

    def set_readonly(self, readonly: bool) -> None:
        """Lock selection while a generation runs (scrolling stays allowed)."""
        self._readonly = readonly
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

    def _make_nav_btn(self, glyph: str, name: str) -> QToolButton:
        btn = QToolButton(self._scroll)
        btn.setText(glyph)
        btn.setCursor(QtC.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        btn.setAccessibleName(name)
        btn.setToolTip(name)
        btn.setFixedSize(_MIN_TARGET_PX, 40)
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
