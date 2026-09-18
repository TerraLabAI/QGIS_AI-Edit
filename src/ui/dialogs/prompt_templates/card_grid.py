"""Card grids that drop columns when the library window is narrow.

A library grid asks for up to three (or four) columns of cards that are at
least 200 px wide. At the window's 640 px minimum only one or two fit, and a
fixed column count pushed the last column past the right edge, where the
page has no horizontal scroll. The reflow watches the grid's host and
re-places the cards in as many columns as fit, in reading order.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QObject, Qt
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

# Narrowest a card may get (the cards' own minimum width), px.
CARD_MIN_WIDTH = 200

# Object name of the empty cells that pad a grid's last row. QGridLayout adds
# its spacing only between columns that hold something, so a row of one card
# spread its two empty columns' share without their gaps: a lone card came
# out 9 px wider than the cards of a full row (the Forestry section, a single
# Favorite, a two-result search).
_FILLER_NAME = "cardGridFiller"


def fitting_columns(width: int, max_columns: int, card_min: int, gap: int) -> int:
    """How many cards of ``card_min`` px fit in ``width`` px with ``gap``
    between them, clamped to 1..``max_columns``."""
    if width <= 0:
        return max(1, max_columns)
    fit = (width + gap) // max(1, card_min + gap)
    return max(1, min(max_columns, fit))


class CardGridReflow(QObject):
    """Event filter on a grid host that keeps its cards within its width."""

    def __init__(self, host: QWidget, grid: QGridLayout, max_columns: int):
        super().__init__(host)
        self.setObjectName("cardGridReflow")
        self._host = host
        self._grid = grid
        self._max_columns = max(1, int(max_columns))
        self._columns = self._max_columns
        # An explicit minimum width stops the grid from forcing the page to
        # the width of every column; the reflow keeps the cards inside it.
        host.setMinimumWidth(CARD_MIN_WIDTH)
        host.installEventFilter(self)

    @property
    def columns(self) -> int:
        return self._columns

    def _drop_fillers(self) -> None:
        grid = self._grid
        for i in range(grid.count() - 1, -1, -1):
            item = grid.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None and widget.objectName() == _FILLER_NAME:
                grid.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()

    def pad_last_row(self) -> None:
        """Fill the empty cells of the last row with zero-height placeholders,
        so every column carries its gap and a short row's cards keep the width
        of a full row's. Run after any card is placed."""
        self._drop_fillers()
        grid = self._grid
        cards = 0
        for i in range(grid.count()):
            item = grid.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None and widget.objectName() != _FILLER_NAME:
                cards += 1
        columns = self._columns
        if cards == 0 or cards % columns == 0:
            return
        row = (cards - 1) // columns
        for col in range(cards % columns, columns):
            filler = QWidget(self._host)
            filler.setObjectName(_FILLER_NAME)
            filler.setFixedHeight(0)
            filler.setMinimumWidth(0)
            filler.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            grid.addWidget(filler, row, col)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt signature
        if obj is self._host and event.type() == QEvent.Type.Resize:
            self.reflow(event.size().width())
        return False

    def _card_min_width(self) -> int:
        widest = CARD_MIN_WIDTH
        for i in range(self._grid.count()):
            item = self._grid.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widest = max(widest, widget.minimumWidth())
        return widest

    def reflow(self, width: int) -> None:
        if width <= 0:
            return
        columns = fitting_columns(
            width, self._max_columns, self._card_min_width(),
            self._grid.horizontalSpacing(),
        )
        if columns == self._columns:
            return
        self._columns = columns
        self._drop_fillers()
        grid = self._grid
        placed = []
        for i in range(grid.count()):
            item = grid.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            row, col, _rs, _cs = grid.getItemPosition(i)
            placed.append((row, col, widget))
        placed.sort(key=lambda entry: (entry[0], entry[1]))
        for _row, _col, widget in placed:
            grid.removeWidget(widget)
        for c in range(self._max_columns):
            grid.setColumnStretch(c, 1 if c < columns else 0)
        for idx, (_row, _col, widget) in enumerate(placed):
            row, col = divmod(idx, columns)
            grid.addWidget(widget, row, col)
        self.pad_last_row()


def add_row_height_filler(card_box: QVBoxLayout, footer: QWidget) -> None:
    """Let a card grow to its grid row's height without stretching its text.
    The footer keeps its own height and a spacer under it takes the rest.
    The spacer grows but never expands: an expanding one would mark the whole
    grid as vertically expanding, and the page's closing stretch would then
    share its space with the cards (a two-result search came out 120 px
    taller than its cards)."""
    footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    card_box.addItem(QSpacerItem(
        0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred))


def _grid_reflow(grid: QGridLayout) -> CardGridReflow | None:
    host = grid.parentWidget()
    if host is None:
        return None
    return host.findChild(CardGridReflow, "cardGridReflow")


