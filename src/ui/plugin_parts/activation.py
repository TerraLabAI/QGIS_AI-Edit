from __future__ import annotations

import time

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QSettings, QUrl
from qgis.PyQt.QtGui import QDesktopServices

from ...core import qt_compat as QtC
from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import (
    clear_activation,
    get_activation_key,
    get_dashboard_url,
    get_server_url,
    save_activation,
    validate_key_with_server,
)
from ...core.config_store import get_export_copy, get_export_dial
from ...core.errors import NETWORK_ERROR_CODES, TRANSIENT_SERVER_ERROR_CODES
from ...core.i18n import tr
from ...core.logger import log, log_debug, log_warning
from ...core.prompts import conversation_thumbs
from ...core.window_focus import bring_qgis_window_to_front
from ...workers.generic_request_task import GenericRequestTask
from ...workers.pairing_poll_task import PairingPollTask
from ..canvas_exporter import has_tuned_config
from .errors import network_next_step, subscribe_error_url
from .lifecycle import REQUEST_TASK_SIGNALS, drain_task



_KEY_REVALIDATION_WINDOW_S = 900


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



        self._refresh_conversations_cache()

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
        self._last_key_validation_unix = 0.0
        clear_activation()
        self._auth_manager.set_activation_key("")
        self._dock_widget.set_activated(False)
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


        for attr in ("_key_validation_worker", "_credits_loader"):
            drain_task(getattr(self, attr, None), REQUEST_TASK_SIGNALS)
            setattr(self, attr, None)
        cancel_history = getattr(self, "_cancel_history_tasks", None)
        if callable(cancel_history):
            cancel_history()
        clear_activation()
        self._auth_manager.set_activation_key("")




        self._clear_local_conversations()
        self._dock_widget.set_activated(False)
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
            get_export_copy("flows.activation.key_verified", tr("Activation key verified!")),
            is_error=False,
        )

        self._dock_widget.set_checking_credits(True)
        self._refresh_credits()



        self._refresh_conversations_cache()

        settings = QSettings()
        if not settings.value("AIEdit/activation_timestamp_unix", "", type=str):
            settings.setValue("AIEdit/activation_timestamp_unix", str(int(time.time())))

    def _start_sibling_sign_in(self):
        from ...core import sibling_sign_in
        try:
            from ...core.device_id import get_device_hash
            device = get_device_hash()
        except Exception:  # noqa: BLE001
            device = ""
        sibling_sign_in.start("ai-edit", self._client.base_url, device, self._on_sibling_sign_in)

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
        if self._pairing_worker is worker:
            self._pairing_worker = None
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








        from ...core.device_id import get_device_hash




        url = (
            f"{self._client.base_url}/connect?code={code}&product=ai-edit"
            f"&device_id={get_device_hash()}"
            "&utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=connect"
        )


        self._dock_widget.set_pairing_link(url)
        opened = QDesktopServices.openUrl(QUrl(url))
        if not opened:
            self._dock_widget.show_pairing_idle()
            self._dock_widget.set_activation_message(
                get_export_copy(
                    "flows.activation.browser_open_failed",
                    tr("Couldn't open your browser. Copy the link and open it manually."),
                ),
                is_error=True,
            )
            return

        if self._pairing_worker is not None and self._pairing_worker.is_active():

            return

        self._pairing_worker = PairingPollTask(self._client, code)
        self._pairing_worker.pairing_succeeded.connect(self._on_pairing_succeeded)
        self._pairing_worker.pairing_failed.connect(self._on_pairing_failed)
        self._pairing_worker.pairing_timeout.connect(self._on_pairing_timeout)
        self._pairing_worker.pairing_browser_seen.connect(self._on_pairing_browser_seen)
        self._pairing_worker.pairing_stalled.connect(self._on_pairing_stalled)
        self._pairing_worker.pairing_network_problem.connect(self._on_pairing_network_problem)
        self._pairing_worker.taskTerminated.connect(
            lambda w=self._pairing_worker: self._on_pairing_task_terminated(w)
        )
        QgsApplication.taskManager().addTask(self._pairing_worker)



        self._pairing_started_unix = time.time()
        self._pairing_stalled = False
        telemetry.track(te.AI_EDIT_PAIR_STARTED)
        telemetry.flush()
        log("Pairing started")

    def _pairing_duration_ms(self) -> int:

        start = getattr(self, "_pairing_started_unix", 0.0)
        if not start:
            return 0
        return int((time.time() - start) * 1000)

    def _on_pairing_succeeded(self, key: str):
        self._apply_activation(key)


        try:
            bring_qgis_window_to_front(self._iface.mainWindow(), self._dock_widget)
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
        self._dock_widget.show_pairing_idle()
        self._dock_widget.set_activation_message(
            get_export_copy(
                "flows.activation.pairing_timed_out",
                tr("Sign-in timed out. Click Connect to try again, "
                   "or enter your key manually."),
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
        if code:


            task = GenericRequestTask(
                get_export_copy("flows.activation.cancelling_sign_in", tr("Cancelling sign-in")),
                lambda c=code: self._client.cancel_pairing(c),
                silent=True,
            )
            self._hold_history_task(task)
        telemetry.track(te.AI_EDIT_PAIR_CANCELLED, {
            "duration_ms": self._pairing_duration_ms(),
            "stalled": bool(getattr(self, "_pairing_stalled", False)),
        })
        telemetry.flush()
        log("Pairing cancelled")

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
