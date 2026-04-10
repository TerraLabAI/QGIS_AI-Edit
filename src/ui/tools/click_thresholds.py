






from __future__ import annotations


def is_step_too_small(dx: float, dy: float, min_step_px: float) -> bool:



    return abs(dx) < min_step_px and abs(dy) < min_step_px


def is_near_point(dx: float, dy: float, close_px: float) -> bool:



    return abs(dx) <= close_px and abs(dy) <= close_px
