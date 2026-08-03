"""Conversation view over cached history jobs.

A conversation is a session group (session_grouping) enriched with a
display title and the fields the resume rows and the Conversations panel
render. Pure Python, QGIS-free, so it stays headless-testable.
"""
from __future__ import annotations

from .session_grouping import group_recent_jobs

# Matches the visible width of a resume row title before eliding.
_TITLE_MAX_CHARS = 60


def conversation_title(members_oldest_first: list[dict]) -> str:
    """User rename first (any member), else the oldest prompt, truncated."""
    for job in members_oldest_first:
        text = " ".join((job.get("session_title") or "").split())
        if text:
            return text[:_TITLE_MAX_CHARS]
    for job in members_oldest_first:
        text = " ".join((job.get("prompt") or "").split())
        if text:
            return text[:_TITLE_MAX_CHARS]
    return ""


def job_has_location(job: dict | None) -> bool:
    """True when the job carries enough geometry to teleport back to its zone
    (native bbox + CRS, or the WGS84 fallback)."""
    job = job or {}
    if job.get("bbox") and job.get("crs_authid"):
        return True
    return bool(job.get("bbox_wgs84"))


def conversation_entries(jobs: list[dict]) -> list[dict]:
    entries = []
    for grp in group_recent_jobs(jobs):
        members_oldest_first = list(reversed(grp["members"]))
        cover = grp["cover"]
        # A conversation you cannot return to is not presented at all
        # (Yvann 2026-07-31): pre-capture-geo generations have no location,
        # so resuming them can only fail.
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
    """Axis-aligned WGS84 test. Antimeridian zones are refused at draw
    time, so the naive comparison is safe here."""
    if not a or not b:
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
    needle = " ".join(text.split()).casefold()
    kept = []
    for entry in entries:
        if needle:
            hay = " ".join(
                [entry["title"]]
                + [(m.get("prompt") or "") for m in entry["members"]]
            ).casefold()
            if needle not in hay:
                continue
        if view_bbox is not None and not bbox_intersects(entry.get("bbox_wgs84"), view_bbox):
            continue
        kept.append(entry)
    return kept
