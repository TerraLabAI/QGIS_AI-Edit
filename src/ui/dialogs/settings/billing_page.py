"""Billing page of the settings dialog: the plan in force, and the way to it.

Every method runs as an ``AccountSettingsDialog`` method: it paints into
``self._billing_col`` and calls ``self._on_upgrade`` (the dialog frame) and
``self._open_dashboard``.

Two cards side by side, AI Agent's layout. The left card is the one number
that matters, how many credits are left. The right card is where to go next:
the dashboard for a subscriber, the Pro offer for a free account. Prices live
on the website, so no card here carries one.
"""
from __future__ import annotations

from qgis.PyQt.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ....core import telemetry
from ....core import telemetry_events as te
from ....core.auth.activation_manager import get_contact_call_url, get_dashboard_url
from ....core.config_store import get_export_copy
from ....core.date_format import format_reset_date
from ....core.i18n import tr
from ....core.number_format import format_count
from ....core.pro_ceiling import pro_ceiling_contact_email
from ...dock.design_tokens import (
    ACCENT,
    BTN_GHOST_QSS,
    BTN_GHOST_WIDE_QSS,
)
from ...external_url import open_external
from .plan_state import AccountPlan, plan_display_name
from .widgets import BillingCard, BillingCardRow, Page, make_button, make_usage_bar, muted_label


