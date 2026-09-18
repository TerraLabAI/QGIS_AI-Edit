"""Draw panel widget (the "Mark up" panel before 2026-09-17).

Self-contained QWidget that owns the Pencil/Line/Arrow/Circle tool tiles, the
colour dots, the stroke count with Undo and Clear all, and Done. Emits signals
the dock relays to the plugin orchestrator.

The screen reads top to bottom as the user works, the way a markup toolbar is
organised in Figma, Photoshop or Apple Markup:

1. the title and one short muted line, with the link to the guide's chapter;
2. one toolbar card: the tools, the colours under them, and the armed tool's
   one-line hint as the card's footer, so the hint belongs to the tool;
3. a status row that says what is on the map ("3 strokes") with Undo and
   Clear all next to the count they change;
4. Done, the panel's one way out.
"""
from __future__ import annotations

import html

from qgis.PyQt.QtCore import QEvent, QPointF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QColor,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
)
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ..dock import design_tokens as T
from ..dock.design_tokens import qcolor
from ..icons import icon_for, render_pixmap, widget_pixel_ratio
from ..onboarding_hint import (
    GUIDE_ANCHOR_MARKUP,
    GUIDE_LINK_HREF,
    HINT_MARKUP,
    build_guide_link_html,
    is_hint_dismissed,
    open_guide,
    register_hint_widget,
)
from ..panel_helpers import (
    add_picked_check,
    build_panel_header,
    is_dark_palette,
    main_window_for_dialog,
    make_color_dot_icon,
    make_custom_color_icon,
    make_hidpi_pixmap,
    make_notice_card,
    sync_picked_check,
)
from ..tools.markup_tools import MARKUP_DEFAULT_COLOR, markup_default_color

# The shipped swatches, named for the tooltip and the screen reader. They
# avoid the map palette (red / green / blue / gray) so the model never reads a
# mark as a class fill; one colour per hue family, maximally separated. The
# default (neon magenta) is first.
_PRESET_SWATCHES = (
    ("magenta", MARKUP_DEFAULT_COLOR),
    ("amber", (245, 158, 11)),
    ("yellow", (250, 204, 21)),
    ("cyan", (14, 165, 188)),
)


def _swatch_name(key: str) -> str:
    names = {
        "magenta": get_export_copy("widgets.markup_panel.color_name_magenta", tr("Magenta")),
        "amber": get_export_copy("widgets.markup_panel.color_name_amber", tr("Amber")),
        "yellow": get_export_copy("widgets.markup_panel.color_name_yellow", tr("Yellow")),
        "cyan": get_export_copy("widgets.markup_panel.color_name_cyan", tr("Cyan")),
        "default": get_export_copy("widgets.markup_panel.color_name_default", tr("Default")),
    }
    return names.get(key, key)


# A function, not a constant, so the server-tunable default is read when the
# colour row is built rather than at import. A served default that is not the
# shipped magenta is called "Default", never by a name it does not wear.
def _markup_color_presets() -> list[tuple[str, int, int, int]]:
    default = markup_default_color()
    first = "magenta" if tuple(default) == MARKUP_DEFAULT_COLOR else "default"
    presets = [(first, *default)]
    presets += [(key, *rgb) for key, rgb in _PRESET_SWATCHES[1:] if tuple(rgb) != tuple(default)]
    return presets


_TOOL_BUTTON_SIZE = 48
_TOOL_ICON_PX = 20
_HINT_GLYPH_PX = 14
_ACTION_GLYPH_PX = 14
_COLOR_DOT_PX = 22
_DOT_BTN_PX = _COLOR_DOT_PX + 6
# What one swatch costs the row: the dot's button plus the gap after it.
_DOT_STEP_PX = _DOT_BTN_PX + T.SPACE_TIGHT
# Under this the panel has not been laid out yet: the dock's own floor is
# 260 px, so no real width lands here.
_MEASURED_MIN_PX = 160
# The toolbar card's own padding: tighter than a settings card, because it
# holds control rows and one hint line, no prose.
_STRIP_MARGINS = (10, 10, 10, 8)
# The Done button keeps one width across the tool panels (References too).
_DONE_MIN_W = 96

