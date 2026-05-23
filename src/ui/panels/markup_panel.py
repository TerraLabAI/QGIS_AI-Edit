















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

_DOT_STEP_PX = _DOT_BTN_PX + T.SPACE_TIGHT


_MEASURED_MIN_PX = 160


_STRIP_MARGINS = (10, 10, 10, 8)

_DONE_MIN_W = 96





_TOOL_TILE_QSS = (
    f"QToolButton {{ background: {T.INSET}; border: 1px solid transparent;"
    f" border-radius: {T.RADIUS_CARD}px;"


    f" padding: 5px 1px; color: {T.INK_2}; font-size: {T.FONT_HINT}px; font-weight: 500; }}"
    f"QToolButton:hover {{ background: {T.HOVER}; color: {T.INK}; }}"
    + T.picked_qss("QToolButton")
    + f"QToolButton:disabled {{ color: {T.INK_3}; }}"
    f"QToolButton:focus {{ border-color: {T.ACCENT_BORDER}; }}"
)
_DIVIDER_QSS = f"QFrame {{ background: {T.LINE}; border: none; }}"

_DOT_BTN_QSS = (
    "QToolButton { background: transparent; border: 1px solid transparent; padding: 0px;"
    f" border-radius: {_DOT_BTN_PX // 2}px; }}"
    f"QToolButton:hover {{ background: {T.HOVER}; }}"
    f"QToolButton:focus {{ border-color: {T.ACCENT_BORDER}; }}"
)

_TOOL_HINT_QSS = T.HINT_QSS + " padding: 0px;"

_COUNT_QSS = (
    f"font-size: {T.FONT_BODY}px; font-weight: 500; color: {T.INK};"
    " background: transparent; border: none;"
)
_COUNT_EMPTY_QSS = (
    f"font-size: {T.FONT_BODY}px; color: {T.INK_2};"
    " background: transparent; border: none;"
)


_BTN_QUIET_DANGER_QSS = (
    "QPushButton { background: transparent; border: 1px solid transparent;"
    f" color: {T.RED_TEXT}; font-size: {T.FONT_BODY}px; font-weight: 500; padding: 3px 7px;"
    f" border-radius: {T.RADIUS_PILL_SMALL}px; }}"
    f"QPushButton:hover {{ background: {T.RED_TINT}; }}"
    f"QPushButton:pressed {{ background: {T.RED_TINT}; }}"
    f"QPushButton:focus {{ border-color: {T.ACCENT_BORDER}; }}"
    f"QPushButton:disabled {{ color: {T.INK_3}; }}"
)

_MAX_CUSTOM_SWATCHES = 4


def _tool_hint(tool_key: str) -> str:



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

    return get_export_copy("widgets.markup_panel.hint_no_tool", tr("Pick a tool to draw."))


def _stroke_count_text(count: int) -> str:
    if count <= 0:
        return get_export_copy("widgets.markup_panel.count_none", tr("No strokes yet"))
    if count == 1:
        return tr("1 stroke")
    return tr("{n} strokes").format(n=count)


def _undo_shortcut_text() -> str:

    try:
        return QKeySequence(QKeySequence.StandardKey.Undo).toString(
            QKeySequence.SequenceFormat.NativeText
        )
    except (AttributeError, TypeError):
        return "Ctrl+Z"


def _make_tool_icon(shape: str, color: QColor) -> QIcon:




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




_SHARED_TOOL_GLYPHS = {"pencil": "pencil", "line": "polyline"}


def _tool_tile_icon(widget: QWidget, shape: str) -> QIcon:


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

    glyph = _SHARED_TOOL_GLYPHS.get(shape)
    if glyph is None:
        return _make_tool_icon(shape, qcolor(T.INK_2)).pixmap(_HINT_GLYPH_PX, _HINT_GLYPH_PX)
    return render_pixmap(glyph, qcolor(T.INK_2), _HINT_GLYPH_PX, widget_pixel_ratio(widget))


