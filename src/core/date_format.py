














from __future__ import annotations

import datetime as dt

from qgis.PyQt.QtCore import QDate, QLocale

from .config_store import get_export_copy
from .i18n import tr


def _parse_iso(ts: str) -> dt.datetime | None:
    if not ts:
        return None
    try:

        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def format_smart_date(iso_ts: str) -> str:

    parsed = _parse_iso(iso_ts)
    if parsed is None:
        return ""

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)

    seconds = max(0, int((now - parsed).total_seconds()))
    if seconds < 60:
        return get_export_copy("pipeline.date_format.just_now", tr("just now"))
    if seconds < 3600:
        return tr("{n} min ago").format(n=seconds // 60)
    if seconds < 86400:
        return tr("{n} h ago").format(n=seconds // 3600)

    parsed_local = parsed.astimezone()
    now_local = now.astimezone()
    days_ago = (now_local.date() - parsed_local.date()).days
    if days_ago == 1:
        return get_export_copy("pipeline.date_format.yesterday", tr("yesterday"))
    if days_ago <= 6:
        return tr("{n} d ago").format(n=days_ago)

    qdate = QDate(parsed_local.year, parsed_local.month, parsed_local.day)
    fmt = "d MMM" if parsed_local.year == now_local.year else "d MMM yyyy"
    return QLocale().toString(qdate, fmt)


def format_reset_date(iso_ts: str) -> str:






    parsed = _parse_iso(iso_ts)
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    local = parsed.astimezone()
    qdate = QDate(local.year, local.month, local.day)
    return QLocale().toString(qdate, "d MMMM yyyy")