# A tool is a tile on the inset step, with no border at rest: four hard boxes
# read as a form, the quiet grounds read as one toolbar. The border is there
# but transparent, so the picked border cannot shift the row by a pixel. The
# picked look is the shared one (hue tint, hue border, a check).
_TOOL_TILE_QSS = (
    f"QToolButton {{ background: {T.INSET}; border: 1px solid transparent;"
    f" border-radius: {T.RADIUS_CARD}px;"
    # 1px of side padding the focus rule can give back, so a focused tile
    # keeps its width and its label does not elide.
    f" padding: 5px 1px; color: {T.INK_2}; font-size: {T.FONT_HINT}px; font-weight: 500; }}"
    f"QToolButton:hover {{ background: {T.HOVER}; color: {T.INK}; }}"
    + T.picked_qss("QToolButton")
    + f"QToolButton:disabled {{ color: {T.INK_3}; }}"
    f"QToolButton:focus {{ border-color: {T.ACCENT_BORDER}; }}"
)
_DIVIDER_QSS = f"QFrame {{ background: {T.LINE}; border: none; }}"
# A colour swatch sits on nothing; the pointer and the keyboard get a round wash.
_DOT_BTN_QSS = (
    "QToolButton { background: transparent; border: 1px solid transparent; padding: 0px;"
    f" border-radius: {_DOT_BTN_PX // 2}px; }}"
    f"QToolButton:hover {{ background: {T.HOVER}; }}"
    f"QToolButton:focus {{ border-color: {T.ACCENT_BORDER}; }}"
)
# The armed tool's hint: the card's footer, muted, on nothing.
_TOOL_HINT_QSS = T.HINT_QSS + " padding: 0px;"
# The stroke count: body ink once something is drawn, muted while empty.
_COUNT_QSS = (
    f"font-size: {T.FONT_BODY}px; font-weight: 500; color: {T.INK};"
    " background: transparent; border: none;"
)
_COUNT_EMPTY_QSS = (
    f"font-size: {T.FONT_BODY}px; color: {T.INK_2};"
    " background: transparent; border: none;"
)
# Clear all is a quiet text action in the danger ink: a red outlined pill
# beside Done read as a second primary, and it is the rarer of the two.
_BTN_QUIET_DANGER_QSS = (
    "QPushButton { background: transparent; border: 1px solid transparent;"
    f" color: {T.RED_TEXT}; font-size: {T.FONT_BODY}px; font-weight: 500; padding: 3px 7px;"
    f" border-radius: {T.RADIUS_PILL_SMALL}px; }}"
    f"QPushButton:hover {{ background: {T.RED_TINT}; }}"
    f"QPushButton:pressed {{ background: {T.RED_TINT}; }}"
    f"QPushButton:focus {{ border-color: {T.ACCENT_BORDER}; }}"
    f"QPushButton:disabled {{ color: {T.INK_3}; }}"
)
# Cap on user-added custom swatches kept in the colour row (oldest dropped).
_MAX_CUSTOM_SWATCHES = 4


def _tool_hint(tool_key: str) -> str:
    """The armed tool's one line: what to do on the map, 8 words at most.
    The finer points (close a Line on its first point, Shift) live in the
    tile's tooltip."""
    hints = {
        "pencil": get_export_copy(
            "widgets.markup_panel.hint_pencil_short", tr("Drag on the map to draw freely.")
        ),
        "line": get_export_copy(
            "widgets.markup_panel.hint_line_short", tr("Click each point, double-click to finish.")
        ),
        "arrow": get_export_copy(
            "widgets.markup_panel.hint_arrow_short", tr("Drag from the start to the tip.")
        ),
        "circle": get_export_copy(
            "widgets.markup_panel.hint_circle_short", tr("Drag across the area to circle it.")
        ),
    }
    return hints.get(tool_key, hints["pencil"])


def _no_tool_hint() -> str:
    """The footer when no tool is armed (a Line left with Escape)."""
    return get_export_copy("widgets.markup_panel.hint_no_tool", tr("Pick a tool to draw."))


def _stroke_count_text(count: int) -> str:
    if count <= 0:
        return get_export_copy("widgets.markup_panel.count_none", tr("No strokes yet"))
    if count == 1:
        return tr("1 stroke")
    return tr("{n} strokes").format(n=count)


def _undo_shortcut_text() -> str:
    """The platform's own spelling of Undo (Ctrl+Z, or the Cmd glyph on macOS)."""
    try:
        return QKeySequence(QKeySequence.StandardKey.Undo).toString(
            QKeySequence.SequenceFormat.NativeText
        )
    except (AttributeError, TypeError):
        return "Ctrl+Z"


def _make_tool_icon(shape: str, color: QColor) -> QIcon:
    """The Arrow and Circle glyphs, which the shared painter does not carry.

    Drawn in the shared painter's 20 px box at its pen width, inside the same
    3.5 to 16.5 band, so all four tiles read at one optical size."""
    size = _TOOL_ICON_PX
    pm = make_hidpi_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(1.6)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if shape == "arrow":
        p.drawLine(QPointF(4.0, 16.0), QPointF(15.5, 4.5))
        p.drawLine(QPointF(15.5, 4.5), QPointF(15.5, 10.5))
        p.drawLine(QPointF(15.5, 4.5), QPointF(9.5, 4.5))
    elif shape == "circle":
        p.drawEllipse(QPointF(size / 2, size / 2), 6.5, 6.5)
    p.end()
    return QIcon(pm)


# The shared glyph set carries a pencil and a polyline; the arrow and the
# circle are drawn above in the same stroke.
_SHARED_TOOL_GLYPHS = {"pencil": "pencil", "line": "polyline"}