def settle_card_grid(grid: QGridLayout) -> None:
    """Pad the grid's last row after cards were added to it (see
    ``CardGridReflow.pad_last_row``). A grid without a reflow is left alone."""
    reflow = _grid_reflow(grid)
    if reflow is not None:
        reflow.pad_last_row()


def card_grid_columns(grid: QGridLayout, default: int) -> int:
    """The column count a grid shows now (``default`` when it has no reflow),
    so cards appended later land in the same pattern."""
    reflow = _grid_reflow(grid)
    return reflow.columns if reflow is not None else default


# Arrow key -> (row step, column step) inside a card grid.
_ARROW_STEPS = {
    Qt.Key.Key_Left: (0, -1),
    Qt.Key.Key_Right: (0, 1),
    Qt.Key.Key_Up: (-1, 0),
    Qt.Key.Key_Down: (1, 0),
}


def _is_filler(widget) -> bool:
    return widget is None or widget.objectName() == _FILLER_NAME


def _scroll_area_of(widget: QWidget) -> QScrollArea | None:
    parent = widget.parentWidget()
    while parent is not None and not isinstance(parent, QScrollArea):
        parent = parent.parentWidget()
    return parent


def _focus_card(target: QWidget) -> None:
    target.setFocus(Qt.FocusReason.TabFocusReason)
    scroll = _scroll_area_of(target)
    if scroll is not None:
        scroll.ensureWidgetVisible(target, 0, 24)


def _card_across_grids(card: QWidget, down: bool) -> QWidget | None:
    """The card in the next (or previous) row of the whole page, crossing
    section and month boundaries, nearest to ``card`` horizontally. The
    per-grid step stops at a section's last row otherwise."""
    scroll = _scroll_area_of(card)
    content = scroll.widget() if scroll is not None else None
    if content is None:
        return None
    here = card.mapTo(content, card.rect().center())
    rows: dict[int, list[tuple[int, QWidget]]] = {}
    for other in content.findChildren(QFrame, "card"):
        if other is card or not other.isVisibleTo(content):
            continue
        if other.focusPolicy() == Qt.FocusPolicy.NoFocus:
            continue
        top = other.mapTo(content, other.rect().topLeft()).y()
        centre = other.mapTo(content, other.rect().center())
        if (down and centre.y() > here.y()) or (not down and centre.y() < here.y()):
            rows.setdefault(top, []).append((abs(centre.x() - here.x()), other))
    if not rows:
        return None
    row_top = min(rows) if down else max(rows)
    return min(rows[row_top], key=lambda entry: entry[0])[1]


def focus_neighbour_card(card: QWidget, key) -> bool:
    """Move the keyboard focus from ``card`` to the card beside it in its
    grid (arrow keys, like ChatGPT's GPT store), scrolling it into view.
    Left and Right run on through the row ends in reading order; Up and Down
    cross into the section above or below. Returns False when the key is not
    an arrow or there is no card that way, so the caller can let the key
    through."""
    step = _ARROW_STEPS.get(key)
    host = card.parentWidget()
    grid = host.layout() if host is not None else None
    if step is None or not isinstance(grid, QGridLayout):
        return False
    index = grid.indexOf(card)
    if index < 0:
        return False
    row, col, _rs, _cs = grid.getItemPosition(index)
    columns = max(1, card_grid_columns(grid, grid.columnCount()))
    if step[0] == 0:
        flat = row * columns + col + step[1]
        if flat < 0:
            return False
        row, col = divmod(flat, columns)
    else:
        row += step[0]
    target = None
    if row >= 0:
        item = grid.itemAtPosition(row, col)
        target = item.widget() if item is not None else None
        if _is_filler(target) and step[0] > 0:
            # Down from a full row onto a shorter last row: its last card.
            target = None
            for c in range(col - 1, -1, -1):
                item = grid.itemAtPosition(row, c)
                widget = item.widget() if item is not None else None
                if not _is_filler(widget):
                    target = widget
                    break
    if _is_filler(target):
        target = None
    if target is None and step[0] != 0:
        target = _card_across_grids(card, down=step[0] > 0)
    if target is None:
        return False
    _focus_card(target)
    return True


def focus_out_of_grid(card: QWidget, key) -> bool:
    """Arrow keys at the grid's edge: Up from the top row goes back to the
    search field, Left from the first card to the open rail row (the GPT
    store's order: search, then the sidebar, then the grid). The window
    provides the two moves; a host without them lets the key through."""
    window = card.window()
    if key == Qt.Key.Key_Up:
        move = getattr(window, "_focus_search", None)
    elif key == Qt.Key.Key_Left:
        move = getattr(window, "_focus_rail", None)
    else:
        return False
    if move is None:
        return False
    move()
    return True
