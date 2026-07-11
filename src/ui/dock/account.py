from __future__ import annotations

from ...core import qt_compat as QtC
from ...core.auth.activation_manager import get_subscribe_url
from ...core.i18n import tr
from ..external_url import open_external
from .style import ERROR_TEXT, SUCCESS_TEXT


class DockAccountMixin:
    """Activation, sign-in pairing, credits display, and subscribe/limit
    CTAs for AIEditDockWidget."""

    # --- Public methods ---

    def set_launch_enabled(self, enabled: bool) -> None:
        """Disable Launch AI Edit during async validation/credit checks
        so the user can't fire a session before we know they're authorised.
        Avoids flashing the sign-up screen on reload. Re-enabling goes through
        the layer gate instead of flipping the button directly: with no visible
        layer there is nothing to capture, and a direct enable here used to
        override that lock right after the credits check."""
        if enabled:
            self._update_layer_warning()
        else:
            self._launch_btn.setEnabled(False)

    def set_activated(self, activated: bool):
        self._activated = activated
        self._activation_widget.setVisible(not activated)
        self._main_widget.setVisible(activated)
        self._settings_btn.setVisible(activated)
        # Vectorize + Before/after both work on any existing AI-Edit raster
        # (not just a fresh result), so they are revealed the moment the dock
        # is activated. Their per-click eligibility is gated by the active
        # layer (set_swipe_button_enabled, vectorize_btn enable refresh).
        self._vectorize_btn.setVisible(activated)
        self._set_swipe_button_visible(activated)
        if activated:
            self.hide_trial_info()
            self._update_layer_warning()
            self._set_upgrade_cta_wanted(self._is_free_tier)
            self.set_launch_state()
            # A stale pairing spinner must never survive a successful activation.
            self._stop_pairing_wait()
        else:
            self._setup_header.setVisible(True)
            self._connect_section.setVisible(True)
            self._stop_pairing_wait()
            self._activation_message.setVisible(False)
            self.hide_activation_limit_cta()
            # The credits ring + count and the upsell pill belong to a signed-in
            # session only; clear them so they never linger after sign-out.
            self._set_credits_wanted(False)
            self._set_upgrade_cta_wanted(False)
            self.hide_trial_info()
        # Reconcile the first-steps guide banner: shown on the idle screen for a
        # signed-in user (set_launch_state above), hidden here after sign-out.
        self._update_first_steps_visibility()

    def hide_consent(self):
        """Hide the consent checkbox after first generation."""
        self._consent_widget.setVisible(False)

    def set_activation_message(self, text: str, is_error: bool = False):
        # Use brighter variants for dark theme readability
        self.hide_activation_limit_cta()
        color = ERROR_TEXT if is_error else SUCCESS_TEXT
        self._activation_message.setStyleSheet(f"font-size: 11px; color: {color};")
        self._activation_message.setText(text)
        self._activation_message.setVisible(True)

    def show_activation_limit_cta(self, subscribe_url: str):
        self._activation_limit_cta_url = subscribe_url
        self._activation_limit_cta_btn.setText(tr("Subscribe"))
        self._activation_limit_cta_btn.setVisible(True)

    def hide_activation_limit_cta(self):
        self._activation_limit_cta_btn.setVisible(False)
        self._activation_limit_cta_url = ""

    def set_credits(
        self,
        used: int | None = None,
        limit: int | None = None,
        is_free_tier: bool = False,
    ):
        """Update the credits ring + compact count in the footer.

        Also drives the trial-exhausted upsell banner so it survives stray
        ``set_status`` calls that otherwise hide it.
        """
        self._is_free_tier = is_free_tier
        # Keep the reference-image gate in sync with the confirmed tier.
        if self._reference_widget is not None:
            self._reference_widget.set_free_tier(is_free_tier)
        # Paid default is "2K" (Detailed): better results out of the box.
        # Applied only on a confirmed paid credits payload, and never over a
        # resolution the user picked themselves. Free tier keeps its "1K"
        # coercion in _refresh_resolution_triggers.
        if (
            used is not None
            and limit is not None
            and not is_free_tier
            and not self._resolution_user_choice
        ):
            self._selected_resolution = "2K"
        if used is not None and limit is not None:
            remaining = max(0, limit - used)
            self._credits_label.setText(f"{remaining} / {limit}")
            self._credit_ring.set_credits(used, limit, free_tier=is_free_tier)
            tooltip = tr("Credits remaining this month: {remaining} / {total}").format(
                remaining=remaining, total=limit
            )
            self._credit_ring.setToolTip(tooltip)
            self._credits_label.setToolTip(tooltip)
            self._set_credits_wanted(True)
            # Cache + auto-surface the upsell banner when free tier hits 0.
            self._cached_used = used
            self._cached_limit = limit
            exhausted = is_free_tier and limit > 0 and used >= limit
            if exhausted and self._trial_info_url:
                self.show_trial_exhausted_info(
                    tr("All {limit} free credits used. Subscribe to continue.").format(
                        limit=limit
                    ),
                    self._trial_info_url,
                )
            elif not exhausted:
                self._trial_info_box.setVisible(False)
        else:
            self._set_credits_wanted(False)
        self._set_upgrade_cta_wanted(is_free_tier and self._activated)
        self._refresh_resolution_triggers()
        self._update_generate_button_text()

    def set_subscribe_url(self, url: str) -> None:
        """Prime the subscribe URL so set_credits can show the upsell on its own."""
        if url:
            self._trial_info_url = url

    def show_trial_exhausted_info(self, message: str, subscribe_url: str):
        self._hide_limit_cta()
        # The CTA tail is suppressed server-side now (the dedicated primary
        # button below carries that action). The previous English substring
        # strip broke fr/es/pt_BR translations and is gone for that reason.
        title = (message or "").strip()
        if not title:
            title = tr("You've used your free credits")
        self._trial_info_text.setText(title)
        self._trial_info_url = subscribe_url
        self._trial_info_btn.setVisible(True)
        self._trial_info_link.setVisible(False)
        self._trial_info_box.setVisible(True)
        self._hide_status_box()

    def show_usage_limit_info(self, message: str, subscribe_url: str):
        self._show_status_box(message, "error")
        self._trial_info_box.setVisible(False)
        self._limit_cta_url = subscribe_url
        self._limit_cta_btn.setVisible(True)

    def set_checking_credits(self, checking: bool):
        # Silent: the credit refresh is fast enough that a flashed status box
        # is more noise than signal. Flag kept in case callers need to query.
        self._checking_credits = checking

    def hide_trial_info(self):
        self._trial_info_box.setVisible(False)
        self._hide_status_box()
        self._hide_limit_cta()

    def _on_settings_btn_clicked(self):
        self.settings_clicked.emit()

    def _on_upgrade_clicked(self):
        from ...core import telemetry
        from ...core import telemetry_events as te
        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "upgrade_cta"})
        # The user leaves QGIS for the browser right after; ship now or the
        # batch dies with the session.
        telemetry.flush()
        open_external(get_subscribe_url())

    def _on_trial_info_subscribe_clicked(self):
        from ...core import telemetry
        from ...core import telemetry_events as te
        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "trial_exhausted_box"})
        # The user leaves QGIS for the browser right after; ship now or the
        # batch dies with the session.
        telemetry.flush()
        url = self._trial_info_url or get_subscribe_url()
        open_external(url)

    def _on_exit_clicked(self):
        """Exit: ask the plugin to cancel + return to LAUNCH state."""
        self.exit_clicked.emit()

    def _on_connect_clicked(self):
        """Start the one-click browser handoff. Mints a high-entropy pairing
        code; the plugin opens the browser and polls until it gets the key."""
        import secrets
        self._pending_pairing_code = secrets.token_urlsafe(32)
        self.show_pairing_waiting()
        self.pairing_requested.emit(self._pending_pairing_code)

    def _on_pairing_reopen_clicked(self):
        """Re-open the browser with the SAME code (do not mint a new one)."""
        if self._pending_pairing_code:
            self.pairing_requested.emit(self._pending_pairing_code)

    def set_pairing_link(self, url: str):
        """Store the connect URL so the copy-link button can offer it (the URL
        is built plugin-side; the dock only displays it)."""
        self._pairing_link = url or ""

    def _on_pairing_copy_clicked(self):
        """Copy the connect link so the user can finish sign-in in another
        browser. Brief 'Copied!' feedback, then restore the label."""
        if not self._pairing_link:
            return
        from qgis.PyQt.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self._pairing_link)
        self._pairing_copy_btn.setText(tr("Copied!"))
        # Parent the shot to the button so it can't fire on a freed C++ widget
        # if the dock is torn down within the 1400 ms window.
        QtC.safe_single_shot(
            1400,
            self._pairing_copy_btn,
            lambda: self._pairing_copy_btn.setText(tr("Link not opening? Copy link")),
        )

    def _on_pairing_cancel_clicked(self):
        self.pairing_cancel_requested.emit(self._pending_pairing_code)
        self._pending_pairing_code = ""
        self.show_pairing_idle()

    def show_pairing_waiting(self):
        """Switch the onboarding into the 'waiting for browser' state."""
        self._pairing_active = True
        self._pairing_status.setText(tr("Waiting for you to sign in in your browser"))
        self._connect_section.setVisible(False)
        self._activation_message.setVisible(False)
        self._pairing_wait_section.setVisible(True)
        self._pairing_anim_timer.start()

    def show_pairing_browser_seen(self):
        """The server saw the browser reach /connect: reassure the user."""
        if self._pairing_active:
            self._pairing_status.setText(
                tr("Browser page open. Finish signing in to connect."))

    def show_pairing_stalled_hint(self):
        """Long wait and the browser was never seen server-side: surface the
        recovery paths instead of an endless spinner."""
        if self._pairing_active:
            self._pairing_status.setText(tr(
                "Still waiting. If the page did not open or shows an error, "
                "click Open again or copy the link into another browser."))

    def _stop_pairing_wait(self):
        """Hide the waiting section and stop its animation timer."""
        self._pairing_active = False
        self._pairing_anim_timer.stop()
        self._pairing_wait_section.setVisible(False)

    def show_pairing_idle(self):
        """Return to the idle onboarding (Connect button visible)."""
        self._stop_pairing_wait()
        self._connect_section.setVisible(True)

    def _on_limit_cta_clicked(self):
        if self._limit_cta_url:
            from ...core import telemetry
            from ...core import telemetry_events as te
            telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "limit_cta"})
            # The user leaves QGIS for the browser right after; ship now or the
            # batch dies with the session.
            telemetry.flush()
            open_external(self._limit_cta_url)

    def _on_activation_limit_cta_clicked(self):
        if self._activation_limit_cta_url:
            from ...core import telemetry
            from ...core import telemetry_events as te
            telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "activation_limit_cta"})
            # The user leaves QGIS for the browser right after; ship now or the
            # batch dies with the session.
            telemetry.flush()
            open_external(self._activation_limit_cta_url)

    def _hide_limit_cta(self):
        self._limit_cta_btn.setVisible(False)
        self._limit_cta_url = ""
