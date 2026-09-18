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

# Second focus retry after a zone is drawn (see set_zone_selected).
_FOCUS_RETRY_MS = 50
# Prep ticker: how often the pre-worker percentage tick advances.
_PREP_TICKER_MS = 1300
# Smooth progress-bar animation step interval.
_PROGRESS_ANIMATE_MS = 30
# Prep ticker ceiling while exporting the canvas, before the upload phase.
_PREP_CAP_CANVAS_PCT = 5
# Prep ticker ceiling during every other pre-worker phase.
_PREP_CAP_OTHER_PCT = 10


class DockGenerationStateMixin:
    """State machine transitions (launch / selecting zone / prompt /
    generating / result), progress animation, and status boxes for
    AIEditDockWidget."""

    def set_reference_target_extent(self, extent, crs) -> None:
        """Align reference image renders to the generation zone extent (pushed
        by the plugin when a zone is drawn). (None, None) reverts to the view."""
        if self._reference_widget is not None:
            self._reference_widget.set_target_extent(extent, crs)

    def selected_input_layer(self):
        """The raster the edit starts from, as picked in "Image to edit", or
        None when the project holds no visible raster."""
        combo = getattr(self, "_layer_combo", None)
        if combo is None:
            return None
        try:
            return combo.currentLayer()
        except RuntimeError:
            return None

    def _place_layer_header(self, target: str) -> None:
        """Re-home "Image to edit": "launch" puts it between the home hero and
        Launch, like AI Segmentation's picker above Start; "flow" puts it at
        the top of the zone, prompt and result steps. One widget, reparented,
        so the pick survives the move."""
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
        """"launch" and "editable": the label over a live combo that follows
        the map view. "locked": from the zone commit to the result the label
        goes, the combo keeps the layer name with its chevron gone and stops
        following the tree, so the user reads which layer the run started
        from. With no raster at all the empty-canvas card owns the screen and
        the header stays hidden."""
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
        # Only when it changes: a stylesheet write re-polishes the combo.
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
        """Zone drawn: show the prompt section and the Generate/Cancel row."""
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
        # Defer focus: the canvas still has it from the just-finished mouse
        # release event. Setting focus synchronously gets clobbered as soon
        # as the canvas finishes its own focus handling. We fire twice
        # (0ms + 50ms) because on some platforms the canvas reclaims focus
        # after the first setFocus call.
        QtC.safe_single_shot(0, self, self._focus_prompt_input)
        QtC.safe_single_shot(
            get_export_dial("dock.generation_state.focus_retry_ms", _FOCUS_RETRY_MS), self, self._focus_prompt_input
        )

    def _focus_prompt_input(self):
        """Bring the dock forward and put the caret in the prompt textarea."""
        self.raise_()
        self.activateWindow()
        self._prompt_input.setFocus(QtC.OtherFocusReason)
        cursor = self._prompt_input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._prompt_input.setTextCursor(cursor)

    def hide_privacy_notice(self):
        """Retire the disclosure line once a generation has completed. The box
        holds only that line, so the whole box goes and leaves no empty frame."""
        self._generate_note_box.setVisible(False)

    def set_zone_cleared(self):
        """Zone removed: return to the SELECTING_ZONE state.

        Called when the user right-clicks → Delete zone, presses Esc on the
        canvas, or clicks the × overlay on the rubber band. We go back to
        the 'Select your zone' invitation rather than all the way to
        LAUNCH - the user is mid-flow, just redrawing.
        """
        self.set_reference_target_extent(None, None)
        self.set_selecting_zone_state()

    def _stop_progress_animation(self):
        """Stop both progress timers: the smooth bar step and the prep ticker.

        The prep ticker restarts the bar timer on every tick, so stopping the
        bar alone (a dock closed during the canvas export) left the pair
        ticking for nothing until the next run."""
        self._stop_prep_ticker()
        if hasattr(self, "_progress_timer") and self._progress_timer is not None:
            self._progress_timer.stop()
        loader = getattr(self, "_progress_loader", None)
        if loader is not None:
            loader.stop_run()

    def _guide_ai_tip_visible(self) -> bool:
        """Visibility gate for the "Guide the AI" tip: retires for the rest of
        the session once the user has used a grounding feature (reference or
        markup), or closed the tip, and stays hidden while a generation runs.
        Read by DismissibleHint.reshow() so a guidance reset never flashes the
        tip back for someone who just used the features, or over a run."""
        if is_hint_dismissed(HINT_GUIDE_AI):
            return False
        try:
            return not self._progress_widget.isVisibleTo(self)
        except (RuntimeError, AttributeError):  # progress widget not built yet
            return True

    def _mark_guide_ai_touched(self) -> None:
        """The user used a grounding feature (a reference got attached, or the
        markup chip or shortcut fired). They know it exists, so retire the tip
        for the rest of the session (it is back next session, see
        SESSION_ONLY_HINTS). This is a use, not a close, so it skips the close
        telemetry."""
        if is_hint_dismissed(HINT_GUIDE_AI):
            return
        hint = getattr(self, "_guide_ai_hint", None)
        if hint is not None:
            hint.hide()
        dismiss_hint(HINT_GUIDE_AI)

    def _on_guide_ai_dismissed(self) -> None:
        telemetry.track(te.GUIDANCE_TIP_DISMISSED)

    def _update_guide_ai_tip(self) -> None:
        """Show the tip for a freshly drawn zone (a new edit group), unless
        the user has already used a grounding feature or closed the tip for
        good."""
        hint = getattr(self, "_guide_ai_hint", None)
        if hint is None:
            return
        if is_hint_dismissed(HINT_GUIDE_AI):
            hint.hide()
            return
        hint.show()
        telemetry.track(te.GUIDANCE_TIP_SHOWN)

    def _set_guide_ai_tip_held(self, held: bool) -> None:
        """Hide the tip for a run; on a cancelled or failed run, give it back
        by its gate without counting a second view."""
        hint = getattr(self, "_guide_ai_hint", None)
        if hint is None:
            return
        hint.setVisible(False if held else self._guide_ai_tip_visible())

    def set_launch_state(self):
        """LAUNCH: show the entry screen with the 'Launch AI Edit' button.

        Used after activation and whenever the user clicks Cancel or New
        edit. The selection tool is expected to be inactive in this state (managed by the plugin).
        """
        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False
        # Leaving the flow voids any onboarding imagery gate, so a mid-load
        # Cancel or zone-clear can't strand Generate disabled for the next zone.
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
        # The lineage belongs to the flow the user just left. Its in-flight
        # slot sits inside _main_widget, which this screen keeps on screen, so
        # without this the home screen showed "Next edit starts from" and dead
        # tiles under the Launch button (Cancel or the dock's X during a run).
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
        # Dirty or never-synced cache on the home screen: the last history
        # fetch failed or has not landed (the post-generation one included).
        # Retry so the rows self-heal on the exact screen that shows them.
        # Visibility gate: set_activated calls this from initGui too, where
        # the dock may be closed, and an idle install must stay network-free
        # (the deferred bootstrap covers the first real show).
        if self.isVisible() and (
            self._library_history_dirty or not self._library_history_loaded
        ):
            self.conversations_refresh_requested.emit()

    def clear_active_template(self) -> None:
        """Drop the armed template so a new zone doesn't reuse a preset that
        was picked for the previous zone. Called from plugin._on_zone_selected."""
        self._active_template_id = None
        self._active_template_name = None
        # Resolution persists for the QGIS session - the paid "2K" default is
        # applied when set_credits confirms the tier, and coerced to "1K" by
        # _refresh_resolution_triggers when free tier is confirmed.
        self._refresh_resolution_triggers()
        self._update_layer_warning()
        # Re-surface the upsell banner on free-tier-exhausted accounts (or the
        # lighter pre-wall nudge one step before that): state transitions
        # otherwise hide both via set_status() side effects.
        if self._is_free_tier_exhausted() and self._trial_info_url:
            self._trial_info_box.setVisible(True)
        elif (self._is_free_tier_prewall() or self._is_pro_low()) and self._prewall_url:
            self._prewall_banner.setVisible(True)

    def set_zone_step_notice(self, text: str = "") -> None:
        """Answer a refused zone inside the draw step. Empty text clears it."""
        label = getattr(self, "_select_zone_notice", None)
        if label is None:
            return
        label.setText(text)
        label.setVisible(bool(text))

    def _scroll_panel_to_top(self) -> None:
        """Put the top of the panel back in view on a step change.

        Launch used to leave a scrolled dock where it was, so the step it
        opened could sit below the fold and the click read as a no-op.
        """
        area = getattr(self, "_scroll_area", None)
        if area is None:
            return
        try:
            area.verticalScrollBar().setValue(0)
        except (RuntimeError, AttributeError):  # scroll area deleted during teardown
            pass

    def set_selecting_zone_state(self):
        """SELECTING_ZONE: invite the user to draw a zone on the canvas.

        Entered after Launch is clicked, or after the user clears their zone.
        The selection tool should be active (managed by the plugin).
        """
        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False
        # Leaving the flow voids any onboarding imagery gate, so a mid-load
        # Cancel or zone-clear can't strand Generate disabled for the next zone.
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
        # No Cancel in this state: the screen is just the draw invitation.
        self._exit_btn.setVisible(False)
        self.set_zone_step_notice()
        self._update_layer_warning()
        # The step always shows itself: see _scroll_panel_to_top.
        self._scroll_panel_to_top()

    def set_generating(self, generating: bool):
        """Toggle generation state -- keep prompt visible but grayed out.

        Wrapped in setUpdatesEnabled(False)/(True) so Qt batches the many
        setVisible() calls below into a single repaint. Without this batch,
        the panel reflows piecewise on Generate click and the user sees the
        dock go blank for ~1s before the progress UI lands.
        """
        if generating:
            self._hide_after_success()
        self.setUpdatesEnabled(False)
        try:
            self._progress_widget.setVisible(generating)
            self._result_section.setVisible(False)
            self._warning_widget.setVisible(False)
            # A run owns the panel: the update card steps aside until it ends.
            self.sync_update_banner()

            if generating:
                self._progress_bar.setRange(0, 100)
                # Start at 1% so the bar is visible immediately on click. The
                # prep ticker animates 1->10% during canvas+upload phases, then
                # the worker's first real progress signal (>=5%) takes over.
                self._progress_bar.setValue(1)
                self._progress_target = 1
                self._hide_status_box()
                self._launch_section.setVisible(False)
                self._select_zone_section.setVisible(False)
                self._prompt_section.setVisible(True)
                self._prompt_container.set_readonly(True)
                # On regenerate the refs widget lives in the result container, so
                # hiding result_section above would also hide the thumbnails.
                # Move it back into the visible prompt container before locking.
                self._place_reference_widget("prompt")
                if self._reference_widget is not None:
                    self._reference_widget.set_readonly(True)
                # The Reference panel is a second view over the same store:
                # lock it in step with the strip.
                if getattr(self, "_reference_panel", None) is not None:
                    self._reference_panel.set_readonly(True)
                # Keep the version lineage visible under the progress bar while
                # the next edit renders, but locked (no base switch mid-run).
                self._place_version_strip("generating")
                self._version_strip.set_readonly(True)
                self._generate_note_box.setVisible(False)
                # The tip is advice for writing the prompt: a locked prompt
                # under a progress bar has no use for it.
                self._set_guide_ai_tip_held(True)
                self._generate_btn.setVisible(False)
                self.set_generate_block_reason(None)
                # Hide Cancel during generation: the user shouldn't be tempted to
                # cancel mid-run from this row. The title-bar X still works as
                # an escape hatch.
                self._exit_btn.setVisible(False)
                self._progress_loader.start_run()
                self._start_prep_ticker("canvas")
                # Generate sits at the bottom of a scrolled result; the card
                # that answers the click opens at the top.
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
                    # A failed or cancelled run started from a result: land back
                    # on that result, versions and the typed next change intact,
                    # with the error under it. The first prompt screen showed
                    # no versions at all, so the lineage read as lost.
                    self._show_result_layout()
                else:
                    self._generate_note_box.setVisible(not has_seen_privacy_notice())
                    self._generate_btn.setVisible(True)
                    self._exit_btn.setVisible(True)
                    self._prompt_section.setVisible(True)
                    self._set_guide_ai_tip_held(False)
                    # Cancelled / errored run: bring the strip back to its home
                    # and unlock it.
                    self._place_version_strip("result")
                    self._version_strip.set_readonly(False)
        finally:
            self.setUpdatesEnabled(True)

    def _show_result_layout(self) -> None:
        """The result screen's widgets, without touching its prompt text:
        the next-change box, Generate from and New edit, then the versions
        and the result tools under them."""
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

    # Prep ticker: animates the bar 1->10% during canvas (export) and upload
    # phases under one factual line per phase, until the worker's first poll.
    # The rotating jokes are gone (Yvann, 2026-09-18): the dots, the clock and
    # the percent are the feedback now.
    def _start_prep_ticker(self, phase: str) -> None:
        self._prep_phase = phase
        if phase == "canvas":
            text = tr("Capturing your zone...")
        else:
            # The worker opens on the same line, then says it is sending.
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
        """Switch the prep ticker to the next phase's line mid-flight.
        Called by plugin.py when canvas export finishes -> upload phase starts.
        """
        if not hasattr(self, "_prep_ticker") or self._prep_ticker is None or not self._prep_ticker.isActive():
            return
        self._start_prep_ticker(phase)

    def _tick_prep(self) -> None:
        # Advance the bar by 1% per tick, capped at the phase ceiling. Stops
        # naturally when the worker emits a real progress signal (>=5%) since
        # set_progress_message stops the prep ticker.
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
        """Update the progress label and bar during generation with smooth animation."""
        # First real worker progress signal -> stop the prep ticker so it stops
        # competing for the label + bar with the worker's own messages.
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
        """Smoothly animate progress bar toward target value."""
        current = self._progress_bar.value()
        target = getattr(self, "_progress_target", current)
        if current < target:
            self._progress_bar.setValue(current + 1)
        else:
            if hasattr(self, "_progress_timer") and self._progress_timer is not None:
                self._progress_timer.stop()

    def _show_status_box(self, message: str, box_type: str = "info"):
        """Show a styled status message box (AI Segmentation style)."""
        # The hue of its kind (error coral, success green, warning amber,
        # info sky): a faint ground, a line in the hue, 10 px corners, the
        # words in the main ink and the meaning carried by the glyph.
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
        # The box sits under the screen's actions: on a short dock a failure
        # landed below the fold and read as nothing happening. Deferred so
        # the layout has placed the box before the scroll is measured.
        QtC.safe_single_shot(0, self, self._reveal_status_box)

    def _reveal_status_box(self) -> None:
        """Scroll just enough to bring the status box into view."""
        area = getattr(self, "_scroll_area", None)
        try:
            if area is not None and self._status_widget.isVisible():
                area.ensureWidgetVisible(self._status_widget, 0, 8)
        except RuntimeError:  # dock torn down before the deferred call
            pass

    def _hide_status_box(self):
        self._status_widget.setVisible(False)
        self._status_label.setText("")
        self.clear_status_action()
        self._hide_limit_cta()

    def clear_status_action(self) -> None:
        """Drop the action button and its handler."""
        button = getattr(self, "_status_action_btn", None)
        if button is not None:
            button.setVisible(False)
            button.setText("")
        self._status_action_handler = None

    def set_status_action(self, label: str, handler) -> None:
        """Put ONE next step beside the current status message.

        Call it right after set_status: set_status clears any previous action,
        so a message can never inherit the button of the one before it.
        """
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
        # Retire the offer before running it: the handler usually replaces the
        # message, and a stale button under a new one points nowhere.
        self.clear_status_action()
        handler()

    def set_status(self, message: str, is_error: bool = False):
        self._hide_limit_cta()
        self.clear_status_action()
        if not message:
            self._hide_status_box()
        else:
            self._show_status_box(message, "error")
        # Only hide the trial-exhausted upsell (or the pre-wall banner) if it's
        # no longer applicable; otherwise transient status updates would
        # clobber it.
        if not self._is_free_tier_exhausted():
            self._trial_info_box.setVisible(False)
        if not (self._is_free_tier_prewall() or self._is_pro_low()):
            self._prewall_banner.setVisible(False)

    def _paywall_state(self) -> str:
        """"wall" | "prewall" | "normal" for the last confirmed free-tier
        balance. See core.paywall_state.classify_paywall_state for the
        threshold semantics (wall wins on overlap)."""
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
        """"pro_low" | "normal" for a paid balance. Generation stays
        allowed either way: the state only drives the contact banner."""
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
        """Show RESULT state with iteration options (retry / done)."""
        self._stop_progress_animation()
        self._progress_bar.setValue(100)
        self._progress_widget.setVisible(False)
        self._hide_status_box()

        # Clear any stale Vectorize suggestion from a previous generation;
        # the plugin re-arms it for this run only if the template carries
        # a vector_color in the catalog.
        self._clear_vectorize_suggestion()

        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        self.set_generate_block_reason(None)
        # The result section has its own New edit button, so the prompt
        # row's Cancel goes.
        self._exit_btn.setVisible(False)
        self._generate_note_box.setVisible(False)

        # The box asks for the NEXT change (Yvann, 2026-09-17): it starts
        # empty and its placeholder names the picked version. Refilling it
        # with the prompt that just ran read as "press Generate to make the
        # same thing again" on top of the result. That prompt stays one click
        # away, in the version's details card.
        self._result_prompt_input.blockSignals(True)
        self._result_prompt_input.clear()
        self._result_prompt_input.blockSignals(False)
        self._adjust_result_prompt_height()
        self._result_prompt_container.refresh_favorite_star()
        self._update_result_guidance_hint("")
        self._update_result_generate_enabled("")
        self._result_prompt_container.set_readonly(False)
        # Generation is done: clear the (now hidden) prompt container's readonly
        # flag too. set_generating(True) set it and the success path never calls
        # set_generating(False), so without this the prompt library stays in
        # view-only mode (browse_only) and template clicks are ignored.
        self._prompt_container.set_readonly(False)
        self._result_section.setVisible(True)
        self._scroll_panel_to_top()
        # Single prompt screen: the version strip below it carries the base
        # choice, so there is no separate choice step to land on first. Bring the
        # strip back from the progress area (success skips set_generating(False)).
        self._result_prompt_widget.setVisible(True)
        self._place_version_strip("result")
        self._version_strip.set_readonly(False)
        self._refresh_resolution_triggers()

        self._place_reference_widget("result")

        # The layer this run wrote. Its name shows in the newest version's
        # details card ("Added to your map as"), read through
        # saved_layer_probe; the result screen spends no row on it.
        del layer_name
        self._saved_layer_id = layer_id

        self._maybe_show_after_success()
        # The result opens at its top, prompt in view, whatever the scroll
        # position of the screen that launched the run.
        self._scroll_panel_to_top()
        # The next thing to do is type the next change: put the caret there so
        # Enter runs it straight away.
        QtC.safe_single_shot(0, self, self._focus_result_prompt)

    def _focus_result_prompt(self) -> None:
        """Caret into the next-change box, without raising a floating dock
        over the map the user is looking at."""
        try:
            if self._result_prompt_widget.isVisible():
                self._result_prompt_input.setFocus(QtC.OtherFocusReason)
        except RuntimeError:  # dock torn down before the deferred call
            pass

    def _enter_iteration_state(self) -> None:
        """Show the RESULT/iterate UI (prompt and Generate row, version strip
        under them) without the post-generation 'Saved as' line.

        Restoring a past generation means 'resume iterating on this image', so it
        lands in the same layout a fresh result does. This keeps the version
        strip in its result home under the action row (the old restore path
        used the in-flight 'generating' slot, which belongs to the progress
        area of the prompt state)."""
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
        """Reproduce a past generation: enter the iterate state and fill its
        prompt. The plugin has already restored the zone."""
        self._active_template_id = str(template_id or "") or None
        self._active_template_name = str(template_name or "") or None
        self._enter_iteration_state()
        self._result_prompt_input.blockSignals(True)
        self._result_prompt_input.setPlainText(format_template_prompt(prompt_text or ""))
        self._result_prompt_input.blockSignals(False)
        self._result_prompt_input.moveCursor(QtC.CursorEnd)
        self._update_result_generate_enabled()
        self._adjust_result_prompt_height()
