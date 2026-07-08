from __future__ import annotations

import urllib.parse

from qgis.PyQt.QtCore import QTimer

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.logger import log, log_warning
from ...core.prompts.prompt_presets import get_preset_by_id

# --- Onboarding basemaps (empty-canvas "Try it on an example") ----------------
# Esri World Imagery: the key-free, ToS-clean global backdrop QGIS and
# QuickMapServices ship. zmax=21 unlocks Esri's native sub-metre tiles in metro
# areas, so a tight zone stays crisp instead of upsampling a z19 tile.
_ESRI_WORLD_IMAGERY_URI = (
    "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D&zmax=21&zmin=0"
)
# IGN Géoplateforme orthophotos: key-free since the 2021 open-data switch,
# Licence Ouverte Etalab 2.0 (commercial reuse + derivatives OK with
# attribution), ~20cm over metropolitan France. Used only for the France demo
# scene because coverage is France-only (blank tiles elsewhere); the WMTS-KVP
# endpoint is consumed as XYZ with the standard PM (web-mercator) tile matrix.
# HR.* is the current canonical layer id (verified serving image/jpeg tiles).
_IGN_ORTHO_TILE_URL = (
    "https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile"
    "&LAYER=HR.ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM"
    "&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
)
# Encode only what would break the outer XYZ URI's own '&'/'=' separators (the
# query's '&','=' and the {z}/{x}/{y} braces); keep '://', '/', '?' literal to
# match the proven Esri form above, which is how QGIS's XYZ provider expects it.
# zmax=19 is this layer's real PM-matrix ceiling (z20+ answers 404, so a higher
# zmax would paint a BLANK canvas once the user zooms past z19 instead of
# letting QGIS stretch the deepest tiles; verified 2026-07-08).
_IGN_ORTHO_URI = (
    "type=xyz&url=" + urllib.parse.quote(_IGN_ORTHO_TILE_URL, safe=":/?") + "&zmax=19&zmin=0"
)
# One known-good tile (Eiffel block, z17) probed before committing to IGN: an
# XYZ layer reports isValid() from the URI alone, never from the network, so
# without this check a user whose network can't reach data.geopf.fr (blocked
# domain, corporate proxy, outage) would get a valid-but-blank demo instead of
# the global Esri fallback.
_IGN_PROBE_URL = _IGN_ORTHO_TILE_URL.format(z=17, y=45091, x=66371)
# Curated demo scene(s) for the first-run hero. One scene, chosen so the user
# can keep iterating with other prompts on the same imagery afterwards: the
# Eiffel Tower block has buildings, roads, vegetation and water in frame, and
# IGN's 20cm ortho keeps it crisp. The pre-filled preset is the most-selected
# template in production (style_site_plan, per the 60-day template_selected
# breakdown), not a demo gimmick.
_DEMO_SCENES: dict[str, dict] = {
    "paris": {
        "zone": {"xmin": 2.2895, "ymin": 48.8552, "xmax": 2.2990, "ymax": 48.8616},
        # Preset id mirrored with the website catalog, with a literal fallback
        # so the demo still has a prompt when the catalog isn't cached yet
        # (first run offline or before the first fetch).
        "preset_id": "style_site_plan",
        "fallback_preset": {
            "id": "style_site_plan",
            "label": "Clean architectural site plan",
            "prompt": "Redraw this area as a clean architectural site plan",
        },
        "prefer_ign": True,
    },
}
_DEFAULT_SCENE_ID = "paris"


