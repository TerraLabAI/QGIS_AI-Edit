"""Off-thread canvas exporter as a QgsTask."""
from __future__ import annotations

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..ui.canvas_exporter import ExportPrep, render_clean_base, render_export


class ExportWorker(QgsTask):
    # b64, out_w, out_h, extent, bytes, format, clean_base_b64, clean_base_format
    # The clean-base slots are "" when the user drew no markup. They travel
    # through the guidance channel (a second image) so the model can restore the
    # pixels under each mark; the marks themselves ride on the main b64.
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

    def run(self) -> bool:
        if self.isCanceled():
            return False
        try:
            b64, size_bytes, actual_extent, fmt = render_export(self._prep)
            clean_base = render_clean_base(self._prep)
        except Exception as err:  # noqa: BLE001
            self._failure = str(err)
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
