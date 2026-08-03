from __future__ import annotations

import re
import urllib.parse

from qgis.PyQt.QtCore import QTimer

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import (
    get_export_dial,
    get_export_dial_objs,
    get_export_dial_str,
)
from ...core.i18n import tr
from ...core.logger import log, log_warning

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
# Curated demo scene(s) for the first-run hero. One scene: the Eiffel Tower
# block has buildings, roads, vegetation and water in frame, and IGN's 20cm
# ortho keeps it crisp. The demo only brings the imagery into view; drawing a
# zone and writing a prompt stays entirely up to the user, same as with their
# own imagery.
_DEMO_SCENES: dict[str, dict] = {
    "paris": {
        "extent": {"xmin": 2.2895, "ymin": 48.8552, "xmax": 2.2990, "ymax": 48.8616},
        "prefer_ign": True,
    },
}
_DEFAULT_SCENE_ID = "paris"
# How long the demo holds Generate while the basemap's tiles paint: the quiet
# window that has to elapse after the last repaint, and the hard ceiling that
# releases the gate on a slow or offline network.
_IMAGERY_SETTLE_MS = 1200
_IMAGERY_CAP_MS = 8000


# -- served onboarding sources ----------------------------------------------
# Every value in this section is a third party's address or a place on Earth,
# which is exactly the kind of thing that rots between releases: IGN has already
# renamed its ortho layer once, and a tile host that moves takes the first-run
# demo down for the whole fleet until a plugin ships. Served, a rename is one
# deploy. Nothing here is trusted: a URI must parse as an https XYZ source and a
# scene must be a real box in degrees, or the shipped value stands.

# What a QGIS raster URI may look like before it is handed to QgsRasterLayer.
# It goes to a tile server and paints in the user's project, so a served one has
# to be the same shape as the two shipped above: an XYZ source over https, with
# none of the characters that would let a value break out of the URI's own
# separators.
_URI_MAX_CHARS = 600
_URI_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f-\x9f<>\"'\\]")
_XYZ_URI_PREFIX = "type=xyz&url=https://"
# A scene id keys telemetry and a QSettings-free lookup; keep it a short slug.
_SCENE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# One deploy may not turn the demo into a catalogue.
_MAX_SERVED_SCENES = 12


def _is_safe_xyz_uri(value) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_XYZ_URI_PREFIX)
        and len(value) <= _URI_MAX_CHARS
        and not _URI_FORBIDDEN_RE.search(value)
    )


def _served_basemap_uri(key: str, fallback: str) -> str:
    """A served XYZ basemap URI, or the shipped one."""
    value = get_export_dial_str(f"onboarding.{key}", fallback)
    return value if _is_safe_xyz_uri(value) else fallback


def _served_probe_url(fallback: str) -> str:
    """The one tile probed before committing to the regional source."""
    value = get_export_dial_str("onboarding.regional_probe_url", fallback)
    if (
        isinstance(value, str)
        and value.startswith("https://")
        and len(value) <= _URI_MAX_CHARS
        and not _URI_FORBIDDEN_RE.search(value)
    ):
        return value
    return fallback


def _valid_scene(entry: dict) -> tuple[str, dict] | None:
    """One served scene as (id, scene), or None when anything about it is off.

    Whole-record validation: a scene that is half right frames the canvas on
    nothing, so a single failed field drops the entry rather than filling in a
    default for it. Extents are degrees, and they are transformed into the
    project CRS later, so they must be a real box inside WGS84 bounds.
    """
    scene_id = entry.get("id")
    if not isinstance(scene_id, str) or not _SCENE_ID_RE.match(scene_id):
        return None
    extent = entry.get("extent")
    if not isinstance(extent, dict):
        return None
    box = {}
    for field in ("xmin", "ymin", "xmax", "ymax"):
        value = extent.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        box[field] = float(value)
    if not (-180.0 <= box["xmin"] < box["xmax"] <= 180.0):
        return None
    if not (-90.0 <= box["ymin"] < box["ymax"] <= 90.0):
        return None
    return scene_id, {"extent": box, "prefer_ign": entry.get("prefer_ign") is True}


def demo_scenes() -> dict[str, dict]:
    """The demo scenes on offer: the shipped ones, then any served addition.

    Union-only, like every other served list here: a deploy ADDS a place and can
    never drop the one the plugin ships, so the demo always has somewhere to go
    even when the served block is wrong.
    """
    scenes = dict(_DEMO_SCENES)
    for entry in get_export_dial_objs("onboarding.scenes", _MAX_SERVED_SCENES):
        valid = _valid_scene(entry)
        if valid is not None and valid[0] not in scenes:
            scenes[valid[0]] = valid[1]
    return scenes


