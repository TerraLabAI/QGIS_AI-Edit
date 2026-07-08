from __future__ import annotations

import os
import shutil
import tempfile

from qgis.core import QgsApplication, QgsGeometry, QgsRectangle

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.log_scrub import scrub_file_paths, scrub_urls
from ...core.logger import log_warning
from ...core.number_format import format_count
from ...core.prompts import conversation_thumbs, history_cache
from ...core.prompts.session_grouping import session_jobs_for
from ...workers.generic_request_task import GenericRequestTask
from ..raster_writer import (
    add_geotiff_to_project,
    extent_and_crs_from_job,
    get_output_dir,
)
from .lifecycle import REQUEST_TASK_SIGNALS, drain_task
from .session_base_notice import _NOTIFY_BRIEF_S, _NOTIFY_ERROR_S, _NOTIFY_QUICK_S, SessionBaseNoticeMixin



_NOTIFY_CONFIRM_S = 5
_NOTIFY_PERSISTENT_S = 8


def _is_strictly_under(path: str, parent: str) -> bool:


    try:
        child = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(parent))
        return child != root and os.path.commonpath([child, root]) == root
    except (OSError, ValueError):
        return False


def place_downloaded_geotiff(produced: str, tmp_dir: str, dest: str) -> str:











    from ...core.output_paths import remove_with_retry, replace_staged_file

    ext = os.path.splitext(produced)[1].lower()
    if ext != ".tif":
        dest = os.path.splitext(dest)[0] + ext
    moves = [(produced, dest)]
    if os.path.exists(produced + ".aux.xml"):
        moves.append((produced + ".aux.xml", dest + ".aux.xml"))
    try:
        for source, target in moves:
            staged = target + ".part"
            try:


                shutil.copyfile(source, staged)
            except OSError:
                remove_with_retry(staged)
                raise
            replace_staged_file(staged, target)
    finally:
        for source, _target in moves:
            remove_with_retry(source)
        produced_dir = os.path.dirname(produced)
        if (
            not _is_strictly_under(produced_dir, tmp_dir)
            and os.path.normcase(os.path.realpath(produced_dir))
            != os.path.normcase(os.path.realpath(tmp_dir))
            and _is_strictly_under(produced_dir, tempfile.gettempdir())
        ):
            shutil.rmtree(produced_dir, ignore_errors=True)
    return dest


