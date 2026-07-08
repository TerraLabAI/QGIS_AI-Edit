from __future__ import annotations

import re

from qgis.PyQt.QtWidgets import QTextEdit

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.number_format import format_count
from ...core.prompts.prompt_presets import detect_prompt_guidance
from ..onboarding_hint import (
    BLUE_TINT,
    HINT_MARKUP_PROMPT,
    DismissibleHint,
    dismiss_hint,
    is_hint_dismissed,
)
from .blocked_reasons import (
    _MIN_PROMPT_CHARS,
    _MIN_PROMPT_WORDS,
    GENERATE_BLOCK_NO_ZONE,
    GENERATE_BLOCK_PROMPT_EMPTY,
    GENERATE_BLOCK_PROMPT_TOO_SHORT,
)
from .design_tokens import BTN_GHOST_WIDE_QSS, BTN_PRIMARY_WIDE_QSS
from .style import MAX_PROMPT_CHARS
from .zone_sources import large_zone_km2


_COARSE_ZONE_M_PER_PX = 10.0

_NUMBERS_RE = re.compile(r"\d+")


def _min_prompt_warning() -> str:









    min_chars = get_export_dial("limits.min_prompt_chars", _MIN_PROMPT_CHARS)
    min_words = get_export_dial("limits.min_prompt_words", _MIN_PROMPT_WORDS)
    sentence = tr("Please describe what you want to change (at least 10 characters, 2 words).")
    if (min_chars, min_words) != (_MIN_PROMPT_CHARS, _MIN_PROMPT_WORDS):
        if len(_NUMBERS_RE.findall(sentence)) >= 2:
            live = iter((str(min_chars), str(min_words)))
            sentence = _NUMBERS_RE.sub(lambda m: next(live, m.group(0)), sentence, count=2)

    return get_export_copy("prompt.too_short", sentence, escape=True)


