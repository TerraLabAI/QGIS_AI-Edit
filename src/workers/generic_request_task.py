"""QgsTask wrapper for one-shot client requests (credits, config, catalog, key validation)."""
from __future__ import annotations

from typing import Any, Callable

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal


class GenericRequestTask(QgsTask):
    """Run a no-args callable off the main thread. Raises or {"error",...} -> failed."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, description: str, request_fn: Callable[[], Any]):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self._request_fn = request_fn
        self._result: Any = None
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
            result = self._request_fn()
        except Exception as e:
            self._failure = (str(e), "")
            return False

        if self.isCanceled():
            return False

        if isinstance(result, dict) and "error" in result:
            self._failure = (
                str(result.get("error", "Unknown error")),
                str(result.get("code", "")),
            )
            return False

        self._result = result
        return True

    def finished(self, result: bool) -> None:
        if self.isCanceled():
            return
        if result:
            self.succeeded.emit(self._result)
        elif self._failure is not None:
            self.failed.emit(*self._failure)