class MarkupPanel(QWidget):






    tool_changed = pyqtSignal(str)
    color_changed = pyqtSignal(QColor)
    clear_clicked = pyqtSignal()


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





        head = QWidget(self)
        head_col = QVBoxLayout(head)
        head_col.setContentsMargins(0, 0, 0, 0)
        head_col.setSpacing(2)

        head_col.addWidget(build_panel_header(
            get_export_copy("widgets.markup_panel.header_title_draw", tr("Draw")),
            show_close=False,
        ))


        self._intro_text = get_export_copy(
            "widgets.markup_panel.intro_short",
            tr("Sketch where the AI should act."),
        )


        self._intro_link = get_export_copy("widgets.markup_panel.markup_hint_link", tr("See an example"))
        self._intro_label = QLabel(head)
        self._intro_label.setWordWrap(True)
        self._intro_label.setIndent(0)
        self._intro_label.setStyleSheet(T.HINT_QSS)
        self._intro_label.setTextFormat(QtC.RichText)


        self._intro_label.setOpenExternalLinks(False)


        self._intro_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._intro_label.linkActivated.connect(self._on_guide_link)
        register_hint_widget(self._intro_label, HINT_MARKUP)
        head_col.addWidget(self._intro_label)
        layout.addWidget(head)
        self._apply_intro()



        self._no_zone_hint = make_notice_card(
            get_export_copy(
                "widgets.markup_panel.no_zone_inside",
                tr("Strokes count only inside your zone."),
            ),
            glyph="polygon",
        )
        layout.addWidget(self._no_zone_hint)



        strip = QFrame(self)
        strip.setObjectName("card")
        strip.setAttribute(QtC.WA_StyledBackground, True)
        strip.setStyleSheet(T.CARD_QSS)


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



        self._tool_labels: dict[str, str] = {}
        for key, label, tooltip in tool_specs:
            btn = QToolButton(strip)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setText(label)


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


            btn.installEventFilter(self)
            self._tool_buttons[key] = btn
            self._tool_labels[key] = label
            tool_row.addWidget(btn, 1)



        self._tool_buttons["pencil"].blockSignals(True)
        self._tool_buttons["pencil"].setChecked(True)
        self._tool_buttons["pencil"].blockSignals(False)





        self._colors_box = QWidget(strip)


        self._colors_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._color_grid = QGridLayout(self._colors_box)
        self._color_grid.setContentsMargins(0, 0, 0, 0)
        self._color_grid.setHorizontalSpacing(T.SPACE_TIGHT)
        self._color_grid.setVerticalSpacing(T.SPACE_TIGHT)
        strip_col.addWidget(self._colors_box)
        self._strip = strip


        self._color_order: list[QToolButton] = []
        self._color_columns = 0

        self._color_btns: dict[tuple[int, int, int], QToolButton] = {}


        self._custom_color_keys: list[tuple[int, int, int]] = []

        for name_key, r, g, b in _markup_color_presets():
            color = QColor(r, g, b)
            btn = self._make_swatch(color, _swatch_name(name_key))
            self._color_btns[(r, g, b)] = btn
            self._color_order.append(btn)


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


        status_row = QHBoxLayout()
        status_row.setContentsMargins(2, 0, 0, 0)
        status_row.setSpacing(T.SPACE_TIGHT)
        self._count_label = QLabel("")
        self._count_label.setIndent(0)



        self._count_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._count_text = ""


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

        clear_tip = get_export_copy("widgets.markup_panel.clear_all_tooltip_final",
                                    tr("Remove every stroke. Undo cannot bring them back."))
        self._clear_btn.setToolTip(clear_tip)
        self._clear_btn.clicked.connect(self.clear_clicked.emit)
        status_row.addWidget(self._clear_btn, 0, Qt.AlignmentFlag.AlignVCenter)


        self._count_label.setMinimumHeight(self._clear_btn.sizeHint().height())
        layout.addLayout(status_row)


        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(T.SPACE_CARD)
        action_row.addStretch()
        self._done_btn = QPushButton(get_export_copy("widgets.markup_panel.done_button", tr("Done")))

        self._done_btn.setStyleSheet(T.BTN_PRIMARY_QSS)
        self._done_btn.setCursor(QtC.PointingHandCursor)
        self._done_btn.setMinimumWidth(_DONE_MIN_W)
        self._done_btn.clicked.connect(self.done_clicked.emit)
        action_row.addWidget(self._done_btn)
        layout.addLayout(action_row)
        layout.addStretch()



        self._no_zone_hint.setVisible(True)
        self._update_color_indicators()
        self._refresh_status()
        self._fit_labels()







    def get_color(self) -> QColor:
        return QColor(self._color)

    def set_annotation_count(self, count: int) -> None:

        self._annotation_count = max(0, int(count))
        self._refresh_status()

    def annotation_count(self) -> int:


        return self._annotation_count

    def set_zone_present(self, has_zone: bool) -> None:



        self._has_zone = has_zone
        self._no_zone_hint.setVisible(not has_zone)
        self._refresh_status()

    def uncheck_tool(self, tool_key: str) -> None:













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

        sync_picked_check(btn)
        self._refresh_status()

    def activate(self) -> None:








        pencil = self._tool_buttons["pencil"]
        pencil.blockSignals(True)
        pencil.setChecked(True)
        pencil.blockSignals(False)


        for btn in self._tool_buttons.values():
            sync_picked_check(btn)
        self.tool_changed.emit("pencil")
        self._apply_intro()
        self._refresh_status()
        self._fit_labels()



    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit_labels()

    def _apply_intro(self) -> None:





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

    def eventFilter(self, obj, event):  # noqa: N802
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



        return max(0, self.width() - _STRIP_MARGINS[0] - _STRIP_MARGINS[2] - 2)

    def _relayout_colors(self, force: bool = False) -> None:





        if getattr(self, "_colors_box", None) is None:
            return
        if self.width() < _MEASURED_MIN_PX:



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

        for column in range(columns + 1):
            self._color_grid.setColumnStretch(column, 1 if column == columns else 0)


        self._colors_box.updateGeometry()
        self._strip.updateGeometry()

    def _on_guide_link(self, href: str) -> None:





        if href == GUIDE_LINK_HREF:
            open_guide("panel_markup", GUIDE_ANCHOR_MARKUP)

    def _on_tool_toggled(self, tool_key: str, checked: bool) -> None:
        if checked:
            self.tool_changed.emit(tool_key)
            self._refresh_status()

    def _on_custom_color_clicked(self) -> None:


        chosen = QColorDialog.getColor(
            self._color,
            main_window_for_dialog(self),
            get_export_copy("widgets.markup_panel.color_dialog_title_stroke", tr("Stroke color")),
        )
        if not chosen.isValid():
            return
        color = QColor(chosen.red(), chosen.green(), chosen.blue())


        self._ensure_custom_swatch(color)
        self._set_color(color)

    def _ensure_custom_swatch(self, color: QColor) -> None:







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


        try:
            return self.receivers(self.undo_clicked) > 0
        except (TypeError, RuntimeError):
            return False

    def _refresh_status(self) -> None:


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



        self._clear_btn.setEnabled(count > 0)
        self._clear_btn.setVisible(count > 0)
        self._undo_btn.setEnabled(count > 0)
        self._undo_btn.setVisible(count > 0 and self._undo_wired())

        if count > 0:
            done_tip = get_export_copy(
                "widgets.markup_panel.done_tooltip_strokes",
                tr("Keep your strokes to guide the edit. They are removed from the result."),
            )
        else:
            done_tip = get_export_copy("widgets.markup_panel.done_tooltip_empty",
                                       tr("Close the panel"))
        self._done_btn.setToolTip(done_tip)
