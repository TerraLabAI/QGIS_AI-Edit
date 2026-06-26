"""Group history jobs that belong to the same iteration session.

A session is an interface concept, not a place: one continuous working thread on
a zone. The plugin mints a session_id when the zone lineage starts (new zone, or
Exit then a new zone) and carries it through every generation; restoring from
history re-enters that session. Grouping keys on that id, never on geometry, so
two separate edits at the same spot stay separate and a session that drifts in
extent still holds together.

Jobs without a session_id (legacy rows, older plugins) never group: each is its
own singleton. Pure Python, QGIS-free, so it is unit-tested off-thread.
"""

from __future__ import annotations


def _session_key(job: dict) -> str | None:
    """The job's session id, or None when it carries none (stays ungrouped)."""
    sid = job.get("session_id")
    return sid or None


def group_recent_jobs(jobs: list[dict]) -> list[dict]:
    """Cluster jobs (newest-first) into sessions, preserving order.

    Each group is ``{"key", "members" (newest-first), "cover" (newest), "count"}``.
    A one-member group renders as a normal card; jobs with no session_id each
    become their own singleton group so they never merge by accident.
    """
    groups: list[dict] = []
    by_key: dict[str, dict] = {}
    solo = 0
    for job in jobs or []:
        sid = _session_key(job)
        if sid is None:
            solo += 1
            groups.append(
                {"key": f"__solo_{solo}__", "members": [job], "cover": job, "count": 1}
            )
            continue
        group = by_key.get(sid)
        if group is None:
            group = {"key": sid, "members": [], "cover": job, "count": 0}
            by_key[sid] = group
            groups.append(group)
        group["members"].append(job)
        group["count"] = len(group["members"])
    return groups


def session_jobs_for(job: dict, jobs: list[dict]) -> list[dict]:
    """All cached jobs sharing ``job``'s session, oldest-first (strip order).

    Falls back to just ``[job]`` when it has no session or nothing else matches.
    """
    sid = _session_key(job)
    if sid is None:
        return [job]
    members = [j for j in (jobs or []) if _session_key(j) == sid]
    rid = job.get("request_id")
    # Identity is request_id, not dict value: a clicked job whose dict differs
    # from its cached twin (stale vs fresh copy) must not double up.
    if not any(j.get("request_id") == rid for j in members):
        members.append(job)
    # request_id breaks ties so same-timestamp multi-model siblings (and rows
    # missing created_at) keep a stable, deterministic order.
    members.sort(key=lambda j: (j.get("created_at") or "", j.get("request_id") or ""))
    return members
