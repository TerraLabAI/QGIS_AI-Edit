from __future__ import annotations

from qgis.core import QgsPointXY, QgsRectangle, QgsWkbTypes
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QCursor

SUPPORTED_RATIOS = [
    (1, 1),
    (5, 4),
    (4, 5),
    (4, 3),
    (3, 4),
    (3, 2),
    (2, 3),
    (16, 9),
    (9, 16),
    (21, 9),
]


class RectangleSelectionTool(QgsMapTool):
    """Map tool for selecting a rectangular zone on the canvas."""

    selection_made = pyqtSignal(QgsRectangle)
    selection_cancelled = pyqtSignal()
    zone_too_small = pyqtSignal()

    MIN_SIZE_PX = 50

    def __init__(self, canvas):
        super().__init__(canvas)
        self._start_point = None
        self._rubber_band = None
        self._is_drawing = False
        self.setCursor(QCursor(Qt.CrossCursor))

    def canvasPressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._start_point = self.toMapCoordinates(event.pos())
            self._is_drawing = True
            self._create_rubber_band()

    def canvasMoveEvent(self, event):
        if not self._is_drawing or self._start_point is None:
            return
        end_point = self.toMapCoordinates(event.pos())
        rect = QgsRectangle(self._start_point, end_point)
        rect.normalize()
        if rect.width() > 0 and rect.height() > 0:
            rect, _ = self._snap_to_ratio(rect)
            self._update_rubber_band_from_rect(rect)
        else:
            self._update_rubber_band(self._start_point, end_point)

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._is_drawing:
            self._is_drawing = False
            end_point = self.toMapCoordinates(event.pos())
            rect = QgsRectangle(self._start_point, end_point)
            rect.normalize()
            rect, ratio = self._snap_to_ratio(rect)

            # Check minimum size in pixels
            p1 = self.toCanvasCoordinates(QgsPointXY(rect.xMinimum(), rect.yMinimum()))
            p2 = self.toCanvasCoordinates(QgsPointXY(rect.xMaximum(), rect.yMaximum()))
            width_px = abs(p2.x() - p1.x())
            height_px = abs(p2.y() - p1.y())

            if width_px < self.MIN_SIZE_PX or height_px < self.MIN_SIZE_PX:
                self._clear_rubber_band()
                self.zone_too_small.emit()
                return

            self.selection_made.emit(rect)

    def _snap_to_ratio(self, rect):
        """Snap rectangle to nearest supported aspect ratio."""
        if rect.width() == 0 or rect.height() == 0:
            return rect, (1, 1)

        current_ratio = rect.width() / rect.height()
        best = min(SUPPORTED_RATIOS, key=lambda r: abs(r[0] / r[1] - current_ratio))
        target_ratio = best[0] / best[1]

        new_height = rect.width() / target_ratio
        new_width = rect.height() * target_ratio
        height_delta = abs(new_height - rect.height())
        width_delta = abs(new_width - rect.width())

        sp = self._start_point
        if sp is not None:
            if height_delta <= width_delta:
                if abs(sp.y() - rect.yMinimum()) < abs(sp.y() - rect.yMaximum()):
                    rect.setYMaximum(rect.yMinimum() + new_height)
                else:
                    rect.setYMinimum(rect.yMaximum() - new_height)
            else:
                if abs(sp.x() - rect.xMinimum()) < abs(sp.x() - rect.xMaximum()):
                    rect.setXMaximum(rect.xMinimum() + new_width)
                else:
                    rect.setXMinimum(rect.xMaximum() - new_width)
        else:
            cx, cy = rect.center().x(), rect.center().y()
            if height_delta <= width_delta:
                rect.setYMinimum(cy - new_height / 2)
                rect.setYMaximum(cy + new_height / 2)
            else:
                rect.setXMinimum(cx - new_width / 2)
                rect.setXMaximum(cx + new_width / 2)

        return rect, best

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._is_drawing = False
            self._clear_rubber_band()
            self.selection_cancelled.emit()

    def deactivate(self):
        self._clear_rubber_band()
        super().deactivate()

    def _create_rubber_band(self):
        self._clear_rubber_band()
        self._rubber_band = QgsRubberBand(self.canvas(), QgsWkbTypes.PolygonGeometry)
        self._rubber_band.setColor(QColor(65, 105, 225, 80))
        self._rubber_band.setStrokeColor(QColor(65, 105, 225, 200))
        self._rubber_band.setWidth(2)

    def _update_rubber_band_from_rect(self, rect):
        if not self._rubber_band:
            return
        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        self._rubber_band.addPoint(QgsPointXY(rect.xMinimum(), rect.yMinimum()), False)
        self._rubber_band.addPoint(QgsPointXY(rect.xMaximum(), rect.yMinimum()), False)
        self._rubber_band.addPoint(QgsPointXY(rect.xMaximum(), rect.yMaximum()), False)
        self._rubber_band.addPoint(QgsPointXY(rect.xMinimum(), rect.yMaximum()), True)

    def _update_rubber_band(self, start, end):
        if not self._rubber_band:
            return
        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        self._rubber_band.addPoint(QgsPointXY(start.x(), start.y()), False)
        self._rubber_band.addPoint(QgsPointXY(end.x(), start.y()), False)
        self._rubber_band.addPoint(QgsPointXY(end.x(), end.y()), False)
        self._rubber_band.addPoint(QgsPointXY(start.x(), end.y()), True)

    def _clear_rubber_band(self):
        if self._rubber_band:
            self.canvas().scene().removeItem(self._rubber_band)
            self._rubber_band = None
