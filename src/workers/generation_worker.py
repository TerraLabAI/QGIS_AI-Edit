"""Worker thread for the full generation pipeline.

Runs off the main thread: auth check → generate → download → write GeoTIFF.
Emits Qt signals for progress, errors, and completion.
"""
from __future__ import annotations

import base64
import random
import time

from qgis.PyQt.QtCore import QThread, pyqtSignal

from ..core.logger import log, log_warning
from ..core.pipeline_context import save_debug_artifacts
from ..ui.raster_writer import write_geotiff

PROGRESS_MESSAGES = [
    "Summoning pixels...",
    "Waking up the AI...",
    "Rearranging atoms...",
    "Teaching geography...",
    "Whispering to satellites...",
    "Convincing clouds to move...",
    "Asking nicely...",
    "Negotiating with terrain...",
    "Pixel diplomacy...",
    "Consulting the map gods...",
    "Brewing something...",
    "Rewriting cartography...",
    "Crunching landscapes...",
    "Bending reality...",
    "Almost there...",
    "One moment...",
    "Patience, young cartographer...",
    "Magic in progress...",
]

# Fallback if server doesn't return estimated_time (should rarely happen)
DEFAULT_ESTIMATED_TIME = 25


class GenerationWorker(QThread):
    """Worker thread for auth check, generation, download, and GeoTIFF writing."""

    finished = pyqtSignal(object)  # dict with result info
    progress = pyqtSignal(str, int)  # status message, percentage (0-100)
    error = pyqtSignal(str, str)  # error message, error code

    def __init__(
        self,
        client,
        auth_manager,
        service,
        image_b64,
        prompt,
        aspect_ratio,
        extent_dict,
        crs_wkt,
        output_dir,
        suggested_resolution,
        ctx=None,
        debug_mode=False,
        plugin_dir="",
        skip_trial_check=False,
    ):
        super().__init__()
        self._client = client
        self._auth_manager = auth_manager
        self._service = service
        self._image_b64 = image_b64
        self._prompt = prompt
        self._aspect_ratio = aspect_ratio
        self._extent_dict = extent_dict
        self._crs_wkt = crs_wkt
        self._output_dir = output_dir
        self._ctx = ctx
        self._debug_mode = debug_mode
        self._plugin_dir = plugin_dir
        self._skip_trial_check = skip_trial_check
        self._suggested_resolution = suggested_resolution

    def run(self):
        self.progress.emit("Preparing...", 0)

        if not self._skip_trial_check:
            try:
                allowed, reason, code = self._auth_manager.check_can_generate()
            except Exception as e:
                self.error.emit(str(e), "CONNECTION_ERROR")
                return

            if not allowed:
                self.error.emit(reason, code)
                return

        self.progress.emit("Sending to the AI...", 5)

        # Shuffle messages so each generation feels different
        messages = list(PROGRESS_MESSAGES)
        random.shuffle(messages)
        self._msg_index = 0
        self._poll_count = 0
        self._start_time = time.time()
        self._last_pct = 5  # track last emitted percentage for smooth transitions

        def _on_progress(status, current, total, estimated_time=None, elapsed=None):
            self._poll_count += 1
            if self._poll_count % 2 == 1:
                msg = messages[self._msg_index % len(messages)]
                self._msg_index += 1

                est = estimated_time or DEFAULT_ESTIMATED_TIME
                t_elapsed = elapsed if elapsed is not None else (time.time() - self._start_time)
                t = min(t_elapsed / est, 1.0) if est > 0 else 0
                # Ease-out quadratic: fast start, slows near end
                target_pct = min(90, int(95 * (1 - (1 - t) ** 2)))

                # Smooth: never jump more than 8% at once, never go backwards
                pct = min(target_pct, self._last_pct + 8)
                pct = max(pct, self._last_pct + 1)  # always advance at least 1%
                pct = min(pct, 90)
                self._last_pct = pct

                self.progress.emit(msg, pct)

        result = self._service.generate(
            image_b64=self._image_b64,
            prompt=self._prompt,
            auth=self._auth_manager.get_auth_header(),
            aspect_ratio=self._aspect_ratio,
            on_progress=_on_progress,
            ctx=self._ctx,
            suggested_resolution=self._suggested_resolution,
        )

        if not result.success:
            self.error.emit(
                result.error or "Generation failed", result.error_code or ""
            )
            return

        self.progress.emit("Grabbing the masterpiece...", 93)

        try:
            image_data = self._client.download_image(result.image_url)
            log(f"Downloaded image: {len(image_data)} bytes")
        except Exception as e:
            self.error.emit(f"Failed to download result image: {e}", "DOWNLOAD_ERROR")
            return

        self.progress.emit("Placing on the map...", 97)

        try:
            ext = self._extent_dict
            log(
                f"Writing GeoTIFF: extent=({ext['xmin']:.2f},{ext['ymin']:.2f})-({ext['xmax']:.2f},{ext['ymax']:.2f})"
            )
            geotiff_path = write_geotiff(
                image_data=image_data,
                extent_dict=self._extent_dict,
                crs_wkt=self._crs_wkt,
                output_dir=self._output_dir,
                prompt=self._prompt,
                ctx=self._ctx,
            )
        except Exception as e:
            self.error.emit(f"Failed to write GeoTIFF: {e}", "WRITE_ERROR")
            return

        # Validate after write_geotiff so received dimensions are populated
        if self._ctx is not None:
            warnings = self._ctx.validate()
            for w in warnings:
                log_warning(f"Pipeline: {w}")
            log(f"Pipeline: {self._ctx.safe_log_summary()}")

        self.finished.emit({"geotiff_path": geotiff_path, "prompt": self._prompt})

        # Save debug artifacts if dev mode
        if self._debug_mode and self._ctx is not None:
            try:
                sent_png = base64.b64decode(self._image_b64)
                save_debug_artifacts(self._ctx, sent_png, image_data, self._plugin_dir)
            except Exception:
                pass
