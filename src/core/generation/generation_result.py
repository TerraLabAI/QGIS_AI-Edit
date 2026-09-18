"""Value returned by the generation pipeline."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GenerationResult:
    success: bool
    image_url: str | None = None
    error: str | None = None
    error_code: str | None = None
    request_id: str | None = None
