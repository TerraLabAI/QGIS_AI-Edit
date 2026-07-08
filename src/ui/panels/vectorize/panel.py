











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

_DONE_MIN_PX = 96


_REFINE_DEBOUNCE_MS = 300


class VectorizePanel(ColorControlsMixin, RefineUiMixin, RunLifecycleMixin, QWidget):


    done_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)



        self._classes_raster_id: str | None = None


        self._pixel_client = None
        self._pixel_auth_manager = None
        self._classes_task = None

        self._pinned_class = None
        self._busy = False
        self._succeeded = False
        self._vectorize_task = None
        self._last_layer_id: str | None = None
        self._last_raster_id: str | None = None


        self._last_signature: tuple | None = None


        self._run_settings: dict | None = None
        self._good_settings: dict | None = None
        self._eyedropper_tool: EyedropperMapTool | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(T.SPACE_STAGE)






        setup_line = get_export_copy(
            "widgets.panel.hint_description",
            tr("Turns a flat-color map into polygons, one class per color."),
        )
        self._header = build_panel_header(
            get_export_copy("widgets.panel.panel_title", tr("Vectorize")),
            subtitle=setup_line,
            show_close=False,
        )



        self._subtitle = next(
            (lbl for lbl in self._header.findChildren(QLabel) if lbl.text() == setup_line),
            None,
        )
        self._setup_line = setup_line
        layout.addWidget(self._header)



        self._layer_group = PanelSection(get_export_copy("widgets.panel.layer_group_title", tr("Layer")))
        layer_box = self._layer_group.body
        self._layer_combo = LayerTreeComboBox()
        self._layer_combo.setStyleSheet(combo_box_qss())
        self._layer_combo.setToolTip(
            get_export_copy("widgets.panel.pick_layer_hint", tr("Pick an AI Edit output to vectorize."))
        )


        self._layer_combo.set_include_hidden(True)
        self._layer_combo.set_layer_filter(_is_ai_edit_output)

        self._layer_combo.set_view_tracking(False)
        self._layer_combo.layerChanged.connect(self._on_layer_picked)
        layer_box.addWidget(self._layer_combo)
        layout.addWidget(self._layer_group)





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





        self._classes_group = PanelSection(get_export_copy("widgets.panel.classes_group_title", tr("Classes")))
        classes_box = self._classes_group.body

        self._class_list = ClassListWidget()
        self._class_list.classes_changed.connect(self._sync_run_enabled)
        classes_box.addWidget(self._class_list)



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





        self._refine_group = self._build_refine_group()
        self._refine_group.setVisible(False)
        layout.addWidget(self._refine_group)


        self._refine_timer = QTimer(self)
        self._refine_timer.setSingleShot(True)
        self._refine_timer.timeout.connect(self._on_refine_apply)





        self._status_label = PanelStatusLine()
        layout.addWidget(self._status_label)





        self._run_btn = QPushButton(get_export_copy("widgets.panel.run_button_label", tr("Vectorize")))
        self._run_btn.setStyleSheet(_BTN_PRIMARY_WIDE_QSS)
        self._run_btn.setCursor(QtC.PointingHandCursor)
        self._run_btn.setAutoDefault(False)
        self._run_btn.clicked.connect(self._on_run_clicked)
        layout.addWidget(self._run_btn)



        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(T.SPACE_CARD)



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

        apply_panel_input_theme(self)
        self._apply_page()







        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            enter = QShortcut(QKeySequence(key), self)
            enter.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            enter.activated.connect(self._on_enter_pressed)



    def activate(self) -> None:



        self._reset_state()




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







        if layer_id:
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is not None:
                self._layer_combo.setLayer(layer)
                self._refresh_panel_state()
        if color_hex and not self._succeeded and self._classes_raster_id is not None:
            qc = QColor(color_hex)
            if qc.isValid():
                rgb = (qc.red(), qc.green(), qc.blue())

                self._pinned_class = (rgb, class_label or "")
                self._class_list.ensure_class(rgb, class_label or "")


                self._show_class_rows(True)

    def deactivate(self) -> None:







        self._refine_timer.stop()
        was_busy = self._busy
        self.cancel_pending_task()
        self.cancel_eyedropper()
        if was_busy:



            self._reset_button()



    def _eyedropper_icon(self) -> QIcon:

        ratio = widget_pixel_ratio(self)
        icon = QIcon(icon_for(self, "plus", _BUTTON_GLYPH_PX, qcolor(T.INK)))
        icon.addPixmap(
            render_pixmap("check", qcolor(T.category_ink(T.PICKED_HUE)), _BUTTON_GLYPH_PX, ratio),
            QIcon.Mode.Normal, QIcon.State.On,
        )
        return icon

    def _page(self) -> str:

        if self._succeeded:
            return "refine"
        if self._layer_combo.count_layers() <= 0:
            return "empty"
        return "setup"

    def _apply_page(self) -> None:

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


            self._subtitle.setVisible(page != "empty")
            self._subtitle.setText(
                get_export_copy(
                    "widgets.panel.refine_line",
                    tr("Each change updates the same layer."),
                )
                if refine
                else self._setup_line
            )





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




        self._layer_group.setEnabled(not locked)
        self._eyedropper_btn.setEnabled(not locked)
        if locked:
            self.cancel_eyedropper()

    def _reset_state(self) -> None:

        self._last_layer_id = None
        self._last_raster_id = None
        self._last_signature = None
        self._run_settings = None
        self._good_settings = None
        self._succeeded = False

        self._classes_raster_id = None
        self._pinned_class = None
        self._status_label.set_message("", "info")
        self._reset_refine_spinboxes()
        self._set_setup_locked(False)
        self._run_btn.setText(get_export_copy("widgets.panel.run_button_label", tr("Vectorize")))
        self._apply_page()



    def _on_done_clicked(self) -> None:
        self.done_clicked.emit()

    def _on_enter_pressed(self) -> None:
        if self._busy:
            return
        if self._succeeded:

            focus = QApplication.focusWidget()
            if isinstance(focus, QAbstractSpinBox):
                focus.interpretText()
            if self._refine_timer.isActive():
                self._refine_timer.stop()
                self._on_refine_apply()
            return
        self._on_run_clicked()

    def _on_layer_picked(self, *_args) -> None:


        self.cancel_eyedropper()
        self._refresh_panel_state()

    def _on_back_clicked(self) -> None:



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



        self._refine_timer.start(
            get_export_dial("widgets.panel.refine_debounce_ms", _REFINE_DEBOUNCE_MS)
        )

    def _refresh_panel_state(self, *_args) -> None:






        if self._succeeded:


            return
        self._apply_page()
        if self._page() == "empty":
            self._clear_class_list()
            self._show_status("", is_error=False)
            self._run_btn.setEnabled(False)
            return

        layer = self._layer_combo.currentLayer()



        if layer is not None and self._last_raster_id and layer.id() != self._last_raster_id:
            self._last_layer_id = None
            self._last_raster_id = None
            self._last_signature = None

        if self._is_usable_raster(layer):
            if self._status_label.kind() == "hint" and not self._eyedropper_tool:
                self._show_status("", is_error=False)
            self._rebuild_class_list(layer)
        else:


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
