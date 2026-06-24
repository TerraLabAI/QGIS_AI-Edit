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

This widget is a pure view: it owns the thumbnails and the selection state, and
emits ``version_selected(index)``. The plugin owns the authoritative mapping
from strip index to the underlying layer / request id. Index 0 is the Original.
"""
from __future__ import annotations

import os

from qgis.PyQt.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import qt_compat as QtC
from ..core.i18n import tr
from ..core.resolution_labels import resolution_display_label

_ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "resources", "icons"
)
_COPY_SVG = os.path.join(_ICONS_DIR, "copy.svg")

BRAND_BLUE = "#1e88e5"
THUMB_PX = 56
_TILE_PX = THUMB_PX + 4  # tile box incl. the 2px max selection border

# Resting tile: subtle 1px border, transparent fill so the prompt container
# shows through. Selected tile: 2px brand-blue ring (mirrors the reference
# capture). Kept as two full stylesheets so toggling is a single setStyleSheet.
_TILE_STYLE = (
    "QFrame { background: rgba(0, 0, 0, 0.0);"
    " border: 1px solid rgba(128, 128, 128, 0.3); border-radius: 4px; }"
)
_TILE_STYLE_SELECTED = (
    "QFrame { background: rgba(0, 0, 0, 0.0);"
    f" border: 2px solid {BRAND_BLUE}; border-radius: 4px; }}"
)

# Permanent caption ("Original" / "V1" / "V2" ...) on each tile's bottom edge.
_CAPTION_STYLE = (
    "QLabel { background: rgba(0, 0, 0, 0.55); color: rgba(255, 255, 255, 0.95);"
    " border: none; border-bottom-left-radius: 3px; border-bottom-right-radius: 3px;"
    " font-size: 9px; font-weight: bold; padding: 0 2px; }"
)

# Selected-state check badge. A non-color cue so the selection is not conveyed
# by the blue ring alone (colour-blind safety, per the design system).
_CHECK_STYLE = (
    f"QLabel {{ background: {BRAND_BLUE}; color: white; border: none;"
    " border-top-right-radius: 3px; border-bottom-left-radius: 3px;"
    " font-size: 9px; font-weight: bold; padding: 0 2px; }"
)

# Small hover-only info button (top-left), opens the details popup.
_INFO_STYLE = (
    "QToolButton { background: rgba(0, 0, 0, 0.6); color: white; border: none;"
    " border-top-left-radius: 3px; border-bottom-right-radius: 3px;"
    " font-size: 10px; font-weight: bold; padding: 0 3px; }"
    "QToolButton:hover { background: rgba(0, 0, 0, 0.85); }"
)

# "Start from" header above the row: a quiet hint that the strip is the picker
# for what the next edit builds on.
_HEADER_STYLE = (
    "QLabel { color: palette(text); font-size: 11px; background: transparent; }"
)

# Version-details popup: small uppercase section label, a boxed prompt, and a
# flat one-click copy. Mirrors the generation detail dialog so the two read as
# one design language.
_SECTION_STYLE = (
    "color: rgba(128,128,128,0.95); font-size: 10px; font-weight: 700;"
    " letter-spacing: 1.2px; background: transparent; border: none;"
)
_META_STYLE = (
    "color: palette(text); font-size: 13px; font-weight: 700;"
    " background: transparent; border: none;"
)
_PROMPT_BOX_STYLE = (
    "QLabel { color: palette(text); font-size: 12px;"
    " background: rgba(128,128,128,0.05);"
    " border: 1px solid rgba(128,128,128,0.15);"
    " border-radius: 6px; padding: 10px 12px; }"
)
_NOTE_STYLE = (
    "color: rgba(128,128,128,0.95); font-size: 12px;"
    " background: transparent; border: none;"
)
_COPY_BTN = (
    "QPushButton { background: transparent; border: none;"
    " color: rgba(128,128,128,0.95); font-size: 11px; font-weight: 600;"
    " padding: 1px 6px; border-radius: 4px; }"
    "QPushButton:hover { background: rgba(128,128,128,0.14); color: palette(text); }"
)


class _VersionInfoPopup(QDialog):
    """Light popup opened from a tile's ⓘ: the prompt + basic info (resolution,
    lineage). No image: the canvas already shows the version full-size."""

    def __init__(
        self,
        label: str,
        definition: str | None,
        base_label: str | None,
        prompt: str,
        is_original: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("Version details"))
        self.setModal(True)
        self._prompt = prompt or ""
        self._copy_btn = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)

        # Meta line: "V1 · 1K · from Original". Version label bold, lineage in
        # brand blue so the eye reads "what this is" before the prompt.
        parts = [label]
        if not is_original and definition:
            parts.append(definition)
        meta_html = " · ".join(parts)
        if not is_original and base_label:
            from_label = tr("from {base}").format(base=base_label)
            meta_html += f" · <span style='color:{BRAND_BLUE}; font-weight:600;'>{from_label}</span>"
        if is_original:
            meta_html += " · " + tr("clean source")
        meta = QLabel(meta_html, self)
        meta.setTextFormat(Qt.TextFormat.RichText)
        meta.setStyleSheet(_META_STYLE)
        layout.addWidget(meta)

        if self._prompt:
            # Header row: "PROMPT" + a tiny one-click Copy prompt button.
            head = QHBoxLayout()
            head.setContentsMargins(0, 0, 0, 0)
            head.setSpacing(6)
            section = QLabel(tr("Prompt").upper(), self)
            section.setStyleSheet(_SECTION_STYLE)
            head.addWidget(section)
            head.addStretch(1)
            self._copy_btn = QPushButton(tr("Copy prompt"), self)
            self._copy_btn.setIcon(QIcon(_COPY_SVG))
            self._copy_btn.setIconSize(QSize(13, 13))
            self._copy_btn.setStyleSheet(_COPY_BTN)
            self._copy_btn.setCursor(QtC.PointingHandCursor)
            self._copy_btn.setFlat(True)
            self._copy_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._copy_btn.clicked.connect(self._copy_prompt)
            head.addWidget(self._copy_btn)
            layout.addLayout(head)

            body = QLabel(self._prompt, self)
            body.setWordWrap(True)
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setStyleSheet(_PROMPT_BOX_STYLE)
            body.setMaximumWidth(360)
            layout.addWidget(body)
        else:
            # Original (or prompt-less) version: a quiet one-line note, no box.
            note = QLabel(
                tr("The original zone, before any AI edit.") if is_original
                else tr("(no prompt)"),
                self,
            )
            note.setWordWrap(True)
            note.setTextFormat(Qt.TextFormat.PlainText)
            note.setStyleSheet(_NOTE_STYLE)
            note.setMaximumWidth(360)
            layout.addWidget(note)

    def _copy_prompt(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._prompt)
        if self._copy_btn is not None:
            self._copy_btn.setText(tr("Copied"))
            QtC.safe_single_shot(1400, self._copy_btn, self._reset_copy_btn)

    def _reset_copy_btn(self) -> None:
        if self._copy_btn is not None:
            self._copy_btn.setText(tr("Copy prompt"))


# Overflow chevron: a small floating button overlaid on the scroll edge. Dark
# semi-opaque so the glyph stays readable over a thumbnail.
_NAV_STYLE = (
    "QToolButton { background: rgba(0, 0, 0, 0.55); color: white;"
    " border: none; border-radius: 4px; font-size: 14px; font-weight: bold; }"
    "QToolButton:hover { background: rgba(0, 0, 0, 0.78); }"
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
    """A single strip thumbnail with a permanent caption + hover ⓘ popup."""

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
        self.setFixedSize(_TILE_PX, _TILE_PX)
        self.setStyleSheet(_TILE_STYLE)
        self.setCursor(QtC.PointingHandCursor)

        label = tr("Original") if is_original else tr("V{n}").format(n=index)
        self._label = label
        self.setAccessibleName(label)

        # Thumbnail, inset by the 2px max border so the selected ring never
        # clips the image.
        self._pixmap_label = QLabel(self)
        self._pixmap_label.setGeometry(2, 2, THUMB_PX, THUMB_PX)
        self._pixmap_label.setAlignment(QtC.AlignCenter)
        if pixmap is not None and not pixmap.isNull():
            self._pixmap_label.setPixmap(
                pixmap.scaled(
                    QSize(THUMB_PX, THUMB_PX),
                    QtC.KeepAspectRatio,
                    QtC.SmoothTransformation,
                )
            )

        # Permanent caption: "Original" / "V1" / "V2"...
        cap = QLabel(label, self)
        cap.setStyleSheet(_CAPTION_STYLE)
        cap.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        cap.adjustSize()
        cap.move(2, THUMB_PX + 2 - cap.height())
        self._caption = cap

        # Check badge (top-right), shown only when this tile is selected.
        self._check = QLabel("✓", self)  # ✓
        self._check.setStyleSheet(_CHECK_STYLE)
        self._check.adjustSize()
        self._check.move(_TILE_PX - self._check.width(), 0)
        self._check.setVisible(False)

        # Hover-only ⓘ (top-left). Its own click opens the popup and, being a
        # child on top, never triggers tile selection.
        self._info_btn = QToolButton(self)
        self._info_btn.setText("ⓘ")  # ⓘ
        self._info_btn.setStyleSheet(_INFO_STYLE)
        self._info_btn.setCursor(QtC.PointingHandCursor)
        self._info_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._info_btn.adjustSize()
        self._info_btn.move(0, 0)
        self._info_btn.setVisible(False)
        self._info_btn.clicked.connect(self._open_info)

    def enterEvent(self, event):  # noqa: N802
        self._info_btn.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._info_btn.setVisible(False)
        super().leaveEvent(event)

    def _open_info(self) -> None:
        # Parent on the top-level window, not self: on macOS fullscreen a dialog
        # parented to a widget inside a (floating) dock can open in another Space.
        parent_window = self
        try:
            from qgis.utils import iface
            mw = iface.mainWindow() if iface is not None else None
            if mw is not None:
                parent_window = mw
        except Exception:  # nosec B110 - fall back to self on any failure.
            pass
        dlg = _VersionInfoPopup(
            self._label,
            resolution_display_label(self._meta.get("definition")),
            self._meta.get("base_label"),
            self._prompt,
            self._is_original,
            parent_window,
        )
        dlg.exec()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.setStyleSheet(_TILE_STYLE_SELECTED if selected else _TILE_STYLE)
        self._check.setVisible(selected)
        self.setAccessibleName(
            self._label + (" - " + tr("selected") if selected else "")
        )

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly

    def mousePressEvent(self, event):  # noqa: N802
        if not self._readonly:
            self.clicked.emit(self._index)
        super().mousePressEvent(event)


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
        self._left_btn = self._make_nav_btn("‹")  # ‹
        self._left_btn.clicked.connect(self._scroll_to_start)
        self._right_btn = self._make_nav_btn("›")  # ›
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

    def _make_nav_btn(self, glyph: str) -> QToolButton:
        btn = QToolButton(self._scroll)
        btn.setText(glyph)
        btn.setCursor(QtC.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setFixedSize(22, 40)
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
