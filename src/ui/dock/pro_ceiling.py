









from __future__ import annotations

from qgis.PyQt.QtWidgets import QApplication, QPushButton

from ...core import qt_compat as QtC
from ...core.config_store import get_activation_copy, get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.number_format import format_count
from ...core.pro_ceiling import pro_ceiling_contact_email

_COPIED_MS = 2000


def copy_cta_text() -> str:
    return get_activation_copy("pro_ceiling.copy_cta", tr("Copy email"))


def copied_text() -> str:
    return get_activation_copy("pro_ceiling.copied", tr("Copied!"))


def custom_needs_line(email: str) -> str:

    text = get_activation_copy("pro_ceiling.custom_needs", tr("Custom needs? Write to us: {email}"))
    return text.replace("{email}", email)


def copy_email_to_clipboard(button: QPushButton, email: str, idle_text: str | None = None) -> None:





    clipboard = QApplication.clipboard()
    if clipboard is None:
        return
    clipboard.setText(email)
    revert = idle_text if idle_text is not None else copy_cta_text()
    button.setText(copied_text())
    QtC.safe_single_shot(
        get_export_dial("dock.pro_ceiling.copied_revert_ms", _COPIED_MS), button, lambda: button.setText(revert)
    )


class DockProCeilingMixin:


    def _is_pro_low(self) -> bool:
        return self._paywall_state() == "pro_low"

    def show_pro_limit_info(self, title: str, manage_url: str) -> None:



        body = get_activation_copy(
            "pro_ceiling.body",
            tr("Need more this month? Write to us and we set up a plan that fits your volume."),
        )
        self._hide_status_box()
        self._trial_info_box.setVisible(False)
        self.hide_prewall_info()
        self._limit_cta_url = manage_url

        self._pro_limit_card.show_contact(
            title,
            body,
            copy_cta_text(),
            pro_ceiling_contact_email(),
            get_export_copy("dock.build.manage_plan_btn", tr("Manage plan")),
        )

    def pro_limit_title(self, fallback: str) -> str:


        used, limit = self._cached_used, self._cached_limit
        if not isinstance(used, int) or not isinstance(limit, int) or limit <= 0 or used < limit:
            return fallback
        title = get_activation_copy("pro_ceiling.block_title", tr("Monthly limit reached ({used}/{limit})"))
        return title.replace("{used}", format_count(used)).replace("{limit}", format_count(limit))

    def show_pro_low_info(self) -> None:



        from .account import _prewall_dismissed_this_session

        if _prewall_dismissed_this_session():
            return
        used, limit = self._cached_used, self._cached_limit
        left = max(0, limit - used)
        email = pro_ceiling_contact_email()
        text = get_activation_copy("pro_ceiling.low_title", tr("{left} of {total} credits left this month"))
        text = text.replace("{left}", format_count(left)).replace("{total}", format_count(limit))



        self._prewall_url = email
        self._prewall_banner.show_low_contact(text, copy_cta_text(), custom_needs_line(email))

    def _on_pro_contact_clicked(self, button: QPushButton | None = None) -> None:

        from ...core import telemetry
        from ...core import telemetry_events as te
        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "pro_contact"})
        target = button if isinstance(button, QPushButton) else self._pro_limit_card.ghost_button
        copy_email_to_clipboard(target, pro_ceiling_contact_email())
