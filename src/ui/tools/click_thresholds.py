"""Screen-space click thresholds shared by the click-per-vertex map tools.

The zone PolygonSelectionTool and the markup LineMapTool ask the same two
questions of a pointer offset, and AI Segmentation's PolygonZoneMapTool asks
them the same way, so one drawing grammar means one copy of each test. Pure
functions, no qgis import: the headless layer can exercise them.
"""
from __future__ import annotations


def is_step_too_small(dx: float, dy: float, min_step_px: float) -> bool:
    """True when a screen-space offset is a jitter / double-click repeat, not
    a deliberate new vertex. Strict less-than on both axes, mirroring AI
    Segmentation's PolygonZoneMapTool._add_point dedup check."""
    return abs(dx) < min_step_px and abs(dy) < min_step_px


def is_near_point(dx: float, dy: float, close_px: float) -> bool:
    """True when a screen-space offset is within the close-to-vertex range
    (inclusive on both axes), mirroring AI Segmentation's
    PolygonZoneMapTool._near_first."""
    return abs(dx) <= close_px and abs(dy) <= close_px