def _tool_tile_icon(widget: QWidget, shape: str) -> QIcon:
    """A tool glyph in the muted ink, and in the picked hue's ink while armed,
    so the glyph, the tint and the border of a picked tile all say one thing."""
    rest, on = qcolor(T.INK_2), qcolor(T.category_ink(T.PICKED_HUE))
    glyph = _SHARED_TOOL_GLYPHS.get(shape)
    if glyph is None:
        icon = QIcon()
        for color, state in ((rest, QIcon.State.Off), (on, QIcon.State.On)):
            icon.addPixmap(_make_tool_icon(shape, color).pixmap(_TOOL_ICON_PX, _TOOL_ICON_PX),
                           QIcon.Mode.Normal, state)
        return icon
    ratio = widget_pixel_ratio(widget)
    icon = QIcon(icon_for(widget, glyph, _TOOL_ICON_PX, rest))
    icon.addPixmap(render_pixmap(glyph, on, _TOOL_ICON_PX, ratio), QIcon.Mode.Normal, QIcon.State.On)
    return icon


def _tool_hint_glyph(widget: QWidget, shape: str):
    """The armed tool's glyph for the hint footer, in the muted ink."""
    glyph = _SHARED_TOOL_GLYPHS.get(shape)
    if glyph is None:
        return _make_tool_icon(shape, qcolor(T.INK_2)).pixmap(_HINT_GLYPH_PX, _HINT_GLYPH_PX)
    return render_pixmap(glyph, qcolor(T.INK_2), _HINT_GLYPH_PX, widget_pixel_ratio(widget))


