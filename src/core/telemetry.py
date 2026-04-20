








from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone

from qgis.core import QgsApplication, QgsTask
from qgis.PyQt.QtCore import QThread

from .privacy_notice import has_accepted_privacy_notice


def _os_props() -> dict:







    if sys.platform == "win32":
        try:
            from qgis.PyQt.QtCore import QSysInfo

            os_version = QSysInfo.productVersion()
        except Exception:
            os_version = ""
        arch = (
            os.environ.get("PROCESSOR_ARCHITEW6432")
            or os.environ.get("PROCESSOR_ARCHITECTURE", "")
        )
        return {"os": "Windows", "os_version": os_version, "arch": arch}
    try:
        uname = os.uname()
        return {"os": uname.sysname, "os_version": uname.release, "arch": uname.machine}
    except Exception:
        return {"os": "", "os_version": "", "arch": ""}


def _on_main_thread() -> bool:


    try:
        app = QgsApplication.instance()
        return app is not None and QThread.currentThread() == app.thread()
    except Exception:
        return False





_TELEMETRY_ENABLED_KEY = "TerraLab/telemetry_enabled"







_telemetry_enabled_memo: bool | None = None


def _read_telemetry_enabled() -> bool:



    try:
        from qgis.PyQt.QtCore import QSettings
        return bool(QSettings().value(_TELEMETRY_ENABLED_KEY, True, type=bool))
    except Exception:  # nosec B110
        return False


def is_telemetry_enabled() -> bool:




    global _telemetry_enabled_memo
    if _telemetry_enabled_memo is None:
        _telemetry_enabled_memo = _read_telemetry_enabled()
    return _telemetry_enabled_memo


def refresh_telemetry_enabled() -> bool:



    global _telemetry_enabled_memo
    _telemetry_enabled_memo = _read_telemetry_enabled()
    return _telemetry_enabled_memo


def set_telemetry_enabled(enabled: bool) -> None:

    global _telemetry_enabled_memo
    try:
        from qgis.PyQt.QtCore import QSettings
        QSettings().setValue(_TELEMETRY_ENABLED_KEY, bool(enabled))
    except Exception:  # nosec B110
        pass


    _telemetry_enabled_memo = bool(enabled)






_NO_CONTENT_EVENTS = frozenset({
    "plugin_opened",
    "plugin_activated",
    "activation_screen_viewed",
    "activation_attempted",
    "launch_clicked",


    "launch_blocked",

    "generate_blocked",
    "subscribe_link_clicked",
    "trial_exhausted_viewed",



    "plugin_update_prompt_shown",
    "plugin_update_prompt_clicked",


    "tutorial_opened",
    "template_selected",
    "generation_started",
    "generation_completed",
    "generation_failed",
    "generation_cancelled",
    "first_generation_milestone",
    "favorite_toggled",
    "recent_selected",

    "history_restored",
    "history_exported",
    "markup_opened",
    "vectorize_panel_opened",
    "vectorize_suggestion_clicked",
    "vectorize_completed",
    "swipe_armed",
    "swipe_disarmed",

    "generation_refund_attempted",
    "generation_refund_failed",



    "ai_edit_pair_started",
    "ai_edit_pair_succeeded",
    "ai_edit_pair_failed",
    "ai_edit_pair_timeout",
    "ai_edit_pair_cancelled",
})


class _TelemetryFlushTask(QgsTask):


    def __init__(self, client, events: list, auth: dict):
        from .qt_compat import silent_task_flags
        super().__init__("AI Edit telemetry flush", silent_task_flags())
        self._client = client
        self._events = events
        self._auth = auth
        from qgis.core import QgsFeedback
        self._feedback = QgsFeedback()

    def run(self) -> bool:
        if self.isCanceled():
            return False


        if not self._post() and not self.isCanceled():
            import time

            for _ in range(8):
                if self.isCanceled():
                    return False
                time.sleep(0.25)
            if self.isCanceled():
                return False
            self._post()
        return True

    def cancel(self) -> None:

        try:
            self._feedback.cancel()
        except Exception:  # nosec B110
            pass
        super().cancel()

    def _post(self) -> bool:




        try:
            from ..api.network_error_classifier import request_feedback
            with request_feedback(self._feedback):
                result = self._client.send_telemetry_batch(self._events, self._auth)
        except Exception:  # nosec B110
            return False
        return not (isinstance(result, dict) and result.get("error"))

    def finished(self, result: bool) -> None:
        return


class TelemetryCollector:
    def __init__(self, client, auth_manager, plugin_version: str = ""):
        self._client = client
        self._auth_manager = auth_manager
        self._plugin_version = plugin_version



        self._lock = threading.Lock()
        self._batch: list = []


        self._pending_pre_auth: list = []
        self._inflight: list[_TelemetryFlushTask] = []
        self._session_props = self._build_session_props()

    def _build_session_props(self) -> dict:
        try:
            from qgis.core import Qgis
            qgis_version = Qgis.version()
        except Exception:
            qgis_version = "unknown"

        props = {
            "plugin_version": self._plugin_version,
            **_os_props(),
            "python_version": sys.version.split()[0],
            "qgis_version": qgis_version,
        }


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

        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )

    def track(self, event: str, properties: dict | None = None):


        if not has_accepted_privacy_notice() or not is_telemetry_enabled():
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








        self._flush(synchronous=False)

    def _flush(self, *, synchronous: bool) -> None:






        if not _on_main_thread():
            return



        if not has_accepted_privacy_notice() or not refresh_telemetry_enabled():
            with self._lock:
                self._batch.clear()
                self._pending_pre_auth.clear()
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



            self._inflight.append(task)

        if synchronous:
            try:
                task.run()
            finally:
                self._drop_inflight(task)
            return


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





        with self._lock:
            stale = list(self._inflight)
        try:
            self._flush(synchronous=True)
        except Exception:  # nosec B110
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