class HistoryMixin(SessionBaseNoticeMixin):
    def _history_account_revision(self) -> int:

        return getattr(self, "_history_account_revision_value", 0)

    def _advance_history_account_revision(self) -> int:
        revision = self._history_account_revision() + 1
        self._history_account_revision_value = revision
        return revision

    def _history_revision_is_current(self, revision: int | None) -> bool:
        return revision is None or revision == self._history_account_revision()

    def _cancel_history_tasks(self) -> None:

        for task in list(getattr(self, "_history_tasks", [])):
            drain_task(task, REQUEST_TASK_SIGNALS + ("taskTerminated",))
        self._history_tasks.clear()
        self._conversations_refresh_task = None
        self._version_fetch_active = False

    def _on_template_selected(self, template_id: str, template_name: str = ""):

        props = {"template_id": template_id}
        if template_name:
            props["template_name"] = template_name
        telemetry.track(te.TEMPLATE_SELECTED, props)



    def _notify(self, text: str, level=None, duration: int = 5):

        from qgis.core import Qgis

        if level is None:
            level = Qgis.MessageLevel.Info
        try:
            self._iface.messageBar().pushMessage("AI Edit", text, level=level, duration=duration)
        except Exception as err:  # nosec B110
            log_warning(f"messageBar push failed: {err}")

    def _hold_history_task(self, task):


        self._history_tasks.append(task)
        task.succeeded.connect(lambda *_: self._release_history_task(task))
        task.failed.connect(lambda *_: self._release_history_task(task))

        task.taskTerminated.connect(lambda *_: self._on_history_task_terminated(task))
        QgsApplication.taskManager().addTask(task)

    def _release_history_task(self, task):
        if task in self._history_tasks:
            self._history_tasks.remove(task)

    def _on_history_task_terminated(self, task):


        if task not in self._history_tasks:
            return
        self._release_history_task(task)
        try:
            cancelled = task.isCanceled()
        except RuntimeError:
            cancelled = True
        if cancelled:
            self._notify(tr("Cancelled"), duration=3)

    def _track_history_error(self, error_code: str, message: str | None = "", failure_code: str | None = "") -> None:




        props = {"stage": "history", "error_code": error_code}
        detail = " ".join(part for part in ((failure_code or "").strip(), (message or "").strip()) if part)
        if detail:
            props["error_message"] = scrub_file_paths(scrub_urls(detail))[:200]
        telemetry.track(te.PLUGIN_ERROR, props)

    def _on_history_add_to_map(self, job: dict):





        output_url = job.get("output_url")
        if not output_url:
            self._notify(
                get_export_copy(
                    "flows.history.image_unavailable",
                    tr("This generation's image is no longer available."),
                ),
                duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
            )
            return
        geo = extent_and_crs_from_job(job)
        if geo is None:
            self._notify(
                get_export_copy(
                    "flows.history.location_unavailable",
                    tr("Location data unavailable for this generation."),
                ),
                duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
            )
            return
        account_revision = self._history_account_revision()
        task = GenericRequestTask(
            get_export_copy(
                "flows.history.adding_to_map_task", tr("Adding past generation to the map")
            ),
            self._make_history_geotiff_work(job, geo),
        )
        task.succeeded.connect(
            lambda result, rev=account_revision: self._on_history_layer_ready(result, rev)
        )
        task.failed.connect(
            lambda msg, code, rev=account_revision:
            self._on_history_add_to_map_failed(msg, code, rev)
        )
        self._notify(
            get_export_copy("flows.history.adding_to_map_status", tr("Adding to map...")),
            duration=get_export_dial("flows.history.notify_quick_s", _NOTIFY_QUICK_S),
        )
        self._hold_history_task(task)

    def _make_history_geotiff_work(self, job: dict, geo: tuple):




        extent_dict, crs_wkt = geo
        output_url = job.get("output_url")
        input_url = job.get("input_url")
        prompt = job.get("prompt") or ""
        request_id = job.get("request_id") or ""
        output_dir = get_output_dir()
        client = self._client

        def _work(
            url=output_url, in_url=input_url, ed=extent_dict, wkt=crs_wkt,
            p=prompt, d=output_dir, rid=request_id,
        ):
            from ..raster_writer import before_file_base, write_geotiff

            data = client.download_image(url)
            path = write_geotiff(data, ed, wkt, d, prompt=p)


            before_path = ""
            if in_url:
                try:
                    before_data = client.download_image(in_url)
                    before_path = write_geotiff(
                        before_data, ed, wkt, d, prompt=p,
                        file_base=before_file_base(path),
                    )
                except Exception:  # nosec B110
                    before_path = ""
            return {
                "path": path, "before_path": before_path,
                "prompt": p, "crs_wkt": wkt,
                "request_id": rid, "source": "download",
            }

        return _work

    def _on_history_add_to_map_failed(
        self, msg, code, account_revision: int | None = None
    ):
        if not self._history_revision_is_current(account_revision):
            return
        self._track_history_error("add_to_map_download_failed", msg, code)
        self._notify(
            tr("Could not add to map: {msg}").format(msg=msg),
            duration=get_export_dial("flows.history.notify_error_s", _NOTIFY_ERROR_S),
        )

    def _on_history_layer_ready(self, result, account_revision: int | None = None):
        if not self._history_revision_is_current(account_revision):
            return
        from qgis.core import Qgis

        path = (result or {}).get("path")
        if not path:
            return
        try:
            layer = add_geotiff_to_project(
                path,
                (result or {}).get("prompt", ""),
                crs_wkt=(result or {}).get("crs_wkt", ""),
                before_path=(result or {}).get("before_path", ""),
            )
        except Exception as err:  # noqa: BLE001
            self._track_history_error("add_to_map_layer_failed")
            self._notify(
                tr("Could not add layer: {msg}").format(msg=err),
                duration=get_export_dial("flows.history.notify_error_s", _NOTIFY_ERROR_S),
            )
            return
        if layer is not None:
            try:
                self._iface.setActiveLayer(layer)
                self._canvas.setExtent(layer.extent())
                self._canvas.refresh()
            except Exception as err:  # nosec B110
                log_warning(f"focus added history layer failed: {err}")


        history_cache.save_output_paths(
            (result or {}).get("request_id") or "",
            path,
            (result or {}).get("before_path") or "",
        )
        self._notify(
            get_export_copy(
                "flows.history.added_to_map_as", tr("Added to your map as {name}")
            ).replace("{name}", layer.name() if layer is not None else ""),
            level=Qgis.MessageLevel.Success,
            duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
        )
        telemetry.track(te.HISTORY_RESTORED, {"kind": "add_to_map"})

    def _on_history_download(self, job: dict):




        from qgis.PyQt.QtWidgets import QFileDialog

        from ..raster_writer import _slugify

        side = job.get("download_side") or "output"
        output_url = job.get("input_url") if side == "input" else job.get("output_url")
        if not output_url:
            self._notify(
                get_export_copy(
                    "flows.history.image_unavailable",
                    tr("This generation's image is no longer available."),
                ),
                duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
            )
            return


        base_slug = _slugify(job.get("prompt") or "")[:40] or "ai_edit"
        slug = f"{base_slug}_{side}"
        geo = extent_and_crs_from_job(job)
        client = self._client

        if geo is not None:
            extent_dict, crs_wkt = geo
            prompt = job.get("prompt") or ""
            default_name = os.path.join(get_output_dir(), f"{slug}.tif")
            dest, _filter = QFileDialog.getSaveFileName(
                self._iface.mainWindow(),
                get_export_copy("flows.history.save_geotiff_title", tr("Save georeferenced GeoTIFF")),
                default_name,
                tr("GeoTIFF (*.tif)"),
            )
            if not dest:
                return

            def _work(url=output_url, ed=extent_dict, wkt=crs_wkt, p=prompt, path=dest):
                from ..raster_writer import write_geotiff

                data = client.download_image(url)
                tmp_dir = tempfile.mkdtemp(prefix="ai_edit_dl_")
                try:
                    produced = write_geotiff(data, ed, wkt, tmp_dir, prompt=p)





                    final = place_downloaded_geotiff(produced, tmp_dir, path)
                finally:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                return {"path": final}
        else:
            default_name = os.path.join(get_output_dir(), f"{slug}.png")
            dest, _filter = QFileDialog.getSaveFileName(
                self._iface.mainWindow(),
                get_export_copy("flows.history.save_image_title", tr("Save generation image")),
                default_name,
                tr("Images (*.png *.jpg *.webp);;All files (*)"),
            )
            if not dest:
                return

            def _work(url=output_url, path=dest):
                from ..raster_writer import replace_staged_file

                data = client.download_image(url)
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                replace_staged_file(tmp, path)
                return {"path": path}

        account_revision = self._history_account_revision()
        task = GenericRequestTask(
            get_export_copy("flows.history.downloading_generation_task", tr("Downloading generation")),
            _work,
        )
        task.succeeded.connect(
            lambda result, rev=account_revision: self._on_history_download_done(result, rev)
        )
        task.failed.connect(
            lambda msg, code, rev=account_revision:
            self._on_history_download_failed(msg, code, rev)
        )
        self._hold_history_task(task)

    def _on_history_download_failed(
        self, msg, code, account_revision: int | None = None
    ):
        if not self._history_revision_is_current(account_revision):
            return
        self._track_history_error("download_failed", msg, code)
        self._notify(
            tr("Download failed: {msg}").format(msg=msg),
            duration=get_export_dial("flows.history.notify_error_s", _NOTIFY_ERROR_S),
        )

    def _on_history_download_done(self, result, account_revision: int | None = None):
        if not self._history_revision_is_current(account_revision):
            return
        from qgis.core import Qgis
        from qgis.PyQt.QtCore import QDir

        path = (result or {}).get("path", "")


        fmt = "geotiff" if path.lower().endswith(".tif") else "image"
        self._notify(
            tr("Saved to {path}").format(path=QDir.toNativeSeparators(path)),
            level=Qgis.MessageLevel.Success,
            duration=get_export_dial("flows.history.notify_confirm_s", _NOTIFY_CONFIRM_S),
        )
        telemetry.track(te.HISTORY_EXPORTED, {"format": fmt})

    def _on_history_restore(self, job: dict):



        if self._dock_widget is None:
            return
        geo = extent_and_crs_from_job(job)
        if geo is None:
            self._track_history_error("restore_no_location")
            self._notify(
                get_export_copy(
                    "flows.history.location_unavailable",
                    tr("Location data unavailable for this generation."),
                ),
                duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
            )
            return
        extent_dict, crs_wkt = geo






        polygon_data = history_cache.get_zone_polygon(job.get("request_id") or "")
        if not self._restore_zone(extent_dict, crs_wkt, polygon_data):
            self._track_history_error("restore_zone_failed")
            return




        if job.get("session_id"):
            self._session_id = job.get("session_id")



        self._dock_widget.clear_references()
        self._dock_widget.restore_generation_context(
            job.get("prompt") or "",
            job.get("template_id"),
            job.get("template_name"),
        )
        self._load_reference_images(job.get("reference_image_urls") or [])



        self._restore_session_chain(job)
        self._notify(
            get_export_copy(
                "flows.history.session_reopened",
                tr("Session reopened. Edit the prompt or pick a version, then Generate."),
            ),
            duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
        )
        telemetry.track(te.HISTORY_RESTORED, {"kind": "restore"})

    def _session_chain_for(self, job: dict) -> list[dict]:





        if not job.get("request_id"):
            return []
        try:
            jobs = self._dock_widget.get_cached_recent_jobs()
        except Exception:  # nosec B110
            jobs = []
        return session_jobs_for(job, jobs)

    def _restore_session_chain(self, job: dict) -> None:






        chain = self._session_chain_for(job)
        if not chain or self._client is None:
            return
        self._pending_session_rid = job.get("request_id")
        session_id = job.get("session_id") or ""
        pixmaps = [
            conversation_thumbs.load_thumb(f"in-{session_id}") if session_id else None
        ]
        pixmaps += [
            conversation_thumbs.load_thumb(j.get("request_id") or "") for j in chain
        ]
        urls = [chain[0].get("input_thumb_url") or chain[0].get("input_url")]
        urls += [j.get("output_thumb_url") or j.get("output_url") for j in chain]
        missing = tuple(
            (i, url)
            for i, (pix, url) in enumerate(zip(pixmaps, urls))
            if pix is None and url
        )
        restored_rid = job.get("request_id")
        if not missing:
            self._seed_restored_strip(chain, restored_rid, pixmaps)
            return

        client = self._client

        def _work(items=missing, c=client):
            blobs = {}
            for i, url in items:
                try:
                    blobs[i] = c.download_image(url)
                except Exception as err:  # noqa: BLE001
                    log_warning(f"session thumb download failed: {err}")
            return {"blobs": blobs}

        account_revision = self._history_account_revision()
        task = GenericRequestTask(
            get_export_copy("flows.history.loading_session_task", tr("Loading session")), _work
        )
        task.succeeded.connect(
            lambda payload, c=chain, rid=restored_rid, base=tuple(pixmaps),
            rev=account_revision:
            self._on_session_thumbs_loaded(c, rid, base, payload, rev)
        )
        task.failed.connect(
            lambda msg, _code: log_warning(f"session restore failed: {msg}")
        )
        self._hold_history_task(task)

    def _on_session_thumbs_loaded(
        self, chain: list, selected_rid: str | None, base: tuple, payload: dict,
        account_revision: int | None = None,
    ) -> None:
        if not self._history_revision_is_current(account_revision):
            return
        pixmaps = list(base)
        for i, blob in ((payload or {}).get("blobs") or {}).items():
            if 0 <= i < len(pixmaps) and pixmaps[i] is None:
                pixmaps[i] = self._pixmap_from_blob(blob)
        self._seed_restored_strip(chain, selected_rid, pixmaps)

    def _seed_restored_strip(
        self, chain: list, selected_rid: str | None, pixmaps: list
    ) -> None:
        if self._dock_widget is None:
            return


        if selected_rid != getattr(self, "_pending_session_rid", None):
            return

        if self._versions:
            return
        if len(pixmaps) != len(chain) + 1:
            return
        blank = self._pixmap_from_blob(None)
        self._versions = [{"layer_id": None, "request_id": None, "prompt": ""}]
        self._dock_widget.seed_version_strip(pixmaps[0] or blank)
        for j, pix in zip(chain, pixmaps[1:]):
            dims = None
            if j.get("output_w") and j.get("output_h"):
                dims = f"{format_count(j['output_w'])} × {format_count(j['output_h'])} px"
            meta = {
                "definition": j.get("resolution") or "",
                "dimensions": dims,
                "template_name": j.get("template_name"),


                "base_label": self._restored_base_label(j, chain),
            }


            self._versions.append({
                "layer_id": None,
                "request_id": j.get("request_id"),
                "prompt": j.get("prompt") or "",
                "job": j,
            })
            self._dock_widget.add_version_thumb(
                pix or blank, j.get("prompt") or "", meta
            )
        index = next(
            (i for i, v in enumerate(self._versions) if v["request_id"] == selected_rid),
            len(self._versions) - 1,
        )
        self._selected_version_index = index
        self._dock_widget.select_version(index)
        self._dock_widget.reveal_version_strip()



        self._arm_base_imagery_check(chain)



        if index > 0 and self._version_needs_layer(self._versions[index]):
            self._materialize_version_layer(index)
        else:
            self._fire_base_imagery_check()

    @staticmethod
    def _restored_base_label(job: dict, chain: list) -> str | None:




        parent = job.get("parent_request_id")
        if not parent:
            return None
        for position, sibling in enumerate(chain, start=1):
            if sibling.get("request_id") == parent:
                return tr("V{n}").format(n=position)
        return None







    def _version_needs_layer(self, version: dict) -> bool:


        from qgis.core import QgsProject

        if not version.get("request_id"):
            return False
        layer_id = version.get("layer_id")
        return not (layer_id and QgsProject.instance().mapLayer(layer_id) is not None)

    def _version_job_for(self, version: dict) -> dict | None:



        job = version.get("job")
        if isinstance(job, dict) and job.get("output_url"):
            return job
        rid = version.get("request_id")
        if not rid or self._dock_widget is None:
            return None
        for cached in self._dock_widget.get_cached_recent_jobs():
            if isinstance(cached, dict) and cached.get("request_id") == rid:
                return cached
        return None

    def _materialize_version_layer(self, index: int, prev_index: int | None = None) -> None:





        if getattr(self, "_version_fetch_active", False):
            return
        if not (0 <= index < len(self._versions)):
            return
        version = self._versions[index]
        local = history_cache.get_output_paths(version.get("request_id") or "")
        if local and os.path.isfile(local.get("path") or ""):
            before = local.get("before_path") or ""
            self._on_version_layer_ready(index, self._session_id, {
                "path": local["path"],
                "before_path": before if os.path.isfile(before) else "",
                "prompt": version.get("prompt") or "",
                "crs_wkt": "",
                "request_id": version.get("request_id") or "",
                "source": "local",
            }, prev_index=prev_index)
            return
        if self._client is None:
            self._disarm_base_imagery_check()
            return
        job = self._version_job_for(version)
        if not job:
            self._track_history_error("version_layer_no_url")
            self._notify(
                get_export_copy(
                    "flows.history.image_unavailable",
                    tr("This generation's image is no longer available."),
                ),
                duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
            )
            self._revert_version_selection(prev_index)
            self._disarm_base_imagery_check()
            return
        geo = extent_and_crs_from_job(job)
        if geo is None:
            self._track_history_error("version_layer_no_location")
            self._notify(
                get_export_copy(
                    "flows.history.location_unavailable",
                    tr("Location data unavailable for this generation."),
                ),
                duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
            )
            self._revert_version_selection(prev_index)
            self._disarm_base_imagery_check()
            return
        self._version_fetch_active = True
        if self._dock_widget is not None:
            self._dock_widget.set_version_strip_locked(True)
        session_token = self._session_id
        account_revision = self._history_account_revision()
        task = GenericRequestTask(
            get_export_copy(
                "flows.history.adding_to_map_task", tr("Adding past generation to the map")
            ),
            self._make_history_geotiff_work(job, geo),
        )
        task.succeeded.connect(
            lambda result, i=index, tok=session_token, p=prev_index, rev=account_revision:
            self._on_version_layer_ready(i, tok, result, prev_index=p, account_revision=rev)
        )
        task.failed.connect(
            lambda msg, code, p=prev_index, rev=account_revision:
            self._on_version_layer_failed(msg, code, p, rev)
        )
        self._notify(
            get_export_copy("flows.history.adding_to_map_status", tr("Adding to map...")),
            duration=get_export_dial("flows.history.notify_quick_s", _NOTIFY_QUICK_S),
        )
        self._hold_history_task(task)

    def _end_version_fetch(self) -> None:
        self._version_fetch_active = False


        if self._dock_widget is not None and (
            self._worker is None or not self._worker.is_active()
        ):
            self._dock_widget.set_version_strip_locked(False)

    def _revert_version_selection(self, prev_index: int | None) -> None:


        if prev_index is None or self._dock_widget is None:
            return
        if 0 <= prev_index < len(self._versions):
            self._selected_version_index = prev_index
            self._dock_widget.select_version(prev_index)

    def _on_version_layer_failed(
        self, msg: str, code: str, prev_index: int | None,
        account_revision: int | None = None,
    ) -> None:
        if not self._history_revision_is_current(account_revision):
            return
        from ...core.errors import NETWORK_ERROR_CODES

        self._end_version_fetch()

        self._disarm_base_imagery_check()
        self._track_history_error("version_layer_download_failed", msg, code)
        if (code or "").strip().upper() in NETWORK_ERROR_CODES:




            offline = tr(
                "No internet connection. This version was made on another "
                "device or cleaned from this disk, so its image must be "
                "downloaded. Reconnect and click the version again."
            )
            if self._dock_widget is not None:
                self._dock_widget.set_status(offline, is_error=True)
            self._notify(
                offline,
                self._warning_level(),
                duration=get_export_dial("flows.history.notify_persistent_s", _NOTIFY_PERSISTENT_S),
            )
        else:
            self._notify(
                tr("Could not add to map: {msg}").format(msg=msg),
                duration=get_export_dial("flows.history.notify_error_s", _NOTIFY_ERROR_S),
            )
        self._revert_version_selection(prev_index)

    def _on_version_layer_ready(
        self, index: int, session_token: str | None, result: dict,
        prev_index: int | None = None,
        account_revision: int | None = None,
    ) -> None:
        if not self._history_revision_is_current(account_revision):
            return
        self._end_version_fetch()



        if session_token != getattr(self, "_session_id", None):
            return
        if not (0 <= index < len(self._versions)):
            return
        path = (result or {}).get("path")
        if not path:
            self._disarm_base_imagery_check()
            return
        try:
            layer = add_geotiff_to_project(
                path,
                (result or {}).get("prompt", ""),
                crs_wkt=(result or {}).get("crs_wkt", ""),
                before_path=(result or {}).get("before_path", ""),
            )
        except Exception as err:  # noqa: BLE001
            self._track_history_error("version_layer_add_failed")
            self._notify(
                tr("Could not add layer: {msg}").format(msg=err),
                duration=get_export_dial("flows.history.notify_error_s", _NOTIFY_ERROR_S),
            )
            self._disarm_base_imagery_check()
            return
        if layer is None:
            self._disarm_base_imagery_check()
            return
        version = self._versions[index]
        version["layer_id"] = layer.id()




        version["preview"] = True
        version["preview_name"] = layer.name()


        history_cache.save_output_paths(
            (result or {}).get("request_id") or version.get("request_id") or "",
            path,
            (result or {}).get("before_path") or "",
        )



        self._redraw_zone_outline()



        if self._worker is not None and self._worker.is_active():
            self._disarm_base_imagery_check()
            return
        self._on_base_version_selected(index)


        if prev_index is not None and prev_index != index and 0 <= prev_index < len(self._versions):
            self._drop_preview_layer(self._versions[prev_index])



        self._fire_base_imagery_check()
        telemetry.track(te.HISTORY_RESTORED, {
            "kind": "version_layer",
            "source": (result or {}).get("source") or "download",
        })

    def _drop_preview_layer(self, version: dict) -> None:





        from qgis.core import QgsProject

        from ..layer_groups import collect_ai_edit_layer_ids

        if not version.get("preview") or version.get("promoted"):
            return
        layer_id = version.get("layer_id")
        if not layer_id:
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            version["layer_id"] = None
            return
        if layer.name() != version.get("preview_name"):
            return
        if layer_id not in collect_ai_edit_layer_ids():
            return
        try:
            QgsProject.instance().removeMapLayer(layer_id)
        except Exception as err:  # nosec B110
            log_warning(f"preview layer drop failed: {err}")
            return
        version["layer_id"] = None

    @staticmethod
    def _pixmap_from_blob(blob):
        from qgis.PyQt.QtGui import QPixmap

        pixmap = QPixmap()
        if blob:
            try:
                pixmap.loadFromData(blob)
            except Exception:  # nosec B110
                pixmap = QPixmap()
        return pixmap

    def _rebuild_history_polygon(self, polygon_data: dict, canvas_crs) -> QgsGeometry | None:





        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsProject,
        )

        from ..tools.polygon_selection_tool import _repair_polygon

        wkt = (polygon_data or {}).get("wkt") or ""
        authid = (polygon_data or {}).get("crs_authid") or ""
        if not wkt or not authid:
            return None
        geom = QgsGeometry.fromWkt(wkt)
        if geom is None or geom.isEmpty():
            return None
        src_crs = QgsCoordinateReferenceSystem(authid)
        if not src_crs.isValid():
            return None
        if src_crs != canvas_crs:
            try:
                xform = QgsCoordinateTransform(src_crs, canvas_crs, QgsProject.instance())
                geom.transform(xform)
            except Exception as err:  # noqa: BLE001
                log_warning(f"restore zone polygon transform failed: {err}")
                return None



        return _repair_polygon(geom)

    def _restore_zone(self, extent_dict: dict, crs_wkt: str, polygon_data: dict | None = None) -> bool:






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
            self._notify(
                get_export_copy(
                    "flows.history.location_unavailable",
                    tr("Location data unavailable for this generation."),
                ),
                duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
            )
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
                self._notify(
                    get_export_copy(
                        "flows.history.zone_placement_failed",
                        tr("Could not place the zone on the current map."),
                    ),
                    duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
                )
                return False
        try:
            validate_zone(rect, canvas_crs, self._canvas.rotation())
        except AIEditError as err:
            self._notify(
                err.message,
                duration=get_export_dial("flows.history.notify_confirm_s", _NOTIFY_CONFIRM_S),
            )
            return False
        except Exception:  # nosec B110
            pass

        polygon = self._rebuild_history_polygon(polygon_data, canvas_crs) if polygon_data else None
        self._selected_extent = rect
        self._selected_polygon = polygon
        self._last_completed_request_id = None
        self._reset_version_lineage()
        self._show_selection_rectangle(rect, polygon)
        if self._map_tool is not None:
            self._map_tool.set_zone(rect)
        self._activate_selection_tool()
        self._dock_widget.set_zone_selected()
        try:
            self._dock_widget.set_reference_target_extent(QgsRectangle(rect), canvas_crs)
        except Exception:  # nosec B110
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


        urls = [u for u in urls if u]
        if not urls or self._client is None:
            return

        client = self._client

        def _work(items=tuple(urls), c=client):
            blobs = []
            for url in items:
                try:
                    blobs.append(c.download_image(url))
                except Exception as err:  # noqa: BLE001
                    log_warning(f"reference image download failed: {err}")
                    blobs.append(None)
            return {"blobs": blobs}

        account_revision = self._history_account_revision()
        task = GenericRequestTask(
            get_export_copy("flows.history.loading_reference_images_task", tr("Loading reference images")),
            _work,
        )
        task.succeeded.connect(
            lambda result, rev=account_revision: self._on_reference_images_loaded(result, rev)
        )
        task.failed.connect(
            lambda msg, _code: log_warning(f"reference reload failed: {msg}")
        )
        self._hold_history_task(task)

    def _on_reference_images_loaded(self, result, account_revision: int | None = None):
        if not self._history_revision_is_current(account_revision):
            return
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
