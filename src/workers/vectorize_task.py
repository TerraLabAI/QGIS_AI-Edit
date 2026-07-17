"""QgsTask wrapper that runs the heavy color-vectorize compute off the main
thread so QGIS never freezes on a large raster, and the run is cancelable.

Only the pure GDAL/numpy/geometry compute runs in ``run()``; the resulting
``QgsFeature`` list is handed back to the main thread, where the panel builds
the ``QgsVectorLayer`` (creating/adding QGIS layer objects must stay on the
main thread). All ``QgsProject`` context (CRS, transform context, ellipsoid)
is captured by the caller on the main thread and passed in.
"""
from __future__ import annotations

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.errors import AIEditError, ErrorCode
from ..core.generation.vectorization_service import compute_class_features
from ..core.i18n import tr
from ..core.logger import log_warning


class VectorizeTask(QgsTask):
    """Compute vectorize features off-thread. Emits the feature list on success;
    the caller builds + styles the layer on the main thread in the slot."""

    # (features, params) - params is the dict passed in, so the slot knows the
    # raster id / target color / is_initial flag without extra bookkeeping.
    succeeded = pyqtSignal(object, object)
    failed = pyqtSignal(str, str)

    def __init__(self, compute_kwargs: dict, params: dict):
        super().__init__("AI Edit vectorize", QgsTask.Flag.CanCancel)
        self._compute_kwargs = compute_kwargs
        self._params = params
        self._features: list | None = None
        self._failure: tuple[str, str] | None = None

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
            feats = compute_class_features(
                is_cancelled=self.isCanceled, **self._compute_kwargs
            )
        except AIEditError as err:
            self._failure = (err.message, err.code.value if err.code else "")
            return False
        except Exception as err:  # nosec B110 - surface as a failed task, never crash QGIS.
            log_warning(f"Vectorize compute failed: {err}")
            # A stable code + translated, user-safe message: the raw exception
            # text (GDAL/numpy internals) must never reach the UI, only the log.
            self._failure = (
                tr("Vectorize failed unexpectedly. Please try again, or report the problem if it persists."),
                ErrorCode.VECTORIZE_INTERNAL_ERROR.value,
            )
            return False
        if feats is None or self.isCanceled():
            # Cancelled mid-compute: no result, no error.
            return False
        self._features = feats
        return True

    def finished(self, result: bool) -> None:
        if self.isCanceled():
            return
        if result and self._features is not None:
            self.succeeded.emit(self._features, self._params)
        elif self._failure is not None:
            self.failed.emit(*self._failure)
