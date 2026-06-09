"""Canvas eyedropper for the Vectorize panel.

Single-shot QgsMapTool: click anywhere on the source raster, the tool
samples the RGB at that pixel, hands the QColor back to a callback, and
restores the previous map tool. Off-raster clicks fire an "off_raster"
callback so the panel can show a recoverable error.
"""
from __future__ import annotations

from typing import Callable

from qgis.core import QgsPointXY, QgsRaster, QgsRasterLayer
from qgis.gui import QgsMapCanvas, QgsMapTool
from qgis.PyQt.QtGui import QColor

from ...core import qt_compat as QtC


class EyedropperMapTool(QgsMapTool):
    """One-shot color sampler bound to a specific raster layer.

    On a successful click, calls `on_color(QColor)` and ends. On a click
    that misses the raster's extent (or where identify returns no value),
    calls `on_off_raster()` so the caller can prompt the user to retry.
    Either way the previous map tool is restored before the callback
    fires, so the panel UI is the one consuming the result.
    """

    def __init__(
        self,
        canvas: QgsMapCanvas,
        raster: QgsRasterLayer,
        on_color: Callable[[QColor], None],
        on_off_raster: Callable[[], None],
        previous_tool: QgsMapTool | None,
    ) -> None:
        super().__init__(canvas)
        self._canvas = canvas
        self._raster = raster
        self._on_color = on_color
        self._on_off_raster = on_off_raster
        self._previous_tool = previous_tool
        self.setCursor(QtC.CrossCursor)

    def canvasReleaseEvent(self, event):  # noqa: N802 (Qt naming)
        if event.button() != QtC.LeftButton:
            return
        map_point: QgsPointXY = self.toMapCoordinates(QtC.event_pos(event))
        color = self._sample_color(map_point)
        # Restore the previous tool BEFORE firing the callback so the
        # panel re-renders against a stable canvas state.
        self._restore_previous_tool()
        if color is None:
            self._on_off_raster()
        else:
            self._on_color(color)

    def keyPressEvent(self, event):  # noqa: N802
        # Escape cancels the sampling without firing either callback.
        if event.key() == QtC.Key_Escape:
            self._restore_previous_tool()
            event.accept()
            return
        # Keys we don't handle: ignore so the canvas keeps its keyboard nav
        # (hold-Space temporary pan). super() would leave the event accepted.
        event.ignore()

    def _sample_color(self, map_point: QgsPointXY) -> QColor | None:
        if self._raster is None or not self._raster.isValid():
            return None
        # Reproject the click into the raster's CRS when they differ.
        canvas_crs = self._canvas.mapSettings().destinationCrs()
        raster_crs = self._raster.crs()
        if canvas_crs != raster_crs:
            from qgis.core import QgsCoordinateTransform, QgsProject
            try:
                xform = QgsCoordinateTransform(
                    canvas_crs, raster_crs, QgsProject.instance()
                )
                map_point = xform.transform(map_point)
            except Exception:  # pragma: no cover  # nosec B110
                return None
        if not self._raster.extent().contains(map_point):
            return None
        provider = self._raster.dataProvider()
        if provider is None:
            return None
        result = provider.identify(map_point, QgsRaster.IdentifyFormatValue)
        if not result.isValid():
            return None
        values = result.results() or {}
        bands = list(values.values())
        if len(bands) < 3:
            return None
        try:
            r = int(round(float(bands[0])))
            g = int(round(float(bands[1])))
            b = int(round(float(bands[2])))
        except (TypeError, ValueError):
            return None
        return QColor(
            max(0, min(255, r)),
            max(0, min(255, g)),
            max(0, min(255, b)),
        )

    def _restore_previous_tool(self) -> None:
        if self._previous_tool is None:
            return
        try:
            self._canvas.setMapTool(self._previous_tool)
        except RuntimeError:  # pragma: no cover - C++ widget gone
            pass
