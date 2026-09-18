"""The account erasure request, run from the settings dialog's danger zone.

Every method runs as an ``AccountSettingsDialog`` method: it reads
``self._client``, ``self._auth``, ``self._email`` and ``self._delete_btn``, and
shows a refusal with ``self._show_account_notice`` (account_page.py).
"""
from __future__ import annotations

from ....core.config_store import get_export_copy
from ....core.i18n import tr
from ....workers.generic_request_task import GenericRequestTask


class AccountDeletionMixin:
    """Typed confirmation, the request, and a sentence for every refusal."""

    def _on_delete_account(self) -> None:
        """Ask for the typed confirmation, then send it."""
        from ..delete_account_dialog import show_delete_account

        if self._delete_worker is not None or not self._email:
            return
        typed = show_delete_account(self, self._email)
        if not typed:
            return
        self._start_account_deletion(typed)

    def _start_account_deletion(self, confirm: str) -> None:
        from qgis.core import QgsApplication

        self._show_account_notice("")
        self._delete_detail = {}
        if self._delete_btn is not None:
            self._delete_btn.setEnabled(False)
            self._delete_btn.setText(
                get_export_copy("dialogs.account_settings_dialog.deleting_status", tr("Deleting...")))

        auth = self._auth
        client = self._client
        detail = self._delete_detail

        def _work():
            answer = client.delete_account(auth=auth, confirm=confirm)
            # Kept for the failure slot: purge_after and retry_after do not
            # survive the (message, code) pair the task emits.
            if isinstance(answer, dict):
                detail.update(answer)
            return answer

        # Deliberately not silent: a rare, irreversible action belongs in the
        # task manager where the user can see it happen.
        self._delete_worker = GenericRequestTask(
            get_export_copy(
                "dialogs.account_settings_dialog.deleting_task_title", tr("Deleting account")),
            _work,
        )
        self._delete_worker.succeeded.connect(self._on_delete_succeeded)
        self._delete_worker.failed.connect(self._on_delete_failed)
        QgsApplication.taskManager().addTask(self._delete_worker)

    def _drop_delete_worker(self) -> None:
        """Detach the deletion task. It is never cancelled: the request is
        already with the server, so it only stops answering into a dialog
        that is on its way out."""
        if self._delete_worker is None:
            return
        try:
            self._delete_worker.succeeded.disconnect()
            self._delete_worker.failed.disconnect()
        except (RuntimeError, TypeError):  # nosec B110
            pass
        self._delete_worker = None

    def _reset_delete_button(self) -> None:
        try:
            if self._delete_btn is not None:
                self._delete_btn.setEnabled(bool(self._email))
                self._delete_btn.setText(tr("Delete"))
        except RuntimeError:  # nosec B110 - the page is already gone.
            pass

    def _on_delete_succeeded(self, payload) -> None:
        from ..confirm_dialog import SUCCESS, info_box

        self._delete_worker = None
        data = payload if isinstance(payload, dict) else {}
        when = self._format_purge_date(data.get("purge_after", ""))

        # Emitted first: the plugin signs out and drops the local caches while
        # the confirmation is still on screen explaining why.
        self.account_deleted.emit()

        if when:
            body = tr("Data erased on {date}.").format(date=when)
        else:
            body = get_export_copy(
                "dialogs.account_settings_dialog.data_erased_grace_period",
                tr("Data erased after the grace period."),
            )
        info_box(
            self,
            get_export_copy(
                "dialogs.account_settings_dialog.deletion_scheduled_title", tr("Account deletion scheduled")),
            "{} {}".format(
                body,
                get_export_copy(
                    "dialogs.account_settings_dialog.signed_out_notice",
                    tr("All TerraLab plugins are signed out. To cancel, sign in on terra-lab.ai."),
                ),
            ),
            tone=SUCCESS,
        )
        self.accept()

    def _on_delete_failed(self, message: str, code: str) -> None:
        self._delete_worker = None
        self._show_account_notice(self._delete_failure_text(message, code))
        self._reset_delete_button()

    def _delete_failure_text(self, message: str, code: str) -> str:
        """The refusal, as a sentence with something to do in it."""
        key = (code or "").strip().upper()
        detail = self._delete_detail or {}

        if key == "CONFIRM_MISMATCH":
            return get_export_copy(
                "dialogs.account_settings_dialog.confirm_mismatch_error",
                tr("That email does not match. Try again."),
            )
        if key in ("ALREADY_SCHEDULED", "ACCOUNT_DELETION_SCHEDULED"):
            when = self._format_purge_date(detail.get("purge_after", ""))
            if when:
                return tr("Deletion already set for {date}. Cancel on terra-lab.ai.").format(date=when)
            return get_export_copy(
                "dialogs.account_settings_dialog.already_scheduled_error",
                tr("Deletion already scheduled. Cancel on terra-lab.ai."),
            )
        if key == "RATE_LIMITED":
            seconds = detail.get("retry_after")
            if isinstance(seconds, (int, float)) and seconds > 0:
                return tr("Too many attempts. Try again in {seconds} "
                          "seconds.").format(seconds=int(seconds))
            return get_export_copy(
                "dialogs.account_settings_dialog.rate_limited_error",
                tr("Too many attempts. Try again soon."),
            )
        if key == "NO_ACCOUNT":
            return get_export_copy(
                "dialogs.account_settings_dialog.no_account_error",
                tr("No account linked to this key."),
            )
        if key in ("NO_AUTH", "INVALID_KEY"):
            return get_export_copy(
                "dialogs.account_settings_dialog.invalid_key_error",
                tr("Session expired. Sign out and back in."),
            )
        if key == "SUBSCRIPTION_INACTIVE":
            return get_export_copy(
                "dialogs.account_settings_dialog.subscription_inactive_error",
                tr("Subscription inactive. Manage it on terra-lab.ai."),
            )
        if key == "BAD_REQUEST":
            return get_export_copy(
                "dialogs.account_settings_dialog.bad_request_error",
                tr("Confirmation refused. Close and try again."),
            )
        return (message or "").strip() or get_export_copy(
            "dialogs.account_settings_dialog.delete_account_fallback_error",
            tr("Account not deleted."),
        )

    @staticmethod
    def _format_purge_date(iso_str) -> str:
        """The erasure date, written the way the reader's locale writes one.

        "" when there is nothing usable, so every caller can fall back to
        naming the grace period instead of a date.
        """
        from qgis.PyQt.QtCore import QDateTime, QLocale, Qt

        text = str(iso_str or "").strip()
        if not text:
            return ""
        stamp = QDateTime.fromString(text, Qt.DateFormat.ISODateWithMs)
        if not stamp.isValid():
            stamp = QDateTime.fromString(text, Qt.DateFormat.ISODate)
        if not stamp.isValid():
            return text
        return QLocale().toString(stamp.toLocalTime().date(), QLocale.FormatType.LongFormat)
