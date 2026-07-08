"""Palette chips, color swatch, and eyedropper handling for the Vectorize panel."""
from __future__ import annotations

from qgis.core import QgsRasterLayer
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QColorDialog, QPushButton

from ....core import qt_compat as QtC
from ....core.i18n import tr
from ...panel_helpers import apply_swatch_style
from ...tools.eyedropper_tool import EyedropperMapTool


class ColorControlsMixin:
    """Detected-color chips plus the manual swatch and canvas eyedropper."""

    def _rebuild_palette_chips(self, raster: QgsRasterLayer) -> None:
        """Detect the raster's dominant colors and show them as one-click chips.

        Recomputes only when the raster changes (cheap, but no need to redo it on
        every project signal). Hidden when detection finds nothing usable, so the
        manual swatch stays the fallback."""
        rid = raster.id()
        if rid == self._palette_raster_id:
            return
        self._palette_raster_id = rid
        # Clear the whole row (chips AND the trailing stretch) so repeated
        # rebuilds never accumulate spacers that shove the chips out of place.
        while self._palette_row.count():
            item = self._palette_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._palette_chips = []

        path = (raster.source() or "").split("|", 1)[0]
        try:
            from ....core.generation.vectorization_service import dominant_palette
            self._palette = dominant_palette(path)
        except Exception:  # noqa: BLE001 - chips are a convenience, never block
            self._palette = []

        if len(self._palette) < 2:
            # Nothing meaningful to offer (single flat color or detection failed).
            self._palette_label.setVisible(False)
            return
        self._palette_label.setVisible(True)
        for rgb, _frac in self._palette:
            chip = QPushButton()
            chip.setCursor(QtC.PointingHandCursor)
            chip.setFixedSize(30, 30)
            apply_swatch_style(chip, QColor(*rgb))
            chip.setToolTip(
                tr("Extract #{hex} as polygons").format(
                    hex="{:02X}{:02X}{:02X}".format(*rgb)
                )
            )
            chip.clicked.connect(lambda _checked=False, c=rgb: self._on_palette_chip(c))
            self._palette_row.addWidget(chip)
            self._palette_chips.append(chip)
        self._palette_row.addStretch()

    def _on_palette_chip(self, rgb: tuple[int, int, int]) -> None:
        """Click a detected color -> extract it now. The most dominant OTHER
        detected color becomes the discarded background, so the boundary between
        the two classes is clean."""
        if self._busy:
            return
        self._color = QColor(*rgb)
        apply_swatch_style(self._color_btn, self._color)
        self._background = self._infer_background(rgb)
        if self._succeeded:
            self._on_refine_changed()
        else:
            self._on_run_clicked()

    def _infer_background(self, rgb: tuple[int, int, int]) -> tuple[int, int, int]:
        """Background color to discard in nearest-mode matching: the most-dominant
        detected color other than the picked one. Falls back to white (the
        canonical "class in color, everything else white" map) when no palette was
        detected, so a hand-picked or sampled color still gets a sane background
        instead of always assuming white on a non-white map."""
        others = [c for c, _ in self._palette if c != rgb]
        return others[0] if others else (255, 255, 255)

    def _on_color_clicked(self) -> None:
        chosen = QColorDialog.getColor(self._color, self, tr("Pick color"))
        if not chosen.isValid():
            return
        self._color = QColor(chosen.red(), chosen.green(), chosen.blue())
        apply_swatch_style(self._color_btn, self._color)
        # Discard the dominant OTHER color (usually the real background), not a
        # hard-coded white, so a hand-picked color works on non-white maps too.
        self._background = self._infer_background(
            (self._color.red(), self._color.green(), self._color.blue())
        )
        # In refine mode a new color re-runs detection on the same layer.
        if self._succeeded:
            self._on_refine_changed()

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
        self._color = color
        apply_swatch_style(self._color_btn, self._color)
        # Discard the dominant OTHER color (usually the real background), not a
        # hard-coded white, so a sampled color works on non-white maps too.
        self._background = self._infer_background(
            (self._color.red(), self._color.green(), self._color.blue())
        )
        self._show_status(
            tr("Sampled {hex}.").format(hex=self._color.name().upper()),
            is_error=False,
        )
        self._eyedropper_tool = None
        # In refine mode, sampling a new color re-runs detection on the same layer.
        if self._succeeded:
            self._on_refine_changed()

    def _on_eyedropper_miss(self) -> None:
        self._show_status(
            tr("That click missed the raster. Try again on the painted area."),
            is_error=True,
        )
        self._eyedropper_tool = None
