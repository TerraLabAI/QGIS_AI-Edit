"""Telemetry batched in memory, flushed once per generation cycle. Fails silently."""

import platform
from datetime import datetime, timezone
from typing import Optional

from qgis.core import QgsApplication, QgsTask

# No user-generated content; ship pre-consent so the activation funnel stays
# observable. plugin_error stays consent-gated (raw exception text can include paths).
_NO_CONSENT_EVENTS = frozenset({
    "plugin_opened",
    "plugin_activated",
    "activation_screen_viewed",
    "activation_attempted",
    "launch_clicked",
    "subscribe_link_clicked",
    "trial_exhausted_viewed",
    "template_selected",
    "generation_started",
    "generation_completed",
    "generation_failed",
    "generation_cancelled",
    "first_generation_milestone",
    "favorite_toggled",
    "recent_selected",
    "markup_opened",
    "vectorize_panel_opened",
    "vectorize_suggestion_clicked",
    "vectorize_completed",
    "swipe_armed",
    "swipe_disarmed",
    # Refund visibility — without these the 199 WRITE_ERROR / 11 DOWNLOAD_ERROR
    # billing-bleed bug stays invisible.
    "generation_refund_attempted",
    "generation_refund_failed",
    # One-click connect onboarding. These fire pre-activation, so they sit in
    # _pending_pre_auth until the first authenticated flush drains them. Server
    # allow-list mirrored in terralab-website plugin/track route.
    "ai_edit_pair_started",
    "ai_edit_pair_succeeded",
    "ai_edit_pair_failed",
    "ai_edit_pair_timeout",
    "ai_edit_pair_cancelled",
})


class _TelemetryFlushTask(QgsTask):
    """Sends one batch. Failures swallowed: telemetry must never break the plugin."""

    def __init__(self, client, events: list, auth: dict):
        super().__init__("AI Edit telemetry flush", QgsTask.Flag.CanCancel)
        self._client = client
        self._events = events
        self._auth = auth

    def run(self) -> bool:
        # One retry with a short backoff covers a transient network blip without
        # a disk queue; a hard-offline session still loses the batch (accepted).
        if self.isCanceled():
            return False
        try:
            self._client.send_telemetry_batch(self._events, self._auth)
        except Exception:  # nosec B110
            if self.isCanceled():
                return False
            import time
            time.sleep(2)
            if self.isCanceled():
                return False
            try:
                self._client.send_telemetry_batch(self._events, self._auth)
            except Exception:  # nosec B110
                pass
        return True

    def finished(self, result: bool) -> None:
        return


class TelemetryCollector:
    def __init__(self, client, auth_manager, plugin_version: str = ""):
        self._client = client
        self._auth_manager = auth_manager
        self._plugin_version = plugin_version
        self._batch: list = []
        # Pre-auth lifecycle events parked here until first authenticated flush
        # drains them, so the activation funnel stays observable. Capped to 50.
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

    def track(self, event: str, properties: Optional[dict] = None):
        evt = {
            "event": event,
            "timestamp": self._now_iso(),
            "properties": {
                **self._session_props,
                **(properties or {}),
            },
        }
        self._batch.append(evt)

    def flush(self):
        """Non-blocking. Lifecycle events ship pre-consent; everything else
        requires consent. Pre-auth events queue in _pending_pre_auth."""
        if not self._batch and not self._pending_pre_auth:
            return

        if not self._has_auth():
            for evt in self._batch:
                if evt["event"] in _NO_CONSENT_EVENTS and len(self._pending_pre_auth) < 50:
                    self._pending_pre_auth.append(evt)
            self._batch.clear()
            return

        consented = self._has_consent()
        events_to_send = list(self._pending_pre_auth) + [
            e for e in self._batch
            if consented or e["event"] in _NO_CONSENT_EVENTS
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
        try:
            task.taskCompleted.connect(lambda t=task: self._drop_inflight(t))
            task.taskTerminated.connect(lambda t=task: self._drop_inflight(t))
        except Exception:  # nosec B110
            pass
        QgsApplication.taskManager().addTask(task)

    def _drop_inflight(self, task: "_TelemetryFlushTask") -> None:
        try:
            self._inflight.remove(task)
        except ValueError:
            pass

    def shutdown(self):
        for task in list(self._inflight):
            try:
                task.cancel()
            except Exception:  # nosec B110
                pass
        self._inflight.clear()


_collector: Optional[TelemetryCollector] = None


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


def track(event: str, properties: Optional[dict] = None):
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
