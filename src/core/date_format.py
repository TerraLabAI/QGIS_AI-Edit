"""Smart relative/absolute date formatting for the prompt library.

Returns:
    < 1 min   -> "just now"
    < 1 hour  -> "12 min ago"
    < 1 day   -> "3 h ago"
    1 day     -> "yesterday"
    < 7 days  -> "4 d ago"
    same yr   -> "15 May"
    older     -> "15 May 2024"

Month names + numbers are routed through QLocale so French/Spanish/Portuguese
QGIS installs render natively without us shipping a separate translation
dictionary per language.
"""
from __future__ import annotations

import datetime as dt

from qgis.PyQt.QtCore import QDate, QLocale

from .i18n import tr


def _parse_iso(ts: str) -> dt.datetime | None:
    if not ts:
        return None
    try:
        # Python <3.11 chokes on the trailing "Z" UTC marker.
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def format_smart_date(iso_ts: str) -> str:
    """Turn an ISO 8601 timestamp into a short, human-readable label."""
    parsed = _parse_iso(iso_ts)
    if parsed is None:
        return ""

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)

    seconds = max(0, int((now - parsed).total_seconds()))
    if seconds < 60:
        return tr("just now")
    if seconds < 3600:
        return tr("{n} min ago").format(n=seconds // 60)
    if seconds < 86400:
        return tr("{n} h ago").format(n=seconds // 3600)

    parsed_local = parsed.astimezone()
    now_local = now.astimezone()
    days_ago = (now_local.date() - parsed_local.date()).days
    if days_ago == 1:
        return tr("yesterday")
    if days_ago <= 6:
        return tr("{n} d ago").format(n=days_ago)

    qdate = QDate(parsed_local.year, parsed_local.month, parsed_local.day)
    fmt = "d MMM" if parsed_local.year == now_local.year else "d MMM yyyy"
    return QLocale().toString(qdate, fmt)
