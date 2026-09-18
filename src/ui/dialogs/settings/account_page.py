"""Account page of the settings dialog: who is signed in, the plan and the
credits, then Advanced (local preferences) and the danger zone.

Every method runs as an ``AccountSettingsDialog`` method. The identity card and
the usage row are repainted into ``self._account_col`` when the account load
lands; Advanced and the danger zone are built once, below them, so a repaint
never takes them away.
"""
from __future__ import annotations

import html

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ....core.config_store import get_export_copy
from ....core.errors import NETWORK_ERROR_CODES, TRANSIENT_SERVER_ERROR_CODES
from ....core.i18n import tr
from ...dock.design_tokens import (
    BTN_DANGER_GHOST_QSS,
    BTN_GHOST_QSS,
    BTN_LINK_QSS,
    DARK,
    INK,
    ON_ACCENT,
    RADIUS_CONTROL,
    RED_TEXT,
    category_fill,
    category_line,
    category_tint,
)
from .category_tile import avatar_category
from .plan_state import AccountPlan, plan_display_name
from .widgets import (
    ROW_NOTE_QSS,
    Page,
    SettingGroup,
    SettingRow,
    make_button,
)

# The identity card's round badge, px.
AVATAR_PX = 36
_EMAIL_QSS = f"font-size: 13px; font-weight: 600; color: {INK}; background: transparent;"
_NOTICE_QSS = (
    f"QLabel#accountNotice {{ background: {category_tint('coral')}; color: {RED_TEXT};"
    f" border: 1px solid {category_line('coral')};"
    f" border-radius: {RADIUS_CONTROL}px; padding: 8px 12px; font-size: 12px; }}"
)


