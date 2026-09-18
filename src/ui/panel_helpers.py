"""Helpers and small widgets shared by the dock and the tool panels.

Used by AIEditDockWidget, MarkupPanel, ReferencePanel and VectorizePanel:
the AI Agent panel pieces (title row, section card, status line, notice
card, colour dots) and the input sheets with real chevron images.
"""
from __future__ import annotations

import os
import tempfile

from qgis.PyQt.QtCore import QEvent, QPointF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QGuiApplication,
    QIcon,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from qgis.PyQt.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from .dock import design_tokens as T
from .dock.design_tokens import qcolor
from .icons import icon_for, pixmap_for, render_pixmap

_CLOSE_GLYPH_PX = 16
_STATUS_GLYPH_PX = 14

SECTION_HEADER_QSS = (
    "font-weight: bold; font-size: 12px; color: palette(text);"
    " margin: 0px; padding: 0px 0px 2px 0px;"
)

SECTION_HEADER_EXTRA_TOP_QSS = (
    "font-weight: bold; font-size: 12px; color: palette(text); padding-top: 6px;"
)


def make_section_header(text: str, extra_top: bool = False) -> QLabel:
    """Section header label (bold, 12px, palette-aware)."""
    label = QLabel(text)
    label.setStyleSheet(SECTION_HEADER_EXTRA_TOP_QSS if extra_top else SECTION_HEADER_QSS)
    label.setContentsMargins(0, 0, 0, 0)
    return label


_PICKED_CHECK_PX = 12
_PICKED_CHECK_INSET = 5


class _PickedCheck(QLabel):
    """The check in the top right corner of a picked option. It follows the
    button's size and its checked state, and lets clicks through."""

    def __init__(self, button, hue: str) -> None:
        super().__init__(button)
        self._button = button
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFixedSize(_PICKED_CHECK_PX, _PICKED_CHECK_PX)
        self.setPixmap(pixmap_for(button, "check", _PICKED_CHECK_PX,
                                  qcolor(T.category_ink(hue))))
        button.installEventFilter(self)
        button.toggled.connect(self._sync)
        self._sync(button.isChecked())

    def _sync(self, checked: bool) -> None:
        self.move(self._button.width() - _PICKED_CHECK_PX - _PICKED_CHECK_INSET,
                  _PICKED_CHECK_INSET)
        self.setVisible(bool(checked))
        self.raise_()

    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        if obj is self._button and event.type() == QEvent.Type.Resize:
            self._sync(self._button.isChecked())
        return False


def add_picked_check(button, hue: str = T.PICKED_HUE) -> None:
    """Give a checkable tile or chip the shared picked check."""
    _PickedCheck(button, hue)


def sync_picked_check(button) -> None:
    """Bring a button's picked check in line with its checked state.

    The check follows ``toggled``, so a state set under ``blockSignals`` (a
    panel resetting its tiles without re-emitting) leaves it showing on a tile
    that is no longer picked, or missing from the one that is."""
    for check in button.findChildren(_PickedCheck):
        check._sync(button.isChecked())


def main_window_for_dialog(fallback: QWidget):
    """Parent to use for popup dialogs.

    On macOS, parenting a dialog to a QDockWidget (especially when the
    dock is floating, or when QGIS itself is in a fullscreen Space) makes
    the dialog open in its own Mission Control Space, yanking the user
    out of the QGIS workspace. The QGIS main window is always anchored
    to the right Space, so we use it as the parent instead.

    Returns ``fallback`` if iface isn't reachable for any reason.
    """
    try:
        from qgis.utils import iface
        mw = iface.mainWindow() if iface is not None else None
        if mw is not None:
            return mw
    except Exception:  # nosec B110 - any failure falls back below.
        pass
    return fallback


