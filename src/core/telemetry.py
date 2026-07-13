"""Telemetry batched in memory, flushed once per generation cycle. Fails silently."""

from __future__ import annotations

import platform
import threading
from datetime import datetime, timezone

from qgis.core import QgsApplication, QgsTask
from qgis.PyQt.QtCore import QThread


def _on_main_thread() -> bool:
    """True when called on the GUI thread. flush() ends in the task manager,
    which is main-thread-only, so worker threads must only track()."""
    try:
        app = QgsApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return False


# Global opt-out switch, shared across TerraLab plugins (AI Edit + AI Segmentation)
# via the same QSettings key, so the user only has to disable telemetry once.
# Mirrors AI Segmentation's telemetry opt-out.
_TELEMETRY_ENABLED_KEY = "TerraLab/telemetry_enabled"


def is_telemetry_enabled() -> bool:
    """Whether anonymous usage telemetry is enabled. Opt-out: defaults to True.

    Reads the shared TerraLab/telemetry_enabled QSettings key. Fails closed: if
    the preference cannot be read, we do NOT send (privacy takes precedence over
    a data point)."""
    try:
        from qgis.PyQt.QtCore import QSettings
        return bool(QSettings().value(_TELEMETRY_ENABLED_KEY, True, type=bool))
    except Exception:  # nosec B110
        return False


def set_telemetry_enabled(enabled: bool) -> None:
    """Persist the global telemetry opt-out flag (shared across TerraLab plugins)."""
    try:
        from qgis.PyQt.QtCore import QSettings
        QSettings().setValue(_TELEMETRY_ENABLED_KEY, bool(enabled))
    except Exception:  # nosec B110
        pass


# Anonymous events with no user-generated content; they need no gate beyond the
# global opt-out. plugin_error stays additionally gated (raw exception text can
# include path fragments).
_NO_CONTENT_EVENTS = frozenset({
    "plugin_opened",
    "plugin_activated",
    "activation_screen_viewed",
    "activation_attempted",
    "launch_clicked",
    "subscribe_link_clicked",
    "trial_exhausted_viewed",
    # Tutorial/guide opens can happen signed-out (footer button is always
    # visible), so they must park pre-auth like the other lifecycle pings.
    "tutorial_opened",
    "template_selected",
    "generation_started",
    "generation_completed",
    "generation_failed",
    "generation_cancelled",
    "first_generation_milestone",
    "favorite_toggled",
    "recent_selected",
    # Library history restore/export: enum-only props, no user content.
    "history_restored",
    "history_exported",
    "markup_opened",
    "vectorize_panel_opened",
    "vectorize_suggestion_clicked",
    "vectorize_completed",
    "swipe_armed",
    "swipe_disarmed",
    # Refund visibility. Without these, failed-delivery refunds stay invisible.
    "generation_refund_attempted",
    "generation_refund_failed",
    # One-click connect onboarding. These fire pre-activation, so they sit in
    # _pending_pre_auth until the first authenticated flush drains them. The
    # server's plugin/track endpoint mirrors this allow-list.
    "ai_edit_pair_started",
    "ai_edit_pair_succeeded",
    "ai_edit_pair_failed",
    "ai_edit_pair_timeout",
    "ai_edit_pair_cancelled",
})


class _TelemetryFlushTask(QgsTask):
    """Sends one batch. Failures swallowed: telemetry must never break the plugin."""

    def __init__(self, client, events: list, auth: dict):
        from ..workers.generic_request_task import silent_task_flags
        super().__init__("AI Edit telemetry flush", silent_task_flags())
        self._client = client
        self._events = events
        self._auth = auth

    def run(self) -> bool:
        if self.isCanceled():
            return False
        # One retry with a short backoff covers a transient network blip without
        # a disk queue; a hard-offline session still loses the batch (accepted).
        if not self._post() and not self.isCanceled():
            import time
            time.sleep(2)
            if self.isCanceled():
                return False
            self._post()
        return True

    def _post(self) -> bool:
        """Send the batch; True only on a successful post. The client returns an
        error dict and never raises, so a failed batch has to be detected from
        the RESULT. Ignoring it made run()'s retry dead code: a failed batch
        returned True and was silently dropped."""
        try:
            result = self._client.send_telemetry_batch(self._events, self._auth)
        except Exception:  # nosec B110 - telemetry must never break the plugin
            return False
        return not (isinstance(result, dict) and result.get("error"))

    def finished(self, result: bool) -> None:
        return


