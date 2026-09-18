"""Vectorize panel widget.

Self-contained QWidget that runs the color-based raster-to-polygon
workflow: detect the map's flat colors as classes, trace every selected
class in one click, then refine live. Manages its own active-layer
tracking, refine debounce, and busy state.

Three pages share one shape with Draw and References: the title with one
muted line, the content, the status line, then the action row whose Done
(bottom right) is the one way out. The setup page adds the wide green
Vectorize above that row; the refine page promotes Done to the primary.
"""
from __future__ import annotations

import os

from qgis.core import QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon, QKeySequence
from qgis.PyQt.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....core import qt_compat as QtC
from ....core.config_store import get_export_copy, get_export_dial
from ....core.i18n import tr
from ....core.qt_compat import QShortcut
from ...dialogs.prompt_templates.library_empty_state import build_library_empty_state
from ...dock import design_tokens as T
from ...dock.design_tokens import qcolor
from ...icons import icon_for, render_pixmap, widget_pixel_ratio
from ...layer_groups import pick_default_layer
from ...layer_tree_combobox import LayerTreeComboBox
from ...panel_helpers import (
    PanelSection,
    PanelStatusLine,
    apply_panel_input_theme,
    build_panel_header,
    combo_box_qss,
    make_notice_card,
)
from ...tools.eyedropper_tool import EyedropperMapTool
from .class_list import ClassListWidget
from .color_controls import ColorControlsMixin
from .layer_filters import _is_ai_edit_output, _is_visible_ai_edit_output
from .refine_ui import RefineUiMixin
from .run_lifecycle import RunLifecycleMixin
from .style import (
    _BTN_DONE_GHOST_QSS,
    _BTN_DONE_PRIMARY_QSS,
    _BTN_PICK_QSS,
    _BTN_PRIMARY_WIDE_QSS,
    _BTN_QUIET_QSS,
)

_BUTTON_GLYPH_PX = 14
# Done keeps one width on both pages, like Draw's and References' Done.
_DONE_MIN_PX = 96

