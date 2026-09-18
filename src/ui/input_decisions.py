"""Pure keyboard and clipboard decisions, kept free of Qt so tests run headless.

The Qt halves live next to their widgets: the dock footer collects the key
sequences QGIS already holds, the prompt box reads the clipboard mime data.
"""
from __future__ import annotations

# Ordered candidates per dock action; the first free one wins. Letters are
# the ones QGIS menus rarely use as mnemonics in any shipped locale. No
# Alt+Shift (the Windows input-language switch) and no Ctrl+Alt (AltGr on
# European layouts types characters with it).
SHORTCUT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "markup": ("Alt+M", "Alt+K", "Alt+J"),
    "vectorize": ("Alt+V", "Alt+Q", "Alt+Z"),
    "swipe": ("Alt+B", "Alt+U", "Alt+Y"),
}

PASTE_FILES = "files"
PASTE_TEXT = "text"
PASTE_IMAGE = "image"
PASTE_DEFAULT = "default"


def normalize_key_text(sequence: str) -> str:
    """Case- and space-insensitive form of a portable key sequence string."""
    return "".join((sequence or "").split()).upper()


def pick_shortcut_sequence(candidates: tuple[str, ...] | list[str], taken: set[str]) -> str:
    """First candidate absent from ``taken`` (portable strings such as
    "Alt+V", compared without case). Falls back to the first candidate when
    every one is taken, so the action always keeps a key."""
    taken_norm = {normalize_key_text(s) for s in taken if s}
    for candidate in candidates:
        if normalize_key_text(candidate) not in taken_norm:
            return candidate
    return candidates[0]


def pick_dock_shortcuts(taken: set[str]) -> dict[str, str]:
    """One sequence per dock action, never the same one twice."""
    used = set(taken)
    out: dict[str, str] = {}
    for action, candidates in SHORTCUT_CANDIDATES.items():
        seq = pick_shortcut_sequence(candidates, used)
        out[action] = seq
        used.add(seq)
    return out


def paste_kind_for_mime(has_file_paths: bool, text: str, has_image: bool) -> str:
    """How the prompt box treats a paste or drop.

    Local image or geodata files become references. Non-empty text wins over
    a bitmap: Excel and OneNote on Windows put a picture of the cells next to
    the text, and the user wants the words. A bare image becomes a reference.
    """
    if has_file_paths:
        return PASTE_FILES
    if (text or "").strip():
        return PASTE_TEXT
    if has_image:
        return PASTE_IMAGE
    return PASTE_DEFAULT
