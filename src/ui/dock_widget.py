from __future__ import annotations

import html
import os
import re
import tempfile

from qgis.core import QgsProject
from qgis.PyQt.QtCore import (
    QPoint,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QShortcut,
    QStyle,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ..core import qt_compat as QtC
from ..core.activation_manager import (
    get_subscribe_url,
    get_tutorial_url,
    has_consent,
)
from ..core.i18n import tr
from ..core.prompt_presets import format_template_prompt
from ..core.reference_image_store import ReferenceImageStore
from .credit_ring import CreditRing
from .markup_panel import MarkupPanel
from .panel_helpers import (
    apply_swatch_style,
    build_panel_header,
    make_section_header,
    panel_section_label,
)
from .reference_images_widget import ReferenceImagesWidget
from .vectorize_panel import VectorizePanel

# ---------------------------------------------------------------------------
# Brand colors (Material Design 2 - shared with AI Segmentation)
# ---------------------------------------------------------------------------
BRAND_GREEN = "#2e7d32"
BRAND_GREEN_HOVER = "#1b5e20"
BRAND_GREEN_DISABLED = "#c8e6c9"
BRAND_BLUE = "#1976d2"
BRAND_BLUE_HOVER = "#1565c0"
BRAND_RED = "#d32f2f"
BRAND_RED_HOVER = "#b71c1c"
BRAND_GRAY = "#757575"
BRAND_GRAY_HOVER = "#616161"
BRAND_DISABLED = "#b0bec5"
DISABLED_TEXT = "#666666"
ERROR_TEXT = "#ef5350"
SUCCESS_TEXT = "#66bb6a"

MAX_PROMPT_CHARS = 2000


TERRALAB_URL = (
    "https://terra-lab.ai/ai-edit"
    "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=dock_branding"
)
SUPPORT_EMAIL = "yvann.barbot@terra-lab.ai"

# ---------------------------------------------------------------------------
# Reusable QSS style constants (design system)
# ---------------------------------------------------------------------------
_BTN_GREEN = (
    f"QPushButton {{ background-color: {BRAND_GREEN}; color: #000000;"
    f" padding: 8px 16px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GREEN_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_GREEN_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_GREEN_AUTH = (
    f"QPushButton {{ background-color: {BRAND_GREEN}; color: #000000; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GREEN_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 6px 12px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE_AUTH = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
)

_BTN_GRAY = (
    f"QPushButton {{ background-color: {BRAND_GRAY}; color: #000000;"
    f" padding: 4px 8px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GRAY_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT}; }}"
)

_BTN_DISABLED = (
    f"QPushButton {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT};"
    f" padding: 8px 16px; }}"
)

_BTN_GHOST = (
    "QPushButton { background-color: transparent; color: palette(text);"
    " padding: 8px 16px; border-radius: 4px;"
    " border: 1px solid rgba(128, 128, 128, 0.35); }"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.15);"
    " border: 1px solid rgba(128, 128, 128, 0.5); }"
    f"QPushButton:disabled {{ background-color: rgba(128, 128, 128, 0.08);"
    f" border: 1px solid rgba(128, 128, 128, 0.15); color: {DISABLED_TEXT}; }}"
)

# Footer icon buttons (gear / question mark) - slim toolbuttons.
# Hover/pressed states are gated by the dynamic ``hover`` property
# rather than Qt's :hover pseudo-state. With InstantPopup menus, Qt
# fails to clear :hover once the menu closes (the synthetic Leave
# event is eaten by the popup), so the button stays tinted. The
# property-driven approach lets us force-reset the visual via
# ``_FooterIconButton.set_hovered(False)``.
_FOOTER_ICON_BTN_STYLE = (
    "QToolButton { background: transparent; border: none; padding: 6px 10px;"
    " font-size: 22px; font-weight: 600;"
    " color: palette(text); border-radius: 4px; }"
    'QToolButton[hover="true"] { background: rgba(128,128,128,0.15); }'
    "QToolButton::menu-indicator { image: none; width: 0; }"
)

_FOOTER_MENU_STYLE = (
    "QMenu { background: palette(base); border: 1px solid rgba(128,128,128,0.35);"
    " border-radius: 6px; padding: 4px; }"
    "QMenu::item { background: transparent; padding: 6px 14px; border-radius: 4px;"
    " color: palette(text); }"
    "QMenu::item:selected { background: rgba(128,128,128,0.18); }"
)

_INSTRUCTION_BOX = (
    "QLabel {"
    "  background-color: rgba(128, 128, 128, 0.12);"
    "  border: 1px solid rgba(128, 128, 128, 0.25);"
    "  border-radius: 4px;"
    "  padding: 8px;"
    "  font-size: 10px;"
    "  color: palette(text);"
    "}"
)


def _picture_icon() -> QIcon:
    """Return the 'attach reference image' glyph as a vector icon.

    Loads the bundled SVG (resources/icons/image.svg) so QIcon's built-in
    SVG renderer re-rasterises it crisply at whatever size the QToolButton
    requests - matching the visual weight of the ⚙ / ? footer glyphs.
    """
    plugin_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return QIcon(os.path.join(plugin_root, "resources", "icons", "image.svg"))