def default_scene_id() -> str:
    """Where the demo goes when the caller names no scene. Restricted to the
    scenes actually on offer, so a typo lands on the shipped one rather than on
    nothing."""
    return get_export_dial_str(
        "onboarding.default_scene",
        _DEFAULT_SCENE_ID,
        allowed=set(demo_scenes()),
    )


class OnboardingMixin:
    """Empty-canvas one-click onboarding ("Try it on an example")."""

    def _auto_load_example_after_signup(self):
        """Post-signup continuity: a fresh account must never land on an empty
        canvas with a question. When the pairing lands and nothing is visible,
        load the demo scene unprompted and arm the zone tool; the account step
        is over and the one next step is drawing a zone. A user whose project
        already shows imagery, or whose demo is switched off server-side, sees
        nothing change."""
        from qgis.core import QgsProject

        from ...core.auth.activation_manager import is_feature_enabled

        if self._dock_widget is None:
            return
        if not is_feature_enabled("demo"):
            return
        root = QgsProject.instance().layerTreeRoot()
        has_visible = any(
            node.isVisible() for node in root.findLayers() if node.layer() is not None
        )
        if has_visible:
            return
        self._on_try_example(trigger="auto")

    def _on_try_example(self, scene_id: str = "", trigger: str = "click"):
        """Empty-canvas one-click onboarding. Drop a satellite basemap and,
        when no imagery is visible, zoom to the chosen demo scene so there is
        real imagery to work with. The user still draws their own zone,
        writes their own prompt and clicks Generate themselves, exactly like
        with any imagery they bring in. When a visible layer already exists,
        only add a global backdrop and leave the user's view and inputs
        untouched. France scenes first probe IGN off-thread (a blocking probe
        would freeze the click for up to 4s); the click finishes in
        _finish_try_example once the source is decided.

        ``trigger`` is "click" (the hero button) or "auto" (the post-signup
        load); the auto path also arms the zone tool once the scene is in."""
        from qgis.core import QgsApplication, QgsProject

        from ...workers.generic_request_task import GenericRequestTask

        probe = getattr(self, "_basemap_probe_task", None)
        if probe is not None and probe.is_active():
            return  # a previous click's probe is still deciding the source
        scenes = demo_scenes()
        scene_id = scene_id if scene_id in scenes else default_scene_id()
        scene = scenes[scene_id]
        # The hero shows when nothing is VISIBLE; layers may still exist
        # unchecked. The user explicitly asked for a demo place, so fly there
        # unless some visible imagery would be stomped by the reframe.
        root = QgsProject.instance().layerTreeRoot()
        has_visible = any(
            node.isVisible() for node in root.findLayers() if node.layer() is not None
        )
        if not scene.get("prefer_ign"):
            self._finish_try_example(scene_id, has_visible, ign_ok=False, trigger=trigger)
            return
        probe_url = _served_probe_url(_IGN_PROBE_URL)
        task = GenericRequestTask(
            tr("Checking imagery availability"),
            lambda url=probe_url: {"ok": self._probe_tile(url)},
            silent=True,
        )
        task.succeeded.connect(
            lambda result, sid=scene_id, hv=has_visible, tg=trigger: self._finish_try_example(
                sid, hv, ign_ok=bool((result or {}).get("ok")), trigger=tg
            )
        )
        # A probe failure just means the global fallback provider is used.
        task.failed.connect(
            lambda _msg, _code, sid=scene_id, hv=has_visible, tg=trigger: self._finish_try_example(
                sid, hv, ign_ok=False, trigger=tg
            )
        )
        # Hard ref: a QgsTask GC'd mid-run aborts QGIS.
        self._basemap_probe_task = task
        QgsApplication.taskManager().addTask(task)

    def _finish_try_example(
        self, scene_id: str, has_visible: bool, ign_ok: bool, trigger: str = "click"
    ):
        """Main-thread second half of the demo click, once the probe answered."""
        self._basemap_probe_task = None
        if self._dock_widget is None:
            return  # plugin unloaded while the probe was in flight
        layer = self._add_backdrop_layer(use_ign=ign_ok)
        ok = layer is not None
        if ok and not has_visible:
            self._pending_demo_scene_id = scene_id
            # Defer past QGIS's zoom-to-first-layer (queued during addMapLayer),
            # which would otherwise snap to the whole world and undo our framing.
            QTimer.singleShot(0, self._frame_demo_scene)
        elif not ok:
            self._dock_widget.show_basemap_error()
        if ok and trigger == "auto":
            # Queued after _frame_demo_scene's deferred zoom, so the tool arms
            # on the framed scene, not mid-flight.
            QTimer.singleShot(0, self._arm_first_zone_after_signup)
        # `trigger` splits the funnel: hero clicks vs the post-signup autoload.
        telemetry.track(
            te.BASEMAP_CTA_CLICKED,
            {"success": ok, "scene": scene_id, "trigger": trigger},
        )
        telemetry.flush()

    def _arm_first_zone_after_signup(self):
        """Close the signup loop: say the account step is done and start zone
        selection, exactly what a Launch click does (minus its telemetry - the
        launch_clicked funnel stays user-intent only)."""
        if self._dock_widget is None:
            return
        self._disarm_swipe()
        self._activate_selection_tool()
        self._dock_widget.set_selecting_zone_state()
        self._dock_widget._show_status_box(
            "✓ " + tr(
                "Account created. Draw a zone on the example map to run "
                "your first edit."
            ),
            "success",
        )

    def _add_backdrop_layer(self, use_ign: bool):
        """Add a satellite basemap at the bottom of the layer tree (AI Edit
        outputs stack above it). ``use_ign`` is True when the off-thread probe
        confirmed IGN's sharp 20cm ortho answers on the user's network (one
        real tile, since XYZ validity never touches the network); anything
        else goes to global Esri (IGN serves blank tiles outside France).
        Returns the layer, or None if nothing loaded."""
        from qgis.core import QgsProject, QgsRasterLayer

        layer = None
        source = ""
        if use_ign:
            uri = _served_basemap_uri("regional_basemap_uri", _IGN_ORTHO_URI)
            candidate = QgsRasterLayer(uri, "Orthophoto (IGN)", "wms")
            if candidate.isValid():
                layer, source = candidate, "ign"
        if layer is None:
            uri = _served_basemap_uri("global_basemap_uri", _ESRI_WORLD_IMAGERY_URI)
            candidate = QgsRasterLayer(uri, "Satellite (Esri)", "wms")
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
        path (QGIS proxy settings included). Blocking, so it must run inside
        a worker task; the timeout only bounds how long that task waits.
        Any failure just means the global fallback provider is used."""
        from qgis.core import QgsBlockingNetworkRequest
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest

        try:
            request = QNetworkRequest(QUrl(url))
            QtC.set_transfer_timeout(request, 4000)  # no-op before Qt 5.15
            return QgsBlockingNetworkRequest().get(request) == QtC.BlockingNoError
        except Exception as err:  # noqa: BLE001 - a probe must never break the click.
            log_warning(f"basemap probe failed: {err}")
            return False

    def _frame_demo_scene(self):
        """Zoom the canvas to the demo scene's location (scaled out a touch,
        matching the framing used elsewhere) and hold Generate until the
        tiles have painted. Nothing else is pre-filled: the user draws their
        own zone and writes their own prompt from here, same as with any
        imagery they bring in themselves."""
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsProject,
            QgsRectangle,
        )

        scenes = demo_scenes()
        scene = scenes.get(
            getattr(self, "_pending_demo_scene_id", ""), scenes[default_scene_id()]
        )
        extent = scene["extent"]
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        rect = QgsRectangle(
            float(extent["xmin"]), float(extent["ymin"]),
            float(extent["xmax"]), float(extent["ymax"]),
        )
        canvas_crs = self._canvas.mapSettings().destinationCrs()
        if wgs84 != canvas_crs:
            try:
                xform = QgsCoordinateTransform(wgs84, canvas_crs, QgsProject.instance())
                rect = xform.transformBoundingBox(rect)
            except Exception as err:  # noqa: BLE001 - a broken transform must not block the demo.
                log_warning(f"demo scene transform failed: {err}")
                self._start_imagery_gate()
                return
        rect.scale(1.15)
        self._canvas.setExtent(rect)
        self._canvas.refresh()
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
            self._imagery_cap_timer.start(
                get_export_dial("onboarding.imagery_cap_ms", _IMAGERY_CAP_MS)
            )
            self._imagery_settle_timer.start(
                get_export_dial("onboarding.imagery_settle_ms", _IMAGERY_SETTLE_MS)
            )
        except Exception as err:  # noqa: BLE001 - release rather than trap Generate.
            log_warning(f"imagery gate setup failed, releasing: {err}")
            self._finish_imagery_gate()

    def _on_imagery_refresh(self):
        """Each finished render restarts the quiet window; when tiles stop
        arriving the window elapses and the gate lifts."""
        if self._imagery_settle_timer is not None:
            self._imagery_settle_timer.start(
                get_export_dial("onboarding.imagery_settle_ms", _IMAGERY_SETTLE_MS)
            )

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