class PanelHeader(QWidget):
    """A tool panel's title row, AI Agent's card head: the title at 13 px 600
    on the left, an optional quiet close glyph on the right, an optional
    muted line under it. ``close_clicked`` fires on the glyph.

    A panel with its own Done button (the one way out of that surface) is
    built with ``show_close=False``: the dock header's own close X above it
    already closes the panel, so a second X here would be a duplicate exit.
    """

    close_clicked = pyqtSignal()

    def __init__(self, title: str, subtitle: str | None = None,
                 close_tip: str = "", parent: QWidget | None = None,
                 show_close: bool = True) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(T.SPACE_CARD)
        # setIndent(0): a styled QLabel otherwise picks up a small automatic
        # indent on the title only, leaving it right of the line under it.
        self.title_label = QLabel(title)
        self.title_label.setIndent(0)
        self.title_label.setStyleSheet(T.TITLE_QSS + " font-size: 14px;")
        row.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.close_button = None
        if show_close:
            self.close_button = QToolButton(self)
            self.close_button.setObjectName("panelCloseButton")
            self.close_button.setStyleSheet(T.BTN_ICON_QSS)
            self.close_button.setIcon(icon_for(self, "close", _CLOSE_GLYPH_PX, qcolor(T.INK_2)))
            self.close_button.setIconSize(QSize(_CLOSE_GLYPH_PX, _CLOSE_GLYPH_PX))
            self.close_button.setFixedSize(T.BTN_SMALL_PX, T.BTN_SMALL_PX)
            self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
            tip = close_tip or tr("Close")
            self.close_button.setToolTip(tip)
            self.close_button.setAccessibleName(tip)
            self.close_button.clicked.connect(self.close_clicked.emit)
            row.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(row)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setWordWrap(True)
            sub.setIndent(0)
            sub.setStyleSheet(T.HINT_QSS)
            col.addWidget(sub)

    def set_close_visible(self, visible: bool) -> None:
        """Show or hide the close glyph built with ``show_close=True``.

        For a panel whose own Done button only appears once its surface
        reaches a finished state (Vectorize's run button becomes Finish),
        the header keeps its close glyph as the way out until then, and
        hides it once that Done stands on its own. A no-op when the header
        was built with ``show_close=False``.
        """
        if self.close_button is not None:
            self.close_button.setVisible(visible)


def build_panel_header(title: str, subtitle: str | None = None,
                       close_tip: str = "", show_close: bool = True) -> PanelHeader:
    """Tool-panel title row (see ``PanelHeader``). Connect ``close_clicked``."""
    return PanelHeader(title, subtitle, close_tip, show_close=show_close)


SECTION_LABEL_QSS = (
    f"font-size: {T.FONT_BODY}px; font-weight: 600; color: {T.INK_2};"
    " background: transparent; border: none; padding: 4px 2px 0px 2px;"
)


def panel_section_label(text: str) -> QLabel:
    """A section header over a card, AI Agent's settings line: sentence case,
    12 px, 600, in the secondary ink."""
    label = QLabel(text)
    label.setIndent(0)
    label.setStyleSheet(SECTION_LABEL_QSS)
    return label


