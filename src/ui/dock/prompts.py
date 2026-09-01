from __future__ import annotations

import re

from qgis.PyQt.QtWidgets import QTextEdit

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.prompts.prompt_presets import detect_prompt_guidance
from ..onboarding_hint import (
    BLUE_TINT,
    HINT_MARKUP_PROMPT,
    DismissibleHint,
    dismiss_hint,
    is_hint_dismissed,
)
from .style import _BTN_DISABLED, _BTN_GREEN, MAX_PROMPT_CHARS

# Fallbacks for the server-tunable prompt-guard dials.
_MIN_PROMPT_CHARS = 10
_MIN_PROMPT_WORDS = 2
# Ground resolution (m/px) above which the coarse-zone hint shows.
_COARSE_ZONE_M_PER_PX = 10.0
# Ground area (km²) above which the large-zone hint shows (Yvann 2026-08-02).
_LARGE_ZONE_KM2 = 20.0

_NUMBERS_RE = re.compile(r"\d+")


def _min_prompt_warning() -> str:
    """The "your prompt is too short" warning, carrying the numbers actually
    in force.

    The sentence ships with the shipped numbers written into it, in every
    language, and the two numbers are dials: the warning used to keep saying
    "10 characters, 2 words" whatever the guard was really enforcing. When a
    dial moves, the first two numbers in the sentence are rewritten to match.
    A translation that does not carry two numbers is left alone rather than
    half-rewritten, and a served string replaces the whole sentence."""
    min_chars = get_export_dial("limits.min_prompt_chars", _MIN_PROMPT_CHARS)
    min_words = get_export_dial("limits.min_prompt_words", _MIN_PROMPT_WORDS)
    sentence = tr("Please describe what you want to change (at least 10 characters, 2 words).")
    if (min_chars, min_words) != (_MIN_PROMPT_CHARS, _MIN_PROMPT_WORDS):
        if len(_NUMBERS_RE.findall(sentence)) >= 2:
            live = iter((str(min_chars), str(min_words)))
            sentence = _NUMBERS_RE.sub(lambda m: next(live, m.group(0)), sentence, count=2)
    # Escaped: the status box is the one label the plugin builds links into.
    return get_export_copy("prompt.too_short", sentence, escape=True)


