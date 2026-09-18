from __future__ import annotations

import os
import shutil
import tempfile

from qgis.core import QgsApplication, QgsGeometry, QgsRectangle

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
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
from .session_base_notice import SessionBaseNoticeMixin

# Message-bar notice durations, in seconds.
_NOTIFY_QUICK_S = 2
_NOTIFY_BRIEF_S = 4
_NOTIFY_CONFIRM_S = 5
_NOTIFY_ERROR_S = 6
_NOTIFY_PERSISTENT_S = 8


def _is_strictly_under(path: str, parent: str) -> bool:
    """True when ``path`` sits inside ``parent`` (never ``parent`` itself).
    realpath expands 8.3 short names, which ascii_safe_dir hands out."""
    try:
        child = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(parent))
        return child != root and os.path.commonpath([child, root]) == root  # win-ok: normcase(realpath) on both sides
    except (OSError, ValueError):
        return False


def place_downloaded_geotiff(produced: str, tmp_dir: str, dest: str) -> str:
    """Copy what write_geotiff produced to the user's ``dest``; return the
    final path.

    write_geotiff may answer with a file outside ``tmp_dir`` (an ASCII-safe
    reroute or the fallback folder) or with a rescued .png/.jpg plus its
    .aux.xml georeferencing. A rescued image keeps its own extension, so a PNG
    never hides under a .tif name, and its sidecar travels with it. Each file
    is staged beside ``dest`` and swapped in, so an old file is only replaced
    by a complete one. The produced files, and their folder when it is a temp
    folder of its own, are removed whatever happens.
    """
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
                # A copy, not shutil.move: across volumes move is copy then
                # unlink, and the unlink fails while a scanner reads the file.
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
        """Return the account generation owned by history tasks."""
        return getattr(self, "_history_account_revision_value", 0)

    def _advance_history_account_revision(self) -> int:
        revision = self._history_account_revision() + 1
        self._history_account_revision_value = revision
        return revision

    def _history_revision_is_current(self, revision: int | None) -> bool:
        return revision is None or revision == self._history_account_revision()

    def _cancel_history_tasks(self) -> None:
        """Disconnect and cancel account-bound history tasks on sign-out."""
        for task in list(getattr(self, "_history_tasks", [])):
            drain_task(task, REQUEST_TASK_SIGNALS + ("taskTerminated",))
        self._history_tasks.clear()
        self._conversations_refresh_task = None
        self._version_fetch_active = False

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
        # A Cancel from the QGIS Task Manager emits neither signal above.
        task.taskTerminated.connect(lambda *_: self._on_history_task_terminated(task))
        QgsApplication.taskManager().addTask(task)

    def _release_history_task(self, task):
        if task in self._history_tasks:
            self._history_tasks.remove(task)

    def _on_history_task_terminated(self, task):
        """taskTerminated also follows a plain failure, which ``failed`` has
        already released and reported; only a real cancel is left here."""
        if task not in self._history_tasks:
            return
        self._release_history_task(task)
        try:
            cancelled = task.isCanceled()
        except RuntimeError:
            cancelled = True
        if cancelled:
            self._notify(tr("Cancelled"), duration=3)

    def _track_history_error(self, error_code: str) -> None:
        """Stable, non-localized failure code for a Library history action."""
        telemetry.track(te.PLUGIN_ERROR, {"stage": "history", "error_code": error_code})

    def _on_history_add_to_map(self, job: dict):
        """Re-add a past generation's output as a georeferenced layer.

        Reconstructs the geotransform from the stored location, downloads the
        output, writes a GeoTIFF off-thread, then adds the layer on the main
        thread."""
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
        """Build the off-thread download -> GeoTIFF closure shared by
        Add-to-map and the restored-version materialization. Downloads the
        output, writes it georeferenced, and best-effort rebuilds the swipe's
        before side from the archived input."""
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
            # Rebuild the swipe's true before side from the archived input.
            # Best-effort: the output layer works without it.
            before_path = ""
            if in_url:
                try:
                    before_data = client.download_image(in_url)
                    before_path = write_geotiff(
                        before_data, ed, wkt, d, prompt=p,
                        file_base=before_file_base(path),
                    )
                except Exception:  # nosec B110 - cosmetic sidecar
                    before_path = ""
            return {
                "path": path, "before_path": before_path,
                "prompt": p, "crs_wkt": wkt,
                "request_id": rid, "source": "download",
            }

        return _work

    def _on_history_add_to_map_failed(
        self, msg, _code, account_revision: int | None = None
    ):
        if not self._history_revision_is_current(account_revision):
            return
        self._track_history_error("add_to_map_download_failed")
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
        # Feed the local index: a later version click on this generation
        # re-adds from disk instead of re-downloading.
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
        """Save a past generation to a file the user picks. When location data
        exists, write a georeferenced GeoTIFF (so it drops into the right place
        in any QGIS project); otherwise save the raw image. ``download_side``
        on the job selects the input (captured zone) or the output (result)."""
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
        # Same 40-character cap as the output writer: a long prompt would
        # otherwise push the file name past the Windows path limit.
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
                    # Staged beside the destination (same volume, so the swap
                    # is atomic) instead of unlinking it first: on Windows the
                    # unlink fails outright when the .tif is already loaded as
                    # a layer, and on any platform it would destroy the old
                    # file when the copy then failed.
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
        self, msg, _code, account_revision: int | None = None
    ):
        if not self._history_revision_is_current(account_revision):
            return
        self._track_history_error("download_failed")
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
        # A georeferenced .tif exports as geotiff; the raw-image fallback keeps
        # its own extension (png/jpg/webp).
        fmt = "geotiff" if path.lower().endswith(".tif") else "image"
        self._notify(
            tr("Saved to {path}").format(path=QDir.toNativeSeparators(path)),
            level=Qgis.MessageLevel.Success,
            duration=get_export_dial("flows.history.notify_confirm_s", _NOTIFY_CONFIRM_S),
        )
        telemetry.track(te.HISTORY_EXPORTED, {"format": fmt})

    def _on_history_restore(self, job: dict):
        """Reproduce a past generation: restore the zone at its original spot,
        refill the prompt, and reload its reference image(s) so the user can
        re-run on the same location."""
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
        # Locally-only companion lookup (spec section 7, D2): the polygon
        # never rode the server round-trip, so it is not on `job`. Present
        # only for a generation that ran with a polygon zone on THIS machine
        # and has not aged out of the cache; every other case (old entries,
        # another device, rectangle zones) returns None and restores as a
        # plain bbox exactly as before.
        polygon_data = history_cache.get_zone_polygon(job.get("request_id") or "")
        if not self._restore_zone(extent_dict, crs_wkt, polygon_data):
            self._track_history_error("restore_zone_failed")
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
        self._notify(
            get_export_copy(
                "flows.history.session_reopened",
                tr("Session reopened. Edit the prompt or pick a version, then Generate."),
            ),
            duration=get_export_dial("flows.history.notify_brief_s", _NOTIFY_BRIEF_S),
        )
        telemetry.track(te.HISTORY_RESTORED, {"kind": "restore"})

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
        """Seed the version strip with the whole session, local thumbs first.

        Thumbs come from the local store written at generation time
        (conversation_thumbs); only slots without a local copy are downloaded,
        and those URLs may be stale signed links from the disk cache, so a
        download miss just leaves that tile blank instead of blocking."""
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
        # Stale arrival: the user restored something else since, or drew a new
        # zone (which invalidates the token). Never overwrite a live session.
        if selected_rid != getattr(self, "_pending_session_rid", None):
            return
        # The user already started generating: the export seeded the lineage.
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
                # "Made from" in the version card, when the history row says
                # which generation this one was built on.
                "base_label": self._restored_base_label(j, chain),
            }
            # The job rides along so a click on this tile can download the
            # archived output and put the actual layer on the map.
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
        # One-shot missing-base check for this restore: armed here, where the
        # strip seeding completes (once per restore, never on version clicks),
        # and fired when the resume has fully landed.
        self._arm_base_imagery_check(chain)
        # Hybrid landing: the resumed version's image comes down right away so
        # the canvas is never bare on resume. Sibling versions stay thumbnails
        # until their first click (see _on_base_version_selected).
        if index > 0 and self._version_needs_layer(self._versions[index]):
            self._materialize_version_layer(index)
        else:
            self._fire_base_imagery_check()

    @staticmethod
    def _restored_base_label(job: dict, chain: list) -> str | None:
        """'V{n}' for the version ``job`` was generated from, read off its
        parent id within the restored chain. None when the row does not name
        a parent inside this chain (older rows, a version started from the
        Original): the card then skips the fact rather than guess."""
        parent = job.get("parent_request_id")
        if not parent:
            return None
        for position, sibling in enumerate(chain, start=1):
            if sibling.get("request_id") == parent:
                return tr("V{n}").format(n=position)
        return None

    # --- Restored-version layer materialization ---------------------------
    # A restored session seeds the strip with thumbnails only (layer_id None
    # everywhere), so selecting a version showed nothing on the canvas. These
    # download the archived output on demand and turn the tile into a real
    # layer, after which the normal selection path takes over.

    def _version_needs_layer(self, version: dict) -> bool:
        """True for a generated version whose raster layer is not in the
        project (restored session, or the user deleted the layer)."""
        from qgis.core import QgsProject

        if not version.get("request_id"):
            return False
        layer_id = version.get("layer_id")
        return not (layer_id and QgsProject.instance().mapLayer(layer_id) is not None)

    def _version_job_for(self, version: dict) -> dict | None:
        """The history job carrying this version's download URLs: the one
        stored at restore time, else a cache lookup by request_id (a live
        session whose layer the user deleted)."""
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
        """Add version ``index``'s output to the map as the layer its tile
        stands for: from this machine's disk when its GeoTIFF still exists
        (zero network, works offline), else by downloading the archived
        output. ``prev_index`` is where the selection ring goes back to when
        the fetch fails (None on the resume landing, nothing to revert to)."""
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
        # A generation started meanwhile locks the strip itself; its own
        # unlock path owns the readonly state then.
        if self._dock_widget is not None and (
            self._worker is None or not self._worker.is_active()
        ):
            self._dock_widget.set_version_strip_locked(False)

    def _revert_version_selection(self, prev_index: int | None) -> None:
        """Put the selection ring back where it was before a failed click, so
        a tile never stays selected with nothing behind it."""
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
        # A failed restore landing must not raise the missing-base notice.
        self._disarm_base_imagery_check()
        self._track_history_error("version_layer_download_failed")
        if (code or "").strip().upper() in NETWORK_ERROR_CODES:
            # Offline is the one failure the user can fix themselves, so it
            # gets a plain-language explanation in the dock (where they are
            # looking), not just the transient bar. Shipped copy on purpose:
            # no server can serve a sentence to a machine that is offline.
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
        # Stale arrival: the user drew a new zone or restored something else,
        # which minted a new session id. The file stays on disk but must not
        # enter the new lineage.
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
        # A browsed layer is a preview until the user commits to it (generates
        # from it, vectorizes it): the next version click replaces it instead
        # of piling layers up. The name pins what we created, so a user rename
        # counts as adoption and the layer is never auto-removed after that.
        version["preview"] = True
        version["preview_name"] = layer.name()
        # Feed the local index so the NEXT click on this version (even after a
        # preview drop, even offline) re-adds from disk instead of the network.
        history_cache.save_output_paths(
            (result or {}).get("request_id") or version.get("request_id") or "",
            path,
            (result or {}).get("before_path") or "",
        )
        # A browsed version is on the map now: the zone frame stays on it,
        # same as a fresh generation. A restored session holds only the
        # rectangle, so that is what gets framed here.
        self._redraw_zone_outline()
        # A generation started mid-download owns the canvas and the selection;
        # leave the layer in place without hijacking either (and drop the
        # missing-base check: the user has moved on to generating).
        if self._worker is not None and self._worker.is_active():
            self._disarm_base_imagery_check()
            return
        self._on_base_version_selected(index)
        # The version browsed just before this one was only being looked at:
        # replace it on the map rather than stacking (preview rule).
        if prev_index is not None and prev_index != index and 0 <= prev_index < len(self._versions):
            self._drop_preview_layer(self._versions[prev_index])
        # Restore landing complete (visibility already synced above): consume
        # the once-per-restore missing-base check. No-op on plain version
        # clicks, whose token was consumed when the restore landed.
        self._fire_base_imagery_check()
        telemetry.track(te.HISTORY_RESTORED, {
            "kind": "version_layer",
            "source": (result or {}).get("source") or "download",
        })

    def _drop_preview_layer(self, version: dict) -> None:
        """Remove a browsed preview layer the user never committed to. Layers
        from live generations (no preview flag), promoted versions, and layers
        the user renamed or moved out of the AI-Edit group are never touched.
        The GeoTIFF stays on disk and indexed, so re-selecting the version
        brings the layer back instantly."""
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
        except Exception as err:  # nosec B110 - a stuck preview is cosmetic.
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
            except Exception:  # nosec B110 - a broken thumb shows as blank.
                pixmap = QPixmap()
        return pixmap

    def _rebuild_history_polygon(self, polygon_data: dict, canvas_crs) -> QgsGeometry | None:
        """Rebuild a restored zone's polygon (spec section 7) from the local
        zone-polygon cache into the CURRENT canvas CRS, mirroring the bbox
        transform in ``_restore_zone``. Returns None on any failure so the
        caller always has a safe bbox-only fallback, never a blocked
        restore."""
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
        # A CRS transform can nudge a marginal geometry invalid (or, at the
        # antimeridian, split it): reuse the same repair the fresh-draw path
        # runs after makeValid (spec section 9).
        return _repair_polygon(geom)

    def _restore_zone(self, extent_dict: dict, crs_wkt: str, polygon_data: dict | None = None) -> bool:
        """Recreate the selection zone from a stored extent + CRS so a past
        generation can be reproduced on the exact same spot. ``polygon_data``
        (spec section 7) is the local zone-polygon cache entry for this job's
        request id, or None for every entry that never had one (old history
        rows, another device, a plain rectangle zone) - the zone then
        restores as a plain bbox exactly as before. Returns True on success."""
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
        except Exception:  # nosec B110 - validation is best-effort here.
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
