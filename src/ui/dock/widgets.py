from __future__ import annotations

import os
import tempfile

from qgis.PyQt.QtCore import QRectF, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPalette,
    QPen,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.prompts.hex_highlight import HEX_RX, contrast_text_for, expand_hex
from ..icons import icon_for, pixmap_for
from ..input_decisions import (
    PASTE_FILES,
    PASTE_IMAGE,
    PASTE_TEXT,
    paste_kind_for_mime,
)
from . import design_tokens as tokens
from .mime import _file_paths_from_mime
from .resolution_visuals import resolution_note, resolution_tile, resolution_visual

# The chevron a select chip draws into its own right padding, and the air
# between it and the chip's edge. AI Agent's select buttons at the same sizes.
CHIP_CHEVRON_PX = 12
CHIP_CHEVRON_INSET = 6


def set_link_ink(label: QLabel) -> None:
    """Paint a rich-text label's links in the link ink.

    A QLabel colours an ``<a>`` from the palette's Link role, which is Qt's
    stock dark blue: 2.4:1 on the dark theme's surfaces. The label's QSS
    cannot reach it, so the palette carries the token instead."""
    palette = label.palette()
    for role in (QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited):
        palette.setColor(role, tokens.qcolor(tokens.LINK_INK))
    label.setPalette(palette)


class _GlyphNote(QWidget):
    """A tinted note with its glyph on the left, the status box's shape for
    a message that is not a status (the zone heads-up). Keeps QLabel's
    setText/text so callers treat it as the label it replaced."""

    def __init__(self, glyph: str, tint: str, ink: str, parent=None, line: str = ""):
        super().__init__(parent)
        self.setObjectName("glyphNote")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#glyphNote {{ background: {tint}; border: 1px solid {line or tokens.LINE};"
            f" border-radius: {tokens.RADIUS_CARD}px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setFixedSize(16, 16)
        icon.setStyleSheet("background: transparent; border: none;")
        icon.setPixmap(pixmap_for(icon, glyph, 16, tokens.qcolor(ink)))
        layout.addWidget(icon, 0, QtC.AlignTop)
        self._label = QLabel("")
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            f"font-size: {tokens.FONT_BODY}px; color: {tokens.INK};"
            " background: transparent; border: none;"
        )
        layout.addWidget(self._label, 1)

    def setText(self, text: str) -> None:
        self._label.setText(text)

    def text(self) -> str:
        return self._label.text()


