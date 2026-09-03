"""Paid-tier credit ceiling: the "write to us for a custom plan" path.

A Pro account near or at its monthly limit gets a human contact instead of
the free-tier wall. The address is shown in the dock and the primary button
copies it to the clipboard: no mail scheme, no OS handler to depend on. The
wording, the address and the "running low" threshold are served (see
core.pro_ceiling), so they change with a website deploy. The widgets reused
here are the status box, the address label, the "Manage plan" button and
the pre-wall banner, all built in build.py / build_result.py.
"""
from __future__ import annotations

import html

from qgis.PyQt.QtWidgets import QApplication, QPushButton

from ...core import qt_compat as QtC
from ...core.config_store import get_activation_copy
from ...core.i18n import tr
from ...core.pro_ceiling import pro_ceiling_contact_email

_COPIED_MS = 2000


def copy_cta_text() -> str:
    return get_activation_copy("pro_ceiling.copy_cta", tr("Copy email"))


def copied_text() -> str:
    return get_activation_copy("pro_ceiling.copied", tr("Copied!"))


def custom_needs_line(email: str) -> str:
    """The served "Custom needs? Write to us: {email}" sentence, filled."""
    text = get_activation_copy("pro_ceiling.custom_needs", tr("Custom needs? Write to us: {email}"))
    return text.replace("{email}", email)


def copy_email_to_clipboard(button: QPushButton, email: str, idle_text: str | None = None) -> None:
    """Copy ``email`` and swap ``button`` to "Copied!" for two seconds.

    The revert timer is parented to the button, so a dock torn down inside
    the window never receives the call on a freed widget.
    """
    clipboard = QApplication.clipboard()
    if clipboard is None:
        return
    clipboard.setText(email)
    revert = idle_text if idle_text is not None else copy_cta_text()
    button.setText(copied_text())
    QtC.safe_single_shot(_COPIED_MS, button, lambda: button.setText(revert))


class DockProCeilingMixin:
    """Pro low-credit banner and Pro limit block for AIEditDockWidget."""

    def _is_pro_low(self) -> bool:
        return self._paywall_state() == "pro_low"

    def show_pro_limit_info(self, title: str, manage_url: str) -> None:
        """The Pro block: served title, served body, the address in bold,
        "Copy email" as the primary action, "Manage plan" kept underneath as
        the secondary one."""
        body = get_activation_copy(
            "pro_ceiling.body",
            tr("Need more this month? Write to us and we set up a plan that fits your volume."),
        )
        self._show_status_box(f"{title}\n{body}", "error")
        self._trial_info_box.setVisible(False)
        self.hide_prewall_info()
        # Bold means rich text, and the address may come from the server, so
        # it is escaped here as well as validated there.
        self._pro_contact_email.setText(f"<b>{html.escape(pro_ceiling_contact_email(), quote=False)}</b>")
        self._pro_contact_email.setVisible(True)
        self._pro_contact_btn.setText(copy_cta_text())
        self._pro_contact_btn.setVisible(True)
        self._limit_cta_url = manage_url
        self._limit_cta_btn.setVisible(True)

    def pro_limit_title(self, fallback: str) -> str:
        """The served block title filled from the cached balance, or the
        caller's sentence when the cache does not confirm the limit yet."""
        used, limit = self._cached_used, self._cached_limit
        if not isinstance(used, int) or not isinstance(limit, int) or limit <= 0 or used < limit:
            return fallback
        title = get_activation_copy("pro_ceiling.block_title", tr("Monthly limit reached ({used}/{limit})"))
        return title.replace("{used}", str(used)).replace("{limit}", str(limit))

    def show_pro_low_info(self) -> None:
        """Pre-wall banner, Pro flavour: how many credits are left this month,
        then the address, and a button that copies it. State-driven like the
        free one (set_credits)."""
        used, limit = self._cached_used, self._cached_limit
        left = max(0, limit - used)
        email = pro_ceiling_contact_email()
        text = get_activation_copy("pro_ceiling.low_title", tr("{left} of {total} credits left this month"))
        text = text.replace("{left}", str(left)).replace("{total}", str(limit))
        self._prewall_text.setText(f"{text}. {custom_needs_line(email)}")
        self._prewall_btn.setText(copy_cta_text())
        # The banner shows only while this is set; the address stands in for
        # the free tier's http link, and _on_prewall_cta_clicked routes on
        # the paywall state, never on this value.
        self._prewall_url = email
        self._prewall_banner.setVisible(True)

    def _on_pro_contact_clicked(self, button: QPushButton | None = None) -> None:
        """Copy the served address from the block or the banner button."""
        from ...core import telemetry
        from ...core import telemetry_events as te
        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "pro_contact"})
        target = button if isinstance(button, QPushButton) else self._pro_contact_btn
        copy_email_to_clipboard(target, pro_ceiling_contact_email())
