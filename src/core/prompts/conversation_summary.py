





from __future__ import annotations

import math

from ..config_store import get_export_dial
from .session_grouping import group_recent_jobs


_TITLE_MAX_CHARS = 60


def _display_text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def conversation_title(members_oldest_first: list[dict]) -> str:

    max_chars = get_export_dial("pipeline.conversation_summary.title_max_chars", _TITLE_MAX_CHARS)
    for job in members_oldest_first:
        text = _display_text(job.get("session_title"))
        if text:
            return text[:max_chars]
    for job in members_oldest_first:
        text = _display_text(job.get("prompt"))
        if text:
            return text[:max_chars]
    return ""


def _valid_bbox(box, keys: tuple[str, str, str, str], wgs84: bool = False) -> bool:
    if not isinstance(box, dict):
        return False
    values = [box.get(key) for key in keys]
    try:
        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) for value in values
        ):
            return False
    except OverflowError:
        return False
    west, south, east, north = values
    if west >= east or south >= north:
        return False
    return not wgs84 or (-180 <= west < east <= 180 and -90 <= south < north <= 90)


def job_has_location(job: dict | None) -> bool:

    if not isinstance(job, dict):
        return False
    crs = job.get("crs_authid")
    if isinstance(crs, str) and crs.strip() and _valid_bbox(
        job.get("bbox"), ("xmin", "ymin", "xmax", "ymax")
    ):
        return True
    return _valid_bbox(job.get("bbox_wgs84"), ("west", "south", "east", "north"), True)


def conversation_entries(jobs: list[dict]) -> list[dict]:
    entries = []
    for grp in group_recent_jobs(jobs):
        members_oldest_first = list(reversed(grp["members"]))
        cover = grp["cover"]



        if not job_has_location(cover):
            continue
        entries.append({
            "key": grp["key"],
            "session_id": cover.get("session_id"),
            "title": conversation_title(members_oldest_first),
            "count": grp["count"],
            "cover": cover,
            "members": grp["members"],
            "created_at": cover.get("created_at") or "",
            "bbox_wgs84": cover.get("bbox_wgs84"),
        })
    return entries


def bbox_intersects(a: dict | None, b: dict | None) -> bool:


    keys = ("west", "south", "east", "north")
    if not _valid_bbox(a, keys, True) or not _valid_bbox(b, keys, True):
        return False
    try:
        return not (
            a["east"] < b["west"]
            or a["west"] > b["east"]
            or a["north"] < b["south"]
            or a["south"] > b["north"]
        )
    except (KeyError, TypeError):
        return False


def filter_entries(
    entries: list[dict], text: str = "", view_bbox: dict | None = None
) -> list[dict]:
    needle = _display_text(text).casefold()
    kept = []
    for entry in entries:
        if needle:
            hay = " ".join(
                [_display_text(entry.get("title"))]
                + [_display_text(m.get("prompt")) for m in entry.get("members", []) if isinstance(m, dict)]
            ).casefold()
            if needle not in hay:
                continue
        if view_bbox is not None and not bbox_intersects(entry.get("bbox_wgs84"), view_bbox):
            continue
        kept.append(entry)
    return kept