class PanelSection(QWidget):
    """A micro header over a card: the AI Agent settings grouping, which
    replaces the old titled group boxes. Add controls to ``body``."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(T.SPACE_CARD)
        self.header = panel_section_label(title)
        col.addWidget(self.header)
        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setStyleSheet(T.CARD_QSS)
        col.addWidget(self.card)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(*T.CARD_MARGINS)
        self.body.setSpacing(T.SPACE_OUTER)

    def setTitle(self, text: str) -> None:  # noqa: N802 - QGroupBox's spelling
        self.header.setText(text)


# A field label inside a card, and a muted line of help under a control.
FIELD_LABEL_QSS = T.BODY_QSS
PANEL_HINT_QSS = T.HINT_QSS

# The panel's own inputs: spin boxes and combos take the input shape with a
# real chevron image (DESIGN-SYSTEM "Spin box and combo arrows": a styled
# frame without an arrow image leaves a control nobody can step).
_SPIN_FRAME_QSS = (
    f"QAbstractSpinBox {{ background: {T.SURFACE}; color: {T.INK};"
    f" border: 1px solid {T.LINE_STRONG}; border-radius: {T.RADIUS_CONTROL}px;"
    " padding: 3px 18px 3px 8px;"
    f" selection-background-color: {T.ACCENT_BORDER}; }}"
    f"QAbstractSpinBox:hover {{ border-color: {T.ACCENT_BORDER_SOFT}; }}"
    f"QAbstractSpinBox:focus {{ border-color: {T.ACCENT_BORDER}; }}"
    f"QAbstractSpinBox:disabled {{ color: {T.INK_3}; background: {T.FIELD}; }}"
    # A dock-wide input sheet would otherwise pad the spin box's own inner
    # line edit and cut its digits in half.
    "QAbstractSpinBox QLineEdit { background: transparent; border: none;"
    " padding: 0px; margin: 0px; }"
    "QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {"
    " subcontrol-origin: border; width: 15px; height: 11px;"
    " background: transparent; border: none; margin-right: 3px; }"
    "QAbstractSpinBox::up-button { subcontrol-position: top right; margin-top: 2px; }"
    "QAbstractSpinBox::down-button { subcontrol-position: bottom right; margin-bottom: 2px; }"
)
_COMBO_FRAME_QSS = (
    f"QComboBox {{ background: {T.SURFACE}; color: {T.INK};"
    f" border: 1px solid {T.LINE_STRONG}; border-radius: {T.RADIUS_CONTROL}px;"
    f" padding: 0 10px; min-height: {T.BTN_PX - 2}px; font-size: {T.FONT_BODY}px; }}"
    f"QComboBox:hover {{ border-color: {T.ACCENT_BORDER_SOFT}; }}"
    f"QComboBox:focus, QComboBox:on {{ border-color: {T.ACCENT_BORDER}; }}"
    f"QComboBox:disabled {{ color: {T.INK_3}; background: {T.FIELD}; border-color: {T.LINE}; }}"
    "QComboBox::drop-down { subcontrol-origin: border; subcontrol-position: center right;"
    " width: 24px; background: transparent; border: none; }"
    f"QComboBox QAbstractItemView {{ color: {T.INK}; background: {T.SURFACE};"
    f" border: 1px solid {T.LINE_STRONG}; border-radius: {T.RADIUS_CARD}px; padding: 4px;"
    f" outline: none; selection-background-color: {T.ACCENT_TINT_ON}; selection-color: {T.INK}; }}"
)
_ARROW_PX = 10
_ARROW_URLS: dict = {}


def _glyph_url(name: str, ink: str = "", size: int = _ARROW_PX) -> str:
    """A glyph PNG in the session temp folder, written once, as a url."""
    color = qcolor(ink or T.INK_2)
    key = (name, color.rgba(), size)
    cached = _ARROW_URLS.get(key)
    if cached is not None:
        return cached
    url = ""
    try:
        folder = os.path.join(tempfile.gettempdir(), "ai_edit_panel_glyphs")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{name}_{color.rgba():08x}_{size}.png")
        if not os.path.exists(path):
            render_pixmap(name, color, size, 2.0).save(path, "PNG")
        url = path.replace("\\", "/")
    except Exception:  # noqa: BLE001 - a glyph-less control still works
        url = ""
    _ARROW_URLS[key] = url
    return url


def _arrow_url(name: str) -> str:
    return _glyph_url(name)


def spin_box_qss() -> str:
    """The spin-box sheet, with the chevrons when they could be written."""
    up, down = _arrow_url("chevron_up"), _arrow_url("chevron_down")
    if not (up and down):
        return _SPIN_FRAME_QSS
    return (
        _SPIN_FRAME_QSS
        + f'QAbstractSpinBox::up-arrow {{ image: url("{up}"); width: {_ARROW_PX}px; height: {_ARROW_PX}px; }}'
        + f'QAbstractSpinBox::down-arrow {{ image: url("{down}"); width: {_ARROW_PX}px; height: {_ARROW_PX}px; }}'
    )


def combo_box_qss() -> str:
    """The combo sheet, with the chevron when it could be written."""
    down = _arrow_url("chevron_down")
    if not down:
        return _COMBO_FRAME_QSS
    return (
        _COMBO_FRAME_QSS
        + f'QComboBox::down-arrow {{ image: url("{down}"); width: {_ARROW_PX}px; height: {_ARROW_PX}px; }}'
    )


def locked_combo_qss() -> str:
    """The layer picker locked for a run, AI Segmentation's locked look: the
    chevron gone, the field on the neutral tint with the name in full ink, so
    the layer being worked on still reads, and not as a greyed-out control."""
    return (
        _COMBO_FRAME_QSS
        + "QComboBox::drop-down { width: 0px; border: none; }"
        + "QComboBox::down-arrow { image: none; width: 0px; }"
        + f"QComboBox:disabled {{ color: {T.INK}; background: {T.FIELD}; border-color: {T.LINE}; }}"
    )


# A slider on the design system's line: a 6 px groove, the filled track in the
# interaction blue, a round handle with a paper ring; fully grey when disabled.
SLIDER_QSS = (
    f"QSlider:horizontal {{ min-height: 22px; background: transparent; }}"
    f"QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {T.FIELD}; }}"
    f"QSlider::sub-page:horizontal {{ height: 6px; border-radius: 3px; background: {T.BRAND_BLUE}; }}"
    f"QSlider::handle:horizontal {{ background: {T.BRAND_BLUE}; border: 2px solid {T.SURFACE};"
    " width: 14px; height: 14px; margin: -6px 0; border-radius: 9px; }"
    f"QSlider::handle:horizontal:hover {{ background: {T.BRAND_BLUE_HOVER}; }}"
    f"QSlider::sub-page:horizontal:disabled {{ background: {T.LINE_STRONG}; }}"
    f"QSlider::handle:horizontal:disabled {{ background: {T.INK_3}; }}"
)


_CHECK_PX = 16


def check_box_qss() -> str:
    """A checkbox on the line: a rounded box on the input outline (3:1), filled
    with the interaction blue and a white check when on."""
    tick = _glyph_url("check", T.ON_BLUE, 12)
    tick_rule = f' image: url("{tick}");' if tick else ""
    return (
        f"QCheckBox {{ spacing: 8px; color: {T.INK}; background: transparent; }}"
        f"QCheckBox::indicator {{ width: {_CHECK_PX}px; height: {_CHECK_PX}px;"
        f" border: 1px solid {T.LINE_INPUT}; border-radius: 5px; background: {T.SURFACE}; }}"
        f"QCheckBox::indicator:hover {{ border-color: {T.ACCENT_BORDER}; }}"
        f"QCheckBox::indicator:checked {{ background: {T.BRAND_BLUE};"
        f" border-color: {T.BRAND_BLUE};{tick_rule} }}"
        f"QCheckBox::indicator:checked:hover {{ background: {T.BRAND_BLUE_HOVER}; }}"
        f"QCheckBox::indicator:disabled {{ background: {T.FIELD}; border-color: {T.LINE}; }}"
    )


def apply_panel_input_theme(root: QWidget) -> None:
    """Give every unstyled spin box, combo, line edit and slider under
    ``root`` the house shape. A widget that already carries its own sheet
    keeps it. Run once over a finished panel."""
    pairs = (
        (QAbstractSpinBox, spin_box_qss()),
        (QComboBox, combo_box_qss()),
        (QLineEdit, T.INPUT_QSS),
        (QSlider, SLIDER_QSS),
        (QCheckBox, check_box_qss()),
    )
    for cls, qss in pairs:
        for widget in root.findChildren(cls):
            # A spin box's or a combo's own line edit is part of that control.
            if isinstance(widget.parentWidget(), (QAbstractSpinBox, QComboBox)):
                continue
            if not widget.styleSheet():
                widget.setStyleSheet(qss)


class PanelStatusLine(QWidget):
    """One line of feedback under a panel's controls: a glyph and words.

    ``kind`` is ``error`` (red warning glyph, red words), ``success`` (green
    check, green words), or ``hint`` / ``info`` (a muted dot glyph, muted
    words). An empty message hides the line."""

    _GLYPHS = {"error": "warning", "success": "check", "hint": "circle", "info": "circle"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 0)
        row.setSpacing(T.SPACE_CARD)
        self._glyph = QLabel(self)
        self._glyph.setFixedSize(_STATUS_GLYPH_PX, _STATUS_GLYPH_PX + 2)
        self._glyph.setStyleSheet("background: transparent; border: none;")
        row.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignTop)
        self._text = QLabel(self)
        self._text.setWordWrap(True)
        self._text.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self._text, 1)
        self._kind = "info"
        self.setVisible(False)

    def set_message(self, message: str, kind: str = "info") -> None:
        self._kind = kind if kind in self._GLYPHS else "info"
        # The glyph keeps the bright shade (3:1), the words take the text one (4.5:1).
        glyph_color = {"error": T.RED, "success": T.GREEN}.get(self._kind, T.INK_2)
        color = {"error": T.RED_TEXT, "success": T.GREEN_TEXT}.get(self._kind, T.INK_2)
        glyph_px = _STATUS_GLYPH_PX if self._kind in ("error", "success") else 6
        self._glyph.setPixmap(pixmap_for(self, self._GLYPHS[self._kind], glyph_px, qcolor(glyph_color)))
        self._glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._text.setStyleSheet(
            f"font-size: {T.FONT_BODY}px; color: {color}; background: transparent; border: none;"
        )
        self._text.setText(message)
        self.setVisible(bool(message))

    def kind(self) -> str:
        return self._kind

    def text(self) -> str:
        return self._text.text()


def make_notice_card(text: str, kind: str = "info", parent: QWidget | None = None,
                     glyph: str = "") -> QFrame:
    """A quiet message card: a glyph and words on the faint tint of its kind
    (info: the inset step, error: coral, warning: amber, success: green; a
    line and a faint ground in the hue), the AI Agent notice line."""
    card = QFrame(parent)
    card.setObjectName("panelNotice")
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    category, default_glyph = {
        "error": ("coral", "warning"),
        "warning": ("amber", "warning"),
        "success": ("green", "check"),
    }.get(kind, ("", "spark"))
    if category:
        tint, border = T.category_tint(category), T.category_line(category)
        ink = T.category_ink(category)
    else:
        tint, border, ink = T.INSET, T.LINE, T.INK_2
    glyph = glyph or default_glyph
    card.setStyleSheet(
        f"QFrame#panelNotice {{ background: {tint}; border: 1px solid {border};"
        f" border-radius: {T.RADIUS_CARD}px; }}"
        "QFrame#panelNotice QLabel { background: transparent; border: none; }"
    )
    row = QHBoxLayout(card)
    row.setContentsMargins(10, 8, 10, 8)
    row.setSpacing(T.SPACE_OUTER)
    icon = QLabel(card)
    icon.setObjectName("panelNoticeGlyph")
    icon.setPixmap(pixmap_for(card, glyph, _STATUS_GLYPH_PX, qcolor(ink)))
    icon.setFixedWidth(_STATUS_GLYPH_PX)
    row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
    label = QLabel(text, card)
    label.setObjectName("panelNoticeText")
    label.setWordWrap(True)
    label.setStyleSheet(f"font-size: {T.FONT_BODY}px; color: {T.RED_TEXT if kind == 'error' else T.INK};")
    row.addWidget(label, 1)
    card.text_label = label
    card.glyph_label = icon
    return card


def apply_swatch_style(button: QPushButton, color: QColor) -> None:
    """A class colour as a round dot with a paper ring, on a quiet button."""
    size = max(16, min(button.width(), button.height()) - 4)
    button.setIcon(make_color_dot_icon(color, selected=False, is_dark=T.DARK, dot_px=size))
    button.setIconSize(QSize(size, size))
    button.setStyleSheet(
        "QPushButton { background: transparent; border: 1px solid transparent; padding: 0px;"
        f" border-radius: {T.RADIUS_CHIP}px; }}"
        f"QPushButton:hover {{ background: {T.HOVER_ON}; }}"
        f"QPushButton:focus {{ border-color: {T.ACCENT_BORDER}; }}"
    )


def is_dark_palette(widget: QWidget) -> bool:
    """Return True when the widget's window color reads as dark."""
    c = widget.palette().color(QPalette.ColorRole.Window)
    return (c.red() + c.green() + c.blue()) / 3 < 128


