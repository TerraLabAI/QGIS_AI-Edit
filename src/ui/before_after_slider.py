"""BeforeAfterSlider - custom Qt widget that mimics the dashboard's
react slider in PyQt. Two QPixmap layers, vertically split by an animated
divider. Idle: auto-loop animation. Hover: pause + accept mouse drag.

Cross-version: works on PyQt5 (QGIS 3 / Qt 5) and PyQt6 (QGIS 4 / Qt 6).
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QT_VERSION, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap
from qgis.PyQt.QtWidgets import QWidget

from ..core.i18n import tr

QT6 = QT_VERSION >= 0x060000

# Auto-loop period (ms): full oscillation 0 → 100 → 0.
_AUTO_LOOP_PERIOD_MS = 5800
# Frame interval (ms): 60 fps target on idle systems.
_FRAME_INTERVAL_MS = 16
# Divider visuals.
_DIVIDER_COLOR = QColor("#FFFFFF")
_DIVIDER_SHADOW = QColor(0, 0, 0, 64)
_HANDLE_RADIUS_PX = 14
_DIVIDER_LINE_PX = 2
_BADGE_BG_BEFORE = QColor(20, 20, 20, 200)
_BADGE_BG_AFTER = QColor(139, 172, 39, 230)
_BADGE_TEXT = QColor("#FFFFFF")
_PLACEHOLDER_BG = QColor("#1E2A35")
_PLACEHOLDER_TEXT = QColor("#557080")


def _ease_in_out(t: float) -> float:
    """Smooth cubic ease-in-out on [0, 1]."""
    if t < 0.5:
        return 4 * t * t * t
    p = -2 * t + 2
    return 1 - p * p * p / 2


class BeforeAfterSlider(QWidget):
    """Two-image overlay slider with auto-loop + drag.

    Owners set ``before_pixmap`` and ``after_pixmap`` and the widget paints
    itself. Sliders that lack either pixmap show a tinted placeholder.
    """

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.setMouseTracking(False)
        self._before: QPixmap | None = None
        self._after: QPixmap | None = None
        # Divider position 0..1 (0 = fully before visible, 1 = fully after).
        self._pos = 0.5
        self._dragging = False
        self._hovering = False
        self._elapsed_ms = 0
        # Click-vs-drag tracking: a press emits `clicked` on release only if
        # the cursor stayed within the threshold; otherwise the gesture is
        # treated as a slider adjustment and no click fires.
        self._press_x: float | None = None
        self._moved_far = False
        self._timer = QTimer(self)
        self._timer.setInterval(_FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    # ---- lifecycle -------------------------------------------------------

    def closeEvent(self, ev):  # noqa: N802 - Qt signature
        self._timer.stop()
        super().closeEvent(ev)

    def deleteLater(self):
        self._timer.stop()
        super().deleteLater()

    # ---- public API ------------------------------------------------------

    def set_before(self, pixmap: QPixmap | None) -> None:
        self._before = pixmap if pixmap and not pixmap.isNull() else None
        self.update()

    def set_after(self, pixmap: QPixmap | None) -> None:
        self._after = pixmap if pixmap and not pixmap.isNull() else None
        self.update()

    def has_images(self) -> bool:
        return self._before is not None and self._after is not None

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt signature
        return QSize(280, 160)

    # ---- animation tick --------------------------------------------------

    def _on_tick(self) -> None:
        if self._hovering or self._dragging:
            return  # paused while user is engaging
        self._elapsed_ms = (self._elapsed_ms + _FRAME_INTERVAL_MS) % _AUTO_LOOP_PERIOD_MS
        # Triangle wave normalised to 0..1.
        half = _AUTO_LOOP_PERIOD_MS / 2
        t = self._elapsed_ms / half
        if t > 1.0:
            t = 2.0 - t
        self._pos = _ease_in_out(t)
        self.update()

    # ---- mouse handling --------------------------------------------------

    def enterEvent(self, ev):  # noqa: N802 - Qt signature
        self._hovering = True
        self.setMouseTracking(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev):  # noqa: N802 - Qt signature
        self._hovering = False
        self._dragging = False
        self.setMouseTracking(False)
        super().leaveEvent(ev)

    # If the mouse moved more than this from the press point, treat the
    # interaction as a drag (slider adjust) rather than a click (select).
    _CLICK_DRAG_THRESHOLD_PX = 5

    def mousePressEvent(self, ev):  # noqa: N802 - Qt signature
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._press_x = self._event_x(ev)
            self._moved_far = False
            self._update_pos_from_event(ev)
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):  # noqa: N802 - Qt signature
        if ev.button() == Qt.MouseButton.LeftButton:
            was_dragging = self._dragging
            moved_far = self._moved_far
            self._dragging = False
            self._moved_far = False
            # Click only when the press barely moved; drag-to-adjust must
            # never accidentally select the preset.
            if was_dragging and not moved_far:
                self.clicked.emit()
        super().mouseReleaseEvent(ev)

    def mouseMoveEvent(self, ev):  # noqa: N802 - Qt signature
        if self._dragging:
            if not self._moved_far and self._press_x is not None:
                if abs(self._event_x(ev) - self._press_x) > self._CLICK_DRAG_THRESHOLD_PX:
                    self._moved_far = True
            self._update_pos_from_event(ev)
        super().mouseMoveEvent(ev)

    @staticmethod
    def _event_x(ev) -> float:
        return ev.position().x() if QT6 else ev.pos().x()

    def _update_pos_from_event(self, ev) -> None:
        # Qt6: ev.position() returns QPointF; Qt5: ev.pos() returns QPoint.
        if QT6:
            x = ev.position().x()
        else:
            x = ev.pos().x()
        w = max(1, self.width())
        self._pos = max(0.0, min(1.0, x / w))
        self.update()

    # ---- paint -----------------------------------------------------------

    def paintEvent(self, ev):  # noqa: N802 - Qt signature
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = self.rect()
        radius = 10.0

        # Clip the whole widget to a rounded rect for a soft card look.
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        painter.setClipPath(path)

        # --- backdrop ----------------------------------------------------
        if self._before is None and self._after is None:
            self._paint_placeholder(painter, rect)
            painter.end()
            return

        # Compute divider X in widget coords.
        split_x = int(rect.width() * self._pos)

        # --- before layer (left of divider) ------------------------------
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

        # --- after layer (right of divider) ------------------------------
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

        # --- divider line + handle ---------------------------------------
        pen_shadow = QPen(_DIVIDER_SHADOW)
        pen_shadow.setWidth(_DIVIDER_LINE_PX + 2)
        painter.setPen(pen_shadow)
        painter.drawLine(split_x, 0, split_x, rect.height())
        pen = QPen(_DIVIDER_COLOR)
        pen.setWidth(_DIVIDER_LINE_PX)
        painter.setPen(pen)
        painter.drawLine(split_x, 0, split_x, rect.height())

        # Handle circle in the middle.
        handle_y = rect.height() // 2
        painter.setPen(QPen(_DIVIDER_SHADOW, 1))
        painter.setBrush(QBrush(_DIVIDER_COLOR))
        painter.drawEllipse(
            QPointF(split_x, handle_y),
            _HANDLE_RADIUS_PX,
            _HANDLE_RADIUS_PX,
        )
        # Twin arrows inside handle (drawn as a simple ASCII-glyph approx).
        painter.setPen(QPen(QColor("#202020"), 2))
        ay = handle_y
        painter.drawLine(split_x - 5, ay, split_x - 1, ay - 4)
        painter.drawLine(split_x - 5, ay, split_x - 1, ay + 4)
        painter.drawLine(split_x + 5, ay, split_x + 1, ay - 4)
        painter.drawLine(split_x + 5, ay, split_x + 1, ay + 4)

        # --- badges ------------------------------------------------------
        self._draw_badge(painter, "BEFORE", x=8, y=8, bg=_BADGE_BG_BEFORE)
        self._draw_badge(
            painter,
            "AFTER",
            x=rect.width() - 60,
            y=8,
            bg=_BADGE_BG_AFTER,
        )

        painter.end()

    def _draw_pixmap_cover(self, painter: QPainter, pm: QPixmap, rect) -> None:
        """Center-crop the pixmap to fully cover the widget rect (object-fit:cover)."""
        if pm.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return
        pw, ph = pm.width(), pm.height()
        if pw <= 0 or ph <= 0:
            return
        widget_ar = rect.width() / rect.height()
        pix_ar = pw / ph
        if pix_ar > widget_ar:
            # Pixmap is wider than widget - fit to height, crop sides.
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

    def _draw_badge(self, painter: QPainter, text: str, x: int, y: int, bg: QColor) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(bg))
        rect = QRectF(x, y, 52, 18)
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QPen(_BADGE_TEXT))
        f = painter.font()
        f.setPointSize(8)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_placeholder(self, painter: QPainter, rect) -> None:
        painter.fillRect(rect, _PLACEHOLDER_BG)
        painter.setPen(QPen(_PLACEHOLDER_TEXT))
        f = painter.font()
        f.setPointSize(9)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tr("Loading…"))
