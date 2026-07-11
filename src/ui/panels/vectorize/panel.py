"""Vectorize panel widget.

Self-contained QWidget that runs the color-based raster-to-polygon
workflow: detect the map's flat colors as classes, trace every selected
class in one click, then refine live. Manages its own active-layer
tracking, refine debounce, and busy state.
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
from ...panel_helpers import GROUP_BOX_QSS, build_panel_header
from ...tools.eyedropper_tool import EyedropperMapTool
from .class_list import ClassListWidget
from .color_controls import ColorControlsMixin
from .layer_filters import _is_ai_edit_output, _is_visible_ai_edit_output
from .refine_ui import RefineUiMixin
from .run_lifecycle import RunLifecycleMixin
from .style import _BTN_BLUE_QSS, _BTN_GHOST_QSS, ERROR_TEXT, SUCCESS_TEXT


class VectorizePanel(ColorControlsMixin, RefineUiMixin, RunLifecycleMixin, QWidget):
    """Class-based raster-to-polygon workflow. Refine box re-runs via debounce."""

    done_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Colors detected for the current raster live in the class list; this
        # id gates re-detection so project signals don't re-run it needlessly.
        self._classes_raster_id: str | None = None
        self._busy = False
        self._succeeded = False
        self._vectorize_task = None
        self._last_layer_id: str | None = None
        self._last_raster_id: str | None = None
        # Snapshot of the traced classes for the last run; a differing
        # signature on refine means the style must be rebuilt.
        self._last_signature: tuple | None = None
        self._eyedropper_tool: EyedropperMapTool | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(build_panel_header(tr("Vectorize")))

        # Dismissible tip at the top (same pattern as Mark up / the prompt
        # library): the "what does this do / when to use it" explanation lives
        # here, kept out of the controls. Restorable from Account Settings;
        # activate() re-checks its state.
        self._hint = DismissibleHint(
            HINT_VECTORIZE,
            "",
            tr("Vectorize turns a flat-color map (Segment, Land cover, masks, "
               "site plans...) into editable polygons - one class per color, "
               "ready to select, measure, style and export. It reads colors, "
               "so it works on colored maps, not photo-realistic images."),
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
        # Hidden outputs stay listed: vectorizing hides the source raster, and
        # the user must still be able to re-vectorize that very result.
        self._layer_combo.set_include_hidden(True)
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

        # Detected classes: one row per flat color found in the map, all real
        # classes pre-checked so the primary flow is a single click on
        # Vectorize. Unchecked rows (background) still absorb their pixels so
        # traced classes never bleed.
        self._classes_group = QGroupBox(tr("Classes"))
        self._classes_group.setStyleSheet(GROUP_BOX_QSS)
        classes_box = QVBoxLayout(self._classes_group)
        classes_box.setContentsMargins(8, 6, 8, 8)
        classes_box.setSpacing(6)

        self._classes_intro = QLabel(
            tr("Colors detected in this map - each checked one becomes "
               "a polygon class:")
        )
        self._classes_intro.setWordWrap(True)
        self._classes_intro.setStyleSheet(
            "font-size: 11px; color: palette(text); background: transparent;"
            " border: none;"
        )
        classes_box.addWidget(self._classes_intro)

        self._class_list = ClassListWidget()
        classes_box.addWidget(self._class_list)

        # Photo-realistic input: no flat palette to offer. Explain instead of
        # showing an empty list; the eyedropper below stays as the power path.
        self._photo_hint = QLabel(
            tr("No flat color classes found - this image looks "
               "photo-realistic. Vectorize works best on maps with solid "
               "colors (Segment or Land cover results). You can still sample "
               "a color below.")
        )
        self._photo_hint.setWordWrap(True)
        self._photo_hint.setStyleSheet(
            "font-size: 11px; color: palette(text); background: transparent;"
            " border: none;"
        )
        self._photo_hint.setVisible(False)
        classes_box.addWidget(self._photo_hint)

        eyedropper_row = QHBoxLayout()
        eyedropper_row.setContentsMargins(0, 0, 0, 0)
        eyedropper_row.setSpacing(8)
        # Glyph outside tr() so translators see clean text.
        self._eyedropper_btn = QPushButton("⌖ " + tr("Add color from map"))
        self._eyedropper_btn.setToolTip(
            tr("Sample a color directly from the source raster and add it "
               "as a class.")
        )
        self._eyedropper_btn.setCursor(QtC.PointingHandCursor)
        self._eyedropper_btn.setStyleSheet(_BTN_GHOST_QSS)
        self._eyedropper_btn.setMinimumHeight(30)
        self._eyedropper_btn.clicked.connect(self._on_eyedropper_clicked)
        eyedropper_row.addWidget(self._eyedropper_btn)
        eyedropper_row.addStretch()
        classes_box.addLayout(eyedropper_row)
        layout.addWidget(self._classes_group)

        # --- Refine box (hidden until first successful vectorization) ---
        # Refining only makes sense once polygons exist, so the whole group
        # stays out of sight on cold entry and appears fully expanded after
        # the first Vectorize click.
        self._refine_group = self._build_refine_group()
        self._refine_group.setVisible(False)
        layout.addWidget(self._refine_group)

        # Debounce so dragging a spinbox doesn't fire 60 vectorizations.
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

        # Refine state's way back: swaps the panel to the setup page (layer +
        # classes) without touching the traced layer. Glyph outside tr().
        self._back_btn = QPushButton("‹ " + tr("Edit classes"))
        self._back_btn.setStyleSheet(_BTN_GHOST_QSS)
        self._back_btn.setCursor(QtC.PointingHandCursor)
        self._back_btn.setMinimumHeight(34)
        self._back_btn.setToolTip(
            tr("Go back to the class list to check, rename or recolor "
               "classes, then vectorize again.")
        )
        self._back_btn.clicked.connect(self._on_back_clicked)
        self._back_btn.setVisible(False)
        action_row.addWidget(self._back_btn)
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
        """Pre-fill the source layer and highlight the template's class.

        Used when the user enters the panel from the result-panel CTA: the
        combo is locked on the just-generated raster and the template's
        vector_color is made sure to sit checked in the class list, carrying
        the template's class label. Call AFTER activate().
        """
        if layer_id:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is not None:
                self._layer_combo.setLayer(layer)
                self._refresh_panel_state()
        if color_hex:
            qc = QColor(color_hex)
            if qc.isValid():
                self._class_list.ensure_class(
                    (qc.red(), qc.green(), qc.blue()), class_label or ""
                )

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
        self._last_signature = None
        self._succeeded = False
        # Force the class list to rebuild for whatever raster is picked next.
        self._classes_raster_id = None
        self._status_label.setVisible(False)
        self._reset_refine_spinboxes()
        self._refine_group.setVisible(False)
        self._run_btn.setText(tr("Vectorize"))
        self._exit_btn.setVisible(True)
        self._back_btn.setVisible(False)
        self._layer_group.setVisible(True)
        self._classes_group.setVisible(True)
        # LayerTreeComboBox auto-refreshes via project signals; no manual
        # repopulation needed here. Combo visibility is re-asserted by
        # _refresh_panel_state right after this in activate().

    def _on_back_clicked(self) -> None:
        """Refine -> setup: re-show the layer + class page so the user can
        reshape the selection, then vectorize again. The traced layer stays on
        the map; the next run on the same raster updates it in place."""
        self._refine_timer.stop()
        self.cancel_pending_task()
        self._succeeded = False
        self._refine_group.setVisible(False)
        self._layer_group.setVisible(True)
        self._classes_group.setVisible(True)
        self._hint.setVisible(not is_hint_dismissed(HINT_VECTORIZE))
        self._run_btn.setText(tr("Vectorize"))
        self._exit_btn.setVisible(True)
        self._back_btn.setVisible(False)
        self._status_label.setVisible(False)
        self._refresh_panel_state()

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
            # Refine mode: the layer + classes are locked and their pickers are
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
        # Back then a different raster picked: forget the previous run so the
        # next Vectorize starts a fresh layer instead of transplanting this
        # raster's polygons into the other raster's result.
        if layer is not None and self._last_raster_id and layer.id() != self._last_raster_id:
            self._last_layer_id = None
            self._last_raster_id = None
            self._last_signature = None
        is_raster = isinstance(layer, QgsRasterLayer)
        has_file_source = is_raster and bool(layer.source()) and os.path.exists(layer.source())
        is_valid = is_raster and layer.bandCount() >= 3 and has_file_source

        if is_valid:
            self._show_status("", is_error=False)
            self._run_btn.setEnabled(not self._busy)
            self._rebuild_class_list(layer)
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
