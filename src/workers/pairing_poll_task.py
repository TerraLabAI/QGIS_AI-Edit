"""QgsTask that polls the server until a browser pairing code is bound to a key.

Backs the one-click "Connect" onboarding: the plugin mints a code, opens the
browser to /connect?code=..., and this task polls /api/plugin/pair/poll until
the signed-in user binds the code to their activation key.
"""
from __future__ import annotations

import time

from qgis.core import QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..core.auth.activation_manager import _KEY_RE
from ..core.i18n import tr
from ..core.logger import log_debug


class PairingPollTask(QgsTask):
    """Poll until the browser handoff completes.

    Emits exactly one of pairing_succeeded(key) / pairing_failed(msg, code) /
    pairing_timeout(). Payloads are plain str (already copied), so finished()
    never touches live context off the worker thread.
    """

    pairing_succeeded = pyqtSignal(str)
    pairing_failed = pyqtSignal(str, str)
    pairing_timeout = pyqtSignal()

    def __init__(
        self,
        client,
        code: str,
        interval_s: float = 3.0,
        total_timeout_s: float = 600.0,
    ):
        super().__init__(tr("Connecting AI Edit"), QgsTask.Flag.CanCancel)
        self._client = client
        self._code = code
        self._interval_s = interval_s
        self._total_timeout_s = total_timeout_s
        self._key: str | None = None
        self._failure: tuple[str, str] | None = None
        self._timed_out = False

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
        deadline = time.monotonic() + self._total_timeout_s
        while not self.isCanceled() and time.monotonic() < deadline:
            try:
                result = self._client.poll_pairing(self._code)
            except Exception:
                result = {"error": "poll failed", "code": "NO_NETWORK"}

            if self.isCanceled():
                return False

            status = result.get("status") if isinstance(result, dict) else None
            if status == "ready":
                key = (result.get("activation_key") or "").strip()
                if _KEY_RE.match(key):
                    self._key = key
                    return True
                # Server said ready but the key is malformed: terminal, never
                # persist garbage.
                self._failure = (
                    tr("Unexpected response from the server. Please try again."),
                    "BAD_KEY",
                )
                return False

            # Everything else - "pending" (bound row not ready yet), "not_found"
            # (the user hasn't reached /connect yet, or the code expired), and
            # transient network/server errors - just means "keep waiting". The
            # poll is idempotent, so we loop until ready or the overall deadline.
            log_debug("Pairing poll: waiting")
            self._sleep_cancellable(self._interval_s)

        if self.isCanceled():
            return False
        self._timed_out = True
        return False

    def _sleep_cancellable(self, seconds: float) -> None:
        """Sleep in short slices so a cancel is honored quickly."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.isCanceled():
                return
            time.sleep(0.25)

    def finished(self, result: bool) -> None:
        if self.isCanceled():
            return
        if result and self._key:
            self.pairing_succeeded.emit(self._key)
        elif self._timed_out:
            self.pairing_timeout.emit()
        elif self._failure is not None:
            self.pairing_failed.emit(*self._failure)
