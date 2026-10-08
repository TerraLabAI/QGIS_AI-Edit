from __future__ import annotations

import time

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QSettings

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import (
    ACTIVATION_TIMESTAMP_KEY,
    PLUGIN_UTM_QUERY,
    SIGNED_IN_BEFORE_KEY,
    clear_activation,
    get_activation_key,
    get_dashboard_url,
    get_server_url,
    save_activation,
    validate_key_with_server,
)
from ...core.auth.pairing_listener import PairingListener
from ...core.auth.pairing_v2 import (
    claim_pairing_key,
    make_pairing_secret,
    normalize_user_code,
    pairing_secret_hash,
    start_pairing_session,
)
from ...core.config_store import get_export_copy, get_export_dial
from ...core.errors import NETWORK_ERROR_CODES, PLAN_STOPPED_CODES, TRANSIENT_SERVER_ERROR_CODES
from ...core.i18n import tr
from ...core.logger import log, log_debug, log_warning
from ...core.product_identity import PRODUCT_ID
from ...core.prompts import conversation_thumbs
from ...core.window_focus import bring_qgis_window_to_front
from ...workers.generic_request_task import GenericRequestTask
from ...workers.pairing_poll_task import PairingPollTask, pairing_status_failure
from ..canvas_exporter import has_tuned_config
from ..external_url import open_external
from .errors import (
    _enrich_error_message,
    dashboard_error_url,
    network_next_step,
    subscribe_error_url,
)
from .lifecycle import REQUEST_TASK_SIGNALS, drain_task



_KEY_REVALIDATION_WINDOW_S = 900


_PLAN_RETURN_WINDOW_S = 7200
_PLAN_RETURN_GAP_S = 10.0

_TASKBAR_FLASH_MS = 3000


_FALLBACK_CODE_AFTER_MS = 15_000


def _sign_in_ended_message() -> str:
    return tr("This sign-in can no longer be used. Click Sign in to start again.")


def _key_validation_request(client, key):




    success, message, code, usage = validate_key_with_server(client, key)
    if success:
        return usage if isinstance(usage, dict) else {}
    return {"error": message, "code": code}


