"""How small the dock is allowed to get.

QGIS lets several panels share one dock area, and the splitter between them
will take a panel down to its title bar. This one had a minimum width and no
minimum height, so a neighbour growing pushed it down to the header and cut
off the prompt box and the button under it (Yvann, 2026-09-17). AI
Segmentation carries the same floor, written the same way.

One number, derived from the font rather than fixed, because what it has to
keep on screen is itself font-scaled.
"""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QWidget

# What the floor has to fit: the header row, the hero line, the prompt box and
# its primary button, plus enough of the next line that the panel does not look
# cut off. Counted in text lines so it tracks the font, with a pixel floor for
# the small-font case.
_FLOOR_TEXT_LINES = 16
_FLOOR_MIN_PX = 260


def dock_minimum_height(widget: QWidget) -> int:
    """The smallest height that still shows the panel's primary action."""
    try:
        line_px = widget.fontMetrics().height()
    except Exception:  # noqa: BLE001 -- a dock without metrics takes the floor
        return _FLOOR_MIN_PX
    return max(_FLOOR_MIN_PX, int(line_px) * _FLOOR_TEXT_LINES)
