"""The prompt library group of the AI Edit public API.

The library is a catalogue of ready-made prompts, kept on the server and
refreshed in the background, so the same plugin build sees new entries without
an update. It is arranged three levels deep: a few families, several categories
inside each family, and the presets inside each category.

Two personal lists sit beside it: the prompts recently sent, and the ones
starred as favourites. Both live on this machine.
"""
from __future__ import annotations

from typing import Any

from .mcp_api_support import _jsonable, _never_raises, not_found_error


def _matches(preset: dict, needle: str) -> bool:
    """The same match the library's own search box makes."""
    haystack = " ".join(
        str(preset.get(field) or "")
        for field in ("label", "prompt", "source_category")
    )
    return needle in haystack.casefold()


class LibraryMixin:
    """Browsing the prompt catalogue, and the personal lists beside it."""

    # --- internals --------------------------------------------------------

    def _catalog(self):
        """The served catalogue as the panel holds it, or None before it lands."""
        dock = self._dock()
        return getattr(dock, "_server_catalog", None) if dock is not None else None

    def _all_presets(self, skip_personal: bool = True) -> list[dict]:
        """Every preset in the catalogue, flattened, newest catalogue wins."""
        from .core.prompts.prompt_presets import get_all_categories

        personal = {"recent", "user_favorites"}
        out: list[dict] = []
        seen: set = set()
        for category in get_all_categories(server_catalog=self._catalog()):
            if skip_personal and category.get("key") in personal:
                continue
            for preset in category.get("presets") or []:
                key = str(preset.get("id") or preset.get("prompt") or "")
                if key and key in seen:
                    continue
                seen.add(key)
                out.append(preset)
        return out

    def _sync_favorite_to_server(self, prompt: str, label, category, now_favorited: bool) -> bool:
        """Mirror a star onto the account, so the website shows the same list.

        Best effort by design: the list on this machine is what the panel reads,
        and a failed mirror must never lose the star.
        """
        plugin = self._plugin
        client = getattr(plugin, "_client", None)
        auth = getattr(plugin, "_auth_manager", None)
        if client is None or auth is None:
            return False
        try:
            header = auth.get_auth_header()
            if not header:
                return False
            if now_favorited:
                client.add_favorite(header, prompt, label or None, category or None)
            else:
                client.remove_favorite(header, prompt)
            return True
        except Exception:  # noqa: BLE001 - the local list is the source of truth.
            return False

    # --- public -----------------------------------------------------------

    @_never_raises
    def get_presets(self) -> dict:
        """List the prompt presets, grouped in the categories the panel shows.

        Returns ``categories`` (each with its ``presets``, every preset with an
        ``id`` usable as ``template_id`` in ``generate()``) and
        ``catalog_loaded``, which is False while only the shipped set is known
        because the served catalogue has not arrived yet.
        """
        from .core.prompts.prompt_presets import get_all_categories

        catalog = self._catalog()
        return {
            "categories": _jsonable(get_all_categories(server_catalog=catalog)),
            "catalog_loaded": catalog is not None,
        }

    @_never_raises
    def get_preset(self, preset_id: str) -> dict:
        """Look one preset up by its id. Costs nothing, no network call.

        Returns ``found`` and, when found, ``preset``: its ``id``, ``label``,
        the ``prompt`` text it fills in, its ``source_category``, and any
        pictures the catalogue carries for it. Pass the id back to
        ``generate(template_id=...)`` to tag a run with it.
        """
        from .core.prompts.prompt_presets import get_preset_by_id

        preset_id = str(preset_id or "").strip()
        if not preset_id:
            return {"_error": "preset_id is required."}
        preset = get_preset_by_id(preset_id, server_catalog=self._catalog())
        if preset is None:
            # Not an error: a caller reads "found". The nearest ids still
            # travel with it, because a typo is the usual reason for a miss.
            miss = not_found_error(
                "preset", preset_id,
                [str(entry.get("id") or "") for entry in self._all_presets()],
                means="preset_id comes from search_presets() or get_top_picks().",
            )
            return {
                "found": False,
                "preset": None,
                "preset_id": preset_id,
                "note": miss["_error"],
                "_suggestions": miss.get("_suggestions") or [],
                "hint": "Call search_presets(query) to find a preset by name.",
            }
        return {
            "found": True,
            "preset": _jsonable(preset),
            "preset_id": preset_id,
            "hint": "Pass this id to generate(prompt, template_id=...) to tag a run with it.",
        }

    @_never_raises
    def search_presets(self, query: str, limit: int = 25) -> dict:
        """Find presets whose name, prompt or category contains ``query``.

        Plain text, no wildcards, upper and lower case treated the same. This
        is the search the library's own box makes. Costs nothing, no network
        call. Returns ``query``, ``count`` and ``presets``, cut to ``limit``.
        """
        needle = str(query or "").strip().casefold()
        if not needle:
            return {"_error": "query is required."}
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            return {"_error": "limit must be a whole number."}
        hits = [p for p in self._all_presets() if _matches(p, needle)]
        out = {
            "query": query,
            "count": len(hits),
            "presets": _jsonable(hits[:limit]),
        }
        if not hits:
            out["hint"] = (
                "Nothing matched: shorten query to one word, or call get_top_picks() "
                "for the shortlist the catalogue puts forward."
            )
        return out

    @_never_raises
    def get_top_picks(self) -> dict:
        """The presets the catalogue puts forward, in its own order.

        This is an editorial shortlist, not a usage ranking. Good starting
        point when you do not know what the library holds. Costs nothing.
        Returns ``count`` and ``presets``.
        """
        from .core.prompts.prompt_presets import get_top_picks

        picks = get_top_picks(server_catalog=self._catalog())
        return {"count": len(picks), "presets": _jsonable(picks)}

    @_never_raises
    def get_preset_families(self) -> dict:
        """List the few families the whole catalogue is arranged under.

        A family answers "what am I trying to do", and holds several
        categories. Each entry carries ``key``, ``label``, ``tagline``, the
        ``categories`` inside it, and how many presets and categories it holds.
        Pass a ``key`` to ``get_preset_family()`` to read its presets. Costs
        nothing.
        """
        from .core.prompts.prompt_presets import get_need_groups, get_need_tiles

        catalog = self._catalog()
        tiles = {t.get("key"): t for t in get_need_tiles(server_catalog=catalog)}
        families = []
        for group in get_need_groups(server_catalog=catalog):
            tile = tiles.get(group.get("key")) or {}
            families.append({
                "key": group.get("key"),
                "label": group.get("label"),
                "tagline": group.get("tagline"),
                "categories": group.get("categories") or [],
                "preset_count": tile.get("preset_count"),
                "category_count": tile.get("category_count"),
            })
        return {
            "count": len(families),
            "families": _jsonable(families),
            "hint": "Pass a key to get_preset_family(family_key) to read its presets.",
        }

    @_never_raises
    def get_preset_family(self, family_key: str) -> dict:
        """Read one family: its categories, each with its presets.

        ``family_key`` comes from ``get_preset_families()``. Costs nothing, no
        network call. Returns ``key``, ``label``, ``tagline`` and
        ``categories``.
        """
        from .core.prompts.prompt_presets import get_need_page

        family_key = str(family_key or "").strip()
        if not family_key:
            return {"_error": "family_key is required. Read one from get_preset_families()."}
        page = get_need_page(family_key, server_catalog=self._catalog())
        if not page or not page.get("categories"):
            known = [f.get("key") for f in (self.get_preset_families().get("families") or [])]
            out = not_found_error(
                "preset family", family_key, known,
                means="family_key comes from get_preset_families().",
            )
            out["known_families"] = known
            return out
        return _jsonable(page)

    @_never_raises
    def list_recent_prompts(self, limit: int = 25) -> dict:
        """The prompts sent from this machine, newest first. Costs nothing.

        Each entry carries the ``prompt`` and the time it was sent. Returns
        ``count`` and ``prompts``.
        """
        from .core.prompts import prompt_history

        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            return {"_error": "limit must be a whole number."}
        entries = prompt_history.get_recent()[:limit]
        return {"count": len(entries), "prompts": _jsonable(entries)}

    @_never_raises
    def list_favorite_prompts(self) -> dict:
        """The starred prompts, newest first. Costs nothing, no network call.

        Each entry carries ``prompt``, an optional ``label``, the
        ``source_category`` it came from, and when it was starred. Returns
        ``count`` and ``prompts``.
        """
        from .core.prompts import prompt_history

        entries = prompt_history.get_favorites()
        return {"count": len(entries), "prompts": _jsonable(entries)}

    @_never_raises
    def add_favorite_prompt(
        self,
        prompt: str,
        label: str | None = None,
        source_category: str | None = None,
    ) -> dict:
        """Star a prompt, as the star next to the prompt box does. Costs nothing.

        Already starred is not an error: the call does nothing and reports
        ``already`` True. ``label`` and ``source_category`` are optional and
        only change how the panel titles the saved card. The star is written on
        this machine and mirrored to the account when signed in.

        Returns ``ok``, ``is_favorite``, ``already`` and ``synced``.
        """
        from .core.prompts import prompt_history

        text = str(prompt or "").strip()
        if not text:
            return {"_error": "prompt is required."}
        if prompt_history.is_favorite(text):
            return {"ok": True, "is_favorite": True, "already": True, "synced": False}
        prompt_history.toggle_favorite(text, label, source_category)
        synced = self._sync_favorite_to_server(text, label, source_category, True)
        return {
            "ok": True,
            "is_favorite": prompt_history.is_favorite(text),
            "already": False,
            "synced": synced,
        }

    @_never_raises
    def remove_favorite_prompt(self, prompt: str) -> dict:
        """Unstar a prompt. Costs nothing, and removes nothing else.

        Not starred is not an error: the call does nothing and reports
        ``already`` True. Returns ``ok``, ``is_favorite``, ``already`` and
        ``synced``.
        """
        from .core.prompts import prompt_history

        text = str(prompt or "").strip()
        if not text:
            return {"_error": "prompt is required."}
        if not prompt_history.is_favorite(text):
            return {"ok": True, "is_favorite": False, "already": True, "synced": False}
        prompt_history.toggle_favorite(text)
        synced = self._sync_favorite_to_server(text, None, None, False)
        return {
            "ok": True,
            "is_favorite": prompt_history.is_favorite(text),
            "already": False,
            "synced": synced,
        }

    @_never_raises
    def get_prompt_guidance(self) -> dict:
        """The limits a prompt has to meet, and how to write one that works.

        Returns ``min_chars``, ``min_words`` and ``max_chars``, which the panel
        applies before it will send anything, plus ``hints``. Costs nothing.
        Check a prompt against these before spending a credit on it: a prompt
        under the minimum is refused without a run.
        """
        from .core.config_store import get_export_dial
        from .ui.dock.prompts import _MIN_PROMPT_CHARS, _MIN_PROMPT_WORDS
        from .ui.dock.style import MAX_PROMPT_CHARS

        out: dict[str, Any] = {
            "min_chars": get_export_dial("limits.min_prompt_chars", _MIN_PROMPT_CHARS),
            "min_words": get_export_dial("limits.min_prompt_words", _MIN_PROMPT_WORDS),
            "max_chars": get_export_dial("limits.max_prompt_chars", MAX_PROMPT_CHARS),
            "hints": [
                "Name the thing to change and what it should become.",
                "One instruction at a time reads better than a paragraph.",
                "Say the colour, the material or the season you want, in plain words.",
                "Attach a picture instead of describing a look you cannot put in words.",
                "Draw on the map instead of describing where, when the where is hard to say.",
            ],
        }
        return out
