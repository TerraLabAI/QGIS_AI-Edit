"""ASCII-only slugs shared by output file names and GeoPackage table names.

GDAL mishandles unicode paths in some Windows locales (WRITE_ERROR), and the
GeoPackage spec wants lowercase [a-z0-9_] table names, so one folding rule
serves both.
"""
from __future__ import annotations

import re
import unicodedata


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "_", text)
    return text.strip("_")
