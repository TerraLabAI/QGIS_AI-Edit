from __future__ import annotations

import re

from qgis.PyQt.QtWidgets import QTextEdit

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.number_format import format_count
from ...core.prompts.prompt_minimum import (
    PromptMinimumCheck,
    check_prompt_minimum,
    served_max_prompt_chars,
)
from ..onboarding_hint import (
    BLUE_TINT,
    HINT_MARKUP_PROMPT,
    DismissibleHint,
    dismiss_hint,
    is_hint_dismissed,
)
from .blocked_reasons import GENERATE_BLOCK_NO_ZONE, cjk_prompt_minimum_text
from .design_tokens import BTN_GHOST_WIDE_QSS, BTN_PRIMARY_WIDE_QSS
from .prompt_hint_requests import PromptHintRequester

_NUMBERS_RE = re.compile(r"\d+")


def _min_prompt_warning(check: PromptMinimumCheck) -> str:






    if check.cjk_chars:
        return cjk_prompt_minimum_text(check.dials)
    min_chars, min_words = check.dials.min_chars, check.dials.min_words
    sentence = tr("Please describe what you want to change (at least 10 characters, 2 words).")
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



        prompt, cut = self._enforce_prompt_max_length(self._prompt_input)
        self._show_prompt_cut_notice(cut, len(prompt))
        prompt = prompt.strip()
        self._update_generate_enabled(prompt)
        self._clear_active_template_if_empty(prompt)
        self._update_prompt_guidance_hint(prompt)

    def _guidance_message_for(self, kind: str | None) -> str | None:










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



        if kind in ("land_cover", "objects"):
            from ..segmentation_handoff import rich_line
            return rich_line(kind)
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
        if getattr(self, "_last_guidance_kind", None) in ("land_cover", "objects"):

            from ..segmentation_handoff import is_installed, run
            installed = is_installed()
            run()

            self._refresh_guidance_hints()
        else:
            installed = open_ai_segmentation()
        telemetry.track(te.SEG_REDIRECT_CLICKED, {
            "guidance_kind": getattr(self, "_last_guidance_kind", None) or "",
            "installed": installed,
        })
        telemetry.flush()

    def _prompt_hint_requester(self) -> PromptHintRequester:

        requester = getattr(self, "_prompt_hints", None)
        if requester is None:
            requester = PromptHintRequester(
                self._show_prompt_hint_kind, self._prompt_hint_client_and_auth, self
            )
            self._prompt_hints = requester
        return requester

    def _prompt_hint_client_and_auth(self) -> tuple:
        client = getattr(self, "_library_client", None)
        manager = getattr(self, "_library_auth_manager", None)
        try:
            auth = manager.get_auth_header() if manager is not None else None
        except Exception:  # noqa: BLE001
            auth = None
        return client, auth

    def _ask_prompt_hint(self, box: str, prompt: str) -> None:
        self._prompt_hint_requester().request(
            box, prompt, bool(self._active_template_id)
        )

    def _show_prompt_hint_kind(self, box: str, kind: str | None) -> None:



        self._last_guidance_kind = kind
        kinds = getattr(self, "_guidance_kind_by_box", None)
        if kinds is None:
            kinds = {}
            self._guidance_kind_by_box = kinds
        kinds[box] = kind
        try:
            if box == "result":
                self._apply_guidance_hint(
                    self._result_guidance_hint, self._guidance_message_for(kind)
                )
            else:
                self._apply_prompt_guidance_message(self._guidance_message_for(kind))
        except RuntimeError:  # nosec B110
            pass

    def _refresh_guidance_hints(self) -> None:


        kinds = getattr(self, "_guidance_kind_by_box", None) or {}
        if "prompt" in kinds:
            self._apply_prompt_guidance_message(self._guidance_message_for(kinds["prompt"]))
        if "result" in kinds:
            self._apply_guidance_hint(
                self._result_guidance_hint, self._guidance_message_for(kinds["result"])
            )

    def _update_prompt_guidance_hint(self, prompt: str | None = None) -> None:



        if prompt is None:
            prompt = self.get_prompt()
        self._ask_prompt_hint("prompt", prompt)

    def _apply_prompt_guidance_message(self, message: str | None) -> None:
        self._apply_guidance_hint(self._prompt_guidance_hint, message)



        tip = getattr(self, "_guide_ai_hint", None)
        if tip is not None and self._prompt_section.isVisibleTo(self):
            tip.setVisible(not message and self._guide_ai_tip_visible())

    def _update_result_guidance_hint(self, prompt: str | None = None) -> None:


        if prompt is None:
            prompt = self._result_prompt_input.toPlainText().strip()
        self._ask_prompt_hint("result", prompt)

    def set_zone_guidance(
        self,
        ground_resolution_m: float | None,
        area_km2: float | None = None,
        hints: dict | None = None,
    ) -> None:






        flags = hints if isinstance(hints, dict) else {}
        if flags.get("large_zone") is True and area_km2 is not None and area_km2 > 0:
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
        metres = ground_resolution_m
        if flags.get("coarse_zone") is not True or metres is None or metres <= 0:
            self._zone_guidance_hint.setVisible(False)
            return


        value = format_count(round(metres)) if metres >= 10 else f"{metres:.1f}"
        msg = get_export_copy("guidance.coarse_zone_m", tr(
            "At this zoom one pixel covers about {m} m. Zoom in or draw a "
            "smaller zone for buildings, trees or roads."
        )).replace("{m}", value)
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

        result, cut = self._enforce_prompt_max_length(self._result_prompt_input)
        if cut:

            max_chars = served_max_prompt_chars()
            if max_chars is not None:
                self._show_status_box(self._prompt_cut_text(max_chars), "warning")
        result = result.strip()
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
    def _prompt_cut_text(max_chars: int) -> str:
        return tr("Prompt cut to {count} characters, the most AI Edit accepts.").replace(
            "{count}", format_count(max_chars)
        )

    def _show_prompt_cut_notice(self, cut: bool, length: int) -> None:


        label = getattr(self, "_prompt_cut_notice", None)
        if label is None:
            return
        max_chars = served_max_prompt_chars()
        if max_chars is None:
            return
        if cut:
            label.setText(self._prompt_cut_text(max_chars))
            label.setVisible(True)
        elif label.isVisible() and length < max_chars:
            label.setVisible(False)

    @staticmethod
    def _enforce_prompt_max_length(text_edit: QTextEdit) -> tuple[str, bool]:




        max_chars = served_max_prompt_chars()
        plain = text_edit.toPlainText()
        if max_chars is None or len(plain) <= max_chars:
            return plain, False
        plain = plain[:max_chars]
        cursor_pos = text_edit.textCursor().position()
        text_edit.blockSignals(True)
        try:
            text_edit.setPlainText(plain)
            cursor = text_edit.textCursor()

            cursor.setPosition(min(cursor_pos, len(plain.encode("utf-16-le")) // 2))
            text_edit.setTextCursor(cursor)
        finally:
            text_edit.blockSignals(False)
        return plain, True

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
        check = check_prompt_minimum(prompt)
        if not check.ok:
            self._show_status_box(_min_prompt_warning(check), "warning")
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
        check = check_prompt_minimum(prompt)
        if not check.ok:
            self._show_status_box(_min_prompt_warning(check), "warning")
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


        check = check_prompt_minimum(text)
        reason = self._generate_block_reason(check)



        enabled = reason is None and not self._imagery_loading
        self._generate_btn.setEnabled(enabled)
        self.set_generate_block_reason(reason, check)
        self._update_generate_style()
        self._update_generate_button_text()


        self._update_markup_prompt_tip()

    def _generate_block_reason(self, check: PromptMinimumCheck) -> str | None:






        if not self._zone_selected:
            return GENERATE_BLOCK_NO_ZONE
        return check.reason

    def set_imagery_loading(self, loading: bool):





        self._imagery_loading = bool(loading)
        self._update_generate_enabled()
        self._update_generate_button_text()



        schedule = getattr(self, "_schedule_layer_warning_update", None)
        if schedule is not None:
            schedule()

    def _update_generate_style(self):





        wall = getattr(self, "_trial_info_box", None)
        wall_up = wall is not None and wall.isVisible() and wall.state == "free_out"
        state = (self._generate_btn.isEnabled(), wall_up)
        if getattr(self, "_generate_style_state", None) == state:
            return
        self._generate_style_state = state
        self._generate_btn.setStyleSheet(BTN_GHOST_WIDE_QSS if wall_up else BTN_PRIMARY_WIDE_QSS)

    def _update_launch_style(self) -> None:




        wall = getattr(self, "_trial_info_box", None)
        wall_up = wall is not None and wall.isVisible() and wall.state == "free_out"
        if getattr(self, "_launch_style_wall", None) is wall_up:
            return
        self._launch_style_wall = wall_up
        self._launch_btn.setStyleSheet(BTN_GHOST_WIDE_QSS if wall_up else BTN_PRIMARY_WIDE_QSS)

    def _update_result_generate_enabled(self, prompt: str | None = None):






        if prompt is None:
            prompt = self._result_prompt_input.toPlainText().strip()
        enabled = bool(prompt)
        self._result_regenerate_btn.setEnabled(enabled)

        wall = getattr(self, "_trial_info_box", None)
        wall_up = wall is not None and wall.isVisible() and wall.state == "free_out"
        state = (enabled, wall_up)
        if getattr(self, "_result_generate_style_state", None) == state:
            return
        self._result_generate_style_state = state
        self._result_regenerate_btn.setStyleSheet(BTN_GHOST_WIDE_QSS if wall_up else BTN_PRIMARY_WIDE_QSS)