class MarkupPanel(QWidget):
    """Tool panel: pencil / line / arrow / circle drawing on the canvas.

    Strokes land in a memory layer (owned by MarkupLayerManager) that the
    CanvasExporter renders into the PNG sent to the AI.
    """

    tool_changed = pyqtSignal(str)        # 'pencil' | 'line' | 'arrow' | 'circle'
    color_changed = pyqtSignal(QColor)
    clear_clicked = pyqtSignal()
    # Undo the last stroke. The dock relays it to the plugin's undo; until
    # something listens the Undo button stays hidden rather than dead.
    undo_clicked = pyqtSignal()
    done_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(*markup_default_color())
        self._annotation_count = 0
        self._has_zone = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_OUTER)

        # Title and its one muted line: what the panel does, then the link to
        # the guide's chapter on the same line. No close glyph on the header:
        # Done is this panel's one way out (owner call 2026-09-17); Escape
        # still works (the dock owns that shortcut).
        head = QWidget(self)
        head_col = QVBoxLayout(head)
        head_col.setContentsMargins(0, 0, 0, 0)
        head_col.setSpacing(2)
        # "Draw", not "Mark up" (Yvann, 2026-09-17).
        head_col.addWidget(build_panel_header(
            get_export_copy("widgets.markup_panel.header_title_draw", tr("Draw")),
            show_close=False,
        ))
        # One short line (the copy rule): what the marks are for. That they
        # are removed from the result is said on Done, where it matters.
        self._intro_text = get_export_copy(
            "widgets.markup_panel.intro_short",
            tr("Sketch where the AI should act."),
        )
        # The link says "example" rather than "how it works" on purpose: what
        # sells the feature is the pink outline turning into trees.
        self._intro_link = get_export_copy("widgets.markup_panel.markup_hint_link", tr("See an example"))
        self._intro_label = QLabel(head)
        self._intro_label.setWordWrap(True)
        self._intro_label.setIndent(0)
        self._intro_label.setStyleSheet(T.HINT_QSS)
        self._intro_label.setTextFormat(QtC.RichText)
        # The plugin resolves and opens the address itself (telemetry rides
        # along), so Qt must not follow the sentinel href on its own.
        self._intro_label.setOpenExternalLinks(False)
        # Keyboard too: Tab reaches "See an example" and Enter opens it, as
        # on the References panel's line.
        self._intro_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._intro_label.linkActivated.connect(self._on_guide_link)
        register_hint_widget(self._intro_label, HINT_MARKUP)
        head_col.addWidget(self._intro_label)
        layout.addWidget(head)
        self._apply_intro()

        # No zone yet: said once, above the tools, in one line. Tools stay
        # enabled so the user can sketch first and draw the zone after.
        self._no_zone_hint = make_notice_card(
            get_export_copy(
                "widgets.markup_panel.no_zone_inside",
                tr("Strokes count only inside your zone."),
            ),
            glyph="polygon",
        )
        layout.addWidget(self._no_zone_hint)

        # One toolbar card: the tools, the colours under them, and the armed
        # tool's hint as its footer.
        strip = QFrame(self)
        strip.setObjectName("card")
        strip.setAttribute(QtC.WA_StyledBackground, True)
        strip.setStyleSheet(T.CARD_QSS)
        # Fixed height: the card is never the piece that takes the panel's
        # spare vertical space.
        strip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        strip_col = QVBoxLayout(strip)
        strip_col.setContentsMargins(*_STRIP_MARGINS)
        strip_col.setSpacing(T.SPACE_OUTER)
        tool_row = QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        tool_row.setSpacing(T.SPACE_CARD)
        strip_col.addLayout(tool_row)

        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons: dict[str, QToolButton] = {}

        # Line sits second, next to Pencil: both are "draw a path" tools,
        # while Arrow and Circle are stamps (spec section 4).
        tool_specs = [
            (
                "pencil",
                get_export_copy("widgets.markup_panel.tool_pencil_label", tr("Pencil")),
                get_export_copy("widgets.markup_panel.tool_pencil_tooltip", tr("Freehand stroke")),
            ),
            (
                "line",
                get_export_copy("widgets.markup_panel.tool_line_label", tr("Line")),
                get_export_copy(
                    "widgets.markup_panel.tool_line_tooltip_shift",
                    tr("Straight lines. Click the first point to close, Shift keeps 45 degree angles."),
                ),
            ),
            (
                "arrow",
                get_export_copy("widgets.markup_panel.tool_arrow_label", tr("Arrow")),
                get_export_copy(
                    "widgets.markup_panel.tool_arrow_tooltip_shift",
                    tr("Drag from the start to the tip. Shift keeps 45 degree angles."),
                ),
            ),
            (
                "circle",
                get_export_copy("widgets.markup_panel.tool_circle_label", tr("Circle")),
                get_export_copy(
                    "widgets.markup_panel.tool_circle_tooltip_shift",
                    tr("Drag across an area. Shift draws a true circle."),
                ),
            ),
        ]

        # The full label of each tile, kept so a narrow dock can elide the
        # button text and still name the tool in the tooltip.
        self._tool_labels: dict[str, str] = {}
        for key, label, tooltip in tool_specs:
            btn = QToolButton(strip)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setText(label)
            # Name first, then what it does: the tile's own label can be
            # elided on a narrow dock, so the tooltip has to carry it whole.
            btn.setToolTip(f"{label}\n{tooltip}")
            btn.setAccessibleName(label)
            btn.setAccessibleDescription(tooltip)
            btn.setIcon(_tool_tile_icon(self, key))
            btn.setIconSize(QSize(_TOOL_ICON_PX, _TOOL_ICON_PX))
            btn.setCheckable(True)
            btn.setCursor(QtC.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.setFixedHeight(_TOOL_BUTTON_SIZE)
            btn.setMinimumWidth(_TOOL_BUTTON_SIZE)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(_TOOL_TILE_QSS)
            add_picked_check(btn)
            btn.setProperty("markup_tool_key", key)
            btn.toggled.connect(
                lambda checked, k=key: self._on_tool_toggled(k, checked)
            )
            self._tool_group.addButton(btn)
            # Arrow keys, Home and End walk the row and arm the tile they
            # land on, as in any toolbar radio group.
            btn.installEventFilter(self)
            self._tool_buttons[key] = btn
            self._tool_labels[key] = label
            tool_row.addWidget(btn, 1)

        # Block signals: activate() emits tool_changed for us when the panel
        # becomes visible.
        self._tool_buttons["pencil"].blockSignals(True)
        self._tool_buttons["pencil"].setChecked(True)
        self._tool_buttons["pencil"].blockSignals(False)

        # Colours: the dots right under the tools, starting on the first
        # tile's edge, with no row label (a row of dots and a "+" says what
        # it is, as in every markup toolbar). The dots sit in a grid that
        # reflows onto a second line rather than pushing the dock wider.
        self._colors_box = QWidget(strip)
        # Ignored width: the box takes the card's width and never asks for
        # more, so a full swatch row cannot raise the dock's minimum width.
        self._colors_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._color_grid = QGridLayout(self._colors_box)
        self._color_grid.setContentsMargins(0, 0, 0, 0)
        self._color_grid.setHorizontalSpacing(T.SPACE_TIGHT)
        self._color_grid.setVerticalSpacing(T.SPACE_TIGHT)
        strip_col.addWidget(self._colors_box)
        self._strip = strip
        # The dots in display order (presets, then custom, then the "+"),
        # which is what the grid is refilled from on every reflow.
        self._color_order: list[QToolButton] = []
        self._color_columns = 0

        self._color_btns: dict[tuple[int, int, int], QToolButton] = {}
        # Custom colours the user picks via "+", oldest first, so the oldest
        # is dropped once the row is full and a re-pick moves to the end.
        self._custom_color_keys: list[tuple[int, int, int]] = []

        for name_key, r, g, b in _markup_color_presets():
            color = QColor(r, g, b)
            btn = self._make_swatch(color, _swatch_name(name_key))
            self._color_btns[(r, g, b)] = btn
            self._color_order.append(btn)

        # Custom colour button (+)
        self._custom_color_btn = QToolButton(self._colors_box)
        self._custom_color_btn.setCursor(QtC.PointingHandCursor)
        self._custom_color_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._custom_color_btn.setFixedSize(_DOT_BTN_PX, _DOT_BTN_PX)
        self._custom_color_btn.setIconSize(QSize(_COLOR_DOT_PX, _COLOR_DOT_PX))
        self._custom_color_btn.setStyleSheet(_DOT_BTN_QSS)
        self._custom_color_btn.setIcon(make_custom_color_icon(
            is_dark=is_dark_palette(self), dot_px=_COLOR_DOT_PX
        ))
        _custom_color_text = get_export_copy("widgets.markup_panel.custom_color_label", tr("Custom color..."))
        self._custom_color_btn.setToolTip(_custom_color_text)
        self._custom_color_btn.setAccessibleName(_custom_color_text)
        self._custom_color_btn.clicked.connect(self._on_custom_color_clicked)
        self._custom_color_btn.installEventFilter(self)
        self._color_order.append(self._custom_color_btn)
        self._relayout_colors(force=True)

        # The card's footer: the armed tool's glyph and its one line, under a
        # hairline, so the hint reads as part of the toolbar and changes with
        # the tile the user just picked.
        divider = QFrame(strip)
        divider.setFixedHeight(1)
        divider.setAttribute(QtC.WA_StyledBackground, True)
        divider.setStyleSheet(_DIVIDER_QSS)
        strip_col.addWidget(divider)
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(2, 0, 0, 0)
        hint_row.setSpacing(T.SPACE_CARD + 2)
        self._hint_glyph = QLabel(strip)
        self._hint_glyph.setFixedSize(_HINT_GLYPH_PX, _HINT_GLYPH_PX)
        self._hint_glyph.setStyleSheet("background: transparent; border: none;")
        # Hidden with no tool armed, but its slot stays: "Pick a tool to
        # draw." used to jump 20 px left of where every tool hint starts.
        glyph_policy = self._hint_glyph.sizePolicy()
        glyph_policy.setRetainSizeWhenHidden(True)
        self._hint_glyph.setSizePolicy(glyph_policy)
        hint_row.addWidget(self._hint_glyph, 0, Qt.AlignmentFlag.AlignTop)
        self._status_label = QLabel("", strip)
        self._status_label.setWordWrap(True)
        self._status_label.setIndent(0)
        self._status_label.setStyleSheet(_TOOL_HINT_QSS)
        hint_row.addWidget(self._status_label, 1)
        strip_col.addLayout(hint_row)
        layout.addWidget(strip)

        # The status row: what is drawn, and the two actions that change it.
        status_row = QHBoxLayout()
        status_row.setContentsMargins(2, 0, 0, 0)
        status_row.setSpacing(T.SPACE_TIGHT)
        self._count_label = QLabel("")
        self._count_label.setIndent(0)
        # Ignored width: the count takes what Undo and Clear all leave and
        # elides (full words in the tooltip), so a long translation cannot
        # push the dock past its 260 px floor.
        self._count_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._count_text = ""
        # Elide on the label's own resize: the panel's resize arrives before
        # the row has handed the label its new width.
        self._count_label.installEventFilter(self)
        status_row.addWidget(self._count_label, 1, Qt.AlignmentFlag.AlignVCenter)

        shortcut = _undo_shortcut_text()
        self._undo_btn = QPushButton(get_export_copy("widgets.markup_panel.undo_button", tr("Undo")))
        self._undo_btn.setStyleSheet(T.BTN_QUIET_QSS)
        self._undo_btn.setCursor(QtC.PointingHandCursor)
        self._undo_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._undo_btn.setIcon(icon_for(
            self, "undo", _ACTION_GLYPH_PX, qcolor(T.INK_2), disabled_color=qcolor(T.INK_3)
        ))
        self._undo_btn.setIconSize(QSize(_ACTION_GLYPH_PX, _ACTION_GLYPH_PX))
        undo_tip = tr("Undo the last stroke ({shortcut})").format(shortcut=shortcut)
        self._undo_btn.setToolTip(undo_tip)
        self._undo_btn.setAccessibleName(undo_tip)
        self._undo_btn.clicked.connect(self.undo_clicked.emit)
        status_row.addWidget(self._undo_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._clear_btn = QPushButton(get_export_copy("widgets.markup_panel.clear_all_button", tr("Clear all")))
        self._clear_btn.setCursor(QtC.PointingHandCursor)
        self._clear_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._clear_btn.setEnabled(False)
        self._clear_btn.setStyleSheet(_BTN_QUIET_DANGER_QSS)
        self._clear_btn.setIcon(icon_for(
            self, "trash", _ACTION_GLYPH_PX, qcolor(T.RED_TEXT), disabled_color=qcolor(T.INK_3)
        ))
        self._clear_btn.setIconSize(QSize(_ACTION_GLYPH_PX, _ACTION_GLYPH_PX))
        # Said up front: Undo cannot bring a cleared drawing back.
        clear_tip = get_export_copy("widgets.markup_panel.clear_all_tooltip_final",
                                    tr("Remove every stroke. Undo cannot bring them back."))
        self._clear_btn.setToolTip(clear_tip)
        self._clear_btn.clicked.connect(self.clear_clicked.emit)
        status_row.addWidget(self._clear_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        # The row keeps the actions' height while they are hidden, so Done
        # does not jump down a few pixels when the first stroke lands.
        self._count_label.setMinimumHeight(self._clear_btn.sizeHint().height())
        layout.addLayout(status_row)

        # Done alone at the right, the same place and width as References.
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(T.SPACE_CARD)
        action_row.addStretch()
        self._done_btn = QPushButton(get_export_copy("widgets.markup_panel.done_button", tr("Done")))
        # The panel's one primary: the strokes are kept and the panel closes.
        self._done_btn.setStyleSheet(T.BTN_PRIMARY_QSS)
        self._done_btn.setCursor(QtC.PointingHandCursor)
        self._done_btn.setMinimumWidth(_DONE_MIN_W)
        self._done_btn.clicked.connect(self.done_clicked.emit)
        action_row.addWidget(self._done_btn)
        layout.addLayout(action_row)
        layout.addStretch()

        # Initial state: no zone (the dock refreshes us as soon as one is
        # drawn), nothing drawn.
        self._no_zone_hint.setVisible(True)
        self._update_color_indicators()
        self._refresh_status()
        self._fit_labels()

        # Esc means Done, but the dock owns the Escape shortcut and emits
        # done_clicked for us: a second Escape shortcut here made the key
        # ambiguous and neither fired.

    # -- public API ------------------------------------------------------

    def get_color(self) -> QColor:
        return QColor(self._color)

    def set_annotation_count(self, count: int) -> None:
        """Update the stroke count, Undo and Clear all."""
        self._annotation_count = max(0, int(count))
        self._refresh_status()

    def annotation_count(self) -> int:
        """The dock-side copy of MarkupLayerManager's authoritative count
        (relayed in through set_annotation_count). Read by the prompt tip."""
        return self._annotation_count

    def set_zone_present(self, has_zone: bool) -> None:
        """Track whether a zone exists. Tools stay enabled either way so
        the user can sketch first and draw the zone after.
        """
        self._has_zone = has_zone
        self._no_zone_hint.setVisible(not has_zone)
        self._refresh_status()

    def uncheck_tool(self, tool_key: str) -> None:
        """External sync: uncheck a tool button without emitting tool_changed.

        Called when the underlying map tool self-deactivates out from under
        the panel (Line's two-stage Escape calls canvas.unsetMapTool(self)
        directly, bypassing the button click path entirely) so the button
        reflects that no drawing tool is armed on the canvas anymore. A no-op
        if the button is already unchecked or the key is unknown.

        An exclusive QButtonGroup refuses to drop its last checked button:
        ``setChecked(False)`` alone is silently reverted by Qt. Toggling
        exclusivity off for the single call is the documented way to reach
        "nothing checked" and back.
        """
        btn = self._tool_buttons.get(tool_key)
        if btn is None or not btn.isChecked():
            return
        btn.blockSignals(True)
        self._tool_group.setExclusive(False)
        try:
            btn.setChecked(False)
        finally:
            self._tool_group.setExclusive(True)
            btn.blockSignals(False)
        # The corner check follows toggled, which was blocked above.
        sync_picked_check(btn)
        self._refresh_status()

    def activate(self) -> None:
        """Re-arm the panel when it becomes visible: reset to the default
        tool (Pencil) and emit it.

        Vectorize's own activate() wipes its setup back to Step 1 on every
        open, so the two tool panels match on "start fresh" rather than
        "remember the last pick" (Yvann, 2026-09-17). The colour is kept, as
        in every drawing app: it is a preference, not a step.
        """
        pencil = self._tool_buttons["pencil"]
        pencil.blockSignals(True)
        pencil.setChecked(True)
        pencil.blockSignals(False)
        # The corner checks follow toggled, which was blocked above: without
        # this Pencil reopened tinted but with no check.
        for btn in self._tool_buttons.values():
            sync_picked_check(btn)
        self.tool_changed.emit("pencil")
        self._apply_intro()
        self._refresh_status()
        self._fit_labels()

    # -- internals -------------------------------------------------------

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._fit_labels()

    def _apply_intro(self) -> None:
        """The intro line, minus its sentence when the server suppresses the
        Draw hint (``hints.suppressed`` carrying ``flow_markup``): the link to
        the guide stays, it is the panel's only way to the chapter. Nothing
        on this line can be closed any more, so "Show tips again" has nothing
        to bring back here; the server lever is the one switch left."""
        link = build_guide_link_html(self._intro_link)
        if is_hint_dismissed(HINT_MARKUP):
            self._intro_label.setText(link)
        else:
            self._intro_label.setText(f"{html.escape(self._intro_text)} {link}")

    def _make_swatch(self, color: QColor, name: str) -> QToolButton:
        btn = QToolButton(self._colors_box)
        btn.setCursor(QtC.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        btn.setFixedSize(_DOT_BTN_PX, _DOT_BTN_PX)
        btn.setIconSize(QSize(_COLOR_DOT_PX, _COLOR_DOT_PX))
        btn.setStyleSheet(_DOT_BTN_QSS)
        btn.setIcon(make_color_dot_icon(
            color, selected=False, is_dark=is_dark_palette(self), dot_px=_COLOR_DOT_PX
        ))
        btn.setToolTip(name)
        btn.setAccessibleName(name)
        btn.installEventFilter(self)
        picked = QColor(color)
        btn.clicked.connect(lambda _checked=False, c=picked: self._set_color(c))
        return btn

    def _fit_labels(self) -> None:
        """Keep every label inside its control at the dock's narrowest width.

        The tool tiles share the row evenly, so a long translation is elided
        rather than allowed to widen the panel; the whole word stays in the
        tooltip.
        """
        for key, btn in self._tool_buttons.items():
            full = self._tool_labels.get(key, "")
            room = max(16, btn.width() - 8)
            text = QFontMetrics(btn.font()).elidedText(
                full, Qt.TextElideMode.ElideRight, room
            )
            if btn.text() != text:
                btn.setText(text)
        self._fit_count_label()
        self._relayout_colors()

    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        if obj is getattr(self, "_count_label", None) and event.type() == QEvent.Type.Resize:
            self._fit_count_label()
        elif event.type() == QEvent.Type.KeyPress:
            if obj in getattr(self, "_tool_buttons", {}).values():
                if self._step_along(list(self._tool_buttons.values()), obj, event.key()):
                    return True
            elif obj in getattr(self, "_color_order", ()):
                if self._step_along(list(self._color_order), obj, event.key()):
                    return True
        return super().eventFilter(obj, event)

    def _step_along(self, tiles: list, current: QToolButton, key) -> bool:
        """Move along a row of tools or swatches from the keyboard and pick
        what the move lands on (never the "+", which opens a dialog).
        Returns whether the key was one of the row's moves."""
        index = tiles.index(current)
        moves = {
            Qt.Key.Key_Right: index + 1, Qt.Key.Key_Down: index + 1,
            Qt.Key.Key_Left: index - 1, Qt.Key.Key_Up: index - 1,
            Qt.Key.Key_Home: 0, Qt.Key.Key_End: len(tiles) - 1,
        }
        if key not in moves:
            return False
        target = tiles[moves[key] % len(tiles)]
        target.setFocus(Qt.FocusReason.TabFocusReason)
        if target is getattr(self, "_custom_color_btn", None):
            return True
        if not (target.isCheckable() and target.isChecked()):
            target.click()
        return True

    def _fit_count_label(self) -> None:
        label = getattr(self, "_count_label", None)
        if label is None:
            return
        full = self._count_text
        room = label.width() if label.width() > 0 and self.width() >= _MEASURED_MIN_PX else 10_000
        text = QFontMetrics(label.font()).elidedText(full, Qt.TextElideMode.ElideRight, max(24, room))
        if label.text() != text:
            label.setText(text)
        label.setToolTip(full if text != full else "")

    def _card_inner_width(self) -> int:
        """The toolbar card's usable width. Measured from the panel, not from
        the card: a child's own width still holds the old value while the
        panel is answering the resize that will change it."""
        return max(0, self.width() - _STRIP_MARGINS[0] - _STRIP_MARGINS[2] - 2)

    def _relayout_colors(self, force: bool = False) -> None:
        """Fill the swatch grid with as many dots per line as the room allows.

        The dots are fixed-size, so a full row of custom swatches on a narrow
        dock wraps onto a second line instead of widening the panel.
        """
        if getattr(self, "_colors_box", None) is None:
            return
        if self.width() < _MEASURED_MIN_PX:
            # Built, not laid out yet: one line, which the first resize
            # corrects. Wrapping on the unlaid width would leave the panel
            # holding room for rows it does not need.
            columns = max(1, len(self._color_order))
        else:
            room = self._card_inner_width()
            columns = max(1, (room + T.SPACE_TIGHT) // _DOT_STEP_PX)
        if columns == self._color_columns and not force:
            return
        self._color_columns = columns
        for index, btn in enumerate(self._color_order):
            self._color_grid.removeWidget(btn)
            self._color_grid.addWidget(btn, index // columns, index % columns)
        # One empty column at the end takes the slack, so the dots stay left.
        for column in range(columns + 1):
            self._color_grid.setColumnStretch(column, 1 if column == columns else 0)
        # The card is a fixed height: without this its old height survives the
        # reflow and the row keeps the air of the rows it no longer has.
        self._colors_box.updateGeometry()
        self._strip.updateGeometry()

    def _on_guide_link(self, href: str) -> None:
        """Open the guide at its own chapter, not at the top of the page.

        The href is checked so a served or translated line that smuggles in
        its own link cannot make the panel open an address nobody chose.
        """
        if href == GUIDE_LINK_HREF:
            open_guide("panel_markup", GUIDE_ANCHOR_MARKUP)

    def _on_tool_toggled(self, tool_key: str, checked: bool) -> None:
        if checked:
            self.tool_changed.emit(tool_key)
            self._refresh_status()

    def _on_custom_color_clicked(self) -> None:
        # The QGIS main window, not the dock: a dialog parented to a floating
        # dock opens in its own macOS Space (see main_window_for_dialog).
        chosen = QColorDialog.getColor(
            self._color,
            main_window_for_dialog(self),
            get_export_copy("widgets.markup_panel.color_dialog_title_stroke", tr("Stroke color")),
        )
        if not chosen.isValid():
            return
        color = QColor(chosen.red(), chosen.green(), chosen.blue())
        # Show the picked colour as a swatch in the row so the user sees what is
        # active (the preset swatches alone never reflect a custom pick).
        self._ensure_custom_swatch(color)
        self._set_color(color)

    def _ensure_custom_swatch(self, color: QColor) -> None:
        """Add a picked custom colour as a selectable swatch before the "+".

        Presets never change; custom swatches are deduped by RGB and capped,
        the oldest dropped once full. Picking a custom colour already in the
        row makes it the newest again, so the one in use is never the next
        to go.
        """
        key = (color.red(), color.green(), color.blue())
        if key in self._custom_color_keys:
            self._custom_color_keys.remove(key)
            self._custom_color_keys.append(key)
            return
        if key in self._color_btns:
            return
        btn = self._make_swatch(
            color, tr("Custom color {hex}").format(hex=color.name().upper())
        )
        # Just before the "+" button, so the presets stay leftmost.
        self._color_order.insert(len(self._color_order) - 1, btn)
        self._color_btns[key] = btn
        self._custom_color_keys.append(key)
        max_custom_swatches = get_export_dial("widgets.markup_panel.max_custom_swatches_count", _MAX_CUSTOM_SWATCHES)
        while len(self._custom_color_keys) > max(1, int(max_custom_swatches)):
            old = self._custom_color_keys.pop(0)
            old_btn = self._color_btns.pop(old, None)
            if old_btn is not None:
                if old_btn in self._color_order:
                    self._color_order.remove(old_btn)
                self._color_grid.removeWidget(old_btn)
                old_btn.deleteLater()
        self._relayout_colors(force=True)

    def _set_color(self, color: QColor) -> None:
        if QColor(color) == self._color:
            return
        self._color = QColor(color)
        self._update_color_indicators()
        self.color_changed.emit(QColor(color))

    def _update_color_indicators(self) -> None:
        active = (self._color.red(), self._color.green(), self._color.blue())
        dark = is_dark_palette(self)
        for rgb, btn in self._color_btns.items():
            btn.setIcon(make_color_dot_icon(
                QColor(*rgb), selected=(rgb == active), is_dark=dark, dot_px=_COLOR_DOT_PX
            ))

    def _undo_wired(self) -> bool:
        """Whether anything listens to undo_clicked: an Undo that does
        nothing is worse than no Undo."""
        try:
            return self.receivers(self.undo_clicked) > 0
        except (TypeError, RuntimeError):
            return False

    def _refresh_status(self) -> None:
        """The hint footer follows the armed tool; the status row follows
        the count. Both always say the truth about the current state."""
        checked = self._tool_group.checkedButton()
        tool_key = checked.property("markup_tool_key") if checked is not None else None
        count = self._annotation_count
        if tool_key and (count <= 0 or tool_key != "pencil"):
            self._status_label.setText(_tool_hint(tool_key))
            self._hint_glyph.setPixmap(_tool_hint_glyph(self, tool_key))
            self._hint_glyph.setVisible(True)
        else:
            if count > 0:
                self._status_label.setText(
                    get_export_copy(
                        "widgets.markup_panel.stroke_count_singular",
                        tr("{n} stroke. Click Done to guide the edit with it."),
                    ).format(n=count)
                    if count == 1
                    else get_export_copy(
                        "widgets.markup_panel.stroke_count_plural",
                        tr("{n} strokes. Click Done to guide the edit with them."),
                    ).format(n=count)
                )
            else:
                self._status_label.setText(_no_tool_hint() if not tool_key else _tool_hint(tool_key))
            self._hint_glyph.setVisible(False)
        self._count_text = _stroke_count_text(count)
        self._count_label.setStyleSheet(_COUNT_QSS if count > 0 else _COUNT_EMPTY_QSS)
        self._fit_count_label()
        # Nothing drawn: the two actions leave the row rather than sit there
        # greyed out, where the quiet ink of a disabled button reads almost
        # like the enabled one. "No strokes yet" is the whole row.
        self._clear_btn.setEnabled(count > 0)
        self._clear_btn.setVisible(count > 0)
        self._undo_btn.setEnabled(count > 0)
        self._undo_btn.setVisible(count > 0 and self._undo_wired())
        # Done's tooltip talks about strokes only when there are some.
        if count > 0:
            done_tip = get_export_copy(
                "widgets.markup_panel.done_tooltip_strokes",
                tr("Keep your strokes to guide the edit. They are removed from the result."),
            )
        else:
            done_tip = get_export_copy("widgets.markup_panel.done_tooltip_empty",
                                       tr("Close the panel"))
        self._done_btn.setToolTip(done_tip)
