"""Vectorize panel widget.

Self-contained QWidget that runs the color-based raster-to-polygon
workflow. Manages its own active-layer tracking, refine debounce, and
busy state.
"""
from __future__ import annotations

import os

from qgis.core import QgsFeature, QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeySequence, QShortcut
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core.errors import AIEditError
from ...core.i18n import tr
from ..layer_groups import (
    AI_EDIT_GROUP_NAME,
    add_layer_to_ai_edit_top,
    find_generation_subgroup_for_layer,
    pick_default_layer,
    promote_layer_to_own_subgroup,
)
from ..layer_tree_combobox import LayerTreeComboBox
from ..onboarding_hint import HINT_VECTORIZE, DismissibleHint, is_hint_dismissed
from ..panel_helpers import (
    GROUP_BOX_QSS,
    apply_swatch_style,
    build_panel_header,
)
from ..tools.eyedropper_tool import EyedropperMapTool

BRAND_BLUE = "#1e88e5"
BRAND_BLUE_HOVER = "#1976d2"
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


def _is_ai_edit_output(layer) -> bool:
    """Return True when ``layer`` lives under the AI-Edit layer-tree group.

    The AI-Edit group is the canonical home for every plugin-generated
    raster, so group membership is a reliable marker for "produced by AI
    Edit" without stamping per-layer properties.
    """
    if not isinstance(layer, QgsRasterLayer):
        return False
    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(layer.id())
    if node is None:
        return False
    parent = node.parent()
    while parent is not None and parent is not root:
        if parent.name() == AI_EDIT_GROUP_NAME:
            return True
        parent = parent.parent()
    return False


def _is_visible_ai_edit_output(layer) -> bool:
    """``_is_ai_edit_output`` plus tree visibility.

    The combo only lists visible layers, so the default pick must also be
    visible — otherwise ``setLayer()`` silently fails and the combo lands
    on whatever fallback it auto-picks.
    """
    if not _is_ai_edit_output(layer):
        return False
    node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    return node is not None and node.isVisible()


