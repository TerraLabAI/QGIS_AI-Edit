"""Vectorize panel widget.

Self-contained QWidget that runs the color-based raster-to-polygon
workflow. Manages its own active-layer tracking, refine debounce, and
busy state.
"""
from __future__ import annotations

import os

from qgis.core import QgsFeature, QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QColorDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core import qt_compat as QtC
from ..core import telemetry
from ..core.i18n import tr
from ..core.vectorization_service import vectorize_by_color
from .eyedropper_tool import EyedropperMapTool
from .layer_groups import get_or_create_ai_edit_group
from .layer_tree_combobox import LayerTreeComboBox
from .panel_helpers import apply_swatch_style, build_panel_header, panel_section_label

BRAND_BLUE = "#1976d2"
BRAND_BLUE_HOVER = "#1565c0"
BRAND_DISABLED = "#b0bec5"
DISABLED_TEXT = "#666666"
ERROR_TEXT = "#ef5350"
SUCCESS_TEXT = "#66bb6a"

_BTN_BLUE_QSS = (
    f"QPushButton {{ background-color: {BRAND_BLUE}; color: #000000;"
    f" padding: 6px 12px; }}"
    f"QPushButton:hover {{ background-color: {BRAND_BLUE_HOVER}; color: #000000; }}"
    f"QPushButton:disabled {{ background-color: {BRAND_DISABLED};"
    f" color: {DISABLED_TEXT}; }}"
)

_BTN_GHOST_QSS = (
    "QPushButton { background-color: transparent; color: palette(text);"
    " padding: 8px 16px; border-radius: 4px;"
    " border: 1px solid rgba(128, 128, 128, 0.35); }"
    "QPushButton:hover { background-color: rgba(128, 128, 128, 0.15);"
    " border: 1px solid rgba(128, 128, 128, 0.5); }"
    f"QPushButton:disabled {{ background-color: rgba(128, 128, 128, 0.08);"
    f" border: 1px solid rgba(128, 128, 128, 0.15); color: {DISABLED_TEXT}; }}"
)


