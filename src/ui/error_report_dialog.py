"""
Error report dialog for the AI Edit plugin.
Minimal dialog: error message + copy logs + email contact.
Also provides a bug report dialog for user-initiated reports.
"""

import os
import platform
import re
import sys
from collections import deque
from datetime import datetime

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


SUPPORT_EMAIL = "yvann.barbot@terra-lab.ai"
TERRALAB_URL = "https://terra-lab.ai/ai-edit"

_log_buffer = deque(maxlen=100)
_log_collector_connected = False


def _anonymize_paths(text: str) -> str:
    """Anonymize file paths to hide the username."""
    if not text:
        return text
    text = re.sub(r"/Users/[^/\s]+(?=/|$|\s)", "<USER>", text)
    text = re.sub(r"/home/[^/\s]+(?=/|$|\s)", "<USER>", text)
    text = re.sub(r"[A-Za-z]:[/\\]Users[/\\][^/\\\s]+(?=[/\\]|$|\s)", "<USER>", text)
    text = re.sub(r"\\\\[^\\]+\\Users\\[^/\\\s]+(?=[/\\]|$|\s)", "<USER>", text)
    return text


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
        pass


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
        _log_buffer.append("[{}] {}".format(timestamp, message))


def _get_recent_logs() -> str:
    if not _log_buffer:
        return "(No logs captured this session)"
    logs = "\n".join(_log_buffer)
    return _anonymize_paths(logs)


def _collect_diagnostic_info(error_message: str) -> str:
    """Collect system diagnostic info for error reporting."""
    lines = []
    lines.append("=== AI Edit - Error Report ===")
    lines.append("")

    if error_message:
        lines.append("--- Error ---")
        lines.append(error_message)
        lines.append("")

    # Plugin version
    lines.append("--- Plugin ---")
    try:
        plugin_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        metadata_path = os.path.join(plugin_dir, "metadata.txt")
        if os.path.exists(metadata_path):
            with open(metadata_path, "r", encoding="utf-8") as f:
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
        "OS: {} ({} {})".format(sys.platform, platform.system(), platform.release())
    )
    lines.append("Architecture: {}".format(platform.machine()))
    lines.append(
        "Python: {}.{}.{}".format(
            sys.version_info.major, sys.version_info.minor, sys.version_info.micro
        )
    )

    try:
        from qgis.core import Qgis

        lines.append("QGIS: {}".format(Qgis.QGIS_VERSION))
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
    """Minimal error report dialog with copy logs + email flow."""

    def __init__(self, error_title: str, error_message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Edit")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)

        self._error_message = error_message
        self._diagnostic_info = _collect_diagnostic_info(error_message)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        error_label = QLabel(self._error_message[:500])
        error_label.setWordWrap(True)
        error_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(error_label)

        help_label = QLabel(
            "Copy your logs with the button below and send them to our email.\n\n"
            "We'll fix your issue :)"
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        # Step 1: Copy logs
        self._copy_btn = QPushButton("1. Click to copy logs")
        self._copy_btn.clicked.connect(self._on_copy)
        layout.addWidget(self._copy_btn)

        # Arrow
        arrow_label = QLabel("\u25bc")
        arrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(arrow_label)

        # Step 2: Email
        self._email_btn = QPushButton("2. Click to send to {}".format(SUPPORT_EMAIL))
        self._email_btn.setToolTip("Open email client")
        self._email_btn.clicked.connect(self._on_open_email)
        layout.addWidget(self._email_btn)

    def _on_copy(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self._diagnostic_info)
        self._copy_btn.setText("Copied!")
        from qgis.PyQt.QtCore import QTimer

        QTimer.singleShot(2000, lambda: self._copy_btn.setText("1. Click to copy logs"))

    def _on_open_email(self):
        from urllib.parse import quote

        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices

        subject = quote("AI Edit - Bug Report")
        QDesktopServices.openUrl(
            QUrl("mailto:{}?subject={}".format(SUPPORT_EMAIL, subject))
        )


class BugReportDialog(QDialog):
    """User-initiated bug report dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Report a Bug")
        self.setModal(True)
        self.setMinimumWidth(400)
        self.setMaximumWidth(500)

        self._diagnostic_info = _collect_diagnostic_info("")
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)

        msg_label = QLabel(
            "Something not working?\n\n"
            "Copy your logs and send them to us, we'll look into it :)"
        )
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        # Step 1: Copy logs
        self._copy_btn = QPushButton("1. Click to copy logs")
        self._copy_btn.clicked.connect(self._on_copy)
        layout.addWidget(self._copy_btn)

        # Arrow
        arrow_label = QLabel("\u25bc")
        arrow_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(arrow_label)

        # Step 2: Email
        self._email_btn = QPushButton("2. Click to send to {}".format(SUPPORT_EMAIL))
        self._email_btn.setToolTip("Open email client")
        self._email_btn.clicked.connect(self._on_open_email)
        layout.addWidget(self._email_btn)

    def _on_copy(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self._diagnostic_info)
        self._copy_btn.setText("Copied!")
        from qgis.PyQt.QtCore import QTimer

        QTimer.singleShot(2000, lambda: self._copy_btn.setText("1. Click to copy logs"))

    def _on_open_email(self):
        from urllib.parse import quote

        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices

        subject = quote("AI Edit - Bug Report")
        QDesktopServices.openUrl(
            QUrl("mailto:{}?subject={}".format(SUPPORT_EMAIL, subject))
        )


def show_error_report(parent, error_title: str, error_message: str):
    """Show the error report dialog."""
    dialog = ErrorReportDialog(error_title, error_message, parent)
    dialog.exec()


def show_bug_report(parent):
    """Show the bug report dialog."""
    dialog = BugReportDialog(parent)
    dialog.exec()
