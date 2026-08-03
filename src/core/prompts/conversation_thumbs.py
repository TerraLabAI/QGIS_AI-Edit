"""Local thumbnails for conversation rows, written at generation time.

Server thumb URLs are signed and expire within the hour, so history rows
restored from the disk cache cannot fetch them reliably. Instead the plugin
saves a small square JPEG the moment a generation lands (the pixmap is
already in hand) and the rows read it back from disk, no network involved.
Generations older than this feature simply have no thumb and render as
text-only rows.

Stored under the QGIS profile dir (never the plugin folder, which updates
wipe), capped and pruned oldest-first.
"""
from __future__ import annotations

import os

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPixmap

from ..logger import log_warning

_THUMB_DIR_NAME = "ai_edit_conversation_thumbs"
# Square edge of the stored file, 2x what the row paints, for hi-DPI.
_THUMB_EDGE_PX = 96
_THUMB_JPEG_QUALITY = 85
# Room for the history-cache window twice over: every conversation stores its
# cover output AND its session Original, plus member outputs for the strip.
_THUMB_CAP = 160


def _thumb_dir() -> str | None:
    base = QgsApplication.qgisSettingsDirPath() or ""
    if not base:
        return None
    path = os.path.join(base, "TerraLab", _THUMB_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as err:
        log_warning(f"conversation thumb dir unavailable: {err}")
        return None
    return path


def _thumb_path(request_id: str) -> str | None:
    rid = (request_id or "").strip()
    if not rid or not all(c.isalnum() or c in "-_" for c in rid):
        return None
    folder = _thumb_dir()
    if folder is None:
        return None
    return os.path.join(folder, rid + ".jpg")


def save_thumb(request_id: str, pixmap: QPixmap | None) -> None:
    """Center-crop ``pixmap`` to a small square and persist it. Best-effort:
    a failure only costs the row its picture."""
    path = _thumb_path(request_id)
    if path is None or pixmap is None or pixmap.isNull():
        return
    scaled = pixmap.scaled(
        _THUMB_EDGE_PX,
        _THUMB_EDGE_PX,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - _THUMB_EDGE_PX) // 2)
    y = max(0, (scaled.height() - _THUMB_EDGE_PX) // 2)
    square = scaled.copy(x, y, _THUMB_EDGE_PX, _THUMB_EDGE_PX)
    if not square.save(path, "JPG", _THUMB_JPEG_QUALITY):
        log_warning("conversation thumb save failed")
        return
    _prune()


def load_thumb(request_id: str) -> QPixmap | None:
    """The locally saved thumb, or None (older generation, pruned, failed)."""
    path = _thumb_path(request_id)
    if path is None or not os.path.isfile(path):
        return None
    pixmap = QPixmap(path)
    return None if pixmap.isNull() else pixmap


def delete_thumb(request_id: str) -> None:
    """Drop one saved thumb (conversation deleted). Missing file is fine."""
    path = _thumb_path(request_id)
    if path is None:
        return
    try:
        os.remove(path)
    except OSError:  # nosec B110 - already gone is the goal.
        pass


def clear_thumbs() -> None:
    """Drop every saved thumb (history wiped from Account Settings)."""
    folder = _thumb_dir()
    if folder is None:
        return
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file():
                os.remove(entry.path)
        except OSError:  # nosec B110
            pass


def _prune() -> None:
    """Drop the oldest files beyond the cap. Errors are non-fatal."""
    folder = _thumb_dir()
    if folder is None:
        return
    try:
        entries = [
            (entry.stat().st_mtime, entry.path)
            for entry in os.scandir(folder)
            if entry.is_file()
        ]
    except OSError:
        return
    entries.sort()
    for _mtime, path in entries[: max(0, len(entries) - _THUMB_CAP)]:
        try:
            os.remove(path)
        except OSError:  # nosec B110 - pruning is best-effort.
            pass
