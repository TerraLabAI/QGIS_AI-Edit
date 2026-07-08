from __future__ import annotations

import os

from qgis.core import QgsApplication, QgsRectangle

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.i18n import tr
from ...core.logger import log_warning
from ...core.prompts.session_grouping import session_jobs_for
from ...workers.generic_request_task import GenericRequestTask
from ..raster_writer import (
    add_geotiff_to_project,
    extent_and_crs_from_job,
    get_output_dir,
)


class HistoryMixin:
    def _on_template_selected(self, template_id: str, template_name: str = ""):
        """Track template selection for analytics."""
        props = {"template_id": template_id}
        if template_name:
            props["template_name"] = template_name
        telemetry.track(te.TEMPLATE_SELECTED, props)

    # --- Past-generation actions (from the prompt library Recent/Favorites) ---

    def _notify(self, text: str, level=None, duration: int = 5):
        """Show a transient message in the QGIS message bar."""
        from qgis.core import Qgis

        if level is None:
            level = Qgis.MessageLevel.Info
        try:
            self._iface.messageBar().pushMessage("AI Edit", text, level=level, duration=duration)
        except Exception as err:  # nosec B110
            log_warning(f"messageBar push failed: {err}")

    def _hold_history_task(self, task):
        """Keep a hard ref (QgsTask GC'd mid-run aborts QGIS) and release it
        once the task settles."""
        self._history_tasks.append(task)
        task.succeeded.connect(lambda *_: self._release_history_task(task))
        task.failed.connect(lambda *_: self._release_history_task(task))
        QgsApplication.taskManager().addTask(task)

    def _release_history_task(self, task):
        if task in self._history_tasks:
            self._history_tasks.remove(task)

    def _on_history_add_to_map(self, job: dict):
        """Re-add a past generation's output as a georeferenced layer.

        Reconstructs the geotransform from the stored location, downloads the
        output, writes a GeoTIFF off-thread, then adds the layer on the main
        thread."""
        output_url = job.get("output_url")
        if not output_url:
            self._notify(tr("This generation's image is no longer available."), duration=4)
            return
        geo = extent_and_crs_from_job(job)
        if geo is None:
            self._notify(tr("Location data unavailable for this generation."), duration=4)
            return
        extent_dict, crs_wkt = geo
        prompt = job.get("prompt") or ""
        output_dir = get_output_dir()

        def _work(url=output_url, ed=extent_dict, wkt=crs_wkt, p=prompt, d=output_dir):
            from ..raster_writer import write_geotiff

            data = self._client.download_image(url)
            path = write_geotiff(data, ed, wkt, d, prompt=p)
            return {"path": path, "prompt": p, "crs_wkt": wkt}

        task = GenericRequestTask(tr("Adding past generation to the map"), _work)
        task.succeeded.connect(self._on_history_layer_ready)
        task.failed.connect(
            lambda msg, _code: self._notify(
                tr("Could not add to map: {msg}").format(msg=msg), duration=6
            )
        )
        self._notify(tr("Adding to map..."), duration=2)
        self._hold_history_task(task)

    def _on_history_layer_ready(self, result):
        from qgis.core import Qgis

        path = (result or {}).get("path")
        if not path:
            return
        try:
            layer = add_geotiff_to_project(
                path,
                (result or {}).get("prompt", ""),
                crs_wkt=(result or {}).get("crs_wkt", ""),
            )
        except Exception as err:  # noqa: BLE001
            self._notify(tr("Could not add layer: {msg}").format(msg=err), duration=6)
            return
        if layer is not None:
            try:
                self._iface.setActiveLayer(layer)
                self._canvas.setExtent(layer.extent())
                self._canvas.refresh()
            except Exception as err:  # nosec B110
                log_warning(f"focus added history layer failed: {err}")
        self._notify(tr("Added to map."), level=Qgis.MessageLevel.Success, duration=4)

    def _on_history_download(self, job: dict):
        """Save a past generation to a file the user picks. When location data
        exists, write a georeferenced GeoTIFF (so it drops into the right place
        in any QGIS project); otherwise save the raw image. ``download_side``
        on the job selects the input (captured zone) or the output (result)."""
        from qgis.PyQt.QtWidgets import QFileDialog

        from ..raster_writer import _slugify

        side = job.get("download_side") or "output"
        output_url = job.get("input_url") if side == "input" else job.get("output_url")
        if not output_url:
            self._notify(tr("This generation's image is no longer available."), duration=4)
            return
        base_slug = _slugify(job.get("prompt") or "") or "ai_edit"
        slug = f"{base_slug}_{side}"
        geo = extent_and_crs_from_job(job)

        if geo is not None:
            extent_dict, crs_wkt = geo
            prompt = job.get("prompt") or ""
            default_name = os.path.join(get_output_dir(), f"{slug}.tif")
            dest, _filter = QFileDialog.getSaveFileName(
                self._iface.mainWindow(),
                tr("Save georeferenced GeoTIFF"),
                default_name,
                tr("GeoTIFF (*.tif)"),
            )
            if not dest:
                return

            def _work(url=output_url, ed=extent_dict, wkt=crs_wkt, p=prompt, path=dest):
                import shutil
                import tempfile

                from ..raster_writer import write_geotiff

                data = self._client.download_image(url)
                tmp_dir = tempfile.mkdtemp(prefix="ai_edit_dl_")
                try:
                    produced = write_geotiff(data, ed, wkt, tmp_dir, prompt=p)
                    if os.path.exists(path):
                        os.remove(path)
                    shutil.move(produced, path)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return {"path": path}
        else:
            default_name = os.path.join(get_output_dir(), f"{slug}.png")
            dest, _filter = QFileDialog.getSaveFileName(
                self._iface.mainWindow(),
                tr("Save generation image"),
                default_name,
                tr("Images (*.png *.jpg *.webp);;All files (*)"),
            )
            if not dest:
                return

            def _work(url=output_url, path=dest):
                data = self._client.download_image(url)
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
                return {"path": path}

        task = GenericRequestTask(tr("Downloading generation"), _work)
        task.succeeded.connect(self._on_history_download_done)
        task.failed.connect(
            lambda msg, _code: self._notify(
                tr("Download failed: {msg}").format(msg=msg), duration=6
            )
        )
        self._hold_history_task(task)

    def _on_history_download_done(self, result):
        from qgis.core import Qgis

        path = (result or {}).get("path", "")
        self._notify(
            tr("Saved to {path}").format(path=path),
            level=Qgis.MessageLevel.Success,
            duration=5,
        )

    def _on_history_restore(self, job: dict):
        """Reproduce a past generation: restore the zone at its original spot,
        refill the prompt, and reload its reference image(s) so the user can
        re-run on the same location."""
        if self._dock_widget is None:
            return
        geo = extent_and_crs_from_job(job)
        if geo is None:
            self._notify(tr("Location data unavailable for this generation."), duration=4)
            return
        extent_dict, crs_wkt = geo
        if not self._restore_zone(extent_dict, crs_wkt):
            return
        # Re-enter the restored generation's session so new edits continue it
        # and group with its siblings. _restore_zone reset the lineage and
        # minted a fresh id above; keep that fresh one for legacy jobs that
        # carry no session.
        if job.get("session_id"):
            self._session_id = job.get("session_id")
        # Reuse means "replace what I have now", so wipe the current prompt
        # (restore_generation_context overwrites it) and any reference images
        # before loading the reused generation's own references.
        self._dock_widget.clear_references()
        self._dock_widget.restore_generation_context(
            job.get("prompt") or "",
            job.get("template_id"),
            job.get("template_name"),
        )
        self._load_reference_images(job.get("reference_image_urls") or [])
        # Rebuild the iteration session this generation belongs to (Original,
        # V1, V2...) so the next edit continues the chain instead of starting
        # a blank lineage. Thumbnails arrive async; the strip appears then.
        self._restore_session_chain(job)
        self._notify(tr("Generation restored. Adjust and generate again."), duration=4)

    def _session_chain_for(self, job: dict) -> list[dict]:
        """All cached generations from `job`'s iteration session, oldest first.

        Keys on the client-minted session_id, so it groups chained iterations
        AND multi-model siblings the user made in one flow on a zone. Falls back
        to just `job` when it carries no session (legacy rows / older plugins)."""
        if not job.get("request_id"):
            return []
        try:
            jobs = self._dock_widget.get_cached_recent_jobs()
        except Exception:  # nosec B110 - cache is best-effort.
            jobs = []
        return session_jobs_for(job, jobs)

    def _restore_session_chain(self, job: dict) -> None:
        """Download the chain's thumbnails off-thread, then seed the version
        strip with the whole session."""
        chain = self._session_chain_for(job)
        if not chain or self._client is None:
            return
        self._pending_session_rid = job.get("request_id")
        urls = [chain[0].get("input_thumb_url") or chain[0].get("input_url")]
        urls += [j.get("output_thumb_url") or j.get("output_url") for j in chain]

        def _work(items=tuple(urls)):
            blobs = []
            for url in items:
                blob = None
                if url:
                    try:
                        blob = self._client.download_image(url)
                    except Exception as err:  # noqa: BLE001
                        log_warning(f"session thumb download failed: {err}")
                blobs.append(blob)
            return {"blobs": blobs}

        restored_rid = job.get("request_id")
        task = GenericRequestTask(tr("Loading session"), _work)
        task.succeeded.connect(
            lambda payload, c=chain, rid=restored_rid:
            self._on_session_thumbs_loaded(c, rid, payload)
        )
        task.failed.connect(
            lambda msg, _code: log_warning(f"session restore failed: {msg}")
        )
        self._hold_history_task(task)

    def _on_session_thumbs_loaded(
        self, chain: list, selected_rid: str | None, payload: dict
    ) -> None:
        if self._dock_widget is None:
            return
        # Stale arrival: the user restored something else since, or drew a new
        # zone (which invalidates the token). Never overwrite a live session.
        if selected_rid != getattr(self, "_pending_session_rid", None):
            return
        # The user already started generating: the export seeded the lineage.
        if self._versions:
            return
        blobs = payload.get("blobs") or []
        if len(blobs) != len(chain) + 1:
            return
        self._versions = [{"layer_id": None, "request_id": None, "prompt": ""}]
        self._dock_widget.seed_version_strip(self._pixmap_from_blob(blobs[0]))
        for j, blob in zip(chain, blobs[1:]):
            dims = None
            if j.get("output_w") and j.get("output_h"):
                dims = f"{j['output_w']} × {j['output_h']}"
            meta = {
                "definition": j.get("resolution") or "",
                "dimensions": dims,
                "template_name": j.get("template_name"),
                "base_label": None,
            }
            self._versions.append({
                "layer_id": None,
                "request_id": j.get("request_id"),
                "prompt": j.get("prompt") or "",
            })
            self._dock_widget.add_version_thumb(
                self._pixmap_from_blob(blob), j.get("prompt") or "", meta
            )
        index = next(
            (i for i, v in enumerate(self._versions) if v["request_id"] == selected_rid),
            len(self._versions) - 1,
        )
        self._selected_version_index = index
        self._dock_widget.select_version(index)
        self._dock_widget.reveal_version_strip()

    @staticmethod
    def _pixmap_from_blob(blob):
        from qgis.PyQt.QtGui import QPixmap

        pixmap = QPixmap()
        if blob:
            try:
                pixmap.loadFromData(blob)
            except Exception:  # nosec B110 - a broken thumb shows as blank.
                pixmap = QPixmap()
        return pixmap

    def _restore_zone(self, extent_dict: dict, crs_wkt: str) -> bool:
        """Recreate the selection zone from a stored extent + CRS so a past
        generation can be reproduced on the exact same spot. Returns True on
        success."""
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsProject,
        )

        from ...core.errors import AIEditError
        from ..canvas_exporter import validate_zone

        src_crs = QgsCoordinateReferenceSystem()
        src_crs.createFromWkt(crs_wkt)
        if not src_crs.isValid():
            self._notify(tr("Location data unavailable for this generation."), duration=4)
            return False
        rect = QgsRectangle(
            float(extent_dict["xmin"]),
            float(extent_dict["ymin"]),
            float(extent_dict["xmax"]),
            float(extent_dict["ymax"]),
        )
        canvas_crs = self._canvas.mapSettings().destinationCrs()
        if src_crs != canvas_crs:
            try:
                xform = QgsCoordinateTransform(src_crs, canvas_crs, QgsProject.instance())
                rect = xform.transformBoundingBox(rect)
            except Exception as err:  # noqa: BLE001
                log_warning(f"restore zone transform failed: {err}")
                self._notify(tr("Could not place the zone on the current map."), duration=4)
                return False
        try:
            validate_zone(rect, canvas_crs, self._canvas.rotation())
        except AIEditError as err:
            self._notify(err.message, duration=5)
            return False
        except Exception:  # nosec B110 - validation is best-effort here.
            pass

        self._selected_extent = rect
        self._last_completed_request_id = None
        self._reset_version_lineage()
        self._show_selection_rectangle(rect)
        if self._map_tool is not None:
            self._map_tool.set_zone(rect)
        self._activate_selection_tool()
        self._dock_widget.set_zone_selected()
        try:
            self._dock_widget.set_reference_target_extent(QgsRectangle(rect), canvas_crs)
        except Exception:  # nosec B110 - alignment is best-effort.
            pass
        try:
            zoom = QgsRectangle(rect)
            zoom.scale(1.15)
            self._canvas.setExtent(zoom)
            self._canvas.refresh()
        except Exception as err:  # nosec B110
            log_warning(f"zoom to restored zone failed: {err}")
        return True

    def _load_reference_images(self, urls: list):
        """Download a past generation's reference images off-thread, then inject
        them into the dock's reference strip."""
        urls = [u for u in urls if u]
        if not urls or self._client is None:
            return

        def _work(items=tuple(urls)):
            blobs = []
            for url in items:
                try:
                    blobs.append(self._client.download_image(url))
                except Exception as err:  # noqa: BLE001
                    log_warning(f"reference image download failed: {err}")
                    blobs.append(None)
            return {"blobs": blobs}

        task = GenericRequestTask(tr("Loading reference images"), _work)
        task.succeeded.connect(self._on_reference_images_loaded)
        task.failed.connect(
            lambda msg, _code: log_warning(f"reference reload failed: {msg}")
        )
        self._hold_history_task(task)

    def _on_reference_images_loaded(self, result):
        from qgis.PyQt.QtCore import QByteArray
        from qgis.PyQt.QtGui import QImage

        blobs = (result or {}).get("blobs") or []
        items = []
        for i, data in enumerate(blobs):
            if not data:
                continue
            img = QImage()
            if img.loadFromData(QByteArray(data)):
                items.append((img, f"reference_{i + 1}"))
        if items and self._dock_widget is not None:
            self._dock_widget.restore_reference_images(items)
