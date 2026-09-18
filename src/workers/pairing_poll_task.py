"""QgsTask that polls the server until a browser pairing code is bound to a key.

Backs the one-click "Connect" onboarding: the plugin mints a code, opens the
browser to /connect?code=..., and this task polls /api/plugin/pair/poll until
the signed-in user binds the code to their activation key.
"""
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

# Poll cadence and overall deadline. Server override via export-config
# `pairing` {stall_s, interval_s, total_s}; cache-only reads, so the worker
# thread never touches the network or QSettings for a dial.
_POLL_INTERVAL_S = 3.0
_TOTAL_TIMEOUT_S = 600.0
# Consecutive network failures before the dock says the network is the problem.
# Polling goes on: a VPN that comes back still finishes the sign-in.
_NETWORK_PROBLEM_AFTER = 3
# Clamp applied to the server's adaptive retry_after poll hint.
_RETRY_AFTER_CLAMP_S = (1.0, 15.0)


class PairingPollTask(QgsTask):
    """Poll until the browser handoff completes.

    Emits exactly one of pairing_succeeded(key) / pairing_failed(msg, code) /
    pairing_timeout(). Payloads are plain str (already copied), so finished()
    never touches live context off the worker thread.
    """

    pairing_succeeded = pyqtSignal(str)
    pairing_failed = pyqtSignal(str, str)
    pairing_timeout = pyqtSignal()
    # The server marks the code 'pending' as soon as /connect renders, so we
    # can tell "user is in the browser, signing in" from "the page never
    # loaded" (browser blocked, page error, wrong machine). Emitted at most
    # once each; older servers never report 'pending' before success, in
    # which case only the stalled hint can fire and the flow is unchanged.
    pairing_browser_seen = pyqtSignal()
    pairing_stalled = pyqtSignal()
    # Emitted once, with the error code, when the poll itself keeps failing on
    # the network (proxy, SSL inspection, filter). Polling continues.
    pairing_network_problem = pyqtSignal(str)

    # How long to poll without ever seeing the browser before hinting the
    # user that the page probably never opened.
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
        # Set before a cancel the plugin asked for, so a cancel from the QGIS
        # Task Manager can be told apart and the dock recovered.
        self.cancelled_by_plugin = False
        # Lets cancel() abort the poll request in flight.
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
        """Message + code used when the poll dies in a way nothing else caught."""
        return (
            get_export_copy(
                "pipeline.pairing_poll_task.unexpected_failure",
                tr("Sign-in failed unexpectedly. Click Connect to try again."),
            ),
            "POLL_ERROR",
        )

    def run(self) -> bool:
        # Last-resort guard. Only the poll_pairing() call is guarded below; the
        # dial read, the key match, the .get() chains and the emits are not, and
        # a raise out of run() reaches finished() as a bare False with nothing
        # recorded. Browser sign-in would then sit on "waiting" for the rest of
        # the session, because only the failure and timeout paths restore idle.
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
                # Server said ready but the key is malformed: terminal, never
                # persist garbage.
                self._failure = (
                    get_export_copy(
                        "pipeline.pairing_poll_task.bad_key",
                        tr("Unexpected response from the server. Please try again."),
                    ),
                    "BAD_KEY",
                )
                return False

            if status == "no_plan":
                # The signed-in account has no active plan to connect. Terminal:
                # stop now with a clear message instead of polling to timeout.
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
                # The user left the browser page without confirming. Terminal:
                # stop polling right away instead of spinning until timeout.
                self._failure = (
                    get_export_copy(
                        "pipeline.pairing_poll_task.cancelled",
                        tr("Sign-in was cancelled in the browser. Click Connect to try again."),
                    ),
                    "CANCELLED",
                )
                return False

            # Everything else - "pending" (browser reached /connect, user still
            # signing in), "not_found" (the browser never reached /connect, or
            # the code expired), and transient network/server errors - just
            # means "keep waiting". The poll is idempotent, so we loop until
            # ready or the overall deadline. Newer servers hint how long to
            # wait; absent or junk falls back to the fixed interval so older
            # servers behave unchanged.
            if status == "pending" and not browser_seen:
                browser_seen = True
                self.pairing_browser_seen.emit()
            elif not browser_seen and not stall_hinted and time.monotonic() - started >= stall_after_s:
                # Long wait and the server never saw the browser: the page
                # probably never opened (blocked browser, page error). Hint
                # the recovery paths instead of spinning silently.
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
                except (TypeError, ValueError, OverflowError):  # malformed retry_after: keep the default interval
                    pass
            # Status detail makes user error reports diagnosable (pending =
            # browser seen, not_found = browser never seen, NO_NETWORK = the
            # poll itself failing). Never log the code itself.
            detail = status or (result.get("code") if isinstance(result, dict) else None)
            log_debug(f"Pairing poll: waiting ({detail or 'unknown'})")
            self._sleep_cancellable(min(sleep_s, max(0.0, deadline - time.monotonic())))

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
            # No slot filled: run() died somewhere sip could not report. Answer
            # anyway, or the Connect button waits for a signal that never comes.
            self.pairing_failed.emit(*self._unexpected_failure())
