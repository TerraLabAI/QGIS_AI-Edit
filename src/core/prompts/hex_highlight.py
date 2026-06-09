"""Shared hex-color helpers for prompt text.

A hex code like `#FF0000` is painted with that color as its background (text
flipped black/white per luminance) so a color list in a template is visually
scannable. The dock's live editor uses these via a QSyntaxHighlighter; the
prompt-library detail popup renders static prompt text to HTML with the same
look. Both go through here so the regex and contrast rule stay in one place.
"""

from __future__ import annotations

import html
import re

HEX_RX = re.compile(r"#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b")


def expand_hex(hex_text: str) -> str:
    """Expand `#RGB` to `#RRGGBB`. Returns input unchanged for 6-digit hex."""
    h = hex_text.lstrip("#")
    if len(h) == 3:
        return "#" + "".join(c * 2 for c in h)
    return "#" + h


def contrast_text_for(hex_text: str) -> str:
    """Pick black or white text for readability against `hex_text` background.
    Uses standard relative-luminance threshold."""
    h = expand_hex(hex_text).lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#000000" if luminance > 0.55 else "#FFFFFF"


def prompt_to_hex_html(text: str) -> str:
    """Escape `text` for rich-text display and wrap each hex code in a colored
    span (its own color as background, contrasting bold text)."""
    out: list[str] = []
    last = 0
    for match in HEX_RX.finditer(text):
        out.append(html.escape(text[last:match.start()]))
        hex_text = match.group(0)
        bg = expand_hex(hex_text)
        fg = contrast_text_for(hex_text)
        out.append(
            f'<span style="background-color:{bg}; color:{fg}; '
            f'font-weight:bold;">{html.escape(hex_text)}</span>'
        )
        last = match.end()
    out.append(html.escape(text[last:]))
    return "".join(out).replace("\n", "<br>")
