from __future__ import annotations

from ...core import qt_compat as QtC
from ...core.auth.activation_manager import (
    get_prewall_url,
    get_subscribe_url,
    get_wall_url,
    is_feature_enabled,
)
from ...core.config_store import get_export_copy, get_export_dial
from ...core.date_format import format_reset_date
from ...core.entitlements import paid_tier_default
from ...core.i18n import tr
from ...core.number_format import format_count
from ...core.paywall_state import total_free_generations
from ...core.pro_ceiling import pro_ceiling_enabled
from ...core.resolution_labels import DEFAULT_RESOLUTION_CREDIT_COSTS
from .blocked_reasons import LAUNCH_BLOCK_NO_KEY
from .design_tokens import FONT_HINT, GREEN_TEXT, RED_TEXT

_PAIRING_COPY_REVERT_MS = 1400
# The longest Launch waits on the account check before it lets the user in
# anyway (the server still answers every real request): a check whose answer
# never came used to leave "Checking your account..." up for the session.
_ACCOUNT_CHECK_MAX_MS = 20000


# The low-balance row was dismissed: hidden until QGIS restarts.
_PREWALL_DISMISSED = False


def _prewall_dismissed_this_session() -> bool:
    return _PREWALL_DISMISSED


class DockAccountMixin:
    """Activation, sign-in pairing, credits display, and subscribe/limit
    CTAs for AIEditDockWidget."""

    # --- Public methods ---

    def _stop_account_check_timer(self) -> None:
        timer = getattr(self, "_account_check_timer", None)
        self._account_check_timer = None
        if timer is None:
            return
        try:
            timer.stop()
            timer.deleteLater()
        except RuntimeError:
            # The dock may already be in Qt teardown.
            pass

    def cleanup_account_check(self) -> None:
        """Stop the deferred Launch fallback before dock teardown.

        ``QApplication.processEvents()`` does not flush DeferredDelete events,
        so relying on the dock's QObject parent to destroy this timer leaves a
        live callback between test cases and plugin unload.
        """
        self._account_check_token = getattr(self, "_account_check_token", 0) + 1
        self._launch_account_pending = False
        self._stop_account_check_timer()

    def set_launch_enabled(self, enabled: bool) -> None:
        """Disable Launch AI Edit during async validation/credit checks
        so the user can't fire a session before we know they're authorised.
        Avoids flashing the sign-up screen on reload. Re-enabling goes through
        the layer gate instead of flipping the button directly: with no visible
        layer there is nothing to capture, and a direct enable here used to
        override that lock right after the credits check."""
        # Remembered, so the layer gate keeps Launch grey for the whole
        # check instead of re-enabling it on the next layer change.
        self._stop_account_check_timer()
        self._launch_account_pending = not enabled
        self._account_check_token = getattr(self, "_account_check_token", 0) + 1
        if enabled:
            self._update_layer_warning()
        else:
            self.set_launch_block_reason(LAUNCH_BLOCK_NO_KEY)
            token = self._account_check_token
            self._account_check_timer = QtC.safe_single_shot(
                get_export_dial("dock.account.account_check_max_ms", _ACCOUNT_CHECK_MAX_MS),
                self,
                lambda: self._expire_account_check(token),
            )

    def _expire_account_check(self, token: int) -> None:
        """Let Launch go when the account check that greyed it never answered."""
        if token != getattr(self, "_account_check_token", 0):
            return
        self._stop_account_check_timer()
        if getattr(self, "_launch_account_pending", False):
            self._launch_account_pending = False
            self._update_layer_warning()

    def set_activated(self, activated: bool):
        self._activated = activated
        self._activation_widget.setVisible(not activated)
        self._main_widget.setVisible(activated)
        self._settings_btn.setVisible(activated)
        self._history_btn.setVisible(activated)
        # Vectorize + Before/after both work on any existing AI-Edit raster
        # (not just a fresh result), so they are revealed the moment the dock
        # is activated. Their per-click eligibility is gated by the active
        # layer (set_swipe_button_enabled, vectorize_btn enable refresh).
        # A server kill switch hides the entry point rather than greying it:
        # the click-time gate behind it stays as the backstop for the other
        # ways in (canvas pills, shortcuts).
        self._apply_feature_visibility(activated)
        if not activated:
            # Signed out: no check is pending any more, and the next sign-in
            # starts its own.
            self._launch_account_pending = False
            # The next account's plan is unknown until it answers.
            self._tier_confirmed = False
        if activated:
            self.hide_trial_info()
            self._update_layer_warning()
            self.set_launch_state()
            # A stale pairing spinner must never survive a successful activation.
            self._stop_pairing_wait()
        else:
            self._setup_header.setVisible(True)
            self._connect_section.setVisible(True)
            self._stop_pairing_wait()
            self._activation_message.setVisible(False)
            self.hide_trial_info()
        self._sync_pro_pill()

    def _apply_feature_visibility(self, activated: bool) -> None:
        """Show or hide the entry points the server can switch off.

        A kill switch hides the entry point rather than greying it: the
        click-time gate behind it stays as the backstop for the other ways in
        (canvas pills, shortcuts).
        """
        self._vectorize_btn.setVisible(activated and is_feature_enabled("vectorize"))
        self._set_swipe_button_visible(activated and is_feature_enabled("swipe"))
        markup_available = is_feature_enabled("markup")
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_markup_available(markup_available)

    def refresh_feature_visibility(self) -> None:
        """Re-read the kill switches and the update offer after a late config arrival.

        The config warms in the background, so the dock is built and often
        activated before the answer lands. Without this, a feature switched off
        server-side stays visible until the next activation refresh, and a
        control that is visible and then refuses is worse than one that is
        absent.
        """
        try:
            self._apply_feature_visibility(bool(self._activated))
            # The two entry points the chrome owns, same switches.
            self._sync_demo_button()
            self._sync_attach_buttons()
        except Exception:  # nosec B110 - a late refresh must never break the dock
            pass
        try:
            # A served answer re-decides the update offer, as in AI Agent.
            self.check_for_updates()
        except Exception:  # nosec B110 - an update check never breaks the dock
            pass

    def set_activation_message(self, text: str, is_error: bool = False):
        color = RED_TEXT if is_error else GREEN_TEXT
        self._activation_message.setStyleSheet(
            f"font-size: {FONT_HINT}px; color: {color}; background: transparent; border: none;"
        )
        self._activation_message.setText(text)
        self._activation_message.setVisible(True)

    def set_credits(
        self,
        used: int | None = None,
        limit: int | None = None,
        is_free_tier: bool = False,
        reset_date: str | None = None,
    ):
        """Take a confirmed balance. The dock prints no count (Settings does);
        the balance drives the quota cards: the free-tier wall and the
        low-balance row before it, so both survive stray ``set_status`` calls
        that would otherwise hide them (see _is_free_tier_exhausted /
        _is_free_tier_prewall in generation_state.py, the re-surfacing
        hooks there). ``reset_date`` is the server's ISO renewal instant
        (reset_date / period_end on the usage payload); may be None on an
        older cached response or for a product with no monthly renewal.
        """
        self._is_free_tier = is_free_tier
        # The plan is now the server's word, not the widget's Free default.
        self._tier_confirmed = True
        self._reset_date = reset_date
        # Keep the reference-image gate in sync with the confirmed tier.
        if self._reference_widget is not None:
            self._reference_widget.set_free_tier(is_free_tier)
        # Paid accounts land on the plan's default tier: better results out of
        # the box. Applied only on a confirmed paid credits payload, and never
        # over a resolution the user picked themselves. The free-tier coercion
        # runs in _refresh_resolution_triggers, off the same helper.
        if used is not None and limit is not None and not is_free_tier and not self._resolution_user_choice:
            self._selected_resolution = paid_tier_default()
        if used is not None and limit is not None:
            # Cache before the paywall check below reads it back.
            self._cached_used = used
            self._cached_limit = limit
            if self._is_free_tier_exhausted():
                self.show_trial_exhausted_info("", get_wall_url())
                self.hide_prewall_info()
            elif self._is_free_tier_prewall():
                self.show_prewall_info(get_prewall_url())
                self._trial_info_box.setVisible(False)
                self._wall_telemetry_shown = False
            elif self._is_pro_low():
                self.show_pro_low_info()
                self._trial_info_box.setVisible(False)
            else:
                self._trial_info_box.setVisible(False)
                self.hide_prewall_info()
                self._wall_telemetry_shown = False
                self._prewall_telemetry_shown = False
        self._refresh_resolution_triggers()
        self._update_generate_button_text()
        self._sync_pro_pill()

    def _wall_title(self) -> str:
        """The wall's headline: total free generations and when they return.

        Built from the last confirmed credits + reset date, so every path
        that shows the wall (a proactive credits refresh, or a rejected
        generation attempt) reads the same sentence. Empty when the credit
        state needed to build it is not cached yet.
        """
        if not self._cached_limit:
            return ""
        unit_cost = self._resolution_credit_costs.get(
            "1K", DEFAULT_RESOLUTION_CREDIT_COSTS["1K"]
        )
        total = total_free_generations(self._cached_limit, unit_cost)
        if not total:
            return ""
        date_str = format_reset_date(self._reset_date) if self._reset_date else ""
        # Served, like the button under it (wall.cta) and the pre-wall banner
        # before it: 112 users a month read this sentence and it was the only
        # one of the three credit surfaces that needed a release to change.
        # Tokens are substituted with replace(), never format(), so a served
        # line carrying a stray brace cannot raise while the wall is built.
        if date_str:
            title = get_export_copy(
                "wall.title", tr("Your {total} free edits return on {date}")
            )
            return title.replace("{total}", format_count(total)).replace("{date}", date_str)
        # The server has no renewal date for this account (older cached
        # response, or a product outside the monthly free renewal). Same
        # sentence minus the specific day rather than a raw/missing value.
        title = get_export_copy(
            "wall.title_no_date", tr("Your {total} free edits return next month")
        )
        return title.replace("{total}", format_count(total))

    def set_subscribe_url(self, url: str) -> None:
        """Prime the subscribe URL so set_credits can show the upsell on its own."""
        if url:
            self._trial_info_url = url

    def show_trial_exhausted_info(self, fallback_message: str, subscribe_url: str):
        """Wall screen: locked wording, one CTA, the renewal date secondary.

        ``fallback_message`` is used only when the credit state needed to
        build the real sentence (total generations + reset date) is not
        cached yet; both paths that reach here (account.py's own credit
        refresh, and a rejected generation in generation_results.py) usually
        arrive with fresh state, so it is rarely the one actually shown.
        """
        self._hide_limit_cta()
        title = get_export_copy(
            "dock.account.trial_exhausted_fallback_title",
            tr("You've used this month's free edits"),
        )
        # When they come back (served wall.title), or the caller's sentence
        # while the credit state is not cached yet.
        note = self._wall_title() or (fallback_message or "").strip()
        # One line of what Pro adds, never a list, never a price in code.
        pitch = get_export_copy(
            "wall.subtext",
            tr("About 150 edits a month and higher-resolution results. Cancel anytime."),
        )
        from ...core.pro_ceiling import pro_ceiling_contact_email
        from .pro_ceiling import custom_needs_line

        self._trial_info_url = subscribe_url
        self._trial_info_box.show_wall(
            title,
            note if note != title else "",
            pitch,
            # The header pill's word, so the offer reads the same everywhere.
            get_export_copy("nudge.header_pill", tr("Get Pro")),
            custom_needs_line(pro_ceiling_contact_email()),
        )
        self._hide_status_box()
        if not self._wall_telemetry_shown:
            from ...core import telemetry
            from ...core import telemetry_events as te
            telemetry.track(te.TRIAL_EXHAUSTED_VIEWED, {"is_free_tier": True})
            self._wall_telemetry_shown = True

    def show_usage_limit_info(self, message: str, subscribe_url: str):
        # Paid tier with the served ceiling on: a human contact, not a wall.
        if not self._is_free_tier and pro_ceiling_enabled():
            self.show_pro_limit_info(self.pro_limit_title(message), subscribe_url)
            return
        self._hide_status_box()
        self._trial_info_box.setVisible(False)
        self._limit_cta_url = subscribe_url
        self._pro_limit_card.show_contact(
            message,
            "",
            "",
            "",
            get_export_copy("dock.build.manage_plan_btn", tr("Manage plan")),
        )

    def show_prewall_info(self, cta_url: str):
        """Pre-wall banner: one generation left this month, shown ahead of
        the wall. Visibility is state-driven (set_credits); a dismiss keeps
        it hidden for the rest of the QGIS session."""
        if _prewall_dismissed_this_session():
            return
        # Served, so the number and the price follow the website without a
        # release. "Working on a project?" was a rhetorical question carrying
        # no claim: every user of a GIS plugin is working on a project.
        self._prewall_url = cta_url
        self._prewall_banner.show_low(
            get_export_copy("prewall.text", tr("Last free edit this month.")),
            # The header pill's word, as on the wall: one name for the offer.
            get_export_copy("nudge.header_pill", tr("Get Pro")),
        )
        if not self._prewall_telemetry_shown:
            from ...core import telemetry
            from ...core import telemetry_events as te
            telemetry.track(te.PAYWALL_PREWALL_SHOWN, {})
            self._prewall_telemetry_shown = True

    def _on_prewall_dismissed(self, _shape: str) -> None:
        global _PREWALL_DISMISSED
        _PREWALL_DISMISSED = True

    def hide_prewall_info(self):
        self._prewall_banner.setVisible(False)

    def _on_prewall_cta_clicked(self):
        if not self._prewall_url:
            return
        if self._is_pro_low():
            self._on_pro_contact_clicked(self._prewall_banner.compact_button)
            return
        self._open_plans("prewall", "plugin_prewall", self._prewall_url)

    def set_checking_credits(self, checking: bool):
        # Silent: the credit refresh is fast enough that a flashed status box
        # is more noise than signal. Flag kept in case callers need to query.
        self._checking_credits = checking

    def hide_trial_info(self):
        self._trial_info_box.setVisible(False)
        self.hide_prewall_info()
        self._hide_status_box()
        self._hide_limit_cta()
        self._wall_telemetry_shown = False
        self._prewall_telemetry_shown = False

    def _on_settings_btn_clicked(self):
        self.settings_clicked.emit()

    def _on_trial_info_subscribe_clicked(self):
        self._open_plans("trial_exhausted_box", "plugin_wall",
                         self._trial_info_url or get_subscribe_url())

    def _open_plans(self, source: str, cta_source: str, fallback_url: str) -> None:
        """The AI Edit plans, signed in through a one-time link when the key
        allows it, else ``fallback_url``. The click is reported once the door
        is known, so ``checkout_link`` says which one opened."""
        from ...core import telemetry
        from ...core import telemetry_events as te
        from ..pro_page_link import open_pro_page

        def report(checkout_link: str) -> None:
            telemetry.track(te.SUBSCRIBE_LINK_CLICKED,
                            {"source": source, "checkout_link": checkout_link})
            # The user leaves QGIS for the browser right after; ship now or
            # the batch dies with the session.
            telemetry.flush()

        open_pro_page(
            cta_source,
            fallback_url,
            client=getattr(self, "_library_client", None),
            auth_manager=getattr(self, "_library_auth_manager", None),
            on_outcome=report,
        )

    def _on_exit_clicked(self):
        """Cancel: ask the plugin to drop the zone and return to LAUNCH."""
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
        self._pairing_copy_btn.setText(get_export_copy("dock.account.pairing_copied", tr("Copied!")))
        # Parent the shot to the button so it can't fire on a freed C++ widget
        # if the dock is torn down within the 1400 ms window.
        QtC.safe_single_shot(
            get_export_dial("dock.account.pairing_copy_revert_ms", _PAIRING_COPY_REVERT_MS),
            self._pairing_copy_btn,
            lambda: self._pairing_copy_btn.setText(
                get_export_copy("dock.account.pairing_copy_link_idle", tr("Link not opening? Copy link"))
            ),
        )

    def _on_pairing_cancel_clicked(self):
        self.pairing_cancel_requested.emit(self._pending_pairing_code)
        self._pending_pairing_code = ""
        self.show_pairing_idle()

    def show_pairing_waiting(self):
        """Switch the onboarding into the 'waiting for browser' state."""
        self._pairing_active = True
        self._pairing_status.setText(
            get_export_copy("dock.account.pairing_waiting", tr("Finish signing in on the page that just opened"))
        )
        self._connect_section.setVisible(False)
        self._activation_message.setVisible(False)
        self._pairing_wait_section.setVisible(True)
        self._pairing_anim_timer.start()

    def show_pairing_browser_seen(self):
        """The server saw the browser reach /connect: reassure the user."""
        if self._pairing_active:
            self._pairing_status.setText(get_export_copy(
                "dock.account.pairing_browser_seen",
                tr("Browser page open. Finish signing in to connect."),
            ))

    def show_pairing_stalled_hint(self):
        """Long wait and the browser was never seen server-side: surface the
        recovery paths instead of an endless spinner."""
        if self._pairing_active:
            self._pairing_status.setText(get_export_copy(
                "dock.account.pairing_stalled_hint",
                tr("Still waiting. If the page did not open or shows an error, "
                   "click Open again or copy the link into another browser."),
            ))

    def show_pairing_network_problem(self, next_step: str):
        """The sign-in check itself keeps failing on the network. Polling goes
        on, so a network that comes back still finishes the sign-in; the user
        gets the step that fixes their network in the meantime."""
        if self._pairing_active:
            text = tr("Still waiting, but AI Edit cannot reach the server to check your sign-in.")
            self._pairing_status.setText(f"{text} {next_step}".strip())

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
            self._open_plans("limit_cta", "plugin_limit_cta", self._limit_cta_url)

    def _hide_limit_cta(self):
        self._pro_limit_card.clear()
        self._limit_cta_url = ""
