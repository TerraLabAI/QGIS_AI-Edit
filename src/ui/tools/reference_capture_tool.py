"""Drag a rectangle on the map to capture it as a reference image.

The native extent tool does the drawing (rubber band, cursor, the drag
itself). This subclass only says WHEN a capture is done: on mouse release,
with a real rectangle, never on the intermediate moves the base class also
reports through ``extentChanged``. Esc or a right click cancels and clears
the band. A drag shorter than a few screen pixels is a click, not a box: it
cancels instead of storing a sliver nobody can read.
"""
from __future__ import annotations

from qgis.core import QgsRectangle
from qgis.gui import QgsMapToolExtent
from qgis.PyQt.QtCore import QPoint, Qt, pyqtSignal

# Screen pixels a drag must span on each axis to count as a box.
MIN_CAPTURE_DRAG_PX = 8


def is_capture_drag_too_small(start: QPoint | None, end: QPoint | None) -> bool:
    """True when a press and release are too close to be a deliberate box."""
    if start is None or end is None:
        return True
    return (abs(end.x() - start.x()) < MIN_CAPTURE_DRAG_PX
            or abs(end.y() - start.y()) < MIN_CAPTURE_DRAG_PX)


class ReferenceCaptureTool(QgsMapToolExtent):
    captured = pyqtSignal(QgsRectangle)  # canvas CRS
    cancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._dragging = False
        self._press_pos: QPoint | None = None

    def canvasPressEvent(self, event):  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.RightButton:
            # A right click is the canvas's usual "stop": no QGIS extent
            # tool draws with it, and here it used to start a box.
            self._cancel()
            return
        self._dragging = True
        self._press_pos = QPoint(event.pixelPoint())
        super().canvasPressEvent(event)

    def canvasReleaseEvent(self, event):  # noqa: N802 - Qt naming
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

    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
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
        except RuntimeError:  # nosec B110 - canvas already gone
            pass
        super().deactivate()
