












from __future__ import annotations

from qgis.core import QgsApplication

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.config_store import get_config_origin, get_store
from ...core.errors import NETWORK_ERROR_CODES
from ...core.i18n import tr
from ...core.logger import log_debug
from ...workers.generic_request_task import GenericRequestTask


def _config_loading_text() -> str:
    return tr("Loading settings from the server...")


def _config_offline_text() -> str:
    return tr("Connect to the internet to load AI Edit settings.")


def _has_any_saved_config() -> bool:
    store = get_store()
    return bool(store is not None and store.has_server_export_config())


class ConfigGateWaiter:


    def __init__(
        self,
        feature: str,
        ready,
        resume,
        waiting_text: str | None = None,
        still_missing_text: str | None = None,
        failed_text=None,
        show_retry: bool = True,
    ):
        self.feature = feature
        self.ready = ready
        self.resume = resume
        self.waiting_text = waiting_text
        self.still_missing_text = still_missing_text

        self.failed_text = failed_text
        self.show_retry = show_retry



        self.auth = None


class ConfigGateMixin:
    def _require_config(self, waiter: ConfigGateWaiter) -> bool:



        try:
            if waiter.ready():
                return True
        except Exception:  # nosec B110
            pass
        source, _age = get_config_origin()
        signed_in = bool(self._auth_manager is not None and self._auth_manager.has_activation_key())
        telemetry.track(te.CONFIG_GATE_WAITED, {
            "feature": waiter.feature,
            "config_source": source,
            "signed_in": signed_in,
        })
        waiter.auth = self._config_gate_auth()
        self._config_gate_waiter = waiter
        self._start_config_refetch()
        self._show_config_gate_state(waiter.waiting_text or _config_loading_text(), waiter, is_error=False)
        return False

    def _config_gate_auth(self) -> dict:
        return (self._auth_manager.get_auth_header() if self._auth_manager else {}) or {}

    def _drop_config_gate_waiter(self) -> None:


        waiter = self._config_gate_waiter
        self._config_gate_waiter = None
        if waiter is not None and self._dock_widget is not None:
            self._dock_widget.set_status("")

    def _on_reference_config_needed(self) -> None:


        from ...core.config_store import has_keys

        keys = ("reference_encode.max_references", "entitlements.free_tier_max_references")
        self._require_config(ConfigGateWaiter(
            "reference_add",
            ready=lambda: has_keys(*keys),
            resume=lambda: self._dock_widget is not None and self._dock_widget.set_status(""),
        ))

    def _show_config_gate_state(self, text: str, waiter: ConfigGateWaiter, is_error: bool) -> None:
        dock = self._dock_widget
        if dock is None:
            return
        dock.set_status(text, is_error=is_error)
        if waiter.show_retry:
            dock.set_status_action(tr("Retry"), lambda w=waiter: self._retry_config_gate(w))

    def _retry_config_gate(self, waiter: ConfigGateWaiter) -> None:
        waiter.auth = self._config_gate_auth()
        self._config_gate_waiter = waiter
        self._start_config_refetch()
        self._show_config_gate_state(waiter.waiting_text or _config_loading_text(), waiter, is_error=False)

    def _start_config_refetch(self) -> None:


        if self._tuned_config_task is not None:
            return
        auth = self._auth_manager.get_auth_header() if self._auth_manager else {}
        task = GenericRequestTask(
            "AI Edit tuned config",
            lambda c=self._client, a=auth: c.get_bootstrap(a or None),
            silent=True,
        )
        task.succeeded.connect(lambda p, a=auth: self._on_refetch_answer(a, p))
        task.failed.connect(lambda m, c, a=auth: self._on_refetch_answer(a, None, (m, c)))
        self._tuned_config_task = task
        QgsApplication.taskManager().addTask(task)

    def _on_refetch_answer(self, auth, payload, failure=None) -> None:

        if not self._answer_key_current(auth, "settings"):
            self._tuned_config_task = None
            return
        if failure is not None:
            self._on_tuned_config_failed(*failure)
        else:
            self._on_tuned_config_loaded(payload)

    def _resume_config_gate(self) -> None:


        waiter = self._config_gate_waiter
        self._config_gate_waiter = None
        if waiter is None or self._dock_widget is None:
            return
        if (waiter.auth or {}) != self._config_gate_auth():
            log_debug(f"Config gate: {waiter.feature} parked under another account, not resumed")
            self._dock_widget.set_status("")
            return
        try:
            ready = bool(waiter.ready())
        except Exception:  # nosec B110
            ready = False
        if ready:
            waiter.resume()
            return
        self._show_config_gate_state(
            waiter.still_missing_text or _config_loading_text(),
            waiter,
            is_error=waiter.still_missing_text is not None,
        )

    def _fail_config_gate(self, message: str, code: str) -> None:
        waiter = self._config_gate_waiter
        self._config_gate_waiter = None
        if waiter is None or self._dock_widget is None:
            return
        log_debug(f"Config gate fetch failed for {waiter.feature} ({code}): {message}")
        if waiter.failed_text is not None:
            self._show_config_gate_state(waiter.failed_text(code), waiter, is_error=True)
            return
        offline = (code or "").strip().upper() in NETWORK_ERROR_CODES and not _has_any_saved_config()
        if offline:
            self._show_config_gate_state(_config_offline_text(), waiter, is_error=True)
        else:
            self._show_config_gate_state(_config_loading_text(), waiter, is_error=False)
