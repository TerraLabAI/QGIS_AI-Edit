"""Image loading for the generation detail dialog (slider tiers, references)."""
from __future__ import annotations

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtWidgets import QFileDialog

from ....core.i18n import tr
from ....core.logger import log_warning
from ....workers.generic_request_task import GenericRequestTask
from .widgets import _ImageLightbox


def _with_preview_size(url: str) -> str:
    """Append the size=preview query so the demo route serves the 2048px variant
    (falls back server-side to the base demo when no preview is seeded)."""
    if not url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}size=preview"


class ImageLoadMixin:
    """Loads slider images and reference thumbnails through the shared loader."""

    def _start_image_loads(self) -> None:
        if self._slider is not None:
            if self._is_generation:
                self._load_generation_images()
            else:
                self._load_template_images()
        if self._is_generation and getattr(self, "_ref_labels", None):
            self._load_reference_thumbs()

    def _load_generation_images(self) -> None:
        if self._demo_loader is None or not self._thumb_key:
            return
        self._demo_loader.loaded.connect(self._on_image_loaded)
        # Thumb first (instant - the card already cached it), then the 2048px
        # preview upgrades it (sharp on any screen, fast on slow links). Each
        # tier falls back to the full URL on rows that predate it. The full
        # original stays reserved for the GeoTIFF download.
        thumb_before = self._job.get("input_thumb_url") or self._job.get("input_url")
        thumb_after = self._job.get("output_thumb_url") or self._job.get("output_url")
        full_before = self._job.get("input_preview_url") or self._job.get("input_url")
        full_after = self._job.get("output_preview_url") or self._job.get("output_url")
        if thumb_before:
            self._demo_loader.request(self._thumb_key, "before", thumb_before)
        if thumb_after:
            self._demo_loader.request(self._thumb_key, "after", thumb_after)
        if full_before:
            self._demo_loader.request(self._full_key, "before", full_before)
        if full_after:
            self._demo_loader.request(self._full_key, "after", full_after)

    def _load_template_images(self) -> None:
        if self._demo_loader is None or self._absolute_url is None:
            return
        self._demo_loader.loaded.connect(self._on_image_loaded)
        ub = self._preset.get("demo_url_before")
        ua = self._preset.get("demo_url_after")
        # Base demo (640px, often already grid-cached) for an instant first
        # paint, then the 2048px preview upgrades it under the _preview key.
        if ub:
            self._demo_loader.request(self._thumb_key, "before", self._absolute_url(ub))
            self._demo_loader.request(
                self._full_key, "before", self._absolute_url(_with_preview_size(ub))
            )
        if ua:
            self._demo_loader.request(self._thumb_key, "after", self._absolute_url(ua))
            self._demo_loader.request(
                self._full_key, "after", self._absolute_url(_with_preview_size(ua))
            )

    def _load_reference_thumbs(self) -> None:
        if self._demo_loader is None:
            return
        rid = str(self._job.get("request_id") or "")
        urls = self._job.get("reference_image_urls") or []
        if not rid or not urls:
            return
        self._demo_loader.loaded.connect(self._on_ref_loaded)
        for i, url in enumerate(urls):
            if url:
                self._demo_loader.request(rid, f"ref{i}", url)

    def _on_image_loaded(self, key: str, which: str, pixmap) -> None:
        if self._slider is None or which not in ("before", "after"):
            return
        is_full = key == self._full_key
        is_thumb = key == self._thumb_key
        if not (is_full or is_thumb):
            return
        # A thumb must never overwrite the full image once it has arrived.
        if is_thumb and which in self._full_done:
            return
        if is_full:
            self._full_done.add(which)
        if which == "before":
            self._slider.set_before(pixmap)
        else:
            self._slider.set_after(pixmap)
        # Prefer the full image's true dimensions for the window aspect.
        if is_full or not self._aspect_locked:
            self._adopt_aspect(pixmap)

    def _adopt_aspect(self, pixmap) -> None:
        """Match the window + slider to the image aspect (used for templates and
        any generation lacking stored dimensions)."""
        if self._aspect_locked or pixmap is None or pixmap.isNull() or pixmap.height() <= 0:
            return
        self._aspect_locked = True
        self._aspect = pixmap.width() / pixmap.height()
        if self._aspect_box is not None:
            self._aspect_box.set_ratio(self._aspect)
        if not self._fullscreen:
            self._apply_image_size()

    def _on_ref_loaded(self, key: str, which: str, pixmap) -> None:
        if key != str(self._job.get("request_id") or ""):
            return
        thumb = getattr(self, "_ref_labels", {}).get(which)
        if thumb is None or pixmap is None or pixmap.isNull():
            return
        thumb.set_pixmap(pixmap)

    def _open_reference(self, index: int) -> None:
        thumb = getattr(self, "_ref_labels", {}).get(f"ref{index}")
        pm = thumb.full_pixmap() if thumb is not None else None
        if pm is None or pm.isNull():
            return
        box = _ImageLightbox(
            pm,
            tr("Reference image {n}").format(n=index + 1),
            on_download=lambda: self._download_reference(index),
            parent=self,
        )
        box.exec()
        box.deleteLater()

    def _download_reference(self, index: int) -> None:
        urls = self._job.get("reference_image_urls") or []
        if index >= len(urls) or not urls[index] or self._client is None:
            return
        from urllib.parse import urlparse

        url = urls[index]
        ext = os.path.splitext(urlparse(url).path)[1] or ".png"
        suggested = f"reference_{index + 1}{ext}"
        dest, _sel = QFileDialog.getSaveFileName(
            self, tr("Save reference image"), suggested
        )
        if not dest:
            return

        def _work(client=self._client, u=url, path=dest):
            # Worker thread: fetch and write here so a stalled link never
            # freezes QGIS (the old blockingGet held the UI for up to 60s).
            data = client.download_image(u)
            if not data:
                raise OSError("empty response")
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return {"path": path}

        task = GenericRequestTask(tr("Downloading reference image"), _work)
        task.succeeded.connect(lambda _res, t=task: self._release_ref_download(t))
        task.failed.connect(lambda msg, _code, t=task: self._on_ref_download_failed(t, msg))
        # Hard ref until the task settles: a QgsTask GC'd mid-run aborts QGIS.
        if getattr(self, "_ref_download_tasks", None) is None:
            self._ref_download_tasks = []
        self._ref_download_tasks.append(task)
        QgsApplication.taskManager().addTask(task)

    def _release_ref_download(self, task) -> None:
        tasks = getattr(self, "_ref_download_tasks", None)
        if tasks and task in tasks:
            tasks.remove(task)

    def _on_ref_download_failed(self, task, msg: str) -> None:
        self._release_ref_download(task)
        log_warning(f"Reference download failed: {msg}")
        try:
            visible = self.isVisible()
        except RuntimeError:
            return  # dialog destroyed while the download was in flight
        if not visible:
            return
        from qgis.PyQt.QtWidgets import QMessageBox

        QMessageBox.warning(
            self, tr("Download failed"),
            tr("Could not download the reference image."),
        )
