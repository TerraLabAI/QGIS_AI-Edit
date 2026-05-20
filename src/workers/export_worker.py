
from __future__ import annotations

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.canvas_export import ExportPrep, render_clean_base, render_export
from ..core.config_store import get_export_copy
from ..core.i18n import tr
from ..core.log_scrub import scrub_file_paths, scrub_urls
from ..core.logger import log_warning


class ExportWorker(QgsTask):




    completed = pyqtSignal(str, int, int, object, int, str, str, str)
    failed = pyqtSignal(str)

    def __init__(self, prep: ExportPrep):
        super().__init__("AI Edit canvas export", QgsTask.Flag.CanCancel)
        self._prep = prep
        self._success_payload: tuple | None = None
        self._failure: str | None = None

    def is_active(self) -> bool:
        try:
            return self.status() in (
                QgsTask.TaskStatus.Running,
                QgsTask.TaskStatus.Queued,
                QgsTask.TaskStatus.OnHold,
            )
        except Exception:
            return False

    @staticmethod
    def _unexpected_failure() -> str:

        return get_export_copy(
            "pipeline.export_worker.unexpected_failure", tr("Unexpected error while rendering the map.")
        )

    def run(self) -> bool:





        try:
            return self._run_export()
        except Exception as err:  # noqa: BLE001
            if self.isCanceled():
                return False
            log_warning(f"Canvas export failed unexpectedly: {err}")
            self._failure = self._unexpected_failure()
            return False

    def _run_export(self) -> bool:
        if self.isCanceled():
            return False
        try:
            b64, size_bytes, actual_extent, fmt = render_export(self._prep)
            if self.isCanceled():
                return False
            clean_base = render_clean_base(self._prep)
        except Exception as err:  # noqa: BLE001




            log_warning(f"Canvas export failed: {scrub_file_paths(scrub_urls(str(err)))}")
            self._failure = self._unexpected_failure()
            return False
        if self.isCanceled():
            return False
        clean_base_b64, clean_base_fmt = clean_base or ("", "")
        self._success_payload = (
            b64,
            self._prep.out_w,
            self._prep.out_h,
            actual_extent,
            size_bytes,
            fmt,
            clean_base_b64,
            clean_base_fmt,
        )
        return True

    def finished(self, result: bool) -> None:
        if self.isCanceled():
            return
        if result and self._success_payload is not None:
            self.completed.emit(*self._success_payload)
        elif self._failure is not None:
            self.failed.emit(self._failure)
        else:


            self.failed.emit(self._unexpected_failure())
