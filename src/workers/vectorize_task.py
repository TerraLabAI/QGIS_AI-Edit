








from __future__ import annotations

import copy

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.config_store import get_export_copy
from ..core.errors import AIEditError, ErrorCode
from ..core.generation.vectorization_service import compute_class_features
from ..core.i18n import tr
from ..core.logger import log_warning


def _detached_payload(value):







    if isinstance(value, dict):
        return {key: _detached_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_detached_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_detached_payload(item) for item in value)
    if type(value).__name__.startswith("Qgs"):
        try:
            return type(value)(value)
        except TypeError:
            return value
    try:
        return copy.deepcopy(value)
    except (TypeError, copy.Error):
        return value


class VectorizeTask(QgsTask):





    succeeded = pyqtSignal(object, object)
    failed = pyqtSignal(str, str)

    def __init__(self, compute_kwargs: dict, params: dict):
        super().__init__("AI Edit vectorize", QgsTask.Flag.CanCancel)


        self._compute_kwargs = dict(compute_kwargs)
        self._params = _detached_payload(params)
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

    @staticmethod
    def _unexpected_failure() -> tuple[str, str]:




        return (
            get_export_copy(
                "pipeline.vectorize_task.unexpected_failure",
                tr("Vectorize failed unexpectedly. Please try again, or report the problem if it persists."),
            ),
            ErrorCode.VECTORIZE_INTERNAL_ERROR.value,
        )

    def run(self) -> bool:



        try:
            return self._run_compute()
        except Exception as err:  # noqa: BLE001
            if self.isCanceled():
                return False
            log_warning(f"Vectorize task failed unexpectedly: {err}")
            self._failure = self._unexpected_failure()
            return False

    def _run_compute(self) -> bool:
        if self.isCanceled():
            return False
        try:
            feats = compute_class_features(
                is_cancelled=self.isCanceled, **self._compute_kwargs
            )
        except AIEditError as err:
            self._failure = (err.message, err.code.value if err.code else "")
            return False
        except Exception as err:
            log_warning(f"Vectorize compute failed: {err}")
            self._failure = self._unexpected_failure()
            return False
        if feats is None or self.isCanceled():

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
        else:


            self.failed.emit(*self._unexpected_failure())
