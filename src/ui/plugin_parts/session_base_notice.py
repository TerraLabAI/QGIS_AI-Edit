"""Missing-base notice for restored sessions.

A session reopened in a project (or on a machine) that no longer holds the
imagery it was edited on shows its AI result over a bare canvas with no
explanation. Once per restore, after the restored strip is seeded and the
resumed version's layer has landed, this mixin checks whether the project
holds any visible layer besides AI Edit's own; when none exists it pushes an
INFO message bar (nothing is broken) with a one-click button that adds the
session's archived input image as a base layer: local before-sidecar first
(zero network), else an off-thread download.

Base class of HistoryMixin (history.py owns the restore pipeline and only
arms/fires/disarms the check at its completion points); split out so
history.py stays within its size budget.
"""
from __future__ import annotations

import os

from ...core.i18n import tr
from ...core.logger import log_warning
from ...core.prompts import history_cache
from ...workers.generic_request_task import GenericRequestTask
from ..raster_writer import (
    add_geotiff_to_project,
    extent_and_crs_from_job,
    get_output_dir,
)


class SessionBaseNoticeMixin:
    # --- once-per-restore arming ------------------------------------------

    def _arm_base_imagery_check(self, chain: list) -> None:
        """Arm the one-shot base check for the restore that just seeded the
        strip. ``chain`` is the session oldest-first, so chain[0] carries the
        archived input of the imagery everything was edited on. The token
        pins the check to this lineage: a new zone or another restore mints a
        new session id, and a stale armed check can never fire."""
        self._dismiss_base_imagery_notice()
        self._base_check_token = self._session_id
        self._base_check_source_job = chain[0] if chain else None

    def _disarm_base_imagery_check(self) -> None:
        """Drop the armed check without firing (restore landing failed, or a
        generation took over). No-op when nothing is armed."""
        self._base_check_token = None
        self._base_check_source_job = None

    def _fire_base_imagery_check(self) -> None:
        """Consume the armed check: called from the two restore completion
        points (strip seeded with nothing left to download, or the resumed
        version's layer landed). Runs at most once per restore; later version
        clicks find the token already consumed."""
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
        except Exception as err:  # nosec B110 - advisory notice only
            log_warning(f"base imagery check failed: {err}")
            return
        self._push_base_imagery_notice(job)

    def _project_has_visible_base_layer(self) -> bool:
        """True when any VISIBLE layer in the project is not one of AI Edit's
        own (generated rasters, vectorize outputs, the Mark up layer). One
        such layer means the user has a base of their own under the AI
        result, even if it differs from the session's original imagery."""
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

    # --- the message bar notice -------------------------------------------

    def _push_base_imagery_notice(self, job: dict | None) -> None:
        """INFO bar telling the user why the canvas under the AI result is
        bare. Sticky (duration 0) so the button survives until acted on or
        dismissed; the next restore replaces it instead of stacking. The
        button only appears when the archived input is actually producible."""
        from qgis.core import Qgis
        from qgis.PyQt.QtWidgets import QPushButton

        self._dismiss_base_imagery_notice()
        try:
            bar = self._iface.messageBar()
            widget = bar.createMessage(
                "AI Edit",
                tr("The imagery this session was edited on is not in this project."),
            )
            if self._session_base_source_available(job):
                button = QPushButton(tr("Add source snapshot"))
                button.clicked.connect(
                    lambda _checked=False, j=job: self._on_add_session_base(j)
                )
                widget.layout().addWidget(button)
            bar.pushWidget(widget, Qgis.MessageLevel.Info, 0)
            self._base_notice_widget = widget
        except Exception as err:  # nosec B110 - a notice must never break restore
            log_warning(f"base imagery notice push failed: {err}")

    def _dismiss_base_imagery_notice(self) -> None:
        """Pop the previous notice so quick successive restores never stack
        two of them. Safe when the user already closed it."""
        widget = getattr(self, "_base_notice_widget", None)
        self._base_notice_widget = None
        if widget is None:
            return
        try:
            self._iface.messageBar().popWidget(widget)
        except Exception as err:  # nosec B110 - already closed by the user
            log_warning(f"base imagery notice pop failed: {err}")

    # --- one-click "Add source snapshot" ----------------------------------

    def _session_base_source_available(self, job: dict | None) -> bool:
        """True when the archived input can be produced: its before sidecar
        is still on this disk, or the job still carries input_url."""
        if not isinstance(job, dict):
            return False
        if self._session_base_local_path(job):
            return True
        return bool(job.get("input_url"))

    @staticmethod
    def _session_base_local_path(job: dict) -> str:
        """Path of the session input already on this disk (the before sidecar
        written when this job's output materialized here), or ""."""
        local = history_cache.get_output_paths(job.get("request_id") or "")
        before = (local or {}).get("before_path") or ""
        return before if before and os.path.isfile(before) else ""

    def _on_add_session_base(self, job: dict) -> None:
        """Add the session's archived input as a georeferenced base layer:
        local sidecar first (zero network), else download it off-thread."""
        self._dismiss_base_imagery_notice()
        local_path = self._session_base_local_path(job)
        if local_path:
            self._add_session_base_layer(
                {"path": local_path, "crs_wkt": "", "source": "local"}
            )
            return
        input_url = job.get("input_url")
        if not input_url or self._client is None:
            self._notify(tr("This generation's image is no longer available."), duration=4)
            return
        geo = extent_and_crs_from_job(job)
        if geo is None:
            self._notify(tr("Location data unavailable for this generation."), duration=4)
            return
        extent_dict, crs_wkt = geo
        rid = job.get("request_id") or ""
        output_dir = get_output_dir()

        def _work(url=input_url, ed=extent_dict, wkt=crs_wkt, d=output_dir, r=rid):
            from ..raster_writer import write_geotiff

            data = self._client.download_image(url)
            path = write_geotiff(data, ed, wkt, d, prompt="session input")
            return {"path": path, "crs_wkt": wkt, "request_id": r, "source": "download"}

        task = GenericRequestTask(tr("Adding session input to the map"), _work)
        task.succeeded.connect(self._on_session_base_ready)
        task.failed.connect(self._on_session_base_failed)
        self._notify(tr("Adding to map..."), duration=2)
        self._hold_history_task(task)

    def _on_session_base_failed(self, msg: str, _code: str) -> None:
        """Offline or expired archive: the click no-ops with an explanation,
        same pattern as the neighbouring history downloads."""
        self._track_history_error("session_base_download_failed")
        self._notify(tr("Could not add to map: {msg}").format(msg=msg), duration=6)

    def _on_session_base_ready(self, result: dict) -> None:
        result = result or {}
        if not result.get("path"):
            return
        self._add_session_base_layer(result)
        # Index the download as this job's before sidecar so the next click
        # (or the swipe's before side) comes from disk instead of the network.
        history_cache.save_before_path(result.get("request_id") or "", result["path"])

    def _add_session_base_layer(self, info: dict) -> None:
        """Materialize the archived input as a normal base layer: named
        "Session input", placed just BELOW the AI-Edit group (it is the base
        the results sit on), never inside it. Outside the group it is not an
        AI Edit result layer: version visibility syncing, retry exports and
        the base check itself all leave it alone."""
        from qgis.core import Qgis

        try:
            layer = add_geotiff_to_project(
                info.get("path") or "", "", crs_wkt=info.get("crs_wkt") or ""
            )
        except Exception as err:  # noqa: BLE001
            self._track_history_error("session_base_add_failed")
            self._notify(tr("Could not add layer: {msg}").format(msg=err), duration=6)
            return
        if layer is None:
            return
        layer.setName(tr("Session input"))
        self._label_session_base_metadata(layer)
        self._move_session_base_below_group(layer.id())
        try:
            self._canvas.refresh()
        except Exception as err:  # nosec B110
            log_warning(f"canvas refresh after session base add failed: {err}")
        self._notify(tr("Added to map."), level=Qgis.MessageLevel.Success, duration=4)

    @staticmethod
    def _label_session_base_metadata(layer) -> None:
        """The generic add path stamps result metadata ("AI-generated
        imagery"); this layer is the archived SOURCE the session was edited
        on, so correct the description (provenance must not overclaim)."""
        try:
            md = layer.metadata()
            md.setTitle(layer.name())
            md.setAbstract(
                "Snapshot of the source imagery an AI Edit session was edited on"
                " (archived session input)."
            )
            layer.setMetadata(md)
        except Exception as err:  # nosec B110 - cosmetic only
            log_warning(f"session base metadata skipped: {err}")

    def _move_session_base_below_group(self, layer_id: str) -> None:
        """add_geotiff_to_project parks new layers at the top of the AI-Edit
        group; the session input must instead sit right AFTER (so under) the
        group, outside it, in whatever parent the user keeps the group in.
        Clone-then-remove: the registry bridge drops a layer whose last tree
        node disappears (same gotcha as promote_layer_to_own_subgroup)."""
        from qgis.core import QgsProject

        try:
            root = QgsProject.instance().layerTreeRoot()
            node = root.findLayer(layer_id)
            if node is None:
                return
            group = node.parent()
            if group is None or group is root:
                return  # already at root, nothing to move out of
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
        except Exception as err:  # nosec B110 - layer stays usable in-group
            log_warning(f"session base move below AI-Edit failed: {err}")