class AccountPageMixin:
    """Identity, plan, credits; Advanced and the danger zone below."""

    def _build_account_page(self) -> Page:
        page = Page(tr("Account"), tr("Sign-in, plan and privacy"), self,
                    glyph="person", category="green")
        # A refusal from the server (a deletion that did not go through) is
        # shown here, above the cards, which stay: the account still exists.
        self._account_notice = QLabel("", page.body_widget)
        self._account_notice.setObjectName("accountNotice")
        self._account_notice.setStyleSheet(_NOTICE_QSS)
        self._account_notice.setWordWrap(True)
        self._account_notice.setVisible(False)
        page.add(self._account_notice)

        self._account_box = QWidget(page.body_widget)
        self._account_col = QVBoxLayout(self._account_box)
        self._account_col.setContentsMargins(0, 0, 0, 0)
        self._account_col.setSpacing(14)
        page.add(self._account_box)

        page.add_group_title(tr("Advanced"))
        page.add(self._build_advanced_group(page.body_widget))
        self._danger_title = page.add_group_title(tr("Danger zone"))
        self._danger_group = page.add(self._build_danger_group(page.body_widget))
        return page

    def _show_account_notice(self, text: str) -> None:
        self._account_notice.setText(text)
        self._account_notice.setVisible(bool(text))

    def _clear_account_box(self) -> None:
        self._avatar = None
        while self._account_col.count():
            item = self._account_col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparented now: deleteLater runs on the next event loop pass,
                # and a widget still parented would paint over the new card.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _paint_account_loading(self) -> None:
        self._clear_account_box()
        group = SettingGroup(self._account_box)
        group.add_row(SettingRow(
            get_export_copy("dialogs.account_settings_dialog.loading_label", tr("Loading...")),
            "", None, group))
        self._account_col.addWidget(group)

    def _paint_account_error(self, message: str, code: str = "") -> None:
        """One card: what went wrong in a few words, then the button that
        fixes it (AI Agent's error card). A connection problem offers Retry;
        a key the server refused offers Sign out, since Retry cannot help."""
        self._clear_account_box()
        title, note, can_retry = _account_error_copy(message, code)
        group = SettingGroup(self._account_box)
        buttons = QWidget(group)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        if can_retry:
            retry = make_button(
                get_export_copy("dialogs.account_settings_dialog.retry_button", tr("Retry")),
                BTN_GHOST_QSS, buttons)
            retry.clicked.connect(self._fetch_account)
            row.addWidget(retry)
        out = make_button(
            get_export_copy("dialogs.account_settings_dialog.sign_out_button", tr("Sign out")),
            BTN_GHOST_QSS, buttons)
        out.clicked.connect(self._on_sign_out)
        row.addWidget(out)
        error_row = SettingRow(title, note, buttons, group)
        error_row.note_label.setTextFormat(Qt.TextFormat.PlainText)
        group.add_row(error_row)
        self._account_col.addWidget(group)

    def _paint_signed_out(self) -> None:
        """No account on this computer: one row that says so, and Sign in."""
        from ...dock.design_tokens import BTN_PRIMARY_QSS

        self._clear_account_box()
        group = SettingGroup(self._account_box)
        sign_in = make_button(
            get_export_copy("dialogs.account_settings_dialog.sign_in_button", tr("Sign in")),
            BTN_PRIMARY_QSS, group)
        sign_in.clicked.connect(self._on_sign_in)
        group.add_row(SettingRow(
            get_export_copy("dialogs.account_settings_dialog.signed_out_row", tr("Not signed in")),
            get_export_copy("dialogs.account_settings_dialog.signed_out_row_note",
                            tr("Sign in to see your plan")),
            sign_in, group))
        self._account_col.addWidget(group)
        self._paint_billing_message(get_export_copy(
            "dialogs.account_settings_dialog.billing_signed_out", tr("Sign in to see your plan.")))
        self._upgrade_holder.setVisible(False)
        # Nothing to delete without an account: no dead red card either.
        self._danger_title.setVisible(False)
        self._danger_group.setVisible(False)

    def _on_sign_in(self) -> None:
        self.sign_in_requested.emit()
        self.accept()

    def _paint_account(self, data: dict, plan: AccountPlan) -> None:
        self._clear_account_box()
        email = str(data.get("email") or "-")
        group = SettingGroup(self._account_box)
        group.add_row(self._identity_row(group, email, plan))
        usage = self._usage_row(group, plan)
        if usage is not None:
            group.add_row(usage)
        if plan.is_free:
            # Outline: the rail's "Upgrade to Pro" is the window's one filled
            # button.
            upgrade = make_button(tr("Upgrade"), BTN_GHOST_QSS, group)
            upgrade.setAccessibleName(tr("Upgrade to Pro"))
            upgrade.clicked.connect(self._on_upgrade)
            group.add_row(SettingRow(
                tr("Pro plan"),
                tr("More edits, commercial use"),
                upgrade, group))
        self._account_col.addWidget(group)
        avatar_url = data.get("avatar_url")
        if avatar_url:
            self._start_avatar_load(avatar_url)

    def _identity_row(self, parent: QWidget, email: str, plan: AccountPlan) -> QFrame:
        chip = QFrame(parent)
        chip.setObjectName("settingsRow")
        row = QHBoxLayout(chip)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)

        avatar = QLabel(email[:1].upper() if email and email != "-" else "?", chip)
        avatar.setFixedSize(AVATAR_PX, AVATAR_PX)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(
            f"background: {category_fill(avatar_category(email))}; color: {ON_ACCENT};"
            f" border-radius: {AVATAR_PX // 2}px;"
            " font-size: 15px; font-weight: 700;")
        self._avatar = avatar
        row.addWidget(avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(1)
        email_label = QLabel(email, chip)
        email_label.setTextFormat(Qt.TextFormat.PlainText)
        email_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        email_label.setStyleSheet(_EMAIL_QSS)
        words.addWidget(email_label)
        words.addWidget(self._plan_line(chip, plan))
        row.addLayout(words, 1)

        manage = make_button(tr("Manage"), BTN_GHOST_QSS, chip)
        manage.setToolTip(get_export_copy(
            "dialogs.account_settings_dialog.manage_account_tooltip",
            tr("Opens your dashboard in the browser")))
        manage.setAccessibleName(get_export_copy(
            "dialogs.account_settings_dialog.manage_account_button", tr("Manage account")))
        manage.clicked.connect(self._open_dashboard)
        row.addWidget(manage, 0, Qt.AlignmentFlag.AlignVCenter)
        out = make_button(
            get_export_copy("dialogs.account_settings_dialog.sign_out_button", tr("Sign out")),
            BTN_GHOST_QSS, chip)
        out.clicked.connect(self._on_sign_out)
        row.addWidget(out, 0, Qt.AlignmentFlag.AlignVCenter)
        return chip

    def _plan_line(self, parent: QWidget, plan: AccountPlan) -> QLabel:
        """"Pro plan", or "Free plan · Personal, non-commercial use" (AI
        Agent's words), and the status when it is not the ordinary one."""
        name = html.escape(plan_display_name(plan), quote=False)
        parts = [name]
        if plan.is_free:
            parts.append(html.escape(get_export_copy(
                "dialogs.account_settings_dialog.free_use_note",
                tr("Personal, non-commercial use"),
            ), quote=False))
        if plan.has_subscription and plan.status not in ("active", "trialing"):
            from ..account_settings_dialog import _status_display

            status_text, status_color = _status_display(plan.status, dark=DARK)
            parts.append(f"<span style='color:{status_color};'>{status_text}</span>")
        label = QLabel(" · ".join(parts), parent)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setStyleSheet(ROW_NOTE_QSS)
        label.setWordWrap(True)
        return label

    def _usage_row(self, parent: QWidget, plan: AccountPlan) -> QFrame | None:
        """One quiet row: the balance lives on Billing only (the same call as
        AI Segmentation), and the link here switches the sidebar to it."""
        link = make_button(tr("See usage"), BTN_LINK_QSS, parent)
        link.setToolTip(tr("Credits left and reset date"))
        link.clicked.connect(lambda: self.show_page(getattr(self, "_billing_row", 1)))
        return SettingRow(tr("Usage"), "", link, parent)

    def _build_danger_group(self, parent: QWidget) -> SettingGroup:
        """Its own heading and card: this one leaves the plugin for good."""
        group = SettingGroup(parent, category="coral")
        self._delete_btn = make_button(tr("Delete"), BTN_DANGER_GHOST_QSS, group)
        self._delete_btn.setToolTip(tr("Confirm with your email. Cancel on terra-lab.ai during the grace period."))
        self._delete_btn.clicked.connect(self._on_delete_account)
        # Nothing to delete until the account has loaded and named itself.
        self._delete_btn.setEnabled(False)
        row = SettingRow(
            get_export_copy("dialogs.account_settings_dialog.delete_account_button", tr("Delete account")),
            get_export_copy(
                "dialogs.account_settings_dialog.delete_account_body",
                tr("Erases all data, stops every TerraLab plugin"),
            ),
            self._delete_btn, group)
        group.add_row(row)
        return group


def _account_error_copy(message: str, code: str) -> tuple[str, str, bool]:
    """(title, note, offer Retry) for a failed account load, by error code.

    The raw server sentence was the whole card before, "Account not loaded"
    over a line nobody could act on."""
    key = (code or "").strip().upper()
    if key in NETWORK_ERROR_CODES or key == "NO_CONNECTION":
        return (
            get_export_copy("dialogs.account_settings_dialog.offline_title",
                            tr("Could not reach TerraLab")),
            get_export_copy("dialogs.account_settings_dialog.offline_note",
                            tr("Check your connection, then retry.")),
            True,
        )
    if key in ("INVALID_KEY", "KEY_REVOKED", "NO_AUTH"):
        return (
            get_export_copy("dialogs.account_settings_dialog.signed_out_title",
                            tr("This computer is no longer signed in")),
            get_export_copy("dialogs.account_settings_dialog.signed_out_note",
                            tr("Sign out, then sign in again.")),
            False,
        )
    if key in TRANSIENT_SERVER_ERROR_CODES:
        return (
            get_export_copy("dialogs.account_settings_dialog.server_busy_title",
                            tr("TerraLab is busy right now")),
            get_export_copy("dialogs.account_settings_dialog.server_busy_note",
                            tr("Try again in a moment.")),
            True,
        )
    return tr("Account not loaded"), (message or "").strip(), True