def screen_device_pixel_ratio(widget: QWidget | None = None) -> float:
    """Device pixel ratio (2.0 on Retina, 1.25/1.5/2.0 on scaled Windows).

    With widget, the ratio of the screen that widget sits on: a laptop at
    150% next to a monitor at 100% gives two answers. Without, the primary
    screen. Falls back to 1.0 before a screen exists."""
    if widget is not None:
        try:
            ratio = widget.devicePixelRatioF()
            if ratio > 0:
                return float(ratio)
        except (AttributeError, RuntimeError):
            pass
    app = QApplication.instance()
    if app is not None:
        screen = app.primaryScreen()
        if screen is not None:
            ratio = screen.devicePixelRatio()
            if ratio > 0:
                return float(ratio)
    return 1.0


def screen_for_dialog(parent: QWidget | None):
    """The screen a dialog parented to parent opens on (Qt centres it on
    its parent window), not the primary one: on a docked laptop the primary
    is often the big monitor. Without a parent, the screen under the mouse.
    QWidget.screen() only exists from Qt 5.14, hence the getattr."""
    if parent is not None:
        try:
            window = parent.window()
            screen_of = getattr(window, "screen", None)
            screen = screen_of() if callable(screen_of) else None
            if screen is None:
                handle = window.windowHandle()
                screen = handle.screen() if handle is not None else None
            if screen is not None:
                return screen
        except RuntimeError:
            pass
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def make_hidpi_pixmap(logical_px: int, dpr: float | None = None) -> QPixmap:
    """Transparent pixmap sized for the current display scale.

    Renders into ``logical_px * dpr`` physical pixels and tags the pixmap
    with its dpr, so a QPainter draws in logical coordinates while the
    output stays crisp at any scale (Retina, Windows 125/150/200%). Without
    this, a fixed-size pixmap gets stretched by Qt and looks pixelated.
    """
    if dpr is None:
        dpr = screen_device_pixel_ratio()
    physical = max(1, round(logical_px * dpr))
    pm = QPixmap(physical, physical)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    return pm


