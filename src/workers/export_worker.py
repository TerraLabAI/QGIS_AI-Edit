"""Off-thread canvas exporter.

Pulled out of the synchronous _on_generate path because the heavy bits
(QgsMapRendererCustomPainterJob.waitForFinished + PNG encode at 2K/4K)
were freezing the UI thread for hundreds of ms, popping the wait cursor
every time the user clicked Generate.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import QThread, pyqtSignal

from ..ui.canvas_exporter import ExportPrep, render_export


class ExportWorker(QThread):
    """Run render_export on a worker thread, emit the result back."""

    completed = pyqtSignal(str, int, int, object, int)
    # b64, out_w, out_h, actual_extent, image_size_bytes

    failed = pyqtSignal(str)

    def __init__(self, prep: ExportPrep, parent=None):
        super().__init__(parent)
        self._prep = prep

    def run(self):
        try:
            b64, size_bytes, actual_extent = render_export(self._prep)
        except Exception as err:  # noqa: BLE001 - surface to UI thread.
            self.failed.emit(str(err))
            return
        self.completed.emit(
            b64,
            self._prep.out_w,
            self._prep.out_h,
            actual_extent,
            size_bytes,
        )
