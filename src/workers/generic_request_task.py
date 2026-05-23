
from __future__ import annotations

from typing import Any, Callable

from qgis.core import QgsFeedback, QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..api.network_error_classifier import request_feedback
from ..core.config_store import get_export_copy, get_export_dial
from ..core.i18n import tr
from ..core.log_scrub import scrub_file_paths, scrub_urls
from ..core.logger import log_warning
from ..core.qt_compat import silent_task_flags





_MAX_FAILURE_CHARS = 500


class GenericRequestTask(QgsTask):






    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, description: str, request_fn: Callable[[], Any], *, silent: bool = False):
        super().__init__(description, silent_task_flags() if silent else QgsTask.Flag.CanCancel)
        self._request_fn = request_fn
        self._result: Any = None
        self._failure: tuple[str, str] | None = None


        self._feedback = QgsFeedback()

    def cancel(self) -> None:
        try:
            self._feedback.cancel()
        except Exception:  # nosec B110
            pass
        super().cancel()

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

        from ..core.errors import ErrorCode
        return (
            get_export_copy(
                "pipeline.generic_request_task.unexpected_failure", tr("The request failed unexpectedly.")
            ),
            ErrorCode.UNKNOWN.value,
        )

    @staticmethod
    def _failure_text(err: Exception) -> str:









        message = getattr(err, "message", "")
        if isinstance(message, str) and message:
            max_chars = get_export_dial("pipeline.generic_request_task.max_failure_chars", _MAX_FAILURE_CHARS)
            return message[:max_chars]
        log_warning(f"Generic request task failed: {scrub_file_paths(scrub_urls(str(err)))}")
        return get_export_copy(
            "pipeline.generic_request_task.unexpected_failure", tr("The request failed unexpectedly.")
        )

    def run(self) -> bool:




        try:
            with request_feedback(self._feedback):
                return self._run_request()
        except Exception as e:



            from ..core.errors import ErrorCode
            raw_code = getattr(e, "code", "")
            code = getattr(raw_code, "value", raw_code) or ErrorCode.UNKNOWN.value
            self._failure = (self._failure_text(e), str(code))
            return False

    def _run_request(self) -> bool:
        if self.isCanceled():
            return False
        result = self._request_fn()

        if self.isCanceled():
            return False

        if isinstance(result, dict) and "error" in result:
            self._failure = (
                self._failure_text(RuntimeError(str(result.get("error") or self._unexpected_failure()[0]))),
                str(result.get("code") or self._unexpected_failure()[1]),
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
        else:


            self.failed.emit(*self._unexpected_failure())