class ActivationMixin:
    def _check_activation_state(self, validate: bool = True):









        self._start_sibling_sign_in()
        settings = QSettings()
        saved_key = get_activation_key(settings)
        if not saved_key:
            from ...core.auth.auth_helper import has_stored_activation

            if has_stored_activation(settings):




                log_warning("Stored activation key could not be read; signed out for this session")
            else:
                clear_activation(settings)
            self._auth_manager.set_activation_key("")
            self._dock_widget.set_activated(False)
            self._last_key_validation_unix = 0.0
            return

        self._auth_manager.set_activation_key(saved_key)





        if (time.time() - self._last_key_validation_unix) < get_export_dial(
            "flows.activation.key_revalidation_window_s", _KEY_REVALIDATION_WINDOW_S
        ):
            self._dock_widget.set_activated(True)

            return





        self._dock_widget.set_activated(True)
        self._dock_widget.set_launch_enabled(False)

        if not validate:
            return

        self._key_validation_worker = GenericRequestTask(
            "AI Edit key validation",
            lambda c=self._client, k=saved_key: _key_validation_request(c, k),
            silent=True,
        )
        self._key_validation_worker.succeeded.connect(self._on_key_valid)
        self._key_validation_worker.failed.connect(self._on_key_invalid)
        QgsApplication.taskManager().addTask(self._key_validation_worker)

    def _on_key_valid(self, usage=None):





        if self._dock_widget is None or not self._auth_manager.has_activation_key():
            return

        self._connectivity_notice_shown = False
        self._last_key_validation_unix = time.time()
        self._dock_widget.set_activated(True)


        if not has_tuned_config():
            self._refresh_tuned_config()
        self._refresh_catalog_after_sign_in()


        if not getattr(self, "_activation_config_keyed", False):
            self._warm_activation_config()



        self._refresh_conversations_cache(reuse_recent=True)

        if isinstance(usage, dict) and "images_used" in usage:
            self._auth_manager.seed_usage(usage)
            self._on_credits_loaded(usage)
        else:
            self._dock_widget.set_checking_credits(True)
            self._refresh_credits()

    def _on_key_invalid(self, message: str, code: str):











        if self._dock_widget is None:
            return
        code_up = (code or "").strip().upper()


        if (
            code_up in NETWORK_ERROR_CODES
            or code_up in TRANSIENT_SERVER_ERROR_CODES
            or code_up == "NO_CONNECTION"
        ):
            self._dock_widget.set_activated(True)


            self._dock_widget.set_launch_enabled(True)
            self._show_connectivity_notice(code, message)
            return



        rejection_code = (code or "").strip().upper() or "KEY_REJECTED"
        telemetry.track(te.PLUGIN_ERROR, {
            "stage": "activate",
            "error_code": rejection_code,
        })
        telemetry.flush()
        if rejection_code == "DEVICE_LIMIT_EXCEEDED":



            self._dock_widget.set_activated(True)
            self._dock_widget.set_launch_enabled(True)
            self._dock_widget.set_status(
                _enrich_error_message(message, rejection_code), is_error=True
            )
            return



        self._on_sign_out()
        if rejection_code in PLAN_STOPPED_CODES:


            self._dock_widget.set_activation_message_link(
                message,
                get_export_copy("flows.activation.inactive_plan_link", tr("Open my dashboard")),
                dashboard_error_url(),
            )
        else:
            self._dock_widget.set_activation_message(message, is_error=True)

    def _on_settings_clicked(self):






        self._disarm_swipe()
        from ..dialogs.account_settings_dialog import AccountSettingsDialog

        signed_in = self._auth_manager.has_activation_key()
        dlg = AccountSettingsDialog(
            client=self._client,
            auth=self._auth_manager.get_auth_header() if signed_in else {},
            activation_key=self._auth_manager.get_activation_key() if signed_in else "",
            parent=self._iface.mainWindow(),
            signed_in=signed_in,
        )
        dlg.sign_in_requested.connect(self._ensure_dock_widget)
        dlg.sign_out_requested.connect(self._on_sign_out)
        dlg.account_deleted.connect(self._on_account_deleted)
        dlg.usage_loaded.connect(self._on_account_usage_loaded)



        dock = self._dock_widget
        if dock is not None:
            dock.set_settings_button_active(True)
        try:
            dlg.exec()
        finally:
            if dock is not None:
                try:
                    dock.set_settings_button_active(False)
                except RuntimeError:
                    pass  # nosec B110

            dlg.deleteLater()

    def _on_account_usage_loaded(self, usage: dict):



        if self._dock_widget is None or not self._auth_manager.has_activation_key():
            return
        self._auth_manager.seed_usage(usage)
        self._on_credits_loaded(usage)

    def _on_sign_out(self):



        advance_history = getattr(self, "_advance_history_account_revision", None)
        if callable(advance_history):
            advance_history()
        self._last_key_validation_unix = 0.0





        for attr in (
            "_key_validation_worker",
            "_credits_loader",
            "_tuned_config_task",
            "_activation_config_loader",
            "_catalog_loader",
            "_bootstrap_task",
        ):
            drain_task(getattr(self, attr, None), REQUEST_TASK_SIGNALS)
            setattr(self, attr, None)


        self._drop_config_gate_waiter()
        cancel_history = getattr(self, "_cancel_history_tasks", None)
        if callable(cancel_history):
            cancel_history()
        clear_activation()
        self._auth_manager.set_activation_key("")




        self._clear_local_conversations()
        self._dock_widget.set_activated(False)


        try:
            from ...core.prompts.prompt_presets_client import drop_signed_in_prompts

            self._dock_widget.set_server_catalog(drop_signed_in_prompts())
        except Exception as err:  # nosec B110
            log_debug(f"Signed-in prompts not dropped: {err}")
        self._load_server_catalog(force=True)


        self._activation_config_keyed = False
        self._warm_activation_config()



        self._load_export_config()
        self._dock_widget.refresh_free_plan_line()
        log_debug("Signed out")

    def _on_account_deleted(self):







        self._on_sign_out()
        conversation_thumbs.clear_thumbs()
        log_debug("Account deletion scheduled: local data cleared")

    def _apply_activation(self, key: str):






        advance_history = getattr(self, "_advance_history_account_revision", None)
        if callable(advance_history):
            advance_history()
        save_activation(key)
        self._auth_manager.set_activation_key(key)
        self._dock_widget.set_activated(True)
        self._dock_widget.set_activation_message(
            get_export_copy("flows.activation.key_verified", tr("Signed in.")),
            is_error=False,
        )

        self._dock_widget.set_checking_credits(True)
        self._refresh_credits()





        self._refresh_tuned_config()
        self._warm_activation_config()
        self._refresh_catalog_after_sign_in()



        self._refresh_conversations_cache()

        settings = QSettings()



        self._returning_sign_in = bool(
            settings.value(ACTIVATION_TIMESTAMP_KEY, "", type=str)
        ) or settings.value(SIGNED_IN_BEFORE_KEY, False, type=bool)
        settings.setValue(SIGNED_IN_BEFORE_KEY, True)
        if not settings.value(ACTIVATION_TIMESTAMP_KEY, "", type=str):
            settings.setValue(ACTIVATION_TIMESTAMP_KEY, str(int(time.time())))

    def _start_sibling_sign_in(self):
        from ...core import sibling_sign_in
        try:
            from ...core.device_id import get_device_hash
            device = get_device_hash()
        except Exception:  # noqa: BLE001
            device = ""
        sibling_sign_in.start(PRODUCT_ID, self._client.base_url, device, self._on_sibling_sign_in)

    def _on_sibling_sign_in(self, result: dict):






        if not result.get("ok") or self._dock_widget is None or self._auth_manager.has_activation_key():
            return
        if self._pairing_worker is not None and self._pairing_worker.is_active():
            return
        key = str(result.get("key") or "")
        from ...core import sibling_sign_in
        if not sibling_sign_in.KEY_RE.match(key):
            return
        self._apply_activation(key)
        email, label = str(result.get("email") or ""), str(result.get("label") or "")
        message = (tr("Signed in as {} (from {}).").format(email, label) if email
                   else tr("Signed in (from {}).").format(label))
        self._dock_widget.set_activation_message(message, is_error=False)
        try:
            from qgis.core import Qgis
            self._iface.messageBar().pushMessage("AI Edit", message, level=Qgis.MessageLevel.Success, duration=10)
        except (RuntimeError, AttributeError):  # nosec B110
            pass
        telemetry.track(te.PLUGIN_ACTIVATED, {"activation_method": "sibling"})
        telemetry.flush()
        log(f"Signed in with the account of {label}")

    def _cancel_pairing_worker(self):

        if self._pairing_worker is not None and self._pairing_worker.is_active():
            try:
                self._pairing_worker.cancel_by_plugin()
            except Exception:  # nosec B110
                pass

    def _on_pairing_task_terminated(self, worker):



        if not worker.isCanceled() or worker.cancelled_by_plugin:
            return
        if self._pairing_worker is not worker:
            return
        self._pairing_worker = None
        self._retire_pairing_codes()
        self._end_pairing_v2()
        if self._dock_widget is None:
            return
        self._dock_widget.show_pairing_idle()
        telemetry.track(te.AI_EDIT_PAIR_CANCELLED, {
            "duration_ms": self._pairing_duration_ms(),
            "stalled": bool(getattr(self, "_pairing_stalled", False)),
        })
        telemetry.flush()
        log("Pairing cancelled from the task manager")

    def _on_pairing_network_problem(self, code: str):
        if self._dock_widget:
            self._dock_widget.show_pairing_network_problem(network_next_step(code))
        log_warning(f"Pairing poll keeps failing on the network ({code})")



    def _on_pairing_requested(self, code: str):








        attempts = self._pairing_attempts
        attempt = attempts.get(code)
        if attempt is not None:
            if attempt.get("url"):
                self._begin_pairing_poll(attempt.get("code") or code, attempt["url"])
            return
        if not self._pairing_wait_running():
            self._end_pairing_v2()
            attempts = self._pairing_attempts
        attempt = {"state": "starting", "url": "", "code": "", "secret": make_pairing_secret()}
        attempts[code] = attempt
        port = self._ensure_pairing_listener()
        secret_hash = pairing_secret_hash(attempt["secret"])
        task = GenericRequestTask(
            get_export_copy("pipeline.pairing_poll_task.connecting", tr("Connecting AI Edit")),
            lambda c=self._client, h=secret_hash, p=port: start_pairing_session(c, h, p or None),
            silent=True,
        )
        task.succeeded.connect(lambda result, dc=code, a=attempt, p=port: self._on_pairing_started(dc, a, result, p))
        task.failed.connect(
            lambda _m, _c, dc=code, a=attempt: self._on_pairing_started(dc, a, {"outcome": "legacy"}, 0)
        )
        self._hold_history_task(task)

    def _pairing_wait_running(self) -> bool:
        worker = self._pairing_worker
        if worker is not None and worker.is_active():
            return True
        return any(a.get("state") == "starting" for a in self._pairing_attempts.values())

    def _ensure_pairing_listener(self) -> int:

        listener = self._pairing_listener
        if listener is None:
            listener = PairingListener()
            listener.grant_received.connect(self._on_pairing_grant)
            self._pairing_listener = listener
        return listener.start()

    def _on_pairing_started(self, dock_code: str, attempt: dict, result, port: int):

        result = result if isinstance(result, dict) else {"outcome": "legacy"}
        outcome = result.get("outcome")
        if self._pairing_attempts.get(dock_code) is not attempt or self._dock_widget is None:

            if outcome == "started":
                self._pairing_codes = [result["code"]]
                self._retire_pairing_codes()
            return
        if outcome == "cancelled":
            return
        from ...core.device_id import get_device_hash

        if outcome == "started":
            server_code = result["code"]
            attempt.update(state="v2", code=server_code)
            self._pairing_secrets[server_code] = attempt["secret"]
            if self._pairing_listener is not None and port:
                self._pairing_listener.add_code(server_code)

            base = result["connect_url"]
            attempt["url"] = f"{base}{'&' if '?' in base else '?'}device_id={get_device_hash()}"
            started = self._begin_pairing_poll(server_code, attempt["url"], total_s=result.get("expires_in"))
            if started and not port:

                self._pairing_code_target = server_code
                self._show_pairing_code_input()
            log("Pairing started (v2)")
            return
        log_warning(f"Sign-in v2 unavailable ({result.get('reason') or 'start failed'}); using the previous flow")
        attempt.update(state="legacy", code=dock_code)
        if not self._pairing_secrets and self._pairing_listener is not None:

            self._pairing_listener.stop()



        attempt["url"] = (
            f"{self._client.base_url}/connect?code={dock_code}&product={PRODUCT_ID}"
            f"&device_id={get_device_hash()}"
            f"&{PLUGIN_UTM_QUERY}&utm_content=connect"
        )
        self._begin_pairing_poll(dock_code, attempt["url"])

    def _begin_pairing_poll(self, code: str, url: str, total_s=None) -> bool:









        self._dock_widget.set_pairing_link(url)

        opened = open_external(url)
        worker = self._pairing_worker
        if worker is not None and worker.is_active() and worker.add_code(code):




            if code not in self._pairing_codes:
                self._pairing_codes.append(code)
            if not opened:
                self._dock_widget.show_pairing_hint(get_export_copy(
                    "flows.activation.browser_open_failed",
                    tr("Couldn't open your browser. Copy the link and open it manually."),
                ))
            return True
        if not opened:
            if code in self._pairing_secrets:
                self._pairing_codes = [code]
                self._retire_pairing_codes()
            self._end_pairing_v2()
            self._dock_widget.show_pairing_idle()
            self._dock_widget.set_activation_message(
                get_export_copy(
                    "flows.activation.browser_open_failed",
                    tr("Couldn't open your browser. Copy the link and open it manually."),
                ),
                is_error=True,
            )
            return False

        worker = PairingPollTask(self._client, code, total_timeout_s=total_s)
        self._pairing_worker = worker
        self._pairing_codes = [code]
        worker.pairing_succeeded.connect(lambda key, w=worker: self._on_pairing_succeeded(key, w))
        worker.pairing_failed.connect(
            lambda message, err, w=worker: None if self._pairing_superseded(w)
            else self._on_pairing_failed(message, err)
        )
        worker.pairing_timeout.connect(
            lambda w=worker: None if self._pairing_superseded(w) else self._on_pairing_timeout()
        )
        worker.pairing_browser_seen.connect(self._on_pairing_browser_seen)
        worker.pairing_stalled.connect(self._on_pairing_stalled)
        worker.pairing_network_problem.connect(self._on_pairing_network_problem)
        worker.pairing_confirmed.connect(self._on_pairing_confirmed)
        worker.taskTerminated.connect(lambda w=worker: self._on_pairing_task_terminated(w))
        QgsApplication.taskManager().addTask(worker)



        self._pairing_started_unix = time.time()
        self._pairing_stalled = False
        telemetry.track(te.AI_EDIT_PAIR_STARTED)
        telemetry.flush()
        log("Pairing started")
        return True



    def _on_pairing_confirmed(self, code: str):


        if code not in self._pairing_secrets:
            return
        self._pairing_code_target = code
        if self._pairing_fallback_timer is None and self._dock_widget is not None:
            delay_ms = get_export_dial("pairing.fallback_code_ms", _FALLBACK_CODE_AFTER_MS)
            self._pairing_fallback_timer = QtC.safe_single_shot(
                delay_ms, self._dock_widget, self._show_pairing_code_input
            )

    def _show_pairing_code_input(self):
        self._pairing_fallback_timer = None
        if self._pairing_code_target in self._pairing_secrets and self._dock_widget is not None:
            self._dock_widget.show_pairing_code_input()

    def _on_pairing_grant(self, request_id: int, code: str, grant: str):

        secret = self._pairing_secrets.get(code)
        if not secret:
            if self._pairing_listener is not None:
                self._pairing_listener.answer(request_id, "")
            return
        self._run_pairing_claim(code, secret, grant=grant, request_id=request_id)

    def _on_pairing_code_entered(self, text: str):

        if self._dock_widget is None:
            return
        code = self._pairing_code_target
        secret = self._pairing_secrets.get(code)
        if not secret:
            self._dock_widget.show_pairing_code_error(_sign_in_ended_message())
            return
        user_code = normalize_user_code(text)
        if not user_code:
            self._dock_widget.show_pairing_code_error(tr("Wrong code"))
            return
        self._run_pairing_claim(code, secret, user_code=user_code)

    def _run_pairing_claim(self, code: str, secret: str, grant: str = "", user_code: str = "",
                           request_id: int = 0):

        typed = bool(user_code)
        claims = self._pairing_claims_in_flight
        claims[code] = claims.get(code, 0) + 1
        task = GenericRequestTask(
            get_export_copy("pipeline.pairing_poll_task.connecting", tr("Connecting AI Edit")),
            lambda c=self._client: claim_pairing_key(c, code, secret, grant=grant, user_code=user_code),
            silent=True,
        )
        task.succeeded.connect(lambda result: self._on_pairing_claimed(code, result, request_id, typed))
        task.failed.connect(
            lambda _m, _c: self._on_pairing_claimed(code, {"outcome": "retry_later"}, request_id, typed)
        )
        self._hold_history_task(task)

    def _on_pairing_claimed(self, code: str, result, request_id: int, typed: bool):
        listener = self._pairing_listener
        outcome = result.get("outcome") if isinstance(result, dict) else "retry_later"
        live = code in self._pairing_secrets and self._dock_widget is not None
        claims = self._pairing_claims_in_flight
        others_pending = max(0, claims.get(code, 0) - 1)
        if others_pending:
            claims[code] = others_pending
        else:
            claims.pop(code, None)
        if outcome == "ready" and live:
            if listener is not None and request_id:
                listener.answer(request_id, f"{self._client.base_url}/connect/done?product={PRODUCT_ID}")
            self._cancel_pairing_worker()
            self._on_pairing_succeeded(result["key"], won_code=code)
            return
        if listener is not None and request_id:
            listener.answer(request_id, "")
        if not live:
            return
        if outcome == "not_found" and others_pending:



            if typed:
                self._pairing_typed_unanswered.add(code)
            return
        typed_unanswered = code in self._pairing_typed_unanswered and not others_pending
        if typed_unanswered:
            self._pairing_typed_unanswered.discard(code)
        if outcome in ("no_plan", "cancelled"):
            self._end_pairing_with_failure(*pairing_status_failure(outcome))
        elif outcome in ("locked", "not_found"):
            self._end_pairing_with_failure(_sign_in_ended_message(), outcome.upper())
        elif typed and outcome == "invalid":
            self._dock_widget.show_pairing_code_error(tr("Wrong code"))
        elif typed or typed_unanswered:


            self._dock_widget.show_pairing_code_error(tr("Could not check the code. Try again."))



    def _end_pairing_with_failure(self, message: str, code: str):
        self._cancel_pairing_worker()
        self._on_pairing_failed(message, code)

    def _end_pairing_v2(self):


        timer = self._pairing_fallback_timer
        self._pairing_fallback_timer = None
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except RuntimeError:  # nosec B110
                pass
        listener = self._pairing_listener
        self._pairing_listener = None
        if listener is not None:
            listener.stop()
            listener.deleteLater()
        self._pairing_secrets = {}
        self._pairing_attempts = {}
        self._pairing_claims_in_flight = {}
        self._pairing_typed_unanswered = set()
        self._pairing_code_target = ""

    def _pairing_superseded(self, worker) -> bool:


        current = self._pairing_worker
        return current is not None and current is not worker and current.is_active()

    def _retire_pairing_codes(self, keep: str = "") -> None:



        codes = tuple(c for c in getattr(self, "_pairing_codes", []) if c and c != keep)
        self._pairing_codes = []
        if not codes:
            return

        def retire(c=self._client, codes=codes):
            result = {}
            for one in codes:
                result = c.cancel_pairing(one)
            return result if isinstance(result, dict) else {}

        task = GenericRequestTask(
            get_export_copy("flows.activation.cancelling_sign_in", tr("Cancelling sign-in")),
            retire,
            silent=True,
        )
        self._hold_history_task(task)

    def _end_pairing_on_unload(self) -> None:


        self._end_pairing_v2()
        worker = self._pairing_worker
        if worker is None or not worker.is_active():
            return
        telemetry.track(te.AI_EDIT_PAIR_FAILED, {
            "error_code": "PLUGIN_UNLOADED",
            "duration_ms": self._pairing_duration_ms(),
            "stalled": bool(getattr(self, "_pairing_stalled", False)),
        })
        telemetry.flush()

    def _pairing_duration_ms(self) -> int:

        start = getattr(self, "_pairing_started_unix", 0.0)
        if not start:
            return 0
        return int((time.time() - start) * 1000)

    def _on_pairing_succeeded(self, key: str, worker=None, won_code: str = ""):
        if worker is not None and self._pairing_worker is not worker:


            self._cancel_pairing_worker()
        self._retire_pairing_codes(keep=won_code or getattr(worker, "won_code", ""))
        self._end_pairing_v2()
        self._apply_activation(key)


        try:
            bring_qgis_window_to_front(
                self._iface.mainWindow(),
                self._dock_widget,
                get_export_dial("pipeline.window_focus.taskbar_flash_ms", _TASKBAR_FLASH_MS),
            )
        except Exception:  # nosec B110
            pass
        telemetry.track(te.AI_EDIT_PAIR_SUCCEEDED, {
            "duration_ms": self._pairing_duration_ms(),
            "stalled": bool(getattr(self, "_pairing_stalled", False)),
        })

        telemetry.track(te.ACTIVATION_ATTEMPTED, {"success": True})
        telemetry.track(te.PLUGIN_ACTIVATED, {"activation_method": "pairing"})
        telemetry.flush()
        log("Pairing successful")



        QtC.safe_single_shot(0, self._dock_widget, self._auto_load_example_after_signup)

    def _on_pairing_failed(self, message: str, code: str):
        self._retire_pairing_codes()
        self._end_pairing_v2()
        self._dock_widget.show_pairing_idle()
        self._dock_widget.set_activation_message(message, is_error=True)
        telemetry.track(te.AI_EDIT_PAIR_FAILED, {
            "error_code": (code or "UNKNOWN"),
            "duration_ms": self._pairing_duration_ms(),
            "stalled": bool(getattr(self, "_pairing_stalled", False)),
        })
        telemetry.track(te.ACTIVATION_ATTEMPTED, {"success": False})
        telemetry.flush()
        log_warning("Pairing failed")

    def _on_pairing_browser_seen(self):
        if self._dock_widget:
            self._dock_widget.show_pairing_browser_seen()

    def _on_pairing_stalled(self):
        self._pairing_stalled = True
        if self._dock_widget:
            self._dock_widget.show_pairing_stalled_hint()
        log_warning("Pairing stalled: browser never reached /connect")

    def _on_pairing_timeout(self):
        self._retire_pairing_codes()
        self._end_pairing_v2()
        self._dock_widget.show_pairing_idle()
        self._dock_widget.set_activation_message(
            get_export_copy(
                "flows.activation.pairing_timed_out",
                tr("Sign-in timed out. Click Sign in to try again."),
            ),
            is_error=True,
        )
        telemetry.track(te.AI_EDIT_PAIR_TIMEOUT, {
            "duration_ms": self._pairing_duration_ms(),
            "stalled": bool(getattr(self, "_pairing_stalled", False)),
        })
        telemetry.flush()
        log("Pairing timed out")

    def _on_cancel_pairing(self, code: str = ""):
        self._cancel_pairing_worker()



        attempt = self._pairing_attempts.get(code) if code else None
        if attempt is not None:
            code = attempt.get("code") or ""
        if code and code not in self._pairing_codes:
            self._pairing_codes.append(code)
        self._retire_pairing_codes()
        self._end_pairing_v2()
        telemetry.track(te.AI_EDIT_PAIR_CANCELLED, {
            "duration_ms": self._pairing_duration_ms(),
            "stalled": bool(getattr(self, "_pairing_stalled", False)),
        })
        telemetry.flush()
        log("Pairing cancelled")

    def _watch_plan_return(self) -> None:

        app = QgsApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state_changed)

    def _unwatch_plan_return(self) -> None:
        QtC.safe_disconnect(QgsApplication.instance(), "applicationStateChanged", self._on_app_state_changed)

    def _on_app_state_changed(self, state) -> None:




        if state != QtC.ApplicationActive:
            return
        if self._dock_widget is None or not self._auth_manager.has_activation_key():
            return
        from ..pro_page_link import last_opened_unix

        now = time.time()
        opened = last_opened_unix()
        if not opened or now - opened > get_export_dial("flows.activation.plan_return_window_s", _PLAN_RETURN_WINDOW_S):
            return
        if now - getattr(self, "_plan_return_read_unix", 0.0) < _PLAN_RETURN_GAP_S:
            return
        self._plan_return_read_unix = now
        log_debug("QGIS back in front after the plans page: reading the plan again")
        self._refresh_credits()

    def _refresh_credits(self):

        self._credits_loader = GenericRequestTask(
            "AI Edit credits",
            self._auth_manager.get_usage_info,
            silent=True,
        )
        self._credits_loader.succeeded.connect(self._on_credits_loaded)
        self._credits_loader.failed.connect(lambda _msg, _code: self._on_credits_failed())
        QgsApplication.taskManager().addTask(self._credits_loader)

    def _on_credits_failed(self):

        if self._dock_widget:
            self._dock_widget.set_checking_credits(False)
            self._dock_widget.set_launch_enabled(True)

    def _refresh_price_displays(self):



        if self._dock_widget is None or not self._auth_manager.has_activation_key():
            return
        try:
            usage = self._auth_manager._fresh_cached_usage()
        except Exception:  # nosec B110
            usage = None
        if isinstance(usage, dict) and "images_used" in usage:
            self._on_credits_loaded(usage)

    def _on_credits_loaded(self, usage: dict):

        if self._dock_widget and self._auth_manager.has_activation_key():
            self._dock_widget.set_checking_credits(False)
            self._dock_widget.set_launch_enabled(True)
            used = usage.get("images_used")
            limit = usage.get("images_limit")
            is_free = usage.get("is_free_tier", False)



            reset_date = usage.get("reset_date") or usage.get("period_end")


            if is_free:



                self._dock_widget.set_subscribe_url(
                    get_server_url("upgrade_url", get_dashboard_url())
                )
            self._dock_widget.set_credits(
                used=used,
                limit=limit,
                is_free_tier=is_free,
                reset_date=reset_date if isinstance(reset_date, str) else None,
            )


            both_ints = isinstance(used, int) and isinstance(limit, int)
            if both_ints and limit > 0 and used >= limit and not is_free:
                self._dock_widget.show_usage_limit_info(
                    tr("Monthly limit reached ({used}/{limit}).").format(used=used, limit=limit),
                    subscribe_error_url(),
                )
            elif both_ints and not is_free:


                from ..pro_page_link import forget_opened

                forget_opened()
