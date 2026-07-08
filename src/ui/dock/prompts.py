from __future__ import annotations

from qgis.PyQt.QtWidgets import QTextEdit

from ...core.auth.activation_manager import has_consent
from ...core.i18n import tr
from ...core.prompts.prompt_presets import detect_prompt_guidance
from .style import _BTN_DISABLED, _BTN_GREEN, MAX_PROMPT_CHARS


class DockPromptMixin:
    """Prompt input handling, guidance hints, template arming, and Generate
    button gating for AIEditDockWidget."""

    def get_prompt(self) -> str:
        return self._prompt_input.toPlainText().strip()

    # --- Private methods ---

    def _on_prompt_changed(self):
        self._enforce_prompt_max_length(self._prompt_input)
        self._update_generate_enabled()
        self._clear_active_template_if_empty()
        self._update_prompt_guidance_hint()

    def _guidance_message_for(self, text: str) -> str | None:
        """Map an off-rails prompt to its soft hint, or None to stay silent.
        Shared by the first-run prompt and the result/retry prompt."""
        kind = detect_prompt_guidance(
            text, has_template=bool(self._active_template_id)
        )
        if kind == "vector_file":
            return tr(
                "AI Edit outputs an image, not a vector file. For polygons "
                "(SHP, GeoJSON), pick a Segment or Land cover template, then "
                "‘Vectorize this result’."
            )
        if kind == "measure":
            return tr(
                "AI Edit can't measure or count. Pick a Segment template, then "
                "‘Vectorize this result’: QGIS gives the area and count per "
                "polygon."
            )
        if kind == "qa":
            return tr(
                "AI Edit edits the image, it doesn't answer questions or count. "
                "Describe a visual change, e.g. colour the buildings red."
            )
        return None

    @staticmethod
    def _apply_guidance_hint(label, msg: str | None) -> None:
        if not msg:
            label.setVisible(False)
            return
        # Glyph kept outside tr() so translators see clean text.
        label.setText("ⓘ  " + msg)
        label.setVisible(True)

    def _update_prompt_guidance_hint(self) -> None:
        """Live off-rails hint under the first-run prompt. Non-blocking."""
        self._apply_guidance_hint(
            self._prompt_guidance_hint, self._guidance_message_for(self.get_prompt())
        )

    def _update_result_guidance_hint(self) -> None:
        """Same hint under the result/retry prompt, so iterating on a v1/v2
        gets the same guidance. Non-blocking."""
        text = self._result_prompt_input.toPlainText().strip()
        self._apply_guidance_hint(
            self._result_guidance_hint, self._guidance_message_for(text)
        )

    def set_zone_guidance(self, ground_resolution_m: float | None) -> None:
        """Soft, non-blocking heads-up when the drawn zone is so zoomed out the
        model can't resolve small features. Called by the plugin on zone
        selection. Threshold ~10 m/px: where the failure rate climbs sharply."""
        coarse = ground_resolution_m is not None and ground_resolution_m >= 10.0
        if not coarse:
            self._zone_guidance_hint.setVisible(False)
            return
        msg = tr(
            "Zoomed out: the AI won't see small features (buildings, cars, "
            "trees) at this scale. Zoom in for object-level detail."
        )
        self._zone_guidance_hint.setText("ⓘ  " + msg)
        self._zone_guidance_hint.setVisible(True)

    def _on_result_prompt_changed(self):
        self._enforce_prompt_max_length(self._result_prompt_input)
        self._update_result_generate_enabled()
        self._clear_active_template_if_empty()
        self._update_result_guidance_hint()

    def _clear_active_template_if_empty(self) -> None:
        """Drop the armed template once both prompt inputs are empty.

        Edits to the prompt text keep the association alive; clearing it out
        (or hitting Exit) is the signal that the next prompt is unrelated.
        """
        prompt = self._prompt_input.toPlainText().strip()
        result = self._result_prompt_input.toPlainText().strip()
        if not prompt and not result:
            self._active_template_id = None
            self._active_template_name = None

    def get_active_template(self) -> tuple[str, str] | None:
        """Return the armed (template_id, template_name) if any."""
        if self._active_template_id:
            return self._active_template_id, self._active_template_name or ""
        return None

    @staticmethod
    def _enforce_prompt_max_length(text_edit: QTextEdit) -> None:
        """Truncate the prompt to MAX_PROMPT_CHARS."""
        plain = text_edit.toPlainText()
        if len(plain) <= MAX_PROMPT_CHARS:
            return
        cursor_pos = text_edit.textCursor().position()
        text_edit.blockSignals(True)
        try:
            text_edit.setPlainText(plain[:MAX_PROMPT_CHARS])
            cursor = text_edit.textCursor()
            cursor.setPosition(min(cursor_pos, MAX_PROMPT_CHARS))
            text_edit.setTextCursor(cursor)
        finally:
            text_edit.blockSignals(False)

    _PROMPT_MAX_HEIGHT = 400

    def _adjust_prompt_height(self):
        """Auto-expand prompt input (60px min, 200px max). When the cap
        kicks in, snap height to a whole number of text lines so the last
        visible line isn't half-cut at the viewport bottom."""
        self._prompt_input.setFixedHeight(
            self._snapped_prompt_height(self._prompt_input, min_h=60)
        )

    def _adjust_result_prompt_height(self):
        """Auto-expand result prompt input (50px min, 200px max, line-snapped)."""
        self._result_prompt_input.setFixedHeight(
            self._snapped_prompt_height(self._result_prompt_input, min_h=50)
        )

    @classmethod
    def _snapped_prompt_height(cls, text_edit: QTextEdit, min_h: int) -> int:
        # QSS sets `padding: 4px` on QTextEdit, so the viewport is inset 4px
        # top and 4px bottom from the widget edge - 8 total. The previous
        # value of 12 was a stale comment ("6+6") and overshot the cut by 4px.
        padding = 8
        frame = 2 * text_edit.frameWidth()
        target = int(text_edit.document().size().height()) + padding + frame
        if target > cls._PROMPT_MAX_HEIGHT:
            line_h = text_edit.fontMetrics().lineSpacing()
            if line_h > 0:
                n_lines = max(1, (cls._PROMPT_MAX_HEIGHT - padding) // line_h)
                target = n_lines * line_h + padding
            else:
                target = cls._PROMPT_MAX_HEIGHT
        return max(min_h, target)

    def _on_retry_clicked(self):
        """Retry on same zone with the (possibly edited) prompt from result section."""
        prompt = self._result_prompt_input.toPlainText().strip()
        if not prompt:
            return
        if len(prompt) < 10 or len(prompt.split()) < 2:
            self._show_status_box(
                tr("Please describe what you want to change (at least 10 characters, 2 words)."),
                "warning",
            )
            return
        # Transfer prompt to main input for the generation flow
        self._prompt_input.setPlainText(prompt)
        self._result_section.setVisible(False)
        self._hide_status_box()
        self.retry_clicked.emit(prompt)

    def _on_generate_clicked(self):
        prompt = self.get_prompt()
        if not prompt:
            return
        if len(prompt) < 10 or len(prompt.split()) < 2:
            msg = tr("Please describe what you want to change (at least 10 characters, 2 words).")
            self._show_status_box(msg, "warning")
            return
        self._hide_status_box()
        self.generate_clicked.emit(prompt)

    def _on_generate_shortcut(self):
        """Global Enter/Return shortcut. Only fires Generate when the button is
        actually visible and enabled, so the key stays a no-op during signup,
        an active run, or before a zone is selected."""
        if not self._main_widget.isVisible():
            return
        if not self._generate_btn.isVisible() or not self._generate_btn.isEnabled():
            return
        self._on_generate_clicked()

    def _on_consent_changed(self):
        """Re-evaluate Generate button when consent checkbox changes."""
        self._update_generate_enabled()

    def _update_generate_enabled(self):
        has_prompt = bool(self.get_prompt())
        consent_ok = has_consent() or self._consent_check.isChecked()
        # Held while an onboarding basemap's online tiles are still warming, so
        # the guided first generation can't export a blank input.
        enabled = self._zone_selected and has_prompt and consent_ok and not self._imagery_loading
        self._generate_btn.setEnabled(enabled)
        self._update_generate_style()
        self._update_generate_button_text()

    def set_imagery_loading(self, loading: bool):
        """Hold or release Generate while an onboarding basemap warms its tiles.

        Exporting the canvas before the online tiles have painted would ship a
        blank input (a crop error), so during warm-up Generate is disabled and
        labelled, then re-enabled once the plugin reports the imagery settled."""
        self._imagery_loading = bool(loading)
        self._update_generate_enabled()
        self._update_generate_button_text()

    def _update_generate_style(self):
        if self._generate_btn.isEnabled():
            self._generate_btn.setStyleSheet(_BTN_GREEN)
        else:
            self._generate_btn.setStyleSheet(_BTN_DISABLED)

    def _update_result_generate_enabled(self):
        """Gate the result-section Generate button on a non-empty prompt.

        The retry field now starts blank after each generation, so the button
        would otherwise sit clickable but silently no-op. Greying it out tells
        the user to type a fresh instruction first.
        """
        enabled = bool(self._result_prompt_input.toPlainText().strip())
        self._result_regenerate_btn.setEnabled(enabled)
        self._result_regenerate_btn.setStyleSheet(_BTN_GREEN if enabled else _BTN_DISABLED)
