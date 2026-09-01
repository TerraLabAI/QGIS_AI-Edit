









from __future__ import annotations

from typing import Any

from .mcp_api_support import _jsonable, _never_raises, _whole_number, not_found_error


def _matches(preset: dict, needle: str) -> bool:

    haystack = " ".join(
        str(preset.get(field) or "")
        for field in ("label", "prompt", "source_category")
    )
    return needle in haystack.casefold()


class LibraryMixin:




    def _catalog(self):

        dock = self._dock()
        return getattr(dock, "_server_catalog", None) if dock is not None else None

    def _all_presets(self, skip_personal: bool = True) -> list[dict]:

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
        except Exception:  # noqa: BLE001
            return False



    @_never_raises
    def get_presets(self) -> dict:







        from .core.prompts.prompt_presets import get_all_categories

        catalog = self._catalog()
        return {
            "categories": _jsonable(get_all_categories(server_catalog=catalog)),
            "catalog_loaded": catalog is not None,
        }

    @_never_raises
    def get_preset(self, preset_id: str) -> dict:







        from .core.prompts.prompt_presets import get_preset_by_id

        preset_id = str(preset_id or "").strip()
        if not preset_id:
            return {"_error": "preset_id is required."}
        preset = get_preset_by_id(preset_id, server_catalog=self._catalog())
        if preset is None:


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






        needle = str(query or "").strip().casefold()
        if not needle:
            return {"_error": "query is required."}
        try:
            limit = max(1, _whole_number(limit))
        except (TypeError, ValueError, OverflowError):
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






        from .core.prompts.prompt_presets import get_top_picks

        picks = get_top_picks(server_catalog=self._catalog())
        return {"count": len(picks), "presets": _jsonable(picks)}

    @_never_raises
    def get_preset_families(self) -> dict:








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





        from .core.prompts import prompt_history

        try:
            limit = max(1, _whole_number(limit))
        except (TypeError, ValueError, OverflowError):
            return {"_error": "limit must be a whole number."}
        entries = prompt_history.get_recent()[:limit]
        return {"count": len(entries), "prompts": _jsonable(entries)}

    @_never_raises
    def list_favorite_prompts(self) -> dict:






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
