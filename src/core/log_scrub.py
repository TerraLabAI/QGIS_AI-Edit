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

import re

# Username segment of any user-home path, all OS spellings, any case:
# /Users/<x>, /home/<x>, C:\Users\<x>, \\host\Users\<x>. The prefix is kept
# (\1) so the log line still shows WHERE the path pointed, only who is hidden.
# The segment deliberately runs to the next separator, NOT the next space:
# a "C:\Users\John Doe" username must be swallowed whole, even if that also
# eats a following word in prose (over-scrubbing is the safe direction).
_USER_PATH_RE = re.compile(r"(?i)([/\\](?:Users|home)[/\\])[^/\\]+")

# URLs and bare multi-dot host[:port] tokens. Kept intentionally broad so a
# server error body or an infrastructure incident page never leaks an
# endpoint or hosting-provider hostname into logs and bug reports.
_URL_RE = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+){2,}(?::\d+)?", re.IGNORECASE)


def scrub_user_paths(text: str) -> str:
    """Replace the username in any user-home path with ***."""
    return _USER_PATH_RE.sub(r"\1***", text or "")


def scrub_urls(text: str) -> str:
    """Mask URLs and host-shaped tokens with <url>."""
    return _URL_RE.sub("<url>", text or "")
