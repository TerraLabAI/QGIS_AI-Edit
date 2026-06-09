





from __future__ import annotations

import math
import time

from qgis.core import QgsFeedback, QgsTask
from qgis.PyQt.QtCore import pyqtSignal

from ..api.network_error_classifier import request_feedback
from ..core.auth.activation_manager import _KEY_RE
from ..core.config_store import get_export_copy, get_export_dial, get_export_dial_pair
from ..core.errors import NETWORK_ERROR_CODES
from ..core.i18n import tr
from ..core.logger import log_debug, log_warning




_POLL_INTERVAL_S = 3.0
_TOTAL_TIMEOUT_S = 600.0


_NETWORK_PROBLEM_AFTER = 3

_RETRY_AFTER_CLAMP_S = (1.0, 15.0)


class PairingPollTask(QgsTask):







    pairing_succeeded = pyqtSignal(str)
    pairing_failed = pyqtSignal(str, str)
    pairing_timeout = pyqtSignal()





    pairing_browser_seen = pyqtSignal()
    pairing_stalled = pyqtSignal()


    pairing_network_problem = pyqtSignal(str)



    STALL_AFTER_S = 45.0

    def __init__(
        self,
        client,
        code: str,
        interval_s: float | None = None,
        total_timeout_s: float | None = None,
    ):
        super().__init__(
            get_export_copy("pipeline.pairing_poll_task.connecting", tr("Connecting AI Edit")),
            QgsTask.Flag.CanCancel,
        )
        self._client = client
        self._code = code
        if interval_s is None:
            interval_s = get_export_dial("pairing.interval_s", _POLL_INTERVAL_S)
        if total_timeout_s is None:
            total_timeout_s = get_export_dial("pairing.total_s", _TOTAL_TIMEOUT_S)
        self._interval_s = self._nonnegative_seconds(interval_s, _POLL_INTERVAL_S)
        self._total_timeout_s = self._nonnegative_seconds(total_timeout_s, _TOTAL_TIMEOUT_S)
        self._key: str | None = None
        self._failure: tuple[str, str] | None = None
        self._timed_out = False


        self.cancelled_by_plugin = False

        self._feedback = QgsFeedback()

    @staticmethod
    def _nonnegative_seconds(value, fallback: float) -> float:
        try:
            seconds = float(value)
        except (TypeError, ValueError, OverflowError):
            return fallback
        return seconds if math.isfinite(seconds) and seconds >= 0 else fallback

    def cancel(self) -> None:
        try:
            self._feedback.cancel()
        except Exception:  # nosec B110
            pass
        super().cancel()

    def cancel_by_plugin(self) -> None:
        self.cancelled_by_plugin = True
        self.cancel()

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
                "pipeline.pairing_poll_task.unexpected_failure",
                tr("Sign-in failed unexpectedly. Click Connect to try again."),
            ),
            "POLL_ERROR",
        )

    def run(self) -> bool:





        try:
            with request_feedback(self._feedback):
                return self._run_poll()
        except Exception as err:  # noqa: BLE001
            if self.isCanceled():
                return False
            log_warning(f"Pairing poll failed unexpectedly: {err}")
            self._failure = self._unexpected_failure()
            return False

    def _run_poll(self) -> bool:
        started = time.monotonic()
        deadline = started + self._total_timeout_s
        stall_after_s = get_export_dial("pairing.stall_s", self.STALL_AFTER_S)
        browser_seen = False
        stall_hinted = False
        network_failures = 0
        network_hinted = False
        while not self.isCanceled() and time.monotonic() < deadline:
            try:
                result = self._client.poll_pairing(self._code)
            except Exception:
                result = {"error": "poll failed", "code": "NO_NETWORK"}

            if self.isCanceled():
                return False

            status = result.get("status") if isinstance(result, dict) else None
            error_code = (
                str(result.get("code") or "").strip().upper()
                if isinstance(result, dict) and "error" in result
                else ""
            )
            if error_code in NETWORK_ERROR_CODES:
                network_failures += 1
                if network_failures >= _NETWORK_PROBLEM_AFTER and not network_hinted:
                    network_hinted = True
                    self.pairing_network_problem.emit(error_code)
            else:
                network_failures = 0
            if status == "ready":
                raw_key = result.get("activation_key")
                key = raw_key.strip() if isinstance(raw_key, str) else ""
                if _KEY_RE.fullmatch(key):
                    self._key = key
                    return True


                self._failure = (
                    get_export_copy(
                        "pipeline.pairing_poll_task.bad_key",
                        tr("Unexpected response from the server. Please try again."),
                    ),
                    "BAD_KEY",
                )
                return False

            if status == "no_plan":


                self._failure = (
                    get_export_copy(
                        "pipeline.pairing_poll_task.no_plan",
                        tr(
                            "This account has no active AI Edit plan. "
                            "Reactivate it on terra-lab.ai, then click Connect again."
                        ),
                    ),
                    "NO_PLAN",
                )
                return False

            if status == "cancelled":


                self._failure = (
                    get_export_copy(
                        "pipeline.pairing_poll_task.cancelled",
                        tr("Sign-in was cancelled in the browser. Click Connect to try again."),
                    ),
                    "CANCELLED",
                )
                return False








            if status == "pending" and not browser_seen:
                browser_seen = True
                self.pairing_browser_seen.emit()
            elif not browser_seen and not stall_hinted and time.monotonic() - started >= stall_after_s:



                stall_hinted = True
                self.pairing_stalled.emit()

            sleep_s = self._interval_s
            hint = result.get("retry_after") if isinstance(result, dict) else None
            if hint is not None:
                try:
                    retry_lo, retry_hi = get_export_dial_pair(
                        "pipeline.pairing_poll_task.retry_after_clamp_s", _RETRY_AFTER_CLAMP_S
                    )
                    hinted_seconds = float(hint)
                    if math.isfinite(hinted_seconds):
                        sleep_s = min(max(hinted_seconds, retry_lo), retry_hi)
                except (TypeError, ValueError, OverflowError):
                    pass



            detail = status or (result.get("code") if isinstance(result, dict) else None)
            log_debug(f"Pairing poll: waiting ({detail or 'unknown'})")
            self._sleep_cancellable(min(sleep_s, max(0.0, deadline - time.monotonic())))

        if self.isCanceled():
            return False
        self._timed_out = True
        return False

    def _sleep_cancellable(self, seconds: float) -> None:

        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.isCanceled():
                return
            time.sleep(min(0.25, max(0.0, end - time.monotonic())))

    def finished(self, result: bool) -> None:
        if self.isCanceled():
            return
        if result and self._key:
            self.pairing_succeeded.emit(self._key)
        elif self._timed_out:
            self.pairing_timeout.emit()
        elif self._failure is not None:
            self.pairing_failed.emit(*self._failure)
        else:


            self.pairing_failed.emit(*self._unexpected_failure())