class VectorizePanel(QWidget):
    """Color-based raster-to-polygon workflow. Refine box re-runs via debounce."""

    done_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Snap the swatch back to the canonical template red (#FF0000).
        # Every Segment & Vectorize template paints classes in that exact
        # hue, so it's the right starting point on every fresh open.
        self._color = QColor(255, 0, 0)
        self._busy = False
        self._succeeded = False
        self._vectorize_task = None
        self._last_layer_id: str | None = None
        self._last_raster_id: str | None = None
        self._last_target_rgb: tuple[int, int, int] | None = None
        # Pre-fill written to the class_name attribute for every polygon.
        # Set by preconfigure() from the template metadata; empty for
        # manual runs so the user fills it inline in the attribute table.
        self._class_label: str = ""
        self._eyedropper_tool: EyedropperMapTool | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(build_panel_header(tr("Vectorize")))

        # Dismissible tip at the top (same pattern as Mark up / the prompt
        # library): the "what does this do" explanation lives here, kept out
        # of the controls. Restorable from Account Settings; activate()
        # re-checks its state.
        self._hint = DismissibleHint(
            HINT_VECTORIZE,
            "",
            tr("After a Segment or Land cover template colors your zone "
               "(buildings, parcels, classes...), Vectorize traces each color "
               "into editable vector polygons - select, measure, style and "
               "export them."),
        )
        layout.addWidget(self._hint)

        # Layer picker, grouped like Mark up's sections. The combo and the
        # empty-state note swap inside the group; the whole group hides once a
        # vectorization succeeds (refine mode locks the layer).
        self._layer_group = QGroupBox(tr("Layer"))
        self._layer_group.setStyleSheet(GROUP_BOX_QSS)
        layer_box = QVBoxLayout(self._layer_group)
        layer_box.setContentsMargins(8, 6, 8, 8)
        layer_box.setSpacing(6)
        self._layer_combo = LayerTreeComboBox()
        self._layer_combo.setToolTip(tr("Pick an AI Edit output to vectorize."))
        self._layer_combo.set_layer_filter(_is_ai_edit_output)
        self._layer_combo.layerChanged.connect(self._refresh_panel_state)
        layer_box.addWidget(self._layer_combo)
        self._empty_state_label = QLabel(
            tr("No AI Edit output yet. Generate a map first, then vectorize it.")
        )
        self._empty_state_label.setWordWrap(True)
        self._empty_state_label.setStyleSheet(
            "font-size: 11px; color: palette(text); background: transparent;"
            " border: none; padding: 2px;"
        )
        self._empty_state_label.setVisible(False)
        layer_box.addWidget(self._empty_state_label)
        layout.addWidget(self._layer_group)

        # Color picker, grouped like Mark up. Hidden on success (refine
        # re-runs reuse the locked color). The swatch + eyedropper are the
        # only controls; what the color means is explained in the tip above.
        self._color_section = QGroupBox(tr("Color to extract"))
        self._color_section.setStyleSheet(GROUP_BOX_QSS)
        color_row = QHBoxLayout(self._color_section)
        color_row.setContentsMargins(8, 6, 8, 8)
        color_row.setSpacing(8)
        self._color_btn = QPushButton()
        self._color_btn.setToolTip(tr("Pick a color from a dialog."))
        self._color_btn.setCursor(QtC.PointingHandCursor)
        self._color_btn.setFixedSize(40, 40)
        apply_swatch_style(self._color_btn, self._color)
        self._color_btn.clicked.connect(self._on_color_clicked)
        color_row.addWidget(self._color_btn)
        # Glyph outside tr() so translators see clean text.
        self._eyedropper_btn = QPushButton("⌖ " + tr("Pick on map"))
        self._eyedropper_btn.setToolTip(
            tr("Sample a color directly from the source raster.")
        )
        self._eyedropper_btn.setCursor(QtC.PointingHandCursor)
        self._eyedropper_btn.setStyleSheet(_BTN_GHOST_QSS)
        self._eyedropper_btn.setMinimumHeight(34)
        self._eyedropper_btn.clicked.connect(self._on_eyedropper_clicked)
        color_row.addWidget(self._eyedropper_btn)
        color_row.addStretch()
        layout.addWidget(self._color_section)

        # --- Refine box (hidden until first successful vectorization) ---
        # Refining only makes sense once polygons exist, so the whole group
        # stays out of sight on cold entry and appears fully expanded after
        # the first Vectorize click.
        self._refine_group = self._build_refine_group()
        self._refine_group.setVisible(False)
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

        # Errors are transient feedback (wrong color, missed click, 0 match):
        # they auto-clear after 4 s so the panel returns to its calm resting
        # state instead of carrying a stale red line forever. Persistent
        # messages (hints, in-flight progress) never arm this timer.
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(
            lambda: self._status_label.setVisible(False)
        )

        # Action row: Exit (left, ghost) and Vectorize (right, primary blue).
        # Mirrors the Mark up panel's bottom row so the two tool panels feel
        # like siblings.
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 6, 0, 0)
        action_row.setSpacing(6)

        # Ghost "Done" to bail out before running. Hidden once a run
        # succeeds: the blue run button then relabels itself to "Done", and
        # two "Done" buttons side by side just confuse (issue #125).
        self._exit_btn = QPushButton(tr("Done"))
        self._exit_btn.setStyleSheet(_BTN_GHOST_QSS)
        self._exit_btn.setCursor(QtC.PointingHandCursor)
        self._exit_btn.setMinimumHeight(34)
        self._exit_btn.setMinimumWidth(80)
        self._exit_btn.clicked.connect(self.done_clicked.emit)
        action_row.addWidget(self._exit_btn)
        action_row.addStretch()

        self._run_btn = QPushButton(tr("Vectorize"))
        self._run_btn.setStyleSheet(_BTN_BLUE_QSS)
        self._run_btn.setCursor(QtC.PointingHandCursor)
        self._run_btn.setMinimumHeight(34)
        self._run_btn.setDefault(True)
        self._run_btn.setAutoDefault(True)
        self._run_btn.clicked.connect(self._on_run_clicked)
        action_row.addWidget(self._run_btn)
        layout.addLayout(action_row)

        # The dismissible tip at the top carries the tool description, so there
        # is no footer info box mixed in with the controls.
        layout.addStretch()

        # Esc → Done (close the panel), Enter → Run. WindowShortcut so
        # Esc fires regardless of which child has focus while the panel
        # is visible — the dock's own Escape handler bails out when the
        # main widget is hidden, so there's no conflict.
        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WindowShortcut)
        esc.activated.connect(self.done_clicked.emit)
        enter = QShortcut(QKeySequence(Qt.Key.Key_Return), self)
        enter.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        enter.activated.connect(self._on_run_clicked)
        enter2 = QShortcut(QKeySequence(Qt.Key.Key_Enter), self)
        enter2.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        enter2.activated.connect(self._on_run_clicked)

    # -- public API ------------------------------------------------------

    def activate(self) -> None:
        """Called when the panel becomes visible: reset state and refresh
        button enabled-state from the current combo selection.
        """
        self._reset_state()
        # Cascade: the user's currently active AI-Edit raster wins, then
        # the most recent AI-Edit output. Stricter predicate than the
        # combo's filter (also requires visibility) so the default never
        # falls on a hidden layer the combo wouldn't display anyway.
        preferred = pick_default_layer(_is_visible_ai_edit_output)
        if preferred is not None:
            self._layer_combo.setLayer(preferred)
        # Re-check the tip each time the panel opens so "Show again" (settings)
        # brings it back without a plugin reload.
        self._hint.setVisible(not is_hint_dismissed(HINT_VECTORIZE))
        self._refresh_panel_state()

    def preconfigure(
        self,
        layer_id: str | None = None,
        color_hex: str | None = None,
        class_label: str | None = None,
    ) -> None:
        """Pre-fill the source layer, target color, and class label before show.

        Used when the user enters the panel from the result-panel CTA so
        the swatch is already set to the template's vector_color and the
        combo is locked on the just-generated raster. class_label seeds
        the class_name attribute on every produced polygon so the table
        opens with a meaningful semantic label instead of blank rows.
        Call AFTER activate().
        """
        if color_hex:
            qc = QColor(color_hex)
            if qc.isValid():
                self._color = QColor(qc.red(), qc.green(), qc.blue())
                apply_swatch_style(self._color_btn, self._color)
        if class_label is not None:
            self._class_label = class_label
        if layer_id:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is not None:
                self._layer_combo.setLayer(layer)
                self._refresh_panel_state()

    def deactivate(self) -> None:
        """Leaving the panel: cancel any in-flight vectorize task so its
        completion callback never fires against a torn-down panel."""
        self.cancel_pending_task()

    # -- internals -------------------------------------------------------

    def _reset_state(self) -> None:
        """Wipe last-run state so the panel re-enters at Step 1."""
        self._last_layer_id = None
        self._last_raster_id = None
        self._last_target_rgb = None
        self._class_label = ""
        self._succeeded = False
        self._color = QColor(255, 0, 0)
        apply_swatch_style(self._color_btn, self._color)
        self._status_label.setVisible(False)
        self._reset_refine_spinboxes()
        self._refine_group.setVisible(False)
        self._run_btn.setText(tr("Vectorize"))
        self._exit_btn.setVisible(True)
        self._layer_group.setVisible(True)
        self._color_section.setVisible(True)
        # LayerTreeComboBox auto-refreshes via project signals; no manual
        # repopulation needed here. Combo visibility is re-asserted by
        # _refresh_panel_state right after this in activate().

    def _build_refine_group(self) -> QGroupBox:
        """Refine group - always fully expanded, no disclosure arrow.

        The whole group stays hidden until the first successful Vectorize
        click; once visible, every control is shown at once with no toggle.
        """
        group = QGroupBox(tr("Refine vectorization"))
        group.setCheckable(False)
        group.setStyleSheet(
            "QGroupBox { background-color: transparent; border: none;"
            " border-radius: 0px; margin: 0px; padding: 0px; padding-top: 20px; }"
            "QGroupBox::title { subcontrol-origin: padding;"
            " subcontrol-position: top left; padding: 2px 4px;"
            " background-color: transparent; border: none;"
            " font-weight: bold; }"
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

        def _dspin_row(parent_layout, label_text: str, tip: str,
                       lo: float, hi: float, default: float, step: float) -> QDoubleSpinBox:
            """Float spinbox for sub-integer (finer) control."""
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(label_text)
            lab.setStyleSheet("font-size: 11px; color: palette(text);")
            lab.setToolTip(tip)
            spin = QDoubleSpinBox()
            spin.setDecimals(1)
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setValue(default)
            spin.setMinimumWidth(55)
            spin.setMaximumWidth(75)
            spin.setToolTip(tip)
            row.addWidget(lab)
            row.addStretch()
            row.addWidget(spin)
            parent_layout.addLayout(row)
            return spin

        def _check_row(parent_layout, label_text: str, tip: str,
                       default: bool) -> QCheckBox:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            lab = QLabel(label_text)
            lab.setStyleSheet("font-size: 11px; color: palette(text);")
            lab.setToolTip(tip)
            chk = QCheckBox()
            chk.setChecked(default)
            chk.setToolTip(tip)
            row.addWidget(lab)
            row.addStretch()
            row.addWidget(chk)
            parent_layout.addLayout(row)
            return chk

        refine_hint = QLabel(
            "ⓘ " + tr("Adjustments re-run instantly and update the same layer.")
        )
        refine_hint.setWordWrap(True)
        refine_hint.setStyleSheet(
            "font-size: 11px; color: rgba(128,128,128,0.95);"
            " background: transparent; border: none; margin-bottom: 2px;"
        )
        content_layout.addWidget(refine_hint)

        # Detection: how the color is matched. Tolerance is per-channel (0-255),
        # so the range is bounded and every step is meaningful. The color itself
        # is re-picked in the swatch above; changing it re-runs the same way.
        content_layout.addWidget(_section(tr("Detection")))
        self._tolerance_spin = _spin_row(
            content_layout,
            tr("Color tolerance:"),
            tr(
                "How far a pixel's color can be from the picked color and still "
                "match (per channel, 0-255). Higher catches more shades."
            ),
            0, 255, 40,
        )
        self._sieve_spin = _spin_row(
            content_layout,
            tr("Remove speckle:"),
            tr("Drop connected blobs smaller than this many pixels before tracing."),
            0, 2000, 10,
        )

        content_layout.addWidget(_section(tr("Outline")))
        self._simplify_spin = _dspin_row(
            content_layout,
            tr("Simplify outline:"),
            tr("Reduce small variations in the outline (0 = no change)."),
            0.0, 50.0, 1.0, 0.5,
        )
        self._round_corners_check = _check_row(
            content_layout,
            tr("Round corners:"),
            tr(
                "Round corners for natural shapes like trees and bushes. "
                "Increase 'Simplify outline' for smoother results."
            ),
            default=False,
        )

        content_layout.addWidget(_section(tr("Selection")))
        self._expand_spin = _spin_row(
            content_layout,
            tr("Expand/Contract:"),
            tr("Positive = expand outward, Negative = shrink inward"),
            -1000, 1000, 0,
        )
        self._expand_spin.setSuffix(" px")
        self._fill_holes_check = _check_row(
            content_layout,
            tr("Fill holes:"),
            tr("Fill interior holes in the selection"),
            default=False,
        )
        self._min_pixels_spin = _spin_row(
            content_layout,
            tr("Min polygon size:"),
            tr(
                "Drop polygons smaller than this many pixels after tracing. "
                "Useful for cleaning up speckle that the sieve missed."
            ),
            0, 100000, 50,
        )
        self._min_pixels_spin.setSuffix(" px")

        outer.addWidget(content)

        # Every refine control re-runs the vectorize (debounced) on change.
        for spin in (
            self._tolerance_spin,
            self._sieve_spin,
            self._simplify_spin,
            self._expand_spin,
            self._min_pixels_spin,
        ):
            spin.valueChanged.connect(self._on_refine_changed)
        for chk in (self._round_corners_check, self._fill_holes_check):
            chk.stateChanged.connect(self._on_refine_changed)

        return group

    def _on_refine_changed(self, _value=None) -> None:
        if self._last_layer_id is None:
            return
        # 300 ms gives the user time to settle on a value when rapidly
        # arrowing through a spinbox; 150 ms used to re-trigger vectorize
        # mid-keystroke and made the panel feel sluggish.
        self._refine_timer.start(300)

    def _reset_refine_spinboxes(self) -> None:
        for spin, default in (
            (self._tolerance_spin, 40),
            (self._sieve_spin, 10),
            (self._simplify_spin, 1),
            (self._expand_spin, 0),
            (self._min_pixels_spin, 50),
        ):
            spin.blockSignals(True)
            spin.setValue(default)
            spin.blockSignals(False)
        for chk, default in (
            (self._round_corners_check, False),
            (self._fill_holes_check, False),
        ):
            chk.blockSignals(True)
            chk.setChecked(default)
            chk.blockSignals(False)

    def _refresh_panel_state(self, *_args) -> None:
        """Enable the Vectorize button when the combo's current layer is
        a multi-band RGB raster with a real on-disk source.

        The combo is filtered to AI Edit outputs only; when the project has
        none, swap the combo for the empty-state hint and disable Vectorize.
        """
        if self._succeeded:
            # Refine mode: the layer + color are locked and their pickers are
            # hidden. Bail so a project-signal refresh can't re-show the combo
            # or fight the Done button's enabled state.
            return
        has_any_output = self._layer_combo.count_layers() > 0
        self._empty_state_label.setVisible(not has_any_output)
        self._layer_combo.setVisible(has_any_output)

        if not has_any_output:
            self._show_status("", is_error=False)
            self._run_btn.setEnabled(False)
            return

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
        else:
            self._show_status(
                tr("Pick an AI Edit output to vectorize."),
                is_error=False,
                is_hint=True,
            )
            self._run_btn.setEnabled(False)

    def _on_color_clicked(self) -> None:
        chosen = QColorDialog.getColor(self._color, self, tr("Pick color"))
        if not chosen.isValid():
            return
        self._color = QColor(chosen.red(), chosen.green(), chosen.blue())
        apply_swatch_style(self._color_btn, self._color)
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

    def _on_run_clicked(self) -> None:
        if self._busy:
            return

        # After success, button reads "Done" and exits; refine spinboxes still re-run.
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
        # Use the CURRENT swatch color so re-picking the color (or the eyedropper)
        # re-runs detection with the new target, not the originally locked one.
        target_rgb = (self._color.red(), self._color.green(), self._color.blue())
        self._run_vectorize(raster, target_rgb, is_initial=False)

    def _run_vectorize(
        self,
        raster: QgsRasterLayer,
        target_rgb: tuple[int, int, int],
        is_initial: bool,
    ) -> None:
        layer_name = f"{raster.name()} (vector)"

        # Supersede any in-flight run (e.g. a debounced refine tick) so the
        # latest parameters win and runs never overlap.
        self.cancel_pending_task()

        # Capture all QgsProject / main-thread context NOW. The heavy compute
        # runs on a worker thread and must not touch QgsProject or the layer.
        project = QgsProject.instance()
        compute_kwargs = {
            "raster_path": (raster.source() or "").split("|", 1)[0],
            "raster_crs": raster.crs(),
            "transform_context": project.transformContext(),
            "ellipsoid": project.ellipsoid() or "EPSG:7030",
            "target_rgb": target_rgb,
            "tolerance": int(self._tolerance_spin.value()),
            "sieve_threshold": int(self._sieve_spin.value()),
            "min_pixels": int(self._min_pixels_spin.value()),
            "simplify_factor": float(self._simplify_spin.value()),
            "round_corners": bool(self._round_corners_check.isChecked()),
            "expand_value": int(self._expand_spin.value()),
            "fill_holes": bool(self._fill_holes_check.isChecked()),
            "class_label": self._class_label,
        }
        params = {
            "raster_id": raster.id(),
            "raster_name": raster.name() or "",
            "raster_crs": raster.crs(),
            "target_rgb": target_rgb,
            "is_initial": is_initial,
            "layer_name": layer_name,
            "tolerance": compute_kwargs["tolerance"],
            "sieve_threshold": compute_kwargs["sieve_threshold"],
            "simplify_factor": compute_kwargs["simplify_factor"],
            "round_corners": compute_kwargs["round_corners"],
            "expand_value": compute_kwargs["expand_value"],
            "fill_holes": compute_kwargs["fill_holes"],
        }

        # Only flip the action row into a "running" state on the initial click.
        # Debounced refine re-runs keep the Done button + status line intact so
        # spinbox ticks don't flicker the panel.
        if is_initial:
            self._busy = True
            self._run_btn.setEnabled(False)
            self._color_btn.setEnabled(False)
            self._run_btn.setText(tr("Vectorizing..."))
            self._show_status(
                tr("Vectorizing “{name}”...").format(name=raster.name()),
                is_error=False,
            )

        from qgis.core import QgsApplication

        from ...workers.vectorize_task import VectorizeTask

        task = VectorizeTask(compute_kwargs, params)
        task.succeeded.connect(self._on_vectorize_succeeded)
        task.failed.connect(self._on_vectorize_failed)
        self._vectorize_task = task
        QgsApplication.taskManager().addTask(task)

    def cancel_pending_task(self) -> None:
        """Cancel any in-flight vectorize task (new run, panel exit, teardown)."""
        task = self._vectorize_task
        self._vectorize_task = None
        if task is not None:
            try:
                if task.is_active():
                    task.cancel()
            except RuntimeError:
                pass

    def _on_vectorize_succeeded(self, feats, params) -> None:
        """Main thread: build the layer from the computed features, then place
        it and update the panel. Layer/project work must stay on this thread."""
        self._vectorize_task = None
        is_initial = params["is_initial"]
        try:
            from ...core.generation.vectorization_service import _build_vector_layer

            new_layer = _build_vector_layer(
                feats, params["raster_crs"], params["layer_name"],
                params["target_rgb"], None, self._class_label,
                source_raster_name=params.get("raster_name", ""),
            )

            previous_id = self._last_layer_id
            existing = (
                QgsProject.instance().mapLayer(previous_id) if previous_id else None
            )
            if existing is None:
                # First run: swap the volatile memory layer for a GeoPackage
                # table so the result survives the QGIS session. Falls back to
                # the memory layer if the write fails.
                persisted = self._persist_layer(new_layer, params)
                if persisted is not None:
                    new_layer = persisted
            if existing is not None:
                # Re-run: transplant the new geometries into the existing layer
                # so the user's symbology, name and layer id all survive.
                provider = existing.dataProvider()
                old_ids = [f.id() for f in existing.getFeatures()]
                if old_ids:
                    provider.deleteFeatures(old_ids)
                fresh_feats = [QgsFeature(f) for f in new_layer.getFeatures()]
                provider.addFeatures(fresh_feats)
                if existing.providerType() == "ogr":
                    # Provider edits went straight to the GeoPackage; re-read
                    # so feature count and ids reflect the file.
                    existing.reload()
                existing.updateExtents()
                # Re-pick of a different colour: restyle so the trace matches the
                # new selection. Same colour keeps the user's symbology untouched.
                if params["target_rgb"] != self._last_target_rgb:
                    from ...core.generation.vectorization_service import (
                        _apply_style,
                        _set_layer_provenance,
                    )
                    _apply_style(existing, params["target_rgb"])
                    _set_layer_provenance(
                        existing, params.get("raster_name", ""),
                        params["target_rgb"], self._class_label,
                    )
                existing.triggerRepaint()
                final_layer = existing
            else:
                QgsProject.instance().addMapLayer(new_layer, False)
                # Lazily promote the source raster into its own sub-group on the
                # first vectorization, then drop the vector layer alongside it.
                raster_id = params["raster_id"]
                subgroup = find_generation_subgroup_for_layer(raster_id)
                if subgroup is None:
                    subgroup = promote_layer_to_own_subgroup(raster_id)
                if subgroup is not None:
                    subgroup.insertLayer(0, new_layer)
                else:
                    add_layer_to_ai_edit_top(new_layer)
                final_layer = new_layer

            # Hide the source raster so the freshly traced polygons read clearly
            # on top. With the vector now in the picked colour, leaving the raster
            # visible underneath would make the trace hard to see against it.
            raster_id = params["raster_id"]
            if raster_id:
                node = QgsProject.instance().layerTreeRoot().findLayer(raster_id)
                if node is not None:
                    node.setItemVisibilityChecked(False)

            self._last_layer_id = final_layer.id()
            self._last_raster_id = params["raster_id"]
            self._last_target_rgb = params["target_rgb"]

            polygon_count = final_layer.featureCount()
            self._show_status(
                "✓ " + tr("{n} polygons added").format(n=polygon_count),
                is_error=False,
                is_success=True,
            )
            self._succeeded = True
            if is_initial:
                self._activate_layer_in_panel(final_layer)
                self._refine_group.setVisible(True)
                self._run_btn.setText(tr("Done"))
                self._exit_btn.setVisible(False)
                # Color section stays visible so the user can re-pick the color
                # / tolerance and refine without restarting. Only the source
                # layer is locked in (you refine one raster at a time).
                self._layer_group.setVisible(False)
            telemetry.track(
                "vectorize_completed",
                {
                    "polygon_count": polygon_count,
                    "tolerance": params["tolerance"],
                    "sieve": params["sieve_threshold"],
                    "simplify": int(params["simplify_factor"]),
                    "round_corners": params["round_corners"],
                    "expand": params["expand_value"],
                    "fill_holes": params["fill_holes"],
                    "is_initial": is_initial,
                },
            )
        except AIEditError as err:
            self._handle_run_error(err.message, err.code)
        except Exception as e:
            self._handle_run_error(str(e), None)
        finally:
            self._reset_button()

    def _persist_layer(self, mem_layer, params):
        """One GeoPackage next to the generated rasters, one table per run
        (lowercase ASCII names per the GeoPackage spec)."""
        import time

        from ...core.generation.vectorization_service import make_layer_permanent
        from ...core.slug import slugify
        from ..raster_writer import get_output_dir

        base = slugify(self._class_label or params.get("raster_name", ""))[:40] or "result"
        table_name = f"vectorize_{base}_{time.strftime('%Y%m%d_%H%M%S')}"
        return make_layer_permanent(
            mem_layer,
            os.path.join(get_output_dir(), "ai_edit.gpkg"),
            table_name,
            params["target_rgb"],
            self._class_label,
            params.get("raster_name", ""),
        )

    def _on_vectorize_failed(self, message: str, code: str) -> None:
        self._vectorize_task = None
        from ...core.errors import ErrorCode as _EC

        code_enum = None
        if code:
            try:
                code_enum = _EC(code)
            except ValueError:
                code_enum = None
        self._handle_run_error(message, code_enum)
        self._reset_button()

    def _handle_run_error(self, message: str, code=None) -> None:
        """Render a friendlier error and steer the user to the lever that
        usually fixes it. ``code`` lets us branch without parsing English
        substrings (replaced lower().contains check).
        """
        from ...core.errors import ErrorCode as _EC
        is_zero_match = code == _EC.NO_PIXELS_MATCHED
        if is_zero_match and self._succeeded:
            # Active refine: detection is fixed, so a re-run only zeroes out
            # when the outline/selection filters drop everything. Steer the
            # user to the lever that usually did it - min polygon size.
            self._show_status(
                tr(
                    "No shapes left after filtering. Lower 'Min polygon "
                    "size' below."
                ),
                is_error=True,
            )
            self._refine_group.setVisible(True)
            self._min_pixels_spin.setFocus()
        elif is_zero_match:
            # Cold 0-match: nothing was vectorized yet, so the refine knobs
            # would be editing polygons that don't exist. Keep them hidden
            # and steer the user back to the color picker (their recovery
            # path stays visible above). Showing 8 dead controls here just
            # confuses (issue #164).
            self._refine_group.setVisible(False)
            self._show_status(
                tr(
                    "0 matches. Pick a closer color above, or use "
                    "Pick on map to sample one from the raster."
                ),
                is_error=True,
            )
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
        self,
        message: str,
        is_error: bool,
        is_success: bool = False,
        is_hint: bool = False,
    ) -> None:
        if is_error:
            color = ERROR_TEXT
        elif is_success:
            color = SUCCESS_TEXT
        elif is_hint:
            # Use palette(text) so the hint stays legible on both light and
            # dark QGIS themes; the smaller font-size handles the visual
            # distinction from error / success messages.
            color = "palette(text)"
        else:
            color = "palette(text)"
        self._status_label.setStyleSheet(
            f"font-size: 11px; color: {color}; background: transparent;"
            " border: none; padding: 2px 0 0 0;"
        )
        self._status_label.setText(message)
        self._status_label.setVisible(bool(message))
        # Auto-dismiss errors after 4 s; cancel any pending dismiss when a
        # non-error (or empty) message takes over so it isn't hidden early.
        if message and is_error:
            self._status_timer.start(4000)
        else:
            self._status_timer.stop()
