"""Lightweight telemetry for AI Edit plugin.

Events are batched in memory and flushed in a single HTTP call at the
end of each generation cycle (success, failure, or cancel). This keeps
overhead to 1 extra request per generation.

Rules:
- Only sends when the user has given consent (privacy checkbox)
- Only sends when an activation key is set (authenticated)
- No PII is collected: no emails, no prompts, no image data
- All data is aggregate/numerical: durations, error codes, resolutions
- Errors in telemetry never affect plugin functionality (fail silently)
"""

import platform
import time
from typing import Optional

from qgis.PyQt.QtCore import QThread

# Events whose payload carries no user-generated content (no prompts, no
# image bytes, no paths, no coords). These ship as soon as the plugin is
# activated. Properties limited to: plugin_version, OS, QGIS version, durations,
# error_codes, resolution string, prompt_length (a count, not the text).
#
# Only `plugin_error` stays gated by ToS acceptance because its error_message
# field carries raw exception text that can include paths or fragments.
_NO_CONSENT_EVENTS = frozenset({
    "plugin_opened",
    "plugin_activated",
    "activation_screen_viewed",
    "activation_attempted",
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
})


class TelemetryFlushWorker(QThread):
    """Sends one telemetry batch without blocking the QGIS UI thread."""

    def __init__(self, client, events: list, auth: dict):
        super().__init__()
        self._client = client
        self._events = events
        self._auth = auth

    def run(self):
        try:
            self._client.send_telemetry_batch(self._events, self._auth)
        except Exception:
            # Telemetry must never break plugin functionality
            pass  # nosec B110


class TelemetryCollector:
    """Collects telemetry events and flushes them as a batch."""

    def __init__(self, client, auth_manager, plugin_version: str = ""):
        self._client = client
        self._auth_manager = auth_manager
        self._plugin_version = plugin_version
        self._batch: list = []
        # Pre-auth lifecycle events (activation_screen_viewed, plugin_opened,
        # activation_attempted) are queued here until the user enters a key.
        # First successful flush() drains the queue so the activation funnel
        # is recoverable. Capped to avoid unbounded growth if the user never
        # activates.
        self._pending_pre_auth: list = []
        self._workers: list = []
        self._session_props = self._build_session_props()

    def _build_session_props(self) -> dict:
        """Static properties sent with every event (computed once)."""
        import sys

        try:
            from qgis.core import Qgis
            qgis_version = Qgis.version()
        except Exception:
            qgis_version = "unknown"

        return {
            "plugin_version": self._plugin_version,
            "os": platform.system(),
            "os_version": platform.release(),
            "arch": platform.machine(),
            "python_version": sys.version.split()[0],
            "qgis_version": qgis_version,
        }

    def _has_auth(self) -> bool:
        auth = self._auth_manager.get_auth_header()
        return bool(auth and auth.get("Authorization"))

    def _has_consent(self) -> bool:
        from .activation_manager import has_consent
        return has_consent()

    def track(self, event: str, properties: Optional[dict] = None):
        """Add an event to the batch. Will be sent on next flush()."""
        evt = {
            "event": event,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "properties": {
                **self._session_props,
                **(properties or {}),
            },
        }
        self._batch.append(evt)

    def flush(self):
        """Send all batched events to the server. Non-blocking on failure.

        Lifecycle events (`_NO_CONSENT_EVENTS`) ship as long as the plugin is
        activated; everything else additionally requires telemetry consent.
        Events emitted before activation (no Bearer token yet) are parked in
        `_pending_pre_auth` and drained on the first authenticated flush so
        the activation_screen_viewed → plugin_activated funnel is observable.
        """
        if not self._batch and not self._pending_pre_auth:
            return

        if not self._has_auth():
            # Park lifecycle events so they reach PostHog once the user
            # activates. Drop everything else (consent-gated paths shouldn't
            # be reachable pre-auth anyway). Cap at 50 to avoid runaway
            # memory if the user opens the plugin many times without ever
            # activating.
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
        worker = TelemetryFlushWorker(self._client, events_to_send, auth)
        self._workers.append(worker)
        worker.finished.connect(lambda: self._on_worker_finished(worker))
        worker.start()

    def _on_worker_finished(self, worker):
        try:
            self._workers.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()


# Module-level singleton (set by plugin.py on init)
_collector: Optional[TelemetryCollector] = None


def init_telemetry(client, auth_manager, plugin_version: str = ""):
    """Initialize the global telemetry collector."""
    global _collector
    _collector = TelemetryCollector(client, auth_manager, plugin_version)


def track(event: str, properties: Optional[dict] = None):
    """Queue a telemetry event. No-op if telemetry not initialized."""
    if _collector:
        _collector.track(event, properties)


def flush():
    """Flush all queued events. No-op if telemetry not initialized."""
    if _collector:
        _collector.flush()
