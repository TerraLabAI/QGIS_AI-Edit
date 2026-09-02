"""Drag a rectangle on the map to capture it as a reference image.

The native extent tool does the drawing (rubber band, cursor, the drag
itself). This subclass only says WHEN a capture is done: on mouse release,
with a real rectangle, never on the intermediate moves the base class also
reports through ``extentChanged``. Esc cancels and clears the band.
"""
from __future__ import annotations

from qgis.core import QgsRectangle
from qgis.gui import QgsMapToolExtent
from qgis.PyQt.QtCore import Qt, pyqtSignal


class ReferenceCaptureTool(QgsMapToolExtent):
    captured = pyqtSignal(QgsRectangle)  # canvas CRS
    cancelled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._dragging = False

    def canvasPressEvent(self, event):  # noqa: N802 - Qt naming
        self._dragging = True
        super().canvasPressEvent(event)

    def canvasReleaseEvent(self, event):  # noqa: N802 - Qt naming
        super().canvasReleaseEvent(event)
        if not self._dragging:
            return
        self._dragging = False
        rect = QgsRectangle(self.extent())
        self.clearRubberBand()
        if rect.isEmpty() or rect.width() <= 0 or rect.height() <= 0:
            self.cancelled.emit()
            return
        self.captured.emit(rect)

    def keyPressEvent(self, event):  # noqa: N802 - Qt naming
        if event.key() == Qt.Key.Key_Escape:
            self._dragging = False
            self.clearRubberBand()
            event.accept()
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def deactivate(self):
        self._dragging = False
        try:
            self.clearRubberBand()
        except RuntimeError:  # nosec B110 - canvas already gone
            pass
        super().deactivate()
