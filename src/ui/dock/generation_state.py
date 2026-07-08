from __future__ import annotations

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QTextCursor

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import has_seen_privacy_notice
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.paywall_state import classify_paywall_state
from ...core.pro_ceiling import pro_ceiling_enabled, pro_low_threshold
from ...core.prompts.prompt_presets import format_template_prompt
from ...core.resolution_labels import DEFAULT_RESOLUTION_CREDIT_COSTS
from ..icons import pixmap_for
from ..onboarding_hint import (
    HINT_GUIDE_AI,
    dismiss_hint,
    is_hint_dismissed,
)
from . import design_tokens as tokens


_FOCUS_RETRY_MS = 50

_PREP_TICKER_MS = 1300

_PROGRESS_ANIMATE_MS = 30

_PREP_CAP_CANVAS_PCT = 5

_PREP_CAP_OTHER_PCT = 10


class DockGenerationStateMixin:




    def set_reference_target_extent(self, extent, crs) -> None:


        if self._reference_widget is not None:
            self._reference_widget.set_target_extent(extent, crs)

    def selected_input_layer(self):


        combo = getattr(self, "_layer_combo", None)
        if combo is None:
            return None
        try:
            return combo.currentLayer()
        except RuntimeError:
            return None

    def _place_layer_header(self, target: str) -> None:




        header = getattr(self, "_layer_header", None)
        launch_layout = getattr(self, "_launch_layout", None)
        if header is None or launch_layout is None:
            return
        if target == "launch":
            if launch_layout.indexOf(header) >= 0:
                return
            self._main_layout.removeWidget(header)
            launch_layout.insertWidget(launch_layout.indexOf(self._launch_hero) + 1, header)
            return
        if self._main_layout.indexOf(header) >= 0:
            return
        launch_layout.removeWidget(header)
        self._main_layout.insertWidget(self._main_layout.indexOf(self._select_zone_section), header)

    def _set_layer_header_state(self, state: str) -> None:






        header = getattr(self, "_layer_header", None)
        combo = getattr(self, "_layer_combo", None)
        if header is None or combo is None:
            return
        from ..panel_helpers import combo_box_qss, locked_combo_qss

        self._place_layer_header("launch" if state == "launch" else "flow")
        locked = state == "locked"
        combo.set_frozen(locked)
        combo.set_view_tracking(not locked)
        combo.setEnabled(not locked)
        qss = locked_combo_qss() if locked else combo_box_qss()

        if getattr(self, "_layer_combo_qss", None) != qss:
            self._layer_combo_qss = qss
            combo.setStyleSheet(qss)
        combo.setToolTip(
            get_export_copy("dock.generation_state.layer_combo_locked_tooltip", tr("Exit to pick another layer."))
            if locked else get_export_copy(
                "dock.build.layer_combo_above_tooltip",
                tr("The layer the AI edits. Visible layers above it are sent as references."),
            )
        )
        self._layer_label.setVisible(not locked)
        from qgis.core import QgsProject

        from .tools_footer import tree_has_visible_raster

        header.setVisible(tree_has_visible_raster(QgsProject.instance().layerTreeRoot()))

    def set_zone_selected(self):

        self._zone_selected = True
        self._hide_status_box()
        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._result_section.setVisible(False)
        self._set_layer_header_state("locked")
        self._prompt_section.setVisible(True)
        self._prompt_container.set_readonly(False)
        self._place_reference_widget("prompt")
        self._generate_note_box.setVisible(not has_seen_privacy_notice())
        self._generate_btn.setVisible(True)
        self._exit_btn.setVisible(True)
        self._refresh_resolution_triggers()
        self._update_generate_enabled()
        self._update_generate_button_text()
        self._update_guide_ai_tip()





        QtC.safe_single_shot(0, self, self._focus_prompt_input)
        QtC.safe_single_shot(
            get_export_dial("dock.generation_state.focus_retry_ms", _FOCUS_RETRY_MS), self, self._focus_prompt_input
        )

    def _focus_prompt_input(self):

        self.raise_()
        self.activateWindow()
        self._prompt_input.setFocus(QtC.OtherFocusReason)
        cursor = self._prompt_input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._prompt_input.setTextCursor(cursor)

    def hide_privacy_notice(self):


        self._generate_note_box.setVisible(False)

    def set_zone_cleared(self):







        self.set_reference_target_extent(None, None)
        self.set_selecting_zone_state()

    def _stop_progress_animation(self):





        self._stop_prep_ticker()
        if hasattr(self, "_progress_timer") and self._progress_timer is not None:
            self._progress_timer.stop()
        loader = getattr(self, "_progress_loader", None)
        if loader is not None:
            loader.stop_run()

    def _guide_ai_tip_visible(self) -> bool:





        if is_hint_dismissed(HINT_GUIDE_AI):
            return False
        try:
            return not self._progress_widget.isVisibleTo(self)
        except (RuntimeError, AttributeError):
            return True

    def _mark_guide_ai_touched(self) -> None:





        if is_hint_dismissed(HINT_GUIDE_AI):
            return
        hint = getattr(self, "_guide_ai_hint", None)
        if hint is not None:
            hint.hide()
        dismiss_hint(HINT_GUIDE_AI)

    def _on_guide_ai_dismissed(self) -> None:
        telemetry.track(te.GUIDANCE_TIP_DISMISSED)

    def _update_guide_ai_tip(self) -> None:



        hint = getattr(self, "_guide_ai_hint", None)
        if hint is None:
            return
        if is_hint_dismissed(HINT_GUIDE_AI):
            hint.hide()
            return
        hint.show()
        telemetry.track(te.GUIDANCE_TIP_SHOWN)

    def _set_guide_ai_tip_held(self, held: bool) -> None:


        hint = getattr(self, "_guide_ai_hint", None)
        if hint is None:
            return
        hint.setVisible(False if held else self._guide_ai_tip_visible())

    def set_launch_state(self):





        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False


        self._imagery_loading = False

        if self._reference_widget is not None:
            self._reference_widget.clear()
            self._reference_widget.setVisible(False)

        self._launch_section.setVisible(True)
        self._set_layer_header_state("launch")
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._progress_widget.setVisible(False)
        self._result_section.setVisible(False)




        self._place_version_strip("launch")
        self._version_strip.set_readonly(False)
        self._generate_note_box.setVisible(False)
        self._generate_btn.setVisible(False)
        self.set_generate_block_reason(None)
        self._exit_btn.setVisible(False)

        self._prompt_container.set_readonly(False)
        self._prompt_input.clear()
        self._prompt_input.setFixedHeight(60)
        self._result_prompt_input.clear()
        self._active_template_id = None
        self._active_template_name = None






        if self.isVisible() and (
            self._library_history_dirty or not self._library_history_loaded
        ):
            self.conversations_refresh_requested.emit()

    def clear_active_template(self) -> None:


        self._active_template_id = None
        self._active_template_name = None



        self._refresh_resolution_triggers()
        self._update_layer_warning()



        if self._is_free_tier_exhausted() and self._trial_info_url:
            self._trial_info_box.setVisible(True)
        elif (self._is_free_tier_prewall() or self._is_pro_low()) and self._prewall_url:
            self._prewall_banner.setVisible(True)

    def set_zone_step_notice(self, text: str = "") -> None:

        label = getattr(self, "_select_zone_notice", None)
        if label is None:
            return
        label.setText(text)
        label.setVisible(bool(text))

    def _scroll_panel_to_top(self) -> None:





        area = getattr(self, "_scroll_area", None)
        if area is None:
            return
        try:
            area.verticalScrollBar().setValue(0)
        except (RuntimeError, AttributeError):
            pass

    def set_selecting_zone_state(self):





        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False


        self._imagery_loading = False

        if self._reference_widget is not None:
            self._reference_widget.setVisible(False)

        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(True)
        self._set_layer_header_state("editable")
        self._prompt_section.setVisible(False)
        self._progress_widget.setVisible(False)
        self._result_section.setVisible(False)
        self._generate_note_box.setVisible(False)
        self._generate_btn.setVisible(False)
        self.set_generate_block_reason(None)

        self._exit_btn.setVisible(False)


        self.refresh_zone_sources()
        self.set_zone_step_notice()
        self._update_layer_warning()

        self._scroll_panel_to_top()

    def set_generating(self, generating: bool):







        if generating:
            self._hide_after_success()
        self.setUpdatesEnabled(False)
        try:
            self._progress_widget.setVisible(generating)
            self._result_section.setVisible(False)
            self._warning_widget.setVisible(False)

            self.sync_update_banner()

            if generating:
                self._progress_bar.setRange(0, 100)



                self._progress_bar.setValue(1)
                self._progress_target = 1
                self._hide_status_box()
                self._launch_section.setVisible(False)
                self._select_zone_section.setVisible(False)
                self._prompt_section.setVisible(True)
                self._prompt_container.set_readonly(True)



                self._place_reference_widget("prompt")
                if self._reference_widget is not None:
                    self._reference_widget.set_readonly(True)


                if getattr(self, "_reference_panel", None) is not None:
                    self._reference_panel.set_readonly(True)


                self._place_version_strip("generating")
                self._version_strip.set_readonly(True)
                self._generate_note_box.setVisible(False)


                self._set_guide_ai_tip_held(True)
                self._generate_btn.setVisible(False)
                self.set_generate_block_reason(None)



                self._exit_btn.setVisible(False)
                self._progress_loader.start_run()
                self._start_prep_ticker("canvas")


                self._scroll_panel_to_top()
            else:
                self._stop_progress_animation()
                self._prompt_container.set_readonly(False)
                if self._reference_widget is not None:
                    self._reference_widget.set_readonly(False)
                if getattr(self, "_reference_panel", None) is not None:
                    self._reference_panel.set_readonly(False)
                self._refresh_resolution_triggers()
                if self._version_strip.count() > 1:




                    self._show_result_layout()
                else:
                    self._generate_note_box.setVisible(not has_seen_privacy_notice())
                    self._generate_btn.setVisible(True)
                    self._exit_btn.setVisible(True)
                    self._prompt_section.setVisible(True)
                    self._set_guide_ai_tip_held(False)


                    self._place_version_strip("result")
                    self._version_strip.set_readonly(False)
        finally:
            self.setUpdatesEnabled(True)

    def _show_result_layout(self) -> None:



        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        self.set_generate_block_reason(None)
        self._exit_btn.setVisible(False)
        self._generate_note_box.setVisible(False)
        self._set_guide_ai_tip_held(True)
        self._result_section.setVisible(True)
        self._result_prompt_widget.setVisible(True)
        self._result_prompt_container.set_readonly(False)
        self._place_version_strip("result")
        self._version_strip.set_readonly(False)
        self._place_reference_widget("result")
        self._update_result_generate_enabled()





    def _start_prep_ticker(self, phase: str) -> None:
        self._prep_phase = phase
        if phase == "canvas":
            text = tr("Capturing your zone...")
        else:

            text = get_export_copy("dock.generation_state.preparing", tr("Preparing..."))
        self._progress_loader.set_phase_text(text)
        if not hasattr(self, "_prep_ticker") or self._prep_ticker is None:
            self._prep_ticker = QTimer(self)
            self._prep_ticker.setInterval(get_export_dial("dock.generation_state.prep_ticker_ms", _PREP_TICKER_MS))
            self._prep_ticker.timeout.connect(self._tick_prep)
        if not self._prep_ticker.isActive():
            self._prep_ticker.start()

    def _stop_prep_ticker(self) -> None:
        if hasattr(self, "_prep_ticker") and self._prep_ticker is not None and self._prep_ticker.isActive():
            self._prep_ticker.stop()

    def prep_advance_phase(self, phase: str) -> None:



        if not hasattr(self, "_prep_ticker") or self._prep_ticker is None or not self._prep_ticker.isActive():
            return
        self._start_prep_ticker(phase)

    def _tick_prep(self) -> None:



        cap = (
            _PREP_CAP_CANVAS_PCT
            if self._prep_phase == "canvas"
            else _PREP_CAP_OTHER_PCT
        )
        current = self._progress_bar.value()
        if current < cap:
            self._progress_target = min(cap, current + 1)
            if not hasattr(self, "_progress_timer") or self._progress_timer is None:
                self._progress_timer = QTimer(self)
                self._progress_timer.setInterval(
                    get_export_dial("dock.generation_state.progress_animate_ms", _PROGRESS_ANIMATE_MS)
                )
                self._progress_timer.timeout.connect(self._animate_progress)
            if not self._progress_timer.isActive():
                self._progress_timer.start()

    def set_progress_message(self, message: str, percentage: int = -1):



        self._stop_prep_ticker()
        self._resume_prep_ticker_on_show = False
        self._progress_loader.set_phase_text(message)
        if percentage >= 0:
            self._progress_bar.setRange(0, 100)
            self._progress_target = percentage
            if not hasattr(self, "_progress_timer") or self._progress_timer is None:
                self._progress_timer = QTimer(self)
                self._progress_timer.setInterval(
                    get_export_dial("dock.generation_state.progress_animate_ms", _PROGRESS_ANIMATE_MS)
                )
                self._progress_timer.timeout.connect(self._animate_progress)
            if self.isVisible() and not self._progress_timer.isActive():
                self._progress_timer.start()

    def _animate_progress(self):

        current = self._progress_bar.value()
        target = getattr(self, "_progress_target", current)
        if current < target:
            self._progress_bar.setValue(current + 1)
        else:
            if hasattr(self, "_progress_timer") and self._progress_timer is not None:
                self._progress_timer.stop()

    def _show_status_box(self, message: str, box_type: str = "info"):




        kinds = {
            "error": ("coral", "warning"),
            "success": ("green", "check"),
            "warning": ("amber", "warning"),
            "info": ("sky", "sparkles"),
        }
        category, glyph = kinds.get(box_type, kinds["error"])
        tint, ink = tokens.category_tint(category), tokens.category_ink(category)
        self._status_widget.setStyleSheet(
            f"QWidget#statusBox {{ background: {tint}; border: 1px solid {tokens.category_line(category)};"
            f" border-radius: {tokens.RADIUS_CARD}px; }}"
        )
        self._status_icon.setPixmap(
            pixmap_for(self._status_icon, glyph, self._status_icon_size, tokens.qcolor(ink))
        )
        self._status_label.setText(message)
        self._status_widget.setVisible(True)



        QtC.safe_single_shot(0, self, self._reveal_status_box)

    def _reveal_status_box(self) -> None:

        area = getattr(self, "_scroll_area", None)
        try:
            if area is not None and self._status_widget.isVisible():
                area.ensureWidgetVisible(self._status_widget, 0, 8)
        except RuntimeError:
            pass

    def _hide_status_box(self):
        self._status_widget.setVisible(False)
        self._status_label.setText("")
        self.clear_status_action()
        self._hide_limit_cta()

    def clear_status_action(self) -> None:

        button = getattr(self, "_status_action_btn", None)
        if button is not None:
            button.setVisible(False)
            button.setText("")
        self._status_action_handler = None

    def set_status_action(self, label: str, handler) -> None:





        button = getattr(self, "_status_action_btn", None)
        if button is None or not label:
            return
        self._status_action_handler = handler
        button.setText(label)
        button.setVisible(True)

    def _on_status_action_clicked(self) -> None:
        handler = getattr(self, "_status_action_handler", None)
        if handler is None:
            return


        self.clear_status_action()
        handler()

    def set_status(self, message: str, is_error: bool = False):
        self._hide_limit_cta()
        self.clear_status_action()
        if not message:
            self._hide_status_box()
        else:
            self._show_status_box(message, "error")



        if not self._is_free_tier_exhausted():
            self._trial_info_box.setVisible(False)
        if not (self._is_free_tier_prewall() or self._is_pro_low()):
            self._prewall_banner.setVisible(False)

    def _paywall_state(self) -> str:



        if self._cached_used is None or self._cached_limit is None:
            return "normal"
        if not self._is_free_tier:
            return self._pro_paywall_state()
        if self._cached_limit <= 0:
            return "normal"
        remaining = max(0, self._cached_limit - self._cached_used)
        unit_cost = self._resolution_credit_costs.get(
            "1K", DEFAULT_RESOLUTION_CREDIT_COSTS["1K"]
        )
        return classify_paywall_state(remaining, unit_cost)

    def _pro_paywall_state(self) -> str:


        if self._cached_limit <= 0 or not pro_ceiling_enabled():
            return "normal"
        remaining = max(0, self._cached_limit - self._cached_used)
        if 0 < remaining <= pro_low_threshold(self._cached_limit):
            return "pro_low"
        return "normal"

    def _is_free_tier_exhausted(self) -> bool:
        return self._paywall_state() == "wall"

    def _is_free_tier_prewall(self) -> bool:
        return self._paywall_state() == "prewall"

    def set_generation_complete(self, layer_name: str, layer_id: str | None = None):

        self._stop_progress_animation()
        self._progress_bar.setValue(100)
        self._progress_widget.setVisible(False)
        self._hide_status_box()




        self._clear_vectorize_suggestion()

        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        self.set_generate_block_reason(None)


        self._exit_btn.setVisible(False)
        self._generate_note_box.setVisible(False)






        self._result_prompt_input.blockSignals(True)
        self._result_prompt_input.clear()
        self._result_prompt_input.blockSignals(False)
        self._adjust_result_prompt_height()
        self._result_prompt_container.refresh_favorite_star()
        self._update_result_guidance_hint("")
        self._update_result_generate_enabled("")
        self._result_prompt_container.set_readonly(False)




        self._prompt_container.set_readonly(False)
        self._result_section.setVisible(True)
        self._scroll_panel_to_top()



        self._result_prompt_widget.setVisible(True)
        self._place_version_strip("result")
        self._version_strip.set_readonly(False)
        self._refresh_resolution_triggers()

        self._place_reference_widget("result")




        del layer_name
        self._saved_layer_id = layer_id

        self._maybe_show_after_success()


        self._scroll_panel_to_top()


        QtC.safe_single_shot(0, self, self._focus_result_prompt)

    def _focus_result_prompt(self) -> None:


        try:
            if self._result_prompt_widget.isVisible():
                self._result_prompt_input.setFocus(QtC.OtherFocusReason)
        except RuntimeError:
            pass

    def _enter_iteration_state(self) -> None:








        self._stop_progress_animation()
        self._progress_widget.setVisible(False)
        self._hide_status_box()
        self._clear_vectorize_suggestion()
        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        self.set_generate_block_reason(None)
        self._exit_btn.setVisible(False)
        self._generate_note_box.setVisible(False)
        self._prompt_container.set_readonly(False)
        self._result_section.setVisible(True)
        self._scroll_panel_to_top()
        self._result_prompt_widget.setVisible(True)
        self._result_prompt_container.set_readonly(False)
        self._place_version_strip("result")
        self._version_strip.set_readonly(False)
        self._place_reference_widget("result")
        self._hide_after_success()
        self._refresh_resolution_triggers()
        self._scroll_panel_to_top()

    def restore_generation_context(
        self, prompt_text: str, template_id=None, template_name=None
    ) -> None:


        self._active_template_id = str(template_id or "") or None
        self._active_template_name = str(template_name or "") or None
        self._enter_iteration_state()
        self._result_prompt_input.blockSignals(True)
        self._result_prompt_input.setPlainText(format_template_prompt(prompt_text or ""))
        self._result_prompt_input.blockSignals(False)
        self._result_prompt_input.moveCursor(QtC.CursorEnd)
        self._update_result_generate_enabled()
        self._adjust_result_prompt_height()
