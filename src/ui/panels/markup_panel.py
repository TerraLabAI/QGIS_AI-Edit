"""Mark up panel widget.

Self-contained QWidget that owns the Pencil/Arrow/Circle tool row, the
color swatches, and the status hint. Emits signals the dock relays to the
plugin orchestrator.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QPointF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPolygonF,
    QShortcut,
)
from qgis.PyQt.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.i18n import tr
from ..onboarding_hint import HINT_MARKUP, DismissibleHint, is_hint_dismissed
from ..panel_helpers import (
    GROUP_BOX_QSS,
    build_panel_header,
    is_dark_palette,
    make_color_dot_icon,
    make_custom_color_icon,
    make_hidpi_pixmap,
)
from ..tools.markup_tools import MARKUP_DEFAULT_COLOR

BRAND_BLUE = "#1e88e5"
BRAND_RED = "#d32f2f"
DISABLED_TEXT = "#666666"

_BTN_GHOST_QSS = (
    "QPushButton { background-color: transparent; color: palette(text);"
    " padding: 8px 16px; border-radius: 4px;"
    " border: 1px solid rgba(128, 128, 128, 0.35); }"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.15);"
    " border: 1px solid rgba(128, 128, 128, 0.5); }"
    f"QPushButton:disabled {{ background-color: rgba(128, 128, 128, 0.08);"
    f" border: 1px solid rgba(128, 128, 128, 0.15); color: {DISABLED_TEXT}; }}"
)


# Annotation colors avoid the map palette (red / green / blue / gray) so the
# model never reads a mark as a class fill. The default (neon magenta) is first.
_MARKUP_PRESETS: list[tuple[str, int, int, int]] = [
    ("Magenta", *MARKUP_DEFAULT_COLOR),
    ("Violet", 138, 43, 226),
    ("Pink", 236, 72, 153),
    ("Amber", 245, 158, 11),
    ("Cyan", 14, 165, 188),
]

_TOOL_BUTTON_SIZE = 56
_TOOL_ICON_PX = 24
_COLOR_DOT_PX = 22
# Cap on user-added custom swatches kept in the color row (oldest dropped).
_MAX_CUSTOM_SWATCHES = 4


def _tool_hint(tool_key: str) -> str:
    hints = {
        "pencil": tr("Drag on the map to sketch a freehand stroke."),
        "arrow": tr("Click and drag on the map to draw an arrow."),
        "circle": tr("Drag on the map to draw an ellipse."),
    }
    return hints.get(tool_key, hints["pencil"])


def _make_tool_icon(shape: str, color: QColor) -> QIcon:
    """Render a clean 24px line icon for a Mark up tool (theme-aware)."""
    size = _TOOL_ICON_PX
    pm = make_hidpi_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color)
    pen.setWidthF(1.9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if shape == "pencil":
        body = QPolygonF(
            [
                QPointF(17.5, 4.5),
                QPointF(20.5, 7.5),
                QPointF(9, 19),
                QPointF(4.5, 20.5),
                QPointF(6, 16),
            ]
        )
        p.drawPolygon(body)
        p.drawLine(QPointF(14.5, 7.5), QPointF(17.5, 10.5))
    elif shape == "arrow":
        p.drawLine(QPointF(5, 19), QPointF(18.5, 5.5))
        p.drawLine(QPointF(18.5, 5.5), QPointF(18.5, 12.5))
        p.drawLine(QPointF(18.5, 5.5), QPointF(11.5, 5.5))
    elif shape == "circle":
        r = 8.5
        p.drawEllipse(QPointF(size / 2, size / 2), r, r)
    p.end()
    return QIcon(pm)


class MarkupPanel(QWidget):
    """Tool panel: pencil / arrow / circle drawing on the canvas.

    Annotations land in a memory layer (owned by MarkupLayerManager) that
    the CanvasExporter renders into the PNG sent to the AI.
    """

    tool_changed = pyqtSignal(str)        # 'pencil' | 'arrow' | 'circle'
    color_changed = pyqtSignal(QColor)
    clear_clicked = pyqtSignal()
    done_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(*MARKUP_DEFAULT_COLOR)
        self._annotation_count = 0
        self._has_zone = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(build_panel_header(tr("Mark up")))

        # Dismissible tip at the top (same pattern as the prompt library):
        # a concise, closeable note on what Mark up is for. Restorable from
        # Account Settings; activate() re-checks its state.
        self._markup_hint = DismissibleHint(
            HINT_MARKUP,
            "",
            tr("Draw on your zone to point the AI where to act. Your marks "
               "guide the edit and are removed from the result."),
        )
        layout.addWidget(self._markup_hint)

        # Tool section - wrapped in a native-feeling group box.
        tool_group = QGroupBox(tr("Tool"))
        tool_group.setStyleSheet(GROUP_BOX_QSS)
        tool_row = QHBoxLayout(tool_group)
        tool_row.setContentsMargins(8, 6, 8, 8)
        tool_row.setSpacing(6)

        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons: dict[str, QToolButton] = {}

        tool_specs = [
            ("pencil", tr("Pencil"), tr("Freehand stroke")),
            ("arrow", tr("Arrow"), tr("Click-drag from start to end")),
            ("circle", tr("Circle"), tr("Drag to draw an ellipse")),
        ]

        text_color = self.palette().color(QPalette.ColorRole.WindowText)

        tool_btn_style = (
            "QToolButton {"
            " background: rgba(128, 128, 128, 0.06);"
            " border: 1px solid rgba(128, 128, 128, 0.20);"
            " border-radius: 8px;"
            " padding: 6px 0px;"
            " color: palette(text);"
            " font-size: 11px;"
            "}"
            "QToolButton:hover {"
            " background: rgba(128, 128, 128, 0.14);"
            " border: 1px solid rgba(128, 128, 128, 0.35);"
            "}"
            f"QToolButton:checked {{"
            f" background: rgba(25, 118, 210, 0.14);"
            f" border: 1.5px solid {BRAND_BLUE};"
            f" color: {BRAND_BLUE};"
            f"}}"
            "QToolButton:disabled {"
            " background: rgba(128, 128, 128, 0.04);"
            " border: 1px solid rgba(128, 128, 128, 0.10);"
            " color: rgba(128, 128, 128, 0.55);"
            "}"
        )

        for key, label, tooltip in tool_specs:
            btn = QToolButton()
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setText(label)
            btn.setToolTip(tooltip)
            btn.setIcon(_make_tool_icon(key, text_color))
            btn.setIconSize(QSize(_TOOL_ICON_PX, _TOOL_ICON_PX))
            btn.setCheckable(True)
            btn.setCursor(QtC.PointingHandCursor)
            btn.setFocusPolicy(QtC.NoFocus)
            btn.setFixedHeight(_TOOL_BUTTON_SIZE)
            btn.setFixedWidth(_TOOL_BUTTON_SIZE + 18)
            btn.setStyleSheet(tool_btn_style)
            btn.setProperty("markup_tool_key", key)
            btn.toggled.connect(
                lambda checked, k=key: self._on_tool_toggled(k, checked)
            )
            self._tool_group.addButton(btn)
            self._tool_buttons[key] = btn
            tool_row.addWidget(btn)
        tool_row.addStretch()
        layout.addWidget(tool_group)

        # Block signals: _status_label isn't built yet, and activate() will
        # re-emit tool_changed for us when the panel becomes visible.
        self._tool_buttons["pencil"].blockSignals(True)
        self._tool_buttons["pencil"].setChecked(True)
        self._tool_buttons["pencil"].blockSignals(False)

        # Color section - matching grouped container.
        color_group = QGroupBox(tr("Color"))
        color_group.setStyleSheet(GROUP_BOX_QSS)
        color_row = QHBoxLayout(color_group)
        color_row.setContentsMargins(8, 6, 8, 8)
        color_row.setSpacing(6)
        self._color_row = color_row

        self._color_btns: dict[tuple[int, int, int], QToolButton] = {}
        # Custom colors the user picks via "+"; kept in insertion order so the
        # oldest can be dropped once the row is full.
        self._custom_color_keys: list[tuple[int, int, int]] = []
        self._dot_btn_style = (
            "QToolButton {"
            " background: transparent;"
            " border: none;"
            " padding: 2px;"
            "}"
            "QToolButton:disabled { opacity: 0.4; }"
        )
        dot_btn_style = self._dot_btn_style

        for _label, r, g, b in _MARKUP_PRESETS:
            btn = QToolButton()
            btn.setCursor(QtC.PointingHandCursor)
            btn.setFocusPolicy(QtC.NoFocus)
            btn.setFixedSize(_COLOR_DOT_PX + 6, _COLOR_DOT_PX + 6)
            btn.setIconSize(QSize(_COLOR_DOT_PX, _COLOR_DOT_PX))
            btn.setStyleSheet(dot_btn_style)
            color = QColor(r, g, b)
            btn.setIcon(make_color_dot_icon(
                color, selected=False, is_dark=is_dark_palette(self), dot_px=_COLOR_DOT_PX
            ))
            btn.setToolTip(color.name().upper())
            btn.clicked.connect(
                lambda _checked=False, c=color: self._set_color(c)
            )
            self._color_btns[(r, g, b)] = btn
            color_row.addWidget(btn)

        # Custom color button (+)
        self._custom_color_btn = QToolButton()
        self._custom_color_btn.setCursor(QtC.PointingHandCursor)
        self._custom_color_btn.setFocusPolicy(QtC.NoFocus)
        self._custom_color_btn.setFixedSize(_COLOR_DOT_PX + 6, _COLOR_DOT_PX + 6)
        self._custom_color_btn.setIconSize(QSize(_COLOR_DOT_PX, _COLOR_DOT_PX))
        self._custom_color_btn.setStyleSheet(dot_btn_style)
        self._custom_color_btn.setIcon(make_custom_color_icon(
            is_dark=is_dark_palette(self), dot_px=_COLOR_DOT_PX
        ))
        self._custom_color_btn.setToolTip(tr("Custom color…"))
        self._custom_color_btn.clicked.connect(self._on_custom_color_clicked)
        color_row.addWidget(self._custom_color_btn)
        color_row.addStretch()
        layout.addWidget(color_group)

        # Status hint
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(
            "font-size: 11px; color: palette(text);"
            " background: transparent; border: none;"
            " padding: 4px 0px 0px 0px;"
        )
        layout.addWidget(self._status_label)

        # Visible only when no zone exists; tools stay enabled to pre-stage hints.
        self._no_zone_hint = QLabel(
            "ⓘ " + tr(
                "Draw a zone first to anchor your guides. They'll ride "
                "along with the next generation inside that zone."
            )
        )
        self._no_zone_hint.setWordWrap(True)
        self._no_zone_hint.setStyleSheet(
            "font-size: 11px; color: palette(text);"
            " background: rgba(128, 128, 128, 0.08);"
            " border: 1px solid rgba(128, 128, 128, 0.20);"
            " border-radius: 4px;"
            " padding: 6px 8px;"
            " margin-top: 4px;"
        )
        layout.addWidget(self._no_zone_hint)

        # Action row (Clear all / Done)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 6, 0, 0)
        action_row.setSpacing(6)

        self._clear_btn = QPushButton(tr("Clear all"))
        self._clear_btn.setCursor(QtC.PointingHandCursor)
        self._clear_btn.setEnabled(False)
        self._clear_btn.setStyleSheet(
            "QPushButton {"
            " background: transparent; border: 1px solid rgba(211, 47, 47, 0.45);"
            f" color: {BRAND_RED}; padding: 6px 12px;"
            " font-size: 12px; border-radius: 4px;"
            "}"
            "QPushButton:hover {"
            " background: rgba(211, 47, 47, 0.18);"
            " border: 1px solid rgba(211, 47, 47, 0.75);"
            "}"
            "QPushButton:disabled {"
            " color: rgba(128, 128, 128, 0.5);"
            " border: 1px solid rgba(128, 128, 128, 0.25);"
            "}"
        )
        self._clear_btn.clicked.connect(self.clear_clicked.emit)
        action_row.addWidget(self._clear_btn)
        action_row.addStretch()

        self._done_btn = QPushButton(tr("Done"))
        self._done_btn.setToolTip(
            tr("Keep your marks on the zone to guide the edit, and close Mark up")
        )
        self._done_btn.setStyleSheet(_BTN_GHOST_QSS)
        self._done_btn.setCursor(QtC.PointingHandCursor)
        self._done_btn.setMinimumHeight(34)
        self._done_btn.setMinimumWidth(80)
        self._done_btn.clicked.connect(self.done_clicked.emit)
        action_row.addWidget(self._done_btn)
        layout.addLayout(action_row)
        layout.addStretch()

        # Initial state - default to no zone (the dock will refresh us as
        # soon as the user draws one). Visible from cold open.
        self._no_zone_hint.setVisible(True)
        self._update_color_indicators()
        self._refresh_status()

        # Esc → Done. WindowShortcut so it fires no matter which child
        # has focus while the panel is visible; the dock's global Esc
        # handler bails out when the main widget is hidden, so no clash.
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WindowShortcut)
        esc.activated.connect(self.done_clicked.emit)

    # -- public API ------------------------------------------------------

    def get_color(self) -> QColor:
        return QColor(self._color)

    def set_annotation_count(self, count: int) -> None:
        """Update the status hint and the Clear all enabled state."""
        self._annotation_count = count
        self._clear_btn.setEnabled(count > 0)
        self._refresh_status()

    def set_zone_present(self, has_zone: bool) -> None:
        """Track whether a zone exists. Tools stay enabled either way so
        the user can sketch hints first and draw the zone after.
        """
        self._has_zone = has_zone
        self._no_zone_hint.setVisible(not has_zone)
        self._refresh_status()

    def activate(self) -> None:
        """Re-arm the panel when it becomes visible: ensure a tool is
        checked and emit the current tool key.
        """
        checked = self._tool_group.checkedButton()
        if checked is None:
            self._tool_buttons["pencil"].setChecked(True)
            checked = self._tool_buttons["pencil"]
        tool_key = checked.property("markup_tool_key")
        if tool_key:
            self.tool_changed.emit(tool_key)
        # Re-check the tip each time the panel opens so "Show again" (settings)
        # brings it back without a plugin reload.
        self._markup_hint.setVisible(not is_hint_dismissed(HINT_MARKUP))

    # -- internals -------------------------------------------------------

    def _on_tool_toggled(self, tool_key: str, checked: bool) -> None:
        if checked:
            self.tool_changed.emit(tool_key)
            self._refresh_status()

    def _on_custom_color_clicked(self) -> None:
        chosen = QColorDialog.getColor(
            self._color, self, tr("Pick annotation color")
        )
        if not chosen.isValid():
            return
        color = QColor(chosen.red(), chosen.green(), chosen.blue())
        # Show the picked color as a swatch in the row so the user sees what is
        # active (the preset swatches alone never reflect a custom pick).
        self._ensure_custom_swatch(color)
        self._set_color(color)

    def _ensure_custom_swatch(self, color: QColor) -> None:
        """Add a picked custom color as a selectable swatch before the "+".

        Presets never change; custom swatches are deduped by RGB and capped, the
        oldest dropped once full. The new swatch reuses the same machinery as the
        presets, so _update_color_indicators highlights it as selected for free.
        """
        key = (color.red(), color.green(), color.blue())
        if key in self._color_btns:
            return
        btn = QToolButton()
        btn.setCursor(QtC.PointingHandCursor)
        btn.setFocusPolicy(QtC.NoFocus)
        btn.setFixedSize(_COLOR_DOT_PX + 6, _COLOR_DOT_PX + 6)
        btn.setIconSize(QSize(_COLOR_DOT_PX, _COLOR_DOT_PX))
        btn.setStyleSheet(self._dot_btn_style)
        btn.setIcon(make_color_dot_icon(
            color, selected=False, is_dark=is_dark_palette(self), dot_px=_COLOR_DOT_PX
        ))
        btn.setToolTip(color.name().upper())
        btn.clicked.connect(
            lambda _checked=False, c=color: self._set_color(c)
        )
        # Insert just before the "+" button so presets stay leftmost.
        idx = self._color_row.indexOf(self._custom_color_btn)
        self._color_row.insertWidget(idx, btn)
        self._color_btns[key] = btn
        self._custom_color_keys.append(key)
        while len(self._custom_color_keys) > _MAX_CUSTOM_SWATCHES:
            old = self._custom_color_keys.pop(0)
            old_btn = self._color_btns.pop(old, None)
            if old_btn is not None:
                self._color_row.removeWidget(old_btn)
                old_btn.deleteLater()

    def _set_color(self, color: QColor) -> None:
        self._color = color
        self._update_color_indicators()
        self.color_changed.emit(color)

    def _update_color_indicators(self) -> None:
        active = (self._color.red(), self._color.green(), self._color.blue())
        dark = is_dark_palette(self)
        for rgb, btn in self._color_btns.items():
            btn.setIcon(make_color_dot_icon(
                QColor(*rgb), selected=(rgb == active), is_dark=dark, dot_px=_COLOR_DOT_PX
            ))

    def _refresh_status(self) -> None:
        count = self._annotation_count
        if count <= 0:
            tool_key = None
            checked = self._tool_group.checkedButton()
            if checked is not None:
                tool_key = checked.property("markup_tool_key")
            text = _tool_hint(tool_key or "pencil")
        else:
            text = tr(
                "{n} mark. Click Done to guide the edit with it."
            ).format(n=count) if count == 1 else tr(
                "{n} marks. Click Done to guide the edit with them."
            ).format(n=count)
        self._status_label.setText(text)
