from __future__ import annotations

import os
import tempfile

from qgis.PyQt.QtCore import QRectF, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QToolButton,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.i18n import tr
from ...core.prompts.hex_highlight import HEX_RX, contrast_text_for, expand_hex
from .mime import _file_paths_from_mime
from .style import BRAND_GREEN, DISABLED_TEXT


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
        cost_text = (
            tr("{n} credit").format(n=credits)
            if credits == 1
            else tr("{n} credits").format(n=credits)
        )
        cost = QLabel(cost_text, self)
        cost.setStyleSheet(f"font-size: 11px; background: transparent; {cost_color}")
        cost.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        row.addWidget(cost)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == QtC.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