class DockPromptMixin:



    def get_prompt(self) -> str:
        return self._prompt_input.toPlainText().strip()

    def focus_prompt_input(self) -> None:





        target = (
            self._result_prompt_input
            if self._result_prompt_widget.isVisible()
            else self._prompt_input
        )
        target.setFocus(QtC.OtherFocusReason)
        cursor = target.textCursor()
        cursor.movePosition(QtC.CursorEnd)
        target.setTextCursor(cursor)



    def _on_prompt_changed(self):



        prompt = self._enforce_prompt_max_length(self._prompt_input).strip()
        self._update_generate_enabled(prompt)
        self._clear_active_template_if_empty(prompt)
        self._update_prompt_guidance_hint(prompt)

    def _guidance_message_for(self, text: str) -> str | None:


        kind = detect_prompt_guidance(
            text, has_template=bool(self._active_template_id)
        )


        self._last_guidance_kind = kind








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
        label.setText(msg)
        label.setVisible(True)

    def _on_guidance_link_activated(self, href: str) -> None:


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


        if prompt is None:
            prompt = self.get_prompt()
        message = self._guidance_message_for(prompt)
        self._apply_guidance_hint(self._prompt_guidance_hint, message)



        tip = getattr(self, "_guide_ai_hint", None)
        if tip is not None and self._prompt_section.isVisibleTo(self):
            tip.setVisible(not message and self._guide_ai_tip_visible())

    def _update_result_guidance_hint(self, prompt: str | None = None) -> None:


        if prompt is None:
            prompt = self._result_prompt_input.toPlainText().strip()
        self._apply_guidance_hint(
            self._result_guidance_hint, self._guidance_message_for(prompt)
        )

    def set_zone_guidance(
        self, ground_resolution_m: float | None, area_km2: float | None = None
    ) -> None:





        area_threshold = large_zone_km2()
        if area_km2 is not None and area_km2 >= area_threshold:
            shipped = tr(
                "Very large zone (about {km2} km²): the AI keeps only broad "
                "shapes at this size. Select a smaller area for object-level "
                "edits."
            )



            value = format_count(round(area_km2)) if area_km2 >= 10 else f"{area_km2:.1f}"
            msg = get_export_copy("guidance.large_zone", shipped).replace("{km2}", value)
            self._zone_guidance_hint.setText(msg)
            self._zone_guidance_hint.setVisible(True)
            return
        threshold = get_export_dial("guidance.coarse_zone_m_per_px", _COARSE_ZONE_M_PER_PX)
        coarse = ground_resolution_m is not None and ground_resolution_m >= threshold
        if not coarse:
            self._zone_guidance_hint.setVisible(False)
            return


        msg = get_export_copy("guidance.coarse_zone", tr(
            "Zoomed out: the AI won't see small features (buildings, cars, "
            "trees) at this scale. Zoom in for object-level detail."
        ))
        self._zone_guidance_hint.setText(msg)
        self._zone_guidance_hint.setVisible(True)








    def _build_markup_prompt_tip(self) -> None:



        self._markup_prompt_hint = DismissibleHint(
            HINT_MARKUP_PROMPT,
            "",
            get_export_copy("markup.prompt_tip", tr(
                'Name your marks in the prompt, e.g. "add a pond inside '
                'the circle". The marks guide the AI and won\'t appear in '
                'the result.'
            )),
            visibility_gate=self._markup_marks_exist,


            tint=BLUE_TINT,
            parent=self._prompt_section,
        )
        self._markup_prompt_hint.setVisible(False)
        self._prompt_layout.addWidget(self._markup_prompt_hint)





        self.markup_clicked.connect(self._schedule_markup_prompt_tip_update)
        self.markup_done_clicked.connect(self._schedule_markup_prompt_tip_update)
        self.markup_clear_clicked.connect(self._schedule_markup_prompt_tip_update)

    def _markup_marks_exist(self) -> bool:




        panel = getattr(self, "_markup_panel", None)
        return panel is not None and panel.annotation_count() > 0

    def _schedule_markup_prompt_tip_update(self) -> None:



        QtC.safe_single_shot(0, self, self._update_markup_prompt_tip)

    def _update_markup_prompt_tip(self) -> None:


        hint = getattr(self, "_markup_prompt_hint", None)
        if hint is None:
            return
        if is_hint_dismissed(HINT_MARKUP_PROMPT) or not self._markup_marks_exist():
            hint.hide()
            return
        hint.show()




        self._mark_guide_ai_touched()

    def _dismiss_markup_prompt_tip(self) -> None:




        if is_hint_dismissed(HINT_MARKUP_PROMPT) or not self._markup_marks_exist():
            return
        hint = getattr(self, "_markup_prompt_hint", None)
        if hint is not None:
            hint.hide()
        dismiss_hint(HINT_MARKUP_PROMPT)

    def _on_result_prompt_changed(self):

        result = self._enforce_prompt_max_length(self._result_prompt_input).strip()
        self._update_result_generate_enabled(result)
        self._clear_active_template_if_empty(result_prompt=result)
        self._update_result_guidance_hint(result)

    def _clear_active_template_if_empty(
        self, prompt: str | None = None, result_prompt: str | None = None
    ) -> None:









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

        if self._active_template_id:
            return self._active_template_id, self._active_template_name or ""
        return None

    @staticmethod
    def _enforce_prompt_max_length(text_edit: QTextEdit) -> str:


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


        min_chars = get_export_dial("limits.min_prompt_chars", _MIN_PROMPT_CHARS)
        min_words = get_export_dial("limits.min_prompt_words", _MIN_PROMPT_WORDS)
        return len(prompt) >= min_chars and len(prompt.split()) >= min_words

    _PROMPT_MAX_HEIGHT = 400

    def _adjust_prompt_height(self):



        self._prompt_input.setFixedHeight(
            self._snapped_prompt_height(self._prompt_input, min_h=60)
        )

    def _adjust_result_prompt_height(self):

        self._result_prompt_input.setFixedHeight(
            self._snapped_prompt_height(self._result_prompt_input, min_h=50)
        )

    @classmethod
    def _snapped_prompt_height(cls, text_edit: QTextEdit, min_h: int) -> int:



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

        prompt = self._result_prompt_input.toPlainText().strip()
        if not prompt:
            return
        if not self._prompt_meets_minimum(prompt):
            self._show_status_box(_min_prompt_warning(), "warning")
            return

        self._prompt_input.setPlainText(prompt)
        self._hide_status_box()
        self._dismiss_markup_prompt_tip()
        self.retry_clicked.emit(prompt)

    def _on_generate_clicked(self):



        button = getattr(self, "_generate_btn", None)
        if button is not None and (button.isHidden() or not button.isEnabled()):
            return
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
        if not self._generate_btn.isVisible():


            launch = getattr(self, "_launch_btn", None)
            if (
                launch is not None
                and launch.isVisible()
                and launch.isEnabled()
                and self._launch_section.isVisible()
            ):
                launch.click()
            return
        if not self._generate_btn.isEnabled():
            return
        self._on_generate_clicked()

    def _update_generate_enabled(self, prompt: str | None = None):
        text = self.get_prompt() if prompt is None else prompt


        reason = self._generate_block_reason(text)



        enabled = reason is None and not self._imagery_loading
        self._generate_btn.setEnabled(enabled)
        self.set_generate_block_reason(reason)
        self._update_generate_style()
        self._update_generate_button_text()


        self._update_markup_prompt_tip()

    def _generate_block_reason(self, prompt: str) -> str | None:






        if not self._zone_selected:
            return GENERATE_BLOCK_NO_ZONE
        if not prompt:
            return GENERATE_BLOCK_PROMPT_EMPTY
        if not self._prompt_meets_minimum(prompt):
            return GENERATE_BLOCK_PROMPT_TOO_SHORT
        return None

    def set_imagery_loading(self, loading: bool):





        self._imagery_loading = bool(loading)
        self._update_generate_enabled()
        self._update_generate_button_text()



        schedule = getattr(self, "_schedule_layer_warning_update", None)
        if schedule is not None:
            schedule()

    def _update_generate_style(self):





        wall = getattr(self, "_trial_info_box", None)
        wall_up = wall is not None and wall.isVisible()
        state = (self._generate_btn.isEnabled(), wall_up)
        if getattr(self, "_generate_style_state", None) == state:
            return
        self._generate_style_state = state
        self._generate_btn.setStyleSheet(BTN_GHOST_WIDE_QSS if wall_up else BTN_PRIMARY_WIDE_QSS)

    def _update_launch_style(self) -> None:




        wall = getattr(self, "_trial_info_box", None)
        wall_up = wall is not None and wall.isVisible()
        if getattr(self, "_launch_style_wall", None) is wall_up:
            return
        self._launch_style_wall = wall_up
        self._launch_btn.setStyleSheet(BTN_GHOST_WIDE_QSS if wall_up else BTN_PRIMARY_WIDE_QSS)

    def _update_result_generate_enabled(self, prompt: str | None = None):






        if prompt is None:
            prompt = self._result_prompt_input.toPlainText().strip()
        enabled = bool(prompt)
        self._result_regenerate_btn.setEnabled(enabled)
        if getattr(self, "_result_generate_style_state", None) is enabled:
            return
        self._result_generate_style_state = enabled
        self._result_regenerate_btn.setStyleSheet(BTN_PRIMARY_WIDE_QSS)
