

















from __future__ import annotations

import re
from dataclasses import dataclass

from ..config_store import ConfigMissing, require_dial


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
    dials: PromptMinimumDials | None


def prompt_minimum_dials() -> PromptMinimumDials | None:

    try:
        return PromptMinimumDials(
            min_chars=int(require_dial("limits.min_prompt_chars", lo=0)),
            min_words=int(require_dial("limits.min_prompt_words", lo=0)),
            min_cjk_chars=int(require_dial("limits.min_prompt_cjk_chars", lo=0)),
        )
    except ConfigMissing:
        return None


def served_max_prompt_chars() -> int | None:



    try:
        return int(require_dial("limits.max_prompt_chars", lo=1))
    except ConfigMissing:
        return None


def check_prompt_minimum(prompt: str | None) -> PromptMinimumCheck:

    dials = prompt_minimum_dials()
    text = (prompt or "").strip()
    if not text:
        return PromptMinimumCheck(False, PROMPT_EMPTY, 0, dials)
    cjk = len(_CJK_RE.findall(text))
    if dials is None:
        return PromptMinimumCheck(True, None, cjk, None)
    if cjk and cjk >= dials.min_cjk_chars:
        return PromptMinimumCheck(True, None, cjk, dials)
    ok = len(text) >= dials.min_chars and len(text.split()) >= dials.min_words
    return PromptMinimumCheck(ok, None if ok else PROMPT_TOO_SHORT, cjk, dials)
