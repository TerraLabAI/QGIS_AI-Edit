
from __future__ import annotations

from ...dock import design_tokens as _T


_BTN_PRIMARY_WIDE_QSS = _T.BTN_PRIMARY_WIDE_QSS
_BTN_QUIET_QSS = _T.BTN_QUIET_QSS


_BTN_DONE_PRIMARY_QSS = _T.BTN_PRIMARY_QSS
_BTN_DONE_GHOST_QSS = _T.BTN_GHOST_QSS


_BTN_PICK_QSS = _T.BTN_GHOST_QSS + _T.picked_qss("QPushButton")


__all__ = [
    "_BTN_DONE_GHOST_QSS",
    "_BTN_DONE_PRIMARY_QSS",
    "_BTN_PICK_QSS",
    "_BTN_PRIMARY_WIDE_QSS",
    "_BTN_QUIET_QSS",
]
