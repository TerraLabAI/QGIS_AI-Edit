








from __future__ import annotations

from qgis.core import QgsRectangle
from qgis.gui import QgsMapToolExtent
from qgis.PyQt.QtCore import QPoint, Qt, pyqtSignal


MIN_CAPTURE_DRAG_PX = 8


def is_capture_drag_too_small(start: QPoint | None, end: QPoint | None) -> bool:

    if start is None or end is None:
        return True
    return (abs(end.x() - start.x()) < MIN_CAPTURE_DRAG_PX
            or abs(end.y() - start.y()) < MIN_CAPTURE_DRAG_PX)


class ReferenceCaptureTool(QgsMapToolExtent):
    captured = pyqtSignal(QgsRectangle)
    cancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._dragging = False
        self._press_pos: QPoint | None = None

    def canvasPressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:


            self._cancel()
            return
        self._dragging = True
        self._press_pos = QPoint(event.pixelPoint())
        super().canvasPressEvent(event)

    def canvasReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            return
        super().canvasReleaseEvent(event)
        if not self._dragging:
            return
        self._dragging = False
        start, self._press_pos = self._press_pos, None
        rect = QgsRectangle(self.extent())
        self.clearRubberBand()
        if (is_capture_drag_too_small(start, QPoint(event.pixelPoint()))
                or rect.isEmpty() or rect.width() <= 0 or rect.height() <= 0):
            self.cancelled.emit()
            return
        self.captured.emit(rect)

    def _cancel(self) -> None:
        self._dragging = False
        self._press_pos = None
        self.clearRubberBand()
        self.cancelled.emit()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self._cancel()
            return
        super().keyPressEvent(event)

    def deactivate(self):
        self._dragging = False
        self._press_pos = None
        try:
            self.clearRubberBand()
        except RuntimeError:  # nosec B110
            pass
        super().deactivate()