def _pencil_icon(ink: QColor) -> QIcon:
    """Tilted pencil outline used by both the prompt-row chip and the
    bottom-bar Mark up button. Drawn at 2x for crisp rendering at 20px.
    """
    from qgis.PyQt.QtCore import QPointF, Qt
    from qgis.PyQt.QtGui import QPainter, QPen, QPixmap, QPolygonF
    size = 40
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(ink)
    pen.setWidthF(2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    body = QPolygonF([
        QPointF(29, 7),
        QPointF(34, 12),
        QPointF(14, 32),
        QPointF(7, 34),
        QPointF(9, 27),
    ])
    p.drawPolygon(body)
    p.drawLine(QPointF(24, 12), QPointF(29, 17))
    p.end()
    return QIcon(pm)


_make_section_header = make_section_header  # backward-compat alias


_IMAGE_DROP_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def _image_paths_from_mime(mime) -> list[str]:
    if not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_DROP_EXTS:
            out.append(path)
    return out


class _FooterIconButton(QToolButton):
    """QToolButton whose hover tint is driven by an explicit ``hover``
    dynamic property rather than Qt's :hover pseudo-state.

    With InstantPopup menus, Qt fails to fire the synthetic Leave event
    after the menu closes, so the button stays visually pressed/hovered
    until the next real mouse move. Tracking hover ourselves lets us
    force-reset it on ``menu.aboutToHide``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("hover", False)

    def set_hovered(self, hovered: bool) -> None:
        if bool(self.property("hover")) == hovered:
            return
        self.setProperty("hover", hovered)
        # Re-polish so the [hover="true"] selector takes effect.
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def enterEvent(self, event):  # noqa: N802
        self.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.set_hovered(False)
        super().leaveEvent(event)


_HEX_RX = re.compile(r"#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b")


def _expand_hex(hex_text: str) -> str:
    """Expand `#RGB` to `#RRGGBB`. Returns input unchanged for 6-digit hex."""
    h = hex_text.lstrip("#")
    if len(h) == 3:
        return "#" + "".join(c * 2 for c in h)
    return "#" + h


def _contrast_text_for(hex_text: str) -> str:
    """Pick black or white text for readability against `hex_text` background.
    Uses standard relative-luminance threshold."""
    h = _expand_hex(hex_text).lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#000000" if luminance > 0.55 else "#FFFFFF"


class _PromptHighlighter(QSyntaxHighlighter):
    """Paints `#RRGGBB` / `#RGB` hex codes with their own color as background,
    text flipped to black or white per luminance. Makes a color list in a
    template visually scannable without leaving the textbox."""

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        if not text:
            return
        for match in _HEX_RX.finditer(text):
            hex_text = match.group(0)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(_expand_hex(hex_text)))
            fmt.setForeground(QColor(_contrast_text_for(hex_text)))
            fmt.setFontWeight(QFont.Weight.Bold)
            self.setFormat(match.start(), match.end() - match.start(), fmt)


class _SubmitTextEdit(QTextEdit):
    """Borderless QTextEdit used inside _PromptContainer.

    - Enter submits, Shift+Enter inserts newline.
    - Image file paths in the clipboard or raw image data (e.g. a screenshot
      copied from Preview) are routed to the references store via
      ``images_pasted`` instead of being inserted as an emoji-doc icon.
    - Drag-drop is delegated to the parent container by disabling
      ``acceptDrops``, so the whole bordered area lights up rather than just
      the text area.
    - No QSS is applied here on purpose: a stylesheet on QTextEdit forces Qt
      into its own renderer, which loses the native (e.g. macOS accent-blue)
      caret. The frame and transparent background are set programmatically.
    """

    submitted = pyqtSignal()
    images_pasted = pyqtSignal(list)

    _INNER_STYLE = (
        "QTextEdit { background: transparent; padding: 4px; }"
        # Some Qt styles still reserve a thin strip for the horizontal
        # scrollbar even with ScrollBarAlwaysOff; force its height to 0.
        "QTextEdit QScrollBar:horizontal { height: 0px; margin: 0px; }"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(False)
        # Remove the native frame - the surrounding _PromptContainer paints it.
        self.setFrameShape(QtC.FrameNoFrame)
        # Transparent background only; keep palette untouched so the QTextEdit
        # keeps its native caret (the accent-blue insertion bar on macOS).
        # Setting QPalette.Base ourselves silently disables the caret on some
        # Qt builds, hence the QSS-only path here.
        self.setStyleSheet(self._INNER_STYLE)
        # Wrap long tokens (URLs, glued words) mid-character so the line never
        # exceeds the box width, and kill the horizontal scrollbar - Qt
        # otherwise reserves space for it (the "invisible bar" at the bottom).
        self.setLineWrapMode(QtC.LineWrapWidgetWidth)
        self.setWordWrapMode(QtC.WrapAtWordBoundaryOrAnywhere)
        # Paint hex codes (`#RRGGBB`) in the textbox with that color as
        # background so color-list templates are scannable at a glance.
        self._hex_highlighter = _PromptHighlighter(self.document())
        self.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        # Force plain text on paste: rich-text from a browser or markdown
        # source can carry <pre> / white-space:nowrap that defeats wrapping,
        # leaving the line wider than the viewport even with wrap modes set.
        self.setAcceptRichText(False)

    def keyPressEvent(self, event):  # noqa: N802
        if (
            event.key() in (QtC.Key_Return, QtC.Key_Enter)
            and not event.modifiers() & QtC.ShiftModifier  # noqa: W503
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source):  # noqa: N802
        if source.hasImage() or _image_paths_from_mime(source):
            return False
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):  # noqa: N802
        paths = _image_paths_from_mime(source)
        if paths:
            self.images_pasted.emit(paths)
            return
        if source.hasImage():
            self._emit_clipboard_image(source)
            return
        super().insertFromMimeData(source)

    def _emit_clipboard_image(self, source) -> None:
        """Save raw clipboard image data to a temp PNG and forward as a path."""
        image = source.imageData()
        if image is None:
            return
        if not isinstance(image, QImage):
            image = QImage(image)
        if image.isNull():
            return
        fd, tmp_path = tempfile.mkstemp(prefix="ai-edit-paste-", suffix=".png")
        os.close(fd)
        saved = False
        try:
            saved = bool(image.save(tmp_path, "PNG"))
            if saved:
                self.images_pasted.emit([tmp_path])
        finally:
            # Listener processed the path synchronously (direct connection);
            # the refs store has its own compressed copy by now.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class _ResolutionMenuItem(QWidget):
    """Custom widget for one resolution row inside the resolution QMenu.

    Layout:   [ ✓ ]  Label                          N credits

    The leading checkmark column is fixed-width so all three rows align.
    Locked rows (free-tier 2K / 4K) render in a muted color but stay
    clickable - the click still fires so the dock widget can show the
    "Subscribe for higher resolution" banner.

    QMenu does not paint its selection highlight under QWidgetAction items,
    so the hover background is drawn by the widget itself via a :hover
    stylesheet. Child labels are marked transparent-for-mouse so the
    parent receives all hover/click events even when the cursor is over
    a QLabel.
    """

    clicked = pyqtSignal()

    _ITEM_STYLE = (
        "QWidget#resolutionMenuItem { background: transparent; border-radius: 4px; }"
        "QWidget#resolutionMenuItem:hover { background: rgba(128,128,128,0.20); }"
    )

    def __init__(
        self,
        label: str,
        credits: int,
        selected: bool,
        locked: bool,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("resolutionMenuItem")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setStyleSheet(self._ITEM_STYLE)
        self.setCursor(QtC.PointingHandCursor)
        self.setMinimumHeight(26)
        if locked:
            self.setToolTip(tr("Subscribe for higher resolution"))

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 16, 4)
        row.setSpacing(8)

        check = QLabel("✓" if selected else "", self)
        check.setFixedWidth(12)
        check.setStyleSheet(
            "font-size: 12px; color: palette(text); background: transparent;"
        )
        check.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        row.addWidget(check)

        muted = f"color: {DISABLED_TEXT};" if locked else "color: palette(text);"
        name = QLabel(label, self)
        name.setStyleSheet(f"font-size: 12px; background: transparent; {muted}")
        name.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        row.addWidget(name)

        row.addStretch()

        cost_color = (
            f"color: {DISABLED_TEXT};"
            if locked
            else "color: rgba(128,128,128,0.85);"
        )
        cost = QLabel(tr("{n} credits").format(n=credits), self)
        cost.setStyleSheet(f"font-size: 11px; background: transparent; {cost_color}")
        cost.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        row.addWidget(cost)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == QtC.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _PromptContainer(QFrame):
    """Bordered frame wrapping prompt textbox + refs strip + footer row.

    Footer layout (bottom row):

        [Templates]                              [ 2K  ⌄ ]   [ + ]

    The whole frame is the drop target so dragging anywhere over it lights up
    a single coherent area, matching the ChatGPT-style attachment flow.
    """

    files_dropped = pyqtSignal(list)
    attach_clicked = pyqtSignal()
    templates_clicked = pyqtSignal()
    resolution_changed = pyqtSignal(str)
    markup_clicked = pyqtSignal()

    _NORMAL_STYLE = (
        "QFrame#promptContainer { border: 1px solid rgba(128,128,128,0.3);"
        " border-radius: 4px; background-color: rgba(128,128,128,0.06); }"
    )
    _READONLY_STYLE = (
        "QFrame#promptContainer { border: 1px solid rgba(128,128,128,0.3);"
        " border-radius: 4px; background-color: rgba(128,128,128,0.10); }"
    )
    _ATTACH_BTN_STYLE = (
        "QToolButton { background: transparent; border: none; padding: 1px 8px;"
        " font-size: 14px; font-weight: normal;"
        " color: rgba(128,128,128,0.75); }"
        "QToolButton:hover { color: palette(text);"
        " background: rgba(128,128,128,0.15); border-radius: 4px; }"
        "QToolButton:disabled { color: rgba(128,128,128,0.35); background: transparent; }"
    )
    _FOOTER_TEXT_BTN_STYLE = (
        "QToolButton { background: transparent; border: none; padding: 2px 6px;"
        " font-size: 11px; color: palette(text); }"
        "QToolButton:hover { color: palette(text);"
        " background: rgba(128,128,128,0.15); border-radius: 4px; }"
        "QToolButton:disabled { color: rgba(128,128,128,0.45); background: transparent; }"
        "QToolButton::menu-indicator { image: none; width: 0; }"
    )
    # Property-driven hover variant for buttons that pop a QMenu - see
    # _FooterIconButton for the rationale (Qt eats the synthetic Leave
    # event when a popup closes, leaving Qt's :hover stuck on).
    _FOOTER_TEXT_BTN_HOVERPROP_STYLE = (
        "QToolButton { background: transparent; border: none; padding: 2px 6px;"
        " font-size: 11px; color: palette(text); border-radius: 4px; }"
        'QToolButton[hover="true"] { background: rgba(128,128,128,0.15); }'
        "QToolButton:disabled { color: rgba(128,128,128,0.45); background: transparent; }"
        "QToolButton::menu-indicator { image: none; width: 0; }"
    )
    _MENU_STYLE = (
        "QMenu { background: palette(base); border: 1px solid rgba(128,128,128,0.35);"
        " border-radius: 6px; padding: 4px; }"
        "QMenu::item { background: transparent; padding: 0; }"
        "QMenu::item:selected { background: rgba(128,128,128,0.18); border-radius: 4px; }"
    )

    def __init__(self, text_edit: _SubmitTextEdit, parent=None):
        super().__init__(parent)
        self.setObjectName("promptContainer")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setAcceptDrops(True)

        self._text_edit = text_edit
        self._base_style = self._NORMAL_STYLE
        self._readonly = False

        # Resolution state mirrored from the dock widget so the popup can be
        # rebuilt locally without re-reaching into the parent.
        self._selected_resolution = "2K"
        self._resolution_costs: dict[str, int] = {"1K": 20, "2K": 30, "4K": 40}
        self._free_tier = False

        # No graphics effect attached at init: applying QGraphicsDropShadowEffect
        # to a parent of a QTextEdit silently breaks the text-insertion caret
        # (the effect pipeline intercepts the QTextEdit's blink timer paint).
        # The drag-over glow is attached on dragEnterEvent and detached on
        # dragLeave / drop - see _set_glow.

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)

        # Slot index 0 is reserved for the refs strip when injected.
        layout.addWidget(text_edit)

        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(0, 0, 0, 0)
        footer_row.setSpacing(0)

        self._templates_btn = QToolButton(self)
        self._templates_btn.setText(tr("Prompt library"))
        self._templates_btn.setToolTip(tr("Open the prompt library: recent, favorites, and templates"))
        self._templates_btn.setCursor(QtC.PointingHandCursor)
        self._templates_btn.setStyleSheet(self._FOOTER_TEXT_BTN_STYLE)
        self._templates_btn.clicked.connect(self.templates_clicked.emit)
        footer_row.addWidget(self._templates_btn)

        footer_row.addStretch()

        self._resolution_menu = QMenu(self)
        self._resolution_menu.setStyleSheet(self._MENU_STYLE)
        # Allow per-action tooltips (Qt swallows them by default in QMenu).
        self._resolution_menu.setToolTipsVisible(True)
        self._resolution_btn = _FooterIconButton(self)
        self._resolution_btn.setToolTip(
            tr("Output resolution. Higher = sharper, more precise edits.")
        )
        self._resolution_btn.setCursor(QtC.PointingHandCursor)
        self._resolution_btn.setStyleSheet(self._FOOTER_TEXT_BTN_HOVERPROP_STYLE)
        self._resolution_btn.clicked.connect(self._show_resolution_menu)
        # Force the hover tint off when the popup closes - Qt does not
        # synthesise a Leave event in this case (same fix as the help menu).
        self._resolution_menu.aboutToHide.connect(
            lambda btn=self._resolution_btn: (btn.setDown(False), btn.set_hovered(False))
        )
        footer_row.addWidget(self._resolution_btn)
        self._rebuild_resolution_menu()
        self._update_resolution_label()

        # Markup chip: icon-only, same visual weight as the attach button.
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        self._markup_chip = QToolButton(self)
        self._markup_chip.setIcon(_pencil_icon(ink))
        self._markup_chip.setIconSize(QSize(20, 20))
        self._markup_chip.setCursor(QtC.PointingHandCursor)
        self._markup_chip.setStyleSheet(self._ATTACH_BTN_STYLE)
        self._markup_chip.setToolTip(
            tr("Mark up: sketch hints on the map for your next prompt.")
        )
        self._markup_chip.clicked.connect(self.markup_clicked.emit)
        footer_row.addWidget(self._markup_chip)

        self._attach_btn = QToolButton(self)
        self._attach_btn.setIcon(_picture_icon())
        self._attach_btn.setIconSize(QSize(20, 20))
        self._attach_btn.setToolTip(tr("Add reference image"))
        self._attach_btn.setCursor(QtC.PointingHandCursor)
        self._attach_btn.setStyleSheet(self._ATTACH_BTN_STYLE)
        self._attach_btn.clicked.connect(self.attach_clicked.emit)
        footer_row.addWidget(self._attach_btn)

        layout.addLayout(footer_row)
        self.setStyleSheet(self._base_style)

    # -- public API --------------------------------------------------------

    def insert_refs_widget(self, widget: QWidget) -> None:
        """Move a shared refs widget into this container at the top slot."""
        old_parent = widget.parentWidget()
        if old_parent is not None and old_parent is not self:
            old_layout = old_parent.layout()
            if old_layout is not None:
                old_layout.removeWidget(widget)
        widget.setParent(self)
        self.layout().insertWidget(0, widget)

    def set_readonly(self, readonly: bool) -> None:
        self._readonly = readonly
        self._base_style = self._READONLY_STYLE if readonly else self._NORMAL_STYLE
        self.setStyleSheet(self._base_style)
        self._text_edit.setReadOnly(readonly)
        # Visible-but-disabled while a generation runs - matches the Claude
        # chat input pattern of leaving the footer chrome in place.
        # Templates stays clickable so the user can still browse the library
        # mid-generation; the dialog itself opens in view-only mode.
        self._templates_btn.setEnabled(True)
        self._templates_btn.setToolTip(
            tr("Browse the prompt library (view only while generating)")
            if readonly
            else tr("Open the prompt library: recent, favorites, and templates")
        )
        self._resolution_btn.setEnabled(not readonly)
        self._markup_chip.setEnabled(not readonly)
        self._attach_btn.setEnabled(not readonly)

    def is_readonly(self) -> bool:
        return self._readonly

    def set_attach_enabled(self, enabled: bool) -> None:
        """Hide the + button when the refs store is at capacity.

        Readonly state is handled by set_readonly, which keeps the button
        visible-but-disabled - do not gate visibility on readonly here.
        """
        self._attach_btn.setVisible(enabled)

    def set_resolution_state(
        self,
        selected: str,
        costs: dict[str, int] | None,
        free_tier: bool,
    ) -> None:
        """Refresh the trigger label, the menu items and their lock state."""
        self._selected_resolution = selected
        if costs:
            self._resolution_costs = costs
        self._free_tier = free_tier
        self._rebuild_resolution_menu()
        self._update_resolution_label()

    # -- resolution menu internals ----------------------------------------

    def _update_resolution_label(self) -> None:
        # ▾ (U+25BE) sits on the text baseline; ⌄ (U+2304) renders too low
        # in most system fonts and breaks the visual alignment.
        self._resolution_btn.setText(f"{self._selected_resolution}  ▾")

    def _rebuild_resolution_menu(self) -> None:
        self._resolution_menu.clear()
        for res in ("1K", "2K", "4K"):
            locked = self._free_tier and res != "1K"
            selected = res == self._selected_resolution
            credits = self._resolution_costs.get(res, 0)
            widget = _ResolutionMenuItem(
                res, credits, selected, locked, self._resolution_menu
            )
            widget.clicked.connect(lambda r=res: self._on_menu_item_clicked(r))
            action = QWidgetAction(self._resolution_menu)
            action.setDefaultWidget(widget)
            if locked:
                action.setToolTip(tr("Subscribe for higher resolution"))
            self._resolution_menu.addAction(action)

    def _on_menu_item_clicked(self, label: str) -> None:
        self._resolution_menu.close()
        self.resolution_changed.emit(label)

    def _show_resolution_menu(self) -> None:
        if not self._resolution_btn.isEnabled():
            return
        anchor = self._resolution_btn.mapToGlobal(QPoint(0, 0))
        menu_height = self._resolution_menu.sizeHint().height()
        anchor.setY(anchor.y() - menu_height)
        self._resolution_menu.popup(anchor)

    # -- drag and drop -----------------------------------------------------

    def _set_glow(self, active: bool) -> None:
        if active:
            effect = QGraphicsDropShadowEffect(self)
            effect.setBlurRadius(14)
            effect.setOffset(0, 0)
            effect.setColor(QColor(25, 118, 210, 200))
            self.setGraphicsEffect(effect)
        else:
            # Detaching the effect restores native QTextEdit rendering and the
            # accent-blue caret blink.
            self.setGraphicsEffect(None)

    def dragEnterEvent(self, event):  # noqa: N802
        if self._readonly:
            return
        if _image_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            self._set_glow(True)

    def dragMoveEvent(self, event):  # noqa: N802
        if self._readonly:
            return
        if _image_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):  # noqa: N802
        self._set_glow(False)
        event.accept()

    def dropEvent(self, event):  # noqa: N802
        paths = _image_paths_from_mime(event.mimeData())
        self._set_glow(False)
        if paths and not self._readonly:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()


class AIEditDockWidget(QDockWidget):
    """Dock widget with prompt-first flow.

    The prompt view is always visible after activation. The selection tool
    stays active so the user can draw a zone at any time. The Generate
    button is disabled (shows "Select your zone") until a zone is drawn.
    """

    stop_clicked = pyqtSignal()
    generate_clicked = pyqtSignal(str)
    retry_clicked = pyqtSignal(str)       # retry on same zone with (possibly edited) prompt
    activation_attempted = pyqtSignal(str)
    change_key_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    launch_clicked = pyqtSignal()          # user clicked "Launch AI Edit" on entry screen
    exit_clicked = pyqtSignal()            # user clicked the always-visible Exit button
    zone_clear_requested = pyqtSignal()    # Escape pressed while a zone was selected
    markup_clicked = pyqtSignal()          # user picked Tools → Mark up
    vectorize_clicked = pyqtSignal()       # user picked Tools → Vectorize
    # (layer_id, color_hex) from the "Extract regions" CTA in the result panel.
    vectorize_suggestion_clicked = pyqtSignal(str, str)
    markup_done_clicked = pyqtSignal()     # user clicked Done in Mark up panel
    markup_clear_clicked = pyqtSignal()    # user clicked Clear all in Mark up
    markup_tool_changed = pyqtSignal(str)  # 'pencil' | 'arrow' | 'circle'
    markup_color_changed = pyqtSignal(QColor)
    vectorize_done_clicked = pyqtSignal()  # user clicked Done in Vectorize panel
    # (template_id, template_name) for analytics - id is stable, name is human-readable.
    template_selected = pyqtSignal(str, str)
    # Fired when the prompt library opens; plugin listens and kicks off a
    # background catalog refetch so the NEXT open shows the latest server
    # state. Stale-while-revalidate: this open uses whatever the dock has
    # cached, the refetch updates `self._server_catalog` for next time.
    catalog_refresh_requested = pyqtSignal()

    def __init__(self, parent=None, reference_store: ReferenceImageStore | None = None):
        super().__init__(tr("AI Edit by TerraLab"), parent)
        self.setAllowedAreas(QtC.LeftDockWidgetArea | QtC.RightDockWidgetArea)
        # 300px keeps the footer (Unlock pill + 3 right-aligned icons) fully
        # visible even after the credit counter hides on narrow docks.
        self.setMinimumWidth(300)
        self._reference_store = reference_store
        # Wired by plugin.py via set_library_dependencies - the Prompt library
        # dialog uses these to sync Recent/Favorites with the server. The
        # server catalog (richer presets with demo image URLs) is set by
        # set_server_catalog after the plugin's startup fetch resolves.
        self._library_client = None
        self._library_auth_manager = None
        self._server_catalog: dict | None = None

        # Armed template: set when the user picks a preset from the prompt
        # library so edits to the prompt text don't drop the association
        # (used by plugin.py to keep vector hints + Vectorize CTA active).
        self._active_template_id: str | None = None
        self._active_template_name: str | None = None

        # Global Escape: exit the flow no matter where focus is (canvas while
        # drawing a zone, prompt textarea, progress bar, etc.). WindowShortcut
        # context lets the shortcut fire on the parent main window's key events
        # via ShortcutOverride, which beats the map tool's local Escape handler.
        self._escape_shortcut = QShortcut(QKeySequence(QtC.Key_Escape), self)
        self._escape_shortcut.setContext(QtC.WindowShortcut)
        self._escape_shortcut.activated.connect(self._on_escape_pressed)

        # Global Enter / Return: launch generation from anywhere in the dock.
        # The prompt textarea consumes Return in its own keyPressEvent so this
        # shortcut only fires when focus is on a non-text-input child.
        self._generate_shortcut_return = QShortcut(QKeySequence(QtC.Key_Return), self)
        self._generate_shortcut_return.setContext(QtC.WindowShortcut)
        self._generate_shortcut_return.activated.connect(self._on_generate_shortcut)
        self._generate_shortcut_enter = QShortcut(QKeySequence(QtC.Key_Enter), self)
        self._generate_shortcut_enter.setContext(QtC.WindowShortcut)
        self._generate_shortcut_enter.activated.connect(self._on_generate_shortcut)

        self._setup_title_bar()

        # Main content
        main_widget = QWidget()
        layout = QVBoxLayout(main_widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # --- Activation section ---
        self._activation_widget = self._build_activation_section()
        layout.addWidget(self._activation_widget)

        # --- Main content section ---
        self._main_widget = QWidget()
        main_layout = QVBoxLayout(self._main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        # Warning widget (no visible layer) - above prompt
        self._warning_widget = self._build_warning_widget()
        self._warning_widget.setVisible(False)
        main_layout.addWidget(self._warning_widget)

        # --- Launch section (entry screen, matches AI Segmentation pattern) ---
        self._launch_section = QWidget()
        launch_layout = QVBoxLayout(self._launch_section)
        launch_layout.setContentsMargins(0, 0, 0, 0)
        launch_layout.setSpacing(8)

        self._launch_btn = QPushButton(tr("Launch AI Edit"))
        self._launch_btn.setToolTip(tr("Start a new AI edit session"))
        self._launch_btn.setCursor(QtC.PointingHandCursor)
        self._launch_btn.setMinimumHeight(40)
        self._launch_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._launch_btn.clicked.connect(self.launch_clicked.emit)
        launch_layout.addWidget(self._launch_btn)

        self._launch_section.setVisible(False)
        main_layout.addWidget(self._launch_section)

        # --- Select-zone section (in-between state: invites user to draw on canvas) ---
        self._select_zone_section = QWidget()
        sz_layout = QVBoxLayout(self._select_zone_section)
        sz_layout.setContentsMargins(0, 0, 0, 0)
        sz_layout.setSpacing(6)

        self._select_zone_header = _make_section_header(tr("Select your zone"))
        sz_layout.addWidget(self._select_zone_header)

        self._select_zone_hint = QLabel(
            tr("Click and drag on the map to draw a rectangular zone.")
        )
        self._select_zone_hint.setWordWrap(True)
        self._select_zone_hint.setStyleSheet(_INSTRUCTION_BOX)
        sz_layout.addWidget(self._select_zone_hint)

        self._select_zone_section.setVisible(False)
        main_layout.addWidget(self._select_zone_section)

        # --- Prompt section (shown after zone selected) ---
        self._prompt_section = QWidget()
        self._prompt_section.setContentsMargins(0, 0, 0, 0)
        self._prompt_layout = QVBoxLayout(self._prompt_section)
        self._prompt_layout.setContentsMargins(0, 0, 0, 0)
        self._prompt_layout.setSpacing(6)

        self._prompt_header = _make_section_header(tr("What should AI change?"))
        self._prompt_header.setVisible(True)
        self._prompt_layout.addWidget(self._prompt_header)

        self._prompt_input = _SubmitTextEdit()
        self._prompt_input.setPlaceholderText(
            tr("type your prompt or pick from the library...")
        )
        self._prompt_input.document().setDocumentMargin(0)
        self._prompt_input.setMinimumHeight(60)
        self._prompt_input.setMaximumHeight(60)
        self._prompt_input.textChanged.connect(self._on_prompt_changed)
        self._prompt_input.submitted.connect(self._on_generate_clicked)
        self._prompt_input.document().documentLayout().documentSizeChanged.connect(
            self._adjust_prompt_height
        )
        self._prompt_container = _PromptContainer(self._prompt_input, self._prompt_section)
        self._prompt_container.templates_clicked.connect(self._on_browse_templates_clicked)
        self._prompt_container.resolution_changed.connect(self._on_resolution_selected)
        self._prompt_container.markup_clicked.connect(self.markup_clicked.emit)
        self._prompt_layout.addWidget(self._prompt_container)

        # Hidden by default: revealed by set_zone_selected() once the user
        # draws a rectangle. Initial dock state only shows the "Select your
        # zone" button.
        self._prompt_section.setVisible(False)
        main_layout.addWidget(self._prompt_section)

        # Reference images widget - created once, moved between the prompt
        # container and the result container as state changes.
        if self._reference_store is not None:
            self._reference_widget = ReferenceImagesWidget(
                self._reference_store, self
            )
            self._reference_widget.error_occurred.connect(
                lambda msg: self._show_status_box(msg, "error")
            )
            self._reference_widget.error_cleared.connect(self._hide_status_box)
            self._reference_widget.images_changed.connect(self._sync_attach_buttons)
            # Forward container actions: drop on container + paste in textbox +
            # paperclip click all funnel into the reference widget.
            self._prompt_container.files_dropped.connect(self._reference_widget.add_paths)
            self._prompt_container.attach_clicked.connect(
                self._reference_widget.open_file_picker
            )
            self._prompt_input.images_pasted.connect(self._reference_widget.add_paths)
            # Idle: keep the widget out of the title bar by hiding it until placed.
            self._reference_widget.setVisible(False)
        else:
            self._reference_widget = None

        # Consent checkbox (shown only until first generation). Use native
        # QGIS style so the checkmark glyph renders correctly.
        self._consent_check = QCheckBox()
        self._consent_check.setText("")  # text set via label below
        consent_layout = QHBoxLayout()
        consent_layout.setContentsMargins(0, 0, 0, 0)
        consent_layout.setSpacing(4)
        consent_layout.addWidget(self._consent_check, 0)
        _terms_url = (
            "https://terra-lab.ai/terms-of-sale"
            "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_terms"
        )
        _privacy_url = (
            "https://terra-lab.ai/privacy-policy"
            "?utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=consent_privacy"
        )
        # Short consent line with clickable Terms + Privacy links. The full
        # disclosure (upload, EU storage, retention) lives behind those links
        # so the panel stays calm. {terms} and {privacy} are placeholders so
        # the linked words can be reordered in translations.
        _consent_template = tr(
            "I agree to the {terms} and {privacy}"
        )
        _terms_link = (
            f'<a href="{_terms_url}" style="color: {BRAND_BLUE};">{tr("Terms")}</a>'
        )
        _privacy_link = (
            f'<a href="{_privacy_url}" style="color: {BRAND_BLUE};">{tr("Privacy")}</a>'
        )
        consent_text = QLabel(
            _consent_template.format(terms=_terms_link, privacy=_privacy_link)
        )
        consent_text.setOpenExternalLinks(True)
        consent_text.setWordWrap(True)
        consent_text.setStyleSheet("font-size: 11px; color: palette(text);")
        consent_layout.addWidget(consent_text, 1)
        self._consent_widget = QWidget()
        self._consent_widget.setLayout(consent_layout)
        self._consent_widget.setVisible(False)
        self._consent_check.stateChanged.connect(self._on_consent_changed)
        main_layout.addWidget(self._consent_widget)

        # Generate + Exit row. Exit is shown in the PROMPT state (zone
        # selected) so the user always has a one-click way back to LAUNCH,
        # but is hidden while generation is in flight to avoid a "cancel mid-
        # run" footgun.
        generate_row = QHBoxLayout()
        generate_row.setContentsMargins(0, 0, 0, 0)
        generate_row.setSpacing(6)

        self._generate_btn = QPushButton(tr("Generate"))
        self._generate_btn.setToolTip(tr("Run the AI edit on your selected zone"))
        self._generate_btn.setCursor(QtC.PointingHandCursor)
        self._generate_btn.setEnabled(False)
        self._update_generate_style()
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        self._generate_btn.setVisible(False)
        generate_row.addWidget(self._generate_btn, 1)

        self._exit_btn = QPushButton(tr("Exit"))
        self._exit_btn.setToolTip(tr("Exit and return to the start"))
        self._exit_btn.setCursor(QtC.PointingHandCursor)
        # Width: hold a longer label ("Quitter", "Salir", "Sair") without
        # clipping. We use minimumWidth instead of fixedWidth so future
        # translations longer than the current set still fit.
        self._exit_btn.setMinimumWidth(88)
        self._exit_btn.setMinimumHeight(34)
        self._exit_btn.setStyleSheet(_BTN_GHOST)
        self._exit_btn.clicked.connect(self._on_exit_clicked)
        self._exit_btn.setVisible(False)
        generate_row.addWidget(self._exit_btn, 0)

        main_layout.addLayout(generate_row)

        # Progress section
        self._progress_widget = QWidget()
        progress_layout = QVBoxLayout(self._progress_widget)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(4)
        self._progress_label = QLabel(tr("Preparing..."))
        self._progress_label.setStyleSheet("font-size: 11px; color: palette(text);")
        progress_layout.addWidget(self._progress_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        progress_layout.addWidget(self._progress_bar)

        self._progress_widget.setVisible(False)
        main_layout.addWidget(self._progress_widget)

        # Status message box (same pattern as AI Segmentation info boxes)
        self._status_widget = QWidget()
        self._status_widget.setVisible(False)
        status_box_layout = QHBoxLayout(self._status_widget)
        status_box_layout.setContentsMargins(8, 6, 8, 6)
        status_box_layout.setSpacing(8)
        self._status_icon = QLabel()
        _ico = self._status_widget.style().pixelMetric(
            QStyle.PixelMetric.PM_SmallIconSize
        )
        self._status_icon.setFixedSize(_ico, _ico)
        self._status_icon_size = _ico
        status_box_layout.addWidget(
            self._status_icon, 0, QtC.AlignTop
        )
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setOpenExternalLinks(True)
        self._status_label.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        status_box_layout.addWidget(self._status_label, 1)

        # CTA button displayed for paid-tier monthly quota exhaustion.
        # Paid users are already subscribed - the action here is plan management,
        # not subscription.
        self._limit_cta_btn = QPushButton(tr("Manage plan"))
        self._limit_cta_btn.setToolTip(tr("Open your dashboard to upgrade or wait for renewal."))
        self._limit_cta_btn.setCursor(QtC.PointingHandCursor)
        self._limit_cta_btn.setStyleSheet(_BTN_BLUE)
        self._limit_cta_btn.clicked.connect(self._on_limit_cta_clicked)
        self._limit_cta_btn.setVisible(False)
        self._limit_cta_url = ""

        # --- Result section (shown after generation complete, iteration flow) ---
        self._result_section = QWidget()
        self._result_layout = QVBoxLayout(self._result_section)
        self._result_layout.setContentsMargins(0, 0, 0, 0)
        self._result_layout.setSpacing(6)

        # "What's next?" header
        result_header = _make_section_header(tr("What's next?"))
        self._result_layout.addWidget(result_header)

        # Editable prompt (edit and retry)
        self._result_prompt_input = _SubmitTextEdit()
        self._result_prompt_input.setPlaceholderText(
            tr("Edit the prompt above and retry, or pick a new action below")
        )
        self._result_prompt_input.document().setDocumentMargin(0)
        self._result_prompt_input.setMinimumHeight(50)
        self._result_prompt_input.setMaximumHeight(50)
        self._result_prompt_input.submitted.connect(self._on_retry_clicked)
        self._result_prompt_input.textChanged.connect(self._on_result_prompt_changed)
        self._result_prompt_input.document().documentLayout().documentSizeChanged.connect(
            self._adjust_result_prompt_height
        )
        self._result_prompt_container = _PromptContainer(
            self._result_prompt_input, self._result_section
        )
        self._result_prompt_container.templates_clicked.connect(
            self._on_browse_templates_clicked
        )
        self._result_prompt_container.resolution_changed.connect(
            self._on_resolution_selected
        )
        self._result_prompt_container.markup_clicked.connect(self.markup_clicked.emit)
        if self._reference_widget is not None:
            self._result_prompt_container.files_dropped.connect(
                self._reference_widget.add_paths
            )
            self._result_prompt_container.attach_clicked.connect(
                self._reference_widget.open_file_picker
            )
            self._result_prompt_input.images_pasted.connect(
                self._reference_widget.add_paths
            )
        self._result_layout.addWidget(self._result_prompt_container)

        # Action row: Generate (primary, flex) + Exit (ghost, fixed).
        result_actions_row = QHBoxLayout()
        result_actions_row.setContentsMargins(0, 4, 0, 0)
        result_actions_row.setSpacing(6)

        self._result_regenerate_btn = QPushButton(tr("Generate"))
        self._result_regenerate_btn.setToolTip(
            tr("Generate on the same zone using the current map view")
        )
        self._result_regenerate_btn.setCursor(QtC.PointingHandCursor)
        self._result_regenerate_btn.setStyleSheet(_BTN_GREEN)
        self._result_regenerate_btn.clicked.connect(self._on_retry_clicked)
        result_actions_row.addWidget(self._result_regenerate_btn, 1)

        self._result_exit_btn = QPushButton(tr("Exit"))
        self._result_exit_btn.setToolTip(tr("Exit and return to the start"))
        self._result_exit_btn.setCursor(QtC.PointingHandCursor)
        # See `_exit_btn` above for why this is a minimum rather than fixed
        # width.
        self._result_exit_btn.setMinimumWidth(88)
        self._result_exit_btn.setMinimumHeight(34)
        self._result_exit_btn.setStyleSheet(_BTN_GHOST)
        self._result_exit_btn.clicked.connect(self._on_exit_clicked)
        result_actions_row.addWidget(self._result_exit_btn, 0)

        self._result_layout.addLayout(result_actions_row)

        # Minimal status line - shown under the action row after generation.
        # Submitting the prompt (Enter key) and the Generate button both
        # trigger a regen on the same zone.
        self._layer_saved_label = QLabel()
        self._layer_saved_label.setWordWrap(True)
        self._layer_saved_label.setTextFormat(QtC.RichText)
        self._layer_saved_label.setStyleSheet(
            "font-size: 11px; color: palette(text); background: transparent;"
            " border: none; padding: 4px 0 0 0;"
        )
        self._layer_saved_label.setCursor(QtC.PointingHandCursor)
        self._layer_saved_label.linkActivated.connect(self._on_layer_saved_link_clicked)
        self._layer_saved_label.setVisible(False)
        self._saved_layer_id: str | None = None
        self._result_layout.addWidget(self._layer_saved_label)

        # --- Vectorize suggestion row (template-driven, hidden by default) ---
        # Shown after a generation when the template carried a vector_color
        # in the catalog. One click opens the Vectorize panel with the
        # source layer locked and the swatch pre-filled. Hidden in every
        # other case so it doesn't add noise to ad-hoc prompts.
        self._vectorize_cta_section = QWidget()
        cta_layout = QHBoxLayout(self._vectorize_cta_section)
        cta_layout.setContentsMargins(0, 4, 0, 0)
        cta_layout.setSpacing(6)
        self._vectorize_cta_swatch = QLabel()
        self._vectorize_cta_swatch.setFixedSize(14, 14)
        self._vectorize_cta_swatch.setStyleSheet(
            "background: #888; border: 1px solid rgba(128,128,128,0.5);"
            " border-radius: 3px;"
        )
        cta_layout.addWidget(self._vectorize_cta_swatch)
        self._vectorize_cta_btn = QPushButton()
        self._vectorize_cta_btn.setText(tr("Vectorize this result →"))
        self._vectorize_cta_btn.setFlat(True)
        self._vectorize_cta_btn.setCursor(QtC.PointingHandCursor)
        self._vectorize_cta_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            f" color: {BRAND_BLUE}; padding: 4px 0px;"
            " font-size: 12px; text-align: left; }"
            f"QPushButton:hover {{ color: {BRAND_BLUE_HOVER};"
            " text-decoration: underline; }}"
        )
        self._vectorize_cta_btn.clicked.connect(self._on_vectorize_cta_clicked)
        cta_layout.addWidget(self._vectorize_cta_btn, 1)
        self._vectorize_cta_section.setVisible(False)
        self._vectorize_cta_pending: tuple[str, str] | None = None
        self._result_layout.addWidget(self._vectorize_cta_section)

        self._result_section.setVisible(False)
        main_layout.addWidget(self._result_section)

        # Status box + CTA placed after result section so they always appear below
        main_layout.addWidget(self._status_widget)
        main_layout.addWidget(self._limit_cta_btn)

        # Trial exhausted info box - conversion panel shown when a free-tier
        # user runs out of credits. Title + 3 benefit bullets + primary button.
        self._trial_info_box = QFrame()
        self._trial_info_box.setStyleSheet(
            "QFrame { background: rgba(25,118,210,0.08); "
            "border: 1px solid rgba(25,118,210,0.2); "
            "border-radius: 4px; }"
            "QLabel { background: transparent; border: none; }"
        )
        trial_layout = QVBoxLayout(self._trial_info_box)
        trial_layout.setContentsMargins(12, 12, 12, 12)
        trial_layout.setSpacing(8)
        self._trial_info_text = QLabel("")
        self._trial_info_text.setWordWrap(True)
        self._trial_info_text.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: palette(text);"
        )
        trial_layout.addWidget(self._trial_info_text)
        self._trial_info_benefits = QLabel(
            tr("Subscribe to unlock:") + "<br>" +
            "&nbsp;&nbsp;✓&nbsp; " + tr("150 edits every month") + "<br>" +
            "&nbsp;&nbsp;✓&nbsp; " + tr("2K and 4K outputs") + "<br>" +
            "&nbsp;&nbsp;✓&nbsp; " + tr("Cancel anytime")
        )
        self._trial_info_benefits.setWordWrap(True)
        self._trial_info_benefits.setTextFormat(QtC.RichText)
        self._trial_info_benefits.setStyleSheet(
            "font-size: 11px; color: palette(text);"
        )
        trial_layout.addWidget(self._trial_info_benefits)
        self._trial_info_btn = QPushButton(tr("Subscribe"))
        self._trial_info_btn.setCursor(QtC.PointingHandCursor)
        self._trial_info_btn.setMinimumHeight(32)
        self._trial_info_btn.setStyleSheet(_BTN_BLUE)
        self._trial_info_btn.clicked.connect(self._on_trial_info_subscribe_clicked)
        trial_layout.addWidget(self._trial_info_btn)
        self._trial_info_url = ""
        # Kept for backwards compatibility with show_trial_exhausted_info callers
        # that still set a link; rendered inline as a fallback if the button is
        # ever hidden by external state.
        self._trial_info_link = QLabel("")
        self._trial_info_link.setOpenExternalLinks(True)
        self._trial_info_link.setStyleSheet(
            "font-size: 11px; background: transparent; border: none;"
        )
        self._trial_info_link.setVisible(False)
        trial_layout.addWidget(self._trial_info_link)
        self._trial_info_box.setVisible(False)
        main_layout.addWidget(self._trial_info_box)

        main_layout.addStretch()

        layout.addWidget(self._main_widget)

        # Mark up panel - full-dock workflow opened via Tools menu, hidden
        # by default; swaps with _main_widget while the user is annotating.
        self._markup_panel = MarkupPanel(self)
        self._markup_panel.setVisible(False)
        self._markup_panel.tool_changed.connect(self.markup_tool_changed.emit)
        self._markup_panel.color_changed.connect(self.markup_color_changed.emit)
        self._markup_panel.clear_clicked.connect(self.markup_clear_clicked.emit)
        self._markup_panel.done_clicked.connect(self.markup_done_clicked.emit)
        layout.addWidget(self._markup_panel)

        # Vectorize panel - same swap pattern as Mark up.
        self._vectorize_panel = VectorizePanel(self)
        self._vectorize_panel.setVisible(False)
        self._vectorize_panel.done_clicked.connect(self.vectorize_done_clicked.emit)
        layout.addWidget(self._vectorize_panel)

        # Spacer to push footer to bottom
        layout.addStretch()

        # Footer section - single row: ring + count + Subscribe pill on the
        # left, gear/help menus on the right. The Subscribe pill auto-hides
        # via resizeEvent when the dock is too narrow to fit everything.
        footer_widget = QWidget()
        footer_row = QHBoxLayout(footer_widget)
        footer_row.setContentsMargins(0, 4, 0, 4)
        footer_row.setSpacing(6)

        self._credit_ring = CreditRing(diameter=16, parent=footer_widget)
        self._credit_ring.setVisible(False)
        footer_row.addWidget(self._credit_ring)

        self._credits_label = QLabel()
        self._credits_label.setStyleSheet(
            "QLabel { font-size: 11px; color: palette(text);"
            " background: transparent; border: none; }"
        )
        self._credits_label.setVisible(False)
        footer_row.addWidget(self._credits_label)

        # "&&" so Qt renders a literal ampersand instead of consuming "&" as
        # a mnemonic accelerator (which would underline the next character).
        self._upgrade_cta = QPushButton(tr("Unlock 2K && 4K"))
        self._upgrade_cta.setToolTip(
            tr("Subscribe to unlock 2K and 4K outputs, 150 edits per month, cancel anytime.")
        )
        self._upgrade_cta.setCursor(QtC.PointingHandCursor)
        self._upgrade_cta.setStyleSheet(
            f"QPushButton {{ border: 1px solid {BRAND_BLUE}; color: {BRAND_BLUE};"
            f" border-radius: 8px; padding: 1px 8px; font-size: 11px;"
            f" background: transparent; font-weight: normal; }}"
            f"QPushButton:hover {{ background: rgba(25,118,210,0.12); }}"
        )
        self._upgrade_cta.clicked.connect(self._on_upgrade_clicked)
        self._upgrade_cta.setVisible(False)
        footer_row.addWidget(self._upgrade_cta)
        # Tracks whether the upsell *should* be shown (subscribers shouldn't).
        self._upgrade_cta_wanted = False
        # Tracks whether the credit ring + count have data; resizeEvent uses
        # this to keep them hidden on narrow docks.
        self._credits_wanted = False

        footer_row.addStretch()

        # Mark up is reachable via the pencil chip next to the prompt; the
        # footer button has been removed to avoid duplication. Alt+M still
        # opens markup via the global shortcut wired below.
        self._markup_shortcut = QShortcut(QKeySequence("Alt+M"), self)
        self._markup_shortcut.setContext(QtC.WindowShortcut)
        self._markup_shortcut.activated.connect(self.markup_clicked.emit)

        self._vectorize_btn = _FooterIconButton(footer_widget)
        self._vectorize_btn.setToolTip(tr("Vectorize"))
        self._vectorize_btn.setAccessibleName(tr("Vectorize"))
        self._vectorize_btn.setCursor(QtC.PointingHandCursor)
        self._vectorize_btn.setFocusPolicy(QtC.NoFocus)
        self._vectorize_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
        self._vectorize_btn.setIcon(self._make_polygon_glyph_icon())
        self._vectorize_btn.setIconSize(QSize(20, 20))
        self._vectorize_btn.clicked.connect(self.vectorize_clicked.emit)
        vectorize_seq = QKeySequence("Alt+V")
        self._vectorize_btn.setShortcut(vectorize_seq)
        self._vectorize_btn.setToolTip(
            tr("Vectorize ({})").format(
                vectorize_seq.toString(QKeySequence.SequenceFormat.NativeText)
            )
        )
        self._vectorize_btn.setVisible(False)
        footer_row.addWidget(self._vectorize_btn)

        # Settings button - gear icon, opens the Account Settings dialog
        # directly. Shortcuts have moved inside that dialog.
        self._settings_btn = _FooterIconButton(footer_widget)
        self._settings_btn.setText("⚙")  # ⚙ U+2699 GEAR
        self._settings_btn.setToolTip(tr("Settings"))
        self._settings_btn.setCursor(QtC.PointingHandCursor)
        self._settings_btn.setFocusPolicy(QtC.NoFocus)
        self._settings_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
        self._settings_btn.clicked.connect(self._on_settings_btn_clicked)
        self._settings_btn.setVisible(False)  # shown when activated
        footer_row.addWidget(self._settings_btn)

        # Help menu - question mark icon, always visible.
        self._help_btn = _FooterIconButton(footer_widget)
        self._help_btn.setText("?")
        self._help_btn.setToolTip(tr("Help"))
        self._help_btn.setCursor(QtC.PointingHandCursor)
        self._help_btn.setFocusPolicy(QtC.NoFocus)
        self._help_btn.setStyleSheet(_FOOTER_ICON_BTN_STYLE)
        self._help_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        help_menu = QMenu(self._help_btn)
        help_menu.setStyleSheet(_FOOTER_MENU_STYLE)
        help_menu.addAction(tr("Tutorial"), self._on_open_tutorial)
        help_menu.addAction(tr("Shortcuts"), self._on_show_shortcuts)
        help_menu.addAction(tr("Contact us"), self._on_contact_us)
        self._help_btn.setMenu(help_menu)
        # Force the hover tint off when the popup closes - Qt does not
        # synthesise a Leave event in this case.
        help_menu.aboutToHide.connect(
            lambda btn=self._help_btn: (btn.setDown(False), btn.set_hovered(False))
        )
        footer_row.addWidget(self._help_btn)

        layout.addWidget(footer_widget)

        # Wrap in scroll area (matches AI Segmentation)
        scroll_area = QScrollArea()
        scroll_area.setWidget(main_widget)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtC.FrameNoFrame)
        scroll_area.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)
        self.setWidget(scroll_area)

        # State
        self._zone_selected = False
        self._activated = False
        self._checking_credits = False
        self._is_free_tier = True  # default hidden until confirmed Pro
        self._cached_used: int | None = None
        self._cached_limit: int | None = None
        # Seeded paid-tier default. `set_credits` downgrades to "1K" the first
        # time a free-tier user is confirmed, so the dock never lands on a
        # locked resolution.
        self._selected_resolution = "2K"
        # Credit cost per resolution. Used to suffix the Generate/Regenerate
        # button text ("Generate (30 credits)"). Overwritten by
        # set_resolution_credit_costs once the server config loads.
        self._resolution_credit_costs: dict[str, int] = {"1K": 20, "2K": 30, "4K": 40}

        # Layer monitoring. We listen to add/remove, visibility-changed in the
        # legend, AND project lifecycle (readProject/cleared) so the Launch
        # button stays in sync when the user starts a new project or opens a
        # different one - those transitions replace the layerTreeRoot, which
        # invalidates any visibilityChanged binding made before.
        QgsProject.instance().layersAdded.connect(self._update_layer_warning)
        QgsProject.instance().layersRemoved.connect(self._update_layer_warning)
        QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
            self._update_layer_warning
        )
        QgsProject.instance().readProject.connect(self._on_project_loaded)
        QgsProject.instance().cleared.connect(self._on_project_loaded)
        self._update_layer_warning()

    def _setup_title_bar(self):
        """Custom title bar matching AI Segmentation style with close button."""
        title_widget = QWidget()
        title_outer = QVBoxLayout(title_widget)
        title_outer.setContentsMargins(0, 0, 0, 0)
        title_outer.setSpacing(0)

        # Title row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(4, 0, 0, 0)
        title_row.setSpacing(0)

        title_label = QLabel(
            "AI Edit by "
            f'<a href="{TERRALAB_URL}" '
            f'style="color: {BRAND_BLUE}; text-decoration: none;">TerraLab</a>'
        )
        title_label.setOpenExternalLinks(True)
        title_row.addWidget(title_label)
        title_row.addStretch()

        icon_size = self.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize)

        float_btn = QToolButton()
        float_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
        )
        float_btn.setToolTip(tr("Dock or undock this panel"))
        float_btn.setFixedSize(icon_size + 4, icon_size + 4)
        float_btn.setAutoRaise(True)
        float_btn.clicked.connect(lambda: self.setFloating(not self.isFloating()))
        title_row.addWidget(float_btn)

        close_btn = QToolButton()
        close_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        )
        close_btn.setToolTip(tr("Close this panel"))
        close_btn.setFixedSize(icon_size + 4, icon_size + 4)
        close_btn.setAutoRaise(True)
        close_btn.clicked.connect(self.close)
        title_row.addWidget(close_btn)

        title_outer.addLayout(title_row)

        # Separator line (like AI Segmentation)
        separator = QFrame()
        separator.setFrameShape(QtC.FrameHLine)
        separator.setFrameShadow(QtC.FrameSunken)
        title_outer.addWidget(separator)

        self.setTitleBarWidget(title_widget)

    def _build_activation_section(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)

        # --- How to set up AI Edit ---
        self._setup_header = QLabel(tr("Two steps to start using AI Edit"))
        self._setup_header.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: palette(text);"
        )
        layout.addWidget(self._setup_header)

        self._setup_desc = QLabel(
            tr("1. Sign up or sign in on terra-lab.ai to get your key") +
            "\n" +
            tr("2. Paste your key below to activate")
        )
        self._setup_desc.setWordWrap(True)
        self._setup_desc.setStyleSheet(_INSTRUCTION_BOX)
        layout.addWidget(self._setup_desc)

        layout.addSpacing(12)

        # --- Step 1: Create account (inside _signup_section) ---
        self._signup_section = QWidget()
        signup_layout = QVBoxLayout(self._signup_section)
        signup_layout.setContentsMargins(0, 0, 0, 0)
        signup_layout.setSpacing(8)

        step1_label = QLabel(tr("1. Sign up / Sign in"))
        step1_label.setStyleSheet("font-weight: bold; font-size: 12px; color: palette(text);")
        signup_layout.addWidget(step1_label)

        self._login_btn = QPushButton(tr("Get Your Key"))
        self._login_btn.setToolTip(tr("Sign up or sign in to receive your activation key"))
        self._login_btn.setMinimumHeight(36)
        self._login_btn.setCursor(QtC.PointingHandCursor)
        self._login_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._login_btn.clicked.connect(self._on_login_clicked)
        signup_layout.addWidget(self._login_btn)

        login_hint = QLabel(tr("5 free generations, no credit card required"))
        login_hint.setAlignment(QtC.AlignCenter)
        login_hint.setWordWrap(True)
        login_hint.setStyleSheet("font-size: 11px; color: palette(text);")
        signup_layout.addWidget(login_hint)

        layout.addWidget(self._signup_section)

        layout.addSpacing(8)

        # --- Step 2: Paste key (outside _signup_section for change-key mode) ---
        self._step2_label = QLabel(tr("2. Paste your activation key"))
        self._step2_label.setStyleSheet("font-weight: bold; font-size: 12px; color: palette(text);")
        layout.addWidget(self._step2_label)

        self._key_input_widget = QWidget()
        key_input_layout = QHBoxLayout(self._key_input_widget)
        key_input_layout.setContentsMargins(0, 0, 0, 0)
        key_input_layout.setSpacing(6)
        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("tl_...")
        self._code_input.setMinimumHeight(28)
        self._code_input.returnPressed.connect(self._on_unlock_clicked)
        key_input_layout.addWidget(self._code_input)

        unlock_btn = QPushButton(tr("Activate"))
        unlock_btn.setToolTip(tr("Validate the activation key you pasted"))
        unlock_btn.setMinimumHeight(28)
        unlock_btn.setMinimumWidth(70)
        unlock_btn.setStyleSheet(_BTN_BLUE_AUTH)
        unlock_btn.clicked.connect(self._on_unlock_clicked)
        key_input_layout.addWidget(unlock_btn)

        layout.addWidget(self._key_input_widget)

        # Cancel button (visible only in change-key mode)
        self._cancel_key_btn = QPushButton(tr("Cancel"))
        self._cancel_key_btn.setToolTip(tr("Discard changes and keep the current key"))
        self._cancel_key_btn.setCursor(QtC.PointingHandCursor)
        self._cancel_key_btn.setStyleSheet(_BTN_GHOST)
        self._cancel_key_btn.clicked.connect(self._on_cancel_change_key)
        self._cancel_key_btn.setVisible(False)
        layout.addWidget(self._cancel_key_btn)

        # Activation message (errors / success)
        self._activation_message = QLabel("")
        self._activation_message.setAlignment(QtC.AlignCenter)
        self._activation_message.setWordWrap(True)
        self._activation_message.setStyleSheet("font-size: 11px;")
        self._activation_message.setVisible(False)
        layout.addWidget(self._activation_message)

        # CTA button displayed on activation flow when usage limit is reached
        self._activation_limit_cta_btn = QPushButton(tr("Subscribe"))
        self._activation_limit_cta_btn.setToolTip(tr("Open the subscription page in your browser"))
        self._activation_limit_cta_btn.setCursor(QtC.PointingHandCursor)
        self._activation_limit_cta_btn.setStyleSheet(_BTN_BLUE_AUTH)
        self._activation_limit_cta_btn.clicked.connect(self._on_activation_limit_cta_clicked)
        self._activation_limit_cta_btn.setVisible(False)
        layout.addWidget(self._activation_limit_cta_btn)
        self._activation_limit_cta_url = ""

        return widget

    # Below ~360px the inline footer (ring + count + pill + 3 icons) starts
    # clipping the right-aligned Help button; hide the credit counter to free
    # room so the Help, Settings and Vectorize icons stay reachable.
    _CREDITS_MIN_WIDTH = 360

    def _set_upgrade_cta_wanted(self, wanted: bool) -> None:
        self._upgrade_cta_wanted = wanted
        self._upgrade_cta.setVisible(wanted)

    def _set_credits_wanted(self, wanted: bool) -> None:
        self._credits_wanted = wanted
        self._apply_credits_visibility()

    def _apply_credits_visibility(self) -> None:
        """Hide the credit ring + count when the dock is too narrow."""
        wide_enough = self.width() >= self._CREDITS_MIN_WIDTH
        show = self._credits_wanted and wide_enough
        self._credits_label.setVisible(show)
        self._credit_ring.setVisible(show)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_credits_visibility()

    def _build_warning_widget(self) -> QWidget:
        """Build yellow warning widget for when no layers are available."""
        widget = QWidget()
        widget.setStyleSheet(
            "QWidget { background-color: rgb(255, 230, 150); "
            "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; }"
            "QLabel { background: transparent; border: none; color: #333333; }"
        )
        warning_layout = QHBoxLayout(widget)
        warning_layout.setContentsMargins(8, 8, 8, 8)
        warning_layout.setSpacing(8)

        icon_label = QLabel()
        style = widget.style()
        warning_icon = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon_label.setPixmap(warning_icon.pixmap(16, 16))
        icon_label.setFixedSize(16, 16)
        warning_layout.addWidget(icon_label, 0, QtC.AlignTop)

        self._warning_text = QLabel(tr("No visible layer. Add imagery to your project."))
        self._warning_text.setWordWrap(True)
        warning_layout.addWidget(self._warning_text, 1)

        return widget

    def _place_reference_widget(self, target: str) -> None:
        """Inject the shared refs strip into the active prompt container.

        ``target`` is "prompt" or "result". The strip lives above the textbox
        inside the bordered container, so the whole input area reads as a
        single ChatGPT-style attachment block.
        """
        if self._reference_widget is None:
            return
        container = (
            self._prompt_container if target == "prompt" else self._result_prompt_container
        )
        container.insert_refs_widget(self._reference_widget)
        # Visibility tracks the store: hidden when 0 refs, shown when ≥1.
        self._reference_widget.setVisible(self._reference_widget.count() > 0)
        self._reference_widget.setEnabled(True)
        # `set_generating(True)` flips this flag on every run, but the
        # generation-done path (set_generation_complete / set_initial_state)
        # never calls set_generating(False), so without this reset the flag
        # stays True and silently blocks +/paste/drop on every subsequent
        # attempt.
        self._reference_widget.set_readonly(False)
        self._sync_attach_buttons()

    def _sync_attach_buttons(self) -> None:
        """Hide paperclips when the refs store is at capacity."""
        if self._reference_widget is None:
            return
        enabled = not self._reference_widget.at_capacity()
        self._prompt_container.set_attach_enabled(enabled)
        self._result_prompt_container.set_attach_enabled(enabled)

    def set_library_dependencies(self, client, auth_manager):
        """Plugin hands us its TerraLabClient + AuthManager so the Prompt
        library dialog can sync Recent/Favorites with the server. Optional -
        if not set, the dialog falls back to local cache only."""
        self._library_client = client
        self._library_auth_manager = auth_manager

    def set_server_catalog(self, catalog: dict | None) -> None:
        """Hand the server-fetched preset catalog (v2 shape) to the dialog.
        When None, the prompt library falls back to the locally-cached
        catalog if present; with neither, themed tabs render empty."""
        self._server_catalog = catalog

    def _main_window_for_dialog(self):
        """Parent to use for popup dialogs.

        On macOS, parenting a dialog to a QDockWidget (especially when the
        dock is floating, or when QGIS itself is in a fullscreen Space) makes
        the dialog open in its own Mission Control Space, yanking the user
        out of the QGIS workspace. The QGIS main window is always anchored
        to the right Space, so we use it as the parent instead.

        Falls back to `self` if iface isn't reachable for any reason.
        """
        try:
            from qgis.utils import iface
            mw = iface.mainWindow() if iface is not None else None
            if mw is not None:
                return mw
        except Exception:  # nosec B110 - any failure falls back below.
            pass
        return self

    def _open_templates_dialog(self) -> dict | None:
        """Open the prompt library. Returns selected preset or None.
        template_selected fires only for curated picks (Top Picks / themed);
        Recent + Favorites have their own telemetry events that don't carry
        user prompt text."""
        # Kick off a background catalog refetch so the NEXT open is fresh.
        # This open uses whatever catalog the dock currently has.
        self.catalog_refresh_requested.emit()
        from .prompt_templates_dialog import PromptTemplatesDialog

        auth_provider = None
        if self._library_auth_manager is not None:
            auth_provider = self._library_auth_manager.get_auth_header
        # Parent the dialog to the QGIS main window, not to this dock widget.
        # On macOS in fullscreen, a dialog parented to a (possibly floating)
        # dock widget gets put into its own Mission Control Space and steals
        # the user out of QGIS. Anchoring to mainWindow() keeps the popup in
        # the same Space as QGIS itself.
        parent_window = self._main_window_for_dialog()
        browse_only = (
            self._prompt_container.is_readonly()
            or self._result_prompt_container.is_readonly()  # noqa: W503
        )
        dlg = PromptTemplatesDialog(
            parent_window,
            client=self._library_client,
            auth_provider=auth_provider,
            server_catalog=self._server_catalog,
            browse_only=browse_only,
        )
        if dlg.exec():
            preset = dlg.get_selected_preset()
            if (
                preset
                and not preset.get("from_recent")  # noqa: W503
                and not preset.get("from_favorites")  # noqa: W503
            ):
                self.template_selected.emit(
                    str(preset.get("id") or ""),
                    str(preset.get("label") or ""),
                )
            return preset
        return None

    # --- Public methods ---

    def set_activated(self, activated: bool):
        self._activated = activated
        self._activation_widget.setVisible(not activated)
        self._main_widget.setVisible(activated)
        self._settings_btn.setVisible(activated)
        self._vectorize_btn.setVisible(activated)
        if activated:
            self.hide_trial_info()
            self._update_layer_warning()
            self._cancel_key_btn.setVisible(False)
            self._set_upgrade_cta_wanted(self._is_free_tier)
            self.set_launch_state()
        else:
            self._setup_header.setVisible(True)
            self._setup_desc.setVisible(True)
            self._signup_section.setVisible(True)
            self._step2_label.setVisible(True)
            self._key_input_widget.setVisible(True)
            self._cancel_key_btn.setVisible(False)
            self._activation_message.setVisible(False)
            self.hide_activation_limit_cta()

    def show_change_key_mode(self):
        """Show only the key input, no signup flow. For users changing their key."""
        self._activated = False
        self._activation_widget.setVisible(True)
        self._main_widget.setVisible(False)
        self._settings_btn.setVisible(False)
        self._vectorize_btn.setVisible(False)
        self._set_upgrade_cta_wanted(False)
        self._setup_header.setVisible(False)
        self._setup_desc.setVisible(False)
        self._signup_section.setVisible(False)
        self._step2_label.setVisible(True)
        self._key_input_widget.setVisible(True)
        self._cancel_key_btn.setVisible(True)
        self._activation_message.setVisible(False)
        self.hide_activation_limit_cta()
        self._code_input.clear()
        self._code_input.setFocus()

    def hide_consent(self):
        """Hide the consent checkbox after first generation."""
        self._consent_widget.setVisible(False)

    def set_activation_message(self, text: str, is_error: bool = False):
        # Use brighter variants for dark theme readability
        self.hide_activation_limit_cta()
        color = ERROR_TEXT if is_error else SUCCESS_TEXT
        self._activation_message.setStyleSheet(f"font-size: 11px; color: {color};")
        self._activation_message.setText(text)
        self._activation_message.setVisible(True)

    def show_activation_limit_cta(self, subscribe_url: str):
        self._activation_limit_cta_url = subscribe_url
        self._activation_limit_cta_btn.setText(tr("Subscribe"))
        self._activation_limit_cta_btn.setVisible(True)

    def hide_activation_limit_cta(self):
        self._activation_limit_cta_btn.setVisible(False)
        self._activation_limit_cta_url = ""

    def set_credits(
        self,
        used: int | None = None,
        limit: int | None = None,
        is_free_tier: bool = False,
    ):
        """Update the credits ring + compact count in the footer.

        Also drives the trial-exhausted upsell banner so it survives stray
        ``set_status`` calls that otherwise hide it.
        """
        was_free = self._is_free_tier
        self._is_free_tier = is_free_tier
        # Restore paid default on free→paid transition (overrides auto-downgrade).
        if was_free and not is_free_tier and self._selected_resolution == "1K":
            self._selected_resolution = "2K"
        if used is not None and limit is not None:
            remaining = max(0, limit - used)
            self._credits_label.setText(f"{remaining} / {limit}")
            self._credit_ring.set_credits(used, limit, free_tier=is_free_tier)
            tooltip = tr("Credits remaining this month: {remaining} / {total}").format(
                remaining=remaining, total=limit
            )
            self._credit_ring.setToolTip(tooltip)
            self._credits_label.setToolTip(tooltip)
            self._set_credits_wanted(True)
            # Cache + auto-surface the upsell banner when free tier hits 0.
            self._cached_used = used
            self._cached_limit = limit
            exhausted = is_free_tier and limit > 0 and used >= limit
            if exhausted and self._trial_info_url:
                self.show_trial_exhausted_info(
                    tr("All {limit} free credits used. Subscribe to continue.").format(
                        limit=limit
                    ),
                    self._trial_info_url,
                )
            elif not exhausted:
                self._trial_info_box.setVisible(False)
        else:
            self._set_credits_wanted(False)
        self._set_upgrade_cta_wanted(is_free_tier and self._activated)
        self._refresh_resolution_triggers()
        self._update_generate_button_text()

    def set_subscribe_url(self, url: str) -> None:
        """Prime the subscribe URL so set_credits can show the upsell on its own."""
        if url:
            self._trial_info_url = url

    def set_zone_selected(self):
        """Zone drawn: show the prompt section and the Generate/Exit row."""
        self._zone_selected = True
        self._hide_status_box()
        self._layer_saved_label.setVisible(False)
        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._result_section.setVisible(False)
        self._prompt_section.setVisible(True)
        self._prompt_container.set_readonly(False)
        self._place_reference_widget("prompt")
        self._consent_widget.setVisible(not has_consent())
        self._generate_btn.setVisible(True)
        self._exit_btn.setVisible(True)
        self._refresh_resolution_triggers()
        self._update_generate_enabled()
        self._update_generate_button_text()
        # Defer focus: the canvas still has it from the just-finished mouse
        # release event. Setting focus synchronously gets clobbered as soon
        # as the canvas finishes its own focus handling. We fire twice
        # (0ms + 50ms) because on some platforms the canvas reclaims focus
        # after the first setFocus call.
        QTimer.singleShot(0, self._focus_prompt_input)
        QTimer.singleShot(50, self._focus_prompt_input)

    def _focus_prompt_input(self):
        """Bring the dock forward and put the caret in the prompt textarea."""
        self.raise_()
        self.activateWindow()
        self._prompt_input.setFocus(QtC.OtherFocusReason)
        cursor = self._prompt_input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._prompt_input.setTextCursor(cursor)

    def set_zone_cleared(self):
        """Zone removed: return to the SELECTING_ZONE state.

        Called when the user right-clicks → Delete zone, presses Esc on the
        canvas, or clicks the × overlay on the rubber band. We go back to
        the 'Select your zone' invitation rather than all the way to
        LAUNCH - the user is mid-flow, just redrawing.
        """
        self.set_selecting_zone_state()

    def _stop_progress_animation(self):
        """Stop the smooth progress animation timer if running."""
        if hasattr(self, "_progress_timer") and self._progress_timer is not None:
            self._progress_timer.stop()

    def set_launch_state(self):
        """LAUNCH: show the entry screen with the 'Launch AI Edit' button.

        Used after activation and whenever the user clicks Exit. The selection
        tool is expected to be inactive in this state (managed by the plugin).
        """
        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False

        if self._reference_widget is not None:
            self._reference_widget.clear()
            self._reference_widget.setVisible(False)

        self._launch_section.setVisible(True)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._progress_widget.setVisible(False)
        self._result_section.setVisible(False)
        self._layer_saved_label.setVisible(False)
        self._consent_widget.setVisible(False)
        self._generate_btn.setVisible(False)
        self._exit_btn.setVisible(False)

        self._prompt_container.set_readonly(False)
        self._prompt_input.clear()
        self._prompt_input.setFixedHeight(60)
        self._result_prompt_input.clear()
        self._active_template_id = None
        self._active_template_name = None
        # Resolution persists for the QGIS session - initial tier default is
        # applied at init (paid "2K") and coerced to "1K" by
        # _refresh_resolution_triggers when free tier is confirmed.
        self._refresh_resolution_triggers()
        self._update_layer_warning()
        # Re-surface the upsell banner on free-tier-exhausted accounts:
        # state transitions otherwise hide it via set_status() side effects.
        if self._is_free_tier_exhausted() and self._trial_info_url:
            self._trial_info_box.setVisible(True)

    def set_selecting_zone_state(self):
        """SELECTING_ZONE: invite the user to draw a zone on the canvas.

        Entered after Launch is clicked, or after the user clears their zone.
        The selection tool should be active (managed by the plugin).
        """
        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False

        if self._reference_widget is not None:
            self._reference_widget.setVisible(False)

        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(True)
        self._prompt_section.setVisible(False)
        self._progress_widget.setVisible(False)
        self._result_section.setVisible(False)
        self._layer_saved_label.setVisible(False)
        self._consent_widget.setVisible(False)
        self._generate_btn.setVisible(False)
        # No Exit in this state - the screen is just the draw invitation.
        self._exit_btn.setVisible(False)
        self._update_layer_warning()

    # Backwards-compat alias for callers that still use the old name.
    def set_prompt_state(self):
        self.set_launch_state()

    def set_generating(self, generating: bool):
        """Toggle generation state -- keep prompt visible but grayed out.

        Wrapped in setUpdatesEnabled(False)/(True) so Qt batches the many
        setVisible() calls below into a single repaint. Without this batch,
        the panel reflows piecewise on Generate click and the user sees the
        dock go blank for ~1s before the progress UI lands.
        """
        self.setUpdatesEnabled(False)
        try:
            self._progress_widget.setVisible(generating)
            self._result_section.setVisible(False)
            self._warning_widget.setVisible(False)
            self._set_upgrade_cta_wanted(False)

            if generating:
                self._progress_bar.setRange(0, 100)
                self._progress_bar.setValue(0)
                self._hide_status_box()
                self._launch_section.setVisible(False)
                self._select_zone_section.setVisible(False)
                self._prompt_section.setVisible(True)
                self._prompt_container.set_readonly(True)
                # On regenerate the refs widget lives in the result container, so
                # hiding result_section above would also hide the thumbnails.
                # Move it back into the visible prompt container before locking.
                self._place_reference_widget("prompt")
                if self._reference_widget is not None:
                    self._reference_widget.set_readonly(True)
                self._consent_widget.setVisible(False)
                self._generate_btn.setVisible(False)
                # Hide Exit during generation: the user shouldn't be tempted to
                # cancel mid-run from this row. The title-bar X still works as
                # an escape hatch.
                self._exit_btn.setVisible(False)
                self._progress_label.setText(tr("Preparing..."))
            else:
                self._prompt_container.set_readonly(False)
                if self._reference_widget is not None:
                    self._reference_widget.set_readonly(False)
                self._consent_widget.setVisible(not has_consent() and self._zone_selected)
                self._generate_btn.setVisible(True)
                self._exit_btn.setVisible(True)
                self._refresh_resolution_triggers()
                self._prompt_section.setVisible(True)
        finally:
            self.setUpdatesEnabled(True)

    def set_generate_loading(self, loading: bool):
        """Toggle loading state on the Generate button during canvas export."""
        if loading:
            self._generate_btn_original_text = self._generate_btn.text()
            self._generate_btn.setText(tr("Preparing..."))
            self._generate_btn.setEnabled(False)
            self._generate_btn.setStyleSheet(_BTN_DISABLED)
        else:
            text = getattr(self, "_generate_btn_original_text", tr("Generate"))
            self._generate_btn.setText(text)
            self._update_generate_style()

    def set_progress_message(self, message: str, percentage: int = -1):
        """Update the progress label and bar during generation with smooth animation."""
        self._progress_label.setText(message)
        if percentage >= 0:
            self._progress_bar.setRange(0, 100)
            self._progress_target = percentage
            if not hasattr(self, "_progress_timer") or self._progress_timer is None:
                self._progress_timer = QTimer(self)
                self._progress_timer.setInterval(30)
                self._progress_timer.timeout.connect(self._animate_progress)
            if not self._progress_timer.isActive():
                self._progress_timer.start()

    def _animate_progress(self):
        """Smoothly animate progress bar toward target value."""
        current = self._progress_bar.value()
        target = getattr(self, "_progress_target", current)
        if current < target:
            self._progress_bar.setValue(current + 1)
        else:
            if hasattr(self, "_progress_timer") and self._progress_timer is not None:
                self._progress_timer.stop()

    def _show_status_box(self, message: str, box_type: str = "info"):
        """Show a styled status message box (AI Segmentation style)."""
        styles = {
            "error": (
                "QWidget { background-color: rgba(211, 47, 47, 0.25); "
                "border: 1px solid rgba(211, 47, 47, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #ef5350; }",
                QStyle.StandardPixmap.SP_MessageBoxCritical,
            ),
            "success": (
                "QWidget { background-color: rgba(46, 125, 50, 0.25); "
                "border: 1px solid rgba(46, 125, 50, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #66bb6a; }",
                QStyle.StandardPixmap.SP_DialogApplyButton,
            ),
            "warning": (
                "QWidget { background-color: rgb(255, 230, 150); "
                "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #333333; }",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            ),
            "info": (
                "QWidget { background-color: rgba(25, 118, 210, 0.08); "
                "border: 1px solid rgba(25, 118, 210, 0.2); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; }",
                QStyle.StandardPixmap.SP_MessageBoxInformation,
            ),
        }
        style_str, icon_enum = styles.get(box_type, styles["error"])
        self._status_widget.setStyleSheet(style_str)
        icon = self._status_widget.style().standardIcon(icon_enum)
        self._status_icon.setPixmap(icon.pixmap(self._status_icon_size, self._status_icon_size))
        self._status_label.setText(message)
        self._status_widget.setVisible(True)

    def _hide_status_box(self):
        self._status_widget.setVisible(False)
        self._status_label.setText("")
        self._hide_limit_cta()

    def set_status(self, message: str, is_error: bool = False):
        self._hide_limit_cta()
        if not message:
            self._hide_status_box()
        else:
            self._show_status_box(message, "error")
        # Only hide the trial-exhausted upsell if it's no longer applicable;
        # otherwise transient status updates would clobber it.
        if not self._is_free_tier_exhausted():
            self._trial_info_box.setVisible(False)

    def _is_free_tier_exhausted(self) -> bool:
        return bool(
            self._is_free_tier and
            self._cached_used is not None and
            self._cached_limit is not None and
            self._cached_limit > 0 and
            self._cached_used >= self._cached_limit
        )

    def set_generation_complete(self, layer_name: str, layer_id: str | None = None):
        """Show RESULT state with iteration options (retry / done)."""
        self._stop_progress_animation()
        self._progress_bar.setValue(100)
        self._progress_widget.setVisible(False)
        self._hide_status_box()

        # Clear any stale Vectorize suggestion from a previous generation;
        # the plugin re-arms it for this run only if the template carries
        # a vector_color in the catalog.
        self._vectorize_cta_section.setVisible(False)
        self._vectorize_cta_pending = None

        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        # The result section has its own Exit button, so suppress the prompt
        # row's Exit to avoid duplication.
        self._exit_btn.setVisible(False)
        self._consent_widget.setVisible(False)

        last_prompt = self._prompt_input.toPlainText().strip()
        self._result_prompt_input.setPlainText(last_prompt)
        self._result_prompt_input.moveCursor(QtC.CursorEnd)
        self._result_prompt_container.set_readonly(False)
        self._result_section.setVisible(True)
        self._refresh_resolution_triggers()

        self._place_reference_widget("result")

        self._saved_layer_id = layer_id
        escaped_name = html.escape(layer_name)
        if layer_id:
            link_html = (
                f'<a href="terralab:focus-layer" '
                f'style="color: {SUCCESS_TEXT}; text-decoration: underline;">'
                f'{escaped_name}</a>'
            )
        else:
            link_html = escaped_name
        self._layer_saved_label.setText(
            tr("✓ Saved as {name}").format(name=link_html))
        self._layer_saved_label.setVisible(True)

        self._set_upgrade_cta_wanted(self._is_free_tier and self._activated)

    def show_trial_exhausted_info(self, message: str, subscribe_url: str):
        self._hide_limit_cta()
        # Strip the trailing "Subscribe to continue" the server sometimes sends -
        # the dedicated primary button below already carries that action.
        title = (message or "").strip()
        for tail in (
            ". Subscribe to continue.",
            ". Subscribe to continue",
            " Subscribe to continue.",
            " Subscribe to continue",
        ):
            if title.endswith(tail):
                title = title[: -len(tail)].rstrip(" .")
                break
        if not title:
            title = tr("You've used your free credits")
        self._trial_info_text.setText(title)
        self._trial_info_url = subscribe_url
        self._trial_info_btn.setVisible(True)
        self._trial_info_link.setVisible(False)
        self._trial_info_box.setVisible(True)
        self._hide_status_box()

    def show_usage_limit_info(self, message: str, subscribe_url: str):
        self._show_status_box(message, "error")
        self._trial_info_box.setVisible(False)
        self._limit_cta_url = subscribe_url
        self._limit_cta_btn.setVisible(True)

    def set_checking_credits(self, checking: bool):
        """Show/hide a loading state while credits are being fetched."""
        self._checking_credits = checking
        if checking:
            self._trial_info_box.setVisible(False)
            self._show_status_box(tr("Checking credits..."), "info")
        else:
            self._hide_status_box()

    def hide_trial_info(self):
        self._trial_info_box.setVisible(False)
        self._hide_status_box()
        self._hide_limit_cta()

    def get_activation_key(self) -> str:
        return self._code_input.text().strip()

    def set_activation_key(self, key: str):
        self._code_input.setText(key)

    def get_prompt(self) -> str:
        return self._prompt_input.toPlainText().strip()

    # --- Private methods ---

    def _update_layer_warning(self, *_args):
        """Show/hide the 'no visible layer' notice and lock the Launch button
        until at least one layer is actually checked in the legend.

        We check ``isVisible()`` on the layer tree, not just registered layers
        in the project - a layer that exists but is unchecked produces no
        canvas pixels for AI Edit to capture, so launching from that state
        would just send an empty rectangle to the model.
        """
        if self._zone_selected:
            self._warning_widget.setVisible(False)
            self._launch_btn.setEnabled(True)
            return
        root = QgsProject.instance().layerTreeRoot()
        has_visible = any(
            node.isVisible() for node in root.findLayers()
            if node.layer() is not None
        )
        self._warning_widget.setVisible(not has_visible)
        self._launch_btn.setEnabled(has_visible)

    def _on_project_loaded(self, *_args):
        """Re-bind to the fresh layerTreeRoot and re-evaluate the Launch gate.

        New-project / open-project replace the layerTreeRoot instance, so the
        original visibilityChanged binding (made in __init__) ends up pointing
        at an orphaned tree. Rebind here and defer the gate check by one event
        loop tick so QGIS finishes syncing the new tree's layers first.
        """
        try:
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._update_layer_warning
            )
        except (TypeError, RuntimeError):
            pass
        QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
            self._update_layer_warning
        )
        QTimer.singleShot(0, self._update_layer_warning)

    def _on_settings_btn_clicked(self):
        self.settings_clicked.emit()

    def _on_upgrade_clicked(self):
        from ..core import telemetry
        telemetry.track("subscribe_link_clicked", {"source": "upgrade_cta"})
        QDesktopServices.openUrl(QUrl(get_subscribe_url()))

    def _on_trial_info_subscribe_clicked(self):
        from ..core import telemetry
        telemetry.track("subscribe_link_clicked", {"source": "trial_exhausted_box"})
        url = self._trial_info_url or get_subscribe_url()
        QDesktopServices.openUrl(QUrl(url))

    def _on_exit_clicked(self):
        """Exit: ask the plugin to cancel + return to LAUNCH state."""
        self.exit_clicked.emit()

    def _on_key_toggle(self, checked: bool):
        pass

    def _on_login_clicked(self):
        """Open terra-lab.ai sign-up page in system browser.

        Users clicking "Get Your Key" don't have an account yet by default, so
        we land them on /signup. The page exposes a Sign-in tab for the
        edge case of an existing user reinstalling the plugin.
        """
        import webbrowser
        webbrowser.open(
            "https://terra-lab.ai/signup?product=ai-edit"
            "&utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit"
            "&utm_content=sign_up"
        )

    def _on_cancel_change_key(self):
        """Cancel key change and restore the activated state."""
        from ..core.activation_manager import get_activation_key
        saved_key = get_activation_key()
        if saved_key:
            self._code_input.setText(saved_key)
            self.set_activated(True)
        else:
            self._signup_section.setVisible(True)
            self._cancel_key_btn.setVisible(False)

    def _on_unlock_clicked(self):
        code = self._code_input.text().strip()
        if not code:
            self.set_activation_message(tr("Enter your code"), is_error=True)
            return
        self.activation_attempted.emit(code)

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _refresh_resolution_triggers(self):
        """Push the current selection / costs / tier into both prompt containers.

        Also coerces the selection to "1K" when a free-tier user is ever
        confirmed (downgrades from the seeded "2K" paid default), so the
        Generate button never quotes a price the user can't actually pay.
        """
        if self._is_free_tier and self._selected_resolution != "1K":
            self._selected_resolution = "1K"
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_resolution_state(
                self._selected_resolution,
                self._resolution_credit_costs,
                self._is_free_tier,
            )

    def _on_resolution_selected(self, label: str):
        """Handle a click inside the resolution dropdown of either container."""
        if self._is_free_tier and label != "1K":
            subscribe_url = get_subscribe_url()
            self._show_status_box(
                tr("{} outputs are unlocked with a subscription.").format(label) +
                f' <a href="{subscribe_url}" style="color: {BRAND_BLUE}; font-weight: bold;">' +
                tr("Subscribe") + "</a>",
                "warning"
            )
            # 12 s gives users enough time to read the banner and click the
            # Subscribe link before it auto-dismisses.
            QTimer.singleShot(12000, self._hide_status_box)
            return

        # Clear any existing status message if switching resolutions
        self._hide_status_box()

        self._selected_resolution = label
        self._refresh_resolution_triggers()
        self._update_generate_button_text()

    def _update_generate_button_text(self):
        """Keep Generate label stable. The 'Select your zone' hint now lives
        in the SELECTING_ZONE section, so the button always reads 'Generate'.
        """
        self._generate_btn.setText(tr("Generate"))
        self._generate_btn.setToolTip(tr("Run the AI edit on your selected zone"))
        self._result_regenerate_btn.setText(tr("Generate"))

    def set_resolution_credit_costs(self, costs: dict[str, int]):
        """Update per-resolution credit costs (server config). Costs are
        displayed inside the resolution dropdown via the prompt containers."""
        if costs:
            self._resolution_credit_costs = costs
        self._refresh_resolution_triggers()

    def get_selected_resolution(self) -> str:
        """Return the user-selected resolution label."""
        return self._selected_resolution

    def _on_browse_templates_clicked(self):
        """Open templates dialog. Fill whichever prompt input is active."""
        preset = self._open_templates_dialog()
        if not preset:
            return

        # Arm the template so subsequent prompt edits keep the association.
        self._active_template_id = str(preset.get("id") or "") or None
        self._active_template_name = str(preset.get("label") or "") or None

        if self._result_section.isVisible():
            self._result_prompt_input.blockSignals(True)
            self._result_prompt_input.setPlainText(format_template_prompt(preset["prompt"]))
            self._result_prompt_input.blockSignals(False)
            self._result_prompt_input.moveCursor(QtC.CursorEnd)
            self._result_prompt_input.setFocus()
            self._update_generate_enabled()
            self._adjust_result_prompt_height()
        else:
            self._prompt_input.blockSignals(True)
            self._prompt_input.setPlainText(format_template_prompt(preset["prompt"]))
            self._prompt_input.blockSignals(False)
            self._prompt_input.moveCursor(QtC.CursorEnd)
            self._prompt_input.setFocus()
            self._update_generate_enabled()
            self._adjust_prompt_height()

    def _on_prompt_changed(self):
        self._enforce_prompt_max_length(self._prompt_input)
        self._update_generate_enabled()
        self._clear_active_template_if_empty()

    def _on_result_prompt_changed(self):
        self._enforce_prompt_max_length(self._result_prompt_input)
        self._clear_active_template_if_empty()

    def _clear_active_template_if_empty(self) -> None:
        """Drop the armed template once both prompt inputs are empty.

        Edits to the prompt text keep the association alive; clearing it out
        (or hitting Exit) is the signal that the next prompt is unrelated.
        """
        prompt = self._prompt_input.toPlainText().strip()
        result = self._result_prompt_input.toPlainText().strip()
        if not prompt and not result:
            self._active_template_id = None
            self._active_template_name = None

    def get_active_template(self) -> tuple[str, str] | None:
        """Return the armed (template_id, template_name) if any."""
        if self._active_template_id:
            return self._active_template_id, self._active_template_name or ""
        return None

    @staticmethod
    def _enforce_prompt_max_length(text_edit: QTextEdit) -> None:
        """Truncate the prompt to MAX_PROMPT_CHARS."""
        plain = text_edit.toPlainText()
        if len(plain) <= MAX_PROMPT_CHARS:
            return
        cursor_pos = text_edit.textCursor().position()
        text_edit.blockSignals(True)
        try:
            text_edit.setPlainText(plain[:MAX_PROMPT_CHARS])
            cursor = text_edit.textCursor()
            cursor.setPosition(min(cursor_pos, MAX_PROMPT_CHARS))
            text_edit.setTextCursor(cursor)
        finally:
            text_edit.blockSignals(False)

    _PROMPT_MAX_HEIGHT = 400

    def _adjust_prompt_height(self):
        """Auto-expand prompt input (60px min, 200px max). When the cap
        kicks in, snap height to a whole number of text lines so the last
        visible line isn't half-cut at the viewport bottom."""
        self._prompt_input.setFixedHeight(
            self._snapped_prompt_height(self._prompt_input, min_h=60)
        )

    def _adjust_result_prompt_height(self):
        """Auto-expand result prompt input (50px min, 200px max, line-snapped)."""
        self._result_prompt_input.setFixedHeight(
            self._snapped_prompt_height(self._result_prompt_input, min_h=50)
        )

    @classmethod
    def _snapped_prompt_height(cls, text_edit: QTextEdit, min_h: int) -> int:
        # QSS sets `padding: 4px` on QTextEdit, so the viewport is inset 4px
        # top and 4px bottom from the widget edge - 8 total. The previous
        # value of 12 was a stale comment ("6+6") and overshot the cut by 4px.
        padding = 8
        frame = 2 * text_edit.frameWidth()
        target = int(text_edit.document().size().height()) + padding + frame
        if target > cls._PROMPT_MAX_HEIGHT:
            line_h = text_edit.fontMetrics().lineSpacing()
            if line_h > 0:
                n_lines = max(1, (cls._PROMPT_MAX_HEIGHT - padding) // line_h)
                target = n_lines * line_h + padding
            else:
                target = cls._PROMPT_MAX_HEIGHT
        return max(min_h, target)

    def _on_retry_clicked(self):
        """Retry on same zone with the (possibly edited) prompt from result section."""
        prompt = self._result_prompt_input.toPlainText().strip()
        if not prompt:
            return
        if len(prompt) < 10 or len(prompt.split()) < 2:
            self._show_status_box(
                tr("Please describe what you want to change (at least 10 characters, 2 words)."),
                "warning",
            )
            return
        # Transfer prompt to main input for the generation flow
        self._prompt_input.setPlainText(prompt)
        self._result_section.setVisible(False)
        self._hide_status_box()
        self.retry_clicked.emit(prompt)

    def _on_generate_clicked(self):
        prompt = self.get_prompt()
        if not prompt:
            return
        if len(prompt) < 10 or len(prompt.split()) < 2:
            msg = tr("Please describe what you want to change (at least 10 characters, 2 words).")
            self._show_status_box(msg, "warning")
            return
        self._hide_status_box()
        self.generate_clicked.emit(prompt)

    def _on_generate_shortcut(self):
        """Global Enter/Return shortcut. Only fires Generate when the button is
        actually visible and enabled, so the key stays a no-op during signup,
        an active run, or before a zone is selected."""
        if not self._main_widget.isVisible():
            return
        if not self._generate_btn.isVisible() or not self._generate_btn.isEnabled():
            return
        self._on_generate_clicked()

    def _on_open_tutorial(self):
        """Open the tutorial URL in the user's default browser."""
        QDesktopServices.openUrl(QUrl(get_tutorial_url()))

    def _on_layer_saved_link_clicked(self, _link: str) -> None:
        """Focus the saved layer in the QGIS Layers panel."""
        layer_id = self._saved_layer_id
        if not layer_id:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return
        try:
            from qgis.utils import iface
        except ImportError:
            return
        if iface is None:
            return
        iface.setActiveLayer(layer)
        tree_view = iface.layerTreeView()
        if tree_view is None:
            return
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer_id) if root is not None else None
        if node is None:
            return
        model = tree_view.layerTreeModel()
        if model is None:
            return
        index = model.node2index(node)
        tree_view.setCurrentIndex(index)
        tree_view.scrollTo(index)

    # ------------------------------------------------------------------
    # Tool panels (Mark up, Vectorize) - full-dock views reached via the
    # 🧰 Tools menu. They swap with `_main_widget` and restore it on Done.
    # ------------------------------------------------------------------

    def _build_panel_header(
        self, title: str, on_back, subtitle: str | None = None
    ) -> QWidget:
        del on_back  # panels exit via the Done button at the bottom
        return build_panel_header(title, subtitle)

    @staticmethod
    def _panel_section_label(text: str) -> QLabel:
        return panel_section_label(text)

    @staticmethod
    def _apply_swatch_style(button: QPushButton, color: QColor) -> None:
        apply_swatch_style(button, color)

    def _make_polygon_glyph_icon(self) -> QIcon:
        """Footer Vectorize button glyph - same square-in-square shape as the
        Prompt Library 'Segment' tab (Unicode ▣), drawn in black so it reads
        clearly on the footer regardless of theme.
        """
        size = 40  # 2x for crisp rendering at 20px
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ink = QColor("#000000")
        pen = QPen(ink)
        pen.setWidthF(2.4)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        outer = QRectF(6, 6, 28, 28)
        p.drawRect(outer)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(ink))
        inner = QRectF(13, 13, 14, 14)
        p.drawRect(inner)
        p.end()
        return QIcon(pm)

    # Public API consumed by the plugin layer ---------------------------

    def set_markup_state(self) -> None:
        """Swap the dock view to the Mark up panel."""
        self._stop_progress_animation()
        self._hide_status_box()
        self._main_widget.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._markup_panel.setVisible(True)
        self._markup_panel.activate()

    def set_vectorize_state(self) -> None:
        """Swap the dock view to the Vectorize panel."""
        self._stop_progress_animation()
        self._hide_status_box()
        self._main_widget.setVisible(False)
        self._markup_panel.setVisible(False)
        self._vectorize_panel.setVisible(True)
        self._vectorize_panel.activate()

    def exit_tool_panel(self) -> None:
        """Hide whichever tool panel is showing and restore _main_widget."""
        self._vectorize_panel.deactivate()
        self._markup_panel.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._main_widget.setVisible(True)

    def set_markup_annotation_count(self, count: int) -> None:
        self._markup_panel.set_annotation_count(count)

    def set_markup_zone_present(self, has_zone: bool) -> None:
        self._markup_panel.set_zone_present(has_zone)

    def get_markup_color(self) -> QColor:
        return self._markup_panel.get_color()

    def set_vectorize_suggestion(
        self, layer_id: str | None, color_hex: str | None
    ) -> None:
        """Inject (or clear) the post-generation Vectorize CTA.

        Called by the plugin orchestrator after a successful generation
        when the template carried a vector_color in the catalog. Hidden
        the moment the user navigates away from the result section.
        """
        if not layer_id or not color_hex:
            self._vectorize_cta_section.setVisible(False)
            self._vectorize_cta_pending = None
            return
        # Normalise the hex so we always pass `#RRGGBB` downstream.
        qc = QColor(color_hex)
        if not qc.isValid():
            self._vectorize_cta_section.setVisible(False)
            self._vectorize_cta_pending = None
            return
        normalised = qc.name().upper()
        self._vectorize_cta_swatch.setStyleSheet(
            f"background: {normalised};"
            " border: 1px solid rgba(128,128,128,0.5); border-radius: 3px;"
        )
        # The swatch communicates the pre-filled color already; the label
        # stays generic so it works for every template.
        self._vectorize_cta_btn.setText(tr("Vectorize this result →"))
        self._vectorize_cta_pending = (layer_id, normalised)
        self._vectorize_cta_section.setVisible(True)

    def _on_vectorize_cta_clicked(self) -> None:
        if self._vectorize_cta_pending is None:
            return
        layer_id, color_hex = self._vectorize_cta_pending
        self.vectorize_suggestion_clicked.emit(layer_id, color_hex)

    def _on_contact_us(self, _link=None):
        """Show a dialog with email + Calendly options."""
        from qgis.PyQt.QtWidgets import QApplication, QDialog
        from qgis.PyQt.QtWidgets import QVBoxLayout as _VBox

        calendly_url = "https://calendly.com/barbot-yvann/30min"

        dlg = QDialog(self._main_window_for_dialog())
        dlg.setWindowTitle(tr("Contact us"))
        dlg.setMinimumWidth(350)
        dlg.setMaximumWidth(450)
        lay = _VBox(dlg)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        msg = QLabel(tr("Bug, question, feature request?\nWe'd love to hear from you!"))
        msg.setWordWrap(True)
        lay.addWidget(msg)

        email_label = QLabel(f"<b>{SUPPORT_EMAIL}</b>")
        email_label.setTextInteractionFlags(QtC.TextSelectableByMouse)
        lay.addWidget(email_label)

        copy_btn = QPushButton(tr("Copy email address"))
        copy_btn.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(SUPPORT_EMAIL),
                copy_btn.setText(tr("Copied!")),
            )
        )
        lay.addWidget(copy_btn)

        or_label = QLabel(tr("or"))
        or_label.setAlignment(QtC.AlignCenter)
        or_label.setStyleSheet("color: palette(text);")
        lay.addWidget(or_label)

        call_btn = QPushButton(tr("Book a video call"))
        call_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(calendly_url))
        )
        lay.addWidget(call_btn)

        dlg.exec()

    def _on_show_shortcuts(self, _link=None):
        from qgis.PyQt.QtWidgets import QDialog
        from qgis.PyQt.QtWidgets import QVBoxLayout as _VBox

        def native(seq: str) -> str:
            return QKeySequence(seq).toString(QKeySequence.SequenceFormat.NativeText)

        undo_key = QKeySequence(QKeySequence.StandardKey.Undo).toString(
            QKeySequence.SequenceFormat.NativeText
        )
        launch_key = native("Ctrl+Alt+E")
        markup_key = native("Alt+M")
        vectorize_key = native("Alt+V")
        key_style = (
            "background-color: rgba(128,128,128,0.18);"
            "border: 1px solid rgba(128,128,128,0.35);"
            "border-radius: 3px; padding: 1px 5px; font-family: monospace;"
        )
        k = f"<span style='{key_style}'>{{}}</span>"

        enter_key = QKeySequence(QtC.Key_Return).toString(
            QKeySequence.SequenceFormat.NativeText
        )
        shortcuts_html = (
            "<table cellspacing='4' cellpadding='2'>"
            f"<tr><td colspan='2' style='padding-bottom:2px;'><b>{tr('Editing')}</b></td></tr>"
            f"<tr><td>{k.format(launch_key)}</td><td>{tr('Launch AI Edit')}</td></tr>"
            f"<tr><td>{k.format(enter_key)}</td><td>{tr('Generate')}</td></tr>"
            f"<tr><td>{k.format('Esc')}</td><td>{tr('Cancel selection')}</td></tr>"
            f"<tr><td>{k.format(undo_key)}</td><td>{tr('Undo')}</td></tr>"
            f"<tr><td>{k.format(markup_key)}</td><td>{tr('Mark up')}</td></tr>"
            f"<tr><td>{k.format(vectorize_key)}</td><td>{tr('Vectorize')}</td></tr>"
            "</table>"
        )

        dlg = QDialog(self._main_window_for_dialog())
        dlg.setWindowTitle(tr("Shortcuts"))
        lay = _VBox(dlg)
        lay.setContentsMargins(16, 16, 16, 12)
        label = QLabel(shortcuts_html)
        label.setTextFormat(QtC.RichText)
        lay.addWidget(label)
        ok_btn = QPushButton(tr("OK"))
        ok_btn.setFixedWidth(80)
        ok_btn.clicked.connect(dlg.accept)
        lay.addWidget(ok_btn, alignment=QtC.AlignCenter)
        dlg.exec()

    def _on_limit_cta_clicked(self):
        if self._limit_cta_url:
            from ..core import telemetry
            telemetry.track("subscribe_link_clicked", {"source": "limit_cta"})
            QDesktopServices.openUrl(QUrl(self._limit_cta_url))

    def _on_activation_limit_cta_clicked(self):
        if self._activation_limit_cta_url:
            from ..core import telemetry
            telemetry.track("subscribe_link_clicked", {"source": "activation_limit_cta"})
            QDesktopServices.openUrl(QUrl(self._activation_limit_cta_url))

    def _on_consent_changed(self):
        """Re-evaluate Generate button when consent checkbox changes."""
        self._update_generate_enabled()

    def _update_generate_enabled(self):
        has_prompt = bool(self.get_prompt())
        consent_ok = has_consent() or self._consent_check.isChecked()
        enabled = self._zone_selected and has_prompt and consent_ok
        self._generate_btn.setEnabled(enabled)
        self._update_generate_style()
        self._update_generate_button_text()

    def _hide_limit_cta(self):
        self._limit_cta_btn.setVisible(False)
        self._limit_cta_url = ""

    def _update_generate_style(self):
        if self._generate_btn.isEnabled():
            self._generate_btn.setStyleSheet(_BTN_GREEN)
        else:
            self._generate_btn.setStyleSheet(_BTN_DISABLED)

    def _on_escape_pressed(self):
        """Escape walks the flow back one step at a time.

        ZONE_SELECTED → SELECTING_ZONE (drop the zone, keep the panel open).
        SELECTING_ZONE / LAUNCH / RESULT → exit to LAUNCH.
        A generation in progress is never cancelable by Escape - credits are
        already booked; only the Stop button can cancel.
        """
        if not self.isVisible() or not self._main_widget.isVisible():
            return
        if self._progress_widget.isVisible():
            return
        # WindowShortcut means the dock receives Escape from anywhere in the
        # QGIS main window. Bail out unless the user is genuinely interacting
        # with AI Edit (canvas focused with our map tool, or focus is inside
        # the dock itself) so we don't steal Escape from QGIS digitizing,
        # measure tool, identify panel, etc.
        if not self._is_escape_for_us():
            return
        if self._zone_selected and self._prompt_section.isVisible():
            self.zone_clear_requested.emit()
            return
        self.exit_clicked.emit()

    def _is_escape_for_us(self) -> bool:
        """Decide whether an Escape keypress should drive AI Edit's flow.

        True when focus is inside the dock, OR the canvas currently runs
        one of our map tools (rectangle selection / Mark up pencil/arrow/
        circle). Anywhere else, Escape belongs to the active QGIS tool.
        """
        from qgis.PyQt.QtWidgets import QApplication

        focus = QApplication.focusWidget()
        if focus is not None:
            w = focus
            while w is not None:
                if w is self:
                    return True
                w = w.parent()
        try:
            from qgis.utils import iface as _iface
            if _iface is None:
                return False
            tool = _iface.mapCanvas().mapTool()
        except Exception:
            return False
        if tool is None:
            return False
        from .markup_tools import _MarkupBaseMapTool
        from .selection_map_tool import RectangleSelectionTool
        return isinstance(tool, (RectangleSelectionTool, _MarkupBaseMapTool))

    def closeEvent(self, event):
        """Cancel generation and disconnect signals on close."""
        self._stop_progress_animation()
        if self._progress_widget.isVisible():
            self.stop_clicked.emit()
        # Drop the iface.currentLayerChanged listener so the Vectorize panel
        # doesn't keep firing into a dock that just got destroyed.
        self._vectorize_panel.deactivate()
        try:
            QgsProject.instance().layersAdded.disconnect(self._update_layer_warning)
            QgsProject.instance().layersRemoved.disconnect(self._update_layer_warning)
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._update_layer_warning
            )
            QgsProject.instance().readProject.disconnect(self._on_project_loaded)
            QgsProject.instance().cleared.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)