class TelemetryCollector:
    def __init__(self, client, auth_manager, plugin_version: str = ""):
        self._client = client
        self._auth_manager = auth_manager
        self._plugin_version = plugin_version
        # Guards _batch / _pending_pre_auth / _inflight: track() can run on a
        # worker thread (refund + write-error paths) while the main thread
        # flushes, so the list mutations must not race.
        self._lock = threading.Lock()
        self._batch: list = []
        # Pre-auth lifecycle events parked here until the first authenticated
        # flush drains them, so early anonymous events are not lost. Capped to 50.
        self._pending_pre_auth: list = []
        self._inflight: list[_TelemetryFlushTask] = []
        self._session_props = self._build_session_props()

    def _build_session_props(self) -> dict:
        import sys

        try:
            from qgis.core import Qgis
            qgis_version = Qgis.version()
        except Exception:
            qgis_version = "unknown"

        props = {
            "plugin_version": self._plugin_version,
            "os": platform.system(),
            "os_version": platform.release(),
            "arch": platform.machine(),
            "python_version": sys.version.split()[0],
            "qgis_version": qgis_version,
        }
        # Anonymous per-machine hash: lets the backend count distinct machines per
        # activation key (measurement only). Best-effort; never break telemetry.
        try:
            from .device_id import get_device_hash
            props["device_hash"] = get_device_hash()
        except Exception:  # nosec B110
            pass
        return props

    def _has_auth(self) -> bool:
        auth = self._auth_manager.get_auth_header()
        return bool(auth and auth.get("Authorization"))

    def _has_consent(self) -> bool:
        from .auth.activation_manager import has_consent
        return has_consent()

    def _now_iso(self) -> str:
        # ms precision so same-second events don't collide on the server-side dedup key.
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )

    def track(self, event: str, properties: dict | None = None):
        # Global opt-out: when disabled, nothing is even queued.
        if not is_telemetry_enabled():
            return
        evt = {
            "event": event,
            "timestamp": self._now_iso(),
            "properties": {
                **self._session_props,
                **(properties or {}),
            },
        }
        with self._lock:
            self._batch.append(evt)

    def flush(self):
        """Non-blocking. Lifecycle events ship pre-consent; everything else
        requires consent. Pre-auth events queue in _pending_pre_auth.

        MAIN THREAD ONLY: it ends in QgsApplication.taskManager().addTask(),
        which is main-thread-only. Worker threads must only telemetry.track()
        and let the next main-thread flush ship the batch (see generation_worker).
        A stray off-thread call is now a safe no-op rather than a hard crash."""
        if not _on_main_thread():
            return
        task = None
        with self._lock:
            if not self._batch and not self._pending_pre_auth:
                return

            if not self._has_auth():
                for evt in self._batch:
                    if evt["event"] in _NO_CONTENT_EVENTS and len(self._pending_pre_auth) < 50:
                        self._pending_pre_auth.append(evt)
                self._batch.clear()
                return

            consented = self._has_consent()
            events_to_send = list(self._pending_pre_auth) + [
                e for e in self._batch
                if consented or e["event"] in _NO_CONTENT_EVENTS
            ]
            self._batch.clear()
            self._pending_pre_auth.clear()

            if not events_to_send:
                return

            auth = self._auth_manager.get_auth_header()
            task = _TelemetryFlushTask(self._client, events_to_send, auth)
            # Hold a strong reference so the task isn't GC'd while running.
            # The TaskManager would keep it alive too, but tracking lets us
            # cancel everything cleanly on shutdown().
            self._inflight.append(task)

        # Outside the lock: connect signals + hand to the task manager (Qt calls).
        try:
            task.taskCompleted.connect(lambda t=task: self._drop_inflight(t))
            task.taskTerminated.connect(lambda t=task: self._drop_inflight(t))
        except Exception:  # nosec B110
            pass
        QgsApplication.taskManager().addTask(task)

    def _drop_inflight(self, task: _TelemetryFlushTask) -> None:
        with self._lock:
            try:
                self._inflight.remove(task)
            except ValueError:
                pass

    def shutdown(self):
        # Best-effort final drain of the in-memory batch BEFORE cancelling
        # anything. flush() (main-thread only, a safe no-op off it) appends one
        # fresh QgsTask the task manager owns; on unload it may not run to
        # completion, so this is a last attempt, never a guarantee. We snapshot
        # the tasks already in flight and cancel ONLY those, so this final batch
        # is not cancelled out from under itself. Never let telemetry break
        # unload.
        with self._lock:
            stale = list(self._inflight)
        try:
            self.flush()
        except Exception:  # nosec B110 - telemetry must never break unload
            pass
        with self._lock:
            for task in stale:
                try:
                    self._inflight.remove(task)
                except ValueError:
                    pass
        for task in stale:
            try:
                task.cancel()
            except Exception:  # nosec B110
                pass


_collector: TelemetryCollector | None = None


def init_telemetry(client, auth_manager, plugin_version: str = ""):
    global _collector
    _collector = TelemetryCollector(client, auth_manager, plugin_version)
    try:
        from .config_store import get_store
        store = get_store()
        if store is not None:
            store.set_telemetry_collector(_collector)
    except Exception:  # nosec B110
        pass


def track(event: str, properties: dict | None = None):
    if _collector:
        _collector.track(event, properties)


def flush():
    if _collector:
        _collector.flush()


def shutdown_telemetry():
    global _collector
    if _collector is not None:
        try:
            _collector.shutdown()
        except Exception:  # nosec B110
            pass
    _collector = None
