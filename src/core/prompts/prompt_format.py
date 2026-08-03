"""Template-prompt layout for the textbox.

Split out of prompt_presets.py (which re-exports format_template_prompt so
existing callers keep importing through the facade).
"""
from __future__ import annotations

import re

_HEX_PAREN_RX = re.compile(r"\(#[0-9A-Fa-f]{3,6}\)")
_SENTENCE_SPLIT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_BULLET_LINE_RX = re.compile(r"^\s*[-*•]\s+")
_ITEM_SEP_RX = re.compile(r"^\s*,?\s*(?:and\s+|et\s+|y\s+|e\s+)?", re.IGNORECASE)
_LEAD_VERB_RX = re.compile(
    r"\b("
    r"render|draw|show|paint|color|colour|fill|mark|highlight|label|outline|map|"
    r"depict|illustrate|display|simulate|trace|"
    r"rendre|dessiner|afficher|peindre|colorier|colorer|remplir|marquer|cartographier|"
    r"renderizar|dibujar|mostrar|pintar|colorear|rellenar|marcar|mapear|"
    r"desenhar|colorir|preencher|destacar"
    r")\b",
    re.IGNORECASE,
)


def _find_top_level_comma(text: str) -> int | None:
    depth = 0
    for i, c in enumerate(text):
        if c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif c == "," and depth == 0:
            return i
    return None


def _split_lead_from_first_item(first_item: str) -> tuple[str | None, str]:
    """Pull a lead-in off the first item so the bulleted list reads as a
    parallel structure. Lead carries the verb when one is found, so each
    bullet can drop into the same grammar."""
    m = _LEAD_VERB_RX.search(first_item)
    if m:
        lead = first_item[: m.end()].rstrip()
        rest = first_item[m.end():].lstrip(" ,;:")
        if rest:
            return lead, rest
    comma_pos = _find_top_level_comma(first_item)
    if comma_pos is not None and comma_pos > 0:
        lead = first_item[:comma_pos].rstrip()
        rest = first_item[comma_pos + 1:].lstrip()
        if rest:
            return lead, rest
    return None, first_item


def _bulletize_color_list(sentence: str) -> str | None:
    """Turn a comma-separated color list into a bulleted block.

    Returns None when the sentence has fewer than 2 hex codes - those keep
    their prose form so we don't bulletize single-color rules."""
    hex_matches = list(_HEX_PAREN_RX.finditer(sentence))
    if len(hex_matches) < 2:
        return None

    items: list[str] = []
    last = 0
    for m in hex_matches:
        chunk = sentence[last:m.end()]
        if items:
            chunk = _ITEM_SEP_RX.sub("", chunk, count=1)
        items.append(chunk.strip())
        last = m.end()
    trailing = sentence[last:].strip()

    lead, first_rest = _split_lead_from_first_item(items[0])
    items[0] = first_rest

    bullet_block = "\n".join(f"- {it}" for it in items)
    if lead:
        lead = lead.rstrip(",. ").strip()
        result = f"{lead}:\n\n{bullet_block}" if lead else bullet_block
    else:
        result = bullet_block

    if trailing:
        trailing = re.sub(r"^[.,;:\s]+", "", trailing)
        if trailing:
            result = f"{result}\n\n{trailing}"
    return result


def _format_text_block(block: str) -> list[str]:
    """Split a prose block into formatted paragraphs (each its own string).
    Sentences with 2+ hex codes become bullet lists; the rest stay as prose."""
    out: list[str] = []
    for raw in _SENTENCE_SPLIT_RX.split(block):
        sentence = raw.strip()
        if not sentence:
            continue
        bulleted = _bulletize_color_list(sentence)
        out.append(bulleted or sentence)
    return out


def format_template_prompt(prompt: str) -> str:
    """Lay out a template prompt for the textbox.

    Splits prose into one paragraph per sentence and turns any
    comma-separated color list into a bulleted block. Bullet groups that
    already ship in the source (server templates with explicit "\n- "
    lines) are preserved as-is so the source stays the source of truth."""
    if not prompt:
        return prompt
    text = prompt.strip()
    if not text:
        return text

    paragraphs: list[str] = []
    pending_text: list[str] = []
    pending_bullets: list[str] = []

    def flush_text() -> None:
        if pending_text:
            joined = " ".join(pending_text).strip()
            pending_text.clear()
            if joined:
                paragraphs.extend(_format_text_block(joined))

    def flush_bullets() -> None:
        if pending_bullets:
            paragraphs.append("\n".join(f"- {b}" for b in pending_bullets))
            pending_bullets.clear()

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush_text()
            flush_bullets()
            continue
        if _BULLET_LINE_RX.match(line):
            flush_text()
            pending_bullets.append(_BULLET_LINE_RX.sub("", line, count=1).strip())
        else:
            flush_bullets()
            pending_text.append(line)
    flush_text()
    flush_bullets()

    return "\n\n".join(paragraphs)
