"""Detail popup for a prompt-library card.

Opened when the user clicks an inline card (a curated template OR one of their
past generations). Mirrors the dashboard detail view: a large before/after
slider on the left (expandable to fullscreen), and an info panel on the right
with the full prompt, the reference image(s), generation metadata, and the
actions that fit the card type.

Two modes, chosen by what the caller passes:
  - ``preset`` (curated template): demo slider + prompt + "Use this prompt".
  - ``job`` (past generation): real before/after + reference thumbnails +
    resolution/ratio/duration/date + Use (full restore) / Add to map /
    Download input+output / favorite.

The dialog never applies anything itself. It records an outcome the parent
library dialog reads after ``exec()`` (so nested modal event loops stay sane),
and forwards add-to-map / download / favorite through callbacks. Confidential
details (provider names, system preprompt, internal URLs) are never shown.

Implementation lives in the ``generation_detail`` package.
"""
from .generation_detail import GenerationDetailDialog

__all__ = ["GenerationDetailDialog"]
