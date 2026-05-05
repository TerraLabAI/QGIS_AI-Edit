




from __future__ import annotations





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

    return "".join((sequence or "").split()).upper()


def pick_shortcut_sequence(candidates: tuple[str, ...] | list[str], taken: set[str]) -> str:



    taken_norm = {normalize_key_text(s) for s in taken if s}
    for candidate in candidates:
        if normalize_key_text(candidate) not in taken_norm:
            return candidate
    return candidates[0]


def pick_dock_shortcuts(taken: set[str]) -> dict[str, str]:

    used = set(taken)
    out: dict[str, str] = {}
    for action, candidates in SHORTCUT_CANDIDATES.items():
        seq = pick_shortcut_sequence(candidates, used)
        out[action] = seq
        used.add(seq)
    return out


def paste_kind_for_mime(has_file_paths: bool, text: str, has_image: bool) -> str:






    if has_file_paths:
        return PASTE_FILES
    if (text or "").strip():
        return PASTE_TEXT
    if has_image:
        return PASTE_IMAGE
    return PASTE_DEFAULT