class VectorizePanel(QWidget):
    """Color-based raster-to-polygon workflow.

    Step 1 (always visible): pick the active raster, pick the color to
    extract, click Vectorize. Step 2 (revealed after the first successful
    run): a collapsible Refine box wired to a 150 ms debounce that re-runs
    the vectorization in place, replacing the previous polygon layer
    instead of stacking duplicates.
    """

    done_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Snap the swatch back to the canonical template red (#FF0000).
        # Every Segment & Vectorize template paints classes in that exact
        # hue, so it's the right starting point on every fresh open.
        self._color = QColor(255, 0, 0)
        self._busy = False
        self._succeeded = False
        self._last_layer_id: str | None = None
        self._last_raster_id: str | None = None
        self._last_target_rgb: tuple[int, int, int] | None = None
        self._eyedropper_tool: EyedropperMapTool | None = None
        # Refine box: always visible from cold-entry, collapsed by default.
        # Title gets a "· modified" badge when any spinbox is off-defaults.
        self._refine_expanded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(
            build_panel_header(
                tr("Vectorize"),
                subtitle=tr(
                    "Turn flat-color regions of a raster layer into vector "
                    "polygons. Pick a source raster and a color, then run."
                ),
            )
        )

        # --- Source raster: layer-tree combo ---------------------------
        layout.addWidget(panel_section_label(tr("From")))

        self._layer_combo = LayerTreeComboBox()
        self._layer_combo.setToolTip(
            tr("Pick the raster you want to extract polygons from.")
        )
        self._layer_combo.layerChanged.connect(self._refresh_panel_state)
        layout.addWidget(self._layer_combo)

        # --- Color row -------------------------------------------------
        layout.addWidget(panel_section_label(tr("Color")))

        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(8)
        self._color_btn = QPushButton()
        self._color_btn.setToolTip(
            tr("Tap to pick a color from a dialog.")
        )
        self._color_btn.setCursor(QtC.PointingHandCursor)
        self._color_btn.setFixedSize(40, 40)
        apply_swatch_style(self._color_btn, self._color)
        self._color_btn.clicked.connect(self._on_color_clicked)
        color_row.addWidget(self._color_btn)

        self._eyedropper_btn = QPushButton(tr("⌖ Pick on map"))
        self._eyedropper_btn.setToolTip(
            tr("Sample a color directly from the source raster.")
        )
        self._eyedropper_btn.setCursor(QtC.PointingHandCursor)
        self._eyedropper_btn.setStyleSheet(_BTN_GHOST_QSS)
        self._eyedropper_btn.setMinimumHeight(34)
        self._eyedropper_btn.clicked.connect(self._on_eyedropper_clicked)
        color_row.addWidget(self._eyedropper_btn)
        color_row.addStretch()
        layout.addLayout(color_row)

        # --- Refine box (hidden until first successful vectorization) ---
        # Refining only makes sense once polygons exist, so the whole group
        # stays out of sight on cold entry and slides in after the first run.
        self._refine_group = self._build_refine_group()
        self._refine_content.setVisible(False)
        self._refine_group.setVisible(False)
        self._refresh_refine_title()
        layout.addWidget(self._refine_group)

        # 150 ms debounce so dragging a spinbox doesn't fire 60 vectorizations.
        self._refine_timer = QTimer(self)
        self._refine_timer.setSingleShot(True)
        self._refine_timer.timeout.connect(self._on_refine_apply)

        # Status line
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(
            "font-size: 11px; color: palette(text); background: transparent;"
            " border: none; padding: 2px 0 0 0;"
        )
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        # Action row: Exit (left, ghost) and Vectorize (right, primary blue).
        # Mirrors the Mark up panel's bottom row so the two tool panels feel
        # like siblings.
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 6, 0, 0)
        action_row.setSpacing(6)

        exit_btn = QPushButton(tr("Exit"))
        exit_btn.setStyleSheet(_BTN_GHOST_QSS)
        exit_btn.setCursor(QtC.PointingHandCursor)
        exit_btn.setMinimumHeight(34)
        exit_btn.setMinimumWidth(80)
        exit_btn.clicked.connect(self.done_clicked.emit)
        action_row.addWidget(exit_btn)
        action_row.addStretch()

        self._run_btn = QPushButton(tr("Vectorize"))
        self._run_btn.setStyleSheet(_BTN_BLUE_QSS)
        self._run_btn.setCursor(QtC.PointingHandCursor)
        self._run_btn.setMinimumHeight(34)
        self._run_btn.clicked.connect(self._on_run_clicked)
        action_row.addWidget(self._run_btn)
        layout.addLayout(action_row)
        layout.addStretch()

    # -- public API ------------------------------------------------------

    def activate(self) -> None:
        """Called when the panel becomes visible: reset state and refresh
        button enabled-state from the current combo selection.
        """
        self._reset_state()
        self._refresh_panel_state()

    def preconfigure(
        self,
        layer_id: str | None = None,
        color_hex: str | None = None,
    ) -> None:
        """Pre-fill the source layer and target color before show.

        Used when the user enters the panel from the result-panel CTA so
        the swatch is already set to the template's vector_color and the
        combo is locked on the just-generated raster. Call AFTER activate().
        """
        if color_hex:
            qc = QColor(color_hex)
            if qc.isValid():
                self._color = QColor(qc.red(), qc.green(), qc.blue())
                apply_swatch_style(self._color_btn, self._color)
        if layer_id:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is not None:
                self._layer_combo.setLayer(layer)
                self._refresh_panel_state()

    def deactivate(self) -> None:
        """Hook kept for symmetry; no-op now that the panel manages its
        own layer selection via LayerTreeComboBox.
        """
        return

    # -- internals -------------------------------------------------------

    def _reset_state(self) -> None:
        """Wipe last-run state so the panel re-enters at Step 1."""
        self._last_layer_id = None
        self._last_raster_id = None
        self._last_target_rgb = None
        self._succeeded = False
        self._color = QColor(255, 0, 0)
        apply_swatch_style(self._color_btn, self._color)
        self._status_label.setVisible(False)
        self._reset_refine_spinboxes()
        self._refine_expanded = False
        self._refine_content.setVisible(False)
        self._refine_group.setVisible(False)
        self._refresh_refine_title()
        self._run_btn.setText(tr("Vectorize"))
        # LayerTreeComboBox auto-refreshes via project signals; no manual
        # repopulation needed here.

    def _build_refine_group(self) -> QGroupBox:
        """Collapsible Refine group, mirrors AI Segmentation's pattern."""
        group = QGroupBox("▼ " + tr("Refine vectorization"))
        group.setCheckable(False)
        group.setCursor(QtC.PointingHandCursor)
        group.mousePressEvent = self._on_refine_title_clicked
        group.setStyleSheet(
            "QGroupBox { background-color: transparent; border: none;"
            " border-radius: 0px; margin: 0px; padding: 0px; padding-top: 20px; }"
            "QGroupBox::title { subcontrol-origin: padding;"
            " subcontrol-position: top left; padding: 2px 4px;"
            " background-color: transparent; border: none; }"
        )
        outer = QVBoxLayout(group)
        outer.setSpacing(0)
        outer.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        content.setObjectName("vectorizeRefineContent")
        content.setStyleSheet(
            "QWidget#vectorizeRefineContent {"
            " background-color: rgba(128, 128, 128, 0.08);"
            " border: 1px solid rgba(128, 128, 128, 0.2);"
            " border-radius: 4px; }"
            "QLabel { background: transparent; border: none; }"
        )
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(6)

        def _section(text: str) -> QLabel:
            lbl = QLabel(text.upper())
            lbl.setStyleSheet(
                "font-size: 10px; color: palette(text); font-weight: bold;"
                " background: transparent; border: none;"
                " border-bottom: 1px solid rgba(128, 128, 128, 0.35);"
                " padding: 4px 0px 4px 0px; margin-bottom: 4px;"
                " letter-spacing: 1px;"
            )
            return lbl

        def _spin_row(parent_layout, label_text: str, tip: str,
                      lo: int, hi: int, default: int) -> QSpinBox:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(label_text)
            lab.setStyleSheet("font-size: 11px; color: palette(text);")
            lab.setToolTip(tip)
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(default)
            spin.setMinimumWidth(55)
            spin.setMaximumWidth(70)
            spin.setToolTip(tip)
            row.addWidget(lab)
            row.addStretch()
            row.addWidget(spin)
            parent_layout.addLayout(row)
            return spin

        content_layout.addWidget(_section(tr("Detection")))
        self._tolerance_spin = _spin_row(
            content_layout,
            tr("Color tolerance:"),
            tr("Per-channel ± distance when matching pixels (0-100)."),
            0, 100, 40,
        )
        self._sieve_spin = _spin_row(
            content_layout,
            tr("Min size (sieve):"),
            tr(
                "Drop tiny blobs before tracing: pre-polygonize filter "
                "(connected pixel count)."
            ),
            0, 500, 10,
        )

        content_layout.addWidget(_section(tr("Outline")))
        self._simplify_spin = _spin_row(
            content_layout,
            tr("Simplify outline:"),
            tr("Higher = smoother polygons (Douglas-Peucker, in pixels)."),
            0, 50, 3,
        )

        outer.addWidget(content)
        self._refine_content = content

        for spin in (self._tolerance_spin, self._sieve_spin, self._simplify_spin):
            spin.valueChanged.connect(self._on_refine_changed)

        return group

    def _on_refine_title_clicked(self, event) -> None:
        y = (
            event.position().toPoint().y()
            if hasattr(event, "position")
            else event.pos().y()
        )
        if y > 25:
            return
        self._refine_expanded = not self._refine_expanded
        self._refresh_refine_title()
        self._refine_content.setVisible(self._refine_expanded)

    def _refresh_refine_title(self) -> None:
        """Title shows the disclosure arrow and a "· modified" badge when
        any spinbox differs from its default.
        """
        arrow = "▼" if self._refine_expanded else "▶"
        modified = (
            self._tolerance_spin.value() != 40
            or self._sieve_spin.value() != 10  # noqa: W503
            or self._simplify_spin.value() != 3  # noqa: W503
        )
        suffix = "  · " + tr("modified") if modified else ""
        self._refine_group.setTitle(arrow + " " + tr("Refine vectorization") + suffix)

    def _on_refine_changed(self, _value=None) -> None:
        self._refresh_refine_title()
        if self._last_layer_id is None:
            return
        self._refine_timer.start(150)

    def _reset_refine_spinboxes(self) -> None:
        for spin, default in (
            (self._tolerance_spin, 40),
            (self._sieve_spin, 10),
            (self._simplify_spin, 3),
        ):
            spin.blockSignals(True)
            spin.setValue(default)
            spin.blockSignals(False)

    def _refresh_panel_state(self, *_args) -> None:
        """Enable the Vectorize button when the combo's current layer is
        a multi-band RGB raster with a real on-disk source.
        """
        layer = self._layer_combo.currentLayer()
        is_raster = isinstance(layer, QgsRasterLayer)
        has_file_source = (
            is_raster
            and bool(layer.source())  # noqa: W503
            and os.path.exists(layer.source())  # noqa: W503
        )
        is_valid = is_raster and layer.bandCount() >= 3 and has_file_source

        if is_valid:
            self._show_status("", is_error=False)
            self._run_btn.setEnabled(not self._busy)
        elif is_raster and not has_file_source:
            self._show_status(
                tr("Online or virtual raster: save it as GeoTIFF first."),
                is_error=True,
            )
            self._run_btn.setEnabled(False)
        elif is_raster:
            self._show_status(
                tr("This raster needs at least 3 bands (RGB)."), is_error=True
            )
            self._run_btn.setEnabled(False)
        else:
            self._show_status("", is_error=False)
            self._run_btn.setEnabled(False)

    def _on_color_clicked(self) -> None:
        chosen = QColorDialog.getColor(self._color, self, tr("Pick color"))
        if not chosen.isValid():
            return
        self._color = QColor(chosen.red(), chosen.green(), chosen.blue())
        apply_swatch_style(self._color_btn, self._color)

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
        self._show_status(
            tr("Sampled {hex}.").format(hex=self._color.name().upper()),
            is_error=False,
        )
        self._eyedropper_tool = None

    def _on_eyedropper_miss(self) -> None:
        self._show_status(
            tr("That click missed the raster. Try again on the painted area."),
            is_error=True,
        )
        self._eyedropper_tool = None

    def _on_run_clicked(self) -> None:
        if self._busy:
            return

        # After a successful run, the primary button morphs to "Done" and
        # clicking it exits the panel. Refine spinboxes still trigger
        # debounced re-runs for tuning, so the user can keep adjusting
        # without re-clicking the button.
        if self._succeeded:
            self.done_clicked.emit()
            return

        raster = self._layer_combo.currentLayer()
        if not isinstance(raster, QgsRasterLayer):
            self._show_status(
                tr("Pick a raster from the source list first."), is_error=True
            )
            return
        if raster.bandCount() < 3:
            self._show_status(
                tr("This raster needs at least 3 bands (RGB)."), is_error=True
            )
            return

        target_rgb = (self._color.red(), self._color.green(), self._color.blue())
        self._run_vectorize(raster, target_rgb, is_initial=True)

    def _on_refine_apply(self) -> None:
        """Debounced re-run on the same raster + extracted color."""
        if self._last_raster_id is None or self._last_target_rgb is None:
            return
        raster = QgsProject.instance().mapLayer(self._last_raster_id)
        if not isinstance(raster, QgsRasterLayer):
            self._show_status(
                tr("Source raster is no longer available."), is_error=True
            )
            return
        self._run_vectorize(raster, self._last_target_rgb, is_initial=False)

    def _run_vectorize(
        self,
        raster: QgsRasterLayer,
        target_rgb: tuple[int, int, int],
        is_initial: bool,
    ) -> None:
        layer_name = f"{raster.name()} (vector)"

        tolerance = int(self._tolerance_spin.value())
        simplify_factor = float(self._simplify_spin.value())
        sieve_threshold = int(self._sieve_spin.value())

        self._busy = True
        self._run_btn.setEnabled(False)
        self._color_btn.setEnabled(False)
        self._run_btn.setText(tr("Vectorizing..."))
        self._show_status(
            tr("Vectorizing “{name}”...").format(name=raster.name()),
            is_error=False,
        )

        QApplication.setOverrideCursor(QtC.WaitCursor)
        QApplication.processEvents()  # let the UI repaint before the blocking call

        try:
            new_layer = vectorize_by_color(
                raster,
                target_rgb,
                tolerance=tolerance,
                sieve_threshold=sieve_threshold,
                simplify_factor=simplify_factor,
                layer_name=layer_name,
            )

            previous_id = self._last_layer_id
            existing = (
                QgsProject.instance().mapLayer(previous_id)
                if previous_id
                else None
            )

            if existing is not None:
                # Re-run: transplant the new geometries into the existing
                # layer so the user's symbology, name and layer id all
                # survive. Avoids the "color resets to default on refine"
                # bug entirely.
                provider = existing.dataProvider()
                old_ids = [f.id() for f in existing.getFeatures()]
                if old_ids:
                    provider.deleteFeatures(old_ids)
                fresh_feats = [QgsFeature(f) for f in new_layer.getFeatures()]
                provider.addFeatures(fresh_feats)
                existing.updateExtents()
                existing.triggerRepaint()
                final_layer = existing
            else:
                QgsProject.instance().addMapLayer(new_layer, False)
                group = get_or_create_ai_edit_group()
                group.insertLayer(0, new_layer)
                final_layer = new_layer

            self._last_layer_id = final_layer.id()
            self._last_raster_id = raster.id()
            self._last_target_rgb = target_rgb

            polygon_count = final_layer.featureCount()
            self._show_status(
                tr("✓ {n} polygons added").format(n=polygon_count),
                is_error=False,
                is_success=True,
            )

            self._activate_layer_in_panel(final_layer)
            self._succeeded = True
            self._refine_group.setVisible(True)
            self._run_btn.setText(tr("Done"))
            telemetry.track(
                "vectorize_completed",
                {
                    "polygon_count": polygon_count,
                    "tolerance": tolerance,
                    "sieve": sieve_threshold,
                    "simplify": int(simplify_factor),
                    "is_initial": is_initial,
                },
            )
        except Exception as e:
            self._handle_run_error(str(e))
        finally:
            QApplication.restoreOverrideCursor()
            self._reset_button()

    def _handle_run_error(self, message: str) -> None:
        """Render a friendlier error and steer the user to the lever that
        usually fixes it. Zero-match cases auto-expand the Refine box and
        highlight the tolerance spinbox; other errors just show the raw
        message in the status line.
        """
        lower = message.lower()
        is_zero_match = (
            "no pixels matched" in lower
            or "no polygons remained" in lower  # noqa: W503
        )
        if is_zero_match:
            self._show_status(
                tr(
                    "0 matches. Widen the tolerance below or pick a "
                    "closer color above."
                ),
                is_error=True,
            )
            # Surface the Refine box (hidden on cold entry) and pull the
            # user's eye to tolerance, which fixes ~90 % of zero-match cases.
            self._refine_group.setVisible(True)
            if not self._refine_expanded:
                self._refine_expanded = True
                self._refine_content.setVisible(True)
                self._refresh_refine_title()
            self._tolerance_spin.setFocus()
        else:
            self._show_status(message, is_error=True)

    def _activate_layer_in_panel(self, layer) -> None:
        """Highlight the freshly-produced layer in the QGIS Layers panel."""
        try:
            from qgis.utils import iface as _iface
            if _iface is not None:
                _iface.setActiveLayer(layer)
        except Exception:  # pragma: no cover  # nosec B110
            pass

    def _reset_button(self) -> None:
        self._busy = False
        self._run_btn.setEnabled(True)
        self._color_btn.setEnabled(True)
        # If we succeeded, the run text was already set to "Done" upstream
        # and we must NOT overwrite it here (otherwise refine re-runs would
        # silently flip the label back to "Vectorize").
        if not self._succeeded:
            self._run_btn.setText(tr("Vectorize"))

    def _show_status(
        self, message: str, is_error: bool, is_success: bool = False
    ) -> None:
        if is_error:
            color = ERROR_TEXT
        elif is_success:
            color = SUCCESS_TEXT
        else:
            color = "palette(text)"
        self._status_label.setStyleSheet(
            f"font-size: 11px; color: {color}; background: transparent;"
            " border: none; padding: 2px 0 0 0;"
        )
        self._status_label.setText(message)
        self._status_label.setVisible(bool(message))
