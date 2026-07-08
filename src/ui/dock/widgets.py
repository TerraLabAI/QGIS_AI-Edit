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



CHIP_CHEVRON_PX = 12
CHIP_CHEVRON_INSET = 6


def set_link_ink(label: QLabel) -> None:





    palette = label.palette()
    for role in (QPalette.ColorRole.Link, QPalette.ColorRole.LinkVisited):
        palette.setColor(role, tokens.qcolor(tokens.LINK_INK))
    label.setPalette(palette)


class _GlyphNote(QWidget):




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





    def __init__(self, diameter: int = 16, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._d = diameter
        self.setFixedSize(diameter, diameter)

    def advance(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):  # noqa: N802


        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            margin = 2.0
            rect = QRectF(margin, margin, self._d - 2 * margin, self._d - 2 * margin)


            pen = QPen(tokens.qcolor(tokens.BRAND_BLUE))
            pen.setWidthF(2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(-self._angle * 16), 270 * 16)
            painter.end()
        except Exception:  # noqa: BLE001
            return


class _ZoneGestureGlyph(QWidget):







    def __init__(self, color: QColor, size: int = 56, parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):  # noqa: N802
        from qgis.PyQt.QtCore import QPointF, Qt
        from qgis.PyQt.QtGui import QPainter, QPen, QPolygonF
        s = float(self.width())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)


        corners = [(0.14, 0.30), (0.40, 0.08), (0.62, 0.22), (0.60, 0.60), (0.22, 0.56)]
        outline = QPen(self._color)
        outline.setWidthF(s * 0.045)
        outline.setStyle(Qt.PenStyle.DashLine)
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(outline)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([QPointF(x * s, y * s) for (x, y) in corners]))

        hs = s * 0.045
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self._color))
        for x, y in corners:
            p.drawEllipse(QPointF(x * s, y * s), hs, hs)
        b = s * 0.60


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

        self.style().unpolish(self)
        self.style().polish(self)
        self._apply_chip_glyph()
        self.update()

    def set_active(self, active: bool) -> None:




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




    def highlightBlock(self, text: str) -> None:  # noqa: N802
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


















    submitted = pyqtSignal()
    images_pasted = pyqtSignal(list)

    _INNER_STYLE = (
        f"QTextEdit {{ background: transparent; padding: 4px; color: {tokens.INK};"
        f" font-size: {tokens.FONT_BASE}px; selection-background-color: {tokens.ACCENT_BORDER_SOFT}; }}"


        "QTextEdit QScrollBar:horizontal { height: 0px; margin: 0px; }"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(False)


        self.setTabChangesFocus(True)

        self.setFrameShape(QtC.FrameNoFrame)




        self.setStyleSheet(self._INNER_STYLE)


        palette = self.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, tokens.qcolor(tokens.INK_3))
        self.setPalette(palette)



        self.setLineWrapMode(QtC.LineWrapWidgetWidth)
        self.setWordWrapMode(QtC.WrapAtWordBoundaryOrAnywhere)


        self._hex_highlighter = _PromptHighlighter(self.document())
        self.setHorizontalScrollBarPolicy(QtC.ScrollBarAlwaysOff)



        self.setAcceptRichText(False)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (QtC.Key_Return, QtC.Key_Enter) and not event.modifiers() & QtC.ShiftModifier:



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


            self.insertPlainText(source.text())
            return
        super().insertFromMimeData(source)

    def _emit_clipboard_image(self, source) -> None:

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


            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class _ResolutionChipButton(_FooterIconButton):









    def paintEvent(self, event):  # noqa: N802
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
























    clicked = pyqtSignal()



    _ROW_MIN_WIDTH = 296
    _CHECK_PX = 14


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




        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        ink = tokens.INK_2 if locked else tokens.INK


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
