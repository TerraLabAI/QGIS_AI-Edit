from __future__ import annotations

from qgis.core import QgsLayerTree, QgsProject, QgsRasterLayer
from qgis.PyQt.QtGui import QColor

from ...core.auth.activation_manager import get_tutorial_url
from ...core.i18n import tr
from ..dialogs.error_report_dialog import (
    REPORT_PROBLEM_HREF,
    show_error_report,
)
from ..external_url import open_external
from ..icons import icon_for
from ..layers_panel import show_layers_panel
from .blocked_reasons import (
    LAUNCH_BLOCK_NO_KEY,
    LAUNCH_BLOCK_NO_RASTER,
    LAUNCH_BLOCK_TILES_WARMING,
)
from .design_tokens import (
    INK,
    INK_2,
    INK_3,
    qcolor,
    repolish_widget,
)
from .tool_bar import set_compare_icon, tool_available


def _tool_panel_is_open(dock) -> bool:

    checker = getattr(dock, "_tool_panel_open", None)
    if callable(checker):
        if bool(checker()):
            return True
    for name in ("_markup_panel", "_vectorize_panel", "_reference_panel"):
        panel = getattr(dock, name, None)
        try:
            if panel is not None and panel.isVisible():
                return True
        except RuntimeError:
            continue
    return False


def tree_has_visible_raster(node) -> bool:








    for child in node.children():
        if not child.itemVisibilityChecked():
            continue
        if QgsLayerTree.isLayer(child):
            if isinstance(child.layer(), QgsRasterLayer):
                return True
        elif QgsLayerTree.isGroup(child) and tree_has_visible_raster(child):
            return True
    return False


