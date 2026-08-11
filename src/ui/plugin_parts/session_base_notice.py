














from __future__ import annotations

import os

from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.logger import log_warning
from ...core.prompts import history_cache
from ...workers.generic_request_task import GenericRequestTask
from ..raster_writer import (
    add_geotiff_to_project,
    extent_and_crs_from_job,
    get_output_dir,
)


_NOTIFY_QUICK_S = 2
_NOTIFY_BRIEF_S = 4
_NOTIFY_ERROR_S = 6


class SessionBaseNoticeMixin:


    def _arm_base_imagery_check(self, chain: list) -> None:





        self._dismiss_base_imagery_notice()
        self._base_check_token = self._session_id
        self._base_check_source_job = chain[0] if chain else None

    def _disarm_base_imagery_check(self) -> None:


        self._base_check_token = None
        self._base_check_source_job = None

    def _fire_base_imagery_check(self) -> None:




        token = getattr(self, "_base_check_token", None)
        if token is None:
            return
        job = getattr(self, "_base_check_source_job", None)
        self._disarm_base_imagery_check()
        if token != self._session_id:
            return
        try:
            if self._project_has_visible_base_layer():
                return
        except Exception as err:  # nosec B110
            log_warning(f"base imagery check failed: {err}")
            return
        self._push_base_imagery_notice(job)

    def _project_has_visible_base_layer(self) -> bool:




        from qgis.core import QgsProject

        from ..layer_groups import MARKUP_LAYER_PROPERTY, collect_ai_edit_layer_ids

        own_ids = collect_ai_edit_layer_ids()
        for node in QgsProject.instance().layerTreeRoot().findLayers():
            if not node.isVisible():
                continue
            if node.layerId() in own_ids:
                continue
            layer = node.layer()
            if layer is None or layer.customProperty(MARKUP_LAYER_PROPERTY):
                continue
            return True
        return False



    def _push_base_imagery_notice(self, job: dict | None) -> None:




        from qgis.core import Qgis
        from qgis.PyQt.QtCore import Qt
        from qgis.PyQt.QtWidgets import QPushButton

        from ..dock import design_tokens as tokens

        self._dismiss_base_imagery_notice()
        try:
            bar = self._iface.messageBar()
            widget = bar.createMessage(
                "AI Edit",
                get_export_copy(
                    "flows.session_base_notice.missing_base_original",
                    tr("The imagery this session was edited on is not in this project. "
                       "Add its Original to see your edits in context."),
                ),
            )
            if self._session_base_source_available(job):
                button = QPushButton(
                    get_export_copy(
                        "flows.session_base_notice.add_original", tr("Add the Original")
                    )
                )

                button.setStyleSheet(tokens.BTN_GHOST_QSS)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.clicked.connect(
                    lambda _checked=False, j=job: self._on_add_session_base(j)
                )
                widget.layout().addWidget(button)
            bar.pushWidget(widget, Qgis.MessageLevel.Info, 0)
            self._base_notice_widget = widget
        except Exception as err:  # nosec B110
            log_warning(f"base imagery notice push failed: {err}")

    def _dismiss_base_imagery_notice(self) -> None:


        widget = getattr(self, "_base_notice_widget", None)
        self._base_notice_widget = None
        if widget is None:
            return
        try:
            self._iface.messageBar().popWidget(widget)
        except Exception as err:  # nosec B110
            log_warning(f"base imagery notice pop failed: {err}")



    def _session_base_source_available(self, job: dict | None) -> bool:


        if not isinstance(job, dict):
            return False
        if self._session_base_local_path(job):
            return True
        return bool(job.get("input_url"))

    @staticmethod
    def _session_base_local_path(job: dict) -> str:


        local = history_cache.get_output_paths(job.get("request_id") or "")
        before = (local or {}).get("before_path") or ""
        return before if before and os.path.isfile(before) else ""

    def _on_add_session_base(self, job: dict) -> None:


        self._dismiss_base_imagery_notice()
        local_path = self._session_base_local_path(job)
        if local_path:
            self._add_session_base_layer(
                {"path": local_path, "crs_wkt": "", "source": "local"}
            )
            return
        input_url = job.get("input_url")
        if not input_url or self._client is None:
            self._notify(
                get_export_copy(
                    "flows.session_base_notice.image_unavailable",
                    tr("This generation's image is no longer available."),
                ),
                duration=get_export_dial("flows.session_base_notice.notify_brief_s", _NOTIFY_BRIEF_S),
            )
            return
        geo = extent_and_crs_from_job(job)
        if geo is None:
            self._notify(
                get_export_copy(
                    "flows.session_base_notice.location_unavailable",
                    tr("Location data unavailable for this generation."),
                ),
                duration=get_export_dial("flows.session_base_notice.notify_brief_s", _NOTIFY_BRIEF_S),
            )
            return
        extent_dict, crs_wkt = geo
        rid = job.get("request_id") or ""
        output_dir = get_output_dir()

        def _work(url=input_url, ed=extent_dict, wkt=crs_wkt, d=output_dir, r=rid):
            from ..raster_writer import write_geotiff

            data = self._client.download_image(url)
            path = write_geotiff(data, ed, wkt, d, prompt="session input")
            return {"path": path, "crs_wkt": wkt, "request_id": r, "source": "download"}

        task = GenericRequestTask(
            get_export_copy(
                "flows.session_base_notice.adding_session_input_task",
                tr("Adding session input to the map"),
            ),
            _work,
        )
        task.succeeded.connect(self._on_session_base_ready)
        task.failed.connect(self._on_session_base_failed)
        self._notify(
            get_export_copy("flows.session_base_notice.adding_to_map_status", tr("Adding to map...")),
            duration=get_export_dial("flows.session_base_notice.notify_quick_s", _NOTIFY_QUICK_S),
        )
        self._hold_history_task(task)

    def _on_session_base_failed(self, msg: str, code: str) -> None:


        self._track_history_error("session_base_download_failed", msg, code)
        self._notify(
            tr("Could not add to map: {msg}").format(msg=msg),
            duration=get_export_dial("flows.session_base_notice.notify_error_s", _NOTIFY_ERROR_S),
        )

    def _on_session_base_ready(self, result: dict) -> None:
        result = result or {}
        if not result.get("path"):
            return
        self._add_session_base_layer(result)


        history_cache.save_before_path(result.get("request_id") or "", result["path"])

    def _add_session_base_layer(self, info: dict) -> None:





        from qgis.core import Qgis

        try:
            layer = add_geotiff_to_project(
                info.get("path") or "", "", crs_wkt=info.get("crs_wkt") or ""
            )
        except Exception as err:  # noqa: BLE001
            self._track_history_error("session_base_add_failed")
            self._notify(
                tr("Could not add layer: {msg}").format(msg=err),
                duration=get_export_dial("flows.session_base_notice.notify_error_s", _NOTIFY_ERROR_S),
            )
            return
        if layer is None:
            return
        layer.setName(
            get_export_copy(
                "flows.session_base_notice.original_layer_name", tr("Original")
            )
        )
        self._label_session_base_metadata(layer)
        self._move_session_base_below_group(layer.id())
        try:
            self._canvas.refresh()
        except Exception as err:  # nosec B110
            log_warning(f"canvas refresh after session base add failed: {err}")
        self._notify(
            get_export_copy(
                "flows.session_base_notice.added_to_map_as", tr("Added to your map as {name}")
            ).replace("{name}", layer.name()),
            level=Qgis.MessageLevel.Success,
            duration=get_export_dial("flows.session_base_notice.notify_brief_s", _NOTIFY_BRIEF_S),
        )

    @staticmethod
    def _label_session_base_metadata(layer) -> None:



        try:
            md = layer.metadata()
            md.setTitle(layer.name())
            md.setAbstract(
                "Snapshot of the source imagery an AI Edit session was edited on"
                " (archived session input)."
            )
            layer.setMetadata(md)
        except Exception as err:  # nosec B110
            log_warning(f"session base metadata skipped: {err}")

    def _move_session_base_below_group(self, layer_id: str) -> None:





        from qgis.core import QgsProject

        try:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(layer_id)
            if node is None:
                return
            group = node.parent()
            if group is None or group is root:
                return
            dest = group.parent() or root
            at = next(
                (i for i, child in enumerate(dest.children()) if child is group),
                None,
            )
            clone = node.clone()
            if at is None:
                dest.addChildNode(clone)
            else:
                dest.insertChildNode(at + 1, clone)
            group.removeChildNode(node)
        except Exception as err:  # nosec B110
            log_warning(f"session base move below AI-Edit failed: {err}")
