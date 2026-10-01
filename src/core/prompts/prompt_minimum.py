















from __future__ import annotations

import re
from dataclasses import dataclass

from ..config_store import get_export_dial


MIN_PROMPT_CHARS = 10
MIN_PROMPT_WORDS = 2
MIN_PROMPT_CJK_CHARS = 4


PROMPT_EMPTY = "prompt_empty"
PROMPT_TOO_SHORT = "prompt_too_short"

_CJK_RE = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]"
)


@dataclass(frozen=True)
class PromptMinimumDials:


    min_chars: int
    min_words: int
    min_cjk_chars: int


@dataclass(frozen=True)
class PromptMinimumCheck:






    ok: bool
    reason: str | None
    cjk_chars: int
    dials: PromptMinimumDials


def prompt_minimum_dials() -> PromptMinimumDials:
    return PromptMinimumDials(
        min_chars=get_export_dial("limits.min_prompt_chars", MIN_PROMPT_CHARS),
        min_words=get_export_dial("limits.min_prompt_words", MIN_PROMPT_WORDS),
        min_cjk_chars=get_export_dial("limits.min_prompt_cjk_chars", MIN_PROMPT_CJK_CHARS),
    )


def check_prompt_minimum(prompt: str | None) -> PromptMinimumCheck:

    dials = prompt_minimum_dials()
    text = (prompt or "").strip()
    if not text:
        return PromptMinimumCheck(False, PROMPT_EMPTY, 0, dials)
    cjk = len(_CJK_RE.findall(text))
    if cjk and cjk >= dials.min_cjk_chars:
        return PromptMinimumCheck(True, None, cjk, dials)
    ok = len(text) >= dials.min_chars and len(text.split()) >= dials.min_words
    return PromptMinimumCheck(ok, None if ok else PROMPT_TOO_SHORT, cjk, dials)
