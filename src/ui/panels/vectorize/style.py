"""Button stylesheets for the Vectorize panel, on the AI Agent line."""
from __future__ import annotations

from ...dock import design_tokens as _T

# The setup page's one wide primary (Vectorize) and the quiet way back.
_BTN_PRIMARY_WIDE_QSS = _T.BTN_PRIMARY_WIDE_QSS
_BTN_QUIET_QSS = _T.BTN_QUIET_QSS
# Done, bottom right like Draw's and References' Done: the primary once the
# polygons exist, an outline next to the green Vectorize on the setup page.
_BTN_DONE_PRIMARY_QSS = _T.BTN_PRIMARY_QSS
_BTN_DONE_GHOST_QSS = _T.BTN_GHOST_QSS
# The eyedropper: an outline pill at rest, the shared picked look while it
# waits for a click on the map (instead of the ghost's blue checked wash).
_BTN_PICK_QSS = _T.BTN_GHOST_QSS + _T.picked_qss("QPushButton")

# Public surface, so the private names read as used.
__all__ = [
    "_BTN_DONE_GHOST_QSS",
    "_BTN_DONE_PRIMARY_QSS",
    "_BTN_PICK_QSS",
    "_BTN_PRIMARY_WIDE_QSS",
    "_BTN_QUIET_QSS",
]
