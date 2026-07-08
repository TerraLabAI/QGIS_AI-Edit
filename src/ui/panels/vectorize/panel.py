"""Vectorize panel widget.

Self-contained QWidget that runs the color-based raster-to-polygon
workflow. Manages its own active-layer tracking, refine debounce, and
busy state.
"""
from __future__ import annotations

import os

from qgis.core import QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QKeySequence, QShortcut
from qgis.PyQt.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.i18n import tr
from ...layer_groups import pick_default_layer
from ...layer_tree_combobox import LayerTreeComboBox
from ...onboarding_hint import HINT_VECTORIZE, DismissibleHint, is_hint_dismissed
from ...panel_helpers import (
    GROUP_BOX_QSS,
    apply_swatch_style,
    build_panel_header,
)
from ...tools.eyedropper_tool import EyedropperMapTool
from .color_controls import ColorControlsMixin
from .layer_filters import _is_ai_edit_output, _is_visible_ai_edit_output
from .refine_ui import RefineUiMixin
from .run_lifecycle import RunLifecycleMixin
from .style import _BTN_BLUE_QSS, _BTN_GHOST_QSS, ERROR_TEXT, SUCCESS_TEXT


class VectorizePanel(ColorControlsMixin, RefineUiMixin, RunLifecycleMixin, QWidget):
    """Color-based raster-to-polygon workflow. Refine box re-runs via debounce."""

    done_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Snap the swatch back to the canonical template red (#FF0000).
        # Every Segment & Vectorize template paints classes in that exact
        # hue, so it's the right starting point on every fresh open.
        self._color = QColor(255, 0, 0)
        # The other dominant color, treated as background to discard. Each pixel
        # is assigned to whichever of (picked color, background) it is closer to,
        # so the class boundary stays clean. Defaults to white (the canonical
        # "class in color, everything else white" map); a palette-chip click
        # repoints it at the actual other dominant color.
        self._background: tuple[int, int, int] = (255, 255, 255)
        # Dominant colors detected in the selected raster, for the one-click
        # extract chips: [((r,g,b), fraction), ...]. Rebuilt per raster.
        self._palette: list = []
        self._palette_raster_id: str | None = None
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
        color_outer = QVBoxLayout(self._color_section)
        color_outer.setContentsMargins(8, 6, 8, 8)
        color_outer.setSpacing(8)

        # One-click chips of the colors actually present in the map. Click the
        # one you want (e.g. the red buildings) and it vectorizes straight away;
        # the other dominant color becomes the discarded background. Populated
        # per raster by _rebuild_palette_chips; hidden when none are detected.
        self._palette_label = QLabel(tr("Colors in this map - click one to extract:"))
        self._palette_label.setStyleSheet(
            "font-size: 11px; color: palette(text); background: transparent;"
            " border: none;"
        )
        self._palette_label.setVisible(False)
        color_outer.addWidget(self._palette_label)
        self._palette_row = QHBoxLayout()
        self._palette_row.setContentsMargins(0, 0, 0, 0)
        self._palette_row.setSpacing(6)
        self._palette_chips: list[QPushButton] = []
        color_outer.addLayout(self._palette_row)

        # Manual fallback: exact swatch + eyedropper, for a color the chips missed.
        manual_row = QHBoxLayout()
        manual_row.setContentsMargins(0, 0, 0, 0)
        manual_row.setSpacing(8)
        self._color_btn = QPushButton()
        self._color_btn.setToolTip(tr("Pick a color from a dialog."))
        self._color_btn.setCursor(QtC.PointingHandCursor)
        self._color_btn.setFixedSize(40, 40)
        apply_swatch_style(self._color_btn, self._color)
        self._color_btn.clicked.connect(self._on_color_clicked)
        manual_row.addWidget(self._color_btn)
        # Glyph outside tr() so translators see clean text.
        self._eyedropper_btn = QPushButton("⌖ " + tr("Pick on map"))
        self._eyedropper_btn.setToolTip(
            tr("Sample a color directly from the source raster.")
        )
        self._eyedropper_btn.setCursor(QtC.PointingHandCursor)
        self._eyedropper_btn.setStyleSheet(_BTN_GHOST_QSS)
        self._eyedropper_btn.setMinimumHeight(34)
        self._eyedropper_btn.clicked.connect(self._on_eyedropper_clicked)
        manual_row.addWidget(self._eyedropper_btn)
        manual_row.addStretch()
        color_outer.addLayout(manual_row)
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
        """Leaving the panel: stop the pending refine debounce and cancel any
        in-flight vectorize task so no callback fires after the user left."""
        self._refine_timer.stop()
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
        self._background = (255, 255, 255)
        # Force the chips to rebuild for whatever raster is picked next.
        self._palette_raster_id = None
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

    def _on_refine_changed(self, _value=None) -> None:
        if self._last_layer_id is None:
            return
        # 300 ms gives the user time to settle on a value when rapidly
        # arrowing through a spinbox; 150 ms used to re-trigger vectorize
        # mid-keystroke and made the panel feel sluggish.
        self._refine_timer.start(300)

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
        has_file_source = is_raster and bool(layer.source()) and os.path.exists(layer.source())
        is_valid = is_raster and layer.bandCount() >= 3 and has_file_source

        if is_valid:
            self._show_status("", is_error=False)
            self._run_btn.setEnabled(not self._busy)
            self._rebuild_palette_chips(layer)
        else:
            self._show_status(
                tr("Pick an AI Edit output to vectorize."),
                is_error=False,
                is_hint=True,
            )
            self._run_btn.setEnabled(False)

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