class _Spinner(QWidget):
    """A small rotating arc, the conventional 'busy' indicator, the same
    round-capped arc AI Agent and AI Segmentation turn. Driven by an external
    QTimer calling ``advance()`` so one timer can be paused with the section
    it belongs to."""

    def __init__(self, diameter: int = 16, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._d = diameter
        self.setFixedSize(diameter, diameter)

    def advance(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt signature
        # An exception raised here escapes into Qt's paint dispatch and can
        # take QGIS down, so the paint is guarded like AI Segmentation's.
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            margin = 2.0
            rect = QRectF(margin, margin, self._d - 2 * margin, self._d - 2 * margin)
            # The interaction blue: the leaf green is the brand mark's colour
            # only (ui.md).
            pen = QPen(tokens.qcolor(tokens.BRAND_BLUE))
            pen.setWidthF(2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(-self._angle * 16), 270 * 16)
            painter.end()
        except Exception:  # noqa: BLE001 - a paintEvent must never raise
            return


class _ZoneGestureGlyph(QWidget):
    """Vector 'outline an area' glyph: a dashed polygon with its clicked
    corners and the pointer on the last one. The zone is a polygon placed
    click by click; the glyph used to draw a dragged box, a gesture the tool
    does not take. Painted live in paintEvent so it stays crisp at any DPI.
    Blue, to echo the zone drawn on the canvas.
    """

    def __init__(self, color: QColor, size: int = 56, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):  # noqa: N802 - Qt signature
        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtGui import QPainter, QPen, QPolygonF
        s = float(self.width())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Dashed polygon (the zone being outlined), its last corner at the
        # lower right so the pointer can sit on it inside the widget.
        corners = [(0.14, 0.30), (0.40, 0.08), (0.62, 0.22), (0.60, 0.60), (0.22, 0.56)]
        outline = QPen(self._color)
        outline.setWidthF(s * 0.045)
        outline.setStyle(Qt.PenStyle.DashLine)
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(outline)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(x * s, y * s) for (x, y) in corners]))
        # A solid dot on each clicked corner.
        hs = s * 0.045
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        for x, y in corners:
            p.drawEllipse(QPointF(x * s, y * s), hs, hs)
        b = s * 0.60
        # Mouse cursor (arrow) pulling that corner: tip on the handle, classic
        # up-left pointer shape, blue fill with a white edge so it reads clearly.
        f = s * 0.020
        pts = [(0, 0), (0, 15), (3.5, 11.5), (6, 17), (8, 16), (5.5, 10.5), (10, 10)]
        cursor = QPolygonF([QPointF(b + x * f, b + y * f) for (x, y) in pts])
        edge = QPen(tokens.qcolor(tokens.SURFACE))
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
        self._chip_glyph: tuple | None = None

    def set_chip_glyph(
        self,
        name: str,
        size: int,
        color: str,
        hover_color: str | None = None,
    ) -> None:
        """Carry the glyph, re-inked under the pointer like AI Agent's ``+``.

        A quiet chip writes its word in the second ink and raises it to the
        full ink on hover; a glyph left at the resting ink then reads a shade
        paler than the word beside it. ``hover_color`` of None keeps one ink,
        which is what a glyph in a hue of its own wants.
        """
        self._chip_glyph = (name, int(size), color, hover_color)
        self._apply_chip_glyph()

    def _apply_chip_glyph(self) -> None:
        if self._chip_glyph is None:
            return
        name, size, color, hover_color = self._chip_glyph
        ink = hover_color if (hover_color and self.property("hover")) else color
        self.setIcon(icon_for(
            self, name, size, tokens.qcolor(ink),
            disabled_color=tokens.qcolor(tokens.INK_3),
        ))
        self.setIconSize(QSize(size, size))

    def set_hovered(self, hovered: bool) -> None:
        if bool(self.property("hover")) == hovered:
            return
        self.setProperty("hover", hovered)
        # Re-polish so the [hover="true"] selector takes effect.
        self.style().unpolish(self)
        self.style().polish(self)
        self._apply_chip_glyph()
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
    - Tab leaves the box (setTabChangesFocus). A prompt is a sentence, never
      indented text, and without this every control after the box in the tab
      order - the chips, the footer icons, Generate - is unreachable going
      forward from the prompt.
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
        f"QTextEdit {{ background: transparent; padding: 4px; color: {tokens.INK};"
        f" font-size: {tokens.FONT_BASE}px; selection-background-color: {tokens.ACCENT_BORDER_SOFT}; }}"
        # Some Qt styles still reserve a thin strip for the horizontal
        # scrollbar even with ScrollBarAlwaysOff; force its height to 0.
        "QTextEdit QScrollBar:horizontal { height: 0px; margin: 0px; }"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(False)
        # Tab moves on instead of inserting a tab character (see the class
        # docstring). Nothing in the prompt path reads a tab.
        self.setTabChangesFocus(True)
        # Remove the native frame - the surrounding _PromptContainer paints it.
        self.setFrameShape(QtC.FrameNoFrame)
        # Transparent background only; keep palette untouched so the QTextEdit
        # keeps its native caret (the accent-blue insertion bar on macOS).
        # Setting QPalette.Base ourselves silently disables the caret on some
        # Qt builds, hence the QSS-only path here.
        self.setStyleSheet(self._INNER_STYLE)
        # The placeholder in the third ink, like AI Agent's composer. The
        # PlaceholderText role leaves Base alone, so the native caret stays.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, tokens.qcolor(tokens.INK_3))
        self.setPalette(palette)
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
            # A locked box (a generation in flight) swallows Enter: the caret
            # can still sit in it, and a second submit would ask for a second
            # paid run on top of the first.
            if not self.isReadOnly():
                self.submitted.emit()
            return
        super().keyPressEvent(event)

    @staticmethod
    def _paste_kind(source, paths: list[str]) -> str:
        text = source.text() if source.hasText() else ""
        return paste_kind_for_mime(bool(paths), text, source.hasImage())

    def canInsertFromMimeData(self, source):  # noqa: N802
        paths = _file_paths_from_mime(source)
        if self._paste_kind(source, paths) in (PASTE_FILES, PASTE_IMAGE):
            return False
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):  # noqa: N802
        paths = _file_paths_from_mime(source)
        kind = self._paste_kind(source, paths)
        if kind == PASTE_FILES:
            self.images_pasted.emit(paths)
            return
        if kind == PASTE_IMAGE:
            self._emit_clipboard_image(source)
            return
        if kind == PASTE_TEXT:
            # Text copied from Excel or OneNote carries a bitmap too; paste
            # the words only.
            self.insertPlainText(source.text())
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
            except OSError:  # temp file already gone; the OS temp dir cleans up
                pass