class BillingPageMixin:
    """The plan card, then Manage (subscriber) or the Pro offer (free)."""

    def _build_billing_page(self) -> Page:
        page = Page(tr("Billing"), "", self, glyph="gem", category="amber")
        self._billing_box = QWidget(page.body_widget)
        self._billing_col = QVBoxLayout(self._billing_box)
        self._billing_col.setContentsMargins(0, 0, 0, 0)
        self._billing_col.setSpacing(14)
        page.add(self._billing_box)
        return page

    def _clear_billing(self) -> None:
        col = self._billing_col
        while col.count():
            item = col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _paint_billing_message(self, text: str) -> None:
        self._clear_billing()
        self._billing_col.addWidget(muted_label(text, self._billing_box))

    def _paint_billing(self, plan: AccountPlan) -> None:
        self._clear_billing()
        cards = BillingCardRow(self._billing_box)
        cards.add_card(self._billing_plan_card(cards, plan))
        if plan.is_free:
            cards.add_card(self._billing_offer_card(cards, plan))
        else:
            cards.add_card(self._billing_manage_card(cards))
        self._billing_col.addWidget(cards)
        self._billing_col.addWidget(self._billing_contact_card(self._billing_box, plan.is_free))

    def _billing_plan_card(self, parent: QWidget, plan: AccountPlan) -> BillingCard:
        """The plan in force and what is left of it this month."""
        card = BillingCard(plan_display_name(plan), parent, category="amber")
        left = plan.left
        if left is not None:
            card.add_stat(format_count(left), tr("credits left of {limit}").format(
                limit=format_count(plan.limit)))
            card.add(make_usage_bar(left, plan.limit or 0, card))
        else:
            # The account answered without a count: say so, instead of a
            # "No usage yet" that read as zero edits on a busy account.
            card.add_status(get_export_copy(
                "dialogs.account_settings_dialog.credits_unknown",
                tr("Credit count not available"),
            ))
        if plan.has_subscription and plan.status not in ("active", "trialing"):
            from ..account_settings_dialog import _status_display

            status_text, _color = _status_display(plan.status)
            card.add_status(status_text)
        # The one date formatter (core/date_format.py), in the reader's
        # locale; a date it cannot read shows nothing rather than raw ISO.
        reset_on = format_reset_date(plan.period_end) if plan.period_end else ""
        if reset_on:
            resets_word = get_export_copy("dialogs.account_settings_dialog.resets_label", tr("Resets"))
            card.add_note(f"{resets_word} {reset_on}")
        # Free's "Personal, non-commercial use" sits once, on the Account
        # page's plan line, as in AI Agent; the Pro card beside this one says
        # what commercial use takes.
        return card

    def _billing_offer_card(self, parent: QWidget, plan: AccountPlan) -> BillingCard:
        """What Pro adds, and the one green button of the page."""
        card = BillingCard(tr("AI Edit Pro"), parent)
        card.add_headline(tr("Keep editing with Pro"))
        if plan.left == 0:
            card.add_status(tr("Free credits used up this month"))
        card.add_point(get_export_copy(
            "dialogs.account_settings_dialog.pro_point_quality",
            tr("Detailed and Maximum quality"),
        ), ACCENT)
        card.add_point(get_export_copy(
            "dialogs.account_settings_dialog.pro_point_commercial",
            tr("Commercial use"),
        ), ACCENT)
        card.add_point(tr("Up to 12 reference images"), ACCENT)
        card.add_point(tr("Cancel anytime"), ACCENT)
        upgrade = QPushButton(tr("Upgrade to Pro"), card)
        upgrade.clicked.connect(self._on_upgrade)
        # Outline: the rail's "Upgrade to Pro" is the window's one filled
        # button.
        card.add_action(upgrade, BTN_GHOST_WIDE_QSS)
        card.add_note(tr("Checkout on terra-lab.ai"))
        return card

    def _billing_manage_card(self, parent: QWidget) -> BillingCard:
        """The way to the dashboard: plan, payment method, invoices."""
        card = BillingCard(tr("Manage"), parent)
        card.add_status(tr("Plan, payment and invoices"))
        manage = QPushButton(tr("Open dashboard"), card)
        manage.setToolTip(get_export_copy(
            "dialogs.account_settings_dialog.manage_account_tooltip",
            tr("Opens your dashboard in the browser")))
        manage.clicked.connect(self._open_dashboard)
        card.add_action(manage, BTN_GHOST_WIDE_QSS)
        return card

    def _billing_contact_card(self, parent: QWidget, is_free: bool) -> BillingCard:
        """A team buy has no self-serve path: the two ways to reach us."""
        heading = (
            get_export_copy(
                "dialogs.account_settings_dialog.contact_heading_team",
                tr("For a team?"),
            ) if is_free
            else get_export_copy(
                "dialogs.account_settings_dialog.contact_heading_more",
                tr("Need more?"),
            )
        )
        card = BillingCard(tr("Custom needs"), parent)
        card.add_headline(heading)
        card.add_status(get_export_copy(
            "dialogs.account_settings_dialog.contact_body",
            tr("Team seats, custom quota, invoices"),
        ))
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        row.setSpacing(8)
        contact_email = pro_ceiling_contact_email()
        copy_btn = make_button(
            get_export_copy("dialogs.account_settings_dialog.copy_email_button", tr("Copy email")),
            BTN_GHOST_QSS, card)
        copy_btn.setToolTip(contact_email)
        copy_btn.clicked.connect(lambda: self._on_copy_contact_email(copy_btn, contact_email))
        row.addWidget(copy_btn)
        # The URL is served (contact_call_url); only an empty shipped constant
        # hides the button.
        call_url = get_contact_call_url()
        if call_url:
            call_btn = make_button(
                get_export_copy("dialogs.account_settings_dialog.book_call_button", tr("Book a call")),
                BTN_GHOST_QSS, card)
            call_btn.clicked.connect(lambda: self._open_contact_call(call_url))
            row.addWidget(call_btn)
        row.addStretch(1)
        card.add_layout(row)
        return card

    def _open_dashboard(self) -> None:
        open_external(get_dashboard_url())

    @staticmethod
    def _on_copy_contact_email(button, address: str) -> None:
        """Put the address on the clipboard, and name the surface it came from."""
        from ...dock.pro_ceiling import copy_email_to_clipboard

        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "account_contact_copy"})
        copy_email_to_clipboard(
            button,
            address,
            idle_text=get_export_copy("dialogs.account_settings_dialog.copy_email_button", tr("Copy email")),
        )

    @staticmethod
    def _open_contact_call(url: str) -> None:
        """Open the booking page, and name the surface the click came from."""
        telemetry.track(te.SUBSCRIBE_LINK_CLICKED, {"source": "account_contact_call"})
        open_external(url)
