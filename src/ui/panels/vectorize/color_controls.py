"""Class detection and eyedropper handling for the Vectorize panel."""
from __future__ import annotations

from qgis.core import QgsRasterLayer
from qgis.PyQt.QtGui import QColor

from ....core.i18n import tr
from ...tools.eyedropper_tool import EyedropperMapTool


class ColorControlsMixin:
    """Populates the detected-class list and runs the canvas eyedropper."""

    def _rebuild_class_list(self, raster: QgsRasterLayer) -> None:
        """Detect the raster's flat colors and fill the class list.

        Recomputes only when the raster changes (cheap, but no need to redo it
        on every project signal). When detection finds nothing usable the map is
        probably photo-realistic; say so instead of showing a dead list."""
        rid = raster.id()
        if rid == self._classes_raster_id:
            return
        self._classes_raster_id = rid

        path = (raster.source() or "").split("|", 1)[0]
        try:
            from ....core.generation.vectorize_palette import detect_classes
            entries = detect_classes(path)
        except Exception:  # noqa: BLE001 - detection is a convenience, never block
            entries = []

        self._class_list.set_classes(entries)
        flat_map = len(entries) >= 2
        self._class_list.setVisible(flat_map)
        self._classes_intro.setVisible(flat_map)
        self._photo_hint.setVisible(not flat_map)

    def _on_eyedropper_clicked(self) -> None:
        """Arm the canvas eyedropper bound to the currently-picked raster."""
        raster = self._layer_combo.currentLayer()
        if not isinstance(raster, QgsRasterLayer):
            self._show_status(
                tr("Pick a raster from the source list first."), is_error=True
            )
            return
        try:
            from qgis.utils import iface as _iface
        except ImportError:  # pragma: no cover - non-QGIS env
            return
        if _iface is None:
            return
        canvas = _iface.mapCanvas()
        previous_tool = canvas.mapTool()
        tool = EyedropperMapTool(
            canvas=canvas,
            raster=raster,
            on_color=self._on_eyedropper_color,
            on_off_raster=self._on_eyedropper_miss,
            previous_tool=previous_tool,
        )
        # Keep a reference on self so the tool isn't GC'd between click
        # and release.
        self._eyedropper_tool = tool
        canvas.setMapTool(tool)
        self._show_status(
            tr("Click anywhere on the source raster to sample its color."),
            is_error=False,
        )

    def _on_eyedropper_color(self, color: QColor) -> None:
        rgb = (color.red(), color.green(), color.blue())
        # A sampled color joins the class list (or checks its near-twin); it
        # takes effect on the next Vectorize click.
        self._class_list.add_class(rgb)
        self._class_list.setVisible(True)
        self._classes_intro.setVisible(True)
        self._photo_hint.setVisible(False)
        self._show_status(
            tr("Added {hex} to the class list.").format(hex=color.name().upper()),
            is_error=False,
        )
        self._eyedropper_tool = None

    def _on_eyedropper_miss(self) -> None:
        self._show_status(
            tr("That click missed the raster. Try again on the painted area."),
            is_error=True,
        )
        self._eyedropper_tool = None
