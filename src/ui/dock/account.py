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



_ACCOUNT_CHECK_MAX_MS = 20000



_PREWALL_DISMISSED = False


def _prewall_dismissed_this_session() -> bool:
    return _PREWALL_DISMISSED


class DockAccountMixin:





    def _stop_account_check_timer(self) -> None:
        timer = getattr(self, "_account_check_timer", None)
        self._account_check_timer = None
        if timer is None:
            return
        try:
            timer.stop()
            timer.deleteLater()
        except RuntimeError:

            pass

    def cleanup_account_check(self) -> None:






        self._account_check_token = getattr(self, "_account_check_token", 0) + 1
        self._launch_account_pending = False
        self._stop_account_check_timer()

    def set_launch_enabled(self, enabled: bool) -> None:








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







        self._apply_feature_visibility(activated)
        if not activated:


            self._launch_account_pending = False

            self._tier_confirmed = False
        if activated:
            self.hide_trial_info()
            self._update_layer_warning()
            self.set_launch_state()

            self._stop_pairing_wait()
        else:
            self._setup_header.setVisible(True)
            self._connect_section.setVisible(True)
            self._stop_pairing_wait()
            self._activation_message.setVisible(False)
            self.hide_trial_info()
        self._sync_pro_pill()

    def _apply_feature_visibility(self, activated: bool) -> None:






        self._vectorize_btn.setVisible(activated and is_feature_enabled("vectorize"))
        self._set_swipe_button_visible(activated and is_feature_enabled("swipe"))
        markup_available = is_feature_enabled("markup")
        for container in (self._prompt_container, self._result_prompt_container):
            container.set_markup_available(markup_available)

    def refresh_feature_visibility(self) -> None:








        try:
            self._apply_feature_visibility(bool(self._activated))

            self._sync_demo_button()
            self._sync_attach_buttons()
        except Exception:  # nosec B110
            pass
        try:

            self.check_for_updates()
        except Exception:  # nosec B110
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









        self._is_free_tier = is_free_tier

        self._tier_confirmed = True
        self._reset_date = reset_date

        if self._reference_widget is not None:
            self._reference_widget.set_free_tier(is_free_tier)




        if used is not None and limit is not None and not is_free_tier and not self._resolution_user_choice:
            self._selected_resolution = paid_tier_default()
        if used is not None and limit is not None:

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







        if not self._cached_limit:
            return ""
        unit_cost = self._resolution_credit_costs.get(
            "1K", DEFAULT_RESOLUTION_CREDIT_COSTS["1K"]
        )
        total = total_free_generations(self._cached_limit, unit_cost)
        if not total:
            return ""
        date_str = format_reset_date(self._reset_date) if self._reset_date else ""





        if date_str:
            title = get_export_copy(
                "wall.title", tr("Your {total} free edits return on {date}")
            )
            return title.replace("{total}", format_count(total)).replace("{date}", date_str)



        title = get_export_copy(
            "wall.title_no_date", tr("Your {total} free edits return next month")
        )
        return title.replace("{total}", format_count(total))

    def set_subscribe_url(self, url: str) -> None:

        if url:
            self._trial_info_url = url

    def show_trial_exhausted_info(self, fallback_message: str, subscribe_url: str):








        self._hide_limit_cta()
        title = get_export_copy(
            "dock.account.trial_exhausted_fallback_title",
            tr("You've used this month's free edits"),
        )


        note = self._wall_title() or (fallback_message or "").strip()

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



        if _prewall_dismissed_this_session():
            return



        self._prewall_url = cta_url
        self._prewall_banner.show_low(
            get_export_copy("prewall.text", tr("Last free edit this month.")),

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



        from ...core import telemetry
        from ...core import telemetry_events as te
        from ..pro_page_link import open_pro_page

        def report(checkout_link: str) -> None:
            telemetry.track(te.SUBSCRIBE_LINK_CLICKED,
                            {"source": source, "checkout_link": checkout_link})


            telemetry.flush()

        open_pro_page(
            cta_source,
            fallback_url,
            client=getattr(self, "_library_client", None),
            auth_manager=getattr(self, "_library_auth_manager", None),
            on_outcome=report,
        )

    def _on_exit_clicked(self):

        self.exit_clicked.emit()

    def _on_connect_clicked(self):


        import secrets
        self._pending_pairing_code = secrets.token_urlsafe(32)
        self.show_pairing_waiting()
        self.pairing_requested.emit(self._pending_pairing_code)

    def _on_pairing_reopen_clicked(self):

        if self._pending_pairing_code:
            self.pairing_requested.emit(self._pending_pairing_code)

    def set_pairing_link(self, url: str):


        self._pairing_link = url or ""

    def _on_pairing_copy_clicked(self):


        if not self._pairing_link:
            return
        from qgis.PyQt.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self._pairing_link)
        self._pairing_copy_btn.setText(get_export_copy("dock.account.pairing_copied", tr("Copied!")))


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

        self._pairing_active = True
        self._pairing_status.setText(
            get_export_copy("dock.account.pairing_waiting", tr("Finish signing in on the page that just opened"))
        )
        self._connect_section.setVisible(False)
        self._activation_message.setVisible(False)
        self._pairing_wait_section.setVisible(True)
        self._pairing_anim_timer.start()

    def show_pairing_browser_seen(self):

        if self._pairing_active:
            self._pairing_status.setText(get_export_copy(
                "dock.account.pairing_browser_seen",
                tr("Browser page open. Finish signing in to connect."),
            ))

    def show_pairing_stalled_hint(self):


        if self._pairing_active:
            self._pairing_status.setText(get_export_copy(
                "dock.account.pairing_stalled_hint",
                tr("Still waiting. If the page did not open or shows an error, "
                   "click Open again or copy the link into another browser."),
            ))

    def show_pairing_network_problem(self, next_step: str):



        if self._pairing_active:
            text = tr("Still waiting, but AI Edit cannot reach the server to check your sign-in.")
            self._pairing_status.setText(f"{text} {next_step}".strip())

    def _stop_pairing_wait(self):

        self._pairing_active = False
        self._pairing_anim_timer.stop()
        self._pairing_wait_section.setVisible(False)

    def show_pairing_idle(self):

        self._stop_pairing_wait()
        self._connect_section.setVisible(True)

    def _on_limit_cta_clicked(self):
        if self._limit_cta_url:
            self._open_plans("limit_cta", "plugin_limit_cta", self._limit_cta_url)

    def _hide_limit_cta(self):
        self._pro_limit_card.clear()
        self._limit_cta_url = ""
