from __future__ import annotations

import html
import os
import random
import tempfile

from qgis.core import QgsMimeDataUtils, QgsProject, QgsRasterLayer, QgsVectorLayer
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
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSizePolicy,
    QStyle,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ..core import qt_compat as QtC
from ..core.auth.activation_manager import (
    get_subscribe_url,
    get_tutorial_url,
    has_consent,
)
from ..core.i18n import tr
from ..core.logger import log_debug, log_warning
from ..core.prompts.hex_highlight import HEX_RX, contrast_text_for, expand_hex
from ..core.prompts.loading_messages import get_phase_messages
from ..core.prompts.prompt_presets import detect_prompt_guidance, format_template_prompt
from ..core.reference_image_store import ReferenceImageStore
from ..core.resolution_labels import resolution_chip_label, resolution_quality_name
from .credit_ring import CreditRing
from .dialogs.error_report_dialog import (
    REPORT_PROBLEM_HREF,
    SUPPORT_EMAIL,
    show_error_report,
)
from .panel_helpers import (
    apply_swatch_style,
    build_panel_header,
    make_hidpi_pixmap,
    make_section_header,
    panel_section_label,
)
from .panels.markup_panel import MarkupPanel
from .panels.vectorize_panel import VectorizePanel
from .reference_images_widget import FREE_TIER_MAX_REFERENCES, ReferenceImagesWidget
from .version_strip import VersionStrip

# ---------------------------------------------------------------------------
# Brand colors (Material Design 2 - shared with AI Segmentation)
# ---------------------------------------------------------------------------
# Primary CTA buttons (Generate / Regenerate / Launch / Login) keep the
# original material green - it reads as THE action color and stays unchanged.
# Every other green accent uses the QGIS lime below.
BTN_GREEN = "#43a047"
BTN_GREEN_HOVER = "#2e7d32"
BTN_GREEN_DISABLED = "#c8e6c9"

# Brand accent green = the QGIS green (the --qgis-green brand token). Lime
# fills use BRAND_GREEN; green text on light backgrounds uses BRAND_GREEN_TEXT
# (#8bac27 only clears ~2.5:1 on white, the darker tone clears AA).
BRAND_GREEN = "#8bac27"
BRAND_GREEN_TEXT = "#4d7c0f"
BRAND_BLUE = "#1e88e5"
BRAND_BLUE_HOVER = "#1976d2"
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
# SUPPORT_EMAIL is imported from .dialogs.error_report_dialog (single source).

# Design-system QSS constants. border: none kills the native frame on dark themes.
_BTN_GREEN = (
    f"QPushButton {{ background-color: {BTN_GREEN}; color: #000000;"
    f" padding: 8px 16px; border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BTN_GREEN_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BTN_GREEN_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_GREEN_AUTH = (
    f"QPushButton {{ background-color: {BTN_GREEN}; color: #000000;"
    f" border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BTN_GREEN_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 6px 12px; border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_BLUE_AUTH = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; }}"
)

_BTN_GRAY = (
    f"QPushButton {{ background-color: {BRAND_GRAY}; color: #000000;"
    f" padding: 4px 8px; border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_GRAY_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT}; }}"
)

_BTN_DISABLED = (
    f"QPushButton {{ background-color: {BRAND_DISABLED}; color: {DISABLED_TEXT};"
    f" padding: 8px 16px; border: none; border-radius: 4px; }}"
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

# Compact filled buttons for the browser-handoff waiting state. Both carry a
# soft tint (never transparent): neutral for "open again", red for "cancel".
_BTN_PAIR_NEUTRAL = (
    "QPushButton { background-color: rgba(128,128,128,0.16); color: palette(text);"
    " border: none; border-radius: 4px; }"
    "QPushButton:hover { background-color: rgba(128,128,128,0.28); }"
)
_BTN_PAIR_CANCEL = (
    f"QPushButton {{ background-color: rgba(211,47,47,0.12); color: {BRAND_RED};"
    f" border: none; border-radius: 4px; }}"
    f"QPushButton:hover {{ background-color: rgba(211,47,47,0.22); }}"
)

# Shared height for the prompt-row chips so text-only and icon chips align.
_CHIP_HEIGHT = 30

# Footer icon buttons (swipe / vectorize / gear / question mark).
# Hover, active and disabled states are all driven by the dynamic
# ``hover`` / ``active`` properties + Qt's :checked pseudo-state. The
# TerraLab leaf-green tint marks "you are inside this tool" so the user
# always knows which AI Edit action owns the canvas. Two states exist for
# the same reason: ``[active]`` lets us light buttons that drive modal
# dialogs / menus (where Qt's :checked would auto-toggle on click), while
# :checked fits the genuine toggle (swipe).
_FOOTER_ICON_BTN_STYLE = (
    "QToolButton { background: transparent; border: none; padding: 6px 10px;"
    " font-size: 22px; font-weight: 600;"
    " color: palette(text); border-radius: 4px; }"
    'QToolButton[hover="true"] { background: rgba(128,128,128,0.15); }'
    'QToolButton[active="true"] { background: rgba(139, 172, 39, 0.55); }'
    'QToolButton[active="true"][hover="true"] { background: rgba(139, 172, 39, 0.75); }'
    "QToolButton:checked { background: rgba(139, 172, 39, 0.55); }"
    "QToolButton:checked:hover { background: rgba(139, 172, 39, 0.75); }"
    "QToolButton:disabled { color: rgba(128, 128, 128, 0.4); }"
    "QToolButton::menu-indicator { image: none; width: 0; }"
)
_FOOTER_ICON_TOGGLE_STYLE = _FOOTER_ICON_BTN_STYLE  # backward-compat alias

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
    "  font-size: 12px;"
    "  color: palette(text);"
    "}"
)


def _tinted_svg_icon(filename: str, ink: QColor) -> QIcon:
    """Render a bundled footer SVG and recolour every opaque pixel to ``ink``.

    The gear and help glyphs are palette-text-coloured button text, so on a
    dark theme they read as bright near-white. The SVG icons (attach image,
    Before/after) ship with a fixed mid-grey stroke, which next to them looks
    dim - almost transparent. Tinting them to the same palette text colour
    (SourceIn keeps the glyph shape, swaps the colour) lines their weight up
    with the rest and keeps them legible on light themes too.
    """
    plugin_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    path = os.path.join(plugin_root, "resources", "icons", filename)
    # Let QIcon's SVG engine rasterise at 2x for a crisp 20px button, then
    # recolour the pixmap IN PLACE. Copying it into a fresh QPixmap dropped
    # the device-pixel-ratio QIcon had baked in, which shrank the glyph to a
    # quarter of its size on Retina displays.
    pm = QIcon(path).pixmap(QSize(40, 40))
    p = QPainter(pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), ink)
    p.end()
    return QIcon(pm)


def _picture_plus_icon(ink: QColor) -> QIcon:
    """Reference-image glyph with a small accent ``+`` badge in the corner, so
    it reads as 'add an image' at a glance (the Krea-style affordance). The
    image stroke is tinted to ``ink`` to match the other footer glyphs; the
    badge uses the brand green so the 'add' intent pops on both themes.
    """
    from qgis.PyQt.QtCore import QPointF, Qt
    from qgis.PyQt.QtGui import QBrush, QPainter, QPen

    pm = QIcon(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "resources", "icons", "image.svg",
        )
    ).pixmap(QSize(40, 40))
    # Tint the image glyph to palette text weight.
    p = QPainter(pm)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(pm.rect(), ink)
    p.end()
    # Paint the green "+" badge on top, in the upper-right corner.
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cx, cy, r = 30.0, 10.0, 9.0
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor("#8BAC27")))
    p.drawEllipse(QPointF(cx, cy), r, r)
    pen = QPen(QColor("#14210A"))
    pen.setWidthF(2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(cx - 4, cy), QPointF(cx + 4, cy))
    p.drawLine(QPointF(cx, cy - 4), QPointF(cx, cy + 4))
    p.end()
    return QIcon(pm)


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


