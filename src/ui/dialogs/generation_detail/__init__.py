"""Detail popup for a prompt-library card, split by concern.

Modules: ``styles`` (icons + stylesheets), ``widgets`` (aspect box, reference
thumb, lightbox), ``build`` (UI construction), ``images`` (loader wiring),
``dialog`` (the GenerationDetailDialog class itself).
"""
from .dialog import GenerationDetailDialog

__all__ = ["GenerationDetailDialog"]
