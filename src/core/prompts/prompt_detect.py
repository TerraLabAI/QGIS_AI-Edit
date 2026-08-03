



















from __future__ import annotations

import re

from ..config_store import get_export_block, get_export_dial_seq, get_export_dial_str





_MAX_SERVED_PATTERNS = 12
_MAX_PATTERN_CHARS = 120
_MAX_SERVED_SCAN_CHARS = 1000


_MAX_TABLE_PATTERNS = 8
_MAX_TABLE_PATTERN_CHARS = 4000
_MAX_TABLE_SCAN_CHARS = 4000


_NESTED_QUANTIFIER_RE = re.compile(r"\([^()]*[+*][^()]*\)\s*(?:[+*]|\{\d*,\})|[+*]{2,}")

_compiled_extras: dict[tuple[str, tuple[str, ...]], tuple] = {}


def _compile_all(patterns, max_chars: int) -> tuple:


    compiled = []
    for pattern in patterns:
        if len(pattern) > max_chars or _NESTED_QUANTIFIER_RE.search(pattern):
            continue
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except (re.error, RecursionError, OverflowError, ValueError):
            continue
    return tuple(compiled)


def _cached(key: tuple, build) -> tuple:



    cached = _compiled_extras.get(key)
    if cached is not None:
        return cached
    out = build()
    if len(_compiled_extras) >= 32:
        _compiled_extras.pop(next(iter(_compiled_extras)), None)
    _compiled_extras[key] = out
    return out


def _served_table(name: str) -> tuple:

    block = get_export_block("prompt_tables")
    raw = block.get(name) if block is not None else None
    if not isinstance(raw, (list, tuple)):
        return ()
    patterns = tuple(p for p in raw[:_MAX_TABLE_PATTERNS] if isinstance(p, str) and p.strip())
    if not patterns:
        return ()
    return _cached(("table:" + name, patterns), lambda: _compile_all(patterns, _MAX_TABLE_PATTERN_CHARS))


def _served_patterns(cfg_key: str) -> tuple:

    raw = get_export_dial_seq(cfg_key, (), max_len=_MAX_SERVED_PATTERNS)
    if not raw:
        return ()
    return _cached((cfg_key, raw), lambda: _compile_all(raw, _MAX_PATTERN_CHARS))


def _search_all(patterns, text: str) -> bool:
    for rx in patterns:
        try:
            if rx.search(text):
                return True
        except Exception:  # nosec B112
            continue
    return False


def _matches(name: str, text: str) -> bool:


    if not text:
        return False
    if _search_all(_served_table(name), text[:_MAX_TABLE_SCAN_CHARS]):
        return True
    return _search_all(_served_patterns("prompt_rules." + name), text[:_MAX_SERVED_SCAN_CHARS])



_FREEFORM_VECTOR_COLOR = "#FF0000"
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def freeform_vector_color() -> str:






    served = get_export_dial_str("prompt_rules.vector_color", _FREEFORM_VECTOR_COLOR)
    return served.upper() if _HEX_COLOR_RE.match(served) else _FREEFORM_VECTOR_COLOR


def detect_freeform_vector_intent(prompt_text: str) -> str | None:










    if not prompt_text:
        return None
    text = prompt_text.strip()
    if not text:
        return None
    if _matches("lulc", text):
        return None
    if _matches("color_word", text):
        return None
    if not _matches("detect_verb", text):
        return None
    return freeform_vector_color()


def detect_seg_context(prompt_text: str) -> bool:








    if not prompt_text:
        return False
    text = prompt_text.strip()
    if not text:
        return False
    return (
        _matches("lulc", text)
        or _matches("detect_verb", text)
        or _matches("seg_style", text)
    )


def detect_prompt_guidance(prompt_text: str, has_template: bool = False) -> str | None:
















    if has_template:
        return None
    text = (prompt_text or "").strip()
    if len(text) < 4:
        return None
    if _matches("guidance_vector_file", text):
        return "vector_file"
    if _matches("guidance_measure", text):
        return "measure"
    if _matches("guidance_qa", text):
        return "qa"
    return None
