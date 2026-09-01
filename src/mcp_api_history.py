"""The history group of the AI Edit public API.

Every edit is kept on the account, so the same work is there from any machine
the user signs in on. Edits made on one zone without leaving are one session,
and a session is what the panel lets a person reopen, rename or delete.

The panel keeps a copy of the newest rows on this machine so it can draw the
list without waiting. The read methods here use that copy by default and only
go to the network when asked to, because a network call costs a second or two
and returns nothing new most of the time.
"""
from __future__ import annotations

from typing import Any

from .mcp_api_support import _jsonable, _never_raises, not_found_error

# Fields worth handing to an integrator. The server may carry more, and the raw
# row is available under "raw" on get_generation(), so nothing is lost by
# keeping this list short and stable.
_JOB_FIELDS = (
    "request_id",
    "session_id",
    "session_title",
    "created_at",
    "prompt",
    "template_id",
    "template_name",
    "resolution",
    "output_w",
    "output_h",
    "aspect_ratio",
    "duration_ms",
    "is_favorite",
    "crs_authid",
    "bbox",
    "bbox_wgs84",
    "input_url",
    "output_url",
    "input_thumb_url",
    "output_thumb_url",
    "reference_image_urls",
)


def _job_summary(job: dict) -> dict:
    """One past edit, cut down to the fields an integrator can rely on."""
    return {field: _jsonable(job.get(field)) for field in _JOB_FIELDS}


