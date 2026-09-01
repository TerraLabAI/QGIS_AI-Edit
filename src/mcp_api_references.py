










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








_BASEMAP_URL_MAX_CHARS = 600
_BASEMAP_URL_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f-\x9f<>\"'\\]")

_BASEMAP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")
_BASEMAP_ZOOM_MAX = 25


def _is_safe_tile_template(value) -> bool:

    if not (
        isinstance(value, str)
        and value.startswith("https://")
        and len(value) <= _BASEMAP_URL_MAX_CHARS
        and all(token in value for token in ("{z}", "{x}", "{y}"))
        and not _BASEMAP_URL_FORBIDDEN_RE.search(value)
    ):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        return bool(parsed.hostname and not parsed.username and not parsed.password
                    and not parsed.fragment and (port is None or 1 <= port <= 65535)
                    and not any(token in parsed.netloc for token in ("{z}", "{x}", "{y}")))
    except ValueError:
        return False


def _served_basemap_catalog() -> dict[str, dict[str, Any]]:







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
    except Exception:  # nosec B110
        pass
    return merged


def _served_basemap_aliases(catalog: dict[str, dict[str, Any]]) -> dict[str, str]:






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
    except Exception:  # nosec B110
        pass
    return aliases


def _basemap_name(name, catalog: dict[str, dict[str, Any]] | None = None) -> str | None:







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




    def _reference_widget(self):
        dock = self._dock()
        return getattr(dock, "_reference_widget", None) if dock is not None else None

    def _reference_store(self):
        dock = self._dock()
        return getattr(dock, "_reference_store", None) if dock is not None else None

    def _align_references_to_zone(self) -> None:

        dock = self._dock()
        extent = getattr(self._plugin, "_selected_extent", None)
        if dock is None or not extent:
            return
        try:
            dock.set_reference_target_extent(QgsRectangle(extent), self._canvas_crs())
        except Exception:  # nosec B110
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
            except Exception:  # nosec B110
                pass
            entries.append(entry)
        return entries

    def _resolve_reference_layer(self, name: str):











        layer = _find_project_layer(name)
        if layer is not None:
            return layer, None, None
        catalog = _served_basemap_catalog()
        friendly = _basemap_name(name, catalog)
        if friendly is None:
            return None, None, None
        try:
            layer = _add_basemap_layer(friendly, catalog[friendly])
        except Exception as err:  # noqa: BLE001
            return None, None, f"Could not add the '{friendly}' basemap to the project: {err}"
        if layer is None:
            return None, None, (
                f"The '{friendly}' basemap did not load. Check the network, or add the layer "
                "to the project yourself and pass the name it has there."
            )
        return layer, friendly, None

    def _attach_references(self, names, extent) -> dict:








        if not isinstance(names, (list, tuple)) or any(not isinstance(name, str) or not name.strip() for name in names):
            return {"_error": "reference_layers must contain non-empty layer names."}
        names = list(dict.fromkeys(name.strip() for name in names))
        dock = self._dock()
        if dock is None:
            return {"_error": "The AI Edit panel is not available for references."}
        widget = self._reference_widget()
        if widget is None:
            return {"_error": "Reference images are not available in this build."}
        try:
            dock.set_reference_target_extent(QgsRectangle(extent), self._canvas_crs())
        except Exception:  # nosec B110
            pass



        replaced = [str(entry.get("name") or "") for entry in self._reference_entries()]
        try:
            dock.clear_references()
        except Exception:  # nosec B110
            pass
        attached, missing, added_layers = [], [], []
        for name in names:
            if widget.at_capacity():
                missing.append(str(name))
                continue
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
                before_count = len(self._reference_entries())
                widget.add_layers([layer])
                if len(self._reference_entries()) > before_count:
                    attached.append(layer.name())
                else:
                    missing.append(str(name))
            except Exception as err:  # noqa: BLE001
                missing.append(f"{name} ({err})")
        return {
            "attached": attached,
            "missing": missing,
            "count": len(attached),
            "added_layers": added_layers,
            "replaced": replaced,
        }



    @_never_raises
    def attach_reference(
        self,
        layer_name: str | None = None,
        path: str | None = None,
        note: str = "",
    ) -> dict:

























        widget = self._reference_widget()
        store = self._reference_store()
        if widget is None or store is None:
            return {"_error": "Reference images are not available. Open the panel and select a zone."}
        layer_name = (layer_name or "").strip()
        path = (path or "").strip()
        if bool(layer_name) == bool(path):
            return {"_error": "Pass layer_name or path, exactly one of the two."}

        if widget.at_capacity():
            return {"ok": False, "added": False, "at_capacity": True, "count": store.count(),
                    "_error": "The reference limit is reached. Remove one before adding another."}
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
            except Exception:  # nosec B110
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







        widget = self._reference_widget()
        entries = self._reference_entries()
        return {
            "count": len(entries),
            "at_capacity": bool(widget.at_capacity()) if widget is not None else False,
            "references": entries,
        }

    @_never_raises
    def set_reference_note(self, reference_id: str, note: str) -> dict:








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






        dock = self._dock()
        widget = self._reference_widget()
        if widget is None:
            return {"_error": "Reference images are not available in this build."}
        if dock is not None and hasattr(dock, "clear_references"):
            dock.clear_references()
        else:
            widget.clear()
        return {"ok": True, "count": len(self._reference_entries())}
