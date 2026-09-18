"""Shared scrubbers for anything that leaves the machine or the session.

Telemetry error messages, QGIS log lines and copied bug reports all carry
free-form text (exception messages, tracebacks, server bodies) that can embed
the OS username inside a path, or a network endpoint. Every emitting site
imports these two helpers; keeping one implementation is what guarantees the
patterns stay in sync (the previous per-site copies had drifted: the Linux
/home/<user> form was missed by two of them).

Import this module core-to-core with a plain import, never inside a
try/except: a broken scrubber must fail loudly, not silently ship usernames.
"""
from __future__ import annotations

import os
import re

# Username segment of any user-home path, all OS spellings, any case:
# /Users/<x>, /home/<x>, C:\Users\<x>, \\host\Users\<x>. The prefix is kept
# (\1) so the log line still shows WHERE the path pointed, only who is hidden.
# The segment deliberately runs to the next separator, NOT the next space:
# a "C:\Users\John Doe" username must be swallowed whole, even if that also
# eats a following word in prose (over-scrubbing is the safe direction).
# Separators come in runs: str(OSError) on Windows prints the filename with
# repr(), so the text reads C:\\Users\\Martin with doubled backslashes.
_USER_PATH_RE = re.compile(r"(?i)([/\\]+(?:Users|home)[/\\]+)[^/\\]+")

# URLs and bare multi-dot host[:port] tokens. Kept intentionally broad so a
# server error body or an infrastructure incident page never leaks an
# endpoint or hosting-provider hostname into logs and bug reports.
_URL_RE = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+){2,}(?::\d+)?", re.IGNORECASE)


def _local_username_re() -> re.Pattern | None:
    # Windows profiles also live outside \Users (D:\<name>\maps, a roaming
    # share, DOMAIN\<name> in an access-denied message). Hiding the account
    # name itself covers those; short names are skipped so "adm" or "gis"
    # never blank out ordinary words.
    name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if len(name) < 4:
        return None
    return re.compile(r"(?i)(?<!\w)" + re.escape(name) + r"(?!\w)")


_LOCAL_USERNAME_RE = _local_username_re()


def scrub_user_paths(text: str) -> str:
    """Replace the username in any user-home path with ***."""
    out = _USER_PATH_RE.sub(r"\1***", text or "")
    if _LOCAL_USERNAME_RE is not None:
        out = _LOCAL_USERNAME_RE.sub("***", out)
    return out


def scrub_urls(text: str) -> str:
    """Mask URLs and host-shaped tokens with <url>."""
    return _URL_RE.sub("<url>", text or "")


# A whole file path, for text that leaves the machine (telemetry): a drive or UNC
# path, or an absolute POSIX path of two segments or more, up to a quote or the end
# of the line. Folder names with spaces are common ("OneDrive/Documents/Site plans"),
# so the match runs past spaces and may eat the words after the path.
_FILE_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^'\"\n]*|(?<![\w:/<])/(?:[^/\s'\"]+/)+[^'\"\n]*")


def scrub_file_paths(text: str) -> str:
    """Mask every file path with <path>."""
    return _FILE_PATH_RE.sub("<path>", text or "")


_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s\"'<>;,]+")
_SECRET_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|activation[_-]?key|"
    r"password|authorization)\b[\"']?\s*[:=]\s*[\"']?)[^\s\"'<>;,}]+"
)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")


def scrub_secrets(text: str) -> str:
    """Remove credential-shaped values without discarding diagnostic field names."""
    text = _BEARER_RE.sub("Bearer <redacted>", text or "")
    text = _SECRET_RE.sub(r"\1<redacted>", text)
    return _JWT_RE.sub("<redacted>", text)
