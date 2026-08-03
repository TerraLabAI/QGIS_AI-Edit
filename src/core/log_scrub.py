











from __future__ import annotations

import os
import re









_USER_PATH_RE = re.compile(r"(?i)([/\\]+(?:Users|home)[/\\]+)[^/\\]+")




_URL_RE = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+){2,}(?::\d+)?", re.IGNORECASE)


def _local_username_re() -> re.Pattern | None:




    name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(name) < 4:
        return None
    return re.compile(r"(?i)(?<!\w)" + re.escape(name) + r"(?!\w)")


_LOCAL_USERNAME_RE = _local_username_re()


def scrub_user_paths(text: str) -> str:

    out = _USER_PATH_RE.sub(r"\1***", text or "")
    if _LOCAL_USERNAME_RE is not None:
        out = _LOCAL_USERNAME_RE.sub("***", out)
    return out


def scrub_urls(text: str) -> str:

    return _URL_RE.sub("<url>", text or "")






_FILE_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^'\"\n]*|(?<![\w:/<])/(?:[^/\s'\"]+/)+[^'\"\n]*")


def scrub_file_paths(text: str) -> str:

    return _FILE_PATH_RE.sub("<path>", text or "")


_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s\"'<>;,]+")
_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|activation[_-]?key|"
    r"password|authorization)\b[\"']?\s*[:=]\s*[\"']?)[^\s\"'<>;,}]+"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def scrub_secrets(text: str) -> str:

    text = _BEARER_RE.sub("Bearer <redacted>", text or "")
    text = _SECRET_RE.sub(r"\1<redacted>", text)
    return _JWT_RE.sub("<redacted>", text)