def make_color_dot_icon(
    color: QColor,
    selected: bool,
    is_dark: bool,
    dot_px: int = 22,
) -> QIcon:
    """A colour swatch: a round dot with a paper ring and a hairline around it.
    Selected adds a ring in the picked hue's ink outside the paper ring, the
    shared picked look (a thin border in the hue), so a picked dot never wears
    the blue of the keyboard focus ring drawn around the same button.
    ``is_dark`` is kept for callers; the paper and hairline come from tokens."""
    s = dot_px
    pm = make_hidpi_pixmap(s)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    center = QPointF(s / 2, s / 2)
    if selected:
        halo = QPen(qcolor(T.category_ink(T.PICKED_HUE)))
        halo.setWidthF(1.8)
        p.setPen(halo)
        p.setBrush(QBrush(qcolor(T.SURFACE)))
        p.drawEllipse(center, s * 0.46, s * 0.46)
    else:
        p.setPen(QPen(qcolor(T.LINE_STRONG), 1.0))
        p.setBrush(QBrush(qcolor(T.SURFACE)))
        p.drawEllipse(center, s * 0.42, s * 0.42)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(color)))
    p.drawEllipse(center, s * 0.32, s * 0.32)
    p.end()
    return QIcon(pm)


def make_custom_color_icon(is_dark: bool, dot_px: int = 22) -> QIcon:
    """The add-a-colour swatch: a dashed hairline circle with a plus."""
    s = dot_px
    pm = make_hidpi_pixmap(s)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    center = QPointF(s / 2, s / 2)
    ring = QPen(qcolor(T.INK_3), 1.0)
    ring.setStyle(Qt.PenStyle.DashLine)
    p.setPen(ring)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(center, s * 0.40, s * 0.40)
    pen = QPen(qcolor(T.INK_2))
    pen.setWidthF(1.4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx = s / 2
    arm = s * 0.18
    p.drawLine(QPointF(cx - arm, cx), QPointF(cx + arm, cx))
    p.drawLine(QPointF(cx, cx - arm), QPointF(cx, cx + arm))
    p.end()
    return QIcon(pm)