# Debounce so dragging a refine spinbox doesn't fire a vectorize per tick.
_REFINE_DEBOUNCE_MS = 300


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
        # Refine settings of the run in flight and of the last run that traced
        # shapes: an emptied re-run names the setting that differs.
        self._run_settings: dict | None = None
        self._good_settings: dict | None = None
        self._eyedropper_tool: EyedropperMapTool | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_STAGE)

        # Title and one muted line, the Draw / References head. No close glyph:
        # it sat right under the dock header's own close X, and Done (bottom
        # right) is this panel's one way out on every page. The muted line
        # replaces the closeable tip card, which said the same thing in a
        # second box (and a third time over the class list).
        setup_line = get_export_copy(
            "widgets.panel.hint_description",
            tr("Turns a flat-color map into polygons, one class per color."),
        )
        self._header = build_panel_header(
            get_export_copy("widgets.panel.panel_title", tr("Vectorize")),
            subtitle=setup_line,
            show_close=False,
        )
        # The muted line says what the page in front of the user does: the
        # refine page used to keep the setup sentence and add a second muted
        # line inside its card.
        self._subtitle = next(
            (lbl for lbl in self._header.findChildren(QLabel) if lbl.text() == setup_line),
            None,
        )
        self._setup_line = setup_line
        layout.addWidget(self._header)

        # Layer picker. The combo and the empty state swap; the whole group
        # hides once a vectorization succeeds (refine mode locks the layer).
        self._layer_group = PanelSection(get_export_copy("widgets.panel.layer_group_title", tr("Layer")))
        layer_box = self._layer_group.body
        self._layer_combo = LayerTreeComboBox()
        self._layer_combo.setStyleSheet(combo_box_qss())
        self._layer_combo.setToolTip(
            get_export_copy("widgets.panel.pick_layer_hint", tr("Pick an AI Edit output to vectorize."))
        )
        # Hidden outputs stay listed: vectorizing hides the source raster, and
        # the user must still be able to re-vectorize that very result.
        self._layer_combo.set_include_hidden(True)
        self._layer_combo.set_layer_filter(_is_ai_edit_output)
        # A pan must not swap the output being traced under the user.
        self._layer_combo.set_view_tracking(False)
        self._layer_combo.layerChanged.connect(self._on_layer_picked)
        layer_box.addWidget(self._layer_combo)
        layout.addWidget(self._layer_group)

        # No AI Edit output in the project: the setup page gives way to one
        # centred empty state. Done below stays the way out, as on the
        # References empty state; a "Create a map" link that only closed the
        # panel promised something it did not do.
        self._empty_state = build_library_empty_state(
            get_export_copy("widgets.panel.empty_state_title", tr("No map to vectorize yet")),
            get_export_copy(
                "widgets.panel.empty_state_hint_generate",
                tr("Generate a flat-color map first, then come back."),
            ),
            [],
            top_margin=T.SPACE_STAGE * 3,
            glyph="polygon",
        )
        self._empty_state.setVisible(False)
        layout.addWidget(self._empty_state)

        # Detected classes: one row per flat color found in the map, all real
        # classes pre-checked so the primary flow is a single click on
        # Vectorize. Unchecked rows (background) still absorb their pixels so
        # traced classes never bleed.
        self._classes_group = PanelSection(get_export_copy("widgets.panel.classes_group_title", tr("Classes")))
        classes_box = self._classes_group.body

        self._class_list = ClassListWidget()
        self._class_list.classes_changed.connect(self._sync_run_enabled)
        classes_box.addWidget(self._class_list)

        # Photo-realistic input: no flat palette to offer. Explain instead of
        # showing an empty list; the eyedropper below stays as the power path.
        self._photo_hint = make_notice_card(
            get_export_copy(
                "widgets.panel.photo_hint",
                tr("This looks like a photo. Pick a color below."),
            ),
            glyph="image",
        )
        self._photo_hint.setVisible(False)
        classes_box.addWidget(self._photo_hint)

        eyedropper_row = QHBoxLayout()
        eyedropper_row.setContentsMargins(0, 0, 0, 0)
        eyedropper_row.setSpacing(T.SPACE_OUTER)
        self._eyedropper_btn = QPushButton(
            get_export_copy("widgets.panel.eyedropper_button_label", tr("Add color from map"))
        )
        # Armed, the button takes the shared picked look (a soft green tint,
        # a green border, a check) until the click lands or Esc cancels; a
        # second press disarms it.
        self._eyedropper_btn.setCheckable(True)
        self._eyedropper_btn.setIcon(self._eyedropper_icon())
        self._eyedropper_btn.setToolTip(
            get_export_copy(
                "widgets.panel.eyedropper_button_tip_map",
                tr("Click a color on the map to add it as a class."),
            )
        )
        self._eyedropper_btn.setCursor(QtC.PointingHandCursor)
        self._eyedropper_btn.setStyleSheet(_BTN_PICK_QSS)
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

        # Status line: a glyph and words, red for a failure, green for a result.
        # Errors stay until the next status replaces them: they used to clear
        # after 4 s, which took the explanation away from the 16 users a month
        # whose vectorize returns nothing usable, right when they were reading it.
        self._status_label = PanelStatusLine()
        layout.addWidget(self._status_label)

        # Primary of the setup page: the full-width green Vectorize, the only
        # filled button on that page, mirroring AI Segmentation's review
        # Export button so the finish line is unmistakable. Grey while no
        # class is checked. The refine page hides it and promotes Done.
        self._run_btn = QPushButton(get_export_copy("widgets.panel.run_button_label", tr("Vectorize")))
        self._run_btn.setStyleSheet(_BTN_PRIMARY_WIDE_QSS)
        self._run_btn.setCursor(QtC.PointingHandCursor)
        self._run_btn.setAutoDefault(False)
        self._run_btn.clicked.connect(self._on_run_clicked)
        layout.addWidget(self._run_btn)

        # The action row, Draw's and References' shape: the way back on the
        # left (refine page only), Done on the right.
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(T.SPACE_CARD)

        # Refine page's way back: swaps the panel to the setup page (layer +
        # classes) without touching the traced layer.
        self._back_btn = QPushButton(
            get_export_copy("widgets.panel.back_button_label", tr("Edit classes"))
        )
        self._back_btn.setIcon(icon_for(self, "chevron_left", _BUTTON_GLYPH_PX, qcolor(T.INK_2)))
        self._back_btn.setStyleSheet(_BTN_QUIET_QSS)
        self._back_btn.setCursor(QtC.PointingHandCursor)
        self._back_btn.setToolTip(
            get_export_copy(
                "widgets.panel.back_button_tip_short",
                tr("Back to the classes: check, rename or recolor, then vectorize again."),
            )
        )
        self._back_btn.clicked.connect(self._on_back_clicked)
        self._back_btn.setVisible(False)
        action_row.addWidget(self._back_btn)
        action_row.addStretch(1)

        self._done_btn = QPushButton(get_export_copy("widgets.panel.done_button", tr("Done")))
        self._done_btn.setCursor(QtC.PointingHandCursor)
        self._done_btn.setAutoDefault(False)
        self._done_btn.setMinimumWidth(_DONE_MIN_PX)
        self._done_btn.clicked.connect(self._on_done_clicked)
        action_row.addWidget(self._done_btn)
        layout.addLayout(action_row)

        layout.addStretch()
        # Spin boxes and anything else left unstyled take the house shape.
        apply_panel_input_theme(self)
        self._apply_page()

        # Enter while focus is in the panel: Vectorize on the setup page,
        # "apply now" on the refine page (a value typed into a refine box
        # used to close the panel, because Enter pressed Finish). Esc means
        # Done and comes from the dock, which owns the Escape shortcut and
        # disables its own Return / Enter while this panel shows: two live
        # shortcuts on one key are ambiguous and neither fires.
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            enter = QShortcut(QKeySequence(key), self)
            enter.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            enter.activated.connect(self._on_enter_pressed)

    # -- public API ------------------------------------------------------

    def activate(self) -> None:
        """Called when the panel becomes visible: reset state and refresh
        button enabled-state from the current combo selection.
        """
        self._reset_state()
        # Cascade: the most recent visible AI Edit output, then the user's
        # active layer when it is one (``pick_default_layer``). Stricter
        # predicate than the combo's filter (also requires visibility) so the
        # default never falls on a hidden layer.
        preferred = pick_default_layer(_is_visible_ai_edit_output)
        if preferred is not None:
            self._layer_combo.setLayer(preferred)
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
        if color_hex and not self._succeeded and self._classes_raster_id is not None:
            qc = QColor(color_hex)
            if qc.isValid():
                self._class_list.ensure_class(
                    (qc.red(), qc.green(), qc.blue()), class_label or ""
                )
                # The template's class is a real row now: show the list even
                # when detection alone called the map a photo.
                self._show_class_rows(True)

    def deactivate(self) -> None:
        """Leaving the panel: stop the pending refine debounce, cancel any
        in-flight vectorize task so no callback fires after the user left,
        and disarm the eyedropper so it never swallows the next map click.

        This is the one choke point every exit path already calls (Done,
        Escape, a sibling panel taking over, the dock closing, unload), so
        the eyedropper never needs a second teardown hook of its own."""
        self._refine_timer.stop()
        was_busy = self._busy
        self.cancel_pending_task()
        self.cancel_eyedropper()
        if was_busy:
            # The cancelled task's signals are cut, so nothing else would
            # clear the busy flag: Vectorize stayed grey on "Vectorizing..."
            # for the rest of the session after leaving mid-run.
            self._reset_button()

    # -- internals -------------------------------------------------------

    def _eyedropper_icon(self) -> QIcon:
        """A plus at rest, the picked check in the picked hue while armed."""
        ratio = widget_pixel_ratio(self)
        icon = QIcon(icon_for(self, "plus", _BUTTON_GLYPH_PX, qcolor(T.INK)))
        icon.addPixmap(
            render_pixmap("check", qcolor(T.category_ink(T.PICKED_HUE)), _BUTTON_GLYPH_PX, ratio),
            QIcon.Mode.Normal, QIcon.State.On,
        )
        return icon

    def _page(self) -> str:
        """Which of the three pages shows: empty, setup or refine."""
        if self._succeeded:
            return "refine"
        if self._layer_combo.count_layers() <= 0:
            return "empty"
        return "setup"

    def _apply_page(self) -> None:
        """One place sets what each page shows, so no path forgets a piece."""
        page = self._page()
        setup = page == "setup"
        refine = page == "refine"
        self._empty_state.setVisible(page == "empty")
        self._layer_group.setVisible(setup)
        self._classes_group.setVisible(setup)
        self._run_btn.setVisible(setup)
        self._refine_group.setVisible(refine)
        self._back_btn.setVisible(refine)
        if self._subtitle is not None:
            # The empty state carries its own title and line; a third
            # sentence above them said the same thing again.
            self._subtitle.setVisible(page != "empty")
            self._subtitle.setText(
                get_export_copy(
                    "widgets.panel.refine_line",
                    tr("Each change updates the same layer."),
                )
                if refine
                else self._setup_line
            )
        # Done is the one way out everywhere. It is the primary once the
        # polygons exist (the work is done); next to the green Vectorize it
        # steps back to an outline so the page keeps one filled button.
        # On the empty page Done is the only way forward, so it takes the
        # green like Draw's Done instead of standing as a lone outline.
        self._done_btn.setStyleSheet(_BTN_DONE_GHOST_QSS if setup else _BTN_DONE_PRIMARY_QSS)
        self._done_btn.setToolTip(
            get_export_copy(
                "widgets.panel.done_tooltip_refine",
                tr("Keep the polygons on your map and close this panel"),
            )
            if refine
            else get_export_copy("widgets.panel.done_tooltip_setup", tr("Close Vectorize"))
        )

    def _sync_run_enabled(self) -> None:
        """Vectorize is grey while it cannot run: busy, no usable map, or
        no class checked (it used to stay green and answer with an error)."""
        layer = self._layer_combo.currentLayer()
        self._run_btn.setEnabled(
            not self._busy
            and self._is_usable_raster(layer)
            and self._class_list.has_checked_class()
        )

    @staticmethod
    def _is_usable_raster(layer) -> bool:
        is_raster = isinstance(layer, QgsRasterLayer)
        has_file_source = is_raster and bool(layer.source()) and os.path.exists(
            (layer.source() or "").split("|", 1)[0]
        )
        return bool(is_raster and layer.bandCount() >= 3 and has_file_source)

    def _set_setup_locked(self, locked: bool) -> None:
        """While a first run traces, the map it reads stays put: a new pick
        mid-run was silently ignored by that run, and an eyedropper armed on
        it sampled a map that was no longer the one being traced. The class
        rows stay live (greying them would grey their colours too)."""
        self._layer_group.setEnabled(not locked)
        self._eyedropper_btn.setEnabled(not locked)
        if locked:
            self.cancel_eyedropper()

    def _reset_state(self) -> None:
        """Wipe last-run state so the panel re-enters at Step 1."""
        self._last_layer_id = None
        self._last_raster_id = None
        self._last_signature = None
        self._run_settings = None
        self._good_settings = None
        self._succeeded = False
        # Force the class list to rebuild for whatever raster is picked next.
        self._classes_raster_id = None
        self._status_label.set_message("", "info")
        self._reset_refine_spinboxes()
        self._set_setup_locked(False)
        self._run_btn.setText(get_export_copy("widgets.panel.run_button_label", tr("Vectorize")))
        self._apply_page()
        # LayerTreeComboBox auto-refreshes via project signals; no manual
        # repopulation needed here.

    def _on_done_clicked(self) -> None:
        self.done_clicked.emit()

    def _on_enter_pressed(self) -> None:
        if self._busy:
            return
        if self._succeeded:
            # Commit a typed refine value now instead of after the debounce.
            focus = QApplication.focusWidget()
            if isinstance(focus, QAbstractSpinBox):
                focus.interpretText()
            if self._refine_timer.isActive():
                self._refine_timer.stop()
                self._on_refine_apply()
            return
        self._on_run_clicked()

    def _on_layer_picked(self, *_args) -> None:
        # The eyedropper samples the raster it was armed on: a new pick
        # would otherwise add a color read from the previous map.
        self.cancel_eyedropper()
        self._refresh_panel_state()

    def _on_back_clicked(self) -> None:
        """Refine -> setup: re-show the layer + class page so the user can
        reshape the selection, then vectorize again. The traced layer stays on
        the map; the next run on the same raster updates it in place."""
        self._refine_timer.stop()
        self.cancel_pending_task()
        self._succeeded = False
        self._busy = False
        self._run_btn.setText(get_export_copy("widgets.panel.run_button_label", tr("Vectorize")))
        self._status_label.set_message("", "info")
        self._apply_page()
        self._refresh_panel_state()

    def _on_refine_changed(self, _value=None) -> None:
        if self._last_layer_id is None:
            return
        # 300 ms gives the user time to settle on a value when rapidly
        # arrowing through a spinbox; 150 ms used to re-trigger vectorize
        # mid-keystroke and made the panel feel sluggish.
        self._refine_timer.start(
            get_export_dial("widgets.panel.refine_debounce_ms", _REFINE_DEBOUNCE_MS)
        )

    def _refresh_panel_state(self, *_args) -> None:
        """Enable the Vectorize button when the combo's current layer is
        a multi-band RGB raster with a real on-disk source.

        The combo is filtered to AI Edit outputs only; when the project has
        none, the page gives way to the empty state.
        """
        if self._succeeded:
            # Refine mode: the layer + classes are locked and their pickers are
            # hidden. Bail so a project-signal refresh can't re-show the combo.
            return
        self._apply_page()
        if self._page() == "empty":
            self._clear_class_list()
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

        if self._is_usable_raster(layer):
            if self._status_label.kind() == "hint" and not self._eyedropper_tool:
                self._show_status("", is_error=False)
            self._rebuild_class_list(layer)
        else:
            # A layer whose file is gone (or not RGB) keeps no classes from
            # the map picked before it.
            self._clear_class_list()
            self._show_status(
                get_export_copy(
                    "widgets.panel.pick_usable_map",
                    tr("This map can't be read. Pick another one."),
                ),
                is_error=False,
                is_hint=True,
            )
        self._sync_run_enabled()

    def _show_status(
        self,
        message: str,
        is_error: bool,
        is_success: bool = False,
        is_hint: bool = False,
    ) -> None:
        if is_error:
            kind = "error"
        elif is_success:
            kind = "success"
        else:
            kind = "hint" if is_hint else "info"
        self._status_label.set_message(message, kind)