class OnboardingMixin:
    """Empty-canvas one-click onboarding ("Try it on an example")."""

    def _on_try_example(self, scene_id: str = ""):
        """Empty-canvas one-click onboarding. Drop a satellite basemap and,
        when no imagery is visible, frame the chosen demo scene, pre-draw an
        example zone and pre-fill its prompt so the only remaining step is
        Generate. When a visible layer already exists, only add a global
        backdrop and leave the user's view and inputs untouched."""
        from qgis.core import QgsProject

        scene_id = scene_id if scene_id in _DEMO_SCENES else _DEFAULT_SCENE_ID
        scene = _DEMO_SCENES[scene_id]
        # The hero shows when nothing is VISIBLE; layers may still exist
        # unchecked. The user explicitly asked for a demo place, so fly there
        # unless some visible imagery would be stomped by the reframe.
        root = QgsProject.instance().layerTreeRoot()
        has_visible = any(
            node.isVisible() for node in root.findLayers() if node.layer() is not None
        )
        layer = self._add_backdrop_layer(prefer_ign=bool(scene.get("prefer_ign")))
        ok = layer is not None
        if ok and not has_visible:
            self._pending_demo_scene_id = scene_id
            # Defer past QGIS's zoom-to-first-layer (queued during addMapLayer),
            # which would otherwise snap to the whole world and undo our framing.
            QTimer.singleShot(0, self._prime_demo_scene)
        elif not ok:
            self._dock_widget.show_basemap_error()
        telemetry.track(te.BASEMAP_CTA_CLICKED, {"success": ok, "scene": scene_id})
        telemetry.flush()

    def _add_backdrop_layer(self, prefer_ign: bool):
        """Add a satellite basemap at the bottom of the layer tree (AI Edit
        outputs stack above it). France scenes prefer IGN's sharp 20cm ortho,
        falling back to global Esri when the IGN service is unreachable from
        the user's network (probed with one real tile, since XYZ validity
        never touches the network); other scenes go straight to Esri (IGN
        serves blank tiles outside France). Returns the layer, or None if
        nothing loaded."""
        from qgis.core import QgsProject, QgsRasterLayer

        layer = None
        source = ""
        if prefer_ign and self._probe_tile(_IGN_PROBE_URL):
            candidate = QgsRasterLayer(_IGN_ORTHO_URI, "Orthophoto (IGN)", "wms")
            if candidate.isValid():
                layer, source = candidate, "ign"
        if layer is None:
            candidate = QgsRasterLayer(_ESRI_WORLD_IMAGERY_URI, "Satellite (Esri)", "wms")
            if candidate.isValid():
                layer, source = candidate, "esri"
        if layer is None:
            log_warning("onboarding basemap: no source loaded")
            return None
        project = QgsProject.instance()
        project.addMapLayer(layer, False)
        project.layerTreeRoot().insertLayer(-1, layer)
        log(f"onboarding basemap added (source={source})")
        return layer

    @staticmethod
    def _probe_tile(url: str) -> bool:
        """True when one real tile answers over the user's actual network
        path (QGIS proxy settings included). Bounded by a short timeout so
        the demo click never hangs; any failure just means the global
        fallback provider is used instead."""
        from qgis.core import QgsBlockingNetworkRequest
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest

        try:
            request = QNetworkRequest(QUrl(url))
            if hasattr(request, "setTransferTimeout"):  # Qt >= 5.15
                request.setTransferTimeout(4000)
            return QgsBlockingNetworkRequest().get(request) == QtC.BlockingNoError
        except Exception as err:  # noqa: BLE001 - a probe must never break the click.
            log_warning(f"basemap probe failed: {err}")
            return False

    def _prime_demo_scene(self):
        """Frame the demo scene, pre-draw the example zone and pre-fill its
        prompt, then hold Generate until the tiles have painted."""
        from qgis.core import QgsCoordinateReferenceSystem

        scene = _DEMO_SCENES.get(
            getattr(self, "_pending_demo_scene_id", ""), _DEMO_SCENES[_DEFAULT_SCENE_ID]
        )
        wgs84_wkt = QgsCoordinateReferenceSystem("EPSG:4326").toWkt()
        # _restore_zone frames (zoom to zone x1.15), draws the rubber band and
        # flips the dock into ZONE_SELECTED - exactly the demo framing we want.
        if not self._restore_zone(dict(scene["zone"]), wgs84_wkt):
            return
        preset = get_preset_by_id(scene.get("preset_id", "")) or scene.get("fallback_preset")
        if preset:
            self._dock_widget.prime_prompt_from_preset(preset)
        self._start_imagery_gate()

    def _start_imagery_gate(self):
        """Hold Generate while the online basemap's tiles warm. Online providers
        fetch tiles async and repaint as they arrive, so exporting now would
        ship a blank input (a crop error). We debounce mapCanvasRefreshed (tiles
        settled once refreshes stop) with a hard cap so a slow or offline
        network never traps the user."""
        if self._dock_widget is None or self._canvas is None:
            return
        self._dock_widget.set_imagery_loading(True)
        # Any failure while arming the watchers must release the gate, or
        # Generate stays stuck on "Loading imagery…" forever.
        try:
            # Parent to the dock (a QObject); the plugin instance is not a
            # QObject, and an unparented QTimer would be at risk of GC.
            self._imagery_settle_timer = QTimer(self._dock_widget)
            self._imagery_settle_timer.setSingleShot(True)
            self._imagery_settle_timer.timeout.connect(self._finish_imagery_gate)
            self._imagery_cap_timer = QTimer(self._dock_widget)
            self._imagery_cap_timer.setSingleShot(True)
            self._imagery_cap_timer.timeout.connect(self._finish_imagery_gate)
            self._canvas.mapCanvasRefreshed.connect(self._on_imagery_refresh)
            self._imagery_cap_timer.start(8000)
            self._imagery_settle_timer.start(1200)
        except Exception as err:  # noqa: BLE001 - release rather than trap Generate.
            log_warning(f"imagery gate setup failed, releasing: {err}")
            self._finish_imagery_gate()

    def _on_imagery_refresh(self):
        """Each finished render restarts the quiet window; when tiles stop
        arriving the window elapses and the gate lifts."""
        if self._imagery_settle_timer is not None:
            self._imagery_settle_timer.start(1200)

    def _finish_imagery_gate(self):
        """Release Generate and tear down the warm-up watchers (idempotent)."""
        for attr in ("_imagery_settle_timer", "_imagery_cap_timer"):
            timer = getattr(self, attr, None)
            if timer is not None:
                timer.stop()
                setattr(self, attr, None)
        if self._canvas is not None:
            try:
                self._canvas.mapCanvasRefreshed.disconnect(self._on_imagery_refresh)
            except (TypeError, RuntimeError):
                pass  # nosec B110 - already disconnected.
        if self._dock_widget is not None:
            self._dock_widget.set_imagery_loading(False)