class _Spinner(QWidget):
    """A small rotating arc, the conventional 'busy' indicator. Driven by an
    external QTimer calling ``advance()`` so one timer can be paused with the
    section it belongs to."""

    def __init__(self, diameter: int = 16, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._d = diameter
        self.setFixedSize(diameter, diameter)

    def advance(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        margin = 2.0
        rect = QRectF(margin, margin, self._d - 2 * margin, self._d - 2 * margin)
        pen = QPen(QColor(BRAND_GREEN))
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rect, int(-self._angle * 16), 270 * 16)
        painter.end()


class _ZoneGestureGlyph(QWidget):
    """Vector 'draw a box' glyph: a dashed rounded box with a drag arrow across
    it. Painted live in paintEvent so it stays crisp at any DPI (no rasterised
    pixmap to pixelate). Blue, to echo the zone box drawn on the canvas.
    """

    def __init__(self, color: QColor, size: int = 56, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):  # noqa: N802 - Qt signature
        from qgis.PyQt.QtCore import QPointF, QRectF, Qt
        from qgis.PyQt.QtGui import QPainter, QPen, QPolygonF
        s = float(self.width())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Dashed box (the zone being drawn), set to the upper-left so the cursor
        # can grab its bottom-right corner without leaving the widget.
        box = QPen(self._color)
        box.setWidthF(s * 0.045)
        box.setStyle(Qt.PenStyle.DashLine)
        box.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(box)
        p.setBrush(Qt.BrushStyle.NoBrush)
        a, b = s * 0.10, s * 0.60
        p.drawRoundedRect(QRectF(a, a, b - a, b - a), s * 0.05, s * 0.05)
        # Solid handle on the corner being dragged.
        hs = s * 0.05
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        p.drawRect(QRectF(b - hs, b - hs, 2 * hs, 2 * hs))
        # Mouse cursor (arrow) pulling that corner: tip on the handle, classic
        # up-left pointer shape, blue fill with a white edge so it reads clearly.
        f = s * 0.020
        pts = [(0, 0), (0, 15), (3.5, 11.5), (6, 17), (8, 16), (5.5, 10.5), (10, 10)]
        cursor = QPolygonF([QPointF(b + x * f, b + y * f) for (x, y) in pts])
        edge = QPen(QColor(255, 255, 255, 235))
        edge.setWidthF(s * 0.022)
        edge.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(edge)
        p.setBrush(QBrush(self._color))
        p.drawPolygon(cursor)
        p.end()


_make_section_header = make_section_header  # backward-compat alias


_IMAGE_DROP_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
_GEODATA_DROP_EXTS = {
    ".tif", ".tiff", ".asc", ".img", ".vrt", ".dem", ".pdf",
    ".shp", ".gpkg", ".geojson", ".kml", ".kmz",
}
_URI_MIME = "application/x-vnd.qgis.qgis.uri"
_LAYERTREE_MIME = "application/qgis.layertreemodeldata"


def _file_paths_from_mime(mime) -> list[str]:
    """Local file paths we can turn into a reference (plain image OR geodata).
    Image-vs-geodata routing happens downstream in the widget."""
    if not mime.hasUrls():
        return []
    out: list[str] = []
    for url in mime.urls():
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        ext = os.path.splitext(path)[1].lower()
        if ext in _IMAGE_DROP_EXTS or ext in _GEODATA_DROP_EXTS:
            out.append(path)
    return out


def _mime_has_droppable(mime) -> bool:
    """Cheap predicate for dragEnter/dragMove - no layer objects built here."""
    if _file_paths_from_mime(mime):
        return True
    return mime.hasFormat(_URI_MIME) or mime.hasFormat(_LAYERTREE_MIME)


def _layers_from_mime(mime) -> list:
    """Resolve QGIS layers dragged from the Layers panel (or a data-source
    drag) to QgsMapLayer objects. Only called on drop."""
    layers: list = []
    seen: set = set()

    # 1. QgsMimeDataUtils URI list: already-loaded layers via layerId,
    #    not-yet-loaded data sources via uri/providerKey. Gate on the URI MIME
    #    format rather than an isUriList() helper (not present on all versions).
    if mime.hasFormat(_URI_MIME):
        try:
            for uri in QgsMimeDataUtils.decodeUriList(mime):
                lid = getattr(uri, "layerId", "") or ""
                if lid:
                    lyr = QgsProject.instance().mapLayer(lid)
                    if lyr is not None:
                        if id(lyr) not in seen:
                            layers.append(lyr)
                            seen.add(id(lyr))
                        continue
                provider = getattr(uri, "providerKey", "") or "ogr"
                name = getattr(uri, "name", "") or "ref"
                if getattr(uri, "layerType", "") == "raster":
                    lyr = QgsRasterLayer(uri.uri, name, provider)
                else:
                    lyr = QgsVectorLayer(uri.uri, name, provider)
                if lyr.isValid() and id(lyr) not in seen:
                    layers.append(lyr)
                    seen.add(id(lyr))
        except Exception as err:  # nosec B110
            log_warning(f"URI-list layer decode failed: {err}")

    if layers:
        return layers

    # 2. Layer-tree-model MIME: parse layer ids, look up in the project.
    if mime.hasFormat(_LAYERTREE_MIME):
        try:
            from qgis.PyQt.QtXml import QDomDocument
            doc = QDomDocument()
            doc.setContent(bytes(mime.data(_LAYERTREE_MIME)))
            nodes = doc.elementsByTagName("layer-tree-layer")
            for i in range(nodes.count()):
                lid = nodes.at(i).toElement().attribute("id", "")
                if not lid:
                    continue
                lyr = QgsProject.instance().mapLayer(lid)
                if lyr is not None and id(lyr) not in seen:
                    layers.append(lyr)
                    seen.add(id(lyr))
        except Exception as err:  # nosec B110
            log_warning(f"Layer-tree MIME decode failed: {err}")

    return layers


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
        self.setProperty("active", False)

    def set_hovered(self, hovered: bool) -> None:
        if bool(self.property("hover")) == hovered:
            return
        self.setProperty("hover", hovered)
        # Re-polish so the [hover="true"] selector takes effect.
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_active(self, active: bool) -> None:
        """Light the green "you are inside this tool" tint without making
        the button checkable. Used for footer icons that drive modal
        dialogs / menus where Qt's :checked would auto-toggle on click.
        """
        if bool(self.property("active")) == active:
            return
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def enterEvent(self, event):  # noqa: N802
        self.set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.set_hovered(False)
        super().leaveEvent(event)


class _PromptHighlighter(QSyntaxHighlighter):
    """Paints `#RRGGBB` / `#RGB` hex codes with their own color as background,
    text flipped to black or white per luminance. Makes a color list in a
    template visually scannable without leaving the textbox."""

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        if not text:
            return
        for match in HEX_RX.finditer(text):
            hex_text = match.group(0)
            fmt = QTextCharFormat()
            fmt.setBackground(QColor(expand_hex(hex_text)))
            fmt.setForeground(QColor(contrast_text_for(hex_text)))
            fmt.setFontWeight(QFont.Weight.Bold)
            self.setFormat(match.start(), match.end() - match.start(), fmt)


class _SubmitTextEdit(QTextEdit):
    """Borderless QTextEdit used inside _PromptContainer.

    - Enter submits, Shift+Enter inserts newline.
    - Image or geodata file paths in the clipboard or raw image data (e.g. a screenshot
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
        if event.key() in (QtC.Key_Return, QtC.Key_Enter) and not event.modifiers() & QtC.ShiftModifier:
            self.submitted.emit()
            return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source):  # noqa: N802
        if source.hasImage() or _file_paths_from_mime(source):
            return False
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):  # noqa: N802
        paths = _file_paths_from_mime(source)
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
        quality: str,
        resolution: str,
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
            self.setToolTip(tr("Subscribe for more detail"))

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 16, 4)
        row.setSpacing(8)

        # Leading column: checkmark on the selected row, empty otherwise.
        # Locked rows carry no icon - the muted text + tooltip already signal
        # the locked state, and a padlock glyph read as cheap.
        check = QLabel("✓" if selected else "", self)
        check.setFixedWidth(12)
        check.setStyleSheet(
            "font-size: 12px; color: palette(text); background: transparent;"
        )
        check.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        row.addWidget(check)

        muted = f"color: {DISABLED_TEXT};" if locked else "color: palette(text);"
        # "Standard" reads as the row label; the exact resolution "(1K)" trails
        # in a dimmer tint as the supporting detail.
        res_color = DISABLED_TEXT if locked else "rgba(128,128,128,0.85)"
        name = QLabel(
            f"{quality} <span style='color: {res_color};'>({resolution})</span>",
            self,
        )
        name.setTextFormat(Qt.TextFormat.RichText)
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

        [Prompt library]                   [ 1K ⌄ ]  [ ✎ ]  [ +img Ref image ]

    The whole frame is the drop target so dragging a file or layer anywhere
    over it lights up a single coherent area.
    """

    files_dropped = pyqtSignal(list)
    layers_dropped = pyqtSignal(list)
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
    # Unified footer chip: one look for the whole prompt row (Prompt library,
    # resolution, markup, Reference). Neutral outlined pill at rest (no bold,
    # no green), leaf-green tint on hover, stronger green when pressed/active -
    # the same TerraLab-green interaction language as the bottom footer icons.
    _CHIP_REST = (
        "QToolButton { background: rgba(128,128,128,0.08);"
        " border: 1px solid rgba(128,128,128,0.40); border-radius: 6px;"
        " padding: 4px 10px; font-size: 12px; color: palette(text); }"
    )
    _CHIP_HOVER = "background: rgba(139,172,39,0.18); border-color: rgba(139,172,39,0.65);"
    _CHIP_PRESSED = "background: rgba(139,172,39,0.32); border-color: rgba(139,172,39,0.85);"
    _CHIP_TAIL = (
        "QToolButton:disabled { color: rgba(128,128,128,0.40);"
        " background: transparent; border-color: rgba(128,128,128,0.20); }"
        "QToolButton::menu-indicator { image: none; width: 0; }"
    )
    _CHIP_BTN_STYLE = "".join((
        _CHIP_REST,
        f"QToolButton:hover {{ {_CHIP_HOVER} }}",
        f"QToolButton:pressed {{ {_CHIP_PRESSED} }}",
        f'QToolButton[active="true"] {{ {_CHIP_PRESSED} }}',
        _CHIP_TAIL,
    ))
    # Same chip, but property-driven hover for buttons that pop a QMenu - Qt
    # eats the synthetic Leave event when a popup closes, leaving :hover stuck
    # on (see _FooterIconButton).
    _CHIP_BTN_HOVERPROP_STYLE = "".join((
        _CHIP_REST,
        f'QToolButton[hover="true"] {{ {_CHIP_HOVER} }}',
        f'QToolButton[active="true"] {{ {_CHIP_PRESSED} }}',
        _CHIP_TAIL,
    ))
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
        self._selected_resolution = "1K"
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
        footer_row.setSpacing(6)

        self._templates_btn = QToolButton(self)
        self._templates_btn.setText(tr("Library"))
        self._templates_btn.setToolTip(tr("Browse templates, your recent prompts, and favorites."))
        self._templates_btn.setCursor(QtC.PointingHandCursor)
        self._templates_btn.setStyleSheet(self._CHIP_BTN_STYLE)
        self._templates_btn.setFixedHeight(_CHIP_HEIGHT)
        self._templates_btn.clicked.connect(self.templates_clicked.emit)
        footer_row.addWidget(self._templates_btn)

        footer_row.addStretch()

        self._resolution_menu = QMenu(self)
        self._resolution_menu.setStyleSheet(self._MENU_STYLE)
        # Allow per-action tooltips (Qt swallows them by default in QMenu).
        self._resolution_menu.setToolTipsVisible(True)
        self._resolution_btn = _FooterIconButton(self)
        self._resolution_btn.setToolTip(
            tr("<b>Output detail</b><br>Higher detail gives a sharper, "
               "more precise result. Standard (1K), Detailed (2K), "
               "Maximum (4K).")
        )
        self._resolution_btn.setCursor(QtC.PointingHandCursor)
        self._resolution_btn.setStyleSheet(self._CHIP_BTN_HOVERPROP_STYLE)
        self._resolution_btn.setFixedHeight(_CHIP_HEIGHT)
        self._resolution_btn.clicked.connect(self._show_resolution_menu)
        # Force the hover tint off when the popup closes - Qt does not
        # synthesise a Leave event in this case (same fix as the help menu).
        self._resolution_menu.aboutToHide.connect(
            lambda btn=self._resolution_btn: (btn.setDown(False), btn.set_hovered(False))
        )
        footer_row.addWidget(self._resolution_btn)
        self._rebuild_resolution_menu()
        self._update_resolution_label()

        # Markup chip: outlined icon button, same boxed weight as resolution
        # and Reference so the whole footer reads as a row of clear controls.
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        self._markup_chip = QToolButton(self)
        self._markup_chip.setIcon(_pencil_icon(ink))
        self._markup_chip.setIconSize(QSize(18, 18))
        self._markup_chip.setCursor(QtC.PointingHandCursor)
        self._markup_chip.setStyleSheet(self._CHIP_BTN_STYLE)
        self._markup_chip.setFixedHeight(_CHIP_HEIGHT)
        self._markup_chip.setToolTip(
            tr("<b>Mark up</b><br>Draw arrows, shapes, or labels on the map to "
               "show the AI what to change and where. Your sketch is sent with "
               "the prompt as visual guidance.")
        )
        self._markup_chip.clicked.connect(self.markup_clicked.emit)
        footer_row.addWidget(self._markup_chip)

        # Reference: a labelled, outlined pill (Krea-style) so users discover
        # they can feed an image or a project layer as guidance. The icon
        # carries a "+" badge; the tooltip explains what it does.
        self._attach_btn = QToolButton(self)
        self._attach_btn.setIcon(_picture_plus_icon(ink))
        self._attach_btn.setIconSize(QSize(18, 18))
        self._attach_btn.setText(tr("Ref image"))
        self._attach_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._attach_btn.setToolTip(
            tr("<b>Reference image</b><br>Click to pick an image or data file from "
               "disk. To use a QGIS layer already in your project, drag it from the "
               "Layers panel into the prompt box. Everything is cropped to your zone.")
        )
        self._attach_btn.setCursor(QtC.PointingHandCursor)
        self._attach_btn.setStyleSheet(self._CHIP_BTN_STYLE)
        self._attach_btn.setFixedHeight(_CHIP_HEIGHT)
        self._attach_btn.clicked.connect(self.attach_clicked.emit)
        footer_row.addWidget(self._attach_btn)

        # Reference counter: a small lime badge tucked INSIDE the right edge of
        # the Ref image button (parented to the button so it reads as part of
        # it, not a detached pill). Shown only when references are attached; the
        # button then reserves extra right padding so the badge never overlaps
        # the label. Dark text on the lime fill reads on both light and dark
        # themes. Hidden with the button when it collapses at capacity.
        self._attach_style_badged = self._CHIP_BTN_STYLE.replace(
            "padding: 4px 10px", "padding: 4px 22px 4px 10px"
        )
        self._ref_count = QLabel("", self._attach_btn)
        self._ref_count.setAttribute(QtC.WA_TransparentForMouseEvents)
        self._ref_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ref_count.setFixedHeight(14)
        self._ref_count.setStyleSheet(
            "QLabel { background: #8bac27; color: #14210a; font-size: 8px;"
            " font-weight: 800; border-radius: 7px; padding: 0 3px; }"
        )
        self._ref_count.hide()

        layout.addLayout(footer_row)
        self._footer_row = footer_row
        self.setStyleSheet(self._base_style)

    # -- public API --------------------------------------------------------

    def _apply_footer_fit(self) -> None:
        """Collapse footer labels when the dock is too narrow for the full row,
        so it never forces a horizontal scrollbar. Reference drops to icon-only
        (its tooltip still explains it). Measured, not threshold-based, so it
        stays correct across font/DPI."""
        avail = self.width() - 16
        if avail <= 0:
            return

        def fits() -> bool:
            self._footer_row.invalidate()
            return self._footer_row.sizeHint().width() <= avail

        # Start from the fullest state, then collapse by priority.
        self._attach_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        if not fits():
            self._attach_btn.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonIconOnly
            )

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_footer_fit()
        self._position_ref_badge()

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
            tr("Browse the library (view only while generating).")
            if readonly
            else tr("Browse templates, your recent prompts, and favorites.")
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

    def set_reference_count(self, count: int) -> None:
        """Show a small lime badge inside the Ref image button with the number
        of attached references, so the control is visibly tied to the
        thumbnails above. Hidden at zero so the footer stays clean."""
        if count > 0:
            self._ref_count.setText(str(count))
            self._ref_count.adjustSize()
            self._attach_btn.setStyleSheet(self._attach_style_badged)
            self._ref_count.show()
            self._ref_count.raise_()
            # Defer so the button has taken its padded width before we anchor.
            QTimer.singleShot(0, self._position_ref_badge)
        else:
            self._ref_count.hide()
            self._attach_btn.setStyleSheet(self._CHIP_BTN_STYLE)

    def _position_ref_badge(self) -> None:
        """Anchor the count badge to the inner right edge of the Ref button,
        vertically centered in the padding reserved for it."""
        if self._ref_count.isHidden():
            return
        btn = self._attach_btn
        badge = self._ref_count
        x = btn.width() - badge.width() - 4
        y = (btn.height() - badge.height()) // 2
        badge.move(max(0, x), max(0, y))
        badge.raise_()

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
        self._resolution_btn.setText(
            f"{resolution_chip_label(self._selected_resolution)}  ▾"
        )

    def _rebuild_resolution_menu(self) -> None:
        self._resolution_menu.clear()
        # Title so it reads as "this picks the output resolution", not as
        # another selectable row. Disabled action = non-clickable header.
        header = QLabel(tr("Output detail"))
        header.setStyleSheet(
            "color: palette(text); font-size: 12px; font-weight: 600; "
            "padding: 9px 14px 7px 14px; background: transparent;"
        )
        header_action = QWidgetAction(self._resolution_menu)
        header_action.setDefaultWidget(header)
        header_action.setEnabled(False)
        self._resolution_menu.addAction(header_action)
        sep = QFrame(self._resolution_menu)
        sep.setFrameShape(QtC.FrameHLine)
        sep.setStyleSheet("color: rgba(128,128,128,0.25); margin: 0 8px;")
        sep_action = QWidgetAction(self._resolution_menu)
        sep_action.setDefaultWidget(sep)
        sep_action.setEnabled(False)
        self._resolution_menu.addAction(sep_action)
        for res in ("1K", "2K", "4K"):
            locked = self._free_tier and res != "1K"
            selected = res == self._selected_resolution
            credits = self._resolution_costs.get(res, 0)
            widget = _ResolutionMenuItem(
                resolution_quality_name(res), res, credits, selected, locked,
                self._resolution_menu,
            )
            widget.clicked.connect(lambda r=res: self._on_menu_item_clicked(r))
            action = QWidgetAction(self._resolution_menu)
            action.setDefaultWidget(widget)
            if locked:
                action.setToolTip(tr("Subscribe for more detail"))
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
        mime = event.mimeData()
        if _mime_has_droppable(mime):
            # Force Copy: a Layers-panel drag proposes MoveAction, which makes
            # QGIS remove the layer from the tree once we accept. We only want a
            # copy as a reference, never to move the user's layer.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self._set_glow(True)
        else:
            # Diagnostic for drags we reject (e.g. some Finder file types):
            # surface the MIME formats and URLs so we can see what arrived.
            urls = [u.toString() for u in mime.urls()] if mime.hasUrls() else []
            log_debug(f"Drag rejected: formats={list(mime.formats())} urls={urls}")

    def dragMoveEvent(self, event):  # noqa: N802
        if self._readonly:
            return
        if _mime_has_droppable(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dragLeaveEvent(self, event):  # noqa: N802
        self._set_glow(False)
        event.accept()

    def dropEvent(self, event):  # noqa: N802
        self._set_glow(False)
        if self._readonly:
            event.ignore()
            return
        mime = event.mimeData()
        paths = _file_paths_from_mime(mime)
        layers = _layers_from_mime(mime)
        if paths:
            self.files_dropped.emit(paths)
        if layers:
            self.layers_dropped.emit(layers)
        if paths or layers:
            # Copy, not move - never let the source remove the user's layer.
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
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
    # Post-generation base picked in the version strip (0 = Original, i = the
    # i-th generated version). The plugin mirrors it on the canvas and uses it
    # to pick the export base + parent for the next edit.
    base_version_selected = pyqtSignal(int)
    retry_clicked = pyqtSignal(str)       # retry on same zone with (possibly edited) prompt
    pairing_requested = pyqtSignal(str)        # one-click connect: emits the minted pairing code
    pairing_cancel_requested = pyqtSignal(str)  # user cancelled the browser handoff (emits the code)
    settings_clicked = pyqtSignal()
    launch_clicked = pyqtSignal()          # user clicked "Launch AI Edit" on entry screen
    exit_clicked = pyqtSignal()            # user clicked the always-visible Exit button
    zone_clear_requested = pyqtSignal()    # Escape pressed while a zone was selected
    markup_clicked = pyqtSignal()          # user picked Tools → Mark up
    vectorize_clicked = pyqtSignal()       # user picked Tools → Vectorize
    # (layer_id, color_hex, class_label) from the "Extract regions" CTA in
    # the result panel. class_label seeds the class_name attribute on
    # every produced polygon (empty for mono-class templates that lack
    # a server-side label).
    vectorize_suggestion_clicked = pyqtSignal(str, str, str)
    # Footer Before/After is a checkable toggle: True = user wants the
    # swipe map tool armed, False = user wants it disarmed. The plugin
    # routes both states to the SwipeController.
    swipe_toggled = pyqtSignal(bool)
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
    # A past generation (history row dict) the user wants re-added to the map
    # as a georeferenced layer, or downloaded to disk. The plugin owns the
    # download + write + layer-add orchestration.
    history_add_to_map = pyqtSignal(dict)
    history_download = pyqtSignal(dict)
    # A past generation the user chose to fully reproduce: the plugin restores
    # the prompt, the reference image(s), and the original zone on the map.
    history_restore = pyqtSignal(dict)
    # Fired when the Help (?) menu opens (True) or closes (False). The
    # plugin uses this to light the green active tint on the help button
    # and to disarm the swipe map tool when the user opens another action.
    help_menu_open_changed = pyqtSignal(bool)

    def __init__(self, parent=None, reference_store: ReferenceImageStore | None = None):
        super().__init__(tr("AI Edit by TerraLab"), parent)
        # Stable objectName lets QGIS save/restore the dock (position + visibility) across
        # sessions, like the native Layers panel.
        self.setObjectName("AIEditDockWidget")
        self.setAllowedAreas(QtC.LeftDockWidgetArea | QtC.RightDockWidgetArea)
        # Scale min width with font so hi-DPI displays don't crop the footer.
        try:
            char_w = self.fontMetrics().averageCharWidth()
            self.setMinimumWidth(max(300, int(char_w * 50)))
        except Exception:
            self.setMinimumWidth(300)
        self._reference_store = reference_store
        self._library_client = None
        self._library_auth_manager = None
        self._server_catalog: dict | None = None

        # Cache of the prompt library's Recent + Favorites, so reopening the
        # library is instant instead of refetching + blank-then-fill each time.
        # Seeded from a persistent disk cache so even the FIRST open of a session
        # renders immediately (then a background refresh picks up any changes);
        # marked dirty so that refresh always runs once per session and after a
        # new generation.
        from ..core.prompts import history_cache as _history_cache

        self._library_recent_cache: list = _history_cache.get_recent_jobs()
        self._library_favorite_cache: list = _history_cache.get_favorite_jobs()
        self._library_history_loaded = bool(
            self._library_recent_cache or self._library_favorite_cache
        )
        self._library_history_dirty = True

        # Armed template: set when the user picks a preset from the prompt
        # library so edits to the prompt text don't drop the association
        # (used by plugin.py to keep vector hints + Vectorize CTA active).
        self._active_template_id: str | None = None
        self._active_template_name: str | None = None

        # Parented so the 12 s shot dies with the dock, not against a deleted widget.
        self._status_hide_timer: QTimer | None = None

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
        # Kept so the version strip can be re-homed under the progress bar while
        # a generation runs (see _place_version_strip).
        self._main_layout = main_layout

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
        self._launch_btn.setMinimumHeight(36)
        self._launch_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._launch_btn.clicked.connect(self.launch_clicked.emit)
        launch_layout.addWidget(self._launch_btn)

        self._launch_section.setVisible(False)
        main_layout.addWidget(self._launch_section)

        # --- Select-zone section: centered empty-state hero inviting the user
        # to draw the zone. The dock is otherwise blank in this state, so the
        # design-system Empty State pattern (gesture glyph + short warm copy,
        # centered) gives it a clear focal point instead of a lonely top box. ---
        self._select_zone_section = QWidget()
        self._select_zone_section.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        sz_layout = QVBoxLayout(self._select_zone_section)
        sz_layout.setContentsMargins(16, 0, 16, 0)
        sz_layout.setSpacing(10)
        sz_layout.addStretch(1)

        self._select_zone_icon = _ZoneGestureGlyph(QColor(BRAND_BLUE))
        sz_layout.addWidget(
            self._select_zone_icon, 0, Qt.AlignmentFlag.AlignHCenter
        )

        self._select_zone_header = _make_section_header(tr("Draw your zone"))
        self._select_zone_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sz_layout.addWidget(self._select_zone_header)

        # Full-width centered text: wrapping on the real width keeps the layout's
        # heightForWidth correct, so the copy is never clipped (a maxWidth + an
        # alignment flag would mis-size the height and cut the last lines off).
        self._select_zone_hint = QLabel(
            tr("Hold the left mouse button and drag to draw a box on the map. Then describe the change you want.")
        )
        self._select_zone_hint.setWordWrap(True)
        self._select_zone_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._select_zone_hint.setStyleSheet(
            "QLabel { font-size: 12px; color: palette(text);"
            " background: transparent; border: none; }"
        )
        sz_layout.addWidget(self._select_zone_hint)

        sz_layout.addStretch(1)

        self._select_zone_section.setVisible(False)
        # Stretch factor so the section claims vertical room (competing with the
        # trailing footer spacer) and its inner stretches can centre the hero.
        main_layout.addWidget(self._select_zone_section, 1)

        # --- Prompt section (shown after zone selected) ---
        self._prompt_section = QWidget()
        self._prompt_section.setContentsMargins(0, 0, 0, 0)
        self._prompt_layout = QVBoxLayout(self._prompt_section)
        self._prompt_layout.setContentsMargins(0, 0, 0, 0)
        self._prompt_layout.setSpacing(6)

        # Soft, non-blocking warning shown at the top when the drawn zone is so
        # zoomed out the model can't resolve small features (set by the plugin
        # on zone selection). Amber to read as "heads up", not an error.
        self._zone_guidance_hint = QLabel()
        self._zone_guidance_hint.setWordWrap(True)
        self._zone_guidance_hint.setStyleSheet(
            "QLabel { background-color: rgb(255, 230, 150); "
            "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; "
            "padding: 6px 8px; font-size: 11px; color: #333333; }"
        )
        self._zone_guidance_hint.setVisible(False)
        self._prompt_layout.addWidget(self._zone_guidance_hint)

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

        # Soft, non-blocking guidance hint shown live under the prompt when the
        # text looks off-rails (asks for a vector file, or talks to the tool
        # like a Q&A/counting bot). Steers the user without blocking Generate.
        # Detection is high-precision (see detect_prompt_guidance); a valid
        # edit/detect/segment instruction never shows this.
        self._prompt_guidance_hint = QLabel()
        self._prompt_guidance_hint.setWordWrap(True)
        self._prompt_guidance_hint.setStyleSheet(
            "QLabel { background-color: rgba(25, 118, 210, 0.08); "
            "border: 1px solid rgba(25, 118, 210, 0.2); border-radius: 4px; "
            "padding: 6px 8px; font-size: 11px; color: palette(text); }"
        )
        self._prompt_guidance_hint.setVisible(False)
        self._prompt_layout.addWidget(self._prompt_guidance_hint)

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
            self._reference_widget.upsell_requested.connect(self._show_reference_upsell)
            # Forward container actions: drop on container + paste in textbox +
            # paperclip click all funnel into the reference widget.
            self._prompt_container.files_dropped.connect(self._reference_widget.add_paths)
            self._prompt_container.layers_dropped.connect(self._reference_widget.add_layers)
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
        # Bigger, easier-to-hit indicator (the default is tiny and hard to click).
        # Size only, no border/background, so the native checkmark still renders.
        self._consent_check.setStyleSheet(
            "QCheckBox::indicator { width: 18px; height: 18px; }"
        )
        self._consent_check.setCursor(QtC.PointingHandCursor)
        consent_layout = QHBoxLayout()
        consent_layout.setContentsMargins(0, 0, 0, 0)
        consent_layout.setSpacing(8)
        consent_layout.addWidget(self._consent_check, 0, QtC.AlignTop)
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
        self._exit_btn.setMinimumHeight(36)
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
        # Manual link routing (not setOpenExternalLinks): http links still open
        # in the browser, but the "Report a problem" sentinel opens the in-app
        # log-report dialog instead of being handed to the OS as a bad URL.
        self._status_label.linkActivated.connect(self._on_status_link)
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
        # A single prompt screen. The version strip below the prompt is the base
        # picker: Original is pinned left, each result appends to the right, and
        # the selected tile is what the next edit builds on. Lives inside
        # _result_section so every state transition that hides it hides together.
        self._result_section = QWidget()
        self._result_layout = QVBoxLayout(self._result_section)
        self._result_layout.setContentsMargins(0, 0, 0, 0)
        self._result_layout.setSpacing(6)

        # --- Prompt + version strip + Generate -----------------------------
        self._result_prompt_widget = QWidget()
        self._result_prompt_layout = QVBoxLayout(self._result_prompt_widget)
        self._result_prompt_layout.setContentsMargins(0, 0, 0, 0)
        self._result_prompt_layout.setSpacing(6)

        # Editable prompt (edit and retry)
        self._result_prompt_input = _SubmitTextEdit()
        self._result_prompt_input.setPlaceholderText(
            tr("Type a new prompt to retry, or pick an action below")
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
            self._result_prompt_container.layers_dropped.connect(
                self._reference_widget.add_layers
            )
            self._result_prompt_container.attach_clicked.connect(
                self._reference_widget.open_file_picker
            )
            self._result_prompt_input.images_pasted.connect(
                self._reference_widget.add_paths
            )
        self._result_prompt_layout.addWidget(self._result_prompt_container)

        # Same soft off-rails hint as the first-run prompt, so iterating on a
        # v1/v2 gets the same guidance (vector / measure / chatbot).
        self._result_guidance_hint = QLabel()
        self._result_guidance_hint.setWordWrap(True)
        self._result_guidance_hint.setStyleSheet(
            "QLabel { background-color: rgba(25, 118, 210, 0.08); "
            "border: 1px solid rgba(25, 118, 210, 0.2); border-radius: 4px; "
            "padding: 6px 8px; font-size: 11px; color: palette(text); }"
        )
        self._result_guidance_hint.setVisible(False)
        self._result_prompt_layout.addWidget(self._result_guidance_hint)

        # Version strip: the base picker. Original pinned left, results append
        # right, selected tile drives the next edit. Hidden until seeded.
        self._version_strip = VersionStrip()
        self._version_strip.version_selected.connect(self._on_version_selected)
        self._result_prompt_layout.addWidget(self._version_strip)

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
        self._result_exit_btn.setMinimumHeight(36)
        self._result_exit_btn.setStyleSheet(_BTN_GHOST)
        self._result_exit_btn.clicked.connect(self._on_exit_clicked)
        result_actions_row.addWidget(self._result_exit_btn, 0)

        self._result_prompt_layout.addLayout(result_actions_row)

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
        # ArrowCursor on the wrapper; Qt switches to PointingHand over the <a>.
        self._layer_saved_label.setCursor(QtC.ArrowCursor)
        self._layer_saved_label.linkActivated.connect(self._on_layer_saved_link_clicked)
        self._layer_saved_label.setVisible(False)
        self._saved_layer_id: str | None = None
        self._result_prompt_layout.addWidget(self._layer_saved_label)

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
            "background: rgba(128,128,128,0.3); border: 1px solid rgba(128,128,128,0.5);"
            " border-radius: 3px;"
        )
        cta_layout.addWidget(self._vectorize_cta_swatch)
        self._vectorize_cta_btn = QPushButton()
        self._vectorize_cta_btn.setText(tr("Vectorize this result") + " →")
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
        self._vectorize_cta_pending: tuple[str, str, str] | None = None
        self._result_prompt_layout.addWidget(self._vectorize_cta_section)

        self._result_layout.addWidget(self._result_prompt_widget)
        self._result_prompt_widget.setVisible(False)

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
        benefits_html = "<br>".join((
            tr("Subscribe to unlock:"),
            "&nbsp;&nbsp;✓&nbsp; " + tr("3,000 credits every month"),
            "&nbsp;&nbsp;✓&nbsp; " + tr("Detailed and Maximum output"),
            "&nbsp;&nbsp;✓&nbsp; " + tr("Cancel anytime"),
        ))
        self._trial_info_benefits = QLabel(benefits_html)
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

        # The Before/After swipe has no dock panel: it's a toggle on the
        # footer Before/After button that arms a map tool on the canvas.
        # See SwipeController in swipe_panel.py and the wiring in plugin.py.

        # Spacer to push footer to bottom
        layout.addStretch()

        # --- Update notification, pinned at the bottom (above the footer) so it
        # stays visible in every state (idle, generating, result). Most users
        # never check for plugin updates, so this is how they learn one exists.
        self._setup_update_notification(layout)

        # Footer section - single row: ring + count + upgrade pill on the
        # left, gear/help menus on the right. As the dock narrows,
        # _apply_footer_responsive collapses the count then shortens the pill
        # so the right-side icons are never clipped.
        footer_widget = QWidget()
        footer_row = QHBoxLayout(footer_widget)
        footer_row.setContentsMargins(0, 4, 0, 4)
        footer_row.setSpacing(6)
        # Kept so resizeEvent can measure the row's natural width and collapse
        # low-priority items (count, then pill text) until it fits.
        self._footer_row = footer_row

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
        # Text is (re)set by _apply_footer_responsive, which shortens it to
        # "Upgrade" when the dock is too narrow for the full label.
        self._upgrade_cta = QPushButton(tr("Unlock more detail"))
        self._upgrade_cta.setToolTip(
            tr("Subscribe to unlock Detailed and Maximum output, 3,000 credits per month, cancel anytime.")
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

        # Swipe button - opens the Before/after compare panel. Hidden until
        # an AI Edit output exists so the footer stays empty on first launch
        # (visibility synced by set_vectorize_button_visible together with
        # the vectorize footer button - both depend on having a generation).
        # Before/After is a checkable toggle: click once to arm the swipe
        # map tool, click again (or Esc on the canvas) to disarm. When
        # armed the button paints a green tint so the user can see at a
        # glance which tool the canvas is in.
        self._swipe_btn = _FooterIconButton(footer_widget)
        self._swipe_btn.setAccessibleName(tr("Before / after"))
        self._swipe_btn.setCursor(QtC.PointingHandCursor)
        self._swipe_btn.setFocusPolicy(QtC.NoFocus)
        self._swipe_btn.setStyleSheet(_FOOTER_ICON_TOGGLE_STYLE)
        self._swipe_btn.setIcon(self._make_swipe_glyph_icon())
        self._swipe_btn.setIconSize(QSize(20, 20))
        self._swipe_btn.setCheckable(True)
        self._swipe_btn.setEnabled(False)  # gated on active layer eligibility
        self._swipe_btn.toggled.connect(self.swipe_toggled.emit)
        swipe_seq = QKeySequence("Alt+B")
        self._swipe_btn.setShortcut(swipe_seq)
        self._swipe_btn.setToolTip(
            tr("Before / after ({})").format(
                swipe_seq.toString(QKeySequence.SequenceFormat.NativeText)
            )
        )
        self._swipe_btn.setVisible(False)
        footer_row.addWidget(self._swipe_btn)

        # Settings button - gear icon, opens the Account Settings dialog
        # directly. Shortcuts have moved inside that dialog.
        self._settings_btn = _FooterIconButton(footer_widget)
        self._settings_btn.setIcon(self._make_gear_glyph_icon())
        self._settings_btn.setIconSize(QSize(20, 20))
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
        help_menu.addAction(tr("Report a problem"), self._on_report_problem)
        self._help_btn.setMenu(help_menu)
        # Force the hover tint off when the popup closes - Qt does not
        # synthesise a Leave event in this case. Also light the green
        # active tint while the menu is open and broadcast the change so
        # the plugin can disarm the swipe map tool.
        help_menu.aboutToShow.connect(
            lambda: (self._help_btn.set_active(True),
                     self.help_menu_open_changed.emit(True))
        )
        help_menu.aboutToHide.connect(
            lambda btn=self._help_btn: (
                btn.setDown(False), btn.set_hovered(False), btn.set_active(False),
                self.help_menu_open_changed.emit(False),
            )
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
        # Kept so the footer responsive logic reads the true available width
        # (the viewport excludes the vertical scrollbar), not the dock width.
        self._scroll_area = scroll_area

        # State
        self._zone_selected = False
        self._activated = False
        self._checking_credits = False
        self._swipe_eligible = False
        self._swipe_panel_lock = False
        self._is_free_tier = True  # default hidden until confirmed Pro
        self._cached_used: int | None = None
        self._cached_limit: int | None = None
        # Universal default. Every tier starts on "1K"; paid users can still
        # bump to 2K/4K by hand, but the dock never opens on a higher tier by
        # default. Free-tier confirmation keeps coercing to "1K" anyway.
        self._selected_resolution = "1K"
        # Credit cost per resolution. Used to suffix the Generate/Regenerate
        # button text ("Generate (30 credits)"). Overwritten by
        # set_resolution_credit_costs once the server config loads.
        self._resolution_credit_costs: dict[str, int] = {"1K": 20, "2K": 30, "4K": 40}

        # Layer monitoring. We listen to add/remove, visibility-changed in the
        # legend, AND project lifecycle (readProject/cleared) so the Launch
        # button stays in sync when the user starts a new project or opens a
        # different one - those transitions replace the layerTreeRoot, which
        # invalidates any visibilityChanged binding made before.
        # layersAdded/layersRemoved fire before QGIS finishes syncing the layer
        # tree, so the new node is not yet in layerTreeRoot().findLayers() when a
        # synchronous handler runs. Defer the gate re-check by one event loop tick
        # (same pattern as _on_project_loaded) so adding the first basemap on a
        # fresh session actually enables the Launch button.
        QgsProject.instance().layersAdded.connect(self._schedule_layer_warning_update)
        QgsProject.instance().layersRemoved.connect(self._schedule_layer_warning_update)
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

        # --- Light headline (the gray instruction box is intentionally gone:
        # the button + one reassurance line is all a new user needs) ---
        layout.addSpacing(6)
        self._setup_header = QLabel(tr("Edit your map with AI") + " 🍌")
        self._setup_header.setAlignment(QtC.AlignCenter)
        self._setup_header.setStyleSheet(
            "font-weight: 600; font-size: 14px; color: palette(text);"
        )
        layout.addWidget(self._setup_header)

        layout.addSpacing(14)

        # --- Primary: one tap to sign in (browser handoff, no copy-paste) ---
        self._connect_section = QWidget()
        connect_layout = QVBoxLayout(self._connect_section)
        connect_layout.setContentsMargins(0, 0, 0, 0)
        connect_layout.setSpacing(6)

        self._connect_btn = QPushButton(tr("Sign in / Sign up to start"))
        self._connect_btn.setToolTip(tr("Sign in via your browser to start using AI Edit"))
        self._connect_btn.setMinimumHeight(38)
        self._connect_btn.setCursor(QtC.PointingHandCursor)
        self._connect_btn.setStyleSheet(_BTN_GREEN_AUTH)
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        connect_layout.addWidget(self._connect_btn)

        connect_hint = QLabel(tr("5 free AI Edits, no credit card"))
        connect_hint.setAlignment(QtC.AlignCenter)
        connect_hint.setWordWrap(True)
        connect_hint.setStyleSheet("font-size: 11px; color: palette(text);")
        connect_layout.addWidget(connect_hint)

        layout.addWidget(self._connect_section)

        # --- Waiting state: shown while the browser handoff is in progress ---
        self._pairing_wait_section = QWidget()
        wait_layout = QVBoxLayout(self._pairing_wait_section)
        wait_layout.setContentsMargins(0, 4, 0, 0)
        wait_layout.setSpacing(12)

        # Spinner + static status text on one centered row (no jumping dots).
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addStretch(1)
        self._pairing_spinner = _Spinner(16)
        status_row.addWidget(self._pairing_spinner, 0, QtC.AlignVCenter)
        self._pairing_status = QLabel(tr("Waiting for you to sign in in your browser"))
        self._pairing_status.setWordWrap(True)
        self._pairing_status.setStyleSheet("font-size: 12px; color: palette(text);")
        status_row.addWidget(self._pairing_status, 0, QtC.AlignVCenter)
        status_row.addStretch(1)
        wait_layout.addLayout(status_row)

        # Two compact, filled buttons side by side.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._pairing_reopen_btn = QPushButton(tr("Open again"))
        self._pairing_reopen_btn.setToolTip(tr("Didn't open? Open the page again"))
        self._pairing_reopen_btn.setMinimumHeight(28)
        self._pairing_reopen_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_reopen_btn.setStyleSheet(_BTN_PAIR_NEUTRAL)
        self._pairing_reopen_btn.clicked.connect(self._on_pairing_reopen_clicked)
        btn_row.addWidget(self._pairing_reopen_btn)

        self._pairing_cancel_btn = QPushButton(tr("Cancel"))
        self._pairing_cancel_btn.setMinimumHeight(28)
        self._pairing_cancel_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_cancel_btn.setStyleSheet(_BTN_PAIR_CANCEL)
        self._pairing_cancel_btn.clicked.connect(self._on_pairing_cancel_clicked)
        btn_row.addWidget(self._pairing_cancel_btn)
        wait_layout.addLayout(btn_row)

        # Copy the connect link so the user can finish sign-in in a different
        # browser (e.g. their default has no Google session). Standard CLI
        # device-flow fallback ("open browser, or copy this link").
        self._pairing_copy_btn = QPushButton(tr("Link not opening? Copy link"))
        self._pairing_copy_btn.setCursor(QtC.PointingHandCursor)
        self._pairing_copy_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none;"
            " color: palette(text); font-size: 11px; padding: 2px;"
            " text-decoration: underline; }"
        )
        self._pairing_copy_btn.clicked.connect(self._on_pairing_copy_clicked)
        wait_layout.addWidget(self._pairing_copy_btn, 0, QtC.AlignCenter)

        self._pairing_wait_section.setVisible(False)
        self._pairing_active = False
        layout.addWidget(self._pairing_wait_section)

        # One timer rotates the spinner while waiting. Parented to the dock
        # (segfault-safe) and stopped the moment the wait section hides.
        self._pairing_anim_timer = QTimer(self)
        self._pairing_anim_timer.setInterval(80)
        self._pairing_anim_timer.timeout.connect(self._pairing_spinner.advance)
        self._pending_pairing_code = ""
        self._pairing_link = ""

        layout.addStretch(1)

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

    def _set_upgrade_cta_wanted(self, wanted: bool) -> None:
        self._upgrade_cta_wanted = wanted
        self._upgrade_cta.setVisible(wanted)
        self._apply_footer_responsive()

    def _set_credits_wanted(self, wanted: bool) -> None:
        self._credits_wanted = wanted
        self._apply_footer_responsive()

    def _set_upgrade_cta_text(self, full: bool) -> None:
        """Full label vs the short "More detail" fallback. Guarded so
        resizeEvent (which fires often) only relayouts when the text changes."""
        text = tr("Unlock more detail") if full else tr("More detail")
        if self._upgrade_cta.text() != text:
            self._upgrade_cta.setText(text)

    def _apply_footer_responsive(self) -> None:
        """Collapse low-priority footer items, by priority, until the row fits
        the dock width. Measured (not threshold-based) so it stays correct
        across font size, DPI and translated pill length.

        The right-side icons (vectorize / swipe / settings / help) are never
        touched - they always stay reachable. Kept longest -> dropped first:
          1. usage count label ("100 / 200")
          2. upgrade pill text shortened to "Upgrade" (the CTA stays visible)
          3. credit ring (last resort only)
        """
        scroll = getattr(self, "_scroll_area", None)
        if scroll is None:
            return
        # Viewport excludes the vertical scrollbar; subtract the main layout's
        # 8px left/right margins to get the width the footer row actually gets.
        avail = scroll.viewport().width() - 16
        if avail <= 0:
            return

        # Start from the fullest state the current data allows, then collapse.
        self._credits_label.setVisible(self._credits_wanted)
        self._credit_ring.setVisible(self._credits_wanted)
        self._set_upgrade_cta_text(full=True)

        def fits() -> bool:
            # invalidate() drops the layout's cached hint so the measurement
            # reflects the visibility / text changes made just above.
            self._footer_row.invalidate()
            return self._footer_row.sizeHint().width() <= avail

        if not fits() and self._credits_wanted:
            self._credits_label.setVisible(False)
        if not fits() and self._upgrade_cta_wanted:
            self._set_upgrade_cta_text(full=False)
        if not fits() and self._credits_wanted:
            self._credit_ring.setVisible(False)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_footer_responsive()

    def _setup_update_notification(self, parent_layout: QVBoxLayout) -> None:
        """Build the 'update available' banner, hidden until check_for_updates finds one.

        Pinned at the bottom of the dock (above the footer) as a sibling of the
        main sections, so it stays visible in every state (idle, generating,
        result) and even while ``_main_widget`` is hidden (unactivated state,
        tool panels). Most users never check for plugin updates, so this banner
        is how they learn a newer version exists.
        """
        # Container only exists to right-align the badge.
        self._update_notif_container = QWidget()
        container_layout = QHBoxLayout(self._update_notif_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addStretch()

        self._update_notification_label = QLabel("")
        self._update_notification_label.setStyleSheet(
            "background-color: rgba(25, 118, 210, 0.15); "
            "border: 2px solid rgba(25, 118, 210, 0.4); border-radius: 6px; "
            "padding: 6px 12px; font-size: 12px; font-weight: bold; color: palette(text);"
        )
        self._update_notification_label.setOpenExternalLinks(False)
        self._update_notification_label.linkActivated.connect(self._on_open_plugin_manager)
        container_layout.addWidget(self._update_notification_label)

        self._update_notif_container.setVisible(False)
        parent_layout.addWidget(self._update_notif_container)

    def check_for_updates(self) -> bool:
        """Show the update banner if QGIS reports a newer plugin version.

        Reads QGIS's cached plugin-repository metadata (the plugin itself makes
        no network call). Returns True once a newer version is detected so the
        caller can stop polling.
        """
        try:
            from pyplugin_installer.installer_data import plugins

            # The pyplugin_installer key is the installed plugin's folder name,
            # which equals this package's root directory name. In a dev install
            # the folder name differs from the published id, so no banner shows.
            plugin_id = os.path.basename(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            )
            plugin_data = plugins.all().get(plugin_id)
            if plugin_data and plugin_data.get("status") == "upgradeable":
                available_version = plugin_data.get("version_available", "?")
                text = '{} <a href="#update" style="color: #1976d2; font-weight: bold;">{}</a>'.format(
                    tr("New version available: v{version}").format(version=available_version),
                    tr("Update now"),
                )
                self._update_notification_label.setText(text)
                self._update_notif_container.setVisible(True)
                return True
        except Exception:
            pass  # nosec B110  No repo metadata yet, dev install, etc.
        return False

    def _on_open_plugin_manager(self, _link: str = "") -> None:
        """Open QGIS's Plugin Manager on the Upgradeable tab (index 3)."""
        try:
            from qgis.utils import iface

            iface.pluginManagerInterface().showPluginManager(3)
        except Exception:
            pass  # nosec B110

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

        self._warning_text = QLabel(tr(
            "No visible imagery. Add a GeoTIFF, image file, or online basemap "
            "(WMS, XYZ) to your project."
        ))
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

    def _place_version_strip(self, target: str) -> None:
        """Re-home the version strip so the lineage stays visible across states.

        ``target`` is "result" (its home, under the result prompt and above the
        Generate row) or "generating" (under the progress bar, so the user keeps
        seeing the versions while the next edit renders). Moving between layouts
        reparents the single strip instance - it is never rebuilt, so tiles and
        selection survive the move.
        """
        self._main_layout.removeWidget(self._version_strip)
        self._result_prompt_layout.removeWidget(self._version_strip)
        if target == "generating":
            # Sit between the prompt and the progress bar (above it), not below.
            idx = self._main_layout.indexOf(self._progress_widget)
            self._main_layout.insertWidget(idx, self._version_strip)
        else:
            # Index 1 = right after the result prompt container (index 0).
            self._result_prompt_layout.insertWidget(1, self._version_strip)
        self._version_strip.setVisible(self._version_strip.count() > 0)

    def _sync_attach_buttons(self) -> None:
        """Hide the + button at capacity, and mirror the reference count onto
        both prompt containers so the Ref image control shows how many images
        are attached (the link to the thumbnails above)."""
        if self._reference_widget is None:
            return
        enabled = not self._reference_widget.at_capacity()
        count = self._reference_widget.count()
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_attach_enabled(enabled)
            container.set_reference_count(count)

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
        # Reentrancy guard: a fast double-click can emit `templates_clicked`
        # twice before the first modal grabs input, stacking two nested exec()
        # loops over the same widgets. The second teardown then races the first
        # and crashes QGIS. One library at a time.
        if getattr(self, "_library_open", False):
            return None
        self._library_open = True
        # Kick off a background catalog refetch so the NEXT open is fresh.
        # This open uses whatever catalog the dock currently has.
        self.catalog_refresh_requested.emit()
        from .dialogs.prompt_templates_dialog import PromptTemplatesDialog

        auth_provider = None
        if self._library_auth_manager is not None:
            auth_provider = self._library_auth_manager.get_auth_header
        # Parent the dialog to the QGIS main window, not to this dock widget.
        # On macOS in fullscreen, a dialog parented to a (possibly floating)
        # dock widget gets put into its own Mission Control Space and steals
        # the user out of QGIS. Anchoring to mainWindow() keeps the popup in
        # the same Space as QGIS itself.
        parent_window = self._main_window_for_dialog()
        browse_only = self._prompt_container.is_readonly() or self._result_prompt_container.is_readonly()
        # Build inside the try so a failure here still clears _library_open
        # (otherwise the guard above would wedge the library shut for good).
        dlg = None
        try:
            history_fresh = (
                self._library_history_loaded and not self._library_history_dirty
            )
            dlg = PromptTemplatesDialog(
                parent_window,
                client=self._library_client,
                auth_provider=auth_provider,
                server_catalog=self._server_catalog,
                browse_only=browse_only,
                recent_jobs=self._library_recent_cache,
                favorite_jobs=self._library_favorite_cache,
                history_fresh=history_fresh,
            )
            # Add-to-map / download run in a background task while the modal
            # stays open, so the user can act on several past generations in
            # one visit.
            dlg.generation_action.connect(self._on_history_generation_action)
            dlg.history_synced.connect(self._on_library_history_synced)
            if dlg.exec():
                # Full-restore beats prompt selection: the user wants the whole
                # generation context (prompt + refs + zone) back, not just text.
                restore = dlg.get_restore_job()
                if restore:
                    self.history_restore.emit(restore)
                    return None
                preset = dlg.get_selected_preset()
                if preset and not preset.get("from_recent") and not preset.get("from_favorites"):
                    self.template_selected.emit(
                        str(preset.get("id") or ""),
                        str(preset.get("label") or ""),
                    )
                return preset
            return None
        finally:
            self._library_open = False
            if dlg is not None:
                dlg.deleteLater()

    def _on_library_history_synced(self, recent: list, favorites: list) -> None:
        """Store the library's freshly fetched/edited Recent + Favorites so the
        next open is instant. Fresh until a new generation marks it dirty. Also
        persisted to disk so the next SESSION opens warm too."""
        self._library_recent_cache = list(recent or [])
        self._library_favorite_cache = list(favorites or [])
        self._library_history_loaded = True
        self._library_history_dirty = False
        from ..core.prompts import history_cache

        history_cache.save_recent_jobs(self._library_recent_cache)
        history_cache.save_favorite_jobs(self._library_favorite_cache)

    def mark_library_history_dirty(self) -> None:
        """Force the next library open to refetch Recent/Favorites (e.g. after a
        new generation completes so it shows up)."""
        self._library_history_dirty = True

    def _on_history_generation_action(self, action: str, job: dict):
        """Route a past-generation action from the prompt library up to the
        plugin, which owns the download + write + layer-add work."""
        if action == "add_to_map":
            self.history_add_to_map.emit(job)
        elif action in ("download", "download_output"):
            self.history_download.emit({**job, "download_side": "output"})
        elif action == "download_input":
            self.history_download.emit({**job, "download_side": "input"})

    # --- Public methods ---

    def set_launch_enabled(self, enabled: bool) -> None:
        """Disable Launch AI Edit during async validation/credit checks
        so the user can't fire a session before we know they're authorised.
        Avoids flashing the sign-up screen on reload. Re-enabling goes through
        the layer gate instead of flipping the button directly: with no visible
        layer there is nothing to capture, and a direct enable here used to
        override that lock right after the credits check."""
        if enabled:
            self._update_layer_warning()
        else:
            self._launch_btn.setEnabled(False)

    def set_activated(self, activated: bool):
        self._activated = activated
        self._activation_widget.setVisible(not activated)
        self._main_widget.setVisible(activated)
        self._settings_btn.setVisible(activated)
        # Vectorize + Before/after both work on any existing AI-Edit raster
        # (not just a fresh result), so they are revealed the moment the dock
        # is activated. Their per-click eligibility is gated by the active
        # layer (set_swipe_button_enabled, vectorize_btn enable refresh).
        self._vectorize_btn.setVisible(activated)
        self._set_swipe_button_visible(activated)
        if activated:
            self.hide_trial_info()
            self._update_layer_warning()
            self._set_upgrade_cta_wanted(self._is_free_tier)
            self.set_launch_state()
            # A stale pairing spinner must never survive a successful activation.
            self._stop_pairing_wait()
        else:
            self._setup_header.setVisible(True)
            self._connect_section.setVisible(True)
            self._stop_pairing_wait()
            self._activation_message.setVisible(False)
            self.hide_activation_limit_cta()
            # The credits ring + count and the upsell pill belong to a signed-in
            # session only; clear them so they never linger after sign-out.
            self._set_credits_wanted(False)
            self._set_upgrade_cta_wanted(False)
            self.hide_trial_info()

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
        self._is_free_tier = is_free_tier
        # Keep the reference-image gate in sync with the confirmed tier.
        if self._reference_widget is not None:
            self._reference_widget.set_free_tier(is_free_tier)
        # No free→paid resolution bump: every tier defaults to "1K". Paid users
        # raise it to 2K/4K by hand if they want, so we never override their
        # current selection on a tier change.
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

    def set_reference_target_extent(self, extent, crs) -> None:
        """Align reference image renders to the generation zone extent (pushed
        by the plugin when a zone is drawn). (None, None) reverts to the view."""
        if self._reference_widget is not None:
            self._reference_widget.set_target_extent(extent, crs)

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
        QtC.safe_single_shot(0, self, self._focus_prompt_input)
        QtC.safe_single_shot(50, self, self._focus_prompt_input)

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
        self.set_reference_target_extent(None, None)
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

    def clear_active_template(self) -> None:
        """Drop the armed template so a new zone doesn't reuse a preset that
        was picked for the previous zone. Called from plugin._on_zone_selected."""
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
                # Start at 1% so the bar is visible immediately on click. The
                # prep ticker animates 1->10% during canvas+upload phases, then
                # the worker's first real progress signal (>=5%) takes over.
                self._progress_bar.setValue(1)
                self._progress_target = 1
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
                # Keep the version lineage visible under the progress bar while
                # the next edit renders, but locked (no base switch mid-run).
                self._place_version_strip("generating")
                self._version_strip.set_readonly(True)
                self._consent_widget.setVisible(False)
                self._generate_btn.setVisible(False)
                # Hide Exit during generation: the user shouldn't be tempted to
                # cancel mid-run from this row. The title-bar X still works as
                # an escape hatch.
                self._exit_btn.setVisible(False)
                self._start_prep_ticker("canvas")
            else:
                self._stop_prep_ticker()
                self._prompt_container.set_readonly(False)
                if self._reference_widget is not None:
                    self._reference_widget.set_readonly(False)
                self._consent_widget.setVisible(not has_consent() and self._zone_selected)
                self._generate_btn.setVisible(True)
                self._exit_btn.setVisible(True)
                self._refresh_resolution_triggers()
                self._prompt_section.setVisible(True)
                # Cancelled / errored run: bring the strip back to its home and
                # unlock it (the result screen may re-appear with it).
                self._place_version_strip("result")
                self._version_strip.set_readonly(False)
        finally:
            self.setUpdatesEnabled(True)

    # Prep ticker: animates the bar 1->10% during canvas (export) and upload
    # phases, rotating playful messages so the user gets visible feedback
    # instead of a static "Preparing..." until the worker's first poll.
    def _start_prep_ticker(self, phase: str) -> None:
        self._prep_phase = phase
        self._prep_messages_pool = get_phase_messages(phase) or [tr("Preparing...")]
        random.shuffle(self._prep_messages_pool)
        self._prep_idx = 0
        # Set first message right away so the user sees something immediately.
        self._progress_label.setText(self._prep_messages_pool[0])
        if not hasattr(self, "_prep_ticker") or self._prep_ticker is None:
            self._prep_ticker = QTimer(self)
            self._prep_ticker.setInterval(1300)
            self._prep_ticker.timeout.connect(self._tick_prep)
        if not self._prep_ticker.isActive():
            self._prep_ticker.start()

    def _stop_prep_ticker(self) -> None:
        if hasattr(self, "_prep_ticker") and self._prep_ticker is not None and self._prep_ticker.isActive():
            self._prep_ticker.stop()

    def prep_advance_phase(self, phase: str) -> None:
        """Switch the prep ticker to a new message pool mid-flight.
        Called by plugin.py when canvas export finishes -> upload phase starts.
        """
        if not hasattr(self, "_prep_ticker") or self._prep_ticker is None or not self._prep_ticker.isActive():
            return
        self._start_prep_ticker(phase)

    def _tick_prep(self) -> None:
        # Cycle messages
        if self._prep_messages_pool:
            self._prep_idx = (self._prep_idx + 1) % len(self._prep_messages_pool)
            self._progress_label.setText(self._prep_messages_pool[self._prep_idx])
        # Advance the bar by 1% per tick, capped at the phase ceiling. Stops
        # naturally when the worker emits a real progress signal (>=5%) since
        # set_progress_message stops the prep ticker.
        cap = 5 if self._prep_phase == "canvas" else 10
        current = self._progress_bar.value()
        if current < cap:
            self._progress_target = min(cap, current + 1)
            if not hasattr(self, "_progress_timer") or self._progress_timer is None:
                self._progress_timer = QTimer(self)
                self._progress_timer.setInterval(30)
                self._progress_timer.timeout.connect(self._animate_progress)
            if not self._progress_timer.isActive():
                self._progress_timer.start()

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
        # First real worker progress signal -> stop the prep ticker so it stops
        # competing for the label + bar with the worker's own messages.
        self._stop_prep_ticker()
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
                "QWidget { background-color: rgba(139, 172, 39, 0.25); "
                "border: 1px solid rgba(139, 172, 39, 0.6); border-radius: 4px; }"
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
        if self._cached_used is None or self._cached_limit is None:
            return False
        return self._is_free_tier and self._cached_limit > 0 and self._cached_used >= self._cached_limit

    def seed_version_strip(self, original_pixmap, prompt: str = "", meta: dict | None = None) -> None:
        """Seed the strip with the Original tile (selected). Called once per
        lineage when the clean base capture becomes available."""
        self._version_strip.reset(original_pixmap, prompt, meta)
        self._update_result_generate_label()

    def add_version_thumb(self, pixmap, prompt: str = "", meta: dict | None = None) -> int:
        """Append a generated version to the strip and auto-select it."""
        index = self._version_strip.add_version(pixmap, prompt, meta)
        self._update_result_generate_label()
        return index

    def reset_version_strip(self) -> None:
        """Clear and hide the strip (new zone breaks the lineage)."""
        self._version_strip.clear()

    def select_version(self, index: int) -> None:
        """Move the strip's selection ring without emitting version_selected."""
        self._version_strip.set_selected(index)

    def reveal_version_strip(self) -> None:
        """Keep the restored lineage in its iterate home (above the Generate
        row). Restoring already entered the iterate state; this just re-asserts
        the strip's placement once its thumbnails arrive."""
        self._place_version_strip("result")

    def get_cached_recent_jobs(self) -> list:
        """Session-cached past generations (newest first). Used to rebuild the
        iteration chain when the user reuses a generation from Recent."""
        return list(self._library_recent_cache or [])

    def set_version_strip_readonly(self, readonly: bool) -> None:
        self._version_strip.set_readonly(readonly)

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

        # Start the next iteration from a blank prompt instead of replaying the
        # one that produced this result. An empty field nudges the user to
        # describe a fresh change rather than re-running the same instruction.
        self._result_prompt_input.clear()
        self._update_result_generate_enabled()
        self._result_prompt_container.set_readonly(False)
        # Generation is done: clear the (now hidden) prompt container's readonly
        # flag too. set_generating(True) set it and the success path never calls
        # set_generating(False), so without this the prompt library stays in
        # view-only mode (browse_only) and template clicks are ignored.
        self._prompt_container.set_readonly(False)
        self._result_section.setVisible(True)
        # Single prompt screen: the version strip below it carries the base
        # choice, so there is no separate choice step to land on first. Bring the
        # strip back from the progress area (success skips set_generating(False)).
        self._result_prompt_widget.setVisible(True)
        self._place_version_strip("result")
        self._version_strip.set_readonly(False)
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
        self._layer_saved_label.setText(tr("Saved as {name}").format(name=link_html))
        self._layer_saved_label.setVisible(True)

        self._set_upgrade_cta_wanted(self._is_free_tier and self._activated)

    def show_trial_exhausted_info(self, message: str, subscribe_url: str):
        self._hide_limit_cta()
        # The CTA tail is suppressed server-side now (the dedicated primary
        # button below carries that action). The previous English substring
        # strip broke fr/es/pt_BR translations and is gone for that reason.
        title = (message or "").strip()
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
        # Silent: the credit refresh is fast enough that a flashed status box
        # is more noise than signal. Flag kept in case callers need to query.
        self._checking_credits = checking

    def hide_trial_info(self):
        self._trial_info_box.setVisible(False)
        self._hide_status_box()
        self._hide_limit_cta()

    def get_prompt(self) -> str:
        return self._prompt_input.toPlainText().strip()

    # --- Private methods ---

    def _schedule_layer_warning_update(self, *_args):
        """Re-check the Launch gate after the layer tree has settled.

        Connected to ``layersAdded`` / ``layersRemoved``, which fire mid-sync:
        the layer tree node for the new layer is not yet present in
        ``layerTreeRoot().findLayers()`` at emit time. Deferring by one event
        loop tick lets QGIS finish wiring the node before we evaluate visibility.
        """
        QtC.safe_single_shot(0, self, self._update_layer_warning)

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
        QtC.safe_single_shot(0, self, self._update_layer_warning)

    def _on_settings_btn_clicked(self):
        self.settings_clicked.emit()

    def _on_upgrade_clicked(self):
        from ..core import telemetry
        from ..core import telemetry_events as te
        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "upgrade_cta"})
        QDesktopServices.openUrl(QUrl(get_subscribe_url()))

    def _on_trial_info_subscribe_clicked(self):
        from ..core import telemetry
        from ..core import telemetry_events as te
        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "trial_exhausted_box"})
        url = self._trial_info_url or get_subscribe_url()
        QDesktopServices.openUrl(QUrl(url))

    def _on_exit_clicked(self):
        """Exit: ask the plugin to cancel + return to LAUNCH state."""
        self.exit_clicked.emit()

    def _on_key_toggle(self, checked: bool):
        pass

    def _on_connect_clicked(self):
        """Start the one-click browser handoff. Mints a high-entropy pairing
        code; the plugin opens the browser and polls until it gets the key."""
        import secrets
        self._pending_pairing_code = secrets.token_urlsafe(32)
        self.show_pairing_waiting()
        self.pairing_requested.emit(self._pending_pairing_code)

    def _on_pairing_reopen_clicked(self):
        """Re-open the browser with the SAME code (do not mint a new one)."""
        if self._pending_pairing_code:
            self.pairing_requested.emit(self._pending_pairing_code)

    def set_pairing_link(self, url: str):
        """Store the connect URL so the copy-link button can offer it (the URL
        is built plugin-side; the dock only displays it)."""
        self._pairing_link = url or ""

    def _on_pairing_copy_clicked(self):
        """Copy the connect link so the user can finish sign-in in another
        browser. Brief 'Copied!' feedback, then restore the label."""
        if not self._pairing_link:
            return
        from qgis.PyQt.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self._pairing_link)
        self._pairing_copy_btn.setText(tr("Copied!"))
        QTimer.singleShot(
            1400, lambda: self._pairing_copy_btn.setText(tr("Link not opening? Copy link"))
        )

    def _on_pairing_cancel_clicked(self):
        self.pairing_cancel_requested.emit(self._pending_pairing_code)
        self._pending_pairing_code = ""
        self.show_pairing_idle()

    def show_pairing_waiting(self):
        """Switch the onboarding into the 'waiting for browser' state."""
        self._pairing_active = True
        self._pairing_status.setText(tr("Waiting for you to sign in in your browser"))
        self._connect_section.setVisible(False)
        self._activation_message.setVisible(False)
        self._pairing_wait_section.setVisible(True)
        self._pairing_anim_timer.start()

    def show_pairing_browser_seen(self):
        """The server saw the browser reach /connect: reassure the user."""
        if self._pairing_active:
            self._pairing_status.setText(
                tr("Browser page open. Finish signing in to connect."))

    def show_pairing_stalled_hint(self):
        """Long wait and the browser was never seen server-side: surface the
        recovery paths instead of an endless spinner."""
        if self._pairing_active:
            self._pairing_status.setText(tr(
                "Still waiting. If the page did not open or shows an error, "
                "click Open again or copy the link into another browser."))

    def _stop_pairing_wait(self):
        """Hide the waiting section and stop its animation timer."""
        self._pairing_active = False
        self._pairing_anim_timer.stop()
        self._pairing_wait_section.setVisible(False)

    def show_pairing_idle(self):
        """Return to the idle onboarding (Connect button visible)."""
        self._stop_pairing_wait()
        self._connect_section.setVisible(True)

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

    def _show_subscribe_banner(self, message: str) -> None:
        """Show a 12 s warning banner with a Subscribe link appended.

        Shared by the free-tier resolution gate and the reference-image gate so
        the upsell copy/styling stays in one place.
        """
        subscribe_url = get_subscribe_url()
        link_style = f"color: {BRAND_BLUE}; font-weight: bold;"
        link = f'<a href="{subscribe_url}" style="{link_style}">{tr("Subscribe")}</a>'
        self._show_status_box(f"{message} {link}", "warning")
        # 12 s banner; parented timer dies with the dock.
        if self._status_hide_timer is not None:
            self._status_hide_timer.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._hide_status_box)
        timer.start(12000)
        self._status_hide_timer = timer

    def _show_reference_upsell(self) -> None:
        """Free-tier user tried to add a second reference image: nudge to
        subscribe instead of adding it."""
        self._show_subscribe_banner(
            tr("Free plan is limited to {n} reference image.").format(
                n=FREE_TIER_MAX_REFERENCES
            )
        )

    def _on_resolution_selected(self, label: str):
        """Handle a click inside the resolution dropdown of either container."""
        if self._is_free_tier and label != "1K":
            self._show_subscribe_banner(
                tr("{} outputs are unlocked with a subscription.").format(label)
            )
            return

        # Clear any existing status message if switching resolutions
        self._hide_status_box()

        self._selected_resolution = label
        self._refresh_resolution_triggers()
        self._update_generate_button_text()

    def _update_generate_button_text(self):
        """Keep the first Generate label stable. The result-state button reflects
        which version the next edit builds on (see _update_result_generate_label).
        """
        self._generate_btn.setText(tr("Generate"))
        self._generate_btn.setToolTip(tr("Run the AI edit on your selected zone"))
        self._update_result_generate_label()

    def _update_result_generate_label(self):
        """Result button + prompt placeholder both name the selected base, so the
        user sees that what they type generates FROM the selected version
        ('Generate from Original' / 'Generate from V2')."""
        base = self._version_strip.label_for(self._version_strip.selected_index())
        self._result_regenerate_btn.setText(tr("Generate from {base}").format(base=base))
        self._result_prompt_input.setPlaceholderText(
            tr("Type a prompt to edit {base}…").format(base=base)
        )

    def _on_version_selected(self, index: int):
        """A version tile was clicked: tell the plugin (canvas sync) and update
        the result button label + prompt placeholder. Never touches the text."""
        self.base_version_selected.emit(index)
        self._update_result_generate_label()

    def set_resolution_credit_costs(self, costs: dict[str, int]):
        """Update per-resolution credit costs (server config). Costs are
        displayed inside the resolution dropdown via the prompt containers."""
        if costs:
            self._resolution_credit_costs = costs
        self._refresh_resolution_triggers()

    def get_selected_resolution(self) -> str:
        """Return the user-selected resolution label."""
        return self._selected_resolution

    def get_base_version_index(self) -> int:
        """Strip index the next edit builds on (0 = Original)."""
        return self._version_strip.selected_index()

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
            self._update_result_generate_enabled()
            self._adjust_result_prompt_height()
        else:
            self._prompt_input.blockSignals(True)
            self._prompt_input.setPlainText(format_template_prompt(preset["prompt"]))
            self._prompt_input.blockSignals(False)
            self._prompt_input.moveCursor(QtC.CursorEnd)
            self._prompt_input.setFocus()
            self._update_generate_enabled()
            self._adjust_prompt_height()

    def _enter_iteration_state(self) -> None:
        """Show the RESULT/iterate UI (prompt + version strip above the Generate
        row) without the post-generation 'Saved as' line.

        Restoring a past generation means 'resume iterating on this image', so it
        lands in the same layout a fresh result does. This keeps the version
        strip in its proper home above the action row instead of falling below
        it (the old restore path used the in-flight 'generating' slot, which sits
        under the Generate/Exit row in the prompt state)."""
        self._stop_progress_animation()
        self._progress_widget.setVisible(False)
        self._hide_status_box()
        self._vectorize_cta_section.setVisible(False)
        self._vectorize_cta_pending = None
        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        self._exit_btn.setVisible(False)
        self._consent_widget.setVisible(False)
        self._prompt_container.set_readonly(False)
        self._result_section.setVisible(True)
        self._result_prompt_widget.setVisible(True)
        self._result_prompt_container.set_readonly(False)
        self._place_version_strip("result")
        self._version_strip.set_readonly(False)
        self._place_reference_widget("result")
        # Nothing was just saved on restore: keep the success line hidden.
        self._layer_saved_label.setVisible(False)
        self._refresh_resolution_triggers()
        # Reconcile the upgrade CTA like set_generation_complete, so the two
        # RESULT-entry paths can't leave a stale CTA on the iterate screen.
        self._set_upgrade_cta_wanted(self._is_free_tier and self._activated)

    def restore_generation_context(
        self, prompt_text: str, template_id=None, template_name=None
    ) -> None:
        """Reproduce a past generation: enter the iterate state and fill its
        prompt. The plugin has already restored the zone."""
        self._active_template_id = str(template_id or "") or None
        self._active_template_name = str(template_name or "") or None
        self._enter_iteration_state()
        self._result_prompt_input.blockSignals(True)
        self._result_prompt_input.setPlainText(format_template_prompt(prompt_text or ""))
        self._result_prompt_input.blockSignals(False)
        self._result_prompt_input.moveCursor(QtC.CursorEnd)
        self._update_result_generate_enabled()
        self._adjust_result_prompt_height()

    def clear_references(self) -> None:
        """Drop every reference image (store + strip). Used when reusing a past
        generation so its references replace, not stack onto, the current ones."""
        if self._reference_widget is not None:
            self._reference_widget.clear()

    def restore_reference_images(self, items: list) -> None:
        """Inject reloaded reference images (QImage, name) into the strip."""
        if self._reference_widget is not None:
            self._reference_widget.add_qimages(items)

    def set_markup_reference(self, image) -> None:
        """Show the rendered zone+marks as the (single) Mark up reference in the
        strip. Replaces any previous one."""
        if self._reference_widget is not None:
            self._reference_widget.set_markup_image(image)

    def clear_markup_reference(self) -> None:
        """Drop the Mark up reference (e.g. strokes cleared)."""
        if self._reference_widget is not None:
            self._reference_widget.clear_markup_image()

    def _on_prompt_changed(self):
        self._enforce_prompt_max_length(self._prompt_input)
        self._update_generate_enabled()
        self._clear_active_template_if_empty()
        self._update_prompt_guidance_hint()

    def _guidance_message_for(self, text: str) -> str | None:
        """Map an off-rails prompt to its soft hint, or None to stay silent.
        Shared by the first-run prompt and the result/retry prompt."""
        kind = detect_prompt_guidance(
            text, has_template=bool(self._active_template_id)
        )
        if kind == "vector_file":
            return tr(
                "AI Edit outputs an image, not a vector file. For polygons "
                "(SHP, GeoJSON), pick a Segment or Land cover template, then "
                "‘Vectorize this result’."
            )
        if kind == "measure":
            return tr(
                "AI Edit can't measure or count. Pick a Segment template, then "
                "‘Vectorize this result’: QGIS gives the area and count per "
                "polygon."
            )
        if kind == "qa":
            return tr(
                "AI Edit edits the image, it doesn't answer questions or count. "
                "Describe a visual change, e.g. colour the buildings red."
            )
        return None

    @staticmethod
    def _apply_guidance_hint(label, msg: str | None) -> None:
        if not msg:
            label.setVisible(False)
            return
        # Glyph kept outside tr() so translators see clean text.
        label.setText("ⓘ  " + msg)
        label.setVisible(True)

    def _update_prompt_guidance_hint(self) -> None:
        """Live off-rails hint under the first-run prompt. Non-blocking."""
        self._apply_guidance_hint(
            self._prompt_guidance_hint, self._guidance_message_for(self.get_prompt())
        )

    def _update_result_guidance_hint(self) -> None:
        """Same hint under the result/retry prompt, so iterating on a v1/v2
        gets the same guidance. Non-blocking."""
        text = self._result_prompt_input.toPlainText().strip()
        self._apply_guidance_hint(
            self._result_guidance_hint, self._guidance_message_for(text)
        )

    def set_zone_guidance(self, ground_resolution_m: float | None) -> None:
        """Soft, non-blocking heads-up when the drawn zone is so zoomed out the
        model can't resolve small features. Called by the plugin on zone
        selection. Threshold ~10 m/px: where the failure rate climbs sharply."""
        coarse = ground_resolution_m is not None and ground_resolution_m >= 10.0
        if not coarse:
            self._zone_guidance_hint.setVisible(False)
            return
        msg = tr(
            "Zoomed out: the AI won't see small features (buildings, cars, "
            "trees) at this scale. Zoom in for object-level detail."
        )
        self._zone_guidance_hint.setText("ⓘ  " + msg)
        self._zone_guidance_hint.setVisible(True)

    def _on_result_prompt_changed(self):
        self._enforce_prompt_max_length(self._result_prompt_input)
        self._update_result_generate_enabled()
        self._clear_active_template_if_empty()
        self._update_result_guidance_hint()

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
        Prompt Library 'Segment' tab (Unicode ▣). Painted in the palette text
        colour (like the pencil chip) so it stays legible on dark themes and
        Windows instead of rendering as an invisible black-on-black square.
        """
        size = 40  # 2x for crisp rendering at 20px
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ink = self.palette().color(QPalette.ColorRole.WindowText)
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

    def _make_swipe_glyph_icon(self) -> QIcon:
        """Footer Before/after glyph - swipe.svg tinted to the palette text
        colour so it carries the same weight as the gear / help glyphs rather
        than looking dim and half-transparent on a dark theme.
        """
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        return _tinted_svg_icon("swipe.svg", ink)

    def _make_gear_glyph_icon(self) -> QIcon:
        """Footer Settings glyph - a vector gear painted in the palette text
        colour. Replaces the U+2699 GEAR character, which Windows renders as a
        colour emoji (Segoe UI Emoji) while macOS shows a flat black glyph; the
        painted version is identical on both platforms and crisp at any DPI.
        """
        import math

        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtGui import QPainter, QPainterPath

        s = 20
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        pm = make_hidpi_pixmap(s)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        cx = cy = s / 2.0
        teeth = 8
        step = 2.0 * math.pi / teeth
        r_tip = s * 0.46
        r_root = s * 0.34
        half_tip = step * 0.18
        half_root = step * 0.30
        path = QPainterPath()
        for i in range(teeth):
            a = i * step
            corners = (
                (a - half_root, r_root),
                (a - half_tip, r_tip),
                (a + half_tip, r_tip),
                (a + half_root, r_root),
            )
            for ang, r in corners:
                pt = QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang))
                if i == 0 and ang == corners[0][0]:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
        path.closeSubpath()
        # Center hole: OddEven fill subtracts it from the gear body.
        path.addEllipse(QPointF(cx, cy), s * 0.15, s * 0.15)
        path.setFillRule(Qt.FillRule.OddEvenFill)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        p.drawPath(path)
        p.end()
        return QIcon(pm)

    # Public API consumed by the plugin layer ---------------------------

    def set_markup_state(self) -> None:
        """Swap the dock view to the Mark up panel."""
        self._stop_progress_animation()
        self._hide_status_box()
        self._vectorize_panel.deactivate()
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
        self._vectorize_btn.set_active(True)
        # Swipe and Vectorize fight for the canvas; lock Swipe while the
        # Vectorize panel is open.
        self._swipe_panel_lock = True
        self._refresh_swipe_enabled()

    def exit_tool_panel(self) -> None:
        """Hide whichever tool panel is showing and restore _main_widget."""
        self._vectorize_panel.deactivate()
        self._markup_panel.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._main_widget.setVisible(True)
        self._vectorize_btn.set_active(False)
        self._swipe_panel_lock = False
        self._refresh_swipe_enabled()

    def set_swipe_button_checked(self, checked: bool) -> None:
        """Sync the Before/After button visual to the controller state.

        Called by the plugin when the swipe is armed or disarmed by
        anything other than a direct button click (Esc on the canvas,
        layer removal, plugin shutdown). Blocks the toggled signal so we
        don't recurse into the controller.
        """
        if self._swipe_btn.isChecked() == checked:
            return
        self._swipe_btn.blockSignals(True)
        try:
            self._swipe_btn.setChecked(checked)
        finally:
            self._swipe_btn.blockSignals(False)

    def set_swipe_button_enabled(self, can_swipe: bool) -> None:
        """Gate the Before/After button on whether a swipeable layer is
        currently the active layer in the QGIS Layers panel. Stays
        enabled while the swipe is on so the user can always click to
        turn it off. Forced off while the Vectorize panel is open
        (mutually exclusive tools).
        """
        self._swipe_eligible = can_swipe
        self._refresh_swipe_enabled()

    def _refresh_swipe_enabled(self) -> None:
        is_checked = self._swipe_btn.isChecked()
        enabled = (self._swipe_eligible or is_checked) and not self._swipe_panel_lock
        self._swipe_btn.setEnabled(enabled)

    def set_vectorize_button_active(self, active: bool) -> None:
        """Light the green tint on the Vectorize footer icon while its
        panel is open. Same visual language as the swipe button so the
        user always knows which AI Edit action owns the canvas.
        """
        self._vectorize_btn.set_active(active)

    def set_settings_button_active(self, active: bool) -> None:
        """Light the green tint on the Settings (gear) footer icon while
        the Account Settings dialog is open.
        """
        self._settings_btn.set_active(active)

    def _set_swipe_button_visible(self, visible: bool) -> None:
        """Show or hide the Before/after footer button.

        Mirrors the Vectorize visibility rule: revealed whenever the dock is
        activated, hidden otherwise. The button operates on whichever AI-Edit
        raster the user has active in the QGIS Layers panel, not just on a
        fresh generation - per-click eligibility (greyed-out vs clickable)
        is driven separately by set_swipe_button_enabled.

        The ``and self._activated`` guard is a safety net: it keeps the button
        hidden if a caller fires this before set_activated has run.
        """
        self._swipe_btn.setVisible(visible and self._activated)

    def set_markup_annotation_count(self, count: int) -> None:
        self._markup_panel.set_annotation_count(count)

    def set_markup_zone_present(self, has_zone: bool) -> None:
        self._markup_panel.set_zone_present(has_zone)

    def get_markup_color(self) -> QColor:
        return self._markup_panel.get_color()

    def set_vectorize_suggestion(
        self,
        layer_id: str | None,
        color_hex: str | None,
        class_label: str = "",
    ) -> None:
        """Inject (or clear) the post-generation Vectorize CTA.

        Called by the plugin orchestrator after a successful generation
        when the template carried a vector_color in the catalog. Hidden
        the moment the user navigates away from the result section.
        ``class_label`` (when known) flows down to the vectorize panel
        so the produced polygons land with a sensible class_name value.
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
        self._vectorize_cta_btn.setText(tr("Vectorize this result") + " →")
        self._vectorize_cta_pending = (layer_id, normalised, class_label or "")
        self._vectorize_cta_section.setVisible(True)

    def _on_vectorize_cta_clicked(self) -> None:
        if self._vectorize_cta_pending is None:
            return
        layer_id, color_hex, class_label = self._vectorize_cta_pending
        self.vectorize_suggestion_clicked.emit(layer_id, color_hex, class_label)

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

    def _on_report_problem(self, _link=None):
        """User-initiated report: copy the session logs and email support."""
        show_error_report(self._main_window_for_dialog())

    def arm_report_context(self, request_id: str = "") -> None:
        """Stash the request id for the next inline 'Report a problem' link so the
        emailed log carries the server correlation key."""
        self._pending_report_request_id = request_id or ""

    def _on_status_link(self, href: str) -> None:
        """Route a clicked link in the status box: the report sentinel opens the
        in-app log dialog; any real URL opens in the browser."""
        if href == REPORT_PROBLEM_HREF:
            show_error_report(
                self._main_window_for_dialog(),
                request_id=getattr(self, "_pending_report_request_id", "") or "",
            )
            return
        QDesktopServices.openUrl(QUrl(href))

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
        swipe_key = native("Alt+B")
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
            f"<tr><td>{k.format(swipe_key)}</td><td>{tr('Before / after')}</td></tr>"
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
            from ..core import telemetry_events as te
            telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "limit_cta"})
            QDesktopServices.openUrl(QUrl(self._limit_cta_url))

    def _on_activation_limit_cta_clicked(self):
        if self._activation_limit_cta_url:
            from ..core import telemetry
            from ..core import telemetry_events as te
            telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "activation_limit_cta"})
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

    def _update_result_generate_enabled(self):
        """Gate the result-section Generate button on a non-empty prompt.

        The retry field now starts blank after each generation, so the button
        would otherwise sit clickable but silently no-op. Greying it out tells
        the user to type a fresh instruction first.
        """
        enabled = bool(self._result_prompt_input.toPlainText().strip())
        self._result_regenerate_btn.setEnabled(enabled)
        self._result_regenerate_btn.setStyleSheet(_BTN_GREEN if enabled else _BTN_DISABLED)

    def _on_escape_pressed(self):
        """Escape walks the flow back one step at a time.

        SWIPE ACTIVE → disarm swipe (highest priority — the canvas-tool
        Escape handler only fires when canvas has focus, but the swipe
        button stays checked otherwise; route the dock-level Escape
        through here so swipe always exits cleanly).
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
        # Swipe takes priority: clicking the (already-checked) button
        # toggles it off, which routes through swipe_toggled → plugin →
        # swipe_controller.stop().
        if self._swipe_btn.isChecked():
            self._swipe_btn.click()
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
        from .panels.swipe_panel import _SwipeMapTool
        from .tools.markup_tools import _MarkupBaseMapTool
        from .tools.selection_map_tool import RectangleSelectionTool
        return isinstance(
            tool, (RectangleSelectionTool, _MarkupBaseMapTool, _SwipeMapTool)
        )

    def closeEvent(self, event):
        """Visibility-only teardown. Persistent disconnects live in cleanup()."""
        self._stop_progress_animation()
        if self._progress_widget.isVisible():
            self.stop_clicked.emit()
        self._vectorize_panel.deactivate()
        super().closeEvent(event)

    def cleanup(self):
        """Called once from plugin.unload() before the dock is removed."""
        try:
            QgsProject.instance().layersAdded.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layersRemoved.disconnect(self._schedule_layer_warning_update)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._update_layer_warning
            )
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().readProject.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        try:
            QgsProject.instance().cleared.disconnect(self._on_project_loaded)
        except (TypeError, RuntimeError):
            pass
        # LayerTreeComboBox hooks its own QgsProject signals; nothing else cleans it.
        try:
            combo = getattr(self._vectorize_panel, "_layer_combo", None)
            if combo is not None and hasattr(combo, "cleanup"):
                combo.cleanup()
        except Exception:  # nosec B110
            pass
