"""Error report dialog + log collector for the AI Edit plugin.

Captures QGIS log messages and surfaces a "copy logs, send email" dialog so
users report genuine failures with diagnostics attached instead of only seeing
a red inline message.
"""
from __future__ import annotations

import html
import os
import platform
import re
import sys
from collections import deque
from datetime import datetime, timezone
from urllib.parse import quote

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ...core import qt_compat as QtC
from ...core.auth.activation_manager import get_support_email
from ...core.config_store import get_export_copy, get_export_dial
from ...core.i18n import tr
from ...core.log_scrub import scrub_user_paths
from ...core.logger import log_warning
from ..external_url import open_mailto
from ..keyboard_focus import settle_dialog_default_button

# How long a displayed error message/detail is truncated to before it is shown
# or wrapped into the fallback message box.
_ERROR_DISPLAY_MAX_CHARS = 500
# How long the "Copied!" confirmation stays on the copy button before it
# reverts to its normal label.
_COPY_LABEL_RESET_MS = 2000

# Single source of truth for the support address. dock_widget imports it here.
SUPPORT_EMAIL = "yvann.barbot@terra-lab.ai"

# Sentinel href the inline status box uses to open this report dialog from a
# link click (plugin.py builds the link, dock_widget._on_status_link routes it).
# Not a real URL, so it must never be handed to QDesktopServices.openUrl.
REPORT_PROBLEM_HREF = "terralab://report-problem"

_TAG_PATTERN = re.compile(r"<[^>]+>")


def plain_error_text(message: str) -> str:
    """The status-box message as words a person reads: the box renders rich
    text, so its messages carry links and escaped entities, and the dialog's
    plain label printed them raw ("Couldn&#x27;t", "<a href=...>")."""
    return " ".join(html.unescape(_TAG_PATTERN.sub("", message or "")).split())


_log_buffer = deque(maxlen=100)
_log_collector_connected = False


def _anonymize_paths(text: str) -> str:
    """Hide the username before the text enters a bug report the user will
    paste into a public issue or an email. Endpoints are scrubbed at the
    source (the API client masks them before they ever reach the QGIS log),
    not here: the URL pattern also matches dotted version numbers, which a
    diagnostic report must keep."""
    if not text:
        return text
    return scrub_user_paths(text)


def start_log_collector():
    """Connect to QgsMessageLog to capture AI Edit messages."""
    global _log_collector_connected
    if _log_collector_connected:
        return
    try:
        from qgis.core import QgsApplication

        QgsApplication.messageLog().messageReceived.connect(_on_log_message)
        _log_collector_connected = True
    except Exception:
        pass  # nosec B110


def stop_log_collector():
    """Disconnect from QgsMessageLog."""
    global _log_collector_connected
    if not _log_collector_connected:
        return
    try:
        from qgis.core import QgsApplication

        QgsApplication.messageLog().messageReceived.disconnect(_on_log_message)
    except (TypeError, RuntimeError):  # already disconnected or log gone at unload
        pass
    _log_collector_connected = False


def _on_log_message(message, tag, level):
    if tag == "AI Edit":
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        _log_buffer.append(f"[{timestamp}] {message}")


def _get_recent_logs() -> str:
    if not _log_buffer:
        return "(No logs captured this session)"
    logs = "\n".join(_log_buffer)
    return _anonymize_paths(logs)


