






from __future__ import annotations

from qgis.PyQt.QtCore import QT_VERSION, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from qgis.PyQt.QtWidgets import QWidget

from ..core import qt_compat as QtC
from ..core.config_store import get_export_copy, get_export_dial, get_export_dial_ratio
from ..core.i18n import tr
from .dock.design_tokens import (
    ACCENT_BORDER,
    BRAND_BLUE,
    FIELD,
    FONT_HINT,
    INK,
    INK_3,
    RADIUS_CARD,
    RADIUS_CHIP,
    qcolor,
)

QT6 = QT_VERSION >= 0x060000


_AUTO_LOOP_PERIOD_MS = 5800


_FRAME_INTERVAL_MS = 33


_KEY_STEP = 0.02
_KEY_STEP_LARGE = 0.10
_FOCUS_RING_PX = 2





_DIVIDER_COLOR = QColor(Qt.GlobalColor.white)
_HANDLE_FILL = qcolor(BRAND_BLUE)
_HANDLE_RADIUS_PX = 14
_HANDLE_RING_PX = 2
_DIVIDER_LINE_PX = 2

_BADGE_BG = qcolor(FIELD)
_BADGE_TEXT = qcolor(INK)
_BADGE_H_PX = 20
_BADGE_PAD_PX = 8
_BADGE_INSET_PX = 8
_PLACEHOLDER_BG = qcolor(FIELD)
_PLACEHOLDER_TEXT = qcolor(INK_3)
_FOCUS_RING = qcolor(ACCENT_BORDER)


_KEYBOARD_FOCUS_REASONS = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)


def _ease_in_out(t: float) -> float:

    if t < 0.5:
        return 4 * t * t * t
    p = -2 * t + 2
    return 1 - p * p * p / 2


