"""QgsTask wrapper for one-shot client requests (credits, config, catalog, key validation)."""
from __future__ import annotations

from typing import Any, Callable

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal


def silent_task_flags(can_cancel: bool = True):
    """CanCancel plus Hidden/Silent when the running QGIS exposes them.

    Hidden / Silent landed in QGIS 3.26; the plugin floor (metadata.txt
    qgisMinimumVersion) is older, so resolve each flag defensively. Naming
    QgsTask.Flag.Hidden directly would AttributeError at import on older builds;
    there the task degrades to a plain (visible) cancellable task, which is
    harmless. Keeping startup/background requests hidden stops the task-manager
    widget from filling with alarming "AI Edit ..." rows on every launch.
    """
    flags = QgsTask.Flag.CanCancel if can_cancel else QgsTask.Flag(0)
    for name in ("Hidden", "Silent"):
        flag = getattr(QgsTask.Flag, name, None)
        if flag is not None:
            flags = flags | flag
    return flags


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

    def run(self) -> bool:
        if self.isCanceled():
            return False
        try:
            result = self._request_fn()
        except Exception as e:
            # Preserve a usable code so consumers that branch on it (network vs
            # app error, whether to open the bug-report dialog) don't misread a
            # raised exception as a generic blank-code error.
            from ..core.errors import ErrorCode
            raw_code = getattr(e, "code", "")
            code = getattr(raw_code, "value", raw_code) or ErrorCode.UNKNOWN.value
            self._failure = (str(e)[:200], str(code))
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
