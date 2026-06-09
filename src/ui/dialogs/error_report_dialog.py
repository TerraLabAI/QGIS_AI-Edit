"""Error report dialog + log collector for the AI Edit plugin.

Captures QGIS log messages and surfaces a "copy logs, send email" dialog so
users report genuine failures with diagnostics attached instead of only seeing
a red inline message.
"""
from __future__ import annotations

import os
import platform
import re
import sys
from collections import deque
from datetime import datetime
from urllib.parse import quote

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ...core import qt_compat as QtC
from ...core.i18n import tr

# Single source of truth for the support address. dock_widget imports it here.
SUPPORT_EMAIL = "yvann.barbot@terra-lab.ai"

# Sentinel href the inline status box uses to open this report dialog from a
# link click (plugin.py builds the link, dock_widget._on_status_link routes it).
# Not a real URL, so it must never be handed to QDesktopServices.openUrl.
REPORT_PROBLEM_HREF = "terralab://report-problem"

_log_buffer = deque(maxlen=100)
_log_collector_connected = False


def _anonymize_paths(text: str) -> str:
    """Anonymize file paths to hide the username."""
    if not text:
        return text
    text = re.sub(r"/Users/[^/\s]+(?=/|$|\s)", "<USER>", text)
    text = re.sub(r"/home/[^/\s]+(?=/|$|\s)", "<USER>", text)
    text = re.sub(r"[A-Za-z]:[/\\]Users[/\\][^/\\\s]+(?=[/\\]|$|\s)", "<USER>", text)
    return re.sub(r"\\\\[^\\]+\\Users\\[^/\\\s]+(?=[/\\]|$|\s)", "<USER>", text)


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
    except (TypeError, RuntimeError):
        pass
    _log_collector_connected = False


def _on_log_message(message, tag, level):
    if tag == "AI Edit":
        timestamp = datetime.now().strftime("%H:%M:%S")
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
        self.setWindowTitle(tr("Report a problem"))
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)

        self._error_message = error_message
        self._diagnostic_info = _collect_diagnostic_info(error_message, request_id)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        if self._error_message:
            error_label = QLabel(self._error_message[:500])
            error_label.setWordWrap(True)
            error_label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(error_label)
            help_text = "{}\n\n{}".format(
                tr("Copy your logs with the button below and send them to our support email."),
                tr("We'll get this fixed for you :)"),
            )
        else:
            help_text = "{}\n\n{}".format(
                tr("Something not working?"),
                tr("Copy your logs and send them to us, we'll look into it :)"),
            )

        help_label = QLabel(help_text)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self._copy_btn = QPushButton(tr("1. Copy logs"))
        self._copy_btn.clicked.connect(self._on_copy)
        layout.addWidget(self._copy_btn)

        arrow_label = QLabel("▼")
        arrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(arrow_label)

        self._email_btn = QPushButton(tr("2. Send to {email}").format(email=SUPPORT_EMAIL))
        self._email_btn.setToolTip(tr("Open email client"))
        self._email_btn.clicked.connect(self._on_open_email)
        layout.addWidget(self._email_btn)

    def _on_copy(self):
        QApplication.clipboard().setText(self._diagnostic_info)
        self._copy_btn.setText(tr("Copied!"))
        # Timer parented to the button: if the dialog closes first, the timer
        # dies with it and never fires into a freed C++ button.
        QtC.safe_single_shot(2000, self._copy_btn, self._restore_copy_label)

    def _restore_copy_label(self):
        try:
            self._copy_btn.setText(tr("1. Copy logs"))
        except RuntimeError:
            pass

    def _on_open_email(self):
        subject = quote("AI Edit - Bug Report")
        QDesktopServices.openUrl(QUrl(f"mailto:{SUPPORT_EMAIL}?subject={subject}"))


def show_error_report(parent, error_message: str = "", request_id: str = "") -> None:
    """Open the error report dialog. Never lets a UI error mask the original one."""
    try:
        dialog = ErrorReportDialog(error_message, request_id, parent)
        dialog.exec()
    except Exception:
        pass  # nosec B110
