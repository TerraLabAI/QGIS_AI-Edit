"""Vectorize panel widget.

Self-contained QWidget that runs the color-based raster-to-polygon
workflow. Implementation lives in the ``vectorize`` package; this module
keeps the historical import path stable.
"""
from __future__ import annotations

from .vectorize.layer_filters import (  # noqa: F401
    _is_ai_edit_output,
    _is_visible_ai_edit_output,
)
from .vectorize.panel import VectorizePanel

__all__ = ["VectorizePanel"]
