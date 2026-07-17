












from __future__ import annotations


def _session_key(job: dict) -> str | None:

    sid = job.get("session_id") if isinstance(job, dict) else None
    return sid if isinstance(sid, str) and sid.strip() else None


def group_recent_jobs(jobs: list[dict]) -> list[dict]:






    groups: list[dict] = []
    by_key: dict[str, dict] = {}
    solo = 0
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
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




    sid = _session_key(job)
    if sid is None:
        return [job]
    members = [j for j in (jobs or []) if isinstance(j, dict) and _session_key(j) == sid]
    rid = job.get("request_id")


    if not any((j.get("request_id") == rid) if rid else j is job for j in members):
        members.append(job)


    members.sort(key=lambda j: (
        j.get("created_at") if isinstance(j.get("created_at"), str) else "",
        j.get("request_id") if isinstance(j.get("request_id"), str) else "",
    ))
    return members
