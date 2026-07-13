from __future__ import annotations

import time

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QSettings, QUrl
from qgis.PyQt.QtGui import QDesktopServices

from ...core import telemetry
from ...core import telemetry_events as te
from ...core.auth.activation_manager import (
    clear_activation,
    get_activation_key,
    get_dashboard_url,
    get_server_config,
    save_activation,
    validate_key_with_server,
)
from ...core.errors import NETWORK_ERROR_CODES, TRANSIENT_SERVER_ERROR_CODES
from ...core.i18n import tr
from ...core.logger import log, log_debug, log_warning
from ...workers.generic_request_task import GenericRequestTask
from ...workers.pairing_poll_task import PairingPollTask
from .errors import SUBSCRIBE_ERROR_URL


def _key_validation_request(client, key):
    """Run validate_key_with_server and reshape its tuple into the dict shape
    GenericRequestTask expects (the /usage payload on success, {"error": msg,
    "code": code} on failure). Passing the payload through lets the caller
    reuse it for the credits display instead of fetching /usage twice."""
    success, message, code, usage = validate_key_with_server(client, key)
    if success:
        return usage if isinstance(usage, dict) else {}
    return {"error": message, "code": code}


class ActivationMixin:
    def _check_activation_state(self, validate: bool = True):
        """Check activation key presence and validate with server.

        ``validate=False`` does only the synchronous UI half (optimistic
        activated view or sign-up screen); the caller provides the server
        confirmation through another channel (the startup bootstrap bundle).
        """
        settings = QSettings()
        saved_key = get_activation_key(settings)
        if not saved_key:
            clear_activation(settings)
            self._auth_manager.set_activation_key("")
            self._dock_widget.set_activated(False)
            self._settings_action.setEnabled(False)
            self._last_key_validation_unix = 0.0
            return

        self._auth_manager.set_activation_key(saved_key)

        # Revalidation window. The server enforces auth on every real request
        # anyway, so this client-side check is purely cosmetic (which screen to
        # show); 30s made every dock toggle cost two /usage calls and was the
        # single largest source of API traffic (10x the generation volume).
        if (time.time() - self._last_key_validation_unix) < 900:
            self._dock_widget.set_activated(True)
            self._settings_action.setEnabled(True)
            # Stay on LAUNCH state; tool is activated on user click.
            return

        # Optimistic activation: a saved key implies the user is already
        # signed up. Show the activated UI immediately and disable
        # Launch until the async credit check confirms; otherwise the
        # sign-up screen flashes for half a second on every reload.
        self._dock_widget.set_activated(True)
        self._settings_action.setEnabled(True)
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
        """Server confirmed the key is valid. The validation call IS a /usage
        fetch, so its payload feeds the credits display directly instead of
        firing a second, identical request."""
        # Guard against an orphaned validation worker firing after unload.
        if self._dock_widget is None:
            return
        # Connection works again: re-arm the one-shot connectivity notice.
        self._connectivity_notice_shown = False
        self._last_key_validation_unix = time.time()
        self._dock_widget.set_activated(True)
        self._settings_action.setEnabled(True)
        # Stay on LAUNCH state; tool is activated on user click.
        if isinstance(usage, dict) and "images_used" in usage:
            self._auth_manager.seed_usage(usage)
            self._on_credits_loaded(usage)
        else:
            self._dock_widget.set_checking_credits(True)
            self._refresh_credits()

    def _on_key_invalid(self, message: str, code: str):
        """Key validation came back negative.

        A genuine auth rejection (INVALID_KEY, SUBSCRIPTION_*, ...) signs the
        user out. A connectivity failure must NOT: clearing the key on a network
        blip would dump an offline user back to the sign-up screen and lose their
        stored key. On a network error we keep the optimistic-activated session
        (the server re-checks on every real call) and show one quiet notice.
        A transient server-side failure (5xx incident, rate limit) says nothing
        about the key either, so it takes the same keep-session path.
        """
        # Guard against an orphaned validation worker firing after unload.
        if self._dock_widget is None:
            return
        code_up = (code or "").strip().upper()
        if code_up in NETWORK_ERROR_CODES or code_up in TRANSIENT_SERVER_ERROR_CODES:
            self._dock_widget.set_activated(True)
            self._settings_action.setEnabled(True)
            # The optimistic path disabled Launch pending this check; restore it
            # so an offline user can still open the tool from a cached session.
            self._dock_widget.set_launch_enabled(True)
            self._show_connectivity_notice(code)
            return
        # Genuine auth rejection (INVALID_KEY / KEY_REVOKED / SUBSCRIPTION_EXPIRED
        # / DEVICE_LIMIT_EXCEEDED). Record the machine code only, never the
        # localized message.
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
        self._settings_action.setEnabled(False)

    def _on_settings_clicked(self):
        """Open the Account Settings dialog."""
        if not self._auth_manager.has_activation_key():
            return
        self._disarm_swipe()
        from ..dialogs.account_settings_dialog import AccountSettingsDialog

        dlg = AccountSettingsDialog(
            client=self._client,
            auth=self._auth_manager.get_auth_header(),
            activation_key=self._auth_manager.get_activation_key(),
            parent=self._iface.mainWindow(),
        )
        dlg.sign_out_requested.connect(self._on_sign_out)
        self._dock_widget.set_settings_button_active(True)
        try:
            dlg.exec()
        finally:
            self._dock_widget.set_settings_button_active(False)

    def _on_sign_out(self):
        """Disconnect: clear the stored key and return to the sign-in screen."""
        self._last_key_validation_unix = 0.0
        clear_activation()
        self._auth_manager.set_activation_key("")
        self._dock_widget.set_activated(False)
        self._settings_action.setEnabled(False)
        log_debug("Signed out")

    def _apply_activation(self, key: str):
        """Shared success funnel for both manual paste and one-click connect.

        Persists the key, loads it into the auth manager, flips the dock to the
        activated state, and kicks a credit refresh. Callers add their own
        path-specific telemetry afterward.
        """
        save_activation(key)
        self._auth_manager.set_activation_key(key)
        self._dock_widget.set_activated(True)
        self._dock_widget.set_activation_message(tr("Activation key verified!"), is_error=False)
        self._dock_widget.hide_activation_limit_cta()
        self._settings_action.setEnabled(True)
        # Stay on LAUNCH state; tool is activated on user click.
        self._dock_widget.set_checking_credits(True)
        self._refresh_credits()
        # Persist activation timestamp once for cohort analysis.
        settings = QSettings()
        if not settings.value("AIEdit/activation_timestamp_unix", "", type=str):
            settings.setValue("AIEdit/activation_timestamp_unix", str(int(time.time())))

    def _cancel_pairing_worker(self):
        """Cancel any in-flight pairing poll (never terminate())."""
        if self._pairing_worker is not None and self._pairing_worker.is_active():
            try:
                self._pairing_worker.cancel()
            except Exception:  # nosec B110
                pass

    # --- One-click connect (browser pairing handoff) ------------------------

    def _on_pairing_requested(self, code: str):
        """Open the browser to /connect and start polling for the key.

        Re-entrant: if a poll is already running (the user clicked "open the
        page again"), we only re-open the browser instead of starting a second
        worker.
        """
        # Build the connect URL from the client base so .env.local
        # TERRALAB_BASE_URL is honored in dev. Never log the code or full URL.
        url = (
            f"{self._client.base_url}/connect?code={code}&product=ai-edit"
            "&utm_source=qgis&utm_medium=plugin&utm_campaign=ai-edit&utm_content=connect"
        )
        # Hand the URL to the dock so its copy-link button can offer it (lets
        # the user finish sign-in in a different browser).
        self._dock_widget.set_pairing_link(url)
        opened = QDesktopServices.openUrl(QUrl(url))
        if not opened:
            self._dock_widget.show_pairing_idle()
            self._dock_widget.set_activation_message(
                tr("Couldn't open your browser. Copy the link and open it manually."),
                is_error=True,
            )
            return

        if self._pairing_worker is not None and self._pairing_worker.is_active():
            # Already polling for this code; the browser was just re-opened.
            return

        self._pairing_worker = PairingPollTask(self._client, code)
        self._pairing_worker.pairing_succeeded.connect(self._on_pairing_succeeded)
        self._pairing_worker.pairing_failed.connect(self._on_pairing_failed)
        self._pairing_worker.pairing_timeout.connect(self._on_pairing_timeout)
        self._pairing_worker.pairing_browser_seen.connect(self._on_pairing_browser_seen)
        self._pairing_worker.pairing_stalled.connect(self._on_pairing_stalled)
        QgsApplication.taskManager().addTask(self._pairing_worker)
        # Anchor the wait clock + reset the stalled flag on a genuine start (this
        # branch is skipped on a browser re-open), so the terminal pairing event
        # can report how long the user waited and whether it stalled first.
        self._pairing_started_unix = time.time()
        self._pairing_stalled = False
        telemetry.track(te.AI_EDIT_PAIR_STARTED)
        telemetry.flush()
        log("Pairing started")

    def _pairing_duration_ms(self) -> int:
        """Milliseconds since AI_EDIT_PAIR_STARTED, or 0 if never started."""
        start = getattr(self, "_pairing_started_unix", 0.0)
        if not start:
            return 0
        return int((time.time() - start) * 1000)

    def _on_pairing_succeeded(self, key: str):
        self._apply_activation(key)
        # Bring QGIS back to front so the user sees the activated dock.
        try:
            mw = self._iface.mainWindow()
            mw.activateWindow()
            mw.raise_()
            self._dock_widget.raise_()
        except Exception:  # nosec B110
            pass
        telemetry.track(te.AI_EDIT_PAIR_SUCCEEDED, {
            "duration_ms": self._pairing_duration_ms(),
            "stalled": bool(getattr(self, "_pairing_stalled", False)),
        })
        # Funnel step between activation_screen_viewed and plugin_activated.
        telemetry.track(te.ACTIVATION_ATTEMPTED, {"success": True})
        telemetry.track(te.PLUGIN_ACTIVATED, {"activation_method": "pairing"})
        telemetry.flush()
        log("Pairing successful")

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
            tr("Sign-in timed out. Click Connect to try again, "
               "or enter your key manually."),
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
            # Retire the code server-side so a later Confirm in the browser
            # shows "expired" instead of binding a key nobody is polling for.
            task = GenericRequestTask(
                tr("Cancelling sign-in"),
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
        """Fetch and display current credits in background (non-blocking)."""
        self._credits_loader = GenericRequestTask(
            "AI Edit credits",
            self._auth_manager.get_usage_info,
            silent=True,
        )
        self._credits_loader.succeeded.connect(self._on_credits_loaded)
        self._credits_loader.failed.connect(lambda _msg, _code: self._on_credits_failed())
        QgsApplication.taskManager().addTask(self._credits_loader)

    def _on_credits_failed(self):
        """Credits fetch failed - clear loading state so the UI is usable."""
        if self._dock_widget:
            self._dock_widget.set_checking_credits(False)
            self._dock_widget.set_launch_enabled(True)

    def _on_credits_loaded(self, usage: dict):
        """Update dock widget with credits from background fetch."""
        if self._dock_widget:
            self._dock_widget.set_checking_credits(False)
            self._dock_widget.set_launch_enabled(True)
            used = usage.get("images_used")
            limit = usage.get("images_limit")
            is_free = usage.get("is_free_tier", False)
            # Prime the subscribe URL BEFORE set_credits so the dock's
            # auto-surface logic can show the upsell banner inline.
            if is_free:
                # Cache-only (no client): the config is pre-warmed off-thread at
                # startup, so this never blocks the UI on the network. Falls back
                # to the default upgrade URL for the brief pre-warm window.
                config = get_server_config()
                dashboard = config.get("upgrade_url", get_dashboard_url())
                self._dock_widget.set_subscribe_url(dashboard)
            self._dock_widget.set_credits(
                used=used,
                limit=limit,
                is_free_tier=is_free,
            )
            # Paid-tier monthly limit still needs the dedicated CTA (different
            # message + different URL than the free-tier upsell).
            both_ints = isinstance(used, int) and isinstance(limit, int)
            if both_ints and limit > 0 and used >= limit and not is_free:
                self._dock_widget.show_usage_limit_info(
                    tr("Monthly limit reached ({used}/{limit}).").format(used=used, limit=limit),
                    SUBSCRIBE_ERROR_URL,
                )
