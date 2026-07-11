from __future__ import annotations

import html
import random

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QTextCursor
from qgis.PyQt.QtWidgets import QStyle

from ...core import qt_compat as QtC
from ...core.auth.activation_manager import has_consent
from ...core.i18n import tr
from ...core.prompts.loading_messages import get_phase_messages
from ...core.prompts.prompt_presets import format_template_prompt
from ..onboarding_hint import HINT_FIRST_STEPS, is_hint_dismissed
from .style import _BTN_DISABLED, SUCCESS_TEXT


class DockGenerationStateMixin:
    """State machine transitions (launch / selecting zone / prompt /
    generating / result), progress animation, and status boxes for
    AIEditDockWidget."""

    def set_reference_target_extent(self, extent, crs) -> None:
        """Align reference image renders to the generation zone extent (pushed
        by the plugin when a zone is drawn). (None, None) reverts to the view."""
        if self._reference_widget is not None:
            self._reference_widget.set_target_extent(extent, crs)

    def set_zone_selected(self):
        """Zone drawn: show the prompt section and the Generate/Exit row."""
        self._zone_selected = True
        self._hide_status_box()
        self._layer_saved_label.setVisible(False)
        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._result_section.setVisible(False)
        self._prompt_section.setVisible(True)
        self._prompt_container.set_readonly(False)
        self._place_reference_widget("prompt")
        self._consent_widget.setVisible(not has_consent())
        self._generate_btn.setVisible(True)
        self._exit_btn.setVisible(True)
        self._refresh_resolution_triggers()
        self._update_generate_enabled()
        self._update_generate_button_text()
        # Defer focus: the canvas still has it from the just-finished mouse
        # release event. Setting focus synchronously gets clobbered as soon
        # as the canvas finishes its own focus handling. We fire twice
        # (0ms + 50ms) because on some platforms the canvas reclaims focus
        # after the first setFocus call.
        QtC.safe_single_shot(0, self, self._focus_prompt_input)
        QtC.safe_single_shot(50, self, self._focus_prompt_input)

    def _focus_prompt_input(self):
        """Bring the dock forward and put the caret in the prompt textarea."""
        self.raise_()
        self.activateWindow()
        self._prompt_input.setFocus(QtC.OtherFocusReason)
        cursor = self._prompt_input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._prompt_input.setTextCursor(cursor)

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
        """Stop the smooth progress animation timer if running."""
        if hasattr(self, "_progress_timer") and self._progress_timer is not None:
            self._progress_timer.stop()

    def _should_show_first_steps(self) -> bool:
        """First-steps guide banner gate: only when signed in, on the idle
        LAUNCH screen, and not yet dismissed. Hidden while selecting a zone,
        generating, or viewing a result, and inside the tool panels."""
        if not getattr(self, "_activated", False):
            return False
        if is_hint_dismissed(HINT_FIRST_STEPS):
            return False
        try:
            # The empty state shows ONLY the hero card (one info per state):
            # the guide banner waits until imagery exists.
            if self._warning_widget.isVisibleTo(self):
                return False
        except (RuntimeError, AttributeError):
            pass
        try:
            # isVisibleTo (not isVisible) so the gate reflects the INTENDED
            # state even if the dock window is not on-screen right now.
            return bool(self._launch_section.isVisibleTo(self))
        except (RuntimeError, AttributeError):
            return False

    def _update_first_steps_visibility(self) -> None:
        """Drive the bottom-pinned first-steps banner from its gate. Called on
        every state transition: the banner is a top-level sibling of the footer,
        so it is not auto-hidden when the flow swaps the content views."""
        hint = getattr(self, "_first_steps_hint", None)
        if hint is not None:
            hint.setVisible(self._should_show_first_steps())

    def set_launch_state(self):
        """LAUNCH: show the entry screen with the 'Launch AI Edit' button.

        Used after activation and whenever the user clicks Exit. The selection
        tool is expected to be inactive in this state (managed by the plugin).
        """
        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False
        # Leaving the flow voids any onboarding imagery gate, so a mid-load
        # Exit or zone-clear can't strand Generate disabled for the next zone.
        self._imagery_loading = False

        if self._reference_widget is not None:
            self._reference_widget.clear()
            self._reference_widget.setVisible(False)

        self._launch_section.setVisible(True)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._progress_widget.setVisible(False)
        self._result_section.setVisible(False)
        self._layer_saved_label.setVisible(False)
        self._consent_widget.setVisible(False)
        self._generate_btn.setVisible(False)
        self._exit_btn.setVisible(False)

        self._prompt_container.set_readonly(False)
        self._prompt_input.clear()
        self._prompt_input.setFixedHeight(60)
        self._result_prompt_input.clear()
        self._active_template_id = None
        self._active_template_name = None
        # Idle screen: reveal the first-steps guide banner (gate-checked).
        self._update_first_steps_visibility()

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
        # Re-surface the upsell banner on free-tier-exhausted accounts:
        # state transitions otherwise hide it via set_status() side effects.
        if self._is_free_tier_exhausted() and self._trial_info_url:
            self._trial_info_box.setVisible(True)

    def set_selecting_zone_state(self):
        """SELECTING_ZONE: invite the user to draw a zone on the canvas.

        Entered after Launch is clicked, or after the user clears their zone.
        The selection tool should be active (managed by the plugin).
        """
        self._stop_progress_animation()
        self._hide_status_box()
        self._zone_selected = False
        # Leaving the flow voids any onboarding imagery gate, so a mid-load
        # Exit or zone-clear can't strand Generate disabled for the next zone.
        self._imagery_loading = False

        if self._reference_widget is not None:
            self._reference_widget.setVisible(False)

        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(True)
        self._prompt_section.setVisible(False)
        self._progress_widget.setVisible(False)
        self._result_section.setVisible(False)
        self._layer_saved_label.setVisible(False)
        self._consent_widget.setVisible(False)
        self._generate_btn.setVisible(False)
        # No Exit in this state - the screen is just the draw invitation.
        self._exit_btn.setVisible(False)
        self._update_layer_warning()
        # Left the idle screen: hide the first-steps guide banner.
        self._update_first_steps_visibility()

    # Backwards-compat alias for callers that still use the old name.
    def set_prompt_state(self):
        self.set_launch_state()

    def set_generating(self, generating: bool):
        """Toggle generation state -- keep prompt visible but grayed out.

        Wrapped in setUpdatesEnabled(False)/(True) so Qt batches the many
        setVisible() calls below into a single repaint. Without this batch,
        the panel reflows piecewise on Generate click and the user sees the
        dock go blank for ~1s before the progress UI lands.
        """
        self.setUpdatesEnabled(False)
        try:
            self._progress_widget.setVisible(generating)
            self._result_section.setVisible(False)
            self._warning_widget.setVisible(False)
            self._set_upgrade_cta_wanted(False)

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
                # Keep the version lineage visible under the progress bar while
                # the next edit renders, but locked (no base switch mid-run).
                self._place_version_strip("generating")
                self._version_strip.set_readonly(True)
                self._consent_widget.setVisible(False)
                self._generate_btn.setVisible(False)
                # Hide Exit during generation: the user shouldn't be tempted to
                # cancel mid-run from this row. The title-bar X still works as
                # an escape hatch.
                self._exit_btn.setVisible(False)
                self._start_prep_ticker("canvas")
            else:
                self._stop_prep_ticker()
                self._prompt_container.set_readonly(False)
                if self._reference_widget is not None:
                    self._reference_widget.set_readonly(False)
                self._consent_widget.setVisible(not has_consent() and self._zone_selected)
                self._generate_btn.setVisible(True)
                self._exit_btn.setVisible(True)
                self._refresh_resolution_triggers()
                self._prompt_section.setVisible(True)
                # Cancelled / errored run: bring the strip back to its home and
                # unlock it (the result screen may re-appear with it).
                self._place_version_strip("result")
                self._version_strip.set_readonly(False)
        finally:
            self.setUpdatesEnabled(True)

    # Prep ticker: animates the bar 1->10% during canvas (export) and upload
    # phases, rotating playful messages so the user gets visible feedback
    # instead of a static "Preparing..." until the worker's first poll.
    def _start_prep_ticker(self, phase: str) -> None:
        self._prep_phase = phase
        self._prep_messages_pool = get_phase_messages(phase) or [tr("Preparing...")]
        random.shuffle(self._prep_messages_pool)
        self._prep_idx = 0
        # Set first message right away so the user sees something immediately.
        self._progress_label.setText(self._prep_messages_pool[0])
        if not hasattr(self, "_prep_ticker") or self._prep_ticker is None:
            self._prep_ticker = QTimer(self)
            self._prep_ticker.setInterval(1300)
            self._prep_ticker.timeout.connect(self._tick_prep)
        if not self._prep_ticker.isActive():
            self._prep_ticker.start()

    def _stop_prep_ticker(self) -> None:
        if hasattr(self, "_prep_ticker") and self._prep_ticker is not None and self._prep_ticker.isActive():
            self._prep_ticker.stop()

    def prep_advance_phase(self, phase: str) -> None:
        """Switch the prep ticker to a new message pool mid-flight.
        Called by plugin.py when canvas export finishes -> upload phase starts.
        """
        if not hasattr(self, "_prep_ticker") or self._prep_ticker is None or not self._prep_ticker.isActive():
            return
        self._start_prep_ticker(phase)

    def _tick_prep(self) -> None:
        # Cycle messages
        if self._prep_messages_pool:
            self._prep_idx = (self._prep_idx + 1) % len(self._prep_messages_pool)
            self._progress_label.setText(self._prep_messages_pool[self._prep_idx])
        # Advance the bar by 1% per tick, capped at the phase ceiling. Stops
        # naturally when the worker emits a real progress signal (>=5%) since
        # set_progress_message stops the prep ticker.
        cap = 5 if self._prep_phase == "canvas" else 10
        current = self._progress_bar.value()
        if current < cap:
            self._progress_target = min(cap, current + 1)
            if not hasattr(self, "_progress_timer") or self._progress_timer is None:
                self._progress_timer = QTimer(self)
                self._progress_timer.setInterval(30)
                self._progress_timer.timeout.connect(self._animate_progress)
            if not self._progress_timer.isActive():
                self._progress_timer.start()

    def set_generate_loading(self, loading: bool):
        """Toggle loading state on the Generate button during canvas export."""
        if loading:
            self._generate_btn_original_text = self._generate_btn.text()
            self._generate_btn.setText(tr("Preparing..."))
            self._generate_btn.setEnabled(False)
            self._generate_btn.setStyleSheet(_BTN_DISABLED)
        else:
            text = getattr(self, "_generate_btn_original_text", tr("Generate"))
            self._generate_btn.setText(text)
            self._update_generate_style()

    def set_progress_message(self, message: str, percentage: int = -1):
        """Update the progress label and bar during generation with smooth animation."""
        # First real worker progress signal -> stop the prep ticker so it stops
        # competing for the label + bar with the worker's own messages.
        self._stop_prep_ticker()
        self._progress_label.setText(message)
        if percentage >= 0:
            self._progress_bar.setRange(0, 100)
            self._progress_target = percentage
            if not hasattr(self, "_progress_timer") or self._progress_timer is None:
                self._progress_timer = QTimer(self)
                self._progress_timer.setInterval(30)
                self._progress_timer.timeout.connect(self._animate_progress)
            if not self._progress_timer.isActive():
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
        styles = {
            "error": (
                "QWidget { background-color: rgba(211, 47, 47, 0.25); "
                "border: 1px solid rgba(211, 47, 47, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #ef5350; }",
                QStyle.StandardPixmap.SP_MessageBoxCritical,
            ),
            "success": (
                "QWidget { background-color: rgba(139, 172, 39, 0.25); "
                "border: 1px solid rgba(139, 172, 39, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #66bb6a; }",
                QStyle.StandardPixmap.SP_DialogApplyButton,
            ),
            "warning": (
                "QWidget { background-color: rgb(255, 230, 150); "
                "border: 1px solid rgba(255, 152, 0, 0.6); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; color: #333333; }",
                QStyle.StandardPixmap.SP_MessageBoxWarning,
            ),
            "info": (
                "QWidget { background-color: rgba(25, 118, 210, 0.08); "
                "border: 1px solid rgba(25, 118, 210, 0.2); border-radius: 4px; }"
                "QLabel { background: transparent; border: none; }",
                QStyle.StandardPixmap.SP_MessageBoxInformation,
            ),
        }
        style_str, icon_enum = styles.get(box_type, styles["error"])
        self._status_widget.setStyleSheet(style_str)
        icon = self._status_widget.style().standardIcon(icon_enum)
        self._status_icon.setPixmap(icon.pixmap(self._status_icon_size, self._status_icon_size))
        self._status_label.setText(message)
        self._status_widget.setVisible(True)

    def _hide_status_box(self):
        self._status_widget.setVisible(False)
        self._status_label.setText("")
        self._hide_limit_cta()

    def set_status(self, message: str, is_error: bool = False):
        self._hide_limit_cta()
        if not message:
            self._hide_status_box()
        else:
            self._show_status_box(message, "error")
        # Only hide the trial-exhausted upsell if it's no longer applicable;
        # otherwise transient status updates would clobber it.
        if not self._is_free_tier_exhausted():
            self._trial_info_box.setVisible(False)

    def _is_free_tier_exhausted(self) -> bool:
        if self._cached_used is None or self._cached_limit is None:
            return False
        return self._is_free_tier and self._cached_limit > 0 and self._cached_used >= self._cached_limit

    def set_generation_complete(self, layer_name: str, layer_id: str | None = None):
        """Show RESULT state with iteration options (retry / done)."""
        self._stop_progress_animation()
        self._progress_bar.setValue(100)
        self._progress_widget.setVisible(False)
        self._hide_status_box()

        # Clear any stale Vectorize suggestion from a previous generation;
        # the plugin re-arms it for this run only if the template carries
        # a vector_color in the catalog.
        self._vectorize_cta_section.setVisible(False)
        self._vectorize_cta_pending = None

        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        # The result section has its own Exit button, so suppress the prompt
        # row's Exit to avoid duplication.
        self._exit_btn.setVisible(False)
        self._consent_widget.setVisible(False)

        # Start the next iteration from a blank prompt instead of replaying the
        # one that produced this result. An empty field nudges the user to
        # describe a fresh change rather than re-running the same instruction.
        self._result_prompt_input.clear()
        self._update_result_generate_enabled()
        self._result_prompt_container.set_readonly(False)
        # Generation is done: clear the (now hidden) prompt container's readonly
        # flag too. set_generating(True) set it and the success path never calls
        # set_generating(False), so without this the prompt library stays in
        # view-only mode (browse_only) and template clicks are ignored.
        self._prompt_container.set_readonly(False)
        self._result_section.setVisible(True)
        # Single prompt screen: the version strip below it carries the base
        # choice, so there is no separate choice step to land on first. Bring the
        # strip back from the progress area (success skips set_generating(False)).
        self._result_prompt_widget.setVisible(True)
        self._place_version_strip("result")
        self._version_strip.set_readonly(False)
        self._refresh_resolution_triggers()

        self._place_reference_widget("result")

        self._saved_layer_id = layer_id
        escaped_name = html.escape(layer_name)
        if layer_id:
            link_html = (
                f'<a href="terralab:focus-layer" '
                f'style="color: {SUCCESS_TEXT}; text-decoration: underline;">'
                f'{escaped_name}</a>'
            )
        else:
            link_html = escaped_name
        self._layer_saved_label.setText(tr("Saved as {name}").format(name=link_html))
        self._layer_saved_label.setVisible(True)

        self._set_upgrade_cta_wanted(self._is_free_tier and self._activated)
        # Result screen (not idle): keep the first-steps guide banner hidden.
        self._update_first_steps_visibility()

    def _enter_iteration_state(self) -> None:
        """Show the RESULT/iterate UI (prompt + version strip above the Generate
        row) without the post-generation 'Saved as' line.

        Restoring a past generation means 'resume iterating on this image', so it
        lands in the same layout a fresh result does. This keeps the version
        strip in its proper home above the action row instead of falling below
        it (the old restore path used the in-flight 'generating' slot, which sits
        under the Generate/Exit row in the prompt state)."""
        self._stop_progress_animation()
        self._progress_widget.setVisible(False)
        self._hide_status_box()
        self._vectorize_cta_section.setVisible(False)
        self._vectorize_cta_pending = None
        self._launch_section.setVisible(False)
        self._select_zone_section.setVisible(False)
        self._prompt_section.setVisible(False)
        self._generate_btn.setVisible(False)
        self._exit_btn.setVisible(False)
        self._consent_widget.setVisible(False)
        self._prompt_container.set_readonly(False)
        self._result_section.setVisible(True)
        self._result_prompt_widget.setVisible(True)
        self._result_prompt_container.set_readonly(False)
        self._place_version_strip("result")
        self._version_strip.set_readonly(False)
        self._place_reference_widget("result")
        # Nothing was just saved on restore: keep the success line hidden.
        self._layer_saved_label.setVisible(False)
        self._refresh_resolution_triggers()
        # Reconcile the upgrade CTA like set_generation_complete, so the two
        # RESULT-entry paths can't leave a stale CTA on the iterate screen.
        self._set_upgrade_cta_wanted(self._is_free_tier and self._activated)
        # Restore can jump straight here from the idle screen: hide the banner.
        self._update_first_steps_visibility()

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
