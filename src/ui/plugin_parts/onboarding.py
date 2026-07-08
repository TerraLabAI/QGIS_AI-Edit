from __future__ import annotations

import urllib.parse

from qgis.PyQt.QtCore import QTimer

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
_IGN_ORTHO_URI = (
    "type=xyz&url=" + urllib.parse.quote(_IGN_ORTHO_TILE_URL, safe=":/?") + "&zmax=21&zmin=0"
)
# Eiffel Tower + the Seine (tower ~48.8584, 2.2945), WGS84. The example zone is
# framed and drawn here: Paris's most recognisable landmark with the river in
# frame, so the pre-filled sea-level-rise prompt has real water to grow from and
# the first result lands as a striking "flood around the Eiffel Tower".
_DEMO_ZONE_WGS84 = {"xmin": 2.2895, "ymin": 48.8552, "xmax": 2.2990, "ymax": 48.8616}
# Preset id (mirrored with the website catalog) pre-filled into the prompt:
# photorealistic sea-level rise, a top-pick climate scenario that reads as an
# instant "wow" on a waterfront scene. A no-op if the catalog isn't cached yet
# (first run offline), so the zone is still drawn.
_DEMO_PRESET_ID = "simulate_sea_level"


class OnboardingMixin:
    """Empty-canvas one-click onboarding ("Try it on an example")."""

    def _on_try_example(self):
        """Empty-canvas one-click onboarding. Drop a satellite basemap and, on a
        blank project, frame a known demo scene, pre-draw an example zone and
        pre-fill a land-cover prompt so the only remaining step is Generate. On
        a project that already has layers, only add a global backdrop and leave
        the user's view and inputs untouched."""
        from qgis.core import QgsProject

        was_empty = len(QgsProject.instance().mapLayers()) == 0
        layer = self._add_backdrop_layer(demo=was_empty)
        ok = layer is not None
        if ok and was_empty:
            # Defer past QGIS's zoom-to-first-layer (queued during addMapLayer),
            # which would otherwise snap to the whole world and undo our framing.
            QTimer.singleShot(0, self._prime_demo_scene)
        elif not ok:
            self._dock_widget.show_basemap_error()
        telemetry.track(te.BASEMAP_CTA_CLICKED, {"success": ok})
        telemetry.flush()

    def _add_backdrop_layer(self, demo: bool):
        """Add a satellite basemap at the bottom of the layer tree (AI Edit
        outputs stack above it). On the demo path prefer IGN's sharp France
        ortho, falling back to global Esri if it won't load; otherwise use Esri
        directly. Returns the layer, or None if nothing loaded."""
        from qgis.core import QgsProject, QgsRasterLayer

        layer = None
        source = ""
        if demo:
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

    def _prime_demo_scene(self):
        """Frame the demo scene, pre-draw the example zone and pre-fill the
        land-cover prompt, then hold Generate until the tiles have painted."""
        from qgis.core import QgsCoordinateReferenceSystem

        wgs84_wkt = QgsCoordinateReferenceSystem("EPSG:4326").toWkt()
        # _restore_zone frames (zoom to zone x1.15), draws the rubber band and
        # flips the dock into ZONE_SELECTED - exactly the demo framing we want.
        if not self._restore_zone(dict(_DEMO_ZONE_WGS84), wgs84_wkt):
            return
        preset = get_preset_by_id(_DEMO_PRESET_ID)
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