class HistoryMixin:
    """Reading, reopening, renaming and deleting past work."""

    # --- internals --------------------------------------------------------

    def _cached_jobs(self) -> list[dict]:
        """The newest rows as the panel holds them. No network call."""
        dock = self._dock()
        getter = getattr(dock, "get_cached_recent_jobs", None) if dock is not None else None
        if callable(getter):
            jobs = getter()
            if jobs:
                return list(jobs)
        from .core.prompts import history_cache
        return list(history_cache.get_recent_jobs() or [])

    def _auth_header(self):
        """The header a server call needs, or None when nobody is signed in."""
        auth = getattr(self._plugin, "_auth_manager", None)
        if auth is None:
            return None
        try:
            header = auth.get_auth_header()
        except Exception:
            return None
        return header or None

    def _job_by_request_id(self, request_id: str) -> dict | None:
        for job in self._cached_jobs():
            if str(job.get("request_id") or "") == request_id:
                return job
        return None

    def _newest_job_of_session(self, session_id: str) -> dict | None:
        members = [
            job for job in self._cached_jobs()
            if str(job.get("session_id") or "") == session_id
        ]
        if not members:
            return None
        return max(members, key=lambda job: str(job.get("created_at") or ""))

    def _known_request_ids(self) -> list[str]:
        """Every edit id in the copy held on this machine."""
        return [str(job.get("request_id") or "") for job in self._cached_jobs()]

    def _known_session_ids(self) -> list[str]:
        """Every session id in the copy held on this machine, newest first."""
        seen: list[str] = []
        for job in self._cached_jobs():
            session_id = str(job.get("session_id") or "")
            if session_id and session_id not in seen:
                seen.append(session_id)
        return seen

    def _session_entries(self) -> list[dict]:
        from .core.prompts.conversation_summary import conversation_entries
        return conversation_entries(self._cached_jobs())

    # --- public -----------------------------------------------------------

    @_never_raises
    def list_sessions(self, query: str = "", limit: int = 25) -> dict:
        """List past pieces of work, newest first. Costs nothing, no network call.

        A session is one zone worked on without leaving: its first edit and
        every edit made on top of it. ``query`` keeps only the sessions whose
        title or any of their prompts contains that text, upper and lower case
        treated the same. This reads the copy the panel holds on this machine;
        call ``list_generations(refresh=True)`` first to bring in anything made
        elsewhere.

        Each entry carries ``session_id``, ``title``, ``count``, ``created_at``
        and ``bbox_wgs84``, the ground it covers. A session with no recorded
        ground is left out, because it cannot be reopened. Returns ``count``
        and ``sessions``.
        """
        from .core.prompts.conversation_summary import filter_entries

        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            return {"_error": "limit must be a whole number."}
        entries = self._session_entries()
        text = str(query or "").strip()
        if text:
            entries = filter_entries(entries, text)
        sessions = [
            {
                "session_id": entry.get("session_id"),
                "title": entry.get("title"),
                "count": entry.get("count"),
                "created_at": entry.get("created_at"),
                "bbox_wgs84": _jsonable(entry.get("bbox_wgs84")),
            }
            for entry in entries[:limit]
        ]
        out = {"count": len(sessions), "sessions": sessions, "query": query}
        if not sessions:
            out["hint"] = (
                "Nothing matched: widen query or leave it out, then call "
                "list_generations(refresh=True) to bring in work done elsewhere."
            )
        return out

    @_never_raises
    def get_session(self, session_id: str) -> dict:
        """Read one session and every edit in it, oldest first. Costs nothing.

        ``session_id`` comes from ``list_sessions()``. Returns ``found``,
        ``session_id``, ``title``, ``count``, ``bbox_wgs84`` and
        ``generations``, in the order the versions were made.
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            return {"_error": "session_id is required. Read one from list_sessions()."}
        entry = next(
            (e for e in self._session_entries() if str(e.get("session_id") or "") == session_id),
            None,
        )
        if entry is None:
            miss = not_found_error(
                "session", session_id, self._known_session_ids(),
                means="session_id comes from list_sessions(), and it reads this machine only.",
            )
            return {
                "found": False,
                "session_id": session_id,
                "note": miss["_error"],
                "_suggestions": miss.get("_suggestions") or [],
                "hint": "Call list_generations(refresh=True) to bring in work done elsewhere.",
            }
        members = list(entry.get("members") or [])
        members.sort(key=lambda job: str(job.get("created_at") or ""))
        return {
            "found": True,
            "session_id": session_id,
            "title": entry.get("title"),
            "count": entry.get("count"),
            "bbox_wgs84": _jsonable(entry.get("bbox_wgs84")),
            "generations": [_job_summary(job) for job in members],
        }

    @_never_raises
    def list_generations(
        self,
        limit: int = 25,
        favorites_only: bool = False,
        refresh: bool = False,
    ) -> dict:
        """List past edits one by one, newest first, sessions ignored.

        With ``refresh`` False, which is the default, this reads the copy on
        this machine and costs nothing. With ``refresh`` True it asks the
        account, which takes a second or two, needs the user to be signed in,
        and brings in work done on another machine. ``favorites_only`` keeps
        only the starred ones, and needs ``refresh`` True to be exact.

        Each entry carries ``request_id``, ``session_id``, ``prompt``,
        ``created_at``, ``resolution``, the output size, and links to the
        before and after images. Those links are short lived: fetch them
        promptly or ask again. Returns ``count``, ``generations``,
        ``from_server`` and ``has_more``.
        """
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            return {"_error": "limit must be a whole number."}

        if refresh:
            client = getattr(self._plugin, "_client", None)
            header = self._auth_header()
            if client is None or header is None:
                return {
                    "_error": (
                        "Reading from the account needs the user signed in. "
                        "Call get_status() and follow action_required, or call "
                        "list_generations() without refresh to read this machine."
                    ),
                }
            payload = client.get_generation_history(
                header, limit=limit, favorites_only=bool(favorites_only)
            ) or {}
            jobs = list(payload.get("jobs") or [])
            return {
                "count": len(jobs),
                "generations": [_job_summary(job) for job in jobs],
                "from_server": True,
                "has_more": bool(payload.get("has_more")),
            }

        jobs = self._cached_jobs()
        if favorites_only:
            jobs = [job for job in jobs if job.get("is_favorite")]
        jobs = jobs[:limit]
        return {
            "count": len(jobs),
            "generations": [_job_summary(job) for job in jobs],
            "from_server": False,
            "has_more": False,
        }

    @_never_raises
    def get_generation(self, request_id: str) -> dict:
        """Read one past edit in full, by its ``request_id``. Costs nothing.

        Returns ``found`` and, when found, every field ``list_generations()``
        reports plus ``raw``, the whole record as the account holds it, and
        ``zone_polygon``, the free shape drawn for it when there was one. This
        reads the copy on this machine.
        """
        request_id = str(request_id or "").strip()
        if not request_id:
            return {"_error": "request_id is required. Read one from list_generations()."}
        job = self._job_by_request_id(request_id)
        if job is None:
            miss = not_found_error(
                "past edit", request_id, self._known_request_ids(),
                means="request_id comes from list_generations(), and it reads this machine only.",
            )
            return {
                "found": False,
                "request_id": request_id,
                "note": miss["_error"],
                "_suggestions": miss.get("_suggestions") or [],
                "hint": "Call list_generations(refresh=True) to bring in work done elsewhere.",
            }
        from .core.prompts import history_cache
        out: dict[str, Any] = _job_summary(job)
        out["found"] = True
        out["raw"] = _jsonable(job)
        out["zone_polygon"] = _jsonable(history_cache.get_zone_polygon(request_id))
        out["output_paths"] = _jsonable(history_cache.get_output_paths(request_id))
        return out

    @_never_raises
    def open_session(self, session_id: str = "", request_id: str = "") -> dict:
        """Reopen past work in the panel, ready to carry on editing it.

        Give either a ``session_id`` or the ``request_id`` of one edit in it.
        This puts the zone back on the map, refills the prompt, rebuilds the
        version list, and re-attaches the pictures that were sent. Images are
        fetched in the background, so the panel fills in over a few seconds.

        Costs nothing on its own. The next ``generate()`` continues that
        session rather than starting a new one. Returns ``ok``, ``session_id``
        and ``request_id``.
        """
        session_id = str(session_id or "").strip()
        request_id = str(request_id or "").strip()
        if bool(session_id) == bool(request_id):
            return {"_error": "Pass session_id or request_id, exactly one of the two."}
        job = (
            self._job_by_request_id(request_id) if request_id
            else self._newest_job_of_session(session_id)
        )
        if job is None:
            return not_found_error(
                "session" if session_id else "past edit",
                session_id or request_id,
                self._known_session_ids() if session_id else self._known_request_ids(),
                means=(
                    "This reads the copy held on this machine: call "
                    "list_generations(refresh=True) first to bring in work done elsewhere."
                ),
            )
        handler = getattr(self._plugin, "_on_history_restore", None)
        if not callable(handler):
            return {"_error": "Reopening past work is not available in this build."}
        self._open_dock()
        handler(dict(job))
        return {
            "ok": True,
            "session_id": job.get("session_id"),
            "request_id": job.get("request_id"),
            "note": "The panel fills in over a few seconds. Read get_zone() and list_versions().",
            "hint": (
                "Once list_versions() reports the restored versions, call generate(prompt) "
                "to carry this session on."
            ),
        }

    @_never_raises
    def add_generation_to_map(self, request_id: str) -> dict:
        """Bring one past result back onto the map as a layer.

        ``request_id`` comes from ``list_generations()``. The image is fetched
        in the background and the layer appears in the project a few seconds
        later, so this returns before it is there: watch the project's layers.
        Costs nothing, no credit is spent. Returns ``ok`` and ``request_id``.
        """
        request_id = str(request_id or "").strip()
        if not request_id:
            return {"_error": "request_id is required. Read one from list_generations()."}
        job = self._job_by_request_id(request_id)
        if job is None:
            return not_found_error(
                "past edit", request_id, self._known_request_ids(),
                means=(
                    "This reads the copy held on this machine: call "
                    "list_generations(refresh=True) first to bring in work done elsewhere."
                ),
            )
        handler = getattr(self._plugin, "_on_history_add_to_map", None)
        if not callable(handler):
            return {"_error": "Adding past work to the map is not available in this build."}
        handler(dict(job))
        return {
            "ok": True,
            "request_id": request_id,
            "note": "The layer is being fetched and appears in the project shortly.",
            "hint": (
                "Once the layer is in the project, vectorize(target_rgb, layer_name=...) "
                "traces one colour of it into polygons."
            ),
        }

    @_never_raises
    def rename_session(self, session_id: str, title: str) -> dict:
        """Give one past session a name of your own. Makes a network call.

        ``session_id`` comes from ``list_sessions()``. The account may shorten
        a long title, and the stored value is what comes back. Needs the user
        signed in. Changes nothing else, and costs no credit.

        Returns ``ok``, ``session_id`` and ``title`` as stored.
        """
        session_id = str(session_id or "").strip()
        title = str(title or "").strip()
        if not session_id:
            return {"_error": "session_id is required. Read one from list_sessions()."}
        if not title:
            return {"_error": "title is required."}
        client = getattr(self._plugin, "_client", None)
        header = self._auth_header()
        if client is None or header is None:
            return {
                "_error": (
                    "Renaming needs the user signed in. Call get_status() and "
                    "follow action_required."
                ),
            }
        payload = client.rename_generation_session(header, session_id, title) or {}
        stored = payload.get("title") or title
        handler = getattr(self._plugin, "_apply_conversation_rename", None)
        if callable(handler):
            try:
                handler(session_id, payload)
            except Exception:  # nosec B110 - the account already has the name.
                pass
        return {"ok": True, "session_id": session_id, "title": stored}

    @_never_raises
    def delete_session(self, session_id: str) -> dict:
        """Delete one past session for good. Makes a network call.

        THIS IS PERMANENT. It removes that one session and every edit inside
        it from the account, and there is no undo. It deletes nothing else: one
        call, one ``session_id``, never a wider sweep, and there is no method
        here that empties the whole history.

        Layers already added to the QGIS project and files already saved on
        disk are untouched, only the record on the account goes. Needs the user
        signed in. Costs no credit.

        Returns ``ok`` and ``session_id``.
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            return {"_error": "session_id is required. Read one from list_sessions()."}
        client = getattr(self._plugin, "_client", None)
        header = self._auth_header()
        if client is None or header is None:
            return {
                "_error": (
                    "Deleting needs the user signed in. Call get_status() and "
                    "follow action_required."
                ),
            }
        client.delete_generation_session(header, session_id=session_id)
        # The panel's own delete path, so the local copy of the history matches
        # the account. Without it list_sessions() still lists the session and
        # get_session() still reports it found.
        entry = next(
            (e for e in self._session_entries() if str(e.get("session_id") or "") == session_id),
            None,
        )
        dropped = False
        applier = getattr(self._plugin, "_apply_conversation_delete", None)
        if entry is not None and callable(applier):
            try:
                applier(entry)
                dropped = True
            except Exception:  # nosec B110 - the account already lost the session.
                dropped = False
        return {
            "ok": True,
            "session_id": session_id,
            "cache_cleared": dropped,
            "note": "Deleted for good. Layers already in the project are untouched.",
        }