def _collect_diagnostic_info(error_message: str, request_id: str = "") -> str:
    """Collect system diagnostic info for error reporting.

    `request_id` is the server correlation key returned by a generation; when
    present it is the fastest way for support to find the matching backend logs.
    """
    lines = []
    lines.append("=== AI Edit - Error Report ===")
    lines.append("")

    if error_message:
        lines.append("--- Error ---")
        lines.append(error_message)
        lines.append("")

    if request_id:
        lines.append("--- Request ---")
        lines.append(f"Request ID: {request_id}")
        lines.append("")

    # Plugin version
    lines.append("--- Plugin ---")
    try:
        plugin_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        metadata_path = os.path.join(plugin_dir, "metadata.txt")
        if os.path.exists(metadata_path):
            with open(metadata_path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("version="):
                        lines.append(
                            "Version: {}".format(line.strip().split("=", 1)[1])
                        )
                        break
    except Exception:
        lines.append("Version: unknown")
    lines.append("")

    # System info
    lines.append("--- System ---")
    lines.append(
        f"OS: {sys.platform} ({platform.system()} {platform.release()})"
    )
    lines.append(f"Architecture: {platform.machine()}")
    lines.append(
        f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )

    try:
        from qgis.core import Qgis

        lines.append(f"QGIS: {Qgis.QGIS_VERSION}")
    except Exception:
        lines.append("QGIS: unknown")
    lines.append("")

    # Recent logs
    lines.append("--- Recent Logs ---")
    lines.append(_get_recent_logs())

    lines.append("")
    lines.append("=== End of Report ===")
    report = "\n".join(lines)
    return _anonymize_paths(report)


class ErrorReportDialog(QDialog):
    """Copy-logs-then-email dialog.

    With an `error_message` it frames a genuine failure; without one it reads as
    a friendly user-initiated bug report. Either way the copy button puts the
    anonymized diagnostic bundle on the clipboard and the email button opens the
    user's mail client addressed to support.
    """

    def __init__(self, error_message: str = "", request_id: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(get_export_copy(
            "dialogs.error_report_dialog.title", tr("Report a problem")))
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)

        self._error_message = plain_error_text(error_message)
        self._diagnostic_info = _collect_diagnostic_info(self._error_message, request_id)

        self._setup_ui()

    def _setup_ui(self):
        # AI Agent's report sheet: the explanation, the green copy step, the
        # ghost email step, both 32 px pills. Tokens are imported lazily: a
        # module-top import of ..dock pulls the dock package __init__, which
        # imports back here -> circular import.
        from ..dock.design_tokens import (
            BTN_GHOST_QSS,
            BTN_PRIMARY_QSS,
            BTN_PX,
            FONT_BODY,
            INK,
            INK_2,
            INSET,
            LINE,
            RADIUS_CONTROL,
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 18, 20, 18)

        max_chars = get_export_dial(
            "dialogs.error_report_dialog.error_display_max_chars", _ERROR_DISPLAY_MAX_CHARS)
        if self._error_message:
            error_label = QLabel(self._error_message[:max_chars])
            error_label.setWordWrap(True)
            error_label.setTextFormat(Qt.TextFormat.PlainText)
            error_label.setStyleSheet(
                f"background: {INSET}; color: {INK}; border: 1px solid {LINE};"
                f" border-radius: {RADIUS_CONTROL}px; padding: 8px 10px; font-size: {FONT_BODY}px;")
            layout.addWidget(error_label)
            help_text = "{}\n\n{}".format(
                get_export_copy(
                    "dialogs.error_report_dialog.help_with_error",
                    tr("Copy your logs with the button below and send them to our support email.")),
                get_export_copy(
                    "dialogs.error_report_dialog.help_fixed_promise",
                    tr("We will look into it and get back to you.")),
            )
        else:
            help_text = "{}\n\n{}".format(
                get_export_copy(
                    "dialogs.error_report_dialog.help_prompt", tr("Something not working?")),
                get_export_copy(
                    "dialogs.error_report_dialog.help_no_error_steps",
                    tr("Copy your logs and send them to us. We will look into it.")),
            )

        help_label = QLabel(help_text)
        help_label.setWordWrap(True)
        help_label.setStyleSheet(f"font-size: {FONT_BODY}px; color: {INK_2}; background: transparent;")
        layout.addWidget(help_label)

        self._copy_btn = QPushButton(get_export_copy(
            "dialogs.error_report_dialog.copy_logs_button", tr("1. Click to copy logs")))
        self._copy_btn.setStyleSheet(BTN_PRIMARY_QSS)
        self._copy_btn.setFixedHeight(BTN_PX)
        self._copy_btn.setCursor(QtC.PointingHandCursor)
        self._copy_btn.clicked.connect(self._on_copy)
        layout.addWidget(self._copy_btn)

        self._email_btn = QPushButton(
            tr("2. Click to send to {email}").format(
                email=get_support_email(SUPPORT_EMAIL)))
        self._email_btn.setToolTip(get_export_copy(
            "dialogs.error_report_dialog.email_button_tooltip", tr("Open email client")))
        self._email_btn.setStyleSheet(BTN_GHOST_QSS)
        self._email_btn.setFixedHeight(BTN_PX)
        self._email_btn.setCursor(QtC.PointingHandCursor)
        self._email_btn.clicked.connect(self._on_open_email)
        layout.addWidget(self._email_btn)
        # Enter copies the logs, the one filled action; never opens the mail client.
        settle_dialog_default_button(self, self._copy_btn)

    def _on_copy(self):
        QApplication.clipboard().setText(self._diagnostic_info)
        self._copy_btn.setText(get_export_copy(
            "dialogs.error_report_dialog.copied_confirmation", tr("Copied!")))
        # Timer parented to the button: if the dialog closes first, the timer
        # dies with it and never fires into a freed C++ button.
        QtC.safe_single_shot(
            get_export_dial("dialogs.error_report_dialog.copy_label_reset_ms", _COPY_LABEL_RESET_MS),
            self._copy_btn, self._restore_copy_label)

    def _restore_copy_label(self):
        try:
            self._copy_btn.setText(get_export_copy(
                "dialogs.error_report_dialog.copy_logs_button", tr("1. Click to copy logs")))
        except RuntimeError:  # dialog closed, button already deleted
            pass

    def _on_open_email(self):
        subject = quote("AI Edit - Bug Report")
        address = get_support_email(SUPPORT_EMAIL)
        if open_mailto(f"mailto:{address}?subject={subject}"):
            return
        # Many Windows installs have no mail client: hand over the address.
        QApplication.clipboard().setText(address)
        self._email_btn.setText(tr("Address copied, paste it in your mail"))


def show_error_report(parent, error_message: str = "", request_id: str = "") -> None:
    """Open the error report dialog. Never lets a UI error mask the original one.

    If the dialog itself fails to construct or show, fall back to a plain
    styled message so the user still gets the original error and a way to reach
    support, instead of the report action silently doing nothing.
    """
    try:
        dialog = ErrorReportDialog(error_message, request_id, parent)
        try:
            dialog.exec()
        finally:
            # Parented to the main window, so it would otherwise live (one
            # more per open) until QGIS quits.
            dialog.deleteLater()
    except Exception as err:
        log_warning(f"Failed to open the error report dialog: {err}")
        detail = plain_error_text(error_message) or get_export_copy(
            "dialogs.error_report_dialog.no_details_fallback",
            tr("No additional details are available."))
        contact = tr("Please contact {email} for help.").format(
            email=get_support_email(SUPPORT_EMAIL)
        )
        max_chars = get_export_dial(
            "dialogs.error_report_dialog.error_display_max_chars", _ERROR_DISPLAY_MAX_CHARS)
        try:
            from .confirm_dialog import error_box

            error_box(
                parent,
                get_export_copy("dialogs.error_report_dialog.title", tr("Report a problem")),
                contact,
                detail=detail[:max_chars],
                selectable=True,
            )
        except Exception as box_err:  # noqa: BLE001 - the log keeps the original error
            log_warning(f"Failed to show the error fallback: {box_err}")