class BeforeAfterSlider(QWidget):






    clicked = pyqtSignal()

    def __init__(
        self,
        parent: QWidget | None = None,
        auto_loop: bool = False,
        show_badges: bool = True,
        example_badge: str | None = None,
        handle_grab_only: bool = False,
        square_bottom: bool = False,
    ):
        super().__init__(parent)



        self._square_bottom = square_bottom
        self._show_badges = show_badges



        self._handle_grab_only = handle_grab_only


        self._example_badge = example_badge or None


        self._placeholder_text = get_export_copy(
            "widgets.before_after_slider.loading_placeholder", tr("Loading..."))
        self.setMinimumHeight(140)
        self.setMouseTracking(False)


        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(
            get_export_copy("widgets.before_after_slider.accessible_name", tr("Before and after comparison")))
        self._before: QPixmap | None = None
        self._after: QPixmap | None = None

        self._pos = 0.5
        self._sync_accessible_description()
        self._dragging = False
        self._hovering = False
        self._elapsed_ms = 0



        self._press_x: float | None = None
        self._moved_far = False





        self._auto_loop = auto_loop
        self._timer = QTimer(self)
        self._timer.setInterval(
            get_export_dial("widgets.before_after_slider.frame_interval_ms", _FRAME_INTERVAL_MS))
        self._timer.timeout.connect(self._on_tick)






    def showEvent(self, ev):  # noqa: N802
        if self._auto_loop and not self._timer.isActive():
            self._timer.start()
        super().showEvent(ev)

    def hideEvent(self, ev):  # noqa: N802
        self._timer.stop()
        super().hideEvent(ev)

    def closeEvent(self, ev):  # noqa: N802
        self._timer.stop()
        super().closeEvent(ev)

    def deleteLater(self):
        self._timer.stop()
        super().deleteLater()



    def set_before(self, pixmap: QPixmap | None) -> None:
        self._before = pixmap if pixmap and not pixmap.isNull() else None
        self.update()

    def set_after(self, pixmap: QPixmap | None) -> None:
        self._after = pixmap if pixmap and not pixmap.isNull() else None
        self.update()

    def has_images(self) -> bool:
        return self._before is not None and self._after is not None

    def set_placeholder_text(self, text: str) -> None:

        self._placeholder_text = text or ""
        if self._before is None and self._after is None:
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(280, 160)



    def _on_tick(self) -> None:
        if not self._auto_loop or self._hovering or self._dragging:
            return
        self._elapsed_ms = (self._elapsed_ms + _FRAME_INTERVAL_MS) % _AUTO_LOOP_PERIOD_MS

        half = _AUTO_LOOP_PERIOD_MS / 2
        t = self._elapsed_ms / half
        if t > 1.0:
            t = 2.0 - t
        self._pos = _ease_in_out(t)
        self.update()



    def keyPressEvent(self, ev):  # noqa: N802
        key = ev.key()
        step = (
            get_export_dial_ratio("widgets.before_after_slider.key_step_large", _KEY_STEP_LARGE)
            if ev.modifiers() & QtC.ShiftModifier
            else get_export_dial_ratio("widgets.before_after_slider.key_step", _KEY_STEP)
        )
        if key == Qt.Key.Key_Left:
            self._set_pos(self._pos - step)
        elif key == Qt.Key.Key_Right:
            self._set_pos(self._pos + step)
        elif key == Qt.Key.Key_Home:
            self._set_pos(0.0)
        elif key == Qt.Key.Key_End:
            self._set_pos(1.0)
        else:


            ev.ignore()
            return

        if not getattr(self, "_keyboard_focus", False):
            self._keyboard_focus = True
            self.update()
        ev.accept()

    def focusInEvent(self, ev):  # noqa: N802


        self._keyboard_focus = ev.reason() in _KEYBOARD_FOCUS_REASONS
        self.update()
        super().focusInEvent(ev)

    def focusOutEvent(self, ev):  # noqa: N802
        self.update()
        super().focusOutEvent(ev)



    def enterEvent(self, ev):  # noqa: N802
        self._hovering = True
        self.setMouseTracking(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev):  # noqa: N802




        self._hovering = False
        if not self._dragging:
            self.setMouseTracking(False)
        super().leaveEvent(ev)



    _CLICK_DRAG_THRESHOLD_PX = 5


    _HANDLE_GRAB_PX = 16

    def mousePressEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_x = self._event_x(ev)
            self._moved_far = False
            if self._handle_grab_only:
                divider_x = self._pos * max(1, self.width())
                self._dragging = abs(self._press_x - divider_x) <= self._HANDLE_GRAB_PX
            else:
                self._dragging = True
            if self._dragging:
                self._update_pos_from_event(ev)
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            was_pressed = self._press_x is not None
            was_dragging = self._dragging
            moved_far = self._moved_far
            self._dragging = False
            self._moved_far = False
            self._press_x = None


            if not self._hovering:
                self.setMouseTracking(False)



            if was_pressed and not moved_far and not (self._handle_grab_only and was_dragging):
                self.clicked.emit()
        super().mouseReleaseEvent(ev)

    def mouseMoveEvent(self, ev):  # noqa: N802
        if self._press_x is not None and not self._moved_far:
            if abs(self._event_x(ev) - self._press_x) > self._CLICK_DRAG_THRESHOLD_PX:
                self._moved_far = True
        if self._dragging:
            self._update_pos_from_event(ev)
        super().mouseMoveEvent(ev)

    @staticmethod
    def _event_x(ev) -> float:


        return QtC.event_pos(ev).x()

    def _update_pos_from_event(self, ev) -> None:
        x = QtC.event_pos(ev).x()
        self._set_pos(x / max(1, self.width()))

    def _set_pos(self, value: float) -> None:


        self._pos = max(0.0, min(1.0, value))
        self._sync_accessible_description()
        self.update()

    def _sync_accessible_description(self) -> None:
        self.setAccessibleDescription(
            tr("Divider at {pct}%. Left and right arrows move it.").format(
                pct=int(round(self._pos * 100))
            )
        )



    def paintEvent(self, ev):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = self.rect()
        radius = float(RADIUS_CARD)


        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        if self._square_bottom:
            lower = QPainterPath()
            lower.addRect(QRectF(rect).adjusted(0, rect.height() / 2.0, 0, 0))
            path = path.united(lower)
        painter.setClipPath(path)


        if self._before is None and self._after is None:
            self._paint_placeholder(painter, rect)
            self._draw_focus_ring(painter, rect, radius)
            painter.end()
            return


        split_x = int(rect.width() * self._pos)


        if self._before is not None:
            painter.save()
            painter.setClipRect(QRectF(0, 0, split_x, rect.height()))
            self._draw_pixmap_cover(painter, self._before, rect)
            painter.restore()
        else:
            painter.save()
            painter.setClipRect(QRectF(0, 0, split_x, rect.height()))
            painter.fillRect(rect, _PLACEHOLDER_BG)
            painter.restore()


        if self._after is not None:
            painter.save()
            painter.setClipRect(QRectF(split_x, 0, rect.width() - split_x, rect.height()))
            self._draw_pixmap_cover(painter, self._after, rect)
            painter.restore()
        else:
            painter.save()
            painter.setClipRect(QRectF(split_x, 0, rect.width() - split_x, rect.height()))
            painter.fillRect(rect, _PLACEHOLDER_BG)
            painter.restore()


        pen = QPen(_DIVIDER_COLOR)
        pen.setWidth(_DIVIDER_LINE_PX)
        painter.setPen(pen)
        painter.drawLine(split_x, 0, split_x, rect.height())


        handle_y = rect.height() // 2
        ring = QPen(_DIVIDER_COLOR)
        ring.setWidth(_HANDLE_RING_PX)
        painter.setPen(ring)
        painter.setBrush(QBrush(_HANDLE_FILL))
        painter.drawEllipse(
            QPointF(split_x, handle_y),
            _HANDLE_RADIUS_PX,
            _HANDLE_RADIUS_PX,
        )

        arrows = QPen(_DIVIDER_COLOR, 2)
        arrows.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(arrows)
        ay = handle_y
        painter.drawLine(split_x - 6, ay, split_x - 2, ay - 4)
        painter.drawLine(split_x - 6, ay, split_x - 2, ay + 4)
        painter.drawLine(split_x + 6, ay, split_x + 2, ay - 4)
        painter.drawLine(split_x + 6, ay, split_x + 2, ay + 4)


        if self._show_badges:
            before = get_export_copy("widgets.before_after_slider.before_badge", tr("Before"))
            after = get_export_copy("widgets.before_after_slider.after_badge", tr("After"))
            self._draw_badge(painter, before, x=_BADGE_INSET_PX, y=_BADGE_INSET_PX)
            self._draw_badge(
                painter, after, x=rect.width() - _BADGE_INSET_PX, y=_BADGE_INSET_PX, align_right=True
            )

        if self._example_badge:
            self._draw_example_badge(painter, rect, self._example_badge)

        self._draw_focus_ring(painter, rect, radius)
        painter.end()

    def _draw_focus_ring(self, painter: QPainter, rect, radius: float) -> None:





        if not self.hasFocus() or not getattr(self, "_keyboard_focus", False):
            return
        pen = QPen(_FOCUS_RING)
        pen.setWidth(_FOCUS_RING_PX)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)


        inset = _FOCUS_RING_PX / 2.0
        painter.drawRoundedRect(
            QRectF(rect).adjusted(inset, inset, -inset, -inset), radius, radius
        )

    def _draw_pixmap_cover(self, painter: QPainter, pm: QPixmap, rect) -> None:

        if pm.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        pw, ph = pm.width(), pm.height()
        if pw <= 0 or ph <= 0:
            return
        widget_ar = rect.width() / rect.height()
        pix_ar = pw / ph
        if pix_ar > widget_ar:

            scale_h = rect.height() / ph
            scaled_w = pw * scale_h
            offset_x = (scaled_w - rect.width()) / 2
            target = QRectF(-offset_x, 0, scaled_w, rect.height())
        else:
            scale_w = rect.width() / pw
            scaled_h = ph * scale_w
            offset_y = (scaled_h - rect.height()) / 2
            target = QRectF(0, -offset_y, rect.width(), scaled_h)
        painter.drawPixmap(target, pm, QRectF(0, 0, pw, ph))

    def _badge_font(self, painter: QPainter) -> None:
        f = painter.font()

        f.setPixelSize(FONT_HINT)
        f.setWeight(QFont.Weight.DemiBold)
        painter.setFont(f)

    def _draw_example_badge(self, painter: QPainter, rect, text: str) -> None:

        self._badge_font(painter)
        bw = painter.fontMetrics().horizontalAdvance(text) + 2 * _BADGE_PAD_PX
        bx = (rect.width() - bw) / 2.0
        badge = QRectF(bx, _BADGE_INSET_PX, bw, _BADGE_H_PX)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_BADGE_BG))
        painter.drawRoundedRect(badge, RADIUS_CHIP, RADIUS_CHIP)
        painter.setPen(QPen(_BADGE_TEXT))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_badge(self, painter: QPainter, text: str, x: int, y: int, align_right: bool = False) -> None:


        self._badge_font(painter)
        bw = painter.fontMetrics().horizontalAdvance(text) + 2 * _BADGE_PAD_PX
        left = x - bw if align_right else x
        rect = QRectF(left, y, bw, _BADGE_H_PX)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(_BADGE_BG))
        painter.drawRoundedRect(rect, RADIUS_CHIP, RADIUS_CHIP)
        painter.setPen(QPen(_BADGE_TEXT))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_placeholder(self, painter: QPainter, rect) -> None:
        painter.fillRect(rect, _PLACEHOLDER_BG)
        painter.setPen(QPen(_PLACEHOLDER_TEXT))
        f = painter.font()
        f.setPixelSize(FONT_HINT)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._placeholder_text)
