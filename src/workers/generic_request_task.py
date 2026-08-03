"""QgsTask wrapper for one-shot client requests (credits, config, catalog, key validation)."""
from __future__ import annotations

from typing import Any, Callable

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.i18n import tr
from ..core.qt_compat import silent_task_flags

# A failure message is shown to the user, so the cap is only there to keep a
# runaway exception out of a notification banner. It has to clear a translated
# sentence: the German write error is 202 characters before the filename is
# substituted in, and the old 200 cut it mid-word in every download failure.
_MAX_FAILURE_CHARS = 500


class GenericRequestTask(QgsTask):
    """Run a no-args callable off the main thread. Raises or {"error",...} -> failed.

    Pass ``silent=True`` for background/startup requests so they do not appear
    in the QGIS task-manager widget.
    """

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, description: str, request_fn: Callable[[], Any], *, silent: bool = False):
        super().__init__(description, silent_task_flags() if silent else QgsTask.Flag.CanCancel)
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

    @staticmethod
    def _unexpected_failure() -> tuple[str, str]:
        """Message + code for a request that died in a way nothing else caught."""
        from ..core.errors import ErrorCode
        return (tr("The request failed unexpectedly."), ErrorCode.UNKNOWN.value)

    @staticmethod
    def _failure_text(err: Exception) -> str:
        """The sentence the user reads. ``AIEditError.__str__`` puts ``[CODE]``
        in front of it, and the failure tuple already carries that code in its
        own slot, so read the plain ``message`` when the exception has one."""
        message = getattr(err, "message", "")
        text = message if isinstance(message, str) and message else str(err)
        return text[:_MAX_FAILURE_CHARS]

    def run(self) -> bool:
        # The guard covers the whole body, not just the call: unpacking the
        # response dict below can raise too, and a raise out of run() reaches
        # finished() as a bare False with no failure recorded, leaving whoever
        # is waiting on succeeded/failed with no answer at all.
        try:
            return self._run_request()
        except Exception as e:
            # Preserve a usable code so consumers that branch on it (network vs
            # app error, whether to open the bug-report dialog) don't misread a
            # raised exception as a generic blank-code error.
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
        else:
            # No slot filled: run() died somewhere sip could not report. Answer
            # anyway, or the caller's "checking..." state never resolves.
            self.failed.emit(*self._unexpected_failure())
