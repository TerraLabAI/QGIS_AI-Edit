"""The reference group of the AI Edit public API.

A reference is an extra picture sent with the prompt to show the model what
you mean: a photograph, a legend, a styled layer, an earlier result. Each one
can carry a note saying what to take from it. References are cropped to the
zone, so set the zone before attaching one.

A reference layer must stay hidden on the map. Anything visible at the zone is
already part of the picture the model is editing, so showing it as well would
send it twice.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any

from qgis.core import QgsProject, QgsRasterLayer, QgsRectangle

from .mcp_api_support import (
    _find_project_layer,
    _never_raises,
    _project_layer_names,
    not_found_error,
)

# --- Well-known public basemaps ---------------------------------------------
# The friendly names an outside caller is most likely to say, each mapped to a
# key-free XYZ tile service anyone may use with attribution. They exist so a
# caller who wants to show the model a satellite or terrain backdrop is not
# stopped by a project that does not carry one yet.
#
# A PROJECT LAYER OF THE SAME NAME ALWAYS WINS. This table is read only when
# the name matches nothing already loaded, so a person's own imagery is never
# shadowed by a public tile service.
#
# ``zmax`` is each service's real deepest zoom. Past it a tile server answers
# 404, and QGIS paints a blank picture instead of stretching its last tile.
#
# This table is the shipped fallback. The table in force comes from
# ``_served_basemap_catalog()``, which merges validated served records over it
# by name, so a tile host that moves is a deploy rather than a release.
BASEMAP_XYZ_TEMPLATES: dict[str, dict[str, Any]] = {
    "Satellite": {
        "url": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        "zmax": 21,
    },
    "Streets": {
        "url": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "World_Street_Map/MapServer/tile/{z}/{y}/{x}"
        ),
        "zmax": 19,
    },
    "Terrain": {
        "url": "https://tile.opentopomap.org/{z}/{x}/{y}.png",
        "zmax": 17,
    },
    "Hillshade": {
        "url": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/"
            "Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}"
        ),
        "zmax": 16,
    },
    "OpenStreetMap": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "zmax": 19,
    },
}

# The other words a caller reaches for, keyed the way _basemap_name() sees them:
# lower case, with spaces, hyphens and underscores gone.
_BASEMAP_ALIASES = {
    "esrisatellite": "Satellite",
    "esriworldimagery": "Satellite",
    "worldimagery": "Satellite",
    "aerial": "Satellite",
    "imagery": "Satellite",
    "osm": "OpenStreetMap",
    "street": "Streets",
    "streetmap": "Streets",
    "roads": "Streets",
    "roadmap": "Streets",
    "topographic": "Terrain",
    "topo": "Terrain",
    "opentopomap": "Terrain",
    "relief": "Hillshade",
    "shadedrelief": "Hillshade",
    "elevation": "Hillshade",
}

_BASEMAP_SEPARATORS_RE = re.compile(r"[\s_-]+")

# What a served basemap record has to look like before it is trusted. A tile
# host is the value most likely to rot between releases, so the table above
# can be retuned by a served record keyed on the same name. Nothing served is
# trusted: the URL goes to a tile server and paints in the user's project, so
# it must have the same shape as the shipped ones (https, a tile template,
# bounded, none of the characters that would break out of the URI's own
# separators), or the shipped record stands.
_BASEMAP_URL_MAX_CHARS = 600
_BASEMAP_URL_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f-\x9f<>\"'\\]")
# A name keys the merge and shows in the layer tree: short and plain.
_BASEMAP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")
_BASEMAP_ZOOM_MAX = 25


def _is_safe_tile_template(value) -> bool:
    """Whether one served URL is an https XYZ tile template we can hand to QGIS."""
    return (
        isinstance(value, str)
        and value.startswith("https://")
        and len(value) <= _BASEMAP_URL_MAX_CHARS
        and "{z}" in value
        and "{x}" in value
        and "{y}" in value
        and not _BASEMAP_URL_FORBIDDEN_RE.search(value)
    )


def _served_basemap_catalog() -> dict[str, dict[str, Any]]:
    """The shipped basemap table with valid served records merged over it.

    Merged by name, so a served record retunes a shipped URL or adds a new
    basemap, and a shipped name can never be removed. A record failing any
    check is dropped and the shipped one stands. Absent or malformed
    configuration means the shipped table, unchanged.
    """
    merged = {name: dict(spec) for name, spec in BASEMAP_XYZ_TEMPLATES.items()}
    try:
        from .core.config_store import get_export_dial_objs

        for record in get_export_dial_objs("basemaps.catalog"):
            name = record.get("name")
            zmax = record.get("zmax")
            if not isinstance(name, str) or not _BASEMAP_NAME_RE.match(name.strip()):
                continue
            if not _is_safe_tile_template(record.get("url")):
                continue
            if isinstance(zmax, bool) or not isinstance(zmax, int) or not 1 <= zmax <= _BASEMAP_ZOOM_MAX:
                continue
            merged[name.strip()] = {"url": record["url"], "zmax": zmax}
    except Exception:  # nosec B110 - the shipped table always works
        pass
    return merged


def _served_basemap_aliases(catalog: dict[str, dict[str, Any]]) -> dict[str, str]:
    """The shipped alias table plus valid served extras. Union-only.

    A served alias may add a new way to ask for a basemap and can never
    replace a shipped alias. The target has to be a name in ``catalog``, so a
    mis-keyed record cannot point a word at nothing.
    """
    aliases = dict(_BASEMAP_ALIASES)
    try:
        from .core.config_store import get_export_dial_objs

        for record in get_export_dial_objs("basemaps.aliases_extra"):
            alias = record.get("alias")
            target = record.get("name")
            if not isinstance(alias, str) or not isinstance(target, str):
                continue
            key = _BASEMAP_SEPARATORS_RE.sub("", alias.strip().lower())
            target = target.strip()
            if key and len(key) <= 64 and target in catalog and key not in aliases:
                aliases[key] = target
    except Exception:  # nosec B110 - the shipped aliases always work
        pass
    return aliases


def _basemap_name(name, catalog: dict[str, dict[str, Any]] | None = None) -> str | None:
    """The table name a caller's spelling means, or None when it means none.

    Case, spaces, hyphens and underscores are all ignored, so "open street map",
    "Open-Street-Map" and "openstreetmap" are one name. ``catalog`` is the
    table from ``_served_basemap_catalog()``; a caller about to add the layer
    resolves it once and passes it, so the name and the record agree.
    """
    key = _BASEMAP_SEPARATORS_RE.sub("", str(name or "").strip().lower())
    if not key:
        return None
    if catalog is None:
        catalog = _served_basemap_catalog()
    for friendly in catalog:
        if _BASEMAP_SEPARATORS_RE.sub("", friendly.lower()) == key:
            return friendly
    return _served_basemap_aliases(catalog).get(key)


def _add_basemap_layer(friendly: str, spec: dict[str, Any]):
    """Put one table basemap in the project, unchecked. Returns it, or None.

    Unchecked on purpose: a reference layer that shows on the map at the zone
    is already part of the picture the model is editing, so a visible one would
    be sent twice. The user still sees the entry in their layer tree, which is
    the point: this call changed their project.
    """
    # Keep ":" and "/" literal so the address survives, and encode the rest, so
    # the "&", "=" and braces of a tile template cannot break out of the URI's
    # own separators.
    quoted = urllib.parse.quote(spec["url"], safe=":/?")
    layer = QgsRasterLayer(f"type=xyz&url={quoted}&zmin=0&zmax={spec['zmax']}", friendly, "wms")
    if not layer.isValid():
        return None
    project = QgsProject.instance()
    project.addMapLayer(layer, True)
    node = project.layerTreeRoot().findLayer(layer.id())
    if node is not None:
        node.setItemVisibilityChecked(False)
    return layer


class ReferencesMixin:
    """Attaching, listing and clearing the pictures sent with a prompt."""

    # --- internals --------------------------------------------------------

    def _reference_widget(self):
        dock = self._dock()
        return getattr(dock, "_reference_widget", None) if dock is not None else None

    def _reference_store(self):
        dock = self._dock()
        return getattr(dock, "_reference_store", None) if dock is not None else None

    def _align_references_to_zone(self) -> None:
        """Render every reference at the zone, so it lines up with the input."""
        dock = self._dock()
        extent = getattr(self._plugin, "_selected_extent", None)
        if dock is None or not extent:
            return
        try:
            dock.set_reference_target_extent(QgsRectangle(extent), self._canvas_crs())
        except Exception:  # nosec B110 - alignment is best effort.
            pass

    def _reference_entries(self) -> list[dict]:
        store = self._reference_store()
        if store is None:
            return []
        entries = []
        for record in store.list():
            entry: dict[str, Any] = {
                "id": record.id,
                "name": record.source_filename,
                "kind": record.source_kind,
                "size_bytes": record.size_bytes,
                "note": "",
                "is_markup": False,
            }
            try:
                entry["note"] = store.get_note(record.id)
                entry["is_markup"] = bool(store.is_markup(record.id))
            except Exception:  # nosec B110 - the id and the name are the payload.
                pass
            entries.append(entry)
        return entries

    def _resolve_reference_layer(self, name: str):
        """The layer one name means, adding a well-known basemap if it has to.

        Returns ``(layer, added_name, error)``. A project layer matches first,
        and comes back with ``added_name`` None because nothing changed. A name
        that matches no project layer but does match the basemap table in
        force (``_served_basemap_catalog``) is added to the project and named in
        ``added_name``, so the answer can say the project changed. A name that
        matches neither comes back all None, for the caller to report as a
        miss. ``error`` is set only when a table name was found and the tile
        service would not load: a plain sentence, never an exception.
        """
        layer = _find_project_layer(name)
        if layer is not None:
            return layer, None, None
        catalog = _served_basemap_catalog()
        friendly = _basemap_name(name, catalog)
        if friendly is None:
            return None, None, None
        try:
            layer = _add_basemap_layer(friendly, catalog[friendly])
        except Exception as err:  # noqa: BLE001 - a failed add is an answer, not a crash.
            return None, None, f"Could not add the '{friendly}' basemap to the project: {err}"
        if layer is None:
            return None, None, (
                f"The '{friendly}' basemap did not load. Check the network, or add the layer "
                "to the project yourself and pass the name it has there."
            )
        return layer, friendly, None

    def _attach_references(self, names, extent) -> dict:
        """Render named project layers cropped to the zone as context images.

        Replaces whatever was attached, so a run only ever carries what this
        call named. Anything an earlier ``attach_reference()`` put there, notes
        included, is dropped and named in ``replaced``. A name that matches no
        project layer and no well-known basemap is reported back rather than
        silently dropped.
        """
        dock = self._dock()
        if dock is None:
            return {"_error": "The AI Edit panel is not available for references."}
        widget = self._reference_widget()
        if widget is None:
            return {"_error": "Reference images are not available in this build."}
        try:
            dock.set_reference_target_extent(QgsRectangle(extent), self._canvas_crs())
        except Exception:  # nosec B110 - alignment is best effort.
            pass
        # Read before the clear: a caller that followed attach_reference() and
        # then named layers here loses that work, and silence about it is how
        # the same run gets set up twice.
        replaced = [str(entry.get("name") or "") for entry in self._reference_entries()]
        try:
            dock.clear_references()
        except Exception:  # nosec B110 - an empty strip is already the goal.
            pass
        attached, missing, added_layers = [], [], []
        for name in names:
            layer, added_layer, add_error = self._resolve_reference_layer(str(name))
            if add_error:
                missing.append(f"{name} ({add_error})")
                continue
            if layer is None:
                missing.append(str(name))
                continue
            if added_layer:
                added_layers.append(added_layer)
            try:
                widget.add_layers([layer])
                attached.append(layer.name())
            except Exception as err:  # noqa: BLE001 - report, never abort the run.
                missing.append(f"{name} ({err})")
        return {
            "attached": attached,
            "missing": missing,
            "count": len(attached),
            "added_layers": added_layers,
            "replaced": replaced,
        }

    # --- public -----------------------------------------------------------

    @_never_raises
    def attach_reference(
        self,
        layer_name: str | None = None,
        path: str | None = None,
        note: str = "",
    ) -> dict:
        """Add one picture to send with the next prompt. Costs nothing.

        Pass ``layer_name`` for a layer already in the QGIS project, which is
        rendered at the zone, or ``path`` for an image file on disk (PNG, JPG,
        WEBP or BMP). Pass one of the two, never both. ``note`` says in plain
        words what the model should take from this picture, for example "use
        these roof colours": leave it out when the picture speaks for itself.

        ``layer_name`` also takes a well-known public basemap: "Satellite",
        "Streets", "Terrain", "Hillshade" or "OpenStreetMap". When the project
        carries no layer of that name, the basemap is added to it, hidden, and
        then attached, and ``added_layer`` names what was added. A project
        layer of the same name always wins, so your own imagery is never
        shadowed.

        Select a zone first, so the picture is cropped to the same ground as
        the edit. With no zone selected the crop falls back to whatever ground
        the panel last held, which is rarely what you meant, so ``note`` says
        so. Keep a reference layer hidden on the map, or it becomes part of the
        image being edited. Free accounts may attach one picture at a time; a
        further add is refused and ``added`` comes back False.

        Returns ``ok``, ``added``, ``reference_id``, ``count``,
        ``at_capacity`` and ``added_layer``.
        """
        widget = self._reference_widget()
        store = self._reference_store()
        if widget is None or store is None:
            return {"_error": "Reference images are not available. Open the panel and select a zone."}
        layer_name = (layer_name or "").strip()
        path = (path or "").strip()
        if bool(layer_name) == bool(path):
            return {"_error": "Pass layer_name or path, exactly one of the two."}

        self._align_references_to_zone()
        has_zone = bool(getattr(self._plugin, "_selected_extent", None))
        known = {record.id for record in store.list()}
        before = store.count()
        added_layer = None

        if path:
            if not os.path.isfile(path):
                return {
                    "_error": (
                        f"No file at '{path}'. path is a picture already on disk; "
                        "use layer_name for a layer already in the project."
                    ),
                }
            widget.add_paths([path])
        else:
            layer, added_layer, add_error = self._resolve_reference_layer(layer_name)
            if add_error:
                return {"_error": add_error}
            if layer is None:
                # The basemap names go first. A project with a dozen layers
                # pushes them past the list's cut-off, and they are the ones a
                # caller cannot discover any other way.
                basemap_names = list(_served_basemap_catalog())
                return not_found_error(
                    "project layer or basemap", layer_name,
                    basemap_names + _project_layer_names(),
                    means=(
                        "layer_name is the layer to SEND as a picture, not a name to give it. "
                        "These basemaps are added to the project for you: "
                        + ", ".join(basemap_names) + "."
                    ),
                )
            widget.add_layers([layer])

        after = store.count()
        added = after > before
        new_id = next((r.id for r in store.list() if r.id not in known), None)
        if added and new_id and note:
            try:
                store.set_note(new_id, str(note))
            except Exception:  # nosec B110 - the picture matters more than the note.
                pass
        notes = []
        if added_layer:
            notes.append(
                f"Your project changed: the layer '{added_layer}' was not in it, the name "
                "matches a well-known public basemap, so it was added. It is added unchecked, "
                "so it stays out of the picture being edited."
            )
        if not added:
            notes.append(
                "Not added. The free plan takes one reference at a time, and there is a "
                "hard ceiling on any plan. Call clear_references() first."
            )
        if added and not has_zone:
            notes.append(
                "No zone is selected, so this picture was cropped to whatever ground the "
                "panel last held. Call set_zone(bbox), then attach it again."
            )
        return {
            "ok": added,
            "added": added,
            "reference_id": new_id,
            "count": after,
            "at_capacity": bool(widget.at_capacity()),
            "references": self._reference_entries(),
            "added_layer": added_layer,
            "note": " ".join(notes) if notes else None,
            "hint": (
                "Call set_reference_note(reference_id, note) to say what to take from it, "
                "then generate(prompt)."
            ) if added else "Call clear_references(), then attach_reference() again.",
        }

    @_never_raises
    def list_references(self) -> dict:
        """List the pictures attached to the next prompt. Costs nothing.

        Each entry carries ``id``, ``name``, ``kind`` ("file" or "layer"),
        ``size_bytes``, its ``note``, and ``is_markup`` for the picture made
        from marks drawn on the map. Returns ``count``, ``at_capacity`` and
        ``references``.
        """
        widget = self._reference_widget()
        entries = self._reference_entries()
        return {
            "count": len(entries),
            "at_capacity": bool(widget.at_capacity()) if widget is not None else False,
            "references": entries,
        }

    @_never_raises
    def set_reference_note(self, reference_id: str, note: str) -> dict:
        """Say what the model should take from one attached picture.

        ``reference_id`` comes from ``list_references()``. ``note`` is one
        short line in plain words, for example "match this roof colour". An
        empty note removes the one that was there. Costs nothing.

        Returns ``ok``, ``reference_id`` and the ``note`` as stored.
        """
        store = self._reference_store()
        if store is None:
            return {"_error": "Reference images are not available in this build."}
        reference_id = str(reference_id or "").strip()
        if not reference_id:
            return {"_error": "reference_id is required. Read one from list_references()."}
        if reference_id not in {record.id for record in store.list()}:
            return not_found_error(
                "attached reference", reference_id, [record.id for record in store.list()],
                means="reference_id is the id list_references() gives, not a layer or file name.",
            )
        store.set_note(reference_id, str(note or ""))
        return {"ok": True, "reference_id": reference_id, "note": store.get_note(reference_id)}

    @_never_raises
    def remove_reference(self, reference_id: str) -> dict:
        """Detach one picture, by the id ``list_references()`` gives. Costs nothing.

        The file it was made from is untouched. Returns ``ok``, ``count`` and
        the remaining ``references``.
        """
        widget = self._reference_widget()
        store = self._reference_store()
        if widget is None or store is None:
            return {"_error": "Reference images are not available in this build."}
        reference_id = str(reference_id or "").strip()
        if reference_id not in {record.id for record in store.list()}:
            return not_found_error(
                "attached reference", reference_id, [record.id for record in store.list()],
                means="reference_id is the id list_references() gives, not a layer or file name.",
            )
        widget.remove_reference(reference_id)
        entries = self._reference_entries()
        return {"ok": True, "count": len(entries), "references": entries}

    @_never_raises
    def clear_references(self) -> dict:
        """Detach every picture. Costs nothing, and touches no file on disk.

        References survive leaving and re-entering a session, so clear them
        between two unrelated pieces of work. Returns ``ok`` and ``count``,
        which is 0 afterwards.
        """
        dock = self._dock()
        widget = self._reference_widget()
        if widget is None:
            return {"_error": "Reference images are not available in this build."}
        if dock is not None and hasattr(dock, "clear_references"):
            dock.clear_references()
        else:
            widget.clear()
        return {"ok": True, "count": len(self._reference_entries())}
