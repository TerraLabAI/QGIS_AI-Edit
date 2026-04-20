"""Log collector for the AI Edit plugin.

Captures QGIS log messages for diagnostics.
"""
from __future__ import annotations

import os
import platform
import re
import sys
from collections import deque
from datetime import datetime

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
        _log_buffer.append(f"[{timestamp}] {message}")


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