class _ResolutionChipButton(_FooterIconButton):
    """The footer's Quality chip: the picked tier's glyph, its name, a
    chevron.

    AI Agent's select buttons draw the chevron themselves into the button's
    right padding (``PermissionChip.paintEvent``) instead of spending the
    button's one icon slot on it. Doing the same here frees that slot for the
    tier's own glyph, so the level is readable with the menu closed.
    """

    def paintEvent(self, event):  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            pixmap = pixmap_for(
                self, "chevron_down", CHIP_CHEVRON_PX, tokens.qcolor(tokens.INK_2))
            x = self.width() - CHIP_CHEVRON_PX - CHIP_CHEVRON_INSET
            y = (self.height() - CHIP_CHEVRON_PX) // 2
            painter.drawPixmap(x, y, pixmap)
        finally:
            painter.end()


class _ResolutionMenuItem(QWidget):
    """One quality row in the picker, AI Agent's popover row shape.

    Layout::

        [tile]  Detailed (2K)   [Pro]   30 credits  [check]
                Sharp, clean result for real maps

    The tile is the tier's own glyph in its own hue (``resolution_visuals``),
    the same pair the closed chip wears. The picked row is a soft tint of that
    hue with a thin border in its ink and a check, never a solid fill
    (``design_tokens.picked_qss`` is that recipe for a checkable button; a
    QWidgetAction row is not checkable, so it is spelled out here from the same
    two helpers).

    Locked rows (free-tier 2K / 4K) keep the muted ink and the "Pro" tag, and
    stay clickable: the click still fires so the dock can push Pro.

    QMenu does not paint its selection highlight under QWidgetAction items,
    so the hover background is drawn by the widget itself via a :hover
    stylesheet. Child labels are marked transparent-for-mouse so the
    parent receives all hover/click events even when the cursor is over
    a QLabel.
    """

    clicked = pyqtSignal()

    # Wide enough for the longest of the three notes at 11 px without wrapping
    # it into a ragged second line inside a popup nobody can resize.
    _ROW_MIN_WIDTH = 296
    _CHECK_PX = 14
    # The locked rows' "Pro" word, a small blue pill: the tier a click on the
    # row leads to.
    _PRO_TAG_STYLE = (
        f"QLabel {{ background: {tokens.ACCENT_TINT}; color: {tokens.LINK_INK};"
        f" border: none; border-radius: 8px; padding: 0 6px;"
        f" font-size: {tokens.FONT_MICRO}px; font-weight: 600; }}"
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
        _glyph, hue = resolution_visual(resolution)
        hue_ink = tokens.category_ink(hue)
        self.setObjectName("resolutionMenuItem")
        self.setAttribute(QtC.WA_StyledBackground, True)
        self.setStyleSheet(self._row_style(hue, hue_ink, selected))
        self.setCursor(QtC.PointingHandCursor)
        self.setMinimumWidth(self._ROW_MIN_WIDTH)
        if locked:
            # The same sentence the row's action carries (prompt_container),
            # naming the tiers by their Quality words, not a second wording.
            self.setToolTip(get_export_copy(
                "dock.prompt_container.quality_pro_tooltip",
                tr("Pro unlocks Detailed and Maximum, for printing and zooming in"),
            ))

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 7, 10, 7)
        row.setSpacing(10)
        row.addWidget(resolution_tile(self, resolution), 0, QtC.AlignTop)

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        # The tag, the credits and the check share the name's line, so the
        # muted note under them runs the full width of the row instead of
        # wrapping into a narrow column.
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        ink = tokens.INK_2 if locked else tokens.INK
        # "Large" reads as the row label; the exact resolution "(2K)" trails
        # in the third ink as the supporting detail.
        name = QLabel(
            f"{quality} <span style='color: {tokens.INK_3};'>({resolution})</span>",
            self,
        )
        name.setTextFormat(Qt.TextFormat.RichText)
        name.setStyleSheet(
            f"font-size: {tokens.FONT_BODY}px; font-weight: 500;"
            f" background: transparent; color: {ink};"
        )
        name.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        head.addWidget(name)
        head.addStretch(1)

        # A word, not just the muted tint: colour alone carries the locked state
        # to nobody who cannot see it, and a tooltip is out of reach by keyboard.
        # "Pro" is the tier name everywhere else: the account dialog prints it,
        # the plan wire value is "pro", and the website labels these same locked
        # 2K/4K rows "Pro". It stays outside tr(), like the other two, because
        # the site ships it untranslated in every locale.
        if locked:
            pro = QLabel("Pro", self)
            pro.setFixedHeight(16)
            pro.setStyleSheet(self._PRO_TAG_STYLE)
            pro.setAttribute(QtC.WA_TransparentForMouseEvents, True)
            head.addWidget(pro, 0, QtC.AlignVCenter)

        cost_text = (
            tr("{n} credit").format(n=credits)
            if credits == 1
            else tr("{n} credits").format(n=credits)
        )
        cost = QLabel(cost_text, self)
        cost.setStyleSheet(
            f"font-size: {tokens.FONT_HINT}px; background: transparent; color: {tokens.INK_3};"
        )
        cost.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        head.addWidget(cost, 0, QtC.AlignVCenter)

        check = QLabel(self)
        check.setFixedSize(self._CHECK_PX, self._CHECK_PX)
        check.setStyleSheet("background: transparent; border: none;")
        if selected and not locked:
            check.setPixmap(pixmap_for(check, "check", self._CHECK_PX, tokens.qcolor(hue_ink)))
        check.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        head.addWidget(check, 0, QtC.AlignVCenter)
        column.addLayout(head)

        note = QLabel(resolution_note(resolution), self)
        note.setStyleSheet(
            f"font-size: {tokens.FONT_HINT}px; background: transparent; color: {tokens.INK_2};"
        )
        note.setAttribute(QtC.WA_TransparentForMouseEvents, True)
        column.addWidget(note)
        row.addLayout(column, 1)

    @staticmethod
    def _row_style(hue: str, hue_ink: str, selected: bool) -> str:
        """The picked recipe spelled out: tint, a thin border in the hue's ink.

        The border is drawn on every row, transparent when the row is not the
        picked one, so picking does not shift the text by a pixel.
        """
        ground = tokens.category_tint(hue) if selected else "transparent"
        border = hue_ink if selected else "transparent"
        hover = tokens.category_tint(hue, strong=True) if selected else tokens.HOVER
        return (
            f"QWidget#resolutionMenuItem {{ background: {ground};"
            f" border: 1px solid {border}; border-radius: {tokens.RADIUS_CONTROL}px; }}"
            f"QWidget#resolutionMenuItem:hover {{ background: {hover}; }}"
        )

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == QtC.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
