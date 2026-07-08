from __future__ import annotations

import re
import urllib.parse

from qgis.PyQt.QtCore import QTimer

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import (
    get_export_copy,
    get_export_dial,
    get_export_dial_objs,
    get_export_dial_str,
)
from ...core.i18n import tr
from ...core.logger import log, log_warning





_ESRI_WORLD_IMAGERY_URI = (
    "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D&zmax=21&zmin=0"
)






_IGN_ORTHO_TILE_URL = (
    "https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile"
    "&LAYER=HR.ORTHOIMAGERY.ORTHOPHOTOS&STYLE=normal&TILEMATRIXSET=PM"
    "&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
)






_IGN_ORTHO_URI = (
    "type=xyz&url=" + urllib.parse.quote(_IGN_ORTHO_TILE_URL, safe=":/?") + "&zmax=19&zmin=0"
)





_IGN_PROBE_URL = _IGN_ORTHO_TILE_URL.format(z=17, y=45091, x=66371)





_DEMO_SCENES: dict[str, dict] = {
    "paris": {
        "extent": {"xmin": 2.2895, "ymin": 48.8552, "xmax": 2.2990, "ymax": 48.8616},
        "prefer_ign": True,
    },
}
_DEFAULT_SCENE_ID = "paris"



_IMAGERY_SETTLE_MS = 1200
_IMAGERY_CAP_MS = 8000

_PROBE_TIMEOUT_MS = 4000















_URI_MAX_CHARS = 600
_URI_FORBIDDEN_RE = re.compile(r"[\x00-\x20\x7f-\x9f<>\"'\\]")
_XYZ_URI_PREFIX = "type=xyz&url=https://"

_SCENE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

_MAX_SERVED_SCENES = 12


def _is_safe_xyz_uri(value) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_XYZ_URI_PREFIX)
        and len(value) <= _URI_MAX_CHARS
        and not _URI_FORBIDDEN_RE.search(value)
    )


def _served_basemap_uri(key: str, fallback: str) -> str:

    value = get_export_dial_str(f"onboarding.{key}", fallback)
    return value if _is_safe_xyz_uri(value) else fallback


def _served_probe_url(fallback: str) -> str:

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






    scenes = dict(_DEMO_SCENES)
    for entry in get_export_dial_objs("onboarding.scenes", _MAX_SERVED_SCENES):
        valid = _valid_scene(entry)
        if valid is not None and valid[0] not in scenes:
            scenes[valid[0]] = valid[1]
    return scenes


def default_scene_id() -> str:



    return get_export_dial_str(
        "onboarding.default_scene",
        _DEFAULT_SCENE_ID,
        allowed=set(demo_scenes()),
    )


class OnboardingMixin:


    def _auto_load_example_after_signup(self):






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












        from qgis.core import QgsApplication, QgsProject

        from ...workers.generic_request_task import GenericRequestTask

        probe = getattr(self, "_basemap_probe_task", None)
        if probe is not None and probe.is_active():
            return
        scenes = demo_scenes()
        scene_id = scene_id if scene_id in scenes else default_scene_id()
        scene = scenes[scene_id]



        root = QgsProject.instance().layerTreeRoot()
        has_visible = any(
            node.isVisible() for node in root.findLayers() if node.layer() is not None
        )
        if not scene.get("prefer_ign"):
            self._finish_try_example(scene_id, has_visible, ign_ok=False, trigger=trigger)
            return
        probe_url = _served_probe_url(_IGN_PROBE_URL)
        task = GenericRequestTask(
            get_export_copy(
                "flows.onboarding.checking_imagery_task", tr("Checking imagery availability")
            ),
            lambda url=probe_url: {"ok": self._probe_tile(url)},
            silent=True,
        )
        task.succeeded.connect(
            lambda result, sid=scene_id, hv=has_visible, tg=trigger: self._finish_try_example(
                sid, hv, ign_ok=bool((result or {}).get("ok")), trigger=tg
            )
        )

        task.failed.connect(
            lambda _msg, _code, sid=scene_id, hv=has_visible, tg=trigger: self._finish_try_example(
                sid, hv, ign_ok=False, trigger=tg
            )
        )

        self._basemap_probe_task = task
        QgsApplication.taskManager().addTask(task)

    def _finish_try_example(
        self, scene_id: str, has_visible: bool, ign_ok: bool, trigger: str = "click"
    ):

        self._basemap_probe_task = None
        if self._dock_widget is None:
            return
        layer = self._add_backdrop_layer(use_ign=ign_ok)
        ok = layer is not None
        if ok and not has_visible:
            self._pending_demo_scene_id = scene_id


            QTimer.singleShot(0, self._frame_demo_scene)
        elif not ok:
            self._dock_widget.show_basemap_error()
        if ok and trigger == "auto":


            QTimer.singleShot(0, self._arm_first_zone_after_signup)

        telemetry.track(
            te.BASEMAP_CTA_CLICKED,
            {"success": ok, "scene": scene_id, "trigger": trigger},
        )
        telemetry.flush()

    def _arm_first_zone_after_signup(self):



        if self._dock_widget is None:
            return
        self._disarm_swipe()
        self._activate_selection_tool()
        self._dock_widget.set_selecting_zone_state()
        self._dock_widget._show_status_box(

            get_export_copy(
                "flows.onboarding.account_created_v2",
                tr("Account created. Outline an area on the example map to "
                   "make your first edit."),
            ),
            "success",
        )

    def _add_backdrop_layer(self, use_ign: bool):






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




        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest

        from ...api.blocking_request import BlockingRequest

        try:
            request = QNetworkRequest(QUrl(url))

            QtC.set_transfer_timeout(
                request, get_export_dial("flows.onboarding.probe_timeout_ms", _PROBE_TIMEOUT_MS)
            )
            blocker = BlockingRequest()


            if blocker.get(request, forceRefresh=True) != QtC.BlockingNoError:
                return False


            return blocker.reply().error() == QtC.NetworkNoError
        except Exception as err:  # noqa: BLE001
            log_warning(f"basemap probe failed: {err}")
            return False

    def _frame_demo_scene(self):





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
            except Exception as err:  # noqa: BLE001
                log_warning(f"demo scene transform failed: {err}")
                self._start_imagery_gate()
                return
        rect.scale(1.15)
        self._canvas.setExtent(rect)
        self._canvas.refresh()
        self._start_imagery_gate()

    def _start_imagery_gate(self):





        if self._dock_widget is None or self._canvas is None:
            return
        self._dock_widget.set_imagery_loading(True)


        try:


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
        except Exception as err:  # noqa: BLE001
            log_warning(f"imagery gate setup failed, releasing: {err}")
            self._finish_imagery_gate()

    def _on_imagery_refresh(self):


        if self._imagery_settle_timer is not None:
            self._imagery_settle_timer.start(
                get_export_dial("onboarding.imagery_settle_ms", _IMAGERY_SETTLE_MS)
            )

    def _finish_imagery_gate(self):

        for attr in ("_imagery_settle_timer", "_imagery_cap_timer"):
            timer = getattr(self, attr, None)
            if timer is not None:
                timer.stop()
                setattr(self, attr, None)
        if self._canvas is not None:
            try:
                self._canvas.mapCanvasRefreshed.disconnect(self._on_imagery_refresh)
            except (TypeError, RuntimeError):
                pass  # nosec B110
        if self._dock_widget is not None:
            self._dock_widget.set_imagery_loading(False)