class DockToolsFooterMixin:



    def _schedule_layer_warning_update(self, *_args):








        self._layer_warning_timer.start(0)

    def _run_layer_warning_update(self) -> None:


        if not self.isVisible():
            self._layer_warning_dirty = True
            return
        self._layer_warning_dirty = False
        self._update_layer_warning()

    def _update_layer_warning(self, *_args):



















        if self._progress_widget.isVisible():
            return



        has_visible = tree_has_visible_raster(QgsProject.instance().layerTreeRoot())
        if not has_visible:




            if _tool_panel_is_open(self):
                self._layer_warning_dirty = True
                return




            keep_prompt = self._zone_selected or getattr(self, "_prompt_kept_for_blank_map", False)
            kept_text = self._prompt_input.toPlainText() if keep_prompt else ""
            kept_template = (self._active_template_id, self._active_template_name)




            self.set_launch_state()
            if kept_text:
                self._prompt_input.blockSignals(True)
                try:
                    self._prompt_input.setPlainText(kept_text)
                finally:
                    self._prompt_input.blockSignals(False)
                self._active_template_id, self._active_template_name = kept_template
            self._prompt_kept_for_blank_map = bool(kept_text)
            self._warning_widget.setVisible(True)
            self._sync_demo_button()



            self._sync_warning_actions()


            self.set_launch_block_reason(
                LAUNCH_BLOCK_NO_RASTER, has_own_card=True
            )
            self._past_sessions_link.setVisible(False)
            if getattr(self, "_launch_hero", None) is not None:
                self._launch_hero.setVisible(False)


            if getattr(self, "_layer_header", None) is not None:
                self._layer_header.setVisible(False)
        else:
            if getattr(self, "_layer_header", None) is not None:
                self._layer_header.setVisible(True)
            self._prompt_kept_for_blank_map = False
            self._warning_widget.setVisible(False)


            if getattr(self, "_launch_account_pending", False):
                reason = LAUNCH_BLOCK_NO_KEY
            elif self._imagery_loading:
                reason = LAUNCH_BLOCK_TILES_WARMING
            else:
                reason = None
            self.set_launch_block_reason(reason)
            self._past_sessions_link.setVisible(True)
            if getattr(self, "_launch_hero", None) is not None:
                self._launch_hero.setVisible(True)

    def _tool_panel_open(self) -> bool:

        for name in ("_markup_panel", "_vectorize_panel", "_reference_panel"):
            panel = getattr(self, name, None)
            try:
                if panel is not None and not panel.isHidden():
                    return True
            except RuntimeError:
                continue
        return False

    def _on_project_loaded(self, *_args):







        try:
            QgsProject.instance().layerTreeRoot().visibilityChanged.disconnect(
                self._schedule_layer_warning_update
            )
        except (TypeError, RuntimeError):
            pass
        QgsProject.instance().layerTreeRoot().visibilityChanged.connect(
            self._schedule_layer_warning_update
        )
        self._schedule_layer_warning_update()

    def _on_open_tutorial(self):

        open_external(get_tutorial_url())

    def _on_layer_saved_link_clicked(self, _link: str) -> None:

        layer_id = self._saved_layer_id
        if not layer_id:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return
        try:
            from qgis.utils import iface
        except ImportError:
            return
        if iface is None:
            return
        iface.setActiveLayer(layer)
        tree_view = iface.layerTreeView()
        if tree_view is None:
            return


        show_layers_panel(tree_view)
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer_id) if root is not None else None
        if node is None:
            return
        model = tree_view.layerTreeModel()
        if model is None:
            return
        index = model.node2index(node)
        tree_view.setCurrentIndex(index)
        tree_view.scrollTo(index)







    def _hide_reference_panel(self) -> None:


        if getattr(self, "_reference_panel", None) is not None:
            self._reference_panel.setVisible(False)

    def set_markup_state(self) -> None:

        self._stop_progress_animation()
        self._hide_status_box()
        self._vectorize_panel.deactivate()
        self._main_widget.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._hide_reference_panel()
        self._markup_panel.setVisible(True)
        self._markup_panel.activate()
        self._refresh_dock_key_shortcuts()

    def set_vectorize_state(self) -> None:

        self._stop_progress_animation()
        self._hide_status_box()
        self._main_widget.setVisible(False)
        self._markup_panel.setVisible(False)
        self._hide_reference_panel()
        self._vectorize_panel.setVisible(True)
        self._vectorize_panel.activate()
        self._vectorize_btn.set_active(True)


        self._swipe_panel_lock = True
        self._refresh_swipe_enabled()
        self._refresh_dock_key_shortcuts()

    def set_reference_capture_armed(self, armed: bool) -> None:


        panel = getattr(self, "_reference_panel", None)
        if panel is not None:
            panel.set_capture_armed(armed)

    def set_reference_state(self) -> None:



        if getattr(self, "_reference_panel", None) is None:
            return
        self._stop_progress_animation()
        self._hide_status_box()
        self._vectorize_panel.deactivate()
        self._main_widget.setVisible(False)
        self._markup_panel.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._reference_panel.setVisible(True)
        self._reference_panel.activate()
        self._refresh_dock_key_shortcuts()

    def exit_tool_panel(self) -> None:

        self._vectorize_panel.deactivate()
        self._markup_panel.setVisible(False)
        self._vectorize_panel.setVisible(False)
        self._hide_reference_panel()
        self._main_widget.setVisible(True)
        self._vectorize_btn.set_active(False)
        self._swipe_panel_lock = False
        self._refresh_swipe_enabled()
        self._refresh_dock_key_shortcuts()


        if self._layer_warning_dirty:
            self._schedule_layer_warning_update()

    def set_swipe_button_checked(self, checked: bool) -> None:







        if self._swipe_btn.isChecked() == checked:
            return
        self._swipe_btn.blockSignals(True)
        try:
            self._swipe_btn.setChecked(checked)
        finally:
            self._swipe_btn.blockSignals(False)
        self._sync_result_tools_row()

    def set_swipe_button_enabled(self, can_swipe: bool) -> None:






        self._swipe_eligible = can_swipe
        self._refresh_swipe_enabled()

    def _refresh_swipe_enabled(self) -> None:
        is_checked = self._swipe_btn.isChecked()
        enabled = (self._swipe_eligible or is_checked) and not self._swipe_panel_lock
        self._swipe_btn.setEnabled(enabled)
        self._sync_result_tools_row()

    def _on_result_compare_toggled(self, checked: bool) -> None:





        if self._swipe_btn.isChecked() != checked:
            self._swipe_btn.setChecked(checked)

    def _sync_result_tools_row(self) -> None:




        compare = getattr(self, "_result_compare_btn", None)
        vectorize = getattr(self, "_result_vectorize_btn", None)
        row = getattr(self, "_result_tools_row", None)
        if compare is None or vectorize is None or row is None:
            return
        try:
            swipe_on = tool_available(self._swipe_btn)
            vectorize_on = tool_available(self._vectorize_btn)
            compare.setVisible(swipe_on)
            compare.setEnabled(self._swipe_btn.isEnabled())
            checked = self._swipe_btn.isChecked()
            if compare.isChecked() != checked:
                compare.blockSignals(True)
                try:
                    compare.setChecked(checked)
                finally:
                    compare.blockSignals(False)
            set_compare_icon(compare)
            vectorize.setVisible(vectorize_on)
            row.setVisible(swipe_on or vectorize_on)
        except RuntimeError:
            pass

    def set_settings_button_active(self, active: bool) -> None:



        self._settings_btn.set_active(active)

    def _set_swipe_button_visible(self, visible: bool) -> None:












        self._swipe_btn.setVisible(visible and self._activated)

    def set_markup_annotation_count(self, count: int) -> None:
        self._markup_panel.set_annotation_count(count)

    def set_markup_zone_present(self, has_zone: bool) -> None:
        self._markup_panel.set_zone_present(has_zone)

    def get_markup_color(self) -> QColor:
        return self._markup_panel.get_color()

    def set_markup_tool_unchecked(self, tool_key: str) -> None:







        self._markup_panel.uncheck_tool(tool_key)

    def set_vectorize_suggestion(
        self,
        layer_id: str | None,
        color_hex: str | None,
        class_label: str = "",
        detected_colors: list[str] | None = None,
        trigger: str = "",
    ) -> None:











        from ...core.auth.activation_manager import is_feature_enabled



        if not layer_id or not color_hex or not is_feature_enabled("vectorize"):
            self._clear_vectorize_suggestion()
            return

        qc = QColor(color_hex)
        if not qc.isValid():
            self._clear_vectorize_suggestion()
            return
        normalised = qc.name().upper()


        finding = ""
        if trigger == "flat_output":
            n_zones = sum(
                1 for c in (detected_colors or [normalised]) if QColor(c).isValid()
            ) or 1
            finding = (
                tr("{n} color zone detected in this result").format(n=n_zones)
                if n_zones == 1
                else tr("{n} color zones detected in this result").format(n=n_zones)
            )
        self._vectorize_cta_pending = (
            layer_id, normalised, class_label or "", trigger or ""
        )
        self._mark_result_vectorize_button(True, finding)

    def _clear_vectorize_suggestion(self) -> None:

        self._vectorize_cta_pending = None
        self._mark_result_vectorize_button(False, "")

    def _mark_result_vectorize_button(self, suggested: bool, finding: str) -> None:


        button = getattr(self, "_result_vectorize_btn", None)
        if button is None:
            return
        plain_tip = getattr(self, "_result_vectorize_tip", "") or button.toolTip()
        try:
            button.setToolTip(f"{finding}. {plain_tip}" if finding else plain_tip)
            if bool(button.property("suggested")) != suggested:
                button.setProperty("suggested", suggested)
                repolish_widget(button)
            button.setIcon(
                icon_for(button, "polygon", 16, qcolor(INK if suggested else INK_2),
                         disabled_color=qcolor(INK_3))
            )
        except RuntimeError:
            pass

    def _on_result_vectorize_clicked(self) -> None:


        pending = getattr(self, "_vectorize_cta_pending", None)
        if pending is None:
            self.vectorize_clicked.emit()
            return
        layer_id, color_hex, class_label, trigger = pending
        self.vectorize_suggestion_clicked.emit(layer_id, color_hex, class_label, trigger)

    def arm_report_context(self, request_id: str = "") -> None:


        self._pending_report_request_id = request_id or ""

    def _on_status_link(self, href: str) -> None:


        if href == REPORT_PROBLEM_HREF:
            show_error_report(
                self._main_window_for_dialog(),
                request_id=getattr(self, "_pending_report_request_id", "") or "",
            )
            return
        open_external(href)