class DockPromptMixin:
    """Prompt input handling, guidance hints, template arming, and Generate
    button gating for AIEditDockWidget."""

    def get_prompt(self) -> str:
        return self._prompt_input.toPlainText().strip()

    # --- Private methods ---

    def _on_prompt_changed(self):
        # One toPlainText() for the whole keystroke. It is O(document length)
        # and allocates a fresh string, and this path used to ask for it five
        # times: once per helper below, twice inside the template clear.
        prompt = self._enforce_prompt_max_length(self._prompt_input).strip()
        self._update_generate_enabled(prompt)
        self._clear_active_template_if_empty(prompt)
        self._update_prompt_guidance_hint(prompt)

    def _guidance_message_for(self, text: str) -> str | None:
        """Map an off-rails prompt to its soft hint, or None to stay silent.
        Shared by the first-run prompt and the result/retry prompt."""
        kind = detect_prompt_guidance(
            text, has_template=bool(self._active_template_id)
        )
        # Remembered for the telemetry on the AI Segmentation link below: the
        # click handler has no other way to know which hint was on screen.
        self._last_guidance_kind = kind
        # These three sentences decide whether someone rewrites a prompt or
        # gives up, so they are the ones worth retuning from what production
        # actually shows. Served per kind (copy.guidance.*), shipped copy when
        # the server says nothing. The measure and vector_file hints carry an
        # inline link to the AI Segmentation plugin (the dedicated tool for
        # detecting, outlining and counting objects); the anchor stays inside
        # tr() so each locale places it naturally, and the href is handled by
        # _on_guidance_link_activated.
        if kind == "vector_file":
            return get_export_copy("guidance.vector_file", tr(
                "AI Edit outputs an image, not a vector file. For polygons "
                "(SHP, GeoJSON), pick a Segment or Land cover template, then "
                "‘Vectorize this result’. For precise object outlines, try our "
                "<a href='ai_seg'>AI Segmentation</a> plugin."
            ))
        if kind == "measure":
            return get_export_copy("guidance.measure", tr(
                "AI Edit can't measure or count. Our "
                "<a href='ai_seg'>AI Segmentation</a> plugin is built for "
                "that: it outlines objects as polygons QGIS can count and "
                "measure."
            ))
        if kind == "qa":
            return get_export_copy("guidance.qa", tr(
                "AI Edit edits the image, it doesn't answer questions or count. "
                "Describe a visual change, e.g. colour the buildings red."
            ))
        return None

    @staticmethod
    def _apply_guidance_hint(label, msg: str | None) -> None:
        if not msg:
            label.setVisible(False)
            return
        # Glyph kept outside tr() so translators see clean text.
        label.setText("ⓘ  " + msg)
        label.setVisible(True)

    def _on_guidance_link_activated(self, href: str) -> None:
        """Inline link in the guidance hint: open the AI Segmentation plugin
        (its dock when installed, the Plugin Manager otherwise)."""
        if href != "ai_seg":
            return
        from ..cross_plugin_discovery import open_ai_segmentation
        installed = open_ai_segmentation()
        telemetry.track(te.SEG_REDIRECT_CLICKED, {
            "guidance_kind": getattr(self, "_last_guidance_kind", None) or "",
            "installed": installed,
        })
        telemetry.flush()

    def _update_prompt_guidance_hint(self, prompt: str | None = None) -> None:
        """Live off-rails hint under the first-run prompt. Non-blocking.
        `prompt` is the already-stripped box text when the caller has it."""
        if prompt is None:
            prompt = self.get_prompt()
        self._apply_guidance_hint(
            self._prompt_guidance_hint, self._guidance_message_for(prompt)
        )

    def _update_result_guidance_hint(self, prompt: str | None = None) -> None:
        """Same hint under the result/retry prompt, so iterating on a v1/v2
        gets the same guidance. Non-blocking."""
        if prompt is None:
            prompt = self._result_prompt_input.toPlainText().strip()
        self._apply_guidance_hint(
            self._result_guidance_hint, self._guidance_message_for(prompt)
        )

    def set_zone_guidance(
        self, ground_resolution_m: float | None, area_km2: float | None = None
    ) -> None:
        """Soft, non-blocking heads-up on zone selection, two independent
        triggers: the zone covers a very large ground area (~20 km², where
        detail loss is certain whatever the view scale), or the view is so
        zoomed out the export goes coarse (~10 m/px). Area wins the wording:
        it names the cause the user can act on directly."""
        area_threshold = get_export_dial("guidance.large_zone_km2", _LARGE_ZONE_KM2)
        if area_km2 is not None and area_km2 >= area_threshold:
            shipped = tr(
                "Very large zone (about {km2} km²): the AI keeps only broad "
                "shapes at this size. Select a smaller area for object-level "
                "edits."
            )
            # A served sentence can reuse {km2}. Plain replace, never
            # format(), so a stray brace cannot raise on the draw path.
            value = f"{area_km2:.0f}" if area_km2 >= 10 else f"{area_km2:.1f}"
            msg = get_export_copy("guidance.large_zone", shipped).replace("{km2}", value)
            self._zone_guidance_hint.setText("ⓘ  " + msg)
            self._zone_guidance_hint.setVisible(True)
            return
        threshold = get_export_dial("guidance.coarse_zone_m_per_px", _COARSE_ZONE_M_PER_PX)
        coarse = ground_resolution_m is not None and ground_resolution_m >= threshold
        if not coarse:
            self._zone_guidance_hint.setVisible(False)
            return
        # The threshold above was already tunable; the sentence is now too, so
        # the two can move together.
        msg = get_export_copy("guidance.coarse_zone", tr(
            "Zoomed out: the AI won't see small features (buildings, cars, "
            "trees) at this scale. Zoom in for object-level detail."
        ))
        self._zone_guidance_hint.setText("ⓘ  " + msg)
        self._zone_guidance_hint.setVisible(True)

    # --- Mark up prompt tip -------------------------------------------
    # A first-time Mark up user draws marks, hits Done, lands back on the
    # prompt, and has no idea the marks must be referenced in the prompt to
    # matter (the marks travel as a separate guidance image next to the clean
    # zone). This tip fills the HINT_GUIDE_AI slot under the prompt input
    # while saved marks exist for the current zone.

    def _build_markup_prompt_tip(self) -> None:
        """Create the tip card in the prompt section, after the guide-AI hint
        (the two never show together, see _update_markup_prompt_tip). Called
        from build.py right after _build_guide_ai_hint."""
        self._markup_prompt_hint = DismissibleHint(
            HINT_MARKUP_PROMPT,
            "",
            get_export_copy("markup.prompt_tip", tr(
                'Name your marks in the prompt, e.g. "add a pond inside '
                'the circle". The marks guide the AI and won\'t appear in '
                'the result.'
            )),
            visibility_gate=self._markup_marks_exist,
            # Blue info tint (owner call 2026-08-03): the tip is a heads-up
            # about how the next generation will behave, not neutral chrome.
            tint=BLUE_TINT,
            parent=self._prompt_section,
        )
        self._markup_prompt_hint.setVisible(False)
        self._prompt_layout.addWidget(self._markup_prompt_hint)
        # Every path into or out of Mark up fires one of these: the chip,
        # Alt+M and the footer toggle (both directions) emit markup_clicked,
        # the in-panel Done emits markup_done_clicked, Clear all emits
        # markup_clear_clicked. Zone changes are covered by the
        # _update_generate_enabled call below.
        self.markup_clicked.connect(self._schedule_markup_prompt_tip_update)
        self.markup_done_clicked.connect(self._schedule_markup_prompt_tip_update)
        self.markup_clear_clicked.connect(self._schedule_markup_prompt_tip_update)

    def _markup_marks_exist(self) -> bool:
        """True when at least one saved Mark up stroke exists for the current
        zone. The dock's live copy of the authoritative count sits on the
        Mark up panel (set_markup_annotation_count relays MarkupLayerManager's
        annotation_count_changed into it)."""
        panel = getattr(self, "_markup_panel", None)
        return panel is not None and panel.annotation_count() > 0

    def _schedule_markup_prompt_tip_update(self) -> None:
        """Recompute one tick later: the markup signals reach this mixin
        before the plugin's own slot settles the annotation count (Clear all
        in particular), so an immediate read would see the pre-click count."""
        QtC.safe_single_shot(0, self, self._update_markup_prompt_tip)

    def _update_markup_prompt_tip(self) -> None:
        """Show the tip while saved marks exist, hide it once they are gone
        (cleared, or dropped with the zone) or the session dismissed it."""
        hint = getattr(self, "_markup_prompt_hint", None)
        if hint is None:
            return
        if is_hint_dismissed(HINT_MARKUP_PROMPT) or not self._markup_marks_exist():
            hint.hide()
            return
        hint.show()
        # Precedence over the broader guide-AI tip, never two banners: marks
        # can only exist after markup_clicked, which already retired that tip
        # for the session (widget.py wires it to _mark_guide_ai_touched).
        # Re-assert it so a guidance reset landing mid-state cannot stack them.
        self._mark_guide_ai_touched()

    def _dismiss_markup_prompt_tip(self) -> None:
        """Generate fired with marks present: the advice has been delivered,
        so retire the tip for the rest of the session (it is back next
        session, see SESSION_ONLY_HINTS). A generation without marks leaves
        it live for a later first Mark up."""
        if is_hint_dismissed(HINT_MARKUP_PROMPT) or not self._markup_marks_exist():
            return
        hint = getattr(self, "_markup_prompt_hint", None)
        if hint is not None:
            hint.hide()
        dismiss_hint(HINT_MARKUP_PROMPT)

    def _on_result_prompt_changed(self):
        # Same one-read rule as _on_prompt_changed.
        result = self._enforce_prompt_max_length(self._result_prompt_input).strip()
        self._update_result_generate_enabled(result)
        self._clear_active_template_if_empty(result_prompt=result)
        self._update_result_guidance_hint(result)

    def _clear_active_template_if_empty(
        self, prompt: str | None = None, result_prompt: str | None = None
    ) -> None:
        """Drop the armed template once both prompt inputs are empty.

        Edits to the prompt text keep the association alive; clearing it out
        (or hitting Exit) is the signal that the next prompt is unrelated.

        Either box's stripped text can be passed in by a caller that already
        read it. Nothing armed, or a non-empty first box, answers without
        touching the other document at all.
        """
        if not self._active_template_id and not self._active_template_name:
            return
        if prompt is None:
            prompt = self._prompt_input.toPlainText().strip()
        if prompt:
            return
        if result_prompt is None:
            result_prompt = self._result_prompt_input.toPlainText().strip()
        if not result_prompt:
            self._active_template_id = None
            self._active_template_name = None

    def get_active_template(self) -> tuple[str, str] | None:
        """Return the armed (template_id, template_name) if any."""
        if self._active_template_id:
            return self._active_template_id, self._active_template_name or ""
        return None

    @staticmethod
    def _enforce_prompt_max_length(text_edit: QTextEdit) -> str:
        """Truncate the prompt to the (server-tunable) max length, and return
        the text now in the box so the caller keeps the one read it paid for."""
        max_chars = get_export_dial("limits.max_prompt_chars", MAX_PROMPT_CHARS)
        plain = text_edit.toPlainText()
        if len(plain) <= max_chars:
            return plain
        plain = plain[:max_chars]
        cursor_pos = text_edit.textCursor().position()
        text_edit.blockSignals(True)
        try:
            text_edit.setPlainText(plain)
            cursor = text_edit.textCursor()
            cursor.setPosition(min(cursor_pos, max_chars))
            text_edit.setTextCursor(cursor)
        finally:
            text_edit.blockSignals(False)
        return plain

    @staticmethod
    def _prompt_meets_minimum(prompt: str) -> bool:
        """Min-prompt guard, server-tunable. The warning that follows a refusal
        is built from these same two values (_min_prompt_warning)."""
        min_chars = get_export_dial("limits.min_prompt_chars", _MIN_PROMPT_CHARS)
        min_words = get_export_dial("limits.min_prompt_words", _MIN_PROMPT_WORDS)
        return len(prompt) >= min_chars and len(prompt.split()) >= min_words

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
        if not self._prompt_meets_minimum(prompt):
            self._show_status_box(_min_prompt_warning(), "warning")
            return
        # Transfer prompt to main input for the generation flow
        self._prompt_input.setPlainText(prompt)
        self._result_section.setVisible(False)
        self._hide_status_box()
        self._dismiss_markup_prompt_tip()
        self.retry_clicked.emit(prompt)

    def _on_generate_clicked(self):
        prompt = self.get_prompt()
        if not prompt:
            return
        if not self._prompt_meets_minimum(prompt):
            self._show_status_box(_min_prompt_warning(), "warning")
            return
        self._hide_status_box()
        self._dismiss_markup_prompt_tip()
        self.generate_clicked.emit(prompt)

    def _on_generate_shortcut(self):
        """Global Enter/Return shortcut. Only fires Generate when the button is
        actually visible and enabled, so the key stays a no-op during signup,
        an active run, or before a zone is selected.

        Mid-draw on the polygon zone tool, this shortcut wins the keyboard
        race before the tool's own keyPressEvent ever runs (WindowShortcut
        context), so Enter would otherwise be silently swallowed instead of
        closing the shape (see AIEditDockWidget._active_polygon_draw_tool).
        """
        # The markup Line tool draws while the Mark up panel hides
        # _main_widget, so its delegation must run BEFORE the visibility
        # early-return or Enter is silently swallowed instead of committing.
        line_tool = self._active_markup_line_tool()
        if line_tool is not None and line_tool.has_points():
            line_tool.close_now()
            return
        if not self._main_widget.isVisible():
            return
        draw_tool = self._active_polygon_draw_tool()
        if draw_tool is not None:
            draw_tool.close_now()
            return
        if not self._generate_btn.isVisible() or not self._generate_btn.isEnabled():
            return
        self._on_generate_clicked()

    def _update_generate_enabled(self, prompt: str | None = None):
        has_prompt = bool(self.get_prompt() if prompt is None else prompt)
        # Held while an onboarding basemap's online tiles are still warming, so
        # the guided first generation can't export a blank input.
        enabled = self._zone_selected and has_prompt and not self._imagery_loading
        self._generate_btn.setEnabled(enabled)
        self._update_generate_style()
        self._update_generate_button_text()
        # Runs on every zone selection too (set_zone_selected), so the tip
        # follows marks that persist onto a redrawn zone.
        self._update_markup_prompt_tip()

    def set_imagery_loading(self, loading: bool):
        """Hold or release Generate while an onboarding basemap warms its tiles.

        Exporting the canvas before the online tiles have painted would ship a
        blank input (a crop error), so during warm-up Generate is disabled and
        labelled, then re-enabled once the plugin reports the imagery settled."""
        self._imagery_loading = bool(loading)
        self._update_generate_enabled()
        self._update_generate_button_text()

    def _update_generate_style(self):
        # Qt re-parses and re-applies an identical stylesheet (~23 us a call),
        # so the guard is on us. State: the enabled flag the sheet was last
        # written for, absent until the first call.
        enabled = self._generate_btn.isEnabled()
        if getattr(self, "_generate_style_state", None) is enabled:
            return
        self._generate_style_state = enabled
        self._generate_btn.setStyleSheet(_BTN_GREEN if enabled else _BTN_DISABLED)

    def _update_result_generate_enabled(self, prompt: str | None = None):
        """Gate the result-section Generate button on a non-empty prompt.

        The retry field now starts blank after each generation, so the button
        would otherwise sit clickable but silently no-op. Greying it out tells
        the user to type a fresh instruction first.
        """
        if prompt is None:
            prompt = self._result_prompt_input.toPlainText().strip()
        enabled = bool(prompt)
        self._result_regenerate_btn.setEnabled(enabled)
        if getattr(self, "_result_generate_style_state", None) is enabled:
            return
        self._result_generate_style_state = enabled
        self._result_regenerate_btn.setStyleSheet(_BTN_GREEN if enabled else _BTN_DISABLED)
