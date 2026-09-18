





from __future__ import annotations

import re
from urllib.parse import urlsplit




SERVED_URL_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f-\x9f<>\"'\\]")


def is_plain_https_url(value) -> bool:



    if not isinstance(value, str) or not value.startswith("https://"):
        return False
    if SERVED_URL_FORBIDDEN_RE.search(value):
        return False
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return False
    return bool(parts.hostname) and not parts.username and not parts.password and (
        port is None or 1 <= port <= 65535)
