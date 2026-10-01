










from __future__ import annotations

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_export_copy
from ...core.i18n import tr
from ...core.prompts.prompt_minimum import (
    PROMPT_EMPTY,
    PROMPT_TOO_SHORT,
    PromptMinimumCheck,
    PromptMinimumDials,
    prompt_minimum_dials,
)
from .design_tokens import HINT_QSS, INK_2


__all__ = [
    "_BLOCK_REASON_QSS",
    "BLOCK_REASON_INK",
    "cjk_prompt_minimum_text",
    "DockBlockedReasonsMixin",
    "GENERATE_BLOCK_NO_ZONE",
    "GENERATE_BLOCK_PROMPT_EMPTY",
    "GENERATE_BLOCK_PROMPT_TOO_SHORT",
    "generate_block_text",
    "LAUNCH_BLOCK_NO_KEY",
    "LAUNCH_BLOCK_NO_RASTER",
    "launch_block_text",
    "LAUNCH_BLOCK_TILES_WARMING",
    "LAUNCH_BLOCK_WORKER_BUSY",
]


LAUNCH_BLOCK_NO_RASTER = "no_raster"
LAUNCH_BLOCK_NO_KEY = "no_key"
LAUNCH_BLOCK_TILES_WARMING = "tiles_warming"
LAUNCH_BLOCK_WORKER_BUSY = "worker_busy"


GENERATE_BLOCK_NO_ZONE = "no_zone"
GENERATE_BLOCK_PROMPT_EMPTY = PROMPT_EMPTY
GENERATE_BLOCK_PROMPT_TOO_SHORT = PROMPT_TOO_SHORT


BLOCK_REASON_INK = INK_2
_BLOCK_REASON_QSS = HINT_QSS


def launch_block_text(reason: str) -> str:

    if reason == LAUNCH_BLOCK_NO_RASTER:
        return get_export_copy("dock.blocked_reasons.no_raster", tr("Add imagery first"))
    if reason == LAUNCH_BLOCK_NO_KEY:
        return get_export_copy("dock.blocked_reasons.no_key", tr("Checking your account..."))
    if reason == LAUNCH_BLOCK_TILES_WARMING:
        return get_export_copy("dock.blocked_reasons.tiles_warming", tr("Loading imagery..."))
    if reason == LAUNCH_BLOCK_WORKER_BUSY:
        return get_export_copy("dock.blocked_reasons.worker_busy", tr("An edit is already running"))
    return ""


def cjk_prompt_minimum_text(dials: PromptMinimumDials) -> str:


    return tr("Please describe what you want to change (at least {chars} characters).").replace(
        "{chars}", str(dials.min_cjk_chars)
    )


def generate_block_text(reason: str, prompt_check: PromptMinimumCheck | None = None) -> str:




    if reason == GENERATE_BLOCK_NO_ZONE:
        return get_export_copy("dock.blocked_reasons.no_zone", tr("Draw a zone on the map first"))
    if reason == GENERATE_BLOCK_PROMPT_EMPTY:





        return ""
    if reason == GENERATE_BLOCK_PROMPT_TOO_SHORT:
        dials = prompt_check.dials if prompt_check is not None else prompt_minimum_dials()
        if prompt_check is not None and prompt_check.cjk_chars:
            return cjk_prompt_minimum_text(dials)
        return tr(
            "Say a bit more: at least {chars} characters and {words} words."
        ).format(chars=dials.min_chars, words=dials.min_words)
    return ""


class DockBlockedReasonsMixin:





    def _track_block_reason(self, event: str, reason: str) -> None:

        seen = getattr(self, "_block_reasons_emitted", None)
        if seen is None:
            seen = set()
            self._block_reasons_emitted = seen
        key = event + ":" + reason
        if key in seen:
            return
        seen.add(key)
        telemetry.track(event, {"reason": reason})

    def is_generation_in_flight(self) -> bool:


        try:
            return bool(self._progress_widget.isVisible())
        except (RuntimeError, AttributeError):
            return False

    def reset_block_reason_memory(self) -> None:

        self._block_reasons_emitted = set()

    def set_launch_block_reason(
        self, reason: str | None, has_own_card: bool = False
    ) -> None:







        label = getattr(self, "_launch_reason_label", None)
        self._launch_btn.setVisible(not has_own_card)
        self._launch_btn.setEnabled(reason is None)
        if label is not None:
            text = "" if has_own_card else launch_block_text(reason or "")
            label.setText(text)
            label.setVisible(bool(text))
        if reason:
            self._track_block_reason(te.LAUNCH_BLOCKED, reason)

    def set_generate_block_reason(
        self, reason: str | None, prompt_check: PromptMinimumCheck | None = None
    ) -> None:






        label = getattr(self, "_generate_reason_label", None)
        if label is not None:




            shown = reason and not self._generate_btn.isHidden()
            text = generate_block_text(reason, prompt_check) if shown else ""
            label.setText(text)
            label.setVisible(bool(text))
        if reason:
            self._track_block_reason(te.GENERATE_BLOCKED, reason)
